# Sprint 21 — "Process insurance" (operational safety)

**STATUS: CLOSED 2026-06-03 (mostly verify-and-close).** The audit found the core items were already implemented since the backlog entries were written — a recurring pattern in this project (the backlog lags the code). One item (B-012) needs a Windows-side test run to triage authoritatively; one (B-085) is an optional process add.

**Discipline:** read-only audit first (Claude); any fix is Zone-A code, write-path runs are Rupert's. Per CLAUDE.md.

---

## Findings

### B-024 — self-healing backup — ✅ RESOLVED (verified)
The mechanism works and coverage is complete:
- `db_health.py` exposes `check/backup/restore/guard/seal` plus the stale-backup defences `warn_if_stale`, `fail_if_stale`, `auto_seal_if_stale`.
- `start.bat` wires the sequence: `restore` → `check` → `auto-seal 24` → `warn-stale 24` → `fail-stale 48`.
- **Every write-path script** calls `db_health.backup()` pre-flight and `db_health.seal()` on success: `eval_signals`, `backtest`, `drain_pending`, `reparse_corpus`, `backfill_prices`, `backfill_announced_at`, `backfill_filings`, `classify_issuers`, `exclude_investment_trusts`, `delete_triaged_orphans`, `fix_incident_buys`, and the new `fix_sprint20_delete_nourl_yearshares` (seal added 2026-06-03 for consistency).
- Confirmed live: the 2026-06-03 scoped reparse printed `[db_health] backup written -> directors.db.bak (24804 KB)`.

### B-015 — sweep `announced_at` safety — ✅ RESOLVED (verified)
`run_pending_sweep.py` derives `announced_at` from the cached HTML JSON-LD `dateCreated` (`_extract_announced_at_from_html`) before the LLM call, then the ±60-day `SANITY_WINDOW_DAYS` guard (`_date_within_window`) fires against that anchor. Entries with no derivable anchor are counted (`missing_announced_at`) and skipped, not blind-accepted.

### B-084 — `hidden`+`flex` display-trap audit — ✅ effectively clean
Grep of the dashboard renderers found no live `hidden`-attribute + display-utility combinations. The only matches are comments in `render_performance.py` documenting that visibility is deliberately toggled via inline `style.display` (NOT the `hidden`+`flex` trap that caused the Phase-4 overlay bug). The known instance is fixed; nothing new to do.

### B-012 — stale test suites — NEEDS A WINDOWS-SIDE RUN
In-sandbox `unittest discover` ran **513 tests in 8.6s** with 9 errors — but all 9 are the same cascade: a FUSE stale-read of `parse_pdmr.py` (poisoned by this session's edits) makes the sandbox import fail at a line nowhere near the edit. Rupert's successful `reparse_corpus` + `backfill_buy_strictness` runs (both import `parse_pdmr`) prove the file is syntactically valid on Windows, so the sandbox failure is an artifact, not a regression.
**Action (Rupert, PowerShell):**
```
python -m unittest discover -s .scripts -p "test_*.py"
```
Expected: far fewer than 9 errors once the parse_pdmr artifact is gone. Triage whatever remains — genuine regression (fix), fixture drift (refresh), or obsolete (delete with a note). Paste the output back and Claude will triage.

### B-085 — lightweight browser smoke-check in the QA loop — OPTIONAL, not done
A manual "open focus → click a dot → click a gap month" checklist (or a cheap headless check) per UI phase, to catch the class of bug that code-read QA + Python tests missed twice. Low effort; carry into the next UI sprint (24) rather than build standalone now.

---

## Outcome
B-024, B-015, B-084 resolved/verified. B-012 handed to a one-line Windows test run. B-085 deferred to the next UI sprint. Net new code this sprint: one `seal()` consistency line. The "process insurance" was largely already in place — which is itself the reassuring finding.
