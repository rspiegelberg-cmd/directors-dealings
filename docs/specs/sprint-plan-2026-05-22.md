# Sprint plan — 2026-05-22

> Sprint 7. Carries the two Sprint 6 items that weren't reached
> (B-023, B-015), adds the one regression-survivor from Sprint 6's
> Windows test run (B-052), and folds in two dashboard polish items
> Rupert flagged 2026-05-22 (B-053, B-054).

**Companion docs:**
- [`sprint-plan-2026-05-20.md`](sprint-plan-2026-05-20.md) — previous plan; full backlog index lives there and is still the source of truth for everything below.
- [`code-review-2026-05-20.md`](code-review-2026-05-20.md) — origin of the H/M/L items folded into Sprint 6.
- [`../backlog.md`](../backlog.md) — living issue list (assume Rupert keeps this in sync; this plan is the delta).

**Estimate units:** Same as previous plans.
- **Rupert-time** — wall-clock attention.
- **Gates** — count of mandatory sign-off pauses.
- **Risk** — Low / Medium / High.

---

## Section 1 — Backlog updates since 2026-05-20

### Shipped 2026-05-21 / 2026-05-22 (Sprint 6 + hotfix)

| ID | Title | Source |
|----|-------|--------|
| B-032 | try/except + `MigrationError` around schema migrations (`db.py`) | code-review M-1 |
| B-035 | fsync before rename in `db_health.backup()` — `rb+` mode for Windows | code-review L-1 |
| B-037 | `repair_dates._insert_transaction` now routes through `db.upsert_transaction` (populates `role_normalized`) | code-review L-3 |
| B-043 | ASCII-only sweep on `check_announced_at_coverage.py` + `repair_pending_review.py` | post-Sprint 4 |
| B-044 (P-A) | Lookahead-bias runtime guard in `detect_clusters.detect()` (`as_of <= today`) | B-040 audit Gate 1 |
| H-C | `db.connect()` moved inside try-block — `fetch_sectors.py` | B-040 audit Gate 1 |
| H-D | `db.connect()` moved inside try-block — `detect_clusters.py` | B-040 audit Gate 1 |
| M-F | `_to_epoch` wraps strptime `ValueError` with diagnostic — `fetch_prices.py` | B-040 audit Gate 1 |
| L-B | fsync added to `llm_cost._save()` atomic write (`rb+` mode) | B-040 audit Gate 1 |
| L-C | `_to_date` length guard — `detect_clusters.py` | B-040 audit Gate 1 |
| HOTFIX | `os.fsync()` open mode `"rb"` → `"rb+"` — Windows raises EBADF on read-only handles | Sprint 6 Windows test run |

All 11 verified green on Windows: `python -m unittest discover -s .scripts -p "test_*.py"` → 266 tests OK in ~5s.

### New items added 2026-05-22

