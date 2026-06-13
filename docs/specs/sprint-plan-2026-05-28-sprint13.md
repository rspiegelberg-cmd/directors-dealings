# Sprint 13 Plan — Behavioural Signals B1 + B2 + Universe Cleanup

**Date:** 2026-05-28  
**Spec reference:** `docs/specs/08-phase-4-behavioural-signals.md`  
**Base state:** 4,598 transactions / 2,973 signals / 424 tests

---

## Pre-flight: what the audit found

Before writing code we audited the codebase against the spec. Here is what is already done and what is not.

| Item | Status | Notes |
|------|--------|-------|
| IT exclusion (`is_excluded_issuer`) | ✅ Done | Already in `tickers_meta` AND wired in `eval_signals.py` lines 130 + 190. No work needed. |
| `prices` table for momentum filter | ✅ Ready | 188,336 rows, 688 tickers. B1's 60-day trailing-return query is directly feasible. |
| `buy_strictness` column on `transactions` | ❌ Missing | Not in schema. Must be added via migration. |
| B1 signal module | ❌ Missing | `.scripts/signals/b1_lone_conviction_buy_v1.py` does not exist. |
| B2 suppression gate | ❌ Missing | No suppression logic anywhere in `eval_signals.py`. |
| `context` field populated | ❌ Empty | 0% of BUY rows have context in the DB. **Critical: `buy_strictness` classifier reads HTML context, so existing rows cannot be classified from the DB alone.** Existing cached HTML in `.scripts/_scrape_cache/` is the source — a corpus reparse is needed to backfill. |
| 108 BUY rows with value ≥ £200k | ✅ Confirmed | B1 universe exists and is queryable. |

---

## ⚠️ Phase 0 — Decisions Rupert must confirm before any code is written

**Stop here. The following decisions shape all downstream code. Read and respond before Phase 1 begins.**

### Decision 1 — Buy_strictness filter scope (most important)

The spec (§2.2) says *all* signal evaluators — T1/T2/T3/T4/S1/T0/F1 — should require `buy_strictness = 'STRICT_BUY'` to fire. This is not a small change: it will mechanically shrink every existing basket by an estimated 30–50%, because vesting/LTIP/DRIP rows that currently pass through as `type=BUY` will be excluded.

**Option A — Full enforcement (recommended)**: Apply `buy_strictness = 'STRICT_BUY'` to all signals. Run a diff after corpus reparse to show you exactly how many signals drop out. Gate before applying.

**Option B — B1/B2 only**: Add the column and classifier but only enforce it in the new B1 module and B2 gate. Existing T/S/F signals continue to fire on all BUYs. Faster, no disruption to existing firing counts, but leaves the known LTIP/DRIP contamination in place.

**Recommended: Option A**, implemented with a Phase A/B gate so you see the diff before it lands.

---

### Decision 2 — S1 deprecation

The spec (§1.2) says S1 (cluster buy) is now **anti-predictive** on our data:

| Cluster intensity | Hit rate T+21 | Sample |
|---|---|---|
| ≥4 directors (S1 fires here) | 22.8% | 57 |
| Lone buyer (B1 targets) | 40.0% | 350 |

B2 suppression will effectively block the worst S1 cases. After B1/B2 land, S1's remaining firing set may be near-zero. Do you want to:

- **(a)** Formally deprecate S1 in this sprint (mark deprecated in `signal_status.json`, existing fired rows preserved)
- **(b)** Leave S1 running but plan to review after B1/B2 have 2 months of live data
- **(c)** Deprecate T3 (NED buy at 27% hit rate) instead, or both

**Recommendation:** Deprecate S1 in Phase 3 once B2 is live. T3 at 27% hit is borderline — keep it for now, re-evaluate at Sprint 14.

---

### Decision 3 — 65% timeline

The spec is explicit: B1 at 55% is the best we can get on 11 months of data. The path to 65% requires new data dimensions (days-since-results, concurrent-sell suppression, director-history opportunistic flag — see spec §6.1). Do you want to:

- **(a)** Ship B1 + B2 now and let data accumulate for 6 months before another tuning pass
- **(b)** Ship B1 + B2 and immediately add dimensions #1 and #2 from spec §6.1 (post-results filter + concurrent-sell suppression) targeting 60% hit rate — those are low-cost, both use existing data
- **(c)** Pause feature work and run a data-dimensions project before any new signal ships

**Recommendation: option (a)** for this sprint. 55% is meaningfully better than the current signals. Lock it in, let the data accumulate.

---

### Decision 4 — B1 thresholds: confirm or adjust

Before code is frozen, confirm these numbers from the spec:

| Parameter | Spec value | Your call |
|---|---|---|
| Minimum trade value | ≥ £200,000 | ? |
| Lone-buyer window | ±30 calendar days | ? |
| Momentum exclusion zone | [-10%, -2%] 60-day trailing | ? |
| B2 cluster threshold | ≥ 4 distinct directors | ? |
| B2 suppression window | 60 calendar days | ? |

If you're happy with all five, just say "defaults confirmed" and Phase 1 begins.

---

## Phase 1 — buy_strictness schema + classifier (code-only, Zone A)

