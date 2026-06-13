# Sprint 29 Plan — Performance enrichment + Paper P&L Phase B
**Date:** 2026-06-04  
**Status:** PLAN — awaiting Rupert approval before any code is written

---

## Scope summary

8 items in order of execution. B-104 is a hard dependency for B-100 Phase B;
everything else is independent.

| Order | ID | Size | What |
|-------|----|------|------|
| 1 | B-104 | S | Fix "By transaction size" filter — include all buy signals |
| 2 | B-079 | XS | Confirm focus-view header mean = export `mean_car_t21_overall` |
| 3 | B-098 | S | Absolute stock return column (Today + Company pages) |
| 4 | B-009 | S | CAR sparkline: 9 weekly → 12 monthly; fix forward-fill |
| 5 | B-019 | S | CAR chart per-series click-to-toggle + double-click solo |
| 6 | B-103 | S | Sortable table headers on Today + Performance pages |
| 7 | B-102 | M | Monthly buy/sell £-value chart on Performance page |
| 8 | B-100 Phase B | M | Paper trade write path — eval_signals + close_paper_trades |

---

## Pre-existing state

- **`HIGH_CONVICTION_NON_NED_SIGNALS`** in `export_dashboard_json.py` (line 92) contains only `{t1a_ceo_founder_buy, t1b_cfo_buy, t7_chair_buy, t2_exec_buy}`. The render tile `scope_note` still says "T1 + T2 buys only".
- **MTM column** already exists on the Today page and the Performance paper-book table. It shows `(latest_close - entry_close) / entry_close` net of 50bps cost. The Company page (`render_company.py`) has **no** return column.
- **Focus-view header** (render_performance.py lines 1972–1987): JS computes `sum(month_mean_car * month_n) / total_n` = simple mean of all individual CARs. This IS the same maths as `mean_car_t21_overall` in the export. Difference is only floating-point rounding. B-079 is therefore a label/tooltip fix only, no logic change.
- **sparkline_points** in the export (`export_dashboard_json.py` ~line 2361): already monthly, one point per calendar month. The scoreboard tiles render them in a tiny Chart.js chart. B-009 is about the JS rendering window (how many points it shows / how it handles nulls).
- **CAR chart** in `render_performance.py`: single signal-group line chart. No per-series toggle exists today.
- **Table headers** on Today/Performance: plain `<th>` with no click handlers.
- **Monthly buy/sell data**: not yet exported. Need to add to `export_dashboard_json.py`.
- **Paper trades table** (`db_schema.sql` line 95): schema already exists with `status IN ('planned','open','closed','skipped')`. `eval_signals.py` does NOT yet write to `paper_trades`. No `close_paper_trades.py` exists.

---

## Item-by-item plan

---

### B-104 — Fix "By transaction size" filter (S)

**Problem:** Only T1a/T1b/T7/T2 signals are included in the bucket cohort. T3 NEDs, T4, T5, T6, S1, F1, B1 are silently excluded — a T4 director buying £1m doesn't appear in the tile at all.

**Fix — `export_dashboard_json.py`:**
- Rename `HIGH_CONVICTION_NON_NED_SIGNALS` → `VALUE_BUCKET_BUY_SIGNALS` (or just expand in-place, same constant name is fine).
- Add to the tuple: `t3_ned_buy`, `t4_other_buy`, `t5_pca_buy`, `t6_company_sec_buy`, `s1_cluster_buy`, `f1_first_time_buy`, `b1_lone_conviction_buy`.
- Exclude: `b2_crowded_cluster_kill` (kill signal, not a buy), `t0_cluster_combo` (composite).
- Both callsites use the constant: `_bucket_scope_filter` (line 877) and the `scope_filter_fn` lambda (line 2644). Both update automatically by changing the tuple.

**Fix — `render_performance.py`:**
- COHORT_TILES `scope_note` for `tile_id="bucket"`: change from `"T1 + T2 buys only"` → `None` (all buy signals, no caveat needed).

