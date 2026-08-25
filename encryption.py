"""
encryption.py — Symmetric field-level encryption for PII stored in Supabase.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the cryptography package.
The key is a URL-safe base64-encoded 32-byte value set in FIELD_ENCRYPTION_KEY.

If FIELD_ENCRYPTION_KEY is not set, encrypt_field() and decrypt_field() are
no-ops that return the value unchanged — the bot continues to work, but PII
is stored in plain text. A warning is logged once at startup.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)
_warned = False


def _get_fernet():
    import config
    global _warned

    key = config.FIELD_ENCRYPTION_KEY
    if not key:
        if not _warned:
            logger.warning(
                "FIELD_ENCRYPTION_KEY is not set — PII fields are stored unencrypted. "
                "Generate a key and add it to .env before going to production."
            )
            _warned = True
        return None

    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_field(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a string field. Returns None unchanged. Returns ciphertext as a str."""
    if plaintext is None:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_field(ciphertext: Optional[str]) -> Optional[str]:
    """
    Decrypt a string field. Returns None unchanged.
    If the value is not valid ciphertext (e.g. a legacy plain-text row),
    returns the value unchanged and logs a debug message so old records
    keep working after the key is added.
    """
    if ciphertext is None:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return ciphertext
    try:
        from cryptography.fernet import InvalidToken
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        logger.debug("decrypt_field: value is not valid ciphertext — returning as-is (legacy row?)")
        return ciphertext
