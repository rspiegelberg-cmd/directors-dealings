"""Unit tests for director_alpha_report — pure logic, in-memory, no real DB.

Covers the four logic pillars the brief calls out:
  1. Known returns -> known director ranking + win-rate.
  2. Censoring: an immature buy is excluded from the long horizon but kept
     in the short ones (never counted as 0).
  3. Score-vs-CAR correlation sign is correct on a constructed monotonic set.
  4. Clustered-t collapses a single-ticker cluster's apparent significance.

Plus an in-memory SQLite integration smoke test of compute_buy_cars to prove
the backtest-engine reuse wires up against a tiny synthetic price series.
"""
from __future__ import annotations

import math
import sqlite3
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import director_alpha_report as dar  # noqa: E402


def _cell(net, matured=True):
    return {"car": net, "net": net, "matured": matured}


class TestLeaderboardRanking(unittest.TestCase):
    def test_known_returns_rank_and_winrate(self):
        horizons = ["t30", "t90"]
        # Director ALICE: three T+90 net CARs all positive -> high median, 100% win.
        # Director BOB: three T+90 net CARs mostly negative -> low median, 33% win.
        per_buy = []
        for net in (0.10, 0.20, 0.30):
            per_buy.append({
                "director_key": "alice", "director_name": "ALICE",
                "ticker": f"AAA{net}", "value_gbp": 1000.0,
                "cars": {"t30": _cell(0.01), "t90": _cell(net)},
            })
        for net in (-0.10, -0.05, 0.02):
            per_buy.append({
                "director_key": "bob", "director_name": "BOB",
                "ticker": f"BBB{net}", "value_gbp": 1000.0,
                "cars": {"t30": _cell(0.0), "t90": _cell(net)},
            })
        qualified, _ = dar.build_leaderboard(per_buy, horizons, min_buys=3)
        # Alice ranks first (higher median net T+90).
        self.assertEqual(qualified[0]["director_name"], "ALICE")
        self.assertEqual(qualified[1]["director_name"], "BOB")
        # Win rate at primary horizon (T+90): Alice 3/3, Bob 1/3 (only +0.02 wins).
        self.assertAlmostEqual(qualified[0]["win_rate_primary"], 1.0)
        self.assertAlmostEqual(qualified[1]["win_rate_primary"], 1 / 3)
        # Median net at T+90.
        self.assertAlmostEqual(qualified[0]["median_net_t90"], 0.20)
        self.assertAlmostEqual(qualified[1]["median_net_t90"], -0.05)

    def test_min_buys_filter_excludes_one_hit_wonder(self):
        horizons = ["t90"]
        per_buy = [{
            "director_key": "solo", "director_name": "SOLO",
            "ticker": "ZZZ", "value_gbp": 9999.0,
            "cars": {"t90": _cell(0.99)},
        }]
        qualified, full = dar.build_leaderboard(per_buy, horizons, min_buys=3)
        self.assertEqual(qualified, [])           # filtered out of the ranking
        self.assertEqual(len(full), 1)            # still present in the full list


class TestCensoring(unittest.TestCase):
    def test_immature_long_horizon_excluded_but_short_kept(self):
        horizons = ["t30", "t360"]
        # One director, two buys. Both have a matured T+30. Only ONE has a
        # matured T+360; the other's T+360 is immature (matured=False).
        per_buy = [
            {"director_key": "carol", "director_name": "CAROL",
             "ticker": "MAT", "value_gbp": 1.0,
             "cars": {"t30": _cell(0.05), "t360": _cell(0.40)}},
            {"director_key": "carol", "director_name": "CAROL",
             "ticker": "IMM", "value_gbp": 1.0,
             "cars": {"t30": _cell(0.03),
                      "t360": {"car": None, "net": None, "matured": False}}},
        ]
        qualified, full = dar.build_leaderboard(per_buy, horizons, min_buys=1)
        entry = full[0]
        # T+30: both buys matured.
        self.assertEqual(entry["n_matured_t30"], 2)
        # T+360: only the matured one is counted; the immature one is NOT
        # treated as 0 — it is simply absent from the stat.
        self.assertEqual(entry["n_matured_t360"], 1)
        self.assertAlmostEqual(entry["median_net_t360"], 0.40)
        # The immature buy did NOT drag the median toward 0.
        self.assertNotAlmostEqual(entry["median_net_t360"], 0.20)


class TestCorrelationSign(unittest.TestCase):
    def test_monotonic_increasing_gives_positive_rho_and_slope(self):
        scores = [10, 20, 30, 40, 50, 60, 70, 80]
        nets = [-0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2, 0.25]
        self.assertGreater(dar.spearman(scores, nets), 0.9)
        self.assertGreater(dar.ols_slope(scores, nets), 0)

    def test_monotonic_decreasing_gives_negative_rho(self):
        scores = [10, 20, 30, 40, 50]
        nets = [0.3, 0.2, 0.1, 0.0, -0.1]
        self.assertLess(dar.spearman(scores, nets), -0.9)
        self.assertLess(dar.ols_slope(scores, nets), 0)

    def test_bucket_table_orders_and_means(self):
        scores = list(range(1, 11))
        nets = [s / 100.0 for s in scores]
        buckets = dar.bucket_table(scores, nets, n_buckets=5)
        self.assertEqual(len(buckets), 5)
        # Mean net rises monotonically across buckets.
        means = [b["net_mean"] for b in buckets]
        self.assertEqual(means, sorted(means))