| ID | Title | Severity | Source |
|----|-------|----------|--------|
| B-045 | LLM body truncated silently at 8,000 chars (`llm_parser.py:133`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-046 | `_fingerprint` import failure surfaces too late (`llm_parser.py:287`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-047 | LLM cost ledger: O(n) file I/O — load+save per API call (`llm_cost.py:110`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-048 | Phantom run records when `run_id` missing (`llm_cost.py:114`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-049 | `network_calls` counter inflated by retries (`fetch_prices.py:280`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-050 | CLI HTML read/write doesn't pin encoding (`parse_pdmr.py:1280`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-051 | 2-digit-year rejection prints to stderr instead of returning warning (`parse_pdmr.py:224`) | MEDIUM | B-040 audit Gate 1 — deferred |
| B-052 | ResourceWarning: `csv_path.open` without `with`-block (`export_dashboard_json.py:1848`) | LOW | Sprint 6 Windows test output |
| B-053 | Company-page chart markers need to be more visible (`render_company.py`) | LOW | Rupert 2026-05-22 |
| B-054 | Add 30d option to cohort cut dropdowns on performance page (`render_performance.py` + downstream) | LOW | Rupert 2026-05-22 |

P3 candidates from B-040 audit (M-G empty CSV silent load, L-A bs4 silent fallback) parked in the existing P3 list rather than getting their own B-NNN — promote to backlog only if they bite.

---

## Section 2 — Prioritized open items (delta)

The full prioritized list lives in [`sprint-plan-2026-05-20.md`](sprint-plan-2026-05-20.md) Section 2. The delta:

**Removed from P0/P1 (now shipped):** B-032, B-035, B-037, B-043, B-026, B-027, B-028, B-029, B-030, B-031, B-033, B-034, B-036, B-038, B-039 — all closed in Sprints 4-6.

**Still P1:**

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-023 | Bundled-PDMR detection doesn't fire on AAL "PCA" pattern | Data accuracy — AAL 8950385 yields 1 row when it should yield 3 | S (~30 min) | Low |
| B-015 | Sweep cannot safety-check rows without `announced_at` | Unblocks 6 stranded pending rows | M (~1 hr) | Low |

**Newly P2 (dashboard polish + hygiene):**

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-053 | Chart markers more visible | Visual clarity on the company page price chart | XS-S (~20 min) | Low |
| B-054 | 30d cohort cut option | Closer-to-current performance lens on the dashboard | M (~1 hr, multi-file) | Low |
| B-052 | ResourceWarning leak | Noisy test output on Windows; latent handle leak | XS (~10 min) | Low |

**P3 carry-forward (unchanged):** B-007, B-014, B-018, B-019, B-020, B-022, B-041, B-042, plus the seven new B-045 through B-051 from the B-040 audit.

---

## Section 3 — Sprint 7

### Sprint 7 — Sprint 6 carryover + dashboard polish (Rupert time: ~2-2.5 hrs, gates: 1, risk: Low)

**Goal:** Close the two Sprint 6 carryovers (B-023, B-015), fix the one regression-survivor (B-052), and ship two dashboard improvements Rupert flagged 2026-05-22 (B-053, B-054). No DB schema changes, no commit-lifecycle edits, no FUSE-vulnerable patterns.

**Scope (5 items)**

| ID | Title | Code size | Sequence |
|----|-------|-----------|----------|
| B-052 | ResourceWarning csv_path.open without `with`-block | XS | 1st — quick win, mechanical |
| B-053 | Company-page chart marker visibility | XS-S | 2nd — visual change, single file |
| B-054 | 30d cohort cut option | M | 3rd — biggest item, multi-file |
| B-023 | Bundled-PDMR detection AAL "PCA" pattern | S | 4th — parser dig + new unit test |
| B-015 | announced_at sweep for 6 stranded rows | M | 5th — Zone B run, do last |

**Sequence within sprint**

1. **B-052 first.** Wrap `csv_path.open(encoding="utf-8")` in a `with` block. One line edit at `export_dashboard_json.py:1848`. Verify ResourceWarning is gone via `python -W error::ResourceWarning -m unittest test_export_dashboard_json`.

2. **B-053.** In `.scripts/dashboard/render_company.py`, the marker datasets currently use `radius: st.r` driven by a style table (around line 590-600). Bump radius by ~50-75% (e.g., 4 → 6, 5 → 8) and bump `pointHoverRadius` proportionally so hover doesn't feel jumpy. Sanity-check on a real company page that markers don't overlap badly on clustered dates. Decision points: keep current colours / shapes — only size changes. Designer-agent optional if Rupert wants a UX pass on overlap behaviour.

3. **B-054.** Three-touch change:
   - `render_performance.py`: add `("30d", "30 d")` as the FIRST entry in `LOOKBACK_LABELS` (line 170). Decide whether to bump `COHORT_DEFAULT_LOOKBACK` to `"30d"` or keep `"90d"` — Rupert's call at Gate 1 below.
   - `export_dashboard_json.py`: extend the cohort aggregation to compute 30-day windows alongside the existing 90d/6m/1y/all (find the lookback loop and add the case).
   - `build_dashboard.py`: drill-down filename pattern already accepts `{key}` — verify nothing hard-codes `90d` specifically.
   - Tests: `test_export_dashboard_json.py` + `test_render_performance_v2.py` will need new assertions for the 30d series. Add them; run `unittest discover` until green.

4. **B-023.** Trace `_bundled_name_warning` in `parse_pdmr.py` against AAL 8950385's cached HTML (in `.scripts/_scrape_cache/`). The bundled-detection regex likely doesn't match the "PCA" variant. Add the variant + a unit test asserting AAL 8950385 yields 3 rows. Now possible because Sprint 5 refreshed the test suite.

5. **B-015.** Derive `announced_at` for sweep input from cached HTML. The 6 stranded pending rows currently fail the safety guard because their pending entry lacks `announced_at`. Read the cached HTML on the fly inside the sweep guard, fall back to the date-from-filename heuristic if HTML parsing fails. Rupert runs `python .scripts/run_pending_sweep.py` from PowerShell to clear the 6 rows. Acceptance: pending row count goes from N to N-6 (or N-fewer if some rows can't be derived).

**Gates**

- **Gate 1 (Rupert, mandatory): scope confirmation after B-054 design but BEFORE B-054 code.** Two decisions to make: (a) bump default lookback to 30d or keep 90d default with 30d as opt-in? (b) does B-054 need a designer-agent UX pass on dropdown ordering (30d first vs last)? Without this gate B-054 risks scope creep into "redesign the cohort dropdown."

**Rupert-time breakdown**

- 5 min: kick-off — capture a `.bak` snapshot (manual safety net per habit).
- 15 min: Gate 1 — review B-054 design call before coding.
- 15 min: eyeball B-053 marker change on a real company page.
- 15 min: review B-023 parser fix + new test.
- 30 min: run the Zone B sweep (B-015) and inspect the cleared pending rows.
- 15 min: final smoke (`unittest discover`, `start.bat` Refresh, dashboard health-panel check).

Total: ~95-110 min on the Rupert side. Stays within the ≤2 hr discipline.

**Definition of done**

- `unittest discover` green on Windows (266+ tests; new tests for B-023 + B-054 push the count up).
- `export_dashboard_json.py:1848` ResourceWarning eliminated.
- Company page renders with clearly visible markers (Rupert-eyeball ack).
- Performance page dropdown shows 30d as an option, and selecting it renders 30-day aggregates without errors.
- AAL filing 8950385 yields 3 rows after `python .scripts/reparse_corpus.py` (or fresh `start.bat` Refresh).
- Pending row count decreases by up to 6 after B-015's sweep.
- `audit_dates.py` still green.

**Risk discussion**

Low across the board. No DB schema changes. No commit-lifecycle edits. No FUSE-vulnerable patterns. The biggest blast-radius item is B-054 because it touches four files plus tests — mitigation is Gate 1 to lock the scope before code. B-053 is reversible by changing one constant. B-015 takes a `.bak` per the C-2 pattern in `repair_dates.py` already.

**Kickoff checklist**

- [ ] Sprint 6 confirmed shipped + Windows-green (already done 2026-05-22).
- [ ] Manual `.bak` snapshot at sprint start.
- [ ] No Zone B pipelines mid-run.
- [ ] Decision on B-054 default-lookback question reached at Gate 1 before coding starts.

---

## Recommended next move

**Sprint 7 is the natural next session.** All five items are low-risk and the dashboard polish (B-053, B-054) directly serves the live-use surface, which has been your daily driver since 2026-05-19. The two carryovers (B-023, B-015) close out unfinished Sprint 6 business cleanly.

Stretch consideration: if the LLM hygiene items (B-045 through B-048) start mattering — e.g., you scale up LLM usage, or the cost ledger grows large enough that the O(n) file I/O bites — bundle them as Sprint 8. Until then they stay parked.
