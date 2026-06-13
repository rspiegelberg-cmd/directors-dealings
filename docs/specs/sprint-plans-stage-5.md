# Sprint plans — Stage 4.5 gate-close + Stage 5 dashboard build
**Created:** 2026-05-14. **Owner:** Rupert.
**Model:** Solo operator + Claude. Each sprint = one focused Claude session (~1–2 hours). Unit of effort is session-hours, not story points.
**Gate rule:** Do not auto-proceed between sprints. Rupert signs off each sprint before the next opens.

---

## Dependency map

```
Sprint 0 (gate-close)
  └── Sprint 1 (dashboard scaffold + scoreboard)
        └── Sprint 2 (Today page completion)
              └── Sprint 3 (Performance page)
                    └── Sprint 4 (Company page)
```

U1, U2, U3, U4 are baked into the sprint where the relevant component is first built — not retrofitted later.

---

## Sprint 0 — Stage 4.5 gate-close

**Goal:** Formally close the F1 outlier gate so Stage 5 is unblocked. Confirm the backtest CSV is clean; write the required data-quality report; Rupert signs the gate.

**Length:** ~30–45 min session.

**Why a separate sprint:** Stage 5 build must not begin on potentially dirty numbers. The PM agent confirmed this is the only hard blocker. It is a short, focused task — mixing it into Sprint 1 risks it getting skipped when the dashboard build gets interesting.

### Capacity

| Who | Role | Notes |
|-----|------|-------|
| Claude | Analyst + writer | Reads CSV, computes ratio check, drafts report |
| Rupert | Decision-maker | Reviews output, signs or requests re-backfill |

### Backlog

| Priority | Task | Effort | Owner | Acceptance criteria |
|----------|------|--------|-------|---------------------|
| P0 | Load `_backtest_results.csv`, compute `abs(mean_car) / abs(median_car)` per signal per horizon | 10 min | Claude | Ratio table produced; F1 ratio confirmed ≤ 3× at all horizons |
| P0 | Write `.data/_data_quality_report.md` | 15 min | Claude | File exists; documents: root cause, adjclose fix confirmation, ratio results, decision |
| P0 | Rupert reviews and signs Stage 4.5 gate | 5 min | Rupert | Gate marked CLOSED in `_data_quality_report.md` |
| P1 | If F1 ratio > 3×: re-run `backfill_prices.py` then `backtest.py` (Windows Python, Zone B) | 45 min | Rupert | Re-run before Sprint 1 opens — adds a session |

### Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| F1 ratio still blown → backfill needed | Adds 45 min + re-run backtest | Flag immediately; Sprint 1 waits |
| `_backtest_results.csv` read fails (FUSE) | Can't compute ratio | Copy to `/tmp/` first; read from there |

### Definition of done

- [ ] Ratio table computed and documented
- [ ] `_data_quality_report.md` exists with gate-close sign-off
- [ ] No F1 row has `|mean_car| > 3 × |median_car|` at any horizon
- [ ] Sprint 1 is unblocked

---

## Sprint 1 — Dashboard scaffold + scoreboard

**Goal:** A working `dashboard/index.html` that loads real JSON, renders the top strip, the per-signal scoreboard with horizon switching, and the freshness chip in the header. All shared components (renderBadge, freshness, state management) locked before other components build on them.

**Length:** ~1.5–2 hour session.

**UX fixes in this sprint:** U3 (freshness chip) — baked into the header at first build, not added later.

**Spec refs:** `stage-05-build-spec.md` steps 1–5; `stage-05-design-final.md §1.1.1`, `§1.2.1`, `§1.1.2`, `§1.2.2`; `stage-05-roadmap-v1.md §U3`.

### Capacity

| Who | Role |
|-----|------|
| Claude (frontend-engineer agent) | Builds HTML/JS |
| Claude (dashboard-designer agent) | QAs each panel before sign-off |
| Rupert | Opens browser, confirms render, signs sprint |

### Backlog

| Priority | Task | Effort | Notes |
|----------|------|--------|-------|
| P0 | Create `dashboard/` folder + `index.html` skeleton (Tailwind CDN, Chart.js CDN, JSON fetch scaffolding) | 15 min | Foundation everything else builds on |
| P0 | Implement `renderBadge(signalId)` shared component + full tooltip table | 20 min | Single source of truth; used by scoreboard, today table, company page |
| P0 | Implement `renderFreshness(generatedAt)` (U3) + wire to header | 15 min | Green/amber/red chip; reads `signals.json.generated_at`; shared across both pages |
| P0 | Top strip — 3 tiles (signals today, active clusters, paper P&L) | 20 min | Include empty states; paper P&L signal-filter dropdown |
| P0 | Shared horizon state + dropdown (`state.horizon`, `horizonChange` event) | 10 min | Must exist before scoreboard or chart can render |
| P0 | Per-signal scoreboard (7 rows × 9 columns, sparklines, status pills, deprecate button) | 30 min | T0→F1 fixed order; tabular numerals; outlier glyph; gated empty-state rows |
| P1 | Designer agent QA: scoreboard vs wireframe in `stage-05-design-final.md` | 10 min | Gate before Sprint 2 opens |

### Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Scoreboard sparklines need SVG precision | May overrun | Use the exact inline-SVG spec from design-final §1.3.5; don't freehand |
| Deprecate button needs Flask sidecar | Button exists but POST fails | Ship button as instruction toast for now; sidecar is v1.1 work |
| Tailwind CDN class purging | Some classes missing | Test every class in browser before sign-off |

### Definition of done

- [ ] `dashboard/index.html` opens in browser with no console errors
- [ ] Freshness chip renders in correct colour state based on `generated_at` age
- [ ] All 7 signal rows render with correct badge colours and tooltips
- [ ] Horizon dropdown switches scoreboard data; `horizonChange` event fires
- [ ] Sparklines render for all signals with data; flat grey baseline for gated signals
- [ ] Designer agent has QA'd the scoreboard panel
- [ ] Rupert confirms render in his browser

---

## Sprint 2 — Today page completion

**Goal:** Complete `index.html` with the Today's buy signals table, This week table, and Active/Brewing clusters panel. Page is fully functional and ready for daily use.

**Length:** ~1–1.5 hour session.

**UX fixes in this sprint:** U1 (Signals column first, Role merged into Director) — baked into the table at first build.

**Spec refs:** `stage-05-design-final.md §1.1.3`, `§1.1.4`; `stage-05-roadmap-v1.md §U1`.

### Backlog

| Priority | Task | Effort | Notes |
|----------|------|--------|-------|
| P0 | Today's buy signals table — 6-column layout with Signals first (U1) | 25 min | Director+Role merged cell; role chip colour rules; empty state |
| P0 | This week sub-table (same shape, reads `this_week[]`) | 10 min | Shared table render function; different heading only |
| P0 | Active/Brewing clusters panel — two-tab, per-cluster cards | 20 min | Brewing definition locked; aggregate £ formatting; empty states |
| P0 | Row click → `companies/{TICKER}.html` in new tab (both tables + cluster cards) | 5 min | `cursor: pointer`; `onclick` on every row/card |
| P0 | Generated-at footer (build sha) | 5 min | Reads `signals.json.generated_at`; 11px text |
| P1 | Designer agent QA: Today page vs full wireframe | 10 min | Check column widths, sort order, MTM glyphs, empty states |
| P1 | Smoke test with real `dealings.json` — confirm sort order is T0 first | 5 min | |

### Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| `dealings.json.today[]` empty (no signals today) | Empty-state must render, not blank | Covered in spec; test this path explicitly |
| Company page links 404 (not built yet) | Row click opens dead link | Expected at this sprint; note in sign-off |

### Definition of done

- [ ] Today table renders with Signals in column 1; Director+Role merged
- [ ] Sort is T0 first, then by £ value desc — visually confirmed
- [ ] Role chips use correct colour (indigo for CEO/CFO, violet for Chair, slate for NED)
- [ ] This week table renders below with correct heading
- [ ] Active tab shows S1-active clusters; Brewing tab shows 30–90d clusters
- [ ] Empty states on both tabs render cleanly
- [ ] Designer agent QA passed
- [ ] Full index.html opens in browser with zero console errors on real JSON

---

## Sprint 3 — Performance page

**Goal:** Build `dashboard/performance.html` — the analytics deep-dive. Per-signal scoreboard (shared with index), diagnostics chart with interactive legend, cohort cuts. Page is ready for signal decision-making.

**Length:** ~1.5–2 hour session.

**UX fixes in this sprint:** U2 (clickable legend on diagnostics chart) — baked in at build, not added later.

**Spec refs:** `stage-05-design-final.md §1.2`; `stage-05-roadmap-v1.md §U2`; `stage-05-build-spec.md §Per-signal diagnostics chart`, `§Cohort cuts`.

### Backlog

| Priority | Task | Effort | Notes |
|----------|------|--------|-------|
| P0 | Create `performance.html` skeleton — same header pattern as index, freshness chip (U3 reused), horizon dropdown right-aligned | 10 min | `renderFreshness()` already exists from Sprint 1 |
| P0 | Per-signal scoreboard (reuse `renderBadge()`; same 7 rows; same horizon state) | 15 min | Scoreboard logic already built — wire to new page, confirm shared state works |
| P0 | Diagnostics chart — Chart.js 8-dataset line chart | 25 min | Reads `diagnostics_series` from `signals.json`; reacts to `horizonChange` |
| P0 | Interactive legend (U2) — `buildDiagLegend()`, click to highlight/fade | 15 min | Implementation snippet in `stage-05-roadmap-v1.md §U2`; `chart.update('none')` |
| P0 | Cohort cuts — Block A (value bucket bar chart) + Block B (sector hit rate rows) | 20 min | Reads `signals.json.cohorts`; green/rose bars; 5-row sector list |
| P1 | Model assessment panel (auto kill-candidates + caveats) | 10 min | Reads from `signals.json` status fields |
| P1 | Designer agent QA: performance page vs wireframe | 10 min | |

### Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| `diagnostics_series` missing from `signals.json` | Chart renders empty with overlay message | Empty-state already specced in build-spec; confirm exporter emits the key |
| 8 lines + interactive legend = complex Chart.js state | Legend state bugs | Test all 8 datasets toggle individually + reset |
| Cohort keys absent if exporter didn't produce them | Cohort panels empty | Spec calls for graceful empty state; confirm with dry-run |

### Definition of done

- [ ] `performance.html` loads with no console errors
- [ ] Freshness chip renders (same logic as index)
- [ ] Horizon dropdown on performance page fires `horizonChange`; scoreboard + chart both re-render
- [ ] Clicking each legend item highlights that line and fades others to ~15% opacity
- [ ] Clicking active legend item resets all lines to full opacity
- [ ] FTSE A-S line never fades below 60% opacity
- [ ] Both cohort panels render with correct colours (green positive, rose negative)
- [ ] Designer agent QA passed
- [ ] Nav link index ↔ performance works both directions

---

## Sprint 4 — Company page

**Goal:** Build `gen_company_pages.py` to generate `companies/{TICKER}.html` for every ticker in the transactions table. Annotated price chart, transactions table, signal-firing history, cluster history.

**Length:** ~1.5–2 hour session.

**UX fixes in this sprint:** U4 (vivid marker palette — green/red/amber/violet, white halo) — baked into the chart annotation spec at first build.

**Spec refs:** `stage-05-1-company-page.md`; `stage-05-roadmap-v1.md §U4`.

### Backlog

| Priority | Task | Effort | Notes |
|----------|------|--------|-------|
| P0 | Update/rewrite `gen_company_pages.py` — reads SQLite + backtest CSV + clusters.json, renders one HTML per ticker | 20 min | Static page; data baked into `<script>` tag; no runtime fetches |
| P0 | Header strip + active status banner (conditional — only if cluster or recent signal) | 10 min | Sector chip; AIM/Main badge; current price + 1d delta |
| P0 | Annotated price chart with U4 marker palette | 30 min | Green-600 exec buys; green-400 NED buys; red-600 sells; amber-600 grants; violet-600 cluster rings; white halo on all |
| P0 | Transactions table — all PDMR rows, sorted desc, last-20 visible with expander | 15 min | Source column mandatory; `ti-external-link` icon; greyed if URL null |
| P1 | Signal-firing history with outcomes (T+1/T+21/T+90/T+252 CAR per firing) | 15 min | Pending cells render `—`; mix of matured + pending in same row allowed |
| P1 | Cluster history panel | 10 min | Active + historical; empty state if no clusters |
| P1 | Footer / disclaimer | 5 min | Standard disclaimer text from spec |
| P1 | Wire into `update.py` so every refresh re-renders all ticker pages | 5 min | Parallelise via `multiprocessing.Pool` if > 30s |
| P1 | Designer agent QA: company page vs spec | 10 min | |
| P1 | Smoke test: open 3 different ticker pages; confirm markers, scroll-on-click, tooltips | 10 min | |

### Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Chart.js annotation plugin for markers | Plugin may need separate CDN import | Use `chartjs-plugin-annotation`; confirm CDN URL before build |
| White halo technique in Chart.js | Two-draw approach may flicker | Use `borderColor: '#fff'` + `borderWidth: 3` on annotation point — simpler and supported |
| 500 tickers × 50 KB = 25 MB generation | Slow if sequential | `multiprocessing.Pool` from the start; spec allows up to 30s |
| Director name normalisation gaps | Same person appears as two rows | Known issue; acceptable for v1; add a note in the page footer |

### Definition of done

- [ ] Every ticker in `transactions` table has a corresponding `companies/{TICKER}.html`
- [ ] Full generation runs in under 30 seconds
- [ ] Exec buy markers are vivid green-600 ▲ with white halo
- [ ] NED buy markers are green-400 ▲ with white halo
- [ ] Sell markers are red-600 ▼ with white halo
- [ ] Grant/SIP markers are amber-600 ■ with white halo
- [ ] Cluster ring is violet-600, 2.5px stroke, no fill
- [ ] Marker hover tooltip shows date · director · role · type · £value · signal (if any)
- [ ] Marker click scrolls to correct transactions table row with 2s highlight pulse
- [ ] Source column renders external-link icon on every row; greyed if URL null
- [ ] Designer agent QA passed
- [ ] Rupert opens 3 real ticker pages and confirms usability

---

## Sprint sequence summary

| Sprint | Session | Goal | UX fixes | Gate |
|--------|---------|------|----------|------|
| 0 | ~30 min | Close Stage 4.5 | — | `_data_quality_report.md` signed |
| 1 | ~2 h | Dashboard scaffold + scoreboard | U3 | index.html renders; scoreboard QA'd |
| 2 | ~1.5 h | Today page complete | U1 | index.html fully functional |
| 3 | ~2 h | Performance page | U2 | performance.html fully functional |
| 4 | ~2 h | Company pages | U4 | All ticker pages generated; smoke tested |

**Total estimated session time:** ~8 hours across 5 sessions.

**How to start each session:** Open a new conversation, state which sprint you're opening, and direct Claude to read `CLAUDE.md` + the relevant spec files listed in each sprint's "Spec refs" before writing a line of code.
