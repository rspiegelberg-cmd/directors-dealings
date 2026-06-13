"""Sprint 10 Phase 5 tests — backfill_prices + backfill_benchmarks resilience.

Verifies two new behaviours introduced in Phase 5:

  5.A — Per-ticker MAX(date)==today skip. Tickers whose latest price
        row is already dated today are skipped before any Yahoo call.

  5.B — Abort-on-systemic-429. After CONSECUTIVE_429_ABORT_THRESHOLD
        (5) consecutive 429 failures, the backfill aborts cleanly,
        marks summary["aborted_after_429"] = True, and writes the
        skipped tickers to .data/_price_skipped_due_to_rate_limit.json
        (or the benchmark-specific equivalent).

WHY THIS MATTERS
----------------
On 2026-05-25 the live pipeline hung for 33+ minutes on the prices
step because Yahoo Finance was 429-ing ~50% of requests and the
in-script retry-with-backoff (30/60/120s × 3 retries) compounded into
~4.5 min per ticker × 563 tickers = many hours. The 20-min subprocess
timeout in refresh_all.py failed to fire on Windows. Phase 5.B bounds
the damage at script level: bail after N=5 consecutive 429s, let the
pipeline continue past prices into the remaining steps.

RUNNING (Windows, per CLAUDE.md):
    python -m unittest .scripts.test_backfill_resilience
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _seed_prices(db_path: Path, rows):
    """Seed the prices table with (ticker, date_iso) rows.

    Uses db.connect() so schema always matches production migration chain.
    """
    import db as db_mod
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            now = db_mod.iso_now()
            for ticker, date_iso in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO prices "
                    "(ticker, date, close, volume, source, fetched_at) "
                    "VALUES (?, ?, 100.0, 1000, 'test', ?)",
                    (ticker, date_iso, now),
                )
            conn.commit()
        finally:
            conn.close()


def _seed_tickers_meta_with_benchmarks(db_path: Path, symbols):
    """Seed tickers_meta so distinct_benchmark_symbols returns the given list."""
    import db as db_mod
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            now = db_mod.iso_now()
            for idx, sym in enumerate(symbols):
                # Use a synthetic ticker per symbol so each tickers_meta row
                # references one benchmark.
                conn.execute(
                    "INSERT INTO tickers_meta(ticker, benchmark_symbol, "
                    "classified_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (f"TST{idx}", sym, now, now),
                )
            conn.commit()
        finally:
            conn.close()


def _seed_transactions(db_path: Path, tickers):
    """Seed minimal transactions rows so distinct_stock_tickers returns them."""
    import db as db_mod
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            now = db_mod.iso_now()
            for tkr in tickers:
                conn.execute(
                    "INSERT INTO transactions("
                    "fingerprint, first_seen, last_seen, seen_count, date, "
                    "ticker, company, director, role, type, shares, price, "
                    "value, context, url, announced_at, cluster_id, "
                    "first_time_buy, parser_source"
                    ") VALUES (?, ?, ?, 1, '2026-01-01', ?, 'TestCo', "
                    "'Joe', 'CEO', 'BUY', 1, 1.0, 1.0, NULL, ?, "
                    "NULL, NULL, 0, 'regex')",
                    (f"fp-{tkr}", now, now, tkr, f"https://x/{tkr}"),
                )
            conn.commit()
        finally:
            conn.close()


def _make_fetch_result(status="ok", detail="", rows=None):
    """Build a FetchResult namedtuple for mocking fetch_prices.fetch."""
    import fetch_prices
    return fetch_prices.FetchResult(
        status=status,
        rows=rows or [],
        currency="GBP",
        yahoo_symbol="TST.L",
        cache_hit=False,
        network_calls=1,
        detail=detail,
    )


def _http_429_result():
    return _make_fetch_result(
        status="error",
        detail="HTTPError: HTTP Error 429: Too Many Requests",
    )


class TestIsRateLimitError(unittest.TestCase):
    """Unit test for the helper that detects 429 results."""

    def test_429_in_detail_returns_true(self):
        import backfill_prices
        r = _http_429_result()
        self.assertTrue(backfill_prices._is_rate_limit_error(r))

    def test_ok_status_returns_false(self):
        import backfill_prices
        r = _make_fetch_result(status="ok")
        self.assertFalse(backfill_prices._is_rate_limit_error(r))

    def test_non_429_error_returns_false(self):
        import backfill_prices
        r = _make_fetch_result(status="error", detail="URLError: timed out")
        self.assertFalse(backfill_prices._is_rate_limit_error(r))

    def test_none_result_returns_false(self):
        import backfill_prices
        self.assertFalse(backfill_prices._is_rate_limit_error(None))


class TestPhase5ASkipToday(unittest.TestCase):
    """5.A — tickers with today's data are skipped before any fetch call."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ticker_with_todays_data_is_skipped(self):
        today_iso = date.today().isoformat()
        _seed_transactions(self.db_path, ["TST"])
        _seed_prices(self.db_path, [("TST", today_iso)])

        import db as db_mod
        import backfill_prices

        # Mock fetch_prices.fetch to record any calls (there should be none).
        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(backfill_prices.fetch_prices, "fetch") as mock_fetch:
            summary = backfill_prices.run(
                date_from="2025-01-01", date_to=today_iso,
                rate_limit=0.0, dry_run=True,
            )

        self.assertEqual(summary["skipped_today"], 1)
        mock_fetch.assert_not_called()

    def test_ticker_without_todays_data_is_fetched(self):
        today_iso = date.today().isoformat()
        _seed_transactions(self.db_path, ["TST"])
        # Seed with yesterday's data — fetch should be attempted.
        _seed_prices(self.db_path, [("TST", "2026-01-01")])

        import db as db_mod
        import backfill_prices

        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(backfill_prices.fetch_prices, "fetch",
                               return_value=_make_fetch_result()) as mock_fetch:
            summary = backfill_prices.run(
                date_from="2025-01-01", date_to=today_iso,
                rate_limit=0.0, dry_run=True,
            )

        self.assertEqual(summary["skipped_today"], 0)
        self.assertEqual(mock_fetch.call_count, 1)


