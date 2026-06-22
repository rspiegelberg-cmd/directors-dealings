"""Tests for conviction_pipeline.py — the DB-aware Conviction Score adapter.

All tests run against an IN-MEMORY SQLite DB (never the real directors.db),
per the CLAUDE.md Zone-A / FUSE rules. We hand-build a tiny schema with just
the tables the adapter reads (transactions, prices, tickers_meta,
reporting_dates) so the suite is self-contained and fast.

Coverage:
  * tier reduction (most-senior wins) in build_company_top_tier
  * PCA-inheritance lift (a PCA inherits the company's top tier)
  * missing market cap -> neutral company_size (engine 0.5)
  * F4 drop + renormalise when no reporting dates exist
  * volume-absent graceful degrade (F2 relative leg dropped)
  * LOOKAHEAD exclusion (a post-buy price bar never moves the score)
  * rolling 28-day window bounds + full-distribution ranking
  * migration 016 reaches schema_version "16" and the table is idempotent
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import conviction_pipeline as cp  # noqa: E402


def _mk_conn() -> sqlite3.Connection:
    """In-memory DB with the minimal tables the adapter reads."""
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
        CREATE TABLE reporting_dates (
            ticker TEXT, date TEXT
        );
        """
    )
    return conn


def _add_tx(conn, fp, ticker, role, *, director="Dir", value=100000.0,
            type_="BUY", date="2026-06-15", announced_at="2026-06-15",
            company="Co"):
    conn.execute(
        "INSERT INTO transactions (fingerprint, date, ticker, company, "
        "director, role, type, value, announced_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fp, date, ticker, company, director, role, type_, value, announced_at),
    )


def _add_meta(conn, ticker, *, benchmark=None, cap=None, excluded=0):
    conn.execute(
        "INSERT INTO tickers_meta (ticker, benchmark_symbol, market_cap_gbp, "
        "is_excluded_issuer) VALUES (?, ?, ?, ?)",
        (ticker, benchmark, cap, excluded),
    )


def _add_prices(conn, ticker, series):
    """series: list of (date, close, volume)."""
    for d, c, v in series:
        conn.execute(
            "INSERT INTO prices (ticker, date, close, volume) VALUES (?, ?, ?, ?)",
            (ticker, d, c, v),
        )


class TestRosterTierReduction(unittest.TestCase):
    def test_most_senior_wins(self):
        conn = _mk_conn()
        # Same ticker, a NED and a CEO -> CEO (T1a) is the company's top tier.
        _add_tx(conn, "f1", "AAA", "Non-Executive Director", director="N")
        _add_tx(conn, "f2", "AAA", "Chief Executive Officer", director="C")
        conn.commit()
        top = cp.build_company_top_tier(conn)
        self.assertEqual(top["AAA"], "T1a")

    def test_chair_outranks_ned(self):
        conn = _mk_conn()
        _add_tx(conn, "f1", "BBB", "Non-Executive Director", director="N")
        _add_tx(conn, "f2", "BBB", "Chairman", director="Ch")
        conn.commit()
        top = cp.build_company_top_tier(conn)
        # Chair (T7) is senior — must beat NED (T3).
        self.assertEqual(top["BBB"], "T7")

    def test_pca_only_company_stays_t5(self):
        conn = _mk_conn()
        _add_tx(conn, "f1", "CCC", "Person Closely Associated", director="P")
        conn.commit()
        top = cp.build_company_top_tier(conn)
        self.assertEqual(top["CCC"], "T5")


class TestPcaInheritance(unittest.TestCase):
    def test_pca_inherits_company_top_tier(self):
        conn = _mk_conn()
        # Company has a CEO on the roster, plus a PCA buy. The PCA should
        # inherit the CEO's strength (F1 high), not the bare-PCA fallback.
        _add_tx(conn, "ceo", "DDD", "Chief Executive Officer", director="C")
        _add_tx(conn, "pca", "DDD", "Person Closely Associated", director="P")
        _add_meta(conn, "DDD")
        conn.commit()
        caches = cp.build_caches(conn)
        pca_row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='pca'"
        ).fetchone()
        res, meta = cp.score_buy(conn, pca_row, caches)
        self.assertTrue(meta["is_pca"])
        # F1 should reflect the inherited CEO strength (1.0), not bare PCA (0.3).
        self.assertAlmostEqual(res.subscores["who"], 1.0, places=6)


