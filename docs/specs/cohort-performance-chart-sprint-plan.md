# Sprint 11 — Cohort performance chart redesign

**Dates:** Thu 2026-05-28 → Wed 2026-06-10 (2 weeks)
**Team:** Rupert (review, decisions, Windows-side DB ops) + Claude (implementation)
**Inputs:**
- `docs/specs/cohort-performance-chart-redesign-brief.md` (locked, v2 with Level 1)
- `docs/specs/cohort-performance-chart-design-spec.md` (designer's spec — covers Level 2 only, Level 1 needs a re-run)

## Sprint goal

Replace the misleading trailing-12-month CAR chart with a two-level cohort view that exposes single-ticker contamination and lets Rupert interrogate any cohort with one click. **Success looks like:** Rupert can scan all 11 signals' trajectories in the augmented performance table, click any row to see its monthly cohort detail, and surface single-ticker outliers (the TIN/T3 problem) without leaving the dashboard.

## Capacity model

Solo dev, so "story points" doesn't fit. Capacity is governed by Rupert's review-and-DB-ops time, not Claude's coding time.

| Resource | Available | Allocation this sprint | Notes |
|----------|-----------|------------------------|-------|
| Rupert | ~3 hours/day × 10 working days = ~30 hours | Review code, run DB writes via PowerShell, decision gates | Bottleneck. Plan to 70% = ~21 hours of meaningful Rupert engagement. |
| Claude | Unlimited within session limits | Coding, tests, QA agents, docs | Not the constraint. |

Total Rupert engagement budget: **~21 hours** (70% of available). The plan below sums to ~16 hours of Rupert time, leaving ~5 hours of buffer for slippage, mid-sprint review, and unplanned interrupts (which always happen on this project — see B-024 backup audit history).

## Sprint backlog — phased delivery with gates between each

Numbering follows the project's `B-XXX` convention; the next available B-number block is yours to assign at kickoff. Each phase has its own QA gate (per CLAUDE.md "QA agent before every gate decision" — established 2026-05-25).

### Phase 0 — Interim reliability warning on existing performance table (B-NN) [SHIP DAY 1]

**Goal:** Protect Rupert from real-money decisions based on misleading mean CAR figures while Sprint 11 is in flight. T3 currently shows mean +7.9% at T+90 but median is −5.0% — a 12.9pp gap driven by a single TIN trade at +1218.9%. Until Phase 6 ships the drill-down, this warning is the only thing on the dashboard that flags the contamination.

**Trigger rule** (per-signal, computed at render time from existing backtest data):

> Fire warning if **|mean − median| > 5pp AND |mean − median| > 0.5 × |mean|**.
>
> Both conditions in conjunction. The first catches absolute divergence; the second catches relative divergence so we don't flag tiny means (e.g. mean +0.5%, median 0% — 0.5pp gap, ignore).

Applied to all signals uniformly. T3 fires (12.9pp gap, mean +7.9%). F1 does not fire (mean −1.15% with N=723 — small mean, small gap, real edge problem not outlier contamination — different kind of bad).

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Compute per-signal median CAR @ T+90 alongside the existing mean in `render_helpers.py` (or wherever the perf table is assembled) | Claude | 0.25 | — |
| Apply trigger rule; attach `divergence_warning: bool` and `gap_pp: float` to each row | Claude | 0.25 | — |
| Render `⚠` badge next to the mean in the table, with hover tooltip: "Mean diverges from median by X.Xpp — likely single-outlier contamination. Drill into the cohort before acting." | Claude | 0.25 | — |
| Tests: synthetic fixture with one contaminated signal (1 huge winner) and one clean signal; verify warning fires on first only | Claude | 0.25 | — |
| Rupert visual review on the live dashboard | — | — | 0.25 |

**QA gate:** Confirm `⚠` appears on T3 in the live dashboard. Confirm it does NOT appear on F1 (flat-and-bad ≠ outlier-contaminated). Read the diff to verify no other table behaviour changed.

**FUSE constraints:** Pure Zone A — `render_helpers.py` and template edits only. No DB writes. Safe for Claude bash to run tests.

**Scope discipline:** Do not extend this to "fix" the underlying signal taxonomy or hide T3 from the table. The goal is a one-line warning the user can see, nothing more. Anything else is Sprint 11 main work.

### Phase 1 — Design completeness (B-NN+1)

**Goal:** Designer spec covers BOTH levels of the architecture.

The dashboard-designer agent ran before the Level 1 (table augmentation) section was added to the brief. Its current output covers the Level 2 detail chart only. We need it to extend the spec to cover Level 1's sparkline column, trend column, and row-click interaction.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Re-spawn dashboard-designer with updated brief v2 | Claude | 1.5 | 0.5 (review output) |
| Verify designer addresses inline-expand vs focus-mode recommendation | Claude | 0.5 | 0.25 |

**QA gate:** Read the updated design spec end-to-end. Confirm Level 1 wireframe present and that the inline-expand vs focus-mode call is made. Block Phase 2 if either missing.

### Phase 2 — Upstream JSON fields (B-NN+2)

**Goal:** `export_dashboard_json.py` emits every field the new chart needs. Additive, no breaking changes.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Per (signal_group, month_iso) aggregates: n_signals, mean/min/max CAR @ T+1/T+21/T+90, hit_rate_t21, signal_id_list | Claude | 2 | — |
| Per signal_group rolling 6m hit rate | Claude | 1 | — |
| sparkline_points (reuses means) + trend_3m_vs_prior3m_t21 for Level 1 | Claude | 0.5 | — |
| Unit tests covering each aggregate against a 3-signal synthetic dataset | Claude | 1 | — |
| Rupert runs `python .scripts/export_dashboard_json.py` from PowerShell, inspects output JSON | — | — | 1.0 |

**QA gate:** Spot-check 2 signal groups in the emitted JSON against manual SQL. Verify sparkline_points has correct N and ordering. Backup `.data/directors.db` taken before any write-path script runs.

**FUSE constraints:** `export_dashboard_json.py` writes under `.data/` → **Rupert runs this from PowerShell, Claude does not**. Per the locked Zone B rule in CLAUDE.md.

### Phase 3 — Level 1 performance table augmentation (B-NN+3)

**Goal:** Performance table has sparkline + trend columns and each row opens Level 2 on click.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Sparkline component — inline SVG sized ~120×30, no axes, zero baseline, signal-tier colour, endpoint dots | Claude | 1.5 | — |
| Trend column — arrow + delta, green/red/grey based on ±1% threshold | Claude | 1 | — |
| Row click handler — opens Level 2 (mechanism per designer's inline-expand vs focus-mode recommendation) | Claude | 1.5 | — |
| Tests: sparkline renders for known data; trend arrow direction matches delta sign; click fires correct signal | Claude | 1 | — |
| Visual review on running dashboard | — | — | 1.0 |

**QA gate:** Read render_helpers.py and the updated table partial. Verify all 11 signals render. Confirm clicking a row triggers Level 2.

### Phase 4 — Level 2 main chart + N strip (B-NN+4)

**Goal:** The cohort chart from Figure 1 of the brief, working.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Chart.js whisker plugin (~40 lines per designer spec) — afterDatasetsDraw, draws whiskers + caps | Claude | 2 | — |
| Dot rendering: filled r=4 for N≥5, open ring r=3 for N<5 | Claude | 1 | — |
| Whisker styling: solid for N≥5, dashed for N<5 | Claude | 0.5 | — |
| 3-month MA overlay (faint line through means) | Claude | 1 | — |
| Single-ticker dominance `!` glyph above whisker when one ticker > 50% of contribution | Claude | 1 | — |
| N strip below main chart, shared x-axis, low-N bars dashed + red labels | Claude | 1.5 | — |
| Dynamic y-axis per active signal (data range + 10% headroom) | Claude | 0.5 | — |
| Tests: render T1B with synthetic data, verify low-N rendering, verify `!` triggers correctly | Claude | 1.5 | — |
| Visual review on 3 different signal groups | — | — | 1.5 |

**QA gate:** Open the chart for T1B, T3, and F1. Verify all visual states present in at least one of them. Confirm `!` glyph appears on the months we know have single-ticker dominance (T3's TIN month is the canonical test case).

### Phase 5 — Hit rate panel (B-NN+5)

**Goal:** Rolling 6m hit rate sub-chart below main, per Figure 2.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Smaller Chart.js line chart, teal/green colour, 50% dashed baseline | Claude | 1.5 | — |
| Shared x-axis sync with main chart | Claude | 0.5 | — |
| Tests | Claude | 0.5 | — |
| Visual review | — | — | 0.5 |

**QA gate:** Confirm hit rate panel aligns with main chart x-axis. Cross-check one month's hit rate against manual count.

### Phase 6 — Drill-down modal (B-NN+6)

**Goal:** Click any cohort dot → modal with trade-level detail, including the Contribution-to-mean column.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Modal HTML + close-on-X / close-on-click-outside | Claude | 1 | — |
| Trade table — ticker, director, date, CAR at T+1/T+21/T+90, sector benchmark, net of costs, contribution to mean | Claude | 1.5 | — |
| Compute contribution to mean: `r_i / (N × cohort_mean)` as % | Claude | 0.5 | — |
| Sortable columns, default sort = contribution descending | Claude | 1 | — |
| Verdict footer logic: detect single-ticker dominance (>40% of mean) and write the one-line summary | Claude | 1 | — |
| Tests: contribution maths sums to 100%, dominance detection fires correctly | Claude | 1 | — |
| Drill into 3 cohorts on the live dashboard | — | — | 1.5 |

**QA gate:** Open M-2 equivalent in current live data. Verify contributions sum to 100%. Verify verdict line correctly identifies the dominant ticker.

### Phase 7 — Feature flag + production swap (B-NN+7) [STRETCH]

**Goal:** New chart goes live behind a feature flag, old chart still accessible for A/B comparison for one release.

| Item | Owner | Est. (h) | Rupert time (h) |
|------|-------|----------|-----------------|
| Feature flag plumbing in render_index.py | Claude | 1 | — |
| Both charts render side-by-side on the perf page until flag flipped | Claude | 0.5 | — |
| Regression test: existing chart still works when flag = off | Claude | 1 | — |
| Manual A/B with Rupert eyeballing both charts on live data | — | — | 1.5 |

**QA gate:** Read the diff. Confirm no regressions in the existing dashboard. Confirm both charts can be toggled.

## Capacity reconciliation

| Resource | Plan total | Budget | Slack |
|----------|-----------|--------|-------|
| Rupert review/DB time | 0.25 h (Phase 0) + 7.75 h (Phases 1-6) + 1.5 h (Phase 7 stretch) = 9.5 h | 21 h | 11.5 h cushion |
| Claude implementation | 1 h (Phase 0) + ~28 h (Phases 1-7) = ~29 h | unlimited | n/a |

Rupert engagement plan well under budget — deliberate, since this sprint introduces meaningful new frontend code and the FUSE corruption history means there's overhead in every DB-touching step.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **FUSE corruption** on `.data/directors.db` if Claude accidentally writes via bash | Could lose backtest history again (4× burned previously) | All write-path scripts run from Rupert's PowerShell. Auto-backup confirmed working (B-024). Code-review gate before each phase ships. |
| **Designer spec incomplete for Level 1** | Phase 3 stalls or builds wrong thing | Phase 1 explicitly re-runs the designer. Don't start Phase 3 until Phase 1's QA gate clears. |
| **Chart.js whisker plugin complexity underestimated** | Phase 4 slips, knock-on to 5-6 | Designer estimated ~40 lines. If it grows past 100, escalate — possibly use Recharts or D3 instead. Trigger: > 4 hours on the plugin alone. |
| **Sparkline rendering in tiny table cells** | CSS sizing is fiddly across browsers | Build a 1-row POC of sparkline rendering before committing to Phase 3 full implementation. ~30 min investment, big de-risking. |
| **Real-money decisions paused** | Rupert delays trades on T3 et al. while waiting for the chart | This sprint should be high-priority precisely because of this. Don't add unrelated work mid-sprint. |
| **TIN/T3 illusion** persists in production until new chart ships | Continued risk of bad trades based on misleading T3 mean | Consider an interim warning on the existing performance table for T3 (one-line text: "median diverges materially from mean — drill down before acting") — 30 min Phase 0 task if you want it. |

## Definition of Done — per phase

- [ ] Code implemented and reviewed by Claude (truncation check via Read tool — mandatory)
- [ ] Tests written and passing (Claude runs `python -m unittest discover -s .scripts -p "test_*.py"` from bash, per audited-safe list)
- [ ] QA gate completed by separate Claude agent (per CLAUDE.md QA-before-gate rule)
- [ ] Rupert visual/manual review on the running dashboard
- [ ] Backup of `directors.db` confirmed before any DB-write step
- [ ] No use of bash for Zone B (data) operations

## Key dates

| Date | Event |
|------|-------|
| Thu 2026-05-28 | Sprint start. **Phase 0 ships same day** (T3 warning live before Rupert places any trades). Phase 1 (re-run designer) kicks off in parallel. |
| Mon 2026-06-01 | Mid-week check: Phases 0–2 should be done; Phase 3 underway. |
| Wed 2026-06-03 | Mid-sprint review. Recalibrate if Chart.js plugin risk has materialised. |
| Mon 2026-06-08 | Phases 4-6 should be done. Decide whether Phase 7 is in or out. |
| Wed 2026-06-10 | Sprint end. New chart live (behind flag if Phase 7 made it, otherwise next sprint). Phase 0 warning becomes redundant once the new chart is the default — remove it as a cleanup step. |
| Thu 2026-06-11 | Personal retro: what shipped, what slipped, what to do differently. |

## What's deliberately out of this sprint

- Small multiples grid (deferred to v2 per brief decision).
- Multi-signal overlay in Level 2 (out of scope per brief).
- Volume-weighting methodology (separate spec, not a chart concern).
- Mobile responsive pass below 600px (out of scope per brief).
- Migrating away from Chart.js to a different library — if the plugin works, ship it; revisit only if Phase 4 risk fires.

## Suggested kickoff sequence

1. Read this sprint plan end-to-end.
2. Assign B-NN block.
3. **Ship Phase 0 first** (interim T3 warning — protects real-money decisions while the rest of the sprint runs). Target: live on the dashboard by end of Day 1.
4. Kick off Phase 1 in parallel with Phase 0 if Claude bandwidth allows: re-spawn the dashboard-designer agent with the updated brief v2.
