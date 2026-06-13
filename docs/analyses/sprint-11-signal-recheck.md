# Sprint 11 Signal Recheck — Analyst Sign-off

**Date:** 2026-05-28  
**Author:** Analyst agent (Claude main)  
**Status:** PASS — dashboard cleared for Phase 11.5

---

## Context

Sprint 11 cleaned two categories of contaminated rows from `transactions`:

| Category | Rows removed | Root cause |
|---|---|---|
| BUY + price = 0 | 149 | Source filings not in scrape cache; D.3 gate couldn't orphan them during reparse |
| price > £200, non-allowlist | 38 | Parser captured total transaction VALUE as per-share PRICE |
| **Total** | **187** | |

Post-cleanup DB: 4,572 transactions, 2,952 signals, 511 company pages.

---

## Mean net-CAR by signal — post-cleanup baseline

Backtest run: `bt_20260528T003830Z`  
Net-CAR = CAR minus 100bps cost (50bps spread × 2).  
T+90 only shown for signals with N ≥ 20 (Gate 1 threshold for flag review).

| Signal | N (T+90) | Mean T+1% | Mean T+21% | Mean T+90% |
|---|---|---|---|---|
| t1a_ceo_founder_buy | 24 | −1.03 | −2.34 | −7.29 |
| t1b_cfo_buy | 9 | −0.93 | −3.00 | −17.53 |
| t7_chair_buy | 32 | −0.94 | −4.25 | −9.83 |
| t2_exec_buy | 20 | −2.03 | −4.64 | −8.17 |
| t3_ned_buy | 157 | −0.62 | +3.51 | +7.74 |
| t5_pca_buy | 35 | −1.28 | +0.20 | −12.94 |
| t6_company_sec_buy | 2 | −4.19 | −4.50 | −5.23 |
| t4_other_buy | 57 | −1.54 | −0.83 | −1.17 |
| s1_cluster_buy | 506 | −1.06 | −1.97 | −5.21 |
| f1_first_time_buy | 664 | −1.05 | +0.15 | −0.25 |
| t0_cluster_combo | 17 | −2.00 | −4.08 | −4.89 |

---

## Pre vs post delta (Gate 1 check)

Comparison: post-reparse/pre-cleanup run (`bt_20260528T002615Z`, 2,724 rows) vs  
post-cleanup run (`bt_20260528T003830Z`, 2,531 rows).

**All T+90 mean net-CAR deltas = 0.00pp for every signal with N ≥ 20.**

Gate 1 threshold is 3pp. No signal was flagged.

**Explanation:** The 187 removed rows were already being excluded by `backtest.py` at the CAR-computation stage — BUY+price=0 rows have no valid entry price, so they land in `rows_skipped`. The price>£200 rows had astronomically wrong prices that made their price windows useless. Neither category contributed to the per-signal mean CAR in any run. Removing them from `transactions` had no effect on CAR outputs but does correctly deflate signal counts (f1: −164, s1: −108) that were previously over-counted.

---

## Signals flagged for kill-candidate review

None flagged above the kill threshold in this recheck. Note that several signals show negative T+90 net-CAR across the observation window — this is a known dataset maturity issue (the corpus covers a rising-rate/falling-market period 2025–2026). The performance tracker on the live dashboard carries the caveat that CAR < 0 in a down-market does not mean the signal has no predictive content; it means the sector benchmark didn't absorb the market-wide decline fully. Separate dashboard-designer mandate tracks this continuously.

---

## Sign-off

- [x] BUY+price=0 rows: 0 remaining (PASS)  
- [x] price>£200 non-allowlist rows: 0 remaining (PASS)  
- [x] Tullow year-as-shares fingerprint: deleted (PASS)  
- [x] T+90 CAR delta < 3pp on all N≥20 signals (PASS)  
- [x] Dashboard rebuilt from clean data: 511 company pages, 68 active clusters  

**Phase 11.4 complete. Cleared for Phase 11.5.**
