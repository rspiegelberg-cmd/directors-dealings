"""Tests for the comp-event-as-STRICT_BUY + pence/pounds parser fixes.

Scope: docs/specs/parser-fix-comp-events-and-pence-2026-06-03.md
Auditor findings: docs/audits/reparse-buy-insert-verification_2026-06-03.md

These tests drive the two pure functions directly on the exact
"Nature of the transaction" and "Price(s)" cell strings copied from the
cached source HTML (.scripts/_scrape_cache/{rns_id}.html). Each string is
quoted in a comment with its rns_id so the source is traceable. No DB,
no network, no cache writes — safe in any sandbox.

Fix 1 (strictness): comp / remuneration forms must NOT classify STRICT_BUY.
Fix 2 (pence):       a bare price cell like "171.0" (pence-quoted, no £)
                     must convert to £1.71, while £-prefixed pounds are
                     left untouched.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_pdmr as p  # noqa: E402


class PositiveStrictness(unittest.TestCase):
    """Comp / remuneration events must NOT remain STRICT_BUY.

    Acceptance: a NON-firing label — NON_BUY_ONLY or MIXED (both gated;
    signals require STRICT_BUY). The hard requirement is simply: != STRICT_BUY.
    """

    # Each case: (label, nature_cell_text). The nature cell is what the three
    # table-aware call sites in parse_announcement actually pass to the
    # classifier (the short scoped "Nature of the transaction" value).
    CASES = [
        # BAE 9490111 — per-PDMR nature cell for the DBP purchase lines.
        # Cache: "...rchase of deferred shares under the DBP. \n ... Price(s)..."
        ("BAE 9490111 DBP", "Purchase of deferred shares under the DBP."),
        # Unilever 9555689 — both the grant prose and the "Purchase of Bonus
        # Deferral Award forfeitable shares" line that currently scores strict.
        # Cache: "Purchase of Bonus Deferral Award forfeitable shares \n £45.35..."
        ("ULVR 9555689 grant",
         "8,876 PLC shares (grant of Bonus Deferral Award of Unilever PLC "
         "Ordinary shares) subject to restrictions"),
        ("ULVR 9555689 purchase",
         "Purchase of Bonus Deferral Award forfeitable shares"),
        # BATS 8871006 — dividend-accrual scrip under a deferred bonus scheme.
        # Cache: "Acquisition of quarterly dividend equivalent shares under the
        #         British American Tobacco Deferred Share Bonus Scheme"
        ("BATS 8871006 scrip",
         "Acquisition of quarterly dividend equivalent shares under the "
         "British American Tobacco Deferred Share Bonus Scheme"),
        # PRU 8890342 — dividend-accrual on deferred share awards.
        # Cache: "Acquisition of shares in respect of dividends accruing to
        #         deferred share awards"
        ("PRU 8890342 scrip",
         "Acquisition of shares in respect of dividends accruing to deferred "
         "share awards"),
        # HAS 9517985 — US Employee Stock Purchase Plan (ESPP). This one
        # currently scores STRICT_BUY because it literally says "Purchase of
        # shares".
        # Cache: "Purchase of shares at a price of 27p per share under the Hays
        #         plc US Employee Stock Purchase Plan"
        ("HAS 9517985 ESPP",
         "Purchase of shares at a price of 27p per share under the Hays plc "
         "US Employee Stock Purchase Plan"),
        # PRU 9566410 — All Employee Share Purchase Plan.
        # Cache: "Acquisition of shares through the Prudential All Employee
        #         Share Purchase Plan"
        ("PRU 9566410 AESPP",
         "Acquisition of shares through the Prudential All Employee Share "
         "Purchase Plan"),
        # RR 9467833 — NED share purchase plan. Also currently STRICT_BUY
        # ("Market purchase of Ordinary Shares").
        # Cache: "Market purchase of Ordinary Shares under a share purchase
        #         plan for Non-Executive Directors"
        ("RR 9467833 SPP",
         "Market purchase of Ordinary Shares under a share purchase plan for "
         "Non-Executive Directors"),
        # CNA 9196719 — Share Purchase Agreement (off-market, not discretionary
        # on-market). Cache: "Acquisition of Shares in accordance with the
        #         Share Purchase Agreement entered into with..."
        ("CNA 9196719 SPA",
         "Acquisition of Shares in accordance with the Share Purchase "
         "Agreement entered into with the Company"),
    ]

    def test_comp_events_not_strict_buy(self):
        for label, nature in self.CASES:
            with self.subTest(case=label):
                got = p._classify_buy_strictness(nature)
                self.assertNotEqual(
                    got, "STRICT_BUY",
                    f"{label}: comp event classified STRICT_BUY (got {got})",
                )


class NegativeStrictness(unittest.TestCase):
    """Genuine on-market discretionary buys must REMAIN STRICT_BUY.

    Hard guardrail: zero of these may be demoted.
    """

    # Confirmed-clean discretionary buys from the audit (genuine on-market /
    # own-capital purchases). Nature cells as the parser sees them.
    CASES = [
        # IGP 8946247 — genuine buy (only its VALUE was misparsed, see pence
        # tests). Cache nature cell: "Purchase of Ordinary Shares".
        ("IGP 8946247", "Purchase of Ordinary Shares"),
        # YNGA 9587726 — live-cross-checked clean buy. "Purchase of shares".
        ("YNGA 9587726", "Purchase of shares"),
        # SYS1 9460976 — clean discretionary buy.
        ("SYS1 9460976", "Purchase of ordinary shares"),
        # ARBB 9494459 — clean discretionary buy.
        ("ARBB 9494459", "Purchase of 300,000 ordinary shares"),
        # BRBY 8952502 — clean discretionary buy.
        ("BRBY 8952502", "Purchase of shares"),
        # Bare nature-cell token form that real filings use.
        ("bare Purchase", "Purchase"),
    ]

    def test_genuine_buys_stay_strict(self):
        for label, nature in self.CASES:
            with self.subTest(case=label):
                got = p._classify_buy_strictness(nature)
                self.assertEqual(
                    got, "STRICT_BUY",
                    f"{label}: genuine buy demoted (got {got})",
                )


class PenceValue(unittest.TestCase):
    """Pence-quoted bare price cells convert to pounds; £-quoted untouched."""

    @unittest.skip(
        "Fix 2 (bare-number -> pence) reverted 2026-06-03: QA RED. The naive "
        "'bare >= 1.0 => pence' rule corrupted genuine pound-quoted buys "
        "stored bare (e.g. Shell 9580273: £2.7m -> £0.3252). Bare numbers are "
        "treated as pounds again. Re-enable when the reworked, reconciliation-"
        "based pence fix lands — see "
        "docs/specs/parser-fix-comp-events-and-pence-2026-06-03.md task #11."
    )
    def test_igp_pence_to_pounds(self):
        # IGP 8946247 Tredoux line — price cell is the canonical table layout
        # "Price(s)\nVolume(s):\n171.0\n60,000" with a BARE 171.0 (no £, no p).
        # Cache: "...Price(s) \n Volume(s) \n 171.0 \n 60,000 ..."
        block = "Price(s) \n Volume(s): \n 171.0 \n 60,000"
        price, shares, warnings = p._parse_price_vol(block)
        self.assertAlmostEqual(price, 1.71, places=4,
                               msg=f"IGP price not converted (got {price})")
        self.assertEqual(shares, 60000)
        value = price * shares
        # ~£102,600, not £10,260,000.
        self.assertTrue(
            100_000 <= value <= 106_000,
            f"IGP value out of band: {value:,.0f} (expected ~103k)",
        )

    @unittest.skip(
        "Fix 2 (bare-number -> pence) reverted 2026-06-03: QA RED. Bare "
        "numbers are treated as pounds again. Re-enable with the reworked "
        "pence fix — docs/specs/parser-fix-comp-events-and-pence-2026-06-03.md "
        "task #11."
    )
    def test_igp_other_pence_line(self):
        # Same filing, Van der Leest aggregate line: bare 177.82 -> £1.7782.
        block = "Price(s) \n Volume(s): \n 177.82 \n 22,450"
        price, shares, _ = p._parse_price_vol(block)
        self.assertAlmostEqual(price, 1.7782, places=4)
        self.assertEqual(shares, 22450)

    def test_pound_prefixed_untouched_ynga(self):
        # YNGA 9587726 — £6.42 must stay £6.42 (NOT 0.0642).
        # Cache: "Price(s) Volume(s) \n £6.42 1,556"
        block = "Price(s) \n Volume(s): \n £6.42 \n 1,556"
        price, shares, _ = p._parse_price_vol(block)
        self.assertAlmostEqual(price, 6.42, places=4,
                               msg=f"£-quoted price wrongly divided (got {price})")
        self.assertEqual(shares, 1556)

    def test_pound_prefixed_untouched_brby(self):
        # BRBY 8952502 — £10.690945 must stay as pounds.
        block = "Price(s) \n Volume(s): \n £10.690945 \n 29,744"
        price, shares, _ = p._parse_price_vol(block)
        self.assertAlmostEqual(price, 10.690945, places=5)
        self.assertEqual(shares, 29744)

    def test_explicit_pence_suffix_still_works(self):
        # Regression: "50p" still -> £0.50 (existing behaviour preserved).
        block = "Price(s) \n Volume(s): \n 50p \n 1,000"
        price, _, _ = p._parse_price_vol(block)
        self.assertAlmostEqual(price, 0.50, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
