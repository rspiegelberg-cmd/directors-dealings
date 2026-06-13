"""Tests for backfill_market_cap.py (B-137 / B-147).

Covers:
  - parse_market_cap_gbp: billions, millions, plain (no suffix)
  - _detect_is_aim: AIM vs Main Market HTML fixtures (B-147)
  - parse_lse_page: happy path, missing meta, partial data, is_aim tuple (B-147)
  - fetch_lse_page: mocked requests.get, cache behaviour
  - DB update path: in-memory SQLite, no real DB touched
  - update_ticker with is_aim=True writes is_aim + benchmark_symbol (B-147)
"""
from __future__ import annotations

import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_market_cap as bmc


# ---------------------------------------------------------------------------
# parse_market_cap_gbp
# ---------------------------------------------------------------------------

class TestParseMarketCapGbp(unittest.TestCase):

    def test_billions_upper(self):
        result = bmc.parse_market_cap_gbp("1.23", "B")
        self.assertAlmostEqual(result, 1_230_000_000.0, places=0)

    def test_billions_lower(self):
        result = bmc.parse_market_cap_gbp("2.5", "b")
        self.assertAlmostEqual(result, 2_500_000_000.0, places=0)

    def test_millions_upper(self):
        result = bmc.parse_market_cap_gbp("456.7", "M")
        self.assertAlmostEqual(result, 456_700_000.0, places=0)

    def test_millions_lower(self):
        result = bmc.parse_market_cap_gbp("123.4", "m")
        self.assertAlmostEqual(result, 123_400_000.0, places=0)

    def test_no_suffix_raw_pounds(self):
        result = bmc.parse_market_cap_gbp("5000000", "")
        self.assertAlmostEqual(result, 5_000_000.0, places=0)

    def test_comma_in_value(self):
        result = bmc.parse_market_cap_gbp("1,234.5", "M")
        self.assertAlmostEqual(result, 1_234_500_000.0, places=0)

    def test_none_input(self):
        self.assertIsNone(bmc.parse_market_cap_gbp(None, "B"))

    def test_empty_string(self):
        self.assertIsNone(bmc.parse_market_cap_gbp("", "B"))

    def test_invalid_numeric(self):
        self.assertIsNone(bmc.parse_market_cap_gbp("n/a", "B"))


# ---------------------------------------------------------------------------
# parse_lse_page
# ---------------------------------------------------------------------------

# Minimal HTML fixtures that match the real lse.co.uk <th>/<td> table format.
# The page renders:
#   <th title="Shares in Issue for Co">Shares in Issue</th>
#   <td>1.55<strong>b</strong></td>
#   <th title="Market Capitalisation for Co">Market Cap</th>
#   <td>£180.5<strong>B</strong></td>

def _make_table_html(shares_cell: str, cap_cell: str, company: str = "TestCo") -> str:
    """Build a minimal LSE-style page with the two key table rows."""
    rows = ""
    if shares_cell is not None:
        rows += f"""
        <tr>
          <th title="Shares in Issue for {company}">Shares in Issue</th>
          <td>{shares_cell}</td>
        </tr>"""
    if cap_cell is not None:
        rows += f"""
        <tr>
          <th title="Market Capitalisation for {company}">Market Cap</th>
          <td>{cap_cell}</td>
        </tr>"""
    return f"<html><body><table>{rows}</table></body></html>"


# Fixtures used across multiple test classes
_FULL_HTML = _make_table_html(
    shares_cell="1.55<strong>b</strong>",
    cap_cell="£180.5<strong>B</strong>",
    company="AZN",
)

_SHARES_ONLY_HTML = _make_table_html(
    shares_cell="500<strong>m</strong>",
    cap_cell=None,
)

_CAP_ONLY_HTML = _make_table_html(
    shares_cell=None,
    cap_cell="£2.3<strong>b</strong>",
)

_NO_META_HTML = "<html><head><title>Not found</title></head><body></body></html>"

