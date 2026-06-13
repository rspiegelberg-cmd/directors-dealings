"""B-003 — Parser unit tests (Layer 2 per docs/specs/date-integrity-test-strategy.md).

Locks in the parser fixes from Sprint 1 + Sprint 3 + the Sprint-4 cleanup
items (B-022 Pass-3 company fallback, B-023 PCA bundled-PDMR detection).
Run with::

    python -m unittest test_parser            # from .scripts/
    run_tests.bat                              # from project root

Or under `unittest discover`::

    python -m unittest discover -s .scripts -p "test_*.py"

WHAT THIS COVERS
----------------
1. **Fixture tests** (one per `.scripts/fixtures/parser/*.html`). Each
   fixture exercises ONE historical bug pattern in isolation so a
   failure points straight at the broken module. Fixtures are synthetic
   minimal HTML (a few hundred bytes each) -- deliberately NOT slices
   of real cached filings so they stay self-documenting and don't
   churn when real-world layouts shift.

2. **`_try_one_date` direct tests** for the B-005 year-pivot defence
   and the original 2026-05-15 dot-separated-date fix.

3. **Live-cache smoke test** that picks 20 random files from
   `.scripts/_scrape_cache/` (seed=0, deterministic) and asserts the
   parser returns without raising. Catches "parser crashes on edge
   case" regressions without overfitting on specific outputs.

CLAUDE.md / FUSE: this test file READS the cache but never writes
to `.data/`. Safe to run from Claude's Linux sandbox.
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parse_pdmr  # noqa: E402

FIXTURE_DIR = HERE / "fixtures" / "parser"
CACHE_DIR = HERE / "_scrape_cache"


# ---------------------------------------------------------------------------
# Fixture-driven tests
# ---------------------------------------------------------------------------


def _load_fixture_pairs() -> list[tuple[Path, dict]]:
    """Discover every `<stem>.html` + `<stem>.expected.json` pair under
    the fixtures directory. Sorted for stable test naming."""
    pairs: list[tuple[Path, dict]] = []
    if not FIXTURE_DIR.exists():
        return pairs
    for html_path in sorted(FIXTURE_DIR.glob("*.html")):
        expected_path = html_path.with_suffix(".expected.json")
        if not expected_path.exists():
            continue
        try:
            spec = json.loads(expected_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"Fixture spec {expected_path.name} is not valid JSON: {e}"
            ) from None
        pairs.append((html_path, spec))
    return pairs


class FixtureCoverageTest(unittest.TestCase):
    """Sanity-check that the fixture directory actually has fixtures.
    Catches the "test scaffold landed but no fixtures shipped" case."""

    def test_fixtures_present(self):
        pairs = _load_fixture_pairs()
        self.assertGreaterEqual(
            len(pairs), 5,
            f"Expected at least 5 fixture pairs under {FIXTURE_DIR}, "
            f"found {len(pairs)}. Has the fixtures directory been deleted?"
        )


def _make_fixture_test(html_path: Path, spec: dict):
    """Build one test method for a fixture pair. Returned closure runs
    `parse_announcement` and asserts against the spec's expected_count,
    rows, and warnings constraints."""

    def runner(self):
        html = html_path.read_text(encoding="utf-8")
        ticker_hint = spec.get("ticker_hint")
        # Synthetic URL with the expected ticker baked in so the
        # URL-slug ticker resolver also finds it -- belt-and-braces.
        url = f"https://example.test/announcement/rns/synthetic-co--{(ticker_hint or 'xxx').lower()}/test/0"
        extracted, warnings, source = parse_pdmr.parse_announcement(
            html, url=url, rns_id="fixture-" + html_path.stem,
            announced_at="2026-05-19T09:00:00Z",
            headline=f"({ticker_hint}) Test fixture {html_path.stem}" if ticker_hint else None,
            ticker_hint=ticker_hint,
        )

        # extracted_count match.
        expected_count = spec.get("extracted_count")
        if expected_count is not None:
            self.assertEqual(
                len(extracted), expected_count,
                f"{html_path.name}: expected {expected_count} extracted rows, "
                f"got {len(extracted)}. extracted={extracted!r} "
                f"warnings={warnings!r}"
            )

        # Row-level field assertions (partial — only checks keys present
        # in the spec, ignores others). Order-sensitive: spec.rows[i]
        # vs extracted[i].
        for i, expected_row in enumerate(spec.get("rows") or []):
            self.assertLess(
                i, len(extracted),
                f"{html_path.name}: spec expects row {i} but only "
                f"{len(extracted)} extracted",
            )
            actual = extracted[i]
            for k, v in expected_row.items():
                self.assertEqual(
                    actual.get(k), v,
                    f"{html_path.name} row {i} field {k!r}: "
                    f"expected {v!r}, got {actual.get(k)!r}. "
                    f"Full row: {actual!r}",
                )

        # Negative field assertions: assert extracted[index][field] != value.
        # Used by the year-as-shares fixtures to lock in "shares must never
        # be the bare year 2026" alongside the positive shares assertion.
        for neg in spec.get("rows_assert_not_equal") or []:
            idx = neg["index"]
            self.assertLess(
                idx, len(extracted),
                f"{html_path.name}: rows_assert_not_equal references row "
                f"{idx} but only {len(extracted)} extracted",
            )
            field = neg["field"]
            self.assertNotEqual(
                extracted[idx].get(field), neg["value"],
                f"{html_path.name} row {idx} field {field!r}: must NOT equal "
                f"{neg['value']!r}, but it did. Full row: {extracted[idx]!r}",
            )

        # Warning constraints. Substring match (any warning that
        # contains the substring counts) so we don't over-specify.
        must_contain = spec.get("warnings_must_contain_substring") or []
        for needle in must_contain:
            self.assertTrue(
                any(needle in w for w in warnings),
                f"{html_path.name}: expected at least one warning "
                f"containing {needle!r}; got {warnings!r}",
            )
        must_not_contain = spec.get("warnings_must_not_contain") or []
        for needle in must_not_contain:
            self.assertFalse(
                any(needle in w for w in warnings),
                f"{html_path.name}: warnings must NOT contain "
                f"{needle!r}; got {warnings!r}",
            )

    runner.__name__ = f"test_fixture_{html_path.stem}"
    runner.__doc__ = spec.get("_doc") or f"Fixture {html_path.name}"
    return runner


# Dynamically attach one test method per fixture to the test class.
class FixtureTests(unittest.TestCase):
    pass


for _html, _spec in _load_fixture_pairs():
    setattr(FixtureTests, f"test_fixture_{_html.stem}",
            _make_fixture_test(_html, _spec))


# ---------------------------------------------------------------------------
# Direct date-helper tests (no HTML round-trip)
# ---------------------------------------------------------------------------


class TryOneDateTest(unittest.TestCase):
    """Direct tests for parse_pdmr._try_one_date covering the date
    fixes that don't need a full HTML fixture to exercise."""

    def test_dotted_2digit_modern_year(self):
        # The 2026-05-15 fix: dot-separated 2-digit year must parse.
        self.assertEqual(parse_pdmr._try_one_date("05.05.26"), "2026-05-05")

    def test_dotted_4digit_year(self):
        self.assertEqual(parse_pdmr._try_one_date("05.05.2026"), "2026-05-05")

    def test_long_month_name(self):
        self.assertEqual(parse_pdmr._try_one_date("31 May 2026"), "2026-05-31")

    def test_short_month_name(self):
        self.assertEqual(parse_pdmr._try_one_date("31 May 2026"), "2026-05-31")

    def test_ordinal_suffix_stripped(self):
        # "1st" / "2nd" / "3rd" / "4th" should be stripped before strptime.
        self.assertEqual(parse_pdmr._try_one_date("1st May 2026"), "2026-05-01")

    def test_iso_dashed(self):
        self.assertEqual(parse_pdmr._try_one_date("2026-05-05"), "2026-05-05")

    def test_b005_pre_1990_rejected(self):
        # B-005: %d.%m.%y pivots at 68/69, so "01.01.69" -> 1969 -> reject.
        self.assertIsNone(parse_pdmr._try_one_date("01.01.69"))

    def test_b005_pre_1990_dotted_year_rejected(self):
        # Even a 4-digit pre-1990 year must be rejected.
        self.assertIsNone(parse_pdmr._try_one_date("01.01.1985"))

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_pdmr._try_one_date("not a date"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_pdmr._try_one_date(""))


# ---------------------------------------------------------------------------
# Live-cache smoke test
# ---------------------------------------------------------------------------


