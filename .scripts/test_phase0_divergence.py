"""Phase 0 tests (cohort-performance-chart sprint) — interim mean-vs-median
divergence warning on the per-signal performance scoreboard.

Trigger rule (evaluated at the T+90 horizon):

    fire if  |mean - median| > 5pp  AND  |mean - median| > 0.5 * |mean|

The warning protects real-money decisions from single-outlier contamination
(e.g. T3 NED shows mean +7.9% at T+90 but median -5.0%, driven by one TIN
trade at +1218.9%). A flat-and-bad signal (F1: mean -1.15%, tiny gap, large N)
must NOT fire — that is a real edge problem, not outlier contamination.

Run under:
    python .scripts/test_phase0_divergence.py
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

from dashboard import render_performance as rp  # noqa: E402
from dashboard import render_helpers as h        # noqa: E402


# --- Synthetic fixture -----------------------------------------------------
# Three signals exercising the three behavioural classes the trigger must
# distinguish. Values are in percent terms (matching the live JSON shape).
#
#   t3  CONTAMINATED  mean +7.9%, median -5.0% -> gap 12.9pp -> MUST fire
#   t2  CLEAN         mean +2.0%, median +1.8% -> gap 0.2pp  -> must NOT fire
#   f1  FLAT-AND-BAD  mean -1.15%, median -1.00% (N=723)     -> must NOT fire
#
# F1 is the critical negative case: a small negative mean with a tiny
# mean-median gap is a genuine edge problem, NOT outlier contamination. The
# relative-divergence condition (gap > 0.5 * |mean|) is what protects it:
# 0.15pp gap is not > 0.5 * 1.15 = 0.575pp.

CONTAMINATED_SID = "t3"
CLEAN_SID = "t2"
FLAT_BAD_SID = "f1"


def _mk_signals():
    """Build a signals_data dict with a populated T+90 horizon block."""
    t90_signals = {
        CONTAMINATED_SID: {"trades": 30, "mean_car": 7.9, "median_car": -5.0,
                           "hit_pct": 45.0},
        CLEAN_SID:        {"trades": 40, "mean_car": 2.0, "median_car": 1.8,
                           "hit_pct": 55.0},
        FLAT_BAD_SID:     {"trades": 723, "mean_car": -1.15, "median_car": -1.0,
                           "hit_pct": 48.0},
    }
    # The scoreboard server-renders the t30 horizon, but the warning reads
    # t90. Mirror the same per-signal data into t30 so the rows render.
    return {
        "horizon_aggregates": {
            "t30": {"base_rate": 50.0, "signals": t90_signals},
            "t90": {"base_rate": 55.0, "signals": t90_signals},
        },
        "cohorts": {"by_value_bucket": {}, "by_sector": []},
        "cohorts_v2": {},
        "active_clusters": [],
        "pending_diagnostics": {"total": 0, "categories": []},
    }


class TestDivergenceTrigger(unittest.TestCase):
    """render_helpers.divergence_warning — the pure trigger function."""

    def test_01_contaminated_fires(self):
        fired, gap = h.divergence_warning(7.9, -5.0)
        self.assertTrue(fired)
        # gap = |7.9 - (-5.0)| = 12.9pp
        self.assertAlmostEqual(gap, 12.9, places=6)

    def test_02_clean_does_not_fire(self):
        fired, gap = h.divergence_warning(2.0, 1.8)
        self.assertFalse(fired)
        self.assertAlmostEqual(gap, 0.2, places=6)

    def test_03_flat_and_bad_does_not_fire(self):
        # F1 case: mean -1.15%, median -1.0% -> gap 0.15pp.
        # Absolute condition fails (0.15 not > 5). Must NOT fire.
        fired, gap = h.divergence_warning(-1.15, -1.0)
        self.assertFalse(fired)
        self.assertAlmostEqual(gap, 0.15, places=6)

    def test_04_large_gap_small_mean_relative_condition(self):
        # Both conditions must hold in conjunction. A 6pp gap clears the
        # absolute bar; with mean +2.0% the relative bar (0.5*2.0=1.0pp) is
        # also cleared -> fires.
        fired, gap = h.divergence_warning(2.0, -4.0)
        self.assertTrue(fired)
        self.assertAlmostEqual(gap, 6.0, places=6)

    def test_05_big_mean_proportional_gap_does_not_fire(self):
        # mean +20%, median +16% -> gap 4pp. Absolute condition (gap>5) fails
        # even though relative (4 > 0.5*20=10? no) — both fail. No fire.
        fired, gap = h.divergence_warning(20.0, 16.0)
        self.assertFalse(fired)
        self.assertAlmostEqual(gap, 4.0, places=6)

    def test_06_boundary_exactly_5pp_does_not_fire(self):
        # Strictly-greater-than: a gap of exactly 5.0pp must NOT fire.
        fired, _ = h.divergence_warning(10.0, 5.0)
        self.assertFalse(fired)

    def test_07_missing_inputs_return_false_zero(self):
        self.assertEqual(h.divergence_warning(None, -5.0), (False, 0.0))
        self.assertEqual(h.divergence_warning(7.9, None), (False, 0.0))
        self.assertEqual(h.divergence_warning(None, None), (False, 0.0))


class TestScoreboardRendering(unittest.TestCase):
    """The badge must appear on the contaminated row only, in the rendered
    scoreboard HTML — and use the amber HTML-entity glyph."""

    def setUp(self):
        self.html = rp._scoreboard(_mk_signals(), {}, "t30")

    def test_08_contaminated_row_shows_divergence_badge(self):
        # The amber warning glyph + the gap-pp tooltip must be present.
        self.assertIn("&#9888;", self.html)
        self.assertIn("Mean diverges from median by 12.9pp", self.html)
        self.assertIn("single-outlier contamination", self.html)
        self.assertIn("Drill into the cohort before acting", self.html)

    def test_09_clean_and_flat_bad_do_not_produce_a_divergence_tooltip(self):
        # The clean (gap 0.2pp) and flat-bad (gap 0.15pp) signals must not
        # generate any divergence tooltip text.
        self.assertNotIn("Mean diverges from median by 0.2pp", self.html)
        self.assertNotIn("Mean diverges from median by 0.1pp", self.html)
        self.assertNotIn("Mean diverges from median by 0.0pp", self.html)

    def test_10_exactly_one_divergence_tooltip_in_whole_table(self):
        # Only the contaminated signal should fire, so the divergence
        # tooltip phrase appears exactly once.
        count = self.html.count("Mean diverges from median")
        self.assertEqual(count, 1)


class TestBadgeHelper(unittest.TestCase):
    """render_helpers.divergence_badge — formatting + ASCII safety."""

    def test_11_badge_uses_html_entity_not_raw_glyph(self):
        badge = h.divergence_badge(12.9)
        self.assertIn("&#9888;", badge)
        # No raw non-ASCII warning glyph (cp1252-subprocess safety).
        self.assertNotIn("⚠", badge)
        self.assertIn("text-amber-600", badge)
        self.assertIn("12.9pp", badge)


if __name__ == "__main__":
    unittest.main(verbosity=2)
