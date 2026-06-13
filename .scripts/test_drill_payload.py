"""Unit tests for build_drill_payload + build_bucket_payload
(Sprint 3 — Performance page redesign v1).

Pinned to spec §5.2 (drill-down JSON shape) and §5.3 (firing-row schema).
Per Rupert's locked decisions:
  Q2 — cohort-level outlier_flag = True when any firing in the drill block
       has |CAR| > 2.0 (fraction). Per-firing outlier_flag DROPPED in v1.2.
  Q5 — multi-signal precedence on firing-row signal_tier = SIGNAL_ORDER
       (t0 > t1a > t1b > t7 > t2 > t3 > t5 > t6 > t4 > s1 > f1).
  Q6 — company name source = transactions.company most-recent (tx_lookup
       built externally; tests pass synthetic tx_lookup dicts directly).

B-027 (2026-05-21): refreshed for B-025 Phase B's 6-tier role payload
(t1a / t1b / t2 / t3 / t5 / t7) and 11-signal SIGNAL_ORDER. The bucket
scope filter is now `HIGH_CONVICTION_NON_NED_SIGNALS` =
(t1a_ceo_founder_buy, t1b_cfo_buy, t7_chair_buy, t2_exec_buy).

Run under:
    python .scripts/test_drill_payload.py
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import export_dashboard_json as edj  # noqa: E402


def _mk_row(
    fired_at,
    signal_id="t1a_ceo_founder_buy",
    fingerprint=None,
    ticker="ABC",
    role="Chief Executive Officer",
    role_class="T1a",
    value_gbp=150_000.0,
    car_t30=0.05,
    car_t1=None,
    car_t90=None,
    car_t365=None,
    benchmark_return_t30=0.01,
):
    """Minimal CSV-row shape with the underscore-prefixed fields already
    populated (mirrors what `load_backtest_csv` produces)."""
    return {
        "fingerprint":           fingerprint or f"fp-{ticker}-{fired_at}",
        "signal_id":             signal_id,
        "ticker":                ticker,
        "role":                  role,
        "role_class":            role_class,
        "value_gbp":             value_gbp,
        "_fired_at":             fired_at,
        "_value_gbp":            value_gbp,
        "_car_t1":               car_t1,
        "_car_t30":              car_t30,
        "_car_t90":              car_t90,
        "_car_t365":             car_t365,
        "benchmark_return_t1":   None,
        "benchmark_return_t30":  benchmark_return_t30,
        "benchmark_return_t90":  None,
        "benchmark_return_t365": None,
    }


class TestBuildBucketPayloadShape(unittest.TestCase):
    """Top-level §5.2 shape and outer-shell semantics."""

    def test_01_top_level_keys_present(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01")]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=True)
        self.assertEqual(out["schema_version"], "1.0")
        self.assertIn("generated_at", out)
        self.assertIn("buckets", out)

    def test_02_bucket_keys_and_labels(self):
        today = date(2026, 5, 18)
        # Seed one firing in each of three buckets.
        rows = [
            _mk_row("2026-05-01", value_gbp=10_000),    # 1k-25k
            _mk_row("2026-05-01", value_gbp=50_000),    # 25k-100k
            _mk_row("2026-05-01", value_gbp=750_000),   # 500k+
        ]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        buckets = out["buckets"]
        self.assertEqual(
            set(buckets.keys()),
            {"1k-25k", "25k-100k", "500k+"},
        )
        self.assertEqual(buckets["1k-25k"]["label"], "£1–25k")
        self.assertEqual(buckets["500k+"]["label"], "£500k+")

    def test_03_scope_note_attached_per_cohort(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01")]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        for key, cohort in out["buckets"].items():
            self.assertEqual(cohort["scope_note"], "T1 + T2 buys only")

    def test_04_drill_block_has_all_nine_required_fields(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01")]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["90d"]
        # Per spec §5.2: 9 mandatory fields. outlier_flag is optional.
        required = {
            "benchmark_car_pct", "total_firings", "distinct_tickers",
            "tickers_with_n3", "hit_pct", "median_car",
            "top_firings", "bottom_firings", "rollup",
        }
        self.assertEqual(required.issubset(block.keys()), True)

    def test_05_horizon_and_lookback_grid_complete(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01", car_t1=0.01, car_t90=0.02, car_t365=0.03)]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        cohort = out["buckets"]["100k-500k"]
        for h in ("t1", "t30", "t90", "t365"):
            self.assertIn(h, cohort)
            for lb in ("30d", "90d", "6m", "1y", "all"):
                self.assertIn(lb, cohort[h])


class TestBuildBucketPayloadSorting(unittest.TestCase):
    """Top 10 / bottom 10 ordering + the <10 losers edge case."""

    def test_06_top_firings_sorted_by_car_desc_bottom_asc(self):
        today = date(2026, 5, 18)
        # 12 firings in the 100k-500k bucket with distinct CARs.
        cars = [0.08, 0.07, 0.06, 0.05, 0.04, 0.03,
                -0.01, -0.02, -0.03, -0.04, -0.05, -0.06]
        rows = [
            _mk_row("2026-05-01",
                    fingerprint=f"fp-{i}",
                    ticker=f"T{i:02d}",
                    car_t30=c)
            for i, c in enumerate(cars)
        ]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        top_cars = [f["car"] for f in block["top_firings"]]
        bottom_cars = [f["car"] for f in block["bottom_firings"]]
        self.assertEqual(len(block["top_firings"]), 10)
        self.assertEqual(len(block["bottom_firings"]), 10)
        # Top: desc; bottom: asc.
        self.assertEqual(top_cars, sorted(top_cars, reverse=True))
        self.assertEqual(bottom_cars, sorted(bottom_cars))
        # Top should contain the biggest, bottom the smallest.
        self.assertEqual(top_cars[0], 8.0)
        self.assertEqual(bottom_cars[0], -6.0)

    def test_07_fewer_than_10_firings_shrinks_panels(self):
        """Spec §2.3: when <10 firings, both panels show what's available
        and FE renders the edge-case note."""
        today = date(2026, 5, 18)
        cars = [0.05, 0.03, -0.01, -0.02]
        rows = [
            _mk_row("2026-05-01",
                    fingerprint=f"fp-{i}",
                    ticker=f"T{i:02d}",
                    car_t30=c)
            for i, c in enumerate(cars)
        ]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        self.assertLessEqual(len(block["top_firings"]), 4)
        self.assertLessEqual(len(block["bottom_firings"]), 4)
        self.assertEqual(block["total_firings"], 4)


class TestBuildBucketPayloadEdgeCases(unittest.TestCase):
    """Outlier flag, empty cohort, scope filter, signal-tier precedence."""

    def test_08_outlier_flag_emitted_at_drill_block_level(self):
        """Rupert Q2: drill_block carries outlier_flag=True if any firing
        in scope has |car| > 2.0. The flag is on the cohort drill_block,
        NOT on individual firing rows (per spec §5.3 v1.2)."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", car_t30=0.05),
            _mk_row("2026-05-01",
                    fingerprint="fp-outlier",
                    ticker="OUT",
                    car_t30=2.5),  # |car| = 250% — trips flag
        ]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        self.assertTrue(block.get("outlier_flag"))
        # Per-firing rows must NOT carry outlier_flag in v1.2.
        for f in block["top_firings"] + block["bottom_firings"]:
            self.assertNotIn("outlier_flag", f)
            self.assertNotIn("bench_car", f)

    def test_09_scope_filter_includes_all_buy_signals(self):
        """B-104 (Sprint 30): HIGH_CONVICTION_NON_NED_SIGNALS was expanded to
        include all buy signal types (t1a/t1b/t2/t3/t4/t5/t6/t7/s1/f1/b1).
        All three rows should appear in the bucket count."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01",
                    signal_id="t1a_ceo_founder_buy", value_gbp=150_000),
            _mk_row("2026-05-01",
                    signal_id="t3_ned_buy", fingerprint="fp-ned",
                    value_gbp=150_000, car_t30=-0.20),
            _mk_row("2026-05-01",
                    signal_id="s1_cluster_buy", fingerprint="fp-s1",
                    value_gbp=150_000, car_t30=-0.30),
        ]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        # B-104: all three signal types now in scope.
        self.assertEqual(block["total_firings"], 3)
        # Median of [5.0, -20.0, -30.0] = -20.0
        self.assertEqual(block["median_car"], -20.0)

    def test_10_empty_input_returns_empty_buckets_no_crash(self):
        today = date(2026, 5, 18)
        out = edj.build_bucket_payload([], today, emit_timestamp=False)
        self.assertEqual(out["buckets"], {})
        self.assertEqual(out["schema_version"], "1.0")

    def test_11_signal_tier_uses_highest_precedence_when_multi_signal(self):
        """Rupert Q5: if a single fingerprint fires multiple signals, the
        firing-row badge shows the highest-precedence tier (t0 > t1a >
        t1b > t7 > t2 > t3 > t5 > t6 > t4 > s1 > f1).
        Test seeds a fingerprint firing both T1a and F1; expects badge=t1a."""
        today = date(2026, 5, 18)
        fp = "fp-multi"
        rows = [
            _mk_row("2026-05-01",
                    signal_id="t1a_ceo_founder_buy",
                    fingerprint=fp, ticker="MULT"),
            _mk_row("2026-05-01",
                    signal_id="f1_first_time_buy",
                    fingerprint=fp, ticker="MULT",
                    value_gbp=150_000),
        ]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        # Both rows feed in (both have signal_id in scope filter? f1 is NOT —
        # so only the T1a row appears, but signal_tier_lookup considers all
        # rows from the input pool and resolves fp -> t1a).
        for f in block["top_firings"]:
            if f["ticker"] == "MULT":
                self.assertEqual(f["signal_tier"], "t1a")


class TestFiringRowSchema(unittest.TestCase):
    """§5.3 firing-row schema and Rupert Q6 (company from tx_lookup)."""

    def test_12_firing_row_carries_expected_fields(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-14", ticker="AAL", role="CEO")]
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        self.assertEqual(len(block["top_firings"]), 1)
        f = block["top_firings"][0]
        # B-025 Phase A added `role_normalized`; Sprint-29 added `abs_return`;
        # B113 added `bench_return`. 12 keys total now.
        expected_keys = {
            "date", "ticker", "company", "director", "role", "role_normalized",
            "role_class", "signal_tier", "value_gbp", "car",
            "abs_return", "bench_return",
        }
        self.assertEqual(set(f.keys()), expected_keys)
        self.assertEqual(f["date"], "2026-05-14")
        self.assertEqual(f["ticker"], "AAL")
        self.assertEqual(f["role"], "CEO")
        # Default _mk_row uses signal_id="t1a_ceo_founder_buy" → short tier "t1a".
        self.assertEqual(f["signal_tier"], "t1a")

    def test_13_company_sourced_from_tx_lookup_per_rupert_q6(self):
        """Rupert Q6: company name comes from transactions.company most-recent
        (passed via tx_lookup). Test confirms the field flows through."""
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01", ticker="AAL")]
        tx_lookup = {
            rows[0]["fingerprint"]: {
                "director": "Duncan Wanblad",
                "company":  "Anglo American Plc",
                "role":     "CEO",
            }
        }
        out = edj.build_bucket_payload(
            rows, today, tx_lookup=tx_lookup, emit_timestamp=False
        )
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        f = block["top_firings"][0]
        self.assertEqual(f["company"], "Anglo American Plc")
        self.assertEqual(f["director"], "Duncan Wanblad")

    def test_14_no_per_firing_bench_car_or_outlier_flag_in_v1_2(self):
        """Spec §5.3 v1.2: bench_car and per-firing outlier_flag DROPPED."""
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01", car_t30=3.0)]  # huge outlier
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        block = out["buckets"]["100k-500k"]["t30"]["all"]
        # Block-level outlier_flag IS expected (Rupert Q2).
        self.assertTrue(block.get("outlier_flag"))
        # But the firing row itself must NOT have bench_car or outlier_flag.
        f = block["top_firings"][0]
        self.assertNotIn("bench_car", f)
        self.assertNotIn("outlier_flag", f)


class TestTickerRollup(unittest.TestCase):
    """§5.2 rollup ordering — N>=3 first, then N<3."""

    def test_15_rollup_sorts_n_ge_3_before_n_lt_3(self):
        today = date(2026, 5, 18)
        # Seed 3 tickers: AAA with 4 firings, BBB with 2, CCC with 1.
        rows: list = []
        for i in range(4):
            rows.append(_mk_row(
                "2026-05-01",
                fingerprint=f"fp-AAA-{i}",
                ticker="AAA", car_t30=0.04,
            ))
        for i in range(2):
            rows.append(_mk_row(
                "2026-05-01",
                fingerprint=f"fp-BBB-{i}",
                ticker="BBB", car_t30=0.09,  # higher hit% but n<3
            ))
        rows.append(_mk_row(
            "2026-05-01",
            fingerprint="fp-CCC-0",
            ticker="CCC", car_t30=0.10,
        ))
        out = edj.build_bucket_payload(rows, today, emit_timestamp=False)
        rollup = out["buckets"]["100k-500k"]["t30"]["all"]["rollup"]
        # AAA (n=4) must come first regardless of its hit%.
        self.assertEqual(rollup[0]["ticker"], "AAA")
        # BBB and CCC (n<3) come after, sorted by hit% desc within.
        self.assertEqual({r["ticker"] for r in rollup[1:]}, {"BBB", "CCC"})


# ---------------------------------------------------------------------------
# Sprint 4 — Role + Sector payload tests + resolve_sector_benchmark
# ---------------------------------------------------------------------------

class TestResolveSectorBenchmark(unittest.TestCase):
    """resolve_sector_benchmark fallback rules per spec §3.3."""

    def test_16_known_sector_returns_its_benchmark(self):
        tickers_meta = {
            "AAL": {"sector": "Materials",  "benchmark_symbol": "^FTNMX1770"},
            "AZN": {"sector": "Health Care", "benchmark_symbol": "^FTNMX2010"},
        }
        self.assertEqual(
            edj.resolve_sector_benchmark("Materials", tickers_meta),
            "^FTNMX1770",
        )

    def test_17_unknown_sector_returns_ftas_fallback(self):
        tickers_meta = {
            "AAL": {"sector": "Materials", "benchmark_symbol": "^FTNMX1770"},
        }
        self.assertEqual(
            edj.resolve_sector_benchmark("UnknownSector", tickers_meta),
            "^FTAS",
        )
        self.assertEqual(
            edj.resolve_sector_benchmark("", tickers_meta),
            "^FTAS",
        )
        self.assertEqual(
            edj.resolve_sector_benchmark(None, tickers_meta),
            "^FTAS",
        )

    def test_18_sector_with_null_benchmark_returns_ftas(self):
        """When the sector exists but its benchmark_symbol is missing or empty,
        the fallback should still kick in."""
        tickers_meta = {
            "X": {"sector": "Quirky", "benchmark_symbol": None},
            "Y": {"sector": "Quirky", "benchmark_symbol": ""},
        }
        self.assertEqual(
            edj.resolve_sector_benchmark("Quirky", tickers_meta),
            "^FTAS",
        )


class TestBuildRolePayload(unittest.TestCase):
    """build_role_payload — spec §5.2 role shape and Rupert decisions."""

    def test_19_role_payload_has_six_per_tier_keys(self):
        """B-025 Phase B (2026-05-20): the role tile now exposes 6 per-tier
        keys (t1a, t1b, t7, t2, t3, t5) instead of the legacy 3 combined
        (ceo_cfo / other_exec / ned). T4 catch-all and T6 Company Sec
        deliberately classify to None and are excluded."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", role="Chief Executive Officer",
                    role_class="", fingerprint="fp1", ticker="A"),
            _mk_row("2026-05-01", role="Chair",
                    role_class="", fingerprint="fp2", ticker="B"),
            _mk_row("2026-05-01", role="Non-Executive Director",
                    role_class="", fingerprint="fp3", ticker="C"),
            # This row will classify to None and must be dropped.
            _mk_row("2026-05-01", role="Company Secretary",
                    role_class="", fingerprint="fp4", ticker="D"),
        ]
        out = edj.build_role_payload(rows, today, emit_timestamp=False)
        self.assertEqual(out["schema_version"], "1.0")
        # CEO → t1a, Chair (bare) → t7, NED → t3. Co Sec → None (dropped).
        self.assertEqual(
            set(out["roles"].keys()),
            {"t1a", "t7", "t3"},
        )

    def test_20_role_labels_match_spec(self):
        today = date(2026, 5, 18)
        # role_class="" forces classify_role onto the bucket-lookup path so
        # the `role` string actually drives the tile choice (the default T1
        # would short-circuit everything to t1a via the legacy fallback).
        rows = [
            _mk_row("2026-05-01", role="CEO",
                    role_class="", fingerprint="fp1", ticker="A"),
            _mk_row("2026-05-01", role="CFO",
                    role_class="", fingerprint="fp1b", ticker="B"),
            _mk_row("2026-05-01", role="Chair",
                    role_class="", fingerprint="fp2", ticker="C"),
            _mk_row("2026-05-01", role="Chief Operating Officer",
                    role_class="", fingerprint="fp2b", ticker="D"),
            _mk_row("2026-05-01", role="Non-Executive Director",
                    role_class="", fingerprint="fp3", ticker="E"),
        ]
        out = edj.build_role_payload(rows, today, emit_timestamp=False)
        # Per ROLE_LABELS in export_dashboard_json.py.
        self.assertEqual(out["roles"]["t1a"]["label"], "CEO + Founder")
        self.assertEqual(out["roles"]["t1b"]["label"], "CFO")
        self.assertEqual(out["roles"]["t7"]["label"],  "Chair")
        self.assertEqual(out["roles"]["t2"]["label"],  "Other exec")
        self.assertEqual(out["roles"]["t3"]["label"],  "NED")

    def test_21_per_role_scope_notes_attached(self):
        """Backend plan §4.3: each role cohort carries its own scope_note
        (different sub-line wording per role). Phase B refresh: notes now
        live per-tier in ROLE_SCOPE_NOTES."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", role="CEO",
                    role_class="", fingerprint="fp1", ticker="A"),
            _mk_row("2026-05-01", role="CFO",
                    role_class="", fingerprint="fp1b", ticker="B"),
            _mk_row("2026-05-01", role="Chair",
                    role_class="", fingerprint="fp2", ticker="C"),
            _mk_row("2026-05-01", role="Non-Executive Director",
                    role_class="", fingerprint="fp3", ticker="D"),
        ]
        out = edj.build_role_payload(rows, today, emit_timestamp=False)
        # Per ROLE_SCOPE_NOTES in export_dashboard_json.py.
        self.assertIn("Chief Executive", out["roles"]["t1a"]["scope_note"])
        self.assertIn("Chief Financial", out["roles"]["t1b"]["scope_note"])
        self.assertIn("Chair",           out["roles"]["t7"]["scope_note"])
        self.assertIn("Non-Executive",   out["roles"]["t3"]["scope_note"])
        # All four are distinct.
        notes = {out["roles"][k]["scope_note"]
                 for k in ("t1a", "t1b", "t7", "t3")}
        self.assertEqual(len(notes), 4)

    def test_22_role_payload_horizon_lookback_grid_complete(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01", role="CEO", role_class="",
                        car_t1=0.01, car_t30=0.02, car_t90=0.03, car_t365=0.04)]
        out = edj.build_role_payload(rows, today, emit_timestamp=False)
        cohort = out["roles"]["t1a"]
        for h in ("t1", "t30", "t90", "t365"):
            self.assertIn(h, cohort)
            for lb in ("30d", "90d", "6m", "1y", "all"):
                self.assertIn(lb, cohort[h])

    def test_23_role_payload_uses_tx_lookup_company(self):
        """Rupert Q6: company on each firing row comes from tx_lookup."""
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01", role="CEO", role_class="",
                        fingerprint="fp-aal", ticker="AAL")]
        tx_lookup = {
            "fp-aal": {
                "director": "Duncan Wanblad",
                "company":  "Anglo American Plc",
                "role":     "CEO",
            }
        }
        out = edj.build_role_payload(rows, today, tx_lookup=tx_lookup,
                                     emit_timestamp=False)
        f = out["roles"]["t1a"]["t30"]["all"]["top_firings"][0]
        self.assertEqual(f["company"],  "Anglo American Plc")
        self.assertEqual(f["director"], "Duncan Wanblad")


class TestBuildSectorPayload(unittest.TestCase):
    """build_sector_payload — spec §5.2 sector shape, §3.3 benchmark logic."""

    def test_24_sector_payload_emits_all_sectors_not_sliced(self):
        """Per backend plan §10.7: the JSON emits ALL sectors with at least
        one in-scope firing; the front-end is responsible for slicing to
        top-3 + bottom-2. Server-side slicing would hard-code presentation."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", ticker="AAL", fingerprint="fp1"),
            _mk_row("2026-05-01", ticker="AZN", fingerprint="fp2"),
            _mk_row("2026-05-01", ticker="BP",  fingerprint="fp3"),
            _mk_row("2026-05-01", ticker="VOD", fingerprint="fp4"),
            _mk_row("2026-05-01", ticker="GSK", fingerprint="fp5"),
            _mk_row("2026-05-01", ticker="ULVR", fingerprint="fp6"),
        ]
        tickers_meta = {
            "AAL":  {"sector": "Materials",     "benchmark_symbol": "^FTNMX1770"},
            "AZN":  {"sector": "Health Care",   "benchmark_symbol": "^FTNMX2010"},
            "BP":   {"sector": "Energy",        "benchmark_symbol": "^FTNMX1010"},
            "VOD":  {"sector": "Telecom",       "benchmark_symbol": "^FTNMX5010"},
            "GSK":  {"sector": "Health Care",   "benchmark_symbol": "^FTNMX2010"},
            "ULVR": {"sector": "Consumer",      "benchmark_symbol": "^FTNMX3010"},
        }
        out = edj.build_sector_payload(
            rows, today, tickers_meta, emit_timestamp=False
        )
        # All 5 distinct sectors emitted — no slicing.
        self.assertEqual(
            set(out["sectors"].keys()),
            {"Materials", "Health Care", "Energy", "Telecom", "Consumer"},
        )

    def test_25_sector_payload_attaches_benchmark_symbol_per_cohort(self):
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", ticker="AAL", fingerprint="fp1"),
            _mk_row("2026-05-01", ticker="AZN", fingerprint="fp2"),
        ]
        tickers_meta = {
            "AAL": {"sector": "Materials",   "benchmark_symbol": "^FTNMX1770"},
            "AZN": {"sector": "Health Care", "benchmark_symbol": "^FTNMX2010"},
        }
        out = edj.build_sector_payload(
            rows, today, tickers_meta, emit_timestamp=False
        )
        self.assertEqual(
            out["sectors"]["Materials"]["benchmark_symbol"],
            "^FTNMX1770",
        )
        self.assertEqual(
            out["sectors"]["Health Care"]["benchmark_symbol"],
            "^FTNMX2010",
        )

    def test_26_sector_payload_falls_back_to_ftas_when_benchmark_missing(self):
        """Risk R3 mitigation: sectors with no benchmark_symbol must surface
        the ^FTAS fallback so the FE can disclose it."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", ticker="X", fingerprint="fp1"),
        ]
        tickers_meta = {
            "X": {"sector": "Quirky", "benchmark_symbol": None},
        }
        out = edj.build_sector_payload(
            rows, today, tickers_meta, emit_timestamp=False
        )
        self.assertEqual(
            out["sectors"]["Quirky"]["benchmark_symbol"],
            "^FTAS",
        )

    def test_27_sector_payload_excludes_tickers_without_sector(self):
        """Tickers with no `sector` in tickers_meta are dropped from the
        sector payload (firings on those tickers don't get a cohort)."""
        today = date(2026, 5, 18)
        rows = [
            _mk_row("2026-05-01", ticker="AAL", fingerprint="fp1"),
            _mk_row("2026-05-01", ticker="UNKNOWN", fingerprint="fp2"),
        ]
        tickers_meta = {
            "AAL": {"sector": "Materials", "benchmark_symbol": "^FTNMX1770"},
            # UNKNOWN deliberately absent from tickers_meta.
        }
        out = edj.build_sector_payload(
            rows, today, tickers_meta, emit_timestamp=False
        )
        self.assertEqual(set(out["sectors"].keys()), {"Materials"})
        # The Materials cohort should have only 1 firing — the UNKNOWN row
        # didn't sneak in via another path.
        block = out["sectors"]["Materials"]["t30"]["all"]
        self.assertEqual(block["total_firings"], 1)

    def test_28_sector_payload_top_level_shape(self):
        today = date(2026, 5, 18)
        rows = [_mk_row("2026-05-01", ticker="AAL", fingerprint="fp1")]
        tickers_meta = {
            "AAL": {"sector": "Materials", "benchmark_symbol": "^FTNMX1770"}
        }
        out = edj.build_sector_payload(rows, today, tickers_meta,
                                       emit_timestamp=True)
        self.assertEqual(out["schema_version"], "1.0")
        self.assertIn("generated_at", out)
        self.assertIn("sectors", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
