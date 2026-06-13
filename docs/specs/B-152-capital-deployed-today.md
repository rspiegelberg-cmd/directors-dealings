# B-152 — Capital Deployed Trending Panel (Today / New Home Page)

**Status:** Spec complete — awaiting Rupert approval before build  
**Raised:** 2026-06-09  
**Scope:** `export_dashboard_json.py`, `render_index.py` (or a new home-page renderer),
one new helper function  
**Depends on:** B-149 (Sprint 56 small-cap split) must be deployed first — the
`small_cap` column in `tickers_meta` and the `signals_small.json` / `signals_large.json`
infrastructure are prerequisites.

---

## 1. What Rupert asked for

> "Capital Deployed on Today page — split into three (All / Small-cap / Large-cap),
> include Capital Deployed (Live), 3-month moving average of the delta for value and
> volume, and a mini chart of the value over the last 3 months for each of the three
> categories. This should be scoped and designed into the new home page."

In plain English: replace the current single "Capital deployed" stat in the Live Paper
Book section with three side-by-side panels — one for All signals, one for Small Cap
only, one for Large Cap only. Each panel shows:

1. The current live capital deployed (£ notional of open positions)
2. A trend chip: how this compares to the 3-month moving average of deployed capital
3. A second trend chip: how the count of open positions (volume) compares to its
   3-month moving average
4. A 12-week sparkline of weekly deployed capital over the last 3 months

The "new home page" mentioned by Rupert is not yet built. Until it exists, these panels
belong on the existing Today page (`index.html`), rendered by `render_index.py`. The
spec is written to slot cleanly into whichever page ends up as the new home.

---

## 2. Current state — what the Today page shows today

The Today page (`render_index.py`) has **no Capital Deployed display at all**. The
three top-strip KPI tiles are:

| Tile | What it shows |
|------|---------------|
| Signals today | Count of distinct PDMR transactions today that fired ≥1 signal |
| Active clusters | Count of active / brewing S1 clusters |
| Open paper P&L | Mark-to-market of all open paper positions (from `paper_trades` table) |

Capital Deployed (the live paper book notional) lives **only on the Performance page**
(`render_performance.py → _paper_book_section()`), which reads:

- `signals_data["paper_book"]["summary"]["open_notional_gbp"]` — point-in-time total
  notional of all OPEN paper positions
- `signals_data["paper_book"]["summary"]["open_count"]` — count of open positions

This is a **single point-in-time number**. There is no time series. There is no
small-cap / large-cap split. The Performance page's paper book section also shows a
full position table (not shown on Today).

The `build_paper_book_summary()` function in `export_dashboard_json.py` does already
have `market_cap_gbp` on each position row (added in B-146), so the data to split by
cap exists at the position level — it just isn't aggregated by cap band or over time.

---

## 3. Data availability — what exists vs what needs to be built

### 3a. Small / large cap classification

**Available today.** Migration 011 added `small_cap INTEGER` to `tickers_meta`. The
flag is populated by `classify_small_cap.py` (run inside `refresh_all.py`). Threshold:
`small_cap = 1` if `market_cap_gbp < £500m`, else `= 0`. Some tickers still have
`small_cap = NULL` (pending classification).

`build_paper_book_summary()` already joins `tickers_meta` and returns
`market_cap_gbp` on each position row. The `small_cap` flag is a one-line addition
to that JOIN.

### 3b. Does the export currently emit time-series data for capital deployed?

**No.** `build_paper_book_summary()` returns only a point-in-time snapshot:

```python
"summary": {
    "open_count":        int,      # OPEN positions right now
    "closed_count":      int,      # CLOSED positions
    "open_notional_gbp": float,    # total notional of OPEN positions
    "open_mtm_pct_mean": float|None,
    "open_winners":      int,
    "open_losers":       int,
}
```

There is no history. The export runs once per pipeline run and overwrites the JSON. No
weekly snapshots are accumulated anywhere.

The only function with a historical time series is `build_monthly_buysell()`, which
produces monthly buy/sell values over a 12-month trailing window — but that measures
**transaction value** (what directors paid for their shares), not **paper-book notional**
(what the system has deployed in its simulated portfolio).

### 3c. What "3-month moving average of delta for value and volume" requires

This is the most complex part of the request. To compute a 3-month moving average of
the change in deployed capital, you need to know what deployed capital was at each
prior weekly or monthly point in time. That requires **historical snapshots**.

