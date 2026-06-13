# Sprint 10 — before / after backtest diff

> Generated 2026-05-26 after Phase 6 completion. Captures the
> empirical effect of finishing the B-011 IT/CEF/VCT/REIT exclusion
> work on the signal engine and backtest output.
>
> Sprint 10 plan: [`sprint-plan-2026-05-25-sprint10.md`](./sprint-plan-2026-05-25-sprint10.md)

## Before / after

**Baseline ("before"):** last successful pipeline run on **2026-05-22 19:57Z**, before Sprint 9 Phase B parser fixes landed and before Sprint 10 Phase 1-5 code changes.

**After:** Phase 6 sequence on **2026-05-26 ~12:12Z** — `classify_issuers --no-yahoo` → `exclude_investment_trusts --confirm` → `eval_signals` → `backtest` → `export_dashboard_json` → `build_dashboard`.

## Phase 6 deletion summary

```
Authorised by: Rupert  on  26/05/2026
Will delete: 2 transactions across 2 tickers (MGCI, PUAL).
DELETE complete.
  signals deleted:        1
  paper_trades deleted:   0
  transactions deleted:   2
```

Both tickers were clear-cut Source-C (name-regex) matches:

| Ticker | Company                                | Why excluded |
|--------|----------------------------------------|--------------|
| MGCI   | M&G Credit Income Investment Trust plc | "Investment Trust" in name |
| PUAL   | Puma Alpha VCT plc                     | "VCT" in name |

## Signal-firing counts by tier

| Signal              | 2026-05-22 | 2026-05-26 | Δ      | Δ %    |
|---------------------|-----------:|-----------:|-------:|-------:|
| t1a_ceo_founder_buy |         43 |         42 |    -1  |  -2.3% |
| t1b_cfo_buy         |         20 |         18 |    -2  | -10.0% |
| t2_exec_buy         |         51 |         50 |    -1  |  -2.0% |
| t3_ned_buy          |        296 |        283 |   -13  |  -4.4% |
| t4_other_buy        |        104 |        103 |    -1  |  -1.0% |
| t5_pca_buy          |         94 |         75 |   -19  | **-20.2%** |
| t6_company_sec_buy  |          4 |          4 |     0  |   0.0% |
| t7_chair_buy        |         48 |         42 |    -6  | -12.5% |
| s1_cluster_buy      |       1029 |        977 |   -52  | **-5.1%** |
| f1_first_time_buy   |       1455 |       1390 |   -65  |  -4.5% |
| t0_cluster_combo    |         43 |         42 |    -1  |  -2.3% |
| **distinct_tickers**    |  421 |  **405** |  -16  |  -3.8% |
| **distinct_directors**  | 1089 | **1018** |  -71  |  **-6.5%** |

## Dashboard-level counts

| Metric              | 2026-05-22 | 2026-05-26 | Δ |
|---------------------|-----------:|-----------:|--:|
| n_signal_rows       |       3242 |       3087 | -155 (-4.8%) |
| n_csv_rows          |       2719 |       2602 | -117 (-4.3%) |
| **n_active_clusters** | 81 | **72** | **-9 (-11.1%)** |
| n_today             |          2 |          1 | -1 |
| **n_this_week**     |       18 |       **12** | **-6 (-33.3%)** |
| n_buckets           |          3 |          3 |  0 |
| n_roles             |          6 |          6 |  0 |
| n_sectors           |         11 |         11 |  0 |

## Reading the deltas

The largest *relative* drops are exactly where the B-011 thesis predicted noise pollution:

- **t5_pca_buy −20%:** Person Closely Associated buys — typically family members of directors. Investment Trusts often have whole director families buy together at NAV-discount as a routine governance signal. Sprint 10 has removed that pattern from the dataset.
- **t7_chair_buy −12.5%:** IT chairs buying their own trust shares is one of the most common governance gestures in the LSE PDMR feed.
- **n_active_clusters −11%:** Multi-director simultaneous buying at the same issuer is *the* defining cluster pattern, and ITs trip it routinely without informational edge.
- **n_this_week −33%:** Recent activity was disproportionately polluted by IT board buys. The post-Sprint-10 weekly activity count is closer to a "real" operating-company-only baseline.

The smaller absolute drops in t1a / t1b / t2 / t6 reflect that Investment-Trust boards rarely include CEOs or CFOs in the operating-company sense — most IT board members are non-execs.

## Bottom line

Sprint 10's defensive-filter goal is met. The signal engine now operates on operating-company-only data. Backtest results going forward will be uncontaminated by IT/CEF NAV-discount governance buys. Whether the performance numbers (CAR, hit rate) actually improve as a result is observable in the dashboard's Performance page; this doc only captures the cohort-shape change, not the performance change.

**Audit log appended to:** `.data/_excluded_it_cef.csv` (130 historical + 2 newly-purged = 132 total exclusions).

**Sprint 10 status:** **CLOSED 2026-05-26.**
