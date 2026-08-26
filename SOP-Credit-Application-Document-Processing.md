# SOP-009: Credit Application Document Processing Pipeline

**Frequency:** Per loan application | **Owner:** Credit/Loan Officer (system: Document Processing Bot) | **Time:** 3–5 minutes automated (vs. 1–3 hours manual)

## Purpose
Convert a raw bundle of applicant documents into an underwriting-ready file — completeness verified, cross-document fields checked for consistency, low-confidence extractions flagged for human review — without making or implying a credit decision at any stage.

**Hard scope boundary:** this pipeline outputs *readiness*, never *approval*. If any step below starts to resemble a risk score or a lending decision, that step is out of scope and gets removed.

---

## Procedure

### Phase 1A: Product Selection (0–15 sec)
1. Applicant sends `/start` to the Telegram bot.
2. Bot presents institution type selection: Microfinance Bank (MFB), Fintech, or Commercial Bank.
3. Based on the selected institution type, bot displays available loan products with summaries (amount range, tenor, interest rate, required documents).
4. Applicant selects a product. The system loads product-specific configuration via `product_adapter.load_product()`, which determines document requirements, validation thresholds, and approval workflow for the rest of the session.

**Decision gate:** product must be selected before personal info collection begins. If the applicant cancels at this stage, no data is saved (no PII collected yet).

### Phase 1B: Personal Information Collection (15–60 sec)
1. Bot collects: full name, address, gender, phone number (E.164), and email.
2. All PII is encrypted at field level (AES-256-GCM) before storage.
3. If the applicant cancels or times out after providing at least a name, phone, or email, the session is captured as a **lead** for officer follow-up (see Phase 6).

### Phase 1C: Document Intake (30 sec – 2 min)
1. Bot prompts for each document required by the selected product (order and labels from `product_adapter.active_product_config().documents_order`).
2. System checks each file is a valid, non-empty image. Anything that fails this check is marked `file_readable = False` and never sent to extraction.
3. Log receipt to Supabase. Do not log full file contents to any external service beyond the extraction call itself.

**Decision gate:** if zero valid files were received, stop and return "no documents received" — do not proceed to extraction.

### Phase 2: Extraction (30 sec – 2 min)
1. For each valid file, call Claude's vision API with the extraction prompt (below), one document at a time.
2. Parse the model's structured JSON response into `ExtractedField` objects — every field carries a `confidence` of `ok`, `unreadable`, or `missing`. No field is ever stored as a guessed value with `ok` confidence unless the model actually read it.
3. Assemble each document's fields into an `ExtractedDocument`, including `document_date` where applicable (bank statement, utility bill).
4. Redact and store: mask BVN/account numbers to last 4 digits (`redact()`) before anything is written to a log or shown on screen.

**Decision gate:** if extraction fails entirely for a file (API error, timeout), mark that document `file_readable = False` and continue with the rest — one failed document shouldn't block the whole bundle.

### Phase 3: Validation (near-instant, deterministic)
Run `validate_application(documents)` from `validation.py`:
1. `check_completeness` — confirms all 4 required doc types are present and readable.
2. `check_field_confidence` — surfaces every `unreadable`/`missing` field as a warning.
3. `check_cross_document_consistency` — plain-Python similarity matching on name, DOB, address across documents. DOB mismatch = blocker; name/address mismatch = warning.
4. `check_recency` — flags bank statement/utility bill older than 90 days.
5. Compile `ValidationResult`: `completeness_pct`, `flags`, `field_comparison`, `ready_for_underwriting`.

**Decision gate:** any `blocker`-severity flag → `ready_for_underwriting = False`. This is a readiness signal only — it is never relabeled "approved" or "rejected" anywhere downstream.

### Phase 4: Officer Review Output (near-instant)
1. Render a summary: completeness %, list of flags grouped by severity, side-by-side field comparison table (redacted values only).
2. Present this to the officer as *"file prepared for your review"* — explicit human-in-the-loop language, never decision language.
3. Officer can request re-upload of any flagged/missing document directly from the same interface.

**Decision gate:** the officer, not the system, decides whether the file proceeds to underwriting.

### Phase 5: Handoff & Logging
1. On officer sign-off, mark the file `underwriting_ready = true` in Supabase with a timestamp.
2. Log processing time (intake → officer sign-off) — this is your turnaround-time metric for the BuildFest demo.
3. Purge raw document images after a defined retention window (or immediately after the demo, for competition data).

### Phase 6: Lead Capture on Abandon
1. When an applicant cancels (`/cancel`) or their session times out after providing at least one PII field (name, phone, or email), the bot calls `_capture_lead()`.
2. The lead record includes: all collected PII (encrypted), selected product (if any), the conversation stage reached, and the abandon source (`cancelled` or `timeout`).
3. Lead is saved to the `leads` table via `storage.save_lead()` and an audit event (`lead_captured`) is logged.
4. Lead status defaults to `new`. Officers can update status to `contacted`, `converted`, or `closed` via `storage.update_lead_status()`.

