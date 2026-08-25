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

5. **(Optional) Supabase table** — only needed if you wire in `storage.py`
   (currently stubbed out in `bot.py`'s `finalize()`):
   ```sql
   create table applications (
     id uuid primary key default gen_random_uuid(),
     telegram_user_id bigint not null,
     reference_number text unique not null,   -- e.g. CRB-20260825-3f7a
     officer_code text,                       -- which officer owns this application
     declared_name text,
     declared_address text,
     declared_gender text,
     phone_number text,                       -- E.164 format, for SMS fallback
     email text,                              -- for email fallback notifications
     status text not null default 'in_progress',
     completeness_pct numeric,
     ready_for_underwriting boolean,
     flags jsonb,
     turnaround_seconds numeric,
     notified_at timestamptz,                 -- when officer sent /notify
     notification_channel text,              -- "telegram" | "sms" | "none"
     notification_status text,               -- "delivered" | "failed"
     created_at timestamptz not null default now(),
     processed_at timestamptz
   );

   -- fast lookups by officer and by reference number
   create index on applications(officer_code);
   create index on applications(reference_number);
   ```
   Note: this table stores structured results only — never raw document
   images or unredacted identifiers, per the data-handling guardrail.

   Also create the immutable audit log (UPDATE/DELETE revoked so rows can
   never be altered after insert):
   ```sql
   create table audit_log (
     id uuid primary key default gen_random_uuid(),
     application_id uuid references applications(id),
     event_type text not null,
     actor text not null,
     payload jsonb,
     created_at timestamptz not null default now()
   );

   revoke update, delete on audit_log from anon, authenticated;
   ```

6. **Run**
   ```bash
   export ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=...
   python bot.py
   ```
   Open Telegram, find your bot, send `/start`.

## What actually works right now

- Full conversation flow: `/start` → 4 documents collected as photos → live
  Claude vision extraction per document → deterministic validation → summary.
- Completeness, cross-document name/DOB/address consistency, BVN/NIN
  **format** validation, and document recency checks — all real, all tested.
- Turnaround time measured and shown in the summary (your demo's headline
  metric: compare it to a stated manual baseline).

## What this build does NOT do (be upfront about this in your pitch)

- **No source-of-truth identity verification.** BVN/NIN checks confirm
  *format* (11 digits), not that the number is real or belongs to the
  applicant. Actual verification requires licensed NIBSS/NIMC API access —
  out of scope for a 3-day build. Say this explicitly; don't let a judge
  discover it by asking.
- **No credit decision of any kind.** `ready_for_underwriting` is a
  readiness signal for a human officer, never an approval/rejection.
- **Supabase persistence is stubbed, not wired.** The bot works end-to-end
  without it; add it if you want a leaderboard/history view for the demo.
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
bot.py            (conversation flow, photo intake)
   |
   v
extraction.py     (Claude vision call, per SOP-009 Phase 2 prompt)
   |
   v
validation.py     (deterministic checks, per SOP-009 Phase 3)
   |
   v
bot.py            (readiness summary back to user, per SOP-009 Phase 4)
   |
   v
storage.py        (stubbed — structured results only, SOP-009 Phase 5)
```

## Swapping to WhatsApp later

The extraction and validation layers are transport-agnostic — `bot.py` is
the only file tied to Telegram's API. A WhatsApp version would replace
`bot.py`'s handlers with WhatsApp Cloud API webhook handlers, reusing
`extraction.py` and `validation.py` unchanged. Not attempted here given
WhatsApp Business verification/template-approval lead times don't fit a
3-day window.