**Tests — `test_sprint29.py` (new file):**
- T3/T4/S1/F1/B1 rows ARE counted in bucket scope; T0/B2 rows are NOT.

**No Zone B touch. No schema change.**

---

### B-079 — Confirm focus-view mean label (XS)

**Finding:** Focus-view JS computes `sum(month_mean * n) / total_n` which is exactly a simple mean of individual CARs — same as `mean_car_t21_overall`. Logic is correct, no change needed.

**Fix — `render_performance.py` (JS section ~line 1984):**
- Add `title="Mean net CAR since inception (all fired signals)"` attribute to the strip `<b>` tag that shows the mean.
- Add a `// B-079: same maths as mean_car_t21_overall in the export` comment.

**No export change. No tests needed (XS).**

---

### B-098 — Absolute stock return column (S)

**Design decision:** The existing `mtm_pct` on Today page is net-of-cost (50bps deducted). B-098 asks for the raw `(latest_close - entry_close) / entry_close` before cost. These are different numbers.

**Approach:** Add a **second field** `abs_return_pct` (gross, no cost) alongside `mtm_pct` in dealings rows. Rename the Today page column from "MTM*" → "Stock Rtn*" and display `abs_return_pct` there (not mtm_pct), so the column is "what the stock has done since the signal fired". Keep `mtm_pct` in the export for backward compat and for the paper-book table (which correctly shows net P&L).

**Fix — `export_dashboard_json.py` (dealings row builder ~line 1595):**
- Compute `abs_return_pct = round((latest_close - entry_close) / entry_close * 100, 1)` using the same `entry_close` and `latest_close` already fetched for `mtm_pct`.
- Add `"abs_return_pct": abs_return_pct` to the row dict.

**Fix — `render_index.py`:**
- Change `<th>MTM*</th>` → `<th>Stock Rtn*</th>`.
- In `_render_row`, read `row.get("abs_return_pct")` instead of `row.get("mtm_pct")`. Same display logic (coloured glyph + pct).
- Update the footnote from "MTM: ..." to "Stock Rtn: raw stock return since T+1 close after signal date, gross of costs. CAR (Performance page) is excess return vs benchmark, net of costs."

**Fix — `render_company.py` (Company page transactions table):**
- The company JSON has `latest_close` (from build_dashboard.py line 156/310) and transaction `price` (what the director paid).
- Add an "Abs Rtn" column to the transactions table: `(latest_close - tx_price) / tx_price * 100`.
- Compute inline in `render_company.py` — no Zone B change needed.
- Show `–` when either price is null or zero.
- Note: uses `tx_price` (actual transaction price), not `entry_close` (T+1 signal price). Different baseline. Label the column accordingly: "Rtn vs Deal Price".

**Tests — `test_sprint29.py`:**
- `abs_return_pct` present in dealings row, correct maths, independent of cost deduction.
- Company page renders column with correct value and graceful null handling.

---

### B-009 — Fix CAR sparkline (S)

**Problem:** The scoreboard tile sparklines currently bucket by 9 points (likely 9 weekly/arbitrary points). Should be 12 monthly points — the most recent 12 calendar months. Forward-fill currently uses 0.0 for gap months, masking data quality; should use `null` (Chart.js will break the line).

**Where:** `render_performance.py` — the JS block that renders scoreboard sparklines, reading `sparkline_points` from the GROUPS JSON.

**Fix — `render_performance.py` (scoreboard JS):**
1. Filter `sparkline_points` to the last 12 entries (already monthly in export).
2. Replace any `0` or `0.0` forward-fill with `null` (the export already emits explicit null for gap months since the locked decision; the JS should honour it, not coerce to 0).
3. Set `spanGaps: false` on the sparkline Chart.js config so null breaks the line.
4. If the existing sparkline rendering is using a weekly window — find the slice window and change from 9 to 12 (last 12 months).

**Tests — `test_sprint29.py`:**
- Sparkline slice uses at most 12 months.
- Null gap months are not coerced to 0.

---

### B-019 — CAR chart per-series toggle + double-click solo (S)

