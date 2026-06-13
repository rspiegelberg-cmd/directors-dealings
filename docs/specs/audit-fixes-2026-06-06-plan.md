# Audit fixes — 2026-06-06 (strategy tracker integrity + drill-down labels)

Origin: CEO-requested forensic audit of dashboard calculations (quant-researcher +
data-integrity-auditor agents, 2026-06-06). Verified against `snapshot_db.py`
text dumps (6,454 tx / 3,057 signals / 3,100 paper_trades).

## What the audit found (data-backed)

- **Table percentages: correct.** Every return/% cell uses the entry value as
  denominator, signs consistent, pence/pounds reconciled, averages exclude
  un-matured rows. Position-return distribution is tight (median 1.01x, p1–p99
  0.76–1.29). No formula bugs. No action needed beyond two cosmetic items
  (logged separately, low priority): company-header `p` label on a £-value, and
  a hit-rate column colour-graded like a return.
- **CAR / backtest engine: sound.** Aligned anchors (entry = first trading day
  after `announced_at`; benchmark sampled on the same date), no benchmark
  zero-default, cost model correct, split-guard already present.
- **£10k Strategy Tracker (B-123): NOT trustworthy as displayed. HIGH.**
  `build_strategy_tracker` reused the backtest entry/exit/cost spine but dropped
  all three data-quality guards. Proxy rebuild on live data (flat £10k,
  1,307 closed positions): gross P&L +2.77%, of which **one position (TIN,
  7.8p→£1.16 = 14.87x unadjusted consolidation) = 38%**. Top 2 names = 45%.
  Excluding TIN → +1.71% gross (before subtracting a rising FTSE and costs).
  - IT/REIT/VCT leakage: 0 in practice (signals not fired on excluded issuers).
  - Hard-excluded tickers (HDD/DCTA): 6 deduped positions leaked in (backtest
    excludes them). Returns in-band, small effect, but should still be excluded
    for parity with the backtest.
- **Cohort drill-down: mixed windows in adjacent columns. MEDIUM.**
  "Stock return" (`abs_return_ann`, announcement-to-latest, lifetime) sits beside
  "Excess vs benchmark (T+21)". Reader may subtract one from the other; the
  windows differ. Tooltip is correct; column header does not state the window.
- **Monthly Activity drill-down: confusing label. LOW.** The bars are correct
  (buys and sells bucketed cleanly — verified). The drill-down panel lists the
  month's biggest transactions of *all* types under the title "top transactions",
  so a large BUY appears under a sell-heavy month and looks mis-bucketed.

## Fixes (this change set — all Zone A / code only)

### 1. Strategy tracker guards (HIGH) — `export_dashboard_json.build_strategy_tracker`
Mirror `backtest.py`:
- Import `EXCLUDED_TICKERS`, `SPLIT_GUARD_MAX_RATIO`, `SPLIT_GUARD_MIN_RATIO`
  from `backtest` (guarded fallback to `["HDD","DCTA"]`, 4.0, 0.25).
- SQL `WHERE`: add `AND t.ticker NOT IN (...)` and
  `AND COALESCE(tm.is_excluded_issuer, 0) != 1`.
- Per-position split guard: drop a position when its mark/entry price ratio is
  `> MAX` or `< MIN`. Mark = realised exit close if closed, else the latest
  close ≤ today (also removes a still-open consolidation and caps stale marks).
- Add transparency counters to `summary`: `excluded_split`, `excluded_ticker`,
  `excluded_issuer` (additive; renderer ignores unknown keys).

Out of scope (logged as follow-up): distinguishing "still maturing" from
"delisted & stale" for never-realised positions. The split guard caps the worst
case; a fuller staleness model is a separate, smaller ticket.

### 2. Cohort drill-down label (MEDIUM) — `render_performance.py` COLS
- Relabel `abs_return_ann` header `Stock return` → `Stock return (since announcement)`
  and tighten the tooltip to contrast it explicitly with the T+21 windows.
  Maths unchanged.

### 3. Monthly Activity drill label (LOW) — `render_performance._monthly_buysell_chart`
- Retitle the drill panel `… — top transactions` → `… — top transactions (buys & sells)`
  so the mixed-type list can't be read as the sell-bar breakdown. Type column
  already colours BUY green / SELL red.

## Verification
- Update/extend `test_sprint29.py` TestB123StrategyTracker: add a consolidation
  fixture (entry 1.0, exit 20.0) → asserts the position is dropped; add an
  excluded-ticker (`HDD`) fixture → asserts it's dropped. Keep existing 3 tests
  green (TST fixture is flat-priced, in-band, not excluded).
- Run `python -m unittest discover -s .scripts -p "test_*.py"` (bash, read-only).
- Verify edited files with the Read tool (FUSE truncation guard).
- Rupert deploys: `python .scripts/export_dashboard_json.py` then
  `python .scripts/build_dashboard.py`, then `python .scripts/snapshot_db.py`.
  Re-run the £10k proxy check to confirm the artifact-free excess.