class TestMissingInputsDegrade(unittest.TestCase):
    def test_missing_cap_is_neutral(self):
        conn = _mk_conn()
        _add_tx(conn, "f1", "EEE", "Chief Executive Officer")
        _add_meta(conn, "EEE", cap=None)  # no market cap
        conn.commit()
        caches = cp.build_caches(conn)
        row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='f1'"
        ).fetchone()
        res, meta = cp.score_buy(conn, row, caches)
        self.assertEqual(res.subscores["company_size"], 0.5)  # neutral
        self.assertIn("company_size", meta["inputs_missing"])

    def test_no_reporting_dates_drops_f4(self):
        conn = _mk_conn()
        _add_tx(conn, "f1", "FFF", "Chief Executive Officer")
        _add_meta(conn, "FFF", cap=40_000_000.0)
        conn.commit()  # no reporting_dates rows
        caches = cp.build_caches(conn)
        row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='f1'"
        ).fetchone()
        res, meta = cp.score_buy(conn, row, caches)
        self.assertTrue(res.earnings_dropped)
        self.assertIn("earnings_timing", meta["inputs_missing"])
        # Re-normalised weights must NOT include earnings_timing.
        self.assertNotIn("earnings_timing", res.weights_used)
        self.assertAlmostEqual(sum(res.weights_used.values()), 1.0, places=6)

    def test_volume_absent_degrades_to_absolute(self):
        conn = _mk_conn()
        _add_tx(conn, "f1", "GGG", "Chief Executive Officer", value=2_000_000.0)
        _add_meta(conn, "GGG", cap=40_000_000.0)
        # Prices with NO volume column populated -> turnover None.
        _add_prices(conn, "GGG", [("2026-06-10", 100.0, None)])
        conn.commit()
        caches = cp.build_caches(conn)
        row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='f1'"
        ).fetchone()
        res, meta = cp.score_buy(conn, row, caches)
        self.assertIn("buy_size_relative", meta["inputs_missing"])
        # Buy size still scores (absolute leg), > 0 for a £2m buy.
        self.assertGreater(res.subscores["buy_size"], 0.0)


class TestLookahead(unittest.TestCase):
    def test_post_buy_bar_does_not_move_score(self):
        conn = _mk_conn()
        # Two identical CEO buys on the same effective day, same ticker family,
        # but one ticker has a huge post-buy price spike and the other doesn't.
        # The post-buy spike must NOT change F5 trailing return (lookahead).
        _add_tx(conn, "f1", "HHH", "Chief Executive Officer",
                announced_at="2026-06-15", date="2026-06-15")
        _add_meta(conn, "HHH", cap=40_000_000.0)
        # Pre-buy history (strictly before 2026-06-15) identical to the control.
        pre = [
            ("2026-03-10", 100.0, 1000),
            ("2026-06-12", 90.0, 1000),
        ]
        _add_prices(conn, "HHH", pre)
        # A post-buy bar that, if leaked, would flip the trailing return.
        conn.execute(
            "INSERT INTO prices (ticker, date, close, volume) VALUES (?, ?, ?, ?)",
            ("HHH", "2026-06-16", 500.0, 1000),
        )
        conn.commit()
        caches = cp.build_caches(conn)
        row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='f1'"
        ).fetchone()
        res, _ = cp.score_buy(conn, row, caches)
        # trailing_return must use only pre-buy closes: 90/100 - 1 = -0.10.
        tret = cp.trailing_return(conn, "HHH", "2026-06-15")
        self.assertAlmostEqual(tret, -0.10, places=6)
        # And the post-buy 500.0 bar is excluded.
        self.assertLess(tret, 0.0)

    def test_turnover_excludes_buy_day_and_after(self):
        conn = _mk_conn()
        _add_prices(conn, "III", [
            ("2026-06-10", 100.0, 10),
            ("2026-06-14", 100.0, 10),
            ("2026-06-15", 100.0, 999999),  # buy day — must be excluded
            ("2026-06-16", 100.0, 999999),  # after — must be excluded
        ])
        conn.commit()
        t = cp.avg_daily_turnover(conn, "III", "2026-06-15")
        # Only the two pre-buy bars: (100*10 + 100*10)/2 = 1000.
        self.assertAlmostEqual(t, 1000.0, places=6)


