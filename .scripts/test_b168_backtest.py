"""Tests for B-168 Phase 3 -- salary-multiple wiring in backtest.py.

Follows the B-161 verification pattern: HEADER pins + source introspection of
run_backtest (the per-row compute correctness -- FX, nominal, salary_multiple,
lookahead -- is unit-tested in test_b168_director_pay.py). Importing backtest
pulls its full dependency chain, so this suite runs in the Windows
`unittest discover` gate alongside the other backtest tests.
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backtest as bt  # noqa: E402

B168_COLS = (
    "salary_multiple_total", "salary_multiple_base",
    "pay_total_gbp", "pay_base_gbp", "pay_fy_end", "pay_confidence", "pay_status",
)


class TestHeader(unittest.TestCase):
    def test_b168_columns_present(self):
        for col in B168_COLS:
            self.assertIn(col, bt.HEADER, f"missing HEADER col: {col}")

    def test_length_is_71(self):
        self.assertEqual(len(bt.HEADER), 71)

    def test_b168_columns_after_windows_available(self):
        # Appended after windows_available so the idx_wa-anchored pins of
        # B-155..B-164 stay valid. (Consumers read by name, not position.)
        idx_wa = bt.HEADER.index("windows_available")
        for col in B168_COLS:
            self.assertGreater(bt.HEADER.index(col), idx_wa, col)

    def test_b168_columns_are_the_tail(self):
        self.assertEqual(tuple(bt.HEADER[-7:]), B168_COLS)


class TestRunBacktestWiring(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(bt.run_backtest)

    def test_has_director_pay_guard(self):
        # one-time sqlite_master guard (B-164 pattern) so old fixtures emit empty
        self.assertIn("has_director_pay", self.src)
        self.assertIn("name='director_pay'", self.src)

    def test_uses_lookahead_helper(self):
        # the AR-publication-date lookahead guard lives in latest_pay_before
        self.assertIn("latest_pay_before", self.src)

    def test_computes_salary_multiple(self):
        self.assertIn("salary_multiple(", self.src)

    def test_buy_only_gate(self):
        # the dp block must be gated to BUY rows with a director
        self.assertIn('tx_type == "BUY" and tx_director', self.src)

    def test_writerow_includes_new_values(self):
        self.assertIn("sm_total", self.src)
        self.assertIn("pay_status_out", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
