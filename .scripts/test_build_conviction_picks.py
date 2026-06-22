"""Tests for export_dashboard_json.build_conviction_picks (B-171, revised surfacing).

Runs against an in-memory SQLite DB (never the real directors.db). We build the
minimal tables the conviction adapter reads PLUS the conviction_scores shadow-log
table, then assert the REVISED contract:
  * the panel returns up to 10 picks (conviction_top10), ALWAYS (no min-score
    gate), sorted desc by score, over the trailing-28-day window
  * a buy at day-27 is included; a buy at day-29 is aged out
  * no min-score filtering: a Low-band buy still appears when it is in the top 10
  * window metadata (window_days / window_start / window_end) is emitted
  * each pick carries the per-factor display fields + inputs_missing + rank/band
  * the shadow log is upserted for EVERY buy in the window (not just the top 10),
    with rank_in_window across the whole distribution and surfaced=1 for the
    surfaced top 10 only; window_end stores the window-END (run) date
  * a re-run is idempotent (INSERT OR REPLACE on the (fingerprint, window_end) PK)
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import export_dashboard_json as ex  # noqa: E402


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE transactions (
            fingerprint TEXT PRIMARY KEY,
            date TEXT, ticker TEXT, company TEXT, director TEXT,
            role TEXT, type TEXT, value REAL, announced_at TEXT
        );
        CREATE TABLE prices (
            ticker TEXT, date TEXT, close REAL, volume INTEGER,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE tickers_meta (
            ticker TEXT PRIMARY KEY, benchmark_symbol TEXT,
            market_cap_gbp REAL, is_excluded_issuer INTEGER DEFAULT 0
        );
        CREATE TABLE reporting_dates (ticker TEXT, report_date TEXT);
        CREATE TABLE conviction_scores (
            fingerprint TEXT NOT NULL, window_end TEXT NOT NULL, scored_at TEXT,
            score REAL, band TEXT, f1_who REAL, f2_buy_size REAL,
            f3_company_size REAL, f4_earnings_timing REAL, f5_past_performance REAL,
            f6_sector_mult REAL, weights_used TEXT, earnings_dropped INTEGER DEFAULT 0,
            rank_in_window INTEGER, surfaced INTEGER DEFAULT 0, inputs_missing TEXT,
            PRIMARY KEY (fingerprint, window_end)
        );
        """
    )
    return conn


def _add_tx(conn, fp, ticker, role, value, *, director="Dir",
            announced_at="2026-06-15", cap=40_000_000.0):
    conn.execute(
        "INSERT INTO transactions (fingerprint, date, ticker, company, director, "
        "role, type, value, announced_at) VALUES (?, ?, ?, ?, ?, ?, 'BUY', ?, ?)",
        (fp, announced_at, ticker, f"{ticker} plc", director, role, value,
         announced_at),
    )
    conn.execute(
        "INSERT OR IGNORE INTO tickers_meta (ticker, market_cap_gbp) VALUES (?, ?)",
        (ticker, cap),
    )


