"""Unit tests for the 2026-06-02 ingest-gate incident fix.

Covers:
  * Phase 1 — run_scrape gate helpers: BLOCKING vs ADVISORY classification,
    the hard per-row zero-value guard, and the grant/exercise nil-cost
    exemption.
  * Phase 3 — parse_pdmr type-scoping: the scoped "Nature of the transaction"
    extractor and the bounded fallback block; a "disposal" in page chrome
    must not flip a buy into a sell.

Read-only: imports modules and exercises pure helpers. Touches NO DB, NO
network, NO files. Safe to run from Claude's Linux sandbox.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parse_pdmr as p
import run_scrape as rs


# ── Phase 1: warning classification ─────────────────────────────────────────

class TestWarningClassification(unittest.TestCase):

    def test_known_blocking_codes_block(self):
        for code in [
            "required_fields_missing", "could_not_parse_tx_date",
            "could_not_extract_ticker", "could_not_extract_PDMR_name",
            "could_not_classify_type", "could_not_separate_price_volume",
            "zero_shares_non_grant", "zero_price_non_grant",
            "foreign_currency", "multiple_distinct_prices",
            "duplicate_number_pull", "llm_invalid_shares",
        ]:
            self.assertTrue(rs._warning_is_blocking(code), code)

    def test_blocking_prefixes_block(self):
        for w in [
            "plausibility_rejected:R1_price_too_high",
            "llm_missing_fields:price,shares",
            "llm_invalid_type:'TRANSFER'",
            "fetch_error:timeout",
            "llm_error:ValueError",
            "llm_unparseable_response:'oops'",
        ]:
            self.assertTrue(rs._warning_is_blocking(w), w)

    def test_advisory_company_is_not_blocking(self):
        self.assertFalse(rs._warning_is_blocking("could_not_extract_company"))

    def test_plausibility_flagged_R5_is_advisory(self):
        # log-only flag the parser deliberately emits — must not block.
        self.assertFalse(
            rs._warning_is_blocking("plausibility_flagged:R5_date_component_in_shares")
        )

    def test_free_text_llm_prose_is_advisory(self):
        prose = ("Filing also includes EXERCISE of 195,049 nil-cost "
                 "Restricted Shares on same date; only SELL_TAX leg captured")
        self.assertFalse(rs._warning_is_blocking(prose))

    def test_empty_and_none_are_not_blocking(self):
        self.assertFalse(rs._warning_is_blocking(""))
        self.assertFalse(rs._warning_is_blocking(None))


# ── Phase 1: per-row ingestability ──────────────────────────────────────────

class TestRowIngestable(unittest.TestCase):

    def _row(self, **kw):
        base = {"type": "BUY", "price": 4.5, "value": 4500.0,
                "shares": 1000, "ticker": "ABC"}
        base.update(kw)
        return base

    def test_clean_buy_with_no_warnings_ingests(self):
        self.assertTrue(rs._row_is_ingestable(self._row(), []))

    def test_clean_buy_with_advisory_only_ingests(self):
        self.assertTrue(
            rs._row_is_ingestable(self._row(), ["could_not_extract_company"])
        )

    def test_buy_with_blocking_warning_is_held(self):
        self.assertFalse(
            rs._row_is_ingestable(self._row(), ["foreign_currency"])
        )

    def test_mixed_warnings_block_if_any_blocking(self):
        self.assertFalse(
            rs._row_is_ingestable(
                self._row(),
                ["could_not_extract_company", "zero_price_non_grant"],
            )
        )

    # Hard per-row zero-value guard --------------------------------------

    def test_nongrant_zero_price_is_held_even_with_no_warning(self):
        # The guard must fire regardless of attached warnings.
        self.assertFalse(
            rs._row_is_ingestable(
                self._row(price=0.0, value=0.0), []
            )
        )

    def test_nongrant_zero_value_only_is_held(self):
        self.assertFalse(
            rs._row_is_ingestable(self._row(price=4.5, value=0.0), [])
        )

    def test_sell_zero_value_is_held(self):
        self.assertFalse(
            rs._row_is_ingestable(
                self._row(type="SELL", price=0.0, value=0.0), []
            )
        )

    # Grant / exercise nil-cost exemption --------------------------------

    def test_grant_with_zero_price_ingests(self):
        self.assertTrue(
            rs._row_is_ingestable(
                self._row(type="GRANT", price=0.0, value=0.0), []
            )
        )

    def test_exercise_with_zero_price_ingests(self):
        self.assertTrue(
            rs._row_is_ingestable(
                self._row(type="EXERCISE", price=0.0, value=0.0), []
            )
        )

    def test_grant_with_blocking_warning_still_held(self):
        # Nil-cost exemption is only for the zero-value guard, not the
        # blocking-warning gate.
        self.assertFalse(
            rs._row_is_ingestable(
                self._row(type="GRANT", price=0.0, value=0.0),
                ["required_fields_missing"],
            )
        )


# ── Phase 3: type scoping ───────────────────────────────────────────────────

class TestTypeScoping(unittest.TestCase):

    def test_scoped_nature_extracts_cell(self):
        text = (
            "Some Investegate header\n"
            "Nature of the transaction\n"
            "Acquisition of ordinary shares\n"
            "Price(s)\n£4.50"
        )
        self.assertEqual(
            p._scoped_nature_text(text), "Acquisition of ordinary shares"
        )

    def test_scoped_nature_same_line(self):
        text = "Nature of the transaction: Purchase of shares"
        self.assertEqual(p._scoped_nature_text(text), "Purchase of shares")

    def test_scoped_nature_absent_returns_none(self):
        self.assertIsNone(p._scoped_nature_text("no label here"))

    def test_disposal_in_chrome_does_not_flip_buy(self):
        # Reproduces the JMAT/GEN/UTL/CAD incident: a news-ticker
        # "disposal" mention must not flip a buy. Whole-page classify would
        # return SELL; scoped classify returns BUY.
        page = (
            "LATEST NEWS: Megacorp announces asset disposal of unit\n"
            "RELATED: more on the disposal saga\n"
            "Nature of the transaction\n"
            "Acquisition of shares\n"
        )
        whole = p._classify_type(page)[0]
        scoped = p._classify_type(p._scoped_nature_text(page))[0]
        self.assertEqual(whole, "SELL")   # demonstrates the old bug
        self.assertEqual(scoped, "BUY")   # demonstrates the fix

    def test_bounded_block_anchors_on_tx_detail(self):
        text = (
            "Sidebar: latest disposal headlines here\n"
            "Date of the transaction\n"
            "01 June 2026\n"
            "Acquisition of shares\n"
        )
        # No explicit Nature cell → bounded fallback. The chrome 'disposal'
        # sits before the anchor and must be excluded.
        self.assertIsNone(p._scoped_nature_text(text))
        block = p._bounded_tx_block(text)
        self.assertIsNotNone(block)
        self.assertNotIn("disposal", block.lower())
        self.assertEqual(p._classify_type(block)[0], "BUY")

    def test_bounded_block_none_when_no_anchor(self):
        self.assertIsNone(p._bounded_tx_block("nothing relevant"))
        # None → could_not_classify_type (correctly BLOCKING downstream).
        self.assertEqual(
            p._classify_type(None), (None, ["could_not_classify_type"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
