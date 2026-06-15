"""test_b161_results_window_flag.py — unit tests for B-161 post-results flag.

Tests:
  1.  Index build: confirmed-only (est excluded), DISTINCT dedup, sorted,
      mixed sources included, NULL ticker/date rows skipped.
  2.  Flag true: results 5 days before buy -> (1, 5).
  3.  Window boundary: gap 14 -> (1, 14); gap 15 -> (0, 15).
  4.  Same-day buy: report_date == A[:10] -> (1, 0) (MAR window opens at
      the results announcement; day 0 is in-window).
  5.  Lookahead (critical, P3-6): future-only confirmed date -> (None,
      None); future + old prior -> matches the prior only; est row
      inside the window never flags.
  6.  Multiple prior dates: most recent wins; both outside -> (0, gap).
  7.  No coverage -> (None, None); outside-window vs no-coverage
      disambiguation asserted explicitly.
  8.  Timestamped A compares on [:10]; malformed inputs -> (None, None);
      cross-ticker isolation; both-None-or-both-populated invariant.
  9.  Backtest integration: imports, HEADER contract (64 cols, B-161
      pair at idx_wa-1/-2), source-level BUY gate (no tx_director
      requirement), missing-table guard analogue.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import results_window_flag as rw  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(rows):
    """In-memory DB with a migration-009-shaped reporting_dates table.

    rows: list of (ticker, report_date, report_type, source, confidence).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE reporting_dates "
        "(ticker TEXT, report_date TEXT, report_type TEXT, source TEXT, "
        " fetched_at TEXT, confidence TEXT, source_url TEXT)"
    )
    conn.executemany(
        "INSERT INTO reporting_dates "
        "(ticker, report_date, report_type, source, fetched_at, "
        " confidence, source_url) "
        "VALUES (?, ?, ?, ?, '2026-06-08T00:00:00Z', ?, '')",
        rows,
    )
    conn.commit()
    return conn


def _rd(ticker, report_date, report_type="INTERIM", source="investegate",
        confidence="confirmed"):
    return (ticker, report_date, report_type, source, confidence)


def _classify(rows, ticker, asof):
    conn = _make_conn(rows)
    index = rw.build_results_date_index(conn)
    conn.close()
    return rw.classify_post_results(index, ticker, asof)


# ---------------------------------------------------------------------------
# 1: index build
# ---------------------------------------------------------------------------

class TestIndexBuild(unittest.TestCase):

    def test_est_rows_excluded(self):
        conn = _make_conn([
            _rd("AAA", "2026-03-01", confidence="confirmed"),
            _rd("AAA", "2026-04-01", report_type="EARNINGS",
                source="expected", confidence="est"),
        ])
        index = rw.build_results_date_index(conn)
        conn.close()
        self.assertEqual(index, {"AAA": ["2026-03-01"]})

    def test_distinct_dedup_same_date_two_types(self):
        conn = _make_conn([
            _rd("AAA", "2026-03-01", report_type="INTERIM"),
            _rd("AAA", "2026-03-01", report_type="TRADING_STMT"),
        ])
        index = rw.build_results_date_index(conn)
        conn.close()
        self.assertEqual(index["AAA"], ["2026-03-01"])

    def test_sorted_and_mixed_sources(self):
        conn = _make_conn([
            _rd("AAA", "2026-03-01", source="lse_diary"),
            _rd("AAA", "2025-09-15", source="investegate"),
        ])
        index = rw.build_results_date_index(conn)
        conn.close()
        self.assertEqual(index["AAA"], ["2025-09-15", "2026-03-01"])

    def test_null_ticker_or_date_skipped(self):
        conn = _make_conn([
            (None, "2026-03-01", "INTERIM", "investegate", "confirmed"),
            ("AAA", None, "INTERIM", "investegate", "confirmed"),
        ])
        index = rw.build_results_date_index(conn)
        conn.close()
        self.assertEqual(index, {})

    def test_timestamped_report_date_truncated_at_build(self):
        """QA hardening: a (theoretical) timestamped report_date is
        truncated to its date component so same-day inclusion holds."""
        conn = _make_conn([
            _rd("AAA", "2026-03-10T00:00:00Z"),
        ])
        index = rw.build_results_date_index(conn)
        conn.close()
        self.assertEqual(index["AAA"], ["2026-03-10"])
        self.assertEqual(
            rw.classify_post_results(index, "AAA", "2026-03-10"), (1, 0))

    def test_malformed_report_date_dropped_at_build(self):
        """QA hardening: garbage dates are dropped at build time so they
        can never mask an earlier valid date."""
        conn = _make_conn([
            _rd("AAA", "zzzz-bogus"),
            _rd("AAA", "2026-03-05"),
        ])
        index = rw.build_results_date_index(conn)
        conn.close()
        self.assertEqual(index["AAA"], ["2026-03-05"])


