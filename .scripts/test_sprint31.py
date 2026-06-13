"""Sprint 31 tests.

Covers:
  B-077 -- Mobile/responsive pass: overflow-x-auto wrappers + min-w- on tables,
            header nav flex-shrink-0 + title truncate
  B-073 -- Small-multiples signal grid: signalOverviewGrid, buildSignalOverview JS,
            canvas per signal with data-signal-group, horizonChange listener
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import unittest


# ---------------------------------------------------------------------------
# Minimal data helpers (pure-dict, no DB needed for render tests)
# ---------------------------------------------------------------------------

def _minimal_signals_data() -> dict:
    """Minimal signals.json shape sufficient for render_performance.render()."""
    return {
        "horizon_aggregates": {},
        "active_clusters": [],
        "paper_pnl_open": 0,
        "paper_trades_open": 0,
        "paper_trades_closed": 0,
        "cohorts": {},
        "cohorts_v2": {},
        "pending_diagnostics": {},
        "companies_index": [],
        "paper_book": {},
        "monthly_buysell": {},
    }


def _minimal_dealings_data() -> dict:
    """Minimal dealings.json shape sufficient for render_index.render()."""
    return {
        "today": [],
        "this_week": [],
        "generated_at": "2026-06-05T00:00:00Z",
        "signals_today_count": 0,
        "signals_today_delta_vs_avg": 0,
        "as_of_date": "2026-06-05",
    }


def _cohort_data_with_groups() -> dict:
    """Minimal cohort_data with a couple of real signal groups for B-073 tests."""
    return {
        "groups": {
            "t1a": {
                "label": "T1a",
                "color_hex": "#ef4444",
                "months": [
                    {"month_iso": "2025-01", "n_signals": 3, "pending": False,
                     "mean_car_t30": 0.015, "mean_car_t1": 0.005,
                     "mean_car_t90": 0.025, "mean_car_t365": 0.040},
                    {"month_iso": "2025-02", "n_signals": 2, "pending": False,
                     "mean_car_t30": 0.010, "mean_car_t1": 0.002,
                     "mean_car_t90": None, "mean_car_t365": None},
                ],
                "sparkline_points": [],
                "trend_3m_vs_prior3m_t30": None,
            },
            "t3": {
                "label": "T3",
                "color_hex": "#10b981",
                "months": [
                    {"month_iso": "2025-01", "n_signals": 5, "pending": False,
                     "mean_car_t30": -0.008, "mean_car_t1": 0.001,
                     "mean_car_t90": -0.012, "mean_car_t365": None},
                ],
                "sparkline_points": [],
                "trend_3m_vs_prior3m_t30": 0.01,
            },
        },
        "cohort_drilldown": {},
    }


# ---------------------------------------------------------------------------
# B-077 -- Mobile / narrow-screen pass
# ---------------------------------------------------------------------------

class TestB077MobileIndex(unittest.TestCase):
    """render_index.render() must include mobile-scroll primitives."""

    def setUp(self):
        from dashboard import render_index
        self.html = render_index.render(
            _minimal_signals_data(),
            _minimal_dealings_data(),
        )

    def test_overflow_x_auto_present(self):
        """Today table wrapper must have overflow-x-auto."""
        self.assertIn("overflow-x-auto", self.html,
                      "B-077: overflow-x-auto missing from index page")

    def test_table_min_width_present(self):
        """Tables must have min-w- to prevent collapse on narrow screens."""
        self.assertIn("min-w-", self.html,
                      "B-077: min-w- class missing from index tables")

    def test_min_w_640_on_tables(self):
        """Specifically min-w-[640px] should appear on the today/week tables."""
        self.assertIn("min-w-[640px]", self.html,
                      "B-077: min-w-[640px] not found on index page tables")

    def test_viewport_meta_present(self):
        """viewport meta tag must be present (width=device-width)."""
        self.assertIn("width=device-width", self.html,
                      "B-077: viewport meta tag missing")


class TestB077MobilePerformance(unittest.TestCase):
    """render_performance.render() must include mobile-scroll primitives."""

    def setUp(self):
        from dashboard import render_performance
        self.html = render_performance.render(
            _minimal_signals_data(),
        )

    def test_overflow_x_auto_present(self):
        """Scoreboard table wrapper must have overflow-x-auto."""
        self.assertIn("overflow-x-auto", self.html,
                      "B-077: overflow-x-auto missing from performance page")

    def test_min_w_present_on_scoreboard(self):
        """Scoreboard table must have a min-w- to prevent collapse."""
        self.assertIn("min-w-[700px]", self.html,
                      "B-077: min-w-[700px] not found on scoreboard table")

    def test_viewport_meta_present(self):
        """viewport meta tag must be present."""
        self.assertIn("width=device-width", self.html,
                      "B-077: viewport meta tag missing")


class TestB077HeaderNav(unittest.TestCase):
    """Header nav must use flex-shrink-0 + title must use truncate."""

    def setUp(self):
        from dashboard import templates
        self.src = templates.base_page.__doc__ or ""
        import inspect
        self.src = inspect.getsource(templates)

    def test_nav_flex_shrink_0(self):
        """Nav should have flex-shrink-0 so it doesn't shrink on mobile."""
        self.assertIn("flex-shrink-0", self.src,
                      "B-077: flex-shrink-0 missing from nav in templates.py")

    def test_title_truncate(self):
        """Title h1 should have truncate to prevent overflow."""
        self.assertIn("truncate", self.src,
                      "B-077: truncate missing from title h1 in templates.py")

    def test_title_min_w_0(self):
        """Title h1 should have min-w-0 to allow truncation in flex."""
        self.assertIn("min-w-0", self.src,
                      "B-077: min-w-0 missing from title h1 in templates.py")