Two possible approaches:

**Option A — Reconstruct from signals history (preferred, no schema change)**

The signals table contains `fired_at` dates for every signal firing. The paper book
logic already simulates open/closed status based on `hold_days` relative to today.
By shifting the "today" parameter backwards in time (walking back over 13 weekly
anchor dates), `build_paper_book_summary()` can be called 13 times to produce a
12-week deployed-capital series for each band. This is a pure computation — no new DB
tables, no stored snapshots.

Cost: `build_paper_book_summary()` currently runs one prices query per ticker. For 13
historical points it would run 13× the price queries — roughly 2–5 seconds extra per
export run. Acceptable for a nightly pipeline.

**Option B — Store weekly snapshots in the DB (heavy, not recommended for v1)**

Add a `capital_deployed_weekly` table. Have the pipeline write a row after each export.
Gives accurate historical series but adds schema complexity, a new Zone-B dependency,
and fragility around pipeline gaps.

**Recommendation: Option A for v1.** Walk back 13 weekly anchor dates, call
`build_paper_book_summary()` with each as the simulated "today", emit 12 weekly data
points per band. The 3-month moving average of the weekly series is the trailing-4-week
mean of the weekly values; the "delta" is `current – moving_average`.

### 3d. "Capital Deployed (Live)" clarification

This means the point-in-time `open_notional_gbp` — the sum of conviction-sized
notional stakes for all signals currently within their hold window. It is NOT the
strategy tracker's `capital_deployed_gbp` (which uses a flat £10k stake per
position). The paper-book notional uses the log-scale conviction sizing from spec 07.

---

## 4. Proposed data model — new fields added to `export_dashboard_json.py`

Add a new top-level function `build_capital_deployed_panels(conn, today)` that returns:

```python
{
    "as_of": "2026-06-09",
    "all": {
        "live_notional_gbp":  float,        # open notional right now, all bands
        "live_count":         int,          # open positions right now
        "weekly_notional":    [float|None, ...],  # 12 values, oldest first
        "weekly_count":       [int, ...],         # 12 values, oldest first
        "weekly_dates":       ["2026-03-17", ...],# 12 ISO week-start dates
        "ma3m_notional_gbp":  float|None,   # trailing-4-week mean of weekly_notional
        "ma3m_count":         float|None,   # trailing-4-week mean of weekly_count
        "delta_notional_gbp": float|None,   # live_notional - ma3m_notional
        "delta_count_pct":    float|None,   # (live_count - ma3m_count) / ma3m_count * 100
    },
    "small": { ...same keys... },
    "large": { ...same keys... },
}
```

This object is added to `signals.json`, `signals_small.json`, and `signals_large.json`
under the key `"capital_deployed_panels"`.

The weekly series uses Monday-anchored weeks, walking back 13 anchor dates from today
(gives 12 weekly deltas). Each anchor calls `build_paper_book_summary()` with
`as_of=anchor_date`. The simulated open/closed status at each anchor is computed by
checking which signals had `hold_days <= _PAPER_BOOK_HOLD_DAYS` on that anchor date.

**Small-cap filter:** `build_paper_book_summary()` gets a new optional parameter
`small_cap: int | None = None`. When set, only positions where the ticker's `small_cap`
flag matches are included.

---

## 5. UI spec — three Capital Deployed panels

### 5a. Placement

These panels replace the current "Open paper P&L" tile in the top-strip (tile 3) on
the Today page, or they are added as a new full-width row immediately above the main
table grid. The recommended placement is a **new full-width row below the existing
3-tile strip**, to avoid cramming three sub-panels into one tile slot.

If a dedicated "new home page" is built in a future sprint, this row becomes a natural
section on that page.

### 5b. Panel layout

```
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│  ALL                    │  │  SMALL CAP              │  │  LARGE CAP              │
│                         │  │                         │  │                         │
│  £247k                  │  │  £182k                  │  │  £65k                   │
│  Capital deployed live  │  │  Capital deployed live  │  │  Capital deployed live  │
│                         │  │                         │  │                         │
│  ▲ +£31k vs 3m avg      │  │  ▲ +£28k vs 3m avg      │  │  ▼ −£3k vs 3m avg       │
│  ▲ +2 positions         │  │  ▲ +3 positions          │  │  ▼ −1 position          │
│                         │  │                         │  │                         │
│  [sparkline 12 weeks]   │  │  [sparkline 12 weeks]   │  │  [sparkline 12 weeks]   │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘
```

