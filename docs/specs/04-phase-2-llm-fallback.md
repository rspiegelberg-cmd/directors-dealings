# Spec: Phase 2 — LLM fallback parser

**Status:** Approved v1.1 — decisions locked 2026-05-05. **D1 = Option A** (per-session env var). **D2 = £5 ceiling** per run.
**Owner:** Rupert
**Target ship:** Week of 2026-05-26 (one focused weekend)
**Source:** `backlog.md` rows P2-1 through P2-5; `Directors-Dealings-PM-Brief.docx` Phase 2
**Author:** PM/back-end planning pass, 2026-05-05

---

## Goal

Push parser coverage from ~71% (after Phase 0 fixes) to ~95%+ by handing filings the regex parser can't extract to Claude Haiku, with strict schema validation so the LLM never invents data. The remaining ~5% are genuinely ambiguous (multi-tranche disclosures, foreign currencies without FX rates, malformed source HTML) and should stay in pending review.

## What changes between Phase 1 and Phase 2

After Phase 1 you have the foundation: SQLite store, parity-checked JSON export, ~80 items in the live pending queue plus however many ended up in `_backfill_pending_review.json` after the historical scrape. The LLM fallback's job is to chew through those queues without flooding the live triage flow.

**Important operational detail:** Phase 2 introduces a new dependency — paid API calls to the Anthropic API. This is *separate* from your Claude Code / Cowork subscription. You'll need an API key from the Anthropic console (https://console.anthropic.com), and your usage will accrue against that account's billing.

The cost ceiling (D4 below) is the safety belt against running the script wrong and burning unexpected money.

---

## Decisions (pre-locked unless flagged)

### Pre-locked, no question — engineering preference

- **Model:** `claude-haiku-4-5-20251001`. Most cost-effective option that's smart enough to extract structured data from plain English. Sonnet would be overkill at 10× the cost.
- **HTTP client:** stdlib `urllib.request` for the API call. Matches the project's stdlib-first stance. ~50 lines of code; no new third-party deps.
- **Input shape:** the cached HTML at `.scripts/_scrape_cache/{rns_id}.html` stripped to plain text via the existing `parse_pdmr.html_to_text()`. Same input the regex parser sees. Saves tokens vs sending raw HTML.
- **Output shape:** structured JSON matching the existing delta schema in `CLAUDE.md`. Use Anthropic's "tool use" interface to force JSON output rather than asking the model nicely.
- **Validation strictness:** strict. Required fields (date, ticker, company, director, type, shares) must all be present and well-formed. Type must be one of the six allowed values. Unknown fields are dropped. Anything missing → reject + log a `warnings` entry, just like the regex parser does.
- **Schema change:** add `parser_source` column to `transactions` table. Values: `'regex'` (default for new regex parses), `'llm'` (set on LLM fallback writes), `NULL` (unknown — pre-existing rows). One-line schema change; needs `migrate_log_to_db.py --apply --reset` to take effect.

### D1 (needs your sign-off) — How does the API key get provided?

The Anthropic SDK normally reads `ANTHROPIC_API_KEY` from the environment. Three options:

- **Option A — Environment variable, set per-PowerShell-session.** You run `$env:ANTHROPIC_API_KEY = "sk-ant-..."` once per session. Easiest, no config file. Risk: forget to set it and the script fails fast with a clear error. **Recommended.**
- **Option B — Persistent environment variable** (`setx` or system properties). Set once, persists across sessions. Slightly less secure (any process on your machine can read it).
- **Option C — Local secrets file** at `.scripts/_secrets.json`, in `.gitignore`. Loaded by `llm_parser.py`. More work to set up but contained.

Recommended: **Option A**. You're going to invoke the LLM parser explicitly (not as part of `Open Dashboard.bat`), so a per-session env var is fine. If you'd rather not type the key every time, **Option B** is reasonable.

### D2 (needs your sign-off) — Cost ceiling

How big a hard limit do you want on a single LLM-parser run?

