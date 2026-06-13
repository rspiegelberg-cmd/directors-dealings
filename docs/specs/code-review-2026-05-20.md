# Code review — 2026-05-20

**Status:** Audit complete. 3 CRITICAL DB-safety fixes recommended before next feature work.
**Trigger:** Rupert requested a clean-up review across all files focused on DB corruption/truncation prevention before moving to new updates.
**Method:** DB-safety agent (comprehensive); Claude direct-audit for Phase A/B cleanup + general code quality. Two of three parallel agents hit rate limits — the remaining ground was covered manually.

---

## Summary

The codebase is generally healthy and well-instrumented. The big-rocks defensive patterns (`db_health.py`, FUSE Zone A/B rules, atomic JSON writes, integrity checks in `backfill_role_normalized.py`) are in place — but they're **not applied consistently**. Four scripts that write to the DB skip the pre-run snapshot + integrity check that `backfill_role_normalized.py` does correctly.

**One high-leverage single fix** removes most of the data-loss risk: add `db_health.backup()` + `db_health.seal()` calls to four scripts (`eval_signals.py`, `classify_issuers.py`, `repair_dates.py`, `backfill_announced_at.py`). Estimated effort: 2 hours.

After that, the next cleanup tier is: rebuild the broken test suite (10 files reference the deprecated 3-bucket / t1_ceo_cfo scheme).

**Verdict:** Approve for next feature work **after** the 3 CRITICAL DB-safety fixes ship. Everything else is hygiene that can be addressed in a follow-up sprint.

---

## CRITICAL findings (data-loss risk — fix BEFORE next feature)

### C-1. `eval_signals.py:307-349` — `--rebuild` deletes the entire `signals` table with no snapshot
- **Risk:** A FUSE blip or crash mid-rebuild leaves the `signals` table half-empty. There's no fresh `.bak` to restore from.
- **Fix:** Add `db_health.backup()` BEFORE the `DELETE FROM signals` line. Add `PRAGMA integrity_check` before AND after. Call `db_health.seal()` on successful exit.
- **Reference implementation:** `backfill_role_normalized.py` already does this — copy the pattern.

### C-2. `repair_dates.py:240-303` — row-by-row delete+insert with no pre-snapshot
- **Risk:** Cascading deletes touch `transactions`, `signals`, `paper_trades`. A FUSE corruption mid-loop leaves the DB partially repaired and the previous `.bak` may already be stale.
- **Fix:** Call `db_health.backup()` at line ~141 (before `conn = db.connect()`). Call `db_health.seal()` after the loop completes.

### C-3. `classify_issuers.py:609-617` — `UPDATE tickers_meta SET is_excluded_issuer = 0` with no pre-snapshot
- **Risk:** Zeros every prior exclusion flag inside the same transaction as the re-apply loop. A crash between the reset and the re-apply silently un-excludes every investment trust / CEF. The `seal()` at line 643 is too late.
- **Fix:** Call `db_health.backup()` immediately before `conn = db.connect()` at line 527. **This is already a known issue per memory `project_classify_issuers_resets_flag.md` — the safety upgrade was deferred.**

---

## HIGH findings (operational risk)

### H-1. `backfill_filings.py:99 + 166-180` — connection-leak pattern
- `conn = db.connect()` runs OUTSIDE the `try:`. If setup raises, the SQLite handle leaks and the Windows file lock holds until process exit. `run_scrape.py:175-304` was fixed for this in B-013 — `backfill_filings.py` was not.
- **Fix:** Mirror `run_scrape.py` pattern: pre-init variables, fold setup inside try/finally.

### H-2. `db.py:112-164 upsert_transaction` — commits after EVERY row
- The single canonical upsert function commits inside the function. For multi-thousand-row backfills this is 3,000+ separate non-sequential SQLite writes — exactly the FUSE-vulnerable pattern.
- **Fix:** Remove the in-function `conn.commit()` at lines 151 and 161. Commit once per filing in the caller (run_scrape, backfill_filings).
- **Effort:** Medium — requires updating callers but eliminates a chronic FUSE risk surface.

### H-3. `backfill_announced_at.py:177-182` — same per-row commit anti-pattern
- The docstring claims "each UPDATE is committed individually so a crash mid-run leaves the DB consistent" — but that's exactly the pattern FUSE corrupts. No pre-run `.bak` either.
- **Fix:** Wrap the loop in a single `BEGIN IMMEDIATE / COMMIT`. Add pre-snapshot.

### H-4 + H-5. Non-atomic JSON sentinel writes
- `eval_signals.py:332` and `backtest.py:373-380` use `SKIPS_PATH.write_text(...)` without the tempfile + `os.replace()` pattern.
- **Risk:** Mid-write crash strands a truncated JSON file. The next run can't parse it.
- **Fix:** Use the same `tmp = path.with_suffix("...tmp"); tmp.write_text(...); os.replace(tmp, path)` pattern used elsewhere in the codebase.

---

## MEDIUM findings (robustness)

- **M-1.** `db.py:_apply_schema_migrations` — no try/except around `conn.executescript(migration_sql)`. If migration 004 fails halfway, `schema_version` is never bumped but partial DDL may have been applied. Recovery path is undocumented.
- **M-2.** `db.connect()` called outside `try:` in `eval_signals.py:307` and `backtest.py:411` — inconsistent with `run_scrape.py`. Benign today but copy-paste risk.
- **M-3.** `detect_clusters.py:165-173` — clean try/finally pattern but no pre-run `.bak`. Inherited from `eval_signals.py`, so the C-1 fix covers it.
- **M-4.** `run_pending_sweep.py:467` — `seal()` only runs if pipeline exits 0. A mid-pipeline failure leaves the DB modified with no fresh backup.

