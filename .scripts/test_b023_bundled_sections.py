"""B-023 — section-aware extractor for bundled multi-PDMR filings.

Some MAR Article 19 filings (e.g. AAL 8950385) bundle multiple PDMRs by
including a SEPARATE 18-row KV detail table per PDMR (instead of a
single transaction table with N rows). Pre-B-023 the standard table
extractor found no qualifying table and the bundled-warning early-return
caused these filings to silently yield zero rows.

This test asserts the new `_extract_via_sections` path fans the bundle
out into one row per PDMR with correct names, dates, prices, and
volumes.

Run on Windows via PowerShell:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from parse_pdmr import parse_announcement, _extract_via_sections  # noqa: E402

CACHE_DIR = HERE / "_scrape_cache"


def _load(rns_id: str) -> str | None:
    p = CACHE_DIR / f"{rns_id}.html"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


@unittest.skipUnless((CACHE_DIR / "8950385.html").exists(),
                     "AAL 8950385 cached HTML not present")
class TestAAL8950385Bundled(unittest.TestCase):
    """AAL 8950385 — 3 PDMRs (Stuart Chambers, Magali Anderson,
    Nonkululeko Nyembezi) each buying Anglo American shares on 2025-06-25
    at GBP 20.44, volumes 859 / 341 / 347.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.html = _load("8950385")

    def test_section_extractor_returns_three_rows(self) -> None:
        rows, company = _extract_via_sections(self.html)
        self.assertIsNotNone(rows, "section extractor should fan out AAL")
        self.assertEqual(len(rows), 3, "expected 3 PDMR sections")
        self.assertIn("Anglo American", (company or ""),
                      f"expected Anglo American company, got {company!r}")

    def test_section_directors(self) -> None:
        rows, _ = _extract_via_sections(self.html)
        names = {r["director"] for r in rows if r["director"]}
        self.assertIn("Stuart Chambers", names)
        self.assertIn("Magali Anderson", names)
        self.assertIn("Nonkululeko Nyembezi", names)

    def test_section_volumes(self) -> None:
        rows, _ = _extract_via_sections(self.html)
        by_name = {r["director"]: r for r in rows if r["director"]}
        self.assertEqual(by_name["Stuart Chambers"]["shares"], 859)
        self.assertEqual(by_name["Magali Anderson"]["shares"], 341)
        self.assertEqual(by_name["Nonkululeko Nyembezi"]["shares"], 347)

    def test_section_dates_and_price(self) -> None:
        rows, _ = _extract_via_sections(self.html)
        for r in rows:
            self.assertEqual(r["date"], "2025-06-25",
                             f"date drift for {r['director']!r}")
            self.assertAlmostEqual(r["price"], 20.44, places=2,
                                   msg=f"price drift for {r['director']!r}")

    def test_parse_announcement_end_to_end_yields_three_rows(self) -> None:
        """Full parse pipeline should now yield 3 rows for AAL 8950385
        (was 0 after the May-19 bundled-regex tightening, was 1 before)."""
        url = ("https://www.investegate.co.uk/announcement/rns/"
               "anglo-american--aal/x/8950385")
        rows, warnings, source = parse_announcement(
            self.html, url, "8950385", "2025-06-25T07:00:00Z",
            headline="Director/PDMR Shareholding", ticker_hint="AAL",
        )
        self.assertEqual(len(rows), 3,
                         f"expected 3 rows from full parse, got {len(rows)} "
                         f"(warnings: {warnings[:3]})")
        self.assertEqual(source, "regex")
        names = {r["director"] for r in rows}
        self.assertEqual(names, {"Stuart Chambers", "Magali Anderson",
                                 "Nonkululeko Nyembezi"})
        # All three should share company, ticker, and date
        for r in rows:
            self.assertEqual(r["ticker"], "AAL")
            self.assertEqual(r["date"], "2025-06-25")
            self.assertIn("Anglo American", r["company"])


class TestSectionExtractorOnSingleSection(unittest.TestCase):
    """Single-section filings should return (None, None) so the standard
    table extractor handles them. Smoke-test against a deliberately tiny
    HTML stub — no cache required.
    """

    def test_no_sections_returns_none(self) -> None:
        rows, company = _extract_via_sections("<html><body></body></html>")
        self.assertIsNone(rows)
        self.assertIsNone(company)

    def test_one_section_returns_none(self) -> None:
        # A filing with one "Details of PDMR" table is the standard
        # layout — section extractor should defer to standard path.
        html = """
        <html><body>
        <table>
            <tr><td>1.</td><td>Details of PDMR / PCA</td></tr>
            <tr><td>a)</td><td>Name</td><td>Test Director</td></tr>
        </table>
        </body></html>
        """
        rows, company = _extract_via_sections(html)
        self.assertIsNone(rows,
                          "single-section filings should defer to standard path")


if __name__ == "__main__":
    unittest.main()