class SmokeTest(unittest.TestCase):
    """Pick 20 random files from the live cache and assert the parser
    runs without raising. Deterministic seed so the test always picks
    the same files across runs. Skipped when the cache is absent
    (fresh clone / cache cleared)."""

    SAMPLE_SIZE = 20
    RNG_SEED = 0

    def test_smoke_random_sample(self):
        if not CACHE_DIR.exists():
            self.skipTest(f"cache dir {CACHE_DIR} absent — fresh clone?")
        files = sorted(CACHE_DIR.glob("*.html"))
        if not files:
            self.skipTest("cache dir is empty")
        rng = random.Random(self.RNG_SEED)
        sample = rng.sample(files, k=min(self.SAMPLE_SIZE, len(files)))
        non_empty = 0
        for f in sample:
            html = f.read_text(encoding="utf-8", errors="replace")
            try:
                extracted, warnings, source = parse_pdmr.parse_announcement(
                    html, url=f"https://example.test/{f.stem}",
                    rns_id=f.stem,
                    announced_at="2026-05-19T09:00:00Z",
                )
            except Exception as e:  # pragma: no cover -- this IS the assertion
                self.fail(
                    f"Parser raised on cached filing {f.name}: "
                    f"{type(e).__name__}: {e}"
                )
            if extracted:
                non_empty += 1
        # Sanity: at least some of a 20-file random sample should produce
        # rows. If zero, something has broken the table-aware path.
        self.assertGreater(
            non_empty, 0,
            f"Random 20-file sample yielded ZERO extractions -- "
            f"parser likely broken. Sampled stems: "
            f"{[f.stem for f in sample]}",
        )


# ---------------------------------------------------------------------------
# Sprint 9 — _plausibility_check unit tests
# ---------------------------------------------------------------------------


