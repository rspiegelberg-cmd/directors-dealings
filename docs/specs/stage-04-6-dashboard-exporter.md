# Stage 4.6 — Dashboard JSON exporter

**Status:** Draft 2026-05-13. Gates Stage 5 dashboard implementation.
**Owner:** Rupert. Author: PM/back-end planning pass.
**Source of contracts:** `docs/specs/stage-05-build-spec.md` (sections "Data contracts").

## Purpose

Stage 4 produces `_backtest_results.csv` (one row per signal-firing × horizon, with realised CARs) plus the live `signals` / `transactions` / `prices` tables in SQLite. The dashboard cannot reasonably parse CSV at page-load. This exporter aggregates those raw sources into two JSON files the dashboard reads via `fetch()`:

- `dashboard/data/signals.json` — per-signal aggregates by horizon, active clusters, paper P&L, cohort cuts.
- `dashboard/data/dealings.json` — today + this-week transaction feed with joined signals + live MTM.

One Python script, stdlib + sqlite3 only, run after every refresh.

## Why a separate stage and not part of Stage 5

Stage 5 is a static HTML dashboard. The transformation from SQL/CSV → JSON is data-pipeline work, not UI work — wrong-shape for the designer agent and wrong-shape for an HTML implementer. Keeping it as Stage 4.6 makes the contract explicit between data and presentation.

## Architecture

- Single script: `.scripts/export_dashboard_json.py`.
- Reads from `.data/directors.db` and `.scripts/_backtest_results.csv`.
- Writes `dashboard/data/signals.json` and `dashboard/data/dealings.json` atomically (write to `.tmp` then `os.rename`).
- Idempotent: same input → byte-identical output (except `generated_at` timestamp).
- Stdlib only: `sqlite3`, `json`, `csv`, `statistics`, `datetime`, `pathlib`, `os`.

## Wiring

Add to `update.py` after `eval_signals.py` and before page regeneration:

```
1. refresh.py           # scrape new RNS, upsert transactions
2. eval_signals.py      # evaluate signals on new transactions
3. backtest.py          # produce/update _backtest_results.csv
4. export_dashboard_json.py   # <-- this stage
5. gen_company_pages.py # per-ticker pages (Stage 5.1)
```

A `--dry-run` flag prints summary stats without writing files.

## Field-by-field derivation

### signals.json

**generated_at:** `datetime.now(timezone.utc).isoformat()` at the moment of write.

**horizon_aggregates[h]** for h in {t1, t21, t90, t252}:

- **base_rate** — pre-computed once, % of rolling h-trading-day windows in `prices` for ticker `^FTAS` that were positive. Cached in `.scripts/_base_rates.json` and refreshed weekly.
- **signals[s]** for s in {t0, t1, t2, t3, t4, s1, f1}:
  - **trades:** count rows in `_backtest_results.csv` where signal_id = s AND signal_version = current AND fired_at within last 365d AND fired_at + h trading days ≤ today.
  - **hit_pct:** `100 * sum(CAR_h > 0) / trades` over that set.
  - **median_car:** `statistics.median(CAR_h)` over that set, rounded to 1dp.
  - **mean_car:** `statistics.mean(CAR_h)`, rounded to 1dp.
  - **edge:** `median_car - benchmark_median_car_h` where benchmark median is precomputed median of all rolling h-day FTSE returns over the same trailing 12 months.
  - **sparkline:** 9 floats. Bucket the matured trades into 9 weekly buckets (weeks t-12 to t-4). Each bucket = rolling 4-week median CAR_h. Forward-fill if a bucket is empty.
  - **status:** auto-computed per the design rule:
    - "gated" — f1 if Stage 4.5 hasn't shipped (check a meta flag in `meta` table); else proceed
    - "live" — median_car ≥ 0 vs base_rate AND was ≥ 0 in the prior 4-week window too
    - "review" — median_car < 0 in the current window only
    - "kill?" — median_car < 0 in both the current and prior 4-week window
  - **outlier_flag:** true if any |CAR_h| > 200% in the matured set (signals the Stage 4.5 fix is still needed).

**active_clusters:** read from `.scripts/clusters.json` (existing). Filter to clusters where `last_buy_date ≥ today - 90`. For each, emit `{ ticker, company, director_count, aggregate_value_gbp, first_buy_date, last_buy_date, s1_active: true }`. Append "brewing" clusters: 2+ directors but most recent buy is between 30 and 90 days back — `s1_active: false`. (Pending Rupert confirm; see Stage 5 build spec open question.)