class TestEarningsDistances(unittest.TestCase):
    def test_next_and_last(self):
        conn = _mk_conn()
        conn.execute("INSERT INTO reporting_dates (ticker, date) VALUES (?, ?)",
                     ("JJJ", "2026-07-10"))  # 25 days after buy
        conn.execute("INSERT INTO reporting_dates (ticker, date) VALUES (?, ?)",
                     ("JJJ", "2026-05-20"))  # 26 days before buy
        conn.commit()
        nxt, last = cp.earnings_distances(conn, "JJJ", "2026-06-15")
        self.assertEqual(nxt, 25.0)
        self.assertEqual(last, 26.0)

    def test_no_rows_returns_none_none(self):
        conn = _mk_conn()
        nxt, last = cp.earnings_distances(conn, "KKK", "2026-06-15")
        self.assertIsNone(nxt)
        self.assertIsNone(last)


class TestScoreWindow(unittest.TestCase):
    """B-171 revised surfacing: rolling trailing-28-day selection."""

    def test_window_includes_day27_excludes_day29(self):
        conn = _mk_conn()
        # as_of = 2026-06-30; window = [2026-06-02 .. 2026-06-30] inclusive.
        # day-27 (2026-06-03) is IN; day-29 (2026-06-01) is OUT.
        _add_tx(conn, "in27", "AAA", "Chief Executive Officer",
                value=3_000_000.0, announced_at="2026-06-03", date="2026-06-03")
        _add_tx(conn, "out29", "BBB", "Chief Executive Officer",
                value=3_000_000.0, announced_at="2026-06-01", date="2026-06-01")
        # An as-of-day buy is also in-window (inclusive upper bound).
        _add_tx(conn, "today", "CCC", "Chief Executive Officer",
                value=2_000_000.0, announced_at="2026-06-30", date="2026-06-30")
        for tk in ("AAA", "BBB", "CCC"):
            _add_meta(conn, tk, cap=40_000_000.0)
        conn.commit()

        ranked = cp.score_window(conn, "2026-06-30", days=28)
        fps = [r["fingerprint"] for r in ranked]
        self.assertIn("in27", fps)     # day-27 -> in window
        self.assertIn("today", fps)    # as-of day -> in window
        self.assertNotIn("out29", fps)  # day-29 -> aged out

    def test_buys_only_and_excluded_issuer_filtered(self):
        conn = _mk_conn()
        _add_tx(conn, "buy", "AAA", "Chief Executive Officer",
                value=2_000_000.0, announced_at="2026-06-20", date="2026-06-20")
        _add_tx(conn, "sell", "AAA", "Chief Executive Officer", type_="SELL",
                value=9_000_000.0, announced_at="2026-06-21", date="2026-06-21")
        _add_tx(conn, "excl", "ZZZ", "Chief Executive Officer",
                value=5_000_000.0, announced_at="2026-06-22", date="2026-06-22")
        _add_meta(conn, "AAA", cap=40_000_000.0)
        _add_meta(conn, "ZZZ", cap=40_000_000.0, excluded=1)
        conn.commit()
        ranked = cp.score_window(conn, "2026-06-30", days=28)
        fps = [r["fingerprint"] for r in ranked]
        self.assertEqual(fps, ["buy"])  # sells + excluded issuers dropped

    def test_full_ranking_desc_with_rank_in_window(self):
        conn = _mk_conn()
        _add_tx(conn, "big", "AAA", "Chief Executive Officer",
                value=5_000_000.0, announced_at="2026-06-20", date="2026-06-20")
        _add_tx(conn, "small", "BBB", "Non-Executive Director",
                value=11_000.0, announced_at="2026-06-21", date="2026-06-21")
        for tk in ("AAA", "BBB"):
            _add_meta(conn, tk, cap=40_000_000.0)
        conn.commit()
        ranked = cp.score_window(conn, "2026-06-30", days=28)
        self.assertEqual(len(ranked), 2)
        self.assertGreaterEqual(ranked[0]["score"], ranked[1]["score"])
        self.assertEqual(ranked[0]["fingerprint"], "big")  # big CEO buy wins
        self.assertEqual(ranked[0]["rank_in_window"], 1)
        self.assertEqual(ranked[1]["rank_in_window"], 2)


