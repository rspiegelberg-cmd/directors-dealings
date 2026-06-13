"""Sprint 25 Phase 0 — PDMR review surface tests.

All tests run in Claude's Linux sandbox (read-only DB access via /tmp copy).
No Zone-B writes are made.

Coverage:
  A. export_dashboard_json — new helper functions
     A1. _rns_id_from_url: URL → rns_id extraction
     A2. build_pending_review_export: shape / bucket counts / item fields
     A3. build_tx_index: shape / field presence
  B. server.py new routes — static shape checks (no live Flask needed)
     B1. /api/rns-html/<rns_id>  — rns_id validation regex
     B2. /api/tx/<fingerprint>   — fingerprint validation regex
  C. render_company.py
     C1. Pencil icon link present in rendered transaction row HTML
     C2. Review <th> column header present
"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE   = Path(__file__).resolve().parent
REPO   = HERE.parent
OUTPUT = REPO / "outputs" / "data"

# Add .scripts to path so we can import the modules
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


# ── A. export_dashboard_json helpers ──────────────────────────────────────────

class TestRnsIdFromUrl(unittest.TestCase):
    """A1. _rns_id_from_url extracts the RNS ID from an Investegate URL."""

    def _fn(self, url):
        from export_dashboard_json import _rns_id_from_url
        return _rns_id_from_url(url)

    def test_standard_url(self):
        url = "https://www.investegate.co.uk/announcement/rns/card-factory/pdmr-dealing/9564925"
        self.assertEqual(self._fn(url), "9564925")

    def test_trailing_slash(self):
        url = "https://www.investegate.co.uk/announcement/rns/abc/pdmr-dealing/9564925/"
        self.assertEqual(self._fn(url), "9564925")

    def test_empty_string(self):
        self.assertEqual(self._fn(""), "")

    def test_non_numeric_tail(self):
        url = "https://example.com/something"
        self.assertEqual(self._fn(url), "")

    def test_short_id_invalid(self):
        # Less than 5 digits is not a valid RNS ID
        url = "https://example.com/1234"
        self.assertEqual(self._fn(url), "")

    def test_valid_5_digit(self):
        url = "https://example.com/12345"
        self.assertEqual(self._fn(url), "12345")


class TestBuildPendingReviewExport(unittest.TestCase):
    """A2. build_pending_review_export produces correct shape."""

    def _build(self, items_dict, cache_dir=None):
        from export_dashboard_json import build_pending_review_export, DEFAULT_PENDING_PATH
        import tempfile, os

        # Write a minimal pending JSON to a temp file
        pending = {
            "generated_at": "2026-06-03T00:00:00Z",
            "count": len(items_dict),
            "items": items_dict,
        }
        tmp = Path(tempfile.mktemp(suffix=".json"))
        tmp.write_text(json.dumps(pending), encoding="utf-8")

        # Use a temp dir as scrape_cache_dir (empty — no cached files)
        with tempfile.TemporaryDirectory() as td:
            result = build_pending_review_export(
                pending_path=tmp,
                scrape_cache_dir=Path(td),
                generated_at="2026-06-03T00:00:00Z",
            )
        tmp.unlink(missing_ok=True)
        return result

    def _make_item(self, rns_id, warnings=None, extracted=None):
        return {
            "url": f"https://www.investegate.co.uk/announcement/rns/abc/pdmr/dealing/{rns_id}",
            "headline": "PDMR Dealing",
            "warnings": warnings or [],
            "extracted": extracted or [],
            "parser_source": "regex",
            "used_llm": False,
        }

    def test_top_level_keys(self):
        result = self._build({"9000001": self._make_item("9000001")})
        for key in ("generated_at", "total", "buckets", "items"):
            self.assertIn(key, result, f"missing key: {key}")

    def test_total_matches_items(self):
        items = {
            "9000001": self._make_item("9000001"),
            "9000002": self._make_item("9000002"),
        }
        result = self._build(items)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 2)

    def test_item_fields(self):
        result = self._build({"9000001": self._make_item("9000001",
            warnings=["could_not_classify_type"])})
        item = result["items"][0]
        for field in ("rns_id", "url", "headline", "bucket", "warnings",
                      "extracted_count", "has_cache", "parser_source", "used_llm"):
            self.assertIn(field, item, f"item missing field: {field}")

    def test_rns_id_extracted(self):
        result = self._build({"9564925": self._make_item("9564925")})
        self.assertEqual(result["items"][0]["rns_id"], "9564925")

    def test_bucket_classification_could_not_classify(self):
        result = self._build({"9000001": self._make_item("9000001",
            warnings=["could_not_classify_type"])})
        self.assertEqual(result["items"][0]["bucket"], "could_not_classify")

    def test_bucket_classification_bundled(self):
        result = self._build({"9000001": self._make_item("9000001",
            warnings=["bundled multi-PDMR filing — names not extractable from boilerplate"])})
        self.assertEqual(result["items"][0]["bucket"], "bundled_multi_pdmr")

    def test_recoverable_sorted_before_hopeless(self):
        items = {
            "9000001": self._make_item("9000001",
                warnings=["bundled multi-PDMR filing — names not extractable"]),
            "9000002": self._make_item("9000002",
                warnings=["could_not_classify_type"]),
        }
        result = self._build(items)
        buckets = [it["bucket"] for it in result["items"]]
        # could_not_classify should appear before bundled_multi_pdmr
        idx_class   = next((i for i, b in enumerate(buckets) if b == "could_not_classify"), 999)
        idx_bundled = next((i for i, b in enumerate(buckets) if b == "bundled_multi_pdmr"), 999)
        self.assertLess(idx_class, idx_bundled)

    def test_buckets_summary_present(self):
        result = self._build({"9000001": self._make_item("9000001")})
        self.assertGreaterEqual(len(result["buckets"]), 6)
        ids = {b["id"] for b in result["buckets"]}
        self.assertIn("bundled_multi_pdmr", ids)
        self.assertIn("could_not_classify", ids)
        self.assertIn("other", ids)

    def test_has_cache_false_when_no_file(self):
        result = self._build({"9000001": self._make_item("9000001")})
        self.assertFalse(result["items"][0]["has_cache"])

    def test_warnings_capped_at_5(self):
        long_warnings = [f"warning_{i}" for i in range(10)]
        result = self._build({"9000001": self._make_item("9000001",
            warnings=long_warnings)})
        self.assertLessEqual(len(result["items"][0]["warnings"]), 5)


class TestBuildTxIndex(unittest.TestCase):
    """A3. build_tx_index returns correct shape from a mocked DB connection."""

    def _make_mock_conn(self, rows):
        """Return a sqlite3.Row-like mock from a list of dicts."""
        mock_rows = []
        for d in rows:
            row = MagicMock()
            row.__getitem__ = lambda self, k, d=d: d[k]
            row.keys = lambda d=d: list(d.keys())
            mock_rows.append(row)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = mock_rows
        return mock_conn

    def _fn(self, rows):
        from export_dashboard_json import build_tx_index
        conn = self._make_mock_conn(rows)
        return build_tx_index(conn, generated_at="2026-06-03T00:00:00Z")

    def test_top_level_keys(self):
        result = self._fn([])
        for key in ("generated_at", "count", "transactions"):
            self.assertIn(key, result)

    def test_count_matches_rows(self):
        rows = [
            {"fingerprint": "abc123", "date": "2026-01-01", "ticker": "TEST",
             "company": "Test Co", "director": "Jane Doe", "role": "CEO",
             "role_normalized": "t1a_ceo", "type": "BUY", "shares": 1000,
             "price": 1.50, "value": 1500.0, "url": "https://example.com/9000001",
             "announced_at": "2026-01-01", "parser_source": "regex",
             "buy_strictness": "STRICT_BUY"},
        ]
        result = self._fn(rows)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["transactions"]), 1)

    def test_rns_id_derived_from_url(self):
        rows = [
            {"fingerprint": "abc123", "date": "2026-01-01", "ticker": "TEST",
             "company": "Test", "director": "Jane", "role": "CEO",
             "role_normalized": None, "type": "BUY", "shares": 100,
             "price": 1.0, "value": 100.0,
             "url": "https://www.investegate.co.uk/announcement/rns/abc/pdmr/9564925",
             "announced_at": "", "parser_source": "regex", "buy_strictness": "STRICT_BUY"},
        ]
        result = self._fn(rows)
        self.assertEqual(result["transactions"][0]["rns_id"], "9564925")

    def test_empty_url_gives_empty_rns_id(self):
        rows = [
            {"fingerprint": "abc123", "date": "2026-01-01", "ticker": "TEST",
             "company": "Test", "director": "Jane", "role": "CEO",
             "role_normalized": None, "type": "BUY", "shares": 100,
             "price": 1.0, "value": 100.0, "url": "",
             "announced_at": "", "parser_source": "regex", "buy_strictness": "STRICT_BUY"},
        ]
        result = self._fn(rows)
        self.assertEqual(result["transactions"][0]["rns_id"], "")


# ── B. server.py route validation regex ───────────────────────────────────────

class TestServerValidationRegex(unittest.TestCase):
    """B1/B2. Regex patterns used in server.py for input validation."""

    _RNS_RE = re.compile(r"^\d{5,12}$")
    _FP_RE  = re.compile(r"^[0-9a-f]{8,32}$")

    # RNS ID validation
    def test_valid_rns_id_7digits(self):
        self.assertTrue(self._RNS_RE.match("9564925"))

    def test_valid_rns_id_5digits(self):
        self.assertTrue(self._RNS_RE.match("12345"))

    def test_rns_id_too_short(self):
        self.assertIsNone(self._RNS_RE.match("1234"))

    def test_rns_id_non_numeric(self):
        self.assertIsNone(self._RNS_RE.match("abc1234"))

    def test_rns_id_path_traversal(self):
        self.assertIsNone(self._RNS_RE.match("../etc/passwd"))

    def test_rns_id_13digits_invalid(self):
        self.assertIsNone(self._RNS_RE.match("1234567890123"))

    # Fingerprint validation
    def test_valid_fingerprint_16hex(self):
        self.assertTrue(self._FP_RE.match("a46984aeff5c532a"))

    def test_valid_fingerprint_8hex(self):
        self.assertTrue(self._FP_RE.match("deadbeef"))

    def test_fingerprint_non_hex(self):
        self.assertIsNone(self._FP_RE.match("xyz123"))

    def test_fingerprint_too_short(self):
        self.assertIsNone(self._FP_RE.match("abc123"))

    def test_fingerprint_path_traversal(self):
        self.assertIsNone(self._FP_RE.match("../../etc/passwd"))


# ── C. render_company.py pencil icon ──────────────────────────────────────────

class TestRenderCompanyPencil(unittest.TestCase):
    """C1/C2. Pencil icon and Review column present in rendered transaction HTML."""

    def _render_rows(self, transactions):
        """Render the transactions table HTML for the given rows.

        B-117: row rendering moved from the old ``_render_transaction_rows``
        into ``_transactions_table(company, today)``, which takes the full
        company dict and returns one HTML string. Re-pointed here.
        """
        import importlib
        rc = importlib.import_module("dashboard.render_company")
        from datetime import date
        html = rc._transactions_table({"transactions": transactions},
                                      date(2026, 6, 3))
        return html, None

    def _make_tx(self, fingerprint="abc123def456ab12"):
        return {
            "fingerprint":   fingerprint,
            "date":          "2026-05-12",
            "txn_type":      "BUY",
            "director":      "Jane Doe",
            "role":          "CEO",
            "role_normalized": "t1a_ceo",
            "shares":        1000,
            "price":         1.50,
            "value":         1500.0,
            "url":           "https://www.investegate.co.uk/announcement/rns/abc/9564925",
            "signals":       [],
        }

    def test_review_link_in_row(self):
        html, _ = self._render_rows([self._make_tx()])
        self.assertIn("/review?tab=b&fp=abc123def456ab12", html,
                      "Review link not found in rendered row")

    def test_pencil_character_present(self):
        html, _ = self._render_rows([self._make_tx()])
        # ✏ = &#9998; in HTML
        self.assertIn("9998", html, "Pencil icon (&#9998;) not found in rendered row")

    def test_review_th_in_header(self):
        html, _ = self._render_rows([self._make_tx()])
        self.assertIn("Review", html, "Review column header not found")

    def test_no_link_when_no_fingerprint(self):
        tx = self._make_tx(fingerprint="")
        html, _ = self._render_rows([tx])
        # Should not have a broken link
        self.assertNotIn("/review?tab=b&fp=", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
