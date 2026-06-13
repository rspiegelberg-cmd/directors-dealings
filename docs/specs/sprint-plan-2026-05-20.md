# Sprint plan — 2026-05-20

> Backlog audit + sprint plan following the role-normalization Phase A/B
> ship and the 2026-05-20 code review. Three CRITICAL DB-safety fixes
> (C-1, C-2, C-3) have shipped. This plan groups the remaining open
> items into three sprints, each sized for ≤ 2 hours of Rupert-time.

**Companion docs:**
- [`docs/backlog.md`](../backlog.md) — the living issue list.
- [`docs/specs/code-review-2026-05-20.md`](code-review-2026-05-20.md) — full audit, source of H-/M-/L- items below.
- [`docs/specs/role-normalization-pass.md`](role-normalization-pass.md) — Phase A + Phase B complete.
- [`docs/specs/sprint-plan-2026-05-18.md`](sprint-plan-2026-05-18.md) — previous sprint plan; structural template for this doc.

**Estimate units:** Same convention as the 2026-05-18 plan.
- **Rupert-time** — wall-clock attention (running scripts, eyeballing previews, reading output, deciding at gates).
- **Gates** — count of mandatory sign-off pauses.
- **Risk** — Low / Medium / High. High = irreversible-by-default with backup mitigation.

---

## Section 1 — Backlog index (updated 2026-05-20)

### Open items

| ID | Title | Status | Notes |
|----|-------|--------|-------|
| B-001 | Multi-row bulk filings drop transactions 2..N | **DONE** 2026-05-18 | Shipped in Sprint 3 table-aware parser rewrite. |
| B-002 | 7 transactions in `_pending_review.json` (foreign + SIP variants) | **DONE** 2026-05-18 | LLM sweep recovered the recoverable rows; remainder parked behind B-015 gate. |
| B-003 | No unit tests on the parser or data pipeline | **OPEN** | Sprint 1 added two new test files; broader coverage still missing. Test-suite refresh (Phase A/B cleanup) is the next move. |
| B-004 | Director-name extraction garbles foreign / SIP layouts | **DONE** 2026-05-18 | Folded into Sprint 3 parser rewrite. |
| B-005 | `_DATE_FMTS` accepts ambiguous `%d.%m.%y` 2-digit years | **DONE** 2026-05-18 | `year < 1990` sanity check shipped in Sprint 1. |
| B-006 | `repair_dates.py` doesn't refresh pending atomically | **DONE** 2026-05-18 | Atomic pending-write shipped in Sprint 1; covered by `test_repair_dates_atomicity.py`. |
| B-007 | Add an "informational" I6 invariant for late filings | **OPEN** | Sprint 3 didn't get to it. Low priority; cosmetic. |
| B-008 | Performance tracker uses `bisect_right` on string-sorted dates | **DONE** 2026-05-18 | Defensive ISO-date assert shipped in Sprint 1. |
| B-009 | Cumulative net CAR chart says "trailing 12 months" but shows ~2 | **DONE** 2026-05-18 | Monthly buckets + null-gap forward-fill shipped in Sprint 1. |
| B-010 | Transaction tables not sorted chronologically; today's rows show time | **DONE** 2026-05-18 | Uniform `ORDER BY date DESC, announced_at DESC` + "Today" formatting shipped in Sprint 1. |
| B-011 | Exclude investment trusts, VCTs, REITs, CEFs | **DONE** 2026-05-18 | Sprint 2 purge; 130 tickers / 452 transactions / 789 signals removed. |
| B-012 | Older Stage 2/4/5 test suites surface ~4 errors under `unittest discover` | **OPEN** → superseded by **B-027** below | Broken-tests problem now larger because of Phase B's signal-tier rename. Roll into B-027. |
| B-013 | Audit DB-touching scripts for connection-leak hygiene | **PARTIAL** | `run_scrape.py` and `repair_dates.py` fixed. `backfill_filings.py` still has the leak (= H-1 below). |
| B-014 | `_pending_review.json` accumulates 4,000+ unrecoverable entries | **OPEN** | P3 housekeeping; defer. |
| B-015 | Sweep cannot safety-check rows whose pending entry lacks `announced_at` | **OPEN** | Blocks the remaining 6 stranded pending rows. P2 — can wait. |
| B-016 | Parser writes regulatory-disclosure boilerplate into `company` | **DONE** 2026-05-18 | Folded into Sprint 3 parser rewrite + boilerplate sentinel. |
| B-017 | Parser writes director name into the `company` field | **DONE** 2026-05-18 | Folded into Sprint 3 parser rewrite. |
| B-018 | Classifier needs a sustainable periodic-refresh data source | **OPEN** | P3 — punt until quarterly re-classification is actually needed. |
| B-019 | CAR chart per-series toggle + solo mode | **OPEN** | Sprint 3 didn't get to it. Low priority dashboard polish. |
| B-020 | Triage 334 orphan candidates from Sprint 3 reparse | **OPEN** | Data hygiene; ~50–80 clean-name orphans need triage. |
| B-021 | `classify_issuers.py` resets `is_excluded_issuer` flags on every run | **DONE** 2026-05-20 | Closed indirectly by C-3 (pre-classify `.bak` + integrity check). Memory note `project_classify_issuers_resets_flag.md` can be retired. |
| B-022 | Filings without a labelled issuer KV row produce empty company | **OPEN** | P3 polish; affects NET-style filings. |
| B-023 | Bundled-PDMR detection doesn't fire on AAL "PCA" pattern | **OPEN** | P2 — known mis-extraction on bundled filings. |
| B-024 | Self-healing auto-backup is broken | **DONE** 2026-05-21 | Re-scoped during Plan: actual issue was coverage gaps, not broken mechanism. Sprint 4 extended C-3 pattern to all 8 DB-writer scripts; AST regression test (`test_scripts_have_backup_pattern.py`) enforces. |
| B-025 | Normalize director `role` into 14 buckets + 8 signal tiers | **DONE** 2026-05-20 | Phase A + Phase B both shipped. Dashboard chips, signal modules, scoreboard, diagnostics chart all updated. |

