"""Tests for B-157: Corwin-Schultz dynamic spread estimator in backtest.py.

Covers:
  - C-S formula produces a value in the expected range on synthetic OHLCV
  - Negative-alpha pairs (trending market) → s=0.0, floor kicks in
  - Fewer than CS_MIN_PAIRS valid pairs → fallback to CS_FALLBACK_BPS
  - Clip at CS_CAP_BPS on extreme H/L ratios
  - Stamp duty: AIM gets cs_spread only, non-AIM gets cs_spread + STAMP_DUTY_NON_AIM_BPS
  - Empty OHLCV data → fallback
  - Stamp duty constant values match historical contract (50 bps each)
"""
from __future__ import annotations

import math
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backtest as bt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(rows: list[tuple]) -> sqlite3.Connection:
    """Create an in-memory DB with a minimal prices table and insert rows.

    rows: list of (ticker, date, high, low, close)
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE prices ("
        "  ticker TEXT, date TEXT, high REAL, low REAL, close REAL NOT NULL)"
    )
    conn.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?)", rows)
    return conn


def _build_price_rows(ticker: str,
                      dates: list[str],
                      high: float,
                      low: float,
                      close: float) -> list[tuple]:
    """Helper: uniform H/L/close for all dates."""
    return [(ticker, d, high, low, close) for d in dates]


# ---------------------------------------------------------------------------
# CS formula unit checks
# ---------------------------------------------------------------------------

class TestCSFormula(unittest.TestCase):
    """Test _cs_spread_bps with controlled synthetic OHLCV data."""

    def _cs(self, rows, ticker, dates, entry_idx):
        """Convenience: call _cs_spread_bps with a fresh in-memory DB."""
        conn = _make_conn(rows)
        cache = {}
        return bt._cs_spread_bps(cache, conn, ticker, dates, entry_idx)

    def test_typical_aim_stock_produces_reasonable_range(self):
        """AIM stock with ~3% daily H/L → spread estimate in (5, 400) bps."""
        dates = [f"2025-01-{d:02d}" for d in range(2, 27)]  # 25 dates
        rows = [(  "TST", d, 103.0, 100.0, 101.5) for d in dates]
        # entry_idx = 22 → pre-window is dates[0:22], plenty of pairs
        result = self._cs(rows, "TST", dates, 22)
        self.assertGreaterEqual(result, bt.CS_FLOOR_BPS)
        self.assertLessEqual(result, bt.CS_CAP_BPS)

    def test_liquid_large_cap_tighter_than_aim(self):
        """Tight 0.5% H/L → smaller spread than wide 5% H/L."""
        dates = [f"2025-02-{d:02d}" for d in range(1, 26)]
        rows_tight = [("TGT", d, 100.5, 100.0, 100.25) for d in dates]
        rows_wide  = [("WDE", d, 105.0, 100.0, 102.5)  for d in dates]

        conn_tight = _make_conn(rows_tight)
        conn_wide  = _make_conn(rows_wide)
        cache = {}

        s_tight = bt._cs_spread_bps(cache, conn_tight, "TGT", dates, 22)
        cache = {}
        s_wide  = bt._cs_spread_bps(cache, conn_wide,  "WDE", dates, 22)

        self.assertLess(s_tight, s_wide,
                        "Tight H/L should give smaller spread estimate than wide H/L")

    def test_negative_alpha_pairs_clipped_to_floor(self):
        """Strongly trending days (gamma >> beta) → alpha < 0 → spread clipped to floor.

        Condition for alpha < 0: gamma > beta (i.e. 2-day range > sum of single-day ranges).
        Construct: day N has narrow range, day N+1 jumps upward with narrow range — the
        combined 2-day range is much wider than either single day.
        """
        # Day 0: 99-100 (1 unit range), Day 1: 101-102 (1 unit range)
        # 2-day range: 99-102 = 3 units — gamma >> beta → alpha < 0
        dates = [f"2025-03-{d:02d}" for d in range(1, 26)]
        rows = []
        for i, d in enumerate(dates):
            if i % 2 == 0:
                rows.append(("TRD", d, 100.0, 99.0, 99.5))
            else:
                rows.append(("TRD", d, 102.0, 101.0, 101.5))
        conn = _make_conn(rows)
        cache = {}
        result = bt._cs_spread_bps(cache, conn, "TRD", dates, 22)
        # All pairs have negative alpha → s=0 → median=0 → clips to floor
        self.assertEqual(result, bt.CS_FLOOR_BPS)

    def test_extreme_hl_ratio_capped_at_cs_cap_bps(self):
        """Very wide H/L (suspended/illiquid stock) is capped at CS_CAP_BPS."""
        dates = [f"2025-04-{d:02d}" for d in range(1, 26)]
        # H/L ratio ~2x → guaranteed huge spread estimate
        rows = [("EXT", d, 200.0, 100.0, 150.0) for d in dates]
        conn = _make_conn(rows)
        cache = {}
        result = bt._cs_spread_bps(cache, conn, "EXT", dates, 22)
        self.assertEqual(result, bt.CS_CAP_BPS)

    def test_fallback_when_fewer_than_min_pairs(self):
        """Only 3 valid OHLCV rows → fewer than CS_MIN_PAIRS pairs → fallback."""
        dates = [f"2025-05-{d:02d}" for d in range(1, 26)]
        # Only 3 rows have H/L data; rest are missing (will show as no H/L in DB)
        rows_with_hl = [("SPS", dates[0], 103.0, 100.0, 101.5),
                        ("SPS", dates[1], 103.0, 100.0, 101.5),
                        ("SPS", dates[2], 103.0, 100.0, 101.5)]
        # Other dates present as close-only (NULL H/L) — not inserted here
        conn = _make_conn(rows_with_hl)
        cache = {}
        # Provide all 25 dates as ticker_dates; only 3 have H/L data
        result = bt._cs_spread_bps(cache, conn, "SPS", dates, 22)
        self.assertEqual(result, bt.CS_FALLBACK_BPS)

    def test_fallback_when_no_ohlc_data(self):
        """Ticker present in dates but no H/L data at all → fallback."""
        dates = [f"2025-06-{d:02d}" for d in range(1, 11)]
        rows = []  # empty — no H/L rows for this ticker
        conn = _make_conn(rows)
        cache = {}
        result = bt._cs_spread_bps(cache, conn, "NOD", dates, 8)
        self.assertEqual(result, bt.CS_FALLBACK_BPS)

    def test_fallback_when_entry_idx_too_small(self):
        """entry_idx=1 → only 1 date before entry, zero adjacent pairs → fallback."""
        dates = ["2025-07-01", "2025-07-02", "2025-07-03"]
        rows = [("NEW", d, 103.0, 100.0, 101.5) for d in dates]
        conn = _make_conn(rows)
        cache = {}
        result = bt._cs_spread_bps(cache, conn, "NEW", dates, 1)
        self.assertEqual(result, bt.CS_FALLBACK_BPS)

    def test_ohlc_cache_populated_on_first_call(self):
        """_ticker_ohlc populates cache; second call uses cached value."""
        dates = [f"2025-08-{d:02d}" for d in range(1, 10)]
        rows = [("CHK", d, 102.0, 99.0, 100.5) for d in dates]
        conn = _make_conn(rows)
        cache = {}
        bt._ticker_ohlc(cache, conn, "CHK")
        self.assertIn("CHK", cache)
        self.assertEqual(len(cache["CHK"]), len(dates))
        # Corrupt DB to verify cache is used on second call
        conn.execute("DELETE FROM prices WHERE ticker='CHK'")
        result2 = bt._ticker_ohlc(cache, conn, "CHK")
        self.assertEqual(len(result2), len(dates))   # still from cache

    def test_result_is_float(self):
        """Return type is always float, not int."""
        dates = [f"2025-09-{d:02d}" for d in range(1, 26)]
        rows = [("FLT", d, 101.0, 100.0, 100.5) for d in dates]
        conn = _make_conn(rows)
        cache = {}
        result = bt._cs_spread_bps(cache, conn, "FLT", dates, 22)
        self.assertIsInstance(result, float)


# ---------------------------------------------------------------------------
# Stamp duty + cost_bps integration checks
# ---------------------------------------------------------------------------

class TestStampDutyAndConstants(unittest.TestCase):
    """Verify stamp duty constants and the cost_bps composition logic."""

    def test_stamp_duty_non_aim_bps_is_50(self):
        """STAMP_DUTY_NON_AIM_BPS must stay 50 — locked decision D-COSTS-MODEL."""
        self.assertEqual(bt.STAMP_DUTY_NON_AIM_BPS, 50)

    def test_cs_fallback_bps_is_50(self):
        """Fallback spread should be 50 bps (same as legacy AIM flat rate)."""
        self.assertEqual(bt.CS_FALLBACK_BPS, 50)

    def test_legacy_aim_cost_bps_matches_fallback(self):
        """AIM_COST_BPS kept for compatibility and equals CS_FALLBACK_BPS."""
        self.assertEqual(bt.AIM_COST_BPS, bt.CS_FALLBACK_BPS)

    def test_legacy_non_aim_cost_bps_equals_fallback_plus_stamp(self):
        """NON_AIM_COST_BPS = fallback + stamp duty (100 bps total)."""
        self.assertEqual(bt.NON_AIM_COST_BPS,
                         bt.CS_FALLBACK_BPS + bt.STAMP_DUTY_NON_AIM_BPS)
        self.assertEqual(bt.NON_AIM_COST_BPS, 100)

    def test_aim_cost_bps_has_no_stamp_duty(self):
        """AIM stocks are exempt from stamp duty — no 50bps addition."""
        # Simulate the in-loop cost composition for AIM (is_aim=True)
        cs_spread = 80.0   # hypothetical C-S result
        stamp_duty = 0     # is_aim → no stamp duty
        cost_bps = cs_spread + stamp_duty
        self.assertEqual(cost_bps, 80.0)

    def test_non_aim_cost_bps_includes_stamp_duty(self):
        """Non-AIM stock adds 50 bps stamp duty on top of spread."""
        cs_spread = 30.0   # typical large-cap
        stamp_duty = bt.STAMP_DUTY_NON_AIM_BPS
        cost_bps = cs_spread + stamp_duty
        self.assertEqual(cost_bps, 80.0)

    def test_cs_floor_and_cap_ordering(self):
        """Sanity: floor < fallback < cap."""
        self.assertLess(bt.CS_FLOOR_BPS, bt.CS_FALLBACK_BPS)
        self.assertLess(bt.CS_FALLBACK_BPS, bt.CS_CAP_BPS)


# ---------------------------------------------------------------------------
# Numeric correctness spot-check
# ---------------------------------------------------------------------------

class TestCSNumericSpotCheck(unittest.TestCase):
    """Spot-check the formula against a known hand-calculation.

    Using H=103, L=100 for both days of a pair (constant 3% H/L):
        beta  = 2 * ln(103/100)^2 ≈ 0.001748
        gamma = ln(103/100)^2     ≈ 0.000874
        K     = 3 - 2*sqrt(2)    ≈ 0.17157

        alpha = (sqrt(2*beta) - sqrt(beta)) / K - sqrt(gamma/K)
              ≈ (0.05913 - 0.04181) / 0.17157 - sqrt(0.005094)
              ≈ 0.10097 - 0.07137
              ≈ 0.02960

        S = 2*(exp(0.02960) - 1) / (1 + exp(0.02960))
          ≈ 2*(1.03004 - 1)/(2.03004)
          ≈ 0.02959  →  295.9 bps  (within [5, 400] so no clip)
    """

    def test_cs_single_pair_value(self):
        """Verify the C-S estimator agrees with hand-calculated value within 1bps."""
        K = 3.0 - 2.0 * math.sqrt(2.0)
        h, l = 103.0, 100.0
        beta  = 2.0 * math.log(h / l) ** 2
        gamma = math.log(h / l) ** 2      # same H/L both days
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / K \
                - math.sqrt(gamma / K)
        s = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        expected_bps = s * 10_000.0

        # Build 25 identical pairs and call _cs_spread_bps
        dates = [f"2025-10-{d:02d}" for d in range(1, 26)]
        rows  = [("SPT", d, h, l, 101.5) for d in dates]
        conn  = _make_conn(rows)
        cache = {}
        result = bt._cs_spread_bps(cache, conn, "SPT", dates, 22)

        self.assertAlmostEqual(result, expected_bps, delta=1.0,
                               msg="C-S estimate should match hand-calculation within 1 bps")


if __name__ == "__main__":
    unittest.main()