class PlausibilityCheckTest(unittest.TestCase):
    """Direct unit tests for parse_pdmr._plausibility_check.

    Covers all five Phase-A rules (R1-R5) plus the allowlist bypass for
    R4. See docs/specs/sprint-plan-2026-05-22-sprint9.md Section 4 for
    rule definitions. These tests MUST pass on the first run -- they
    are the Phase A correctness gate.
    """

    def _row(self, **overrides):
        base = {
            "fingerprint": "fp", "date": "2026-05-19", "ticker": "TEST",
            "company": "Test plc", "director": "Jane Smith", "role": "CEO",
            "type": "BUY", "shares": 1000, "price": 1.50, "value": 1500.0,
        }
        base.update(overrides)
        return base

    # ---- happy path -------------------------------------------------------

    def test_normal_buy_passes(self):
        ok, reasons = parse_pdmr._plausibility_check(
            self._row(shares=1000, price=1.50, value=1500.0)
        )
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_normal_sell_passes(self):
        ok, reasons = parse_pdmr._plausibility_check(
            self._row(type="SELL", shares=5000, price=2.30, value=11500.0)
        )
        self.assertTrue(ok)

    # ---- R1: sub-pound value on a real trade ------------------------------

    def test_R1_fires_on_sub_pound_buy(self):
        # The Hutton Anpario-class case: BUY 4 shares @ 0p = £0.
        # Real-world example from dashboard 2026-05-22 (Richard Hutton,
        # 04 Nov 2025). value < £1, type != SIP/DIV/GRANT -> R1 fires.
        row = self._row(shares=4, price=0.0, value=0.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertFalse(ok)
        self.assertIn("R1_sub_pound_value", reasons)

    def test_R1_skips_SIP(self):
        # Legit micro-purchase SIP: £0.50 SIP topup must not trip R1.
        row = self._row(type="SIP", shares=2, price=0.25, value=0.50)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R1_sub_pound_value", reasons)

    def test_R1_skips_GRANT_with_zero_value(self):
        # Option grant with no cash exchanged -- value=0 is fine.
        row = self._row(type="GRANT", shares=50000, price=0.0, value=0.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R1_sub_pound_value", reasons)

    # ---- R2: tiny share count at sub-pound price --------------------------

    def test_R2_fires_on_tiny_shares_low_price(self):
        row = self._row(shares=4, price=0.05, value=0.20)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertFalse(ok)
        self.assertIn("R2_tiny_shares_low_price", reasons)

    def test_R2_skips_SIP(self):
        row = self._row(type="SIP", shares=4, price=0.05, value=0.20)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R2_tiny_shares_low_price", reasons)

    def test_R2_does_not_fire_on_realistic_low_price_with_many_shares(self):
        # Penny-stock with 50,000 shares @ 0.5p is a real pattern.
        row = self._row(shares=50_000, price=0.005, value=250.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R2_tiny_shares_low_price", reasons)

    # ---- R3: price too high -----------------------------------------------

    def test_R3_fires_above_200_gbp(self):
        # The Romberg WOSG case: price stored as £66,643.
        row = self._row(shares=12853, price=66643.0, value=856_562_479.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertFalse(ok)
        self.assertIn("R3_price_too_high", reasons)

    def test_R3_does_not_fire_at_200_gbp_boundary(self):
        # Exactly £200 — boundary case, must NOT fire (use > not >=).
        row = self._row(shares=100, price=200.0, value=20000.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R3_price_too_high", reasons)

    def test_R3_does_not_fire_for_high_pence_prices(self):
        # 9000p = £90. Should pass.
        row = self._row(shares=1000, price=90.0, value=90000.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R3_price_too_high", reasons)

    # ---- R4: excessive value with allowlist bypass ------------------------

    def test_R4_fires_above_100m_without_allowlist(self):
        row = self._row(
            ticker="WOSG", shares=12853, price=66643.0, value=856_562_479.0,
        )
        # Sprint 9 Phase B: kwarg renamed allowlist -> block_allowlist.
        ok, reasons = parse_pdmr._plausibility_check(
            row, block_allowlist=set()
        )
        self.assertIn("R4_excessive_value", reasons)

    def test_R4_bypassed_by_allowlist(self):
        # If WOSG were in the allowlist (it shouldn't be), R4 wouldn't fire.
        row = self._row(
            ticker="WOSG", shares=12853, price=66643.0, value=856_562_479.0,
        )
        ok, reasons = parse_pdmr._plausibility_check(
            row, block_allowlist={"WOSG"}
        )
        self.assertNotIn("R4_excessive_value", reasons)

    def test_R4_does_not_fire_at_100m_boundary(self):
        row = self._row(
            ticker="HSBA", shares=10_000_000, price=10.0, value=100_000_000.0,
        )
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R4_excessive_value", reasons)

    # ---- R5: shares looks like a date component ---------------------------

    def test_R5_fires_on_day_of_month_with_low_value(self):
        # 19 shares — looks like "May 19"; with value < £100 this is
        # almost certainly a date-component bleed.
        row = self._row(shares=19, price=0.1693, value=3.22)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertIn("R5_date_component_in_shares", reasons)

    def test_R5_fires_on_year_with_low_value(self):
        row = self._row(shares=2026, price=0.01, value=20.26)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertIn("R5_date_component_in_shares", reasons)

    def test_R5_does_not_fire_on_legit_small_grant(self):
        # 20 shares granted to a NED at £5.00 = £100 trade. Value is
        # exactly £100 (boundary), R5 must not fire.
        row = self._row(shares=20, price=5.00, value=100.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R5_date_component_in_shares", reasons)

    def test_R5_does_not_fire_when_value_is_substantial(self):
        # 20 shares @ £100 = £2,000. Looks like a date component but
        # the value is real — not a parsing error.
        row = self._row(shares=20, price=100.0, value=2000.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R5_date_component_in_shares", reasons)

    # ---- multi-rule firing ------------------------------------------------

    def test_multiple_rules_can_fire_for_one_row(self):
        # The Smothers GRG case: 19 shares @ 16.93p = £3.22. Trips R2
        # (shares<100, price<£1, type=BUY) AND R5 (shares=19 looks like
        # a day-of-month with value<£100). Does NOT trip R1 because
        # value=£3.22 is above the £1 threshold.
        row = self._row(shares=19, price=0.1693, value=3.22)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertFalse(ok)
        self.assertIn("R2_tiny_shares_low_price", reasons)
        self.assertIn("R5_date_component_in_shares", reasons)

    # ---- defensive: handles missing / zero fields -------------------------

    def test_handles_missing_fields(self):
        # An empty dict shouldn't crash; it just gets defaults and either
        # passes silently or trips whichever rules apply to zeroes.
        ok, reasons = parse_pdmr._plausibility_check({})
        # value=0 + type='' — R1 fires (0 < 1, '' not in non-trade-types).
        # R2 fires (0 < 100, 0 < 1, '' != 'SIP').
        self.assertFalse(ok)
        self.assertIn("R1_sub_pound_value", reasons)

    def test_module_constants_have_expected_shape(self):
        # Sprint 9 Phase B (2026-05-25): HBR seeded into the
        # institutional-block allowlist; LTI seeded into the high-priced
        # trust allowlist; nil-cost carve-out set defined for R1.
        # Sprint 11 (2026-05-28): NXT, AZN, GAW added to high-priced allowlist
        # (confirmed genuine >£100 stocks; BHP excluded — Yahoo shows ~£20).
        self.assertEqual(parse_pdmr.INSTITUTIONAL_BLOCK_ALLOWLIST, {"HBR"})
        self.assertEqual(parse_pdmr.HIGH_PRICED_TRUST_ALLOWLIST, {"LTI", "NXT", "AZN", "GAW"})
        self.assertEqual(
            parse_pdmr.NON_TRADE_TYPES_FOR_PLAUSIBILITY,
            frozenset({"SIP", "DIVIDEND", "GRANT"}),
        )
        self.assertEqual(
            parse_pdmr.NIL_COST_CARVEOUT_TYPES,
            frozenset({"GRANT", "EXERCISE"}),
        )

    # ---- Sprint 9 Phase B (2026-05-25) — new rules ------------------------

    def test_R1_nil_cost_grant_carveout(self):
        # GRANT with value=0 is the legit norm for option awards.
        # Phase B carve-out skips R1 even though value < £1 and type
        # is GRANT (which was already in NON_TRADE_TYPES, but assert
        # explicitly).
        row = self._row(type="GRANT", shares=50_000, price=0.0, value=0.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R1_sub_pound_value", reasons)

    def test_R1_nil_cost_exercise_carveout(self):
        # EXERCISE with value=0 (nil-cost option exercise / DSBP vest)
        # is legit. EXERCISE is NOT in NON_TRADE_TYPES, so without the
        # NIL_COST_CARVEOUT_TYPES carve-out R1 would fire — verify it
        # doesn't.
        row = self._row(type="EXERCISE", shares=10_000, price=0.0, value=0.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R1_sub_pound_value", reasons)

    def test_R1_still_fires_on_EXERCISE_with_nonzero_subpound_value(self):
        # An EXERCISE row with value > 0 but < £1 is still suspicious —
        # the carve-out only protects value == 0 exactly.
        row = self._row(type="EXERCISE", shares=100, price=0.005, value=0.50)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertIn("R1_sub_pound_value", reasons)

    def test_R3_LTI_default_allowlist_bypass(self):
        # Lindsell Train Investment Trust legitimately trades £800-1000+.
        # Default HIGH_PRICED_TRUST_ALLOWLIST should bypass R3.
        row = self._row(ticker="LTI", shares=100, price=830.50, value=83050.0)
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R3_price_too_high", reasons)

    def test_R4_HBR_default_allowlist_bypass(self):
        # Harbour Energy Potomac View £153m placing is genuine.
        # Default INSTITUTIONAL_BLOCK_ALLOWLIST should bypass R4.
        row = self._row(
            ticker="HBR", shares=100_000_000, price=1.53, value=153_000_000.0,
        )
        ok, reasons = parse_pdmr._plausibility_check(row)
        self.assertNotIn("R4_excessive_value", reasons)


# ---------------------------------------------------------------------------
# Sprint 9 Phase B — D.2 / D.4 helper unit tests
# ---------------------------------------------------------------------------


class Sprint9PhaseBValidatorTests(unittest.TestCase):
    """Direct unit tests for the new D.2 (date-bleed) and D.4 (director
    narrative) validators introduced in Sprint 9 Phase B.
    """

    # ---- D.4: _validate_director_cell narrative checks --------------------

    def test_D4_accepts_legit_name_with_title(self):
        # A real director name + role line should pass unchanged.
        out = parse_pdmr._validate_director_cell(
            "Jane Smith", company="Test plc"
        )
        self.assertEqual(out, "Jane Smith")

    def test_D4_rejects_narrative_capture_transaction(self):
        # AZN-class capture: the parser grabbed "Nature of the
        # transaction" as the director name.
        out = parse_pdmr._validate_director_cell(
            "Nature of the transaction", company="AstraZeneca plc",
        )
        self.assertIsNone(out)

    def test_D4_rejects_long_paragraph(self):
        # > 80 character cell — no real PDMR name+title is this long.
        long_capture = (
            "This notification is being made in accordance with Article 19 "
            "of Regulation EU No 596 2014 on market abuse"
        )
        out = parse_pdmr._validate_director_cell(
            long_capture, company="Test plc"
        )
        self.assertIsNone(out)

    def test_D4_rejects_sentence_with_multiple_commas(self):
        # 3+ commas — sentence structure, not a name.
        sentence = "The shares were purchased by John, Jane, Bob, and Alice"
        out = parse_pdmr._validate_director_cell(sentence, company="Test plc")
        self.assertIsNone(out)

    # ---- D.2: _looks_like_date_bleed --------------------------------------

    def test_D2_rejects_day_of_month_with_month_word_and_no_price(self):
        # "19" extracted as volume from "...19 May 2026..."; no
        # companion price; should be flagged.
        block = "acquired 1,615 ordinary shares on 19 May 2026"
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(19, block, price_gbp=0.0)
        )

    def test_D2_accepts_day_range_volume_when_price_is_present(self):
        # 19 shares is rare but legit if there's a real price too.
        # The trigger requires price_gbp < 1.0 — a present price means
        # the regex grabbed the right number.
        block = "Price £5.00 Volume 19 shares"
        self.assertFalse(
            parse_pdmr._looks_like_date_bleed(19, block, price_gbp=5.0)
        )

    def test_D2_rejects_year_only_isolated_integer(self):
        # "2026" extracted as volume from a narrative containing only
        # that integer.
        block = "...transacted in the year 2026 by the PDMR..."
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2026, block, price_gbp=0.0)
        )

    def test_D2_rejects_tiny_share_count_with_no_companion_price(self):
        # Single-digit share count + price=0 → date fragment grab.
        block = "share count 5 with no price label"
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(5, block, price_gbp=0.0)
        )


# ---------------------------------------------------------------------------
# Sprint 11 Fix #1 — _parse_volume_cell date-bleed defence
# ---------------------------------------------------------------------------


class Sprint11ParseVolumeCellTests(unittest.TestCase):
    """Direct unit tests for parse_pdmr._parse_volume_cell after Sprint 11
    Fix #1 ports `_looks_like_date_bleed()` into the table-aware path.

    Pre-fix: the table-aware parser returned the first int >= 1 from the
    volume cell with no date-bleed check, leading to 38 DB rows with
    `shares == year(transaction_date)` (e.g. TLW 9585916 stored as 2026
    instead of 115,000). See docs/audits/audit_2026-05-27_initial.md.

    Post-fix: year-only and day-only candidates are skipped; a clean
    integer (115,000) still returns normally.
    """

    def test_year_only_returns_zero_with_volume_only_contained_dates(self):
        # "2026" alone: Trigger 2 (val in 1990..2099, only integer in
        # block) fires → date-bleed → no candidate survives.
        shares, warnings = parse_pdmr._parse_volume_cell("2026")
        self.assertEqual(shares, 0)
        self.assertIn("volume_only_contained_dates", warnings)

    def test_year_only_with_whitespace_returns_zero(self):
        # Whitespace padding mustn't change behaviour.
        shares, warnings = parse_pdmr._parse_volume_cell("   2026   ")
        self.assertEqual(shares, 0)
        self.assertIn("volume_only_contained_dates", warnings)

    def test_date_string_only_returns_zero(self):
        # "DD/MM/2026 2026" — the literal "DD" and "MM" are not numeric,
        # so NUMBER_RE only matches the year 2026 twice. Trigger 2
        # (val in 1990..2099 AND no OTHER distinct integer in the block)
        # fires for the year → date-bleed → no candidate survives.
        shares, warnings = parse_pdmr._parse_volume_cell("DD/MM/2026 2026")
        self.assertEqual(shares, 0)
        self.assertIn("volume_only_contained_dates", warnings)

    def test_clean_volume_returns_int(self):
        # The corrected TLW 9585916 case: a clean "115,000" must still
        # return (115000, []) — the fix must not over-reject.
        shares, warnings = parse_pdmr._parse_volume_cell("115,000")
        self.assertEqual(shares, 115000)
        self.assertEqual(warnings, [])

    def test_empty_cell_returns_could_not_separate(self):
        # Regression — the original empty-cell warning must remain
        # distinct from the new "volume_only_contained_dates" signal so
        # we can tell parser-source from input-source failures apart.
        shares, warnings = parse_pdmr._parse_volume_cell("")
        self.assertEqual(shares, 0)
        self.assertEqual(warnings, ["could_not_separate_price_volume"])

    def test_no_integer_candidates_returns_could_not_separate(self):
        # A cell with only floats/text (no whole-share candidate) must
        # take the legacy `could_not_separate_price_volume` warning path,
        # NOT the new `volume_only_contained_dates` one.
        shares, warnings = parse_pdmr._parse_volume_cell("price only £1.50")
        self.assertEqual(shares, 0)
        # Either is plausible — what matters is we don't claim the
        # cell contained only dates when it contained no candidates at all.
        self.assertNotIn("volume_only_contained_dates", warnings)


class Sprint11LooksLikeDateBleedTrigger2Tests(unittest.TestCase):
    """Sprint 11 Fix #1 REDO — direct tests for the revised Trigger 2
    in `_looks_like_date_bleed`. The real Tullow filing 9585916 flowed
    through the LEGACY regex path (`parser_source = 'regex'`) and the
    pre-revision Trigger 2 let `2026` through because day-of-month
    integers (`10`, `22`) in the captured block counted as "other
    integers". The revised Trigger 2 filters out integers that would
    themselves trip Trigger 1 (1..31 + month word) before checking
    whether the year is isolated.

    See docs/specs/sprint-11-fix1-qa.md and Section 4 Fix #1 (REVISED)
    of docs/specs/sprint-11-parser-hardening-plan.md.
    """

    def test_year_with_day_and_par_value_now_flagged(self):
        # The actual failure case: narrative bleed
        #   "ordinary shares of 10p each on May 22, 2026"
        # Integers in block: 10 (par value), 22 (day), 2026 (year).
        # Pre-fix: `10` and `22` were "other ints" → Trigger 2 failed →
        #          2026 was returned as the share count (38 DB rows).
        # Post-fix: `10` and `22` are filtered (1..31 + month word) →
        #           `2026` becomes the only surviving integer →
        #           Trigger 2 fires → True.
        block = "ordinary shares of 10p each on May 22, 2026"
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2026, block, 0.0),
            f"Expected Trigger 2 to flag 2026 as a date-bleed in {block!r}",
        )

    def test_year_with_real_share_count_now_flagged_hard_guard(self):
        # Year-as-shares refix (2026-05-31, Step A): the "other integers
        # present" escape hatch is REMOVED. A 4-digit year is now ALWAYS
        # rejected by Trigger 2, even when a plausible share count (1,615)
        # also appears in the block. This is the locked decision: a bare or
        # prose-embedded year can never be accepted as a volume; the rare
        # genuine ~2,0xx-share holding is surfaced via pending-review instead
        # of being silently trusted. (Previously this test asserted False.)
        block = "1,615 ordinary shares on May 19, 2026"
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2026, block, 0.0),
            f"Hard year guard: Trigger 2 must flag 2026 in {block!r} "
            "regardless of other integers present.",
        )

    def test_year_with_only_day_integers_now_flagged(self):
        # Narrative-only bleed with no par value: "on May 22, 2026".
        # Year hard guard fires unconditionally.
        block = "on May 22, 2026"
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2026, block, 0.0),
            f"Expected Trigger 2 to flag 2026 in {block!r}",
        )

    def test_year_no_month_word_now_flagged_hard_guard(self):
        # Year-as-shares refix (2026-05-31, Step A): with the escape hatch
        # removed, the presence of other integers and the absence of a month
        # word no longer matter — a value in 1990..2099 is always flagged.
        # (Previously this test asserted False under the old isolated-year
        # logic.)
        block = "10 and 22 and 2026"
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2026, block, 0.0),
            f"Hard year guard: Trigger 2 must flag 2026 in {block!r} "
            "even with other integers and no month word.",
        )


