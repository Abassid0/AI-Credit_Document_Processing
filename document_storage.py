"""
document_storage.py — Supabase Storage layer for uploaded applicant documents.

Stores sanitized document images in a private Supabase Storage bucket so
officers can review the original uploads alongside extracted data.

Files are stored at: documents/{reference_number}/{doc_type}.jpg
Signed URLs (time-limited) are generated for officer viewing — documents
are never publicly accessible.

Data-handling guardrail: only sanitized images (stripped of EXIF/metadata
by security.py) are stored. Raw uploads are never persisted.
"""
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

BUCKET_NAME = "documents"
URL_EXPIRY_SECONDS = 3600


def _get_client():
    from storage import get_client
    return get_client()


def _ensure_bucket() -> bool:
    try:
        client = _get_client()
        buckets = client.storage.list_buckets()
        if not any(b.name == BUCKET_NAME for b in buckets):
            client.storage.create_bucket(
                BUCKET_NAME,
                options={"public": False},
            )
            logger.info("Created storage bucket '%s'", BUCKET_NAME)
        return True
    except Exception:
        logger.exception("Failed to ensure storage bucket")
        return False


def upload_document(
    reference: str,
    doc_type: str,
    image_bytes: bytes,
) -> Optional[str]:
    """
    Upload a sanitized document image to Supabase Storage.
    Returns the storage path on success, None on failure.
    """
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        return None
    try:
        if not _ensure_bucket():
            return None
        path = f"{reference}/{doc_type}.jpg"
        client = _get_client()
        client.storage.from_(BUCKET_NAME).upload(
            path,
            image_bytes,
            file_options={"content-type": "image/jpeg", "upsert": "true"},
        )
        logger.info("Stored document: %s", path)
        return path
    except Exception:
        logger.exception("Failed to upload document %s/%s", reference, doc_type)
        return None


def list_documents(reference: str) -> list[dict]:
    """
    List all stored documents for an application reference.
    Returns list of {name, doc_type, path}.
    """
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        return []
    try:
        client = _get_client()
        files = client.storage.from_(BUCKET_NAME).list(reference)
        results = []
        for f in files:
            name = f.get("name", "")
            if not name:
                continue
            doc_type = name.replace(".jpg", "").replace(".png", "")
            results.append({
                "name": name,
                "doc_type": doc_type,
                "label": doc_type.replace("_", " ").title(),
                "path": f"{reference}/{name}",
            })
        return results
    except Exception:
        logger.exception("Failed to list documents for %s", reference)
        return []


def get_signed_url(path: str, expires_in: int = URL_EXPIRY_SECONDS) -> Optional[str]:
    """
    Generate a time-limited signed URL for officer viewing.
    """
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        return None
    try:
        client = _get_client()
        result = client.storage.from_(BUCKET_NAME).create_signed_url(
            path, expires_in
        )
        return result.get("signedURL") or result.get("signedUrl")
    except Exception:
        logger.exception("Failed to create signed URL for %s", path)
        return None


def get_document_urls(reference: str) -> list[dict]:
    """
    Returns all documents for a reference with signed viewing URLs.
    Each item: {doc_type, label, url}.
    """
    docs = list_documents(reference)
    results = []
    for doc in docs:
        url = get_signed_url(doc["path"])
        if url:
            results.append({
                "doc_type": doc["doc_type"],
                "label": doc["label"],
                "url": url,
            })
    return results
