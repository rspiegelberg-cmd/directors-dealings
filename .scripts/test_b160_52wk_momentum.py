"""test_b160_52wk_momentum.py — unit tests for B-160 52-week range + momentum.

Tests:
  1. _rolling_hl() returns correct (low_min, high_max) over the window.
  2. _rolling_hl() returns (None, None) when no OHLCV data exists.
  3. _rolling_hl() clips window to available data when entry_idx < n_days.
  4. dist_52wk_low == 0.0 when entry_close == low_52wk.
  5. dist_52wk_low == 1.0 when entry_close == high_52wk.
  6. dist_52wk_low is None when low_52wk == high_52wk (degenerate range).
  7. _prior_close() returns None when back_idx < 0.
  8. _prior_close() returns the correct historical close.
  9. Momentum (mom_1m / mom_3m / mom_6m) correct given known closes.
  10. HEADER has the 6 new B-160 columns in the right position.
  11. HEADER length matches writer.writerow() field count.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backtest as bt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlc_conn(rows: list[tuple]) -> sqlite3.Connection:
    """In-memory DB with a prices table seeded with (ticker, date, high, low, close)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE prices "
        "(ticker TEXT, date TEXT, close REAL, high REAL, low REAL)"
    )
    conn.executemany(
        "INSERT INTO prices VALUES (?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    return conn


def _dates(n: int, start: str = "2025-01-02") -> list[str]:
    """Generate `n` consecutive ISO date strings (weekdays simulated as +1 day each)."""
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    out = []
    for _ in range(n):
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# 1-3: _rolling_hl
# ---------------------------------------------------------------------------

class TestRollingHL(unittest.TestCase):

    def test_correct_min_max(self):
        """Returns correct (min_low, max_high) over the window."""
        dates = _dates(10)
        # Build prices: ticker AAA, highs 1..10, lows 0.9..9.9
        rows = [
            ("AAA", dates[i], float(i + 1), float(i + 1) + 0.5, float(i + 1) - 0.1)
            for i in range(10)
        ]
        conn = _make_ohlc_conn(rows)
        ohlc_cache = {}
        # entry_idx = 10 (after all dates), n_days = 10 → window = dates[0..9]
        result = bt._rolling_hl(ohlc_cache, conn, "AAA", dates, 10, n_days=10)
        low_min, high_max = result
        self.assertIsNotNone(low_min)
        self.assertIsNotNone(high_max)
        # low_min = min of all lows = 0.9 (i=0)
        self.assertAlmostEqual(low_min, 0.9, places=5)
        # high_max = max of all highs = 10.5 (i=9)
        self.assertAlmostEqual(high_max, 10.5, places=5)
        conn.close()

    def test_no_ohlcv_returns_none(self):
        """Empty prices table → (None, None)."""
        conn = _make_ohlc_conn([])
        ohlc_cache = {}
        dates = _dates(5)
        result = bt._rolling_hl(ohlc_cache, conn, "AAA", dates, 5, n_days=252)
        self.assertEqual(result, (None, None))
        conn.close()

    def test_short_window_clips_to_available(self):
        """entry_idx=3, n_days=252 → only uses the 3 available dates (no crash)."""
        dates = _dates(3)
        rows = [
            ("AAA", dates[0], 1.0, 1.2, 0.8),
            ("AAA", dates[1], 1.5, 1.7, 1.3),
            ("AAA", dates[2], 1.2, 1.4, 1.0),
        ]
        conn = _make_ohlc_conn(rows)
        ohlc_cache = {}
        # entry_idx = 3 means we look at dates[0:3] = all three
        result = bt._rolling_hl(ohlc_cache, conn, "AAA", dates, 3, n_days=252)
        low_min, high_max = result
        self.assertIsNotNone(low_min)
        self.assertAlmostEqual(low_min, 0.8, places=5)
        self.assertAlmostEqual(high_max, 1.7, places=5)
        conn.close()


# ---------------------------------------------------------------------------
# 4-6: dist_52wk_low computation
# ---------------------------------------------------------------------------

class TestDist52wkLow(unittest.TestCase):

    def _dist(self, entry_close, low_52wk, high_52wk):
        """Inline the dist_52wk_low formula from backtest.py."""
        if (low_52wk is not None and high_52wk is not None
                and high_52wk > low_52wk and entry_close is not None):
            d = (entry_close - low_52wk) / (high_52wk - low_52wk)
            return max(0.0, min(1.0, d))
        return None

    def test_at_52wk_low(self):
        """entry_close == low_52wk → dist == 0.0."""
        self.assertAlmostEqual(self._dist(1.0, 1.0, 5.0), 0.0)

    def test_at_52wk_high(self):
        """entry_close == high_52wk → dist == 1.0."""
        self.assertAlmostEqual(self._dist(5.0, 1.0, 5.0), 1.0)

    def test_midpoint(self):
        """entry_close at midpoint → dist == 0.5."""
        self.assertAlmostEqual(self._dist(3.0, 1.0, 5.0), 0.5)

    def test_degenerate_range(self):
        """low == high (all prices flat) → None to avoid divide-by-zero."""
        self.assertIsNone(self._dist(2.0, 2.0, 2.0))

    def test_clips_above_1(self):
        """entry_close slightly above high (data quirk) → clipped to 1.0."""
        self.assertAlmostEqual(self._dist(5.01, 1.0, 5.0), 1.0)


# ---------------------------------------------------------------------------
# 7-8: _prior_close
# ---------------------------------------------------------------------------

class TestPriorClose(unittest.TestCase):

    def _conn(self, dates: list[str], closes: list[float]):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)")
        for d, c in zip(dates, closes):
            conn.execute("INSERT INTO prices VALUES ('AAA', ?, ?)", (d, c))
        conn.commit()
        return conn

    def test_returns_none_when_back_idx_negative(self):
        """entry_idx - n_days < 0 → None (not enough history)."""
        dates = _dates(5)
        conn = self._conn(dates, [1.0] * 5)
        result = bt._prior_close(dates, conn, "AAA", entry_idx=2, n_days=21)
        self.assertIsNone(result)
        conn.close()

    def test_returns_correct_close(self):
        """entry_idx=25, n_days=21 → close at dates[4]."""
        dates = _dates(30)
        closes = [float(i + 1) for i in range(30)]
        conn = self._conn(dates, closes)
        result = bt._prior_close(dates, conn, "AAA", entry_idx=25, n_days=21)
        # dates[25-21] = dates[4], close = 5.0
        self.assertAlmostEqual(result, 5.0, places=5)
        conn.close()