**Gate to open Phase 1:** Rupert confirms decisions above.

### 1A — DB migration 005

New file: `.scripts/schema_migrations/005_buy_strictness.sql`

```sql
ALTER TABLE transactions ADD COLUMN buy_strictness VARCHAR(16);
-- Values: STRICT_BUY | MIXED | NON_BUY_ONLY | UNKNOWN | NULL (unclassified)
-- NULL = not yet processed by classifier (pre-backfill rows)
```

`db.py`'s `_ensure_schema()` picks up migrations in numeric order — no other wiring needed.

### 1B — Classifier in `parse_pdmr.py`

New function `_classify_buy_strictness(context_text: str) -> str` using the NON_BUY / STRICT_BUY regex ruleset from spec §2.2. Returns one of: `STRICT_BUY`, `MIXED`, `NON_BUY_ONLY`, `UNKNOWN`.

Wire into `db.upsert_transaction` so every new row gets classified at parse time. Existing rows stay NULL until the corpus reparse (Zone B, Rupert runs).

### 1C — Unit tests

Target: +12 tests in `test_stage_02.py` or a new `test_buy_strictness.py`:
- Pure STRICT_BUY phrases
- Pure NON_BUY phrases (vesting, LTIP, DRIP, SIP, SAYE, RSP, PSP, nil-cost option, exercise of options)
- MIXED (both present)
- UNKNOWN (no match)
- Edge cases: empty string, None

**Phase 1 gate: run full test suite. Target ≥ 436 tests green (424 + 12). Then hand off to Rupert.**

### Phase 1 gate — Rupert runs (Zone B)

```powershell
python .scripts\reparse_corpus.py
```

This reads cached HTML from `.scripts/_scrape_cache/`, re-classifies `buy_strictness` for every existing transaction. After this runs, the DB should have buy_strictness populated for all rows that have cached HTML.

**After reparse Rupert runs:**
```powershell
python -c "import sqlite3; c=sqlite3.connect('.data/directors.db'); r=c.execute('SELECT buy_strictness, COUNT(*) FROM transactions WHERE type=''BUY'' GROUP BY buy_strictness').fetchall(); print(r)"
```

Share the output — it tells us how many BUYs are STRICT_BUY vs NON_BUY_ONLY vs UNKNOWN before we change any signal logic.

---

## Phase 2 — B1 signal module (code-only, Zone A)

**Gate to open Phase 2:** Phase 1 gate complete (buy_strictness populated). Decision 1 confirmed.

### 2A — B1 module

New file: `.scripts/signals/b1_lone_conviction_buy_v1.py`

Based on spec §3.5. Queries:
- `value_gbp ≥ 200,000`
- No other directors buying same ticker within ±30d (at as_of)
- 60-day trailing return from `prices` table NOT in [-10%, -2%]
- buy_strictness = 'STRICT_BUY' checked centrally OR inside evaluate()

`get_close_pair()` helper: already exists in the codebase or will be extracted from the backtest path — confirm location before writing.

### 2B — Register in `signals/__init__.py`