# ---------------------------------------------------------------------------
# 2-4: core classification + boundary
# ---------------------------------------------------------------------------

class TestClassification(unittest.TestCase):

    def test_results_five_days_before(self):
        rows = [_rd("AAA", "2026-03-05")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"), (1, 5))

    def test_boundary_fourteen_in(self):
        rows = [_rd("AAA", "2026-03-01")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-15"), (1, 14))

    def test_boundary_fifteen_out(self):
        rows = [_rd("AAA", "2026-03-01")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-16"), (0, 15))

    def test_same_day_buy_is_in_window(self):
        """Day 0: MAR window opens at the 07:00 results announcement;
        a same-day PDMR buy is the canonical first-window trade."""
        rows = [_rd("AAA", "2026-03-10")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"), (1, 0))


# ---------------------------------------------------------------------------
# 5: lookahead guards (critical, P3-6)
# ---------------------------------------------------------------------------

class TestLookaheadGuards(unittest.TestCase):

    def test_future_only_date_is_no_coverage(self):
        rows = [_rd("AAA", "2026-09-01")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"),
                         (None, None))

    def test_future_date_ignored_prior_matched(self):
        rows = [
            _rd("AAA", "2025-09-15"),
            _rd("AAA", "2026-09-01"),  # scheduled, future
        ]
        flag, days = _classify(rows, "AAA", "2026-03-10")
        self.assertEqual(flag, 0)
        self.assertEqual(days, 176)  # gap to 2025-09-15, not the future one

    def test_est_inside_window_never_flags(self):
        rows = [
            _rd("AAA", "2026-03-08", report_type="EARNINGS",
                source="expected", confidence="est"),
        ]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"),
                         (None, None))


# ---------------------------------------------------------------------------
# 6-7: multiple dates, coverage states
# ---------------------------------------------------------------------------

class TestCoverageStates(unittest.TestCase):

    def test_most_recent_prior_wins(self):
        rows = [
            _rd("AAA", "2025-08-20"),
            _rd("AAA", "2026-03-04"),
        ]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"), (1, 6))

    def test_both_prior_outside_window(self):
        rows = [
            _rd("AAA", "2025-08-20"),
            _rd("AAA", "2026-01-22"),
        ]
        flag, days = _classify(rows, "AAA", "2026-03-10")
        self.assertEqual(flag, 0)
        self.assertEqual(days, 47)

    def test_no_coverage_is_both_none(self):
        self.assertEqual(_classify([], "AAA", "2026-03-10"), (None, None))

    def test_outside_window_disambiguated_from_no_coverage(self):
        """flag=0 with populated days = covered-but-outside;
        (None, None) = no data. Never (0, None) or (None, days)."""
        covered = _classify([_rd("AAA", "2026-01-22")], "AAA", "2026-03-10")
        uncovered = _classify([], "AAA", "2026-03-10")
        self.assertEqual(covered, (0, 47))
        self.assertEqual(uncovered, (None, None))


# ---------------------------------------------------------------------------
# 8: input handling + isolation + invariant
# ---------------------------------------------------------------------------

class TestInputsAndInvariants(unittest.TestCase):

    def test_timestamped_a_compares_on_date_part(self):
        rows = [_rd("AAA", "2026-03-10")]
        self.assertEqual(
            _classify(rows, "AAA", "2026-03-10T07:00:12Z"), (1, 0))

    def test_unusable_inputs(self):
        rows = [_rd("AAA", "2026-03-05")]
        self.assertEqual(_classify(rows, "", "2026-03-10"), (None, None))
        self.assertEqual(_classify(rows, None, "2026-03-10"), (None, None))
        self.assertEqual(_classify(rows, "AAA", ""), (None, None))
        self.assertEqual(_classify(rows, "AAA", "not-a-date"),
                         (None, None))

    def test_malformed_report_date_safe(self):
        rows = [("AAA", "bogus-date", "INTERIM", "investegate",
                 "confirmed")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"),
                         (None, None))

    def test_cross_ticker_isolation(self):
        rows = [_rd("BBB", "2026-03-05")]
        self.assertEqual(_classify(rows, "AAA", "2026-03-10"),
                         (None, None))

    def test_both_none_or_both_populated_invariant(self):
        cases = [
            ([], "AAA", "2026-03-10"),
            ([_rd("AAA", "2026-03-05")], "AAA", "2026-03-10"),
            ([_rd("AAA", "2026-01-01")], "AAA", "2026-03-10"),
            ([_rd("AAA", "2026-09-01")], "AAA", "2026-03-10"),
        ]
        for rows, ticker, asof in cases:
            flag, days = _classify(rows, ticker, asof)
            self.assertEqual(flag is None, days is None)
            if flag is not None:
                self.assertEqual(flag,
                                 1 if days <= rw.WINDOW_CALENDAR_DAYS
                                 else 0)
                self.assertGreaterEqual(days, 0)


# ---------------------------------------------------------------------------
# 9: backtest integration + HEADER contract
# ---------------------------------------------------------------------------

class TestBacktestIntegration(unittest.TestCase):

    def test_backtest_imports_classifier(self):
        import backtest as bt
        self.assertIsNotNone(bt.build_results_date_index)
        self.assertIsNotNone(bt.classify_post_results)

    def test_header_columns_present_and_positioned(self):
        import backtest as bt
        idx_wa = bt.HEADER.index("windows_available")
        self.assertEqual(bt.HEADER[idx_wa - 1], "days_since_results")
        self.assertEqual(bt.HEADER[idx_wa - 2], "post_results_flag")
        self.assertEqual(bt.HEADER[idx_wa - 3], "net_shares_prior_12m")
        self.assertEqual(bt.HEADER[idx_wa - 4], "seller_reversal_flag")

    def test_header_length_64(self):
        import backtest as bt
        # 62 (post-B-159) + post_results_flag + days_since_results
        # (B-161) = 64; + 7 B-168 salary-multiple cols = 71.
        self.assertEqual(len(bt.HEADER), 71)

    def test_run_backtest_gates_on_buy_without_director(self):
        """classify_post_results must be gated on tx_type == "BUY" but
        must NOT require tx_director (ticker-level lookup). Source-level
        check; the else branch must null both fields."""
        import inspect
        import backtest as bt
        src = inspect.getsource(bt.run_backtest)
        call_pos = src.index("classify_post_results(")
        # 300-char window covers the B-161 gate but stays clear of the
        # adjacent B-159 block (which legitimately uses tx_director).
        gate_window = src[max(0, call_pos - 300):call_pos]
        self.assertIn('tx_type == "BUY"', gate_window)
        self.assertNotIn("tx_director", gate_window)
        post = src[call_pos:call_pos + 400]
        self.assertIn("post_results_val, days_since_results = None, None",
                      post)

    def test_missing_table_guard_in_source(self):
        """run_backtest must guard the index build on sqlite_master
        showing reporting_dates (B-164 has_short_data analogue), so old
        fixtures without the table still run with empty cells."""
        import inspect
        import backtest as bt
        src = inspect.getsource(bt.run_backtest)
        build_pos = src.index("build_results_date_index(conn)")
        guard_window = src[max(0, build_pos - 500):build_pos]
        self.assertIn("reporting_dates", guard_window)
        self.assertIn("sqlite_master", guard_window)


if __name__ == "__main__":
    unittest.main()