class Sprint11TullowRealLegacyPathTest(unittest.TestCase):
    """Sprint 11 Fix #1 REDO — end-to-end regression test against the
    real Tullow filing 9585916 HTML (the actual filing that produced
    the year-as-shares=2026 bug in the live DB).

    QA report sprint-11-fix1-qa.md showed that:
      - This filing routes via the LEGACY regex path (parser_source='regex')
      - The first build's table-aware patch had no effect on it
      - The legacy `_VOLUME_LABEL_RE` matched a narrative bleed
        `"shares of 10p each on May 22, 2026..."` and `_looks_like_date_bleed`
        Trigger 2 let `2026` through because `10` and `22` counted as
        "other integers".

    Post-revision contract:
      - shares MUST NOT be 2026 (the FAIL mode)
      - shares should be either 115000 (clean extraction) or 0 (row
        dropped with a date-bleed warning) — both are operationally clean.
      - parser_source MUST be 'regex' (confirms the legacy path routing
        — if this changes, the test design is no longer probing the
        path the bug lives on).
    """

    FIXTURE = (
        Path(__file__).resolve().parent
        / "fixtures" / "parser" / "tlw_9585916_real.html"
    )

    def test_sprint11_tlw_real_legacy_path(self):
        self.assertTrue(
            self.FIXTURE.exists(),
            f"Sprint 11 fixture missing: {self.FIXTURE}",
        )
        html = self.FIXTURE.read_text(encoding="utf-8")
        extracted, warnings, source = parse_pdmr.parse_announcement(
            html,
            url="https://example.test/announcement/rns/tullow-oil-plc--tlw/test/9585916",
            rns_id="fixture-tlw_9585916_real",
            announced_at="2026-05-22T09:00:00Z",
            headline="(TLW) Director/PDMR Shareholding",
            ticker_hint="TLW",
        )

        # Routing assertion — if this fails, the path the bug lives on
        # has changed and the rest of the test is no longer probing
        # the right surface. Re-derive Fix #1 if needed.
        self.assertEqual(
            source, "regex",
            f"Expected legacy regex path for TLW 9585916. "
            f"Got source={source!r}. If routing changed, this test "
            f"no longer probes the path the bug originated from.",
        )

        # The post-fix contract: at least one row was extracted (the
        # filing is a real PDMR announcement) but the FAIL mode must
        # not appear. If extraction dropped the row entirely (0 rows),
        # that is also acceptable — the bug is "stored wrong number",
        # not "didn't store".
        if extracted:
            self.assertNotEqual(
                extracted[0].get("shares"), 2026,
                f"Sprint 11 Fix #1 FAIL: parser still returns shares=2026 "
                f"for the real Tullow filing. Row: {extracted[0]!r}. "
                f"Warnings: {warnings!r}",
            )
            self.assertIn(
                extracted[0].get("shares"), (115000, 0),
                f"Sprint 11 Fix #1: shares must be 115000 (clean) or 0 "
                f"(dropped). Got {extracted[0].get('shares')!r}. "
                f"Full row: {extracted[0]!r}. Warnings: {warnings!r}",
            )


# ---------------------------------------------------------------------------
# Sprint 11 Fix #2 — duplicate-number-pull guard (price == shares)
# ---------------------------------------------------------------------------


def _apply_duplicate_number_pull_guard(price, shares):
    """Mirror of the Sprint 11 Fix #2 guard logic embedded in
    parse_pdmr.parse_announcement (legacy path) and
    parse_pdmr._extract_via_table (defensive). Kept here for
    direct positive/boundary unit tests that don't need a full
    HTML round-trip.

    Contract (must stay byte-identical to the production guard):
      Trigger only when BOTH fields exceed 1000 AND are within
      0.5 of each other. On trigger: both fields zeroed and the
      warning `duplicate_number_pull` is added.
    """
    warnings: list = []
    if (price is not None and shares is not None
            and float(price) > 1000.0
            and int(shares) > 1000
            and abs(float(price) - float(shares)) < 0.5):
        price, shares = 0.0, 0
        warnings.append("duplicate_number_pull")
    return price, shares, warnings


class Sprint11DuplicateNumberPullTests(unittest.TestCase):
    """Sprint 11 Fix #2 — direct unit tests for the duplicate-number-pull
    guard.

    Background. Five rows in the live DB had `price == shares` because
    the legacy regex path pulled the same number into both fields
    (FAN GRANT 41,444 x £41,444 = £1.72B). All five were
    `parser_source = 'regex'` — the table-aware path has not produced
    this class of bug, but the guard is applied defensively there too.

    Threshold rationale. `>1000` (strict) on BOTH fields. Lower
    thresholds (`>100`) would false-positive on Games Workshop
    (GAW 2025-11-21: 184 shares at £184 each — Yahoo confirms GAW
    trades around £187). The boundary tests below pin this contract.

    See:
      - docs/specs/sprint-11-parser-hardening-plan.md Section 4 Fix #2
      - .scripts/fixtures/parser/gaw_9254618_real_184sh.html (negative)
    """

    # ---- Positive cases — the 4 confirmed bad rows -----------------------

    def test_fan_grant_41444(self):
        """FAN GRANT 41,444 shares at £41,444 each (= £1.72B). Bad row."""
        price, shares, w = _apply_duplicate_number_pull_guard(41444.0, 41444)
        self.assertEqual(price, 0.0)
        self.assertEqual(shares, 0)
        self.assertIn("duplicate_number_pull", w)

    def test_fan_grant_29001(self):
        """FAN GRANT 29,001 shares at £29,001 each. Bad row."""
        price, shares, w = _apply_duplicate_number_pull_guard(29001.0, 29001)
        self.assertEqual(price, 0.0)
        self.assertEqual(shares, 0)
        self.assertIn("duplicate_number_pull", w)

    def test_tko_6000(self):
        """TKO 6,000 shares at £6,000 each. Bad row."""
        price, shares, w = _apply_duplicate_number_pull_guard(6000.0, 6000)
        self.assertEqual(price, 0.0)
        self.assertEqual(shares, 0)
        self.assertIn("duplicate_number_pull", w)

    def test_tko_4000(self):
        """TKO 4,000 shares at £4,000 each. Bad row."""
        price, shares, w = _apply_duplicate_number_pull_guard(4000.0, 4000)
        self.assertEqual(price, 0.0)
        self.assertEqual(shares, 0)
        self.assertIn("duplicate_number_pull", w)

    # ---- Boundary cases — pin the `> 1000` contract ----------------------

    def test_boundary_1000_exact_does_not_trigger(self):
        """shares=1000, price=1000.0 must NOT trigger — the guard is
        strict `>1000`, not `>=1000`. A 1000sh x £1000 row is rare but
        plausible (e.g. very-high-priced infrequent trade) and we
        prefer not to drop it on the boundary."""
        price, shares, w = _apply_duplicate_number_pull_guard(1000.0, 1000)
        self.assertEqual(price, 1000.0)
        self.assertEqual(shares, 1000)
        self.assertNotIn("duplicate_number_pull", w)

    def test_boundary_1001_does_trigger(self):
        """shares=1001, price=1001.0 SHOULD trigger — first integer
        strictly above the threshold on both sides."""
        price, shares, w = _apply_duplicate_number_pull_guard(1001.0, 1001)
        self.assertEqual(price, 0.0)
        self.assertEqual(shares, 0)
        self.assertIn("duplicate_number_pull", w)

    # ---- Negative cases — GAW class must be preserved --------------------

    def test_gaw_184_real_must_not_trigger(self):
        """Games Workshop GAW: 184 shares at £184. Real Yahoo-confirmed
        trade (GAW trades around £187 GBP). Direct unit test against
        the guard — must not zero out."""
        price, shares, w = _apply_duplicate_number_pull_guard(184.0, 184)
        self.assertEqual(price, 184.0)
        self.assertEqual(shares, 184)
        self.assertNotIn("duplicate_number_pull", w)