### 5c. KPI number

- Format: `£Xk` (e.g. `£247k`) or `£X.Xm` for values above £1m, consistent with
  the existing `open_notional_gbp` display in `render_performance.py`
- Font: `text-2xl font-semibold tabular-nums text-slate-900`
- Label below the number: `text-[10px] uppercase tracking-wide text-slate-500`
  → `"Capital deployed (live)"`

### 5d. Delta chips

Two chips per panel, stacked below the KPI:

**Value delta:**
- If `delta_notional_gbp` is positive: `▲ +£Xk vs 3m avg` in `text-emerald-600`
- If negative: `▼ −£Xk vs 3m avg` in `text-rose-600`
- If zero / null: `— vs 3m avg` in `text-slate-400`
- Format: same £k / £m as the main KPI

**Volume delta:**
- If `delta_count_pct` is positive: `▲ +X% positions` in `text-emerald-600`
- If negative: `▼ X% positions` in `text-rose-600`
- Both chips use `text-xs` weight, shown in a single line using a `·` separator

### 5e. Sparkline

- Reuse the existing `_brewing_sparkline_svg()` helper from `render_index.py` — it
  already takes a list of float values and renders a polyline
- Width: 120px, height: 24px
- Data: `weekly_notional` array (12 points, oldest left → newest right)
- Color: `currentColor` (inherits from parent — slate by default)
- Tooltip: not required in v1; add a `title` attribute to the `<svg>` with the latest
  weekly value

### 5f. Visual reference

The existing `_stat()` helper in `render_performance.py → _paper_book_section()` provides
the visual pattern to follow:

```python
_stat("Capital deployed", notional_str)
```

The new panels use the same card style (`bg-slate-50 border border-slate-200 rounded-lg`)
as the existing top-strip tiles.

---

## 6. File-by-file changes

### 6a. `export_dashboard_json.py`

**New function:** `build_capital_deployed_panels(conn, today)`

1. Compute 13 weekly anchor dates (Monday-anchored, walking back from `today`):
   ```python
   def _week_anchors(today, n=13):
       # Walk back to last Monday, then step back by 7 days n-1 more times
       ...
   ```
2. For each band (`all`, `small`, `large`), call `build_paper_book_summary()` with
   each anchor as the simulated as-of date. Extract `open_notional_gbp` and
   `open_count` from the result summary.
3. Compute `ma3m_notional` = mean of the 4 most recent weekly values (weeks 9–12
   of the 12-point series, i.e. the trailing 4 weeks before the current week).
4. Compute deltas: `delta_notional_gbp = live_notional - ma3m_notional`.
5. Return the nested dict described in §4.

**`build_paper_book_summary()` change:** Add `small_cap: int | None = None` parameter.
When set, filter the initial SQL query with:
```sql
AND (tm.small_cap = ? OR (? IS NULL AND 1=1))
```
Or more simply: conditionally append `AND tm.small_cap = ?` to the WHERE clause.

**`build_payload()` change:** Call `build_capital_deployed_panels(conn, today)` and
add it to `signals_payload` under key `"capital_deployed_panels"`.

**`_build_band_signals_payload()` change:** Also call
`build_capital_deployed_panels(conn, today, small_cap=small_cap)` for the appropriate
band and add to the band payload.

### 6b. `render_index.py`

**New function:** `_capital_deployed_row(signals_data)` → returns HTML string.

Reads `signals_data["capital_deployed_panels"]` and renders the three-panel row
described in §5. Uses the existing `_brewing_sparkline_svg()` helper for the
sparklines.

**`render()` change:** Insert the new row between the 3-tile top strip and the main
12-column grid.

No new JavaScript required — the sparkline is server-side SVG; the delta chips are
static HTML generated at build time.

### 6c. Tests

Add `test_capital_deployed_panels.py` (or extend `test_sprint_XX.py`) covering:

- `build_capital_deployed_panels()` returns the correct structure when all tickers
  have `small_cap` populated
- `build_capital_deployed_panels()` returns None values gracefully when no positions
  are open
- `_capital_deployed_row()` renders without error on both populated and empty data

---

## 7. What is NOT in scope

