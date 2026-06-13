# Sprint plan — Performance page redesign v1 (backend)

**Author:** Claude
**Date:** 2026-05-18
**Status:** ready to execute, one sprint at a time
**Source documents:**
- Spec: `docs/specs/performance-page-redesign-v1.md` (v1.2)
- Backend plan: `docs/specs/performance-page-redesign-v1-backend-plan.md`
- QA review: `docs/specs/performance-page-redesign-v1-qa-review.md`
- Mockups: `docs/specs/mockups/performance-*-preview.html`

## Locked decisions from Rupert (2026-05-18)

1. **Migration:** dual-emit (legacy `cohorts` + new `cohorts_v2` in `signals.json` during transition)
2. **`outlier_flag` on bucket rows:** emit `true` when any firing in the bucket has `|car| > 200%`
3. **Performance bound:** target exporter completes in <10s for ~5,000-row backtest CSV; relax only if corruption/truncation observed
4. **"Chairman" classification:** accept current regex precedence (Non-Executive Chairman → NED, bare Chairman → other_exec)
5. **Multi-signal tier badge:** SIGNAL_ORDER precedence (`t0 > t1 > t2 > t3 > t4 > s1 > f1`); highest-conviction tier wins
6. **Company name source:** `transactions.company` (most recent row per ticker)

## Working principles

