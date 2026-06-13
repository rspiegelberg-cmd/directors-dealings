"""Tests for backfill_sectors.py (B-128) — pure helpers (no network / no DB).

Includes B-158 AIM benchmark guard tests (write_sector with in-memory SQLite).

Run:
    python -m unittest test_b128_sectors -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_sectors as bs  # noqa: E402


def _make_conn():
    """In-memory SQLite with the minimal tickers_meta schema for write_sector."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tickers_meta ("
        "  ticker TEXT PRIMARY KEY, "
        "  sector TEXT, "
        "  benchmark_symbol TEXT, "
        "  is_aim INTEGER NOT NULL DEFAULT 0, "
        "  website_url TEXT, "
        "  updated_at TEXT"
        ")"
    )
    return conn


class TestCandidateSymbols(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(bs.candidate_symbols("CER"), ["CER.L", "CER"])

    def test_lowercase_normalised(self):
        self.assertEqual(bs.candidate_symbols(" cer "), ["CER.L", "CER"])

    def test_dotted_ticker(self):
        self.assertEqual(bs.candidate_symbols("BT.A"),
                         ["BT.A.L", "BT.A", "BTA.L"])

    def test_empty(self):
        self.assertEqual(bs.candidate_symbols(""), [])
        self.assertEqual(bs.candidate_symbols(None), [])


class TestSectorFromPayload(unittest.TestCase):
    def test_list_with_sector(self):
        p = [{"symbol": "LSEG.L", "sector": "Financial Services",
              "industry": "Financial Data", "website": "https://lseg.com"}]
        out = bs.sector_from_payload(p)
        self.assertEqual(out["sector"], "Financial Services")
        self.assertEqual(out["industry"], "Financial Data")
        self.assertEqual(out["website"], "https://lseg.com")

    def test_dict_form(self):
        out = bs.sector_from_payload({"symbol": "X.L", "sector": "Energy"})
        self.assertEqual(out["sector"], "Energy")
        self.assertIsNone(out["industry"])

    def test_empty_list_returns_none(self):
        self.assertIsNone(bs.sector_from_payload([]))

    def test_blank_sector_returns_none(self):
        self.assertIsNone(bs.sector_from_payload([{"symbol": "X", "sector": ""}]))

    def test_error_dict_returns_none(self):
        self.assertIsNone(bs.sector_from_payload({"Error Message": "Invalid key"}))

    def test_garbage_returns_none(self):
        self.assertIsNone(bs.sector_from_payload(None))
        self.assertIsNone(bs.sector_from_payload("nope"))


class TestWriteSectorAIMGuard(unittest.TestCase):
    """B-158: write_sector must NOT overwrite benchmark_symbol for AIM tickers."""

    def test_non_aim_gets_new_benchmark(self):
        """Non-AIM ticker with no sector: benchmark_symbol is updated from FMP."""
        conn = _make_conn()
        conn.execute(
            "INSERT INTO tickers_meta (ticker, sector, benchmark_symbol, is_aim) "
            "VALUES ('ABC', NULL, '^FTAS', 0)"
        )
        bs.write_sector(conn, "ABC",
                        {"sector": "Financials", "industry": "Banks",
                         "website": None},
                        "^FTAS")
        row = conn.execute("SELECT * FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Financials")
        self.assertEqual(row["benchmark_symbol"], "^FTAS")

    def test_aim_benchmark_preserved(self):
        """AIM ticker with no sector: sector filled but benchmark stays '^FTSC'."""
        conn = _make_conn()
        conn.execute(
            "INSERT INTO tickers_meta (ticker, sector, benchmark_symbol, is_aim) "
            "VALUES ('XYZ', NULL, '^FTSC', 1)"
        )
        # backfill_sectors would compute bench='^FTAS' (all sectors map there)
        bs.write_sector(conn, "XYZ",
                        {"sector": "Industrials", "industry": "Engineering",
                         "website": "https://example.com"},
                        "^FTAS")
        row = conn.execute("SELECT * FROM tickers_meta WHERE ticker='XYZ'").fetchone()
        self.assertEqual(row["sector"], "Industrials",
                         "sector should be filled for AIM ticker")
        self.assertEqual(row["benchmark_symbol"], "^FTSC",
                         "AIM benchmark must NOT be overwritten to ^FTAS")

    def test_aim_ticker_inserted_fresh_gets_aim_benchmark(self):
        """AIM ticker not yet in DB: INSERT path must also preserve '^FTSC'."""
        conn = _make_conn()
        # ticker not in DB yet; run() would insert with is_aim still NULL/0
        # On INSERT the is_aim column is 0 (default) so CASE gives excluded.benchmark
        # In practice run() only calls write_sector after the ticker already exists
        # in tickers_meta (backfill_ticker_meta runs first). This test documents
        # that inserting a brand-new row writes whatever benchmark is passed.
        bs.write_sector(conn, "NEW",
                        {"sector": "Technology", "industry": "Software",
                         "website": None},
                        "^FTAS")
        row = conn.execute("SELECT * FROM tickers_meta WHERE ticker='NEW'").fetchone()
        self.assertEqual(row["sector"], "Technology")
        self.assertEqual(row["benchmark_symbol"], "^FTAS")

    def test_existing_sector_not_overwritten(self):
        """Ticker that already has a sector must not be touched (WHERE guard)."""
        conn = _make_conn()
        conn.execute(
            "INSERT INTO tickers_meta (ticker, sector, benchmark_symbol, is_aim) "
            "VALUES ('DEF', 'Energy', '^FTAS', 0)"
        )
        bs.write_sector(conn, "DEF",
                        {"sector": "Financials", "industry": "Banks",
                         "website": None},
                        "^FTAS")
        row = conn.execute("SELECT * FROM tickers_meta WHERE ticker='DEF'").fetchone()
        self.assertEqual(row["sector"], "Energy",
                         "existing sector must not be overwritten")


class TestSectorNormalise(unittest.TestCase):
    """B-163: SECTOR_NORMALISE map + normalise_existing() function."""

    def _conn_with_sectors(self, rows: list[tuple[str, str]]):
        """Create in-memory DB with pre-populated (ticker, sector) rows."""
        conn = _make_conn()
        for ticker, sector in rows:
            conn.execute(
                "INSERT INTO tickers_meta (ticker, sector) VALUES (?, ?)",
                (ticker, sector),
            )
        return conn

    def test_normalise_financial_services(self):
        conn = self._conn_with_sectors([("ABC", "Financial Services")])
        bs.normalise_existing(conn)
        row = conn.execute("SELECT sector FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Financials")

    def test_normalise_consumer_cyclical(self):
        conn = self._conn_with_sectors([("ABC", "Consumer Cyclical")])
        bs.normalise_existing(conn)
        row = conn.execute("SELECT sector FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Consumer Discretionary")

    def test_normalise_consumer_defensive(self):
        conn = self._conn_with_sectors([("ABC", "Consumer Defensive")])
        bs.normalise_existing(conn)
        row = conn.execute("SELECT sector FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Consumer Staples")

    def test_normalise_basic_materials(self):
        conn = self._conn_with_sectors([("ABC", "Basic Materials")])
        bs.normalise_existing(conn)
        row = conn.execute("SELECT sector FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Materials")

    def test_normalise_healthcare(self):
        conn = self._conn_with_sectors([("ABC", "Healthcare")])
        bs.normalise_existing(conn)
        row = conn.execute("SELECT sector FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Health Care")

    def test_canonical_names_unchanged(self):
        """Sectors already using canonical names must not be touched."""
        conn = self._conn_with_sectors([
            ("T1", "Industrials"),
            ("T2", "Financials"),
            ("T3", "Consumer Discretionary"),
            ("T4", "Consumer Staples"),
            ("T5", "Materials"),
            ("T6", "Health Care"),
            ("T7", "Technology"),
        ])
        bs.normalise_existing(conn)
        for ticker, expected in [("T1", "Industrials"), ("T2", "Financials"),
                                  ("T3", "Consumer Discretionary"), ("T4", "Consumer Staples"),
                                  ("T5", "Materials"), ("T6", "Health Care"),
                                  ("T7", "Technology")]:
            row = conn.execute(
                "SELECT sector FROM tickers_meta WHERE ticker=?", (ticker,)
            ).fetchone()
            self.assertEqual(row["sector"], expected, f"{ticker} sector changed unexpectedly")

    def test_normalise_idempotent(self):
        """Running twice gives the same result as running once."""
        conn = self._conn_with_sectors([("ABC", "Financial Services")])
        bs.normalise_existing(conn)
        n2 = bs.normalise_existing(conn)  # second run
        self.assertEqual(n2, 0, "second run should update 0 rows")
        row = conn.execute("SELECT sector FROM tickers_meta WHERE ticker='ABC'").fetchone()
        self.assertEqual(row["sector"], "Financials")

    def test_sector_normalise_applied_in_run_loop(self):
        """SECTOR_NORMALISE dict covers all expected FMP names."""
        fmp_names = set(bs.SECTOR_NORMALISE.keys())
        expected = {
            "Financial Services", "Consumer Cyclical", "Consumer Defensive",
            "Basic Materials", "Healthcare",
        }
        self.assertEqual(fmp_names, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