### New items added 2026-05-20 (from code review)

| ID | Title | Severity | Source |
|----|-------|----------|--------|
| B-026 | `backfill_filings.py` connection-leak (B-013 leftover) | HIGH | code-review H-1 |
| B-027 | Refresh broken test suite (10 files reference deprecated signals/buckets) | HIGH | Phase B cleanup + B-012 |
| B-028 | `db.upsert_transaction` commits per row — should commit per batch | HIGH | code-review H-2 |
| B-029 | `backfill_announced_at.py` per-row commit + no pre-snapshot | HIGH | code-review H-3 |
| B-030 | `eval_signals.py` non-atomic `_backtest_skips.json` write | HIGH | code-review H-4 |
| B-031 | `backtest.py` non-atomic `_backtest_skips.json` write | HIGH | code-review H-5 |
| B-032 | `db.py:_apply_schema_migrations` no try/except around migration script | MEDIUM | code-review M-1 |
| B-033 | `db.connect()` called outside try-block in eval_signals.py + backtest.py | MEDIUM | code-review M-2 |
| B-034 | `run_pending_sweep.py` `seal()` only runs on exit-0 | MEDIUM | code-review M-4 |
| B-035 | `db_health.py:backup()` no fsync before rename | LOW | code-review L-1 |
| B-036 | `backfill_announced_at.py` opens two connections in dry-run mode | LOW | code-review L-2 |
| B-037 | `repair_dates.py:_insert_transaction` bypasses `db.upsert_transaction` → no `role_normalized` | LOW | code-review L-3 |
| B-038 | Consolidate four atomic-JSON-write copies into `db.atomic_write_json` helper | LOW | code-review L-4 |
| B-039 | Delete dead file `signals/t1_ceo_cfo_buy_v1.py` and unused regex constants in `classify_role.py` | LOW | Phase A/B cleanup |
| B-040 | Second-pass audit on `parse_pdmr.py`, `llm_parser.py`, `llm_cost.py`, `fetch_prices.py`, `fetch_sectors.py`, `detect_clusters.py` | MEDIUM | not yet audited |
| B-041 | Performance page CEO/Founder split from CFO | LOW | exploratory (Rupert mentioned 2026-05-20) |
| B-042 | T7 chair split into T7a exec / T7b non-exec (if performance diverges) | LOW | exploratory |

