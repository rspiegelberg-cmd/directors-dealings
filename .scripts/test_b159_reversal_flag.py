"""test_b159_reversal_flag.py — unit tests for B-159 net-seller-reversal flag.

Tests:
  1.  Reversal true: prior-12m net seller + BUY -> (1, negative).
  2.  Net-zero and net-buyer -> (0, ...).
  3.  No history -> (0, 0) (first-buy is F1's job, not None).
  4.  Window boundary: SELL at exactly A-365d counted (inclusive lower
      bound); A-366d not counted; boundary computed off A[:10].
  5.  Lookahead guard (critical, P3-6): back-dated future SELL excluded;
      identical-timestamp sibling excluded; one second prior included.
  6.  Type taxonomy: SELL_TAX ignored; EXERCISE/GRANT/SIP never indexed.
  7.  Name noise: case/NBSP variants merge via director_key reuse.
  8.  Cross-key isolation: other ticker / other director.
  9.  announced_at fallback; shares hygiene; unusable inputs.
 10.  Backtest integration: imports, HEADER contract (64 cols, B-161
      pair then B-159 pair then B-155 pair before windows_available),
      source-level BUY gate on run_backtest.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import reversal_flag as rv  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(rows):
    """In-memory DB with a minimal transactions table.

    rows: list of (director, ticker, date, type, shares, announced_at).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE transactions "
        "(fingerprint TEXT, director TEXT, ticker TEXT, date TEXT, "
        " type TEXT, shares, announced_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO transactions "
        "(fingerprint, director, ticker, date, type, shares, announced_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(f"fp{i}", *r) for i, r in enumerate(rows)],
    )
    conn.commit()
    return conn


def _tx(director, ticker, date, tx_type, shares, announced_at=None):
    return (director, ticker, date, tx_type, shares,
            announced_at if announced_at is not None else date)


def _classify(rows, director, ticker, asof):
    conn = _make_conn(rows)
    index = rv.build_trade_history_index(conn)
    conn.close()
    return rv.classify_reversal(index, director, ticker, asof)


# ---------------------------------------------------------------------------
# 1-3: core classification
# ---------------------------------------------------------------------------

