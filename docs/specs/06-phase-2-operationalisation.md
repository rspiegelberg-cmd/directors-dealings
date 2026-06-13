# Spec: Phase 2 — operationalisation plan

**Status:** Draft v1.0 — 2026-05-06
**Owner:** Rupert
**Target ship:** Week of 2026-05-26 (one focused weekend, ~5 hours total)
**Source:** Companion to `04-phase-2-llm-fallback.md` (the design spec, Approved v1.1). `backlog.md` rows P2-1 through P2-5.
**Author:** PM/back-end planning pass, 2026-05-06

---

## Why this doc exists

The Phase 2 design spec (`04-phase-2-llm-fallback.md`) is approved and decisions are locked — D1 = per-session env var, D2 = £5 ceiling. As of 2026-05-06, **most of the code already exists on disk**, written alongside the Phase 5 paper-trading ship on 2026-05-05:

| File | Status | Lines |
|---|---|---|
| `.scripts/llm_parser.py` | Written, never run | 416 |
| `.scripts/llm_cost.py` | Written, never run | 165 |
| `.scripts/llm_retry_pending.py` | Written, never run | 231 |
| `.scripts/db.py` | Written, never run | 291 |
| `.scripts/db_schema.sql` | Written, includes `parser_source TEXT` + CHECK constraint | 166 |
| `.scripts/migrate_log_to_db.py` | Written, never run | 167 |
| `.scripts/test_p2_llm_validation.py` | Written, never run | — |

What is **not** done:

- No `dealings.db` file exists anywhere on disk. The SQLite migration has never been executed.
- `update.py` does not call `llm_parser`. The architectural diagram in the design spec — where flagged filings auto-fallback to the LLM during the daily refresh — is **not wired in**. Today, the LLM path only runs via the explicit `llm_retry_pending.py` CLI.
- No Anthropic API key is provisioned. `ANTHROPIC_API_KEY` is referenced in code but never set in the environment.
- No code-review pass (P2-5) has run.

So the work in front of us is operationalisation, not new construction. This doc is the rollout plan to take what's built and put it in service.

---

## Pre-requisites

1. **Anthropic API key.** Get one from https://console.anthropic.com → Settings → API Keys → Create Key. Treat it like a password — anyone with the key can spend money against your account. It is *separate* from your Claude Code / Cowork subscription.
2. **PowerShell with the project as cwd.** All commands below assume:
   ```powershell
   cd "C:\Users\Rupert Spiegelberg\OneDrive\Documents\Claude\Projects\Directors Dealings"
   ```
3. **The "use the briefing this week" check has run.** If feedback from a week of using `daily-briefing.html` has surfaced anything that should jump the queue, that takes precedence over Phase 2.

---

## Plan: 5 sessions over one weekend

### Session 1 — API key + smoke test (~30 min, ~£0.001)