class Sprint11Fix2GawNegativeFixtureTest(unittest.TestCase):
    """Sprint 11 Fix #2 — end-to-end NEGATIVE test against the real
    Games Workshop filing 9254618 HTML. Pins that the guard does NOT
    fire on the genuine 184sh x £184 row that originally motivated
    the threshold tightening from `>100` to `>1000`.

    The full filing also exercises the legacy regex emission path —
    so this doubles as a confirmation that the guard sits in the
    right place in `parse_announcement` and doesn't accidentally
    rewrite real low-value rows.
    """

    FIXTURE = (
        Path(__file__).resolve().parent
        / "fixtures" / "parser" / "gaw_9254618_real_184sh.html"
    )

    def test_gaw_real_fixture_does_not_trigger_guard(self):
        if not self.FIXTURE.exists():
            self.skipTest(
                f"Sprint 11 Fix #2 GAW fixture missing: {self.FIXTURE}. "
                f"Fixture is copied from .scripts/_scrape_cache/9254618.html "
                f"during sprint-11 build."
            )
        html = self.FIXTURE.read_text(encoding="utf-8")
        extracted, warnings, source = parse_pdmr.parse_announcement(
            html,
            url=(
                "https://www.investegate.co.uk/announcement/rns/"
                "games-workshop-group--gaw/director-pdmr-shareholding/9254618"
            ),
            rns_id="fixture-gaw_9254618_real_184sh",
            announced_at="2025-11-21T09:00:00Z",
            headline="(GAW) Director/PDMR Shareholding",
            ticker_hint="GAW",
        )

        # The guard must not have fired anywhere in the parse — neither
        # the table-aware path nor the legacy path nor any per-row
        # warning list rolled up into the top-level warnings.
        self.assertNotIn(
            "duplicate_number_pull", warnings,
            f"Sprint 11 Fix #2 FAIL: guard fired on real GAW filing "
            f"(184 shares x £184). Threshold of >1000 should have "
            f"preserved this row. Warnings: {warnings!r}. "
            f"Extracted: {extracted!r}",
        )

        # If the parser successfully extracted any rows, none of their
        # warnings (which are flattened into the top-level list in both
        # paths but checked again here for paranoia) should carry the
        # marker either, and the price/shares must not be the zeroed
        # `(0.0, 0)` the guard produces.
        for i, row in enumerate(extracted):
            self.assertNotEqual(
                (row.get("price"), row.get("shares")), (0.0, 0),
                f"Sprint 11 Fix #2: GAW row {i} was zeroed — "
                f"guard misfired. Row: {row!r}",
            )


class Sprint11DirectorValidatorTests(unittest.TestCase):
    """Sprint 11 Fix #3 — direct unit tests for the boilerplate +
    truncated-extraction additions to ``_validate_director_cell``.

    Background. 6 rows in the live DB had a `director` field that was
    either boilerplate text ("Person closely associated with Daniel
    Rabie", "Trustee of the Kimberly A Nelson Revocable Trust") or a
    2-3 char truncated fragment ("Bl", "Ant"). All `parser_source =
    'regex'` failures. See audit_2026-05-27_initial.md and
    docs/specs/sprint-11-parser-hardening-plan.md Section 4 Fix #3.

    Fix adds two new checks inside `_validate_director_cell`, AFTER
    newline normalisation and BEFORE the existing D.4 narrative-capture
    pass:

      1. ``_BOILERPLATE_DIRECTOR_RE.match(s)`` → reject. Catches
         "Person closely associated", "Trustee of", "PDMR", "Notifier",
         "The Company", "Director" (bare), "Managerial responsibilities".
      2. ``len(s) < 4`` → reject. Catches "Bl", "Ant", "Joh".

    False-positive trap (pinned by negative tests below): real PDMR
    names with short first names or initials must still pass. The
    rule applies to the WHOLE stripped director string, not the first
    word — so "A Dolan", "Al Cook", "Dr Smith", "J Lyttle" (all >= 4
    chars total) survive. "John" (exactly 4 chars) is the minimum
    legitimate name we accept.
    """

    # ---- Positive rejections (must return None) --------------------------

    def test_rejects_two_char_truncation_bl(self):
        """HLN bug — 'Bl' captured as full director name."""
        out = parse_pdmr._validate_director_cell("Bl", company="Haleon plc")
        self.assertIsNone(out)

    def test_rejects_three_char_truncation_ant(self):
        """LGEN bug (x2 rows) — 'Ant' captured as full director name."""
        out = parse_pdmr._validate_director_cell("Ant", company="Legal & General plc")
        self.assertIsNone(out)

    def test_rejects_three_char_truncation_joh(self):
        """Boundary: 3-char fragment must be rejected."""
        out = parse_pdmr._validate_director_cell("Joh", company="Test plc")
        self.assertIsNone(out)

    def test_rejects_person_closely_associated_phrase(self):
        """GETB bug — boilerplate phrase captured instead of real PDMR name."""
        out = parse_pdmr._validate_director_cell(
            "Person closely associated with Daniel Rabie",
            company="GetBusy plc",
        )
        self.assertIsNone(out)

    def test_rejects_person_closely_associated_caps(self):
        """Case-insensitive — 'PERSON CLOSELY ASSOCIATED' must also fail."""
        out = parse_pdmr._validate_director_cell(
            "PERSON CLOSELY ASSOCIATED", company="Test plc",
        )
        self.assertIsNone(out)

    def test_rejects_trustee_of_phrase(self):
        """TATE bug — 'Trustee of the Kimberly A Nelson Revocable Trust'."""
        out = parse_pdmr._validate_director_cell(
            "Trustee of the Kimberly A Nelson Revocable Trust",
            company="Tate & Lyle plc",
        )
        self.assertIsNone(out)

    def test_rejects_bare_pdmr(self):
        """'PDMR' alone is a label, not a name."""
        out = parse_pdmr._validate_director_cell("PDMR", company="Test plc")
        self.assertIsNone(out)

    def test_rejects_bare_the_company(self):
        """'The Company' alone is a label."""
        out = parse_pdmr._validate_director_cell(
            "The Company", company="Test plc",
        )
        self.assertIsNone(out)

    def test_rejects_bare_director(self):
        """'Director' alone is a label — must match the 'director$' branch
        of the boilerplate regex (anchored end, single token only)."""
        out = parse_pdmr._validate_director_cell("Director", company="Test plc")
        self.assertIsNone(out)

    # ---- Negative cases — real names MUST survive ------------------------

    def test_accepts_single_initial_surname_a_dolan(self):
        """'A Dolan' — single initial + surname. Real PDMR cell shape.
        The `len < 4` rule applies to the WHOLE stripped string (7
        chars including space), not the first word ('A' alone)."""
        out = parse_pdmr._validate_director_cell("A Dolan", company="Test plc")
        self.assertEqual(out, "A Dolan")

    def test_accepts_short_first_name_al_cook(self):
        """'Al Cook' — 2-char first name + 4-char surname. Real PDMR."""
        out = parse_pdmr._validate_director_cell("Al Cook", company="Test plc")
        self.assertEqual(out, "Al Cook")

    def test_accepts_title_surname_dr_smith(self):
        """'Dr Smith' — honorific + surname. Real PDMR cell shape."""
        out = parse_pdmr._validate_director_cell("Dr Smith", company="Test plc")
        self.assertEqual(out, "Dr Smith")

    def test_accepts_single_initial_surname_j_lyttle(self):
        """'J Lyttle' — single initial + surname."""
        out = parse_pdmr._validate_director_cell("J Lyttle", company="Test plc")
        self.assertEqual(out, "J Lyttle")

    def test_accepts_full_name_richard_miller(self):
        """Sanity — a normal full name still passes."""
        out = parse_pdmr._validate_director_cell(
            "Richard Miller", company="Test plc",
        )
        self.assertEqual(out, "Richard Miller")

    def test_accepts_boundary_four_char_john(self):
        """Boundary: 4-char name must PASS (the rule is `< 4`, not `<= 4`).
        'John', 'Anna', 'Liam' are all real first names in the corpus."""
        out = parse_pdmr._validate_director_cell("John", company="Test plc")
        self.assertEqual(out, "John")


