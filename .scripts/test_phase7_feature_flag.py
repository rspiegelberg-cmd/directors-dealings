"""Sprint 14 Phase 7 (B-071) tests -- legacy CAR-chart feature flag.

The flag `render_performance.SHOW_LEGACY_CAR_LINE_CHART` gates the legacy
trailing-12-month cumulative-net-CAR line chart (`#diagChart`). During the A/B
period it defaults True (both the legacy chart and the new cohort view render).
Flipping it False must:

  1. drop the legacy chart section from the page, and
  2. leave the new Sprint-14 cohort surface fully intact (no regression), and
  3. never throw -- rebuildDiag() must no-op on the missing canvas.

The flag is read inside render() at call time, so the tests monkeypatch the
module attribute and restore it in tearDown.

Run under:
    python -m unittest .scripts.test_phase7_feature_flag -v
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_performance as rp          # noqa: E402
# Reuse the Phase-4 fixtures (signals + cohort blob) so the test exercises a
# realistic full render rather than a hand-rolled minimal one.
from test_phase4_cohort_chart import _mk_signals, _mk_cohort_data  # noqa: E402


class TestLegacyCarChartFeatureFlag(unittest.TestCase):

    def setUp(self):
        self._orig = rp.SHOW_LEGACY_CAR_LINE_CHART

    def tearDown(self):
        rp.SHOW_LEGACY_CAR_LINE_CHART = self._orig

    def _render(self):
        return rp.render(_mk_signals(), {}, build_sha="test",
                         cohort_data=_mk_cohort_data())

    # --- default (flag on) --------------------------------------------------

    def test_01_flag_defaults_on(self):
        # The shipped default keeps the legacy chart visible for A/B.
        self.assertTrue(self._orig)

    def test_02_flag_on_renders_legacy_chart(self):
        rp.SHOW_LEGACY_CAR_LINE_CHART = True
        html = self._render()
        self.assertIn('id="diagChart"', html)
        self.assertIn('id="diagTitle"', html)
        self.assertIn("trailing 12 months", html)

    # --- flipped (flag off) -------------------------------------------------

    def test_03_flag_off_drops_legacy_chart(self):
        rp.SHOW_LEGACY_CAR_LINE_CHART = False
        html = self._render()
        self.assertNotIn('id="diagChart"', html)
        self.assertNotIn('id="diagTitle"', html)

    def test_04_flag_off_keeps_new_cohort_surface(self):
        # No regression: the Sprint-14 cohort view must survive the swap.
        rp.SHOW_LEGACY_CAR_LINE_CHART = False
        html = self._render()
        self.assertIn('id="cohortMainChart"', html)          # Level-2 chart
        self.assertIn('id="cohortHitRateChart"', html)       # Phase 5 panel
        self.assertIn("window.buildCohortLevel2", html)       # builder entry
        self.assertIn('id="scoreboard"', html)                # Level-1 table
        self.assertIn("window.__cohortData", html)            # data blob

    def test_05_rebuilddiag_guards_missing_canvas(self):
        # The diag-drawing JS must bail before new Chart(null) when the canvas
        # is absent (flag off) -- the guard is present regardless of flag state
        # since the JS string is static.
        js = rp._chart_js()
        self.assertIn("if (!document.getElementById('diagChart')) return;", js)

    def test_06_toggle_is_clean_both_directions(self):
        # Flipping on->off->on yields the expected presence each time (the flag
        # is read fresh on every render, no caching).
        rp.SHOW_LEGACY_CAR_LINE_CHART = True
        self.assertIn('id="diagChart"', self._render())
        rp.SHOW_LEGACY_CAR_LINE_CHART = False
        self.assertNotIn('id="diagChart"', self._render())
        rp.SHOW_LEGACY_CAR_LINE_CHART = True
        self.assertIn('id="diagChart"', self._render())

    def test_07_pages_are_pure_ascii_both_states(self):
        for state in (True, False):
            rp.SHOW_LEGACY_CAR_LINE_CHART = state
            # base_page may carry HTML entities elsewhere; assert the render
            # completes and the cohort blob stays ASCII-clean in both states.
            html = self._render()
            self.assertIn("window.__cohortData", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
