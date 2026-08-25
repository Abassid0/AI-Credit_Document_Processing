"""
security.py — Security primitives for the credit application bot.

Covers: image sanitization (polyglot/payload stripping), per-user rate
limiting, PII-redacting log filter, and cryptographic officer code
generation. Import and wire these in bot.py — keep this module free of
Telegram or Anthropic dependencies so it stays independently testable.
"""
import io
import logging
import re
import secrets
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Image sanitization — strips polyglot payloads by re-encoding via Pillow
# ---------------------------------------------------------------------------

MAX_IMAGE_BYTES = 8 * 1024 * 1024   # 8 MB hard cap
MAX_IMAGE_DIMENSION = 6000           # longest-side pixel cap


def sanitize_image(raw_bytes: bytes) -> bytes:
    """
    Validates and re-encodes an uploaded image.

    Steps:
    1. Size check — reject oversized uploads before touching them.
    2. Pillow verify() — checks the file signature and raises on corrupt/
       malformed data or unrecognised formats.
    3. Dimension check — reject unusually large canvases.
    4. Re-encode as JPEG — this strips EXIF metadata, embedded scripts,
       ICC profiles, and any polyglot payload hidden in ancillary chunks.

    Raises ValueError with a user-safe message on any failure.
    Returns sanitized JPEG bytes ready to send to Claude.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        raise RuntimeError("Pillow is required for image sanitization: pip install Pillow")

    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"File too large ({len(raw_bytes) // 1024} KB). "
            f"Please send a photo under {MAX_IMAGE_BYTES // (1024*1024)} MB."
        )

    try:
        buf = io.BytesIO(raw_bytes)
        probe = Image.open(buf)
        probe.verify()  # raises on malformed data; exhausts the stream
    except Exception as exc:
        raise ValueError(f"Image could not be verified: {exc}") from exc

    # Re-open after verify() — the stream is exhausted after that call
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
    except Exception as exc:
        raise ValueError(f"Image could not be re-opened after verify: {exc}") from exc

    if max(w, h) > MAX_IMAGE_DIMENSION:
        raise ValueError(
            f"Image dimensions ({w}×{h} px) exceed the {MAX_IMAGE_DIMENSION} px limit."
        )

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=85, optimize=True)
    sanitized = out.getvalue()
    logger.debug("Image sanitized: %d bytes → %d bytes JPEG", len(raw_bytes), len(sanitized))
    return sanitized


# ---------------------------------------------------------------------------
# Rate limiter — per-user session cap, in-memory
# ---------------------------------------------------------------------------

_session_log: dict[int, list[float]] = defaultdict(list)


def check_rate_limit(
    user_id: int,
    max_sessions: int,
    window_seconds: int = 3600,
) -> bool:
    """
    Returns True if the user is within the rate limit and records this
    session start. Returns False (without recording) if they have exceeded
    max_sessions within the rolling window_seconds window.
    """
    now = time.monotonic()
    cutoff = now - window_seconds
    recent = [t for t in _session_log[user_id] if t > cutoff]
    _session_log[user_id] = recent

    if len(recent) >= max_sessions:
        return False

    _session_log[user_id].append(now)
    return True


# ---------------------------------------------------------------------------
# PII-redacting log filter
# ---------------------------------------------------------------------------

_REDACTION_RULES: list[tuple[re.Pattern, str]] = [
    # 11-digit numbers (BVN / NIN)
    (re.compile(r'\b\d{11}\b'), '***'),
    # 10-digit account numbers
    (re.compile(r'\b\d{10}\b'), '***'),
    # Quoted or colon-delimited name/address field values
    (re.compile(
        r'(?i)(full_name|declared_name|address|declared_address)'
        r'(["\s:=]+)(["\']?)([^"\'{\n,]{2,80})\3'
    ), r'\1\2[REDACTED]'),
]


class PIIRedactingFilter(logging.Filter):
    """
    Scrubs PII patterns (BVN/NIN digits, name/address field values) from
    log records before they are written to any handler. Attach once to
    the root logger via install_pii_filter().
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True  # don't break logging on formatting errors

        for pattern, replacement in _REDACTION_RULES:
            msg = pattern.sub(replacement, msg)

        record.msg = msg
        record.args = ()
        return True


def install_pii_filter() -> None:
    """
    Attaches PIIRedactingFilter to every handler on the root logger and
    to the root logger itself (catches handlers added after this call).
    Call once at application startup, before any logging occurs.
    """
    f = PIIRedactingFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(f)
    root.addFilter(f)
    logger.debug("PII redacting log filter installed")


# ---------------------------------------------------------------------------
# Officer code generator
# ---------------------------------------------------------------------------

def generate_officer_code(prefix: str = "OFC") -> str:
    """
    Returns a cryptographically random officer code, e.g. 'OFC-3a9f1c2b4e7d'.
    Use this instead of sequential OFC001/OFC002 codes to prevent enumeration.

    Add the result and the officer's Telegram chat ID to OFFICER_MAP in .env:
        OFFICER_MAP={"OFC-3a9f1c2b4e7d": 123456789}
    Their invite link is then:
        https://t.me/<BotUsername>?start=OFC-3a9f1c2b4e7d
    """
    return f"{prefix}-{secrets.token_hex(6)}"


if __name__ == "__main__":
    # Run directly to generate a new officer code: python security.py
    code = generate_officer_code()
    print(f"New officer code: {code}")
    print(f"Add to .env OFFICER_MAP: {{\"{code}\": <telegram_chat_id>}}")
    print(f"Invite link: https://t.me/<YourBot>?start={code}")