class TestPhase5BAbortOn429(unittest.TestCase):
    """5.B — abort cleanly after N consecutive 429 failures."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.skip_log = Path(self._tmpdir.name) / "_skipped.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_aborts_after_5_consecutive_429s(self):
        today_iso = date.today().isoformat()
        # 10 tickers, none current — all will attempt fetch.
        _seed_transactions(self.db_path,
                           ["T01", "T02", "T03", "T04", "T05",
                            "T06", "T07", "T08", "T09", "T10"])

        import db as db_mod
        import backfill_prices

        # Every fetch returns 429.
        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(backfill_prices.fetch_prices, "fetch",
                               return_value=_http_429_result()) as mock_fetch, \
             mock.patch.object(backfill_prices, "RATE_LIMIT_SKIP_PATH",
                               self.skip_log):
            summary = backfill_prices.run(
                date_from="2025-01-01", date_to=today_iso,
                rate_limit=0.0, dry_run=True,
            )

        self.assertTrue(summary["aborted_after_429"])
        self.assertEqual(summary["rate_limit_429s"], 5)
        # Fetch was called exactly 5 times before abort.
        self.assertEqual(mock_fetch.call_count, 5)
        # Skipped count = the 5 remaining tickers we didn't reach.
        self.assertEqual(summary["abort_skipped_count"], 5)
        # Log file written with the skipped + remaining tickers (10 total).
        self.assertTrue(self.skip_log.exists())
        payload = json.loads(self.skip_log.read_text())
        self.assertEqual(payload["n_skipped"], 10)

    def test_counter_resets_on_successful_fetch(self):
        today_iso = date.today().isoformat()
        _seed_transactions(self.db_path,
                           ["T01", "T02", "T03", "T04", "T05",
                            "T06", "T07", "T08", "T09", "T10"])

        import db as db_mod
        import backfill_prices

        # Sequence: 4 × 429, 1 × ok, 4 × 429, 1 × ok — never 5 in a row.
        # Should NOT abort.
        results = [
            _http_429_result(), _http_429_result(), _http_429_result(),
            _http_429_result(), _make_fetch_result(status="ok"),
            _http_429_result(), _http_429_result(), _http_429_result(),
            _http_429_result(), _make_fetch_result(status="ok"),
        ]

        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(backfill_prices.fetch_prices, "fetch",
                               side_effect=results), \
             mock.patch.object(backfill_prices, "RATE_LIMIT_SKIP_PATH",
                               self.skip_log):
            summary = backfill_prices.run(
                date_from="2025-01-01", date_to=today_iso,
                rate_limit=0.0, dry_run=True,
            )

        self.assertFalse(summary["aborted_after_429"])
        self.assertEqual(summary["rate_limit_429s"], 8)
        # No abort means no log file (no skipped tickers).
        self.assertFalse(self.skip_log.exists())

    def test_counter_resets_on_today_skip(self):
        """5.A skip resets the 429 counter."""
        today_iso = date.today().isoformat()
        _seed_transactions(self.db_path,
                           ["T01", "T02", "T03", "T04", "T05"])
        # T03 is "already current today" — 5.A will skip it without a fetch.
        _seed_prices(self.db_path, [("T03", today_iso)])

        import db as db_mod
        import backfill_prices

        # All fetches (T01, T02, T04, T05) return 429. T03 is 5.A-skipped.
        # Sequence of fetch calls: T01-429, T02-429, [T03 skipped], T04-429, T05-429.
        # That's 4 consecutive 429s — below threshold. No abort.
        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(backfill_prices.fetch_prices, "fetch",
                               return_value=_http_429_result()) as mock_fetch, \
             mock.patch.object(backfill_prices, "RATE_LIMIT_SKIP_PATH",
                               self.skip_log):
            summary = backfill_prices.run(
                date_from="2025-01-01", date_to=today_iso,
                rate_limit=0.0, dry_run=True,
            )

        self.assertFalse(summary["aborted_after_429"])
        self.assertEqual(summary["skipped_today"], 1)
        self.assertEqual(mock_fetch.call_count, 4)


class TestPhase5BBenchmarks(unittest.TestCase):
    """5.A + 5.B applied to backfill_benchmarks."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.skip_log = Path(self._tmpdir.name) / "_benchmark_skipped.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_benchmark_aborts_after_5_consecutive_429s(self):
        today_iso = date.today().isoformat()
        symbols = ["^A", "^B", "^C", "^D", "^E", "^F", "^G"]
        _seed_tickers_meta_with_benchmarks(self.db_path, symbols)

        import db as db_mod
        import backfill_benchmarks

        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(backfill_benchmarks.fetch_prices, "fetch",
                               return_value=_http_429_result()) as mock_fetch, \
             mock.patch.object(backfill_benchmarks, "RATE_LIMIT_SKIP_PATH",
                               self.skip_log):
            summary = backfill_benchmarks.run(
                date_from="2025-01-01", date_to=today_iso,
                rate_limit=0.0, dry_run=True,
            )

        self.assertTrue(summary["aborted_after_429"])
        self.assertEqual(summary["rate_limit_429s"], 5)
        self.assertEqual(mock_fetch.call_count, 5)
        # Log file uses the benchmark-specific path; entries are
        # called "skipped_symbols" not "skipped_tickers".
        self.assertTrue(self.skip_log.exists())
        payload = json.loads(self.skip_log.read_text())
        self.assertIn("skipped_symbols", payload)


if __name__ == "__main__":
    unittest.main()