_LOWERCASE_M_HTML = _make_table_html(
    shares_cell="78.4<strong>m</strong>",
    cap_cell="£78.4<strong>m</strong>",
    company="TinyCo",
)


# ---------------------------------------------------------------------------
# AIM detection fixtures (B-147)
# ---------------------------------------------------------------------------

# AIM stock: paragraph links to ftse-aim-all-share
_AIM_DETAILS_PARAGRAPH = """
<p class="sp-share-details__text">Metals One is listed in the <a
  class="sp-share-details__link"
  href="https://www.lse.co.uk/share-prices/indices/ftse-aim-all-share/"
  >FTSE AIM All-Share</a> index.</p>
"""

# Main Market stock: paragraph links to ftse-all-share / ftse-100 (not AIM)
_MAIN_MARKET_DETAILS_PARAGRAPH = """
<p class="sp-share-details__text">Airtel Africa is listed in the <a
  class="sp-share-details__link"
  href="https://www.lse.co.uk/share-prices/indices/ftse-all-share/"
  >FTSE All-Share</a>, <a
  class="sp-share-details__link"
  href="https://www.lse.co.uk/share-prices/indices/ftse-100/"
  >FTSE 100</a> indices.</p>
"""

_AIM_PAGE_HTML = (
    _make_table_html("1.13<strong>b</strong>", "£14.57<strong>m</strong>", "MetalsOne")
    .replace("</body>", _AIM_DETAILS_PARAGRAPH + "</body>")
)

_MAIN_MARKET_PAGE_HTML = (
    _make_table_html("3.65<strong>b</strong>", "£12.39<strong>b</strong>", "AirtelAfrica")
    .replace("</body>", _MAIN_MARKET_DETAILS_PARAGRAPH + "</body>")
)

_NO_DETAILS_PAGE_HTML = _make_table_html(
    "500<strong>m</strong>", "£1.2<strong>b</strong>", "UnknownCo"
)  # no sp-share-details__text paragraph at all