class TestBuildConvictionPicks(unittest.TestCase):
    def setUp(self):
        self.conn = _mk_conn()
        # 4 buys all inside the trailing-28-day window ending 2026-06-18
        # (window = [2026-05-21 .. 2026-06-18]). Mixed sizes/roles so the
        # ranking is deterministic (big CEO buys outrank small NED buys).
        _add_tx(self.conn, "a", "AAA", "Chief Executive Officer", 5_000_000.0,
                announced_at="2026-06-15")
        _add_tx(self.conn, "b", "BBB", "Chief Financial Officer", 3_000_000.0,
                announced_at="2026-06-16")
        _add_tx(self.conn, "c", "CCC", "Chief Executive Officer", 1_000_000.0,
                announced_at="2026-06-17")
        _add_tx(self.conn, "d", "DDD", "Non-Executive Director", 11_000.0,
                announced_at="2026-06-18")
        self.conn.commit()
        self.today = date(2026, 6, 18)

    def test_top10_window_metadata(self):
        out = ex.build_conviction_picks(self.conn, self.today)
        self.assertEqual(out["window_days"], 28)
        self.assertEqual(out["window_end"], "2026-06-18")
        self.assertEqual(out["window_start"], "2026-05-21")  # 18 June - 28 days
        self.assertIn("top10", out)

    def test_top10_sorted_desc_no_min_score_gate(self):
        out = ex.build_conviction_picks(self.conn, self.today)
        picks = out["top10"]
        # All 4 fit within 10 -> all surfaced (no min-score bar).
        self.assertEqual(len(picks), 4)
        scores = [p["score"] for p in picks]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # The small NED buy is the weakest but STILL appears (it is in the top
        # 10) — proving there is no minimum-score filtering.
        fps = [p["fingerprint"] for p in picks]
        self.assertIn("d", fps)

    def test_at_most_10_rows_emitted(self):
        conn = _mk_conn()
        for i in range(15):
            _add_tx(conn, f"x{i}", f"T{i}", "Chief Executive Officer",
                    1_000_000.0 + i, announced_at="2026-06-15")
        conn.commit()
        out = ex.build_conviction_picks(conn, self.today)
        self.assertEqual(len(out["top10"]), 10)  # capped at 10

    def test_day27_in_window_day29_aged_out(self):
        conn = _mk_conn()
        # today = 2026-06-30; window = [2026-06-02 .. 2026-06-30].
        # day-27 (2026-06-03) is IN; day-29 (2026-06-01) is OUT.
        _add_tx(conn, "in27", "AAA", "Chief Executive Officer", 3_000_000.0,
                announced_at="2026-06-03")
        _add_tx(conn, "out29", "BBB", "Chief Executive Officer", 3_000_000.0,
                announced_at="2026-06-01")
        conn.commit()
        out = ex.build_conviction_picks(conn, date(2026, 6, 30))
        fps = [p["fingerprint"] for p in out["top10"]]
        self.assertIn("in27", fps)
        self.assertNotIn("out29", fps)

    def test_pick_has_factor_breakdown(self):
        out = ex.build_conviction_picks(self.conn, self.today)
        pick = out["top10"][0]
        self.assertIn("factors", pick)
        self.assertEqual(len(pick["factors"]), 5)  # five additive factors
        self.assertIn("inputs_missing", pick)
        self.assertIn("rank", pick)
        self.assertIn("band", pick)

    def test_shadow_log_covers_whole_distribution(self):
        ex.build_conviction_picks(self.conn, self.today)
        rows = self.conn.execute(
            "SELECT fingerprint, rank_in_window, surfaced "
            "FROM conviction_scores WHERE window_end = '2026-06-18' "
            "ORDER BY rank_in_window"
        ).fetchall()
        # All 4 buys logged, keyed by the window-END (run) date.
        self.assertEqual(len(rows), 4)
        self.assertEqual([r["rank_in_window"] for r in rows], [1, 2, 3, 4])
        # surfaced = 1 for all 4 here (all within the top 10).
        self.assertEqual([r["surfaced"] for r in rows], [1, 1, 1, 1])

    def test_shadow_log_surfaced_flag_caps_at_10(self):
        conn = _mk_conn()
        for i in range(15):
            _add_tx(conn, f"x{i}", f"T{i}", "Chief Executive Officer",
                    1_000_000.0 + i, announced_at="2026-06-15")
        conn.commit()
        ex.build_conviction_picks(conn, self.today)
        n_surfaced = conn.execute(
            "SELECT COUNT(*) AS c FROM conviction_scores "
            "WHERE window_end = '2026-06-18' AND surfaced = 1"
        ).fetchone()["c"]
        n_total = conn.execute(
            "SELECT COUNT(*) AS c FROM conviction_scores "
            "WHERE window_end = '2026-06-18'"
        ).fetchone()["c"]
        self.assertEqual(n_total, 15)      # whole distribution logged
        self.assertEqual(n_surfaced, 10)   # only top 10 flagged surfaced

    def test_rerun_idempotent(self):
        ex.build_conviction_picks(self.conn, self.today)
        ex.build_conviction_picks(self.conn, self.today)  # second run
        n = self.conn.execute(
            "SELECT COUNT(*) AS c FROM conviction_scores "
            "WHERE window_end = '2026-06-18'"
        ).fetchone()["c"]
        self.assertEqual(n, 4)  # INSERT OR REPLACE -> still 4, not 8

    def test_empty_window_returns_empty_top10(self):
        conn = _mk_conn()  # no transactions
        out = ex.build_conviction_picks(conn, self.today)
        self.assertEqual(out["top10"], [])
        self.assertEqual(out["window_end"], "2026-06-18")
        self.assertEqual(out["window_days"], 28)


if __name__ == "__main__":
    unittest.main()