- **Each sprint is one paste-and-run unit.** Don't start the next one until the current sprint's stage gate is signed off.
- **Zone discipline (CLAUDE.md):** Claude does Zone-A (code, tests, diagnostics that don't write to `.data/`) autonomously. Anything that writes `.data/`, the DB, or `.json` outputs Rupert runs from PowerShell — Claude provides the exact command.
- **Mandatory truncation check** after every file write — Read tool, not bash. Reads from FUSE bash can be stale.
- **One sprint can be paused indefinitely** — sprints are designed so the codebase remains shippable between them. The dual-emit migration (Sprint 5) is the only sprint that materially changes user-visible behaviour, and even that only adds data — nothing is removed until front-end work is done in a future plan.

---

## Sprint overview

| # | Sprint | Goal | Who runs | Stage gate? | Est. size |
|---|---|---|---|---|---|
| 1 | Foundation | Role classifier + cohort-table helper, both fully unit-tested | Claude | Yes — Rupert reviews tests + classifier output | M (~250 lines new code, ~150 lines tests) |
| 2 | Role diagnostic | Rupert eyeballs the real-corpus role classification before any payload code lands | Rupert | Yes — Rupert sign-off, written to a small approval marker | S (one script, ~80 lines; one PowerShell run) |
| 3 | Bucket payload | First drill-down JSON file end-to-end, isolates the shared `build_drill_payload` helper | Claude | No (rolls into Sprint 5 gate) | M (~200 lines new code, ~150 lines tests) |
| 4 | Role + Sector payloads | Both remaining drill-down JSONs; sector adds benchmark lookup | Claude | No (rolls into Sprint 5 gate) | M (~250 lines new code, ~200 lines tests) |
| 5 | Dual-emit + wire-up | Modify main exporter to emit legacy + v2 cohorts AND the three new files. Tests verify side-by-side coexistence | Claude | Yes — Claude runs full test suite, then Rupert kicks off Sprint 6 | M (~120 lines exporter mods, ~150 lines tests) |
| 6 | Integration smoke | Rupert runs the full pipeline end-to-end, verifies all four output files + performance + no DB corruption | Rupert | **Yes — this is THE stage gate before front-end work begins** | S (paste-and-run; no code) |

Total: ≈ 1,200 lines of new code + ≈ 650 lines of tests across the six sprints. Roughly 2-3 sessions of work depending on how cleanly each gate goes.

---

## Sprint 1 — Foundation: classifier + shared helper

### Goal

Build the two pure-function components that the three payload builders all depend on, with no DB or JSON-write side effects. Everything Zone-A, everything Claude can run + verify itself.

### Prerequisites

- None. This sprint can start immediately.

### Inputs (what Claude reads)

- `docs/specs/performance-page-redesign-v1.md` §5.4 (role classifier locked precedence)
- `docs/specs/performance-page-redesign-v1-backend-plan.md` §2, §3 (function signatures + 12-case test matrix)
- `.scripts/export_dashboard_json.py` (existing patterns to match — helpers, atomic writes, naming conventions)

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/classify_role.py` | NEW | `classify_role(role_class, role_str) -> 'ceo_cfo' \| 'other_exec' \| 'ned' \| None` per locked precedence | ~80 lines |
| `.scripts/test_classify_role.py` | NEW | Full 12-case unit test matrix from backend plan §3 + 3 edge cases | ~120 lines |
| `.scripts/export_dashboard_json.py` | MODIFY | Add `build_cohort_table(rows, group_fn, label_fn, horizons, lookbacks)` helper near top of file | ~100 lines added |
| `.scripts/test_cohort_table.py` | NEW | Unit tests for `build_cohort_table` — fixed-input, deterministic | ~80 lines |

### Acceptance criteria

- `python -m unittest .scripts/test_classify_role.py` passes 15/15
- `python -m unittest .scripts/test_cohort_table.py` passes (target 8 tests)
- `classify_role` returns `None` (not error) on unmappable strings; never raises
- `build_cohort_table` is pure — same input always returns same output, no DB access
- Both new files added to the project's test suite discovery

### Zone discipline

All Zone A. Claude can:
- Write the new files (Edit/Write)
- Run the unit tests from bash (`python -m unittest`) — safe; no DB or `.data/` writes
- Verify file integrity via Read tool after every write

### Stage gate

**Rupert reviews:**
- The classifier file (does the precedence read right? are there obvious string patterns missing?)
- The 12-case test matrix output (was anything mis-classified?)
- Brief test-pass report

Approval mechanism: Rupert says "go to Sprint 2".

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Regex fails on a real corpus title pattern we didn't anticipate | Sprint 2's corpus diagnostic catches this before any payload code ships |
| `build_cohort_table` over-abstracts and bakes in the wrong shape | 8 unit tests with worked examples force a concrete shape early |

---

## Sprint 2 — Role diagnostic gate (Rupert runs)

### Goal

Before any payload code uses `classify_role` on real data, prove the classifier behaves sensibly on the actual corpus. This is the mandatory gate from spec §5.4.

### Prerequisites

- Sprint 1 complete and signed off.

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/classify_role_diagnostic.py` | NEW | Reads `_backtest_results.csv` (or `transactions` via `db.py`), runs `classify_role` on every row, prints a frequency table of (raw role_str → bucket) sorted by count. Highlights any rows that fall to `None` for Rupert to review. | ~100 lines |

### Acceptance criteria

- Diagnostic outputs three tables: counts per bucket; top 30 most-common raw role strings + their classification; full list of rows classified as `None` (the catch-all).
- Output is plain-text, readable in a terminal, no charts.
- Rupert can spot mis-classifications by eye in < 5 minutes.

### Zone discipline

- Diagnostic READS from CSV / DB → Zone B → **Rupert runs from PowerShell**, Claude must not run it from bash.

### Paste-and-run command for Rupert

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\classify_role_diagnostic.py | Tee-Object -FilePath .data\_classify_role_diagnostic.txt
```

### Stage gate

Rupert reads the output. Acceptable outcomes:
- ✅ Counts look right (CEO/CFO + Other exec + NED ≈ total exec firings; `None` bucket is < 5% and contains only weird edge cases) → approve, proceed to Sprint 3
- ⚠️ Some surprising classifications visible → flag back to Claude, who patches the classifier in Sprint 1's file and Rupert re-runs the diagnostic
- ❌ Major mis-bucketing (e.g., a CEO landing in NED) → halt; revisit spec §5.4

Approval marker: `.data\_classify_role_diagnostic.txt` retained as evidence + Rupert says "go to Sprint 3".

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| The corpus has structural surprises (e.g., role strings in title-case the regex misses) | Diagnostic surfaces these before any downstream code is built |
| Diagnostic takes too long to run | Backend plan §3 estimates < 5s for current 1,200-row corpus; if slower, profile and short-circuit |

---

## Sprint 3 — Bucket payload builder

### Goal

Build the first drill-down JSON file end-to-end. This sprint isolates the shared `build_drill_payload()` helper so Sprints 4 picks it up cheaply.

### Prerequisites

- Sprint 1 complete (classifier + cohort-table helper exist).
- Sprint 2 complete (corpus diagnostic approved — confirms the role classifier is trustworthy; not directly used here but unblocks Sprint 4).

### Inputs (what Claude reads)

- Backend plan §2.1 (function signatures), §4.1 (bucket JSON shape), §4.4 (size estimate)

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/export_dashboard_json.py` | MODIFY | Add `build_drill_payload(rows, filter_fn, group_fn, label_fn, scope_note, outlier_threshold=2.0)` — the shared helper, produces the `top_firings` / `bottom_firings` / `rollup` triplet | ~80 lines added |
| `.scripts/export_dashboard_json.py` | MODIFY | Add `build_bucket_payload(rows)` — calls helper with bucket-specific filters and labels; emits `outlier_flag: true` per Rupert Q2 when any firing has `|car| > 200%` | ~60 lines added |
| `.scripts/test_drill_payload.py` | NEW | Tests for both: shape, ordering (top 10 = CAR desc, bottom 10 = asc), outlier_flag rule, "fewer than 10 losers" edge case (returns what's available + sets metadata flag), label mapping | ~150 lines |

### Acceptance criteria

- `build_bucket_payload` returns a dict matching spec §5.2 shape exactly (validated by a schema-shape test)
- Function is pure — no DB or file I/O; takes pre-loaded rows
- Tests cover: normal case, fewer-than-10-losers, fewer-than-10-winners, zero firings, single-bucket-dominates, outlier threshold trip
- `python -m unittest .scripts/test_drill_payload.py` passes
- The payload includes `benchmark_car_pct` scalar at the cohort level only — no per-firing `bench_car` (per spec v1.2)

### Zone discipline

All Zone A. Claude builds + tests.

### Stage gate

None — bundled with Sprint 4 + 5 for review.

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| The "fewer than 10 losers" edge case has subtle off-by-one bugs | Three explicit edge-case unit tests (0 losers, 2 losers, 9 losers) |
| `outlier_flag` definition is fuzzy ("any firing with `|car| > 200%`") — what's the unit, % or fraction? | Test fixture pins the threshold to `2.0` in the code, comment explains it's "fraction, not percent" |

---

## Sprint 4 — Role + Sector payload builders

### Goal

Finish the two remaining drill-down JSON builders using the helper from Sprint 3.

### Prerequisites

- Sprint 3 complete (the shared helper exists).
- Sprint 2 approved (role classifier trustworthy).

### Inputs

- Backend plan §2.2-2.3, §4.2-4.3 (role + sector JSON shapes)
- Spec §5.4 (role mapping), §3.3 (sector-specific benchmark)

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/export_dashboard_json.py` | MODIFY | Add `resolve_sector_benchmark(sector, tickers_meta) -> str` — returns sector's `benchmark_symbol` or `'^FTAS'` fallback; emits the resolved symbol so FE can disclose fallback | ~40 lines added |
| `.scripts/export_dashboard_json.py` | MODIFY | Add `build_role_payload(rows)` — uses `classify_role` + `build_drill_payload` | ~50 lines added |
| `.scripts/export_dashboard_json.py` | MODIFY | Add `build_sector_payload(rows, tickers_meta)` — uses sector benchmark resolver + `build_drill_payload`. Emits ALL sectors (FE slices top 3 + bottom 2; do not slice server-side per plan §10.7) | ~70 lines added |
| `.scripts/test_drill_payload.py` | MODIFY | Extend with tests for role and sector builders | ~100 lines added |

### Acceptance criteria

- Both payloads match spec §5.2 shape (`roles` / `sectors` top-level keys, per-cohort sub-objects)
- Role payload has exactly 3 keys: `ceo_cfo`, `other_exec`, `ned`. The `None` (T4 catch-all) bucket is excluded
- Sector payload includes resolved `benchmark_symbol` so FE can disclose fallback
- Multi-signal firing tier-badge tests use `SIGNAL_ORDER` precedence (Rupert Q5) — `t0 > t1 > t2 > t3 > t4 > s1 > f1`
- Company name comes from `transactions.company` most-recent (Rupert Q6) — test pins this behaviour
- All tests pass

### Zone discipline

All Zone A. Claude builds + tests.

### Stage gate

None — bundled with Sprint 5 for review.

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Some sectors have no `benchmark_symbol` mapping in `tickers_meta`, fallback to FTSE A-S happens silently | Resolved symbol included in JSON output → front-end can disclose |
| Multi-signal precedence test depends on existing `SIGNAL_ORDER` constant location | Import directly from `render_helpers.py` where it lives; test imports from same path |
| Company name lookup adds a per-ticker SQL query → exporter slowdown | Single batch query at the top of `build_sector_payload`, cache results in-process |

---

## Sprint 5 — Dual-emit migration + wire-up

### Goal

Integrate the three new payload builders into the main exporter entry point. The existing `cohorts.by_value_bucket` and `cohorts.by_sector` keep emitting in the old shape (so nothing breaks for the current front-end), AND the new `cohorts_v2` block plus three new JSON files are written.

### Prerequisites

- Sprints 1, 3, 4 all complete (the four helpers + three builders exist and are tested).

### Inputs

- Backend plan §7.2 (dual-emit migration strategy)
- Existing `_atomic_write_json` pattern in `export_dashboard_json.py`

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/export_dashboard_json.py` | MODIFY | Modify main entry point to: (a) keep legacy `cohorts.by_value_bucket` + `cohorts.by_sector` emit unchanged; (b) add new `cohorts_v2` block in `signals.json` with all three tiles in new shape; (c) call `build_bucket_payload` / `build_role_payload` / `build_sector_payload` and write three new files via `_atomic_write_json` | ~120 lines modified |
| `.scripts/test_export_dashboard_json.py` | MODIFY (or NEW if absent) | Tests that prove: legacy `cohorts` key still present; `cohorts_v2` key present with three sub-tiles; three new output files written; both shapes coexist in same `signals.json` without one overwriting the other | ~150 lines |

### Acceptance criteria

- After exporter runs, `signals.json` contains BOTH `cohorts.by_value_bucket` (old shape, single scalar per bucket) AND `cohorts_v2.by_value_bucket` (new horizon × lookback × rows shape)
- Same for `cohorts.by_sector` (legacy) + `cohorts_v2.by_sector` (new)
- New: `cohorts_v2.by_role` (no legacy equivalent — fresh addition)
- Three new files written: `dashboard/data/performance_bucket.json`, `dashboard/data/performance_role.json`, `dashboard/data/performance_sector.json`
- File sizes within bounds (< 200 KB each at current data volume)
- `_atomic_write_json` used for all four writes (no partial writes if exporter crashes mid-run)
- Existing `render_performance.py` does not crash (it reads legacy `cohorts.by_value_bucket` — must still work)
- Full project test suite (`python -m unittest discover -s .scripts -p "test_*.py"`) passes

### Zone discipline

- Building + Claude-side unit tests = Zone A ✓
- Running the full exporter on real data = Zone B → **Rupert runs in Sprint 6**, not here

Claude **does not** run `export_dashboard_json.py` against the real DB during Sprint 5. Unit tests use synthetic fixtures only.

### Stage gate

**Claude reports to Rupert:**
- Full test suite output (must show 0 failures across all `.scripts/test_*.py`)
- Diff of modified `export_dashboard_json.py` (key changes only — not full file)
- Brief summary of the new shapes in `signals.json`

Rupert approves → proceed to Sprint 6 (integration smoke test on real data).

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| New code path takes the exporter over the 10s performance bound (Rupert Q3) | Sprint 6's integration test measures actual runtime; if breached, Sprint 5 reopens with profiling |
| Concurrent write to `signals.json` corrupts both legacy + v2 | `_atomic_write_json` pattern is already battle-tested in the project; no change to write path |
| FE crashes because it reads new shape that isn't ready yet | FE is in a separate plan; dual-emit means we never have to ship a broken intermediate to the current FE |

---

## Sprint 6 — Integration smoke test (Rupert runs)

### Goal

Prove the full pipeline works end-to-end on real data, with no corruption, no truncation, and within the performance bound. This is **THE stage gate** before any front-end work begins.

### Prerequisites

- Sprint 5 complete + signed off.

### Deliverables

None new. This is a test sprint, not a build sprint.

### Acceptance criteria — must ALL pass

1. **Exporter runs without error** end-to-end on the live DB
2. **All four files written:** `signals.json`, `performance_bucket.json`, `performance_role.json`, `performance_sector.json`
3. **JSON validity:** all four files parse as valid JSON (no truncation, no FUSE corruption)
4. **Schema:** `signals.json` has both `cohorts` (legacy) AND `cohorts_v2` (new) keys
5. **File sizes within bound:** each performance_* file < 200 KB
6. **Performance:** exporter total runtime < 10 seconds (Rupert Q3 bound; relax to 30s if corruption-free at slower speed)
7. **No DB corruption:** `python .scripts\db_health.py` passes after the exporter run
8. **No regression:** existing `dashboard/index.html` and `dashboard/performance.html` still render the same way they did before this work began (they read legacy `cohorts.*` keys, which are unchanged)
9. **Visual spot-check:** Rupert pastes one of the three new JSON files into a JSON viewer and confirms the shape matches spec §5.2

### Paste-and-run sequence for Rupert

Replace any existing pipeline commands with the explicit sequence below — Sprint 6 must be run as a clean sequence, not as part of `refresh_all.py`, to isolate any issues.

```powershell
# Step 1 — back up the DB before any pipeline run (project policy)
cd C:\Dev\DirectorsDealings
Copy-Item .data\directors.db .data\directors.db.bak.sprint6 -Force

# Step 2 — run the modified exporter against current data
$start = Get-Date
python .scripts\export_dashboard_json.py
$elapsed = (Get-Date) - $start
Write-Host "Exporter runtime: $($elapsed.TotalSeconds) s"

# Step 3 — verify all four output files exist + parse
Get-ChildItem dashboard\data\signals.json,
              dashboard\data\performance_bucket.json,
              dashboard\data\performance_role.json,
              dashboard\data\performance_sector.json | Format-Table Name, Length

python -c "import json; [json.load(open(f)) for f in ['dashboard/data/signals.json','dashboard/data/performance_bucket.json','dashboard/data/performance_role.json','dashboard/data/performance_sector.json']]; print('All four JSON files parse OK')"

# Step 4 — verify both legacy + v2 shapes coexist in signals.json
python -c "import json; d = json.load(open('dashboard/data/signals.json')); print('legacy cohorts:', list(d.get('cohorts', {}).keys())); print('cohorts_v2:', list(d.get('cohorts_v2', {}).keys()))"

# Step 5 — DB health check
python .scripts\db_health.py

# Step 6 — visual regression: open the existing dashboard
Start-Process dashboard\performance.html
```

### Stage gate

Rupert reads the output of all six commands and confirms:
- Runtime within bound (or acceptable if corruption-free at slower speed per Q3)
- All JSON parses
- Legacy + v2 cohorts both present in `signals.json`
- DB health passes
- Existing `performance.html` still renders correctly in the browser

If all checks pass → **backend redesign work is complete. Front-end work can now be planned in a separate sprint sequence.**

If any check fails → diagnose the offending sprint, fix, re-run Sprint 6.

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Exporter runtime regresses past 10s | Sprint 6 measures; if breached, profile and either accept (Q3 allows slower if corruption-free) or open a perf-fix sprint |
| Some sector has no benchmark_symbol → silent FTSE A-S fallback noise | The JSON includes the resolved symbol; Rupert can grep the output |
| FUSE writes truncate one of the four files mid-run | `_atomic_write_json` pattern + the project's auto-backup catch this; if it happens, restore from `directors.db.bak.sprint6` and rerun |
| `dashboard/performance.html` renders broken because it reads legacy `cohorts.by_value_bucket` and the new exporter changed the legacy shape | Sprint 5 acceptance criterion explicitly requires "legacy shape unchanged" — test must enforce this |

---

## After Sprint 6 — handoff to front-end

When Sprint 6's gate passes, this plan is **complete**. The next step is a separate sprint plan covering:
- `render_performance.py` rewrite (consume `cohorts_v2` shape, kill bar charts, add three tiles)
- New renderers: `render_performance_drilldown.py` (parameterised — one renderer for all three drill-down pages)
- Wire the new pages into `build_dashboard.py`
- Keyboard accessibility (`tabindex` + `role="link"` + Enter/Space handlers per spec §2.5)
- Smoke-test that every clickable ticker on every drill-down page resolves to an existing `companies/{TICKER}.html` file
- Eventually: remove the legacy `cohorts` keys from `signals.json` once nothing reads them

Front-end plan to be authored when Sprint 6 passes — not before, so it's built against the verified JSON shapes.

---

## Quick reference — sprint dependency graph

```
  Sprint 1 (Foundation)
       │
       ▼
  Sprint 2 (Diagnostic gate — Rupert)
       │
       ▼
  Sprint 3 (Bucket payload) ───┐
                               │
  Sprint 4 (Role + Sector) ────┤
                               │
                               ▼
                    Sprint 5 (Dual-emit + wire-up)
                               │
                               ▼
                    Sprint 6 (Integration — Rupert)
                               │
                               ▼
                       Backend work complete →
                       front-end plan begins
```

Sprints 3 and 4 are technically parallelisable since Sprint 4 only needs Sprint 1's classifier + Sprint 3's helper. But each is small enough that doing them sequentially keeps the work simple and one-step-at-a-time as Rupert prefers.
