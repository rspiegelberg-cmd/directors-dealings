"""Tests for B-138: small_cap classification and backtest field pass-through.

Safe to run from bash (uses in-memory SQLite only, never touches .data/).
"""
from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import sqlite3


# ---------------------------------------------------------------------------
# Helpers — build a minimal in-memory DB with tickers_meta + required tables
# ---------------------------------------------------------------------------

def _make_conn():
    """Return an in-memory sqlite3 connection with the tables needed for tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickers_meta (
            ticker           TEXT PRIMARY KEY,
            sector           TEXT,
            benchmark_symbol TEXT,
            is_aim           INTEGER NOT NULL DEFAULT 0,
            market_cap_gbp   REAL,
            shares_outstanding REAL,
            website_url      TEXT,
            is_excluded_issuer INTEGER DEFAULT 0,
            small_cap        INTEGER DEFAULT 0,
            updated_at       TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z'
        );
        CREATE TABLE IF NOT EXISTS transactions (
            fingerprint    TEXT PRIMARY KEY,
            first_seen     TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',
            last_seen      TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',
            seen_count     INTEGER NOT NULL DEFAULT 1,
            date           TEXT NOT NULL DEFAULT '2026-01-01',
            ticker         TEXT NOT NULL,
            company        TEXT NOT NULL DEFAULT '',
            director       TEXT NOT NULL DEFAULT '',
            role           TEXT,
            role_normalized TEXT,
            type           TEXT NOT NULL DEFAULT 'BUY',
            shares         INTEGER NOT NULL DEFAULT 100,
            price          REAL NOT NULL DEFAULT 1.0,
            value          REAL NOT NULL DEFAULT 100.0,
            context        TEXT,
            url            TEXT,
            announced_at   TEXT,
            cluster_id     TEXT,
            first_time_buy INTEGER NOT NULL DEFAULT 0,
            parser_source  TEXT,
            buy_strictness TEXT,
            price_audit    TEXT,
            resulting_shares INTEGER
        );
        CREATE TABLE IF NOT EXISTS signals (
            signal_id      TEXT NOT NULL,
            signal_version TEXT NOT NULL,
            fingerprint    TEXT NOT NULL,
            fired_at       TEXT NOT NULL,
            confidence     TEXT,
            metadata       TEXT,
            PRIMARY KEY (signal_id, signal_version, fingerprint),
            FOREIGN KEY (fingerprint) REFERENCES transactions(fingerprint)
        );
        CREATE TABLE IF NOT EXISTS prices (
            ticker     TEXT NOT NULL,
            date       TEXT NOT NULL,
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL NOT NULL,
            volume     INTEGER,
            source     TEXT NOT NULL DEFAULT 'yahoo',
            fetched_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id       TEXT PRIMARY KEY,
            started_at   TEXT NOT NULL,
            finished_at  TEXT,
            signal_id    TEXT,
            signal_version TEXT,
            metadata     TEXT,
            universe     TEXT,
            period_start TEXT,
            period_end   TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Import classify module under test
# ---------------------------------------------------------------------------

import classify_small_cap as csc


class TestClassifyThresholdLogic(unittest.TestCase):
    """Unit tests for classify_small_cap.classify()."""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def _insert_meta(self, ticker: str, market_cap_gbp):
        self.conn.execute(
            "INSERT INTO tickers_meta (ticker, market_cap_gbp) VALUES (?, ?)",
            (ticker, market_cap_gbp),
        )
        self.conn.commit()

    def _get_small_cap(self, ticker: str):
        row = self.conn.execute(
            "SELECT small_cap FROM tickers_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["small_cap"] if row else None

    # ---- basic threshold ----

    def test_below_threshold_is_small_cap(self):
        self._insert_meta("TINY", 50_000_000)  # 50m -- well below 300m
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("TINY"), 1)

    def test_above_threshold_is_not_small_cap(self):
        self._insert_meta("BIG", 1_000_000_000)  # 1bn
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("BIG"), 0)

    def test_exactly_at_threshold_is_not_small_cap(self):
        """Boundary: at exactly the threshold means NOT small cap (< not <=)."""
        self._insert_meta("AT", 300_000_000)
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("AT"), 0)

    def test_one_pence_below_threshold_is_small_cap(self):
        self._insert_meta("JUST_UNDER", 299_999_999.99)
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("JUST_UNDER"), 1)

    def test_zero_market_cap_is_small_cap(self):
        """Zero market cap is < threshold, so small_cap=1."""
        self._insert_meta("ZERO", 0.0)
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("ZERO"), 1)

    def test_very_large_cap_is_not_small_cap(self):
        self._insert_meta("MEGA", 100_000_000_000)  # 100bn
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("MEGA"), 0)

    # ---- NULL market cap ----

    def test_null_market_cap_leaves_default(self):
        """Tickers with no market cap data: small_cap stays at DEFAULT (0)."""
        self._insert_meta("UNKNOWN", None)
        summary = csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(summary["no_cap_count"], 1)
        # small_cap column should still be the default 0 (not written)
        self.assertEqual(self._get_small_cap("UNKNOWN"), 0)

    # ---- dry run ----

    def test_dry_run_does_not_write(self):
        self._insert_meta("SMALL", 100_000_000)
        summary = csc.classify(self.conn, threshold=300_000_000, dry_run=True)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["small_cap_count"], 1)
        # No write should have happened -- still default
        self.assertEqual(self._get_small_cap("SMALL"), 0)

    # ---- summary counts ----

    def test_summary_counts_correct(self):
        self._insert_meta("S1", 50_000_000)
        self._insert_meta("S2", 200_000_000)
        self._insert_meta("L1", 500_000_000)
        self._insert_meta("L2", 2_000_000_000)
        self._insert_meta("U1", None)
        summary = csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(summary["small_cap_count"], 2)
        self.assertEqual(summary["large_cap_count"], 2)
        self.assertEqual(summary["no_cap_count"], 1)
        self.assertEqual(summary["total"], 5)

    # ---- custom threshold ----

    def test_custom_threshold_100m(self):
        self._insert_meta("A", 80_000_000)   # below 100m -> small
        self._insert_meta("B", 150_000_000)  # above 100m -> not small
        csc.classify(self.conn, threshold=100_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("A"), 1)
        self.assertEqual(self._get_small_cap("B"), 0)

    # ---- idempotency ----

    def test_reclassify_is_idempotent(self):
        """Running classify twice produces the same result."""
        self._insert_meta("X", 50_000_000)
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        csc.classify(self.conn, threshold=300_000_000, dry_run=False)
        self.assertEqual(self._get_small_cap("X"), 1)


class TestBacktestFields(unittest.TestCase):
    """Verify that backtest.py HEADER and _select_firings include the new fields."""

    def test_header_contains_market_cap_gbp(self):
        import backtest
        self.assertIn("market_cap_gbp", backtest.HEADER,
                      "HEADER must include market_cap_gbp")

    def test_header_contains_small_cap(self):
        import backtest
        self.assertIn("small_cap", backtest.HEADER,
                      "HEADER must include small_cap")

    def test_header_order_market_cap_after_is_aim(self):
        """market_cap_gbp and small_cap must appear right after is_aim."""
        import backtest
        h = backtest.HEADER
        aim_idx = h.index("is_aim")
        mc_idx  = h.index("market_cap_gbp")
        sc_idx  = h.index("small_cap")
        bench_idx = h.index("benchmark_symbol")
        # market_cap_gbp and small_cap should be between is_aim and benchmark_symbol
        self.assertGreater(mc_idx, aim_idx)
        self.assertGreater(sc_idx, aim_idx)
        self.assertLess(mc_idx, bench_idx)
        self.assertLess(sc_idx, bench_idx)

    def test_select_firings_query_contains_market_cap_gbp(self):
        """The SQL in _select_firings must reference tm.market_cap_gbp."""
        import inspect
        import backtest
        source = inspect.getsource(backtest._select_firings)
        self.assertIn("market_cap_gbp", source,
                      "_select_firings SQL must select tm.market_cap_gbp")

    def test_select_firings_query_contains_small_cap(self):
        """The SQL in _select_firings must reference small_cap."""
        import inspect
        import backtest
        source = inspect.getsource(backtest._select_firings)
        self.assertIn("small_cap", source,
                      "_select_firings SQL must select tm.small_cap")

    def test_backtest_row_includes_new_fields(self):
        """Integration: run a minimal backtest and verify CSV includes the columns."""
        import backtest
        conn = _make_conn()

        # Seed a ticker in tickers_meta with market cap data.
        conn.execute(
            "INSERT INTO tickers_meta "
            "(ticker, is_aim, benchmark_symbol, market_cap_gbp, small_cap, updated_at) "
            "VALUES (?, 0, '^FTAS', 150000000, 1, '2026-01-01T00:00:00Z')",
            ("TEST",),
        )
        # Seed enough price history (>= 30 rows before announced_at).
        from datetime import date, timedelta
        base = date(2025, 1, 1)
        for i in range(100):
            d = (base + timedelta(days=i)).isoformat()
            for sym in ("TEST", "^FTAS"):
                conn.execute(
                    "INSERT INTO prices (ticker, date, close, fetched_at) "
                    "VALUES (?, ?, ?, '2026-01-01T00:00:00Z')",
                    (sym, d, 100.0 + i * 0.1),
                )
        # announced_at well within history.
        announced = (base + timedelta(days=40)).isoformat()
        conn.execute(
            "INSERT INTO transactions "
            "(fingerprint, date, ticker, company, director, type, "
            " shares, price, value, announced_at, "
            " first_seen, last_seen, seen_count) "
            "VALUES (?, ?, 'TEST', 'TestCo', 'Dir A', 'BUY', "
            "        1000, 1.0, 1000.0, ?, "
            "        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1)",
            ("fp_test_001", announced, announced),
        )
        conn.execute(
            "INSERT INTO signals (signal_id, signal_version, fingerprint, fired_at) "
            "VALUES ('t1', 'v1', 'fp_test_001', ?)",
            (announced,),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '11')"
        )
        conn.commit()

        import tempfile, shutil
        tmp_dir = Path(tempfile.mkdtemp(prefix="dd_test_small_cap_"))
        out_path = tmp_dir / "backtest.csv"
        try:
            backtest.run_backtest(conn, out_path=out_path, run_id="test_run_01")

            # Read the CSV and check the new columns are present and correct.
            with out_path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        finally:
            conn.close()
            shutil.rmtree(str(tmp_dir), ignore_errors=True)

        self.assertGreater(len(rows), 0, "Backtest should emit at least one row")
        row = rows[0]
        self.assertIn("market_cap_gbp", row, "CSV must have market_cap_gbp column")
        self.assertIn("small_cap", row, "CSV must have small_cap column")
        self.assertEqual(float(row["market_cap_gbp"]), 150_000_000.0)
        self.assertEqual(int(row["small_cap"]), 1)


if __name__ == "__main__":
    unittest.main()