- Add to `REGISTRY` dict
- Add `b1_lone_conviction_buy` to `EVAL_ORDER` (after F1, before T0 in first pass)
- NOT in `DEPENDENT_SIGNALS` (B1 doesn't need signals table from pass 1)

### 2C — If Decision 1 = Option A: add buy_strictness filter to universe

In `eval_signals.py` → `_universe_rows()`: add `AND COALESCE(t.buy_strictness, 'STRICT_BUY') = 'STRICT_BUY'` to the WHERE clause. The COALESCE fallback means rows not yet classified by the reparse are treated as STRICT_BUY (conservative — doesn't drop them silently).

**Alternative safe approach**: filter inside each individual evaluator, not at universe level. Slower but doesn't risk silently dropping rows.

**Recommendation**: universe-level filter with COALESCE fallback. Cleaner, one place to change.

### 2D — Unit tests for B1

Target: +8 tests in a new `test_signals_b1.py`:
- Fires on valid lone large buy with neutral momentum
- Does not fire when value < £200k
- Does not fire when other director bought within 30d
- Does not fire when trailing return in [-10%, -2%] falling-knife zone
- Does not fire when buy_strictness != 'STRICT_BUY'
- Does not fire when no price data available
- Metadata dict contains `trail60` and `value_gbp`
- Fired signal has correct signal_id and version

**Phase 2 gate: full test suite ≥ 444 tests green. Then hand off to Rupert.**

### Phase 2 gate — Rupert runs (Zone B)

```powershell
python .scripts\eval_signals.py --rebuild --verbose
```

Rupert shares the `by_signal` summary. We produce a diff CSV showing which firings dropped (from buy_strictness filter) and how many B1 firings appeared. Rupert approves before pipeline rebuild.

---

## Phase 3 — B2 suppression gate (code-only, Zone A)

**Gate to open Phase 3:** Phase 2 gate complete and diff approved.

### 3A — Pre-compute B2 suppression windows

In `eval_signals.py` → `evaluate_all()`, before the main loop: run a query that pre-computes all (ticker, window_start, window_end) tuples where ≥4 distinct directors bought in a 30d window. Store as a dict keyed by ticker → list of (start_date, end_date) suppression windows.

SQLite doesn't support window-function date ranges — use Python groupby instead:
```python
def _compute_b2_suppression_windows(conn) -> dict[str, list[tuple]]:
    """Pre-compute (ticker, suppress_from, suppress_until) tuples for B2.
    
    A suppression window opens the day the 4th director fires, and
    runs for 60 calendar days. Multiple overlapping windows are merged.
    """
    ...
```

### 3B — Gate in main eval loop

Before each `mod.evaluate(tx, conn, as_of)` call in the first pass, check if the transaction falls in a B2 suppression window. If yes, skip that (tx, signal) pair — do not call evaluate(), do not write to signals table.

```python
if _is_b2_suppressed(tx, b2_windows):
    continue
```

This is applied to ALL signals (T/S/F/B1), consistent with spec §4.1.

### 3C — Unit tests for B2

Target: +6 tests in `test_signals_b2.py`:
- Suppression window correctly computed for ticker with ≥4 directors
- Ticker with 3 directors is NOT suppressed
- Transaction date inside suppression window → signal blocked
- Transaction date outside suppression window → signal passes through
- Multiple overlapping windows merge correctly
- Works correctly with as_of date boundary

### 3D — Optional: S1 deprecation (if Decision 2 = option a)

Write `'s1_cluster_buy'` into `.data/signal_status.json` under `deprecated`. This suppresses future S1 evaluations without touching existing fired rows.

**Phase 3 gate: full test suite ≥ 450 tests green. Then hand off to Rupert.**

### Phase 3 gate — Rupert runs (Zone B)

```powershell
python .scripts\eval_signals.py --rebuild --verbose
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

Review dashboard: B1 firings visible in dealings table, B2 suppression reducing cluster-driven signals, overall signal count change vs Phase 2.

---

## Phase 4 — optional, only if time allows

Low-cost data dimensions from spec §6.1 that could be added without a new sprint:

| # | Feature | Estimated test delta |
|---|---|---|
| 1 | **Concurrent-sell suppression** (60d lookback) | +4 tests |
| 2 | **Post-results filter** (days-since-results, via RNS calendar) | +6 tests, requires date scrape |

Decision: skip for Sprint 13 unless Phase 1-3 ship faster than expected.

---

## Definition of done

Sprint 13 is complete when:
- [ ] Phase 0 decisions confirmed
- [ ] `buy_strictness` column in schema, classifier in `parse_pdmr.py`
- [ ] Corpus reparse run (Rupert), buy_strictness distribution reviewed
- [ ] `b1_lone_conviction_buy` module registered and tested
- [ ] B2 pre-compute + suppression gate in `eval_signals.py`
- [ ] Full test suite ≥ 450 tests green
- [ ] `eval_signals --rebuild` run on live DB (Rupert)
- [ ] Diff CSV reviewed and approved
- [ ] Dashboard rebuilt — B1 visible, B2 gate active
- [ ] S1 deprecation decision made and actioned (or explicitly deferred)

---

## Risk register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `context` NULL → reparse doesn't populate buy_strictness (some filings never cached) | Medium | COALESCE fallback in universe filter treats NULL as STRICT_BUY; these rows pass through rather than silently dropping |
| B1 firings < 20 on live DB (vs 20 on backtest) | Medium | Backtest used 11-month window; live DB has ~12 months. Small-sample variance expected. Accept if n ≥ 12 |
| B2 over-suppresses (kills B1 firings) | Low | B2 window is 60 days, B1 requires lone buyer — by construction a lone buyer cannot trigger B2 |
| Schema migration breaks existing tests | Low | Migration adds a nullable column; existing rows have NULL which is valid |
| FUSE staleness on large multi-file edits | High (known) | /tmp reconstruction for test runs; Read tool for ground truth |

---

## Files touched by this sprint

**Zone A (Claude edits):**
- `.scripts/schema_migrations/005_buy_strictness.sql` (new)
- `.scripts/parse_pdmr.py` (add `_classify_buy_strictness()` + wire into upsert)
- `.scripts/db.py` (if migration auto-pickup needs updating)
- `.scripts/signals/b1_lone_conviction_buy_v1.py` (new)
- `.scripts/signals/__init__.py` (register B1, add TIER_RANK entry if needed)
- `.scripts/eval_signals.py` (B2 pre-compute + suppression gate + optional universe filter)
- `.scripts/test_buy_strictness.py` (new, Phase 1)
- `.scripts/test_signals_b1.py` (new, Phase 2)
- `.scripts/test_signals_b2.py` (new, Phase 3)
- `.data/signal_status.json` (if S1 deprecated — Rupert can edit this directly)

**Zone B (Rupert runs):**
- `python .scripts\reparse_corpus.py` — buy_strictness backfill (Phase 1 gate)
- `python .scripts\eval_signals.py --rebuild --verbose` (Phase 2 + 3 gates)
- `python .scripts\export_dashboard_json.py` (Phase 3 gate)
- `python .scripts\build_dashboard.py` (Phase 3 gate)
