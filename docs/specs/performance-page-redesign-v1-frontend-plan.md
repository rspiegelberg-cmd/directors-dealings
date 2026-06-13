# Sprint plan — Performance page redesign v1 (front-end)

**Author:** Claude
**Date:** 2026-05-19
**Status:** ready to execute, three sprints + one Rupert gate at the end
**Source documents:**
- Spec: `docs/specs/performance-page-redesign-v1.md` (v1.2)
- Backend plan (shipped 2026-05-19): `docs/specs/performance-page-redesign-v1-backend-plan.md`
- Backend sprint plan (shipped): `docs/specs/performance-page-redesign-v1-sprint-plan.md`
- Mockups (approved by Rupert 2026-05-19): `docs/specs/mockups/performance-*-preview.html`

## What's already shipped

Six backend sprints completed 2026-05-19. The exporter now emits:

- `signals.json` — legacy `cohorts.by_value_bucket` + `cohorts.by_sector` (UNCHANGED — render_performance.py still consumes these today) AND new `cohorts_v2` block with three tiles in spec §5.1 shape
- `dashboard/data/performance_bucket.json` (398 KB)
- `dashboard/data/performance_role.json` (1.04 MB)
- `dashboard/data/performance_sector.json` (934 KB)

Front-end work consumes these new shapes. Legacy code reading `signals.cohorts.*` stays working until the FE migration completes — that's the dual-emit safety net.

## Locked decisions from Rupert (2026-05-19)

