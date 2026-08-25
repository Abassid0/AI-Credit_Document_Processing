"""
bot.py — Telegram bot for the credit application document processing
pipeline (SOP-009).

Applicant conversation flow:
  greet → name → address → gender → phone → 4 documents → summary

Officer commands (outside conversation, usable any time):
  /notify <reference> <message>  — send a status update to an applicant

This bot NEVER issues a lending decision — all output is framed as
"readiness for officer review."
"""
import asyncio
import io
import logging
import re
import time
from datetime import timedelta

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config
import security
from extraction import extract_document
from notifications import (
    generate_reference,
    notify_applicant,
    normalize_phone,
    validate_phone_input,
    format_officer_update,
    send_via_telegram,
    send_via_sms,
)
from validation import validate_application, redact, ExtractedDocument, FieldConfidence

SENSITIVE_COMPARISON_FIELDS = {"bvn", "nin", "account_number", "id_number"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
security.install_pii_filter()
logger = logging.getLogger(__name__)

# Conversation states
COLLECTING_NAME    = 0
COLLECTING_ADDRESS = 1
COLLECTING_GENDER  = 2
COLLECTING_PHONE   = 3
COLLECTING_EMAIL   = 4
COLLECTING_DOCS    = 5

VALID_GENDERS = {"male", "female", "other", "prefer not to say"}


# ---------------------------------------------------------------------------
# Conversation handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if not security.check_rate_limit(user_id, max_sessions=config.RATE_LIMIT_MAX_SESSIONS):
        await update.message.reply_text(
            "You have started too many applications recently. "
            "Please wait a while before trying again."
        )
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["documents"]   = []
    context.user_data["doc_index"]   = 0
    context.user_data["start_time"]  = time.monotonic()
    context.user_data["reference"]   = generate_reference()

    # Deep-link officer code: t.me/BotName?start=OFC-xxxx
    officer_code = context.args[0] if context.args else None
    context.user_data["officer_code"] = officer_code

    if officer_code and officer_code not in config.OFFICER_MAP:
        logger.warning("Unknown officer code in deep link: %s", officer_code)

    await update.message.reply_text(
        "Welcome to the Credit Application Document Assistant.\n\n"
        "I'll help prepare your file for review by a credit officer. "
        "I do NOT decide your loan — a human officer reviews everything "
        "before any decision is made.\n\n"
        "Let's start with a few personal details.\n\n"
        "Please enter your *full name* as it appears on your ID:",
        parse_mode="Markdown",
    )
    return COLLECTING_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Please enter a valid full name.")
        return COLLECTING_NAME

    context.user_data["declared_name"] = name
    await update.message.reply_text(
        f"Thanks, {name.split()[0]}.\n\n"
        "Now please enter your *current residential address*:",
        parse_mode="Markdown",
    )
    return COLLECTING_ADDRESS


async def receive_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    address = update.message.text.strip()
    if len(address) < 5:
        await update.message.reply_text("Please enter a valid address.")
        return COLLECTING_ADDRESS

    context.user_data["declared_address"] = address
    await update.message.reply_text(
        "Got it.\n\n"
        "Please enter your *gender* (Male / Female / Other / Prefer not to say):",
        parse_mode="Markdown",
    )
    return COLLECTING_GENDER


async def receive_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    gender = update.message.text.strip()
    if gender.lower() not in VALID_GENDERS:
        await update.message.reply_text(
            "Please enter one of: Male, Female, Other, or Prefer not to say."
        )
        return COLLECTING_GENDER

    context.user_data["declared_gender"] = gender.title()
    await update.message.reply_text(
        "Got it.\n\n"
        "Please enter your *Nigerian phone number* — we'll use this to "
        "reach you if your Telegram notifications are off:\n"
        "_(e.g. 08012345678 or +2348012345678)_",
        parse_mode="Markdown",
    )
    return COLLECTING_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    normalized = normalize_phone(raw)

    if not normalized:
        await update.message.reply_text(
            "That doesn't look like a valid Nigerian phone number.\n"
            "Please enter it in the format 08012345678 or +2348012345678."
        )
        return COLLECTING_PHONE

    context.user_data["phone_number"] = normalized   # stored as E.164

    await update.message.reply_text(
        "Got it.\n\n"
        "Please enter your *email address* — used as a backup if Telegram "
        "and SMS both fail to reach you.\n\n"
        "_Type your email or_ *skip* _to continue without one._",
        parse_mode="Markdown",
    )
    return COLLECTING_EMAIL


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    import re as _re
    raw = update.message.text.strip()

    if raw.lower() == "skip":
        context.user_data["email"] = None
    elif _EMAIL_RE.match(raw):
        context.user_data["email"] = raw.lower()
    else:
        await update.message.reply_text(
            "That doesn't look like a valid email address.\n"
            "Please enter a valid email or type *skip* to continue without one.",
            parse_mode="Markdown",
        )
        return COLLECTING_EMAIL

    await update.message.reply_text(
        "Perfect — your details are saved.\n\n"
        "Now let's collect your documents. We'll go one at a time.\n\n"
        "📸 *First: your passport photograph*\n\n"
        "Tap the 📎 attachment icon then choose *Camera* to take a selfie now, "
        "or *Gallery* to upload a recent passport-style photo.\n\n"
        "Make sure your face is clearly visible against a plain background.\n\n"
        "_For all other documents you can send a photo OR a PDF. "
        "Send /cancel anytime to stop._",
        parse_mode="Markdown",
    )
    return COLLECTING_DOCS


def convert_pdf_to_image(pdf_bytes: bytes, dpi: int = 200) -> bytes:
    """
    Renders the first page of a PDF to a JPEG using PyMuPDF (no external
    binaries required). Returns raw JPEG bytes ready for sanitize_image()
    and extract_document(). Raises ValueError on corrupt or empty PDFs.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ValueError("pymupdf is not installed — cannot process PDF uploads.")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc

    if doc.page_count == 0:
        raise ValueError("PDF appears to have no pages.")

    page = doc.load_page(0)
    zoom = dpi / 72  # fitz default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img = pix.tobytes("jpeg")
    doc.close()
    return img


async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc_index = context.user_data.get("doc_index", 0)
    if doc_index >= len(config.REQUIRED_DOCUMENTS_ORDER):
        return await finalize(update, context)

    doc_type, doc_label = config.REQUIRED_DOCUMENTS_ORDER[doc_index]

    photo = update.message.photo[-1] if update.message.photo else None
    pdf_doc = (
        update.message.document
        if update.message.document and update.message.document.mime_type == "application/pdf"
        else None
    )

    if photo is None and pdf_doc is None:
        if doc_type == "passport_photo":
            await update.message.reply_text(
                "Please send your passport photograph.\n\n"
                "Tap the 📎 attachment icon → *Camera* to take a selfie now, "
                "or *Gallery* to upload a recent photo.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"Please send your {doc_label} as a *photo* or *PDF*.\n"
                "Tap 📎 to attach.",
                parse_mode="Markdown",
            )
        return COLLECTING_DOCS

    # Download bytes from whichever format was sent
    if photo:
        tg_file = await context.bot.get_file(photo.file_id)
        raw_bytes = bytes(await tg_file.download_as_bytearray())
        source_label = "image"
    else:
        if pdf_doc.file_size and pdf_doc.file_size > 10 * 1024 * 1024:
            await update.message.reply_text(
                "That PDF is too large (limit 10 MB). "
                "Please compress it or send a photo of the document instead."
            )
            return COLLECTING_DOCS
        tg_file = await context.bot.get_file(pdf_doc.file_id)
        raw_bytes = bytes(await tg_file.download_as_bytearray())
        source_label = "PDF"

    # Convert PDF to image if needed
    if source_label == "PDF":
        try:
            raw_bytes = await asyncio.to_thread(convert_pdf_to_image, raw_bytes)
        except ValueError as exc:
            await update.message.reply_text(
                f"⚠️ Could not read that PDF: {exc}\n"
                "Please send a clearer copy or photograph the document instead."
            )
            return COLLECTING_DOCS

    try:
        image_bytes = security.sanitize_image(raw_bytes)
    except ValueError as exc:
        await update.message.reply_text(
            f"⚠️ Could not process that {source_label}: {exc}\n"
            "Please retake the photo or send a different copy."
        )
        return COLLECTING_DOCS

    await update.message.reply_text(f"Got it — reading your {doc_label}...")

    extracted: ExtractedDocument = await asyncio.to_thread(
        extract_document, image_bytes, f"{doc_type}.jpg", doc_type
    )
    context.user_data["documents"].append(extracted)
    context.user_data["doc_index"] = doc_index + 1

    next_index = doc_index + 1
    if next_index < len(config.REQUIRED_DOCUMENTS_ORDER):
        next_type, next_label = config.REQUIRED_DOCUMENTS_ORDER[next_index]
        if next_type == "passport_photo":
            await update.message.reply_text(
                f"Next: your *{next_label}*.\n"
                "Tap 📎 → *Camera* to take a selfie now, or *Gallery* to upload.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"Next: your *{next_label}*.\n"
                "Send it as a photo or PDF — tap 📎 to attach.",
                parse_mode="Markdown",
            )
        return COLLECTING_DOCS

    return await finalize(update, context)


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

def _format_comparison_table(field_comparison: dict) -> str:
    if not field_comparison:
        return ""
    lines = ["📎 Field Comparison (evidence behind the flags above)"]
    for field_name, entries in field_comparison.items():
        if not entries:
            continue
        lines.append(f"\n{field_name.replace('_', ' ').title()}:")
        for doc_type, value, confidence in entries:
            if confidence != FieldConfidence.OK:
                shown = f"({confidence.value})"
            elif field_name in SENSITIVE_COMPARISON_FIELDS:
                shown = redact(value)
            else:
                shown = value or "—"
            lines.append(f"  • {doc_type}: {shown}")
    return "\n".join(lines)


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    documents: list[ExtractedDocument] = context.user_data.get("documents", [])
    start_time    = context.user_data.get("start_time", time.monotonic())
    declared_name    = context.user_data.get("declared_name")
    declared_address = context.user_data.get("declared_address")
    declared_gender  = context.user_data.get("declared_gender")
    phone_number     = context.user_data.get("phone_number")
    email            = context.user_data.get("email")
    officer_code     = context.user_data.get("officer_code")
    reference     = context.user_data.get("reference", generate_reference())
    applicant_id  = update.effective_user.id

    await update.message.reply_text("All documents received — running checks...")

    result = validate_application(documents, user_info={
        "name": declared_name,
        "address": declared_address,
        "gender": declared_gender,
    })
    elapsed = time.monotonic() - start_time

    # ── Applicant-facing summary ──────────────────────────────────────────
    lines = [
        "📋 Document Readiness Summary",
        f"Reference: {reference}",
        "",
        "Declared by applicant:",
        f"  Name:    {declared_name or '—'}",
        f"  Address: {declared_address or '—'}",
        f"  Gender:  {declared_gender or '—'}",
        "",
        f"Completeness: {result.completeness_pct}%",
        f"Ready for officer review: {'Yes' if result.ready_for_underwriting else 'No — see flags below'}",
        f"Processed in {elapsed:.1f} seconds",
        "",
    ]

    if result.flags:
        lines.append("Flags:")
        for flag in result.flags:
            icon = "🛑" if flag.severity == "blocker" else "⚠️"
            lines.append(f"{icon} {flag.message}")
    else:
        lines.append("No issues found.")

    lines += [
        "",
        "─────────────────────────────",
        "✅ Your application has been submitted.",
        f"Your reference number is: {reference}",
        "",
        "A credit officer will review your documents and send you an "
        "update here on Telegram"
        + (", via SMS" if phone_number else "")
        + (", or by email" if email else "")
        + " once their review is complete. "
        "This typically takes 1–3 business days.",
        "",
        "Nothing in this report is an approval or rejection — "
        "all decisions are made by a human officer.",
    ]

    summary_text = "\n".join(lines)
    comparison_text = _format_comparison_table(result.field_comparison)

    await update.message.reply_text(summary_text)
    if comparison_text:
        await update.message.reply_text(comparison_text)

    # ── Officer-facing forward ────────────────────────────────────────────
    officer_chat_id = (
        config.OFFICER_MAP.get(officer_code) if officer_code else None
    ) or config.DEFAULT_OFFICER_CHAT_ID

    if officer_chat_id:
        officer_header = (
            f"📥 New application received\n"
            f"Reference:          {reference}\n"
            f"Applicant Tg ID:    {applicant_id}\n"
            f"Phone:              {'provided' if phone_number else 'not provided'}\n"
            f"Email:              {'provided' if email else 'not provided'}\n"
            f"Officer code:       {officer_code or 'direct'}\n"
            f"\n⚠️ IDENTITY UNVERIFIED — applicant's documents have NOT been "
            f"checked against NIBSS/NIMC. Confirm the applicant's identity "
            f"independently before proceeding.\n\n"
            f"To send the applicant a status update once you have vetted "
            f"their file, use:\n"
            f"/notify {reference} <your message>\n\n"
        )
        try:
            await context.bot.send_message(
                chat_id=officer_chat_id,
                text=officer_header + summary_text,
            )
            if comparison_text:
                await context.bot.send_message(
                    chat_id=officer_chat_id,
                    text=comparison_text,
                )
            # Audit: report forwarded — logged after app_id is set below
            context.user_data["_forward_succeeded"] = True
        except Exception:
            logger.exception("Failed to forward report to officer chat %s", officer_chat_id)
    else:
        logger.warning(
            "No officer chat for code %r — report not forwarded. "
            "Set OFFICER_MAP or DEFAULT_OFFICER_CHAT_ID in .env.",
            officer_code,
        )

    # ── Supabase persistence ──────────────────────────────────────────────
    if config.SUPABASE_URL and config.SUPABASE_KEY:
        try:
            from storage import create_application, save_validation_result, append_audit_event
            app_id = create_application(
                telegram_user_id=applicant_id,
                reference_number=reference,
                officer_code=officer_code,
                declared_name=declared_name,
                declared_address=declared_address,
                declared_gender=declared_gender,
                phone_number=phone_number,
                email=email,
            )
            save_validation_result(
                application_id=app_id,
                completeness_pct=result.completeness_pct,
                ready_for_underwriting=result.ready_for_underwriting,
                flags=[f.__dict__ for f in result.flags],
                turnaround_seconds=elapsed,
            )
            append_audit_event(
                application_id=app_id,
                event_type="application_submitted",
                actor="applicant",
                payload={
                    "reference": reference,
                    "completeness_pct": result.completeness_pct,
                    "ready_for_underwriting": result.ready_for_underwriting,
                },
            )
            if context.user_data.pop("_forward_succeeded", False):
                append_audit_event(
                    application_id=app_id,
                    event_type="report_forwarded_to_officer",
                    actor="bot",
                    payload={"officer_chat_id": officer_chat_id},
                )
            logger.info("Application %s saved to Supabase", reference)
        except Exception:
            logger.exception("Supabase persistence failed for %s", reference)

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Officer /notify command
# ---------------------------------------------------------------------------

async def officer_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Officer sends: /notify <reference_or_telegram_id> <message text>

    The bot:
      1. Validates the sender is a registered officer (optional — logs warning if not).
      2. Looks up the applicant via Supabase (by reference) or uses the
         telegram_user_id directly (if Supabase isn't configured).
      3. Sends the applicant a formatted notification via Telegram → SMS fallback.
      4. Logs delivery back to the officer and to Supabase.
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /notify <reference_or_telegram_id> <message>\n\n"
            "Example:\n"
            "/notify CRB-20260825-3f7a Your documents have been verified. "
            "Please visit our Abuja branch on Monday with your original ID."
        )
        return

    target   = context.args[0]
    message  = " ".join(context.args[1:])
    sender_chat_id = update.effective_chat.id

    # Identify officer by their chat ID
    officer_code = config.get_officer_code_by_chat_id(sender_chat_id)
    if officer_code is None and sender_chat_id != config.DEFAULT_OFFICER_CHAT_ID:
        logger.warning(
            "Unregistered chat %d attempted /notify — proceeding but flagged",
            sender_chat_id,
        )

    # ── Resolve applicant ─────────────────────────────────────────────────
    telegram_user_id: int | None = None
    phone_number: str | None     = None
    email: str | None            = None
    applicant_name: str          = "Applicant"
    app_id: str | None           = None
    reference: str               = target

    is_reference = target.upper().startswith("CRB-")

    if is_reference and config.SUPABASE_URL and config.SUPABASE_KEY:
        try:
            from storage import get_application_by_reference
            record = get_application_by_reference(target.upper())
            if record is None:
                await update.message.reply_text(
                    f"❌ No application found for reference {target}."
                )
                return
            # Ownership check — officer can only notify their own applicants
            if officer_code and record.get("officer_code") != officer_code:
                await update.message.reply_text(
                    "❌ That application belongs to a different officer."
                )
                return
            telegram_user_id = record["telegram_user_id"]
            phone_number     = record.get("phone_number")
            email            = record.get("email")
            applicant_name   = record.get("declared_name") or "Applicant"
            app_id           = record["id"]
            reference        = record["reference_number"]
        except Exception:
            logger.exception("Supabase lookup failed for /notify %s", target)
            await update.message.reply_text(
                "⚠️ Could not look up that reference — database error. "
                "Try again or use the applicant's Telegram ID directly."
            )
            return

    elif target.isdigit():
        # Direct by Telegram user ID (works without Supabase)
        telegram_user_id = int(target)
    else:
        await update.message.reply_text(
            f"❌ '{target}' is not a valid reference number (CRB-YYYYMMDD-xxxx) "
            "or Telegram user ID."
        )
        return

    # ── Deliver ───────────────────────────────────────────────────────────
    success, channel = await notify_applicant(
        bot=context.bot,
        telegram_user_id=telegram_user_id,
        phone_number=phone_number,
        email=email,
        applicant_name=applicant_name,
        reference=reference,
        officer_message=message,
    )

    # ── Confirm to officer ────────────────────────────────────────────────
    if success:
        await update.message.reply_text(
            f"✅ Notification delivered via {channel}.\n"
            f"Reference: {reference}"
        )
    else:
        await update.message.reply_text(
            f"❌ Delivery failed on all channels (Telegram + SMS).\n"
            f"Reference: {reference}\n"
            "The applicant may need to be contacted through another channel."
        )

    # ── Log to Supabase ───────────────────────────────────────────────────
    if app_id and config.SUPABASE_URL and config.SUPABASE_KEY:
        try:
            from storage import save_notification_delivery, append_audit_event
            save_notification_delivery(
                application_id=app_id,
                channel=channel,
                status="delivered" if success else "failed",
            )
            append_audit_event(
                application_id=app_id,
                event_type="officer_notified",
                actor=officer_code or "default_officer",
                payload={"channel": channel, "reference": reference},
            )
        except Exception:
            logger.exception("Failed to log notification delivery for %s", reference)


# ---------------------------------------------------------------------------
# Session & utility handlers
# ---------------------------------------------------------------------------

async def session_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update and update.message:
        await update.message.reply_text(
            "Your session has expired due to inactivity. "
            "Send /start to begin a new application."
        )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Application cancelled. Send /start to begin again."
    )
    return ConversationHandler.END


async def unrecognized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send /start to begin a new credit application."
    )


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------

def build_app() -> Application:
    config.validate_config()
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            COLLECTING_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            COLLECTING_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_address)],
            COLLECTING_GENDER:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gender)],
            COLLECTING_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            COLLECTING_EMAIL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)],
            COLLECTING_DOCS:    [MessageHandler(filters.PHOTO | filters.Document.PDF, receive_document)],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, session_timeout),
                CommandHandler("start", session_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=timedelta(seconds=config.SESSION_TIMEOUT_SECONDS),
    )

    application.add_handler(conv_handler)
    # /notify is available to officers outside any conversation context
    application.add_handler(CommandHandler("notify", officer_notify))
    application.add_handler(MessageHandler(filters.ALL, unrecognized))
    return application


def main() -> None:
    app = build_app()
    logger.info("Starting credit application bot...")
    app.run_polling()


if __name__ == "__main__":
    main()