**Guardrail:** leads are only created when meaningful data exists. A session abandoned before any PII is entered produces no lead record.

### Phase 7: Analytics Dashboard
1. A separate Flask service (`dashboard.py`) provides real-time analytics for officers and management.
2. KPIs: total applications, processed count, average completeness, readiness rate, average turnaround time — each with 15-day trend comparison.
3. Charts: volume over time, readiness split, flag distribution, notification channels, completeness distribution, gender breakdown, turnaround trends, product popularity, product readiness, institution type split.
4. Tables: recent applications (with product column), leads (with status/source/stage), audit trail.
5. Insights panel: attention items (flagged applications, below-threshold completeness, delivery rate issues) and quick insights (volume trends, lead counts, product activity).
6. Falls back to synthetic demo data when Supabase is not configured.

**Guardrail:** dashboard shows PII only in the leads table (officer-facing view). Application table intentionally omits PII columns.

---

## Output
- Structured validation report (completeness %, flags, field comparison)
- Product-specific document requirements and approval workflow
- Encrypted PII storage with field-level AES-256-GCM
- Lead records from abandoned sessions for officer follow-up
- Analytics dashboard with KPIs, charts, and trend analysis
- Redacted document log in Supabase
- Turnaround-time measurement (pipeline time vs. manual baseline)
- Explicit non-decision: no approval/rejection/score is ever produced by this pipeline

---

## Extraction Prompt (Claude vision call — Phase 2)

Use this as the system/user prompt for each document image. One call per document, not one call for the whole bundle — keeps failures isolated and outputs predictable.

```
SYSTEM:
You are a document field extractor for a loan application intake system.
Your only job is to read the provided document image and extract specific
fields as structured JSON. You do not evaluate creditworthiness, make
recommendations, or assess the applicant in any way.

Rules:
1. If a field is not legible or not confidently readable, set its value to
   null and confidence to "unreadable". NEVER guess or infer a value you
   cannot actually read on the document.
2. If a field does not appear on this document type at all, set value to
   null and confidence to "missing".
3. If you can read the field clearly, set confidence to "ok" and provide
   the exact value as printed (do not reformat names, do not normalize
   dates beyond ISO 8601 YYYY-MM-DD).
4. Return ONLY valid JSON, no commentary, no markdown fences.

USER:
Document type: {doc_type}   # one of: government_id, bank_statement,
                             # proof_of_income, proof_of_address

Extract the following fields for this document type:
{field_list_for_doc_type}

Return JSON in exactly this shape:
{
  "doc_type": "{doc_type}",
  "file_readable": true,
  "document_date": "YYYY-MM-DD or null",
  "fields": {
    "full_name": {"value": "...", "confidence": "ok|unreadable|missing"},
    "date_of_birth": {"value": "...", "confidence": "ok|unreadable|missing"},
    "address": {"value": "...", "confidence": "ok|unreadable|missing"}
    // include only the fields relevant to this doc_type
  }
}

[document image attached]
```

**Field list per doc_type** (default set — product-specific configs may extend these via `CreditProduct.get_field_lists()`):
- `passport_photo`: `face_visible`, `photo_usable`
- `government_id`: `full_name`, `date_of_birth`, `id_number`, `nin`, `address`
- `bank_statement`: `full_name`, `account_number`, `statement_period_end`, `bvn`, `date_of_birth`, `address`
- `proof_of_income`: `full_name`, `employer_or_business_name`, `income_amount`, `date_of_birth`
- `proof_of_address`: `full_name`, `address`, `bill_date`

**Parsing note:** if the model returns anything that fails JSON parsing, or returns a field value that wasn't accompanied by `confidence: "ok"`, treat the whole document as `file_readable = False` and flag it — fail closed, not open.

---

## Guardrail Cross-Reference
Every phase above ties back to the guardrails already established:
- No credit decision at any phase (scope guardrail)
- Human-in-the-loop explicit at Phase 4 (scope guardrail)
- Synthetic data only for demo/testing (data guardrail)
- Redaction before display/logging (data guardrail)
- Field-level PII encryption (AES-256-GCM) for all stored personal data (data guardrail)
- `unreadable`/`missing` never silently upgraded to a guessed value (model guardrail)
- Cross-document matching is auditable Python, not an opaque LLM judgment (model guardrail)
- Fail-closed on malformed extraction output (model guardrail)
- Lead capture only fires when meaningful PII exists; no empty leads (data guardrail)
- Dashboard omits PII from application table; leads table is officer-facing only (data guardrail)
- Product adapter defaults to existing behavior when no product is loaded; zero-behavior-change migration (operational guardrail)