class Sprint11RoleValidatorTests(unittest.TestCase):
    """Sprint 11 Fix #4 — direct unit tests for ``_validate_role_cell``.

    Background. 25 rows in the live DB had a `role` field that was
    free-text prose bled in from the announcement body — ", at the date
    of grant", "in this regard", "of senior employees and directors at
    Gateley...", "ing Officer, who purchased 99 ordinary shares...".
    All 25 are `parser_source = 'regex'` failures. See
    audit_2026-05-27_initial.md and
    docs/specs/sprint-11-parser-hardening-plan.md Section 4 Fix #4
    (REVISED 2026-05-27 after pre-dispatch check).

    Three rules + a normalisation:
      1. Starts with `,` `.` `;` `:` → reject (sentence-mid bleed)
      2. Length > 80 → reject (titles aren't sentences)
      3. First char islower() and not punctuation → title-case the
         first char only (preserves "interim Chief Financial Officer"
         and "group Senior Executive Vice-President").

    False-positive trap (pinned by negative tests below): the spec
    explicitly forbids a blanket lowercase-reject because "interim CFO"
    and "acting CEO" are legitimate role designations.

    Known limitation (documented, accepted): a long-enough bleed that
    starts with a lowercase NON-punctuation word and stays under 80
    chars (e.g. "the business to capitalise on opportunities in its
    markets", CKN ×4 in the live DB) WILL be preserved by this fix as
    "The business...". Catching it would require a vocabulary rule
    that risks false-positives on real titles; spec accepts the
    trade-off.
    """

    # ---- Positive rejections (must return None) --------------------------
    # Drawn from the 25 known bad rows in the live DB.

    def test_rejects_leading_comma_at_date_of_grant(self):
        """MUL / VIC bug — ', at the date of grant' bled into role field."""
        out = parse_pdmr._validate_role_cell(", at the date of grant")
        self.assertIsNone(out)

    def test_rejects_leading_period(self):
        """A sentence-end fragment that bled into the role cell."""
        out = parse_pdmr._validate_role_cell(". The PDMR is a Director")
        self.assertIsNone(out)

    def test_rejects_leading_semicolon(self):
        """Sentence-mid bleed starting with semicolon."""
        out = parse_pdmr._validate_role_cell("; and Chief Financial Officer")
        self.assertIsNone(out)

    def test_rejects_leading_colon(self):
        """Sentence-mid bleed starting with colon."""
        out = parse_pdmr._validate_role_cell(": Chief Operating Officer")
        self.assertIsNone(out)

    def test_rejects_long_prose_ing_officer(self):
        """HMSO bug — 'ing Officer, who purchased 99 ordinary shares at a
        price of £3.241 per share through a Dividend Reinvestment Plan
        on 12' (>80 chars, sentence-fragment start)."""
        bleed = (
            "ing Officer, who purchased 99 ordinary shares at a price of "
            "£3.241 per share through a Dividend Reinvestment Plan on 12"
        )
        self.assertGreater(len(bleed), 80)
        out = parse_pdmr._validate_role_cell(bleed)
        self.assertIsNone(out)

    def test_rejects_long_prose_value_for_customers(self):
        """MER bug — 's are intended to create value for our customers and
        the people they serve while also driving sustainable financial
        retu' (>80 chars)."""
        bleed = (
            "s are intended to create value for our customers and the "
            "people they serve while also driving sustainable financial retu"
        )
        self.assertGreater(len(bleed), 80)
        out = parse_pdmr._validate_role_cell(bleed)
        self.assertIsNone(out)

    def test_rejects_long_prose_senior_employees(self):
        """GTLY bug — 'of senior employees and directors at Gateley...'
        (>80 chars with full context)."""
        bleed = (
            "of senior employees and directors at Gateley (Holdings) Plc "
            "as part of the long-term incentive plan"
        )
        self.assertGreater(len(bleed), 80)
        out = parse_pdmr._validate_role_cell(bleed)
        self.assertIsNone(out)

    def test_rejects_short_in_this_regard_via_length_does_not_apply(self):
        """V3TC bug — 'in this regard' is 14 chars, lowercase start, NOT
        punctuation, NOT >80. Documented known limitation: this WILL
        pass through this fix as 'In this regard'. Captured as an
        acknowledged trade-off rather than a regression — see the spec
        Section 4 Fix #4 'Known limitation' paragraph."""
        out = parse_pdmr._validate_role_cell("in this regard")
        # Trade-off: preserved with title-cased first letter. This is
        # the spec-acknowledged behaviour, not a bug. If a future
        # sprint adds a vocabulary-based rule, the assertion below
        # should flip to assertIsNone.
        self.assertEqual(out, "In this regard")

    def test_rejects_exact_eighty_one_chars(self):
        """Boundary — 81 chars must reject."""
        s = "A" + "b" * 80  # 81 chars total, starts uppercase so length
                            # is the only rule that can reject it.
        self.assertEqual(len(s), 81)
        out = parse_pdmr._validate_role_cell(s)
        self.assertIsNone(out)

    def test_rejects_very_long_string(self):
        """100-char input must reject."""
        out = parse_pdmr._validate_role_cell("X" * 100)
        self.assertIsNone(out)

    def test_rejects_punctuation_with_whitespace(self):
        """Leading whitespace + punctuation — strip first, then check."""
        out = parse_pdmr._validate_role_cell("   , at the date of grant")
        self.assertIsNone(out)

    # ---- Negative preservations — real roles MUST survive ----------------

    def test_preserves_chief_financial_officer(self):
        """Sanity — a normal title passes unchanged."""
        out = parse_pdmr._validate_role_cell("Chief Financial Officer")
        self.assertEqual(out, "Chief Financial Officer")

    def test_preserves_chief_executive_officer(self):
        """Sanity — another normal title."""
        out = parse_pdmr._validate_role_cell("Chief Executive Officer")
        self.assertEqual(out, "Chief Executive Officer")

    def test_preserves_director(self):
        """Single-word 'Director' is a legitimate role — must pass."""
        out = parse_pdmr._validate_role_cell("Director")
        self.assertEqual(out, "Director")

    def test_preserves_ceo_acronym(self):
        """Acronym 'CEO' is a real role — must pass."""
        out = parse_pdmr._validate_role_cell("CEO")
        self.assertEqual(out, "CEO")

    def test_normalises_interim_cfo(self):
        """RENX ×2 — 'interim Chief Financial Officer' is a REAL title
        with sloppy source-side casing. Must NOT be rejected. First char
        title-cased so the canonical DB form is 'Interim...'."""
        out = parse_pdmr._validate_role_cell("interim Chief Financial Officer")
        self.assertEqual(out, "Interim Chief Financial Officer")

    def test_normalises_group_svp(self):
        """BNC ×3 — 'group Senior Executive Vice-President' is a real
        title with wrong-case source. Must NOT be rejected. First char
        title-cased to 'Group...'."""
        out = parse_pdmr._validate_role_cell(
            "group Senior Executive Vice-President"
        )
        self.assertEqual(out, "Group Senior Executive Vice-President")

    def test_normalises_acting_ceo(self):
        """Defensive — 'acting CEO' is the canonical 'acting/interim'
        pattern. Must normalise to 'Acting CEO'."""
        out = parse_pdmr._validate_role_cell("acting CEO")
        self.assertEqual(out, "Acting CEO")

    def test_preserves_boundary_eighty_chars(self):
        """Boundary — 80 chars exactly must PASS (rule is `> 80`, not
        `>= 80`). Build a synthetic 80-char title that starts uppercase
        so length is the only condition tested."""
        s = "A" + "b" * 79  # 80 chars total
        self.assertEqual(len(s), 80)
        out = parse_pdmr._validate_role_cell(s)
        self.assertEqual(out, s)

    def test_preserves_with_trailing_whitespace(self):
        """Leading/trailing whitespace is stripped before validation."""
        out = parse_pdmr._validate_role_cell("  Chief Financial Officer  ")
        self.assertEqual(out, "Chief Financial Officer")

    # ---- Edge cases ------------------------------------------------------

    def test_empty_string_returns_none(self):
        """Empty input is treated as 'no role'."""
        self.assertIsNone(parse_pdmr._validate_role_cell(""))

    def test_none_input_returns_none(self):
        """None passes through as None (caller doesn't have to pre-check)."""
        self.assertIsNone(parse_pdmr._validate_role_cell(None))

    def test_whitespace_only_returns_none(self):
        """Whitespace-only input is treated as empty."""
        self.assertIsNone(parse_pdmr._validate_role_cell("   \t  "))


# ---------------------------------------------------------------------------
# Sprint 11 Fix #5 — director-name normalisation
# ---------------------------------------------------------------------------


