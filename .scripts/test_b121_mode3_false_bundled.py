"""B-121 Mode-3 — "false-bundled" single-PDMR recovery.

A genuine single-PDMR filing whose "Details of PDMR / PCA" header is
text-duplicated on a non-header row (a copy-paste error — section 2 mis-pasted
instead of "Reason for the notification") used to be REFUSED by the bundled
gate, dropping a clean individual director buy. The gate now only refuses on a
STRONG bundle signal (`_BUNDLED_PDMR_RE`: numbered name list / "Notification N
of M"); Mode-3 filings fall through to the legacy extractor.

Genuine bundles (AAL 8950385) and Schroders-style numbered bundles must STILL
be refused / fanned out correctly.

Run on Windows:
    python -m unittest test_b121_mode3_false_bundled -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from parse_pdmr import parse_announcement  # noqa: E402

CACHE = HERE / "_scrape_cache"


def _load(rns_id: str):
    p = CACHE / f"{rns_id}.html"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def _one(rns_id: str):
    rows, warnings, _src = parse_announcement(
        _load(rns_id), url="", rns_id=rns_id, announced_at="")
    return rows, warnings


@unittest.skipUnless((CACHE / "8967255.html").exists(), "8967255 not in cache")
class TestPrimaryRecovery(unittest.TestCase):
    """BOWL 8967255 — Stephen Burns, single BUY 12,427 @ £2.39."""

    def test_recovers_single_buy(self):
        rows, warnings = _one("8967255")
        self.assertEqual(len(rows), 1, f"expected 1 row; warnings={warnings}")
        r = rows[0]
        self.assertEqual(r["director"], "Stephen Burns")
        self.assertEqual((r["type"] or "").upper(), "BUY")
        self.assertEqual(r["shares"], 12427)
        self.assertAlmostEqual(r["price"], 2.39, places=2)


@unittest.skipUnless((CACHE / "8950385.html").exists(), "8950385 not in cache")
class TestGenuineBundleStillFansOut(unittest.TestCase):
    """AAL 8950385 — real 3-PDMR bundle: must still yield 3 distinct directors,
    never a single mis-attributed row."""

    def test_three_rows_not_one(self):
        rows, warnings = _one("8950385")
        self.assertGreaterEqual(len(rows), 3, f"warnings={warnings}")
        names = {r["director"] for r in rows}
        self.assertIn("Stuart Chambers", names)
        self.assertIn("Magali Anderson", names)
        self.assertIn("Nonkululeko Nyembezi", names)


@unittest.skipUnless((CACHE / "8871609.html").exists(), "8871609 not in cache")
class TestRecoveryStirling(unittest.TestCase):
    def test_recovers(self):
        rows, warnings = _one("8871609")
        self.assertEqual(len(rows), 1, f"warnings={warnings}")
        self.assertEqual(rows[0]["director"], "David Stirling")
        self.assertEqual((rows[0]["type"] or "").upper(), "BUY")
        self.assertEqual(rows[0]["shares"], 3000)


@unittest.skipUnless((CACHE / "8969590.html").exists(), "8969590 not in cache")
class TestRecoveryShapland(unittest.TestCase):
    def test_recovers(self):
        rows, warnings = _one("8969590")
        self.assertEqual(len(rows), 1, f"warnings={warnings}")
        self.assertEqual(rows[0]["director"], "Wendy Shapland")
        self.assertEqual((rows[0]["type"] or "").upper(), "BUY")
        self.assertEqual(rows[0]["shares"], 12500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
