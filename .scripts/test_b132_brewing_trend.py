"""Tests for B-132 — cluster-brewing trend (count + 8-week sparkline).

Covers the walk-forward exporter (build_cluster_brewing_trend /
_brewing_count_as_of), the render sparkline helper, and the panel trend chip.

Run:
    python -m unittest test_b132_brewing_trend -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import export_dashboard_json as ex  # noqa: E402
from dashboard import render_index  # noqa: E402

TODAY = date(2026, 6, 6)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE transactions (ticker TEXT, company TEXT, "
              "director TEXT, date TEXT, type TEXT, value REAL, "
              "cluster_id TEXT, price_audit TEXT, role_normalized TEXT, "
              "role TEXT)")
    c.execute("CREATE TABLE tickers_meta (ticker TEXT, is_excluded_issuer INTEGER)")
    return c


def _buy(c, ticker, director, d, cid, value=60000):
    # role columns present (B-136 cluster SELECT reads them); individuals here.
    c.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
              (ticker, f"{ticker} plc", director, d, "BUY", value, cid, None,
               None, None))


class TestBrewingCountAsOf(unittest.TestCase):
    def test_brewing_window(self):
        c = _conn()
        # last buy 45 days before today -> brewing (30-90d window)
        d45 = (TODAY - timedelta(days=45)).isoformat()
        d47 = (TODAY - timedelta(days=47)).isoformat()
        _buy(c, "AAA", "Dir1", d47, "AAA-1")
        _buy(c, "AAA", "Dir2", d45, "AAA-1")
        self.assertEqual(ex._brewing_count_as_of(c, TODAY), 1)

    def test_active_not_brewing(self):
        c = _conn()
        # last buy 10 days ago -> active, not brewing
        d10 = (TODAY - timedelta(days=10)).isoformat()
        _buy(c, "AAA", "Dir1", d10, "AAA-1")
        _buy(c, "AAA", "Dir2", d10, "AAA-1")
        self.assertEqual(ex._brewing_count_as_of(c, TODAY), 0)

    def test_single_director_excluded(self):
        c = _conn()
        d45 = (TODAY - timedelta(days=45)).isoformat()
        _buy(c, "AAA", "Dir1", d45, "AAA-1")  # only one director
        self.assertEqual(ex._brewing_count_as_of(c, TODAY), 0)

    def test_walk_forward_ignores_future_buys(self):
        c = _conn()
        # As-of a past date, a later buy must NOT pull the cluster into "active".
        as_of = TODAY - timedelta(days=60)
        # two buys ~45d before as_of -> brewing as-of as_of
        _buy(c, "AAA", "Dir1", (as_of - timedelta(days=45)).isoformat(), "AAA-1")
        _buy(c, "AAA", "Dir2", (as_of - timedelta(days=44)).isoformat(), "AAA-1")
        # a future buy (after as_of) that would make it "active" if leaked in
        _buy(c, "AAA", "Dir1", (as_of + timedelta(days=5)).isoformat(), "AAA-1")
        self.assertEqual(ex._brewing_count_as_of(c, as_of), 1)


class TestBuildTrend(unittest.TestCase):
    def test_shape(self):
        c = _conn()
        d45 = (TODAY - timedelta(days=45)).isoformat()
        _buy(c, "AAA", "Dir1", d45, "AAA-1")
        _buy(c, "AAA", "Dir2", d45, "AAA-1")
        out = ex.build_cluster_brewing_trend(c, TODAY)
        self.assertEqual(set(out), {"current", "avg_30d", "weekly"})
        self.assertEqual(len(out["weekly"]), 8)
        self.assertTrue(all(isinstance(v, int) for v in out["weekly"]))
        self.assertEqual(out["current"], out["weekly"][-1])
        self.assertEqual(out["current"], 1)
        self.assertIsInstance(out["avg_30d"], float)


class TestSparkline(unittest.TestCase):
    def test_renders_polyline(self):
        svg = render_index._brewing_sparkline_svg([0, 1, 2, 1, 3, 2, 4, 3])
        self.assertIn("<svg", svg)
        self.assertIn("<polyline", svg)

    def test_too_few_points(self):
        self.assertEqual(render_index._brewing_sparkline_svg([2]), "")
        self.assertEqual(render_index._brewing_sparkline_svg([]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
