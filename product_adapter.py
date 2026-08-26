"""
product_adapter.py — Bridges credit_products.py to the existing pipeline.

This module translates a CreditProduct config into the exact data formats
consumed by extraction.py, validation.py, config.py, and bot.py — so those
files stay untouched. When the active product is set, every downstream
module can call the adapter instead of its own hardcoded constants.

Usage:
    from product_adapter import load_product, active_product_config

    # At startup or when user selects a product:
    load_product("MFB-SAL-001")

    # Then anywhere in the pipeline:
    cfg = active_product_config()
    cfg.field_lists          # replaces extraction.FIELD_LISTS
    cfg.required_documents   # replaces validation.REQUIRED_DOCUMENTS
    cfg.documents_order      # replaces config.REQUIRED_DOCUMENTS_ORDER
    cfg.name_match_threshold # replaces validation.NAME_MATCH_THRESHOLD
    ...
"""

from dataclasses import dataclass, field
from typing import Optional

from credit_products import (
    CreditProduct,
    PRODUCT_CATALOG,
    InstitutionType,
    list_products,
    get_product,
)


# ---------------------------------------------------------------------------
# Resolved config — the bridge between CreditProduct and existing modules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedProductConfig:
    """
    Flattened, pipeline-ready view of a CreditProduct.
    Every field maps 1:1 to a constant or structure the existing code reads.
    """
    product_code: str
    product_name: str
    institution_type: str

    # extraction.py — drop-in replacement for FIELD_LISTS
    field_lists: dict[str, list[str]]

    # validation.py — drop-in replacement for REQUIRED_DOCUMENTS
    required_documents: list[str]

    # config.py — drop-in replacement for REQUIRED_DOCUMENTS_ORDER
    documents_order: list[tuple[str, str]]

    # validation.py — drop-in replacements for threshold constants
    name_match_threshold: float
    address_match_threshold: float
    max_document_age_days: int
    completeness_threshold: float

    # Extended validation params (new capabilities beyond current code)
    dti_max: Optional[float]
    min_salary_inflows: int
    min_account_age_months: Optional[int]
    collateral_coverage_pct: Optional[float]

    # Workflow
    approval_stages: list[dict[str, str]]
    auto_approve_eligible: bool
    credit_bureau_check: bool
    alternative_data_scoring: bool

    # Loan parameters
    amount_range: tuple[float, float]
    tenor_range_days: tuple[int, int]
    interest_type: str
    interest_rate_range: tuple[float, float]
    currency: str
    repayment_frequency: str
    disbursement_method: str

    # Product characteristics
    collateral_required: bool
    guarantor_required: bool
    guarantor_count: int
    group_lending: bool
    progressive_lending: bool

    # Notification
    notification_channels: list[str]

    description: str


def resolve_product(product: CreditProduct) -> ResolvedProductConfig:
    """Convert a CreditProduct into a ResolvedProductConfig."""
    return ResolvedProductConfig(
        product_code=product.product_code,
        product_name=product.product_name,
        institution_type=product.institution_type.value,
        field_lists=product.get_field_lists(),
        required_documents=product.get_required_doc_types(),
        documents_order=[
            (doc.doc_type, doc.label) for doc in product.documents if doc.required
        ],
        name_match_threshold=product.validation.name_match_threshold,
        address_match_threshold=0.70,
        max_document_age_days=max(
            product.validation.statement_recency_days,
            product.validation.address_recency_days,
        ),
        completeness_threshold=product.completeness_threshold,
        dti_max=product.validation.dti_max,
        min_salary_inflows=product.validation.min_salary_inflows,
        min_account_age_months=product.validation.min_account_age_months,
        collateral_coverage_pct=product.validation.collateral_coverage_pct,
        approval_stages=[
            {"role": s.role, "action": s.action, "auto": str(s.auto).lower()}
            for s in product.approval_stages
        ],
        auto_approve_eligible=product.auto_approve_eligible,
        credit_bureau_check=product.credit_bureau_check,
        alternative_data_scoring=product.alternative_data_scoring,
        amount_range=(product.amount_min, product.amount_max),
        tenor_range_days=(product.tenor_min_days, product.tenor_max_days),
        interest_type=product.interest_type.value,
        interest_rate_range=(product.interest_rate_min, product.interest_rate_max),
        currency=product.currency,
        repayment_frequency=product.repayment_frequency.value,
        disbursement_method=product.disbursement_method.value,
        collateral_required=product.collateral_required,
        guarantor_required=product.guarantor_required,
        guarantor_count=product.guarantor_count,
        group_lending=product.group_lending,
        progressive_lending=product.progressive_lending,
        notification_channels=list(product.notification_channels),
        description=product.description,
    )


# ---------------------------------------------------------------------------
# Runtime product selection
# ---------------------------------------------------------------------------

_active_config: Optional[ResolvedProductConfig] = None


def load_product(code: str) -> ResolvedProductConfig:
    """
    Set the active product by code (e.g. "MFB-SAL-001").
    Returns the resolved config. Raises KeyError if code not found.
    """
    global _active_config
    product = get_product(code)
    _active_config = resolve_product(product)
    return _active_config


