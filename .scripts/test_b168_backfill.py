"""Tests for B-168 Phase 2 -- backfill_director_pay collection harness.

Covers the pure, unit-testable surface: the pdftotext figure extractor, the
record builder (FX + nominal classification + pay_kind mapping + status), and
the DB worklist selection (firing-frequency order, PCA + excluded-issuer drop,
director_key aggregation). Network/binary lanes (download, pdftotext) are not
unit-tested. In-memory DB only -- never touches .data/directors.db.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import backfill_director_pay as bf  # noqa: E402


class TestExtract(unittest.TestCase):
    def test_single_total_figure_and_base(self):
        text = ("Directors' Remuneration Report\n"
                "Single total figure of remuneration: 3,108,751\n"
                "Base salary 933,620  Pension 46,000\n")
        figs = bf.extract_pay_figures(text)
        self.assertEqual(figs["total"], 3108751.0)
        self.assertEqual(figs["base"], 933620.0)

    def test_total_fallback_row(self):
        figs = bf.extract_pay_figures("Salary 190,000\nTotal 293,901\n")
        self.assertEqual(figs["total"], 293901.0)
        self.assertEqual(figs["base"], 190000.0)

    def test_missing_returns_none(self):
        figs = bf.extract_pay_figures("No remuneration table here.")
        self.assertIsNone(figs["total"])
        self.assertIsNone(figs["base"])

    def test_empty(self):
        self.assertEqual(bf.extract_pay_figures(""), {"total": None, "base": None})


class TestBuildRecord(unittest.TestCase):
    def test_total_gbp(self):
        r = bf.build_record(ticker="PSN", director_name="Dean Finch",
                            fy_end="2025-12-31", pay_native=3108751,
                            currency="GBP", pay_kind="total")
        self.assertEqual(r["pay_type"], "single_figure_total")
        self.assertEqual(r["pay_status"], "ok")
        self.assertEqual(r["pay_gbp"], 3108751.0)
        self.assertEqual(r["director_key"], "dean finch")

    def test_base_gbp(self):
        r = bf.build_record(ticker="PSN", director_name="Dean Finch",
                            fy_end="2025-12-31", pay_native=933620,
                            currency="GBP", pay_kind="base")
        self.assertEqual(r["pay_type"], "base_salary")

    def test_usd_converts(self):
        r = bf.build_record(ticker="HOC", director_name="Eduardo Landin",
                            fy_end="2025-12-31", pay_native=2160000,
                            currency="USD", pay_kind="total")
        self.assertEqual(r["currency"], "USD")
        self.assertAlmostEqual(r["fx_rate"], 0.79)
        self.assertAlmostEqual(r["pay_gbp"], round(2160000 * 0.79, 2))

    def test_nominal_bucket(self):
        r = bf.build_record(ticker="KZG", director_name="Richard Jennings",
                            fy_end="2025-06-30", pay_native=5000,
                            currency="GBP", pay_kind="total")
        self.assertEqual(r["pay_type"], "nominal")
        self.assertEqual(r["pay_status"], "ok")

    def test_zero_is_fee_waiver(self):
        r = bf.build_record(ticker="ASC", director_name="William Barker",
                            fy_end="2025-08-31", pay_native=0,
                            currency="GBP", pay_kind="total")
        self.assertEqual(r["pay_type"], "fee_waiver_zero")

    def test_status_new_appointee(self):
        r = bf.build_record(ticker="TOO", director_name="Scott Livingston",
                            fy_end="", pay_native=None, currency="GBP",
                            pay_kind="none", status="new_appointee_no_disclosure")
        self.assertEqual(r["pay_type"], "none")
        self.assertEqual(r["pay_status"], "new_appointee_no_disclosure")
        self.assertEqual(r["fy_end"], "")

    def test_unsupported_currency_is_extraction_fail(self):
        r = bf.build_record(ticker="X", director_name="Y", fy_end="2025-12-31",
                            pay_native=100, currency="JPY", pay_kind="total")
        self.assertEqual(r["pay_type"], "none")
        self.assertEqual(r["pay_status"], "extraction_fail")


class TestSelectTargets(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(self.conn)
        self._seed()

    def tearDown(self):
        self.conn.close()

    def _tx(self, fp, ticker, director, role="CEO", dt="2025-01-01"):
        self.conn.execute(
            "INSERT INTO transactions (fingerprint, first_seen, last_seen, "
            "date, ticker, company, director, type, shares, role_normalized) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fp, "2025-01-01", "2025-01-01", dt, ticker, ticker + " plc",
             director, "BUY", 1000, role))

    def _sig(self, sid, fp):
        self.conn.execute(
            "INSERT INTO signals (signal_id, signal_version, fingerprint, fired_at) "
            "VALUES (?,?,?,?)", (sid, "1.0.0", fp, "2025-01-02"))

    def _seed(self):
        # AAA / Jane Doe: 3 buy signals across case variants (must merge)
        self._tx("fp1", "AAA", "Jane Doe");      self._sig("t1a_ceo_founder_buy", "fp1")
        self._tx("fp2", "AAA", "Jane Doe");       self._sig("s1_cluster_buy", "fp2")
        self._tx("fp3", "AAA", "JANE DOE");       self._sig("f1_first_time_buy", "fp3")
        # BBB / Bob PCA: only a PCA buy -> out of scope (not in _BUY_SIGNALS)
        self._tx("fp4", "BBB", "Bob PCA", role="PCA"); self._sig("t5_pca_buy", "fp4")
        # CCC / Eve: exec buy but issuer is excluded
        self._tx("fp5", "CCC", "Eve");            self._sig("t2_exec_buy", "fp5")
        self.conn.execute(
            "INSERT INTO tickers_meta (ticker, updated_at, is_excluded_issuer) "
            "VALUES ('CCC', '2025-01-01', 1)")
        # DDD / Al Low: one NED buy
        self._tx("fp6", "DDD", "Al Low", role="NED"); self._sig("t3_ned_buy", "fp6")
        self.conn.commit()

    def test_scope_order_and_merge(self):
        targets = bf.select_targets(self.conn)
        names = [(t["ticker"], t["director_key"], t["buy_signals"]) for t in targets]
        # AAA Jane Doe merged to 3; DDD 1; BBB & CCC excluded
        self.assertEqual(names, [("AAA", "jane doe", 3), ("DDD", "al low", 1)])

    def test_pca_only_excluded(self):
        keys = {(t["ticker"], t["director_key"]) for t in bf.select_targets(self.conn)}
        self.assertNotIn(("BBB", "bob pca"), keys)

    def test_excluded_issuer_dropped(self):
        keys = {t["ticker"] for t in bf.select_targets(self.conn)}
        self.assertNotIn("CCC", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
