# Sprint plan — Table-aware parser (B-001 + B-004)

**Author:** Claude
**Date:** 2026-05-19
**Status:** ready to execute, one sprint at a time
**Source documents:**
- Backlog scope: [`backlog-scopes-2026-05-18.md` § B-001 + B-004](./backlog-scopes-2026-05-18.md) (lines 625-747)
- Today's pipeline diagnostic: 22 of today's 43 RNS filings (51.2%) went to pending review because the regex parser couldn't handle their templates. This is the work that fixes that.

## Why this sprint exists

The current `parse_pdmr.py` is **pure regex** — it walks flat text looking for patterns. Two known failure modes have a shared root cause (no HTML table awareness):

1. **B-001 (multi-row tables):** Filings like DRIPs, SIP/SAYE, year-end compliance — multiple transactions in one filing. Only the first row makes it into the DB. Example: filing 9541612 (National Grid, Jacqueline Agg) has 4 transactions but only 1 is ingested.
2. **B-004 (director-name crosses cells):** The director regex matches across `<td>` boundaries, producing values like `"Kingfisher plc\nb"` — it captures the company name + the next field label instead of the actual director name.

Both fixes share: rewrite the extractor to walk the HTML **table structure** using BeautifulSoup. The parser stops being template-fragile.

**Bonus impact:** today's diagnostic showed 22/43 filings (51.2%) couldn't be parsed. The "bundled multi-PDMR" and "multi-tranche" categories make up 77.6% of the accumulated 4,189-entry pending pile. This sprint sweeps that whole class of bug.

## Locked decisions to make at PM check

1. **Fingerprint stability — Option A vs Option B** (backlog flag, sign-off in Sprint 4 preview gate):
   - **Option A (preferred):** When the new parser extracts a corrected director name for a row that already exists, detect the duplicate via `(date, ticker, type, shares, price)` match and **update the existing row in place**. Log to `.data/_reparse_director_fixes.log`. Lower-risk for v1.
   - **Option B:** Add a `fingerprint_version` field; old rows kept but marked superseded. More machinery; better audit trail.
2. **BeautifulSoup is a new dependency** — `pip install beautifulsoup4`. Pure-Python, no version drama. Confirm at PM check.

## Working principles

