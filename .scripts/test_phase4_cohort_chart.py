"""Sprint 14 Phase 4 (B-068) tests -- the Level-2 cohort chart.

The Chart.js canvas itself is not unit-testable headless, so these tests pin
the two things that ARE testable from Python:

  1. The data-shaping that threads the per-group monthly series to the browser
     (`_cohort_client_data`): every group carries label + color_hex + a months[]
     array, and every month carries the exact keys the chart consumes.
  2. The HTML / JS scaffolding the chart needs is emitted: the canvas mounts,
     the pill switcher, the DOM tooltip, the three custom plugins, the builder
     entry point, the no-date-adapter (category axis) choice, and the N-label
     overlay (no chartjs-plugin-datalabels dependency).

Spec: docs/specs/cohort-performance-chart-design-spec.md ("Chart.js shape").

Run under:
    python -m unittest .scripts.test_phase4_cohort_chart -v
or:
    python -m unittest discover -s .scripts -p "test_*.py"
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

# The exact per-month keys the Level-2 chart reads (whiskers, dots, N strip,
# rolling hit rate, MA overlay, dominance marker).
_REQUIRED_MONTH_KEYS = {
    "month_iso", "n_signals", "mean_car_t30", "min_car_t30", "max_car_t30",
    "hit_rate_t30", "hit_rate_t30_rolling_6m", "single_ticker_weight",
    "ma3_mean_car_t30",
}


def _month(iso, n, mean, lo, hi, hit, roll, dom, ma3):
    return {
        "month_iso": iso, "n_signals": n,
        "mean_car_t1": 0.001, "mean_car_t30": mean, "mean_car_t90": 0.02,
        "min_car_t30": lo, "max_car_t30": hi,
        "hit_rate_t30": hit, "hit_rate_t30_rolling_6m": roll,
        "single_ticker_weight": dom, "ma3_mean_car_t30": ma3,
        "signal_ids": ["a" * 16, "b" * 16],   # NOT consumed by Level-2
    }


def _mk_cohort_data():
    """Two-group blob: t3 (healthy, one dominance month) and t1b (low-N)."""
    groups = {
        "t3": {
            "label": "T3 NED buy", "color_hex": "#10b981",
            "trend_3m_vs_prior3m_t30": 0.024,
            "sparkline_points": [{"month_iso": "2025-06", "mean_car_t30": 0.012}],
            "header": {"n_total_signals": 30, "mean_car_t30_overall": 0.03,
                       "hit_rate_t30_overall": 0.4},
            "months": [
                _month("2025-06", 12, 0.012, -0.08, 0.12, 0.5, 0.42, 0.18, None),
                # null-gap month (no firings) -- must be passed through but the
                # chart drops it (no dot / connector break handled client-side).
                _month("2025-07", 0, None, None, None, None, 0.42, None, None),
                _month("2025-08", 9, -0.023, -0.18, 0.13, 0.33, 0.40, 0.78,
                       -0.004),   # dominance month (>0.5)
                _month("2025-09", 6, 0.04, -0.05, 0.10, 0.6, 0.45, 0.2, 0.01),
            ],
        },
        "t1b": {
            "label": "T1B CFO buy", "color_hex": "#f43f5e",
            "trend_3m_vs_prior3m_t30": 0.0343,
            "sparkline_points": [],
            "header": {"n_total_signals": 4, "mean_car_t30_overall": -0.03,
                       "hit_rate_t30_overall": 0.5},
            "months": [
                _month("2025-06", 3, -0.03, -0.10, 0.02, 0.33, 0.33, 0.4, None),
            ],
        },
    }
    return {"signal_groups": list(groups.keys()), "groups": groups}


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
    """Pull the JSON out of the window.__cohortData = {...}; script tag."""
    m = re.search(r"window\.__cohortData\s*=\s*(\{.*?\});", html_or_script,
                  re.DOTALL)
    assert m, "window.__cohortData assignment not found"
    return json.loads(m.group(1))


# --- Data threading to the browser -----------------------------------------

class TestClientDataThreading(unittest.TestCase):

    def setUp(self):
        self.groups = _mk_cohort_data()["groups"]
        self.script = rp._cohort_client_data(self.groups)
        self.blob = _extract_cohort_blob(self.script)

    def test_01_emits_window_cohort_data(self):
        self.assertIn("window.__cohortData", self.script)
        self.assertIn("groups", self.blob)
        self.assertIn("order", self.blob)

    def test_02_every_group_present_with_label_and_color(self):
        for grp in ("t3", "t1b"):
            self.assertIn(grp, self.blob["groups"])
            g = self.blob["groups"][grp]
            self.assertTrue(g["label"])
            self.assertTrue(g["color_hex"].startswith("#"))
            self.assertIsInstance(g["months"], list)

    def test_03_every_month_carries_required_keys(self):
        for grp, g in self.blob["groups"].items():
            for m in g["months"]:
                missing = _REQUIRED_MONTH_KEYS - set(m.keys())
                self.assertFalse(
                    missing, f"{grp} month missing keys: {missing}")

    def test_04_null_gap_months_passed_through(self):
        # t3 has a July null-gap month (n_signals == 0); it must survive so
        # the client can decide how to break the line.
        t3 = self.blob["groups"]["t3"]["months"]
        july = [m for m in t3 if m["month_iso"] == "2025-07"]
        self.assertEqual(len(july), 1)
        self.assertEqual(july[0]["n_signals"], 0)
        self.assertIsNone(july[0]["mean_car_t30"])

    def test_05_order_follows_display_order(self):
        # Only groups that exist; in SIGNAL_DISPLAY_ORDER sequence (t1b before t3).
        self.assertEqual(self.blob["order"],
                         [s for s in h.SIGNAL_DISPLAY_ORDER
                          if s in self.blob["groups"]])

    def test_06_signal_ids_not_shipped(self):
        # Drill-down (Phase 6) is out of scope -- keep the payload lean.
        for g in self.blob["groups"].values():
            for m in g["months"]:
                self.assertNotIn("signal_ids", m)

    def test_07_dominance_weight_preserved(self):
        aug = [m for m in self.blob["groups"]["t3"]["months"]
               if m["month_iso"] == "2025-08"][0]
        self.assertEqual(aug["single_ticker_weight"], 0.78)

    def test_08_color_hex_falls_back_to_palette(self):
        # A group lacking color_hex should inherit the tier palette hex.
        groups = {"t3": {"label": "T3 NED buy", "months": []}}
        blob = _extract_cohort_blob(rp._cohort_client_data(groups))
        self.assertEqual(blob["groups"]["t3"]["color_hex"],
                         h.TIER_PALETTE["t3"]["hex"])

    def test_09_client_data_is_pure_ascii(self):
        rp._cohort_client_data(self.groups).encode("ascii")


# --- Chart scaffolding (overlay mounts + script) ---------------------------

class TestChartScaffolding(unittest.TestCase):

    def setUp(self):
        self.overlay = rp._cohort_focus_overlay()
        self.script = rp._cohort_level2_script()

    def test_10_canvas_mounts_exist(self):
        self.assertIn('id="cohortMainChart"', self.overlay)
        self.assertIn('id="cohortNStrip"', self.overlay)
        self.assertIn('id="cohort-level2-mount"', self.overlay)

    def test_11_pill_switcher_container_exists(self):
        self.assertIn('id="cohortPills"', self.overlay)
        self.assertIn('role="tablist"', self.overlay)

    def test_12_dom_tooltip_present_and_native_disabled(self):
        # DOM tooltip container exists ...
        self.assertIn('id="cohortTooltip"', self.overlay)
        for el in ("ttMonth", "ttN", "ttMean", "ttRange", "ttHit"):
            self.assertIn(f'id="{el}"', self.overlay)
        # ... and the Chart.js native tooltip is disabled in the script.
        self.assertIn("tooltip: { enabled: false }", self.script)

    def test_13_three_custom_plugins_defined(self):
        self.assertIn("whiskerPlugin", self.script)
        self.assertIn("zeroLine", self.script)
        self.assertIn("dominanceMarkers", self.script)
        # registered on the main chart
        self.assertIn(
            "plugins: [pendingMarkers, whiskerPlugin, zeroLinePlugin, dominanceMarkers]",
            self.script)

    def test_14_whisker_draws_shaft_and_caps_with_dash_for_lowN(self):
        # min->max shaft + two horizontal caps, dashed when low-N.
        self.assertIn("getPixelForValue(pt._min)", self.script)
        self.assertIn("getPixelForValue(pt._max)", self.script)
        self.assertIn("setLineDash([3,3])", self.script)

    def test_15_dominance_marker_glyph_and_threshold(self):
        self.assertIn("single_ticker_weight", self.script)
        self.assertIn("> 0.5", self.script)
        self.assertIn("fillText('!'", self.script)

    def test_16_lowN_open_ring_vs_filled_dot(self):
        # N<5 -> white fill + r=3; N>=5 -> tier fill + r=4.
        self.assertIn("_lowN ? 3 : 4", self.script)
        self.assertIn("_lowN ? '#ffffff' : hex", self.script)

    def test_17_connector_is_straight_no_smoothing(self):
        self.assertIn("showLine: true", self.script)
        self.assertIn("tension: 0", self.script)

    def test_18_ma_overlay_breaks_at_nulls(self):
        # 3m MA line uses spanGaps:false so the first-2-month nulls break it.
        # Sprint 24: the field is now read via cohortPick(m, activeH, 'ma3_mean_car').
        self.assertIn("ma3_mean_car", self.script)
        self.assertIn("spanGaps: false", self.script)

    def test_19_no_date_adapter_used(self):
        # Only Chart.js core + annotation are on the page (no date adapter),
        # and the brief forbids a new CDN -> the chart MUST use a category
        # axis, never type:'time'.
        self.assertNotIn("type: 'time'", self.script)
        self.assertIn("type: 'category'", self.script)

    def test_20_n_labels_via_overlay_no_datalabels_dep(self):
        # N values drawn through an overlay div, not chartjs-plugin-datalabels.
        self.assertIn('id="cohortNLabels"', self.overlay)
        self.assertIn("drawNLabels", self.script)
        self.assertNotIn("datalabels", self.script)

    def test_21_dynamic_y_axis_with_headroom(self):
        self.assertIn("0.10", self.script)   # 10% headroom
        self.assertIn("min: yMin", self.script)
        self.assertIn("max: yMax", self.script)

    def test_22_builder_entry_point_and_pointer_cursor(self):
        self.assertIn("window.buildCohortLevel2", self.script)
        self.assertIn("cursor = 'pointer'", self.script)

    def test_23_drilldown_click_routes_to_modal(self):
        # Phase 6 (B-070): onCohortClick is no longer a no-op -- it resolves the
        # clicked point and routes to window.openCohortDrilldown(group, month).
        self.assertIn("onCohortClick", self.script)
        self.assertIn("window.openCohortDrilldown", self.script)
        # Gap (null) months must not open a modal.
        self.assertIn("_monthIso == null", self.script)

    def test_24_script_is_pure_ascii(self):
        self.script.encode("ascii")
        self.overlay.encode("ascii")


# --- Full render integration ------------------------------------------------

class TestGapMonthsOnAxis(unittest.TestCase):
    """Sprint 14 follow-up: gap months (n=0) must occupy their own x-slot so
    the Level-2 axis is a calendar-true expanding window (brief decisions 1&2),
    with the connector line BREAKING across the gap rather than collapsing it.

    The Chart.js canvas is not headless-testable, so we pin the JS source
    contract that produces the behaviour, plus the data-blob shape that feeds
    it (the threading test already proves gap months survive the export).
    """

    def setUp(self):
        self.script = rp._cohort_level2_script()
        # A group with an INTERIOR gap month (2025-07, n=0) bracketed by valued
        # months, plus a wholly-empty group (no valued month at all).
        self.groups = {
            "t3": {
                "label": "T3 NED buy", "color_hex": "#10b981",
                "months": [
                    _month("2025-06", 12, 0.012, -0.08, 0.12, 0.5, 0.42, 0.18,
                           None),
                    _month("2025-07", 0, None, None, None, None, 0.42, None,
                           None),   # interior gap
                    _month("2025-08", 9, -0.023, -0.18, 0.13, 0.33, 0.40, 0.78,
                           -0.004),
                ],
            },
            # No firings at all -> wholly empty -> empty state must fire.
            "t4": {
                "label": "T4 buy", "color_hex": "#6366f1",
                "months": [
                    _month("2025-06", 0, None, None, None, None, None, None,
                           None),
                    _month("2025-07", 0, None, None, None, None, None, None,
                           None),
                ],
            },
        }
        self.blob = _extract_cohort_blob(rp._cohort_client_data(self.groups))

    def test_29_axis_built_from_all_months_not_just_fired(self):
        # build() must map labels/points over the full `all` array (every
        # calendar month), NOT the `real`-filtered subset.
        self.assertIn("var all = (months || []).filter", self.script)
        self.assertIn("var labels = all.map", self.script)
        self.assertIn("var pts = all.map", self.script)
        self.assertIn("var maPts = all.map", self.script)
        self.assertIn("var nPts = all.map", self.script)

    def test_30_gap_month_emits_null_y_and_breaks_line(self):
        # Gap months emit y:null; the means dataset breaks at gaps (spanGaps
        # false on BOTH the means line and the 3m MA overlay).
        self.assertIn("if (!valued){", self.script)
        self.assertIn("x: i, y: null", self.script)
        # means dataset connector breaks at gaps. Phase 5 (B-069) added a third
        # spanGaps:false on the rolling-hit-rate line. B-074 (Sprint 32) wired up
        # the previously-dead multi-signal overlay, whose means line is a fourth
        # spanGaps:false -> the total is now 4
        # (build: means + 3m MA + hit-rate; buildOverlay: means).
        self.assertEqual(self.script.count("spanGaps: false"), 4)

    def test_31_interior_gap_survives_export_with_null_mean(self):
        # The interior July gap is shipped to the client (axis will span it),
        # carrying n_signals 0 and a null mean.
        t3 = self.blob["groups"]["t3"]["months"]
        isos = [m["month_iso"] for m in t3]
        self.assertEqual(isos, ["2025-06", "2025-07", "2025-08"])
        july = [m for m in t3 if m["month_iso"] == "2025-07"][0]
        self.assertEqual(july["n_signals"], 0)
        self.assertIsNone(july["mean_car_t30"])
        # Axis length == ALL months (3), not just the 2 fired months.
        self.assertEqual(len(t3), 3)

    def test_32_empty_state_only_for_wholly_empty_group(self):
        # The empty-state guard keys on `real` (valued months), not `all`, so a
        # group with gaps but >=1 valued month renders the chart.
        self.assertIn("if (!real.length){", self.script)
        self.assertIn("var real = all.filter", self.script)
        # t4 has zero valued months -> still exported, but client shows empty.
        t4 = self.blob["groups"]["t4"]["months"]
        valued = [m for m in t4
                  if m["n_signals"] > 0 and m["mean_car_t30"] is not None]
        self.assertEqual(valued, [])
        # t3 has valued months -> NOT empty.
        t3 = self.blob["groups"]["t3"]["months"]
        valued_t3 = [m for m in t3
                     if m["n_signals"] > 0 and m["mean_car_t30"] is not None]
        self.assertEqual(len(valued_t3), 2)

    def test_33_gap_month_n_label_suppressed(self):
        # Gap months (n=0) get no N-strip label (just hold the x-slot).
        self.assertIn("if (!pt.y) return;", self.script)

    def test_34_script_still_pure_ascii(self):
        self.script.encode("ascii")


class TestPhase5HitRatePanel(unittest.TestCase):
    """Sprint 14 Phase 5 (B-069): rolling-6-month hit-rate panel + legend.

    The Chart.js canvas is not headless-testable, so we pin the testable
    contract: the hit-rate canvas + header exist in the overlay; the chart is
    built from hit_rate_t30_rolling_6m over ALL months with spanGaps:false; the
    50% baseline plugin is registered; and the static legend strip is present
    with the key items and stays ASCII-clean.
    """

    def setUp(self):
        self.overlay = rp._cohort_focus_overlay()
        self.script = rp._cohort_level2_script()

    def test_35_hitrate_canvas_and_header_exist(self):
        self.assertIn('id="cohortHitRateChart"', self.overlay)
        # Sprint 24 (B-072) wrapped the horizon in a span so it updates live, so
        # the header text is split: "... @ <span id=cohortHitRateHorizon>T+21".
        self.assertIn("Rolling 6-month hit rate @", self.overlay)
        self.assertIn('id="cohortHitRateHorizon">T+30</span>', self.overlay)
        self.assertIn("% of signals beating sector benchmark", self.overlay)

    def test_36_hitrate_built_from_rolling_field_over_all_months(self):
        # The hit-rate series reads m['hit_rate_' + activeH + '_rolling_6m']
        # dynamically (B-151 horizon toggle), mapped over the full `all` array
        # (every calendar month incl. gaps) so it column-aligns with the main
        # chart. See also test_53 which checks the same dynamic expression.
        self.assertIn("'hit_rate_' + activeH + '_rolling_6m'", self.script)
        self.assertIn("var hitPts = all.map", self.script)
        # built as a line chart on the hit-rate canvas
        self.assertIn("getElementById('cohortHitRateChart')", self.script)

    def test_37_hitrate_breaks_at_null_months(self):
        # null rolling-hit values emit null y; spanGaps:false breaks the line.
        # Now FOUR spanGaps:false (means, 3m MA, hit-rate line, + B-074 overlay
        # means line wired up in Sprint 32).
        self.assertEqual(self.script.count("spanGaps: false"), 4)
        self.assertIn("(hr == null) ? null : hr * 100", self.script)

    def test_38_hitrate_is_teal_with_light_fill_and_small_points(self):
        self.assertIn("'#0d9488'", self.script)            # teal-600 stroke
        self.assertIn("rgba(13,148,136,0.08)", self.script)  # light teal fill
        self.assertIn("pointRadius: 2", self.script)
        self.assertIn("tension: 0", self.script)

    def test_39_hitrate_y_axis_0_to_100_pct(self):
        self.assertIn("min: 0, max: 100", self.script)
        self.assertIn("return v + '%'", self.script)

    def test_40_baseline50_plugin_registered(self):
        self.assertIn("baseline50", self.script)
        self.assertIn("getPixelForValue(50)", self.script)
        self.assertIn("plugins: [baseline50Plugin]", self.script)

    def test_41_hitchart_built_and_destroyed_for_leak_safety(self):
        self.assertIn("hitChart = new Chart", self.script)
        self.assertIn("if (hitChart) { hitChart.destroy(); hitChart = null; }",
                      self.script)

    def test_42_legend_strip_present_with_key_items(self):
        self.assertIn('id="cohortLegend"', self.overlay)
        # one item per visual element of the chart's language
        for txt in (
            "5+ signals",
            "under 5 (low N)",
            "solid whisker = 5+",
            "dashed whisker = under 5",
            "dashed line = 3-month average",
            "one ticker drove &gt;50% of the cohort",
        ):
            self.assertIn(txt, self.overlay)

    def test_43_legend_is_quiet_styling(self):
        # The legend stays visually subordinate: 10px slate text.
        self.assertIn('id="cohortLegend"', self.overlay)
        self.assertIn("text-[10px]", self.overlay)
        self.assertIn("text-slate-400", self.overlay)

    def test_44_phase5_blocks_pure_ascii(self):
        self.overlay.encode("ascii")
        self.script.encode("ascii")


class TestFullRenderIntegration(unittest.TestCase):

    def setUp(self):
        self.html = rp.render(_mk_signals(), {}, build_sha="test",
                              cohort_data=_mk_cohort_data())

    def test_25_full_page_carries_data_overlay_and_builder(self):
        self.assertIn("window.__cohortData", self.html)
        self.assertIn('id="cohortMainChart"', self.html)
        self.assertIn("window.buildCohortLevel2", self.html)
        self.assertIn('id="cohortPills"', self.html)

    def test_26_focus_opener_invokes_builder(self):
        # The focus opener must call the Level-2 builder when a row opens.
        self.assertIn("window.buildCohortLevel2(group)", self.html)

    def test_27_full_page_is_pure_ascii_in_new_blocks(self):
        # The whole page may legitimately contain HTML entities elsewhere;
        # assert at least our injected blob + builder are clean.
        blob = _extract_cohort_blob(self.html)
        json.dumps(blob).encode("ascii")

    def test_28_renders_without_cohort_data(self):
        # No cohort_data -> empty groups, but the page still builds and the
        # builder/script are still present (graceful no-op client-side).
        html = rp.render(_mk_signals(), {}, build_sha="test", cohort_data=None)
        self.assertIn("window.__cohortData", html)
        self.assertIn('id="cohortMainChart"', html)


class TestSprint24HorizonToggle(unittest.TestCase):
    """Sprint 24: horizon toggle wiring on the Level-2 cohort chart.

    These tests pin the JS contract (source presence) for:
      - cohortPick() helper
      - window.__cohortActiveHorizon default
      - horizonChange subscriber
      - build() using cohortPick instead of hardcoded _t30 fields
      - pending_horizons check
      - T+252 empty-state message
    """

    def setUp(self):
        self.script = rp._cohort_level2_script()

    def test_45_cohortPick_function_present(self):
        self.assertIn("function cohortPick(", self.script)
        # reads the right field name dynamically
        self.assertIn("metric + '_' + h", self.script)

    def test_46_cohortActiveHorizon_default_is_t30(self):
        self.assertIn("window.__cohortActiveHorizon", self.script)
        self.assertIn("'t30'", self.script)

    def test_47_horizonChange_subscriber_present(self):
        # The IIFE must subscribe to the page-level horizonChange event.
        self.assertIn("document.addEventListener('horizonChange'", self.script)
        # On change: update window.__cohortActiveHorizon and re-render.
        self.assertIn("window.__cohortActiveHorizon = h", self.script)
        # B-074 (Sprint 32): the listener is now overlay-aware -- it stays in
        # multi-signal overlay mode when >1 signal is selected, else rebuilds
        # the single detailed chart.
        self.assertIn("buildOverlay(visibleSet)", self.script)
        self.assertIn("else if (currentGroup)", self.script)
        self.assertIn("build(currentGroup)", self.script)

    def test_48_build_uses_activeH_not_hardcoded_t30(self):
        # The build function must read activeH from window.__cohortActiveHorizon.
        self.assertIn("var activeH = window.__cohortActiveHorizon", self.script)
        # And pass it to cohortPick.
        self.assertIn("cohortPick(m, activeH,", self.script)

    def test_49_pending_horizons_checked_in_build(self):
        # Build checks m.pending_horizons for the per-horizon pending state.
        self.assertIn("pending_horizons", self.script)
        self.assertIn(".indexOf(activeH)", self.script)

    def test_50_t365_empty_state_message_present(self):
        # T+365 specific empty-state message.
        self.assertIn("T+365 data will build over the coming year", self.script)
        self.assertIn("activeH === 't365'", self.script)

    def test_51_horizon_label_shown_in_strip(self):
        # The strip now includes the active horizon label.
        self.assertIn("HORIZON_LABELS_COHORT", self.script)
        self.assertIn("hLabel", self.script)

    def test_52_ma3_uses_cohortPick(self):
        # maPts must use cohortPick, not a hardcoded ma3_mean_car_t30.
        self.assertIn("cohortPick(m, activeH, 'ma3_mean_car')", self.script)

    def test_53_hit_rate_chart_uses_activeH(self):
        # The rolling hit-rate chart must use the active horizon's field.
        self.assertIn("'hit_rate_' + activeH + '_rolling_6m'", self.script)

    def test_54_tooltip_uses_activeH_label(self):
        # Tooltip text references the active horizon label, not hardcoded T+21.
        self.assertIn("aHLabel", self.script)
        self.assertIn("return not yet matured", self.script)

    def test_55_horizon_labels_map_defined(self):
        # The HORIZON_LABELS_COHORT map is defined inside the IIFE.
        self.assertIn("var HORIZON_LABELS_COHORT", self.script)
        for h in ("t1:'T+1'", "t30:'T+30'", "t90:'T+90'", "t365:'T+365'"):
            self.assertIn(h, self.script)

    def test_56_script_pure_ascii(self):
        self.script.encode("ascii")


class TestB074MultiSignalOverlay(unittest.TestCase):
    """Sprint 32 (B-074): the multi-signal overlay in the Level-2 chart.

    The overlay path (buildOverlay) shipped dormant under B-019 but threw a
    ReferenceError (monthLabel + activeH out of scope) and was never wired to
    the horizon toggle, so it never rendered. These tests pin the JS contract
    that makes it live: the hoisted monthLabel, the window-scoped horizon read,
    the horizon-aware re-render, and the single-group chrome teardown.
    """

    def setUp(self):
        self.overlay = rp._cohort_focus_overlay()
        self.script = rp._cohort_level2_script()

    def test_57_monthLabel_hoisted_single_source(self):
        # Exactly one monthLabel definition in the Level-2 script: the IIFE-level
        # one shared by build() and buildOverlay(). The duplicate inside build()
        # (which made buildOverlay throw) has been removed.
        self.assertEqual(self.script.count("function monthLabel("), 1)

    def test_58_overlay_reads_horizon_from_window(self):
        # buildOverlay must resolve the active horizon from window state, not a
        # build()-local activeH (the original ReferenceError).
        self.assertIn("function buildOverlay(set)", self.script)
        self.assertIn("cohortPick(m, activeH, 'mean_car')", self.script)
        # the window-scoped read appears in both build() and buildOverlay().
        self.assertGreaterEqual(
            self.script.count("window.__cohortActiveHorizon || 't30'"), 2)

    def test_59_horizon_toggle_keeps_overlay(self):
        # Switching horizon while >1 signal is selected must rebuild the overlay,
        # not collapse to a single chart.
        self.assertIn("if (visibleSet && visibleSet.length > 1)", self.script)
        self.assertIn("buildOverlay(visibleSet)", self.script)

    def test_60_dispatch_routes_one_vs_many(self):
        # _dispatch: 1 group -> detailed build(); >1 -> means-only overlay.
        self.assertIn("if (set.length === 1)", self.script)
        self.assertIn("buildOverlay(set)", self.script)

    def test_61_single_group_chrome_toggled(self):
        # The N-strip / whisker legend / rolling-hit panels are hidden in overlay
        # mode and restored in single-group mode.
        self.assertIn("function setSingleGroupChromeVisible(on)", self.script)
        self.assertIn("setSingleGroupChromeVisible(false)", self.script)  # overlay
        self.assertIn("setSingleGroupChromeVisible(true)", self.script)   # build
        for el_id in ("cohortNStripWrap", "cohortLegend", "cohortHitSection"):
            self.assertIn(el_id, self.script)

    def test_62_overlay_tears_down_single_group_charts(self):
        # buildOverlay destroys the N-strip + rolling-hit charts so they don't
        # linger stale. (Both build() and buildOverlay() now destroy them.)
        self.assertGreaterEqual(self.script.count("nChart.destroy()"), 2)
        self.assertGreaterEqual(self.script.count("hitChart.destroy()"), 2)

    def test_63_new_chrome_ids_in_overlay_html(self):
        # The two wrapper ids the overlay toggles must exist in the DOM.
        self.assertIn('id="cohortNStripWrap"', self.overlay)
        self.assertIn('id="cohortHitSection"', self.overlay)

    def test_64_discoverability_hint_present(self):
        # The pill interaction (click-to-add, double-click-to-solo) is otherwise
        # invisible; a quiet hint line makes it discoverable.
        self.assertIn("Click to add or remove a signal", self.overlay)

    def test_65_overlay_blocks_pure_ascii(self):
        # The B-074 additions stay ASCII-clean (project convention).
        self.assertIn("function buildOverlay", self.script)
        self.assertIn("Click to add or remove a signal", self.overlay)


if __name__ == "__main__":
    unittest.main(verbosity=2)
