# Sprint 34 plan — 2026-06-05

Three items. All Zone A (code only). Rupert runs `export_dashboard_json` →
`build_dashboard` once at the end.

Items:
- **B113** — Benchmark return column alongside every RTN column
- **C**    — Cluster conviction score
- **#3**   — Monthly activity depth (click-through + 12mo totals + trend)

Build order: B113 → C → #3 (cheapest to most complex).

---

## B113 — Benchmark return column alongside every RTN

### What
Wherever the dashboard shows a stock return, add a "Bmk" column showing what
the sector benchmark returned over the same period. Lets the reader instantly
see "stock +8%, sector +12% → actually underperformed" vs "stock +8%, sector
-3% → strong outperformance."

### Three surfaces

**Surface 1 — This Week table** (`render_index.py` + `build_dealings`)

- Current: `abs_return_pct` = gross stock return, T+1 close → latest close
- Gap: benchmark return over the SAME period not computed in `build_dealings()`
- Fix:
  1. Add `_bench_return_pct(conn, ticker, announced_at, dates_cache,
     close_cache, latest_cache, tickers_meta, date_field)` to
     `export_dashboard_json.py`. Mirrors `_abs_return_pct` but fetches the
     sector benchmark ticker (via `tickers_meta`) and returns
     `(bench_latest / bench_t1 - 1) * 100`.
  2. Wire `tickers_meta` into `build_dealings(conn, today)` — currently it
     is loaded in `build_payload()` but not passed down. Change signature to
     `build_dealings(conn, today, tickers_meta)` and update the call site.
  3. Add `"bench_return_pct": bench_ret` to the `_row()` dict.
  4. In `render_index.py` `_row_html()`: render `bench_return_pct` using
     `car_cell()` in a new `<td>`.
  5. Add "Bmk" `<th>` to the table headers (after the Stock Rtn column).
     Update footer footnote.

**Surface 2 — Performance top/bottom firings panel** (`render_helpers._firing_row`)

- Current: `abs_return` rendered as `abs_html`. `_bench_{horizon}` already
  on every firing row dict (loaded from CSV in `load_backtest_csv`).
- Gap: `bench_return` not included in the `_firing_row()` dict and not
  rendered in `render_helpers._firing_row()`.
- Fix:
  1. In `export_dashboard_json._firing_row()`: add
     `"bench_return": round(bench * 100.0, 1) if bench is not None else None`
     (bench = `row.get(f"_bench_{horizon}")`, already computed at line ~670).
  2. In `render_helpers._firing_row()`: read `firing.get("bench_return")`,
     call `car_cell()`, render after `abs_html` as a new `<td>`.
  3. Add "Bmk" column header wherever `_firing_row` is used (render_performance
     top/bottom panel headers).

**Surface 3 — Company page transactions table** (`render_company.py`)

- Current: `Rtn*` computed inline: `(latest_close / price - 1) * 100`.
  `latest_close` comes from `company.get("latest_close")`.
  The `prices` list in company data already carries `bench` close values per
  date (confirmed at line ~16 of the render_company docstring).
- Gap: no benchmark return computed or rendered.
- Fix (all inline in `render_company.py` — no exporter change needed):
  1. Before the transaction loop, find `bench_entry` = bench value at or
     nearest-after the transaction date; `bench_latest` = last bench value
     in `prices`. Use `company.get("prices") or []`.
  2. Compute `bench_ret_pct = (bench_latest / bench_entry - 1) * 100`
     per row (only for BUY rows with a valid price).
  3. Add "Bmk" `<th>` header; render `bench_ret_pct` using `h.car_cell()`.
  4. Update the footnote: "*Rtn: gross stock return from deal price to latest
     close. **Bmk: sector benchmark return over same period.**"

### Notes
- Label is "Bmk" (4 chars) — narrow enough not to break the 8-col layout.
- All three render paths use `h.car_cell()` for consistent green/red colouring.
- No new unit test: B113 is purely display. Visual QA suffices (confirmed
  +/- colour matches, a known up-market period shows positive Bmk).

---

## C — Cluster conviction score

### What
Score each cluster in the Active Clusters panel by:
`director_count`, `aggregate_value_gbp`, and `time-compression` of the buys.
Sort panel by score desc. Show score as a small chip on each card.

% of board deferred (no board-size data).

### Formula (transparent, documented in code)

```python
from math import log10

days_span = (last_d - first_d).days          # 0 if same-day
value_score = min(4.0, max(0.0, log10(max(1, agg_value)) - 3))
# £1k→0, £10k→1, £100k→2, £1m→3, £10m→4 (capped)
compression = max(0.0, (30.0 - days_span) / 30.0)
# 1.0 = all in one day, 0.0 = spread over >=30 days
conviction = round(
    director_count * 3.0 +   # 2 dirs→6, 5 dirs→15
    value_score * 2.0 +       # £100k→4, £1m→6
    compression * 2.0,        # same-day→2, 15d→1
    1
)
```

Range: minimum ~6 (2 dirs, £1k, 30 days); typical strong cluster ~12–16.

