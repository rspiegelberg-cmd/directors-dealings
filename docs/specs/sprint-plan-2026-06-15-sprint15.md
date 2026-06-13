# Sprint 15 — Horizon toggle on the cohort charts (DRAFT scope for Rupert review)

**Status:** DRAFT scoping — not yet committed. Review + adjust the B-block, phase
order, and dates before kickoff. Nothing here is built.
**Proposed dates:** Mon 2026-06-15 → Fri 2026-06-26 (2 weeks; starts after Sprint 14 closes 2026-06-12).
**Author:** Project Manager (Claude) + Rupert (PM, decisions, Windows-side DB ops).
**Origin:** Rupert request 2026-05-29 (deferred from Sprint 14 as "Phase 8 / dedicated item").
**Inputs:** `docs/specs/sprint-plan-2026-05-29-sprint14.md` (cohort-chart build it extends), `docs/specs/cohort-performance-chart-design-spec.md`, `CLAUDE.md` (FUSE two-zone rule, QA-before-gate, truncation discipline), `docs/backlog.md` (B-NNN numbering).

---

## One-line goal

Let Rupert switch the Level-2 cohort chart (and its N strip + rolling-hit-rate panel) between **T+1 / T+21 / T+90 / T+252** using the horizon dropdown that already drives the scoreboard and the legacy diagnostics chart — so every cohort metric can be read at the holding period that matters, not just T+21.

---

## Pre-flight — fresh source audit (2026-05-31)

These were confirmed against source on 2026-05-31 and **correct a stale assumption** in the original backlog note ("add a T+252 horizon to the backtest"). T+252 already exists end-to-end in the backtest — the real work is upstream aggregation + front-end wiring, not the backtest.

| Fact | Detail | Source |
|------|--------|--------|
| **Backtest already computes all 4 horizons** | `OFFSETS = (1, 21, 90, 252)`; CSV carries `car_t252`, `net_car_t252`, `benchmark_t252`, `benchmark_return_t252`. **No backtest change needed.** | `.scripts/backtest.py:54,72-79` |
| **Loader gap** | `load_backtest_csv` declares `HORIZONS=["t1","t21","t90","t252"]` but only hydrates `_net_car_t1/t21/t90` + `_car_t252` — **`_net_car_t252` is not exposed**, and the only benchmark hydrated is `_bench_t21`. | `.scripts/export_dashboard_json.py:98,168-174` |
| **Export gap (the bulk of the work)** | `build_cohort_performance` emits `mean_car_t1/t21/t90` per month, but **`min`/`max`/`hit_rate`/`rolling_6m`/`single_ticker_weight`/`ma3` are T+21-ONLY**, and there is **no `mean_car_t252`**. The chart can only switch horizon if these exist per-horizon. | `.scripts/export_dashboard_json.py:1980-1993` |
| **Front-end dropdown already exists** | `#horizon` select + `HORIZON_LABELS` (all 4 horizons) + a `horizonChange` custom event are already wired; the scoreboard, legacy diag chart, and cohort *tiles* subscribe. | `render_performance.py:2199,2488`; `render_helpers.py:143-148` |
| **Cohort Level-2 chart is hardwired to T+21** | `build()` reads `m.mean_car_t21`, `m.min_car_t21`, `m.max_car_t21`, `m.hit_rate_t21`, `m.single_ticker_weight`, `m.ma3_mean_car_t21`, `m.hit_rate_t21_rolling_6m` directly. It does **not** subscribe to `horizonChange`. | `render_performance.py:~1490-1750` |
| **T+252 maturity caveat** | The dataset is ~12 months deep; 252 trading days ≈ 1 calendar year, so **almost no fire has matured to T+252 yet**. T+252 will render near-empty today and fill over the next year. Plumb it in now; expect blanks initially. The chart's existing low-N rings / empty-state handling already covers this — no special-casing beyond a clear "insufficient maturity" empty message. | derived |

### B-NNN block (proposed): **B-072 → B-077**
Live backlog max is **B-063**; Sprint 14 used B-064–B-071. Next free block is **B-072**. Flag if you want a different block.

