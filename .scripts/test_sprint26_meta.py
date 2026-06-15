"""Sprint 26 — Unit tests for ticker-meta enrichment (B-097 / B-101 / B-105).

Covers:
  - Schema migrations 006 + 007 + 008 apply cleanly on a fresh in-memory DB
  - backfill_ticker_meta._extract_meta() parses Yahoo quoteSummary responses
  - AIM exchange detection sets is_aim=1 and benchmark_symbol='^FTSC'
  - fetch_sectors.resolve() AIM override guard
  - fetch_sectors.upsert_meta() does NOT clobber Sprint 26 enrichment columns
  - backfill_reporting_dates._extract_reporting_dates() parses calendarEvents
  - 60-day near-results flag logic

Safe to run from bash (no Zone-B writes — all DB work uses in-memory SQLite).
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_memory_db() -> sqlite3.Connection:
    """Open a fresh in-memory DB with the full schema + all migrations applied."""
    # db.DB_PATH points at the real file; patch it temporarily to :memory:
    orig = db.DB_PATH
    try:
        db.DB_PATH = ":memory:"  # type: ignore[assignment]
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(conn)
        return conn
    finally:
        db.DB_PATH = orig


# ---------------------------------------------------------------------------
# Migration 006/007/008 tests
# ---------------------------------------------------------------------------

class TestMigrations(unittest.TestCase):

    def test_shares_outstanding_column_exists(self):
        conn = _in_memory_db()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tickers_meta)").fetchall()}
        self.assertIn("shares_outstanding", cols,
                      "Migration 006 should add shares_outstanding to tickers_meta")

    def test_website_url_column_exists(self):
        conn = _in_memory_db()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tickers_meta)").fetchall()}
        self.assertIn("website_url", cols,
                      "Migration 007 should add website_url to tickers_meta")

    def test_reporting_dates_table_exists(self):
        conn = _in_memory_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reporting_dates'"
        ).fetchone()
        self.assertIsNotNone(row,
                             "Migration 008 should create reporting_dates table")

    def test_reporting_dates_primary_key(self):
        """Can't insert duplicate (ticker, report_date, report_type)."""
        conn = _in_memory_db()
        now = db.iso_now()
        conn.execute(
            "INSERT INTO reporting_dates (ticker, report_date, report_type, source, fetched_at) "
            "VALUES ('BARC', '2026-07-01', 'EARNINGS', 'yahoo', ?)",
            (now,),
        )
        conn.commit()
        # Duplicate should silently be ignored with OR IGNORE.
        cur = conn.execute(
            "INSERT OR IGNORE INTO reporting_dates "
            "(ticker, report_date, report_type, source, fetched_at) "
            "VALUES ('BARC', '2026-07-01', 'EARNINGS', 'yahoo', ?)",
            (now,),
        )
        self.assertEqual(cur.rowcount, 0, "Duplicate row should be ignored (PK conflict)")

    def test_schema_version_at_chain_head(self):
        # Pins the migration chain head. Bump this whenever a new
        # migration is added (015 director_pay -> head is now "15").
        conn = _in_memory_db()
        version = db.get_meta(conn, "schema_version")
        self.assertEqual(version, "15",
                         f"schema_version should be '15' after all migrations; got {version!r}")

    def test_migrations_idempotent(self):
        """Calling db.migrate() twice on the same connection must not raise."""
        conn = _in_memory_db()
        try:
            db.migrate(conn)
        except Exception as e:
            self.fail(f"Second migrate() call raised: {e}")


# ---------------------------------------------------------------------------
# backfill_ticker_meta._extract_meta tests
# ---------------------------------------------------------------------------

