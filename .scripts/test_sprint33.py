"""Sprint 33 tests — B-107, B-108, B-109, B-110.

B-108 and B-109 were found to be already shipped. Tests confirm the shipped
behaviour. B-107 and B-110 are new additions.

Run:
    python -m unittest .scripts.test_sprint33 -v
"""

import unittest
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# B-110 — signal_live_chip
# ---------------------------------------------------------------------------

class TestSignalLiveChip(unittest.TestCase):
    """B-110: live / ageing / closed chip boundary tests.

    Acceptance criteria: state mapping correct at boundary ages 0 / 89 / 90 / 200 d.
    """

    def _chip(self, age_days: int) -> str:
        from dashboard.render_helpers import signal_live_chip
        today = date(2026, 6, 5)
        fired = today - timedelta(days=age_days)
        return signal_live_chip(fired.isoformat(), today.isoformat())

    def test_age_0_is_live(self):
        html = self._chip(0)
        self.assertIn("live", html)
        self.assertIn("emerald", html)

    def test_age_30_is_live(self):
        html = self._chip(30)
        self.assertIn("live", html)
        self.assertIn("emerald", html)

    def test_age_31_is_ageing(self):
        html = self._chip(31)
        self.assertIn("ageing", html)
        self.assertIn("amber", html)

    def test_age_89_is_ageing(self):
        html = self._chip(89)
        self.assertIn("ageing", html)
        self.assertIn("amber", html)

    def test_age_90_is_closed(self):
        html = self._chip(90)
        self.assertIn("closed", html)
        self.assertIn("slate", html)

    def test_age_200_is_closed(self):
        html = self._chip(200)
        self.assertIn("closed", html)
        self.assertIn("slate", html)

    def test_invalid_date_returns_empty(self):
        from dashboard.render_helpers import signal_live_chip
        self.assertEqual(signal_live_chip(""), "")
        self.assertEqual(signal_live_chip("not-a-date", "2026-06-05"), "")

    def test_today_str_optional(self):
        """Called with no today_str — should not raise."""
        from dashboard.render_helpers import signal_live_chip
        result = signal_live_chip("2026-05-01")
        # Returns a non-empty chip string (exact label depends on run date).
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# B-107 — _matured_abs helper
# ---------------------------------------------------------------------------

class TestMaturedAbs(unittest.TestCase):
    """B-107: _matured_abs returns gross stock returns = car + bench."""

    def _call(self, rows, horizon="t30"):
        from export_dashboard_json import _matured_abs
        return _matured_abs(rows, horizon)

    def test_basic(self):
        rows = [{"_car_t30": 0.05, "_bench_t30": 0.02}]
        result = self._call(rows)
        self.assertAlmostEqual(result[0], 0.07, places=9)

    def test_sector_down_positive_car_negative_abs(self):
        """High CAR can come with negative Abs if sector also fell."""
        rows = [{"_car_t30": 0.10, "_bench_t30": -0.15}]
        result = self._call(rows)
        self.assertAlmostEqual(result[0], -0.05, places=9)

    def test_excludes_none_car(self):
        rows = [
            {"_car_t30": None, "_bench_t30": 0.01},
            {"_car_t30": 0.03, "_bench_t30": 0.01},
        ]
        result = self._call(rows)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0], 0.04, places=9)

    def test_excludes_none_bench(self):
        rows = [
            {"_car_t30": 0.02, "_bench_t30": None},
            {"_car_t30": 0.03, "_bench_t30": 0.01},
        ]
        result = self._call(rows)
        self.assertEqual(len(result), 1)

    def test_empty_rows(self):
        self.assertEqual(self._call([]), [])

    def test_respects_horizon(self):
        rows = [{"_car_t1": 0.01, "_bench_t1": 0.005,
                 "_car_t30": 0.05, "_bench_t30": 0.02}]
        r_t1 = self._call(rows, "t1")
        r_t30 = self._call(rows, "t30")
        self.assertAlmostEqual(r_t1[0], 0.015, places=9)
        self.assertAlmostEqual(r_t30[0], 0.07, places=9)


# ---------------------------------------------------------------------------
# B-107 — aggregate_signals includes mean_abs_return
# ---------------------------------------------------------------------------