class TestDetectIsAim(unittest.TestCase):
    """Unit tests for _detect_is_aim() -- B-147."""

    def _soup(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")

    def test_aim_page_returns_true(self):
        """AIM index-membership paragraph -> True."""
        soup = self._soup(_AIM_PAGE_HTML)
        self.assertTrue(bmc._detect_is_aim(soup))

    def test_main_market_returns_false(self):
        """Main Market paragraph (FTSE All-Share / FTSE 100) -> False."""
        soup = self._soup(_MAIN_MARKET_PAGE_HTML)
        self.assertFalse(bmc._detect_is_aim(soup))

    def test_no_paragraph_returns_false(self):
        """No sp-share-details__text paragraph -> False."""
        soup = self._soup(_NO_DETAILS_PAGE_HTML)
        self.assertFalse(bmc._detect_is_aim(soup))

    def test_empty_html_returns_false(self):
        soup = self._soup("")
        self.assertFalse(bmc._detect_is_aim(soup))

    def test_href_case_insensitive(self):
        """Detection is case-insensitive on the href."""
        html = """<p class="sp-share-details__text">
          <a href="https://www.lse.co.uk/share-prices/indices/FTSE-AIM-All-Share/">AIM</a>
        </p>"""
        soup = self._soup(html)
        self.assertTrue(bmc._detect_is_aim(soup))


# ---------------------------------------------------------------------------
# parse_lse_page (updated for 3-tuple return -- B-147)
# ---------------------------------------------------------------------------

class TestParseLsePage(unittest.TestCase):

    def test_full_data(self):
        shares, cap, is_aim = bmc.parse_lse_page(_FULL_HTML)
        # 1.55b -> 1_550_000_000 (integer estimate)
        self.assertEqual(shares, 1_550_000_000)
        self.assertAlmostEqual(cap, 180_500_000_000.0, places=0)
        self.assertFalse(is_aim)

    def test_shares_only(self):
        shares, cap, is_aim = bmc.parse_lse_page(_SHARES_ONLY_HTML)
        self.assertEqual(shares, 500_000_000)
        self.assertIsNone(cap)
        self.assertFalse(is_aim)

    def test_cap_only(self):
        shares, cap, is_aim = bmc.parse_lse_page(_CAP_ONLY_HTML)
        self.assertIsNone(shares)
        self.assertAlmostEqual(cap, 2_300_000_000.0, places=0)
        self.assertFalse(is_aim)

    def test_no_table_rows(self):
        shares, cap, is_aim = bmc.parse_lse_page(_NO_META_HTML)
        self.assertIsNone(shares)
        self.assertIsNone(cap)
        self.assertFalse(is_aim)

    def test_empty_html(self):
        shares, cap, is_aim = bmc.parse_lse_page("")
        self.assertIsNone(shares)
        self.assertIsNone(cap)
        self.assertFalse(is_aim)

    def test_none_input(self):
        shares, cap, is_aim = bmc.parse_lse_page(None)
        self.assertIsNone(shares)
        self.assertIsNone(cap)
        self.assertFalse(is_aim)

    def test_lowercase_m_cap(self):
        shares, cap, is_aim = bmc.parse_lse_page(_LOWERCASE_M_HTML)
        self.assertEqual(shares, 78_400_000)
        self.assertAlmostEqual(cap, 78_400_000.0, places=0)
        self.assertFalse(is_aim)

    def test_aim_page_is_aim_true(self):
        """AIM page sets is_aim=True in 3-tuple return."""
        shares, cap, is_aim = bmc.parse_lse_page(_AIM_PAGE_HTML)
        self.assertEqual(shares, 1_130_000_000)
        self.assertAlmostEqual(cap, 14_570_000.0, places=0)
        self.assertTrue(is_aim)

    def test_main_market_page_is_aim_false(self):
        """Main Market page sets is_aim=False in 3-tuple return."""
        shares, cap, is_aim = bmc.parse_lse_page(_MAIN_MARKET_PAGE_HTML)
        self.assertEqual(shares, 3_650_000_000)
        self.assertAlmostEqual(cap, 12_390_000_000.0, places=0)
        self.assertFalse(is_aim)

    def test_real_azn_pattern(self):
        """Test against pattern exactly as seen on lse.co.uk for AZN."""
        html = _make_table_html(
            shares_cell="1.55<strong>b</strong>",
            cap_cell="£214.92<strong>b</strong>",
            company="Astrazeneca",
        )
        shares, cap, is_aim = bmc.parse_lse_page(html)
        self.assertEqual(shares, 1_550_000_000)
        self.assertAlmostEqual(cap, 214_920_000_000.0, places=0)
        self.assertFalse(is_aim)

    def test_real_lloy_pattern(self):
        """Test against pattern exactly as seen on lse.co.uk for LLOY (large share count)."""
        html = _make_table_html(
            shares_cell="58.27<strong>b</strong>",
            cap_cell="£57.78<strong>b</strong>",
            company="Lloyds",
        )
        shares, cap, is_aim = bmc.parse_lse_page(html)
        self.assertEqual(shares, 58_270_000_000)
        self.assertAlmostEqual(cap, 57_780_000_000.0, places=0)
        self.assertFalse(is_aim)


# ---------------------------------------------------------------------------
# fetch_lse_page (network mocked)
# ---------------------------------------------------------------------------

class TestFetchLsePage(unittest.TestCase):

    def test_fetches_and_caches(self):
        """fetch_lse_page calls requests.get and writes to cache."""
        fake_html = _FULL_HTML

        fake_resp = MagicMock()
        fake_resp.text = fake_html
        fake_resp.raise_for_status = MagicMock()

        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                with patch("requests.get", return_value=fake_resp) as mock_get:
                    with patch("time.sleep"):  # skip delays in tests
                        result = bmc.fetch_lse_page("AZN", force=True)
                self.assertEqual(result, fake_html)
                mock_get.assert_called_once()
                # Cache file should now exist
                cache_file = bmc._cache_path("AZN")
                self.assertTrue(cache_file.exists(), "Cache file should be written")
            finally:
                bmc.CACHE_DIR = orig_cache

    def test_uses_cache_within_ttl(self):
        """fetch_lse_page reads from cache when TTL has not expired."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                # Pre-populate cache
                cache_file = bmc._cache_path("HSBA")
                bmc.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(_FULL_HTML, encoding="utf-8")
                # Touch to set mtime to now (within TTL)
                cache_file.touch()

                with patch("requests.get") as mock_get:
                    result = bmc.fetch_lse_page("HSBA", force=False)
                mock_get.assert_not_called()
                self.assertEqual(result, _FULL_HTML)
            finally:
                bmc.CACHE_DIR = orig_cache

    def test_retries_on_failure(self):
        """fetch_lse_page raises RuntimeError after MAX_RETRIES failures."""
        import requests as req_lib
        orig_cache = bmc.CACHE_DIR
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                with patch("requests.get",
                           side_effect=req_lib.exceptions.ConnectionError("down")):
                    with patch("time.sleep"):
                        with self.assertRaises(RuntimeError):
                            bmc.fetch_lse_page("FAIL", force=True)
            finally:
                bmc.CACHE_DIR = orig_cache


# ---------------------------------------------------------------------------
# DB update path (in-memory SQLite)
# ---------------------------------------------------------------------------

def _make_mem_db() -> sqlite3.Connection:
    """Create an in-memory DB with a minimal tickers_meta table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tickers_meta ("
        "  ticker TEXT PRIMARY KEY,"
        "  sector TEXT,"
        "  is_excluded_issuer INTEGER NOT NULL DEFAULT 0,"
        "  shares_outstanding REAL,"
        "  market_cap_gbp REAL,"
        "  is_aim INTEGER NOT NULL DEFAULT 0,"
        "  benchmark_symbol TEXT NOT NULL DEFAULT '^FTAS',"
        "  updated_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z'"
        ")"
    )
    conn.executemany(
        "INSERT INTO tickers_meta (ticker) VALUES (?)",
        [("AZN",), ("HSBA",), ("LLOY",), ("MET1",)],
    )
    conn.commit()
    return conn


class TestDbUpdate(unittest.TestCase):

    def test_update_ticker_writes_both_columns(self):
        conn = _make_mem_db()
        bmc.update_ticker(conn, "AZN", 1_573_124_000, 180_500_000_000.0)
        row = conn.execute(
            "SELECT shares_outstanding, market_cap_gbp, is_aim, benchmark_symbol "
            "FROM tickers_meta WHERE ticker='AZN'"
        ).fetchone()
        self.assertEqual(row["shares_outstanding"], 1_573_124_000)
        self.assertAlmostEqual(row["market_cap_gbp"], 180_500_000_000.0, places=0)
        # is_aim not set -> stays at default 0
        self.assertEqual(row["is_aim"], 0)
        self.assertEqual(row["benchmark_symbol"], "^FTAS")
        conn.close()

    def test_update_ticker_accepts_none(self):
        conn = _make_mem_db()
        bmc.update_ticker(conn, "HSBA", None, None)
        row = conn.execute(
            "SELECT shares_outstanding, market_cap_gbp FROM tickers_meta "
            "WHERE ticker='HSBA'"
        ).fetchone()
        self.assertIsNone(row["shares_outstanding"])
        self.assertIsNone(row["market_cap_gbp"])
        conn.close()

    def test_update_ticker_aim_sets_is_aim_and_benchmark(self):
        """is_aim=True writes is_aim=1 and benchmark_symbol='^FTSC' (B-147)."""
        conn = _make_mem_db()
        bmc.update_ticker(conn, "MET1", 1_130_000_000, 14_570_000.0, is_aim=True)
        row = conn.execute(
            "SELECT shares_outstanding, market_cap_gbp, is_aim, benchmark_symbol "
            "FROM tickers_meta WHERE ticker='MET1'"
        ).fetchone()
        self.assertEqual(row["shares_outstanding"], 1_130_000_000)
        self.assertAlmostEqual(row["market_cap_gbp"], 14_570_000.0, places=0)
        self.assertEqual(row["is_aim"], 1)
        self.assertEqual(row["benchmark_symbol"], "^FTSC")
        conn.close()

    def test_update_ticker_overwrites_is_aim_with_lse_authoritative(self):
        """update_ticker always writes LSE-authoritative is_aim (B-147 / B-154).

        Protection against stale data is at the run() level (skip on fetch
        error), not inside update_ticker itself. A successful page parse with
        is_aim=False is authoritative and overwrites the existing flag.
        Benchmark is still ^FTSC here because market_cap=15M < £300M small-cap.
        """
        conn = _make_mem_db()
        conn.execute(
            "UPDATE tickers_meta SET is_aim=1, benchmark_symbol='^FTSC' WHERE ticker='MET1'"
        )
        conn.commit()
        bmc.update_ticker(conn, "MET1", 1_200_000_000, 15_000_000.0, is_aim=False)
        row = conn.execute(
            "SELECT is_aim, benchmark_symbol FROM tickers_meta WHERE ticker='MET1'"
        ).fetchone()
        # LSE page returned is_aim=False -> flag is overwritten to 0
        self.assertEqual(row["is_aim"], 0)
        # benchmark stays ^FTSC because market_cap=15M < £300M (small-cap)
        self.assertEqual(row["benchmark_symbol"], "^FTSC")
        conn.close()

    def test_load_tickers_all(self):
        conn = _make_mem_db()
        tickers = bmc.load_tickers(conn)
        self.assertIn("AZN", tickers)
        self.assertIn("HSBA", tickers)
        self.assertIn("LLOY", tickers)
        conn.close()

    def test_load_tickers_override(self):
        conn = _make_mem_db()
        tickers = bmc.load_tickers(conn, ["AZN", "LLOY"])
        self.assertEqual(sorted(tickers), ["AZN", "LLOY"])
        conn.close()

    def test_load_tickers_excludes_investment_trusts(self):
        conn = _make_mem_db()
        conn.execute(
            "UPDATE tickers_meta SET is_excluded_issuer=1 WHERE ticker='LLOY'"
        )
        conn.commit()
        tickers = bmc.load_tickers(conn)
        self.assertNotIn("LLOY", tickers)
        conn.close()

    def test_run_dry_run_does_not_write(self):
        """dry_run=True: parse happens, DB stays unchanged."""
        conn = _make_mem_db()
        fake_resp = MagicMock()
        fake_resp.text = _FULL_HTML
        fake_resp.raise_for_status = MagicMock()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                with patch("requests.get", return_value=fake_resp):
                    with patch("time.sleep"):
                        stats = bmc.run(
                            tickers_override=["AZN"],
                            dry_run=True,
                            conn=conn,
                        )
            finally:
                bmc.CACHE_DIR = orig_cache

        # Dry run: DB should not be updated
        row = conn.execute(
            "SELECT shares_outstanding FROM tickers_meta WHERE ticker='AZN'"
        ).fetchone()
        self.assertIsNone(row["shares_outstanding"],
                          "dry_run should not write to DB")
        # _FULL_HTML has 1.55b shares + 180.5B cap -> both found, so shares_populated=1
        self.assertEqual(stats["scraped"], 1)
        conn.close()

    def test_run_live_writes_db(self):
        """run() without dry_run writes parsed values to the DB."""
        conn = _make_mem_db()
        fake_resp = MagicMock()
        fake_resp.text = _FULL_HTML
        fake_resp.raise_for_status = MagicMock()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                with patch("requests.get", return_value=fake_resp):
                    with patch("time.sleep"):
                        stats = bmc.run(
                            tickers_override=["AZN"],
                            dry_run=False,
                            conn=conn,
                        )
            finally:
                bmc.CACHE_DIR = orig_cache

        row = conn.execute(
            "SELECT shares_outstanding, market_cap_gbp "
            "FROM tickers_meta WHERE ticker='AZN'"
        ).fetchone()
        # _FULL_HTML: 1.55b shares -> 1_550_000_000; £180.5B cap -> 180_500_000_000
        self.assertEqual(row["shares_outstanding"], 1_550_000_000)
        self.assertAlmostEqual(row["market_cap_gbp"], 180_500_000_000.0, places=0)
        self.assertEqual(stats["shares_populated"], 1)
        self.assertEqual(stats["cap_populated"], 1)
        conn.close()

    def test_run_aim_page_writes_aim_columns(self):
        """run() on an AIM page sets is_aim=1 and benchmark_symbol='^FTSC' (B-147)."""
        conn = _make_mem_db()
        fake_resp = MagicMock()
        fake_resp.text = _AIM_PAGE_HTML   # fixture defined above
        fake_resp.raise_for_status = MagicMock()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                with patch("requests.get", return_value=fake_resp):
                    with patch("time.sleep"):
                        stats = bmc.run(
                            tickers_override=["MET1"],
                            dry_run=False,
                            conn=conn,
                        )
            finally:
                bmc.CACHE_DIR = orig_cache

        row = conn.execute(
            "SELECT is_aim, benchmark_symbol FROM tickers_meta WHERE ticker='MET1'"
        ).fetchone()
        self.assertEqual(row["is_aim"], 1)
        self.assertEqual(row["benchmark_symbol"], "^FTSC")
        self.assertEqual(stats["aim_detected"], 1)
        conn.close()


# ---------------------------------------------------------------------------
# Slug-variant retry (B-169)
# ---------------------------------------------------------------------------

def _make_404_response():
    """Build a mock requests response whose raise_for_status raises a 404
    HTTPError carrying response.status_code = 404 (as real requests does)."""
    import requests as req_lib
    resp404 = MagicMock()
    resp404.status_code = 404
    err = req_lib.exceptions.HTTPError("404 Client Error", response=resp404)
    failing = MagicMock()
    failing.raise_for_status.side_effect = err
    failing.text = ""
    return failing


def _make_ok_response(html: str):
    ok = MagicMock()
    ok.text = html
    ok.raise_for_status = MagicMock()
    return ok


class TestSlugVariant(unittest.TestCase):
    """_slug_variant pure helper -- B-169."""

    def test_strips_trailing_dot(self):
        self.assertEqual(bmc._slug_variant("TW."), "TW")

    def test_appends_dot(self):
        self.assertEqual(bmc._slug_variant("NYCE"), "NYCE.")

    def test_single_char(self):
        self.assertEqual(bmc._slug_variant("X"), "X.")
        self.assertEqual(bmc._slug_variant("X."), "X")


class TestSlugRetry(unittest.TestCase):
    """fetch_lse_page 404 fast-fail + fetch_lse_page_with_variants -- B-169."""

    def test_404_fast_fails_single_attempt(self):
        """A 404 must not burn MAX_RETRIES back-off attempts on the same slug."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                with patch("requests.get",
                           return_value=_make_404_response()) as mock_get:
                    with patch("time.sleep"):
                        with self.assertRaises(RuntimeError):
                            bmc.fetch_lse_page("DEAD.", force=True)
                self.assertEqual(mock_get.call_count, 1,
                                 "404 should fail fast after one attempt")
            finally:
                bmc.CACHE_DIR = orig_cache

    def test_variant_succeeds_after_404_strip_dot(self):
        """First call 404s on 'TW.', dot-stripped variant 'TW' succeeds."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                side = [_make_404_response(), _make_ok_response(_FULL_HTML)]
                with patch("requests.get", side_effect=side) as mock_get:
                    with patch("time.sleep"):
                        html, slug = bmc.fetch_lse_page_with_variants(
                            "TW.", force=True)
                self.assertEqual(html, _FULL_HTML)
                self.assertEqual(slug, "TW")
                self.assertEqual(mock_get.call_count, 2)
                second_url = mock_get.call_args_list[1][0][0]
                self.assertTrue(second_url.endswith("shareprice=TW"),
                                f"variant URL wrong: {second_url}")
            finally:
                bmc.CACHE_DIR = orig_cache

    def test_variant_succeeds_after_404_append_dot(self):
        """First call 404s on 'NYCE', dot-appended variant 'NYCE.' succeeds."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                side = [_make_404_response(), _make_ok_response(_FULL_HTML)]
                with patch("requests.get", side_effect=side) as mock_get:
                    with patch("time.sleep"):
                        html, slug = bmc.fetch_lse_page_with_variants(
                            "NYCE", force=True)
                self.assertEqual(slug, "NYCE.")
                second_url = mock_get.call_args_list[1][0][0]
                self.assertTrue(second_url.endswith("shareprice=NYCE."),
                                f"variant URL wrong: {second_url}")
            finally:
                bmc.CACHE_DIR = orig_cache

    def test_both_slugs_fail_raises(self):
        """404 on both canonical and variant slug -> RuntimeError, 2 attempts."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                side = [_make_404_response(), _make_404_response()]
                with patch("requests.get", side_effect=side) as mock_get:
                    with patch("time.sleep"):
                        with self.assertRaises(RuntimeError):
                            bmc.fetch_lse_page_with_variants("QQ.", force=True)
                self.assertEqual(mock_get.call_count, 2,
                                 "exactly 2 slug attempts, no more")
            finally:
                bmc.CACHE_DIR = orig_cache

    def test_run_404_then_variant_writes_db(self):
        """run(): canonical slug 404s, variant page parses -> DB row updated
        under the CANONICAL ticker."""
        conn = _make_mem_db()
        conn.execute("INSERT INTO tickers_meta (ticker) VALUES ('TW.')")
        conn.commit()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                side = [_make_404_response(), _make_ok_response(_FULL_HTML)]
                with patch("requests.get", side_effect=side):
                    with patch("time.sleep"):
                        stats = bmc.run(tickers_override=["TW."],
                                        dry_run=False, conn=conn)
            finally:
                bmc.CACHE_DIR = orig_cache

        row = conn.execute(
            "SELECT shares_outstanding, market_cap_gbp "
            "FROM tickers_meta WHERE ticker='TW.'"
        ).fetchone()
        self.assertEqual(row["shares_outstanding"], 1_550_000_000)
        self.assertAlmostEqual(row["market_cap_gbp"], 180_500_000_000.0,
                               places=0)
        self.assertEqual(stats["cap_populated"], 1)
        self.assertEqual(stats["missing"], 0)
        conn.close()

    def test_run_empty_page_then_variant_writes_db(self):
        """run(): canonical slug returns a 200 page with no data table,
        the dot-variant page has the data -> values written."""
        conn = _make_mem_db()
        conn.execute("INSERT INTO tickers_meta (ticker) VALUES ('UU.')")
        conn.commit()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                side = [_make_ok_response(_NO_META_HTML),
                        _make_ok_response(_FULL_HTML)]
                with patch("requests.get", side_effect=side) as mock_get:
                    with patch("time.sleep"):
                        stats = bmc.run(tickers_override=["UU."],
                                        dry_run=False, conn=conn)
                self.assertEqual(mock_get.call_count, 2)
            finally:
                bmc.CACHE_DIR = orig_cache

        row = conn.execute(
            "SELECT market_cap_gbp FROM tickers_meta WHERE ticker='UU.'"
        ).fetchone()
        self.assertAlmostEqual(row["market_cap_gbp"], 180_500_000_000.0,
                               places=0)
        self.assertEqual(stats["missing"], 0)
        conn.close()

    def test_run_empty_page_variant_also_empty_is_miss(self):
        """Both slugs return data-less pages -> counted as missing, no write."""
        conn = _make_mem_db()
        conn.execute("INSERT INTO tickers_meta (ticker) VALUES ('ZNT')")
        conn.commit()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cache = bmc.CACHE_DIR
            bmc.CACHE_DIR = Path(tmpdir)
            try:
                side = [_make_ok_response(_NO_META_HTML),
                        _make_ok_response(_NO_META_HTML)]
                with patch("requests.get", side_effect=side) as mock_get:
                    with patch("time.sleep"):
                        stats = bmc.run(tickers_override=["ZNT"],
                                        dry_run=False, conn=conn)
                self.assertEqual(mock_get.call_count, 2,
                                 "cap at 2 slug attempts")
            finally:
                bmc.CACHE_DIR = orig_cache

        self.assertEqual(stats["missing"], 1)
        row = conn.execute(
            "SELECT market_cap_gbp FROM tickers_meta WHERE ticker='ZNT'"
        ).fetchone()
        self.assertIsNone(row["market_cap_gbp"])
        conn.close()


# ---------------------------------------------------------------------------
# B-169 CSV seed rows load through the CSV-fallback path
# ---------------------------------------------------------------------------

_B169_SEED_CSV = """ticker,market_cap_gbp,notes
AGR,1790000000,Assura plc - delisted Oct 2025 (PHP takeover; offer valued GBP1.79B) - B-169 seed
JUST,2400000000,Just Group plc - delisted Apr 2026 (Brookfield take-private at 219.16p; GBP2.4B) - B-169 seed
LIFS,850000,LifeSafe Holdings plc (AIM micro-cap) - approx Dec 2025 (1.8p x ~46.8m shares) - B-169 seed
PHLL,3500000000,Petershill Partners plc - delisted Dec 2025 (USD4.5B equity return; approx GBP3.5B) - B-169 seed
SOLG,842000000,SolGold plc - delisted Mar 2026 (Jiangxi Copper takeover; 3.01b shares at 27.9p) - B-169 seed
SXS,4200000000,Spectris plc - delisted Dec 2025 (KKR take-private; equity GBP4.2B) - B-169 seed
ULTP,40000000,Ultimate Products plc - approx Jun 2026 (83.4m shares at ~48p) - B-169 seed
"""

_B169_EXPECTED = {
    "AGR":  1_790_000_000.0,
    "JUST": 2_400_000_000.0,
    "LIFS": 850_000.0,
    "PHLL": 3_500_000_000.0,
    "SOLG": 842_000_000.0,
    "SXS":  4_200_000_000.0,
    "ULTP": 40_000_000.0,
}


class TestB169CsvSeedRows(unittest.TestCase):
    """The B-169 seed rows parse through backfill_ticker_meta's CSV-fallback
    loader (_load_csv_overrides) with correct float values."""

    def test_seed_rows_load_via_csv_fallback(self):
        import tempfile
        import backfill_ticker_meta as bmt

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "_company_market_caps.csv"
            csv_path.write_text(_B169_SEED_CSV, encoding="utf-8")
            orig_caps = bmt._CSV_MARKET_CAPS
            orig_webs = bmt._CSV_WEBSITES
            bmt._CSV_MARKET_CAPS = csv_path
            bmt._CSV_WEBSITES = Path(tmpdir) / "_nonexistent.csv"
            try:
                overrides = bmt._load_csv_overrides()
            finally:
                bmt._CSV_MARKET_CAPS = orig_caps
                bmt._CSV_WEBSITES = orig_webs

        for ticker, expected in _B169_EXPECTED.items():
            self.assertIn(ticker, overrides,
                          f"{ticker} missing from CSV overrides")
            self.assertAlmostEqual(
                overrides[ticker]["market_cap_gbp"], expected, places=0,
                msg=f"{ticker} market_cap_gbp wrong")

    def test_real_csv_contains_seed_rows(self):
        """The real .data/_company_market_caps.csv carries the B-169 rows
        (skipped when running outside the project tree, e.g. /tmp mirror)."""
        import backfill_ticker_meta as bmt
        if not bmt._CSV_MARKET_CAPS.exists():
            self.skipTest("real _company_market_caps.csv not present")
        overrides = bmt._load_csv_overrides()
        for ticker, expected in _B169_EXPECTED.items():
            self.assertIn(ticker, overrides)
            self.assertAlmostEqual(
                overrides[ticker]["market_cap_gbp"], expected, places=0)


if __name__ == "__main__":
    unittest.main()
