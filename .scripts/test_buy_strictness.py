"""Tests for parse_pdmr._classify_buy_strictness (Sprint 13 Phase 1).

Covers STRICT_BUY, NON_BUY_ONLY, MIXED, UNKNOWN outputs and the full
set of NON_BUY trigger phrases from the spec §2.2.

Run:
    python -m unittest test_buy_strictness -v
"""
import sys
import unittest
from pathlib import Path

# Allow running from project root or .scripts/ directory.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from parse_pdmr import _classify_buy_strictness


class TestClassifyBuyStrictnessStrictBuy(unittest.TestCase):
    """Nature texts that should classify as STRICT_BUY."""

    def test_on_market_purchase_of_ordinary_shares(self):
        text = "On-market purchase of ordinary shares"
        self.assertEqual(_classify_buy_strictness(text), "STRICT_BUY")

    def test_on_market_purchase_no_ordinary(self):
        text = "On-market purchase of shares at a price of 120p per share"
        self.assertEqual(_classify_buy_strictness(text), "STRICT_BUY")

    def test_purchase_of_ordinary_shares(self):
        text = "Purchase of ordinary shares"
        self.assertEqual(_classify_buy_strictness(text), "STRICT_BUY")

    def test_purchase_of_shares_lowercase(self):
        text = "purchase of shares"
        self.assertEqual(_classify_buy_strictness(text), "STRICT_BUY")

    # --- B-093 (Sprint 20): real nature-cell forms that were tagged UNKNOWN ---
    def test_bare_purchase_word(self):
        # UTL/IMB: the "Nature of the transaction" cell is just "Purchase".
        self.assertEqual(_classify_buy_strictness("Purchase"), "STRICT_BUY")

    def test_bare_purchase_uppercase(self):
        self.assertEqual(_classify_buy_strictness("PURCHASE"), "STRICT_BUY")

    def test_purchase_of_n_company_shares(self):
        # ULVR: "Purchase of 345 PLC shares" — company word before "shares".
        self.assertEqual(
            _classify_buy_strictness("Purchase of 345 PLC shares"), "STRICT_BUY")

    def test_prose_purchased_n_ordinary_shares(self):
        # IMB prose form.
        self.assertEqual(
            _classify_buy_strictness("has purchased 33,657 ordinary shares of 10p each"),
            "STRICT_BUY")

    def test_purchase_plus_saye_is_mixed(self):
        # Permissive purchase wording must NOT override a non-buy marker.
        self.assertEqual(
            _classify_buy_strictness("Purchase of shares via Sharesave (SAYE)"),
            "MIXED")

    def test_case_insensitive_on_market(self):
        text = "ON-MARKET PURCHASE OF ORDINARY SHARES"
        self.assertEqual(_classify_buy_strictness(text), "STRICT_BUY")


class TestClassifyBuyStrictnessNonBuyOnly(unittest.TestCase):
    """Nature texts that should classify as NON_BUY_ONLY (no buy language)."""

    def test_vesting_of_an_award(self):
        text = "Acquisition of shares following the vesting of an award under the RSP"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_ltip(self):
        text = "Grant of Long Term Incentive Plan awards"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_ltip_abbreviation(self):
        text = "LTIP award of 50,000 shares"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_rsp_abbreviation(self):
        text = "RSP conditional award"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_psp_abbreviation(self):
        text = "PSP vesting event"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_drip(self):
        text = "Dividend Reinvestment Plan (DRIP) purchase"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_sip(self):
        text = "SIP partnership shares acquired via payroll deduction"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_sharesave(self):
        text = "Sharesave scheme exercise — shares acquired"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_saye_abbreviation(self):
        text = "SAYE option exercise"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_save_as_you_earn(self):
        text = "Save-as-you-earn scheme maturity"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_exercise_of_options(self):
        text = "Exercise of options under the Executive Share Option Plan"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_nil_cost_option(self):
        text = "Nil-cost option vesting"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_vested_word(self):
        text = "Shares vested under the 2022 LTIP award"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_grant_of_award(self):
        text = "Grant of an award of 100,000 shares"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_grant_of_options(self):
        text = "Grant of options under the company share option plan"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_employee_benefit_trust(self):
        text = "Transfer from Employee Benefit Trust"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_scrip_dividend(self):
        text = "Scrip dividend election — shares received in lieu of cash"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_restricted_share_plan(self):
        text = "Restricted Share Plan conditional award"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_deferred_bonus_plan(self):
        text = "Deferred Bonus Plan award release"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")

    def test_performance_share_plan(self):
        text = "Performance Share Plan vesting"
        self.assertEqual(_classify_buy_strictness(text), "NON_BUY_ONLY")


class TestClassifyBuyStrictnessMixed(unittest.TestCase):
    """Texts with both buy and non-buy language should be MIXED."""

    def test_vest_and_on_market_purchase(self):
        # E.g. the director sold vested shares AND bought on-market on same day.
        text = ("Following the vesting of an award, the director made an "
                "on-market purchase of ordinary shares.")
        self.assertEqual(_classify_buy_strictness(text), "MIXED")

    def test_ltip_and_purchase(self):
        text = "LTIP award of 10,000 shares and purchase of 5,000 shares"
        self.assertEqual(_classify_buy_strictness(text), "MIXED")


class TestClassifyBuyStrictnessUnknown(unittest.TestCase):
    """Texts with no matching patterns should be UNKNOWN."""

    def test_empty_string(self):
        self.assertEqual(_classify_buy_strictness(""), "UNKNOWN")

    def test_none_input(self):
        self.assertEqual(_classify_buy_strictness(None), "UNKNOWN")

    def test_no_relevant_keywords(self):
        text = "Notification of change in PDMRs holdings"
        self.assertEqual(_classify_buy_strictness(text), "UNKNOWN")

    def test_sell_transaction(self):
        # A sell transaction — no buy or non-buy markers in its nature text.
        text = "Disposal of ordinary shares in the open market"
        self.assertEqual(_classify_buy_strictness(text), "UNKNOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
