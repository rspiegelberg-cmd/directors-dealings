# Sprint 24 — "Performance lens polish"

**Status:** ✅ CLOSED 2026-06-03  
**Tests:** 277 green  
**Gate:** Rupert runs `python .scripts\export_dashboard_json.py` → `python .scripts\build_dashboard.py`

---

## Items shipped

| ID | Item | Files changed |
|----|------|---------------|
| **B-083** | Remove dead `render_helpers.sparkline_svg()` | `.scripts/dashboard/render_helpers.py`, `.scripts/test_stage_05.py` |
| **B-072** | Horizon toggle T+1/T+21/T+90/T+252 on Level-2 cohort chart | `.scripts/dashboard/render_performance.py` — `_LEVEL2_MONTH_KEYS` extended; horizon toggle buttons + `syncHorizonButtons()` added to `_cohort_focus_overlay()` + `_cohort_focus_script()` |
| **B-076** | localStorage persistence: selected signal + horizon | `.scripts/dashboard/render_performance.py` — `_cohort_focus_script()` saves/restores via `dd_cohort_group` + `dd_cohort_horizon` keys |
| **B-099** | B1 added to cohort chart; B2 suppression count annotation | `.scripts/export_dashboard_json.py` (COHORT_SIGNAL_GROUPS + _COHORT_SHORT_TO_LONG + labels); `.scripts/dashboard/render_performance.py` (`_row_for_signal` b2 branch) |
| **B-075** | CSV export button on drill-down pages | `.scripts/dashboard/render_performance_drilldown.py` — `_csv_export_script()` added; wired into `render_drilldown_page()` body |
| **B-085** | 277-test QA run | Sandbox verified — no new failures vs baseline |

---

## Implementation notes

**B-072 detail:** The JS `cohortPick(m, h, metric)` helper and `horizonChange` event listener were already built into `_cohort_level2_script()` during Sprint 14. The sole gap was Python only threading T+21 keys into `window.__cohortData`. `_LEVEL2_MONTH_KEYS` now includes all four horizons' min/max/hit/rolling-hit/stw/ma3 fields — the JSON already had them. The horizon toggle buttons dispatch `horizonChange`; the existing chart rebuild handles the rest.

**B-099 detail:** B2 (`b2_crowded_cluster_kill`) is a suppression signal with no buy-side CAR series. Per QA flag A from the roadmap, it is correctly excluded from `COHORT_SIGNAL_GROUPS`. The scoreboard trajectory cell for B2 now shows `"N S1 suppressed"` text using the existing `trades` count from the signals data. B1 data will be sparse initially; it will build up over months and display with the normal "N=X (building)" low-N warning (amber ⚠).

**Deploy sequence (Rupert runs):**
1. `python .scripts\export_dashboard_json.py` — regenerates `signals.json` + `cohort_performance.json` (adds b1 group)
2. `python .scripts\build_dashboard.py` — rebuilds all HTML

---

## Next: Sprint 25 — "PDMR editor" (spec 10)

Phase 0: read-only queue view first. Every edit staged, audited, reversible. Requires Sprint 21's backup guarantee (done).