| Item Rupert mentioned | Status | Reason |
|---|---|---|
| "New home page" | Out of scope for B-152 | No new home page renderer exists. The panels are wired into the existing Today page (`render_index.py`). When a new home page is built in a future sprint, these panels migrate there with no data-model changes needed. |
| Real-time / intra-day deployed capital | Out of scope | The paper book is computed at export time (nightly pipeline). The "live" number is live as of the last export run, not real-time. |
| Per-signal breakdown within each cap band | Out of scope | The three panels show aggregate totals only; per-signal breakdown is already available on the Performance page paper book table. |
| Historical weekly snapshots stored in DB | Out of scope for v1 | Option A (recompute from signals history) is used instead. If recompute latency becomes a problem, a stored-snapshots approach (Option B) is a future upgrade. |
| Interactivity (click to drill down) | Out of scope | Static HTML at build time is sufficient for v1. |

---

## 8. Effort estimate

| Layer | Work | Estimate |
|---|---|---|
| `export_dashboard_json.py` — `build_capital_deployed_panels()` function | Walk 13 anchor dates × 3 bands, aggregate, compute MA, deltas | 2.5 hr |
| `export_dashboard_json.py` — `build_paper_book_summary()` small_cap param | SQL clause addition + test | 0.5 hr |
| `export_dashboard_json.py` — wire into `build_payload()` + `_build_band_signals_payload()` | 2 call sites | 0.5 hr |
| `render_index.py` — `_capital_deployed_row()` + `render()` wiring | 3-panel row HTML + sparkline | 1.5 hr |
| Tests | Unit tests for new function + render smoke test | 1.0 hr |
| Integration run + verify | Export + build + visual check | 0.5 hr |
| **Total** | | **~6.5 hr** |

---

## 9. Execution sequence (Rupert runs after code is written)

```powershell
# Step 1 — export JSON (new capital_deployed_panels key flows through)
python .scripts\export_dashboard_json.py

# Step 2 — rebuild dashboard HTML
python .scripts\build_dashboard.py

# Step 3 — snapshot DB for verification
python .scripts\snapshot_db.py
```

No `backtest.py` re-run required — this change reads from the live DB (signals +
prices), not the backtest CSV.

---

## 10. Open questions for Rupert

Before building, please confirm the following:

**Q1 — Placement.** Where should the three panels go?
- Option A: Replace the current "Open paper P&L" tile in the 3-tile top strip
  (the three sub-panels slot into one tile's width — compact but tight)
- Option B: A new full-width row below the 3-tile strip, above the main table
  (recommended — gives each panel breathing room)
- Option C: Defer placement to the new home page sprint and only build the data
  model now

**Q2 — "3-month moving average of the delta" — confirm interpretation.**  
The spec interprets this as: compute a trailing-4-week mean of the weekly deployed
capital series, then show `current – mean` as the delta. Is this correct, or does
Rupert want the 3-month moving average plotted as a line on the sparkline chart, with
the current value annotated relative to it?

**Q3 — What counts as "Capital Deployed"?**  
The spec uses `open_notional_gbp` from the live paper book (conviction-sized per
spec 07, log scale, £5k cap). The strategy tracker uses a flat £10k-per-position
measure. Which figure should the "Capital Deployed (Live)" KPI show?
Recommendation: paper-book notional (spec 07), because it is what would actually be
deployed if following the signal output.

**Q4 — Small / large cap threshold.**  
B-149 uses £500m as the small/large cap boundary (consistent with `classify_small_cap.py`
migration 011 comment: "small_cap = 1 if market_cap_gbp < £300m" — note the migration
comment says £300m but the B-149 spec says £500m). Please confirm which threshold is
canonical. This spec uses £500m to match B-149 and Sprint 56.

**Q5 — Tickers with `small_cap = NULL`.**  
Positions where the ticker is unclassified (`small_cap IS NULL`) will not appear in
either the Small or Large panel — they will only appear in the All panel. This may
make `small + large < all`. Is that acceptable, or should unclassified tickers be
assigned to one of the bands (e.g. Large by default)?

---

## 11. Dependencies

| Dependency | Status |
|---|---|
| B-149 (Sprint 56) — `small_cap` flag in `tickers_meta` + `signals_small.json` / `signals_large.json` infrastructure | Must be deployed before B-152 |
| `build_paper_book_summary()` — existing function in `export_dashboard_json.py` | Already exists; needs `small_cap` param added |
| `market_cap_gbp` on each position row | Already populated by B-146 |
| `_brewing_sparkline_svg()` in `render_index.py` | Already exists; reusable as-is |
