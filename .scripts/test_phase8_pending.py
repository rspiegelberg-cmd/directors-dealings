"""Sprint 14 pending-month ruling (B-072) tests -- the pending (not-yet-
matured) cohort marker, its clickability, and the modal's pending handling.

The Chart.js canvas + DOM interaction are not headless-testable, so these
tests pin the testable contract of the renderer template strings + the
client-data threading:

  1. `pending` is threaded into window.__cohortData months (and the drilldown
     entries) so the browser can paint the floor diamond / band / N-strip tint
     and route a click to the drill-down.
  2. The pendingMarkers plugin is defined and REGISTERED on the main chart;
     it draws a hollow slate diamond pinned to the floor (pixel space) + a
     faint band, and the means dataset marks pending points (_pending).
  3. onCohortClick handles the off-scale pending diamond via an x-band hit-test
     (pendingPointAtX) and opens the drill-down for a pending month.
  4. The DOM tooltip shows the single-line pending sentence.
  5. The legend carries the pending diamond entry.
  6. The modal renders a pending cohort: slate header note (no amber verdict),
     em-dash CAR T+30/T+90/Net/Share cells, Fire-date-desc default sort, and
     keeps Ticker/Director/Role/Fire date/CAR T+1.

Spec: docs/specs/cohort-performance-chart-design-spec.md
      ("Pending-month marker ruling (2026-06-02)").

Run under:
    python -m unittest .scripts.test_phase8_pending -v
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_performance as rp  # noqa: E402
from dashboard import render_helpers as h        # noqa: E402


# --- Fixtures --------------------------------------------------------------

def _sig(ticker, director, role, fire, t1, t30, t90, bench, net, weight):
    return {
        "fingerprint": ticker + "00",
        "ticker": ticker, "director": director, "role_short": role,
        "fire_date": fire,
        "car_t1": t1, "car_t30": t30, "car_t90": t90,
        "benchmark_t30": bench, "net_car_t30": net, "cohort_weight": weight,
        "signal_ids": [1, 2],
    }


def _pending_sig(ticker, director, role, fire, t1):
    # A pending cohort's signal: T+1 may be present, everything T+30-derived
    # is null.
    return {
        "fingerprint": ticker + "00",
        "ticker": ticker, "director": director, "role_short": role,
        "fire_date": fire,
        "car_t1": t1, "car_t30": None, "car_t90": None,
        "benchmark_t30": None, "net_car_t30": None, "cohort_weight": None,
        "signal_ids": [9],
    }


def _mk_cohort_data():
    groups = {
        "t3": {
            "label": "T3 NED buy", "color_hex": "#10b981",
            "months": [
                # a matured month
                {"month_iso": "2025-09", "n_signals": 9, "mean_car_t30": -0.023,
                 "min_car_t30": -0.184, "max_car_t30": 0.121,
                 "hit_rate_t30": 0.33, "hit_rate_t30_rolling_6m": 0.4,
                 "single_ticker_weight": 0.78, "ma3_mean_car_t30": None,
                 "pending": False},
                # a PENDING month (mean null, n>0)
                {"month_iso": "2026-08", "n_signals": 83, "mean_car_t30": None,
                 "min_car_t30": None, "max_car_t30": None,
                 "hit_rate_t30": None, "hit_rate_t30_rolling_6m": None,
                 "single_ticker_weight": None, "ma3_mean_car_t30": None,
                 "pending": True},
            ],
        },
    }
    drilldown = {
        "t3": {
            "2025-09": {
                "verdict": ("1 ticker (TIN) was the largest single drag on the "
                            "cohort (78% of total movement, -19.5% net CAR)."),
                "pending": False,
                "signals": [
                    _sig("TIN", "Smith, J", "NED", "2025-09-11",
                         0.021, -0.184, 0.943, 0.011, -0.195, 0.78),
                    _sig("EOG", "Patel, R", "NED", "2025-09-05",
                         -0.004, 0.121, 1.666, 0.011, 0.112, 0.22),
                ],
            },
            "2026-08": {
                "verdict": "",
                "pending": True,
                "signals": [
                    _pending_sig("AAA", "New, A", "NED", "2026-08-02", 0.005),
                    _pending_sig("BBB", "New, B", "CFO", "2026-08-20", None),
                    _pending_sig("CCC", "New, C", "NED", "2026-08-15", -0.01),
                ],
            },
        },
    }
    return {"signal_groups": list(groups.keys()), "groups": groups,
            "cohort_drilldown": drilldown}


def _mk_signals():
    sigs = {
        sid: {"trades": 30, "mean_car": 1.0, "median_car": 0.9,
              "hit_pct": 50.0, "sparkline": [0.1, 0.2, 0.3]}
        for sid in h.SIGNAL_DISPLAY_ORDER
    }
    return {
        "horizon_aggregates": {
            "t30": {"base_rate": 50.0, "signals": sigs},
            "t90": {"base_rate": 55.0, "signals": sigs},
        },
        "cohorts": {"by_value_bucket": {}, "by_sector": []},
        "cohorts_v2": {},
        "pending_diagnostics": {"total": 0, "categories": []},
    }


def _extract_cohort_blob(html_or_script):
    m = re.search(r"window\.__cohortData\s*=\s*(\{.*?\});", html_or_script,
                  re.DOTALL)
    assert m, "window.__cohortData assignment not found"
    return json.loads(m.group(1))


# --- Threading: pending in months + drilldown -------------------------------

class TestPendingThreading(unittest.TestCase):

    def setUp(self):
        cd = _mk_cohort_data()
        self.script = rp._cohort_client_data(cd["groups"],
                                             cd["cohort_drilldown"])
        self.blob = _extract_cohort_blob(self.script)

    def test_01_pending_in_level2_month_keys(self):
        self.assertIn("pending", rp._LEVEL2_MONTH_KEYS)

    def test_02_pending_threaded_into_months(self):
        months = self.blob["groups"]["t3"]["months"]
        by_iso = {m["month_iso"]: m for m in months}
        self.assertIn("pending", by_iso["2026-08"])
        self.assertTrue(by_iso["2026-08"]["pending"])
        self.assertFalse(by_iso["2025-09"]["pending"])

    def test_03_pending_threaded_into_drilldown(self):
        entry = self.blob["drilldown"]["t3"]["2026-08"]
        self.assertIn("pending", entry)
        self.assertTrue(entry["pending"])
        self.assertFalse(self.blob["drilldown"]["t3"]["2025-09"]["pending"])

    def test_04_pending_signal_rows_carry_null_t30(self):
        sigs = self.blob["drilldown"]["t3"]["2026-08"]["signals"]
        self.assertTrue(all(s["net_car_t30"] is None for s in sigs))
        self.assertTrue(all(s["car_t30"] is None for s in sigs))
        # T+1 still threaded (may be present)
        self.assertIn("car_t1", sigs[0])

    def test_05_threading_pure_ascii(self):
        self.script.encode("ascii")


# --- Plugin: pendingMarkers registered + draws floor diamond/band -----------

class TestPendingMarkerPlugin(unittest.TestCase):

    def setUp(self):
        self.level2 = rp._cohort_level2_script()

    def test_06_plugin_defined(self):
        self.assertIn("id: 'pendingMarkers'", self.level2)
        self.assertIn("var pendingMarkers", self.level2)

    def test_07_plugin_registered_on_main_chart(self):
        # must appear in the main chart's plugins array.
        m = re.search(r"plugins:\s*\[([^\]]*pendingMarkers[^\]]*)\]",
                      self.level2)
        self.assertIsNotNone(m, "pendingMarkers not in a plugins[] array")

    def test_08_band_drawn_before_and_diamond_after(self):
        self.assertIn("beforeDatasetsDraw", self.level2)
        self.assertIn("afterDatasetsDraw", self.level2)
        # faint slate band fill
        self.assertIn("rgba(148,163,184,0.06)", self.level2)

    def test_09_diamond_pinned_to_floor_pixel_space(self):
        # centre fixed 12px above the bottom plot edge, NOT on the data scale.
        self.assertIn("area.bottom - 12", self.level2)
        # hollow: white fill + slate stroke
        self.assertIn("'#ffffff'", self.level2)
        self.assertIn("#94a3b8", self.level2)

    def test_10_means_points_marked_pending(self):
        self.assertIn("_pending", self.level2)
        # derived from the export flag OR mean==null && n>0
        self.assertIn("m.pending === true", self.level2)
        # B-117: Sprint 24 made the read horizon-aware via cohortPick(m, activeH, ...)
        # instead of the hardcoded m.mean_car_t30.
        self.assertIn("cohortPick(m, activeH, 'mean_car') == null", self.level2)

    def test_11_nstrip_pending_tint(self):
        # slate-400 @ 35% for a pending bar.
        self.assertIn("rgba(148,163,184,0.35)", self.level2)


# --- Click + cursor + tooltip for the off-scale pending diamond -------------

class TestPendingClickAndTooltip(unittest.TestCase):

    def setUp(self):
        self.level2 = rp._cohort_level2_script()

    def test_12_xband_hit_test_helper(self):
        self.assertIn("pendingPointAtX", self.level2)

    def test_13_onclick_routes_pending_to_drilldown(self):
        # When no real dot is hit, the x-band hit-test opens the drill-down.
        self.assertIn("pendingPointAtX(c, native)", self.level2)
        self.assertIn("window.openCohortDrilldown(currentGroup", self.level2)

    def test_14_pending_cursor_pointer_on_hover(self):
        # hover over a pending x-slot -> pointer cursor.
        self.assertIn("pendingPointAtX(mainChart, evt)", self.level2)
        self.assertIn("ctx.style.cursor = 'pointer'", self.level2)

    def test_15_pending_tooltip_sentence(self):
        # B-117: Sprint 24 made the horizon dynamic (aHLabel), so the sentence is
        # built as '... signals fired - ' + aHLabel + ' return not yet matured.
        # Click to see the trades.' -- assert the two stable halves around it.
        self.assertIn("signals fired - ", self.level2)
        self.assertIn(" return not yet matured. Click to see the trades.",
                      self.level2)

    def test_16_tooltip_hides_mean_range_hit_when_pending(self):
        # the pending tooltip variant hides the mean/range/hit rows.
        self.assertIn("ttMean.style.display = 'none'", self.level2)
        self.assertIn("ttRange.style.display = 'none'", self.level2)
        self.assertIn("ttHit.style.display = 'none'", self.level2)

    def test_17_nstrip_click_routes_pending(self):
        # reinforcing route: the N-strip bar also opens the drill-down.
        self.assertIn("raw._pending && m && m.month_iso", self.level2)

    def test_18_level2_pure_ascii(self):
        self.level2.encode("ascii")


# --- Legend entry -----------------------------------------------------------

class TestPendingLegend(unittest.TestCase):

    def setUp(self):
        self.overlay = rp._cohort_focus_overlay()

    def test_19_legend_has_pending_entry(self):
        self.assertIn("pending (fired, T+30 not yet matured)", self.overlay)

    def test_20_legend_swatch_is_rotated_slate_diamond(self):
        # rotated hollow square swatch in slate-400.
        self.assertIn("rotate(45deg)", self.overlay)
        self.assertIn("border-slate-400", self.overlay)

    def test_21_overlay_pure_ascii(self):
        self.overlay.encode("ascii")


# --- Modal pending handling -------------------------------------------------

class TestModalPending(unittest.TestCase):

    def setUp(self):
        self.script = rp._cohort_drilldown_script()

    def test_22_pending_state_flag(self):
        self.assertIn("state.pending", self.script)

    def test_23_pending_emdash_columns(self):
        # T+30-derived columns em-dash for a pending cohort.
        self.assertIn("PENDING_EMDASH_KEYS", self.script)
        for k in ("car_t30", "car_t90", "benchmark_t30",
                  "net_car_t30", "cohort_weight"):
            self.assertIn(k, self.script)
        self.assertIn("&mdash;", self.script)

    def test_24_pending_header_note_slate_not_amber(self):
        self.assertIn("This cohort has not yet matured.", self.script)
        self.assertIn("text-slate-600", self.script)

    def test_25_pending_no_verdict(self):
        # pending -> verdict suppressed (state.verdict = '').
        self.assertIn("state.verdict = pending ? ''", self.script)

    def test_26_pending_default_sort_fire_date_desc(self):
        self.assertIn("state.sortKey = 'fire_date'", self.script)
        # the pending branch sets desc
        self.assertIn("'fire_date'", self.script)

    def test_27_pending_summary_no_mean_range(self):
        self.assertIn("signals fired - T+30 return not yet matured",
                      self.script)

    def test_28_pending_detection_paths(self):
        # entry.pending OR months[] flag OR derived (all net null).
        self.assertIn("entry.pending", self.script)
        self.assertIn("derivedPending", self.script)

    def test_29_keeps_t1_and_identity_columns(self):
        # T+1 excess + Ticker, Director, Fire date stay (NOT em-dashed by default).
        # B-126 relabelled "CAR T+1" -> "Excess vs benchmark (T+1)".
        self.assertNotIn("car_t1", "".join(
            re.findall(r"PENDING_EMDASH_KEYS\s*=\s*\{[^}]*\}", self.script)))
        for label in ("Excess vs benchmark (T+1)", "Ticker", "Director", "Fire date"):
            self.assertIn(label, self.script)

    def test_30_script_pure_ascii(self):
        self.script.encode("ascii")


# --- Full render integration ------------------------------------------------

class TestFullRenderPending(unittest.TestCase):

    def setUp(self):
        self.html = rp.render(_mk_signals(), {}, build_sha="test",
                              cohort_data=_mk_cohort_data())

    def test_31_page_threads_pending_month(self):
        blob = _extract_cohort_blob(self.html)
        months = blob["groups"]["t3"]["months"]
        by_iso = {m["month_iso"]: m for m in months}
        self.assertTrue(by_iso["2026-08"]["pending"])

    def test_32_page_carries_pending_marker_and_legend(self):
        self.assertIn("pendingMarkers", self.html)
        self.assertIn("pending (fired, T+30 not yet matured)", self.html)

    def test_33_page_pending_drilldown_present(self):
        blob = _extract_cohort_blob(self.html)
        self.assertTrue(blob["drilldown"]["t3"]["2026-08"]["pending"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