---

## LOW findings (hygiene)

- **L-1.** `db_health.py:55-71 backup()` — `shutil.copy2()` doesn't `fsync` before `replace`. Low-likelihood damage on hard reset only.
- **L-2.** `backfill_announced_at.py:97-112` opens two `db.connect()` connections in dry-run mode.
- **L-3.** `repair_dates.py:108-124` — `_insert_transaction` uses raw `INSERT OR IGNORE` and bypasses `db.upsert_transaction`. After Phase B this means inserted rows lack `role_normalized`.
- **L-4.** Four distinct copies of the atomic JSON-write pattern. Drift risk. Consolidate into `db.atomic_write_json(path, payload)` helper.

---

## Phase A/B cleanup (B-025 leftovers)

### Broken tests — 10 files reference the deprecated 3-bucket scheme
These will fail when run under `python -m unittest discover`:

- `test_classify_role.py` — asserts old `ceo_cfo` / `other_exec` / `ned` return values from `classify_role()`
- `test_drill_payload.py` — same
- `test_render_drilldown.py` — same
- `test_render_performance_v2.py` — same
- `test_cohort_table.py` — possibly affected
- `test_export_dashboard_json.py` — possibly affected
- `test_stage_04.py`, `test_stage_04_6.py`, `test_p3_lookahead.py`, `test_stage_05.py` — reference old t1_ceo_cfo_buy signal_id

**Fix:** Refresh assertions to use the new 8-tier keys (t1a/t1b/t2/t3/t5/t6/t7) and the new signal IDs. ~1-2 hours.

### Dead code
- `.scripts/signals/t1_ceo_cfo_buy_v1.py` — deprecated stub that raises ImportError. Can be physically deleted from Windows when nothing imports it (it's been on disk since the cut-over; safe to delete now).
- `.scripts/classify_role.py` — keeps `CEO_CFO_RE`, `NED_RE`, `OTHER_EXEC_RE` regex constants for backward compatibility. The mapper no longer uses them. Could be removed if no external code imports them.

### Documentation
- The Phase B spec is comprehensive. No documentation gaps found.

---

## General code-quality notes

### What looks good
- **`server.py` subprocess spawning** uses `subprocess.Popen` with a list of args (no `shell=True`), no user input flows directly into the command line, and `scrape_days` goes through `int()` before being added. Safe.
- **All SQL queries** use parameter binding (`?` placeholders). No f-string SQL composition spotted — good defense against SQL injection.
- **JSON writes** generally use the tempfile + `os.replace()` atomic pattern. The H-4/H-5 sites are exceptions, not the rule.
- **Connection lifecycle** is mostly handled correctly. The H-1 backfill_filings exception is a known gap (B-013 partial fix).

### Not audited deeply (out of scope for this pass)
- `parse_pdmr.py` — recent Sprint 3 rewrite. Worth its own focused review before the next parser change.
- `llm_parser.py` — LLM fallback. Cost-control logic in `llm_cost.py` should be sampled.
- `fetch_prices.py` / `fetch_sectors.py` — Yahoo Finance fetchers. Rate-limit and retry logic untouched in this pass.
- Hot-path analysis on `detect_clusters.py` — O(N²) candidate but no perf data to confirm an issue.

---

## Recommended action plan (in order)

### Sprint Now (~3 hours, MUST do before next feature)
1. **Fix C-1, C-2, C-3** — add `db_health.backup()` + integrity-check to `eval_signals.py`, `repair_dates.py`, `classify_issuers.py`. ~2 hours. **Highest-leverage single change in this review.**
2. **Fix H-4, H-5** — atomic JSON writes for `_backtest_skips.json`. ~30 minutes.

### Sprint Next (~3 hours, SHOULD do soon)
3. **Fix H-1** — backfill_filings.py connection-leak pattern.
4. **Fix H-2 + H-3** — move per-row commits to per-batch commits in `db.upsert_transaction` callers.
5. **Refresh broken tests** — 10 test files need updating to the new 8-tier scheme.

### Sprint Later (~2 hours, NICE to have)
6. **L-4** — Consolidate the 4 atomic-JSON-write copies into one helper.
7. **M-1** — Document the migration-failure recovery path. Wrap each migration in explicit transaction.
8. **Backlog the 4 remaining MEDIUM / LOW findings** with B-026 through B-029 entries.

### Process change
- Add a pre-commit habit: **every new Zone B script gets `db_health.backup()` at start and `db_health.seal()` at success-exit.** Worth documenting in `CLAUDE.md` as a hard rule. The existence of `backfill_role_normalized.py` (which does it right) shows the pattern is understood — it just needs to be the default, not opt-in.

---

## Verdict

**Approve for next feature work** after the 3 CRITICAL fixes (~2 hours of work). The codebase is in good shape; the gaps are concentrated in a small number of high-blast-radius scripts that need the same defensive pattern applied uniformly. No security or correctness bombs found in the audited surface area.

If you want me to implement the 3 CRITICAL fixes right now, say the word and I'll do them — they're small, mechanical, and copy-paste from `backfill_role_normalized.py`.