**Where:** `render_performance.py` — JS section that renders the main Level-2 cohort chart (~line 2076 onwards).

**Fix:** After `new Chart(ctx, ...)`, add event delegation on the cohort pills (the clickable signal buttons above the chart). Currently clicking a pill replaces the entire dataset. B-019 wants a **toggle overlay** mode instead.

**Implementation approach:**
- A per-series toggle means clicking a pill that is already active **hides** that series on the chart instead of rebuilding. Double-click solos it.
- Maintain a `Set<string>` of visible group IDs in JS (`var visibleGroups`). On single-click: toggle the group in/out. On double-click: solo (set `visibleGroups` to just that group).
- When `visibleGroups` changes, call `chart.data.datasets.forEach(ds => ds.hidden = !visibleGroups.has(ds._groupId))` + `chart.update()`.
- The existing `build()` call wipes and rebuilds on every pill click. Keep `build()` for the initial render and horizon changes; add incremental show/hide on repeated pill clicks.
- Pill visual state: add `opacity-40` Tailwind class to hidden-series pills so the user can see what's active.

**Tests — `test_sprint29.py`:**
- JS logic test is hard to unit-test in Python. Instead: verify the generated HTML includes the toggle JS logic (assert key variable names or event listener patterns are present in the rendered output).

---

### B-103 — Sortable table headers (S)

**Tables to sort:** Today page (today + this-week tables in `render_index.py`), Performance page cohort tables (the COHORT_TILES rows in `render_performance.py`). 

**Approach:** Pure client-side JS — no server change. Add `data-sort` attribute to each `<th>`. Add a shared `<script>` block in the page that wires a click handler.

**Sort spec:**
- Numeric columns (£ Value, MTM/Stock Rtn): numeric sort
- Date/time column: ISO string sort
- Ticker, Company, Director, Role: alphanumeric
- Signal badges: count of badges as sort key
- Click once: ascending; click again: descending; visual indicator (▲/▼) in header
- State is NOT persisted to localStorage (per existing pattern for this feature)

**Files:**
- `render_index.py`: add `data-sort` to each `<th>`, add shared `_table_sort_js()` function, call it in page footer.
- `render_performance.py`: same for the cohort tile tables.

**Tests — `test_sprint29.py`:**
- `data-sort` attributes present on key column headers.
- JS sort block present in rendered HTML.

---

### B-102 — Monthly buy/sell £-value chart (M)

**Design:** Trailing 12-month grouped bar chart on Performance page. Buys = positive bars (above axis, green); sells = negative bars (below axis, rose). X-axis = calendar months; Y-axis = £ GBP total value.

**Part 1 — Export (`export_dashboard_json.py`):**
Add a `monthly_buysell` key to the main payload:
```json
{
  "monthly_buysell": {
    "months": ["2025-07", ..., "2026-06"],       // 12 months trailing
    "buy_values": [1234567.0, ..., null],         // null if no buys that month
    "sell_values": [-456789.0, ..., null],        // negative; null if no sells
    "buy_counts": [12, ..., 0],
    "sell_counts": [3, ..., 0]
  }
}
```
- Source: `transactions` table, grouping by `strftime('%Y-%m', COALESCE(announced_at, date))`, filtering last 12 months, excluding `is_excluded_issuer`.
- Uses `SUM(value)` for buy_values (positive) and `-SUM(value)` for sell_values (stored as negative so Chart.js places them below axis).

**Part 2 — Render (`render_performance.py`):**
- New `_monthly_buysell_chart(perf_data)` function.
- Renders a Chart.js bar chart below the existing Performance header, above the cohort tiles.
- Bar colours: buys = `#10b981` (emerald), sells = `#f43f5e` (rose), matching the signal palette.
- Y-axis label: "£ value (GBP)"; formatted with k/M suffix.
- Tooltip: "N buys totalling £X" / "N sells totalling £X".
- Empty state: hide section if both buy_values and sell_values are all null/zero.

