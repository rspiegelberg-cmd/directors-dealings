"""test_b155_routine_flag.py — unit tests for B-155 routine vs opportunistic flag.

Tests:
  1.  director_key: case collapse, NBSP collapse, multi-space collapse,
      idempotence, empty/None round-trip.
  2.  routine: same month in 3 prior years; majority variant (2 of 3).
  3.  opportunistic: prior years with no common month; the max(2, ...) floor.
  4.  insufficient_history: zero prior buys; one prior year only.
  5.  Lookahead guard (critical, P3-6): a future buy that would flip the
      result is excluded; identical-timestamp siblings are excluded.
  6.  announced_at fallback: empty announced_at uses date for visibility.
  7.  Cross-key isolation: same director, different ticker.
  8.  Backtest integration: classifier imported; the BUY-only gate and
      the strictly-prior classify call are present in run_backtest
      source (source-level check, same pattern as the B-164 P3-6 test).
  9.  HEADER contract: columns present, positions, length 64.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import routine_flag as rf  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(rows):
    """In-memory DB with a minimal transactions table.

    rows: list of (director, ticker, date, type, announced_at).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE transactions "
        "(fingerprint TEXT, director TEXT, ticker TEXT, date TEXT, "
        " type TEXT, announced_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO transactions "
        "(fingerprint, director, ticker, date, type, announced_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(f"fp{i}", *r) for i, r in enumerate(rows)],
    )
    conn.commit()
    return conn


def _buy(director, ticker, date, announced_at=None):
    return (director, ticker, date, "BUY", announced_at or date)


def _classify(rows, director, ticker, asof):
    conn = _make_conn(rows)
    index = rf.build_buy_history_index(conn)
    conn.close()
    return rf.classify_routine(index, director, ticker, asof)


# ---------------------------------------------------------------------------
# 1. director_key
# ---------------------------------------------------------------------------

class TestDirectorKey(unittest.TestCase):

    def test_case_collapse(self):
        self.assertEqual(rf.director_key("MURRAY MCGOWAN"),
                         rf.director_key("Murray McGowan"))

    def test_nbsp_collapse(self):
        self.assertEqual(rf.director_key("Serpil\xa0Timuray"),
                         rf.director_key("Serpil Timuray"))

    def test_multi_space_collapse(self):
        self.assertEqual(rf.director_key("John   Smith"),
                         rf.director_key(" John Smith "))

    def test_idempotent(self):
        k = rf.director_key("Jane  Doe")
        self.assertEqual(rf.director_key(k), k)

    def test_empty_and_none(self):
        self.assertEqual(rf.director_key(""), "")
        self.assertEqual(rf.director_key(None), "")

    def test_joint_pca_stays_distinct(self):
        a = rf.director_key("Leslie Van De Walle")
        b = rf.director_key("LESLIE VAN DE WALLE AND Domitille van de walle")
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# 2. routine
# ---------------------------------------------------------------------------