**Goal:** prove the API key and the `llm_parser.py` module work end-to-end on one filing before doing anything bigger.

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python .scripts\llm_parser.py --rns-id 9542495
```

`9542495` is one of the cleanest items in the current pending queue (single PDMR, single transaction, one regex hiccup). If the LLM can't handle this, something's wrong with the prompt or the API call.

**Pass criteria:**

- Script prints a JSON delta matching the schema in `CLAUDE.md`.
- `parser_source` would be `'llm'` (visible in the dry-run output).
- `llm_cost.py` records ~3000 input + ~200 output tokens against the meta table.
- No errors, no warnings about missing required fields.

**Fail mode handling:**

- If the API key is rejected: re-paste the key, watch for stray whitespace.
- If the prompt produces malformed JSON: read `llm_parser.py:153` onwards (the schema validation rejection path). Iterate on the system prompt before any further runs.

### Session 2 — Stand up the SQLite store (~1h, ~£0)

**Goal:** create `dealings.db` and import everything from `dealings-log.json` into it.

This delivers P1-3 + P1-4 from Phase 1's backlog (currently marked `Backlog` but actually coded) as a side-effect, since the code's already there.

```powershell
python .scripts\migrate_log_to_db.py --dry-run
```

Read the report. It will show how many rows would be inserted, updated, unchanged, or skipped. If the skip count is alarming, stop and investigate before applying.

```powershell
python .scripts\migrate_log_to_db.py --apply
```

**Sanity check:**

```powershell
python -c "import sqlite3, json; c=sqlite3.connect('dealings.db'); n_db=c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]; n_json=len(json.load(open('dealings-log.json'))['transactions']); print(f'DB: {n_db}, JSON: {n_json}, diff: {n_json - n_db}')"
```

**Pass criteria:**

- `dealings.db` exists at the project root.
- Row count in DB matches (or is within a small skip count of) the JSON.
- All rows have `parser_source = NULL` (the historical rows have unknown provenance).

### Session 3 — LLM fallback dry run (~30 min, ~£0)

**Goal:** see what the LLM *would* do on five real flagged items before spending any money.

```powershell
python .scripts\llm_retry_pending.py --dry-run --limit 5
```

For each of the 5 items, the dry-run output should show: the rns_id, the warnings the regex parser raised, and the proposed delta the LLM extracted. **Read each one against the cached HTML** at `.scripts/_scrape_cache/{rns_id}.html`.

**Pass criteria — qualitative judgement:**

- Director name correctly extracted on bundled-PDMR filings (or correctly returned as empty + warning).
- Foreign-currency prices correctly returned as `price=0, value=0` plus a warning. **Not** an invented FX-converted GBP number.
- Transaction type correctly classified into one of the six allowed values.
- Multi-transaction filings either return one delta per transaction or are correctly rejected with a warning.

If any of these fail, fix the prompt in `llm_parser.py` and re-run dry-run before spending money.

### Session 4 — Bounded first apply (~1h, ~£0.01)

**Goal:** actually write 10 LLM-parsed deltas to disk, with tight budget guardrails.

```powershell
python .scripts\llm_retry_pending.py --apply --limit 10 --max-spend 1
```

`--max-spend 1` overrides the £5 default down to £1 for this run. Belt and braces.

**Pass criteria:**

- 10 items leave `_pending_review.json`.
- 10 new rows appear in `transactions` with `parser_source='llm'`.
- The same 10 rows appear in `dealings-log.json` (dual-write).
- `llm_cost.py` running total is roughly £0.01.
- Dashboard footer (if cost monitor display lands here too — spec calls it out as P2-4 deliverable) shows the spend.

**Verify with:**

```powershell
python -c "import sqlite3; c=sqlite3.connect('dealings.db'); print(list(c.execute('SELECT parser_source, COUNT(*) FROM transactions GROUP BY parser_source')))"
```

Expect a row showing `('llm', 10)` plus the historical NULLs.

### Session 5 — Clear the residual + code review (~1.5h, ~£0.07)

**Goal:** put the whole 68-item residual queue through the LLM and finish P2-5.

```powershell
python .scripts\llm_retry_pending.py --apply
```

No `--limit` this time. £5 ceiling kicks in if anything goes wrong; expected actual spend is ~£0.07 (68 items × £0.001).

**What to expect:**

- A meaningful fraction will clear — these are items the regex couldn't handle but the LLM can (bundled PDMR with one director clearly identifiable, atypical date formats the regex missed, etc.).
- Some won't clear and will stay in `_pending_review.json` with both regex and LLM warnings appended. These are the genuine ~5% the design spec calls out: multi-tranche disclosures with truly ambiguous prices, foreign-currency without FX rates, malformed source HTML. These are the items where human eyes are the only option.

**Then P2-5 — code-review pass.** A single read-through of `llm_parser.py`, `llm_cost.py`, and `llm_retry_pending.py` looking specifically for:

- Any path where LLM output is written to the DB *without* validation.
- Any path where `parser_source='llm'` isn't set on an LLM-derived write.
- Any way the budget ceiling can be bypassed.
- Behaviour when the API returns HTTP 5xx mid-batch — does the script crash leaving the pending queue half-modified, or does it commit-as-it-goes?

Use the `engineering:code-review` skill, or a manual read with the spec open alongside.

---

## What's deliberately out of scope for this rollout

### Auto-fallback in `update.py` (deferred)

The Phase 2 design spec's architecture diagram has `update.py` automatically calling `llm_parser.parse()` whenever the regex flags a filing — i.e. every `Open Dashboard.bat` run silently spends a few pence on whatever needs LLM help. This integration is **not yet wired in** and **should remain unwired for at least a few weeks after the manual rollout above**.

Reasons:

- Auto-fallback in the daily flow means money gets spent without the user watching. Cost is small but the behaviour change is real and worth being deliberate about.
- Manual `llm_retry_pending.py` invocation gives batched control, predictable spend, and a meaningful relationship between the cost-monitor footer and what was just done.
- The wiring itself is a 30-minute change — `update.py` lines around 198–235 already have the regex-flag detection point. Adding the LLM call is ~10 lines of code. Not a meaningful saving worth shipping early.

Re-evaluate after two weeks of explicit-CLI use.

### Backfill scrape (P1-1)

5-year scrape walk-back to Jan 2019 is *not* part of this rollout. It's a separate decision and a much bigger commitment (several hours of unattended scraper time, plus a likely 18,000+ flagged filings to LLM-parse at ~£18 of Anthropic spend). Run that as a deliberate Phase 1 push, not bolted onto Phase 2.

### Issuer-specific prompt tuning

If the LLM struggles with specific issuer templates (Schroders, BAT, HSBC), the temptation will be to add issuer-conditional logic to the prompt. **Resist.** The design spec is explicit: one prompt, period. If accuracy is poor, iterate on the single prompt. Per-issuer specialisation is a Phase 3 problem (signal-source audit by issuer).

---

## Cost summary

| Item | Spend |
|---|---|
| Session 1 smoke test | ~£0.001 |
| Session 2 SQLite migration | £0 |
| Session 3 dry run | £0 |
| Session 4 bounded apply (10 items) | ~£0.01 |
| Session 5 clear residual (68 items) | ~£0.07 |
| **Anthropic total** | **~£0.10** |
| Sonnet dev tokens (this rollout) | ~80–120k (~£0.30) |
| **All-in** | **<£0.50** |

The design spec's cost cheat sheet projected ~£0.05/week steady state. Until the auto-fallback is wired into `update.py`, steady-state spend is **£0** — the LLM only runs when you explicitly invoke it.

---

## Backlog updates triggered by completion

When this rollout finishes, the following `backlog.md` rows should flip:

| Row | New status | Notes |
|---|---|---|
| P1-2 | Done | Schema designed in `db_schema.sql` |
| P1-3 | Done | SQLite store stood up, `db.py` migration logic |
| P1-4 | Done | `migrate_log_to_db.py --apply` ran, JSON imported |
| P2-1 | Done | `llm_parser.py` validated end-to-end on real filings |
| P2-2 | Done | `parser_source` column populated in DB |
| P2-3 | Done | `llm_retry_pending.py` cleared the residual |
| P2-4 | Done | `llm_cost.py` budget ceiling and running total operational |
| P2-5 | Done | Code review pass complete |

P1-1, P1-5, P1-6 (backfill scrape, OHLCV backfill, parity tests) remain genuinely outstanding and will need their own Phase 1 rollout doc.

---

## What we need from Rupert before Session 1 starts

1. Anthropic API key, kept private.
2. Confirmation that the briefing-watching week has surfaced no urgent feature requests that should bump this rollout.
3. ~5 hours of weekend time, ideally in a single sitting so context stays loaded.

That's it. The code is on disk and waiting.

---

## Open issue noted while drafting this doc

While verifying the file-by-file state of Phase 2 on 2026-05-06, partial investigation surfaced that `daily-briefing.html` is currently showing no data when opened from disk. Likely cause: browsers block `fetch()` from `file://` pages reading sibling files, so the HTML's call to `fetch("paper-trades.json")` silently fails. Independent of that, the "New firings" panel filters `status === "planned"`, but the data only contains `closed`, `open`, and `skipped` statuses — so that panel would always be empty even with fetch working. **This is a Phase 5 follow-up, separate from Phase 2 rollout, and worth a dedicated bug-fix session.**
