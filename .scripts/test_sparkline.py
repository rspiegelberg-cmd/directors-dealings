"""B-009 regression test for `_sparkline` in export_dashboard_json.py.

Goal: lock in the post-B-009 contract:

  * `_sparkline(..., period_months=12)` returns a 13-entry list.
  * Buckets are monthly calendar slices, M-12 ... M-1, Now.
  * Each entry is either a median CAR percentage rounded to 2 dp, or
    None when the bucket has no matured signals.
  * Empty-bucket sentinel must be None (Chart.js gap), never 0.0 --
    the pre-B-009 bug forward-filled empty buckets to 0, painting a
    misleading flat zero line.

Run under:
    python -m unittest .scripts/test_sparkline.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import export_dashboard_json as edj


def _mk(fired_at: str, car_t90: float | None) -> dict:
    """Minimal row shape that _sparkline reads."""
    return {"_fired_at": fired_at, "_car_t90": car_t90}


class TestSparkline(unittest.TestCase):
    def test_length_is_13_for_default_12_months(self):
        today = date(2026, 5, 18)
        out = edj._sparkline(rows=[], horizon="t90", today=today)
        self.assertEqual(len(out), 13)
        # All-None when there's no data.
        self.assertTrue(all(v is None for v in out))

    def test_length_respects_period_months_parameter(self):
        today = date(2026, 5, 18)
        out6 = edj._sparkline(rows=[], horizon="t90", today=today,
                              period_months=6)
        self.assertEqual(len(out6), 7)
        out24 = edj._sparkline(rows=[], horizon="t90", today=today,
                               period_months=24)
        self.assertEqual(len(out24), 25)

    def test_empty_buckets_return_None_not_zero(self):
        """The pre-B-009 bug was emitting 0.0 for empty buckets, painting
        a flat zero line. The fix is to emit None so Chart.js draws a gap."""
        today = date(2026, 5, 18)
        # Place a single signal in M-3 only. Every other bucket must be None.
        rows = [_mk("2026-02-15T12:00:00Z", 0.05)]  # 5% raw -> 5.0%
        out = edj._sparkline(rows=rows, horizon="t90", today=today)
        # Index 9 = M-3 (Now is index 12). Verify the M-3 bucket is filled
        # and every other bucket is None, not 0.
        self.assertEqual(out[9], 5.0)
        for i, v in enumerate(out):
            if i == 9:
                continue
            self.assertIsNone(v, f"bucket {i} should be None, got {v!r}")

    def test_median_aggregation_preserved(self):
        """Rupert decision (2026-05-18): keep median, do not switch to mean.
        With values [1.0, 3.0, 100.0] the median (3.0) must be reported,
        not the mean (~34.7)."""
        today = date(2026, 5, 18)
        # Three signals in the "Now" bucket (May 2026).
        rows = [
            _mk("2026-05-01T09:00:00Z", 0.01),     # 1.0%
            _mk("2026-05-05T09:00:00Z", 0.03),     # 3.0%
            _mk("2026-05-10T09:00:00Z", 1.00),     # 100% outlier
        ]
        out = edj._sparkline(rows=rows, horizon="t90", today=today)
        self.assertEqual(out[-1], 3.0,
                         "median should resist the 100% outlier")

    def test_unmatured_signals_are_excluded(self):
        """A signal with car_t90=None has not yet matured -- it must not
        contribute to the t90 sparkline (would otherwise crash on float
        compare, or worse, silently distort the median)."""
        today = date(2026, 5, 18)
        rows = [
            _mk("2026-05-01T09:00:00Z", 0.02),
            _mk("2026-05-02T09:00:00Z", None),  # not matured
            _mk("2026-05-03T09:00:00Z", 0.04),
        ]
        out = edj._sparkline(rows=rows, horizon="t90", today=today)
        # Median of [2.0, 4.0] = 3.0%, ignoring the None.
        self.assertEqual(out[-1], 3.0)

    def test_month_boundary_assignment(self):
        """A signal fired on the last day of a month must land in that
        month's bucket, not in the next month's."""
        today = date(2026, 5, 18)
        rows = [_mk("2026-04-30T23:00:00Z", 0.07)]  # last day of April
        out = edj._sparkline(rows=rows, horizon="t90", today=today)
        # Index 11 = M-1 = April 2026. Now = index 12 = May 2026.
        self.assertEqual(out[11], 7.0)
        self.assertIsNone(out[12])


if __name__ == "__main__":
    unittest.main()
