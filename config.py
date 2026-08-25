"""
config.py — Environment configuration for the credit application bot.
All secrets come from environment variables, never hardcoded.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (the directory containing this file).
# System environment variables take precedence over .env values.
load_dotenv(Path(__file__).parent / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# Fernet key for field-level PII encryption in Supabase.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# If blank, PII is stored unencrypted (warning logged at startup).
FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

# Africa's Talking SMS gateway (https://africastalking.com)
AT_USERNAME  = os.environ.get("AT_USERNAME", "")
AT_API_KEY   = os.environ.get("AT_API_KEY", "")
AT_SENDER_ID = os.environ.get("AT_SENDER_ID", "")   # optional alphanumeric sender name

# WhatsApp Cloud API (Meta) — https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
# WHATSAPP_PHONE_ID: numeric Phone Number ID from Meta for Developers → WhatsApp → API Setup
# WHATSAPP_TOKEN:    System User access token (permanent) or temporary token from the same page
# WHATSAPP_TEMPLATE: approved template name for outbound messages outside the 24-hour window
#                    (e.g. "credit_update"). Leave blank to attempt a free-form text message
#                    (only works during an active 24-hour customer-service window).
WHATSAPP_PHONE_ID  = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_TOKEN     = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_TEMPLATE  = os.environ.get("WHATSAPP_TEMPLATE", "")

# SMTP email (any provider — Gmail, Outlook, Zoho, etc.)
SMTP_HOST      = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASS      = os.environ.get("SMTP_PASS", "")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "CreditBot")

# Max number of application sessions a single Telegram user can start per hour.
RATE_LIMIT_MAX_SESSIONS: int = int(os.environ.get("RATE_LIMIT_MAX_SESSIONS", "3"))

# Session inactivity timeout in seconds. A conversation idle longer than this
# is automatically ended so no one can resume a session on a shared device.
SESSION_TIMEOUT_SECONDS: int = int(os.environ.get("SESSION_TIMEOUT_SECONDS", "1800"))  # 30 min

# Fallback chat ID that receives reports when no officer code is matched.
# Set to a shared team group so no application is silently dropped.
DEFAULT_OFFICER_CHAT_ID: int | None = (
    int(v) if (v := os.environ.get("DEFAULT_OFFICER_CHAT_ID", "")) else None
)

# JSON map of officer code → Telegram chat ID stored as an env variable.
# Example value: {"OFC001": 123456789, "OFC002": 987654321}
# Each officer's chat ID is their personal Telegram user ID (or a private
# group they own). Generate an officer's invite link with:
#   https://t.me/<BotUsername>?start=<officer_code>
_officer_map_raw = os.environ.get("OFFICER_MAP", "{}")
try:
    OFFICER_MAP: dict[str, int] = {k: int(v) for k, v in json.loads(_officer_map_raw).items()}
except (json.JSONDecodeError, ValueError):
    OFFICER_MAP = {}

# Order in which the bot asks for documents. (doc_type, human-readable prompt)
REQUIRED_DOCUMENTS_ORDER = [
    ("passport_photo",  "passport photograph (a clear selfie or recent passport-style photo of your face)"),
    ("government_id",   "government-issued ID (National ID/NIN slip, driver's license, or passport)"),
    ("bank_statement",  "bank statement (last 3-6 months)"),
    ("proof_of_income", "proof of income (payslip or business statement)"),
    ("proof_of_address","proof of address (a recent utility bill)"),
]


def get_officer_code_by_chat_id(chat_id: int) -> "str | None":
    """Reverse-lookup: given a Telegram chat ID, return the officer code."""
    for code, cid in OFFICER_MAP.items():
        if cid == chat_id:
            return code
    return None


def validate_config() -> None:
    """Fail loudly at startup if required secrets are missing."""
    missing = [
        name for name, val in [
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ] if not val
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
