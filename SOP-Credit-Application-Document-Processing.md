# SOP-009: Credit Application Document Processing Pipeline

**Frequency:** Per loan application | **Owner:** Credit/Loan Officer (system: Document Processing Bot) | **Time:** 3–5 minutes automated (vs. 1–3 hours manual)

## Purpose
Convert a raw bundle of applicant documents into an underwriting-ready file — completeness verified, cross-document fields checked for consistency, low-confidence extractions flagged for human review — without making or implying a credit decision at any stage.

**Hard scope boundary:** this pipeline outputs *readiness*, never *approval*. If any step below starts to resemble a risk score or a lending decision, that step is out of scope and gets removed.

---

## Procedure

### Phase 1: Intake (0–30 sec)
1. Applicant uploads documents via Telegram bot (or web form): government ID, bank statement, proof of income, proof of address, plus the loan application form itself.
2. System checks each file is a valid, non-empty image/PDF. Anything that fails this check is marked `file_readable = False` and never sent to extraction — no wasted model calls on garbage input.
3. Log receipt (filename, doc type claimed by uploader, timestamp) to Supabase. Do not log full file contents to any external service beyond the extraction call itself.

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

---

## Output
- Structured validation report (completeness %, flags, field comparison)
- Redacted document log in Supabase
- Turnaround-time measurement (manual baseline vs. pipeline time) for your demo's headline metric
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

**Field list per doc_type** (pass the relevant subset into the prompt):
- `government_id`: `full_name`, `date_of_birth`, `id_number`
- `bank_statement`: `full_name`, `account_number`, `statement_period_end`
- `proof_of_income`: `full_name`, `employer_or_business_name`, `income_amount`
- `proof_of_address`: `full_name`, `address`, `bill_date`

**Parsing note:** if the model returns anything that fails JSON parsing, or returns a field value that wasn't accompanied by `confidence: "ok"`, treat the whole document as `file_readable = False` and flag it — fail closed, not open.

---

## Guardrail Cross-Reference
Every phase above ties back to the guardrails already established:
- No credit decision at any phase (scope guardrail)
- Human-in-the-loop explicit at Phase 4 (scope guardrail)
- Synthetic data only for demo/testing (data guardrail)
- Redaction before display/logging (data guardrail)
- `unreadable`/`missing` never silently upgraded to a guessed value (model guardrail)
- Cross-document matching is auditable Python, not an opaque LLM judgment (model guardrail)
- Fail-closed on malformed extraction output (model guardrail)