### New items added 2026-05-21 (post-Sprint 4 surface)

| ID | Title | Severity | Source |
|----|-------|----------|--------|
| B-043 | Non-cp1252 Unicode chars in pipeline `print()` statements (`check_announced_at_coverage.py`, `repair_pending_review.py`) | LOW | Sprint 4 refresh-failure debug. Hotfix already shipped for `db_health.py`; this is the remaining surface. Memory: `feedback_avoid_non_cp1252_in_subprocess_prints.md` |

### Recently shipped (chronological)

| Date | Item |
|------|------|
| 2026-05-21 | **Sprint 5 — Connection-leak + commit-batch + test refresh** (B-026, B-027, B-028, B-033, B-036, B-038, B-039). `db.upsert_transaction` no longer commits per row — callers commit per filing (B-028). All connections opened inside `try:` blocks. Atomic-JSON-write consolidated into `db.atomic_write_json` helper. 10 test files refreshed for 8-tier role scheme — `unittest discover` green. Two real production precedence bugs surfaced and fixed during B-027 (CEO routing as Chair; Non-Exec Chairman routing as Chair). Verified end-to-end via `backfill_announced_at` (clean run, idempotent retry) + `start.bat` Refresh. |
| 2026-05-21 | **Sprint 4 — DB-safety + auto-backup repair** (B-024, B-029, B-030, B-031, B-034). C-3 pattern (pre-check + pre-backup + post-check + seal) extended to `run_scrape.py`, `backfill_filings.py`, `backfill_prices.py`, `backtest.py`, `backfill_announced_at.py`. `run_pending_sweep.py` `seal()` now runs on all exit paths. Atomic writes for `_backtest_skips.json`. `db_health fail-stale 48` CLI wired into `start.bat`. AST regression test enforces the pattern across 8 DB-writer scripts. |
| 2026-05-20 | **C-1 / C-2 / C-3 — CRITICAL DB-safety fixes** — `eval_signals.py`, `repair_dates.py`, `classify_issuers.py` now all take pre-run `.bak` + integrity check + post-run seal. Pattern lifted from `backfill_role_normalized.py`. Closes B-021 indirectly. |
| 2026-05-20 | **B-025 — Role Normalization Phase A + Phase B** — 14-bucket canonical taxonomy + 8-tier signal scheme. New `role_normalized` column, deterministic `normalize_role()`, new signal modules (`t1a_ceo_founder_buy`, `t1b_cfo_buy`, `t5_pca_buy`, `t6_company_sec_buy`, `t7_chair_buy`). Dashboard chips, Performance scoreboard, role tile, diagnostics chart all updated. £17.98m of historical BUY value previously misclassified as T1/T2/T3 reclassified as PCA. |
| 2026-05-18 | **Sprint 3 — Table-aware parser** (B-001 + B-004 + B-016 + B-017). `parse_pdmr.py` rewritten. `reparse_corpus.py` walks every cached HTML and applies Option A in-place director-name updates + multi-row inserts. Net DB: 2,003 → 2,278 rows. Signal firings: 1,532 → 1,801 (+17.5%). |
| 2026-05-18 | **Sprint 2 — IT/CEF purge** (B-011). 130 tickers / 452 transactions / 789 signals removed. |
| 2026-05-18 | **Sprint 1 — Quick wins + chart honesty** (B-002, B-005, B-006, B-008, B-009, B-010). Six items in one session, zero gates. |
| 2026-05-15 | Date integrity audit + dashboard health panel, dot-separated date fix, repair_dates FK ordering, I5 threshold relaxed. |

---