def active_product_config() -> ResolvedProductConfig:
    """
    Return the currently loaded product config.
    Falls back to the default (existing hardcoded behavior) if none loaded.
    """
    if _active_config is not None:
        return _active_config
    return _default_config()


def _default_config() -> ResolvedProductConfig:
    """
    Mirrors the exact hardcoded values in config.py, extraction.py, and
    validation.py — so calling active_product_config() without load_product()
    behaves identically to the current codebase. Zero behavior change.
    """
    return ResolvedProductConfig(
        product_code="DEFAULT",
        product_name="Standard Document Review",
        institution_type="default",
        field_lists={
            "passport_photo":  ["face_visible", "photo_usable"],
            "government_id":   ["full_name", "date_of_birth", "id_number", "nin", "address"],
            "bank_statement":  ["full_name", "account_number", "statement_period_end",
                                "bvn", "date_of_birth", "address"],
            "proof_of_income": ["full_name", "employer_or_business_name", "income_amount",
                                "date_of_birth"],
            "proof_of_address":["full_name", "address", "bill_date"],
        },
        required_documents=[
            "passport_photo", "government_id", "bank_statement",
            "proof_of_income", "proof_of_address",
        ],
        documents_order=[
            ("passport_photo",  "passport photograph (a clear selfie or recent passport-style photo of your face)"),
            ("government_id",   "government-issued ID (National ID/NIN slip, driver's license, or passport)"),
            ("bank_statement",  "bank statement (last 3-6 months)"),
            ("proof_of_income", "proof of income (payslip or business statement)"),
            ("proof_of_address","proof of address (a recent utility bill)"),
        ],
        name_match_threshold=0.82,
        address_match_threshold=0.70,
        max_document_age_days=90,
        completeness_threshold=75.0,
        dti_max=None,
        min_salary_inflows=3,
        min_account_age_months=None,
        collateral_coverage_pct=None,
        approval_stages=[
            {"role": "loan_officer", "action": "review_and_recommend", "auto": "false"},
        ],
        auto_approve_eligible=False,
        credit_bureau_check=True,
        alternative_data_scoring=False,
        amount_range=(0, 0),
        tenor_range_days=(0, 0),
        interest_type="flat",
        interest_rate_range=(0, 0),
        currency="NGN",
        repayment_frequency="monthly",
        disbursement_method="bank_transfer",
        collateral_required=False,
        guarantor_required=False,
        guarantor_count=0,
        group_lending=False,
        progressive_lending=False,
        notification_channels=["telegram"],
        description="Default configuration matching current hardcoded behavior.",
    )


# ---------------------------------------------------------------------------
# Convenience helpers for integration
# ---------------------------------------------------------------------------

def get_available_products_menu() -> list[dict[str, str]]:
    """
    Returns a list of {code, name, description} dicts suitable for
    building a Telegram inline keyboard or selection prompt.
    """
    return [
        {
            "code": p.product_code,
            "name": p.product_name,
            "type": p.institution_type.value.upper(),
            "description": p.description,
        }
        for p in PRODUCT_CATALOG.values()
    ]


def get_products_by_institution(inst_type: str) -> list[dict[str, str]]:
    """Filter product menu by institution type string (mfb/fintech/bank)."""
    try:
        it = InstitutionType(inst_type.lower())
    except ValueError:
        return []
    return [
        {
            "code": p.product_code,
            "name": p.product_name,
            "description": p.description,
        }
        for p in list_products(it)
    ]


def format_product_summary(code: str) -> str:
    """Human-readable summary of a product's requirements for bot display."""
    try:
        product = get_product(code)
    except KeyError:
        return f"Unknown product: {code}"

    cfg = resolve_product(product)

    lines = [
        f"*{cfg.product_name}* ({cfg.product_code})",
        f"_{cfg.description}_",
        "",
        f"Amount: {cfg.currency} {cfg.amount_range[0]:,.0f} - {cfg.amount_range[1]:,.0f}",
        f"Tenor: {cfg.tenor_range_days[0]} - {cfg.tenor_range_days[1]} days",
        f"Interest: {cfg.interest_rate_range[0]}% - {cfg.interest_rate_range[1]}% ({cfg.interest_type})",
        f"Repayment: {cfg.repayment_frequency}",
        "",
        f"Documents required ({len(cfg.required_documents)}):",
    ]
    for doc_type, label in cfg.documents_order:
        lines.append(f"  • {label}")

    if cfg.collateral_required:
        lines.append("\nCollateral: Required")
    if cfg.guarantor_required:
        lines.append(f"Guarantors: {cfg.guarantor_count} required")
    if cfg.group_lending:
        lines.append("Group lending: Yes")

    lines.append(f"\nApproval stages: {len(cfg.approval_stages)}")
    for stage in cfg.approval_stages:
        auto_tag = " (automated)" if stage["auto"] == "true" else ""
        lines.append(f"  > {stage['role'].replace('_', ' ').title()}: "
                      f"{stage['action'].replace('_', ' ')}{auto_tag}")

    return "\n".join(lines)
