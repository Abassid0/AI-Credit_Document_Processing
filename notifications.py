"""
notifications.py — Async applicant notification dispatch.

Delivery order:
  1. Telegram — zero cost, same channel the applicant already used.
     Uses the telegram_user_id stored at session start.
  2. SMS via Africa's Talking — fallback if Telegram delivery fails
     (bot blocked, account deleted, notifications off).
  3. WhatsApp via Meta Cloud API — fallback if SMS also fails.
     Requires WHATSAPP_PHONE_ID and WHATSAPP_TOKEN in .env.
     Set WHATSAPP_TEMPLATE to an approved template name for outbound
     messages outside the 24-hour customer-service window.
  4. Email via SMTP (STARTTLS) — last resort.

The officer triggers a notification with /notify from their own Telegram
chat. This module handles formatting, delivery, and failure logging.
Never logs raw phone numbers — the PII filter in security.py covers
log output, and this module avoids surfacing them in messages it
doesn't own.
"""
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reference number
# ---------------------------------------------------------------------------

def generate_reference() -> str:
    """
    Returns a unique application reference, e.g. CRB-20260825-3f7a.
    Generated once at session start and stored in both the applicant
    summary and the officer's forwarded report so both sides can cite it.
    """
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_part = secrets.token_hex(2)   # 4 hex chars — short, typeable
    return f"CRB-{date_part}-{rand_part}"


# ---------------------------------------------------------------------------
# Phone number helpers
# ---------------------------------------------------------------------------

_PHONE_STRIP = re.compile(r"[\s\-\(\)\+]")


def normalize_phone(raw: str) -> Optional[str]:
    """
    Normalises a Nigerian phone number to E.164 (+2348012345678).
    Accepts: 08012345678 | 8012345678 | +2348012345678 | 2348012345678
    Returns None if the input doesn't look like a valid Nigerian number.
    """
    digits = _PHONE_STRIP.sub("", raw)
    digits = "".join(c for c in digits if c.isdigit())

    if digits.startswith("234") and len(digits) == 13:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 11:
        return f"+234{digits[1:]}"
    if len(digits) == 10:
        return f"+234{digits}"
    return None


def validate_phone_input(raw: str) -> bool:
    return normalize_phone(raw) is not None


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_officer_update(
    applicant_name: str,
    reference: str,
    officer_message: str,
) -> str:
    """
    Formats the message the applicant receives when the officer sends /notify.
    Keeps it human and clear — this may be the first real feedback after
    the applicant submitted their documents.
    """
    first_name = applicant_name.split()[0] if applicant_name else "Applicant"
    return (
        f"📬 Update on your credit application\n\n"
        f"Hi {first_name},\n\n"
        f"{officer_message.strip()}\n\n"
        f"Reference: {reference}\n\n"
        f"Reply to this message or contact your officer directly "
        f"if you have any questions."
    )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

async def send_via_telegram(bot, telegram_user_id: int, message: str) -> bool:
    """Sends a Telegram message to the applicant. Returns True on success."""
    try:
        await bot.send_message(chat_id=telegram_user_id, text=message)
        logger.info("Telegram notification delivered (user %d)", telegram_user_id)
        return True
    except Exception as exc:
        logger.warning(
            "Telegram delivery failed for user %d: %s",
            telegram_user_id, type(exc).__name__,
        )
        return False


def send_via_sms(phone_number: str, message: str) -> bool:
    """
    Sends an SMS via Africa's Talking. Returns True on success.
    phone_number must already be in E.164 format (+2348012345678).
    Requires AT_API_KEY and AT_USERNAME to be configured.
    """
    import config
    if not config.AT_API_KEY or not config.AT_USERNAME:
        logger.warning("Africa's Talking credentials not configured — SMS skipped")
        return False

    try:
        import africastalking
        africastalking.initialize(config.AT_USERNAME, config.AT_API_KEY)
        sms = africastalking.SMS
        kwargs: dict = {"message": message, "recipients": [phone_number]}
        if config.AT_SENDER_ID:
            kwargs["senderId"] = config.AT_SENDER_ID
        response = sms.send(**kwargs)
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])
        if recipients and recipients[0].get("status") == "Success":
            logger.info("SMS notification delivered")
            return True
        logger.warning("SMS non-success response: %s", response.get("SMSMessageData", {}).get("Message"))
        return False
    except ImportError:
        logger.warning("africastalking package not installed — SMS skipped")
        return False
    except Exception as exc:
        logger.warning("SMS delivery failed: %s", type(exc).__name__)
        return False


