"""Sprint 28 test suite.

Tests for:
  B-096b  backfill_reporting_dates (Investegate scraper)
  B-097b  backfill_ticker_meta CSV fallback (market cap)
  B-101b  backfill_ticker_meta CSV fallback (website)
  B-100   build_paper_book_summary (read-only paper book)
  B-078   build_cohort_table vw_mean_car field

All tests run without touching the live DB -- they use in-memory sqlite3
or mock data. Safe to run from bash.
"""
from __future__ import annotations
import ast
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


# ---------------------------------------------------------------------------
# Helper: in-memory SQLite with the project schema shape
# ---------------------------------------------------------------------------

def _make_mem_db():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (
            fingerprint TEXT PRIMARY KEY,
            date TEXT,
            ticker TEXT,
            company TEXT,
            director TEXT,
            role TEXT,
            type TEXT,
            shares REAL,
            price REAL,
            value REAL,
            announced_at TEXT,
            cluster_id TEXT,
            role_normalized TEXT,
            buy_strictness TEXT
        );
        CREATE TABLE signals (
            signal_id TEXT,
            signal_version TEXT,
            fingerprint TEXT,
            fired_at TEXT,
            confidence TEXT,
            metadata TEXT
        );
        CREATE TABLE prices (
            ticker TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source TEXT,
            fetched_at TEXT
        );
        CREATE TABLE tickers_meta (
            ticker TEXT PRIMARY KEY,
            sector TEXT,
            benchmark_symbol TEXT,
            is_aim INTEGER DEFAULT 0,
            market_cap_gbp REAL,
            updated_at TEXT,
            is_excluded_issuer INTEGER DEFAULT 0,
            excluded_source TEXT,
            classified_at TEXT,
            shares_outstanding REAL,
            website_url TEXT
        );
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            signal_id TEXT, signal_version TEXT,
            fingerprint TEXT, sizing_scheme TEXT,
            notional_gbp REAL, entry_date TEXT,
            entry_close REAL, shares REAL,
            exit_date TEXT, exit_close TEXT,
            status TEXT, opened_at TEXT,
            updated_at TEXT, notes TEXT
        );
        CREATE TABLE reporting_dates (
            ticker TEXT,
            report_date TEXT,
            report_type TEXT,
            source TEXT,
            fetched_at TEXT,
            source_url TEXT,
            PRIMARY KEY (ticker, report_date, report_type)
        );
    """)
    return conn


# ---------------------------------------------------------------------------
# B-096b: backfill_reporting_dates
# ---------------------------------------------------------------------------

class TestBackfillReportingDates(unittest.TestCase):

    def test_module_imports(self):
        """Module must import without error and expose key entry points."""
        import backfill_reporting_dates as brd
        self.assertTrue(hasattr(brd, 'run'))
        self.assertTrue(hasattr(brd, 'main'))
        # B-154: SEARCH_TYPES removed when scraper switched from per-type
        # search URL to per-company page (2026-06-08 rewrite).
        self.assertFalse(hasattr(brd, 'SEARCH_TYPES'))

    def test_per_company_api_attributes(self):
        """Post-rewrite (B-096b, 2026-06-08): per-company page approach helpers."""
        import backfill_reporting_dates as brd
        # Key helpers still present
        self.assertTrue(hasattr(brd, '_extract_dates_from_html'))
        self.assertTrue(hasattr(brd, '_parse_date'))
        self.assertTrue(hasattr(brd, '_upsert_reporting_dates'))
        self.assertTrue(hasattr(brd, 'distinct_stock_tickers'))
        self.assertTrue(hasattr(brd, 'CACHE_DIR'))

    def test_parse_date_valid(self):
        import backfill_reporting_dates as brd
        result = brd._parse_date("02", "Jun", "2026")
        self.assertEqual(result, "2026-06-02")

    def test_parse_date_invalid_month(self):
        import backfill_reporting_dates as brd
        result = brd._parse_date("01", "Xyz", "2026")
        self.assertIsNone(result)

    def test_parse_date_invalid_day(self):
        import backfill_reporting_dates as brd
        result = brd._parse_date("32", "Jan", "2026")
        self.assertIsNone(result)

    def test_extract_dates_from_html_empty(self):
        import backfill_reporting_dates as brd
        # B-154: API now takes 3 args (removed type_slug); uses per-company HTML.
        result = brd._extract_dates_from_html("", "TEST", date(2020, 1, 1))
        self.assertEqual(result, [])

    def test_extract_dates_from_html_matching_row(self):
        import backfill_reporting_dates as brd
        # Per-company page format: table.table-investegate with announcement-link.
        html = (
            '<table class="table-investegate"><tbody>'
            '<tr>'
            '<td>02 Jun 2026 07:09 AM</td>'
            '<td>RNS</td>'
            '<td>TestCo</td>'
            '<td><a class="announcement-link" '
            'href="/announcement/rns/company/prelim/12345678">'
            'Preliminary Results</a></td>'
            '</tr>'
            '</tbody></table>'
        )
        cutoff = date(2023, 1, 1)
        result = brd._extract_dates_from_html(html, "TEST", cutoff)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["report_date"], "2026-06-02")
        self.assertEqual(result[0]["report_type"], "PRELIM")
        self.assertEqual(result[0]["ticker"], "TEST")
        self.assertEqual(result[0]["source"], "investegate")

    def test_extract_dates_ignores_old_dates(self):
        import backfill_reporting_dates as brd
        html = (
            '<table class="table-investegate"><tbody>'
            '<tr>'
            '<td>15 Jan 2020 09:00 AM</td>'
            '<td>RNS</td>'
            '<td>TestCo</td>'
            '<td><a class="announcement-link" '
            'href="/announcement/rns/company/prelim/12345678">'
            'Preliminary Results</a></td>'
            '</tr>'
            '</tbody></table>'
        )
        # Cutoff is 2023 -- 2020 should be excluded
        cutoff = date(2023, 1, 1)
        result = brd._extract_dates_from_html(html, "TEST", cutoff)
        self.assertEqual(result, [])

    def test_extract_dates_no_duplicate_dates(self):
        import backfill_reporting_dates as brd
        html = (
            '<table class="table-investegate"><tbody>'
            '<tr>'
            '<td>02 Jun 2026 07:09 AM</td><td>RNS</td><td>Co</td>'
            '<td><a class="announcement-link" href="/rns/co/prelim/111">'
            'Preliminary Results</a></td></tr>'
            '<tr>'
            '<td>02 Jun 2026 08:00 AM</td><td>RNS</td><td>Co</td>'
            '<td><a class="announcement-link" href="/rns/co/prelim/222">'
            'Preliminary Results</a></td></tr>'
            '</tbody></table>'
        )
        cutoff = date(2020, 1, 1)
        result = brd._extract_dates_from_html(html, "TEST", cutoff)
        # Same date+type should only appear once
        self.assertEqual(len(result), 1)

    def test_distinct_stock_tickers(self):
        import backfill_reporting_dates as brd
        conn = _make_mem_db()
        conn.execute(
            "INSERT INTO transactions (fingerprint,date,ticker,type,value) "
            "VALUES ('fp1','2026-01-01','TEST','BUY',1000)"
        )
        conn.execute(
            "INSERT INTO transactions (fingerprint,date,ticker,type,value) "
            "VALUES ('fp2','2026-01-02','^FTAS','BUY',1000)"
        )
        conn.commit()
        tickers = brd.distinct_stock_tickers(conn)
        self.assertIn("TEST", tickers)
        self.assertNotIn("^FTAS", tickers)
        conn.close()

    def test_upsert_reporting_dates_inserts(self):
        import backfill_reporting_dates as brd
        conn = _make_mem_db()
        dates = [
            {"ticker": "TEST", "report_date": "2026-06-01",
             "report_type": "PRELIM", "source": "investegate"},
        ]
        n = brd._upsert_reporting_dates(conn, dates, now="2026-06-04T00:00:00Z")
        self.assertEqual(n, 1)
        conn.commit()
        row = conn.execute("SELECT * FROM reporting_dates").fetchone()
        self.assertEqual(row["report_type"], "PRELIM")
        conn.close()

    def test_upsert_reporting_dates_ignores_duplicate(self):
        import backfill_reporting_dates as brd
        conn = _make_mem_db()
        dates = [
            {"ticker": "TEST", "report_date": "2026-06-01",
             "report_type": "PRELIM", "source": "investegate"},
        ]
        brd._upsert_reporting_dates(conn, dates, now="2026-06-04T00:00:00Z")
        conn.commit()
        # Insert again -- should be ignored
        n2 = brd._upsert_reporting_dates(conn, dates, now="2026-06-04T00:00:00Z")
        self.assertEqual(n2, 0)
        conn.close()

    def test_cache_roundtrip(self):
        import backfill_reporting_dates as brd
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = brd.CACHE_DIR
            brd.CACHE_DIR = Path(tmpdir)
            try:
                payload = {"ticker": "T", "type_slug": "Preliminary+Results",
                           "fetched_at": "2026-06-04T00:00:00Z",
                           "extracted": [{"report_date": "2026-09-15"}]}
                brd._write_cache("T", "Preliminary+Results", payload)
                result = brd._read_cache("T", "Preliminary+Results")
                self.assertIsNotNone(result)
                self.assertEqual(result["extracted"][0]["report_date"], "2026-09-15")
            finally:
                brd.CACHE_DIR = orig_cache

    def test_upsert_reporting_dates_multiple_types(self):
        """Multiple report_types for same ticker should all insert."""
        import backfill_reporting_dates as brd
        conn = _make_mem_db()
        dates = [
            {"ticker": "TEST", "report_date": "2026-09-01",
             "report_type": "PRELIM", "source": "investegate"},
            {"ticker": "TEST", "report_date": "2026-03-15",
             "report_type": "INTERIM", "source": "investegate"},
            {"ticker": "TEST", "report_date": "2026-11-01",
             "report_type": "TRADING_STMT", "source": "investegate"},
        ]
        n = brd._upsert_reporting_dates(conn, dates, now="2026-06-04T00:00:00Z")
        self.assertEqual(n, 3)
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM reporting_dates WHERE ticker='TEST'"
        ).fetchone()[0]
        self.assertEqual(count, 3)
        conn.close()


# ---------------------------------------------------------------------------
# B-097b / B-101b: backfill_ticker_meta CSV fallback
# ---------------------------------------------------------------------------

class TestBackfillTickerMetaCSV(unittest.TestCase):

    def test_module_imports(self):
        import backfill_ticker_meta as btm
        self.assertTrue(hasattr(btm, '_load_csv_overrides'))
        self.assertTrue(hasattr(btm, '_upsert_meta'))

    def test_load_csv_overrides_with_valid_files(self):
        import backfill_ticker_meta as btm
        with tempfile.TemporaryDirectory() as tmpdir:
            mc_csv = Path(tmpdir) / "_company_market_caps.csv"
            web_csv = Path(tmpdir) / "_company_websites.csv"
            mc_csv.write_text("ticker,market_cap_gbp,notes\nTEST,1000000,Test\n",
                               encoding="utf-8")
            web_csv.write_text("ticker,website_url,notes\nTEST,https://test.com,T\n",
                                encoding="utf-8")
            orig_mc = btm._CSV_MARKET_CAPS
            orig_web = btm._CSV_WEBSITES
            btm._CSV_MARKET_CAPS = mc_csv
            btm._CSV_WEBSITES = web_csv
            try:
                result = btm._load_csv_overrides()
                self.assertIn("TEST", result)
                self.assertEqual(result["TEST"]["market_cap_gbp"], 1_000_000.0)
                self.assertEqual(result["TEST"]["website_url"], "https://test.com")
            finally:
                btm._CSV_MARKET_CAPS = orig_mc
                btm._CSV_WEBSITES = orig_web

    def test_load_csv_overrides_missing_files(self):
        """Missing CSVs must not raise -- return empty dict."""
        import backfill_ticker_meta as btm
        orig_mc = btm._CSV_MARKET_CAPS
        orig_web = btm._CSV_WEBSITES
        btm._CSV_MARKET_CAPS = Path("/nonexistent/market_caps.csv")
        btm._CSV_WEBSITES = Path("/nonexistent/websites.csv")
        try:
            result = btm._load_csv_overrides()
            self.assertEqual(result, {})
        finally:
            btm._CSV_MARKET_CAPS = orig_mc
            btm._CSV_WEBSITES = orig_web

    def test_upsert_meta_csv_market_cap_fallback(self):
        """When Yahoo returns None marketCap, CSV value is used."""
        import backfill_ticker_meta as btm
        conn = _make_mem_db()
        conn.execute(
            "INSERT INTO tickers_meta (ticker, is_aim, benchmark_symbol) "
            "VALUES ('TEST', 0, '^FTAS')"
        )
        conn.commit()
        enrichment = {"market_cap_gbp": None, "is_aim": 0,
                      "benchmark_symbol": None, "website_url": None,
                      "shares_outstanding": None}
        csv_override = {"market_cap_gbp": 5_000_000.0, "website_url": None}
        btm._upsert_meta(conn, "TEST", enrichment, now="2026-06-04T00:00:00Z",
                         csv_override=csv_override)
        conn.commit()
        row = conn.execute(
            "SELECT market_cap_gbp FROM tickers_meta WHERE ticker='TEST'"
        ).fetchone()
        self.assertEqual(row["market_cap_gbp"], 5_000_000.0)
        conn.close()

    def test_upsert_meta_website_url_set(self):
        """website_url is set from CSV when currently NULL."""
        import backfill_ticker_meta as btm
        conn = _make_mem_db()
        conn.execute(
            "INSERT INTO tickers_meta (ticker, is_aim, benchmark_symbol) "
            "VALUES ('TEST', 0, '^FTAS')"
        )
        conn.commit()
        enrichment = {"market_cap_gbp": None, "is_aim": 0,
                      "benchmark_symbol": None, "website_url": None,
                      "shares_outstanding": None}
        csv_override = {"market_cap_gbp": None, "website_url": "https://example.com"}
        btm._upsert_meta(conn, "TEST", enrichment, now="2026-06-04T00:00:00Z",
                         csv_override=csv_override)
        conn.commit()
        row = conn.execute(
            "SELECT website_url FROM tickers_meta WHERE ticker='TEST'"
        ).fetchone()
        self.assertEqual(row["website_url"], "https://example.com")
        conn.close()

    def test_upsert_meta_website_not_overwritten(self):
        """Existing website_url is not overwritten by CSV."""
        import backfill_ticker_meta as btm
        conn = _make_mem_db()
        conn.execute(
            "INSERT INTO tickers_meta (ticker, is_aim, benchmark_symbol, website_url) "
            "VALUES ('TEST', 0, '^FTAS', 'https://original.com')"
        )
        conn.commit()
        enrichment = {"market_cap_gbp": None, "is_aim": 0,
                      "benchmark_symbol": None, "website_url": None,
                      "shares_outstanding": None}
        csv_override = {"market_cap_gbp": None, "website_url": "https://csv.com"}
        btm._upsert_meta(conn, "TEST", enrichment, now="2026-06-04T00:00:00Z",
                         csv_override=csv_override)
        conn.commit()
        row = conn.execute(
            "SELECT website_url FROM tickers_meta WHERE ticker='TEST'"
        ).fetchone()
        self.assertEqual(row["website_url"], "https://original.com")
        conn.close()


# ---------------------------------------------------------------------------
# B-100: build_paper_book_summary
# ---------------------------------------------------------------------------

class TestPaperBookSummary(unittest.TestCase):

    def test_module_imports(self):
        import export_dashboard_json as edj
        self.assertTrue(hasattr(edj, 'build_paper_book_summary'))

    def test_empty_signals(self):
        import export_dashboard_json as edj
        conn = _make_mem_db()
        today = date(2026, 6, 4)
        result = edj.build_paper_book_summary(conn, today)
        self.assertIn("positions", result)
        self.assertIn("summary", result)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["summary"]["open_count"], 0)
        conn.close()

    def _setup_signal_with_price(self, conn, ticker="TEST", fp="fp001",
                                  signal_id="t3_ned_buy",
                                  fired_at="2026-06-01", value=10000.0,
                                  price_date="2026-06-01", close=100.0):
        conn.execute(
            "INSERT OR IGNORE INTO transactions "
            "(fingerprint,date,ticker,company,director,type,value) "
            "VALUES (?,?,?,?,?,?,?)",
            (fp, fired_at[:10], ticker, "Test Co", "J Smith", "BUY", value)
        )
        conn.execute(
            "INSERT OR IGNORE INTO signals (signal_id,signal_version,fingerprint,fired_at,confidence) "
            "VALUES (?,?,?,?,?)",
            (signal_id, "1.0.0", fp, fired_at, "med")
        )
        conn.execute(
            "INSERT OR IGNORE INTO prices (ticker,date,close,source,fetched_at) "
            "VALUES (?,?,?,?,?)",
            (ticker, price_date, close, "yahoo", "2026-06-04T00:00:00Z")
        )
        conn.commit()

    def test_open_position_appears(self):
        import export_dashboard_json as edj
        conn = _make_mem_db()
        # Fire signal today-5 days (within 21 day window -> OPEN)
        today = date(2026, 6, 4)
        fired = (today - timedelta(days=5)).isoformat()
        self._setup_signal_with_price(conn, fired_at=fired,
                                      price_date=fired, close=100.0)
        # Add a current price (later date)
        conn.execute(
            "INSERT INTO prices (ticker,date,close,source,fetched_at) "
            "VALUES ('TEST','2026-06-03',110.0,'yahoo','2026-06-04T00:00:00Z')"
        )
        conn.commit()
        result = edj.build_paper_book_summary(conn, today)
        self.assertEqual(result["summary"]["open_count"], 1)
        pos = result["positions"][0]
        self.assertEqual(pos["status"], "OPEN")
        self.assertEqual(pos["ticker"], "TEST")
        # MTM should be positive (110 > 100)
        self.assertIsNotNone(pos["mtm_pct"])
        self.assertGreater(pos["mtm_pct"], 0)
        conn.close()

    def test_closed_position_after_21_days(self):
        import export_dashboard_json as edj
        conn = _make_mem_db()
        today = date(2026, 6, 4)
        # Fire 45 days ago -> CLOSED (> _PAPER_BOOK_HOLD_DAYS=30 calendar days)
        # B-151: hold threshold changed from 21 to 30 calendar days.
        fired = (today - timedelta(days=45)).isoformat()
        self._setup_signal_with_price(conn, fired_at=fired,
                                      price_date=fired, close=100.0)
        result = edj.build_paper_book_summary(conn, today)
        self.assertEqual(result["summary"]["closed_count"], 1)
        self.assertEqual(result["summary"]["open_count"], 0)
        pos = result["positions"][0]
        self.assertEqual(pos["status"], "CLOSED")
        conn.close()

    def test_notional_conviction_sized(self):
        import export_dashboard_json as edj
        conn = _make_mem_db()
        today = date(2026, 6, 4)
        fired = (today - timedelta(days=5)).isoformat()
        # B-115 / spec 07: £500k buy log-scales to £1,000 (10x the £50k ref),
        # well under the £5k cap. (Was: flat-capped at £50k pre-B-115.)
        self._setup_signal_with_price(conn, fired_at=fired, value=500_000.0,
                                      price_date=fired, close=100.0)
        result = edj.build_paper_book_summary(conn, today)
        pos = result["positions"][0]
        self.assertEqual(pos["notional_gbp"], 1000.0)
        conn.close()

    def test_b2_excluded(self):
        """b2_crowded_cluster_kill should not appear in paper book."""
        import export_dashboard_json as edj
        conn = _make_mem_db()
        today = date(2026, 6, 4)
        fired = (today - timedelta(days=5)).isoformat()
        self._setup_signal_with_price(conn, fp="fp_b2",
                                      signal_id="b2_crowded_cluster_kill",
                                      fired_at=fired, price_date=fired)
        result = edj.build_paper_book_summary(conn, today)
        self.assertEqual(result["summary"]["open_count"], 0)
        conn.close()

    def test_dedup_highest_conviction_wins(self):
        """When multiple signals fire for same fingerprint, highest conviction wins."""
        import export_dashboard_json as edj
        conn = _make_mem_db()
        today = date(2026, 6, 4)
        fired = (today - timedelta(days=3)).isoformat()
        self._setup_signal_with_price(conn, fp="fp_dup",
                                      signal_id="t3_ned_buy",
                                      fired_at=fired, price_date=fired)
        # Add a higher-conviction signal for same fingerprint
        conn.execute(
            "INSERT INTO signals (signal_id,signal_version,fingerprint,fired_at,confidence) "
            "VALUES ('t1a_ceo_founder_buy','1.0.0','fp_dup',?,'high')",
            (fired,)
        )
        conn.commit()
        result = edj.build_paper_book_summary(conn, today)
        # Only one position for this fingerprint
        positions = [p for p in result["positions"] if p["fingerprint"] == "fp_dup"]
        self.assertEqual(len(positions), 1)
        # Should be t1a (higher conviction)
        self.assertEqual(positions[0]["signal_id"], "t1a")
        conn.close()

    def test_missing_price_no_mtm(self):
        """Position with no price entry should have mtm_pct=None."""
        import export_dashboard_json as edj
        conn = _make_mem_db()
        today = date(2026, 6, 4)
        fired = (today - timedelta(days=5)).isoformat()
        # Insert signal + transaction but NO price entry
        conn.execute(
            "INSERT INTO transactions (fingerprint,date,ticker,type,value) "
            "VALUES ('fp_np', ?, 'NOPX', 'BUY', 5000)",
            (fired[:10],)
        )
        conn.execute(
            "INSERT INTO signals (signal_id,signal_version,fingerprint,fired_at,confidence) "
            "VALUES ('t3_ned_buy','1.0.0','fp_np',?,'med')",
            (fired,)
        )
        conn.commit()
        result = edj.build_paper_book_summary(conn, today)
        nopx_pos = [p for p in result["positions"] if p["ticker"] == "NOPX"]
        self.assertEqual(len(nopx_pos), 1)
        self.assertIsNone(nopx_pos[0]["mtm_pct"])
        conn.close()


# ---------------------------------------------------------------------------
# B-078: build_cohort_table vw_mean_car field
# ---------------------------------------------------------------------------

class TestCohortTableVWMean(unittest.TestCase):

    def test_vw_mean_car_in_row(self):
        """build_cohort_table rows must include vw_mean_car field."""
        import export_dashboard_json as edj
        from datetime import date as _date

        # Minimal CSV rows with _car_t30 and _value_gbp
        rows = [
            {"_car_t30": 0.05, "_value_gbp": 10000.0, "_fired_at": "2026-01-01",
             "signal_id": "t3_ned_buy", "ticker": "AAA", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "fp1"},
            {"_car_t30": 0.10, "_value_gbp": 30000.0, "_fired_at": "2026-01-15",
             "signal_id": "t3_ned_buy", "ticker": "BBB", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "fp2"},
        ]
        today = _date(2026, 6, 4)
        result = edj.build_cohort_table(
            rows,
            group_fn=lambda r: "ned",
            label_fn=lambda k: "NED",
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        row_data = result["t30"]["all"]["rows"][0]
        self.assertIn("vw_mean_car", row_data)

    def test_vw_mean_car_weighted_correctly(self):
        """VW mean should weight larger transactions more heavily."""
        import export_dashboard_json as edj
        from datetime import date as _date

        # Two trades: small (£10k, +5%) and large (£90k, +15%)
        # EW mean = (5 + 15)/2 = 10%
        # VW mean = (10k*5% + 90k*15%) / 100k = (500 + 13500) / 100000 * 100 = 14%
        rows = [
            {"_car_t30": 0.05, "_value_gbp": 10000.0, "_fired_at": "2026-01-01",
             "signal_id": "t3_ned_buy", "ticker": "A", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "f1"},
            {"_car_t30": 0.15, "_value_gbp": 90000.0, "_fired_at": "2026-01-15",
             "signal_id": "t3_ned_buy", "ticker": "B", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "f2"},
        ]
        today = _date(2026, 6, 4)
        result = edj.build_cohort_table(
            rows,
            group_fn=lambda r: "ned",
            label_fn=lambda k: "NED",
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        row_data = result["t30"]["all"]["rows"][0]
        # VW mean should be ~14.0%
        self.assertIsNotNone(row_data["vw_mean_car"])
        self.assertAlmostEqual(row_data["vw_mean_car"], 14.0, places=0)
        # EW median_car should be the median of 5% and 15% = 10%
        self.assertAlmostEqual(row_data["median_car"], 10.0, places=0)

    def test_vw_mean_car_none_when_no_values(self):
        """vw_mean_car is None when value_gbp is missing for all rows."""
        import export_dashboard_json as edj
        from datetime import date as _date

        rows = [
            {"_car_t30": 0.05, "_value_gbp": None, "_fired_at": "2026-01-01",
             "signal_id": "t3_ned_buy", "ticker": "A", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "f1"},
        ]
        today = _date(2026, 6, 4)
        result = edj.build_cohort_table(
            rows,
            group_fn=lambda r: "ned",
            label_fn=lambda k: "NED",
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        row_data = result["t30"]["all"]["rows"][0]
        self.assertIsNone(row_data["vw_mean_car"])

    def test_existing_median_car_unchanged(self):
        """The existing median_car field must not be affected by VW addition."""
        import export_dashboard_json as edj
        from datetime import date as _date

        rows = [
            {"_car_t30": 0.03, "_value_gbp": 5000.0, "_fired_at": "2026-01-01",
             "signal_id": "t3_ned_buy", "ticker": "A", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "f1"},
            {"_car_t30": 0.07, "_value_gbp": 5000.0, "_fired_at": "2026-01-15",
             "signal_id": "t3_ned_buy", "ticker": "B", "role": "NED",
             "role_class": "T3", "role_normalized": "t3",
             "_car_t1": None, "_car_t90": None, "_car_t365": None,
             "_net_car_t30": None, "_bench_t30": None,
             "_net_car_t1": None, "_net_car_t90": None, "_net_car_t365": None,
             "_bench_t1": None, "_bench_t90": None, "_bench_t365": None,
             "fingerprint": "f2"},
        ]
        today = _date(2026, 6, 4)
        result = edj.build_cohort_table(
            rows,
            group_fn=lambda r: "ned",
            label_fn=lambda k: "NED",
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        row_data = result["t30"]["all"]["rows"][0]
        # Median of [3%, 7%] = 5.0%
        self.assertAlmostEqual(row_data["median_car"], 5.0, places=0)


# ---------------------------------------------------------------------------
# AST integrity checks for all modified files
# ---------------------------------------------------------------------------

class TestASTIntegrity(unittest.TestCase):

    def _check_file(self, rel_path):
        p = HERE.parent / rel_path if not rel_path.startswith('.scripts') else HERE / rel_path.split('/')[-1]
        # Try both paths
        candidates = [
            HERE / rel_path.split('/')[-1],
            HERE.parent / rel_path,
        ]
        for c in candidates:
            if c.exists():
                with open(c, 'rb') as f:
                    content = f.read()
                try:
                    ast.parse(content.decode('utf-8'))
                    return
                except SyntaxError as e:
                    self.fail(f"SyntaxError in {c}: {e}")
        self.fail(f"File not found: {rel_path}")

    def test_backfill_reporting_dates_ast(self):
        self._check_file("backfill_reporting_dates.py")

    def test_backfill_ticker_meta_ast(self):
        self._check_file("backfill_ticker_meta.py")

    def test_export_dashboard_json_ast(self):
        self._check_file("export_dashboard_json.py")

    def test_render_performance_ast(self):
        p = HERE / "dashboard" / "render_performance.py"
        self.assertTrue(p.exists(), f"Not found: {p}")
        with open(p, 'rb') as f:
            content = f.read()
        try:
            ast.parse(content.decode('utf-8'))
        except SyntaxError as e:
            self.fail(f"SyntaxError in render_performance.py: {e}")


# ---------------------------------------------------------------------------
# render_performance._paper_book_section smoke test
# ---------------------------------------------------------------------------

class TestPaperBookSection(unittest.TestCase):

    def test_empty_state_renders(self):
        """_paper_book_section with no data should render an empty state."""
        from dashboard.render_performance import _paper_book_section
        html = _paper_book_section({})
        self.assertIn("paper book", html.lower())
        self.assertIn("export_dashboard_json", html)

    def test_positions_render(self):
        """_paper_book_section with data should render positions table."""
        from dashboard.render_performance import _paper_book_section
        data = {
            "paper_book": {
                "positions": [
                    {
                        "signal_id": "t3",
                        "fingerprint": "abc123",
                        "ticker": "TEST",
                        "company": "Test Co",
                        "director": "J Smith",
                        "fired_at": "2026-06-01",
                        "entry_date": "2026-06-02",
                        "entry_close": 100.0,
                        "current_date": "2026-06-04",
                        "current_close": 105.0,
                        "notional_gbp": 10000.0,
                        "hold_days": 3,
                        "mtm_pct": 5.0,
                        "status": "OPEN",
                    }
                ],
                "summary": {
                    "open_count": 1,
                    "closed_count": 0,
                    "open_notional_gbp": 10000.0,
                    "open_mtm_pct_mean": 5.0,
                    "open_winners": 1,
                    "open_losers": 0,
                }
            }
        }
        html = _paper_book_section(data)
        self.assertIn("TEST", html)
        # Paper book only renders open positions — "OPEN" text appears as
        # "Open positions only" in the note footer, not as a status badge.
        self.assertIn("Open positions only", html)
        self.assertIn("+5.00%", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