1. **Sprint structure:** 3 sprints with one Rupert gate at the end (compressed vs backend's 6)
2. **Mockups approved as-is** — no design changes before implementation
3. **Visual preview pattern stays:** static mockups + production pages built from spec §1.5 and §2.5 Tailwind snippets
4. **Tech-debt cosmetics** (ResourceWarning at `csv_path.open()`, double `--verbose` summary print) — defer until after FE work ships

## Working principles

- **Each sprint is one paste-and-run unit.** Don't start the next one until the current sprint's internal QA gates are signed off.
- **Zone discipline (CLAUDE.md):** Claude does Zone-A (HTML / JS / Python rendering code, tests that don't write to `.data/`) autonomously. Anything that writes to `.data/` or the DB, Rupert runs from PowerShell.
- **Visual fidelity rule:** every rendered page must match the corresponding mockup at `docs/specs/mockups/`. Discrepancies get caught in the QA pass per sprint, not in Sprint 3 integration.
- **Mandatory truncation check** after every code write — Read tool, not bash. Bash FUSE cache is known unreliable.
- **No new pages live until Sprint 3 stage gate.** Pages render to `outputs/` but Rupert verifies via local http server in Sprint 3 before approving.

---

## Sprint overview

| # | Sprint | Goal | Who runs | Stage gate? | Est. size |
|---|---|---|---|---|---|
| FE1 | Cohort tiles | Refactor `render_performance.py` to render 3-tile cohort layout from `cohorts_v2`. Existing 2 tiles replaced; new "By director role" tile added. Lookback dropdown wired per tile. | Claude | Internal QA only | M (~250 lines, ~10 tests) |
| FE2 | Drill-down renderer + pages | Write `render_performance_drilldown.py` — ONE parameterised renderer for all three drill-down page types. Wire into `build_dashboard.py` so each cohort key produces one HTML page (4 + 3 + 11 = 18 new pages). | Claude | Internal QA only | L (~350 lines, ~15 tests) |
| FE3 | Integration smoke (Rupert runs) | Rupert runs the full pipeline + browser-inspects each new page. Visual diff vs mockups. Click-through to companies/{TICKER}.html verified. Keyboard accessibility check. | Rupert | **Yes — THE gate before declaring v1 done** | S (paste-and-run; no code) |

Total: ≈ 600 lines of new / modified code + ≈ 250 lines of tests across two Claude sprints. Roughly 1-2 sessions to FE1 + FE2 complete, then Rupert's integration pass.

---

## Sprint FE1 — Cohort tiles refactor

### Goal

Replace the existing 2-tile cohort section in `render_performance.py` with three tiles in spec §1 layout. Reads from `signals.cohorts_v2` (the new shape from Sprint 5). The rest of `performance.html` (per-signal scoreboard, diagnostic chart, model assessment, pending diagnostics) stays untouched.

### Prerequisites

- Backend Sprint 5/6 complete (cohorts_v2 in signals.json, three drill files exist). ✓
- Mockup approved (`docs/specs/mockups/performance-preview.html`). ✓

### Inputs (what Claude reads)

- `docs/specs/performance-page-redesign-v1.md` §1 (layout), §1.2 (N-band visuals), §1.5 (Tailwind snippets), §5.1 (cohorts_v2 shape)
- `docs/specs/mockups/performance-preview.html` (the design target — match it pixel-for-pixel where reasonable)
- `.scripts/dashboard/render_performance.py` (current state) — specifically `_cohort_value_section()` (line 153) and `_cohort_sector_section()` (line 204) which are being replaced

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/dashboard/render_performance.py` | MODIFY | Delete `_cohort_value_section` + `_cohort_sector_section`. Add `_cohort_tile(tile_id, title, scope_note, cohort_data, click_destination_pattern)` shared helper. Call it three times for value bucket / role / sector. Add lookback dropdown JS. | ~250 lines added, ~100 lines removed |
| `.scripts/dashboard/render_helpers.py` | MODIFY | Add `n_band_cell(n)` helper — renders N with amber ⚠ when N<20, gray italic when N=0 (per spec §1.2). Reuse across all three tiles. | ~30 lines added |
| `.scripts/test_render_performance_v2.py` | NEW | Tests for the 3-tile cohort section: shape, N-band rendering, lookback dropdown markup, click destinations, scope_note attachment. | ~150 lines |

### Acceptance criteria

- Rendered `performance.html` reads `cohorts_v2` block, NOT legacy `cohorts.by_value_bucket` / `cohorts.by_sector`
- Three tiles render in a `grid-cols-1 md:grid-cols-3` layout (matches mockup)
- Each tile has: H3 title, optional scope_note sub-line, lookback `<select>` (90d / 6m / 1y / all), table with `Bucket/Role/Sector | N | Hit% | Median CAR` columns, footer caption
- N-band rule from spec §1.2 applied:
  - N=0 → row gray italic, em-dashes throughout
  - N<20 → amber ⚠ glyph next to N
  - N≥20 → normal styling
  - bucket key absent in JSON → row entirely omitted (do NOT render `—` rows)
- Each data row carries `class="clickable"` + `data-href="performance-{bucket|role|sector}.html?key={...}"` + `tabindex="0"` + `role="link"` + `aria-label` (per spec §2.5 keyboard accessibility minima)
- Lookback dropdown dispatches a `lookbackChange` custom event scoped by `data-tile` attribute — renderer hooks each tile's lookback to refetch the right `cohorts_v2[tile][horizon][lookback]` sub-object client-side (no network round-trip; full data is embedded as JSON in a `<script>` tag)
- Hit % color rule per spec §1.5 — green if `hit_pct >= base_rate`, red if `hit_pct < base_rate * 0.85`, slate otherwise
- Median CAR color rule via existing `h.car_cell()` helper (no change)
- Sector tile slices client-side to top 3 + bottom 2 by hit% (per spec §1.3) — JSON carries all sectors, FE slices
- Auto-wire script reads `data-href` from each row + handles click + Enter/Space keyboard
- ALL existing tests in `.scripts/test_stage_05.py` continue to pass (no regression on per-signal scoreboard, refresh button, etc.)
- `python -m unittest .scripts/test_render_performance_v2.py` passes 100%

### Zone discipline

All Zone A. Claude writes Python + HTML + JS + tests. Claude runs `python -m unittest` from bash (no DB writes).

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| Existing render_performance.py is monolithic — refactoring breaks unrelated sections | Restrict edits to `_cohort_value_section` / `_cohort_sector_section` region only. Run full test_stage_05.py suite after every edit. |
| Lookback dropdown JS gets bloated — 3 tiles × 4 lookbacks × event listeners | Use one delegated event listener on the cohort-section root, switch table body via JSON lookup. Single ~30-line script. |
| Sector tile's top-3+bottom-2 slicing logic lives client-side; could mis-handle ties | Pin behaviour in a unit test: 5 sectors with known hit%, assert which 5 render (top 3 + bottom 2 wrapping the divider row). |
| N-band ⚠ glyph rendered inconsistently | `n_band_cell()` is the single source of truth, used in all three tiles via `render_helpers.py` import. |

---

## Sprint FE2 — Drill-down renderer + 18 new pages

### Goal

Write the SINGLE parameterised renderer that produces all three drill-down page types (bucket / role / sector). Wire it into `build_dashboard.py` so each cohort key in the corresponding JSON produces one HTML page.

Output: 4 bucket pages (`performance-bucket.html?bucket=1k-25k` etc.) + 3 role pages + 11 sector pages = **18 new HTML files** in `outputs/`.

### Prerequisites

- FE1 complete (cohort tiles link out to these pages — internal QA verified the `data-href` values are correct)
- Mockups `performance-bucket-preview.html`, `performance-role-preview.html`, `performance-sector-preview.html` approved (Rupert 2026-05-19)

### Inputs

- Spec §2 (shared drill-down structure), §2.2 (status pill ±50% rule), §2.3 (top/bottom 10 + edge case note), §2.4 (rollup table), §2.5 (keyboard accessibility), §2.6 (breadcrumb), §3 (per-page variants)
- The three shipped JSON files at `dashboard/data/performance_*.json`
- Mockup HTML — match it pixel-for-pixel where reasonable

### Deliverables

| File | Action | Purpose | Est. size |
|---|---|---|---|
| `.scripts/dashboard/render_performance_drilldown.py` | NEW | Single function `render_drilldown_page(cohort_type, cohort_key, payload, horizon='t21', lookback='90d') -> str`. Renders the breadcrumb + page-header stats line + status pill + top/bottom 10 firings panels + all-tickers rollup table. Parameterised so all three cohort types share the same code path. | ~250 lines |
| `.scripts/dashboard/render_helpers.py` | MODIFY | Add `status_pill(hit_pct, base_rate, cohort_type) -> str` — emits green / red / nothing per spec §2.2. Add `firing_row(firing, horizon) -> str` — renders one §5.3 firing row with badge + chev + click target. | ~80 lines added |
| `.scripts/build_dashboard.py` | MODIFY | Iterate over each cohort_type's JSON keys, call `render_drilldown_page` once per key, write to `outputs/performance-{bucket\|role\|sector}.html?key={key}`. URL-encode sector names. | ~60 lines added |
| `.scripts/test_render_drilldown.py` | NEW | Tests for the drill-down renderer: shape, status pill rules, top/bottom 10 sort, < 10 firings edge-case note, rollup ordering, keyboard accessibility attrs, every-ticker-resolves-to-companies smoke test. | ~250 lines |

### Acceptance criteria

- `render_drilldown_page` is pure — no DB / file I/O; takes pre-loaded payload dict from `performance_*.json`
- Output HTML matches the corresponding mockup at `docs/specs/mockups/` (structurally — colour palette, padding, font sizes all per spec §2.5 Tailwind snippets)
- Status pill conditional rendering per spec §2.2:
  - `hit_pct >= base_rate * 1.5` → green pill, copy varies by cohort_type ("Top bucket" / "Strong role cohort" / "Top sector this period")
  - `hit_pct <= base_rate * 0.5` → red pill, copy varies
  - otherwise → no pill rendered (NOT a "middle" pill)
- Top 10 firings panel: green header (`bg-emerald-50`), title "Top 10 firings — best CAR @ {horizon}", `winners` label, rows in `car` desc order, max 10
- Bottom 10 firings panel: red header (`bg-rose-50`), title "Bottom 10 firings — worst CAR @ {horizon}", rows in `car` asc order, max 10
- Edge-case note rendered when fewer than 10 losers OR winners per spec §2.3: italic gray sub-text "Only N of M firings had negative CAR..." (symmetric for winners)
- Rollup table: tickers with N≥3 first (sorted by hit% desc), dashed-border divider, then N<3 in italic faded
- Every clickable element (firing row, rollup row, breadcrumb): `tabindex="0"`, `role="link"`, `aria-label="View {TICKER} company page"`, keydown handler for Enter and Space
- Smoke test: every ticker linked from any drill page must resolve to an existing `outputs/companies/{TICKER}.html` file. If a ticker has no company page (e.g. missing price history per Stage 5 Sprint 4), the link is rendered as non-clickable italic with tooltip "company page not generated"
- Sector pages carry `data-benchmark-symbol="{symbol}"` attribute on the page-header so the FE can disclose `^FTAS` fallback if applicable
- `build_dashboard.py` writes all 18 pages via `_atomic_write_text()` (same pattern as the other pages)
- ALL existing tests pass; new tests pass

### Zone discipline

All Zone A. Claude writes + tests. Build_dashboard.py is NOT run by Claude (it's in CLAUDE.md's Zone-B list); the unit tests mock the orchestrator and assert on the rendered HTML strings directly.

### Risk + mitigation

| Risk | Mitigation |
|---|---|
| 18 pages × 4 horizons × 4 lookbacks of data embedded per page = potential bloat | Embed only the requested horizon × lookback view per page. Lookback dropdown becomes a "switch URL" pattern (page reloads with `&lookback=6m`) rather than a client-side data switch. Trade-off: simpler, no JS state; slightly slower lookback changes. |
| Status pill ±50% rule on tiny N triggers spurious pills | Spec §1.2 already requires N≥20 for trustworthy stats; pill emission also gated to `n >= 20` to match spec |
| Sector name URL-encoding bugs (spaces, slashes) | Test C3 in new test file: round-trip-encode a sector name like "Consumer Discretionary" through the URL builder and assert the link works |
| Existing company-page generator may have skipped some tickers — drill pages link to dead URLs | Pre-flight check at the top of `build_dashboard.py`: read the list of `outputs/companies/*.html`, mark drill-rows whose ticker isn't in that list as non-clickable. One pass, O(N). |
| Keyboard accessibility regression on the existing per-signal scoreboard | Add a test that the new auto-wire script doesn't accidentally rebind elements on other pages |

---

## Sprint FE3 — Integration smoke (Rupert runs)

### Goal

Verify the full FE pipeline works end-to-end on real data. Rupert browses every new page, confirms visual parity with the approved mockups, validates click-through, and signs off the v1 redesign.

### Prerequisites

- FE1 + FE2 complete (Claude reports clean test suite + diff summary)

### Acceptance criteria — must ALL pass

1. **Pipeline runs without error** — `python .scripts\build_dashboard.py` completes cleanly
2. **All 18 new drill pages exist** — Get-ChildItem on outputs returns the expected files
3. **Existing `performance.html` renders** with three new tiles (NOT two) populated with real data; lookback dropdown works per tile; clicking any row navigates to the correct drill-down page
4. **Each drill-down page renders** with: correct breadcrumb leaf, page-header stats line, optional status pill, top/bottom panels, rollup table
5. **Every ticker in every drill-down page** links to a real `outputs/companies/{TICKER}.html` (or renders italic-faded with tooltip if absent)
6. **Keyboard accessibility** — Tab focuses each row; Enter/Space activates the link (sample test on one page is sufficient)
7. **Visual parity with mockups** — Rupert opens both side-by-side (mockup file + production page) and confirms they look the same (modulo real data values)
8. **No regression on Today** — `index.html` still renders correctly (the only cross-page concern is the auto-wire script — confirm it's scoped to drill pages only)
9. **No console errors** in browser DevTools on any new page

### Paste-and-run sequence for Rupert

```powershell
cd C:\Dev\DirectorsDealings

# Step 1 — backup the DB (project policy)
Copy-Item .data\directors.db .data\directors.db.bak.fe3 -Force

# Step 2 — run the exporter (may not be needed if Sprint 6 outputs are still fresh; safe to re-run)
python .scripts\export_dashboard_json.py

# Step 3 — run the dashboard builder (writes all HTML pages incl. 18 new ones)
python .scripts\build_dashboard.py

# Step 4 — count new pages and verify file sizes
Get-ChildItem outputs\performance-bucket.html, outputs\performance-role.html, outputs\performance-sector.html `
              -ErrorAction SilentlyContinue | Format-Table Name, Length

Get-ChildItem outputs\*.html | Where-Object { $_.Name -like "performance-*.html" } |
              Measure-Object | Select-Object Count

# Step 5 — start local HTTP server + open each page
cd outputs
Start-Job -ScriptBlock { python -m http.server 8000 }
Start-Sleep -Seconds 2
Start-Process "http://localhost:8000/performance.html"
```

Rupert then clicks through all 18 drill pages (or a representative sample: 1 bucket + 1 role + 2 sectors) and confirms acceptance criteria 1-9. When done, `Stop-Job *` halts the server.

### Stage gate

If all 9 are green → **performance page redesign v1 is COMPLETE.** The work is shippable.

If any check fails → diagnose, fix, re-run FE3. The DB backup at Step 1 is your safety net (though FE work doesn't touch the DB, the backup is project policy).

---

## After FE3 — what's left

When FE3 passes, the v1 redesign is done. Backlog items to revisit:

1. **Tech-debt cosmetics** (deferred from backend Sprint 6):
   - Pre-existing ResourceWarning on `csv_path.open()` at `export_dashboard_json.py:1788` — 2-line `with` fix
   - Double-print of `--verbose` summary in `main()` — one-line dedup
2. **Eventual cleanup:** remove the legacy `cohorts` keys from `signals.json` once nothing reads them (post-FE3, after a stable week)
3. **Spec follow-up:** update the 200 KB file-size cap in spec §5.2 to reflect the ~1 MB actual on real data
4. **v1.1 candidates** (out of scope for v1, listed in spec §7 for reference):
   - Bucket / role / sector tile cross-linking ("hovering CEO/CFO highlights CEO firings in sector tile")
   - Per-tile signal-filtered cohorts ("show me only T1 trades for ABC")
   - CSV / clipboard export
   - Saved views / URL state for sort order
   - Mobile / tablet-specific layouts (beyond responsive stack)

---

## Quick reference — sprint dependency graph

```
  FE1 (Cohort tiles refactor)
       │
       ▼
  FE2 (Drill-down renderer + 18 pages)
       │
       ▼
  FE3 (Integration smoke — Rupert)
       │
       ▼
  Performance page redesign v1 complete →
  legacy cohorts keys can be deprecated
```

FE1 and FE2 could technically be parallelised (FE2 needs only the JSON shapes which already exist), but doing them sequentially keeps the scope simple and lets the auto-wire script land cleanly in FE1 before drill pages start consuming it.