| Phase | B-number | Title |
|-------|----------|-------|
| 0 | **B-072** | Decision gate: which horizons ship in the toggle (all 4 vs T+21/T+90 first) |
| 1 | **B-073** | Loader + export: per-horizon cohort aggregates (Zone B re-run) |
| 2 | **B-074** | Front-end: make the Level-2 chart + N strip + hit-rate panel horizon-aware |
| 3 | **B-075** | Front-end: tooltip / labels / empty-state per horizon + T+252 low-maturity messaging |
| 4 | **B-076** | Tests + analyst per-horizon cross-check |
| 5 | **B-077** | Persist last-selected horizon + polish [STRETCH] |

---

## Phased delivery — gates between each (per CLAUDE.md "QA agent before every gate")

### Phase 0 — Decision gate: horizon scope (B-072)
**Question for Rupert (PM call):** ship the toggle for **all four** horizons at once, or **T+21 + T+90 first** (the two with real matured data) and add T+1/T+252 once the front-end is proven? Recommendation: **all four** — the export work is the same effort regardless, and T+1/T+252 simply render thin/empty until data matures, which the low-N handling already communicates honestly. Hiding them would mean a second front-end pass later.
**Deliverable:** a one-line decision recorded here. **Zone A (doc).**

### Phase 1 — Loader + export: per-horizon cohort aggregates (B-073)
**Goal:** every per-month metric the chart consumes exists for each chosen horizon, not just T+21.
**Work:**
- **Loader:** hydrate `_net_car_t252` (and a clean per-horizon accessor so the emit isn't littered with `t21`-specific names). Confirm whether per-horizon hit rate needs a per-horizon benchmark or whether `net_car_th > 0` is the agreed "beat benchmark" definition at every horizon (it is for T+21 today — confirm the costing/benchmark is horizon-correct for T+1/T+90/T+252).
- **Export:** generalise `build_cohort_performance` so each `months[]` entry carries, **per horizon**, `mean_car`, `min_car`, `max_car`, `hit_rate`, `hit_rate_rolling_6m`, `single_ticker_weight`, `ma3_mean_car`. Cleanest shape: nest under `months[].by_horizon.{t1,t21,t90,t252}` (additive; keep the existing flat `*_t21` keys during a transition so Sprint 14's chart never breaks). Header rollups (`mean_car_overall`, `hit_rate_overall`) per horizon too.
**Agent dispatch:** Back-end Engineer (emit + loader); **Analyst** (sanity-check one horizon's numbers vs manual SQL **before** the gate); **QA** (spot-check 2 groups × 2 horizons in the emitted JSON, confirm additive — no Sprint-14 field removed).
**FUSE — ZONE B:** `export_dashboard_json.py` writes under `.data/`. **Rupert runs `python .scripts\export_dashboard_json.py` from PowerShell.** Claude writes the code (Zone A) + tests; Rupert runs the export. `.bak` confirmed before the run.
**DoD:** new per-horizon fields emitted; unit tests green on synthetic data (Claude, Zone A); Rupert runs the export; analyst + QA spot-checks pass; existing T+21 chart still renders unchanged (additive proof).

### Phase 2 — Front-end: horizon-aware Level-2 chart (B-074)
**Goal:** the main scatter (means + whiskers + dominance `!`), the 3m-MA overlay, the N strip, and the rolling-hit-rate panel all read the **active horizon's** fields and rebuild when `horizonChange` fires.
**Work:** add an `activeHorizon` to the cohort builder (default T+21); field reads go through a `pick(m, horizon)` helper instead of hardcoded `*_t21`; subscribe `build(currentGroup)` to the existing `horizonChange` event; the y-axis re-scales per horizon (T+252 ranges are much wider). Reuse the existing dropdown — **no new control**.
**Agent dispatch:** Front-end Engineer; Dashboard Designer (consulted — does the chart title/subtitle state the horizon? y-axis re-scale behaviour on switch). **QA** before gate: switch all 4 horizons on T3 + one low-N group; confirm whiskers/dominance/MA/N-strip/hit-rate all move together and the axis re-scales; truncation check via Read tool.
**FUSE:** **Zone A** — Claude-safe.
**DoD:** all chart elements switch with the dropdown; no console errors on any horizon; tests green; Rupert visual review.

### Phase 3 — Tooltip / labels / empty-state per horizon + T+252 messaging (B-075)
**Goal:** the DOM tooltip, header strip, and empty-state all name the active horizon, and T+252 (and any thin horizon) shows an honest "insufficient maturity — N fires have reached T+252" message rather than a misleading near-empty chart.
**Agent dispatch:** Front-end Engineer; **Trader + Analyst** (consulted — is the low-maturity wording honest for a real-money reader? does a 2-fire T+252 mean get an explicit don't-trust-this treatment?). **QA** before gate.
**FUSE:** **Zone A.**
**DoD:** tooltip/header/empty-state horizon-correct; thin-horizon honesty message present; QA + Rupert review.

### Phase 4 — Tests + analyst per-horizon cross-check (B-076)
**Goal:** lock the behaviour. Extend `test_cohort_performance_export.py` (per-horizon emit) and the front-end render tests (horizon-aware field reads, `horizonChange` subscription, axis re-scale). Analyst independently reconstructs one month's mean + hit rate at a **non-T+21** horizon from source and confirms it matches the emitted value.
**FUSE:** **Zone A** (tests). The full sweep runs Windows-side (the FUSE truncation seen in Sprint 14 Phase 7 means freshly-edited large files may not run cleanly in the sandbox — confirm green on Windows).
**DoD:** tests green Windows-side; analyst cross-check passes; QA sign-off.

### Phase 5 — Persist last-selected horizon + polish (B-077) [STRETCH]
Remember the chosen horizon across page loads (localStorage, mirroring the existing diag legend persistence); minor polish. **Zone A.** Product Manager owns whether this is in or deferred.

---

## Definition of Done — applies to EVERY phase
- Code implemented + reviewed by the builder agent, then **truncation check via the Read tool** (not bash).
- Tests written + passing (`python -m unittest discover -s .scripts -p "test_*.py"`); large-file edits confirmed green **Windows-side**.
- **QA gate by a separate QA agent BEFORE the gate decision** (builder never grades own work).
- Rupert visual/manual review on the running dashboard.
- `directors.db` `.bak` confirmed before the Phase 1 export run.
- **No bash writes to Zone B (`.data/`).** The export is run by Rupert in PowerShell.

---

## Risks
| Risk | Impact | Mitigation |
|------|--------|------------|
| **Additive-JSON regression** breaks Sprint 14's T+21 chart | Live dashboard breaks | Keep the flat `*_t21` keys during transition; QA proves the existing chart renders unchanged before crediting Phase 1. |
| **T+252 near-empty misread as "no edge"** | Bad real-money inference | Phase 3 honest "insufficient maturity (N matured)" messaging; low-N rings already in place. |
| **Per-horizon benchmark/cost correctness** | Wrong hit rates at non-T+21 horizons | Phase 1 explicitly confirms the "beat benchmark" definition + costing is horizon-correct, not a T+21 assumption copied across. |
| **FUSE truncation on the big edited files** (`export_dashboard_json.py`, `render_performance.py`) | Sandbox can't run tests | Verify via Read tool; run the suite Windows-side (as in Sprint 14 Phase 7). |
| **y-axis re-scale jank on switch** | Confusing UX | Dashboard Designer rules on re-scale (snap vs animate) in Phase 2. |

## Out of scope
- New backtest horizons beyond the existing four.
- Volume-weighted cohort means (separate methodology).
- Small-multiples / multi-horizon-at-once view.
- Mobile pass below 600px.
- Per-horizon changes to the Level-1 sparkline/trend table (they stay fixed at T+21 unless Rupert asks — raise as a follow-up).

## Suggested kickoff sequence
1. Rupert reviews this draft; confirm the **B-072–B-077** block and the Phase 0 horizon-scope decision (all 4 vs phased).
2. Phase 1 backend (Claude writes code + tests; **Rupert runs the export**).
3. Gate, then Phase 2 front-end. Do not start Phase 2 until Phase 1's JSON exists and QA clears.
