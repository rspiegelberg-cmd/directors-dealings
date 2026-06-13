"""B-066 (Sprint 14) — tests for the cohort_performance.json emit.

Exercises `build_cohort_performance` + its helpers in
`export_dashboard_json.py` against a synthetic dataset spanning three
signal groups across several calendar months, including:

  * a low-N month (N < 5),
  * a month with one dominant ticker (single-ticker-dominance > 50%),
  * an empty / gap month in the middle of a group's history.

Assertions cover: month grouping correctness + ascending order, the
rolling-6m hit-rate window, ma3 (null for first 2 months then correct),
sparkline_points emitting null for the gap month, share-of-cohort-movement
weights summing to ~100% (bounded abs-share, sign-stable on all-negative
cohorts), single_ticker_weight detection (bounded <= 1.0), and the directional
verdict string (drag vs contributor) firing on the dominant-ticker month.

Stdlib-only. No DB writes, no file I/O (synthetic in-memory rows). Run::

    python -u .scripts/test_cohort_performance_export.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import export_dashboard_json as ex  # noqa: E402


# Reference "today": 2026-01-15. Latest COMPLETE month = 2025-12.
TODAY = date(2026, 1, 15)


def _row(signal_id, fired_iso, ticker, net30, *, net1=None, net90=None,
         car30=None, bench30=0.01, fp=None):
    """Build a synthetic backtest-CSV row dict (post-load_backtest_csv shape).

    `net30` is the net-of-cost CAR @ T+30 as a fraction. `car30` (raw) defaults
    to net30 + a nominal cost so the drill-down raw vs net columns differ.
    """
    if car30 is None:
        car30 = net30 + 0.01
    return {
        "signal_id":    signal_id,
        "_fired_at":    fired_iso,
        "ticker":       ticker,
        "fingerprint":  fp or f"{ticker}-{fired_iso[:10]}-{net30}",
        "_net_car_t1":  net1 if net1 is not None else net30 / 2.0,
        "_net_car_t30": net30,
        "_net_car_t90": net90 if net90 is not None else net30 * 1.5,
        "_car_t1":      (net1 if net1 is not None else net30 / 2.0) + 0.01,
        "_car_t30":     car30,
        "_car_t90":     (net90 if net90 is not None else net30 * 1.5) + 0.01,
        "_bench_t30":   bench30,
        "_value_gbp":   50_000.0,
    }


def _pending_row(signal_id, fired_iso, ticker, *, car1=None, fp=None):
    """A NOT-YET-MATURED firing: the T+21 window has not elapsed.

    `_net_car_t30` / `_car_t30` / `_car_t90` / `_net_car_t90` / `_bench_t30`
    are all None (no matured returns). `car_t1` may already be populated
    (the +1 day return is reachable immediately) -> pass `car1` to populate
    it, or None to leave it absent too.
    """
    return {
        "signal_id":    signal_id,
        "_fired_at":    fired_iso,
        "ticker":       ticker,
        "fingerprint":  fp or f"{ticker}-{fired_iso[:10]}",
        "_net_car_t1":  car1,
        "_net_car_t30": None,
        "_net_car_t90": None,
        "_car_t1":      car1,
        "_car_t30":     None,
        "_car_t90":     None,
        "_bench_t30":   None,
        "_value_gbp":   50_000.0,
    }


def make_synthetic_rows():
    """3 groups across several months.

    t3_ned_buy:
      2025-06  N=5  clean spread
      2025-07  (GAP — no rows)
      2025-08  N=3  LOW-N
      2025-09  N=4  DOMINANT TICKER (one TIN trade huge, others tiny)
      2025-10  N=6
      2025-11  N=5
      2025-12  N=5
    f1_first_time_buy:
      2025-10  N=8
      2025-11  N=8
      2025-12  N=8
    t1b_cfo_buy:
      2025-12  N=2  (low-N, single month)
    Plus one out-of-range row in 2026-01 (current, incomplete) that must be
    excluded.
    """
    rows = []

    # ---- t3: 2025-06 (N=5, clean, positive-ish) ----
    for i, v in enumerate([0.02, 0.03, -0.01, 0.04, 0.01]):
        rows.append(_row("t3_ned_buy", "2025-06-05T08:00:00Z",
                         f"AAA{i}", v))

    # ---- t3: 2025-07 = GAP (no rows) ----

    # ---- t3: 2025-08 (N=3, low-N) ----
    for i, v in enumerate([0.05, -0.02, 0.01]):
        rows.append(_row("t3_ned_buy", "2025-08-10T08:00:00Z",
                         f"BBB{i}", v))

    # ---- t3: 2025-09 (N=4, dominant ticker TIN drives the mean) ----
    # TIN net +0.60, others tiny -> TIN should be >50% of cohort weight.
    rows.append(_row("t3_ned_buy", "2025-09-11T08:00:00Z", "TIN", 0.60))
    for i, v in enumerate([0.005, -0.004, 0.006]):
        rows.append(_row("t3_ned_buy", "2025-09-20T08:00:00Z",
                         f"CCC{i}", v))

    # ---- t3: 2025-10 (N=6) ----
    for i, v in enumerate([0.01, 0.02, -0.03, 0.04, -0.01, 0.02]):
        rows.append(_row("t3_ned_buy", "2025-10-05T08:00:00Z",
                         f"DDD{i}", v))

    # ---- t3: 2025-11 (N=5) ----
    for i, v in enumerate([0.03, -0.02, 0.01, 0.00, 0.02]):
        rows.append(_row("t3_ned_buy", "2025-11-05T08:00:00Z",
                         f"EEE{i}", v))

    # ---- t3: 2025-12 (N=5) ----
    for i, v in enumerate([0.02, 0.01, 0.03, -0.01, 0.02]):
        rows.append(_row("t3_ned_buy", "2025-12-05T08:00:00Z",
                         f"FFF{i}", v))

    # ---- f1: 2025-10 / 11 / 12 (N=8 each) ----
    for mo in ("2025-10-12", "2025-11-12", "2025-12-12"):
        for i in range(8):
            v = 0.01 if i % 2 == 0 else -0.02
            rows.append(_row("f1_first_time_buy", f"{mo}T08:00:00Z",
                             f"GG{mo[5:7]}{i}", v))

    # ---- t1b: 2025-12 (N=2, low-N single month) ----
    rows.append(_row("t1b_cfo_buy", "2025-12-03T08:00:00Z", "HHH", 0.05))
    rows.append(_row("t1b_cfo_buy", "2025-12-03T08:00:00Z", "III", -0.03))

    # ---- t2: 2025-12 (N=4, ALL-NEGATIVE, dominated by worst loser DRAGX) ----
    # Sign-stability fixture: DRAGX carries most of the abs movement AND is the
    # worst loser. abs-share must flag it as a DRAG, not a contributor.
    rows.append(_row("t2_exec_buy", "2025-12-09T08:00:00Z", "DRAGX", -0.50))
    for i, v in enumerate([-0.04, -0.05, -0.03]):
        rows.append(_row("t2_exec_buy", "2025-12-15T08:00:00Z",
                         f"NEG{i}", v))

    # ---- t6: PENDING-month fixture (B-072) ----
    # 2025-11 N=2 MATURED (mean not null) -> pending:false, drilldown present.
    # 2025-12 (latest COMPLETE month) N=3 PENDING: signals fired but the T+21
    # window has not elapsed -> net_car_t30 / car_t30 / car_t90 / bench null,
    # car_t1 populated. mean_car_t30 must be null; the month + drilldown must
    # carry pending:true.
    rows.append(_row("t6_company_sec_buy", "2025-11-04T08:00:00Z", "MAT0", 0.03))
    rows.append(_row("t6_company_sec_buy", "2025-11-04T08:00:00Z", "MAT1", -0.01))
    rows.append(_pending_row("t6_company_sec_buy", "2025-12-02T08:00:00Z",
                             "PEND0", car1=0.011))
    rows.append(_pending_row("t6_company_sec_buy", "2025-12-18T08:00:00Z",
                             "PEND1", car1=0.022))
    rows.append(_pending_row("t6_company_sec_buy", "2025-12-09T08:00:00Z",
                             "PEND2", car1=None))

    # ---- out-of-range: 2026-01 (current, incomplete month) — excluded ----
    rows.append(_row("t3_ned_buy", "2026-01-05T08:00:00Z", "ZZZ", 0.99))

    return rows


class CohortPerformanceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows = make_synthetic_rows()
        cls.payload = ex.build_cohort_performance(
            cls.rows, TODAY, tx_lookup={}, emit_timestamp=False,
        )
        cls.t3 = cls.payload["groups"]["t3"]
        cls.f1 = cls.payload["groups"]["f1"]
        cls.t1b = cls.payload["groups"]["t1b"]
        cls.t2 = cls.payload["groups"]["t2"]
        cls.t6 = cls.payload["groups"]["t6"]   # B-072 pending fixture

    # ---- top-level shape ----

    def test_top_level_keys(self):
        for key in ("horizon", "signal_groups", "groups", "cohort_drilldown"):
            self.assertIn(key, self.payload)
        self.assertEqual(self.payload["horizon"], "t30")
        # B-117: COHORT_SIGNAL_GROUPS grew to 12 when b1 was added (B-099 / Sprint 24).
        self.assertEqual(len(self.payload["signal_groups"]), 12)

    def test_group_keys_present(self):
        g = self.t3
        for key in ("label", "color_hex", "header", "months",
                    "sparkline_points", "trend_3m_vs_prior3m_t30"):
            self.assertIn(key, g)
        # color_hex reused from TIER_PALETTE (t3 = emerald-500).
        self.assertEqual(g["color_hex"], "#10b981")

    def test_month_entry_keys(self):
        m = self.t3["months"][0]
        for key in ("month_iso", "n_signals", "mean_car_t1", "mean_car_t30",
                    "mean_car_t90", "min_car_t30", "max_car_t30",
                    "hit_rate_t30", "hit_rate_t30_rolling_6m",
                    "single_ticker_weight", "ma3_mean_car_t30", "signal_ids"):
            self.assertIn(key, m)

    # ---- month grouping + ordering ----

    def test_months_ascending_and_contiguous(self):
        months = [m["month_iso"] for m in self.t3["months"]]
        # inception 2025-06 .. latest complete 2025-12, contiguous incl gap.
        self.assertEqual(
            months,
            ["2025-06", "2025-07", "2025-08", "2025-09",
             "2025-10", "2025-11", "2025-12"],
        )

    def test_gap_month_has_zero_signals(self):
        gap = next(m for m in self.t3["months"] if m["month_iso"] == "2025-07")
        self.assertEqual(gap["n_signals"], 0)
        self.assertIsNone(gap["mean_car_t30"])
        self.assertIsNone(gap["min_car_t30"])
        self.assertIsNone(gap["hit_rate_t30"])

    def test_current_incomplete_month_excluded(self):
        months = [m["month_iso"] for m in self.t3["months"]]
        self.assertNotIn("2026-01", months)
        # And the ZZZ +0.99 trade must not pollute the header mean.
        self.assertLess(self.t3["header"]["mean_car_t30_overall"], 0.1)

    def test_n_signals_correct(self):
        bymo = {m["month_iso"]: m["n_signals"] for m in self.t3["months"]}
        self.assertEqual(bymo["2025-06"], 5)
        self.assertEqual(bymo["2025-08"], 3)   # low-N
        self.assertEqual(bymo["2025-09"], 4)
        self.assertEqual(bymo["2025-12"], 5)

    # ---- sparkline: explicit null for gap month ----

    def test_sparkline_points_null_for_gap(self):
        sp = {p["month_iso"]: p["mean_car_t30"]
              for p in self.t3["sparkline_points"]}
        self.assertIn("2025-07", sp)
        self.assertIsNone(sp["2025-07"])          # explicit null, Rupert's call
        self.assertIsNotNone(sp["2025-06"])
        # ordered ascending from inception, same length as months[].
        self.assertEqual(
            [p["month_iso"] for p in self.t3["sparkline_points"]],
            [m["month_iso"] for m in self.t3["months"]],
        )

    # ---- ma3: null for first 2 months, then trailing-3 mean ----

    def test_ma3_null_first_two_then_value(self):
        months = self.t3["months"]
        self.assertIsNone(months[0]["ma3_mean_car_t30"])   # 2025-06
        self.assertIsNone(months[1]["ma3_mean_car_t30"])   # 2025-07 (gap)
        # 2025-08 is the 3rd month -> ma3 = mean of (06, 07-gap, 08) real means.
        self.assertIsNotNone(months[2]["ma3_mean_car_t30"])
        m06 = months[0]["mean_car_t30"]
        m08 = months[2]["mean_car_t30"]
        # gap month contributes nothing -> average of the two real means.
        self.assertAlmostEqual(
            months[2]["ma3_mean_car_t30"],
            round((m06 + m08) / 2.0, 4), places=4,
        )

    # ---- rolling-6m hit rate ----

    def test_rolling_hit_rate_window(self):
        # For 2025-08, window = 2025-03..2025-08 -> trades from 06 (5) + 08 (3).
        # 06 nets: +,+,-,+,+ -> 4 wins. 08 nets: +,-,+ -> 2 wins. total 6/8.
        bymo = {m["month_iso"]: m["hit_rate_t30_rolling_6m"]
                for m in self.t3["months"]}
        self.assertAlmostEqual(bymo["2025-08"], round(6 / 8, 4), places=4)

    def test_rolling_hit_rate_direct_helper(self):
        # Direct unit on the helper with a clean fixture.
        month_rows = {
            "2025-01": [0.1, -0.1],          # 1 win / 2
            "2025-02": [0.1, 0.1, 0.1],      # 3 wins / 3
        }
        out = ex._rolling_hit_rate(month_rows, ["2025-01", "2025-02"], 6)
        self.assertAlmostEqual(out["2025-01"], 0.5, places=4)
        # 2025-02 window includes both months: 4 wins / 5.
        self.assertAlmostEqual(out["2025-02"], round(4 / 5, 4), places=4)

    # ---- single-ticker dominance ----

    def test_single_ticker_weight_detection(self):
        sep = next(m for m in self.t3["months"]
                   if m["month_iso"] == "2025-09")
        # TIN dominates: its weight must exceed 0.5.
        self.assertIsNotNone(sep["single_ticker_weight"])
        self.assertGreater(sep["single_ticker_weight"], 0.5)
        # A balanced month should be well under 0.5.
        jun = next(m for m in self.t3["months"]
                   if m["month_iso"] == "2025-06")
        self.assertLess(jun["single_ticker_weight"], 0.5)

    def test_single_ticker_weight_bounded(self):
        # Bounded abs-share: every emitted single_ticker_weight is <= 1.0.
        for grp in self.payload["groups"].values():
            for m in grp["months"]:
                w = m["single_ticker_weight"]
                if w is not None:
                    self.assertGreaterEqual(w, 0.0)
                    self.assertLessEqual(w, 1.0)

    def test_negative_mean_month_worst_loser_not_top_contributor(self):
        # Sign-stability through the full payload: t2 2025-12 is an all-negative
        # cohort dominated by the worst loser DRAGX. The verdict must label it a
        # DRAG, never a contributor, and single_ticker_weight stays in [0, 1].
        m = next(x for x in self.payload["groups"]["t2"]["months"]
                 if x["month_iso"] == "2025-12")
        self.assertIsNotNone(m["single_ticker_weight"])
        self.assertGreater(m["single_ticker_weight"], 0.5)
        self.assertLessEqual(m["single_ticker_weight"], 1.0)
        block = self.payload["cohort_drilldown"]["t2"]["2025-12"]
        self.assertIn("DRAGX", block["verdict"])
        self.assertIn("largest single drag", block["verdict"])
        self.assertNotIn("contributor", block["verdict"])

    # ---- share-of-cohort-movement (abs-share) sums to ~100% ----

    def test_contributions_sum_to_one(self):
        for grp, monthmap in self.payload["cohort_drilldown"].items():
            for mo, block in monthmap.items():
                # B-072: pending blocks carry null cohort_weight — skip them.
                if block.get("pending"):
                    continue
                total = sum(s["cohort_weight"] for s in block["signals"])
                self.assertAlmostEqual(
                    total, 1.0, places=2,
                    msg=f"{grp}/{mo} sum={total}")

    def test_abs_share_bounded_and_sums_to_one(self):
        # Bounded abs-magnitude share: each weight in [0, 1], sums to ~1.
        nets = [0.10, -0.10, 0.05, -0.05]
        weights = ex._cohort_contributions(nets)
        self.assertAlmostEqual(sum(weights), 1.0, places=6)
        for w in weights:
            self.assertGreaterEqual(w, 0.0)
            self.assertLessEqual(w, 1.0)
        # all-zero degenerate -> equal weight, sums to 1.
        w2 = ex._cohort_contributions([0.0, 0.0])
        self.assertEqual(w2, [0.5, 0.5])
        self.assertAlmostEqual(sum(w2), 1.0, places=6)
        # empty input -> empty list.
        self.assertEqual(ex._cohort_contributions([]), [])

    def test_abs_share_sign_stable_on_all_negative(self):
        # All-negative cohort: abs-share must NOT invert. The largest |r|
        # (worst loser here) genuinely carries the largest share, and every
        # weight stays in [0, 1] (the old signed formula went negative / >1).
        nets = [-0.40, -0.05, -0.05]
        weights = ex._cohort_contributions(nets)
        self.assertAlmostEqual(sum(weights), 1.0, places=6)
        for w in weights:
            self.assertGreaterEqual(w, 0.0)
            self.assertLessEqual(w, 1.0)
        # index 0 has the largest |r| -> largest share.
        self.assertEqual(max(range(len(weights)), key=lambda i: weights[i]), 0)
        self.assertGreater(weights[0], 0.5)

    def test_normal_positive_mean_abs_share(self):
        nets = [0.10, 0.20, 0.30]
        weights = ex._cohort_contributions(nets)
        self.assertAlmostEqual(sum(weights), 1.0, places=6)
        # 0.30 carries the largest share.
        self.assertEqual(max(range(len(weights)), key=lambda i: weights[i]), 2)

    # ---- verdict string ----

    def test_verdict_contributor_on_dominant_positive_ticker(self):
        # 2025-09: TIN at +0.60 dominates (positive) -> "contributor" wording.
        block = self.payload["cohort_drilldown"]["t3"]["2025-09"]
        self.assertIn("TIN", block["verdict"])
        self.assertIn("largest single contributor", block["verdict"])
        self.assertIn("net CAR", block["verdict"])
        self.assertNotIn("contribution_basis", block)

    def test_verdict_drag_when_dominant_ticker_negative(self):
        # All-negative cohort, one ticker carries most of the abs movement and
        # has a negative own net CAR -> "drag" wording, never "contributor".
        sigs = [
            {"ticker": "BAD", "net_car_t30": -0.40},
            {"ticker": "OK1", "net_car_t30": -0.04},
            {"ticker": "OK2", "net_car_t30": -0.03},
        ]
        v = ex.cohort_verdict(sigs)
        self.assertIn("BAD", v)
        self.assertIn("largest single drag", v)
        self.assertNotIn("contributor", v)
        # negative branch prints a leading '-' and no '+'.
        self.assertIn("-40.0% net CAR", v)
        self.assertNotIn("+", v)

    def test_verdict_contributor_when_dominant_ticker_positive(self):
        sigs = [
            {"ticker": "WIN", "net_car_t30": 0.40},
            {"ticker": "OK1", "net_car_t30": 0.04},
            {"ticker": "OK2", "net_car_t30": -0.03},
        ]
        v = ex.cohort_verdict(sigs)
        self.assertIn("WIN", v)
        self.assertIn("largest single contributor", v)
        self.assertIn("+40.0% net CAR", v)

    def test_verdict_percent_never_exceeds_100(self):
        # The old signed formula printed shares >100% on negative means. The
        # abs-share is bounded, so the {share}% token is always <= 100.
        import re
        sigs = [
            {"ticker": "BAD", "net_car_t30": -0.90},
            {"ticker": "OK", "net_car_t30": -0.02},
        ]
        v = ex.cohort_verdict(sigs)
        pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)% of total", v)]
        self.assertTrue(pcts)
        for p in pcts:
            self.assertLessEqual(p, 100.0)

    def test_verdict_empty_on_balanced_spread_above_threshold(self):
        # 2025-06: spread is 0.04 - (-0.01) = 0.05 -> NOT < 0.05, no dominant
        # ticker -> empty verdict.
        block = self.payload["cohort_drilldown"]["t3"].get("2025-06")
        self.assertIsNotNone(block)
        self.assertEqual(block["verdict"], "")

    def test_verdict_consistent_when_tight_spread(self):
        sigs = [
            {"ticker": "A", "net_car_t30": 0.01},
            {"ticker": "B", "net_car_t30": 0.02},
            {"ticker": "C", "net_car_t30": 0.03},
        ]
        self.assertEqual(ex.cohort_verdict(sigs),
                         "Cohort outcomes were broadly consistent.")

    # ---- drill-down row shape ----

    def test_drilldown_signal_row_keys(self):
        block = self.payload["cohort_drilldown"]["t3"]["2025-09"]
        row = block["signals"][0]   # default sort = contribution desc -> TIN
        self.assertEqual(row["ticker"], "TIN")
        for key in ("ticker", "director", "role_short", "fire_date",
                    "car_t1", "car_t30", "car_t90", "benchmark_t30",
                    "net_car_t30", "cohort_weight"):
            self.assertIn(key, row)
        self.assertEqual(row["role_short"], "NED")
        self.assertEqual(row["benchmark_t30"], 0.01)

    def test_drilldown_default_sort_contribution_desc(self):
        block = self.payload["cohort_drilldown"]["t3"]["2025-09"]
        weights = [s["cohort_weight"] for s in block["signals"]]
        self.assertEqual(weights, sorted(weights, reverse=True))

    # ---- header rollups ----

    def test_header_keys_and_totals(self):
        h = self.t3["header"]
        for key in ("n_total_signals", "mean_car_t30_overall",
                    "hit_rate_t30_overall"):
            self.assertIn(key, h)
        # total = 5+0+3+4+6+5+5 = 28 (excludes 2026-01 ZZZ).
        self.assertEqual(h["n_total_signals"], 28)

    # ---- trend ----

    def test_trend_present_and_signed(self):
        # f1 last3 (10,11,12) vs prior3 (07,08,09 — none exist) -> None,
        # because f1 inception is 2025-10 so prior3 window empty.
        self.assertIsNone(self.f1["trend_3m_vs_prior3m_t30"])
        # t3 has 7 months -> both windows populated -> a float.
        self.assertIsInstance(self.t3["trend_3m_vs_prior3m_t30"], float)

    # ---- low-N month still emits a complete entry ----

    def test_low_n_month_complete(self):
        m = next(x for x in self.t1b["months"] if x["month_iso"] == "2025-12")
        self.assertEqual(m["n_signals"], 2)
        self.assertIsNotNone(m["mean_car_t30"])
        self.assertIsNotNone(m["hit_rate_t30"])

    # ---- helper: month math ----

    def test_month_minus(self):
        self.assertEqual(ex._month_minus("2025-03", 5), "2024-10")
        self.assertEqual(ex._month_minus("2025-01", 1), "2024-12")
        self.assertEqual(ex._month_minus("2025-06", 0), "2025-06")

    def test_completed_month(self):
        self.assertEqual(ex._completed_month(date(2026, 1, 15)), "2025-12")
        self.assertEqual(ex._completed_month(date(2025, 12, 1)), "2025-11")

    def test_month_range_inclusive(self):
        self.assertEqual(
            ex._month_range_inclusive("2025-11", "2026-02"),
            ["2025-11", "2025-12", "2026-01", "2026-02"],
        )

    # ---- B-072: pending (not-yet-matured) month ----

    def _t6_month(self, mo):
        return next(m for m in self.t6["months"] if m["month_iso"] == mo)

    def test_pending_month_in_months_with_flag(self):
        # 2025-12 is the latest complete month; t6's 2025-12 firings have not
        # matured -> n_signals>0, mean_car_t30 None, pending True.
        dec = self._t6_month("2025-12")
        self.assertEqual(dec["n_signals"], 3)
        self.assertIsNone(dec["mean_car_t30"])
        self.assertTrue(dec["pending"])
        # car_t1-derived mean may exist (2 of 3 carry car1); not asserted as a
        # specific value, but the pending flag must not depend on it.

    def test_matured_month_pending_false(self):
        nov = self._t6_month("2025-11")
        self.assertEqual(nov["n_signals"], 2)
        self.assertIsNotNone(nov["mean_car_t30"])
        self.assertFalse(nov["pending"])

    def test_gap_month_is_not_pending(self):
        # The t3 gap month (2025-07, n_signals==0) is empty, NOT pending.
        gap = next(m for m in self.t3["months"] if m["month_iso"] == "2025-07")
        self.assertEqual(gap["n_signals"], 0)
        self.assertFalse(gap["pending"])

    def test_all_matured_months_pending_false(self):
        # Every month with matured T+21 data across all groups is pending:false.
        for grp in self.payload["groups"].values():
            for m in grp["months"]:
                if m["mean_car_t30"] is not None:
                    self.assertFalse(
                        m["pending"],
                        msg=f"{m['month_iso']} matured but flagged pending")

    def test_pending_drilldown_present_with_flag(self):
        block = self.payload["cohort_drilldown"]["t6"]["2025-12"]
        self.assertTrue(block.get("pending"))
        self.assertEqual(block["verdict"], "")
        self.assertEqual(len(block["signals"]), 3)

    def test_pending_drilldown_signal_shape(self):
        block = self.payload["cohort_drilldown"]["t6"]["2025-12"]
        row = block["signals"][0]
        # Same key names as the matured drill-down rows (front-end reuse).
        for key in ("ticker", "director", "role_short", "fire_date",
                    "car_t1", "car_t30", "car_t90", "benchmark_t30",
                    "net_car_t30", "cohort_weight"):
            self.assertIn(key, row)
        self.assertEqual(row["role_short"], "Co-Sec")
        # All maturity-dependent fields null; cohort_weight null (no abs-share
        # fallback on partial data).
        for s in block["signals"]:
            self.assertIsNone(s["car_t30"])
            self.assertIsNone(s["car_t90"])
            self.assertIsNone(s["net_car_t30"])
            self.assertIsNone(s["benchmark_t30"])
            self.assertIsNone(s["cohort_weight"])

    def test_pending_drilldown_sorted_fire_date_desc(self):
        block = self.payload["cohort_drilldown"]["t6"]["2025-12"]
        dates = [s["fire_date"] for s in block["signals"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        # Explicit: 2025-12-18 (PEND1) first, 2025-12-02 (PEND0) last.
        self.assertEqual(dates[0], "2025-12-18")
        self.assertEqual(dates[-1], "2025-12-02")

    def test_matured_drilldown_has_no_pending_flag(self):
        # Matured drill-down entries are unchanged — no `pending` key added.
        block = self.payload["cohort_drilldown"]["t6"]["2025-11"]
        self.assertNotIn("pending", block)
        # And still carries computed cohort_weight (abs-share) summing to ~1.
        total = sum(s["cohort_weight"] for s in block["signals"])
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_pending_block_excluded_from_contribution_sum_check(self):
        # The contributions-sum invariant must skip pending blocks (their
        # weights are null). Recompute it here only over matured blocks.
        for grp, monthmap in self.payload["cohort_drilldown"].items():
            for mo, block in monthmap.items():
                if block.get("pending"):
                    continue
                total = sum(s["cohort_weight"] for s in block["signals"])
                self.assertAlmostEqual(total, 1.0, places=2,
                                       msg=f"{grp}/{mo} sum={total}")


# ---------------------------------------------------------------------------
# Sprint 24 — multi-horizon extensions
# ---------------------------------------------------------------------------

class Sprint24HorizonBackendTests(unittest.TestCase):
    """Tests for the per-horizon extensions added in Sprint 24 (B-Sprint24).

    Uses the same synthetic dataset as CohortPerformanceTests.
    The synthetic rows carry _net_car_t90 = net30 * 1.5 (from _row helper),
    so t90 matured values exist for every month that has t30 values.
    t365 is NOT set in the synthetic rows (the helper doesn't add it), so
    t365 columns will be null for every month — allowing the pending_horizons
    logic to be tested.
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = make_synthetic_rows()
        cls.payload = ex.build_cohort_performance(
            cls.rows, TODAY, tx_lookup={}, emit_timestamp=False,
        )
        cls.t3 = cls.payload["groups"]["t3"]
        cls.t6 = cls.payload["groups"]["t6"]

    # ---- load_backtest_csv additions ----

    def test_load_backtest_csv_adds_net_car_t365(self):
        # Build a minimal CSV-like row dict and confirm the new keys are hydrated.
        import io, csv as csvmod
        lines = [
            "net_car_t365,benchmark_return_t1,benchmark_return_t90,"
            "benchmark_return_t365,net_car_t1,net_car_t30,net_car_t90,"
            "car_t1,car_t30,car_t90,car_t365,value_gbp,fired_at",
            "0.12,0.01,0.03,0.04,0.02,0.05,0.07,0.06,0.055,0.08,0.13,50000,2025-06-01",
        ]
        tmp = io.StringIO("\n".join(lines))
        reader = csvmod.DictReader(tmp)
        raw = [dict(r) for r in reader]
        # Manually run the hydration logic (mirrors load_backtest_csv).
        import export_dashboard_json as ex2
        from pathlib import Path
        import tempfile, os
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write("\n".join(lines))
            fname = f.name
        try:
            result = ex2.load_backtest_csv(Path(fname))
        finally:
            os.unlink(fname)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertAlmostEqual(row["_net_car_t365"], 0.12, places=6)
        self.assertAlmostEqual(row["_bench_t1"],    0.01, places=6)
        self.assertAlmostEqual(row["_bench_t90"],   0.03, places=6)
        self.assertAlmostEqual(row["_bench_t365"],  0.04, places=6)

    # ---- month entry carries new horizon keys ----

    def test_month_entry_has_t90_extended_keys(self):
        m = self.t3["months"][0]   # 2025-06, N=5, all t90 matured
        for key in ("min_car_t90", "max_car_t90", "hit_rate_t90",
                    "hit_rate_t90_rolling_6m", "single_ticker_weight_t90",
                    "ma3_mean_car_t90"):
            self.assertIn(key, m, msg=f"Missing key: {key}")

    def test_month_entry_has_t365_keys(self):
        m = self.t3["months"][0]
        for key in ("mean_car_t365", "min_car_t365", "max_car_t365",
                    "hit_rate_t365", "hit_rate_t365_rolling_6m",
                    "single_ticker_weight_t365", "ma3_mean_car_t365"):
            self.assertIn(key, m, msg=f"Missing key: {key}")

    def test_month_entry_has_t1_extended_keys(self):
        m = self.t3["months"][0]
        for key in ("min_car_t1", "max_car_t1", "hit_rate_t1",
                    "hit_rate_t1_rolling_6m", "single_ticker_weight_t1",
                    "ma3_mean_car_t1"):
            self.assertIn(key, m, msg=f"Missing key: {key}")

    def test_t90_matured_values_populated(self):
        # Synthetic data: t90 = net30 * 1.5. Should be non-null for t3 2025-06.
        jun = next(m for m in self.t3["months"] if m["month_iso"] == "2025-06")
        self.assertIsNotNone(jun["mean_car_t90"])
        self.assertIsNotNone(jun["min_car_t90"])
        self.assertIsNotNone(jun["max_car_t90"])
        self.assertIsNotNone(jun["hit_rate_t90"])

    def test_t365_null_when_not_in_csv(self):
        # Synthetic rows have no net_car_t365 -> all t365 per-month fields null.
        for m in self.t3["months"]:
            self.assertIsNone(m["mean_car_t365"],
                              msg=f"Expected null mean_car_t365 for {m['month_iso']}")

    def test_t30_keys_present(self):
        # T+30 keys must be present.
        m = self.t3["months"][0]
        for key in ("mean_car_t30", "min_car_t30", "max_car_t30",
                    "hit_rate_t30", "hit_rate_t30_rolling_6m",
                    "single_ticker_weight", "ma3_mean_car_t30"):
            self.assertIn(key, m, msg=f"Missing key: {key}")

    def test_single_ticker_weight_t30_matches_original(self):
        # The single_ticker_weight_t30 should match the old single_ticker_weight.
        for m in self.t3["months"]:
            self.assertEqual(
                m.get("single_ticker_weight"),
                m.get("single_ticker_weight_t30"),
                msg=f"Mismatch for {m['month_iso']}",
            )

    # ---- pending_horizons ----

    def test_pending_horizons_key_present(self):
        for grp in self.payload["groups"].values():
            for m in grp["months"]:
                self.assertIn("pending_horizons", m)

    def test_pending_horizons_empty_for_gap_month(self):
        gap = next(m for m in self.t3["months"] if m["month_iso"] == "2025-07")
        self.assertEqual(gap["pending_horizons"], [])

    def test_pending_horizons_includes_t365_when_no_t365_data(self):
        # All synthetic months with n_signals>0 have no t365 data -> t365 pending.
        for m in self.t3["months"]:
            if m["n_signals"] > 0:
                self.assertIn("t365", m["pending_horizons"],
                              msg=f"Expected t365 in pending_horizons for {m['month_iso']}")

    def test_pending_horizons_excludes_t90_when_t90_matured(self):
        # t90 data IS populated in synthetic rows -> t90 should NOT be in pending_horizons
        # for months that have matured t90.
        for m in self.t3["months"]:
            if m["n_signals"] > 0 and m["mean_car_t90"] is not None:
                self.assertNotIn("t90", m["pending_horizons"],
                                 msg=f"t90 should not be pending for {m['month_iso']}")

    def test_t6_pending_month_has_all_horizons_pending(self):
        # t6 2025-12: signals fired but nothing matured (pending row) -> all horizons.
        dec = next(m for m in self.t6["months"] if m["month_iso"] == "2025-12")
        # B-117: the fixture deliberately populates _car_t1 on 2 of the 3 rows, so
        # the t1 mean is non-null and t1 is correctly NOT pending (pending = mean
        # is None). Assert the three horizons that are genuinely unmatured here.
        for h in ("t30", "t90", "t365"):
            self.assertIn(h, dec["pending_horizons"])

    # ---- header rollups ----

    def test_header_has_multi_horizon_rollups(self):
        h = self.t3["header"]
        for key in ("mean_car_t1_overall", "hit_rate_t1_overall",
                    "mean_car_t90_overall", "hit_rate_t90_overall",
                    "mean_car_t365_overall", "hit_rate_t365_overall"):
            self.assertIn(key, h, msg=f"Missing header key: {key}")

    def test_header_t90_rollup_non_null(self):
        # t90 data exists in synthetic rows -> rollup should be non-null.
        h = self.t3["header"]
        self.assertIsNotNone(h["mean_car_t90_overall"])
        self.assertIsNotNone(h["hit_rate_t90_overall"])

    def test_header_t365_rollup_null(self):
        # t365 absent from synthetic data -> null rollups.
        h = self.t3["header"]
        self.assertIsNone(h["mean_car_t365_overall"])
        self.assertIsNone(h["hit_rate_t365_overall"])

    # ---- idempotency ----

    def test_idempotent(self):
        p2 = ex.build_cohort_performance(
            self.rows, TODAY, tx_lookup={}, emit_timestamp=False,
        )
        self.assertEqual(self.payload, p2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
