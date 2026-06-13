"""Tests for backfill_expected_reporting_dates.py (B-118).

Pure projection helpers + the gap-filler write path against an in-memory DB
mirroring the post-migration-009 reporting_dates schema.

Run:
    python -m unittest test_expected_reporting_dates -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_expected_reporting_dates as be  # noqa: E402

TODAY = date(2026, 6, 6)

# Post-migration-009 reporting_dates schema (inline, for the write test).
_DDL = """
CREATE TABLE reporting_dates (
    ticker      TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'EARNINGS'
                CHECK (report_type IN ('INTERIM','FINAL','TRADING_UPDATE','EARNINGS',
                                       'PRELIM','QUARTERLY','TRADING_STMT')),
    source      TEXT NOT NULL DEFAULT 'yahoo',
    fetched_at  TEXT NOT NULL,
    confidence  TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (confidence IN ('confirmed','est')),
    PRIMARY KEY (ticker, report_date, report_type)
);
"""


class TestMedianCadence(unittest.TestCase):
    def test_default_with_too_few(self):
        self.assertEqual(be.median_cadence_days([]), be.DEFAULT_CADENCE_DAYS)
        self.assertEqual(be.median_cadence_days(["2025-06-01"]),
                         be.DEFAULT_CADENCE_DAYS)

    def test_half_year_cadence(self):
        # interim + final alternating ~ every 182 days
        ds = ["2024-03-01", "2024-09-01", "2025-03-01", "2025-09-01"]
        g = be.median_cadence_days(ds)
        self.assertTrue(170 <= g <= 200, g)

    def test_annual_cadence(self):
        ds = ["2023-09-01", "2024-09-01", "2025-09-01"]
        g = be.median_cadence_days(ds)
        self.assertTrue(360 <= g <= 370, g)

    def test_outlier_gap_clamped(self):
        # a 3-year hole must not push the cadence past the max-gap ceiling
        ds = ["2019-01-01", "2025-01-01", "2025-07-01"]
        self.assertLessEqual(be.median_cadence_days(ds), be.MAX_GAP_DAYS)


class TestEstimateNext(unittest.TestCase):
    def test_no_history_returns_none(self):
        self.assertIsNone(be.estimate_next_results_date([], TODAY))

    def test_projects_into_future_within_horizon(self):
        # last results 2026-01-15, half-year cadence -> ~2026-07 (within 180d)
        ds = ["2025-01-15", "2025-07-15", "2026-01-15"]
        out = be.estimate_next_results_date(ds, TODAY)
        self.assertIsNotNone(out)
        self.assertGreater(out, TODAY.isoformat())
        self.assertLessEqual(
            (date.fromisoformat(out) - TODAY).days, be.DEFAULT_HORIZON_DAYS)

    def test_too_far_out_returns_none(self):
        # last report a month ago, annual cadence -> next is ~11 months out
        ds = ["2024-05-10", "2025-05-10"]
        self.assertIsNone(
            be.estimate_next_results_date(ds, TODAY, horizon_days=120))

    def test_horizon_widening_includes_it(self):
        ds = ["2024-05-10", "2025-05-10"]
        out = be.estimate_next_results_date(ds, TODAY, horizon_days=400)
        self.assertIsNotNone(out)


class TestWritePath(unittest.TestCase):
    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.executescript(_DDL)
        c.execute("CREATE TABLE transactions (ticker TEXT)")
        # held: AAA (needs estimate), BBB (has confirmed future -> skip), CCC (no history)
        c.executemany("INSERT INTO transactions (ticker) VALUES (?)",
                      [("AAA",), ("BBB",), ("CCC",), ("AAA",)])
        # AAA history -> projects a near-term estimate
        c.executemany(
            "INSERT INTO reporting_dates VALUES (?,?,?,?,?,?)",
            [
                ("AAA", "2025-01-20", "INTERIM", "lse_diary", "t", "confirmed"),
                ("AAA", "2025-07-20", "PRELIM", "lse_diary", "t", "confirmed"),
                ("AAA", "2026-01-20", "INTERIM", "lse_diary", "t", "confirmed"),
                # BBB already has a CONFIRMED future date -> must be skipped
                ("BBB", "2026-06-30", "INTERIM", "lse_diary", "t", "confirmed"),
                # a stale est row that must be wiped on rerun
                ("ZZZ", "2026-07-01", "EARNINGS", "est", "t", "est"),
            ],
        )
        c.commit()
        return c

    def test_gap_filler_skips_confirmed_and_no_history(self):
        conn = self._conn()
        ests = be.build_estimates(conn, TODAY)
        tickers = {e["ticker"] for e in ests}
        self.assertIn("AAA", tickers)        # projected
        self.assertNotIn("BBB", tickers)     # has confirmed future
        self.assertNotIn("CCC", tickers)     # no history
        self.assertNotIn("ZZZ", tickers)     # not held

    def test_writes_est_confidence_and_replaces(self):
        conn = self._conn()
        ests = be.build_estimates(conn, TODAY)
        stats = be.write_estimates(conn, ests)
        self.assertEqual(stats["written"], len(ests))
        rows = conn.execute(
            "SELECT ticker, report_type, source, confidence FROM reporting_dates "
            "WHERE source='est' ORDER BY ticker").fetchall()
        self.assertTrue(rows)
        for tk, rtype, src, conf in rows:
            self.assertEqual(src, "est")
            self.assertEqual(conf, "est")
            self.assertEqual(rtype, "EARNINGS")
        # stale ZZZ est row gone; confirmed rows untouched
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM reporting_dates WHERE ticker='ZZZ'").fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM reporting_dates WHERE ticker='BBB' "
            "AND confidence='confirmed'").fetchone())

    def test_dry_run_writes_nothing(self):
        conn = self._conn()
        before = conn.execute(
            "SELECT count(*) FROM reporting_dates WHERE source='est'").fetchone()[0]
        be.write_estimates(conn, be.build_estimates(conn, TODAY), dry_run=True)
        after = conn.execute(
            "SELECT count(*) FROM reporting_dates WHERE source='est'").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