### Changes
1. **`export_dashboard_json.py` `compute_active_clusters()`**:
   - Import `math.log10` (already in stdlib, no new import if `import math`).
   - After building each cluster dict, compute `conviction` score.
   - Add `"conviction": conviction` to the cluster dict.
   - Change final sort: `out.sort(key=lambda c: -c["conviction"])` instead of
     `last_buy_date` desc.

2. **`render_index.py` `_cluster_card()`**:
   - Read `cluster.get("conviction")` (or `0` as fallback).
   - Render a small grey chip: `★ {conviction}` alongside the S1/brewing badge.
   - Position: right side of the ticker row (replacing nothing — just added
     alongside the existing badge).

### Test plan
Unit test fixture: cluster A (3 dirs, £500k, 10 days) should outscore cluster
B (2 dirs, £50k, 25 days). Assert `conviction_A > conviction_B`.

---

## #3 — Monthly trading activity depth

### What
Three additions to the buy/sell activity chart on the Performance page:

1. **Click-through**: click a bar segment → inline table of transactions for
   that month + type (buys or sells).
2. **12-month totals**: show trailing-12 totals (count + £) above/below chart.
3. **Rolling trend glyph**: trailing-12 this month vs trailing-12 last month,
   direction on £. Shows "n/a" if < 13 months of data.

### Exporter changes (`build_monthly_buysell`)

Add to the return dict:

```python
# Click-through: flat list of {month, type, ticker, company, director, value_gbp}
"monthly_txns": [...],

# 12-month trailing totals (sum of all 12 months in the current axis)
"trailing12_buy_gbp":   float | None,
"trailing12_buy_count": int,
"trailing12_sell_gbp":  float | None,
"trailing12_sell_count": int,

# Rolling trend (compare trailing-12 this month vs last month, on £)
# prev_trailing12 = sum of the 12 months ending with months[-2]
"trend_buy_direction":  "up" | "down" | None,   # None if < 13mo data
"trend_buy_delta_pct":  float | None,
"trend_sell_direction": "up" | "down" | None,
"trend_sell_delta_pct": float | None,
```

For `monthly_txns`: extend the existing SQL query to also pull
`ticker`, `company`, `director` columns. Emit one row per transaction
(not aggregated). Cap at ~500 rows to keep JSON lean (12 months of PDMR
data is typically 200–400 rows).

For the trend: the function already builds a 12-month `months` axis.
To compute `prev_trailing12`, extend the raw SQL lookback to 13 months
and sum the first 12 (months[0] through months[-2]) for prev, and the
current 12 (months[0] through months[-1]) for this.

Actually simpler: run the same query with a 13-month lookback. The 12-month
totals use `months` (the current 12). The prev trailing-12 uses
`months_13[0:12]` (shifting the window back one month).

### Render changes (`render_performance.py`)

1. **Totals bar** above the chart: two compact stat chips —
   "Buys: £Xm (N)" and "Sells: £Xm (N)" — with trend glyph (▲/▼) +
   delta%. If `trend_*_direction` is None, show "n/a" for trend.
   Reuse the existing ▲/▼ colouring convention.

2. **Click handler** on the Chart.js bar chart: on click, receive the month
   index and dataset index (0=buys, 1=sells) from the Chart.js `onClick`
   callback. Filter `monthly_txns` array (injected as inline JS variable)
   by `month === clickedMonth && type === clickedType`. Render a small
   summary table below the chart (or in a collapsible div). Include columns:
   Date | Ticker | Company | Director | £ Value.

3. **Null-value guard**: if `trend_*_direction` is None (< 13 months),
   render "trend: n/a" in slate text rather than showing a misleading glyph.

### Risks
- `monthly_txns` adds ~10–30 KB to the JSON — acceptable.
- £ value depends on clean `value` field: exclude rows where `value IS NULL`
  from the trend calculation (already done for buy_val via `t.value IS NOT NULL`
  filter in the existing SQL). Count is always reliable; flag separately.
- 13-month window needs a trivial axis extension — no schema change.

### Test plan
Unit: `build_monthly_buysell` fixture with 13 months of synthetic data;
assert `trailing12_buy_gbp` matches the sum of all 12 months; assert trend
direction is "up" when this month's trailing-12 > last month's trailing-12.
Unit: < 12 months of data → `trend_*_direction` is None.

---

## Sequence summary

| Phase | Changes | Files touched |
|-------|---------|---------------|
| B113-1 | `_bench_return_pct()` helper + wire into `build_dealings` | `export_dashboard_json.py` |
| B113-2 | Render Bmk column — This Week table | `render_index.py` |
| B113-3 | Add `bench_return` to `_firing_row` dict | `export_dashboard_json.py` |
| B113-4 | Render Bmk column — top/bottom panels | `render_helpers.py` + `render_performance.py` (headers) |
| B113-5 | Render Bmk column — company page | `render_company.py` |
| C-1    | Conviction score in `compute_active_clusters` | `export_dashboard_json.py` |
| C-2    | Score chip in `_cluster_card` | `render_index.py` |
| #3-1   | Extend `build_monthly_buysell` (txns + totals + trend) | `export_dashboard_json.py` |
| #3-2   | Totals bar + trend glyph above chart | `render_performance.py` |
| #3-3   | Click-through handler + inline table | `render_performance.py` |

**Rupert runs once at end:** `export_dashboard_json` → `build_dashboard`
