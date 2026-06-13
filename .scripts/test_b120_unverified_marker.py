"""Tests for B-120 — "unverified" price marker in dealings tables.

Rows whose price failed the B-060 market-data audit (price_audit in
'unresolved'/'no_market') must render an "unverified" chip on the value cell,
on both the This-Week table (render_index) and the company page
(render_company). Verified rows must not.

Run:
    python -m unittest test_b120_unverified_marker -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_index, render_company  # noqa: E402


def _week_row(**over):
    base = {
        "ticker": "CER", "company": "Cerillion", "director": "Jane",
        "role": "CEO", "role_normalized": None, "txn_type": "BUY",
        "value_gbp": 1000, "signals_fired": [], "abs_return_pct": None,
        "bench_return_pct": None, "time_utc": "2026-06-01",
        "near_reporting_date": None, "near_reporting_est": False,
        "unverified": False,
    }
    base.update(over)
    return base


def _company(**tx_over):
    tx = {
        "fingerprint": "fp1", "date": "2026-06-01", "announced_at": "2026-06-01",
        "director": "Jane", "role": "CEO", "role_normalized": None,
        "txn_type": "BUY", "shares": 100, "price": 10.0, "value": 1000.0,
        "url": "", "company": "Cerillion", "signals": [],
        "near_reporting_date": None, "unverified": False,
    }
    tx.update(tx_over)
    return {"ticker": "CER", "company": "Cerillion", "transactions": [tx],
            "prices": [], "latest_close": None}


class TestThisWeekTable(unittest.TestCase):
    def test_unverified_row_shows_chip(self):
        html = render_index._row_html(_week_row(unverified=True), "2026-06-05")
        self.assertIn("unverified", html)
        self.assertIn("&#9888;", html)            # warning glyph

    def test_verified_row_has_no_chip(self):
        html = render_index._row_html(_week_row(unverified=False), "2026-06-05")
        self.assertNotIn("unverified", html)


class TestCompanyTable(unittest.TestCase):
    def test_unverified_row_shows_chip(self):
        html = render_company._transactions_table(
            _company(unverified=True), date(2026, 6, 5))
        self.assertIn("unverified", html)
        self.assertIn("&#9888;", html)

    def test_verified_row_has_no_chip(self):
        html = render_company._transactions_table(
            _company(unverified=False), date(2026, 6, 5))
        self.assertNotIn("unverified", html)


class TestFlagDerivation(unittest.TestCase):
    """Mirrors the exporter rule: price_audit -> unverified bool."""

    @staticmethod
    def _flag(audit):
        return audit in ("unresolved", "no_market")

    def test_flagged_states(self):
        self.assertTrue(self._flag("unresolved"))
        self.assertTrue(self._flag("no_market"))

    def test_clean_states(self):
        for ok in (None, "ok", "ok_pounds", "corrected_pence"):
            self.assertFalse(self._flag(ok))


if __name__ == "__main__":
    unittest.main(verbosity=2)
