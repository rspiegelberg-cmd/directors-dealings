"""Tests for B-140 -- Basket Report (export_baskets_json + render_baskets).

Run:
    python -m unittest .scripts/test_baskets.py -v
    # or from repo root:
    python -m unittest discover -s .scripts -p "test_baskets.py" -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from export_baskets_json import (  # noqa: E402
    _compute_basket, load_backtest_csv, export_baskets,
)
import render_baskets  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASKETS_CONFIG_FIXTURE = {
    "baskets": [
        {
            "id": "f1_small",
            "signal_ids": ["f1_first_time_buy"],
            "require_small_cap": True,
            "label": "First-time buyers -- small cap",
            "description": "First buy, small cap",
        },
        {
            "id": "t7_small",
            "signal_ids": ["t7_chair_buy"],
            "require_small_cap": True,
            "label": "Chair buyers -- small cap",
            "description": "Chair buy, small cap",
            "early_data": True,
        },
    ],
    "n_proven_threshold": 30,
    "small_cap_threshold_gbp": 300000000,
    "updated": "2026-06-07",
}

# Minimal rows that mimic _backtest_results.csv fields used by export_baskets_json.
def _make_row(signal_id: str, small_cap: str, net_car_t30: str,
              net_car_t90: str, fired_at: str = "2026-01-15",
              value_gbp: str = "5000", market_cap_gbp: str = "50000000",
              ticker: str = "AAAA") -> dict:
    """Return a CSV-style dict with the _* typed fields already attached."""
    r: dict = {
        "signal_id": signal_id,
        "small_cap": small_cap,
        "net_car_t30": net_car_t30,
        "net_car_t90": net_car_t90,
        "fired_at": fired_at,
        "value_gbp": value_gbp,
        "market_cap_gbp": market_cap_gbp,
        "ticker": ticker,
    }
    # Simulate the typing done by load_backtest_csv
    def _sf(s):
        if s is None or s == "" or s == "None":
            return None
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    r["_net_car_t21"]  = _sf(r["net_car_t30"])
    r["_net_car_t90"]  = _sf(r["net_car_t90"])
    r["_value_gbp"]    = _sf(r["value_gbp"])
    r["_market_cap"]   = _sf(r["market_cap_gbp"])
    r["_small_cap"]    = r["small_cap"].strip()
    r["_fired_at"]     = r["fired_at"][:10]
    return r


# ---------------------------------------------------------------------------
# Tests: _compute_basket
# ---------------------------------------------------------------------------

class TestComputeBasket(unittest.TestCase):

    def _cfg(self, extra=None):
        cfg = {
            "id": "f1_small",
            "signal_ids": ["f1_first_time_buy"],
            "require_small_cap": True,
            "label": "First-time buyers -- small cap",
            "description": "First buy, small cap",
        }
        if extra:
            cfg.update(extra)
        return cfg

    def test_n_count_small_cap_only(self):
        """Only rows with signal match + small_cap=1 are counted."""
        rows = [
            _make_row("f1_first_time_buy", "1", "0.05", "0.10"),
            _make_row("f1_first_time_buy", "0", "0.20", "0.30"),  # large cap -- excluded
            _make_row("t3_ned_buy",        "1", "0.15", "0.20"),  # wrong signal -- excluded
            _make_row("f1_first_time_buy", "1", "0.08", "0.12"),
        ]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        self.assertEqual(result["n"], 2)

    def test_median_car_calculation(self):
        """Median of net_car values should be computed correctly."""
        # net_car values (as fractions): 0.10, 0.20, 0.30 -> median = 0.20 -> 20.0%
        rows = [
            _make_row("f1_first_time_buy", "1", "0.10", "0.10"),
            _make_row("f1_first_time_buy", "1", "0.20", "0.20"),
            _make_row("f1_first_time_buy", "1", "0.30", "0.30"),
        ]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        self.assertAlmostEqual(result["median_net_car_21"], 20.0, places=1)
        self.assertAlmostEqual(result["median_net_car_90"], 20.0, places=1)

    def test_median_skips_none(self):
        """Rows with None net_car_t30 should be skipped in median calc."""
        rows = [
            _make_row("f1_first_time_buy", "1", "0.10", ""),
            _make_row("f1_first_time_buy", "1", "",     "0.20"),
            _make_row("f1_first_time_buy", "1", "0.30", "0.40"),
        ]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        # car21 values: [0.10, 0.30] -> median = 0.20 -> 20.0%
        self.assertAlmostEqual(result["median_net_car_21"], 20.0, places=1)
        # car90 values: [0.20, 0.40] -> median = 0.30 -> 30.0%
        self.assertAlmostEqual(result["median_net_car_90"], 30.0, places=1)

    def test_proven_gate_true(self):
        """n >= threshold should set proven=True."""
        rows = [_make_row("f1_first_time_buy", "1", "0.05", "0.10")
                for _ in range(30)]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        self.assertTrue(result["proven"])

    def test_proven_gate_false(self):
        """n < threshold should set proven=False."""
        rows = [_make_row("f1_first_time_buy", "1", "0.05", "0.10")
                for _ in range(7)]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        self.assertFalse(result["proven"])

    def test_pct_positive(self):
        """% positive should count values > 0."""
        # 2 positive, 1 negative, 1 zero -> 50%
        rows = [
            _make_row("f1_first_time_buy", "1", "0.10", "0.05"),
            _make_row("f1_first_time_buy", "1", "0.08", "0.02"),
            _make_row("f1_first_time_buy", "1", "-0.05", "-0.03"),
            _make_row("f1_first_time_buy", "1", "0.00", "-0.01"),
        ]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        self.assertAlmostEqual(result["pct_positive_21"], 50.0, places=0)

    def test_latest_firings_limit_and_order(self):
        """Latest 10 firings should be sorted by fired_at descending."""
        rows = [
            _make_row("f1_first_time_buy", "1", "0.05", "0.10",
                      fired_at=f"2026-0{i + 1}-01")
            for i in range(9)  # 9 rows
        ]
        result = _compute_basket(self._cfg(), rows, proven_threshold=30)
        self.assertEqual(len(result["latest_firings"]), 9)
        # First firing should be most recent
        dates = [f["fired_at"] for f in result["latest_firings"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_empty_rows(self):
        """Zero matching rows -> n=0, all stats None."""
        result = _compute_basket(self._cfg(), [], proven_threshold=30)
        self.assertEqual(result["n"], 0)
        self.assertFalse(result["proven"])
        self.assertIsNone(result["median_net_car_21"])
        self.assertIsNone(result["median_net_car_90"])
        self.assertIsNone(result["pct_positive_21"])

    def test_early_data_flag_propagated(self):
        """early_data config flag should pass through to output."""
        cfg_with_early = self._cfg({"early_data": True})
        result = _compute_basket(cfg_with_early, [], proven_threshold=30)
        self.assertTrue(result["early_data"])

    def test_output_structure_keys(self):
        """Output dict should have all required keys."""
        result = _compute_basket(self._cfg(), [], proven_threshold=30)
        for key in ("id", "label", "description", "n", "proven", "early_data",
                    "median_net_car_21", "median_net_car_90",
                    "pct_positive_21", "pct_positive_90", "latest_firings"):
            self.assertIn(key, result, f"missing key: {key}")

    def test_signal_id_filter_multi(self):
        """A basket with multiple signal_ids should include all matching rows."""
        cfg = {
            "id": "combo",
            "signal_ids": ["f1_first_time_buy", "t3_ned_buy"],
            "require_small_cap": True,
            "label": "Combo",
            "description": "",
        }
        rows = [
            _make_row("f1_first_time_buy", "1", "0.05", "0.10"),
            _make_row("t3_ned_buy",        "1", "0.07", "0.12"),
            _make_row("t7_chair_buy",      "1", "0.09", "0.14"),  # excluded
        ]
        result = _compute_basket(cfg, rows, proven_threshold=30)
        self.assertEqual(result["n"], 2)


# ---------------------------------------------------------------------------
# Tests: export_baskets (integration -- uses tempfile, no DB)
# ---------------------------------------------------------------------------

class TestExportBaskets(unittest.TestCase):

    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        import csv
        fieldnames = [
            "run_id", "signal_id", "signal_version", "fingerprint", "fired_at",
            "ticker", "role", "role_normalized", "role_class", "value_gbp", "is_aim",
            "market_cap_gbp", "small_cap", "benchmark_symbol",
            "entry_date", "entry_close",
            "t1_close", "t30_close", "t90_close", "t180_close", "t365_close",
            "benchmark_entry", "benchmark_t1", "benchmark_t30",
            "benchmark_t90", "benchmark_t180", "benchmark_t365",
            "raw_return_t1", "raw_return_t30", "raw_return_t90", "raw_return_t180", "raw_return_t365",
            "benchmark_return_t1", "benchmark_return_t30",
            "benchmark_return_t90", "benchmark_return_t180", "benchmark_return_t365",
            "car_t1", "car_t30", "car_t90", "car_t180", "car_t365",
            "cost_bps", "net_car_t1", "net_car_t30", "net_car_t90", "net_car_t180", "net_car_t365",
            "windows_available",
        ]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                # Fill missing fields with empty strings
                out = {k: row.get(k, "") for k in fieldnames}
                writer.writerow(out)

    def test_json_output_structure(self):
        """export_baskets writes valid JSON with expected top-level keys."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            csv_path = tmp_p / "_backtest_results.csv"
            cfg_path = tmp_p / "baskets_config.json"
            out_path = tmp_p / "baskets.json"

            # Write 35 f1_small rows (proven) + 5 t7_small rows (not proven)
            rows = []
            for i in range(35):
                rows.append({
                    "signal_id": "f1_first_time_buy",
                    "small_cap": "1",
                    "net_car_t30": "0.04",
                    "net_car_t90": "0.08",
                    "fired_at": f"2026-0{(i % 9) + 1}-01",
                    "value_gbp": "5000",
                    "market_cap_gbp": "100000000",
                    "ticker": f"T{i:03d}",
                })
            for i in range(5):
                rows.append({
                    "signal_id": "t7_chair_buy",
                    "small_cap": "1",
                    "net_car_t30": "0.06",
                    "net_car_t90": "0.12",
                    "fired_at": f"2026-0{(i % 9) + 1}-01",
                    "value_gbp": "10000",
                    "market_cap_gbp": "80000000",
                    "ticker": f"C{i:03d}",
                })
            self._write_csv(csv_path, rows)
            cfg_path.write_text(
                json.dumps(BASKETS_CONFIG_FIXTURE), encoding="utf-8"
            )

            result = export_baskets(
                csv_path=csv_path, config_path=cfg_path, out_path=out_path
            )

            # Check the written file
            self.assertTrue(out_path.exists())
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("generated_at", payload)
            self.assertIn("baskets", payload)
            self.assertIn("proven_threshold", payload)
            self.assertEqual(payload["proven_threshold"], 30)

            # f1_small should be proven; t7_small should not
            by_id = {b["id"]: b for b in payload["baskets"]}
            self.assertTrue(by_id["f1_small"]["proven"])
            self.assertEqual(by_id["f1_small"]["n"], 35)
            self.assertFalse(by_id["t7_small"]["proven"])
            self.assertEqual(by_id["t7_small"]["n"], 5)

    def test_sorted_by_car90_descending(self):
        """Baskets should be ranked by median_net_car_90 descending in output."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            csv_path = tmp_p / "_backtest_results.csv"
            cfg_path = tmp_p / "baskets_config.json"
            out_path = tmp_p / "baskets.json"

            rows = []
            # f1 has car90=0.04, t7 has car90=0.12 -> t7 should rank first
            for _ in range(35):
                rows.append({
                    "signal_id": "f1_first_time_buy",
                    "small_cap": "1",
                    "net_car_t30": "0.02",
                    "net_car_t90": "0.04",
                    "fired_at": "2026-01-10",
                    "value_gbp": "5000",
                    "market_cap_gbp": "100000000",
                    "ticker": "AAA",
                })
            for _ in range(35):
                rows.append({
                    "signal_id": "t7_chair_buy",
                    "small_cap": "1",
                    "net_car_t30": "0.06",
                    "net_car_t90": "0.12",
                    "fired_at": "2026-01-15",
                    "value_gbp": "10000",
                    "market_cap_gbp": "80000000",
                    "ticker": "BBB",
                })
            self._write_csv(csv_path, rows)
            cfg_path.write_text(json.dumps(BASKETS_CONFIG_FIXTURE), encoding="utf-8")

            export_baskets(csv_path=csv_path, config_path=cfg_path, out_path=out_path)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            ids = [b["id"] for b in payload["baskets"]]
            # t7 has higher car90; should appear first
            self.assertEqual(ids[0], "t7_small")


# ---------------------------------------------------------------------------
# Tests: render_baskets
# ---------------------------------------------------------------------------

class TestRenderBaskets(unittest.TestCase):

    def _make_baskets_json(self, baskets: list[dict]) -> dict:
        return {
            "generated_at": "2026-06-07T10:00:00Z",
            "proven_threshold": 30,
            "baskets": baskets,
        }

    def test_html_contains_basket_labels(self):
        """Rendered page should contain all basket labels."""
        data = self._make_baskets_json([
            {
                "id": "f1_small",
                "label": "First-time buyers -- small cap",
                "description": "First buy, small cap",
                "n": 165,
                "proven": True,
                "early_data": False,
                "median_net_car_30": 3.5,
                "median_net_car_90": 7.2,
                "pct_positive_30": 58.0,
                "pct_positive_90": 62.0,
                "latest_firings": [],
            },
            {
                "id": "t7_small",
                "label": "Chair buyers -- small cap",
                "description": "Chair buy, small cap",
                "n": 7,
                "proven": False,
                "early_data": True,
                "median_net_car_30": 1.2,
                "median_net_car_90": 2.8,
                "pct_positive_30": 57.0,
                "pct_positive_90": 57.0,
                "latest_firings": [],
            },
        ])
        html_out = render_baskets.render_baskets_page(data, build_sha="test")
        self.assertIn("First-time buyers", html_out)
        self.assertIn("Chair buyers", html_out)

    def test_car_values_in_html(self):
        """Positive and negative CAR values should appear in the HTML."""
        # B-154: render_baskets reads median_net_car_21 (renamed from _30 in B-151).
        data = self._make_baskets_json([
            {
                "id": "f1_small",
                "label": "Test basket",
                "description": "",
                "n": 50,
                "proven": True,
                "early_data": False,
                "median_net_car_21": 4.75,
                "median_net_car_90": -1.20,
                "pct_positive_21": 55.0,
                "pct_positive_90": 45.0,
                "latest_firings": [],
            },
        ])
        html_out = render_baskets.render_baskets_page(data, build_sha="test")
        self.assertIn("4.75%", html_out)
        self.assertIn("-1.20%", html_out)

    def test_early_data_warning_for_t7(self):
        """t7_small (early_data=True, n<30) should show the early data warning."""
        data = self._make_baskets_json([
            {
                "id": "t7_small",
                "label": "Chair buyers -- small cap",
                "description": "Chair buy, small cap",
                "n": 7,
                "proven": False,
                "early_data": True,
                "median_net_car_30": 1.2,
                "median_net_car_90": 2.8,
                "pct_positive_30": 57.0,
                "pct_positive_90": 57.0,
                "latest_firings": [],
            },
        ])
        html_out = render_baskets.render_baskets_page(data, build_sha="test")
        self.assertIn("Insufficient data", html_out)

    def test_proven_basket_no_early_warning(self):
        """A proven basket (n>=30) should NOT show the early data warning."""
        data = self._make_baskets_json([
            {
                "id": "f1_small",
                "label": "First-time buyers",
                "description": "",
                "n": 165,
                "proven": True,
                "early_data": False,
                "median_net_car_30": 3.5,
                "median_net_car_90": 7.2,
                "pct_positive_30": 58.0,
                "pct_positive_90": 62.0,
                "latest_firings": [],
            },
        ])
        html_out = render_baskets.render_baskets_page(data, build_sha="test")
        self.assertNotIn("Insufficient data", html_out)

    def test_nav_contains_baskets_link(self):
        """Rendered page nav should include a Baskets link."""
        data = self._make_baskets_json([])
        html_out = render_baskets.render_baskets_page(data, build_sha="test")
        self.assertIn("baskets.html", html_out)

    def test_latest_firings_tickers_appear(self):
        """Ticker symbols in latest_firings should appear in the HTML."""
        data = self._make_baskets_json([
            {
                "id": "f1_small",
                "label": "First-time buyers",
                "description": "",
                "n": 50,
                "proven": True,
                "early_data": False,
                "median_net_car_30": 3.5,
                "median_net_car_90": 7.2,
                "pct_positive_30": 58.0,
                "pct_positive_90": 62.0,
                "latest_firings": [
                    {
                        "ticker": "DNLM",
                        "fired_at": "2026-05-01",
                        "value_gbp": 25000,
                        "net_car_30": 4.5,
                        "net_car_90": 8.1,
                        "market_cap_gbp": 120000000,
                    },
                ],
            },
        ])
        html_out = render_baskets.render_baskets_page(data, build_sha="test")
        self.assertIn("DNLM", html_out)

    def test_render_to_file_creates_file(self):
        """render_to_file should create baskets.html on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            baskets_json = tmp_p / "baskets.json"
            out_html = tmp_p / "baskets.html"

            baskets_json.write_text(json.dumps(self._make_baskets_json([])),
                                    encoding="utf-8")
            n = render_baskets.render_to_file(
                baskets_json_path=baskets_json,
                out_path=out_html,
                build_sha="test",
            )
            self.assertTrue(out_html.exists())
            self.assertGreater(n, 0)
            content = out_html.read_text(encoding="utf-8")
            self.assertIn("Basket Report", content)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