class TestRoutine(unittest.TestCase):

    def test_same_month_three_years(self):
        rows = [
            _buy("Jane Doe", "AAA", "2023-03-10"),
            _buy("Jane Doe", "AAA", "2024-03-12"),
            _buy("Jane Doe", "AAA", "2025-03-11"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-03-15")
        self.assertEqual(flag, rf.ROUTINE)
        self.assertEqual(n, 3)

    def test_majority_two_of_three(self):
        rows = [
            _buy("Jane Doe", "AAA", "2023-03-10"),
            _buy("Jane Doe", "AAA", "2024-03-12"),
            _buy("Jane Doe", "AAA", "2025-06-11"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15")
        self.assertEqual(flag, rf.ROUTINE)
        self.assertEqual(n, 3)

    def test_two_of_two(self):
        rows = [
            _buy("Jane Doe", "AAA", "2024-09-01"),
            _buy("Jane Doe", "AAA", "2025-09-03"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-02-01")
        self.assertEqual(flag, rf.ROUTINE)
        self.assertEqual(n, 2)

    def test_case_variant_history_merges(self):
        """History under a case-variant name still counts (key collapse)."""
        rows = [
            _buy("MURRAY MCGOWAN", "AAA", "2024-05-01"),
            _buy("Murray McGowan", "AAA", "2025-05-02"),
        ]
        flag, n = _classify(rows, "Murray  McGowan", "AAA", "2026-01-01")
        self.assertEqual(flag, rf.ROUTINE)
        self.assertEqual(n, 2)


# ---------------------------------------------------------------------------
# 3. opportunistic
# ---------------------------------------------------------------------------

class TestOpportunistic(unittest.TestCase):

    def test_no_common_month(self):
        rows = [
            _buy("Jane Doe", "AAA", "2024-01-10"),
            _buy("Jane Doe", "AAA", "2025-07-12"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-02-01")
        self.assertEqual(flag, rf.OPPORTUNISTIC)
        self.assertEqual(n, 2)

    def test_floor_needs_two_years_same_month(self):
        """2 prior years, the shared-month count is 1 -> opportunistic
        (the max(2, ...) floor: a single year's month is never routine)."""
        rows = [
            _buy("Jane Doe", "AAA", "2024-04-10"),
            _buy("Jane Doe", "AAA", "2024-04-20"),  # same month, SAME year
            _buy("Jane Doe", "AAA", "2025-08-12"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-02-01")
        self.assertEqual(flag, rf.OPPORTUNISTIC)
        self.assertEqual(n, 2)


# ---------------------------------------------------------------------------
# 4. insufficient_history
# ---------------------------------------------------------------------------

class TestInsufficientHistory(unittest.TestCase):

    def test_zero_prior(self):
        flag, n = _classify([], "Jane Doe", "AAA", "2026-02-01")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 0)

    def test_one_prior_year(self):
        rows = [
            _buy("Jane Doe", "AAA", "2025-03-10"),
            _buy("Jane Doe", "AAA", "2025-09-10"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-02-01")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 1)

    def test_unusable_inputs(self):
        self.assertEqual(_classify([], "", "AAA", "2026-01-01"), (None, None))
        self.assertEqual(_classify([], "Jane", "", "2026-01-01"), (None, None))
        self.assertEqual(_classify([], "Jane", "AAA", ""), (None, None))


# ---------------------------------------------------------------------------
# 5. Lookahead guard (critical, P3-6)
# ---------------------------------------------------------------------------

class TestLookaheadGuard(unittest.TestCase):

    def test_future_buy_excluded(self):
        """A buy announced AFTER the tx, in exactly the month/year that
        would complete a routine pattern, must be excluded."""
        rows = [
            _buy("Jane Doe", "AAA", "2024-03-10"),
            # Future filing (announced 2026-06-01) back-dated trade
            # 2025-03-12 — would make Mar a 2-of-2 routine month.
            ("Jane Doe", "AAA", "2025-03-12", "BUY", "2026-06-01"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 1)

    def test_identical_timestamp_sibling_excluded(self):
        """Same-RNS sibling sharing the exact announced_at is excluded
        (strict <), so it cannot leak into its own classification."""
        rows = [
            _buy("Jane Doe", "AAA", "2024-03-10"),
            ("Jane Doe", "AAA", "2025-03-12", "BUY", "2026-01-15T07:00:00Z"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15T07:00:00Z")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 1)

    def test_strictly_prior_included(self):
        rows = [
            _buy("Jane Doe", "AAA", "2024-03-10"),
            ("Jane Doe", "AAA", "2025-03-12", "BUY", "2026-01-15T06:59:59Z"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15T07:00:00Z")
        self.assertEqual(flag, rf.ROUTINE)
        self.assertEqual(n, 2)


# ---------------------------------------------------------------------------
# 6. announced_at fallback
# ---------------------------------------------------------------------------

class TestAnnouncedAtFallback(unittest.TestCase):

    def test_empty_announced_at_uses_date(self):
        rows = [
            ("Jane Doe", "AAA", "2024-03-10", "BUY", ""),   # eff = date
            ("Jane Doe", "AAA", "2025-03-12", "BUY", None),  # eff = date
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15")
        self.assertEqual(flag, rf.ROUTINE)
        self.assertEqual(n, 2)


# ---------------------------------------------------------------------------
# 7. Cross-key isolation
# ---------------------------------------------------------------------------

class TestCrossKeyIsolation(unittest.TestCase):

    def test_other_ticker_does_not_count(self):
        rows = [
            _buy("Jane Doe", "BBB", "2024-03-10"),
            _buy("Jane Doe", "BBB", "2025-03-12"),
            _buy("Jane Doe", "AAA", "2025-06-01"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 1)

    def test_other_director_does_not_count(self):
        rows = [
            _buy("John Smith", "AAA", "2024-03-10"),
            _buy("John Smith", "AAA", "2025-03-12"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 0)

    def test_sells_do_not_count(self):
        rows = [
            ("Jane Doe", "AAA", "2024-03-10", "SELL", "2024-03-10"),
            ("Jane Doe", "AAA", "2025-03-12", "SELL", "2025-03-12"),
        ]
        flag, n = _classify(rows, "Jane Doe", "AAA", "2026-01-15")
        self.assertEqual(flag, rf.INSUFFICIENT)
        self.assertEqual(n, 0)


# ---------------------------------------------------------------------------
# 8-9. Backtest integration + HEADER contract
# ---------------------------------------------------------------------------

class TestBacktestIntegration(unittest.TestCase):

    def test_backtest_imports_classifier(self):
        import backtest as bt
        self.assertIsNotNone(bt.build_buy_history_index)
        self.assertIsNotNone(bt.classify_routine)

    def test_header_columns_present_and_positioned(self):
        import backtest as bt
        # B-159 then B-161 (Sprint 63) each added a pair directly
        # before windows_available, shifting the B-155 pair back by 4.
        idx_wa = bt.HEADER.index("windows_available")
        self.assertEqual(bt.HEADER[idx_wa - 1], "days_since_results")
        self.assertEqual(bt.HEADER[idx_wa - 2], "post_results_flag")
        self.assertEqual(bt.HEADER[idx_wa - 3], "net_shares_prior_12m")
        self.assertEqual(bt.HEADER[idx_wa - 4], "seller_reversal_flag")
        self.assertEqual(bt.HEADER[idx_wa - 5], "routine_prior_buy_years")
        self.assertEqual(bt.HEADER[idx_wa - 6], "routine_flag")
        self.assertEqual(bt.HEADER[idx_wa - 7], "short_pct_at_announcement")

    def test_header_length_64(self):
        import backtest as bt
        # 58 (post-B-164) + routine_flag + routine_prior_buy_years = 60;
        # + seller_reversal_flag + net_shares_prior_12m (B-159) = 62;
        # + post_results_flag + days_since_results (B-161) = 64;
        # + 7 B-168 salary-multiple cols (appended after windows_available) = 71.
        self.assertEqual(len(bt.HEADER), 71)

    def test_select_firings_selects_director(self):
        """_select_firings must expose t.director for the classifier."""
        import inspect
        import backtest as bt
        src = inspect.getsource(bt._select_firings)
        self.assertIn("t.director", src)

    def test_run_backtest_gates_on_buy(self):
        """The classify call must be gated on tx_type == "BUY" so
        non-BUY firings emit empty cells (source-level check; a
        regression to unconditional classification fails loudly)."""
        import inspect
        import backtest as bt
        src = inspect.getsource(bt.run_backtest)
        call_pos = src.index("classify_routine(")
        # The gate must appear shortly before the call site.
        gate_window = src[max(0, call_pos - 400):call_pos]
        self.assertIn('tx_type == "BUY"', gate_window)
        # And the else branch must null both fields.
        post = src[call_pos:call_pos + 400]
        self.assertIn("routine_flag_val, routine_years = None, None", post)


if __name__ == "__main__":
    unittest.main()
