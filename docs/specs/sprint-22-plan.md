# Sprint 22 — "Recover the lost filings" (data completeness)

**STATUS: FULLY CLOSED 2026-06-03.**

- **B-091** historical backfill shipped (Phase 1).
- **B-094** IT filter + purge shipped (Phase 1).
- **B-092** parser fixes shipped (Phase 2): EXERCISE keywords extended (EBT transfers, RSU vesting, nil-cost options, plan allocations); GRANT keywords extended (bonus/share award plans); same-person bundled gate fixed. Audit confirmed no BZW/EQS/GNW in corpus — PRN uses identical HTML layout to RNS. 33 rows recovered via sweep, 166 tests green, signal counts stable.
- **B-090** (bundled multi-PDMR) deferred to Sprint 29.

**Date opened:** 2026-06-03.
**Theme:** harvest the filings the old scraper never captured, and decide what to do about the single largest missing cohort.
**Discipline:** audit-first (done, below); scrapes/reparses are **write-path → Rupert runs them**; diff-first on anything that shifts ingest/firing counts.

---

## Audit findings (read-only, 2026-06-03)

**The discovery fix is already shared by the backfill path.** `_row_is_pdmr` is now the Phase-4 denylist-based version (trust-the-category, fail-open), and **both** `iter_index` (daily) and `iter_archive` (historical, walks `/announcement-archive` in 30-day windows) call it. So a historical re-scrape via `iter_archive` already inherits the fix — no extra code needed for B-091.

**Pending backlog = 4,296 filings.** Bucket breakdown:

| Cohort | Count | Nature |
|---|---|---|
| **Bundled multi-PDMR** | **~2,349** | One filing = several directors; parser refuses to fan out rather than mis-attribute. **This is B-090.** |
| required_fields_missing / could_not_classify_type / multiple_distinct_prices / could_not_separate_price_volume / zero_shares | ~1,300 | Assorted hard layouts; lower value, mixed recoverability. |
| foreign_currency | ~253 | Intentionally rejected; need FX conversion (separate). |
| (215 entries have ≥1 partially-extracted row) | | |

This confirms the ~2,271 estimate: **bundled multi-PDMR is ~55% of all pending** and the dominant prize (and the dominant risk).

---

## Scope & honest sizing

This is **not** a one-sitting sprint. Two very different kinds of work:

- **B-091 (historical backfill) + B-092 (non-RNS layouts)** — contained, mostly write-path. Do these first.
- **B-090 (bundled multi-PDMR recovery)** — a genuine multi-phase *engineering project* (~2,349 filings, per-director attribution, LLM cost, mandatory data-integrity-auditor in the loop). High value (Hikma, Dr Martens, Great Portland land here) but high risk of mis-attributing a trade to the wrong person. **Should run as its own dedicated, gated effort — likely spanning sessions — not bolted onto this sprint.**

### ⚠ B-094 — validation caught a re-pollution bug (2026-06-03)

The Step-1 validation window (`--from 2026-04-01 --to 2026-06-03`) returned
**seen=1326, written=340, pending=998** — discovery works. **But the insert list
was full of investment trusts** (FGT, EDIN, NCYF, BVT, SSIT, UEM, MIGO, NAIT,
BNKR, SST, WWH…). Root cause: **`backfill_filings.py` had no IT/CEF exclusion
filter** (verified by code read — `run_scrape.py` filters at ingest, the backfill
did not). So the historic re-scrape was re-importing the exact issuers Sprint 2
purged.

**Fixed (Zone-A, 2026-06-03):** ported `reparse_corpus`'s CSV-aware
`_load_excluded_tickers` (reads `is_excluded_issuer=1` **and** `_excluded_it_cef.csv`)
into `backfill_filings.py`, with a per-row drop + `_excluded_at_ingest.log` audit +
an `excluded_at_ingest` counter in the run summary. **Do not run Step 2 until the
already-inserted ITs are purged and Step 1 re-validates clean.**

