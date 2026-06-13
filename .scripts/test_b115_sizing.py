"""B-115 / spec 07 — unit tests for conviction position sizing.

Pure-function tests on sizing.position_size(); no DB, safe in any sandbox.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sizing import position_size, FLOOR_GBP, CAP_GBP, SCHEMES  # noqa: E402


class TestPositionSize(unittest.TestCase):

    def test_all_schemes_respect_floor_and_cap(self):
        for scheme in SCHEMES:
            for v in (5_000, 50_000, 500_000, 5_000_000, 20_000_000):
                n = position_size(v, sizing=scheme)
                self.assertGreaterEqual(n, FLOOR_GBP, (scheme, v))
                self.assertLessEqual(n, CAP_GBP, (scheme, v))

    def test_missing_or_nonpositive_value_sizes_at_floor(self):
        self.assertEqual(position_size(0), FLOOR_GBP)
        self.assertEqual(position_size(-100), FLOOR_GBP)
        self.assertEqual(position_size(None), FLOOR_GBP)

    def test_log_is_monotonic_nondecreasing(self):
        vals = (10_000, 50_000, 100_000, 500_000, 5_000_000, 20_000_000)
        sizes = [position_size(v, sizing="log") for v in vals]
        for a, b in zip(sizes, sizes[1:]):
            self.assertLessEqual(a, b)

    def test_log_reference_points(self):
        # value == LOG_REF (50k) -> log10(1)=0 -> clamped up to floor
        self.assertEqual(position_size(50_000, sizing="log"), FLOOR_GBP)
        # 10x ref (500k) -> base * 1 = 1000
        self.assertAlmostEqual(position_size(500_000, sizing="log"), 1000.0, places=6)
        # 100x ref (5M) -> base * 2 = 2000
        self.assertAlmostEqual(position_size(5_000_000, sizing="log"), 2000.0, places=6)

    def test_flat_is_conviction_blind(self):
        small = position_size(10_000, sizing="flat")
        large = position_size(5_000_000, sizing="flat")
        self.assertEqual(small, large)
        self.assertEqual(small, 1000.0)

    def test_constant_value_yields_identical_notionals(self):
        # spec S6 invariant: weighted == flat when every trade has equal value
        sizes = {position_size(200_000, sizing="log") for _ in range(5)}
        self.assertEqual(len(sizes), 1)

    def test_tier_steps(self):
        self.assertEqual(position_size(5_000, sizing="tier"), FLOOR_GBP)   # < 10k
        self.assertEqual(position_size(20_000, sizing="tier"), 500.0)
        self.assertEqual(position_size(100_000, sizing="tier"), 1000.0)
        self.assertEqual(position_size(500_000, sizing="tier"), 2000.0)
        self.assertEqual(position_size(2_000_000, sizing="tier"), 5000.0)

    def test_linear_factor_and_saturation(self):
        # 0.5% of 200k = 1000
        self.assertAlmostEqual(position_size(200_000, sizing="linear"), 1000.0, places=6)
        # saturates at cap well above £1M
        self.assertEqual(position_size(5_000_000, sizing="linear"), CAP_GBP)

    def test_unknown_scheme_raises(self):
        with self.assertRaises(ValueError):
            position_size(100_000, sizing="bogus")


if __name__ == "__main__":
    unittest.main()
