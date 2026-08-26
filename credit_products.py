"""
credit_products.py — Configurable credit product definitions.

Each financial institution (MFB, fintech, commercial bank) can define
one or more loan products. A product config tells CreditBot:
  - what documents to collect
  - what fields to extract from each
  - what validation rules to enforce
  - what completeness threshold qualifies as "ready"
  - what the approval workflow looks like

This module is additive — it does NOT modify extraction.py, validation.py,
or bot.py. When integrated, the active product config is loaded at runtime
and fed into the extraction/validation pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class InstitutionType(str, Enum):
    MFB = "mfb"
    FINTECH = "fintech"
    BANK = "bank"


class TargetSegment(str, Enum):
    INDIVIDUAL = "individual"
    SME = "sme"
    CORPORATE = "corporate"
    GROUP = "group"


class InterestType(str, Enum):
    FLAT = "flat"
    REDUCING_BALANCE = "reducing_balance"


class RepaymentFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    BULLET = "bullet"


class DisbursementMethod(str, Enum):
    BANK_TRANSFER = "bank_transfer"
    MOBILE_WALLET = "mobile_wallet"
    CHEQUE = "cheque"


class FlagSeverity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DocumentRequirement:
    doc_type: str
    label: str
    required: bool = True
    extraction_fields: list[str] = field(default_factory=list)


@dataclass
class ValidationRules:
    name_consistency: bool = True
    name_match_threshold: float = 0.82
    id_expiry_check: bool = True
    statement_recency_days: int = 90
    address_recency_days: int = 90
    min_salary_inflows: int = 3
    dti_max: Optional[float] = None
    min_account_age_months: Optional[int] = None
    collateral_coverage_pct: Optional[float] = None


@dataclass
class ApprovalStage:
    role: str
    action: str
    auto: bool = False


@dataclass
class CreditProduct:
    product_code: str
    product_name: str
    institution_type: InstitutionType
    target_segment: TargetSegment

    amount_min: float
    amount_max: float
    tenor_min_days: int
    tenor_max_days: int
    interest_type: InterestType
    interest_rate_min: float
    interest_rate_max: float
    currency: str = "NGN"

    collateral_required: bool = False
    guarantor_required: bool = False
    guarantor_count: int = 0
    group_lending: bool = False
    group_min_size: int = 0
    group_max_size: int = 0
    mandatory_savings_pct: float = 0.0
    progressive_lending: bool = False

    documents: list[DocumentRequirement] = field(default_factory=list)
    validation: ValidationRules = field(default_factory=ValidationRules)

    completeness_threshold: float = 75.0
    auto_approve_eligible: bool = False
    credit_bureau_check: bool = True
    alternative_data_scoring: bool = False

    approval_stages: list[ApprovalStage] = field(default_factory=list)

    notification_channels: list[str] = field(default_factory=lambda: ["telegram"])
    disbursement_method: DisbursementMethod = DisbursementMethod.BANK_TRANSFER
    repayment_frequency: RepaymentFrequency = RepaymentFrequency.MONTHLY

    description: str = ""

    def get_required_doc_types(self) -> list[str]:
        return [d.doc_type for d in self.documents if d.required]

    def get_field_lists(self) -> dict[str, list[str]]:
        return {d.doc_type: d.extraction_fields for d in self.documents}


# ---------------------------------------------------------------------------
# Pre-built product templates — Microfinance Banks
# ---------------------------------------------------------------------------

MFB_SALARY_ADVANCE = CreditProduct(
    product_code="MFB-SAL-001",
    product_name="Salary Advance",
    institution_type=InstitutionType.MFB,
    target_segment=TargetSegment.INDIVIDUAL,
    description="Short-term advance against verified salary for MFB customers.",
    amount_min=15_000,
    amount_max=3_000_000,
    tenor_min_days=30,
    tenor_max_days=180,
    interest_type=InterestType.FLAT,
    interest_rate_min=2.5,
    interest_rate_max=5.0,
    collateral_required=False,
    guarantor_required=True,
    guarantor_count=1,
    mandatory_savings_pct=10.0,
    documents=[
        DocumentRequirement("passport_photo", "Passport photograph",
                            extraction_fields=["face_visible", "photo_usable"]),
        DocumentRequirement("government_id", "Valid ID (NIN/Voter/Passport/License)",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin", "address"]),
        DocumentRequirement("proof_of_income", "Employment letter or recent payslip",
                            extraction_fields=["full_name", "employer_or_business_name", "income_amount",
                                               "date_of_birth"]),
        DocumentRequirement("bank_statement", "6-month bank statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "date_of_birth", "address"]),
        DocumentRequirement("proof_of_address", "Utility bill (within 3 months)",
                            extraction_fields=["full_name", "address", "bill_date"]),
        DocumentRequirement("guarantor_id", "Guarantor valid ID", required=False,
                            extraction_fields=["full_name", "id_number"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=90,
        address_recency_days=90,
        min_salary_inflows=3,
        dti_max=0.40,
    ),
    completeness_threshold=75.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("loan_officer", "review_and_recommend"),
        ApprovalStage("branch_manager", "approve_or_reject"),
    ],
    notification_channels=["telegram", "sms"],
    repayment_frequency=RepaymentFrequency.MONTHLY,
)

MFB_GROUP_LOAN = CreditProduct(
    product_code="MFB-GRP-001",
    product_name="Group Loan",
    institution_type=InstitutionType.MFB,
    target_segment=TargetSegment.GROUP,
    description="Grameen-model group loan with peer cross-guarantees.",
    amount_min=10_000,
    amount_max=500_000,
    tenor_min_days=30,
    tenor_max_days=365,
    interest_type=InterestType.FLAT,
    interest_rate_min=2.1,
    interest_rate_max=4.0,
    collateral_required=False,
    guarantor_required=False,
    group_lending=True,
    group_min_size=5,
    group_max_size=10,
    mandatory_savings_pct=10.0,
    progressive_lending=True,
    documents=[
        DocumentRequirement("passport_photo", "Passport photograph",
                            extraction_fields=["face_visible", "photo_usable"]),
        DocumentRequirement("government_id", "Valid ID",
                            extraction_fields=["full_name", "date_of_birth", "id_number"]),
        DocumentRequirement("proof_of_address", "Utility bill or tenancy agreement",
                            extraction_fields=["full_name", "address", "bill_date"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        address_recency_days=90,
    ),
    completeness_threshold=70.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("loan_officer", "verify_group_and_recommend"),
        ApprovalStage("branch_manager", "approve_or_reject"),
    ],
    notification_channels=["sms"],
    repayment_frequency=RepaymentFrequency.WEEKLY,
)

MFB_SME_LOAN = CreditProduct(
    product_code="MFB-SME-001",
    product_name="SME Business Loan",
    institution_type=InstitutionType.MFB,
    target_segment=TargetSegment.SME,
    description="Working capital or expansion finance for micro and small enterprises.",
    amount_min=100_000,
    amount_max=10_000_000,
    tenor_min_days=90,
    tenor_max_days=540,
    interest_type=InterestType.REDUCING_BALANCE,
    interest_rate_min=2.5,
    interest_rate_max=4.0,
    collateral_required=False,
    guarantor_required=True,
    guarantor_count=2,
    documents=[
        DocumentRequirement("passport_photo", "Passport photograph",
                            extraction_fields=["face_visible", "photo_usable"]),
        DocumentRequirement("government_id", "Valid ID (NIN/Voter/Passport/License)",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin", "address"]),
        DocumentRequirement("bank_statement", "12-month bank statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "address"]),
        DocumentRequirement("proof_of_income", "Business registration or financial records",
                            extraction_fields=["full_name", "employer_or_business_name", "income_amount"]),
        DocumentRequirement("proof_of_address", "Utility bill (within 3 months)",
                            extraction_fields=["full_name", "address", "bill_date"]),
        DocumentRequirement("business_registration", "CAC certificate", required=False,
                            extraction_fields=["business_name", "rc_number", "registration_date"]),
        DocumentRequirement("guarantor_id", "Guarantor 1 valid ID", required=False,
                            extraction_fields=["full_name", "id_number"]),
        DocumentRequirement("guarantor_id_2", "Guarantor 2 valid ID", required=False,
                            extraction_fields=["full_name", "id_number"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=90,
        address_recency_days=90,
        min_salary_inflows=6,
        dti_max=0.50,
    ),
    completeness_threshold=75.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("loan_officer", "review_and_recommend"),
        ApprovalStage("branch_manager", "approve_or_reject"),
    ],
    notification_channels=["telegram", "sms"],
    repayment_frequency=RepaymentFrequency.MONTHLY,
)

MFB_AGRIC_LOAN = CreditProduct(
    product_code="MFB-AGR-001",
    product_name="Agricultural Loan",
    institution_type=InstitutionType.MFB,
    target_segment=TargetSegment.INDIVIDUAL,
    description="Seasonal credit for agricultural inputs, aligned with harvest cycles.",
    amount_min=50_000,
    amount_max=5_000_000,
    tenor_min_days=120,
    tenor_max_days=270,
    interest_type=InterestType.FLAT,
    interest_rate_min=2.0,
    interest_rate_max=3.5,
    collateral_required=False,
    guarantor_required=True,
    guarantor_count=1,
    documents=[
        DocumentRequirement("passport_photo", "Passport photograph",
                            extraction_fields=["face_visible", "photo_usable"]),
        DocumentRequirement("government_id", "Valid ID",
                            extraction_fields=["full_name", "date_of_birth", "id_number"]),
        DocumentRequirement("bank_statement", "6-month bank statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end", "bvn"]),
        DocumentRequirement("proof_of_address", "Utility bill or community leader letter",
                            extraction_fields=["full_name", "address", "bill_date"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=90,
    ),
    completeness_threshold=70.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("loan_officer", "site_visit_and_recommend"),
        ApprovalStage("branch_manager", "approve_or_reject"),
    ],
    notification_channels=["sms"],
    repayment_frequency=RepaymentFrequency.QUARTERLY,
)


# ---------------------------------------------------------------------------
# Pre-built product templates — Fintechs
# ---------------------------------------------------------------------------

FINTECH_INSTANT_LOAN = CreditProduct(
    product_code="FIN-INS-001",
    product_name="Instant Personal Loan",
    institution_type=InstitutionType.FINTECH,
    target_segment=TargetSegment.INDIVIDUAL,
    description="Unsecured instant personal loan disbursed via mobile app.",
    amount_min=1_000,
    amount_max=3_000_000,
    tenor_min_days=7,
    tenor_max_days=365,
    interest_type=InterestType.FLAT,
    interest_rate_min=2.5,
    interest_rate_max=30.0,
    progressive_lending=True,
    documents=[
        DocumentRequirement("government_id", "Valid ID (NIN slip, voter card, or passport)",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin"]),
        DocumentRequirement("selfie", "Selfie / liveness check",
                            extraction_fields=["face_visible", "liveness_passed"]),
        DocumentRequirement("bank_statement", "3-month bank statement (or linked account)",
                            required=False,
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "average_balance", "total_credits"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=60,
    ),
    completeness_threshold=60.0,
    auto_approve_eligible=True,
    credit_bureau_check=True,
    alternative_data_scoring=True,
    approval_stages=[
        ApprovalStage("ml_scoring_engine", "auto_approve_or_reject", auto=True),
    ],
    notification_channels=["telegram", "sms", "email"],
    disbursement_method=DisbursementMethod.BANK_TRANSFER,
    repayment_frequency=RepaymentFrequency.MONTHLY,
)

FINTECH_BNPL = CreditProduct(
    product_code="FIN-BNPL-001",
    product_name="Buy Now Pay Later",
    institution_type=InstitutionType.FINTECH,
    target_segment=TargetSegment.INDIVIDUAL,
    description="Point-of-sale credit split into installments.",
    amount_min=5_000,
    amount_max=1_000_000,
    tenor_min_days=14,
    tenor_max_days=180,
    interest_type=InterestType.FLAT,
    interest_rate_min=0.0,
    interest_rate_max=10.0,
    progressive_lending=True,
    documents=[
        DocumentRequirement("government_id", "Valid ID",
                            extraction_fields=["full_name", "date_of_birth", "id_number"]),
        DocumentRequirement("selfie", "Selfie / liveness check",
                            extraction_fields=["face_visible", "liveness_passed"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
    ),
    completeness_threshold=50.0,
    auto_approve_eligible=True,
    credit_bureau_check=True,
    alternative_data_scoring=True,
    approval_stages=[
        ApprovalStage("ml_scoring_engine", "auto_approve_or_reject", auto=True),
    ],
    notification_channels=["sms", "email"],
    disbursement_method=DisbursementMethod.BANK_TRANSFER,
    repayment_frequency=RepaymentFrequency.BIWEEKLY,
)

FINTECH_SME_LOAN = CreditProduct(
    product_code="FIN-SME-001",
    product_name="SME Working Capital",
    institution_type=InstitutionType.FINTECH,
    target_segment=TargetSegment.SME,
    description="Working capital loan for small businesses based on transaction history.",
    amount_min=50_000,
    amount_max=6_000_000,
    tenor_min_days=30,
    tenor_max_days=365,
    interest_type=InterestType.REDUCING_BALANCE,
    interest_rate_min=2.5,
    interest_rate_max=9.0,
    documents=[
        DocumentRequirement("government_id", "Owner valid ID",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin"]),
        DocumentRequirement("bank_statement", "6-month business bank statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "average_balance", "total_credits", "total_debits"]),
        DocumentRequirement("business_registration", "CAC certificate",
                            extraction_fields=["business_name", "rc_number", "registration_date"]),
        DocumentRequirement("selfie", "Selfie / liveness check",
                            extraction_fields=["face_visible", "liveness_passed"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=60,
        min_salary_inflows=6,
        min_account_age_months=6,
    ),
    completeness_threshold=70.0,
    auto_approve_eligible=False,
    credit_bureau_check=True,
    alternative_data_scoring=True,
    approval_stages=[
        ApprovalStage("ml_scoring_engine", "pre_score", auto=True),
        ApprovalStage("credit_analyst", "review_and_approve"),
    ],
    notification_channels=["telegram", "sms", "email"],
    repayment_frequency=RepaymentFrequency.MONTHLY,
)


# ---------------------------------------------------------------------------
# Pre-built product templates — Commercial / Investment Banks
# ---------------------------------------------------------------------------

BANK_TERM_LOAN = CreditProduct(
    product_code="BNK-TRM-001",
    product_name="SME Term Loan",
    institution_type=InstitutionType.BANK,
    target_segment=TargetSegment.SME,
    description="Fixed-amount business loan repaid over a set schedule.",
    amount_min=500_000,
    amount_max=500_000_000,
    tenor_min_days=90,
    tenor_max_days=2555,
    interest_type=InterestType.REDUCING_BALANCE,
    interest_rate_min=18.0,
    interest_rate_max=32.0,
    collateral_required=True,
    guarantor_required=True,
    guarantor_count=1,
    documents=[
        DocumentRequirement("passport_photo", "Passport photograph (directors)",
                            extraction_fields=["face_visible", "photo_usable"]),
        DocumentRequirement("government_id", "Valid ID (directors)",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin", "address"]),
        DocumentRequirement("business_registration", "CAC registration certificate",
                            extraction_fields=["business_name", "rc_number", "registration_date"]),
        DocumentRequirement("board_resolution", "Board resolution authorizing borrowing",
                            extraction_fields=["company_name", "resolution_date", "loan_amount_authorized",
                                               "signatories"]),
        DocumentRequirement("memorandum_articles", "Memorandum and Articles of Association",
                            extraction_fields=["company_name", "authorized_share_capital"]),
        DocumentRequirement("tax_clearance", "Tax clearance certificate (3 years)",
                            extraction_fields=["company_name", "tin_number", "clearance_year",
                                               "tax_amount_paid"]),
        DocumentRequirement("audited_financials", "Audited financial statements (2-3 years)",
                            extraction_fields=["company_name", "financial_year", "total_revenue",
                                               "net_profit", "total_assets", "total_liabilities",
                                               "current_assets", "current_liabilities"]),
        DocumentRequirement("bank_statement", "12-month bank statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "average_balance", "total_credits", "total_debits"]),
        DocumentRequirement("cash_flow_projection", "Cash flow projection (loan tenor + 1 year)",
                            extraction_fields=["company_name", "projection_period",
                                               "projected_revenue", "projected_net_cash_flow"]),
        DocumentRequirement("collateral_title", "Certificate of Occupancy / title deed",
                            extraction_fields=["property_address", "owner_name", "title_number",
                                               "registered_date"]),
        DocumentRequirement("property_valuation", "Property valuation report", required=False,
                            extraction_fields=["property_address", "market_value", "forced_sale_value",
                                               "valuation_date", "valuer_name"]),
        DocumentRequirement("insurance_policy", "Insurance policy (assigned to bank)", required=False,
                            extraction_fields=["policy_number", "insured_name", "coverage_amount",
                                               "expiry_date"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=90,
        address_recency_days=90,
        dti_max=0.60,
        collateral_coverage_pct=1.5,
    ),
    completeness_threshold=80.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("relationship_manager", "prepare_credit_memo"),
        ApprovalStage("credit_analyst", "financial_analysis"),
        ApprovalStage("risk_officer", "risk_assessment"),
        ApprovalStage("credit_committee", "approve_or_reject"),
    ],
    notification_channels=["email", "sms"],
    repayment_frequency=RepaymentFrequency.MONTHLY,
)

BANK_WORKING_CAPITAL = CreditProduct(
    product_code="BNK-WCL-001",
    product_name="Working Capital Facility",
    institution_type=InstitutionType.BANK,
    target_segment=TargetSegment.SME,
    description="Short-term revolving facility for day-to-day operations.",
    amount_min=1_000_000,
    amount_max=200_000_000,
    tenor_min_days=90,
    tenor_max_days=365,
    interest_type=InterestType.REDUCING_BALANCE,
    interest_rate_min=20.0,
    interest_rate_max=30.0,
    collateral_required=True,
    guarantor_required=True,
    guarantor_count=1,
    documents=[
        DocumentRequirement("government_id", "Valid ID (directors)",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin"]),
        DocumentRequirement("business_registration", "CAC certificate",
                            extraction_fields=["business_name", "rc_number", "registration_date"]),
        DocumentRequirement("tax_clearance", "Tax clearance certificate",
                            extraction_fields=["company_name", "tin_number", "clearance_year"]),
        DocumentRequirement("audited_financials", "Audited financial statements (2 years)",
                            extraction_fields=["company_name", "financial_year", "total_revenue",
                                               "net_profit", "total_assets", "total_liabilities",
                                               "current_assets", "current_liabilities"]),
        DocumentRequirement("bank_statement", "6-month bank statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "average_balance", "total_credits"]),
        DocumentRequirement("collateral_title", "Collateral documentation",
                            extraction_fields=["property_address", "owner_name", "title_number"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        statement_recency_days=90,
        dti_max=0.55,
        collateral_coverage_pct=1.25,
    ),
    completeness_threshold=80.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("relationship_manager", "prepare_credit_memo"),
        ApprovalStage("credit_analyst", "financial_analysis"),
        ApprovalStage("credit_committee", "approve_or_reject"),
    ],
    notification_channels=["email"],
    repayment_frequency=RepaymentFrequency.MONTHLY,
)

BANK_SALARY_LOAN = CreditProduct(
    product_code="BNK-SAL-001",
    product_name="Personal Salary Loan",
    institution_type=InstitutionType.BANK,
    target_segment=TargetSegment.INDIVIDUAL,
    description="Consumer credit secured against salary domiciliation.",
    amount_min=100_000,
    amount_max=20_000_000,
    tenor_min_days=90,
    tenor_max_days=1825,
    interest_type=InterestType.REDUCING_BALANCE,
    interest_rate_min=18.0,
    interest_rate_max=28.0,
    collateral_required=False,
    guarantor_required=False,
    documents=[
        DocumentRequirement("passport_photo", "Passport photograph",
                            extraction_fields=["face_visible", "photo_usable"]),
        DocumentRequirement("government_id", "Valid ID",
                            extraction_fields=["full_name", "date_of_birth", "id_number", "nin", "address"]),
        DocumentRequirement("proof_of_income", "Employment letter and payslip",
                            extraction_fields=["full_name", "employer_or_business_name", "income_amount",
                                               "date_of_birth"]),
        DocumentRequirement("bank_statement", "6-month salary account statement",
                            extraction_fields=["full_name", "account_number", "statement_period_end",
                                               "bvn", "date_of_birth", "address"]),
        DocumentRequirement("proof_of_address", "Utility bill",
                            extraction_fields=["full_name", "address", "bill_date"]),
    ],
    validation=ValidationRules(
        name_consistency=True,
        id_expiry_check=True,
        statement_recency_days=60,
        address_recency_days=90,
        min_salary_inflows=6,
        dti_max=0.33,
    ),
    completeness_threshold=80.0,
    credit_bureau_check=True,
    approval_stages=[
        ApprovalStage("loan_officer", "verify_employment_and_recommend"),
        ApprovalStage("branch_manager", "approve_or_reject"),
    ],
    notification_channels=["email", "sms"],
    repayment_frequency=RepaymentFrequency.MONTHLY,
)


# ---------------------------------------------------------------------------
# Product registry
# ---------------------------------------------------------------------------

PRODUCT_CATALOG: dict[str, CreditProduct] = {
    p.product_code: p
    for p in [
        # MFB products
        MFB_SALARY_ADVANCE,
        MFB_GROUP_LOAN,
        MFB_SME_LOAN,
        MFB_AGRIC_LOAN,
        # Fintech products
        FINTECH_INSTANT_LOAN,
        FINTECH_BNPL,
        FINTECH_SME_LOAN,
        # Bank products
        BANK_TERM_LOAN,
        BANK_WORKING_CAPITAL,
        BANK_SALARY_LOAN,
    ]
}


def get_product(code: str) -> CreditProduct:
    """Look up a product by its code. Raises KeyError if not found."""
    return PRODUCT_CATALOG[code]


def list_products(institution_type: Optional[InstitutionType] = None) -> list[CreditProduct]:
    """Return all products, optionally filtered by institution type."""
    products = list(PRODUCT_CATALOG.values())
    if institution_type is not None:
        products = [p for p in products if p.institution_type == institution_type]
    return products


def list_products_by_segment(segment: TargetSegment) -> list[CreditProduct]:
    """Return all products targeting a specific borrower segment."""
    return [p for p in PRODUCT_CATALOG.values() if p.target_segment == segment]
