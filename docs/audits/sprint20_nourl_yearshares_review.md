# Sprint 20 — no-URL year-as-shares rows: delete vs keep

**Date:** 2026-06-03 (read-only DB query). **Context:** R6 flags any `shares` in 1990–2099. 31 such rows have **no source URL**, so a reparse cannot reach them. They must NOT be deleted blindly — many are genuine ~2,000-share holdings, not the year-bleed bug. Split below by the unambiguous signature: **does the share count equal the transaction's own year?**

## DELETE candidates — shares == own transaction year (15 rows)
High-confidence year-bleed; stored volume is known-wrong and unrecoverable without a source. Safe to delete (+ their signals; eval_signals regenerates).

| fingerprint | ticker | date | shares | type |
|---|---|---|---|---|
| 553d2d022d461461 | SCLP | 2025-07-30 | 2025 | BUY |
| fa80558bcec8605b | SFOR | 2025-09-25 | 2025 | BUY |
| 07178a5ab3323df7 | SFOR | 2025-09-25 | 2025 | BUY |
| 5af985261b6c1bd6 | RHR | 2025-12-19 | 2025 | BUY |
| 53265bd1b41914fd | TKO | 2026-01-08 | 2026 | EXERCISE |
| 9d3b44a030758477 | TKO | 2026-01-08 | 2026 | EXERCISE |
| e96986c7fc5035fc | TKO | 2026-01-12 | 2026 | EXERCISE |
| 07dac526c1c5f4db | KEN | 2026-02-02 | 2026 | BUY |
| 8f8c3564c7bf3644 | TST | 2026-02-06 | 2026 | BUY |
| e2cad68bfc299d37 | TST | 2026-03-05 | 2026 | BUY |
| 55215415d9ee02b7 | ESNT | 2026-03-17 | 2026 | BUY |
| 2ba6448548a53d52 | ESNT | 2026-03-17 | 2026 | BUY |
| 74d120aec530bcf1 | LSL | 2025-07-03 | 2025 | GRANT |
| e01a20d9d93932e7 | LSL | 2025-07-03 | 2025 | GRANT |
| 01dc494a0a6dea6d | LSL | 2025-07-03 | 2025 | GRANT |

## KEEP — shares != own year, likely genuine ~2,000-share holdings (16 rows)
Do NOT delete. If ever a source appears they can be confirmed; for now they are plausible real volumes (e.g. a 2,000-share purchase) and only tripped R6's deliberately-aggressive net.

CCEP 2000 (2025-12-22), CBG 2088 (2025-05-29), SVT 2003 (2025-07-25), BARC 2074 (2025-07-30), BRK 2021 (2025-09-26), GHV1 2050 (2025-10-30), BWY 2021 (2025-11-03), BIRG 2057 (2025-11-03), MTO 1994 (2026-01-02), MKS 2044 (2026-01-09), FXPO 2073 (2026-02-27), STAN 2071 (2026-03-03), STAN 2009 (2026-03-02), STB 2029 (2026-03-30), BWY 2024 (2026-04-22), RMV 2017 (2025-11-17).

**Recommendation:** delete the 15 above (pending Rupert's OK) via a tiny fingerprint-scoped script; leave the 16. Revisit the KEEP set only if their filings become available.
