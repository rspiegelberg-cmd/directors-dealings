# Role normalization pass

**Status:** Phase A code complete; Phase B code complete; awaiting Rupert's backfill + eval_signals re-run.
**Owner:** Rupert
**Trigger:** DB inspection on 2026-05-20 showed 627 distinct strings in the `transactions.role` column across 2,108 rows. Same role appears under 4+ case variants; ~3% of values are PDF parser fragments like "Nature of the transaction".
**Gate for Phase A:** Cleared 2026-05-20 (Rupert approved 14-bucket taxonomy + Phase A/B split).
**Gate for Phase B:** Cleared 2026-05-20 (Rupert approved 8-tier scheme + per-bucket signal firings after seeing the diff).

---

## Why this exists

The `role` field is free-text scraped from the RNS PDMR notification PDF. Three problems:

1. **Case/spacing splits the same role across many buckets.** "Non-Executive Director" (196), "Non-executive Director" (27), "Non-executive director" (15), "NON-EXECUTIVE DIRECTOR" (2) — same role, four buckets.
2. **Parser bleed.** ~3% of rows have role values that are clearly PDF table fragments, not titles: `"Nature of the transaction"`, `"Number of shares acquired"`, `"| Partnership Shares | Matching..."`.
3. **No semantic grouping.** A CEO under "Chief Executive Officer", "CEO", "Chief Executive", "Group CEO", "AW - Chief Executive Officer/PDMR", and "1) Chief Executive" can't be queried as one cohort without a mapping layer.

Rupert wants to fire signals on **combos of director type** (CEO+CFO same-day buy, Chair+NED cluster, Founder solo buy). Today's `role` field can't support that without a normalization layer.

## The 14-bucket canonical taxonomy

| # | Bucket | Rows | What goes here |
|---|---|---:|---|
| 1 | **CEO** | 378 | "Chief Executive Officer", "CEO", "Group CEO", "Chief Executive", "Chief Executive Officer/Director", "CEO Designate". Excludes regional/divisional CEOs (→ bucket 8). |
| 2 | **CFO** | 228 | "Chief Financial Officer", "CFO", "Finance Director", "Group CFO", "Group Finance Director", "Financial Director". |
| 3 | **Other Chief** | 138 | All other C-suite: COO, CTO, CMO, CRO, CHRO, CCO, CSO, Chief Investment Officer, Chief People Officer, Chief Risk Officer, etc. |
| 4 | **Chair (executive)** | 129 | "Chair", "Chairman", "Executive Chair", "Executive Chairman", "Board Chair", "Chair of the Board" — when no "Non-Executive" qualifier. |
| 5 | **Non-Exec Chair** | 86 | "Non-Executive Chair", "Non-Executive Chairman", "Independent Non-Executive Chair", "Non-executive Director (Chair of the Board)". |
| 6 | **NED** | ~496 | "Non-Executive Director" (all case variants), "Senior Independent Director", "Independent Non-Executive Director", "Supervisory Board Member", **bare "Director" (UK convention)**. |
| 7 | **Executive Director** | 54 | "Executive Director", "Managing Director", "Business Development Director", "Commercial Director" — execs without a Chief title. |
| 8 | **Divisional / Regional Exec** | ~124 | Titles with explicit geography or business unit: "Chief Executive Officer, North America", "CEO, Savills UK & EMEA", "Managing Director: Recruitment Ireland", **"Regional Director"**, "President, Higher Education", "CEO, Insurance, Pensions & Investments". |
| 9 | **Founder** | 64 | Anything containing "Founder" — "President and Founder", "Founder & President", "Co-Founder". |
| 10 | **President / VP** | 15 | US-style senior exec titles: "Vice President", "SVP", "EVP", "Senior Executive Vice-President", "Division President". |
| 11 | **Company Secretary / General Counsel** | 29 | "Company Secretary", "General Counsel", "Group Legal Director", "Group General Counsel". |
| 12 | **PCA (Person Closely Associated)** | 139 | "PCA of/to/with X", "Person Closely Associated with X", "Spouse of", "Wife of", "Daughter of", "Family trust", "Connected person to X". |
| 13 | **PDMR-only** | 93 | Raw value is "PDMR" alone, or "Director/PDMR", "PDMR of the Company" — no other title disclosed. |
| 14 | **Other / unclassified** | ~78 | Genuinely unclassifiable: "Fund Manager", "Global Head of Strategic Partnerships", "Executive Committee Member", "(blank)". |

Plus a **data-quality flag** (not a signal bucket): **Parser fragment** (~65 rows) — strings like "Nature of the transaction", table headers, sentence fragments. These are surfaced in the data-quality report for Sprint 3 reparse, not used by any signal.

**Final refinements applied (2026-05-20):**
- "Regional Director" (8 rows) maps to bucket 8 (Divisional / Regional Exec), not Other.
- Bare "Director" (90 rows) maps to bucket 6 (NED) — UK convention.

## In scope

### Phase A — additive, safe to ship today