- **Each sprint is one paste-and-run unit.** Don't start the next one until the current sprint's gate is signed off.
- **Zone discipline (CLAUDE.md):** Claude does Zone-A (code, tests, dry-runs that don't write `.data/`) autonomously. The corpus re-parse + signal re-eval + backtest writes are Zone-B — **Rupert runs from PowerShell**, Claude provides the exact command.
- **Mandatory truncation check** after every code write — Read tool, not bash.
- **Preview-and-sign-off gate is mandatory before the live re-parse** — backlog AC #4 (`.data/_reparse_corpus_preview.csv`).
- **Pre-flight backup is mandatory** — back up `.data/directors.db` to `.data/directors.db.pre-b001.bak` before any pipeline re-run. Verify `PRAGMA integrity_check` passes.

---

## Sprint overview

| # | Sprint | Goal | Who runs | Stage gate? | Est. size |
|---|---|---|---|---|---|
| 1 | Foundation + tests | Install BeautifulSoup, pre-flight backup, write the 12-case test matrix BEFORE touching the parser (TDD). All tests should FAIL initially. | Claude | Internal QA | S (~150 lines tests, no production code yet) |
| 2 | Parser rewrite | Rewrite `_extract_transaction_rows` + `_extract_director_name` in `parse_pdmr.py` using BeautifulSoup. All 12 tests pass. | Claude | Internal QA | M (~200 lines of new parser logic) |
| 3 | Reparse orchestrator | Build `.scripts/reparse_corpus.py` — walks every cached HTML file, re-runs the new parser, emits preview CSV. **No DB writes in this sprint.** | Claude | Internal QA | S (~80 lines + preview-shape test) |
| 4 | Preview gate | Rupert reviews `.data/_reparse_corpus_preview.csv`. Signs off Option A vs B for fingerprint stability. Decides whether to proceed. | Rupert | **Yes — THE gate before any live DB writes** | Paste-and-run only |
| 5 | Apply re-parse | Rupert runs reparse with `--confirm`. Pipeline re-runs (eval_signals → backtest → export → build). Today's stuck filings should appear in dealings.json. | Rupert | Self-verifying via integration smoke | Paste-and-run sequence |
| 6 | Integration verification | All 5 audit invariants pass. Validation SQL queries return zero bad rows. Diff: transactions count, pending count, today's signal count. | Rupert | **Yes — final completion gate** | Paste-and-run only |

Total: ≈ 280 lines of new code + ≈ 200 lines of tests across two Claude sprints. Roughly 1 session of Claude work + 2 Rupert sessions (one for preview gate, one for apply + verify).

---

## Sprint 1 — Foundation + tests (TDD)

### Goal

Set the foundation: install BeautifulSoup, take the pre-flight DB backup, then write the **complete 12-case test matrix BEFORE touching the parser code** (TDD). Every test should FAIL initially against the current regex parser — confirming each test is a real bug, not a tautology.

### Prerequisites

- B-011 (IT/CEF purge) complete. ✓ — already in DB
- Rupert PM-check: confirm Option A vs Option B path (default Option A unless objection)
- Rupert installs BeautifulSoup from PowerShell

### Paste-and-run command for Rupert (one-time setup)

```powershell
cd C:\Dev\DirectorsDealings
pip install beautifulsoup4
Copy-Item .data\directors.db .data\directors.db.pre-b001.bak -Force
python -c "import sqlite3; r = sqlite3.connect('.data/directors.db.pre-b001.bak').execute('PRAGMA integrity_check').fetchone(); print('integrity:', r[0]); assert r[0] == 'ok', 'backup is corrupt!'"
```

Expected: `integrity: ok`. If not, abort and investigate.

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/test_parse_pdmr_tableaware.py` | NEW | 12-case test matrix for the table-aware parser. Includes B-001 multi-row cases, B-004 cross-cell director cases, edge cases (foreign templates, empty cells, etc.). | ~200 lines |
| `.scripts/fixtures/parser/` | NEW directory | ~10 HTML fixture files paired with `.expected.json` files containing the expected extraction output. | n/a |

### Acceptance criteria

- `.scripts/test_parse_pdmr_tableaware.py` runs under `python -m unittest`. **All 12 tests FAIL** (confirms each test catches a real regex-parser bug, not a tautology). The failures are the bugs we're about to fix.
- Fixture HTML files are minimal slices from real cached filings — one per known bug pattern:
  - `9541612_multirow_sip.html` — multi-row SIP/SAYE filing (B-001 reproducer)
  - `9573943_grant.html` — single-row grant (sanity baseline; should already pass)
  - `xxxx_kingfisher_director_bleed.html` — director-name regex captures company name (B-004 reproducer)
  - `xxxx_dot_separated_date.html` — `dd.mm.yy` date format (B-005 regression)
  - `xxxx_foreign_currency.html` — non-GBP filing
  - `xxxx_bundled_multi_pdmr.html` — multiple directors in one filing (parser refuses to split per spec — but our test should confirm graceful skip)
  - `xxxx_multi_tranche.html` — same director, multiple price tranches
  - 3-5 simpler positive-control cases
- BeautifulSoup4 confirmed installed: `python -c "import bs4; print(bs4.__version__)"` returns a version string.
- DB backup file exists at `.data/directors.db.pre-b001.bak` and passes integrity check.

### Zone discipline

- Pre-flight backup + BeautifulSoup install = Rupert PowerShell.
- Writing test code + fixture files = Claude (Zone A).
- Running `python -m unittest .scripts/test_parse_pdmr_tableaware.py` = Claude bash (safe — no DB writes).

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Tests pass against current regex parser (no bug to fix) | Each test must explicitly assert behaviour the current code CAN'T produce. Verified by running tests and observing failures. |
| Fixture HTML is too synthetic — doesn't catch real-world variants | Copy fixtures from `.scripts/_scrape_cache/` real filings, not hand-crafted HTML. Anonymise only if needed. |
| BeautifulSoup version drift breaks tests in CI later | Pin in fixture: import bs4; assert bs4.__version__ ≥ "4.10". |

### Stage gate

**Internal QA only.** Claude reports to Rupert:
- All 12 tests FAIL (confirming they catch real bugs)
- BeautifulSoup installed
- DB backup verified
- Fixture files listed

Rupert says "go to Sprint 2".

---

## Sprint 2 — Table-aware parser rewrite

### Goal

Rewrite the date + director extraction in `parse_pdmr.py` using BeautifulSoup. All 12 tests from Sprint 1 should now PASS. No DB writes yet — the corpus re-parse happens in Sprint 5.

### Prerequisites

- Sprint 1 complete and signed off.

### Inputs (what Claude reads)

- Backlog scope §B-001 + B-004 implementation section.
- Existing `.scripts/parse_pdmr.py` — understand current entry points and helper functions.
- Sprint 1's 12 test cases — drive the design.

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/parse_pdmr.py` | MODIFY | Replace `_extract_transaction_rows` and `_extract_director_name` with BeautifulSoup-based table-walkers. Keep the public API (the `extract(...)` function signature) unchanged so existing callers don't break. | +180 lines / -80 lines |

### Acceptance criteria

- All 12 tests in `test_parse_pdmr_tableaware.py` pass.
- All existing tests in the project test suite still pass (~154 from the perf redesign). No regressions.
- Filing 9541612 (the multi-row reproducer) parses to **4 transactions** with the correct dates 2024-08-13, 2025-02-12, 2025-08-13, 2026-01-14.
- Director-name extraction never crosses table cells. Validation logic: a final assertion before returning the row asserts the director string doesn't contain `\n` AND doesn't match `(plc|Ltd|LLP)\b`.
- Date extraction still handles the dot-separated format (`05.05.26`) — don't break B-005.
- Public API unchanged: `extract(html, url, rns_id, announced_at) -> list[transaction_dict]` returns the same shape. Existing callers (`run_scrape.py`, `backfill_filings.py`) don't need changes.

### Zone discipline

All Zone A. Claude builds + tests.

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| New parser breaks on a template variant not covered by the 12 fixtures | Sprint 3's preview-and-sign-off pass catches this before any live DB writes |
| Public API regression breaks existing callers | Run the full test suite after the rewrite — any breakage shows immediately |
| BeautifulSoup is slower than regex | Acceptable for v1 — re-parse is a one-shot, not a per-filing hot path. Sprint 5 measures wall-clock anyway. |

### Stage gate

**Internal QA only.** Claude reports:
- All 12 new tests pass
- All ~154 existing tests pass
- Diff summary of `parse_pdmr.py` changes
- Manual eyeball: filing 9541612 produces 4 transactions

Rupert says "go to Sprint 3".

---

## Sprint 3 — Reparse orchestrator + preview CSV

### Goal

Build the orchestrator that walks every cached HTML file, re-runs the new parser, and emits the preview CSV. **No DB writes in this sprint** — the script supports `--preview` (default) and `--confirm` (Sprint 5) modes.

### Prerequisites

- Sprint 2 complete and signed off.

### Inputs

- Backlog scope §B-001 + B-004 — preview-and-sign-off requirements (acceptance criteria #4).

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/reparse_corpus.py` | NEW | Walks every cached HTML in `.scripts/_scrape_cache/`, calls `parse_pdmr.extract(...)`, compares to existing DB rows, emits preview CSV. `--preview` (default, no writes) or `--confirm` (Sprint 5). | ~120 lines |
| `.scripts/test_reparse_corpus.py` | NEW | Tests the orchestrator on a tiny in-memory DB + 3-4 fixture HTML files. Tests cover: preview mode writes no DB rows, --confirm mode writes correctly, fingerprint-stability Option A logic. | ~120 lines |

### Acceptance criteria

- Default mode (`--preview`) writes `.data/_reparse_corpus_preview.csv` with columns:
  - `fingerprint_old` (existing row, if any), `fingerprint_new` (after re-parse)
  - `ticker`, `date_old`, `date_new`, `director_old`, `director_new`, `shares_old`, `shares_new`, `price_old`, `price_new`
  - `action` — one of `unchanged` / `update_in_place` / `new_insert` / `obsolete_skip`
  - `notes` — free-text reason
- Preview mode also prints to stdout:
  - Total cached HTML files processed
  - Count by `action`
  - Sample of 5 `update_in_place` diffs (existing row → proposed row)
  - Sample of 5 `new_insert` rows (would be added)
- `--confirm` mode is gated by a flag check: requires the preview CSV to have a `signed_off_by` line in the header (Sprint 5 enforces).
- Option A fingerprint stability logic: when `(date, ticker, type, shares, price)` match but director differs, the action is `update_in_place` (not `new_insert`).
- Excluded issuers (B-011's `is_excluded_issuer = 1`) are skipped — no IT/CEF rows are re-introduced.
- All 4 new unit tests pass. Full project test suite still passes.

### Zone discipline

- Writing the orchestrator + unit tests = Claude (Zone A).
- Running the preview generator on the LIVE corpus = **Rupert in Sprint 5** (Zone B — reads the live DB).
- Claude runs the unit tests only (synthetic fixtures, no live data).

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Preview CSV is too large for Rupert to eyeball (4,189 pending entries × variants) | Preview prints only sample diffs to stdout — full CSV is for grep'ing. |
| Option A logic accidentally merges genuinely-different rows | Test fixture: seed two rows that differ ONLY in director — assert one becomes `update_in_place`. Test fixture: seed two rows differing in shares OR price — assert they become separate `new_insert` rows. |
| Re-parse takes too long on the full corpus | Sprint 5 measures wall-clock. If >5 min, profile + add chunking. |

### Stage gate

**Internal QA only.** Claude reports:
- 4 new tests pass
- Full suite passes
- Preview CSV column schema documented

Rupert says "go to Sprint 4".

---

## Sprint 4 — Preview gate (Rupert runs)

### Goal

Generate the live preview CSV and **Rupert reviews it before any live DB writes**. This is the mandatory pre-live sign-off per backlog AC #4. It's also where Rupert locks in Option A vs B for fingerprint stability.

### Prerequisites

- Sprint 3 complete + signed off.
- DB backup `.data/directors.db.pre-b001.bak` still verifiable.

### Paste-and-run command for Rupert

```powershell
cd C:\Dev\DirectorsDealings

# Step 1 — confirm the backup is still intact
python -c "import sqlite3; r = sqlite3.connect('.data/directors.db.pre-b001.bak').execute('PRAGMA integrity_check').fetchone(); print('backup integrity:', r[0])"

# Step 2 — generate the preview (NO DB writes — safe to run)
python .scripts\reparse_corpus.py --preview | Tee-Object -FilePath .data\_reparse_corpus_preview.stdout.txt

# Step 3 — review the preview CSV
Import-Csv .data\_reparse_corpus_preview.csv | Group-Object action | Select-Object Count, Name
```

### Stage gate — Rupert reviews

**Read the stdout summary and the CSV. Sign off on:**

1. **Counts look right.** `unchanged` is most rows. `update_in_place` is at most ~300 (the director-name fixes). `new_insert` is the multi-row recovery — should be tens to low hundreds. `obsolete_skip` should be near zero.
2. **Sample `update_in_place` diffs look right.** Each one should be a director-name correction — the "before" director string contained `\n` or company-name bleed; the "after" is a clean director name.
3. **Sample `new_insert` rows look right.** They should be the previously-truncated rows from multi-row tables (DRIPs, SAYE, year-end compliance).
4. **No false positives.** Spot-check 3 random `new_insert` rows by opening the source filing in Investegate — confirm the row is real (not parser hallucination).
5. **Option A vs B decision.** Default to Option A (update-in-place). If you want B, flag now.

**Approval mechanism:** Rupert adds a line at the top of the preview CSV:
```
# signed_off_by: rupert
# signed_off_at: 2026-05-19T...
# option: A
```
Sprint 5's `--confirm` flag verifies this header is present.

If sign-off → proceed to Sprint 5.
If concerns → paste back to Claude; iterate on parser or reparse logic.

---

## Sprint 5 — Apply reparse + re-run pipeline (Rupert runs)

### Goal

Apply the re-parse to the live DB, then re-run the full pipeline so today's stuck filings + any other recovered rows produce signals and appear in the dashboard.

### Prerequisites

- Sprint 4 signed off (preview CSV has `signed_off_by` header).
- DB backup still intact (verified once more).

### Paste-and-run command for Rupert

```powershell
cd C:\Dev\DirectorsDealings

# Step 1 — extra safety backup just before the live write
Copy-Item .data\directors.db .data\directors.db.pre-b001-apply.bak -Force
python -c "import sqlite3; r = sqlite3.connect('.data/directors.db.pre-b001-apply.bak').execute('PRAGMA integrity_check').fetchone(); assert r[0]=='ok', 'safety backup is corrupt!'"

# Step 2 — apply re-parse with confirm (Zone B — writes DB)
python .scripts\reparse_corpus.py --confirm 2>&1 | Tee-Object -FilePath .data\_reparse_corpus_apply.log

# Step 3 — re-run the dependent pipeline stages so signals + dashboard reflect new rows
python .scripts\eval_signals.py
python .scripts\backtest.py
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py

# Step 4 — verify audit invariants still pass
python .scripts\audit_dates.py
```

### Acceptance criteria — Sprint 5 reports

1. **Reparse log shows:**
   - `applied: N` (count of rows actually modified)
   - `update_in_place: M` (director-name corrections)
   - `new_insert: K` (multi-row recoveries)
   - `errors: 0`
2. **Eval signals reports:** new signals fired (e.g. `+5 t1_ceo_cfo_buy` if recovered rows trigger T1).
3. **Backtest succeeds:** `_backtest_results.csv` regenerates, `rows_written > 0`.
4. **Audit dates:** All 5 invariants PASS. `OVERALL: PASS`.
5. **No errors** in any step. Exit code 0 throughout.

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Re-parse introduces a regression in existing transactions | Step 1 safety backup. Rollback: `Copy-Item .data\directors.db.pre-b001-apply.bak .data\directors.db -Force` |
| FUSE truncates the DB mid-write | `_atomic_write_json` is text only; the DB writes are SQLite which uses its own transaction journal — safer than the JSON path. Worst case: integrity check fails after, restore from backup, investigate. |
| `eval_signals.py` or `backtest.py` crash on the modified data | Step 1 safety backup is the rollback. Claude inspects the error and patches. |

### Stage gate

If all 4 ACs pass → proceed to Sprint 6.
If anything fails → restore from `.data/directors.db.pre-b001-apply.bak` and diagnose.

---

## Sprint 6 — Integration verification (Rupert runs)

### Goal

Final verification: confirm today's previously-stuck filings now appear in the dashboard, all integrity checks pass, validation SQL returns zero bad rows.

### Prerequisites

- Sprint 5 complete and reported clean.

### Paste-and-run sequence

```powershell
cd C:\Dev\DirectorsDealings

# Validation 1 — no director name has \n or contains "plc"
python -c "
import sqlite3
c = sqlite3.connect('.data/directors.db')
r = c.execute(\"SELECT COUNT(*) FROM transactions WHERE director LIKE '%plc%' OR INSTR(director, CHAR(10)) > 0\").fetchone()
print(f'Dirty director rows: {r[0]}  (expected: 0)')
"

# Validation 2 — filing 9541612 has 4 transactions (the multi-row reproducer)
python -c "
import sqlite3
c = sqlite3.connect('.data/directors.db')
r = c.execute(\"SELECT COUNT(*) FROM transactions WHERE url LIKE '%9541612%'\").fetchone()
print(f'Filing 9541612 row count: {r[0]}  (expected: 4)')
"

# Validation 3 — pending count dropped
python -c "
import json
n = len(json.load(open('.scripts/_pending_review.json')))
print(f'Pending review count: {n}  (was ~4,189 before — should be substantially lower)')
"

# Validation 4 — today's signals_today_count
python -c "
import json
d = json.load(open('dashboard/data/dealings.json'))
print(f'signals_today_count: {d[\"signals_today_count\"]}  (was 0 yesterday — should now be >0 if today had ingestible firings)')
"

# Validation 5 — visual: open the dashboard
Start-Process http://localhost:5000/
```

### Acceptance criteria — must ALL pass

| # | Check | Status |
|---|---|---|
| 1 | Validation 1: zero dirty director rows | ⏳ |
| 2 | Validation 2: filing 9541612 has exactly 4 rows | ⏳ |
| 3 | Validation 3: pending count substantially lower | ⏳ |
| 4 | Validation 4: today's `signals_today_count > 0` (if any filings ingestible) | ⏳ |
| 5 | Validation 5: dashboard renders + Today tab shows new firings | ⏳ |
| 6 | All ~154 unit tests still pass | ⏳ |
| 7 | `audit_dates.py` OVERALL: PASS | ⏳ |

If all 7 are green → **B-001 + B-004 is COMPLETE.**

Backlog item is marked done. Memory updated. Pending pile shrinks (probably from 4,189 to ~1,000 — the bundled-multi-PDMR rows remain since the parser still refuses to split those by spec).

### After Sprint 6

Optional follow-up (not in this sprint plan, but worth flagging):
- **Schedule weekly re-parse.** Now that the parser is fixed, the pending pile shouldn't grow as fast. But cache files keep accumulating. A weekly `reparse_corpus.py --confirm` keeps the corpus fresh as new template variants appear.
- **Refresh button could trigger reparse_corpus.py automatically** if pending grows above N% of total. Backlog candidate for next iteration.

---

## Quick reference — sprint dependency graph

```
  Sprint 1 (Foundation + tests — TDD)
       │
       ▼
  Sprint 2 (Parser rewrite — Claude)
       │
       ▼
  Sprint 3 (Reparse orchestrator — Claude)
       │
       ▼
  Sprint 4 (Preview gate — Rupert reviews CSV)
       │
       ▼
  Sprint 5 (Apply + pipeline re-run — Rupert)
       │
       ▼
  Sprint 6 (Integration verification — Rupert)
       │
       ▼
   B-001 + B-004 complete
   Pending pile drops by ~3,000 rows
   Today's stuck filings recovered
```

Sprints 1-3 can be paused indefinitely (no live data touched). Sprints 4-6 should run as a tight sequence (gap-day between 4 and 5 is fine; gap between 5 and 6 should be minimal because Sprint 5 modifies live data).
