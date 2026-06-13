"""B-060 TDD suite for price_reconcile.reconcile_price.

Covers the two failure modes the live diagnostic found plus the regression the
prior naive fix caused (genuine pound prices must NOT be divided by 100).
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from price_reconcile import reconcile_price  # noqa: E402


class TestReconcilePence(unittest.TestCase):
    # --- Mode A: pence-as-pounds, must be corrected -----------------------
    def test_igp_pence_corrected(self):
        # IGP 8948382: stored 171 (=> GBP171), Intercede closes ~170p = GBP1.70
        price, status = reconcile_price(171.0, 60000, 1.70, "BUY")
        self.assertEqual(status, "corrected_pence")
        self.assertAlmostEqual(price, 1.71, places=2)
        self.assertAlmostEqual(price * 60000, 102600, delta=1)

    def test_gbg_pence_corrected(self):
        # GB Group ~198p = GBP1.98
        price, status = reconcile_price(198.0, 25001, 1.97, "BUY")
        self.assertEqual(status, "corrected_pence")
        self.assertAlmostEqual(price, 1.98, places=2)

    def test_sip_small_pence_corrected(self):
        # QinetiQ SIP ~153p stored as GBP153
        price, status = reconcile_price(153.19, 10, 1.52, "SIP")
        self.assertEqual(status, "corrected_pence")
        self.assertAlmostEqual(price, 1.5319, places=3)

    # --- Pounds non-regression: must stay unchanged -----------------------
    def test_genuine_pound_price_unchanged(self):
        # A stock genuinely trading at ~GBP27 (e.g. 2700p quoted as 27)
        price, status = reconcile_price(27.0, 100, 27.10, "BUY")
        self.assertEqual(status, "ok_pounds")
        self.assertEqual(price, 27.0)

    def test_already_correct_subpound_unchanged(self):
        # Price cell parsed correctly to GBP4.316 against ~GBP4.30 close
        price, status = reconcile_price(4.316, 500, 4.30, "BUY")
        self.assertEqual(status, "ok_pounds")
        self.assertEqual(price, 4.316)

    def test_high_priced_allowlist_style_unchanged(self):
        # NXT ~ GBP90 genuine (close GBP91) -> pounds reading correct
        price, status = reconcile_price(90.0, 50, 91.0, "BUY")
        self.assertEqual(status, "ok_pounds")
        self.assertEqual(price, 90.0)

    # --- Mode B: garbage price, must quarantine ---------------------------
    def test_idox_garbage_quarantined(self):
        # IDOX exercise: GBP1.8m/share, Idox closes ~60p
        price, status = reconcile_price(1837455.0, 2, 0.60, "EXERCISE")
        self.assertEqual(status, "unresolved")
        self.assertEqual(price, 1837455.0)  # unchanged; caller routes to pending

    def test_fan_garbage_quarantined(self):
        # FAN grant: price==shares==41444, Volution closes ~GBP5.50
        price, status = reconcile_price(41444.0, 41444, 5.50, "GRANT")
        self.assertEqual(status, "unresolved")

    def test_value_ceiling_backstop(self):
        # Pence reading is in-tolerance but value still absurd -> quarantine
        # raw=100 (=>1.00 pence) * 2_000_000_000 shares = GBP2bn
        price, status = reconcile_price(100.0, 2_000_000_000, 1.0, "GRANT")
        self.assertEqual(status, "unresolved")

    # --- No market price: cannot decide -----------------------------------
    def test_no_market_price(self):
        price, status = reconcile_price(171.0, 60000, None, "BUY")
        self.assertEqual(status, "no_market")
        self.assertEqual(price, 171.0)

    def test_zero_market_price(self):
        _price, status = reconcile_price(171.0, 60000, 0.0, "BUY")
        self.assertEqual(status, "no_market")

    # --- Nil-cost / zero price: nothing to reconcile ----------------------
    def test_nil_cost_grant_zero(self):
        price, status = reconcile_price(0.0, 5000, 4.0, "GRANT")
        self.assertEqual(status, "ok_pounds")
        self.assertEqual(price, 0.0)

    def test_none_price(self):
        price, status = reconcile_price(None, 5000, 4.0, "GRANT")
        self.assertEqual(status, "ok_pounds")
        self.assertIsNone(price)

    # --- Tolerance boundaries --------------------------------------------
    def test_pence_at_upper_boundary_included(self):
        # raw=135, close=1.0 -> r_pence=1.35 == 1+TOL -> inclusive -> corrected
        price, status = reconcile_price(135.0, 10, 1.0, "BUY")
        self.assertEqual(status, "corrected_pence")
        self.assertAlmostEqual(price, 1.35, places=2)

    def test_just_past_boundary_quarantined(self):
        # raw=140, close=1.0 -> r_pence=1.40 (out), r_pounds=140 (out) -> quarantine
        _price, status = reconcile_price(140.0, 10, 1.0, "BUY")
        self.assertEqual(status, "unresolved")

    def test_pounds_at_lower_boundary_included(self):
        # raw=0.65, close=1.0 -> r_pounds=0.65 == 1-TOL -> inclusive -> ok_pounds
        price, status = reconcile_price(0.65, 10, 1.0, "BUY")
        self.assertEqual(status, "ok_pounds")
        self.assertEqual(price, 0.65)


if __name__ == "__main__":
    unittest.main(verbosity=2)