def send_via_whatsapp(phone_number: str, message: str) -> bool:
    """
    Sends a WhatsApp message via the Meta WhatsApp Cloud API.
    phone_number must be in E.164 format (+2348012345678).

    If WHATSAPP_TEMPLATE is set, sends an approved template message so
    delivery works outside the 24-hour customer-service window. The
    template must accept exactly two parameters: {{1}} = reference snippet,
    {{2}} = the officer's message body.

    If WHATSAPP_TEMPLATE is blank, sends a free-form text message (only
    works while an active 24-hour session is open with that number).

    Returns True on HTTP 200 with no errors in the response body.
    Requires WHATSAPP_PHONE_ID and WHATSAPP_TOKEN to be configured.
    """
    import config
    if not config.WHATSAPP_PHONE_ID or not config.WHATSAPP_TOKEN:
        logger.warning("WhatsApp credentials not configured — WhatsApp skipped")
        return False

    import json
    import urllib.request
    import urllib.error

    url = (
        f"https://graph.facebook.com/v19.0/"
        f"{config.WHATSAPP_PHONE_ID}/messages"
    )

    if config.WHATSAPP_TEMPLATE:
        # Split message into two halves so the template's two parameters are
        # populated: first line(s) as the reference/header, rest as the body.
        parts = message.split("\n\n", 1)
        param1 = parts[0][:1024]
        param2 = parts[1][:1024] if len(parts) > 1 else ""
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "template",
            "template": {
                "name": config.WHATSAPP_TEMPLATE,
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": param1},
                            {"type": "text", "text": param2},
                        ],
                    }
                ],
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": message[:4096]},
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body_bytes = resp.read()
            resp_json = json.loads(body_bytes)
            # A successful send has a "messages" key with at least one entry
            if resp_json.get("messages"):
                logger.info("WhatsApp notification delivered")
                return True
            logger.warning("WhatsApp unexpected response: %s", resp_json)
            return False
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        logger.warning("WhatsApp HTTP %d: %s", exc.code, err_body[:300])
        return False
    except Exception as exc:
        logger.warning("WhatsApp delivery failed: %s", type(exc).__name__)
        return False


def send_via_email(to_email: str, subject: str, body: str) -> bool:
    """
    Sends a plain-text email via SMTP (STARTTLS).
    Requires SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS in config.
    Returns True on success, False on any failure.
    """
    import config
    if not config.SMTP_USER or not config.SMTP_PASS:
        logger.warning("SMTP credentials not configured — email not sent")
        return False

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_USER))
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.sendmail(config.SMTP_USER, to_email, msg.as_string())

        logger.info("Email notification delivered")
        return True
    except Exception as exc:
        logger.warning("Email delivery failed: %s", type(exc).__name__)
        return False


async def notify_applicant(
    bot,
    telegram_user_id: int,
    phone_number: Optional[str],
    email: Optional[str],
    applicant_name: str,
    reference: str,
    officer_message: str,
) -> tuple[bool, str]:
    """
    Delivery order: Telegram → SMS → WhatsApp → Email.
    Stops at the first successful channel.
    Returns (success: bool, channel_used: str).
    channel_used is "telegram", "sms", "whatsapp", "email", or "none".
    """
    message = format_officer_update(applicant_name, reference, officer_message)

    if await send_via_telegram(bot, telegram_user_id, message):
        return True, "telegram"

    if phone_number:
        normalized = normalize_phone(phone_number)
        if normalized:
            if send_via_sms(normalized, message):
                return True, "sms"
            if send_via_whatsapp(normalized, message):
                return True, "whatsapp"

    if email:
        subject = f"Update on your credit application — {reference}"
        if send_via_email(email, subject, message):
            return True, "email"

    logger.error("All notification channels failed for reference %s", reference)
    return False, "none"
