# CAR, Edge, MTM and Benchmark — what the numbers actually mean

_Reference card for Rupert. Written 2026-06-05 against the live codebase._

---

## 1. CAR — Cumulative Abnormal Return

### What it is supposed to be

CAR answers the question: **did the stock outperform the market after the director bought?**

It is not the raw stock price move. It is the stock's return _minus_ what the market (the benchmark) returned over the same period. If the stock went up 8% and the market went up 5%, the CAR is +3%. If the stock went up 3% and the market also went up 5%, the CAR is -2% — the director's call underperformed.

### What the code actually computes (`backtest.py`, function `run_backtest`)

1. **Starting point ("entry date"):** the first trading day _strictly after_ `announced_at` — i.e. the day after the RNS announcement was published. The entry price is the closing price on that day (`entry_close`). This is correct and matches the spec.

   - If `announced_at` is missing for a row, the code falls back to the transaction `date` (the day the director actually traded). This introduces a small lookahead bias of 1–7 days on those rows because the market might not have known about the trade yet on that date. This is a known, accepted compromise documented in a code comment at line 178 of `backtest.py`.

2. **Forward windows:** T+1, T+21, T+90, T+252 trading days after the entry date. These are counted in actual trading days (market open days), not calendar days.

3. **Stock return at each window:** `(window_close / entry_close) - 1`

4. **Benchmark return at each window:** same formula, applied to the benchmark index starting on the same entry date.

5. **CAR = stock return minus benchmark return** at each window.

6. **Split guard (B-095):** if the price at any window is more than 4x or less than 0.25x the entry price, the CAR for that window is set to null (blanked out). This prevents a stock split or consolidation that Yahoo Finance has not adjusted for from producing a fake +1,000% or -90% result. Those rows are logged to `_split_guard_flagged.csv`.

7. **Minimum history requirement:** if a ticker has fewer than 30 trading days of price history before the announcement date, the entire firing is skipped (not included in any results). Logged to `_backtest_skips.json`.

### Horizons

| Label | What it means |
|-------|--------------|
| T+1   | 1 trading day after entry — essentially "the next day" |
| T+21  | ~1 calendar month (21 trading days) |
| T+90  | ~4.5 calendar months (one quarter) |
| T+252 | ~1 full year |

### Known issue

CAR figures for rows where `announced_at` was blank are calculated from the transaction date rather than the announcement date. This makes some historical CARs slightly optimistic (the entry price is before the market knew about the trade). The affected rows are a known scraper gap — the fix is `backfill_announced_at.py`, which Rupert runs to patch this upstream.

---

## 2. Edge

### What it is supposed to be

Edge is meant to show whether a given signal tier (e.g. CEO buys, CFO buys) is doing _better than chance_. If the whole market delivers a +3% median return over a given window, and CEO buys are delivering +5%, the edge is +2%. It is the signal's added value above what any passive investor would have got.

### What the code actually computes (`export_dashboard_json.py`, function `aggregate_signals`, line ~373)

Edge is computed as:

```
edge = signal's median CAR (%) - benchmark's median return (%)
```

Both figures come from the same backtest CSV:

- **Signal's median CAR** is the median of the `car_t*` column across all firings for that signal within the trailing 12 months, expressed as a percentage.
- **Benchmark median** is computed from the `benchmark_return_t*` column across _all_ rows in the same CSV (not just this signal's rows). This is the median of what the sector benchmark returned over all the measured windows in the dataset.

The result is rounded to 1 decimal place (e.g. `+2.3` means the signal beat the benchmark by 2.3 percentage points at the median).

### Important nuance

Edge uses the **gross CAR** (before costs), not the net-of-cost CAR. The cost deduction is applied separately to the `net_car_*` columns. So an edge of +2% does not mean you pocket +2% after trading costs — you would need to subtract 0.5% (AIM) or 1.0% (main market) from that figure to get the true net edge.

---

## 3. MTM — Mark-to-Market

### What it is supposed to be

MTM shows how much money you would be up or down _right now_ if you had bought the stock on the day the announcement was made, and still held it today. It is a live unrealised return, expressed as a percentage.

### What the code actually computes (`export_dashboard_json.py`, function `_mtm_pct`, line ~1538)

1. **Entry point:** the first trading day close _strictly after_ `announced_at`. This is the same T+1 entry logic used for CAR.

   - The function uses `_to_iso_day()` to normalise `announced_at`. If `announced_at` is blank, it falls back to the transaction `date`.

2. **Current price:** the most recent close in the `prices` table for that ticker (latest date available — whatever was last fetched from Yahoo Finance).

3. **Gross return:** `(current_close / entry_close - 1) × 100`

4. **Cost deduction:**
   - AIM stocks: subtract 0.5% (spread only)
   - Non-AIM stocks: subtract 1.0% (0.5% spread + 0.5% stamp duty)

5. **MTM result** = gross return minus cost deduction, rounded to 2 decimal places.

There is also a companion column, `abs_return_pct` (B-098), which is the gross return with **no cost deduction**, so you can see the raw stock move.

### Known bug — now fixed (2026-06-03)

The scraper sometimes stores `announced_at` as a human-readable date like `"02 Jun 2026"` instead of the standard machine format `"2026-06-02"`. Before the fix, the code did a blind 10-character slice of this string, producing the garbage value `"02 Jun 20"`. Because that garbage string sorts _before_ every real date in the price history, the bisect search would find the very first price on record (roughly one year old) and use that as the entry price. This made MTM wildly wrong — UTG was showing -41%, CLX was showing +40%, when the true values were close to zero. The fix (added `_to_iso_day()` normaliser) handles both the ISO and the human-date formats correctly. The 7 rows affected at the time of the fix (all 01–03 June 2026 announcements) required a re-run of `export_dashboard_json.py` + `build_dashboard.py` to update.

**Still open:** the scraper still writes non-ISO `announced_at` values for new filings. Every new non-ISO row will be handled correctly by the exporter now, but the signals `b1_lone_conviction_buy` and `b2_crowded_cluster_kill` still have their own `strptime` calls that would silently skip non-ISO rows when evaluating. This is the "Phase 4 scraper fix" on the open backlog.

### What MTM does NOT do

MTM is not the same as CAR. It does not subtract benchmark performance. It is the raw stock move net of your entry costs. A stock that is up 3% when the market is up 5% will show MTM +2% (non-AIM) but a negative CAR.

---

## 4. Sector Benchmark

### What it is supposed to be

Each stock should be measured against the index that best represents its sector — so an energy stock is compared to an energy benchmark, not the whole market. This avoids flattering a stock that merely went up because its whole sector went up.

### What the code actually computes

**Per-ticker benchmark assignment** (`backfill_ticker_meta.py` + `benchmark_symbols.json`):

- AIM-listed stocks: always use `^AIM` (FTSE AIM All-Share index).
- All other stocks: the `tickers_meta` table stores a `benchmark_symbol` per ticker. This is populated by `backfill_ticker_meta.py` and cross-referenced against `benchmark_symbols.json`.
- If a ticker has no entry in `tickers_meta`, or its `benchmark_symbol` is null: fall back to `^FTAS` (FTSE All-Share).

**In practice, almost every non-AIM stock uses `^FTAS`.** The `benchmark_symbols.json` file shows that all 11 FTSE sector sub-indices (e.g. `^FTNMX5710` for Financials) were probed in May 2026 and returned HTTP 404 from Yahoo Finance — they are not available. So the map explicitly sets `^FTAS` for every sector. FTSE 100 (`^FTSE`) and FTSE 250 (`^FTMC`) differentiation by market cap was deferred pending `market_cap_gbp` data being populated.

**Implication:** the benchmark is effectively the FTSE All-Share for almost all stocks (except AIM stocks which use `^AIM`). This means CAR is measuring outperformance vs the broad UK market, not sector-specific outperformance. This is a known limitation documented in the codebase.

**Date range used for the benchmark:** the benchmark return is measured over exactly the same date window as the stock — entry date to the T+1/T+21/T+90/T+252 close. The benchmark index's closing prices are fetched and stored in the same `prices` table as the stocks. The code uses a `bisect_left` search to find the benchmark entry price on the same date as the stock entry, then counts forward the same number of trading days (`b_idx + n`).

---

## 5. Net of Costs

### What it is supposed to be

Trading a UK stock costs money even if the price does not move. There is a bid-ask spread (you buy slightly above the market mid-price) and a government stamp duty on purchases of non-AIM stocks. These costs should be subtracted from measured returns so that the signal performance figures represent what a real investor would actually receive.

### What the code actually computes

**Two separate places apply cost deductions — they use the same logic but are independent.**

#### In the backtest (`backtest.py`, lines 362–366)

Applied once at the end of each firing's calculation:

```
AIM stock:      cost = 50 bps  (0.50%)   — spread only
Non-AIM stock:  cost = 100 bps (1.00%)   — spread + stamp duty
```

`net_car = car - cost_fraction`

This produces the `net_car_t1`, `net_car_t21`, `net_car_t90`, `net_car_t252` columns in `_backtest_results.csv`. Costs are subtracted **once** regardless of the holding period — so the 50bps or 100bps comes off T+1, T+21, T+90, and T+252 equally. There is no additional holding-cost model (no financing cost over time).

#### In the MTM column (`export_dashboard_json.py`, `_mtm_pct`, line 1563)

```python
cost_pct = 0.5 + (0.0 if is_aim else 0.5)
```

- AIM: 0.5%
- Non-AIM: 1.0%

Same logic, same numbers, applied to the live MTM figure.

#### Stamp duty clarification

Stamp duty in the UK is 0.5% on purchases of shares listed on the main market (LSE). AIM stocks are exempt. The code models this correctly: non-AIM stocks pay 50bps spread + 50bps stamp = 100bps total; AIM stocks pay 50bps spread only.

#### What is NOT modelled

- Exit spread (the cost to sell). The model charges only entry costs.
- Financing costs for holding positions over time.
- Broker commissions.

The spec explicitly locks this as a one-shot entry cost model ("D-COSTS-MODEL, decision #4 + #12" in `backtest.py` line 10).

---

## Summary table

| Metric | Entry date | Benchmark subtracted? | Costs deducted? | "To today" or fixed window? |
|--------|-----------|----------------------|----------------|-----------------------------|
| CAR (gross) | T+1 after `announced_at` | Yes | No | Fixed (T+1, T+21, T+90, T+252) |
| CAR (net) | T+1 after `announced_at` | Yes | Yes (once at entry) | Fixed (T+1, T+21, T+90, T+252) |
| Edge | (derived from CAR) | Yes — that is what it measures | No (uses gross CAR) | Fixed (trailing 12mo firings) |
| MTM | T+1 after `announced_at` | No | Yes (once at entry) | Open — vs latest price today |
| Stock Rtn | T+1 after `announced_at` | No | No | Open — vs latest price today |
