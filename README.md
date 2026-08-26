# Credit Application Document Processing Bot

Telegram bot that collects a loan applicant's documents, extracts structured
fields with Claude, and runs deterministic checks (completeness, cross-document
consistency, identifier format, recency) to prepare a **readiness report** for
a human credit officer. It does not approve, reject, or score anything —
see `SOP-Credit-Application-Document-Processing.md` for the full pipeline spec
and guardrails this build follows.

## Setup

1. **Get a Telegram bot token**
   Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts, copy the token it gives you.

2. **Get an Anthropic API key**
   https://console.anthropic.com/settings/keys

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # edit .env with your real keys
   ```
   The bot reads these via `os.environ` — if you deploy on Railway, set them
   as project environment variables instead of shipping `.env`.

5. **Supabase tables** — required for persistence, dashboard, and lead capture:
   ```sql
   -- Applications
   create table applications (
     id uuid primary key default gen_random_uuid(),
     telegram_user_id bigint not null,
     reference_number text unique not null,
     officer_code text,
     declared_name text,
     declared_address text,
     declared_gender text,
     phone_number text,
     email text,
     product_code text,
     product_name text,
     status text not null default 'in_progress',
     completeness_pct numeric,
     ready_for_underwriting boolean,
     flags jsonb,
     turnaround_seconds numeric,
     notified_at timestamptz,
     notification_channel text,
     notification_status text,
     created_at timestamptz not null default now(),
     processed_at timestamptz
   );
   create index on applications(officer_code);
   create index on applications(reference_number);

   -- Immutable audit log
   create table audit_log (
     id uuid primary key default gen_random_uuid(),
     application_id uuid references applications(id),
     event_type text not null,
     actor text not null,
     payload jsonb,
     created_at timestamptz not null default now()
   );
   revoke update, delete on audit_log from anon, authenticated;

   -- Leads (abandoned/timed-out sessions)
   create table leads (
     id uuid primary key default gen_random_uuid(),
     telegram_user_id bigint not null,
     declared_name text,
     phone_number text,
     email text,
     declared_address text,
     declared_gender text,
     product_code text,
     product_name text,
     institution_type text,
     stage_reached text,
     source text not null default 'abandoned',
     status text not null default 'new',
     notes text,
     followed_up_at timestamptz,
     created_at timestamptz default now()
   );
   create index on leads(status);
   create index on leads(created_at desc);
   ```
   PII columns (name, address, phone, email) are encrypted at rest
   using AES-256-GCM via `encryption.py`. Raw document images are never
   stored.

6. **Run**
   ```bash
   export ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=...
   python bot.py
   ```
   Open Telegram, find your bot, send `/start`.

## What actually works right now

- **Multi-product credit flow**: `/start` → select institution type (MFB /
  Fintech / Bank) → choose a loan product → personal info collection →
  product-specific documents collected as photos → live Claude vision
  extraction per document → deterministic validation → officer summary.
- 10 pre-built loan products across MFB, Fintech, and Bank institution
  types, each with its own document requirements, validation thresholds,
  and approval workflow (see `credit_products.py`).
- Completeness, cross-document name/DOB/address consistency, BVN/NIN
  **format** validation, and document recency checks — all real, all tested.
- Turnaround time measured and shown in the summary.
- **Lead capture**: when an applicant fills personal details then cancels or
  times out, their partial data is saved as a lead for officer follow-up.
- **Analytics dashboard** at `/` — KPIs, volume/readiness/flag charts,
  product analytics, leads table, and audit trail. Runs as a separate
  Railway service.
- **Field-level PII encryption** (AES-256-GCM) on name, address, phone,
  email in Supabase — decrypted only for officer-facing views.

## What this build does NOT do (be upfront about this in your pitch)

- **No source-of-truth identity verification.** BVN/NIN checks confirm
  *format* (11 digits), not that the number is real or belongs to the
  applicant. Actual verification requires licensed NIBSS/NIMC API access.
- **No credit decision of any kind.** `ready_for_underwriting` is a
  readiness signal for a human officer, never an approval/rejection.
- **Only accepts photo uploads**, not PDF documents, in this build.

## Testing without a live Telegram bot

Generate synthetic test documents (never real applicant data):
```bash
python tools/generate_test_documents.py
```
This writes two sets to `test_documents/`:
- `clean/` — one persona, all fields consistent — should come back ~100%
  complete with `ready_for_underwriting=True`.
- `flagged/` — same persona, deliberate DOB mismatch, name-spelling
  mismatch, address mismatch, and a stale bank statement — proves the
  consistency and recency checks actually catch problems.

Every generated image is watermarked "SPECIMEN — SYNTHETIC TEST DOCUMENT"
and uses fictitious names/numbers/institutions.

Run the real pipeline against either set without touching Telegram:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python tools/run_local_test.py test_documents/clean
python tools/run_local_test.py test_documents/flagged
```
This calls the live Claude API (4 calls per run) and prints the same
summary + field comparison table the bot would send — useful for fast
iteration before recording your demo video.

## Testing without real applicant data

Never use real BVN/NIN/bank statements — including your own — for the
public demo. Generate synthetic sample documents (fake names, fake ID
numbers, mocked bank statement layout) for every test run and for the
demo video itself.

## Architecture

```
Telegram user
   |
   v
bot.py                  conversation flow, product selection, photo intake
   |                        |--- cancel/timeout ---> _capture_lead() ---> leads table
   v                                                                        |
credit_products.py      10 loan products (MFB, Fintech, Bank)               |
product_adapter.py      bridges product config to pipeline modules           |
   |                                                                        |
   v                                                                        |
extraction.py           Claude vision call, per-product field lists          |
   |                                                                        |
   v                                                                        |
validation.py           deterministic checks (completeness, consistency)     |
   |                                                                        |
   v                                                                        |
bot.py                  readiness summary forwarded to officer               |
   |                                                                        |
   v                                                                        v
storage.py              encrypted persistence (applications, audit)    leads table
encryption.py           AES-256-GCM field-level PII encryption
   |                                                                        |
   v                                                                        v
dashboard.py            Flask analytics dashboard (KPIs, charts, leads, audit)
```

## Swapping to WhatsApp later

The extraction and validation layers are transport-agnostic — `bot.py` is
the only file tied to Telegram's API. A WhatsApp version would replace
`bot.py`'s handlers with WhatsApp Cloud API webhook handlers, reusing
`extraction.py` and `validation.py` unchanged. Not attempted here given
WhatsApp Business verification/template-approval lead times don't fit a
3-day window.
