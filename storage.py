"""
storage.py — Minimal Supabase persistence layer for application sessions.

Data-handling guardrail: this layer stores structured results (completeness,
flags, timestamps) — never raw document images and never unredacted
identifiers. Redact before anything reaches this module.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client

import config
from encryption import encrypt_field, decrypt_field

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


def create_application(
    telegram_user_id: int,
    reference_number: str,
    officer_code: Optional[str] = None,
    declared_name: Optional[str] = None,
    declared_address: Optional[str] = None,
    declared_gender: Optional[str] = None,
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
) -> str:
    """
    Creates a new application record. Returns the Supabase record id.
    phone_number stored in E.164 format. email stored as-is (lowercased).
    """
    client = get_client()
    result = client.table("applications").insert({
        "telegram_user_id": telegram_user_id,
        "reference_number": reference_number,
        "officer_code": officer_code,
        "declared_name":    encrypt_field(declared_name),
        "declared_address": encrypt_field(declared_address),
        "declared_gender":  declared_gender,           # not PII-sensitive, stored plain
        "phone_number":     encrypt_field(phone_number),
        "email":            encrypt_field(email),
        "status": "in_progress",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return result.data[0]["id"]


def save_validation_result(
    application_id: str,
    completeness_pct: float,
    ready_for_underwriting: bool,
    flags: list[dict],
    turnaround_seconds: float,
) -> None:
    client = get_client()
    client.table("applications").update({
        "status": "processed",
        "completeness_pct": completeness_pct,
        "ready_for_underwriting": ready_for_underwriting,  # readiness signal only — never a decision
        "flags": flags,
        "turnaround_seconds": turnaround_seconds,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", application_id).execute()


def get_application_by_reference(reference_number: str) -> Optional[dict]:
    """
    Fetches an application record by reference number.
    Returns the full row dict, or None if not found.
    Used by the officer /notify command to look up the applicant.
    """
    client = get_client()
    result = (
        client.table("applications")
        .select("*")
        .eq("reference_number", reference_number)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    row = result.data[0]
    row["declared_name"]    = decrypt_field(row.get("declared_name"))
    row["declared_address"] = decrypt_field(row.get("declared_address"))
    row["phone_number"]     = decrypt_field(row.get("phone_number"))
    row["email"]            = decrypt_field(row.get("email"))
    return row


def append_audit_event(
    application_id: Optional[str],
    event_type: str,
    actor: str,
    payload: Optional[dict] = None,
) -> None:
    """
    Appends one row to the immutable audit_log table.
    UPDATE/DELETE are revoked on that table so entries can never be altered.
    Silently logs a warning and returns if Supabase is not configured.
    """
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        logger.warning("append_audit_event: Supabase not configured — event %s not logged", event_type)
        return
    try:
        client = get_client()
        client.table("audit_log").insert({
            "application_id": application_id,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
        }).execute()
    except Exception:
        logger.exception("append_audit_event: failed to write event %s", event_type)


def list_applications(limit: int = 500) -> list[dict]:
    """
    Returns processed application rows for dashboard analytics.
    Columns returned: reference_number, officer_code, declared_gender,
    completeness_pct, ready_for_underwriting, flags, turnaround_seconds,
    notification_channel, notification_status, created_at, processed_at.
    PII columns (name, address, phone, email) are intentionally omitted.
    """
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        return []
    try:
        client = get_client()
        result = (
            client.table("applications")
            .select(
                "reference_number,officer_code,declared_gender,"
                "completeness_pct,ready_for_underwriting,flags,"
                "turnaround_seconds,notification_channel,notification_status,"
                "created_at,processed_at,status"
            )
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        logger.exception("list_applications: query failed")
        return []


def list_audit_events(limit: int = 200) -> list[dict]:
    """
    Returns recent audit log rows for the dashboard audit trail view.
    Columns: event_type, actor, payload, created_at.
    application_id omitted (UUID not useful in the UI).
    """
    if not (config.SUPABASE_URL and config.SUPABASE_KEY):
        return []
    try:
        client = get_client()
        result = (
            client.table("audit_log")
            .select("event_type,actor,payload,created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        logger.exception("list_audit_events: query failed")
        return []


def save_notification_delivery(
    application_id: str,
    channel: str,
    status: str,
) -> None:
    """
    Records when and how the applicant was notified by the officer.
    channel: "telegram" | "sms" | "none"
    status:  "delivered" | "failed"
    """
    client = get_client()
    client.table("applications").update({
        "notified_at": datetime.now(timezone.utc).isoformat(),
        "notification_channel": channel,
        "notification_status": status,
    }).eq("id", application_id).execute()