## Section 2 — Prioritized open-items list

Priority rubric:
- **P0** — data-correctness / safety-net gaps (would still produce wrong figures or unrecoverable corruption).
- **P1** — operational quality (would cause Rupert to lose work or trust a wrong number).
- **P2** — hygiene + maintainability (slows future work but doesn't break anything).
- **P3** — exploratory enhancements (would-be-nice, no urgency).

### P0 — data-correctness / safety-net gaps

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-024 | Self-healing auto-backup is broken | `directors.db.bak` doesn't refresh automatically. The C-1/C-2/C-3 fixes patched the worst offenders but the pipeline-wide safety net is still broken. Until fixed, every Zone B script Rupert runs is one FUSE blip away from un-recoverable corruption. | M (~2 hrs) | High |
| B-029 | `backfill_announced_at.py` per-row commit + no pre-snapshot | Per-row commits are the FUSE-vulnerable pattern. Also missing pre-snapshot — a crash mid-run leaves the DB modified with no backup. | S (~45 min) | High |
| B-030 | `eval_signals.py` non-atomic `_backtest_skips.json` write | Mid-write crash strands a truncated JSON; next pipeline run can't parse it. | XS (~15 min) | Low (DB safe, just sentinel file) |
| B-031 | `backtest.py` non-atomic `_backtest_skips.json` write | Same as B-030, different file. | XS (~15 min) | Low |

### P1 — operational quality

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-026 | `backfill_filings.py` connection-leak | `conn = db.connect()` outside `try:` — if setup raises, the SQLite handle leaks and the Windows file lock holds until process exit. Next `start.bat` may fail to open the DB. Same pattern fixed in `run_scrape.py` and `repair_dates.py` already. | XS (~20 min) | Low |
| B-028 | `db.upsert_transaction` commits per row | Chronic FUSE risk surface — 3,000+ separate non-sequential writes during multi-thousand-row backfills. Commit per batch instead. Touches callers. | M (~1.5 hrs) | Medium (caller changes) |
| B-027 | Refresh broken test suite (10 files reference deprecated buckets/signal IDs) | After Phase B, `unittest discover` is noisy. Until cleaned up we can't trust a green run as a CI gate. Blocks future "must be green before ship" discipline. | M (~1.5 hrs) | Low |
| B-034 | `run_pending_sweep.py` `seal()` only runs on exit-0 | A mid-pipeline failure leaves the DB modified with no fresh backup. Same shape as B-029 but in a different script. | XS (~15 min) | Medium |

### P2 — hygiene + maintainability

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-032 | `db.py:_apply_schema_migrations` no try/except | If migration 005 ever fails halfway, `schema_version` doesn't bump but partial DDL may be applied. Recovery is undocumented. | S (~30 min) | Low |
| B-033 | `db.connect()` outside try-block in eval_signals.py + backtest.py | Inconsistent with `run_scrape.py`. Benign today, copy-paste risk tomorrow. | XS (~15 min) | Low |
| B-038 | Consolidate atomic-JSON-write into `db.atomic_write_json` helper | Four distinct copies drift independently. One helper means fixes happen once. | S (~30 min) | Low |
| B-037 | `repair_dates.py:_insert_transaction` bypasses `db.upsert_transaction` | After Phase B, inserts via this path lack `role_normalized`. Quiet correctness drift. | XS (~20 min) | Low |
| B-036 | `backfill_announced_at.py` opens two connections in dry-run mode | Connection-leak risk in dry-run path. Hygiene. | XS (~10 min) | Low |
| B-035 | `db_health.py:backup()` no fsync before rename | Low-likelihood damage on hard reset only. | XS (~15 min) | Low |
| B-039 | Delete dead file + unused regex constants | `signals/t1_ceo_cfo_buy_v1.py` is a deprecation stub; `CEO_CFO_RE` / `NED_RE` / `OTHER_EXEC_RE` in `classify_role.py` are unused. | XS (~15 min) | Low |
| B-043 | Non-cp1252 Unicode in pipeline `print()` (`check_announced_at_coverage.py`, `repair_pending_review.py`) | Same trap that broke Sprint 4 refresh. Currently dormant because neither script is in `refresh_all.py` STEPS — but a future addition would re-surface the same UnicodeEncodeError. ASCII replacement is mechanical. | XS (~15 min) | Low |
| B-040 | Second-pass audit on `parse_pdmr.py`, `llm_parser.py`, `fetch_prices.py`, etc. | Five files weren't covered in the 2026-05-20 review. Catches unknown unknowns. | M (~1 hr) | Low |
| B-023 | Bundled-PDMR detection doesn't fire on AAL "PCA" pattern | Partial mis-extraction — AAL 8950385 yields 1 row when it should yield 3. | S (~30 min) | Low |
| B-020 | Triage 334 orphan candidates from Sprint 3 reparse | 50–80 clean-name orphans need explanation; otherwise we can't safely run `reparse_corpus.py --delete-orphans`. | M (~2 hrs) | Low |
| B-015 | Sweep cannot safety-check rows whose pending entry lacks `announced_at` | Currently blocks 6 stranded pending rows. The safety guard was a placebo for repair-sourced entries. | M (~1 hr) | Low |

### P3 — exploratory / nice-to-have

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-019 | CAR chart per-series toggle + solo mode | Dashboard polish — 7 overlapping signal lines are hard to read. | S (~1 hr) | Low |
| B-007 | I6 informational late-filings badge | Visibility — count of legitimately-late filings as a neutral grey badge. | S (~30 min) | Low |
| B-022 | Filings without labelled issuer KV row produce empty company | NET-style filings → `company=''`. Polish. | S (~30 min) | Low |
| B-014 | `_pending_review.json` accumulates 4,000+ unrecoverable entries | Mental clutter — "4,171 pending" sounds scarier than it is. Triage + archive. | M (~1.5 hrs) | Low |
| B-018 | Classifier needs sustainable periodic-refresh data source | Only matters if/when we re-classify quarterly. | L (~3 hrs) | Low |
| B-041 | Performance page CEO/Founder split from CFO | Stage 6 enhancement — Rupert wants the tiles to split CEO+Founder from CFO if/when performance diverges. | S (~30 min) | Low |
| B-042 | T7 chair split into T7a exec / T7b non-exec | Defer until performance data shows divergence. | S (~30 min) | Low |

---

## Section 3 — Proposed sprints

Three sprints, each ≤ 2 hours of Rupert-time. Recommended order is Sprint 4 → 5 → 6 (most leverage first, hygiene next, audit last).

### Sprint sequence at a glance

| Sprint | Theme | Items | Rupert time | Gates | Risk |
|--------|-------|-------|-------------|-------|------|
| 4 | DB-safety + auto-backup repair | B-024, B-029, B-030, B-031, B-034 | ~25 min | 1 | High (touches the backup mechanism itself) |
| 5 | Connection-leak + commit-batch hygiene + test refresh | B-026, B-027, B-028, B-033, B-036, B-038, B-039 | ~30 min | 1 | Medium (test changes can mask regressions) |
| 6 | Deferred audit + cleanup polish | B-040, B-032, B-035, B-037, B-043, B-015, B-023 | ~45 min | 1 | Low |

P3 items (B-007, B-014, B-018, B-019, B-020, B-022, B-041, B-042) stay parked until after Sprint 6 unless Rupert explicitly pulls one forward.

---

### Sprint 4 — DB-safety + auto-backup repair (Rupert time: ~25 min, gates: 1, risk: High)

**Goal:** Close the remaining safety-net gaps. After C-1/C-2/C-3 the worst offenders are patched, but the pipeline-wide auto-backup (`directors.db.bak`) is still broken — every Zone B run is one FUSE blip from corruption with no fresh backup to restore from. This sprint fixes that, plus the two non-atomic JSON writes and the per-row commit pattern in `backfill_announced_at.py`.

**Scope (5 items)**

| ID | Title | Code size | Sequence |
|----|-------|-----------|----------|
| B-024 | Self-healing auto-backup repair | M | 1st — biggest leverage |
| B-029 | `backfill_announced_at.py` per-row commit + no pre-snapshot | S | 2nd |
| B-034 | `run_pending_sweep.py` `seal()` only runs on exit-0 | XS | 3rd — sibling pattern |
| B-030 | `eval_signals.py` atomic JSON write | XS | 4th |
| B-031 | `backtest.py` atomic JSON write | XS | 5th |

**Sequence within sprint**

1. **B-024 first.** Audit `db_health.py:backup()` — confirm it's being called, written to the right path, surviving FUSE re-mount. Add a "stale-backup" warning to `start.bat` (>24 hrs → warn loud, >48 hrs → fail loud). The same audit will guide whether B-035 (fsync before rename) is needed in Sprint 6.
2. **B-029** — wrap the loop in a single `BEGIN IMMEDIATE / COMMIT`, add `db_health.backup()` pre-run + `seal()` post-run. Same pattern as C-1/C-2/C-3.
3. **B-034** — move `seal()` into a `finally:` block so it runs whether the pipeline exits 0 or non-zero. One-line edit.
4. **B-030 + B-031** — both use `SKIPS_PATH.write_text(...)`. Replace with the `tmp.write_text(); os.replace(tmp, path)` pattern. Tiny mechanical edit; do them together.

**Gates**

- **Gate 1 — backup repair sanity-check (mandatory).** After B-024 ships, Rupert runs `start.bat` once and confirms `.data/directors.db.bak` is freshly dated. Without this gate, the rest of the sprint's defensive work is sitting on a broken safety net.

**Rupert-time breakdown**

- 10 min: paste-back of the auto-backup audit output + decide on stale-backup thresholds (24/48 hrs).
- 5 min: Gate 1 verification — run `start.bat`, eyeball `.bak` modification time.
- 5 min: kick off `backfill_announced_at.py` from PowerShell (Zone B).
- 5 min: final dashboard smoke after Sprint 4 completes.

**Definition of done**

- `.data/directors.db.bak` refreshes automatically after every successful pipeline run.
- `start.bat` warns / fails loud on a stale backup.
- `backfill_announced_at.py` takes a pre-run `.bak` and commits the loop as one transaction.
- `run_pending_sweep.py` runs `seal()` on all exit paths.
- `_backtest_skips.json` writes are atomic (tmp + rename) in both `eval_signals.py` and `backtest.py`.
- `audit_dates.py` still green.

**Risk discussion**

High because B-024 touches the backup mechanism itself — if we break it further before we know it was already broken, we've made the situation worse. Mitigation: take a **manual** `.bak` snapshot at the start of the sprint (Rupert runs from PowerShell, Zone B) and verify integrity. If anything goes sideways we restore from that manual snapshot.

**Kickoff checklist**

- [ ] Rupert has taken a manual `.bak` snapshot at sprint start and shared the filename + integrity-check output.
- [ ] No Zone B pipelines are mid-run.
- [ ] Engineer has read `db_health.py` and the C-1/C-2/C-3 reference implementations.

---

### Sprint 5 — Connection-leak + commit-batch + test refresh (Rupert time: ~30 min, gates: 1, risk: Medium)

**Goal:** Finish the connection-leak audit that started with B-013, move `db.upsert_transaction` from per-row to per-batch commits (chronic FUSE risk surface), and refresh the broken test suite so we can finally trust `unittest discover` as a green/red CI gate.

**Scope (7 items)**

| ID | Title | Code size | Sequence |
|----|-------|-----------|----------|
| B-026 | `backfill_filings.py` connection-leak | XS | 1st — quick win |
| B-033 | `db.connect()` outside try-block in eval_signals.py + backtest.py | XS | 2nd — mechanical |
| B-036 | `backfill_announced_at.py` two-conn dry-run leak | XS | 3rd — finish leak audit |
| B-039 | Delete dead `t1_ceo_cfo_buy_v1.py` + unused regex constants | XS | 4th — clears noise before tests |
| B-027 | Refresh broken test suite (10 files) | M | 5th — biggest item |
| B-038 | Consolidate atomic-JSON-write helper | S | 6th — pays back B-030/B-031 |
| B-028 | `db.upsert_transaction` commits per row → per batch | M | 7th — touches callers, do last |

**Sequence within sprint**

1. **B-026 + B-033 + B-036** — three connection-leak fixes, same pattern. Quick warmup.
2. **B-039** — delete the dead file and unused constants. Grep first to confirm no external imports.
3. **B-027** — refresh the 10 test files to use the new 8-tier scheme (t1a/t1b/t2/t3/t5/t6/t7) and the new signal IDs. Files: `test_classify_role.py`, `test_drill_payload.py`, `test_render_drilldown.py`, `test_render_performance_v2.py`, `test_cohort_table.py`, `test_export_dashboard_json.py`, `test_stage_04.py`, `test_stage_04_6.py`, `test_p3_lookahead.py`, `test_stage_05.py`. Run `python -m unittest discover -s .scripts -p "test_*.py"` at the end; target green.
4. **B-038** — consolidate the four atomic-JSON-write copies into `db.atomic_write_json(path, payload)`. Migrate callers including the two sites fixed in Sprint 4 (B-030, B-031).
5. **B-028** — move per-row commit out of `db.upsert_transaction`; commit per filing in callers (`run_scrape.py`, `backfill_filings.py`). Touches callers — do last, with a fresh `.bak` immediately before.

**Gates**

- **Gate 1 — broken-tests baseline (informational, not blocking).** Before B-027 starts, capture the current `unittest discover` failure list. After B-027 ships, compare — every previously-failing test either now passes or is deliberately deleted with a backlog note. The point is to make sure we know what changed.

**Rupert-time breakdown**

- 10 min: paste-back of leak-audit grep results + Gate 1 baseline.
- 10 min: review the test-refresh diff before B-028 starts (changes to assertions are subtle; worth a sanity skim).
- 10 min: final smoke run + dashboard verify after B-028 (commit-batch change is the highest-blast-radius edit in this sprint).

**Definition of done**

- `backfill_filings.py`, `eval_signals.py`, `backtest.py`, `backfill_announced_at.py` all open connections inside `try:` / `with closing(...)`.
- `signals/t1_ceo_cfo_buy_v1.py` deleted. Unused regex constants removed from `classify_role.py`.
- `python -m unittest discover -s .scripts -p "test_*.py"` exits 0 (or every failure has a documented reason).
- `db.atomic_write_json` exists; the four pre-existing copies are migrated to it.
- `db.upsert_transaction` no longer commits per row; callers commit per filing.
- One full `refresh_all.py` smoke run completes end-to-end.

**Risk discussion**

Medium. B-028 changes the commit lifecycle in the single canonical upsert function — a wrong move here breaks every backfill. Mitigation: take a `.bak` immediately before B-028, run on `_review_candidates.csv` first, and only proceed to full corpus after a small batch verifies. B-027 is lower risk on its own — but if the new tests are wrong they'll mask future regressions, which is sneaky. Skim the diff at Gate 1.

**Kickoff checklist**

- [ ] Sprint 4 complete; auto-backup confirmed working.
- [ ] Engineer has the C-1/C-2/C-3 + Phase B specs as reference patterns.
- [ ] `unittest discover` baseline captured (file + failure list).

---

### Sprint 6 — Deferred audit + cleanup polish (Rupert time: ~45 min, gates: 1, risk: Low)

**Goal:** Cover the audit blind-spots from the 2026-05-20 review (parser, LLM, prices, sectors, cluster-detection), fix the remaining medium/low hygiene items, and clear the long-standing B-015 + B-023 items so the parser surface is fully clean.

**Scope (6 items)**

| ID | Title | Code size | Sequence |
|----|-------|-----------|----------|
| B-040 | Second-pass audit on `parse_pdmr.py`, `llm_parser.py`, `llm_cost.py`, `fetch_prices.py`, `fetch_sectors.py`, `detect_clusters.py` | M | 1st — surfaces new items |
| B-032 | `db.py:_apply_schema_migrations` no try/except | S | 2nd |
| B-035 | `db_health.py:backup()` no fsync before rename | XS | 3rd — depends on Sprint 4 outcome |
| B-037 | `repair_dates.py:_insert_transaction` bypasses `db.upsert_transaction` | XS | 4th |
| B-043 | Non-cp1252 Unicode in pipeline `print()` — sweep `check_announced_at_coverage.py` + `repair_pending_review.py` | XS | 5th — fold into the audit pass |
| B-023 | Bundled-PDMR detection doesn't fire on AAL "PCA" pattern | S | 6th |
| B-015 | Sweep cannot safety-check rows without `announced_at` | M | 7th — unblocks 6 stranded rows |

**Sequence within sprint**

1. **B-040 first.** Spend ~1 hour reading the six unaudited files. Triage findings into one of three buckets: (a) backlog as a new B-NNN item, (b) fold into this sprint if small + adjacent, (c) defer to Sprint 7 if it's a substantial new programme. Report findings to Rupert before continuing.
2. **B-032** — wrap each migration in try/except + explicit transaction. Document the recovery path in `CLAUDE.md`.
3. **B-035** — fsync before rename in `db_health.py:backup()`. Skip if Sprint 4's audit found this isn't needed.
4. **B-037** — change `_insert_transaction` to call `db.upsert_transaction`. Inserts will now populate `role_normalized`.
5. **B-023** — trace `_bundled_name_warning` against AAL 8950385's flat text. Fix and add a unit test (now possible because Sprint 5 refreshed the test suite).
6. **B-015** — derive `announced_at` from cached HTML for sweep input; re-run sweep against the 6 stranded rows. Recovers up to 7 rows currently parked.

**Gates**

- **Gate 1 — B-040 audit triage (mandatory).** After the second-pass audit completes, Rupert reviews the findings list and decides which items fold into Sprint 6, which become new backlog items, and which become Sprint 7. Without this gate the sprint scope can balloon.

**Rupert-time breakdown**

- 20 min: read the audit findings + triage call.
- 10 min: review B-023 / B-015 results — AAL filing should now show 3 rows; the 6 stranded pending rows should clear (or be deliberately archived).
- 15 min: final dashboard smoke + audit-dates.py check.

**Definition of done**

- Second-pass audit complete; findings either fixed in this sprint or filed as B-NNN items.
- Migration script runs are bounded by try/except + explicit transactions.
- `repair_dates.py` inserts go through `db.upsert_transaction` (so `role_normalized` is populated).
- AAL 8950385 yields 3 rows.
- The 6 stranded pending rows from B-015 are either recovered or archived.
- `audit_dates.py` green.
- One full `refresh_all.py` smoke run completes.

**Risk discussion**

Low. No deletes, no schema changes, no commit-lifecycle edits. The audit may surface a high-severity finding mid-sprint — handle via Gate 1 (carve out a hotfix sprint if needed; don't try to absorb a critical fix into this scope).

**Kickoff checklist**

- [ ] Sprints 4 and 5 complete.
- [ ] `unittest discover` green (Sprint 5 deliverable).
- [ ] Engineer is happy to budget ~1 hour for the read-only audit phase before any code edits.

---

## Recommended next move

**Start Sprint 4 next.** The C-1/C-2/C-3 fixes closed the worst per-script gaps, but the system-wide safety net (`directors.db.bak` auto-refresh) is still broken — memory note `project_auto_backup_broken.md` and code-review M-4 both flag this, and every Zone B run since Sprint 3 has been one FUSE blip from un-recoverable corruption. Sprint 4 is short (~25 min Rupert-time), has the highest blast-radius reduction per minute spent, and creates the safety net Sprints 5 and 6 will depend on. Sprint 5's commit-batch refactor in particular (B-028) is the kind of change you only want to make when you trust the backup mechanism.