class Sprint11NameNormalisationTests(unittest.TestCase):
    """Sprint 11 Fix #5 — direct unit tests for ``_normalise_director_name``.

    Background. Pre-flight scan found 9 known capitalisation-variant
    identities (DEREK MAPP/Derek Mapp; KATE ROCK/Kate Rock; PHIL BENTLEY;
    etc.) and 84 total all-CAPS director rows in the live corpus.
    Without normalisation each variant gets its own cluster_id, splitting
    one PDMR into two clusters and inflating cluster-count metrics.

    The helper handles: plain Title-Case, lowercase particles ("of", "van",
    "de" at non-initial position), Mc-prefix surnames (algorithmic),
    Mac-prefix surnames (exception list — MacKinnon vs Macaulay), apostrophe
    surnames (O'Brien, D'Souza), hyphenated names (Jean-Benoit,
    Seymour-Jackson), post-nominals (CMG/OBE/CBE/MBE — preserved uppercase),
    and Unicode characters (Benoît Macé via Python 3's Unicode-aware
    str.capitalize).

    Idempotency invariant: applying the helper twice produces the same
    result as applying it once. Every test below that ends with an
    assertEqual implicitly relies on this.
    """

    # ---- Core normalisation: all-CAPS -> Title-Case ----------------------

    def test_derek_mapp_uppercase_normalises(self):
        """The original Sprint 11 motivating case — DEREK MAPP cluster
        was split from Derek Mapp until this helper landed."""
        out = parse_pdmr._normalise_director_name("DEREK MAPP")
        self.assertEqual(out, "Derek Mapp")

    def test_kate_rock_uppercase_normalises(self):
        """KATE ROCK / Kate Rock — one of the 9 confirmed duplicate
        identities in the live DB."""
        out = parse_pdmr._normalise_director_name("KATE ROCK")
        self.assertEqual(out, "Kate Rock")

    def test_phil_bentley_uppercase_normalises(self):
        """PHIL BENTLEY / Phil Bentley duplicate identity."""
        out = parse_pdmr._normalise_director_name("PHIL BENTLEY")
        self.assertEqual(out, "Phil Bentley")

    # ---- Idempotency ------------------------------------------------------

    def test_idempotent_already_titlecase(self):
        """Applying to an already-canonical name is a no-op."""
        out = parse_pdmr._normalise_director_name("Derek Mapp")
        self.assertEqual(out, "Derek Mapp")

    def test_idempotent_double_application(self):
        """f(f(x)) == f(x) — the core idempotency contract."""
        once = parse_pdmr._normalise_director_name("DEREK MAPP")
        twice = parse_pdmr._normalise_director_name(once)
        self.assertEqual(once, twice)

    # ---- Empty / whitespace / None inputs --------------------------------

    def test_empty_string_preserved(self):
        """Empty string round-trips unchanged (falsy short-circuit)."""
        out = parse_pdmr._normalise_director_name("")
        self.assertEqual(out, "")

    def test_none_preserved(self):
        """None round-trips unchanged (caller doesn't have to pre-check)."""
        out = parse_pdmr._normalise_director_name(None)
        self.assertIsNone(out)

    def test_whitespace_only_preserved(self):
        """Whitespace-only input: stripped is empty, so input returns
        unchanged (avoids losing the caller's whitespace marker)."""
        out = parse_pdmr._normalise_director_name("  ")
        self.assertEqual(out, "  ")

    # ---- Mc-prefix surnames (algorithmic, NO exception entry) ------------

    def test_mc_already_canonical_idempotent(self):
        """McDonald is already correct — no change."""
        out = parse_pdmr._normalise_director_name("Iain McDonald")
        self.assertEqual(out, "Iain McDonald")

    def test_mc_lowercase_normalises(self):
        """iain mcdonald — recognise the mc prefix and uppercase the
        following letter while lowercasing the rest."""
        out = parse_pdmr._normalise_director_name("iain mcdonald")
        self.assertEqual(out, "Iain McDonald")

    def test_mc_uppercase_normalises(self):
        """ALEX MCINTOSH — the Mc algorithm should still fire even when
        the input is all-caps (post-_normalise_word sees 'MCINTOSH')."""
        out = parse_pdmr._normalise_director_name("ALEX MCINTOSH")
        self.assertEqual(out, "Alex McIntosh")

    # ---- Apostrophe surnames (O'Brien, O'Donnell, O'Connell) -------------

    def test_apostrophe_already_canonical_idempotent(self):
        """O'Brien already canonical — no change."""
        out = parse_pdmr._normalise_director_name("Andy O'Brien")
        self.assertEqual(out, "Andy O'Brien")

    def test_apostrophe_uppercase_normalises(self):
        """FRANK O'DONNELL — capitalise before and after the apostrophe.
        Matches the 14 apostrophe surnames found in the pre-flight scan."""
        out = parse_pdmr._normalise_director_name("FRANK O'DONNELL")
        self.assertEqual(out, "Frank O'Donnell")

    def test_apostrophe_lowercase_normalises(self):
        """billy o'connell — fully lowercase input must round-trip via
        the apostrophe-split + capitalise rules."""
        out = parse_pdmr._normalise_director_name("billy o'connell")
        self.assertEqual(out, "Billy O'Connell")

    # ---- Hyphenated names (Jean-Benoit, Seymour-Jackson) -----------------

    def test_hyphenated_already_canonical_idempotent(self):
        """Seymour-Jackson already canonical — no change."""
        out = parse_pdmr._normalise_director_name("Angela Seymour-Jackson")
        self.assertEqual(out, "Angela Seymour-Jackson")

    def test_hyphenated_uppercase_normalises(self):
        """JEAN-BENOIT BERTY — each hyphen-split segment title-cased."""
        out = parse_pdmr._normalise_director_name("JEAN-BENOIT BERTY")
        self.assertEqual(out, "Jean-Benoit Berty")

    # ---- Lowercase particles (van, der, of, de) --------------------------

    def test_particle_already_canonical_idempotent(self):
        """Marle van der Walt — exception list match preserves canonical
        form (van / der are non-initial particles)."""
        out = parse_pdmr._normalise_director_name("Marle van der Walt")
        self.assertEqual(out, "Marle van der Walt")

    def test_particle_uppercase_via_exception(self):
        """MARLE VAN DER WALT — case-insensitive exception lookup picks
        up the canonical 'Marle van der Walt' form."""
        out = parse_pdmr._normalise_director_name("MARLE VAN DER WALT")
        self.assertEqual(out, "Marle van der Walt")

    def test_particle_mixed_case_corrected_via_exception(self):
        """Marle Van der Walt — exception list collapses the 'Van' cap
        variant back to canonical 'van' lowercase."""
        out = parse_pdmr._normalise_director_name("Marle Van der Walt")
        self.assertEqual(out, "Marle van der Walt")

    def test_particle_at_first_position_capitalised(self):
        """Van Helsing — when 'Van' is the FIRST word it stays
        capitalised (the lowercase-particle rule is for non-initial
        positions only). Distinguishes a Van-as-name from a Van-as-particle."""
        out = parse_pdmr._normalise_director_name("Van Helsing")
        self.assertEqual(out, "Van Helsing")

    def test_particle_algorithmic_path(self):
        """A particle name NOT in the exception list runs through the
        algorithm. 'Peter van den Berg' should keep 'van' and 'den'
        lowercase and capitalise Peter/Berg."""
        out = parse_pdmr._normalise_director_name("PETER VAN DEN BERG")
        self.assertEqual(out, "Peter van den Berg")

    # ---- Post-nominals (CMG, OBE, CBE, MBE) — preserved uppercase --------

    def test_post_nominal_cmg_uppercase_preserved(self):
        """ALAN JOHNSON CMG — CMG must stay uppercase; the rest goes to
        Title-Case."""
        out = parse_pdmr._normalise_director_name("ALAN JOHNSON CMG")
        self.assertEqual(out, "Alan Johnson CMG")

    def test_post_nominal_obe_idempotent(self):
        """John Smith OBE — already canonical, no change."""
        out = parse_pdmr._normalise_director_name("John Smith OBE")
        self.assertEqual(out, "John Smith OBE")

    def test_post_nominal_mbe_uppercase_normalises(self):
        """JOHN SMITH MBE — MBE preserved; rest title-cased."""
        out = parse_pdmr._normalise_director_name("JOHN SMITH MBE")
        self.assertEqual(out, "John Smith MBE")

    # ---- Exception list (Mac/non-Mac, multi-word particles) --------------

    def test_exception_macaulay_uppercase(self):
        """FIONA MACAULAY — exception list overrides the default capitalize
        path so 'Macaulay' (single-word Mac, NOT MacX) is preserved as
        'Macaulay' rather than mangled by any Mac algorithm."""
        out = parse_pdmr._normalise_director_name("FIONA MACAULAY")
        self.assertEqual(out, "Fiona Macaulay")

    def test_exception_mackinnon_preserved(self):
        """Andy MacKinnon — exception list preserves the internal
        capital K (algorithm would lowercase it to 'Mackinnon')."""
        out = parse_pdmr._normalise_director_name("Andy MacKinnon")
        self.assertEqual(out, "Andy MacKinnon")

    def test_exception_mclean_with_intentional_cap(self):
        """Nicola Jane Mclean — corrected to 'McLean' via the exception
        list (the Mc algorithm would output McLean too, but the input
        'Mclean' would word-by-word fall into the Mc branch and the
        exception-first lookup confirms the canonical form regardless)."""
        out = parse_pdmr._normalise_director_name("Nicola Jane Mclean")
        self.assertEqual(out, "Nicola Jane McLean")

    # ---- Foreign characters (Unicode-aware) ------------------------------

    def test_unicode_idempotent(self):
        """Benoît Macé — Python 3's str.capitalize handles non-ASCII
        characters correctly. Idempotency holds."""
        out = parse_pdmr._normalise_director_name("Benoît Macé")
        self.assertEqual(out, "Benoît Macé")

    def test_unicode_lowercase_normalises(self):
        """benoît macé — lowercase Unicode input should round-trip to
        the correct Title-Case form."""
        out = parse_pdmr._normalise_director_name("benoît macé")
        self.assertEqual(out, "Benoît Macé")

    # ---- Override of the module-level exception map ---------------------

    def test_custom_exceptions_override(self):
        """Caller can pass a custom exception map to override the file's
        loaded map. Confirms the API contract for unit-testability."""
        out = parse_pdmr._normalise_director_name(
            "TEST PERSON", exceptions={"TEST PERSON": "Test PerSon"}
        )
        self.assertEqual(out, "Test PerSon")

    def test_empty_exceptions_falls_through_to_algorithm(self):
        """If the caller passes an empty exceptions dict, the helper
        bypasses the lookup and falls through to the word-by-word
        algorithm. Validates that the exception map isn't load-bearing
        for plain names."""
        out = parse_pdmr._normalise_director_name(
            "DEREK MAPP", exceptions={}
        )
        self.assertEqual(out, "Derek Mapp")


