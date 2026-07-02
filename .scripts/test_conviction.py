"""Unit tests for the Phase-1 Conviction Score engine (conviction.py / B-171).

Pure-compute: every test feeds plain dicts / numbers and asserts on the
0.0-1.0 factor curves and the 0-100 composite. No DB, no I/O. Run from bash:

    python -m unittest .scripts.test_conviction -v
or  cd .scripts && python -m unittest test_conviction -v

Covers (per the build brief, revised 2026-07-01/02 for the 5-factor design —
see outputs/conviction-sector-weighting-review-2026-07-02.md Part 3):
  * each factor curve at low / mid / high inputs (F1-F4, F5 sector_momentum)
  * the weighted composite (§4 formula), now with sector_momentum ADDITIVE
    inside the same weighted sum (no separate post-clamp multiplier)
  * f5_sector_momentum(): neutral at net=0, monotonic, bounded, None->0.5
  * PCA inheritance (§3 F1, decision 2026-06-18)
  * missing-earnings-date re-normalisation (§4, decision 2026-06-18)
  * strength bands (§4)
  * the deprecated F6 multiplier functions still work (kept, unused)
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

# Make .scripts/ importable whether run from repo root or from .scripts/.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import conviction as cv  # noqa: E402


class TestF1Who(unittest.TestCase):
    """F1 — role strength by seniority + PCA inheritance."""

    def test_high_seniority(self):
        # Chair / CEO / Founder ~= 1.0
        self.assertEqual(cv.f1_who("T1a"), 1.0)
        self.assertEqual(cv.f1_who("T7"), 1.0)

    def test_mid_seniority(self):
        # CFO ~= 0.9, other exec ~= 0.7
        self.assertAlmostEqual(cv.f1_who("T1b"), 0.9)
        self.assertAlmostEqual(cv.f1_who("T2"), 0.7)

    def test_low_seniority(self):
        # NED kept low (~0.3); catch-all lowest.
        self.assertAlmostEqual(cv.f1_who("T3"), 0.3)
        self.assertAlmostEqual(cv.f1_who("T4"), 0.2)

    def test_unknown_tier_falls_to_catch_all(self):
        self.assertAlmostEqual(cv.f1_who(None), 0.2)
        self.assertAlmostEqual(cv.f1_who("ZZZ"), 0.2)

    def test_pca_scores_max_independently(self):
        # PCAs score at max (1.0) independently (decision 2026-07-01) —
        # regardless of company_top_tier, which is retained only for
        # signature compatibility and no longer changes the result.
        bare = cv.f1_who("T5")
        with_top = cv.f1_who("T5", company_top_tier="T7")
        self.assertAlmostEqual(bare, 1.0)
        self.assertAlmostEqual(with_top, 1.0)

    def test_pca_flag_routes_to_max(self):
        # An explicit is_pca flag also maxes the score, regardless of tier.
        res = cv.f1_who("T4", is_pca=True, company_top_tier="T1b")
        self.assertAlmostEqual(res, 1.0)

    def test_pca_without_company_top_still_max(self):
        self.assertAlmostEqual(cv.f1_who("T5", company_top_tier=None), 1.0)


class TestF2BuySize(unittest.TestCase):
    """F2 — absolute £ + relative-to-volume, bigger = stronger."""

    def test_absolute_low_mid_high(self):
        self.assertEqual(cv.f2_buy_size(5_000), 0.0)        # at floor
        self.assertEqual(cv.f2_buy_size(1_000), 0.0)        # below floor
        self.assertEqual(cv.f2_buy_size(5_000_000), 1.0)    # at ceil
        self.assertEqual(cv.f2_buy_size(50_000_000), 1.0)   # above ceil
        mid = cv.f2_buy_size(150_000)                       # ~halfway in log
        self.assertTrue(0.0 < mid < 1.0)

    def test_monotonic_absolute(self):
        a = cv.f2_buy_size(20_000)
        b = cv.f2_buy_size(200_000)
        c = cv.f2_buy_size(2_000_000)
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_relative_volume_blend_raises_small_company_buy(self):
        # A modest £ buy that is large vs the stock's daily turnover should
        # score HIGHER than the same £ on a thickly-traded name.
        small_co = cv.f2_buy_size(100_000, avg_daily_turnover_gbp=30_000)   # ~3.3x
        big_co = cv.f2_buy_size(100_000, avg_daily_turnover_gbp=5_000_000)  # tiny frac
        self.assertGreater(small_co, big_co)

    def test_no_volume_falls_back_to_absolute(self):
        self.assertEqual(
            cv.f2_buy_size(100_000, avg_daily_turnover_gbp=None),
            cv.f2_buy_size(100_000),
        )
        self.assertEqual(
            cv.f2_buy_size(100_000, avg_daily_turnover_gbp=0),
            cv.f2_buy_size(100_000),
        )

    def test_none_value(self):
        self.assertEqual(cv.f2_buy_size(None), 0.0)


class TestF3CompanySize(unittest.TestCase):
    """F3 — smaller cap = stronger."""

    def test_micro_large_anchors(self):
        self.assertEqual(cv.f3_company_size(50_000_000), 1.0)        # micro
        self.assertEqual(cv.f3_company_size(10_000_000), 1.0)        # below micro
        self.assertAlmostEqual(cv.f3_company_size(10_000_000_000), 0.1)  # large
        self.assertAlmostEqual(cv.f3_company_size(50_000_000_000), 0.1)  # mega

    def test_monotonic_decreasing(self):
        small = cv.f3_company_size(100_000_000)    # £100m
        mid = cv.f3_company_size(1_000_000_000)    # £1bn
        big = cv.f3_company_size(8_000_000_000)    # £8bn
        self.assertGreater(small, mid)
        self.assertGreater(mid, big)

    def test_missing_cap_is_neutral(self):
        self.assertEqual(cv.f3_company_size(None), 0.5)
        self.assertEqual(cv.f3_company_size(0), 0.5)


class TestF4EarningsTiming(unittest.TestCase):
    """F4 — earnings-proximity curve."""

    def test_inside_close_period_invalid(self):
        # < 30 days to results: directors can't legally deal -> 0.0
        self.assertEqual(cv.f4_earnings_timing(days_to_next_results=10), 0.0)
        self.assertEqual(cv.f4_earnings_timing(days_to_next_results=29), 0.0)

    def test_just_before_lockout_elevated(self):
        # 31-45 days out -> peak (1.0). This is the spec's worked example.
        self.assertEqual(cv.f4_earnings_timing(days_to_next_results=31), 1.0)
        self.assertEqual(cv.f4_earnings_timing(days_to_next_results=45), 1.0)

    def test_mid_cycle_decays_low(self):
        far = cv.f4_earnings_timing(days_to_next_results=110)
        nearer = cv.f4_earnings_timing(days_to_next_results=60)
        self.assertLess(far, nearer)
        self.assertLessEqual(far, 0.25)  # mid-cycle is low
        self.assertGreaterEqual(far, 0.1)  # floored, never zero mid-cycle

    def test_just_after_results_high(self):
        day0 = cv.f4_earnings_timing(days_since_last_results=0)
        day21 = cv.f4_earnings_timing(days_since_last_results=21)
        self.assertGreater(day0, day21)        # decaying
        self.assertGreaterEqual(day0, 0.8)     # high just after results
        self.assertGreater(day21, 0.1)

    def test_takes_stronger_of_two_reads(self):
        # Just-after old results AND just-before new results -> takes the max.
        both = cv.f4_earnings_timing(days_to_next_results=35,
                                     days_since_last_results=5)
        self.assertEqual(both, 1.0)  # pre-lockout peak dominates

    def test_no_input_returns_zero(self):
        # The composite is expected to DROP F4 in this case; the bare curve
        # returns 0.0 as a safe default.
        self.assertEqual(cv.f4_earnings_timing(), 0.0)


class TestF5PastPerformanceDeprecated(unittest.TestCase):
    """F5 (legacy name) — inverted trailing return (dip-buy bias), capped.

    NOTE: this factor is NOT part of WEIGHTS / composite() since 2026-07-01
    (direction unresolved). The function itself is unchanged and still
    unit-tested here since it remains in the module for any external caller.
    """

    def test_dip_buy_scores_high(self):
        self.assertEqual(cv.f5_past_performance(-0.30), 1.0)  # at cap
        self.assertEqual(cv.f5_past_performance(-0.80), 1.0)  # capped, same as -30%

    def test_rally_scores_low(self):
        self.assertEqual(cv.f5_past_performance(0.20), 0.0)   # at cap
        self.assertEqual(cv.f5_past_performance(0.50), 0.0)   # capped

    def test_flat_is_mid(self):
        # Caps are asymmetric (-30% dip vs +20% rally), so flat sits at
        # exactly 0.4 — slightly toward the dip-buy side, by design.
        mid = cv.f5_past_performance(0.0)
        self.assertAlmostEqual(mid, 0.4)
        self.assertTrue(0.3 < mid < 0.6)  # roughly neutral

    def test_monotonic_inverted(self):
        # More negative trailing return -> higher score.
        a = cv.f5_past_performance(0.10)
        b = cv.f5_past_performance(-0.05)
        c = cv.f5_past_performance(-0.20)
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_missing_is_neutral(self):
        self.assertEqual(cv.f5_past_performance(None), 0.5)


class TestF5SectorMomentum(unittest.TestCase):
    """F5 (current 5th additive factor) — sigmoid of trailing-30d sector net buys."""

    def test_neutral_at_zero(self):
        self.assertAlmostEqual(cv.f5_sector_momentum(0), 0.5)
        self.assertAlmostEqual(cv.f5_sector_momentum(0.0), 0.5)

    def test_none_is_neutral(self):
        self.assertAlmostEqual(cv.f5_sector_momentum(None), 0.5)

    def test_monotonic(self):
        low = cv.f5_sector_momentum(-20)
        mid_neg = cv.f5_sector_momentum(-5)
        neutral = cv.f5_sector_momentum(0)
        mid_pos = cv.f5_sector_momentum(5)
        high = cv.f5_sector_momentum(20)
        self.assertLess(low, mid_neg)
        self.assertLess(mid_neg, neutral)
        self.assertLess(neutral, mid_pos)
        self.assertLess(mid_pos, high)

    def test_bounded_0_1(self):
        for net in (-1000, -100, -24, -12, 0, 12, 24, 100, 1000):
            v = cv.f5_sector_momentum(net)
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_hottest_observed_sector_reads_about_0_88(self):
        # k=12 calibrated so net~+24 (hottest observed live) reads ~0.88,
        # not pinned near 1.0 (per the review doc).
        v = cv.f5_sector_momentum(24)
        self.assertAlmostEqual(v, 1.0 / (1.0 + math.exp(-2.0)), places=6)
        self.assertTrue(0.85 < v < 0.90)

    def test_symmetric_around_neutral(self):
        pos = cv.f5_sector_momentum(12)
        neg = cv.f5_sector_momentum(-12)
        self.assertAlmostEqual(pos - 0.5, 0.5 - neg, places=6)

    def test_custom_k(self):
        # A smaller k saturates faster.
        default_k = cv.f5_sector_momentum(12)
        smaller_k = cv.f5_sector_momentum(12, k=1.0)
        self.assertGreater(smaller_k, default_k)


class TestF6SectorGuardrailDeprecated(unittest.TestCase):
    """F6 (deprecated) — guardrail MULTIPLIER (0.7-1.0), trims only.

    Kept only because the function is still in the module (not deleted);
    no longer called by conviction_score()/composite().
    """

    def test_calm_sector_no_discount(self):
        self.assertEqual(cv.f6_sector_guardrail(0.0), 1.0)
        self.assertEqual(cv.f6_sector_guardrail(None), 1.0)

    def test_hot_sector_max_discount(self):
        self.assertAlmostEqual(cv.f6_sector_guardrail(1.0), 0.7)

    def test_monotonic_and_bounded(self):
        m0 = cv.f6_sector_guardrail(0.0)
        m5 = cv.f6_sector_guardrail(0.5)
        m1 = cv.f6_sector_guardrail(1.0)
        self.assertGreater(m0, m5)
        self.assertGreater(m5, m1)
        self.assertLessEqual(m0, 1.0)   # never a booster
        self.assertGreaterEqual(m1, 0.7)  # never below floor

    def test_net_buys_to_f6_still_works(self):
        self.assertEqual(cv.net_buys_to_f6(0), 0.0)
        self.assertEqual(cv.net_buys_to_f6(21), 1.0)
        self.assertEqual(cv.net_buys_to_f6(-21), -1.0)

    def test_f6_sector_multiplier_still_works(self):
        self.assertEqual(cv.f6_sector_multiplier(None, None), 1.0)
        self.assertEqual(cv.f6_sector_multiplier("Financials", {"Financials": 1.0}), 2.0)


class TestComposite(unittest.TestCase):
    """§4 — weighted composite, sector_momentum now ADDITIVE (no multiplier)."""

    def test_all_max_is_100(self):
        subs = {k: 1.0 for k in cv.WEIGHTS}
        res = cv.composite(subs)
        self.assertAlmostEqual(res.score, 100.0)
        self.assertEqual(res.band, "Exceptional")

    def test_all_zero_is_zero(self):
        subs = {k: 0.0 for k in cv.WEIGHTS}
        res = cv.composite(subs)
        self.assertEqual(res.score, 0.0)
        self.assertEqual(res.band, "Low")

    def test_weighted_sum_matches_formula(self):
        # Hand-computed with the 2026-07-01 weights:
        # who .24, buy_size .24, company_size .176, earnings_timing .144,
        # sector_momentum .20
        # = .24*1 + .24*.5 + .176*0 + .144*1 + .20*0 = .24+.12+0+.144+0 = .504
        subs = {"who": 1.0, "buy_size": 0.5, "company_size": 0.0,
                "earnings_timing": 1.0, "sector_momentum": 0.0}
        res = cv.composite(subs)
        expected = 100.0 * (0.24 * 1.0 + 0.24 * 0.5 + 0.176 * 0.0
                            + 0.144 * 1.0 + 0.20 * 0.0)
        self.assertAlmostEqual(res.score, expected, places=4)
        self.assertAlmostEqual(res.score, 50.4, places=4)

    def test_no_separate_multiplier_param(self):
        # composite() no longer accepts sector_multiplier= — sector is baked
        # into `subscores["sector_momentum"]` instead.
        with self.assertRaises(TypeError):
            cv.composite({k: 1.0 for k in cv.WEIGHTS}, sector_multiplier=0.7)

    def test_sector_momentum_moves_score_additively(self):
        base = {"who": 0.5, "buy_size": 0.5, "company_size": 0.5,
                "earnings_timing": 0.5}
        hot = cv.composite({**base, "sector_momentum": 1.0})
        cold = cv.composite({**base, "sector_momentum": 0.0})
        neutral = cv.composite({**base, "sector_momentum": 0.5})
        self.assertGreater(hot.score, neutral.score)
        self.assertLess(cold.score, neutral.score)
        # The spread between hot and cold must equal 100 * weight("sector_momentum").
        self.assertAlmostEqual(hot.score - cold.score,
                               100.0 * cv.WEIGHTS["sector_momentum"], places=4)

    def test_result_exposes_sector_momentum_field(self):
        subs = {k: 1.0 for k in cv.WEIGHTS}
        subs["sector_momentum"] = 0.7
        res = cv.composite(subs)
        self.assertAlmostEqual(res.sector_momentum, 0.7)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(cv.WEIGHTS.values()), 1.0)

    def test_weights_have_five_keys(self):
        self.assertEqual(set(cv.WEIGHTS.keys()),
                        {"who", "buy_size", "company_size",
                         "earnings_timing", "sector_momentum"})
        self.assertAlmostEqual(cv.WEIGHTS["who"], 0.24)
        self.assertAlmostEqual(cv.WEIGHTS["buy_size"], 0.24)
        self.assertAlmostEqual(cv.WEIGHTS["company_size"], 0.176)
        self.assertAlmostEqual(cv.WEIGHTS["earnings_timing"], 0.144)
        self.assertAlmostEqual(cv.WEIGHTS["sector_momentum"], 0.20)


class TestMissingEarningsRenorm(unittest.TestCase):
    """§4 / decision 2026-06-18 — drop F4 & re-normalise when date unknown.

    _renormalise() is generic over whatever keys are in WEIGHTS, so this
    still works unchanged with five factors instead of four.
    """

    def test_renormalised_weights_sum_to_one(self):
        subs = {k: 1.0 for k in cv.WEIGHTS}
        res = cv.composite(subs, drop_earnings=True)
        self.assertNotIn("earnings_timing", res.weights_used)
        self.assertAlmostEqual(sum(res.weights_used.values()), 1.0)
        self.assertTrue(res.earnings_dropped)

    def test_missing_earnings_does_not_penalise(self):
        # With all OTHER factors maxed, dropping F4 should still yield 100,
        # NOT less (which is what zeroing F4 without re-normalising would give).
        subs = {"who": 1.0, "buy_size": 1.0, "company_size": 1.0,
                "earnings_timing": 0.0, "sector_momentum": 1.0}
        dropped = cv.composite(subs, drop_earnings=True)
        self.assertAlmostEqual(dropped.score, 100.0)
        # Contrast: NOT dropping (F4 genuinely 0) caps the score below 100.
        kept = cv.composite(subs, drop_earnings=False)
        self.assertAlmostEqual(kept.score, 100.0 * (1.0 - cv.WEIGHTS["earnings_timing"]))
        self.assertLess(kept.score, dropped.score)

    def test_renorm_preserves_relative_weight_ratios(self):
        # After dropping the 0.144 earnings weight, the remaining four
        # (.24, .24, .176, .20 -> sum .856) keep their ratios.
        res = cv.composite({k: 0.0 for k in cv.WEIGHTS}, drop_earnings=True)
        w = res.weights_used
        total = 1.0 - cv.WEIGHTS["earnings_timing"]
        self.assertAlmostEqual(w["who"], cv.WEIGHTS["who"] / total, places=6)
        self.assertAlmostEqual(w["buy_size"], cv.WEIGHTS["buy_size"] / total, places=6)
        self.assertAlmostEqual(w["company_size"], cv.WEIGHTS["company_size"] / total, places=6)
        self.assertAlmostEqual(w["sector_momentum"], cv.WEIGHTS["sector_momentum"] / total, places=6)


class TestBands(unittest.TestCase):
    """§4 — strength bands."""

    def test_band_boundaries(self):
        self.assertEqual(cv.band_for(0.0), "Low")
        self.assertEqual(cv.band_for(39.9), "Low")
        self.assertEqual(cv.band_for(40.0), "Moderate")
        self.assertEqual(cv.band_for(59.9), "Moderate")
        self.assertEqual(cv.band_for(60.0), "High")
        self.assertEqual(cv.band_for(79.9), "High")
        self.assertEqual(cv.band_for(80.0), "Exceptional")
        self.assertEqual(cv.band_for(100.0), "Exceptional")


class TestEndToEnd(unittest.TestCase):
    """conviction_score() — the Phase-2 entry point, full pipeline."""

    def test_worked_example_scores_high(self):
        # Spec §2 worked example: large buy, small-cap, Chair-linked PCA,
        # ~31 days before earnings, hot sector. Should land High/Exceptional.
        res = cv.conviction_score(
            tier="T5", is_pca=True, company_top_tier="T7",  # PCA
            value_gbp=2_000_000, avg_daily_turnover_gbp=200_000,
            market_cap_gbp=80_000_000,        # small-cap
            days_to_next_results=31,          # just before lockout
            sector="Financials",
            sector_net_buy_map={"Financials": 15},  # hot sector
        )
        self.assertGreaterEqual(res.score, 60.0)
        self.assertIn(res.band, ("High", "Exceptional"))
        # PCA scores max independently.
        self.assertAlmostEqual(res.subscores["who"], 1.0)

    def test_weak_week_lands_low_or_moderate(self):
        # Small NED buy on a large-cap, cold sector -> honest low score.
        res = cv.conviction_score(
            tier="T3",
            value_gbp=8_000,
            market_cap_gbp=8_000_000_000,
            sector="Materials",
            sector_net_buy_map={"Materials": -20},
            # no earnings date -> F4 dropped & re-normalised
        )
        self.assertLess(res.score, 40.0)
        self.assertEqual(res.band, "Low")
        self.assertTrue(res.earnings_dropped)

    def test_missing_earnings_drops_f4(self):
        res = cv.conviction_score(
            tier="T1a", value_gbp=500_000, market_cap_gbp=100_000_000,
            # both timing inputs omitted
        )
        self.assertTrue(res.earnings_dropped)
        self.assertNotIn("earnings_timing", res.weights_used)

    def test_present_earnings_keeps_f4(self):
        res = cv.conviction_score(
            tier="T1a", value_gbp=500_000, market_cap_gbp=100_000_000,
            days_to_next_results=35,
        )
        self.assertFalse(res.earnings_dropped)
        self.assertIn("earnings_timing", res.weights_used)

    def test_result_as_dict_shape(self):
        res = cv.conviction_score(tier="T1a", value_gbp=100_000)
        d = res.as_dict()
        for key in ("score", "band", "subscores", "weights_used",
                    "sector_momentum", "earnings_dropped"):
            self.assertIn(key, d)
        for fk in cv.WEIGHTS:
            self.assertIn(fk, d["subscores"])
            self.assertTrue(0.0 <= d["subscores"][fk] <= 1.0)

    def test_no_sector_map_is_neutral(self):
        # No sector / no map -> sector_momentum subscore defaults neutral 0.5.
        res = cv.conviction_score(tier="T1a", value_gbp=100_000)
        self.assertAlmostEqual(res.subscores["sector_momentum"], 0.5)

    def test_unmapped_sector_is_neutral(self):
        res = cv.conviction_score(
            tier="T1a", value_gbp=100_000, sector="Unknown Sector",
            sector_net_buy_map={"Financials": 20},
        )
        self.assertAlmostEqual(res.subscores["sector_momentum"], 0.5)

    def test_hot_sector_scores_meaningfully_higher_than_cold_or_none(self):
        # Same inputs, only the sector net-buy map differs: heavy net buying
        # in this buy's sector must score meaningfully higher than heavy net
        # selling, and both distinctly differ from the no-map neutral case.
        kwargs = dict(
            tier="T2", value_gbp=250_000, market_cap_gbp=200_000_000,
            days_to_next_results=35, sector="Technology",
        )
        hot = cv.conviction_score(**kwargs, sector_net_buy_map={"Technology": 24})
        cold = cv.conviction_score(**kwargs, sector_net_buy_map={"Technology": -24})
        neutral = cv.conviction_score(**kwargs, sector_net_buy_map=None)
        self.assertGreater(hot.score, neutral.score)
        self.assertLess(cold.score, neutral.score)
        self.assertGreater(hot.score, cold.score)
        # Meaningful gap: at least half the sector_momentum weight's full swing.
        self.assertGreater(hot.score - cold.score,
                           50.0 * cv.WEIGHTS["sector_momentum"])

    def test_score_always_in_range(self):
        # Fuzz a spread of inputs; score must never leave [0,100].
        for tier in ("T1a", "T1b", "T2", "T3", "T4", "T5", "T7", None):
            for val in (None, 1_000, 100_000, 9_999_999):
                for cap in (None, 30_000_000, 2_000_000_000):
                    for net in (None, -30, 0, 30):
                        res = cv.conviction_score(
                            tier=tier, value_gbp=val, market_cap_gbp=cap,
                            sector="X", sector_net_buy_map=(
                                {"X": net} if net is not None else None),
                        )
                        self.assertGreaterEqual(res.score, 0.0)
                        self.assertLessEqual(res.score, 100.0)
                        self.assertFalse(math.isnan(res.score))


if __name__ == "__main__":
    unittest.main(verbosity=2)
