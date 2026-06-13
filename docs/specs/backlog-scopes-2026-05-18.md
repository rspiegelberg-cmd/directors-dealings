# Backlog execution plan & scopes — 2026-05-18

> Sequenced deployment plan for all 11 open backlog items. Each item has
> an engineer-ready scope with acceptance criteria, files to touch, and
> validation steps. Reviewed against dependencies so no earlier-tier
> item depends on a later-tier item.

Source backlog: [`docs/backlog.md`](../backlog.md).

## Changelog

**2026-05-19 (post-redesign-v1 follow-ups):**
- Added 12 new items (B-012 through B-023) surfaced during the
  Performance page redesign v1 build. All new items live in a
  dedicated "REDESIGN V1 FOLLOW-UPS" section at the bottom of this
  file so the original B-001…B-011 tier sequencing is preserved.
- Tier 1 additions: B-012 (5-fix bundle), B-013 (DRY slugify),
  B-014 (thread base_rate to drill pill), B-015 (delete stale
  dashboard/*.html templates).
- Tier 2 addition: B-016 (RNS scraper role-field garbage).
- Tier 3 additions (v1.1 candidates): B-017 (functional drill
  dropdowns — Option A vs B decision pending), B-018 (per-tile
  signal-filtered cohorts), B-019 (CSV export), B-020 (mobile /
  tablet layouts), B-021 (compare two cohorts), B-022 (cross-tile
  linked highlight), B-023 (auto-deprecate kill verdicts after 2x
  regime windows — locked by Rupert pending dataset coverage).

**2026-05-18 (post-QA review):**
- Corrected file paths: render scripts live in `.scripts/dashboard/`,
  not `.scripts/`. Schema lives in `db_schema.sql` + a new
  `migrations/003_*.sql` file, not `db.py`.
- Corrected function references: `parse_with_llm(...)` not
  `llm_parser.parse(...)`; `_format_time()` is the precise function
  in B-010.
- Fixed validation SQL bug in B-001+B-004 (`LIKE '%\n%'` → use
  `INSTR(..., CHAR(10))` — SQLite does not interpret `\n` in LIKE).
- Reworded B-006 implementation to remove the impossible "wrap a
  filesystem write in a SQL transaction" step.
- BeautifulSoup confirmed as a **new** dependency for B-001+B-004
  (not pre-installed as initially assumed).
- Effort estimates revised upward: B-002 5 min → 30 min · B-011 3 h
  → 6 h · B-001+B-004 2 h → 3.5 h.
- Added explicit ACs for `eval_signals.py` + `build_dashboard.py`
  re-runs after every Tier 3 step.
- Added pre-Tier-3 backup gate.
- Added preview-and-sign-off gate to B-001+B-004 (Rupert decision).
- B-009 keeps **median** (Rupert decision — was silently switched to
  mean in the draft).
- Added missing edge cases: empty `dates` in B-008, empty
  `announced_at` in B-010 and B-007, missing cache files in B-002,
  fingerprint stability rule in B-001+B-004.

---

## Deployment order at a glance

Effort estimates **revised after QA review** (2026-05-18) — original
optimistic numbers replaced with realistic-for-an-engineer-new-to-the-
codebase figures.

| Tier | Item | Title | Effort | Cost |
|------|------|-------|--------|------|
| 1 | B-002 | LLM sweep — recover 7 pending rows | 30 min | £0.30 |
| 1 | B-005 | Reject dates with year < 1990 | 10 min | £0 |
| 1 | B-008 | Defensive ISO-date assert in backtest | 10 min | £0 |
| 1 | B-006 | repair_dates.py atomic pending write | 30 min | £0 |
| 1 | B-010 | Transaction table sort + today's-date format | 30 min | £0 |
| 2 | B-009 | CAR chart — true 12-month buckets + null gaps | 1 h | £0 |
| 3 | B-011 | Exclude investment trusts / CEFs / VCTs / REITs | 6 h | £0–0.20 |
| 3 | B-001 + B-004 | Table-aware parser (multi-row + director-name) | 3.5 h | £0 |
| 3 | B-003 | Unit tests on parser (Layer 2) | 2 h | £0 |
| 4 | B-007 | I6 informational late-filings badge | 30 min | £0 |
| 1 | B-012 | Performance redesign v1.1 small-fix bundle | 30 min | £0 |
| 1 | B-013 | DRY the slugify helper | 15 min | £0 |
| 1 | B-014 | Thread real horizon base_rate to drill pill | 30 min | £0 |
| 1 | B-015 | Delete or archive stale dashboard/*.html templates | 20 min | £0 |
| 2 | B-016 | Clean RNS scraper garbage in role field | 2 h | £0 |
| 3 | B-017 | Drill-page lookback/horizon dropdowns made functional | 6 h | £0 |
| 3 | B-018 | Per-tile signal-filtered cohorts | 4 h | £0 |
| 3 | B-019 | CSV / clipboard export of cohort and drill tables | 3 h | £0 |
| 3 | B-020 | Mobile/tablet layouts beyond responsive stack | 6 h | £0 |
| 3 | B-021 | "Compare two cohorts" view | 8 h | £0 |
| 3 | B-022 | Cross-tile linked highlight on hover | 3 h | £0 |
| 3 | B-023 | Auto-deprecate kill verdicts after 2x regime windows | 4 h | £0 |

**Tier 1 total:** ~3 h 25 min · **Tier 2:** 3 hours · **Tier 3:** 45.5 hours ·
**Tier 4:** 30 min. **All-in:** ~52 hours of engineering + ~£0.50 cost.
(Original B-001…B-011 batch ~15 h; redesign-v1 follow-ups B-012…B-023
add ~37 h.)

### Backup gate before Tier 3 (mandatory)
Before any Tier 3 work begins, an explicit backup of `.data/directors.db`
is taken from **Windows-side Python only** (never bash — FUSE rules per
CLAUDE.md). Save as `.data/directors.db.pre-tier3.bak`. Verify the
backup opens cleanly with `PRAGMA integrity_check` before proceeding.
The existing self-healing backup at `.data/directors.db.bak` is not
sufficient on its own — it gets overwritten after every successful
pipeline run, so once you've run anything after the IT/CEF purge it's
gone as a rollback point.

Tier 1 items have no dependencies between them; they can be done in any
sub-order or in parallel by different agents. Tier 3 items are strictly
sequential.

---

## Dependency map

```
Tier 1 (parallel-safe)
  ├── B-002  (LLM sweep)
  ├── B-005  (date sanity)
  ├── B-008  (bisect assert)
  ├── B-006  (atomic pending write)
  └── B-010  (table sort)

Tier 2
  └── B-009  (CAR chart)        ← independent of all parser work

Tier 3 (strict sequence)
  B-011  →  B-001 + B-004  →  B-003
  (delete ITs   (parser fix      (tests lock
   first)        on clean data)   in fixes)

Tier 4
  └── B-007  (after Tier 3 — uses cleaned dataset)
```

---

# TIER 1 — Quick wins (no risk, no dependencies)

## B-002 — LLM sweep over 7 pending-review filings

**Tier:** 1 · **Effort:** 30 min · **Cost:** ~£0.30 · **Depends on:** none

### Problem
Seven RNS filings sit in `.scripts/_pending_review.json` (note: the
file lives next to the parser, not in `.data/`) because the regex-based
parser can't locate a transaction date in their HTML (template variants
— JSE, SIP, foreign issuer). These rows are missing from the dashboard,
an undisclosed gap.

### Acceptance criteria
- Zero rows remain in `_pending_review.json` after the run.
- All 7 fingerprints exist in `transactions` with a valid `date` field
  (passes I1 ISO-format invariant).
- New rows are tagged `parser_source = 'llm'` in the `transactions`
  table (matching the existing convention in `backfill_filings.py`,
  not `'llm_parser'`).
- `eval_signals.py` is re-run after the 7 inserts so any signals
  these rows would trigger are now firing.
- The 7 rows appear correctly on the dashboard's Today / company pages
  after the next `build_dashboard.py` run.

### Files to touch
- `.scripts/llm_parser.py` — already exists. Entry point is
  `parse_with_llm(html, url, rns_id, announced_at, *, run_id, model)`
  (see `.scripts/llm_parser.py:312`).
- `.scripts/run_pending_sweep.py` — new ~50-line wrapper.

### Implementation
1. Load `.scripts/_pending_review.json` (confirm path at runtime; it's
   next to the script, not in `.data/`).
2. For each entry, read cached HTML from `.scripts/_scrape_cache/`.
   If the cache file is missing, log a warning and skip — do not
   re-fetch from Investegate in this script.
3. Call `parse_with_llm(html, url, rns_id, announced_at, run_id=...,
   model=...)` — use the existing signature.
4. Insert into `transactions` via `db.upsert_transaction()` with
   `parser_source='llm'`.
5. Move processed entries out of `_pending_review.json` to
   `.scripts/_resolved_pending.json` for audit.
6. After the loop completes successfully, invoke (via subprocess so
   the per-step isolation matches the pipeline) `python .scripts/eval_signals.py`
   and then `python .scripts/build_dashboard.py`.
7. Print cost summary at end.

### Validation
- Manual eyeball on 2 of the 7 — open Investegate URL, confirm date
  matches LLM extraction.
- Run `audit_dates.py` after — all 5 invariants must still pass.
- Open dashboard; the 7 rows are visible on Today / company pages.

### Out of scope
- Adding per-template regex patches (that's a separate item, deferred).
- Changing the scraper to call LLM proactively on new filings.
- Re-fetching missing cache files.

### Risks
- LLM hallucinates a date. Mitigation: cross-check that returned date
  is within ±60 days of `announced_at`.
- Cost overrun (>£1). Mitigation: hard cost ceiling in the wrapper.
- Missing cache file for one of the 7 fingerprints. Mitigation: log
  and skip; engineer reports the skip to Rupert who can decide on
  re-fetching manually.

---

## B-005 — Reject dates with parsed year < 1990

**Tier:** 1 · **Effort:** 10 min · **Cost:** £0 · **Depends on:** none

### Problem
`_DATE_FMTS` accepts `%d.%m.%y` (two-digit year). Python's `strptime`
pivots at 68/69, so "69" → 1969. Cheap defence-in-depth needed.

### Acceptance criteria
- A test HTML fragment with a transaction date "01.01.69" returns
  `None` from the date parser (was: returned 1969-01-01).
- A test HTML fragment with "01.01.26" still returns 2026-01-01.
- Warning logged when year < 1990 is rejected.

### Files to touch
- `.scripts/parse_pdmr.py` — `_try_one_date()` function.

### Implementation
After successful `strptime`, add:
```python
if parsed.year < 1990:
    # NOTE: parse_pdmr.py does not currently import logging — either
    # use a plain `print(..., file=sys.stderr)` to match existing
    # convention in the file, or add `import logging; logger =
    # logging.getLogger(__name__)` at module top. Engineer's choice;
    # be consistent with whatever the file already does.
    print(f"WARN: rejected suspicious date with year {parsed.year}: {raw}",
          file=sys.stderr)
    return None
```

### Validation
Two unit tests in B-003's eventual test file:
- `test_rejects_pre_1990_year`
- `test_accepts_modern_2digit_year`

### Out of scope
- Changing the date format list itself.
- Wider date-parsing rewrites (would go in B-001 bundle).

### Risks
- None material.

---

## B-008 — Defensive ISO-date assert in performance tracker

**Tier:** 1 · **Effort:** 10 min · **Cost:** £0 · **Depends on:** none

### Problem
`backtest.py:_first_trading_date_after()` uses `bisect_right` over a
sorted list of date strings. Lexicographic sort only equals
chronological sort if every date is YYYY-MM-DD. If a non-ISO date
sneaks in, bisect silently returns the wrong entry day → wrong CAR
calculation, silent corruption.

### Acceptance criteria
- An assert fires loudly if any date in the loop input is not ISO
  format.
- Backtest run completes successfully on the current clean dataset.

### Files to touch
- `.scripts/backtest.py` — `_first_trading_date_after()`.

### Implementation
At top of the function, before sorting:
```python
ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
for d in dates:
    assert ISO_DATE_RE.match(d), f"Non-ISO date in backtest: {d!r}"
```

### Validation
Run `backtest.py` end-to-end — completes without assertion error.
This is a no-op on healthy data; the value is the loud failure mode if
something later regresses.

### Out of scope
- Refactoring the date-handling code to use real `date` objects (larger
  scope, considered for Tier 4+).

### Edge cases
- Empty `dates` list (new ticker with no historic prices). The assert
  does not fire because there's nothing to iterate. The existing code
  path beyond the assert must still handle this case — confirm the
  function returns an explicit None or sentinel rather than raising
  `IndexError` on the empty list.

### Risks
- None.

---

## B-006 — Atomic pending-review write in repair_dates.py

**Tier:** 1 · **Effort:** 30 min · **Cost:** £0 · **Depends on:** none

### Problem
`repair_dates.py` Case B path deletes from `signals`, `paper_trades`,
then `transactions`, and writes the row metadata to `_pending_review.json`
in a single batch at end-of-loop. If the script crashes mid-loop after
a delete but before the batch JSON write, that row is gone from the DB
and not yet in pending. Low probability but data-losing.

### Acceptance criteria
- Every row deleted in Case B is appended to `_pending_review.json`
  before the next iteration begins.
- Pending JSON file is read-modify-write under a tempfile-then-rename
  pattern (no half-written file possible).
- Test: kill the script after the first Case B deletion (use a `--abort-after`
  CLI flag in test mode) — the pending file contains exactly 1 entry,
  the DB is missing exactly 1 row, and re-running the script later picks
  up cleanly without duplicate work.

### Files to touch
- `.scripts/repair_dates.py` — Case B loop.

### Implementation
The existing code at `repair_dates.py:212` already opens a SQLite
transaction via `with conn:`. The gap is purely the filesystem-side
write of `_pending_review.json`, which currently happens once at
end-of-loop. The fix is to write the pending file **before** committing
the SQL transaction for each row.

1. Inside the existing `with conn:` block, on every Case B row:
   - Read current `_pending_review.json` from disk (default `[]`).
   - Append the new entry.
   - Write to a sibling temp file `_pending_review.json.tmp` (in the
     same directory so `os.replace` is atomic on Windows).
   - Call `os.replace(tmp, final)` to atomically swap.
2. Only **after** the pending file is durably on disk, let the
   `with conn:` block close to commit the DB DELETEs. If the script
   crashes between the tempfile write and the DB commit, the next run
   sees the row still in the DB and the row already in pending — a
   harmless duplicate flag the script can detect and skip.
3. Add a `--abort-after-n` CLI flag (default disabled) to support the
   crash-test described in the AC.
4. On script startup, clean up any orphan `_pending_review.json.tmp`
   left by a previous crashed run (simple `os.path.exists` + unlink).

### Validation
- New script `.scripts/test_repair_dates_atomicity.py` (~40 lines): seed
  a tiny test DB, run repair_dates with `--abort-after-n 1`, assert
  pending file has 1 entry and DB has correct row count.

### Edge cases
- Orphan `.tmp` file from previous crash — cleanup on startup (step 4).
- Pending file is currently being read by another process (rare). Not
  in scope; single-user assumption.

### Out of scope
- Re-architecting repair_dates.py more broadly.

### Risks
- File-locking under concurrent runs. Mitigation: this script is
  single-user / single-runner; not a concurrency concern.

---

## B-010 — Transaction table sort + today's-date format

**Tier:** 1 · **Effort:** 30 min · **Cost:** £0 · **Depends on:** none

### Problem
Transaction tables on Today, Performance, and Company pages are not
consistently sorted with the freshest dealing at the top. Additionally,
rows dated today display the hour-level filing time rather than the
date — visually noisy and inconsistent with older rows.

### Acceptance criteria
- All transaction tables across Today, Performance, and per-Company
  pages render rows sorted by `(date DESC, announced_at DESC)`.
- Rows whose `date` equals today render the date column as either
  "Today" or the bare date (e.g. "18 May") — **not** a `HH:MM` time.
- Same sort applies to the cluster expand-out view on the Active
  Clusters panel.
- Yesterday and earlier rows continue to show the bare date — unchanged.

### Files to touch
- `.scripts/export_dashboard_json.py` — sort order applied when
  generating `dealings.json` / `signals.json`.
- `.scripts/dashboard/render_index.py` — the Today page. The
  hour-level format bug is at `_format_time()` (lines 21–34 in the
  current file): when the row's date equals today, it returns
  `dt.strftime("%H:%M")` — this is the precise line to replace.
- `.scripts/dashboard/render_performance.py` — date column formatter.
- `.scripts/dashboard/render_company.py` — date column formatter.
- `.scripts/dashboard/render_helpers.py` — existing shared helpers
  module; add the new `format_dashboard_date` helper here (do **not**
  create a new file).
- Active-clusters expand template (likely inline in the Today /
  index render or a small JS helper invoked from it).

### Implementation
1. Add `format_dashboard_date(date_str, today_iso)` helper to
   `.scripts/dashboard/render_helpers.py` — returns `"Today"` if
   `date_str == today_iso`, else `datetime.fromisoformat(date_str).strftime("%-d %b")`
   (use `%#d` on Windows).
2. Replace the `_format_time()` body in `render_index.py:21-34` and any
   sibling time-formatted output in the other render scripts so they
   call `format_dashboard_date` instead.
3. Ensure SQL queries that drive the tables have explicit `ORDER BY`
   clauses: `date DESC, announced_at DESC NULLS LAST, ticker ASC` as the
   tiebreaker chain. (SQLite supports `NULLS LAST` in modern builds; if
   not available, use `ORDER BY date DESC, (announced_at IS NULL),
   announced_at DESC, ticker ASC`.)

### Validation
- Open the dashboard. Visual check: Today's rows show "Today"
  (or "18 May"), older rows show their date. Top row of each table is
  the freshest.
- Quick sanity SQL: `SELECT date FROM transactions ORDER BY date DESC
  LIMIT 1` — top of every page matches this date.

### Edge cases
- A row has empty / NULL `announced_at` (rare but exists in historic
  backfilled data). The `NULLS LAST` tiebreaker ensures these don't
  spuriously appear at the top of a date group.

### Out of scope
- Adding a user-toggleable sort. We're picking one consistent default,
  not a feature.
- Restructuring the table columns.

### Risks
- Multiple rows share the same `announced_at` second — tertiary tie on
  `ticker` is fine.

---

# TIER 2 — Chart honesty

## B-009 — CAR chart shows true 12 months + honest gaps

**Tier:** 2 · **Effort:** 1 h · **Cost:** £0 · **Depends on:** none

### Problem
The "Cumulative net CAR — trailing 12 months" chart in
`render_performance.py` is labelled 12 months and has 13 monthly slots
on the x-axis, but `_sparkline()` in `export_dashboard_json.py` only
generates 9 weekly buckets (weeks t-12 through t-4 — about 2 months).
The chart pads the front with nulls and forward-fills empty buckets
from `last_val = 0.0`, producing a misleading flat 0% line for most
of the chart.

### Acceptance criteria
- `_sparkline()` returns 13 **monthly** buckets covering a full 12
  months (M-12, M-11, … M-1, Now).
- Existing **median** aggregation is preserved (Rupert decision,
  2026-05-18 — do not switch to mean; the cluster-mean swing risk is
  why median was chosen originally).
- Empty buckets (no signals yet matured) render as Chart.js **gaps**,
  not as 0%. (`last_val` initialised to `None`, not `0.0`; `spanGaps:
  true` already set in the chart config — confirm this is on.)
- Visual check: when looking at the chart, you can clearly distinguish
  "we don't have data here yet" (gap) from "the data here is 0%"
  (line at zero).
- FTSE All-Share benchmark line remains visible as a dashed line.
- New unit test in `.scripts/test_sparkline.py` (~30 lines): seed a
  fixture of signals + matured CARs, call `_sparkline(period_months=12)`,
  assert the return is a list of length 13 with the expected month
  labels and median values. Test must run under `python -m unittest`.

### Files to touch
- `.scripts/export_dashboard_json.py` — `_sparkline()` function.
- `.scripts/render_performance.py` — `_diagnostics_chart_section()`
  schema check / chart config.

### Implementation
1. Rewrite `_sparkline()`:
   - Accept a `period_months` parameter (default 12).
   - Build buckets by walking back from today, one month at a time, for
     `period_months + 1` buckets (so M-12 through Now inclusive).
   - For each bucket, compute **median** net CAR of signals that fired
     in that bucket window and have matured to T+90 — using
     `statistics.median`, exactly as the current function does.
   - Return a list of `{label, value}` where `value` is `None` when no
     matured signals exist in the window.
2. In `render_performance.py`:
   - Update label generation to read `period_months` from data, not
     hard-code 13.
   - Confirm `spanGaps: true` is in the Chart.js dataset config (add
     if missing).
   - Remove any forward-fill that initialises from 0.0.

### Validation
- Run the build, open Performance page. Chart shows clear gaps for
  recent months where T+90 hasn't matured yet, not a flat zero line.
- Manual cross-check: pick a month bucket with data, manually average
  net CAR for signals fired in that month from `_backtest_results.csv`
  — should match the chart value within rounding.

### Out of scope
- Switching the chart to weekly resolution.
- Adding a confidence band.

### Risks
- Median is robust to single outliers but a sparse bucket (1–2 signals
  matured) is still noisy. Acceptable for now; flag for future
  "median + IQR band" enhancement if confidence intervals become
  important.

---

# TIER 3 — Major data work (strict sequence)

## B-011 — Exclude investment trusts / CEFs / VCTs / REITs

**Tier:** 3 (1st) · **Effort:** 3 h · **Cost:** £0–0.20 · **Depends on:** none
**Blocks:** B-001+B-004 (do these on the cleaned dataset)

### Problem
Investment trusts, VCTs, REITs and closed-end funds have very different
insider-dealing dynamics from operating companies. Director purchases
at NAV-discount are a routine governance signal, not an informational
edge. Including them in clusters and signal scoring pollutes the
buy-side signal tiers (S1, T2).

### Decision (Rupert, 2026-05-18)
Hard delete from the database. No soft-flag alternative.

### Acceptance criteria
- **Pre-flight backup** of `.data/directors.db` exists at
  `.data/directors.db.pre-it-purge.bak` and passes
  `PRAGMA integrity_check` before any DELETE runs. Backup must be
  taken from Windows-side Python (CLAUDE.md FUSE rule).
- A classifier identifies IT / CEF / VCT / REIT issuers among current
  tickers with high precision (target: zero false positives in the
  preview list; some false negatives acceptable).
- AIC member list scrape produces **at least 300 entries**; if it
  produces fewer, the run aborts and asks the engineer to investigate
  (defensive — if the AIC page changes structure we don't want to
  silently delete on weak classification).
- Tickers present in `transactions` but missing from `tickers_meta`
  are populated into `tickers_meta` by the classifier (a `INSERT OR
  IGNORE` step) before the exclusion column is set — otherwise the
  filter would silently miss them.
- Preview list is written to `.data/_excluded_it_cef.preview.csv` with
  columns `(ticker, company, source, signed_off_by, signed_off_at)`
  and presented to Rupert for sign-off **before** any DELETE is
  executed.
- After delete: zero transactions remain whose ticker is in the
  excluded list.
- After delete: zero signals remain referencing deleted fingerprints.
- After delete: `eval_signals.py` is re-run so any sibling signals
  (e.g. S1 clusters that included a deleted IT row) are recomputed
  against the cleaned dataset.
- After delete: backtest re-run completes and
  `.data/_backtest_results.csv` is regenerated against the cleaned
  dataset.
- After delete: `build_dashboard.py` re-runs so the dashboard JSON
  + HTML reflect the cleaned dataset. (Without this step the deletion
  is invisible in the UI.)
- Scrape pipeline rejects future filings from excluded issuers at
  ingest (no further IT rows accumulate).
- `.data/_excluded_it_cef.csv` lists every deleted fingerprint with
  ticker / company / reason — audit trail.
- Audit dates invariant suite (`audit_dates.py`) still passes.

### Files to touch
- `.scripts/classify_issuers.py` — **new**. Builds the excluded-issuer
  set.
- `.scripts/exclude_investment_trusts.py` — **new**. The one-time
  deletion runner.
- `.scripts/run_scrape.py` — add ingest-time filter.
- `.scripts/db_schema.sql` — add `is_excluded_issuer INTEGER NOT NULL
  DEFAULT 0` column to `tickers_meta` (table currently defined around
  line 63 of the schema file).
- `.scripts/migrations/003_add_is_excluded_issuer.sql` — **new**
  migration file applied by `db.py`'s existing migrator (see the
  `MIGRATIONS_DIR / "002_add_parser_source.sql"` reference in
  `db.py:62-63` for the convention).
- `.scripts/backtest.py` — no code changes; will re-run on cleaned data.

### Implementation
0. **Pre-flight backup**. From Windows-side Python (NOT bash —
   CLAUDE.md FUSE rule), copy `.data/directors.db` to
   `.data/directors.db.pre-it-purge.bak`. Verify the backup opens and
   `PRAGMA integrity_check` returns `ok`. Abort if not.
1. **Classifier** (`classify_issuers.py`):
   - Source A: AIC member list (scrape `theaic.co.uk`, list of ~400
     UK investment trusts and VCTs). Cache locally. Assert
     `len(aic_list) >= 300` or abort.
   - Source B: Yahoo Finance `quoteType` per ticker — flags `ETF`,
     `MUTUALFUND`, closed-end funds via security type. Cache results
     in `.scripts/_classifier_cache.json` to avoid re-hitting Yahoo
     on subsequent runs.
   - Source C: Name regex — `(?i)(investment\s+trust|VCT|REIT|capital\s+trust)`.
     Apply only to companies that don't already match A or B (regex
     is the catch-all, not the primary).
   - **Populate missing `tickers_meta`** first: `INSERT OR IGNORE`
     every distinct `transactions.ticker` so the classifier has a
     row to update for every ticker that's actually in the data.
   - Write classification to `tickers_meta.is_excluded_issuer` (1 or 0).
2. **Preview** (`exclude_investment_trusts.py --preview`):
   - Write all rows that would be deleted to
     `.data/_excluded_it_cef.preview.csv` with columns `(ticker,
     company, source, signed_off_by, signed_off_at)` — last two
     columns blank, for Rupert to fill in.
   - Print row counts and signal-impact summary to stdout.
   - **Pause for Rupert sign-off** — no DELETE without `--confirm`.
     The engineer reads the preview CSV with Rupert before running
     the next step.
3. **Delete** (`--confirm`):
   - Verify the preview CSV has `signed_off_by` filled in for the
     summary row; abort if not.
   - Log each fingerprint to `.data/_excluded_it_cef.csv` first
     (audit trail, separate from preview).
   - DELETE in FK-safe order: `signals` → `paper_trades` →
     `transactions`.
   - Single SQL transaction so a crash mid-delete rolls back cleanly.
4. **Signal re-eval**:
   - Trigger `eval_signals.py` after delete completes.
   - Sibling signals (e.g. S1 cluster that included a deleted IT)
     are recomputed on the cleaned data.
5. **Backtest re-run**:
   - Trigger `backtest.py` after `eval_signals.py` completes.
   - Confirm `_backtest_results.csv` regenerates without IT/CEF
     fingerprints.
6. **Dashboard rebuild**:
   - Trigger `build_dashboard.py` after backtest completes.
   - Confirm the rendered dashboard no longer shows excluded tickers
     anywhere (tables, clusters, performance tracker).
7. **Scrape filter**:
   - In `run_scrape.py`, after a new filing's ticker is resolved, look
     up `is_excluded_issuer`; if 1, skip insert and log to
     `.data/_excluded_at_ingest.log`.

### Validation
- Eyeball the preview list before sign-off. Look for false positives
  (e.g. is "Trustpilot" caught by the name regex? It should not be).
- After delete: `SELECT COUNT(*) FROM transactions t JOIN tickers_meta
  m ON t.ticker = m.ticker WHERE m.is_excluded_issuer = 1` returns 0.
- Re-open the dashboard — clusters panel should show fewer items;
  performance tracker numbers should change slightly (cleaner signal).

### Out of scope
- Soft-flag alternative (explicitly rejected by Rupert).
- Building a UI to flip the exclusion flag on/off per ticker.
- Re-classifying every quarter — initially a one-shot.

### Risks
- **False positive deletes an operating company.** Mitigation: the
  preview / sign-off step is mandatory. The audit CSV makes reversal
  possible by re-running the original scraper on those URLs.
- **AIC list is incomplete / out-of-date.** Mitigation: combining 3
  sources reduces this risk substantially. Acceptable to leave a
  handful of false negatives — Rupert can flag them ad hoc later.
- **Yahoo `quoteType` rate-limit.** Mitigation: cache results in
  `.scripts/_classifier_cache.json`; budget at most 2,500 lookups.
- **Backtest re-run takes longer than 10 minutes.** Mitigation:
  acceptable; just plan for it.

---

## B-001 + B-004 — Table-aware parser (multi-row + director-name)

**Tier:** 3 (2nd) · **Effort:** 3.5 h · **Cost:** £0
**Depends on:** B-011 (run on cleaned dataset to avoid wasted parsing of IT rows)

### Pre-step (must run before code work begins)
`pip install beautifulsoup4` — grep of `.scripts/` shows no current
import of `bs4` / `BeautifulSoup`. The parser is currently pure-regex.
This is a **new dependency** — confirm with Rupert before adding (it's
a stable, pure-Python lib with no version drama; just don't sneak it
in unannounced).

### Problem
Two related bugs share the same root cause — a regex-based extractor
that doesn't understand HTML table structure:

- **B-001:** Multi-row transaction tables (DRIPs, SIP/SAYE bulk
  filings, year-end compliance disclosures) only contribute their
  first row. Filing 9541612 (National Grid, Jacqueline Agg) has 4
  transactions, only 1 is in the DB.
- **B-004:** Director-name regex captures across `<td>` boundaries,
  producing values like "Kingfisher plc\nb" — picks up the company
  name plus the next field label instead of the actual director.

### Acceptance criteria
- Filing 9541612 produces **4 transactions** in the DB, one per
  transaction-table row, with correct dates 2024-08-13, 2025-02-12,
  2025-08-13, 2026-01-14.
- No director-name string contains `\n`, the company name, or known
  field labels (`LEI`, `b`, etc.). Validation SQL: `SELECT director
  FROM transactions WHERE director LIKE '%plc%' OR INSTR(director,
  CHAR(10)) > 0` returns zero rows. (SQLite `LIKE` does **not**
  interpret `\n` — must use `CHAR(10)` or `INSTR`.)
- Re-running the parser against the existing cached HTML corpus
  recovers all previously-truncated rows.
- **Preview-and-sign-off gate (new — Rupert decision, 2026-05-18):**
  Before the corpus re-parse writes anything to the live DB, the
  engineer produces a preview at `.data/_reparse_corpus_preview.csv`
  showing: total rows that would be created or modified, count of
  director-name fixes, 5 sample diffs (existing row → proposed row).
  Rupert signs off; no DB writes happen without `--confirm`.
- `eval_signals.py` re-runs after the corpus re-parse to recompute
  any signals affected by newly-extracted rows (e.g. multi-row bulk
  filings may now form a cluster that wasn't visible before).
- `build_dashboard.py` re-runs after `eval_signals.py` so the
  dashboard reflects the new rows.
- All 5 `audit_dates.py` invariants still pass after re-parse.
- Excluded issuers (B-011) are still filtered out — no IT rows
  reappear.

### Fingerprint stability rule
The existing fingerprint scheme keys on (date, ticker, director,
type, shares, price). If the new parser extracts a corrected director
name for a row that already exists, the fingerprint changes, so the
old row becomes orphaned and a new row inserts — a duplicate.

The engineer must implement one of:
- **Option A (preferred):** detect "this looks like a corrected
  version of an existing row" by matching on (date, ticker, type,
  shares, price) and update the existing row in place rather than
  insert. Log every such update to `.data/_reparse_director_fixes.log`.
- **Option B:** add a fingerprint-version field; old rows are kept
  but marked superseded. More machinery, less risk of accidentally
  merging genuinely-different rows.

Rupert to decide A or B at preview-gate sign-off.

### Files to touch
- `.scripts/parse_pdmr.py` — main parser.
- `.scripts/_scrape_cache/` — input only, no changes.
- `.scripts/reparse_corpus.py` — **new** ~50-line script to re-parse
  every cached HTML file and upsert any newly-extracted rows.

### Implementation
1. Switch transaction-row extraction from regex-on-flat-text to
   table-aware parsing using BeautifulSoup:
   - Locate the transaction table by header pattern ("Date of
     transaction", "Price", "Volume").
   - Iterate every data row beneath the header.
   - For each row, extract cells by column index → date, price,
     volume.
2. Director-name extraction:
   - Anchor on the "Name" or "Personal details" header cell.
   - Read only the **next single cell** in the table; do not cross
     row boundaries; reject strings containing newlines or matching
     `(plc|Ltd|LLP)\b`.
3. Build a corpus re-parse script that walks every cached HTML file,
   re-runs the parser, and upserts any new rows (existing fingerprints
   stay unchanged).
4. Respect `is_excluded_issuer` — if the resolved ticker is excluded,
   skip the upsert.

### Validation
- Manual: open filing 9541612 in the cache, run parser standalone,
  assert 4 rows out with the expected dates.
- Bulk: `SELECT COUNT(*) FROM transactions` before vs. after the
  corpus re-parse — count should rise by tens of rows (every
  previously-truncated bulk filing now contributes its full set).
- `SELECT director FROM transactions WHERE director LIKE '%plc%' OR
  INSTR(director, CHAR(10)) > 0` — should return zero rows. (Earlier
  draft used `LIKE '%\n%'` — incorrect; SQLite doesn't escape `\n`
  inside LIKE patterns.)
- `audit_dates.py` — all green.

### Out of scope
- Adding per-template patches for JSE / SIP / foreign layouts (those
  7 specific filings — handled separately in B-002).
- Switching from regex date parsing to a full table-aware date
  approach (deferred; current dot-separated date fix is good enough).

### Risks
- **Re-parse introduces signal duplicates** if any signal-evaluation
  state was keyed on the truncated row count. Mitigation: the
  preview-and-sign-off gate above + the `eval_signals.py` re-run AC
  cover this.
- **BeautifulSoup is a new dependency** (confirmed via grep of
  `.scripts/`). Install with `pip install beautifulsoup4` as the
  pre-step at top of this scope. Pure-Python, no version drama.
- **Fingerprint instability under director-name fixes** — see
  "Fingerprint stability rule" section above. Mitigation is one of
  the two Options A / B, chosen at sign-off.

---

## B-003 — Unit tests on the parser (Layer 2)

**Tier:** 3 (3rd) · **Effort:** 2 h · **Cost:** £0
**Depends on:** B-001+B-004 (tests lock in the **fixed** behaviour)

### Problem
The parser has no automated tests. Recent date-integrity work
uncovered 4 distinct bugs that trivial unit tests would have caught
instantly. Without tests, every parser change is "fingers crossed."

### Acceptance criteria
- New file `.scripts/test_parser.py` runs under `python -m unittest`
  with zero failures on the current codebase.
- Coverage includes at least the 4 known historical bug patterns:
  - Dot-separated date `"05.05.26"` extracts correctly.
  - Cross-cell date label (e.g. `<td>Date</td><td>05.05.26</td>`)
    extracts correctly.
  - No silent fallback to a non-transaction date (page chrome
    timestamps must be rejected).
  - Multi-row table extracts all rows (B-001 acceptance test
    embedded as a regression test).
- Plus boundary cases: B-005 (year < 1990 rejected), B-004 (newline
  in director name rejected).
- Fixture directory `.scripts/fixtures/parser/` contains ~10 small
  HTML files paired with `.expected.json` files of what should
  extract.
- Smoke test against the live cache is **deterministic**: uses
  `random.seed(0)` so the same 20 files are picked every run.
- Tests pass on Windows (the project's native dev environment) —
  use `pathlib.Path`, never raw forward/backslash strings.
- CI-ready: a `run_tests.bat` exits non-zero on failure.

### Files to touch
- `.scripts/test_parser.py` — **new**.
- `.scripts/fixtures/parser/` — **new** directory with HTML/JSON
  pairs.
- `run_tests.bat` — **new** Windows wrapper.

### Implementation
1. Build fixture HTML files by copying minimal slices from real
   cached filings — one per bug pattern.
2. For each fixture, hand-write the expected `extract()` output to
   a sibling `.expected.json`.
3. `test_parser.py`:
   - Discover all `.expected.json` files.
   - For each: load HTML, run parser, assert result equals expected
     JSON.
4. Add a smoke test that parses 20 random files from the live cache
   and asserts non-empty results (catches "parser crashes on edge
   case" regressions without overfitting on specific outputs).

### Validation
- `python -m unittest .scripts/test_parser.py` → all green.
- Deliberately break the parser (e.g. comment out the date-label fix)
  → tests fail loudly with clear messages.

### Out of scope
- End-to-end pipeline tests (just the parser layer for now).
- Property-based / fuzz testing.

### Risks
- Brittle fixtures break on legitimate parser improvements.
  Mitigation: use minimal HTML slices, not full pages.

---

# TIER 4 — Polish

## B-007 — I6 informational late-filings badge

**Tier:** 4 · **Effort:** 30 min · **Cost:** £0 · **Depends on:** Tier 3 complete
(badge reads cleaned dataset)

### Problem
I5 was relaxed from 365 → 1,460 days to allow legitimate late filings.
We have no visibility into how many late filings exist — useful as a
data-quality signal but currently invisible.

### Acceptance criteria
- New invariant `I6` in `audit_dates.py` counts (does not fail on)
  transactions where `(announced_at - date) > 90 days`.
- New row in the dashboard's data-quality panel shows the count as a
  neutral grey badge ("12 late-disclosed transactions").
- Badge never shows red — purely informational.

### Files to touch
- `.scripts/audit_dates.py` — add `_run_I6()` next to the existing
  `_run_I1` … `_run_I5` functions.
- `.scripts/export_dashboard_json.py` — surface I6 result in the
  health-panel payload.
- `.scripts/dashboard/render_health_panel.py` — render the grey
  badge.

### Implementation
1. `_run_I6()` returns a dict `{"count": N, "examples": [first 3 tickers]}`.
2. Health-panel payload gains an `I6` key.
3. Template renders: `<span class="badge badge-info">{count}
   late-disclosed transactions</span>`.

### Validation
- Visual check on the dashboard data-quality panel.
- The count matches `SELECT COUNT(*) FROM transactions WHERE
  announced_at IS NOT NULL AND announced_at <> '' AND
  julianday(announced_at) - julianday(date) > 90`. The `IS NOT NULL`
  guard matters because `julianday('')` returns NULL and silently
  excludes empty-string rows, which is fine for the count but worth
  being explicit.

### Out of scope
- Drill-down view of late filings.
- Changing the I5 threshold further.

### Risks
- None.

---

# REDESIGN V1 FOLLOW-UPS — 2026-05-19

Items below were surfaced during the Performance page redesign v1
build (completed 2026-05-19). They are sequenced after the original
B-001…B-011 set but use the same tier conventions: Tier 1 = quick
wins, Tier 2 = small enhancements, Tier 3 = major v1.1 features,
Tier 4 = polish.

The Tier 3 items in this section are explicitly **v1.1 candidates** —
not committed to a release, queued for scoping once v1 ships and
real usage exposes which features matter most.

---

## B-012 — Performance redesign v1.1 small-fix bundle

**Tier:** 1 · **Effort:** 30 min · **Cost:** £0 · **Depends on:** none

### Problem
Five small defects identified in the redesign v1 QA pass. Each is a
single-line or doc-only change — bundled to amortise the change
ceremony.

### Acceptance criteria
- `export_dashboard_json.py:1788` — `sum(1 for _ in csv_path.open(...))`
  no longer raises `ResourceWarning`. The open call is wrapped in a
  `with` block.
- Running `export_dashboard_json.py --verbose` prints the summary
  line **once**, not twice. Pick whichever of the duplicate call
  sites is the canonical location.
- `classify_role_diagnostic.py` source contains no em-dash
  characters (U+2014). Replaced with `--`. PowerShell cp1252 console
  no longer shows `ù` artifacts on stdout.
- Spec `docs/specs/performance-page-redesign-v1.md` §5.5 size-cap
  paragraph says "~1 MB per drill file" (or whatever the current
  data-driven reality is), not "200 KB".
- All 5 fixes ship in one commit — easier to revert if any one
  breaks.

### Files to touch
- `.scripts/export_dashboard_json.py` — line 1788 fix + dedupe
  duplicate `--verbose` print between `main()` and `run()`.
- `.scripts/classify_role_diagnostic.py` — em-dash sweep.
- `docs/specs/performance-page-redesign-v1.md` — §5.5 doc fix.

### Implementation
1. **ResourceWarning at line 1788.** Current code:
   `n = sum(1 for _ in csv_path.open(...))`. Replace with:
   `with csv_path.open(...) as f: n = sum(1 for _ in f)`.
2. **Duplicate verbose print.** Search for the summary print string
   in `export_dashboard_json.py`; keep the one in `run()`, drop the
   one in `main()` (or vice versa — engineer's call, but document
   the choice in the commit).
3. **Em-dash sweep.** Open `classify_role_diagnostic.py` in a UTF-8
   aware editor; replace `—` with `--` everywhere. Confirm no other
   smart-quote / typographic characters remain.
4. **Spec doc fix.** Edit §5.5; change "200 KB" → "~1 MB per drill
   file" (or current measured size, rounded).

### Validation
- `python .scripts/export_dashboard_json.py --verbose` — no
  ResourceWarning, summary line appears exactly once.
- `python .scripts/classify_role_diagnostic.py` in PowerShell —
  output contains no `ù` artifacts.
- Open spec, confirm §5.5 reads "~1 MB per drill file".

### Out of scope
- Larger refactor of `export_dashboard_json.py` verbose-logging
  infrastructure.
- Unicode normalisation across the rest of the `.scripts/` tree
  (only the one diagnostic file is in scope here).

### Risks
- None material.

---

## B-013 — DRY the slugify helper

**Tier:** 1 · **Effort:** 15 min · **Cost:** £0 · **Depends on:** none

### Problem
Three implementations of the same cohort-key-to-URL-slug regex live
in the codebase:
- `_slug_for_url` in `.scripts/dashboard/render_performance.py`
- `_drill_slug_for_key` in `.scripts/build_dashboard.py`
- A JS mirror in the cohort-tile auto-wire script (embedded in the
  rendered HTML)

Three sources of truth for one transformation — guaranteed to drift.

### Acceptance criteria
- A single Python function `slug_for_url(key)` lives in
  `.scripts/dashboard/render_helpers.py`.
- Both `render_performance.py` and `build_dashboard.py` import and
  use this function — the local `_slug_for_url` and
  `_drill_slug_for_key` definitions are deleted.
- The JS mirror in the cohort-tile auto-wire script is annotated
  with a comment: `// must match Python slug_for_url in
  render_helpers.py — keep in sync`.
- The 18 cohort keys in the current build produce the same slugs
  before and after this refactor (byte-for-byte filename match).

### Files to touch
- `.scripts/dashboard/render_helpers.py` — new `slug_for_url()`.
- `.scripts/dashboard/render_performance.py` — delete local def,
  add import.
- `.scripts/build_dashboard.py` — delete local def, add import.
- JS mirror — add comment, no code change.

### Implementation
1. Pick whichever of the two Python implementations is canonical
   (likely the one in `render_performance.py` — verify they're
   identical first).
2. Move to `render_helpers.py` as `slug_for_url(key: str) -> str`.
3. Replace both call sites with the imported version.
4. Run the dashboard build, diff the `performance_*.json` filenames
   before and after — must be identical.

### Validation
- Diff filename list of `outputs/performance_*.html` before and
  after the refactor — zero diff.
- `python -m unittest discover -s .scripts -p "test_*.py"` — full
  sweep still green.

### Out of scope
- Porting the JS slug regex to a separate JS module (would require
  build-step refactor — overkill for v1.1).

### Risks
- Subtle Python-vs-JS regex behaviour difference. Mitigation: the
  filename byte-diff check catches any divergence.

---

## B-014 — Thread real horizon base_rate into drill-page status pill

**Tier:** 1 · **Effort:** 30 min · **Cost:** £0 · **Depends on:** none

### Problem
`render_performance_drilldown._base_rate_for_horizon` is currently
hard-coded to `return 50.0`. The drill page's status pill always
shows a 50% baseline, regardless of horizon. The real per-horizon
base rate should come from the backtest aggregates.

### Acceptance criteria
- The drill page status pill displays the actual base rate for the
  selected horizon (T+1 / T+21 / T+90), reading from
  `payload.horizon_aggregates[horizon].base_rate`.
- The backend exporter (`export_dashboard_json.py`) includes a
  `horizon_aggregates` key in each `performance_<cohort>.json` drill
  payload, with one entry per horizon containing `base_rate` and
  whatever other per-horizon stats are needed by the pill.
- If `horizon_aggregates` is missing (legacy payloads), the pill
  falls back to the existing 50.0 default and logs a warning —
  no crash.
- Visual check: open three drill pages for cohorts with different
  base rates, confirm the pill values differ.

### Files to touch
- `.scripts/export_dashboard_json.py` — emit
  `horizon_aggregates` in each cohort drill payload.
- `.scripts/dashboard/render_performance_drilldown.py` —
  `_base_rate_for_horizon()` reads from payload, with the 50.0
  fallback.

### Implementation
1. In `export_dashboard_json.py`, where each cohort drill payload
   is assembled, add a `horizon_aggregates` dict: keys are horizon
   names (`"T+1"`, `"T+21"`, `"T+90"`), values are dicts containing
   at minimum `base_rate` (the cohort's all-signals positive-CAR
   rate at that horizon).
2. The base rate source: `_backtest_results.csv` filtered to the
   cohort's fingerprints, grouped by horizon, `% of rows with
   net_car > 0`.
3. In `_base_rate_for_horizon(payload, horizon)`: read
   `payload["horizon_aggregates"][horizon]["base_rate"]`. If KeyError
   or missing, fall back to 50.0 and `logger.warning(...)`.

### Validation
- Inspect a freshly-rendered drill HTML — pill displays a non-50%
  number for at least one horizon on at least one cohort.
- Manual cross-check: pick a cohort, manually compute the % of
  positive net_cars at T+21 from `_backtest_results.csv`, confirm it
  matches the pill.

### Out of scope
- Fetching from `signals.json` client-side (rejected — keeps
  drill payloads self-contained).
- Confidence intervals on the base rate.

### Risks
- Backwards compatibility with cached payloads. Mitigation: the
  fallback path handles missing `horizon_aggregates` gracefully.

---

## B-015 — Delete or archive stale dashboard/*.html static templates

**Tier:** 1 · **Effort:** 20 min · **Cost:** £0 · **Depends on:** none

### Problem
The `dashboard/` directory contains static HTML files
(`performance.html`, `index.html`, etc.) that are leftover templates
from a pre-Flask-rendered era. Flask's `server.py` serves
`outputs/`, not `dashboard/`. The stale files caused ~30 minutes of
confusion during FE1 verification — the engineer was editing
templates that had no effect on the live page.

### Acceptance criteria
- Either: every stale file in `dashboard/` is deleted; OR every
  stale file is moved to `dashboard/_legacy/` with a `README.md`
  explaining "these are not authoritative, the live dashboard is
  built from `.scripts/dashboard/render_*.py` into `outputs/`".
- `CLAUDE.md` project guide gains a line noting which directory is
  authoritative for the rendered dashboard (already mentioned in
  MEMORY.md but should be in the project guide too).
- Flask `server.py` still serves the dashboard correctly after the
  cleanup — visual check on `localhost:8000`.

### Files to touch
- `dashboard/*.html` — delete or move to `_legacy/`.
- `dashboard/_legacy/README.md` — new (if moving rather than
  deleting).
- `C:\Dev\DirectorsDealings\CLAUDE.md` — add note about
  dashboard/ vs outputs/ authority.

### Implementation
1. Confirm which `dashboard/*.html` files are still referenced
   anywhere — `grep -r "dashboard/.*\.html"` across the project.
   Anything referenced by `server.py` or a render script stays.
2. For each unreferenced file, choose delete vs archive. Recommend
   archive — cheap insurance, future-proof if anyone wants to
   reference the old template structure.
3. If archiving: create `dashboard/_legacy/README.md` with a clear
   "DO NOT EDIT — see `.scripts/dashboard/render_*.py` for the live
   templates" header.
4. Update `CLAUDE.md` with a 2-line "where the dashboard lives" note
   next to the existing FUSE rules.

### Validation
- After cleanup: open `localhost:8000`, click through Today /
  Performance / a company page — all render correctly.
- `grep -r "dashboard/.*\.html" .` shows zero references to any
  moved/deleted files.

### Out of scope
- Renaming `dashboard/` to something less ambiguous (would touch
  many config files; defer).

### Risks
- Hidden reference to a stale template that grep misses. Mitigation:
  archive (not delete) on the first pass; promote to delete a sprint
  later if no regressions.

---

## B-016 — Clean up RNS scraper garbage in role field

**Tier:** 2 · **Effort:** 2 h · **Cost:** £0 · **Depends on:** none

### Problem
The Sprint 2 corpus diagnostic flagged ~22 rows classified as
`role = None` whose `role` field contains body-text fragments
rather than job titles. Examples:
- "Number of shares purchased"
- "the business to capitalise on opportunities"
- "ing Officer, who purchased…"

These are **RNS scraper / `parse_pdmr.py` extraction bugs**, not
classifier bugs. The extractor is mis-anchored — picking up text
that crosses `<td>` boundaries or grabbing prose adjacent to a
"Position / status" header instead of the cell value.

### Acceptance criteria
- After fix, re-running `classify_role_diagnostic.py` shows the
  None% rate drop by ~1-2pp (the 22 garbage rows now classify
  correctly, or — if their underlying HTML really has no role
  field — they classify as `None` with a clean / empty `role`
  value, not body-text junk).
- A unit test in `.scripts/test_parser.py` (the B-003 file)
  covers at least 3 of the known garbage patterns and asserts the
  parser returns either a clean role string or a clean empty
  string — never body-text prose.
- `SELECT role FROM transactions WHERE role LIKE '%purchased%' OR
  role LIKE '%capitalise%' OR LENGTH(role) > 80` returns zero rows
  after the corpus re-parse.

### Files to touch
- `.scripts/parse_pdmr.py` — role-field extraction.
- `.scripts/test_parser.py` (assumed already exists from B-003) —
  add 3 fixture cases for known garbage patterns.
- `.scripts/reparse_corpus.py` — invoked after the fix to
  recompute the affected rows (already exists per B-001+B-004).

### Implementation
1. Pull the 22 known-bad rows from the DB, identify their cached
   HTML files via `rns_id`.
2. For each, inspect the HTML to find what the extractor is
   mis-anchoring on. Common patterns: header text "Position" with
   no following cell value, role split across two cells, OCR-style
   line breaks inside a cell.
3. Tighten the role-cell extraction logic — anchor on the exact
   header cell text, read only the next `<td>` sibling, reject
   if the value contains prose markers (`who`, `that`, `purchased`,
   `the` at start of string, etc.) or exceeds a sane max length
   (~80 chars).
4. Add the 3 worst patterns as fixture cases in `test_parser.py`.
5. Re-run `reparse_corpus.py` against the cached HTML corpus
   (subject to the B-001+B-004 fingerprint-stability rule).

### Validation
- The corpus re-parse log shows 22 (or close to) role-field
  updates, no other unexpected diffs.
- `classify_role_diagnostic.py` after re-parse: None% drops by
  ~1-2pp.
- The SQL guard query above returns zero rows.

### Out of scope
- Re-architecting the parser more broadly (B-001+B-004 territory).
- Adding role normalisation / canonicalisation (separate item).

### Risks
- Tightening the extractor causes false negatives on rows that
  currently classify correctly. Mitigation: the fixture coverage
  plus the diagnostic re-run catch this.

---

## B-017 — Drill-page lookback/horizon dropdowns made functional (v1.1)

**Tier:** 3 · **Effort:** 6 h · **Cost:** £0 · **Depends on:** B-014 recommended (for base_rate plumbing)

### Problem
The redesigned drill pages render `<select>` dropdowns for both
lookback period and horizon, but they're currently **inert**. Each
cohort key has exactly one pre-rendered file at the default
(t21 / 90d) view. Changing the dropdown updates the URL but the
page content is unchanged.

### Decision required (defer to scope-time review)
Two approaches, pick one before engineering starts:

**Option A — pre-render all combos (static-first).**
- For every cohort, render all 16 horizon×lookback combos →
  18 cohorts × 16 combos = 288 HTML files.
- Build is ~3-4× slower; output dir is ~3-4× larger.
- Zero JS dependency for the dropdown UX — pure `<a href>` links.
- Engineer-recommended for v1.1 (incremental, low-risk).

**Option B — SPA refactor.**
- Single HTML file per `cohort_type`, embeds the full cohort
  payload as JSON. JS re-renders the page on URL param change.
- Smaller output dir, larger initial page weight per file.
- Sets up the architecture for v2 (cross-tile interactivity, real
  filtering).

**Recommendation:** Option A for v1.1 ship, Option B for v2.

### Acceptance criteria
- Changing the lookback dropdown on a drill page navigates to a
  page reflecting the chosen lookback — table rows, sparkline,
  status pill all reflect that lookback window.
- Same for the horizon dropdown.
- All 16 combos per cohort produce sensible output (no empty
  states unless the underlying data genuinely has zero signals
  in the window).
- The default landing combo (t21 / 90d) renders identically to
  the v1 build — i.e. no regression on the existing single-combo
  files.

### Files to touch
- (Option A) `.scripts/export_dashboard_json.py` — emit a payload
  per cohort × horizon × lookback.
- (Option A) `.scripts/dashboard/render_performance_drilldown.py`
  — loop over the 16 combos per cohort, write 16 files.
- (Option A) `.scripts/build_dashboard.py` — orchestrate the
  larger render set.
- (Option B) `render_performance_drilldown.py` — refactor to
  embed full payload + JS state machine.

### Implementation
Defer until Option A vs B is decided. Each option has its own
detailed implementation sketch at scope time.

### Validation
- Click each dropdown option on at least 3 cohort drill pages —
  numbers change, no console errors.
- Bookmark a non-default URL, reload — page renders correctly.
- Build time: confirm it doesn't exceed ~3 minutes (Option A) or
  ~1 minute (Option B).

### Out of scope
- Per-tile filter UI (separate item B-018).
- CSV export (separate item B-019).
- Cross-page state persistence beyond URL params.

### Risks
- **Option A:** build time blows out. Mitigation: cache the
  per-combo aggregates; only re-render on data change.
- **Option B:** JS state machine bugs produce stale views.
  Mitigation: minimal state machine; URL is the single source of
  truth.

---

## B-018 — Per-tile signal-filtered cohorts (v1.1)

**Tier:** 3 · **Effort:** 4 h · **Cost:** £0 · **Depends on:** B-017 (cleaner if dropdowns work first)

### Problem
Users want to combine the existing cohort tile filter with a
signal-tier filter — "Show me only T1 trades in the Materials
sector" or "Only S1 buys from the CEO/CFO role bucket". v1 ships
without this; v1.1 candidate.

### Acceptance criteria
- Each cohort tile has a small `<select>` for signal tier.
  Defaults to "all signals".
- Changing the select re-renders the tile contents (and, when on a
  drill page, the drill view too) using only signals of the chosen
  tier.
- The cohorts_v2 payload shape gains per-signal-tier sub-keys so
  the front end can index into them without an extra fetch.

### Files to touch
- `.scripts/export_dashboard_json.py` — extend cohorts_v2 shape.
- `.scripts/dashboard/render_performance.py` — add the `<select>`
  per tile, wire to URL params or local JS.
- `.scripts/dashboard/render_performance_drilldown.py` — read
  the signal-tier filter from URL params.

### Implementation
1. Extend cohorts_v2 to include `signal_tier_breakdown: {T1: {...},
   T2: {...}, ..., all: {...}}` per cohort key.
2. UI: small `<select>` above each cohort tile's headline number.
   On change, JS re-reads the breakdown subtree.
3. Drill pages: respect a `?tier=T1` URL param.

### Validation
- Pick a sector with mixed signal tiers; toggle the tier filter,
  confirm headline number changes sensibly.
- Drill into a single-tier subset; confirm the drill page shows
  only that tier's signals.

### Out of scope
- Multi-select signal tiers (single-select only for v1.1).
- Filtering by signal sub-type (e.g. T1 + T2 combined).

### Risks
- Payload size grows ~7×. Mitigation: only emit non-zero tier
  buckets per cohort; most cohorts have signals from only 1-3
  tiers.

---

## B-019 — CSV / clipboard export of cohort and drill tables (v1.1)

**Tier:** 3 · **Effort:** 3 h · **Cost:** £0 · **Depends on:** none

### Problem
Spec §7 lists CSV export as v1 out-of-scope. v1.1 candidate. Users
want to pull cohort and drill tables into Excel for their own
analysis.

### Acceptance criteria
- Each table on Performance and on every drill page has an
  "Export to CSV" button.
- Clicking the button downloads a CSV with the table's currently-
  visible columns and rows.
- Also: a "Copy to clipboard" variant that copies tab-separated
  values (pastes cleanly into Excel).
- Exported filename follows the pattern
  `<cohort-type>_<cohort-key>_<horizon>_<lookback>.csv`.

### Files to touch
- `.scripts/dashboard/render_performance.py` — add export buttons
  on cohort tiles.
- `.scripts/dashboard/render_performance_drilldown.py` — add
  export buttons on drill tables.
- New small JS module for the client-side CSV serialiser (or
  inline if compact enough).

### Implementation
1. Client-side: serialise the visible `<table>` DOM to CSV /
   TSV; trigger download via `Blob` + `URL.createObjectURL`.
2. Optional server-side variant: Flask CSV endpoint
   `/csv/cohort/<key>?horizon=...&lookback=...`. Defer unless
   client-side proves insufficient.

### Validation
- Export a drill table, open in Excel — columns and rows match
  the rendered page.
- Copy variant: paste into Excel, columns split correctly.

### Out of scope
- XLSX export (CSV is the v1.1 deliverable).
- Server-side endpoint (defer pending demand).

### Risks
- None material.

---

## B-020 — Mobile/tablet layouts beyond responsive stack (v1.1)

**Tier:** 3 · **Effort:** 6 h · **Cost:** £0 · **Depends on:** none

### Problem
v1 ships with a simple `grid-cols-1 md:grid-cols-3` Tailwind stack.
On a real tablet (~768px) or phone (~375px), the cohort tiles are
either too wide (waste of space) or stack into a single column with
tables that overflow horizontally. v1.1 candidate for proper
breakpoint-tuned layouts.

### Acceptance criteria
- Tablet (640px–1024px): cohort tiles render in a 2-column grid
  (not 3, not 1). Tables remain readable without horizontal scroll.
- Mobile (≤640px): tiles render in a single column. Tables collapse
  to a 2-row stacked layout per row (label column + value column).
- Drill pages: status pill, sparkline, and table all reflow
  sensibly at both breakpoints.
- Swipeable navigation between cohort tiles on mobile (left/right
  swipe = next/prev tile). Optional — drop if scope tightens.

### Files to touch
- `.scripts/dashboard/render_performance.py` — Tailwind
  responsive classes.
- `.scripts/dashboard/render_performance_drilldown.py` — same.
- Possible new CSS module if Tailwind utilities prove insufficient
  for the swipe behaviour.

### Implementation
1. Audit the current breakpoint behaviour on real devices /
   Chrome devtools mobile emulation.
2. Tune Tailwind responsive classes: `sm:` for ≥640px, `md:` for
   ≥768px, `lg:` for ≥1024px.
3. Tables: implement the 2-row stacked layout via CSS
   `display: block` + `<td>` pseudo-labels.
4. Swipe: minimal `touchstart` / `touchend` JS handler. Drop if
   complexity bites.

### Validation
- Open dashboard on iPhone, iPad, and desktop Chrome at 3 widths
  (375, 768, 1440). All three render cleanly without horizontal
  scroll.
- No content is cut off, no tiles overlap.

### Out of scope
- Mobile-only navigation menu / drawer (v2).
- PWA / offline support.

### Risks
- Time blowout. Mitigation: ship the breakpoint tuning first; swipe
  is the easy-cut.

---

## B-021 — "Compare two cohorts" view (v1.1)

**Tier:** 3 · **Effort:** 8 h · **Cost:** £0 · **Depends on:** B-017 (functional dropdowns)

### Problem
Spec §7 lists this as "likely v2". Promoted to v1.1 candidate after
the FE1 review — users want to compare two sectors or two role
buckets side-by-side, synced on horizon and lookback.

### Acceptance criteria
- New page `/compare?cohort_type=sector&a=Materials&b=Energy&horizon=T+21&lookback=90d`.
- Renders two drill-page views side by side, headed by a tile each
  for cohort A and cohort B.
- Changing horizon or lookback updates both panes in lockstep.
- A delta row at the top shows headline diffs (e.g. "A: 62% T+90
  positive, B: 48% T+90 positive, delta +14pp").
- Mobile: stacks vertically rather than side-by-side.

### Files to touch
- `.scripts/dashboard/render_performance_compare.py` — **new**.
- `.scripts/build_dashboard.py` — orchestrate compare-page
  generation.
- `.scripts/export_dashboard_json.py` — likely re-uses existing
  drill payloads; no new shape required.

### Implementation
1. New render module that reads two cohort drill payloads and
   composes them into a side-by-side template.
2. Static pre-render for the top-N most likely compare pairs
   (engineer guesses: same-cohort-type pairs ranked by signal
   volume), or dynamic via Flask. Decide at scope time.
3. Delta row computed at render time, not embedded in the payloads.

### Validation
- Open the compare URL for two sectors, confirm both render
  correctly and the delta row matches manual arithmetic.
- Change horizon, confirm both panes update.

### Out of scope
- 3+ way comparison.
- Cross-cohort-type comparison (sector vs role bucket — different
  payload shapes; deferred to v2).

### Risks
- Combinatorial pre-render explosion. Mitigation: render on demand
  via Flask, or limit pre-render to top-20 pairs.

---

## B-022 — Cross-tile linked highlight on hover (v1.1)

**Tier:** 3 · **Effort:** 3 h · **Cost:** £0 · **Depends on:** none

### Problem
Spec §7 lists this as "interesting but expensive — v1.1 or v2".
The idea: hovering "CEO/CFO" in the role-bucket tile highlights
all CEO/CFO-driven sector rows in the sector tile, and vice versa.
Surfaces cross-cohort patterns ("CEO buys are heavy in Energy
right now").

### Acceptance criteria
- Hovering any cohort row in any tile applies a visual highlight
  (background tint, not text colour) to all related rows in sibling
  tiles.
- Highlight is bidirectional — sector row → role tile rows;
  role tile row → sector tile rows.
- Highlight clears on mouseout.
- Works on touch devices via tap-to-highlight (tap again to
  clear).

### Files to touch
- `.scripts/dashboard/render_performance.py` — emit
  `data-related-keys` attributes per row.
- New small JS module for the hover/tap behaviour.

### Implementation
1. Backend: for each cohort row, compute the set of "related"
   cohort keys in other tile types. (Sector row → role-bucket keys
   that have transactions in that sector, and vice versa.)
2. Emit as `data-related-keys="role:CEO_CFO,role:Board"` per row.
3. JS: on mouseenter, find all `[data-cohort-key]` elements whose
   key is in the hovered row's related-keys set, apply a
   `.is-related` class.

### Validation
- Hover a sector row, confirm role tile rows highlight.
- Hover a role row, confirm sector tile rows highlight.
- Hover something with no related rows — no crash, no spurious
  highlights.

### Out of scope
- Animated transitions (instant on/off is fine).
- Highlighting in the drill table (tile-level only for v1.1).

### Risks
- The related-keys computation explodes payload size. Mitigation:
  cap at top-10 related keys per row.

---

## B-023 — Auto-deprecate kill verdicts after 2x regime windows (v1.1)

**Tier:** 3 · **Effort:** 4 h plus regime-detection work · **Cost:** £0 · **Depends on:** regime-detection infrastructure (not yet built)

### Problem
The "deprecate" button on the per-signal scoreboard is currently
manual-only. Locked by Rupert (2026-05-18, performance-page-redesign-v1
spec §13) pending **two non-overlapping regime windows of evidence**
at the relevant horizon's length. Once the dataset spans enough
diverse regimes, we can auto-deprecate signals that fail across
both — much stronger evidence than a single-regime kill.

### Acceptance criteria
- Regime detector classifies each historical date into a regime
  bucket (e.g. bull / bear / sideways, or higher-resolution if the
  detector supports it).
- For each signal: compute its CAR at the target horizon within
  each regime window the data spans.
- A signal qualifies for auto-deprecation only when the dataset
  covers ≥2 non-overlapping regime windows AND the signal fails
  the kill criterion in **both**.
- The model-assessment panel header text in §13 already documents
  this rule — confirm the running implementation matches the
  documented behaviour.
- Auto-deprecation is gated behind a config flag (default off until
  the dataset coverage is adequate).
- Manual deprecate still works as today.

### Files to touch
- `.scripts/eval_signals.py` — regime-aware signal CAR computation.
- `.scripts/regime_detect.py` — **new** (or wire to existing
  regime infrastructure if it lands first).
- `.scripts/export_dashboard_json.py` — surface regime coverage
  in the model-assessment panel data.
- `.scripts/dashboard/render_performance.py` — render the
  "auto-deprecated" pill on signals that qualify.

### Implementation
Detailed implementation deferred until regime-detection design is
locked in. Sketch:
1. Detector emits per-date regime label (cached in DB or JSON).
2. `eval_signals.py` groups CAR rows by regime, evaluates kill
   criterion per regime.
3. Auto-deprecate flag set when ≥2 regimes fail.

### Validation
- Synthesise a fixture dataset spanning two regimes, one signal
  failing in both. Confirm the auto-deprecate flag fires.
- Real data: confirm zero auto-deprecations until coverage is
  adequate (likely no fires before mid-2027 given current dataset
  start).

### Out of scope
- Regime detector v1 (separate scope; this item is downstream).
- Auto-reinstate logic (manual reinstate only).

### Risks
- **Premature auto-deprecation if regime detector mis-classifies.**
  Mitigation: config flag defaults off; explicit Rupert sign-off
  required to enable.
- **Regime infrastructure is a large dependency.** Mitigation:
  this scope cannot start until that infrastructure is built;
  acknowledge in dependency notes.

---

# Reviewer checklist

Before handing to engineering, the QA pass should confirm:

1. **Acceptance criteria are testable.** Each criterion can be turned
   into a single SQL query, file diff, or visual check.
2. **No hidden cross-tier dependencies.** A Tier-1 item never reads or
   depends on output from a Tier-2 or Tier-3 item.
3. **File paths and function names are accurate** — exist in the
   current codebase or marked clearly as **new**.
4. **Risks have explicit mitigations**, not just acknowledgement.
5. **Out-of-scope sections are concrete**, not generic.
6. **Validation is independent of the implementer** — checks against
   external truth (the cache, the live DB), not internal state of the
   change itself.

QA produces a punch list. Engineer reads scope + punch list before
starting each item.