class TestExtractMeta(unittest.TestCase):

    def _make_meta(self, *, exchange="LSE", full_exchange="LSE",
                   currency="GBP", market_cap=500_000_000):
        """Build a minimal Yahoo v8 chart meta block (chart.result[0]['meta']).

        v10/quoteSummary returns HTTP 401 as of 2026; backfill_ticker_meta.py
        was updated to use v8/finance/chart meta instead (same endpoint as
        fetch_prices.py). The meta block is flat, not nested.
        website_url is not available from this endpoint.
        """
        meta = {
            "exchangeName":     exchange,
            "fullExchangeName": full_exchange,
            "currency":         currency,
        }
        if market_cap is not None:
            meta["marketCap"] = market_cap
        return meta

    def setUp(self):
        from backfill_ticker_meta import _extract_enrichment
        self._extract = _extract_enrichment

    def test_non_aim_main_market(self):
        meta = self._make_meta(exchange="LSE", market_cap=1_000_000_000)
        result = self._extract(meta)
        self.assertEqual(result["is_aim"], 0)
        self.assertIsNone(result["benchmark_symbol"],
                          "Non-AIM should not override benchmark_symbol")
        self.assertAlmostEqual(result["market_cap_gbp"], 1_000_000_000.0)
        self.assertIsNone(result["website_url"],
                          "website_url not available from v8 chart endpoint")

    def test_aim_exchange_detected(self):
        meta = self._make_meta(exchange="AIM", full_exchange="FTSE AIM",
                               market_cap=50_000_000)
        result = self._extract(meta)
        self.assertEqual(result["is_aim"], 1)
        self.assertEqual(result["benchmark_symbol"], "^FTSC")
        self.assertAlmostEqual(result["market_cap_gbp"], 50_000_000.0)

    def test_aim_detected_from_full_exchange_name(self):
        meta = self._make_meta(exchange="LON", full_exchange="AIM - LSE",
                               market_cap=30_000_000)
        result = self._extract(meta)
        self.assertEqual(result["is_aim"], 1)
        self.assertEqual(result["benchmark_symbol"], "^FTSC")

    def test_gbp_pence_currency_conversion(self):
        """Market cap in GBp (pence) should be divided by 100."""
        meta = self._make_meta(exchange="LSE", currency="GBp",
                               market_cap=50_000_000_000)  # 50bn pence = 500m GBP
        result = self._extract(meta)
        self.assertAlmostEqual(result["market_cap_gbp"], 500_000_000.0)

    def test_usd_currency_rejected(self):
        """Non-GBP/GBp market caps should not be stored."""
        meta = self._make_meta(exchange="LSE", currency="USD",
                               market_cap=2_000_000_000)
        result = self._extract(meta)
        self.assertIsNone(result["market_cap_gbp"])

    def test_no_market_cap_in_meta(self):
        """Some tickers don't include marketCap in the chart meta -- handled."""
        meta = self._make_meta(exchange="LSE", market_cap=None)
        result = self._extract(meta)
        self.assertIsNone(result["market_cap_gbp"])

    def test_empty_meta_dict(self):
        """Empty meta (network edge case) returns safe defaults."""
        result = self._extract({})
        self.assertEqual(result["is_aim"], 0,
                         "Empty meta should default to non-AIM")
        self.assertIsNone(result["market_cap_gbp"])


# ---------------------------------------------------------------------------
# fetch_sectors AIM override guard
# ---------------------------------------------------------------------------

class TestFetchSectorsAIMOverride(unittest.TestCase):

    def setUp(self):
        from fetch_sectors import resolve
        self._resolve = resolve

    def test_non_aim_ticker_gets_ftas(self):
        sector_map = {"BARC": {"sector": "Financials", "benchmark_symbol": None, "is_aim": 0}}
        bmarks = {"_default": "^FTAS", "Financials": "^FTAS"}
        meta = self._resolve("BARC", sector_map, bmarks)
        self.assertEqual(meta.benchmark_symbol, "^FTAS")
        self.assertEqual(meta.is_aim, 0)

    def test_aim_ticker_in_csv_gets_aim_benchmark(self):
        sector_map = {"ARC": {"sector": "Technology", "benchmark_symbol": None, "is_aim": 1}}
        bmarks = {"_default": "^FTAS"}
        meta = self._resolve("ARC", sector_map, bmarks)
        self.assertEqual(meta.benchmark_symbol, "^FTSC",
                         "is_aim=1 should force benchmark to ^FTSC")
        self.assertEqual(meta.is_aim, 1)

    def test_aim_csv_overrides_explicit_benchmark(self):
        """Even if CSV has explicit benchmark_symbol, is_aim=1 should override."""
        sector_map = {
            "ARC": {"sector": "Technology", "benchmark_symbol": "^FTAS", "is_aim": 1}
        }
        bmarks = {"_default": "^FTAS"}
        meta = self._resolve("ARC", sector_map, bmarks)
        self.assertEqual(meta.benchmark_symbol, "^FTSC",
                         "is_aim=1 AIM override should win over explicit CSV benchmark")


# ---------------------------------------------------------------------------
# fetch_sectors upsert_meta preserves Sprint 26 enrichment columns
# ---------------------------------------------------------------------------

class TestUpsertMetaPreservesEnrichment(unittest.TestCase):

    def test_upsert_does_not_clobber_enrichment(self):
        from fetch_sectors import TickerMeta, upsert_meta
        conn = _in_memory_db()
        # Seed a tickers_meta row with Sprint 26 enrichment data.
        conn.execute(
            "INSERT INTO tickers_meta "
            "(ticker, sector, benchmark_symbol, is_aim, "
            " market_cap_gbp, shares_outstanding, website_url, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("BARC", "Financials", "^FTAS", 0,
             50_000_000_000.0, 17_000_000_000.0, "https://group.barclays.com",
             db.iso_now()),
        )
        conn.commit()
        # Now simulate fetch_sectors.py run: upsert_meta with new sector/benchmark.
        meta = TickerMeta(ticker="BARC", sector="Financials",
                          benchmark_symbol="^FTAS", is_aim=0)
        upsert_meta(conn, meta)
        conn.commit()
        row = conn.execute(
            "SELECT market_cap_gbp, shares_outstanding, website_url "
            "FROM tickers_meta WHERE ticker='BARC'"
        ).fetchone()
        self.assertAlmostEqual(row["market_cap_gbp"], 50_000_000_000.0,
                               msg="market_cap_gbp must not be reset by fetch_sectors")
        self.assertAlmostEqual(row["shares_outstanding"], 17_000_000_000.0,
                               msg="shares_outstanding must not be reset by fetch_sectors")
        self.assertEqual(row["website_url"], "https://group.barclays.com",
                         "website_url must not be reset by fetch_sectors")