**paper_pnl_open / paper_trades_open / paper_trades_closed:**
- Read from `paper_trades` table (Stage 4 / spec 07). 
- `paper_pnl_open` = sum of MTM P&L for trades where status = open.
- `paper_trades_open` = count rows where status = open.
- `paper_trades_closed` = count rows where status = closed.

**cohorts** (new key, used by Stage 5's cohort blocks):
- **by_value_bucket:** for buckets [(1000,25000), (25000,100000), (100000,500000), (500000, inf)], compute median CAR_t21 across all T1+T2 firings whose `value_gbp` falls in the bucket. Emit `{ bucket_label: median_car }`.
- **by_sector:** for each sector with ≥10 firings in the last 90 days, compute hit_pct at T+21 vs base_rate. Emit sorted desc, top 5.

### dealings.json

**generated_at, as_of_date:** UTC now + today's date.

**signals_today_count:** count of distinct transaction fingerprints where `date(announced_at) == today` AND has at least one signal firing.

**signals_today_delta_vs_avg:** `today_count - mean(daily_counts_last_7_days)`, rounded to int.

**today:** transactions where `date(announced_at) == today`, sorted by max signal severity asc (T0 strongest = 0). For each:
- Join `signals` to get `signals_fired` array.
- `mtm_pct` = `(latest_close / entry_close - 1) * 100 - cost_pct` where `entry_close` = close on T+1 trading day after `announced_at`. Returns `null` if T+1 hasn't happened yet OR the ticker has no recent price.
- `cost_pct` = 0.5 (round-trip spread) + (0.5 if non-AIM else 0).

**this_week:** transactions where `announced_at` in (today-7, today-1] AND has at least one signal firing. Same join, same MTM rule.

## Performance

- Target: full run in under 5 seconds on Rupert's laptop with ~50k transactions and ~500 tickers.
- All CSV reads done once into memory dicts.
- Indexes on `signals(fired_at, signal_id)` and `transactions(announced_at)` recommended.
- No remote calls. Pure local SQLite + CSV reads.

## Atomicity and idempotency

- Write to `{path}.tmp` then `os.replace` for atomic swap.
- Identical inputs (same DB, same CSV, same date) must produce byte-identical output except `generated_at`. Add a `--no-timestamp` flag for round-trip testing.

## Tests

`.scripts/test_p4_6_exporter.py`:

1. Synthetic SQLite + CSV fixture with known cases per signal × per horizon → assert exact computed values.
2. Idempotency: run twice with `--no-timestamp` → diff is empty.
3. Empty-DB case → exporter produces valid JSON with zero counts and no crashes.
4. Outlier case: synthetic +500% CAR → assert outlier_flag = true on the corresponding signal.
5. Schema validation: output JSON validates against an inline JSON Schema.

## Out of scope

- Sell-signal aggregation (spec 05 excludes sells from v1).
- Per-director cohort cuts (out of v1 per Stage 5).
- Conviction-weighted sizing aggregates — Stage 5.1's v1.1 work; exporter gains a `--sizing weighted` flag then.
- Streaming / incremental updates — full re-export every run is fine at this scale.
- Schema migrations — output JSON Schema versioned in the file as `schema_version: "1.0"`. Bump on any breaking change.

## Acceptance criteria

- All Stage 5 fields in the data contract are populated for every horizon × signal combination.
- Run completes in < 5 seconds on Rupert's machine.
- Test suite green.
- Exporter wired into `update.py` and runs after `backtest.py`.
- Dashboard fed with this output renders with zero JS console errors.

## Gates before implementation

1. Stage 4 ships — `_backtest_results.csv` exists with the agreed columns.
2. Stage 1 dual-write flipped — SQLite is canonical and `paper_trades` table populated.
3. Confirm "brewing" cluster scoping with Rupert before that field goes live in `active_clusters`.

## Risks

- The status auto-computation rule uses a 4-week window comparison. Edge cases: signal with only one matured firing in trailing 12 months will be unstable. Mitigation: if matured count < 5 in current window, status = "review" regardless. Add to implementation.
- Base-rate caching may go stale. The weekly refresh is a `cron` candidate but for v1 it's a check-and-recompute-if-older-than-7-days call inside the exporter itself.
- F1 outlier_flag = true should be the trigger to keep status = "gated". Wire that explicitly so we don't accidentally promote F1 to "live" before Stage 4.5 lands.