class TestReversal(unittest.TestCase):

    def test_net_seller_flags(self):
        rows = [_tx("Jane Doe", "AAA", "2025-12-01", "SELL", 10000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -10000.0)

    def test_net_seller_mixed_trades(self):
        rows = [
            _tx("Jane Doe", "AAA", "2025-09-01", "BUY", 2000),
            _tx("Jane Doe", "AAA", "2025-12-01", "SELL", 5000),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -3000.0)

    def test_net_zero_not_flagged(self):
        rows = [
            _tx("Jane Doe", "AAA", "2025-09-01", "BUY", 10000),
            _tx("Jane Doe", "AAA", "2025-12-01", "SELL", 10000),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_net_buyer_not_flagged(self):
        rows = [
            _tx("Jane Doe", "AAA", "2025-09-01", "BUY", 10000),
            _tx("Jane Doe", "AAA", "2025-12-01", "SELL", 4000),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 6000.0)

    def test_no_history_is_zero_zero(self):
        flag, net = _classify([], "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)


# ---------------------------------------------------------------------------
# 4: window boundary
# ---------------------------------------------------------------------------

class TestWindowBoundary(unittest.TestCase):

    def test_sell_at_exactly_365_days_counted(self):
        # A = 2026-06-01 -> lower bound 2025-06-01 inclusive.
        rows = [_tx("Jane Doe", "AAA", "2025-06-01", "SELL", 5000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -5000.0)

    def test_sell_at_366_days_not_counted(self):
        rows = [_tx("Jane Doe", "AAA", "2025-05-31", "SELL", 5000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_boundary_uses_date_part_of_timestamped_a(self):
        # Timestamped A: lower bound still computed from A[:10].
        rows = [_tx("Jane Doe", "AAA", "2025-06-01", "SELL", 5000)]
        flag, net = _classify(rows, "Jane Doe", "AAA",
                              "2026-06-01T07:00:00Z")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -5000.0)


# ---------------------------------------------------------------------------
# 5: lookahead guard (critical, P3-6)
# ---------------------------------------------------------------------------

class TestLookaheadGuard(unittest.TestCase):

    def test_future_announced_sell_excluded(self):
        """Back-dated trade announced AFTER A must not count."""
        rows = [
            ("Jane Doe", "AAA", "2025-12-01", "SELL", 10000, "2026-07-01"),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_identical_timestamp_sibling_excluded(self):
        rows = [
            ("Jane Doe", "AAA", "2025-12-01", "SELL", 10000,
             "2026-06-01T07:00:00Z"),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA",
                              "2026-06-01T07:00:00Z")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_one_second_prior_included(self):
        rows = [
            ("Jane Doe", "AAA", "2025-12-01", "SELL", 10000,
             "2026-06-01T06:59:59Z"),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA",
                              "2026-06-01T07:00:00Z")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -10000.0)

    def test_date_only_history_on_announcement_day_counts_as_prior(self):
        """Documented mixed-length semantics (matches B-155): a date-only
        history eff on the same day as a timestamped A sorts before it
        lexically, so it counts as prior — visible from start of day."""
        rows = [
            ("Jane Doe", "AAA", "2026-06-01", "SELL", 10000, "2026-06-01"),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA",
                              "2026-06-01T07:00:00Z")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -10000.0)


# ---------------------------------------------------------------------------
# 6: type taxonomy
# ---------------------------------------------------------------------------

class TestTypeTaxonomy(unittest.TestCase):

    def test_sell_tax_alone_does_not_flag(self):
        rows = [_tx("Jane Doe", "AAA", "2025-12-01", "SELL_TAX", 10000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_sell_tax_mixed_only_sell_counts(self):
        rows = [
            _tx("Jane Doe", "AAA", "2025-11-01", "SELL_TAX", 50000),
            _tx("Jane Doe", "AAA", "2025-12-01", "SELL", 3000),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -3000.0)

    def test_exercise_grant_sip_never_indexed(self):
        rows = [
            _tx("Jane Doe", "AAA", "2025-10-01", "EXERCISE", 8000),
            _tx("Jane Doe", "AAA", "2025-11-01", "GRANT", 9000),
            _tx("Jane Doe", "AAA", "2025-12-01", "SIP", 100),
        ]
        conn = _make_conn(rows)
        index = rv.build_trade_history_index(conn)
        conn.close()
        self.assertEqual(index, {})


# ---------------------------------------------------------------------------
# 7-8: name noise + cross-key isolation
# ---------------------------------------------------------------------------

class TestKeying(unittest.TestCase):

    def test_case_variant_history_merges(self):
        rows = [_tx("JANE DOE", "AAA", "2025-12-01", "SELL", 10000)]
        flag, net = _classify(rows, "Jane  Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)

    def test_nbsp_variant_history_merges(self):
        rows = [_tx("Jane\xa0Doe", "AAA", "2025-12-01", "SELL", 10000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)

    def test_joint_pca_stays_distinct(self):
        rows = [_tx("Jane Doe AND John Roe", "AAA", "2025-12-01",
                    "SELL", 10000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_other_ticker_does_not_count(self):
        rows = [_tx("Jane Doe", "BBB", "2025-12-01", "SELL", 10000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)

    def test_other_director_does_not_count(self):
        rows = [_tx("John Smith", "AAA", "2025-12-01", "SELL", 10000)]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)


# ---------------------------------------------------------------------------
# 9: fallbacks + hygiene
# ---------------------------------------------------------------------------

class TestFallbacksAndHygiene(unittest.TestCase):

    def test_empty_announced_at_uses_date(self):
        rows = [
            ("Jane Doe", "AAA", "2025-12-01", "SELL", 10000, ""),
            ("Jane Doe", "AAA", "2025-11-01", "SELL", 2000, None),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -12000.0)

    def test_null_shares_row_skipped(self):
        rows = [
            ("Jane Doe", "AAA", "2025-12-01", "SELL", None, "2025-12-01"),
            _tx("Jane Doe", "AAA", "2025-11-01", "SELL", 2000),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 1)
        self.assertEqual(net, -2000.0)

    def test_unparseable_shares_row_skipped(self):
        rows = [
            ("Jane Doe", "AAA", "2025-12-01", "SELL", "n/a", "2025-12-01"),
        ]
        flag, net = _classify(rows, "Jane Doe", "AAA", "2026-06-01")
        self.assertEqual(flag, 0)
        self.assertEqual(net, 0.0)

    def test_unusable_inputs(self):
        self.assertEqual(_classify([], "", "AAA", "2026-01-01"),
                         (None, None))
        self.assertEqual(_classify([], "Jane", "", "2026-01-01"),
                         (None, None))
        self.assertEqual(_classify([], "Jane", "AAA", ""),
                         (None, None))

    def test_malformed_asof_returns_none(self):
        self.assertEqual(_classify([], "Jane", "AAA", "not-a-date"),
                         (None, None))


# ---------------------------------------------------------------------------
# 10: backtest integration + HEADER contract
# ---------------------------------------------------------------------------

class TestBacktestIntegration(unittest.TestCase):

    def test_backtest_imports_classifier(self):
        import backtest as bt
        self.assertIsNotNone(bt.build_trade_history_index)
        self.assertIsNotNone(bt.classify_reversal)

    def test_header_columns_present_and_positioned(self):
        import backtest as bt
        # B-161 (Sprint 63) added its pair directly before
        # windows_available, shifting the B-159 pair back by 2.
        idx_wa = bt.HEADER.index("windows_available")
        self.assertEqual(bt.HEADER[idx_wa - 1], "days_since_results")
        self.assertEqual(bt.HEADER[idx_wa - 2], "post_results_flag")
        self.assertEqual(bt.HEADER[idx_wa - 3], "net_shares_prior_12m")
        self.assertEqual(bt.HEADER[idx_wa - 4], "seller_reversal_flag")
        self.assertEqual(bt.HEADER[idx_wa - 5], "routine_prior_buy_years")
        self.assertEqual(bt.HEADER[idx_wa - 6], "routine_flag")

    def test_header_length_64(self):
        import backtest as bt
        # 60 (post-B-155) + seller_reversal_flag + net_shares_prior_12m
        # (B-159) = 62; + post_results_flag + days_since_results
        # (B-161) = 64.
        self.assertEqual(len(bt.HEADER), 64)

    def test_run_backtest_gates_on_buy(self):
        """The classify_reversal call must be gated on tx_type == "BUY"
        so non-BUY firings emit empty cells (source-level check)."""
        import inspect
        import backtest as bt
        src = inspect.getsource(bt.run_backtest)
        call_pos = src.index("classify_reversal(")
        gate_window = src[max(0, call_pos - 400):call_pos]
        self.assertIn('tx_type == "BUY"', gate_window)
        post = src[call_pos:call_pos + 400]
        self.assertIn("reversal_flag_val, net_shares_prior = None, None",
                      post)


if __name__ == "__main__":
    unittest.main()