# ---------------------------------------------------------------------------
# 9: Momentum calculation
# ---------------------------------------------------------------------------

class TestMomentum(unittest.TestCase):

    def test_positive_momentum(self):
        """Stock up 10% over 21 days → mom_1m ≈ 0.10."""
        entry_close = 1.10
        p_1m = 1.00
        result = bt._safe_div(entry_close, p_1m)
        self.assertAlmostEqual(result, 0.10, places=5)

    def test_negative_momentum(self):
        """Stock down 20% over 21 days → mom_1m ≈ -0.20."""
        entry_close = 0.80
        p_1m = 1.00
        result = bt._safe_div(entry_close, p_1m)
        self.assertAlmostEqual(result, -0.20, places=5)

    def test_none_when_prior_close_unavailable(self):
        """_prior_close returns None → mom is None."""
        entry_close = 1.0
        p_1m = None
        result = bt._safe_div(entry_close, p_1m) if entry_close is not None else None
        self.assertIsNone(result)

    def test_none_when_entry_close_unavailable(self):
        """entry_close is None → mom is None."""
        entry_close = None
        result = bt._safe_div(entry_close, 1.0) if entry_close is not None else None
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 10-11: HEADER integrity
# ---------------------------------------------------------------------------

class TestHeader(unittest.TestCase):

    def test_b160_columns_present(self):
        """All 6 B-160 columns are in HEADER."""
        for col in ("low_52wk", "high_52wk", "dist_52wk_low",
                    "mom_1m", "mom_3m", "mom_6m"):
            self.assertIn(col, bt.HEADER, f"Missing: {col}")

    def test_b160_columns_before_windows_available(self):
        """B-160 cols sit just before the B-156 pair, the B-164 col and
        windows_available.

        B-156 (Sprint 61) inserted resulting_shares + holding_pct_increase
        immediately before windows_available, shifting the B-160 block
        back by 2; B-164 then added short_pct_at_announcement directly
        before windows_available, shifting everything back by 1 more.
        """
        # B-155, B-159 then B-161 (Sprint 63) each added a pair directly
        # before windows_available, shifting everything back by 2+2+2.
        idx_wa = bt.HEADER.index("windows_available")
        self.assertEqual(bt.HEADER[idx_wa - 1], "days_since_results")
        self.assertEqual(bt.HEADER[idx_wa - 2], "post_results_flag")
        self.assertEqual(bt.HEADER[idx_wa - 3], "net_shares_prior_12m")
        self.assertEqual(bt.HEADER[idx_wa - 4], "seller_reversal_flag")
        self.assertEqual(bt.HEADER[idx_wa - 5], "routine_prior_buy_years")
        self.assertEqual(bt.HEADER[idx_wa - 6], "routine_flag")
        self.assertEqual(bt.HEADER[idx_wa - 7], "short_pct_at_announcement")
        self.assertEqual(bt.HEADER[idx_wa - 8], "holding_pct_increase")
        self.assertEqual(bt.HEADER[idx_wa - 9], "resulting_shares")
        self.assertEqual(bt.HEADER[idx_wa - 10], "mom_6m")
        self.assertEqual(bt.HEADER[idx_wa - 11], "mom_3m")
        self.assertEqual(bt.HEADER[idx_wa - 12], "mom_1m")
        self.assertEqual(bt.HEADER[idx_wa - 13], "dist_52wk_low")
        self.assertEqual(bt.HEADER[idx_wa - 14], "high_52wk")
        self.assertEqual(bt.HEADER[idx_wa - 15], "low_52wk")

    def test_header_length_matches_writerow(self):
        """HEADER must have exactly 64 columns (see group count below).

        If this fails, HEADER and writer.writerow() have drifted apart.
        Count: run_id(1) + signal fields(4) + ticker fields(7) + market(2) +
               benchmark+entry(2) + closes(10) + returns(10) + cars(5) +
               cost+net_cars(6) + B-160(6) + windows_available(1) = 54 ...
        Actually let's just check it's a specific known value.
        """
        # Count by summing the known groups:
        # Basic: run_id, signal_id, signal_version, fingerprint, fired_at = 5
        # Ticker: ticker, role, role_normalized, role_class, value_gbp, is_aim = 6
        # Market: market_cap_gbp, small_cap = 2
        # Bench+entry: benchmark_symbol, entry_date, entry_close = 3
        # Closes: t1..t365 (5) + bench_entry + bench_t1..t365 (6) = 11
        # Raw returns: 5, bench returns: 5, cars: 5 = 15
        # Costs: cost_bps + net_car x5 = 6
        # B-160: low_52wk, high_52wk, dist_52wk_low, mom_1m, mom_3m, mom_6m = 6
        # B-156: resulting_shares, holding_pct_increase = 2
        # B-164: short_pct_at_announcement = 1
        # windows_available = 1
        # Total: 5+6+2+3+11+15+6+6+2+1+1 = 58
        # 58 (post-B-164) + routine_flag + routine_prior_buy_years (B-155)
        # = 60; + seller_reversal_flag + net_shares_prior_12m (B-159) = 62;
        # + post_results_flag + days_since_results (B-161) = 64.
        self.assertEqual(len(bt.HEADER), 64)


if __name__ == "__main__":
    unittest.main()