- **Option A — £5 ceiling per run** (recommended). Catches obvious mistakes (forgot to add `--limit-items`, accidentally re-running a full backfill). At £0.001/filing this caps you at ~5,000 filings per invocation, way more than any single legitimate use case.
- **Option B — £20 ceiling per run.** Looser. Lets you do a full historical backfill in one go.
- **Option C — No ceiling, just running totals.** You'd watch the dashboard footer and Ctrl-C if it spirals.

Recommended: **A**. You can always pass `--max-spend 20` to override on a specific run.

---

## Architecture

```
                        update.py (daily)
                              │
                              ▼
                       parse_pdmr.parse_announcement()
                              │
                  ┌───────────┴───────────┐
                  │                       │
              clean parse              warnings
              (regex worked)           (regex flagged)
                  │                       │
                  ▼                       ▼
         dual-write delta          llm_parser.parse(item)
         parser_source='regex'           │
                                  ┌──────┴───────┐
                                  │              │
                           clean LLM parse   LLM also failed
                                  │              │
                                  ▼              ▼
                         dual-write delta    pending_review
                         parser_source='llm'  (with both warnings)
```

The LLM is **only** called for filings the regex flagged. Clean regex parses go straight to the dual-write path — we don't waste tokens.

### Files to create / change

| File | New / changed | Purpose |
|---|---|---|
| `.scripts/llm_parser.py` | new | API call, prompt template, schema validation, returns `(deltas, warnings)` tuple matching `parse_pdmr.parse_announcement` |
| `.scripts/llm_cost.py` | new | Tracks running token spend in `meta` table, enforces budget ceiling |
| `.scripts/db_schema.sql` | edited | adds `parser_source TEXT` column |
| `.scripts/db.py` | edited | `upsert_transaction()` accepts `parser_source` arg |
| `.scripts/llm_retry_pending.py` | new | One-shot CLI to chew through `_pending_review.json` and `_backfill_pending_review.json` via the LLM |
| `update.py` | edited | After regex parser flags a filing, call `llm_parser.parse()` before sending to pending |
| `.scripts/test_p2_llm_validation.py` | new | Tests strict-validation paths with synthetic LLM responses (no real API calls) |

### Prompt design (rough draft)

```
SYSTEM: You extract structured data from UK PDMR (director dealings)
RNS notifications. You return ONLY valid JSON matching the schema. If
any required field is unclear, return null for that field and the
caller will reject the result — do NOT guess.

USER: Here is the filing text:
<plain text from html_to_text()>

Extract one delta object per director-transaction row. Schema:
{
  "deltas": [
    {
      "date": "YYYY-MM-DD",            // transaction date, required
      "ticker": "XXXX",                  // bare LSE ticker, required
      "company": "...",                  // required
      "director": "Full Name",           // required
      "role": "...",                     // optional
      "type": "BUY|SELL|SELL_TAX|EXERCISE|GRANT|SIP",  // required
      "shares": 12345,                   // required, integer
      "price": 1.23,                     // GBP per share; 0 if undisclosed
      "value": 15142.35,                 // GBP total; 0 if undisclosed
      "context": "Brief factual summary",
      "announced_at": "YYYY-MM-DD HH:MM UTC"
    }
  ],
  "warnings": ["string description of any concern"]
}

If the filing reports prices in non-GBP currency, set price=0, value=0
and add a warning. If multi-tranche prices, return one delta per
distinct price. If you cannot identify a director, set director="" and
add a warning — do NOT invent a name.
```

This is sent via Anthropic's tool-use interface so the response is forced into the JSON shape; no parsing of free-text replies.

---

## Per-item plan

### P2-2 — `parser_source` column (build first, blocks everything else)

**Output.** Single-line edit to `db_schema.sql` adding `parser_source TEXT`. Edit `db.py:upsert_transaction()` to accept a `parser_source` kwarg. Default for new regex inserts: `'regex'`. The migration via `migrate_log_to_db.py --apply --reset` re-imports the JSON with `parser_source=NULL` (pre-existing rows have unknown source).

**Token estimate.** ~10k.

### P2-1 — `llm_parser.py`

**Output.** Module exposing `parse(item) -> tuple[list[dict], list[str]]` — same signature as `parse_pdmr.parse_announcement`. Internal flow:

1. Load cached HTML from `.scripts/_scrape_cache/{rns_id}.html`. If missing, fetch and cache.
2. Strip via `parse_pdmr.html_to_text()`.
3. Build messages payload, call Anthropic API with tool-use forcing JSON.
4. Validate response against schema. Reject (return `[], [warning]`) on any required-field miss or type mismatch.
5. Record token usage via `llm_cost.record(input_toks, output_toks)`.

Plus a small CLI (`python .scripts\llm_parser.py --rns-id 9540067`) for spot-testing.

**Token estimate.** ~50k Sonnet (build + tests).

### P2-4 — Cost monitor

**Output.** `llm_cost.py` providing `record(input_toks, output_toks, model)` and `running_total() -> dict`. Persists to `meta` table keyed by `llm_spend_input_tokens`, `llm_spend_output_tokens`, `llm_spend_gbp`. Refuses further calls when running total exceeds budget; raises a clear exception that callers translate to a graceful exit.

**Token estimate.** ~25k.

### P2-3 — `llm_retry_pending.py`

**Output.** Mirror of `retry_pending.py` but routes flagged items through `llm_parser.parse()` instead of just re-running the regex. Dual-writes accepted parses; updates the pending file in-place.

```text
python .scripts\llm_retry_pending.py --dry-run             # see what the LLM would do
python .scripts\llm_retry_pending.py --apply               # actually write
python .scripts\llm_retry_pending.py --apply --limit 10    # process only 10 filings
python .scripts\llm_retry_pending.py --apply --max-spend 1 # tighter budget for this run
python .scripts\llm_retry_pending.py --apply --pending-file .scripts/_backfill_pending_review.json
                                                            # target the backfill queue specifically
```

**Token estimate.** ~25k Sonnet dev + Haiku runtime budget — see below.

### P2-5 — Code review

**Output.** A single pass through `llm_parser.py`, `llm_cost.py`, and `llm_retry_pending.py` looking specifically for: (a) any path where LLM output writes to the DB without validation, (b) any path where `parser_source='llm'` isn't set, (c) any way to bypass the budget ceiling, (d) how does it behave if the API returns an HTTP 5xx mid-batch?

**Token estimate.** ~25k Sonnet.

---

## Order of execution

```
P2-2 (schema)
  │
  ▼
P2-1 (llm_parser.py)  ──┐
                        ├──► P2-4 (cost monitor)  ──► P2-3 (llm_retry_pending)  ──► P2-5 (review)
                        │
```

P2-1 and P2-4 can be drafted in parallel since they're independent modules; both must exist before P2-3 wires them together.

---

## Honest cost expectations

Per call:
- ~3000 input tokens (HTML stripped to text + schema + instructions)
- ~200 output tokens (JSON delta)
- Haiku 4.5 at current pricing: ~£0.001 per filing

Live pending queue today (~80 items): ~£0.08 to clear in one batch.

Backfill pending queue (TBD — depends on what comes back from `backfill_filings.py`). At Phase 0's ~30% flag rate × 5y × 50 filings/day × 250 trading days = ~18,750 flagged filings × £0.001 = **~£19 to clear the full historical backlog**.

Steady state (live flow only): ~50 filings/week × 30% × £0.001 = **~£0.05/week**.

The brief's target was <£0.20/week ongoing — well within reach.

---

## What's deliberately out of scope

- Re-parsing items that the regex parser handled successfully (waste of money).
- Dynamic prompt-engineering: we ship one prompt, period. If accuracy is poor we iterate, but no per-issuer customisation.
- Multi-shot reasoning: one LLM call per filing, take it or leave it.
- Tracking which specific issuer templates the LLM is good vs bad at — Phase 3's signal-source audit handles that.

---

## What we need from Rupert before code changes

1. **Anthropic API key.** From https://console.anthropic.com → Settings → API Keys → Create Key. Keep it private — if it leaks, anyone can spend money on it under your name.
2. **D1 sign-off:** how to provide the key (env var per session, persistent env var, or local secrets file).
3. **D2 sign-off:** cost ceiling (£5 / £20 / no limit). Recommended £5.

Once those are answered, P2-2 (schema) ships immediately, then P2-1 + P2-4, then P2-3.
