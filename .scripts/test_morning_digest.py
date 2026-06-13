"""Tests for morning_digest.py (B-116) — pure-function digest + freshness guard.

No network / no SMTP is exercised. Run:
    python -m unittest test_morning_digest -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import morning_digest as md  # noqa: E402

TODAY = date(2026, 6, 6)


def _buy(ticker="CER", value=100000, near=None, est=False, **over):
    r = {"ticker": ticker, "company": f"{ticker} plc", "director": "Jane",
         "txn_type": "BUY", "value_gbp": value,
         "near_reporting_date": near, "near_reporting_est": est}
    r.update(over)
    return r


class TestFreshnessGuard(unittest.TestCase):
    def test_fresh_when_as_of_today(self):
        self.assertTrue(md.is_fresh({"as_of_date": "2026-06-06"}, TODAY))

    def test_stale_when_as_of_old(self):
        self.assertFalse(md.is_fresh({"as_of_date": "2026-06-04"}, TODAY))

    def test_stale_when_missing(self):
        self.assertFalse(md.is_fresh({}, TODAY))


class TestBuildDigest(unittest.TestCase):
    def test_no_buys_quiet_night(self):
        d = md.build_digest({"as_of_date": "2026-06-06", "today": [],
                             "this_week": []}, TODAY)
        self.assertFalse(d["has_content"])
        self.assertEqual(d["n_buys"], 0)
        self.assertIn("no new director buys", d["subject"].lower())

    def test_today_buys_counted(self):
        d = md.build_digest({"today": [_buy("CER"), _buy("CHG")]}, TODAY)
        self.assertTrue(d["has_content"])
        self.assertEqual(d["n_buys"], 2)
        self.assertEqual(d["n_pre_earnings"], 0)
        self.assertIn("CER", d["text"])

    def test_pre_earnings_highlighted(self):
        d = md.build_digest(
            {"today": [_buy("CER", near="2026-06-20"),
                       _buy("CHG")]}, TODAY)
        self.assertEqual(d["n_buys"], 2)
        self.assertEqual(d["n_pre_earnings"], 1)
        self.assertIn("PRE-EARNINGS", d["text"])
        self.assertIn("2026-06-20", d["text"])
        self.assertIn("pre-earnings", d["subject"].lower())

    def test_est_marked(self):
        d = md.build_digest(
            {"today": [_buy("CER", near="2026-06-20", est=True)]}, TODAY)
        self.assertIn("(est date)", d["text"])

    def test_falls_back_to_this_week_when_today_empty(self):
        d = md.build_digest(
            {"today": [], "this_week": [_buy("CER")]}, TODAY)
        self.assertTrue(d["has_content"])
        self.assertEqual(d["n_buys"], 1)
        self.assertIn("this week", d["text"])

    def test_digest_is_ascii(self):
        d = md.build_digest({"today": [_buy("CER", near="2026-06-20")]}, TODAY)
        d["text"].encode("ascii")
        d["subject"].encode("ascii")


class TestFmtGbp(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(md._fmt_gbp(2_500_000), "GBP 2.5m")
        self.assertEqual(md._fmt_gbp(120_000), "GBP 120k")
        self.assertEqual(md._fmt_gbp(500), "GBP 500")
        self.assertEqual(md._fmt_gbp(None), "-")


if __name__ == "__main__":
    unittest.main(verbosity=2)
