"""
validation.py — Credit Application Document Processing: Validation Logic

Scope guardrail: this module NEVER produces a credit decision, approval,
rejection, or risk score. It only reports completeness and consistency
of a document bundle, for a human officer to review. Keep it that way.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Config — required document set and field-matching tolerances
# ---------------------------------------------------------------------------

REQUIRED_DOCUMENTS = [
    "passport_photo",
    "government_id",
    "bank_statement",
    "proof_of_income",
    "proof_of_address",
]

# How similar two strings must be (0-1) to count as "matching" across docs.
# Loose enough to tolerate OCR noise / minor formatting differences.
NAME_MATCH_THRESHOLD = 0.82
ADDRESS_MATCH_THRESHOLD = 0.70

# How old a "recency-sensitive" document is allowed to be, in days.
MAX_DOCUMENT_AGE_DAYS = 90


class FieldConfidence(str, Enum):
    OK = "ok"                # extracted cleanly
    UNREADABLE = "unreadable"  # model couldn't read it — DO NOT guess a value
    MISSING = "missing"      # field not present on the document at all


@dataclass
class ExtractedField:
    """
    One field pulled from one document by the extraction step.
    `confidence` must be set honestly by the extraction prompt — if the
    model isn't sure, it must return UNREADABLE, never a fabricated value.
    """
    value: Optional[str]
    confidence: FieldConfidence


@dataclass
class ExtractedDocument:
    doc_type: str  # must be one of REQUIRED_DOCUMENTS
    fields: dict[str, ExtractedField]  # e.g. {"full_name": ExtractedField(...)}
    document_date: Optional[date] = None  # statement/bill date, if applicable
    file_readable: bool = True  # False if file was blank/corrupt/wrong type


@dataclass
class ValidationFlag:
    code: str
    severity: str  # "info" | "warning" | "blocker"
    message: str


@dataclass
class ValidationResult:
    completeness_pct: float
    flags: list[ValidationFlag] = field(default_factory=list)
    field_comparison: dict[str, list[tuple[str, Optional[str], FieldConfidence]]] = field(default_factory=dict)

    @property
    def ready_for_underwriting(self) -> bool:
        """
        True only if there are no blocker-severity flags. This is a
        readiness signal, not a lending decision — it says "a human can
        now review this file efficiently," not "approve this loan."
        """
        return not any(f.severity == "blocker" for f in self.flags)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _similarity(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def redact(value: Optional[str], keep_last: int = 4) -> str:
    """
    Mask sensitive values for display (BVN, account numbers, ID numbers).
    Guardrail: never render full sensitive identifiers in UI/demo output.
    """
    if not value:
        return "—"
    digits = value.strip()
    if len(digits) <= keep_last:
        return "*" * len(digits)
    return "*" * (len(digits) - keep_last) + digits[-keep_last:]


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def check_completeness(documents: list[ExtractedDocument]) -> tuple[float, list[ValidationFlag]]:
    flags = []
    present_types = {d.doc_type for d in documents if d.file_readable}
    missing = [t for t in REQUIRED_DOCUMENTS if t not in present_types]

    for doc in documents:
        if not doc.file_readable:
            flags.append(ValidationFlag(
                code="unreadable_file",
                severity="blocker",
                message=f"{doc.doc_type}: file could not be processed (blank, corrupt, or wrong format). Re-upload required.",
            ))

    for missing_type in missing:
        flags.append(ValidationFlag(
            code="missing_document",
            severity="blocker",
            message=f"Missing required document: {missing_type.replace('_', ' ')}.",
        ))

    completeness_pct = round(
        100 * (len(REQUIRED_DOCUMENTS) - len(missing)) / len(REQUIRED_DOCUMENTS), 1
    )
    return completeness_pct, flags


def check_field_confidence(documents: list[ExtractedDocument]) -> list[ValidationFlag]:
    """
    Guardrail: surface every UNREADABLE/MISSING field explicitly instead of
    silently proceeding as if the value were known. Never let a guessed
    value pass through as if it were confidently extracted.
    """
    flags = []
    for doc in documents:
        for field_name, ef in doc.fields.items():
            if ef.confidence == FieldConfidence.UNREADABLE:
                flags.append(ValidationFlag(
                    code="low_confidence_extraction",
                    severity="warning",
                    message=f"{doc.doc_type}.{field_name}: could not be read reliably — needs manual review.",
                ))
            elif ef.confidence == FieldConfidence.MISSING:
                flags.append(ValidationFlag(
                    code="field_not_found",
                    severity="warning",
                    message=f"{doc.doc_type}.{field_name}: field not found on document.",
                ))
    return flags


def check_cross_document_consistency(
    documents: list[ExtractedDocument],
) -> tuple[dict[str, list[tuple[str, Optional[str], FieldConfidence]]], list[ValidationFlag]]:
    """
    Explicit, auditable comparison logic — deliberately NOT delegated to a
    single LLM "does this look consistent?" call. Every match/mismatch here
    can be explained by pointing at the actual field values.
    """
    flags = []
    comparison: dict[str, list[tuple[str, Optional[str], FieldConfidence]]] = {}

    def collect(field_name: str) -> list[tuple[str, Optional[str], FieldConfidence]]:
        out = []
        for doc in documents:
            ef = doc.fields.get(field_name)
            if ef is not None:
                out.append((doc.doc_type, ef.value, ef.confidence))
        return out

    for field_name, threshold in [
        ("full_name", NAME_MATCH_THRESHOLD),
        ("address", ADDRESS_MATCH_THRESHOLD),
        ("date_of_birth", 1.0),  # DOB should match exactly, allow no fuzz
    ]:
        entries = collect(field_name)
        comparison[field_name] = entries

        readable = [(doc_type, val) for doc_type, val, conf in entries if conf == FieldConfidence.OK and val]
        if len(readable) < 2:
            continue  # not enough readable values to compare

        base_doc, base_val = readable[0]
        for doc_type, val in readable[1:]:
            sim = _similarity(base_val, val)
            if sim < threshold:
                flags.append(ValidationFlag(
                    code="field_mismatch",
                    severity="blocker" if field_name == "date_of_birth" else "warning",
                    message=(
                        f"{field_name.replace('_', ' ')} mismatch between {base_doc} "
                        f"and {doc_type} (similarity {sim:.0%}) — needs manual review."
                    ),
                ))

    return comparison, flags


def check_identifier_formats(documents: list[ExtractedDocument]) -> list[ValidationFlag]:
    """
    Level 1 validation only: confirms extracted BVN/NIN values are
    structurally plausible (11 numeric digits). This does NOT confirm the
    number is real or belongs to the applicant — that requires a live
    NIBSS (BVN) or NIMC (NIN) verification API call, which is out of
    scope for this build. Never present this check's pass as if it were
    that confirmation; the officer-facing summary must label it as
    "format valid," not "verified."
    """
    flags = []
    identifier_fields = {
        "bvn": ("BVN", 11),
        "nin": ("NIN", 11),
    }

    for doc in documents:
        for field_key, ef in doc.fields.items():
            if field_key not in identifier_fields:
                continue
            label, expected_len = identifier_fields[field_key]
            if ef.confidence != FieldConfidence.OK or not ef.value:
                continue  # already flagged by check_field_confidence
            digits = ef.value.strip()
            if not digits.isdigit() or len(digits) != expected_len:
                flags.append(ValidationFlag(
                    code="invalid_identifier_format",
                    severity="blocker",
                    message=(
                        f"{doc.doc_type}.{field_key}: {label} '{redact(digits)}' is not "
                        f"a valid {expected_len}-digit number — format check failed. "
                        f"(Format-valid only; source-of-truth verification against "
                        f"NIBSS/NIMC is not performed by this pipeline.)"
                    ),
                ))
    return flags


def check_declared_vs_extracted(
    documents: list[ExtractedDocument],
    user_info: dict,
) -> list[ValidationFlag]:
    """
    Cross-checks what the applicant declared (name, address) against what
    Claude extracted from the documents. A mismatch here is a warning —
    it may be OCR noise or a nickname, but it warrants manual review.
    Gender is not cross-checked (documents rarely carry it in a
    comparable form).
    """
    flags = []
    checks = [
        ("full_name", user_info.get("name"), "declared name", NAME_MATCH_THRESHOLD, "warning"),
        ("address", user_info.get("address"), "declared address", ADDRESS_MATCH_THRESHOLD, "warning"),
    ]

    for field_name, declared_val, label, threshold, severity in checks:
        if not declared_val:
            continue
        for doc in documents:
            ef = doc.fields.get(field_name)
            if ef is None or ef.confidence != FieldConfidence.OK or not ef.value:
                continue
            sim = _similarity(declared_val, ef.value)
            if sim < threshold:
                flags.append(ValidationFlag(
                    code="declared_vs_extracted_mismatch",
                    severity=severity,
                    message=(
                        f"{doc.doc_type}.{field_name}: applicant's {label} "
                        f"({declared_val!r}) differs from extracted value "
                        f"({ef.value!r}) — similarity {sim:.0%}. Manual review required."
                    ),
                ))
    return flags


def check_recency(documents: list[ExtractedDocument], as_of: Optional[date] = None) -> list[ValidationFlag]:
    as_of = as_of or datetime.now().date()
    flags = []
    recency_sensitive = {"bank_statement", "proof_of_address"}

    for doc in documents:
        if doc.doc_type not in recency_sensitive or doc.document_date is None:
            continue
        age_days = (as_of - doc.document_date).days
        if age_days > MAX_DOCUMENT_AGE_DAYS:
            flags.append(ValidationFlag(
                code="stale_document",
                severity="warning",
                message=(
                    f"{doc.doc_type}: dated {doc.document_date.isoformat()}, "
                    f"{age_days} days old (limit {MAX_DOCUMENT_AGE_DAYS} days) — request an updated copy."
                ),
            ))
    return flags


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate_application(
    documents: list[ExtractedDocument],
    user_info: Optional[dict] = None,
) -> ValidationResult:
    """
    Runs the full check suite and returns a readiness report.
    Output is explicitly a completeness/consistency report — NOT a lending
    decision. Downstream code/UI must never relabel `ready_for_underwriting`
    as "approved."
    """
    completeness_pct, completeness_flags = check_completeness(documents)
    confidence_flags = check_field_confidence(documents)
    field_comparison, consistency_flags = check_cross_document_consistency(documents)
    identifier_flags = check_identifier_formats(documents)
    recency_flags = check_recency(documents)
    declared_flags = check_declared_vs_extracted(documents, user_info or {})

    all_flags = (
        completeness_flags + confidence_flags + consistency_flags
        + identifier_flags + recency_flags + declared_flags
    )

    return ValidationResult(
        completeness_pct=completeness_pct,
        flags=all_flags,
        field_comparison=field_comparison,
    )


if __name__ == "__main__":
    # Minimal smoke test with synthetic data only — no real applicant data,
    # ever, per guardrails.
    sample_docs = [
        ExtractedDocument(
            doc_type="government_id",
            fields={
                "full_name": ExtractedField("Chidinma A. Okoye", FieldConfidence.OK),
                "date_of_birth": ExtractedField("1994-03-12", FieldConfidence.OK),
            },
        ),
        ExtractedDocument(
            doc_type="bank_statement",
            fields={
                "full_name": ExtractedField("Chidinma Okoye", FieldConfidence.OK),
            },
            document_date=date(2026, 6, 1),
        ),
        ExtractedDocument(
            doc_type="proof_of_income",
            fields={"full_name": ExtractedField(None, FieldConfidence.UNREADABLE)},
        ),
        ExtractedDocument(
            doc_type="proof_of_address",
            fields={"address": ExtractedField("14 Allen Ave, Ikeja, Lagos", FieldConfidence.OK)},
            document_date=date(2026, 3, 1),
        ),
    ]

    result = validate_application(sample_docs)
    print(f"Completeness: {result.completeness_pct}%")
    print(f"Ready for underwriting review: {result.ready_for_underwriting}")
    for flag in result.flags:
        print(f"  [{flag.severity.upper()}] {flag.code}: {flag.message}")
