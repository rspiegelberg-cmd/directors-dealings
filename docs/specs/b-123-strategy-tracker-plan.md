# B-123 — £10k-per-signal Strategy Tracker vs FTSE All-Share — spec

**Status:** DRAFT for Rupert's approval (plan-first gate). Do not code until approved.
**Sprint:** 51 · **Linear:** DIR-51 · **Agent:** general-purpose · **Size:** L (5)
**Source decision:** product-fixes-2026-06-06-plan.md #2 (locked design).
**Date:** 2026-06-06

---

## 1. Problem

The Performance page's "Live paper book" panel shows an **"Open Paper P&L (~£57k)"** stat
that is **not a P&L** — it's `open_notional_gbp`, i.e. capital currently deployed. The only
return number on the panel is an unweighted "Mean MTM %". There is no answer to the question
Rupert actually wants: *"if I'd put a flat stake behind every buy signal, would I have beaten
the market?"*

## 2. Agreed design (locked 2026-06-06)

A new **Strategy Tracker** panel that runs one simple, legible strategy and a matched index
shadow:

| Parameter | Value |
|-----------|-------|
| Stake | flat **£10,000 per buy signal firing** (all directional buy signals) |
| Entry | close on the **first trading day after the announcement** (same entry as backtest) |
| Exit | **sell at T+21** trading days (realised). Positions younger than 21 trading days are **open** and marked-to-market daily |
| Shadow | buy **£10,000 of `^FTAS`** (FTSE All-Share) on the same entry date, same T+21 exit/accounting |
| Portfolio value | realised cash from closed trades **+** daily MTM of open trades, as a **time series** |
| Display | strategy value vs FTSE-shadow value (£ and %), on a **trailing-30-day basis** with an up/down trend |

This is a *flat-stake* strategy, deliberately distinct from the conviction-sized paper book
(spec 07 log sizing). It answers "signal edge per equal bet," not "optimally-sized book."

## 3. Resolved open question — data source

**Decision: reuse `backtest.py`'s per-firing entry/exit/benchmark conventions; do NOT use the
Phase-B `paper_trades` realised path.**

Reasoning:
- `paper_trades` is conviction-sized (variable £500–£5k notional) and exists to drive the live
  *open* book — wrong stake model and wrong purpose for a flat-£10k time series.
- `backtest.py` already computes, **per firing**: entry date = first trading day after
  `announced_at` (`_first_trading_date_after`), T+21 close, `^FTAS`-fallback benchmark entry +
  T+21 close, AIM flag, and the 50bps-spread / 0.5%-stamp cost model. That is exactly the
  spine of this strategy.
- All required raw data already exists: `prices` (daily closes incl. `^FTAS`), `transactions`
  (`announced_at`), and `_backtest_results.csv` (per-firing entry/exit closes + benchmark).

The tracker therefore **reuses backtest's entry/exit/benchmark logic** and adds a **daily
mark-to-market layer** (which backtest does not need) so the portfolio line is smooth.

## 4. Build

### 4.1 New `build_strategy_tracker(conn, today)` in `export_dashboard_json.py`

**Position set:** every **buy-signal firing** (the 11 directional buy signal_ids — reuse the
existing buy-signal id list already used by the paper book / cohort builders; single source of
truth, do not hard-code a second copy).

