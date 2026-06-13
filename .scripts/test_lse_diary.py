"""Tests for backfill_lse_diary.py (B-111).

The parser is tested against the COMMITTED real sample
`.scripts/fixtures/lse_diary_sample.html` (LSE Financial Diary, June 2026 month
view). The DB write path is tested against an in-memory SQLite DB that mirrors
the post-migration-009 `reporting_dates` schema.

Run:
    python -m unittest test_lse_diary -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_lse_diary as bd  # noqa: E402

FIXTURE = HERE / "fixtures" / "lse_diary_sample.html"
ALLOWED_TYPES = {"PRELIM", "INTERIM", "QUARTERLY", "TRADING_STMT"}


# Post-migration-009 reporting_dates schema (inline, for the write test).
_REPORTING_DATES_DDL = """
CREATE TABLE reporting_dates (
    ticker      TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'EARNINGS'
                CHECK (report_type IN ('INTERIM','FINAL','TRADING_UPDATE','EARNINGS',
                                       'PRELIM','QUARTERLY','TRADING_STMT')),
    source      TEXT NOT NULL DEFAULT 'yahoo',
    fetched_at  TEXT NOT NULL,
    confidence  TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (confidence IN ('confirmed','est')),
    PRIMARY KEY (ticker, report_date, report_type)
);
"""


class TestPureHelpers(unittest.TestCase):

    def test_report_type_map(self):
        self.assertEqual(bd.report_type_for_title("Final Results"), "PRELIM")
        self.assertEqual(bd.report_type_for_title("Interim Results"), "INTERIM")
        for q in ("Q1 Results", "Q2 Results", "Q3 Results", "Q4 Results"):
            self.assertEqual(bd.report_type_for_title(q), "QUARTERLY")
        self.assertEqual(bd.report_type_for_title("Trading Announcement"),
                         "TRADING_STMT")
        self.assertEqual(bd.report_type_for_title("Interim Management Statement"),
                         "TRADING_STMT")
        # ignored sections -> None
        for ignore in ("AGM", "EGM", "GM", "Annual Report", "Drilling Report",
                       "Final Dividend Payment Date", "Final Ex-Dividend Date",
                       "UK Economic Announcement", "Intl Economic Announcement"):
            self.assertIsNone(bd.report_type_for_title(ignore))

    def test_to_iso_date(self):
        self.assertEqual(bd.to_iso_date("01-Jun-2026"), "2026-06-01")
        self.assertEqual(bd.to_iso_date("30-Dec-2025"), "2025-12-30")
        self.assertIsNone(bd.to_iso_date("not a date"))
        self.assertIsNone(bd.to_iso_date(""))

    def test_tidm_from_href(self):
        self.assertEqual(
            bd.tidm_from_href("https://www.lse.co.uk/SharePrice.html?"
                              "shareprice=BLOE&share=Block-Energy-P"), "BLOE")
        self.assertEqual(bd.tidm_from_href("x?shareprice=bt.a&y=1"), "BT.A")
        self.assertIsNone(bd.tidm_from_href("https://x/no-param"))

    def test_normalise_and_match_keys(self):
        self.assertEqual(bd.normalise_tidm("  bloe "), "BLOE")
        self.assertEqual(bd.ticker_match_keys("BT.A"), ["BT.A", "BTA"])
        self.assertEqual(bd.ticker_match_keys("CER"), ["CER"])


class TestParseFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE.read_text(encoding="utf-8", errors="replace")
        cls.events = bd.parse_diary_html(cls.html)
        cls.index = {(e["report_date"], e["tidm"]): e for e in cls.events}

    def test_fixture_exists_and_parses(self):
        self.assertTrue(FIXTURE.exists(), "committed diary fixture missing")
        self.assertGreater(len(self.events), 0, "no events parsed")

    def test_only_results_types(self):
        # No AGM/dividend/economic rows leak in: every kept event is a results type.
        for e in self.events:
            self.assertIn(e["report_type"], ALLOWED_TYPES, e)

    def test_all_dates_iso_and_june(self):
        for e in self.events:
            self.assertRegex(e["report_date"], r"^2026-06-\d{2}$", e)

    def test_known_final_results_are_prelim(self):
        # Block Energy (BLOE) Final Results on 01-Jun-2026 -> PRELIM.
        e = self.index.get(("2026-06-01", "BLOE"))
        self.assertIsNotNone(e, "BLOE 2026-06-01 not parsed")
        self.assertEqual(e["report_type"], "PRELIM")
        self.assertIn("Block Energy", e["company"])

    def test_known_interim_results(self):
        # Cerillion (CER) and Lpa (LPA) are Interim Results on 01-Jun-2026.
        for tidm in ("CER", "LPA"):
            e = self.index.get(("2026-06-01", tidm))
            self.assertIsNotNone(e, f"{tidm} 2026-06-01 not parsed")
            self.assertEqual(e["report_type"], "INTERIM")
        # Chemring (CHG) / Paragon (PAG) interim on 02-Jun-2026.
        for tidm in ("CHG", "PAG"):
            e = self.index.get(("2026-06-02", tidm))
            self.assertIsNotNone(e, f"{tidm} 2026-06-02 not parsed")
            self.assertEqual(e["report_type"], "INTERIM")

    def test_company_name_captured(self):
        e = self.index.get(("2026-06-01", "CER"))
        self.assertTrue(e["company"], "company name not captured for CER")
        self.assertNotEqual(e["company"].upper(), "CER")  # not the TIDM itself


class TestWritePath(unittest.TestCase):

    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.executescript(_REPORTING_DATES_DDL)
        c.execute("CREATE TABLE transactions (ticker TEXT)")
        # we hold CER and CHG; NOT BLOE.
        c.executemany("INSERT INTO transactions (ticker) VALUES (?)",
                      [("CER",), ("CHG",), ("CER",)])
        # a pre-existing yahoo row (must survive) and a stale lse_diary row
        # (must be replaced).
        c.execute("INSERT INTO reporting_dates VALUES "
                  "('CER','2026-09-01','INTERIM','yahoo','t','confirmed')")
        c.execute("INSERT INTO reporting_dates VALUES "
                  "('OLD','2026-07-01','PRELIM','lse_diary','t','confirmed')")
        c.commit()
        return c

    def test_filters_held_and_writes_confirmed(self):
        conn = self._conn()
        held = bd.load_held_tickers(conn)
        self.assertEqual(held, {"CER", "CHG"})
        events = [
            {"report_date": "2026-06-01", "tidm": "CER",
             "report_type": "INTERIM", "company": "Cerillion"},
            {"report_date": "2026-06-02", "tidm": "CHG",
             "report_type": "INTERIM", "company": "Chemring"},
            {"report_date": "2026-06-01", "tidm": "BLOE",
             "report_type": "PRELIM", "company": "Block Energy"},  # not held
        ]
        stats = bd.write_reporting_dates(conn, events, held)
        # API changed (B-154): all events stored regardless of held;
        # "held_with_future_date" replaces "matched"; "unmatched_tidms" removed.
        self.assertEqual(stats["held_with_future_date"], 2)  # CER + CHG in held
        self.assertEqual(stats["written"], 3)  # BLOE now stored too

        rows = conn.execute(
            "SELECT ticker, report_type, source, confidence FROM reporting_dates "
            "WHERE source='lse_diary' ORDER BY ticker").fetchall()
        self.assertEqual(rows, [
            ("BLOE", "PRELIM", "lse_diary", "confirmed"),   # stored even though not held
            ("CER", "INTERIM", "lse_diary", "confirmed"),
            ("CHG", "INTERIM", "lse_diary", "confirmed"),
        ])
        # stale lse_diary row replaced ...
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM reporting_dates WHERE ticker='OLD'").fetchone())
        # ... but the yahoo row survives.
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM reporting_dates WHERE source='yahoo'").fetchone())

    def test_dry_run_writes_nothing(self):
        conn = self._conn()
        held = bd.load_held_tickers(conn)
        before = conn.execute(
            "SELECT count(*) FROM reporting_dates WHERE source='lse_diary'"
        ).fetchone()[0]
        stats = bd.write_reporting_dates(
            conn, [{"report_date": "2026-06-01", "tidm": "CER",
                    "report_type": "INTERIM", "company": "Cerillion"}],
            held, dry_run=True)
        self.assertEqual(stats["written"], 0)
        after = conn.execute(
            "SELECT count(*) FROM reporting_dates WHERE source='lse_diary'"
        ).fetchone()[0]
        self.assertEqual(before, after)


class TestB114PreEarningsChip(unittest.TestCase):
    """B-111 + B-114: pre-earnings conviction chip + row tag on This-Week rows."""

    def setUp(self):
        from dashboard import render_index
        self.ri = render_index

    def _row(self, **over):
        base = {
            "ticker": "CER", "company": "Cerillion", "director": "Jane",
            "role": "CEO", "role_normalized": None, "txn_type": "BUY",
            "value_gbp": 1000, "signals_fired": [], "abs_return_pct": None,
            "bench_return_pct": None, "time_utc": "2026-06-01",
            "near_reporting_date": None, "near_reporting_est": False,
        }
        base.update(over)
        return base

    def test_no_chip_and_row_tagged_zero(self):
        html = self.ri._row_html(self._row(), "2026-06-05")
        self.assertNotIn("pre-earnings", html)
        self.assertIn('data-pe="0"', html)         # B-114 filter tag

    def test_confirmed_chip_and_row_tagged_one(self):
        html = self.ri._row_html(
            self._row(near_reporting_date="2026-06-20"), "2026-06-05")
        self.assertIn("pre-earnings", html)        # B-114 conviction chip
        self.assertIn('data-pe="1"', html)         # row tagged for the filter
        self.assertNotIn("(est)", html)
        self.assertIn("2026-06-20", html)          # date in the title
        self.assertIn("bg-amber-200", html)        # elevated conviction style
        self.assertIn("pe-chip", html)

    def test_est_chip_has_suffix(self):
        html = self.ri._row_html(
            self._row(near_reporting_date="2026-06-20",
                      near_reporting_est=True), "2026-06-05")
        self.assertIn("pre-earnings", html)
        self.assertIn("(est)", html)
        self.assertIn('data-pe="1"', html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