class TestRealSchema(unittest.TestCase):
    """Integration tests that build the test DB from the REAL project schema.

    DURABLE FIX for the schema-drift class of bug (the `reporting_dates.date`
    crash): instead of fabricating tables with hand-picked column names, these
    tests apply `db_schema.sql` + every chained migration via `db.migrate`, then
    INSERT fixture rows using the REAL column names. Any future rename of a
    column the adapter reads (reporting_dates.report_date, prices.close/volume,
    tickers_meta.market_cap_gbp/benchmark_symbol/is_excluded_issuer,
    transactions.*) now fails LOUDLY here rather than only at real-DB runtime.

    The hand-rolled `_mk_conn` suite above is retained for fast unit coverage of
    the scoring logic; this class is the guard against column-name drift.
    """

    def _real_conn(self):
        import db
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(conn)
        return conn

    def _insert_tx(self, conn, *, fp, ticker, role, director="Dir",
                   value=100000.0, type_="BUY", date="2026-06-15",
                   announced_at="2026-06-15", company="Co", shares=1000):
        conn.execute(
            "INSERT INTO transactions (fingerprint, first_seen, last_seen, "
            "seen_count, date, ticker, company, director, role, type, shares, "
            "value, announced_at) "
            "VALUES (?, 't', 't', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fp, date, ticker, company, director, role, type_, shares, value,
             announced_at),
        )

    def _insert_meta(self, conn, ticker, *, benchmark=None, cap=None,
                     excluded=0):
        conn.execute(
            "INSERT INTO tickers_meta (ticker, benchmark_symbol, "
            "market_cap_gbp, is_excluded_issuer, updated_at) "
            "VALUES (?, ?, ?, ?, 't')",
            (ticker, benchmark, cap, excluded),
        )

    def _insert_reporting_date(self, conn, ticker, report_date,
                               report_type="EARNINGS"):
        # Uses the REAL column name `report_date` — the column the production
        # crash was about. If this column is ever renamed, this INSERT (and the
        # earnings_distances SELECT below) fail here.
        conn.execute(
            "INSERT INTO reporting_dates (ticker, report_date, report_type, "
            "source, fetched_at) VALUES (?, ?, ?, 'test', 't')",
            (ticker, report_date, report_type),
        )

    def _insert_price(self, conn, ticker, date, close, volume=1000):
        conn.execute(
            "INSERT INTO prices (ticker, date, close, volume, fetched_at) "
            "VALUES (?, ?, ?, ?, 't')",
            (ticker, date, close, volume),
        )

    def test_earnings_distances_executes_real_query(self):
        """The exact path that crashed on the real DB (reporting_dates.date).

        Builds the real `reporting_dates` schema and inserts rows, then calls
        earnings_distances — which previously did `SELECT date FROM
        reporting_dates`. On the real schema that raises OperationalError: no
        such column: date. This test fails LOUDLY if the bug regresses.
        """
        conn = self._real_conn()
        self._insert_reporting_date(conn, "JJJ", "2026-07-10")  # 25d after
        self._insert_reporting_date(conn, "JJJ", "2026-05-20",
                                    report_type="INTERIM")  # 26d before
        conn.commit()
        nxt, last = cp.earnings_distances(conn, "JJJ", "2026-06-15")
        self.assertEqual(nxt, 25.0)
        self.assertEqual(last, 26.0)

    def test_score_window_end_to_end_with_reporting_dates(self):
        """Full score_window over the real schema, incl. a ticker WITH
        reporting_dates rows so earnings_distances' real query actually runs.

        This is the broad guard: it exercises build_company_top_tier,
        avg_daily_turnover, trailing_return, earnings_distances, sector_hotness,
        the tickers_meta cache, and the score_window transaction/tickers_meta
        join — all against the REAL column names in one pass.
        """
        conn = self._real_conn()
        # Ticker WITH reporting dates -> earnings_distances query executes.
        self._insert_tx(conn, fp="ceo", ticker="AAA",
                        role="Chief Executive Officer", value=2_000_000.0,
                        announced_at="2026-06-20", date="2026-06-20")
        self._insert_meta(conn, "AAA", benchmark="^FTAS", cap=40_000_000.0)
        self._insert_reporting_date(conn, "AAA", "2026-07-15")  # forward
        self._insert_reporting_date(conn, "AAA", "2026-05-01",
                                    report_type="INTERIM")      # trailing
        # Pre-buy price + benchmark history (lookahead-safe).
        for d, c in [("2026-03-10", 100.0), ("2026-06-18", 110.0)]:
            self._insert_price(conn, "AAA", d, c)
            self._insert_price(conn, "^FTAS", d, c)
        # A second, excluded issuer that must be filtered out by score_window.
        self._insert_tx(conn, fp="excl", ticker="ZZZ",
                        role="Chief Executive Officer", value=5_000_000.0,
                        announced_at="2026-06-21", date="2026-06-21")
        self._insert_meta(conn, "ZZZ", cap=40_000_000.0, excluded=1)
        conn.commit()

        ranked = cp.score_window(conn, "2026-06-30", days=28)
        fps = [r["fingerprint"] for r in ranked]
        self.assertIn("ceo", fps)
        self.assertNotIn("excl", fps)  # excluded issuer dropped
        # The AAA buy must NOT report earnings_timing as missing, proving the
        # earnings_distances real query returned data (not (None, None)).
        ceo = next(r for r in ranked if r["fingerprint"] == "ceo")
        self.assertNotIn("earnings_timing", ceo["inputs_missing"])

    def test_all_table_touching_factors_run_on_real_schema(self):
        """Each table-touching helper executes cleanly against real columns.

        A focused per-function smoke that would have caught the `date` bug and
        catches any sibling drift in prices / tickers_meta / transactions.
        """
        conn = self._real_conn()
        self._insert_tx(conn, fp="f1", ticker="BBB",
                        role="Chief Executive Officer", value=1_000_000.0,
                        announced_at="2026-06-15", date="2026-06-15")
        self._insert_meta(conn, "BBB", benchmark="^FTAS", cap=50_000_000.0)
        self._insert_reporting_date(conn, "BBB", "2026-07-01")
        for d, c in [("2026-03-10", 100.0), ("2026-06-12", 90.0)]:
            self._insert_price(conn, "BBB", d, c, volume=5000)
            self._insert_price(conn, "^FTAS", d, c, volume=5000)
        conn.commit()

        # build_company_top_tier (transactions.ticker/role)
        top = cp.build_company_top_tier(conn)
        self.assertEqual(top["BBB"], "T1a")
        # avg_daily_turnover (prices.close/volume)
        self.assertIsNotNone(cp.avg_daily_turnover(conn, "BBB", "2026-06-15"))
        # trailing_return (prices.date/close)
        self.assertIsNotNone(cp.trailing_return(conn, "BBB", "2026-06-15"))
        # earnings_distances (reporting_dates.report_date) — the crash path
        nxt, last = cp.earnings_distances(conn, "BBB", "2026-06-15")
        self.assertEqual(nxt, 16.0)  # 2026-07-01 is 16 days after the buy
        # sector_hotness (prices for the benchmark symbol)
        self.assertIsNotNone(cp.sector_hotness(conn, "^FTAS", "2026-06-15"))
        # score_buy end-to-end (tickers_meta cache + all of the above)
        caches = cp.build_caches(conn)
        row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='f1'"
        ).fetchone()
        res, meta = cp.score_buy(conn, row, caches)
        self.assertIsNotNone(res.score)
        self.assertNotIn("earnings_timing", meta["inputs_missing"])


class TestMigration016(unittest.TestCase):
    def test_schema_reaches_16_and_table_idempotent(self):
        import db
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(conn)
        ver = db.get_meta(conn, "schema_version")
        self.assertEqual(ver, "16")
        # Table exists.
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='conviction_scores'"
        ).fetchone()
        self.assertIsNotNone(row)
        # INSERT OR REPLACE is idempotent on (fingerprint, window_end).
        # First insert a parent transaction to satisfy the FK.
        conn.execute(
            "INSERT INTO transactions (fingerprint, first_seen, last_seen, "
            "seen_count, date, ticker, company, director, type, shares) "
            "VALUES ('fp1','t','t',1,'2026-06-15','AAA','Co','D','BUY',1)"
        )
        for _ in range(2):
            conn.execute(
                "INSERT OR REPLACE INTO conviction_scores "
                "(fingerprint, window_end, scored_at, score, band, rank_in_window, "
                " surfaced, weights_used, inputs_missing) "
                "VALUES ('fp1','2026-06-15','t',77.0,'High',1,1,?,?)",
                (json.dumps({"who": 0.25}), json.dumps([])),
            )
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM conviction_scores"
        ).fetchone()["c"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
