# Sprint 14 — Cohort performance (CAR) chart redesign

**Dates:** Fri 2026-05-29 → Fri 2026-06-12 (2 weeks)
**Author:** Project Manager (Claude) + Rupert (PM, decisions, Windows-side DB ops)
**Supersedes:** `docs/specs/cohort-performance-chart-sprint-plan.md` (mislabelled "Sprint 11", predated Sprints 12 & 13, built on a stale base state). That file is now historical; use this one.
**Inputs:**
- `docs/specs/cohort-performance-chart-redesign-brief.md` (locked design decisions — v2 with Level 1. **Do not re-litigate.**)
- `docs/specs/cohort-performance-chart-design-spec.md` (designer spec — **covers Level 2 only**; Level 1 design re-run still outstanding — see Phase 1)
- `docs/backlog.md` (B-NNN numbering)
- `docs/agents/INDEX.md` (agent roster)
- `CLAUDE.md` (FUSE two-zone rule, QA-before-gate rule, truncation discipline)

---

## Pre-flight — fresh codebase audit (2026-05-29)

Bake these facts in before sizing or starting any work. They were confirmed by a fresh audit on sprint kickoff and **correct several stale assumptions** carried by the old plan.

| Fact | Detail |
|------|--------|
| **Today** | 2026-05-29. This is **Sprint 14**. |
| **Base state** | **4,598 transactions / 2,973 signals / 424 tests green.** (The old plan's numbers predate Sprints 12–13 and are wrong.) |
| **Build status of THIS feature** | Essentially **nothing is built**. Phases 0, 2, 3, 4, 5, 6, 7 are all genuinely remaining work. Phase 1 design is **partially done** (Level-2 spec exists; Level-1 design re-run still needed). |
| **File-name collision trap** | `render_performance.py`, `render_performance_drilldown.py`, `test_cohort_table.py`, `test_drill_payload.py`, `test_render_performance_v2.py` belong to the **earlier "performance-page-redesign-v1" feature** (value/role/sector tiles with lookback dropdowns). That is a **DIFFERENT feature** that merely shares files and the word "cohort". **Do NOT credit any of it against this sprint** and do not assume those tests cover this work. |
| **Phase 0 status** | **No interim divergence warning exists in the code today.** Phase 0 is still required and ships first. |
| **JSON gap** | `export_dashboard_json.py` currently emits per-signal aggregates bucketed **by HORIZON, not by calendar month.** This feature needs a **brand-new `cohort_performance.json` blob** keyed `groups[grp].months[]` plus `cohort_drilldown[grp][month_iso]`. |
| **Schema** | **No DB schema change needed.** All cohort fields are derivable from `signal_fires` × `signal_performance` joined on `signal_id`, grouped by `signal_group` and `month_iso`. |

### B-NNN block assigned: **B-064 → B-071**

The brief said "latest used is B-059", but the **live `docs/backlog.md` already records B-060, B-061, B-062, B-063** (Sprint 11 data-integrity items). The backlog file is the source of truth, so the next free block is **B-064 onwards**. Flag for Rupert if you intended a different block.

| Phase | B-number | Title |
|-------|----------|-------|
| 0 | **B-064** | Interim divergence warning on existing performance table |
| 1 | **B-065** | Design completeness — Level-1 design-spec re-run |
| 2 | **B-066** | Upstream `cohort_performance.json` emit (Zone B — Rupert PowerShell) |
| 3 | **B-067** | Level-1 performance-table augmentation (sparkline + trend + row-click) |
| 4 | **B-068** | Level-2 main chart + N strip |
| 5 | **B-069** | Rolling-6m hit-rate panel |
| 6 | **B-070** | Drill-down modal |
| 7 | **B-071** | Feature flag + production swap [STRETCH] |

---

## Sprint goal

Replace the misleading trailing-12-month CAR line chart with a two-level cohort view that exposes single-ticker contamination and lets Rupert interrogate any cohort with one click. **Success looks like:** Rupert can scan all 11 signals' trajectories in the augmented performance table (Level 1), click any row to open that signal's monthly cohort detail (Level 2), and surface single-ticker outliers (the TIN/T3 problem — mean +7.9% vs median −5.0% at T+90, driven by one trade) without leaving the dashboard.

---

## Agent roster for this sprint (from `docs/agents/INDEX.md`)

| Agent | Used for |
|-------|----------|
| **Product Manager** | Scope decisions, acceptance criteria, "is this in scope" rulings |
| **Dashboard Designer** | Any design question or design-spec work (Phase 1 re-run; ad-hoc design rulings) |
| **Back-end Engineer** | `.scripts` Python — `export_dashboard_json.py` aggregates, rolling-hit-rate + verdict + contribution maths |
| **Front-end Engineer** | HTML/CSS/JS/Tailwind/Chart.js in the renderer (`render_*.py`) + `outputs/` |
| **QA** | **Independent verification BEFORE every gate decision** (mandatory, per CLAUDE.md) |
| **Analyst** | Cohort-analysis sanity checks (does the chart tell the truth about T3/F1?) |
| **Trader** | Markets-realism check (are net-of-cost numbers and the "edge" framing honest?) |

**The QA-before-gate rule is non-negotiable.** Every phase below names a QA pass that runs *before* Rupert sees the diff and *before* the gate is declared cleared. The builder agent never grades its own work.

---

## Per-phase agent-dispatch summary (the map Rupert asked for)

| Phase | Build agent(s) | Design/analysis agent | QA gate | Zone |
|-------|----------------|-----------------------|---------|------|
| 0 | Front-end Eng (badge) + Back-end Eng (median calc) | — | **QA** before gate | A (Claude-safe) |
| 1 | — | **Dashboard Designer** (re-run) | **QA** before gate | A (doc only) |
| 2 | Back-end Eng | **Analyst** (cohort-aggregate sanity) | **QA** before gate | **B — Rupert PowerShell runs the export** |
| 3 | Front-end Eng | Dashboard Designer (inline-expand vs focus call, from Phase 1) | **QA** before gate | A (Claude-safe) |
| 4 | Front-end Eng | Dashboard Designer (whisker/low-N rendering rulings) | **QA** before gate | A (Claude-safe) |
| 5 | Front-end Eng | **Analyst** (cross-check one month's hit rate) | **QA** before gate | A (Claude-safe) |
| 6 | Front-end Eng + Back-end Eng (contribution + verdict) | **Analyst + Trader** (verdict honesty, single-ticker realism) | **QA** before gate | A (Claude-safe) |
| 7 | Front-end Eng | Product Manager (release call) | **QA** before gate | A (Claude-safe) |

---

## Sprint backlog — phased delivery with gates between each

Each phase has its own QA gate (CLAUDE.md "QA agent before every gate decision", established 2026-05-25). **Stages do not auto-proceed** — manual gate between each, per Rupert's locked discipline.

### Phase 0 — Interim divergence warning on existing performance table (B-064) [SHIP DAY 1]

**Goal:** Protect Rupert from real-money decisions based on misleading mean-CAR figures while the rest of Sprint 14 is in flight. T3 currently shows mean +7.9% at T+90 but median −5.0% — a ~12.9pp gap driven by a single TIN trade at +1218.9%. Until Phase 6 ships the drill-down, this warning is the **only** thing on the dashboard that flags the contamination.

**Trigger rule** (per-signal, computed at render time from existing backtest data):

> Fire warning if **|mean − median| > 5pp AND |mean − median| > 0.5 × |mean|**.

Both conditions in conjunction — the first catches absolute divergence, the second catches relative divergence so we don't flag tiny means. T3 fires (12.9pp gap). F1 does NOT fire (mean −1.15%, N=723 — small mean, small gap, a real edge problem not outlier contamination).

**Agent dispatch:**
- **Back-end Engineer** — compute per-signal median CAR @ T+90 alongside the existing mean; apply trigger rule; attach `divergence_warning: bool` + `gap_pp: float` to each row. (Pure `render_helpers.py` / render-time compute — no DB write.)
- **Front-end Engineer** — render the `⚠` badge next to the mean with hover tooltip ("Mean diverges from median by X.Xpp — likely single-outlier contamination. Drill into the cohort before acting.").
- **QA — BEFORE the gate:** confirm `⚠` appears on T3 in the live dashboard; confirm it does NOT appear on F1; read the diff to confirm no other table behaviour changed; truncation check via Read tool.

**FUSE:** Pure **Zone A** — `render_helpers.py` + template edits, plus synthetic-fixture tests. **Claude-safe**, run tests from bash.

**Definition of Done:**
- [ ] Median computed + trigger rule applied; `divergence_warning` / `gap_pp` on each row.
- [ ] `⚠` badge + tooltip render on the live performance table.
- [ ] Synthetic fixture test: one contaminated signal (1 huge winner) fires, one clean signal does not.
- [ ] QA pass complete (T3 fires, F1 does not, no other regression, Read-tool truncation check).
- [ ] Rupert visual review on the live dashboard.

**Scope discipline (Product Manager owns):** do NOT "fix" the signal taxonomy or hide T3. One-line warning only.

### Phase 1 — Design completeness: Level-1 design-spec re-run (B-065)

**Goal:** The designer spec covers BOTH levels. The dashboard-designer ran before the Level-1 (table augmentation) section was added to the brief, so the current spec covers the **Level-2 detail chart only**. We need it extended to cover Level 1: the sparkline column, trend column, and row-click interaction — including a firm **inline-expand vs focus-mode** recommendation.

**Agent dispatch:**
- **Dashboard Designer** — re-run with the updated brief v2 (sections "Level 1 — performance table augmentation"). Deliverable: Level-1 wireframe + Tailwind catalogue for sparkline/trend cells + the inline-expand-vs-focus-mode call + the JSON fields Level 1 consumes (`sparkline_points`, `trend_3m_vs_prior3m_t21`).
- **QA — BEFORE the gate:** read the updated design spec end-to-end; confirm a Level-1 wireframe is present and the inline-expand-vs-focus-mode decision is explicitly made. **Block Phase 3 if either is missing.**

**FUSE:** **Zone A — doc only.** Claude-safe.

**Definition of Done:** — ✅ CLOSED 2026-05-31 (QA-verified)
- [x] Design spec updated with a Level-1 section (wireframe + Tailwind + JSON fields). — `docs/specs/cohort-performance-chart-level1-design.md` §§1,2,4,5.
- [x] Inline-expand vs focus-mode decision made and justified. — **FOCUS MODE** locked (decision 1); inline-expand explicitly marked SUPERSEDED.
- [x] QA confirms completeness before Phase 3 starts. — independent QA PASS 2026-05-31; both end-of-doc open questions resolved by locked decisions, not dangling.

### Phase 2 — Upstream `cohort_performance.json` emit (B-066)

**Goal:** `export_dashboard_json.py` emits the brand-new `cohort_performance.json` blob with every field the chart needs. Additive — no breaking changes to existing JSON. Keyed `groups[grp].months[]` (per-calendar-month aggregates) plus `cohort_drilldown[grp][month_iso]`.

**Fields per `groups[grp].months[]`:** `month_iso`, `n_signals`, `mean_car_t1/t21/t90`, `min_car_t21`, `max_car_t21`, `hit_rate_t21`, `hit_rate_t21_rolling_6m`, `single_ticker_weight`, `ma3_mean_car_t21` (null for first 2 months), `signal_ids[]`. Plus `groups[grp].header` (`n_total_signals`, `mean_car_t21_overall`, `hit_rate_t21_overall`), `groups[grp].sparkline_points`, `groups[grp].trend_3m_vs_prior3m_t21`, and `cohort_drilldown[grp][month_iso]` (`verdict` + `signals[]`). All derivable from `signal_fires` × `signal_performance`; **no schema change.**

**Agent dispatch:**
- **Back-end Engineer** — write the new emit function: per-(group, month) aggregates; rolling-6m hit rate; 3-month MA; single-ticker weight; sparkline points + trend; drill-down `signals[]` + verdict string (verdict logic per design spec §"Upstream verdict logic"). Unit tests against a 3-signal synthetic dataset.
- **Analyst — BEFORE the gate:** sanity-check 2 emitted signal groups against manual SQL (is T3's TIN month flagged with high `single_ticker_weight`? Does F1's near-zero edge show honestly?). This is exactly the cohort-truth check this whole feature exists to enable.
- **QA — BEFORE the gate:** spot-check 2 signal groups in the emitted JSON against manual SQL; verify `sparkline_points` N and ordering; confirm a `.bak` was taken before the write-path script ran.
- **Rupert (PowerShell)** — runs the export and inspects output JSON.

**FUSE — ZONE B:** `export_dashboard_json.py` writes under `.data/`. **Rupert runs it from PowerShell. Claude does NOT run it via bash.** Per the locked Zone B rule in CLAUDE.md. Claude writes the *code* (Zone A), Rupert executes the *run* (Zone B). A `.data/directors.db` backup must be confirmed before the run.

> **Exact command for Rupert (do not let Claude run this):**
> `python .scripts\export_dashboard_json.py`
> (Confirm `.data\directors.db.bak` exists / refreshed first.)

**Definition of Done:** — ✅ CLOSED 2026-05-31 (QA-verified)
- [x] New emit function written; unit tests green on synthetic data. — `build_cohort_performance` + helpers in `export_dashboard_json.py`; `test_cohort_performance_export.py` 33 tests green.
- [x] Rupert runs the export from PowerShell; `cohort_performance.json` produced. — produced 2026-05-29 16:27 (986 KB, all 11 groups populated).
- [x] Analyst sanity-check + QA SQL spot-check pass. — rolling-6m reconstructed independently for t3 (10/11 months exact, last off 0.0049 = reconstruction rounding only); shapes spot-checked on t3 + s1.
- [x] `.bak` confirmed before the write. — `_refresh_status.json` shows `directors.db.bak` written on the 2026-05-29 run.

### Phase 3 — Level-1 performance-table augmentation (B-067)

**Goal:** The performance table gains a sparkline column + trend column, and each row opens Level 2 on click (mechanism per Phase 1's inline-expand-vs-focus-mode decision).

**Agent dispatch:**
- **Front-end Engineer** — inline-SVG sparkline (~120×30, no axes, faint zero baseline, signal-tier colour, endpoint dots); trend column (arrow + delta, green > +1% / red < −1% / grey otherwise); row-click handler opening Level 2. Tests: sparkline renders for known data; trend-arrow direction matches delta sign; click fires the correct signal.
- **Dashboard Designer** (consulted) — confirm implementation matches the Phase 1 inline-expand-vs-focus-mode call.
- **QA — BEFORE the gate:** read `render_helpers.py` + the updated table partial; verify all 11 signals render a sparkline + trend; confirm row-click triggers Level 2; truncation check.

**De-risk:** build a 1-row sparkline POC first (~30 min) — tiny-cell SVG sizing is fiddly across browsers.

**FUSE:** **Zone A** — Claude-safe.

**Definition of Done:**
- [ ] Sparkline + trend columns render for all 11 signals.
- [ ] Row-click opens Level 2 via the agreed mechanism.
- [ ] Tests green; QA pass; Rupert visual review.

### Phase 4 — Level-2 main chart + N strip (B-068)

**Goal:** The cohort chart (State A of the design spec): per-month dots, min/max whiskers, low-N rings, 3-month MA overlay, single-ticker `!` glyph, dynamic y-axis, and the N strip below sharing the x-axis.

**Agent dispatch:**
- **Front-end Engineer** — Chart.js whisker plugin (~40 lines, `afterDatasetsDraw`); dot rendering (filled r=4 for N≥5, open ring r=3 for N<5); whisker styling (solid N≥5, dashed N<5); 3-month MA faint overlay; single-ticker `!` glyph above whisker when one ticker > 50% weight; N strip (low-N bars dashed + red labels); dynamic y-axis (data range + 10% headroom). Tests: render T1B synthetic, verify low-N rendering, verify `!` triggers.
- **Dashboard Designer** (consulted) — rulings on any rendering ambiguity (e.g. ring stroke weight, glyph placement).
- **QA — BEFORE the gate:** open the chart for T1B, T3, F1; verify all visual states appear in at least one; confirm `!` appears on T3's TIN month (canonical single-ticker-dominance test case); truncation check.

**Risk:** if the whisker plugin grows past ~100 lines or burns > 4h, escalate to Product Manager — consider an alternative before sinking more time.

**FUSE:** **Zone A** — Claude-safe.

**Definition of Done:**
- [ ] Main chart renders dots + whiskers + low-N rings + MA + `!` glyph + dynamic y-axis.
- [ ] N strip aligns to the main chart x-axis; low-N bars styled.
- [ ] Tests green; QA pass on T1B/T3/F1; Rupert visual review on 3 groups.

### Phase 5 — Rolling-6m hit-rate panel (B-069)

**Goal:** The smaller hit-rate sub-chart below the main chart (State A, second card): rolling 6-month hit rate, teal/green, 50% dashed baseline, shared x-axis.

**Agent dispatch:**
- **Front-end Engineer** — Chart.js line chart (teal-600, distinct from CAR series), 50% dashed baseline plugin, x-axis synced to main chart. Tests.
- **Analyst — BEFORE the gate:** cross-check one month's rolling hit rate against a manual count from the source data.
- **QA — BEFORE the gate:** confirm the panel aligns with the main chart x-axis; verify the analyst's cross-checked month matches; truncation check.

**FUSE:** **Zone A** — Claude-safe.

**Definition of Done:** — ✅ CODE CLOSED 2026-05-31 (QA-verified); ⏳ Rupert visual review pending
- [x] Hit-rate panel renders with 50% baseline, teal series, shared x-axis. — `_cohort_focus_overlay()` canvas + `_cohort_level2_script()` `hitChart` (teal #0d9488, `baseline50Plugin`, shared `labels`/x-axis, `spanGaps:false`).
- [x] One month cross-checked against manual count. — t3 rolling-6m reconstructed from per-month (n, hit) and matched within rounding (analyst check 2026-05-31).
- [x] Tests green; QA pass — `TestPhase5HitRatePanel` (tests 35–44) green; independent QA PASS 2026-05-31. ⏳ **Rupert visual review on the live dashboard still outstanding.**

### Phase 6 — Drill-down modal (B-070)

**Goal:** Click any cohort dot → modal with trade-level detail, including the **Contribution-to-mean** column and the pre-computed verdict footer.

**Agent dispatch:**
- **Front-end Engineer** — modal HTML (close-on-X / click-outside / Esc); trade table (ticker, director+role, fire date, CAR @ T+1/T+21/T+90, sector benchmark, net of costs, contribution-to-mean); sortable columns, default sort = contribution descending.
- **Back-end Engineer** — contribution-to-mean maths (`r_i / (N × cohort_mean)` as %, sums to 100%) and the verdict-string logic (already emitted in Phase 2's JSON; verify the renderer consumes it correctly).
- **Analyst + Trader — BEFORE the gate:** Analyst confirms contributions sum to 100% and the dominant ticker is correctly identified; Trader sanity-checks that the verdict line is *honest* (does "1 ticker drove 78%…" read correctly for a real-money user?) and that net-of-cost framing is right.
- **QA — BEFORE the gate:** open a known single-ticker-dominant cohort (T3's TIN month) in live data; verify contributions sum to 100%; verify the verdict line names the dominant ticker; truncation check.

**FUSE:** **Zone A** — Claude-safe. (Drill-down reads the pre-baked `cohort_performance.json` blob from Phase 2 — no new DB read at render time.)

**Definition of Done:**
- [ ] Modal opens on dot click, closes on X / outside / Esc.
- [ ] Trade table renders all columns; contribution sums to 100%; default sort correct.
- [ ] Verdict footer reads from upstream JSON and names the dominant ticker.
- [ ] Analyst + Trader sanity-check + QA pass; Rupert drills into 3 cohorts live.

### Phase 7 — Feature flag + production swap (B-071) [STRETCH]

**Goal:** New chart goes live behind a feature flag; the old chart stays accessible for A/B comparison for one release.

**Agent dispatch:**
- **Front-end Engineer** — feature-flag plumbing in `render_index.py`; both charts render until the flag is flipped; regression test that the existing chart still works when flag = off.
- **Product Manager** — owns the release call (flip now vs next sprint).
- **QA — BEFORE the gate:** read the diff; confirm no regression in the existing dashboard; confirm both charts toggle cleanly; truncation check.

**FUSE:** **Zone A** — Claude-safe.

**Definition of Done:** — ✅ CODE + TESTS CLOSED 2026-05-31 (647 green Windows-side); ⏳ Rupert A/B eyeball pending
- [x] Feature flag added; old + new charts both renderable; flag toggles cleanly. — `SHOW_LEGACY_CAR_LINE_CHART` (default True) in `render_performance.py`; gates `_diagnostics_chart_section` in `render()`; `rebuildDiag()` early-returns on missing `#diagChart` so flag-off never throws.
- [x] Regression test green with flag off. — `test_phase7_feature_flag.py` (7 tests: flag-on shows `#diagChart`; flag-off drops it but keeps the cohort surface; guard present; on/off/on toggle clean). **Full suite 647 OK on Windows 2026-05-31** (prior 640 + 7 new; the sandbox FUSE-truncation block did not reproduce Windows-side).
- [x] QA pass. — Read-tool verification of all edits + Windows-side 647-green. ⏳ **Rupert A/B eyeball on live data still outstanding** (set flag False + re-run build_dashboard.py only when ready to retire the legacy chart).

> **Flip decision (Product Manager / Rupert):** flag ships **on** (legacy chart still visible) so nothing changes until you decide to retire it. To swap: set `SHOW_LEGACY_CAR_LINE_CHART = False` and re-run `build_dashboard.py`.

---

## Definition of Done — applies to EVERY phase

- [ ] Code implemented and reviewed by the **builder agent**, then **truncation check via the Read tool** (mandatory — not bash `cat`/`wc`).
- [ ] Tests written and passing (`python -m unittest discover -s .scripts -p "test_*.py"` from bash — audited-safe, Zone A).
- [ ] **QA gate completed by a separate QA agent BEFORE the gate decision** (per CLAUDE.md — builder never grades own work).
- [ ] Rupert visual/manual review on the running dashboard.
- [ ] Backup of `directors.db` confirmed before any DB-write step (Phase 2 only).
- [ ] **No use of bash for Zone B (data) operations.** Phase 2's export is run by Rupert in PowerShell.

---

## Key dates

| Date | Event |
|------|-------|
| **Fri 2026-05-29** | Sprint start. **Phase 0 ships same day** (divergence warning live before Rupert places any trades). Phase 1 (re-run designer) kicks off in parallel. Assign B-064–B-071. |
| Mon 2026-06-01 | Check-in: Phases 0–1 done; Phase 2 code written, awaiting Rupert's PowerShell export run. |
| Wed 2026-06-04 | Mid-sprint review. Recalibrate if the Chart.js whisker-plugin risk (Phase 4) is materialising. |
| Mon 2026-06-08 | Phases 2–4 should be done; Phases 5–6 underway. Decide whether Phase 7 is in or out. |
| Thu 2026-06-11 | Phases 5–6 done. Phase 7 decision locked. |
| **Fri 2026-06-12** | Sprint end. New chart live (behind flag if Phase 7 made it, otherwise next sprint). Phase 0 warning becomes redundant once the new chart is the default — remove as a cleanup step. |
| Mon 2026-06-15 | Retro: what shipped, what slipped, what to change. |

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **FUSE corruption** on `.data/directors.db` if Claude writes via bash | Lost backtest history (burned 4×) | Phase 2 export run **only** from Rupert's PowerShell. Auto-backup confirmed; `.bak` before the run. QA verifies backup before crediting the gate. |
| **File-name collision with performance-redesign-v1** | Wrong files credited; tests assumed to cover work they don't | Pre-flight names the trap explicitly. Builder agents must confirm which feature a file belongs to before editing. |
| **Designer spec incomplete for Level 1** | Phase 3 stalls or builds the wrong thing | Phase 1 re-runs the designer; QA blocks Phase 3 until the Level-1 wireframe + inline-vs-focus call land. |
| **Chart.js whisker-plugin complexity underestimated** | Phase 4 slips, knocks on to 5–6 | ~40 lines estimated. If > 100 lines or > 4h, escalate to Product Manager before sinking more time. |
| **Sparkline rendering in tiny table cells** | Fiddly cross-browser CSS | 1-row POC before committing to full Phase 3 (~30 min de-risk). |
| **TIN/T3 illusion** persists until the new chart ships | Bad real-money trades on a misleading T3 mean | Phase 0 warning ships Day 1 specifically to cover this gap. |

---

## What's deliberately out of this sprint

- Small multiples grid (deferred to v2 per brief).
- Multi-signal overlay in Level 2 (out of scope per brief).
- Volume-weighted cohort means (separate methodology spec, not a chart concern).
- Mobile responsive pass below 600px (out of scope per brief).
- Migrating off Chart.js — if the plugin works, ship it; revisit only if Phase 4 risk fires.
- Cost-methodology changes — net-of-cost numbers are upstream and untouched.

## Suggested kickoff sequence

1. Read this plan end-to-end.
2. Confirm the **B-064–B-071** block (flag if a different block was intended — the brief said B-060 but the live backlog already uses B-060–B-063).
3. **Ship Phase 0 first** (divergence warning — protects real-money decisions while the rest runs). Target live by end of Day 1.
4. Kick off Phase 1 in parallel: re-run the dashboard-designer with brief v2 to complete the Level-1 spec.
5. Do not start Phase 3 until Phase 1's QA gate clears.
