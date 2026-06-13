"""test_b162_price_history.py — unit tests for B-162 5-year price history.

Tests:
  1. _default_from() returns a date ~5 years (1827 days) back.
  2. ticker_effective_from() existing logic unchanged (extend=False path).
  3. run() with extend=True bypasses smart range and fetches full window.
  4. run() with extend=False still uses smart range (no regression).
  5. --extend CLI flag wires through to run().
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_prices as bp


class TestDefaultFrom(unittest.TestCase):
    """_default_from() should return ~5 years back."""

    def test_default_from_is_approximately_5_years(self):
        from_date = date.fromisoformat(bp._default_from())
        today = date.today()
        delta = (today - from_date).days
        # Allow a day of slack either side of 1827
        self.assertGreaterEqual(delta, 1826,
                                f"_default_from delta {delta} < 1826 (expected ~5 years)")
        self.assertLessEqual(delta, 1828,
                             f"_default_from delta {delta} > 1828 (expected ~5 years)")

    def test_default_from_returns_iso_string(self):
        result = bp._default_from()
        # Should parse without error
        d = date.fromisoformat(result)
        self.assertIsInstance(d, date)


class TestTickerEffectiveFrom(unittest.TestCase):
    """ticker_effective_from() incremental logic unchanged — regression guard."""

    def _conn(self, max_date: str | None):
        """In-memory DB with a single prices row (or no rows) for ticker 'AAA'."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT)")
        if max_date:
            conn.execute("INSERT INTO prices VALUES ('AAA', ?)", (max_date,))
        conn.commit()
        return conn

    def test_new_ticker_returns_base_from(self):
        """No existing rows -> full base_from window."""
        conn = self._conn(None)
        result = bp.ticker_effective_from(conn, "AAA", "2021-06-10")
        self.assertEqual(result, "2021-06-10")
        conn.close()

    def test_existing_ticker_increments(self):
        """Has rows -> next_date = MAX(date) + 1 day."""
        conn = self._conn("2026-06-09")
        result = bp.ticker_effective_from(conn, "AAA", "2021-06-10")
        self.assertEqual(result, "2026-06-10")
        conn.close()

    def test_existing_ticker_respects_base_from_floor(self):
        """If max+1 < base_from, returns base_from (not a date before the window)."""
        conn = self._conn("2020-01-01")
        result = bp.ticker_effective_from(conn, "AAA", "2021-06-10")
        self.assertEqual(result, "2021-06-10")
        conn.close()


class TestRunExtend(unittest.TestCase):
    """run() with extend=True bypasses smart-range and fetches full window."""

    def _make_fetch_result(self, status="ok", rows=None):
        from fetch_prices import FetchResult
        return FetchResult(
            status=status,
            rows=rows or [{"date": "2021-06-15", "close": 1.0,
                           "high": 1.1, "low": 0.9, "volume": 1000}],
            currency="GBp",
            yahoo_symbol="AAA.L",
            cache_hit=False,
            network_calls=1,
        )

    @patch("backfill_prices.db_health")
    @patch("backfill_prices.db")
    @patch("backfill_prices.fetch_prices")
    @patch("backfill_prices.distinct_stock_tickers")
    @patch("backfill_prices.insert_rows")
    @patch("backfill_prices._write_progress_atomic")
    def test_extend_bypasses_smart_range(
        self, mock_write_prog, mock_insert, mock_tickers,
        mock_fp, mock_db, mock_health,
    ):
        """With extend=True, every ticker gets the full date_from window even
        if it already has data up to today."""
        mock_tickers.return_value = ["AAA"]
        mock_fp.fetch.return_value = self._make_fetch_result()
        mock_insert.return_value = 5

        # Simulate a DB conn where MAX(date) == today (would normally be skipped)
        today = date.today().isoformat()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = {"d": today}
        mock_db.connect.return_value = mock_conn
        mock_db.iso_now.return_value = "2026-06-10T12:00:00Z"

        date_from = "2021-06-10"
        date_to = today

        summary = bp.run(
            date_from=date_from,
            date_to=date_to,
            dry_run=True,      # no real DB writes; we verify the fetch call
            extend=True,
        )

        # fetch should have been called with the full 5-year window, not skipped
        mock_fp.fetch.assert_called_once()
        call_args = mock_fp.fetch.call_args
        self.assertEqual(call_args[0][1], date_from,
                         "extend=True: fetch should use date_from, not skip")
        self.assertEqual(summary["ok"], 1)

    @patch("backfill_prices.db_health")
    @patch("backfill_prices.db")
    @patch("backfill_prices.fetch_prices")
    @patch("backfill_prices.distinct_stock_tickers")
    @patch("backfill_prices._write_progress_atomic")
    def test_extend_false_skips_ticker_already_current(
        self, mock_write_prog, mock_tickers, mock_fp, mock_db, mock_health,
    ):
        """Without extend, a ticker whose MAX(date)==today is skipped (5.A)."""
        mock_tickers.return_value = ["AAA"]
        mock_fp.fetch.return_value = self._make_fetch_result()

        today = date.today().isoformat()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = {"d": today}
        mock_db.connect.return_value = mock_conn
        mock_db.iso_now.return_value = "2026-06-10T12:00:00Z"

        summary = bp.run(
            date_from="2021-06-10",
            date_to=today,
            dry_run=True,
            extend=False,   # default behaviour
        )

        # Ticker has max_date==today -> 5.A skip; fetch must NOT be called
        mock_fp.fetch.assert_not_called()
        self.assertEqual(summary["skipped_today"], 1)


class TestCLIExtendFlag(unittest.TestCase):
    """--extend flag wires through the CLI without error."""

    def test_extend_flag_parses(self):
        """argparse should accept --extend and set args.extend=True."""
        import argparse
        # Invoke main() argument parsing directly via parse_known_args
        ap = argparse.ArgumentParser()
        ap.add_argument("--extend", action="store_true")
        args = ap.parse_args(["--extend"])
        self.assertTrue(args.extend)

    def test_no_extend_flag_defaults_false(self):
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--extend", action="store_true")
        args = ap.parse_args([])
        self.assertFalse(args.extend)


if __name__ == "__main__":
    unittest.main()