1. **Add `role_normalized` column** to `transactions` (TEXT, nullable). Raw `role` stays untouched.
2. **Build `normalize_role(raw: str) -> str`** in `.scripts/role_normalize.py`. Deterministic. Pure function. Most-specific keyword first.
3. **Schema migration** `004_add_role_normalized.sql` + step-3→4 block in `db.py:_apply_schema_migrations`.
4. **Backfill script** `backfill_role_normalized.py` — Rupert runs from PowerShell (Zone B). Takes a fresh `.bak` snapshot first, runs single transaction, runs `PRAGMA integrity_check` before and after, supports `--dry-run`, idempotent.
5. **Wire into ingest path** — `db.py:upsert_transaction` populates `role_normalized` alongside `role` so the column never drifts.
6. **Unit tests** `test_role_normalize.py` — ~30 raw strings drawn from the live corpus covering case variants, precedence (PCA > Founder > Divisional > Chair > NED > CEO), parser fragments, blanks, multi-role strings.
7. **Integration tests** `test_role_backfill.py` — tempfile DB, mid-run crash simulation, idempotency check.
8. **Update cosmetic chip code** in `render_helpers.py` (`role_chip`), `render_company.py` (exec/NED colour logic), `dashboard/index.html` (`roleChipCls`). Replace substring matching with dict lookup on `role_normalized`. Fall back to raw `role` substring when `role_normalized` is missing (e.g. for old cached JSON during rollout).
9. **Update JSON export** — `export_dashboard_json.py` emits `role_normalized` alongside raw `role`. Bump JSON schema_version.
10. **Update backtest CSV** — `backtest.py` adds `role_normalized` column. Keep `role` and `role_class` (audit trail).

### Phase B — completed 2026-05-20

11. **Diff report.** Generated — 633 firings move tier under the new 8-tier scheme. £17.98m of historical BUY value previously misclassified as T1/T2/T3 was actually PCA activity. Rupert signed off after seeing the diff.
12. **Cut over signal logic.** `.scripts/signals/roles.py` now returns 8 tier strings (T1a, T1b, T2, T3, T4, T5, T6, T7) via dict lookup on the canonical bucket. `.scripts/classify_role.py` updated similarly for the Performance page tiles.
13. **Signal modules split per bucket** (Rupert's request 2026-05-20):
    - `t1_ceo_cfo_buy` REMOVED. Replaced by:
    - `t1a_ceo_founder_buy` (T1a, £100k threshold, high confidence)
    - `t1b_cfo_buy` (T1b, £100k threshold, high confidence)
    - `t5_pca_buy` (T5, £10k threshold, low confidence) — NEW cohort
    - `t6_company_sec_buy` (T6, £10k threshold, low confidence) — NEW cohort
    - `t7_chair_buy` (T7, £25k threshold, med confidence) — NEW, combines exec + non-exec chair
    - `t2_exec_buy`, `t3_ned_buy`, `t4_other_buy` UNCHANGED (modules still fire on their respective tiers; cohorts are cleaner now)
14. **Signal registry + dispatcher updated** — `.scripts/signals/__init__.py` and `.scripts/eval_signals.py`.

## The final 8-tier signal scheme

| Tier | Buckets | Signal | Threshold | Confidence |
|---|---|---|---|---|
| T1a | CEO + Founder | `t1a_ceo_founder_buy` | £100k | high |
| T1b | CFO | `t1b_cfo_buy` | £100k | high |
| T2 | Other Chief + Exec Director + Divisional/Regional Exec + President/VP | `t2_exec_buy` | £25k | med |
| T3 | NED | `t3_ned_buy` | £10k | med |
| T4 | PDMR-only + Other + Parser fragment | `t4_other_buy` | £1k | low |
| T5 | PCA (Person Closely Associated) | `t5_pca_buy` | £10k | low |
| T6 | Company Secretary + General Counsel | `t6_company_sec_buy` | £10k | low |
| T7 | Chair (executive + non-executive) | `t7_chair_buy` | £25k | med |

Tier-rank for orchestrator dedup (lower = higher priority):
T1a (1) > T1b (2) > T7 (3) > T2 (4) > T3 (5) > T5 (6) > T6 (7) > T4 (8).

## Out of scope

- Fixing the underlying PDF parser (Sprint 3 reparse).
- Director-name normalization (separate spec).
- Inferring role from director name when blank.
- Visualising "Parser fragment" rows in the dashboard data-quality panel (Sprint 4).

## Effort estimate

Phase A: ~4 hours (10 file changes + 2 new test files + 1 backfill script).
Phase B: ~1–2 hours (diff report + 2 file edits).

## Acceptance criteria — Phase A

- `transactions.role_normalized` exists, populated for all 2,108 current rows.
- Every value in `role_normalized` is one of the 14 canonical buckets or "Parser fragment".
- Unit test covers all precedence rules and the case-variant edge cases.
- Integration test proves backfill is atomic + idempotent.
- Bucket-distribution sanity: CEO ≥ 350, NED ≥ 450, CFO ≥ 200, Parser fragment ≤ 5%.
- `Other / unclassified` ≤ 5% of rows.
- All existing tests in `.scripts/test_*.py` still pass.
- The dashboard renders identically (cosmetic chip colours unchanged for the dominant cases).
- `directors.db.bak-pre-role-normalize-YYYYMMDD` exists before the backfill commits.

## Acceptance criteria — Phase B

- Diff CSV of T1–T4 firing counts pre/post is generated and inspected.
- Rupert signs off on the bucket shifts in writing (note in the spec, not just verbal).
- After cut-over, `signals/roles.py` and `classify_role.py` read only from `role_normalized`.
- Historical backtest CSV is re-run; new performance numbers replace old in the dashboard.

## Why two phases

If Phase A and B ship together, your historical signal counts shift the same session — a 5-string case variant of "Non-Executive Director" suddenly counts correctly, T3 firings go up, and the per-signal performance grid on the dashboard changes. That's the *correct* change, but you should see the delta before it lands. Phase A makes the column available without changing any firing logic; Phase B is the deliberate cut-over with eyes wide open.