**Cleanup + re-validate sequence (Rupert, PowerShell):**
```
# 1. re-flag issuers (the re-inserted ITs now have transactions to match on)
python .scripts\classify_issuers.py
# 2. purge flagged IT/CEF rows  (needs .data\directors.db.pre-it-purge.bak;
#    if missing, copy directors.db to it first, or use --dry-run to inspect)
python .scripts\exclude_investment_trusts.py --preview
python .scripts\exclude_investment_trusts.py --confirm
# 3. re-run the Step-1 window — now with the B-094 filter:
python .scripts\backfill_filings.py --from 2026-04-01 --to 2026-06-03 --no-llm --verbose
#    EXPECT: "excluded_at_ingest=<N>" > 0 and far fewer IT inserts.
```
Paste the re-run summary line; if `excluded_at_ingest` is healthy and inserts look
like operating companies, proceed to Step 2.

### Phase 1 — Historical backfill (B-091) + non-RNS layouts (B-092)

1. **B-091 re-scrape — VERIFIED COMMANDS (Rupert, write-path, diff-first).**
   The tool is **`backfill_filings.py`** (it drives `iter_archive`, the 30-day-chunk
   archive walker, which already calls the fixed denylist `_row_is_pdmr`). It is
   **resumable** (`_backfill_progress.json`), **cost-capped** (`--llm-budget-usd`,
   default $50), and does `db_health.backup()` pre-flight + `seal()` on success.
   There is **no `--dry-run`**, so we de-risk with a short validation window first.

   **Step 1 — validation window (recent ~2 months, no LLM cost):**
   ```
   python .scripts\backfill_filings.py --from 2026-04-01 --to 2026-06-03 --no-llm --verbose
   ```
   Review: how many filings newly discovered/ingested (these are the ones the old
   keyword filter dropped), any `ArchiveCalibrationError`, and how much `_pending_review.json`
   grows (bundled filings landing there is expected/correct).

   **Step 2 — full launch→today (only if Step 1 looks right):**
   ```
   python .scripts\backfill_filings.py --from 2025-05-01 --to 2026-06-03 --no-llm --resume
   ```
   `--no-llm` keeps it free — anything needing LLM date/field recovery routes to
   pending and can be swept later via `run_pending_sweep.py` (now B-015-safe).
   `--resume` lets it continue if a chunk hits a calibration error.

   **Step 3 — rebuild:** `eval_signals → backtest → export_dashboard_json → build_dashboard`.

   **Diff-first note:** the Step 1 output IS the diff — review the newly-ingested
   count and a sample before committing to the full Step 2 window.
   **Cost note:** `--no-llm` = £0. Drop it only if you want LLM recovery and accept
   the capped spend.
2. **B-092 non-RNS layouts.** After the next live scrape, scan `_pending_review.json` for TotalEnergies / Next 15 / M&G Credit / Magnum / Ferguson. For any that landed there, add a parser recogniser for that provider's layout (Zone-A; Claude builds, Rupert reparses the affected rns_ids via the new `--only-rns`).

### Phase 2 — Bundled multi-PDMR recovery (B-090) [DECISION REQUIRED]

A dedicated project, not started here. Outline:
- Design a multi-row extractor that fans one bundled filing into one row per PDMR, with **strong per-director attribution** (name ↔ role ↔ volume ↔ price must not cross-contaminate).
- LLM-assisted extraction is likely required for the messy boilerplate → **real per-filing token cost across ~2,349 filings**. Needs a cost ceiling + a sample run first.
- **data-integrity-auditor in the loop**, sampling attribution accuracy before any bulk ingest.
- Diff-first; gated; phased (sample → validate → scale).

---

## Decisions for Rupert

1. **B-090 appetite & timing.** Recommend: do **Phase 1 (B-091 + B-092) now**, and schedule **B-090 as its own dedicated sprint** given its size, LLM cost, and mis-attribution risk. Confirm — or say if you want B-090 scoped immediately.
2. **B-091 cost.** The historical re-scrape may trigger the LLM fallback on some filings (token cost) and will take real wall-clock time across many 30-day windows. OK to proceed once the exact command is confirmed?
3. **Lower-value cohorts (~1,300 hard-layout + ~253 FX).** Leave in pending for now (low value / needs FX work), or in scope? Recommend leave.

---

## Out of scope
Signal keep/kill (Sprint 23), performance polish (Sprint 24), PDMR editor (Sprint 25), FX-currency handling.
