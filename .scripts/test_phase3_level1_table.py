"""Sprint 14 Phase 3 (B-067) tests — Level-1 performance-table augmentation.

Covers the two new scoreboard columns and the focus-mode row click:

  * cohort_sparkline_svg() — inline SVG renders; null gaps BREAK the path
    (no interpolation); <2 real points -> em-dash placeholder.
  * cohort_trend_cell_inner() — arrow direction + colour vs the delta sign
    and the +/-0.01 thresholds (incl. boundary and None -> em-dash).
  * The whole rendered scoreboard — all 11 cohort groups produce a sparkline
    + trend cell; each row carries the focus-mode click hook; the Phase-4
    Level-2 placeholder mount + back affordance exist.

Spec: docs/specs/cohort-performance-chart-level1-design.md sections 1, 2, 3.

Run under:
    python -m unittest .scripts.test_phase3_level1_table -v
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

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

def _mk_points(vals):
    """[v0, None, v1, ...] -> list of {month_iso, mean_car_t30} entries."""
    return [
        {"month_iso": f"2025-{i + 1:02d}", "mean_car_t30": v}
        for i, v in enumerate(vals)
    ]


def _mk_cohort_data():
    """11-group cohort_performance blob mirroring the live shape.

    Includes a null-gap group (t1a), a young group (t6 -> trend None,
    only 2 real points), a clean monotone group (t3), and flat/down/up
    trend cases for the trend-column assertions.
    """
    groups = {
        "t0":  {"label": "T0 cluster+combo", "color_hex": "#dc2626",
                "trend_3m_vs_prior3m_t30": -0.1141,
                "sparkline_points": _mk_points([0.01, None, -0.02, 0.03, None, 0.0])},
        # Two multi-point runs split by an interior null gap (idx 2): the
        # path MUST break (no interpolation), yielding >=2 polylines.
        "t1a": {"label": "T1A CEO/Founder buy", "color_hex": "#ef4444",
                "trend_3m_vs_prior3m_t30": -0.1726,
                "sparkline_points": _mk_points([0.018, -0.054, None, 0.012, -0.03, 0.02, 0.01, 0.005])},
        "t1b": {"label": "T1B CFO buy", "color_hex": "#f43f5e",
                "trend_3m_vs_prior3m_t30": 0.0343,
                "sparkline_points": _mk_points([0.01, 0.02, None, 0.03, 0.025])},
        "t7":  {"label": "T7 Chair buy", "color_hex": "#8b5cf6",
                "trend_3m_vs_prior3m_t30": -0.017,
                "sparkline_points": _mk_points([0.01, -0.01, 0.02, None, 0.0])},
        "t2":  {"label": "T2 exec buy", "color_hex": "#f59e0b",
                "trend_3m_vs_prior3m_t30": -0.0207,
                "sparkline_points": _mk_points([0.02, None, 0.01, -0.01])},
        "t3":  {"label": "T3 NED buy", "color_hex": "#10b981",
                "trend_3m_vs_prior3m_t30": 0.024,
                "sparkline_points": _mk_points([0.012, 0.02, 0.036, 0.04, 0.079])},
        "t5":  {"label": "T5 PCA buy", "color_hex": "#fb923c",
                "trend_3m_vs_prior3m_t30": 0.087,
                "sparkline_points": _mk_points([0.01, 0.02, 0.03])},
        "t6":  {"label": "T6 Co Sec buy", "color_hex": "#cbd5e1",
                "trend_3m_vs_prior3m_t30": None,
                "sparkline_points": _mk_points([0.01, None, None, 0.02])},
        "t4":  {"label": "T4 other buy", "color_hex": "#94a3b8",
                "trend_3m_vs_prior3m_t30": -0.0195,
                "sparkline_points": _mk_points([0.01, 0.0, -0.01])},
        "s1":  {"label": "S1 cluster", "color_hex": "#3b82f6",
                "trend_3m_vs_prior3m_t30": -0.026,
                "sparkline_points": _mk_points([-0.009, -0.021, -0.053])},
        "f1":  {"label": "F1 first-time buy", "color_hex": "#a855f7",
                "trend_3m_vs_prior3m_t30": -0.0113,
                "sparkline_points": _mk_points([-0.001, -0.002, -0.011])},
    }
    return {"signal_groups": list(groups.keys()), "groups": groups}


def _mk_signals():
    """Minimal signals_data with t30 + t90 horizon blocks so rows render."""
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


# --- Sparkline -------------------------------------------------------------

class TestSparkline(unittest.TestCase):

    def test_01_renders_svg_for_clean_group(self):
        pts = _mk_cohort_data()["groups"]["t3"]["sparkline_points"]
        svg = h.cohort_sparkline_svg(pts, "#10b981")
        self.assertIn("<svg", svg)
        self.assertIn('viewBox="0 0 120 30"', svg)
        self.assertIn("<polyline", svg)
        # Tier colour threaded through, not hardcoded.
        self.assertIn("#10b981", svg)
        # Two endpoint dots (first + last non-null) at radius 2.
        self.assertEqual(svg.count('r="2"'), 2)

    def test_02_null_gap_breaks_path_no_interpolation(self):
        # t1a has interior nulls -> path must split into >1 polyline run.
        pts = _mk_cohort_data()["groups"]["t1a"]["sparkline_points"]
        svg = h.cohort_sparkline_svg(pts, "#ef4444")
        self.assertGreaterEqual(svg.count("<polyline"), 2,
                                "null gaps must break the path into runs")

    def test_03_clean_series_single_polyline(self):
        # No nulls -> exactly one polyline run.
        pts = _mk_cohort_data()["groups"]["t3"]["sparkline_points"]
        svg = h.cohort_sparkline_svg(pts, "#10b981")
        self.assertEqual(svg.count("<polyline"), 1)

    def test_04_fewer_than_two_points_is_em_dash(self):
        self.assertIn("&mdash;", h.cohort_sparkline_svg([], "#10b981"))
        one = _mk_points([0.01, None])
        out = h.cohort_sparkline_svg(one, "#10b981")
        self.assertIn("&mdash;", out)
        self.assertNotIn("<svg", out)

    def test_05_zero_baseline_only_when_zero_in_band(self):
        # All-positive series -> no zero baseline dashed line.
        pos = _mk_points([0.01, 0.02, 0.03])
        self.assertNotIn("stroke-dasharray", h.cohort_sparkline_svg(pos, "#10b981"))
        # Series crossing zero -> baseline present.
        cross = _mk_points([-0.02, 0.01, 0.03])
        self.assertIn("stroke-dasharray", h.cohort_sparkline_svg(cross, "#10b981"))

    def test_06_single_point_run_emits_a_dot(self):
        # An isolated real point between two gaps -> a tiny dot (r=1.2).
        pts = _mk_points([0.01, None, 0.05, None, 0.02])
        svg = h.cohort_sparkline_svg(pts, "#10b981")
        self.assertIn('r="1.2"', svg)

    def test_07_output_is_pure_ascii(self):
        pts = _mk_cohort_data()["groups"]["t1a"]["sparkline_points"]
        svg = h.cohort_sparkline_svg(pts, "#ef4444")
        svg.encode("ascii")  # raises if any non-ASCII char slipped in


# --- Trend column ----------------------------------------------------------

class TestTrendCell(unittest.TestCase):

    def test_08_improving_green_up_arrow(self):
        cell = h.cohort_trend_cell_inner(0.024)
        self.assertIn("&#9650;", cell)        # up triangle
        self.assertIn("text-emerald-600", cell)
        self.assertIn("+2.4%", cell)

    def test_09_decaying_red_down_arrow(self):
        cell = h.cohort_trend_cell_inner(-0.018)
        self.assertIn("&#9660;", cell)        # down triangle
        self.assertIn("text-rose-600", cell)
        self.assertIn("-1.8%", cell)

    def test_10_flat_grey_with_value(self):
        cell = h.cohort_trend_cell_inner(0.003)
        self.assertIn("&#9644;", cell)        # flat rectangle
        self.assertIn("text-slate-500", cell)
        self.assertIn("+0.3%", cell)

    def test_11_none_is_flat_grey_em_dash(self):
        cell = h.cohort_trend_cell_inner(None)
        self.assertIn("&#9644;", cell)
        self.assertIn("&mdash;", cell)
        self.assertIn("text-slate-400", cell)
        self.assertNotIn("%", cell)

    def test_12_boundary_positive_001_is_flat(self):
        # d = 0.01 exactly: NOT > 0.01 -> flat (grey), not green.
        cell = h.cohort_trend_cell_inner(0.01)
        self.assertIn("&#9644;", cell)
        self.assertNotIn("&#9650;", cell)
        self.assertIn("+1.0%", cell)

    def test_13_boundary_negative_001_is_flat(self):
        # d = -0.01 exactly: NOT < -0.01 -> flat (grey), not red.
        cell = h.cohort_trend_cell_inner(-0.01)
        self.assertIn("&#9644;", cell)
        self.assertNotIn("&#9660;", cell)

    def test_14_just_past_threshold_fires(self):
        self.assertIn("&#9650;", h.cohort_trend_cell_inner(0.0101))
        self.assertIn("&#9660;", h.cohort_trend_cell_inner(-0.0101))

    def test_15_trend_output_is_pure_ascii(self):
        for v in (0.024, -0.018, 0.003, None, 0.01):
            h.cohort_trend_cell_inner(v).encode("ascii")


# --- Full scoreboard render ------------------------------------------------

class TestScoreboardIntegration(unittest.TestCase):

    def setUp(self):
        cohort = _mk_cohort_data()
        self.groups = cohort["groups"]
        self.html = rp._scoreboard(_mk_signals(), {}, "t30",
                                   cohort_groups=self.groups)

    def test_16_all_eleven_groups_render_a_trajectory_cell(self):
        # Every scoreboard row carries a trajectory cell (one per signal in
        # display order; b1/b2 with no cohort entry get placeholder cells).
        self.assertEqual(self.html.count("cohort-traj"),
                         len(h.SIGNAL_DISPLAY_ORDER))
        # All 11 cohort groups produce a real sparkline: each group's tier
        # hex must appear in the rendered SVG output.
        for grp, gd in self.groups.items():
            self.assertIn(gd["color_hex"], self.html,
                          f"{grp} colour missing from sparkline output")

    def test_17_all_eleven_groups_render_a_trend_cell(self):
        # One trend cell per display-order row; the 11 groups carry real
        # arrows, b1/b2 carry the em-dash placeholder.
        self.assertEqual(self.html.count("cohort-trend"),
                         len(h.SIGNAL_DISPLAY_ORDER))
        # Sanity: an up, a down, and a flat arrow all appear across the 11.
        self.assertIn("&#9650;", self.html)   # t3/t5 improving
        self.assertIn("&#9660;", self.html)   # t1a/s1 decaying
        self.assertIn("&#9644;", self.html)   # t6 None -> flat

    def test_18_new_column_headers_present(self):
        self.assertIn(">Trajectory<", self.html)
        self.assertIn(">3m trend<", self.html)

    def test_18b_legacy_12w_sparkline_column_removed(self):
        # The legacy ~12-week sparkline column (header "12w trend") was
        # removed once the Phase 3 Trajectory cohort sparkline superseded it.
        # The legacy header and the legacy per-row spark <td> (which carried
        # no cohort-traj/cohort-trend class) must be gone, while the two new
        # cohort columns remain.
        self.assertNotIn("12w trend", self.html)
        self.assertNotIn(">12w<", self.html)
        # Trajectory + 3m-trend retained (one cell each per display-order row).
        self.assertIn(">Trajectory<", self.html)
        self.assertIn(">3m trend<", self.html)
        self.assertEqual(self.html.count("cohort-traj"),
                         len(h.SIGNAL_DISPLAY_ORDER))
        self.assertEqual(self.html.count("cohort-trend"),
                         len(h.SIGNAL_DISPLAY_ORDER))

    def test_19_rows_carry_focus_mode_click_hook(self):
        # One clickable focus-mode row per signal in display order.
        self.assertEqual(self.html.count("cohort-row"),
                         len(h.SIGNAL_DISPLAY_ORDER))
        self.assertIn('role="button"', self.html)
        self.assertIn('data-signal-group="t3"', self.html)
        self.assertIn('data-signal-label="T3 NED buy"', self.html)

    def test_20_young_group_trend_is_em_dash(self):
        # t6 has trend None -> grey flat + em-dash in its row.
        # The whole table has exactly one None-trend group here.
        self.assertIn("&mdash;", self.html)

    def test_21_signals_without_cohort_entry_get_placeholders(self):
        # b1/b2 are in SIGNAL_DISPLAY_ORDER but absent from cohort groups.
        # They must still render rows (with em-dash placeholders), not crash.
        self.assertIn('data-signal-id="b1"', self.html)
        self.assertIn('data-signal-id="b2"', self.html)


class TestFocusModeShell(unittest.TestCase):
    """Focus-mode overlay (NOT inline-expand) + Phase-4 placeholder mount."""

    def setUp(self):
        self.overlay = rp._cohort_focus_overlay()
        self.script = rp._cohort_focus_script()

    def test_22_level2_placeholder_mount_exists(self):
        # Sprint 14 Phase 4 (B-068): the placeholder copy ("coming in Phase 4")
        # was replaced by the live Level-2 chart mount. The mount id persists;
        # it now carries the chart canvas instead of placeholder text.
        self.assertIn('id="cohort-level2-mount"', self.overlay)
        self.assertIn('id="cohortMainChart"', self.overlay)

    def test_23_focus_mode_not_inline_expand(self):
        # Focus mode overlays the page (fixed inset-0), it is not an extra
        # row inserted into the table.
        self.assertIn('id="cohortFocus"', self.overlay)
        self.assertIn("fixed inset-0", self.overlay)
        self.assertNotIn('colspan="99"', self.overlay)

    def test_24_back_affordance_and_label(self):
        self.assertIn('id="cohortFocusBack"', self.overlay)
        # B-117: the back button was relabelled "Performance overview" (a separate
        # "Dashboard" link sits beside it).
        self.assertIn("Performance overview", self.overlay)
        self.assertIn('id="cohortFocusLabel"', self.overlay)

    def test_25_script_wires_rows_and_back(self):
        self.assertIn("tr.cohort-row", self.script)
        self.assertIn("Escape", self.script)
        self.assertIn("cohortFocusBack", self.script)

    def test_26_full_render_includes_overlay_and_script(self):
        html = rp.render(_mk_signals(), {}, build_sha="test",
                         cohort_data=_mk_cohort_data())
        self.assertIn('id="cohortFocus"', html)
        self.assertIn('id="cohort-level2-mount"', html)
        self.assertIn("tr.cohort-row", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