**Tests — `test_sprint29.py`:**
- Export function produces correct monthly aggregates with trailing 12-month window.
- Sell values are negative in the JSON.
- Null for months with no activity.

---

### B-100 Phase B — Paper trade write path (M)

**Design (from roadmap decisions):**
- Position sizing: `notional_gbp = min(transaction.value, 50_000)` (conviction-scaled, £50k cap).
- Entry: T+1 close after `fired_at` date. Status starts as `OPEN` when entry price found, `planned` when not yet.
- Exit at T+21 (21 trading days after entry date). T+90 exit also closed by `close_paper_trades.py`.
- Both scripts are Zone B (Rupert runs them).

**Part 1 — `eval_signals.py`:**
After `_upsert(conn, r)` in the first pass (line 304), if the signal is a buy signal (NOT b2, NOT t0), open a paper_trade row:
```python
_open_paper_trade(conn, r, tx)
```
Where `_open_paper_trade`:
- Generates `trade_id = f"pt_{signal_id}_{fingerprint}"` (stable, idempotent).
- Looks up entry_close = first close in `prices` for ticker on or after `tx["announced_at"]`.
- If found: `status='open'`, `entry_date=...`, `entry_close=...`, `shares=notional_gbp/entry_close`.
- If not found: `status='planned'`, entry fields NULL.
- Uses `INSERT OR IGNORE` on `trade_id` so re-runs don't duplicate.
- Also opens for second-pass signals (T0) if applicable. But T0 is a combo, not a pure buy — **exclude T0 from paper trades** (same as paper-book Phase A which excludes `t0`).

**Part 2 — New script `.scripts/close_paper_trades.py`:**
```
python .scripts\close_paper_trades.py [--horizon t21|t90] [--dry-run] [--verbose]
```
- Default horizon: t21 (21 trading days). 
- Finds all `paper_trades` where `status='open'` and `entry_date` was >= 21 trading days ago (counting from `prices` table, not calendar days).
- Looks up `exit_close = close from prices on the exit date`.
- Updates: `status='closed'`, `exit_date=...`, `exit_close=...`, `updated_at=now`.
- Skips if price not found (leaves open; logs a warning).
- `--dry-run` prints what would be closed, no writes.
- Also handles `status='planned'` rows where entry price now available: upgrades them to `open` first.

**No new schema changes** (schema v8 already has `paper_trades`).

**Tests — `test_sprint29.py`:**
- `eval_signals.py`: paper_trade row created on signal fire; trade_id is idempotent; b2/t0 excluded.
- `close_paper_trades.py`: closes row when 21 days elapsed; dry-run makes no writes; planned→open upgrade works.

---

## Files touched summary

| File | Items | Zone |
|------|-------|------|
| `.scripts/export_dashboard_json.py` | B-104, B-098, B-102 | A |
| `.scripts/dashboard/render_performance.py` | B-104, B-079, B-009, B-019, B-103, B-102 | A |
| `.scripts/dashboard/render_index.py` | B-098, B-103 | A |
| `.scripts/dashboard/render_company.py` | B-098 | A |
| `.scripts/eval_signals.py` | B-100 Phase B | A (code only — writes go via Rupert) |
| `.scripts/close_paper_trades.py` | B-100 Phase B | A (new Zone B script) |
| `.scripts/test_sprint29.py` | all | A |

**Zone B scripts Rupert runs after this sprint:**
```
python .scripts\eval_signals.py --rebuild        # opens paper_trade rows
python .scripts\close_paper_trades.py --verbose  # closes any matured
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

---

## Gate

Sprint 29 QA agent checks before any Rupert deploy:
1. `python -m unittest discover -s .scripts -p "test_*.py"` — full suite green.
2. `test_sprint29.py` all pass.
3. No Zone B writes from bash.
4. B-104 diff: bucket cohort row count increases (more signals in scope).
5. B-100 Phase B: `paper_trades` table still empty until Rupert runs `eval_signals.py --rebuild`.
6. File integrity: Read tool on all 7 changed files — line counts match expectations, no truncation.