# ---------------------------------------------------------------------------
# backfill_reporting_dates._extract_reporting_dates tests
# ---------------------------------------------------------------------------

@unittest.skip(
    "Sprint 28 B-096b: _extract_reporting_dates (Yahoo calendarEvents) replaced "
    "by _extract_dates_from_html (Investegate). New tests in test_sprint28.py."
)
class TestExtractReportingDates(unittest.TestCase):

    def setUp(self):
        from backfill_reporting_dates import _extract_reporting_dates
        self._extract = _extract_reporting_dates

    def _make_cal_raw(self, dates: list) -> dict:
        """Build a minimal Yahoo calendarEvents quoteSummary response.

        `dates` items can be dicts with "raw" epoch or ISO "fmt" strings.
        """
        return {
            "quoteSummary": {
                "result": [{
                    "calendarEvents": {
                        "earnings": {
                            "earningsDate": dates,
                        }
                    }
                }],
                "error": None,
            }
        }

    def test_future_date_extracted(self):
        # Pick a date 30 days from today.
        today = date.today()
        from datetime import timedelta
        future = today + timedelta(days=30)
        epoch = int(datetime(future.year, future.month, future.day,
                             tzinfo=timezone.utc).timestamp())
        raw = self._make_cal_raw([{"raw": epoch, "fmt": future.isoformat()}])
        dates = self._extract(raw, "BARC")
        self.assertEqual(len(dates), 1)
        self.assertEqual(dates[0]["report_date"], future.isoformat())
        self.assertEqual(dates[0]["ticker"], "BARC")
        self.assertEqual(dates[0]["report_type"], "EARNINGS")

    def test_stale_date_filtered_out(self):
        """Dates more than 30 days in the past should be excluded."""
        from datetime import timedelta
        old = date.today() - timedelta(days=60)
        epoch = int(datetime(old.year, old.month, old.day,
                             tzinfo=timezone.utc).timestamp())
        raw = self._make_cal_raw([{"raw": epoch, "fmt": old.isoformat()}])
        dates = self._extract(raw, "BARC")
        self.assertEqual(len(dates), 0,
                         "60-day-old reporting date should be filtered out")

    def test_empty_calendar_events(self):
        raw = {"quoteSummary": {"result": [{"calendarEvents": {}}], "error": None}}
        dates = self._extract(raw, "BARC")
        self.assertEqual(dates, [])

    def test_fmt_string_used_when_available(self):
        from datetime import timedelta
        future = date.today() + timedelta(days=14)
        raw = self._make_cal_raw([{"raw": 9999999999, "fmt": future.isoformat()}])
        dates = self._extract(raw, "BARC")
        self.assertEqual(len(dates), 1)
        self.assertEqual(dates[0]["report_date"], future.isoformat())


# ---------------------------------------------------------------------------
# 60-day near-results badge logic
# ---------------------------------------------------------------------------

class TestNearResultsBadgeLogic(unittest.TestCase):
    """Verify the transaction-level near-reporting-date flag injected by
    build_dashboard._build_company_record(). We test the predicate
    independently: for each txn, is any reporting_date within 0-60 days
    AFTER the transaction date?
    """

    def _check(self, txn_date: str, reporting_dates: list[str]) -> str | None:
        """Mirror the predicate from build_dashboard._build_company_record()."""
        txn_d = date.fromisoformat(txn_date)
        for rd_str in reporting_dates:
            rd = date.fromisoformat(rd_str)
            days_before = (rd - txn_d).days
            if 0 <= days_before <= 60:
                return rd_str
        return None

    def test_transaction_within_60_days_flagged(self):
        txn = "2026-09-01"
        rd = "2026-10-15"   # 44 days after txn
        result = self._check(txn, [rd])
        self.assertEqual(result, rd)

    def test_transaction_on_exact_reporting_date_flagged(self):
        txn = "2026-10-15"
        result = self._check(txn, ["2026-10-15"])
        self.assertEqual(result, "2026-10-15")

    def test_transaction_61_days_before_not_flagged(self):
        txn = "2026-08-15"
        rd = "2026-10-15"   # 61 days after txn
        result = self._check(txn, [rd])
        self.assertIsNone(result)

    def test_transaction_after_reporting_date_not_flagged(self):
        txn = "2026-11-01"
        rd = "2026-10-15"   # reporting date is in the past relative to txn
        result = self._check(txn, [rd])
        self.assertIsNone(result)

    def test_picks_nearest_date(self):
        txn = "2026-09-01"
        # Two upcoming dates; nearest (Oct 10) should win.
        rds = ["2026-10-10", "2026-12-01"]
        result = self._check(txn, rds)
        self.assertEqual(result, "2026-10-10")


if __name__ == "__main__":
    unittest.main()