class TestAggregateSignalsMeanAbs(unittest.TestCase):
    """B-107: aggregate_signals emits mean_abs_return in per_signal dict."""

    def _build_row(self, signal_id, car_t30, bench_t30,
                   fired_at="2026-01-01"):
        return {
            # B-117: aggregate_signals keys input rows by the LONG signal_id
            # (from SIGNAL_ORDER); callers pass the short "t1a", so map it or the
            # rows are silently skipped and mean_abs_return comes back None.
            # Output per_signal is still keyed by the short id, so reads use "t1a".
            "signal_id":   {"t1a": "t1a_ceo_founder_buy"}.get(signal_id, signal_id),
            "_fired_at":   fired_at,
            "_car_t1":     car_t30 * 0.3,
            "_bench_t1":   bench_t30 * 0.3,
            "_car_t30":    car_t30,
            "_bench_t30":  bench_t30,
            "_car_t90":    car_t30 * 2,
            "_bench_t90":  bench_t30 * 2,
            "_car_t365":   car_t30 * 3,
            "_bench_t365": bench_t30 * 3,
            "_net_car_t1":   car_t30 * 0.3 - 0.005,
            "_net_car_t30":  car_t30 - 0.005,
            "_net_car_t90":  car_t30 * 2 - 0.005,
            "_net_car_t365": car_t30 * 3 - 0.005,
            "benchmark_return_t30": bench_t30,
            "benchmark_return_t1":  bench_t30 * 0.3,
            "benchmark_return_t90": bench_t30 * 2,
            "benchmark_return_t365": bench_t30 * 3,
        }

    def test_mean_abs_return_present(self):
        from export_dashboard_json import aggregate_signals
        rows = [self._build_row("t1a", 0.10, 0.02)]
        result = aggregate_signals(rows, date(2026, 6, 5), lookback_days=365)
        t30 = result["t30"]
        t1a = t30["signals"].get("t1a")
        self.assertIsNotNone(t1a, "t1a signal missing from t30 aggregates")
        self.assertIn("mean_abs_return", t1a)

    def test_mean_abs_return_value_correct(self):
        """mean_abs_return = mean of (car + bench) across matured rows."""
        from export_dashboard_json import aggregate_signals
        # Two firings with known car+bench sums.
        rows = [
            self._build_row("t1a", 0.10, 0.02),  # abs = 0.12
            self._build_row("t1a", 0.04, 0.01),  # abs = 0.05
        ]
        result = aggregate_signals(rows, date(2026, 6, 5), lookback_days=365)
        t30 = result["t30"]
        t1a = t30["signals"].get("t1a")
        self.assertIsNotNone(t1a)
        # mean = (12 + 5) / 2 = 8.5 (reported as % so * 100)
        self.assertAlmostEqual(t1a["mean_abs_return"], 8.5, places=1)

    def test_mean_abs_return_none_when_no_bench(self):
        """When bench is None, mean_abs_return is None (not crash)."""
        from export_dashboard_json import aggregate_signals
        row = self._build_row("t1a", 0.10, 0.02)
        row["_bench_t30"] = None  # strip bench
        result = aggregate_signals([row], date(2026, 6, 5), lookback_days=365)
        t30 = result["t30"]
        t1a = t30["signals"].get("t1a")
        self.assertIsNone(t1a["mean_abs_return"])


# ---------------------------------------------------------------------------
# B-108 — render_index page structure
# ---------------------------------------------------------------------------

class TestB108PageStructure(unittest.TestCase):
    """B-108: page has no separate Today block; Signals-today tile present."""

    def _get_source(self):
        import importlib, sys, types
        # Minimal render: import render_index and check its module-level
        # structure rather than full render (avoids needing live JSON).
        import ast, pathlib
        src = pathlib.Path(
            __file__
        ).parent / "dashboard" / "render_index.py"
        return src.read_text(encoding="utf-8")

    def test_no_sort_today_function(self):
        """_sort_today must not exist — B-108 removed the Today-only sort."""
        src = self._get_source()
        self.assertNotIn("def _sort_today", src,
                         "_sort_today still present — B-108 not complete")

    def test_title_contains_this_week(self):
        """<title> must mention 'This Week'."""
        src = self._get_source()
        self.assertIn("This Week", src,
                      "Page title/heading does not mention 'This Week'")

    def test_signals_today_tile_present(self):
        """Signals-today count tile must be retained."""
        src = self._get_source()
        self.assertIn("signals_today", src,
                      "Signals-today tile reference missing")


# ---------------------------------------------------------------------------
# B-109 — hit_rate_panel wired in render()
# ---------------------------------------------------------------------------

class TestB109HitRatePanelWired(unittest.TestCase):
    """B-109: _hit_rate_panel() is called in render_performance.render()."""

    def test_hit_rate_panel_called_in_render(self):
        import pathlib, ast
        src = pathlib.Path(
            __file__
        ).parent / "dashboard" / "render_performance.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        render_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "render":
                render_fn = node
                break
        self.assertIsNotNone(render_fn, "render() function not found")
        calls = [
            n.func.id
            for n in ast.walk(render_fn)
            if isinstance(n, ast.Call)
            and isinstance(getattr(n, "func", None), ast.Name)
        ]
        self.assertIn(
            "_hit_rate_panel", calls,
            "_hit_rate_panel() is not called from render()",
        )


# ---------------------------------------------------------------------------
# B-107 — scoreboard HTML includes Abs Rtn header
# ---------------------------------------------------------------------------

class TestB107ScoreboardHeader(unittest.TestCase):
    """B-107: scoreboard table has an Abs Rtn column header."""

    def test_abs_rtn_header_present(self):
        import pathlib
        src = pathlib.Path(
            __file__
        ).parent / "dashboard" / "render_performance.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("Abs Rtn", text,
                      "Abs Rtn column header not found in render_performance")

    def test_mean_abs_return_in_row_builder(self):
        """_row_for_signal must read mean_abs_return from row_data."""
        import pathlib
        src = pathlib.Path(
            __file__
        ).parent / "dashboard" / "render_performance.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("mean_abs_return", text,
                      "mean_abs_return not referenced in render_performance")

    def test_js_updates_abs_rtn_on_horizon_change(self):
        """rebuildScoreboard JS must reference mean_abs_return."""
        import pathlib
        src = pathlib.Path(
            __file__
        ).parent / "dashboard" / "render_performance.py"
        text = src.read_text(encoding="utf-8")
        # Both the Python render layer and the JS rebuild layer.
        self.assertIn("d.mean_abs_return", text,
                      "JS rebuildScoreboard does not reference mean_abs_return")


if __name__ == "__main__":
    unittest.main()