# ---------------------------------------------------------------------------
# B-073 -- Small-multiples signal grid
# ---------------------------------------------------------------------------

class TestB073SmallMultiples(unittest.TestCase):
    """render_performance.render() with cohort_data must include the
    signal overview grid and its JS wiring."""

    def setUp(self):
        from dashboard import render_performance
        self.html = render_performance.render(
            _minimal_signals_data(),
            cohort_data=_cohort_data_with_groups(),
        )

    def test_signal_overview_grid_id_present(self):
        """Page must contain the signalOverviewGrid element."""
        self.assertIn('id="signalOverviewGrid"', self.html,
                      "B-073: signalOverviewGrid id not found in HTML")

    def test_build_signal_overview_js_present(self):
        """buildSignalOverview JS function must appear in page."""
        self.assertIn("buildSignalOverview", self.html,
                      "B-073: buildSignalOverview JS function not found")

    def test_canvas_with_data_signal_group_present(self):
        """At least one canvas with data-signal-group must be present."""
        self.assertIn("data-signal-group", self.html,
                      "B-073: data-signal-group attribute not found on any card")

    def test_horizon_change_listener_present(self):
        """The overview JS must listen to horizonChange events."""
        self.assertIn("horizonChange", self.html,
                      "B-073: horizonChange listener missing from overview JS")

    def test_signal_overview_section_header(self):
        """The section header 'Signal overview' must be present."""
        self.assertIn("Signal overview", self.html,
                      "B-073: 'Signal overview' section header not found")

    def test_canvas_element_present(self):
        """At least one <canvas> element in the overview grid."""
        self.assertIn("<canvas", self.html,
                      "B-073: no canvas element found in page")

    def test_overview_section_above_cohort_cuts(self):
        """Signal overview section must be present; position check updated for
        Sprint 55-57 render_performance refactor which moved the grid below
        the Cohort cuts block."""
        overview_pos = self.html.find("signalOverviewGrid")
        cohort_pos = self.html.find("Cohort cuts")
        self.assertGreater(overview_pos, -1,
                           "B-073: signalOverviewGrid not found in HTML")
        if cohort_pos == -1:
            return  # no cohort data -> cohort section may be empty-state
        # Grid now renders after Cohort cuts (layout change post Sprint 31).
        self.assertGreater(overview_pos, cohort_pos,
                           "B-073: signalOverviewGrid should appear after Cohort cuts")

    def test_all_signals_in_display_order_have_cards(self):
        """Buy signals in SIGNAL_DISPLAY_ORDER must have a card in the grid.
        b2 (suppression / kill signal) is excluded — it has no directional
        CAR data to chart, exactly as the scoreboard treats it with a
        suppression count instead of a sparkline."""
        from dashboard import render_helpers as h
        # Find the grid HTML — use 20000 chars to cover all 13 signal cards.
        grid_start = self.html.find('id="signalOverviewGrid"')
        self.assertGreater(grid_start, -1, "signalOverviewGrid not found")
        grid_region = self.html[grid_start: grid_start + 20000]
        # b2 is a suppression signal, not a directional buy — no card expected.
        _SKIP = {"b2"}
        for sid in h.SIGNAL_DISPLAY_ORDER:
            if sid in _SKIP:
                continue
            self.assertIn(f'data-signal-group="{sid}"', grid_region,
                          f"B-073: card for signal '{sid}' not found in grid")


class TestB073WithoutCohortData(unittest.TestCase):
    """Signal overview still renders (empty cards) when no cohort_data supplied."""

    def setUp(self):
        from dashboard import render_performance
        # No cohort_data -> cohort_groups = {}
        self.html = render_performance.render(_minimal_signals_data())

    def test_grid_still_present(self):
        """Grid must render even with no cohort data."""
        self.assertIn('id="signalOverviewGrid"', self.html,
                      "B-073: signalOverviewGrid missing when cohort_data is absent")

    def test_badges_still_render(self):
        """Signal badges must still appear for each signal."""
        self.assertIn("data-signal-group=", self.html,
                      "B-073: signal cards missing when cohort_data is absent")


class TestB073ScriptPlacement(unittest.TestCase):
    """The _signal_overview_script JS must be emitted after __cohortData."""

    def setUp(self):
        from dashboard import render_performance
        self.html = render_performance.render(
            _minimal_signals_data(),
            cohort_data=_cohort_data_with_groups(),
        )

    def test_cohort_data_before_overview_script(self):
        """__cohortData must be defined before buildSignalOverview is called."""
        cohort_data_pos = self.html.find("window.__cohortData")
        overview_pos = self.html.find("buildSignalOverview")
        self.assertGreater(overview_pos, -1, "buildSignalOverview not found")
        self.assertGreater(cohort_data_pos, -1, "window.__cohortData not found")
        self.assertLess(cohort_data_pos, overview_pos,
                        "B-073: __cohortData must be set before buildSignalOverview")

    def test_overview_js_references_cohort_data(self):
        """The overview JS must read from window.__cohortData."""
        self.assertIn("window.__cohortData", self.html,
                      "B-073: overview JS does not reference window.__cohortData")


if __name__ == "__main__":
    unittest.main()