**Per position, derive once:**
- `entry_date`, `entry_close` (ticker) — first trading day after `announced_at`.
- `exit_date` = entry_date + 21 trading days; `exit_close`.
- `bench_entry_close`, and daily `^FTAS` closes for MTM.
- `shares = 10_000 / entry_close`; `bench_units = 10_000 / bench_entry_close`.
- Apply entry costs **consistently to both legs** (50bps spread; +0.5% stamp on non-AIM buys
  for the equity leg only — *decision point, see §6*). Default: charge costs on the equity leg,
  not the index leg (you can't pay stamp on an index); flag in the methodology note.

**Daily time series** over `[inception … today]` (inception = earliest entry_date):
for each calendar date `d` with market data, portfolio value =
- closed positions (where `d ≥ exit_date`): realised `shares × exit_close`
- open positions (`entry_date ≤ d < exit_date`): `shares × close_on(ticker, d)` (last
  available close ≤ d if d is non-trading)
- not-yet-entered (`d < entry_date`): the £10k sits as cash (so the curve starts at total
  capital deployed and isn't distorted by staggered entries) — **decision point §6**.

Same three-way logic for the `^FTAS` shadow using `bench_units`.

**Emit** `dashboard/data/strategy_tracker.json`:
```json
{
  "series": [{"date": "2025-07-02", "strategy_value_gbp": 123456.0, "ftse_value_gbp": 121000.0}, ...],
  "summary": {
    "as_of": "2026-06-06",
    "n_positions": 412,
    "capital_deployed_gbp": 4120000.0,
    "strategy_value_gbp": 4380000.0,
    "ftse_value_gbp": 4255000.0,
    "excess_gbp": 125000.0,
    "excess_pct": 2.94,
    "strategy_trend_30d_pct": 1.8,
    "ftse_trend_30d_pct": 0.9
  }
}
```
`*_trend_30d_pct` = % change in each leg's value over the trailing 30 calendar days.

### 4.2 New Performance-page panel in `render_performance.py`

- Headline stats: strategy value, FTSE-shadow value, **£ and % excess**, 30-day trend chips
  (reuse the existing `_trend_chip` style from `_monthly_buysell_chart`).
- Two-line chart (strategy vs FTSE) over the trailing window + sparkline. Chart.js is already
  loaded on the page; follow the existing chart-block pattern.
- Methodology note line: stake, entry, T+21 exit, cost treatment, "flat-stake ≠ conviction book."

### 4.3 Rename the misleading stat (small, ships with this)

In `_paper_book_section`, relabel **"Open Paper P&L" / "Open notional"** so it reads as
**capital deployed**, not profit. (Exact current label is "Open notional" at
`render_performance.py` stat strip — confirm and align wording; the misleading "P&L" phrasing
is what Rupert flagged.) No math change.

## 5. Acceptance criteria

1. Panel shows the strategy portfolio value and the FTSE-shadow value on a trailing-30-day
   basis, each with an up/down trend chip.
2. £ and % excess (strategy − FTSE) shown.
3. Both legs use the same entry date and T+21 exit; entry costs documented.
4. Methodology note present; no stat on the page is labelled "P&L" when it is capital deployed.
5. New unit tests green (see §7); full `unittest discover` sweep stays green.

## 6. Decisions — LOCKED (Rupert, 2026-06-06)

1. **Costs: CHARGED.** 50bps spread + 0.5% stamp (non-AIM buys) on the equity leg at entry;
   FTSE shadow gets spread only (no stamp on an index). Matches the cohort net-of-cost numbers.
2. **Pre-entry cash: HOLD AS CASH FROM INCEPTION.** Total pot = N signals × £10k held as cash,
   deployed as each signal fires; both legs accounted the same way so the comparison is
   like-for-like ("did the strategy grow the money").
3. **Chart: FULL HISTORY + 30-DAY TREND CHIPS.** Plot the whole inception-to-date line for both
   legs; trailing-30-day change shown as up/down trend chips above the chart.

**Related — B-125 scope (LOCKED):** keep the both-sides exclusion (corporate/PCA removed from
both buys and sells). Already live; no change.

## 7. Test plan

- `build_strategy_tracker`: tiny in-memory DB, 2 positions (1 closed past T+21, 1 open) + a
  `^FTAS` price series → assert series length, realised vs MTM values, excess_gbp/pct,
  trend_30d sign.
- Cost application: position with known entry_close → assert shares net of 50bps (+stamp when
  non-AIM).
- Edge: position with no post-announcement price (entry_close None) is skipped, not crashed.
- Renderer smoke: panel HTML contains the two series labels + excess + trend chips.

## 8. Out of scope

- Conviction sizing (that's the existing paper book).
- Other horizons (T+1/T+90) — T+21 only per the locked decision.
- Sector-matched benchmark per position — the shadow is explicitly the single FTSE All-Share
  index per the decision (the per-firing sector benchmark already drives the cohort CAR).

## 9. Deploy (Rupert, Windows — Zone B)

After approval + build:
`python .scripts\export_dashboard_json.py` → `python .scripts\build_dashboard.py`
→ `python .scripts\snapshot_db.py` (read-only) if any DB read needs refreshing.
No DB writes in this feature (read-only over existing tables + `_backtest_results.csv`).
