# Sprint 10 plan — Finish B-011 + pipeline resilience

> Theme: **close the four open B-011 gaps, harden the pipeline against
> Yahoo rate-limiting, and clear the stale-status hang.**
>
> Status: **APPROVED at Gate 1 on 2026-05-25.** Build authorised. See
> Section 9 for the decisions log.
>
> Date drafted: 2026-05-25. Author: Claude (main session). This sprint
> is single-themed on data correctness + pipeline reliability. The
> three Sprint 9 deferred parser bug classes (USD, par-value,
> type-mislabel) go to **Sprint 11**.

**Companion docs:**

- [`sprint-plan-2026-05-25-sprint9-phase-b.md`](./sprint-plan-2026-05-25-sprint9-phase-b.md) — preceding sprint; reparse + four parser fixes. Sprint 10 build is **gated on Phase B reparse closing cleanly** (see Section 6 preconditions).
- [`backlog-scopes-2026-05-18.md`](./backlog-scopes-2026-05-18.md) — backlog source for B-011 (Tier 3, ~6h estimate).
- [`sprint-plan-2026-05-22-sprint9.md`](./sprint-plan-2026-05-22-sprint9.md) — Sprint 9 plan; note its Section 2 incorrectly stated "B-011 already shipped". This sprint corrects that.

**Estimate units:** Rupert-time = wall-clock attention; gates = mandatory sign-off pauses; risk = Low / Medium / High.

---

## Section 0 — Why this sprint exists

Sprint 2 built `classify_issuers.py` and `exclude_investment_trusts.py`,
ran the one-shot IT/CEF/VCT/REIT purge, and produced
`.data/_excluded_it_cef.csv` (112KB audit log). Sprint 9's plan
explicitly described B-011 as "already shipped." A read-only audit of
the codebase on 2026-05-25 (the planning session that produced this
document) found that statement was **partly wrong**:

| Gap | Backlog promise | Actual state |
|-----|-----------------|--------------|
| 1. Classifier | Built | ✓ Done |
| 2. One-shot purge | Built + run | ✓ Done |
| 3. Ingest-time filter | Drop new IT filings at scrape | ✓ Built in `run_scrape.py` (lines 68–96, 273–289); **but** only catches tickers already in `tickers_meta`. A brand-new IT issuer's first-ever filing slips through. |
| 4. Dashboard defensive filter | Filter excluded issuers in `index.html` queries | ❌ **Zero references** to `is_excluded_issuer` across `export_dashboard_json.py`, `build_dashboard.py`, `eval_signals.py`, `backtest.py`, or any of the 7 render scripts in `.scripts/dashboard/`. |
| 5. Re-classification cadence | (Discovered Sprint 3 — memory.md) | ⚠️ `classify_issuers.py` is **never called from `refresh_all.py`**. Pipeline never auto-reclassifies. Newly-listed ITs leak in until manually re-run. |
| 6. Backtest re-run on clean data | Per B-011 acceptance | Pending — depends on 4+5 landing. |

Sprint 10 closes 3, 4, 5 and runs 6.

The **planning session also surfaced two adjacent reliability bugs**
that warrant inclusion:

- **B (today's fire):** `_refresh_status.json` got stuck on
  `"status": "running"` from May 22 19:57 (a refresh died mid-flight).
  `start.bat` only clears *malformed* status JSON, not stale-valid.
  Result: dashboard spinner hangs indefinitely on next launch.
- **C (today's second fire):** `backfill_prices.py` got stuck for
  33+ minutes during the live Phase B test. Root cause: Yahoo Finance
  returning HTTP 429 on ~50% of requests today; `fetch_prices.py`
  retries with 30s/60s/120s backoff per ticker, so 563 tickers ×
  partial 429 hit rate = legitimately many hours of work. Designed
  behaviour, but the pipeline has no abort-on-systemic-failure
  protection.

Both B and C are small, in the spirit of the sprint (pipeline
hygiene), and worth bundling.

---

## Section 1 — Goal & success criteria

**Goal.** Eliminate the silent gaps in IT/CEF exclusion, prevent the
stale-status hang from recurring, and add abort-on-systemic-failure
to the prices step.

**Success criteria (measurable):**

1. `classify_issuers.py` runs as a pipeline step inside
   `refresh_all.py` (placed between `sectors` and `signals`).
   `_refresh_status.json` records its completion.
2. Every downstream reader of `transactions` / `signals` /
   `paper_trades` filters out `is_excluded_issuer = 1` rows via a
   single shared helper (`db.excluded_ticker_set(conn)` or
   equivalent). Concretely: `export_dashboard_json.py`,
   `eval_signals.py`, `backtest.py`, and the relevant
   `.scripts/dashboard/render_*.py` callsites.
3. `classify_issuers.py` no longer zeros every flag before
   re-applying. New flow: compute the union, then do an additive
   `UPSERT` per ticker. Sticky behaviour: a previously-flagged
   ticker stays flagged unless the latest run *positively decides*
   it isn't IT/CEF.
4. Post-Sprint-10 audit query against `transactions` shows zero rows
   where `ticker IN (SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1)`.
   (i.e. nothing leaked through the new defensive layer.)
5. Backtest re-run completes cleanly on post-Phase-B + post-Sprint-10
   data; `_backtest_results.csv` reflects operating-company-only
   signal performance.
6. `start.bat` detects a `_refresh_status.json` whose `updated_at`
   is `> 60 minutes` old AND `status == "running"`, deletes both it
   and any orphan `.tmp` sibling, and prints an audit line.
7. `backfill_prices.py` aborts cleanly after **N=5 consecutive 429s**
   per session, marks the step as `"partial_success"` (not `"error"`),
   logs the skipped ticker list to
   `.data/_price_skipped_due_to_rate_limit.json`, and **lets the
   pipeline continue** past prices into signals/backtest/build. Next
   refresh re-attempts the skipped tickers.
8. **Per-ticker Yahoo-skip rule (Gate 1 addition):**
   `backfill_prices.py` and `backfill_benchmarks.py` skip Yahoo
   entirely for any ticker whose `MAX(date)` in the `prices` table
   equals today's date. No network call, no rate-limit consumed.
   Prints `[prices] TICKER: already current for today, skipped` per
   skipped ticker.
9. `unittest discover` green on Windows (current count + 5–8 new
   tests: 1 for stale-status detection, 2–3 for sticky-flag
   classifier behaviour, 2–3 for the 429-abort path, 1–2 for the
   per-ticker skip rule).

---

## Section 2 — Out of scope

- The three deferred parser bug classes from Sprint 9 (USD, par-value,
  type-mislabel). Sprint 11.
- Performance-page redesign v1.1 features (B-017 functional drill
  dropdowns, B-018 per-tile filtered cohorts, B-019 CSV export). Sprint
  12 candidates.
- Replacing Yahoo as the price data source. Out of scope but worth a
  separate "vendor risk" memo if Yahoo continues degrading.
- Diagnosing why `subprocess.run(timeout=...)` didn't fire in
  `refresh_all.py` after 20 minutes. Separate Windows-specific issue;
  fold into a future hygiene sprint unless it bites again.

---

## Section 3 — Files touched

| File | Change type | Detail |
|------|-------------|--------|
| `.scripts/refresh_all.py` | Edit | Add `classify_issuers` step to `STEPS` between `sectors` and `signals`. Timeout: 5min. |
| `.scripts/classify_issuers.py` | Edit | Replace zero-then-reapply with additive UPSERT (Section 5.3). |
| `.scripts/db.py` | Edit | Add `excluded_ticker_set(conn) -> set[str]` helper (Section 5.2). |
| `.scripts/export_dashboard_json.py` | Edit | Apply exclusion filter at the `_load_tickers_meta` + transactions-read points. |
| `.scripts/eval_signals.py` | Edit | Filter excluded tickers from signal-firing input. |
| `.scripts/backtest.py` | Edit | Filter excluded tickers from backtest input. |
| `.scripts/dashboard/render_index.py` | Edit | Apply filter to today + this-week queries. |
| `.scripts/dashboard/render_company.py` | Edit | Skip rendering for excluded tickers (return 404-equivalent). |
| `.scripts/dashboard/render_performance.py` | Edit | Apply filter to cohort builders. |
| `.scripts/dashboard/render_performance_drilldown.py` | Edit | Apply filter to drill queries. |
| `.scripts/backfill_prices.py` | Edit | (a) Track consecutive 429 count; abort cleanly at N=5. (b) Per-ticker MAX(date)==today skip rule (Gate 1 addition). |
| `.scripts/backfill_benchmarks.py` | Edit | Same two changes as backfill_prices (N=5 abort + MAX(date)==today skip). |
| `.scripts/fetch_prices.py` | Possible edit | Expose retry-attempt outcome so backfill can count 429s without re-implementing. |
| `start.bat` | Edit | Stale-status check: detect `updated_at > 60min` AND `status == "running"`; delete both files; log. |
| `.scripts/test_*.py` | New tests | 5–8 new tests covering: sticky-flag classifier, 429-abort, stale-status detection, defensive-filter coverage. |

**No DB schema change.** The `is_excluded_issuer` column already
exists (migration 003). All Sprint 10 edits are application-layer.

---

## Section 4 — Build phases

### Phase 1 — Defensive filter helper + downstream propagation

**Order:** Add `db.excluded_ticker_set(conn)`. Then thread it through
each callsite in this order: `eval_signals.py` → `backtest.py` →
`export_dashboard_json.py`. Test after each.

**Refinement during build (2026-05-25):** A grep of
`.scripts/dashboard/` confirmed zero direct DB reads in render
scripts — they all consume JSONs prepared by
`export_dashboard_json.py`. So filtering at the JSON-export layer is
the last line of defence the dashboard needs. **Render scripts
removed from Phase 1 scope.** Final touched-files count: 3 (down
from 7 originally listed in Section 3).

**Why this order:** signals first (primary upstream filter) →
backtest (defensive on signals) → export (defensive on transactions,
signals, paper_trades all together).

**Estimate:** ~50 LOC of helper + ~13 callsite SQL-WHERE additions
across 3 files. Rupert-time: ~15 min for review.

### Phase 2 — Pipeline integration of `classify_issuers`

Insert as a new STEP in `refresh_all.py`. Defaults: no flags
(`classify_issuers.py` is already idempotent and produces no console
spam when nothing changes). Step timeout: 5 min (it's a small SELECT +
small UPDATE on tickers_meta).

**Order:** must come *after* `sectors` (which populates
`tickers_meta.sector`) and *before* `signals` (so any new flags
take effect before signals are recomputed).

**Estimate:** ~20 LOC. Rupert-time: ~5 min for review.

### Phase 3 — Sticky-flag classifier

Replace the `UPDATE tickers_meta SET is_excluded_issuer = 0 WHERE ...`
followed by per-ticker re-flag, with an additive UPSERT:

```sql
INSERT INTO tickers_meta (ticker, is_excluded_issuer, excluded_source, classified_at)
VALUES (?, 1, ?, ?)
ON CONFLICT(ticker) DO UPDATE
  SET is_excluded_issuer = 1,
      excluded_source = excluded.excluded_source,
      classified_at = excluded.classified_at;
```

Tickers no longer matching any of {AIC, Yahoo, regex} **stay flagged**
unless an explicit `--unflag TICKER` CLI flag is passed. This is the
sticky behaviour: once classified as IT/CEF, you don't get
un-classified by an upstream data hiccup (Yahoo missing `quoteType`,
AIC page-structure change, etc.).

**Estimate:** ~20 LOC change + 2–3 unit tests. Rupert-time: ~10 min
to review the sticky-behaviour decision.

### Phase 4 — start.bat stale-status detector

Add to `start.bat` after the existing "clear stale/corrupted refresh
status file" block. **Threshold per Gate 1: 45 minutes.**

```bat
REM Stale-running detector: clear if updated_at is older than 45 min.
if exist ".data\_refresh_status.json" (
    python -c "import json, datetime as dt, pathlib, sys; p=pathlib.Path('.data/_refresh_status.json'); s=json.loads(p.read_text()); ts=s.get('updated_at') or s.get('started_at'); st=s.get('status'); now=dt.datetime.now(dt.timezone.utc); age=(now-dt.datetime.fromisoformat(ts.replace('Z','+00:00'))).total_seconds() if ts else 0; sys.exit(0 if st!='running' or age<2700 else 7)" >nul 2>nul
    if errorlevel 7 (
        del /f /q ".data\_refresh_status.json" >nul 2>nul
        del /f /q ".data\_refresh_status.json.tmp" >nul 2>nul
        echo Cleared stale "running" status file (age >45 min).
    )
)
```

(Final implementation may refactor into a small `db_health.py`
subcommand for readability — Rupert preference.)

**Estimate:** ~20 LOC. Rupert-time: ~5 min.

### Phase 5 — Prices/benchmarks resilience (two changes)

#### 5.A — Per-ticker MAX(date)==today skip (Gate 1 addition)

**Before the existing per-ticker fetch loop**, for each ticker check:

```python
last_date = conn.execute(
    "SELECT MAX(date) FROM prices WHERE ticker = ?", (ticker,)
).fetchone()[0]
if last_date and date.fromisoformat(last_date) >= date.today():
    if verbose:
        print(f"[prices] {ticker}: already current for today, skipped")
    summary["already_current"] += 1
    continue
```

This is the bluntest possible "don't poll Yahoo if I already polled
today" rule. It runs *before* the existing 20-hour cache check in
`fetch_prices.py` — meaning we don't even read the cache file for an
already-current ticker. Saves both network calls *and* disk reads.

Apply identical block to `backfill_benchmarks.py`.

#### 5.B — Abort-on-systemic-429

Modify `backfill_prices.py` and `backfill_benchmarks.py` to:

1. Track `consecutive_429s` across tickers (counter resets on any
   successful fetch — including a 5.A skip).
2. If `consecutive_429s >= 5`, stop iterating tickers, log the
   skipped ticker list to
   `.data/_price_skipped_due_to_rate_limit.json` (atomic write),
   print a clear message, and `exit 0` (not 1 — so refresh_all
   continues into signals/backtest/build).
3. On next refresh, the skipped tickers are at the *front* of the
   work list (so we re-attempt before doing already-current work).

This is **not** a fix for Yahoo's rate limiting — it's a fix for
"don't take the whole pipeline down when Yahoo misbehaves". Stale
prices are tolerable for a session; a dead pipeline is not.

#### Combined effect (5.A + 5.B)

Once Phase 5 ships, the typical day looks like:

- **First refresh of the day, morning:** 5.A skips nothing (DB stale
  from yesterday). 5.B counter likely stays low. Full Yahoo fetch
  runs as today, ~563 calls.
- **Second refresh of the day, afternoon:** 5.A skips every ticker
  that the morning run already updated through to today. Zero Yahoo
  calls if morning was successful. Pipeline finishes prices step in
  seconds.
- **Bad Yahoo day:** if Yahoo starts 429-ing during the morning
  refresh, 5.B triggers after 5 consecutive 429s and exits the step
  cleanly with partial coverage. Pipeline continues to signals.
  Afternoon retry uses 5.A to skip whatever was completed in the
  morning + retries whatever was skipped.

**Estimate:** ~50 LOC across the two backfill scripts + 4–5 tests
with a mocked `fetch_prices` (one test for 5.A skip, one for 5.B
abort, one combined). Rupert-time: ~10 min.

### Phase 6 — Backtest re-run + verification

After Phases 1–5 land and Sprint 9 Phase B reparse is closed:

```powershell
python .scripts\classify_issuers.py --verbose
python .scripts\exclude_investment_trusts.py --preview
# Rupert reviews preview if any new tickers flagged
python .scripts\exclude_investment_trusts.py --confirm   # only if new flags
python .scripts\backtest.py
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

**Estimate:** Rupert-time: ~20 min (mostly waiting + reviewing preview).

---

## Section 5 — Detailed specs for the non-trivial bits

### 5.1 — Pipeline ordering with the new classifier step

```
scrape → prices → benchmarks → sectors → CLASSIFY → signals
       → backtest → export → audit → build
```

**Rationale for placement after `sectors`:** `tickers_meta.sector` is
populated by `fetch_sectors.py` (the sectors step). A new IT issuer
seen for the first time in today's scrape won't have a Yahoo
`quoteType` cached yet either — but `classify_issuers.py`'s name-regex
catch-all (matches "Investment Trust", "VCT", "REIT", etc.) will catch
it on the first pass without needing Yahoo. After classify runs,
`signals` skips the now-excluded ticker correctly.

### 5.2 — `db.excluded_ticker_set(conn)` helper

Add to `.scripts/db.py`:

```python
def excluded_ticker_set(conn) -> set[str]:
    """Return the set of tickers flagged is_excluded_issuer = 1.

    Defensive: returns an empty set if the column is missing (pre-
    migration DB). Callers should pass the result as a parameter, not
    re-fetch per row.
    """
    try:
        rows = conn.execute(
            "SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1"
        ).fetchall()
        return {r["ticker"] for r in rows}
    except Exception:
        return set()
```

Each caller threads this through its existing query. SQL-side, the
filter is `WHERE ticker NOT IN (SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1)`
which SQLite optimises into a hash-join on the small (~400 row)
tickers_meta table. No performance concern.

### 5.3 — Sticky-flag classifier — semantic detail

| Input state | Old behaviour | New behaviour |
|-------------|---------------|---------------|
| Ticker matches AIC + Yahoo + regex | Flag = 1 | Flag = 1 |
| Ticker matches only AIC | Flag = 1 | Flag = 1 |
| Ticker matches only regex catch-all | Flag = 1 | Flag = 1 |
| Ticker previously flagged, NO source matches now | **Flag = 0** (silent un-flag) | **Flag stays 1** (sticky). Logged to `.data/_classifier_sticky_holds.log` for audit. |
| Ticker previously flagged + explicit `--unflag TICKER` CLI | Flag = 0 | Flag = 0 |
| Ticker never seen, matches at least one source | Flag = 1 | Flag = 1 |

**Why sticky:** the silent un-flag in the old behaviour is precisely
the Sprint 3 gotcha that's been on memory for over a week. A
classifier whose output can flip on a Yahoo API blip is not a
classifier we can trust. Sticky behaviour means human review is
required to un-flag — a small but real safety net.

### 5.4 — 429-abort threshold tuning

N=5 consecutive 429s is the working threshold. Justification:

- **Lower (N=3):** too sensitive on a borderline-bad day where every
  3rd ticker gets 429'd. Pipeline aborts prematurely.
- **Higher (N=10):** wastes 10× 30s = 5 min before aborting. Still
  too slow when Yahoo is fully blocking.
- **N=5:** ~2.5 min worst case before abort. Strong signal that
  Yahoo is systemically refusing rather than transiently overloaded.

Reset to 0 on any 200 response. Don't reset on 4xx other than 429.

---

## Section 6 — Preconditions (Gate 1 — CLOSED)

All preconditions resolved 2026-05-25:

1. ✓ **Sprint 9 Phase B reparse has closed cleanly** (Rupert confirmed
   at Gate 1).
2. ⚠ **Phase B parser code live-data validation deferred.** Today's
   live test was inconclusive (pipeline hung on prices step before
   reaching the parser-touching scrape on a non-bank-holiday). Treated
   as acceptable risk: the next non-bank-holiday scrape after Sprint
   10's build serves as the implicit validation.
3. ✓ **Sticky-flag semantics (Section 5.3) APPROVED** as designed.
4. ✓ **Phase 5 abort threshold N=5 APPROVED.**
5. ✓ **Per-ticker MAX(date)==today skip rule APPROVED** as the
   Yahoo-polling reduction mechanism (added at Gate 1, see Phase 5.A).
6. ✓ **45-minute stale-status threshold APPROVED** for Phase 4.
7. ✓ **Benchmarks get same protections as prices** — both 429-abort
   and the per-ticker skip rule applied to `backfill_benchmarks.py`.
8. **Pending — Section 9 default decision for `classify_issuers` in
   pipeline:** plan recommendation is `--no-yahoo` (AIC + regex only)
   per Rupert's stated preference for minimal Yahoo polling. Yahoo
   sweep remains opt-in for manual classifier runs.

---

## Section 7 — Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | Defensive filter accidentally over-filters (a real operating company gets `is_excluded_issuer = 1` by classifier mistake, then disappears from the dashboard). | Low–Medium | Medium | New defensive-filter unit test asserts a known good-issuer ticker isn't in `excluded_ticker_set`. Audit log on classify_issuers captures every change. |
| 2 | Sticky-flag holds a real un-flagging — e.g. a holding company was wrongly flagged once and stays wrong. | Low | Low | `--unflag TICKER` CLI is the manual override. Sticky-holds log gives Rupert a list to review. |
| 3 | Phase 5 mocked-fetch tests don't catch a real Yahoo behavioural quirk. | Medium | Low | Phase 5 abort is purely a safety valve. If it triggers wrongly, worst case is "prices step ends early"; pipeline still proceeds. |
| 4 | The 60-min staleness threshold in start.bat is wrong for a legitimately long pipeline run. | Low | Low | Longest single STEP timeout is 30 min (scrape). 60 min covers 2× that. If a refresh genuinely runs >60 min, that's anomalous anyway. |
| 5 | Backtest re-run produces materially different numbers from the existing `_backtest_results.csv`. | High (this is by design — that's why we're re-running) | Medium | Phase B + Sprint 10 both shift the dataset. Document the change in a brief `_sprint10_backtest_diff.md` showing before/after firing counts by tier. |
| 6 | Wiring `classify_issuers` into every refresh adds Yahoo dependency to the pipeline (via the optional Yahoo `quoteType` sweep). | Medium | Low | Add `--no-yahoo` to the refresh_all step args. Yahoo sweep is opt-in for manual runs only. AIC + name regex are enough for routine pipeline runs. |

---

## Section 8 — Rollback

Same protocol as Sprint 9:

1. `Copy-Item .data\directors.db.pre-sprint-10.bak .data\directors.db -Force`
2. `PRAGMA integrity_check`
3. Revert code edits manually.
4. Delete `_sprint10_backtest_diff.md` and any new sticky-holds log.
5. Re-run `start.bat` Refresh.

Pre-flight backup is taken automatically on first successful run via
the existing `db_health.guard()` pattern.

---

## Section 9 — Gate 1 decisions log (LOCKED 2026-05-25)

| # | Original question | Decision |
|---|-------------------|----------|
| 1 | Sprint 9 Phase B reparse close before build? | **CLOSED — reparse complete.** Sprint 10 build authorised. |
| 2 | Sticky-flag semantics (Section 5.3) | **APPROVED as designed.** Once flagged as IT/CEF, ticker stays flagged unless explicit `--unflag TICKER` CLI. |
| 3 | N=5 abort threshold | **APPROVED.** |
| 4 | classify_issuers default in pipeline | **Recommendation locked: `--no-yahoo`.** AIC + name regex only for the routine pipeline step. Yahoo sweep remains opt-in for manual runs. Consistent with Rupert's Yahoo-polling-reduction preference. |
| 5 | start.bat stale-status threshold | **45 min** (revised from 60). |
| 6 | Benchmark step same protection as prices | **APPROVED.** Both 429-abort AND per-ticker skip rule apply to `backfill_benchmarks.py`. |
| 7 (NEW at Gate 1) | Yahoo polling reduction — what mechanism? | **Per-ticker MAX(date)==today skip** (5.A). Bluntest version that achieves the goal. Cleaner than time-based or step-level alternatives. |

**Gate 1 closed — build authorised. Next session: begin Phase 1 (Section 4.1).**

---

## Section 10 — Estimated effort

| Phase | Claude time | Rupert time |
|-------|------------|-------------|
| 1. Defensive filter helper + propagation | ~60 min | ~15 min review |
| 2. classify_issuers into pipeline | ~20 min | ~5 min |
| 3. Sticky-flag classifier | ~30 min | ~10 min |
| 4. start.bat stale-status | ~20 min | ~5 min |
| 5. 429 abort | ~45 min | ~10 min |
| 6. Backtest re-run + verification | n/a | ~20 min |
| **Total** | **~3 hours Claude** | **~65 min Rupert** |

Comparable to Sprint 9 Phase B in size. Plan-first then build then
verify, no auto-proceed past gates.

---

## Section 11 — How this slots against memory

Cross-checked against `MEMORY.md` for known traps:

- `feedback_no_rmw_json_in_hotpath.md` — Phase 5's
  `_price_skipped_due_to_rate_limit.json` is a single end-of-step
  write of a small list, not a per-row RMW. Safe.
- `feedback_signal_id_three_layer_surface.md` — no new signal IDs
  added in Sprint 10. N/A.
- `feedback_grep_all_callers_before_edit.md` — applies directly to
  Phase 1. Mandatory exhaustive `Grep` on every new helper before
  declaring "done."
- `project_auto_backup_broken.md` — Sprint 10 changes touch
  multiple Zone B writers (classify_issuers, exclude_investment_trusts
  during the backtest re-run). Each must have the C-3 pattern (fresh
  .bak before opening DB for write). Audit this in verification.
- `feedback_avoid_non_cp1252_in_subprocess_prints.md` — Phase 1+5
  edits go through subprocess-captured stdout. No `→ ✓ ✗ ⚠`; use
  ASCII (`->`, `[ok]`).
- `project_classify_issuers_resets_flag.md` — Phase 3 directly
  resolves this. Memory note can be updated/closed after Sprint 10
  ships.
- `feedback_pipeline_order_export_json.md` — Section 5.1 ordering
  respects the export-between-backtest-and-build rule. The new
  `classify` step sits before signals, well clear of the
  export/build pair.

---

*End of Sprint 10 plan draft. Awaiting Gate 1.*