# ---------------------------------------------------------------------------
# Year-as-shares refix (2026-05-31) — Step A hard year guard
# ---------------------------------------------------------------------------


class YearAsSharesStepAGuardTests(unittest.TestCase):
    """Step A — the hard 4-digit-year guard in `_looks_like_date_bleed`
    (Trigger 2) and `_plausibility_check` (R6).

    The Sprint-11 logic only rejected a year-like volume when it was the
    only integer in the block; prose ("…purchased 127,083 Ordinary Shares
    … 2026") always slipped through. The 2026-05-31 refix removes that
    escape hatch: any value in 1990..2099 is rejected unconditionally, and
    a `shares` value exactly equal to a year is a value-independent REJECT
    in the plausibility gate (routed to pending-review by the ingest layer).
    """

    # ---- Trigger 2: bare-year rejection -----------------------------------

    def test_bare_year_rejected(self):
        # A bare "2026" must be flagged regardless of context.
        self.assertTrue(parse_pdmr._looks_like_date_bleed(2026, "2026", 0.0))

    def test_bare_year_rejected_even_with_companion_price(self):
        # The old logic could keep a year when a price was present; the
        # hard guard rejects it anyway. Year is never a real volume.
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2025, "Price £4.00 2025", 4.0)
        )

    def test_prose_bleed_year_rejected_regression(self):
        # THE REGRESSION ASSERTION. This block mirrors the EMAN narrative
        # that previously let 2026 through (real share count 127,083 also
        # present). Pre-refix Trigger 2 returned False here. Must be True.
        block = (
            "purchased a total of 127,083 Ordinary Shares at a price of "
            "33.5 pence per Ordinary Share between 13 and 20 May 2026"
        )
        self.assertTrue(
            parse_pdmr._looks_like_date_bleed(2026, block, 0.0),
            "Prose-bleed year 2026 must be rejected even though 127,083 "
            "(a real share count) appears in the same block.",
        )

    def test_year_boundaries(self):
        # 1990 and 2099 inclusive; 1989 and 2100 are not year-guarded by
        # Trigger 2 (they fall outside the year window).
        self.assertTrue(parse_pdmr._looks_like_date_bleed(1990, "1990 5,000", 0.0))
        self.assertTrue(parse_pdmr._looks_like_date_bleed(2099, "2099 5,000", 0.0))
        self.assertFalse(parse_pdmr._looks_like_date_bleed(1989, "1989 5,000", 5.0))
        self.assertFalse(parse_pdmr._looks_like_date_bleed(2100, "2100 5,000", 5.0))

    def test_real_share_count_not_year_still_accepted(self):
        # A genuine 5-/6-digit share count must NOT be rejected by the
        # year guard — only values inside the year window are touched.
        self.assertFalse(
            parse_pdmr._looks_like_date_bleed(127083, "127,083 shares", 0.335)
        )

    # ---- R6: plausibility rejection of shares == year ---------------------

    def _row(self, **overrides):
        base = {
            "fingerprint": "fp", "date": "2026-05-20", "ticker": "EMAN",
            "company": "Everyman Media Group plc", "director": "Charles Dorfman",
            "role": "PDMR", "type": "BUY", "shares": 2026, "price": 0.335,
            "value": 678.71,
        }
        base.update(overrides)
        return base

    def test_R6_rejects_shares_equal_year_low_value(self):
        # The exact EMAN failure shape: shares=2026, value≈£678.71.
        ok, reasons = parse_pdmr._plausibility_check(self._row())
        self.assertFalse(ok)
        self.assertIn("R6_shares_equals_year", reasons)

    def test_R6_rejects_shares_equal_year_regardless_of_value(self):
        # R6 is value-INDEPENDENT — unlike R5 it is not gated on value<£100.
        # A year-as-shares row with a large fabricated value must still
        # reject on R6.
        ok, reasons = parse_pdmr._plausibility_check(
            self._row(shares=2026, price=500.0, value=1_013_000.0)
        )
        self.assertFalse(ok)
        self.assertIn("R6_shares_equals_year", reasons)

    def test_R6_does_not_fire_on_real_share_count(self):
        ok, reasons = parse_pdmr._plausibility_check(
            self._row(shares=127083, price=0.335, value=42572.0)
        )
        self.assertNotIn("R6_shares_equals_year", reasons)

    def test_R6_boundaries(self):
        # 1990 / 2099 trip R6; 1989 / 2100 do not.
        _, r1990 = parse_pdmr._plausibility_check(self._row(shares=1990, value=10000.0))
        _, r2099 = parse_pdmr._plausibility_check(self._row(shares=2099, value=10000.0))
        _, r1989 = parse_pdmr._plausibility_check(self._row(shares=1989, value=10000.0))
        _, r2100 = parse_pdmr._plausibility_check(self._row(shares=2100, value=10000.0))
        self.assertIn("R6_shares_equals_year", r1990)
        self.assertIn("R6_shares_equals_year", r2099)
        self.assertNotIn("R6_shares_equals_year", r1989)
        self.assertNotIn("R6_shares_equals_year", r2100)


# ---------------------------------------------------------------------------
# Year-as-shares refix (2026-05-31) — Step B aggregate / tranche-sum
# ---------------------------------------------------------------------------


class YearAsSharesStepBAggregateTests(unittest.TestCase):
    """Step B — the table-aware aggregate / tranche-sum extractor for the
    MAR Article 19 template (nested Price|Volume sub-table, name from the
    'a) Name' KV row). Direct helper tests plus end-to-end against the two
    real EMAN filings (also covered by the fixture pairs).
    """

    FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "parser"

    def _parse_fixture(self, stem: str):
        html = (self.FIXTURE_DIR / f"{stem}.html").read_text(encoding="utf-8")
        return parse_pdmr.parse_announcement(
            html,
            url=(
                "https://www.investegate.co.uk/announcement/rns/"
                "everyman-media-group--eman/pdmr-dealing/" + stem.split("_")[1]
            ),
            rns_id="fixture-" + stem,
            announced_at="2026-05-29T09:00:00Z",
            headline="(EMAN) PDMR Dealing",
            ticker_hint="EMAN",
        )

    # ---- tranche-sum helper (aggregate volume present) --------------------

    def test_multitranche_uses_labelled_aggregate_volume(self):
        # 9581012: nested Date|Price|Volume table (50,000 + 5,583 + 71,500)
        # AND a labelled "- Aggregated volume" = 127,083. Result: 1 row,
        # shares=127083, dated to the latest tranche (2026-05-20).
        extracted, warnings, source = self._parse_fixture(
            "eman_9581012_multitranche"
        )
        self.assertEqual(len(extracted), 1, f"warnings={warnings!r}")
        row = extracted[0]
        self.assertEqual(row["shares"], 127083)
        self.assertNotEqual(row["shares"], 2026)
        self.assertEqual(row["date"], "2026-05-20")
        self.assertEqual(row["director"], "Charles Dorfman")
        self.assertEqual(row["type"], "BUY")
        self.assertNotIn("required_fields_missing", warnings)

    # ---- tranche-sum-on-N/A (aggregate volume is "N/A") -------------------

    def test_two_col_na_sums_tranche_volume(self):
        # 9592451: nested Price|Volume table (single tranche 67,649), the
        # "- Aggregated volume" cell reads "N/A" → parser SUMS the tranche
        # volume; date comes from the "e) Date of the transaction" KV row.
        extracted, warnings, source = self._parse_fixture(
            "eman_9592451_two_col_na"
        )
        self.assertEqual(len(extracted), 1, f"warnings={warnings!r}")
        row = extracted[0]
        self.assertEqual(row["shares"], 67649)
        self.assertNotEqual(row["shares"], 2026)
        self.assertEqual(row["date"], "2026-05-27")
        self.assertEqual(row["director"], "Charles Dorfman")

    # ---- aggregate-label reader: value vs N/A fallback --------------------

    def test_aggregate_volume_label_reads_integer(self):
        # When "- Aggregated volume" parses to an int, the extractor uses
        # it directly (does not re-sum). 9581012's labelled total is the
        # canonical case and equals the tranche sum (127,083), so this is
        # asserted via the end-to-end multitranche test above. Here we
        # confirm the helper that backs it: _parse_volume_cell on the label.
        shares, w = parse_pdmr._parse_volume_cell("127,083")
        self.assertEqual(shares, 127083)
        self.assertEqual(w, [])

    def test_aggregate_volume_label_na_is_not_a_volume(self):
        # "N/A" must not parse as a share count, forcing the tranche-sum
        # fallback path in the extractor.
        shares, w = parse_pdmr._parse_volume_cell("N/A")
        self.assertEqual(shares, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