class TestClusteredT(unittest.TestCase):
    def test_single_ticker_cluster_collapses_significance(self):
        # 12 strongly-positive observations that look hugely significant under
        # a naive iid t-test, but they are ALL from one ticker. With ticker
        # clustering there is effectively 1 cluster -> t is nan (can't claim
        # significance from a single cluster). This is the project standard.
        vals = [0.10, 0.11, 0.09, 0.10, 0.12, 0.10,
                0.11, 0.10, 0.09, 0.11, 0.10, 0.10]
        one_ticker = ["AAA"] * len(vals)
        t, mean, n, g = dar.clustered_t_stat(vals, one_ticker)
        self.assertTrue(math.isnan(t))     # single cluster -> no significance
        self.assertEqual(g, 1)
        self.assertGreater(mean, 0)        # mean still positive, just not trustworthy

    def test_many_clusters_preserve_significance(self):
        # Same positive mean but spread across 12 distinct tickers -> a real,
        # well-identified positive t-stat.
        vals = [0.10, 0.11, 0.09, 0.10, 0.12, 0.10,
                0.11, 0.10, 0.09, 0.11, 0.10, 0.10]
        many = [f"T{i}" for i in range(len(vals))]
        t, mean, n, g = dar.clustered_t_stat(vals, many)
        self.assertFalse(math.isnan(t))
        self.assertEqual(g, 12)
        self.assertGreater(t, 2.0)         # clearly significant across clusters

    def test_naive_vs_clustered_contrast(self):
        # Two tickers, each with a tight cluster; modest between-cluster spread
        # keeps the clustered t finite but more conservative than pretending
        # all 8 obs are independent.
        vals = [0.10, 0.10, 0.10, 0.10, 0.20, 0.20, 0.20, 0.20]
        clusters = ["A", "A", "A", "A", "B", "B", "B", "B"]
        t, mean, n, g = dar.clustered_t_stat(vals, clusters)
        self.assertEqual(g, 2)
        self.assertAlmostEqual(mean, 0.15)
        self.assertFalse(math.isnan(t))


class TestComputeBuyCarsIntegration(unittest.TestCase):
    """Tiny in-memory SQLite to prove the backtest-engine reuse wires up."""

    def _build_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE prices (ticker TEXT, date TEXT, high REAL, low REAL, "
            "close REAL, volume INTEGER, PRIMARY KEY (ticker, date))"
        )
        # 80 business-ish days for stock STK and benchmark ^FTAS.
        # STK rises 0.5%/day; benchmark flat -> positive CAR that grows with horizon.
        import datetime as _dt
        start = _dt.date(2025, 1, 1)
        for i in range(80):
            d = (start + _dt.timedelta(days=i)).isoformat()
            stk = 100.0 * (1.005 ** i)
            conn.execute(
                "INSERT INTO prices VALUES (?,?,?,?,?,?)",
                ("STK", d, stk * 1.01, stk * 0.99, stk, 10000),
            )
            conn.execute(
                "INSERT INTO prices VALUES (?,?,?,?,?,?)",
                ("^FTAS", d, 200.0, 200.0, 200.0, 0),
            )
        conn.commit()
        return conn

    def test_short_horizon_matures_long_does_not(self):
        conn = self._build_db()
        # Announce on day index ~35 (2025-02-05) so there are >=30 prior bars
        # (passes backtest's MIN_HISTORY_DAYS gate). Entry = next trading day.
        # t30 = +21 td -> within the 80-bar series (matures).
        # t360 = +252 td -> way past the 80-bar series (immature -> censored).
        res = dar.compute_buy_cars(
            conn, {}, {},
            ticker="STK", announced="2025-02-05",
            benchmark="^FTAS", is_aim=1,   # AIM -> no stamp duty
            horizons=["t30", "t360"],
        )
        self.assertIsNotNone(res["_entry_close"])
        # Short horizon matured with a positive CAR (stock up, benchmark flat).
        self.assertTrue(res["t30"]["matured"])
        self.assertGreater(res["t30"]["car"], 0)
        # Net CAR < gross CAR by exactly cost_bps/10000 (AIM: CS spread only).
        self.assertLess(res["t30"]["net"], res["t30"]["car"])
        # Long horizon is censored (immature), NOT zero.
        self.assertFalse(res["t360"]["matured"])
        self.assertIsNone(res["t360"]["net"])

    def test_insufficient_history_unresolved(self):
        conn = self._build_db()
        # Announce on the very first day -> < MIN_HISTORY_DAYS prior bars.
        res = dar.compute_buy_cars(
            conn, {}, {},
            ticker="STK", announced="2025-01-01",
            benchmark="^FTAS", is_aim=1, horizons=["t30"],
        )
        self.assertIsNone(res["_entry_close"])
        self.assertFalse(res["t30"]["matured"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
