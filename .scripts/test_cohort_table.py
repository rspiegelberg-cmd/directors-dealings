"""Unit tests for `build_cohort_table` (Sprint 1 — Performance page redesign v1).

Tests cover the deterministic, pure-function contract of the shared cohort-table
helper. Eight cases per the Sprint 1 plan's acceptance target.

Anchored to:
- Backend plan §4.1 (algorithm pseudocode)
- Spec §5.1 (output shape)
- Rupert Q2 (2026-05-18): outlier_flag = True when any firing has |CAR| > 2.0
  (fraction, not percent)

Run under:
    python .scripts/test_cohort_table.py
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import statistics
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import export_dashboard_json as edj  # noqa: E402


def _mk(fired_at: str, signal_id: str, car_t30: float | None,
        car_t90: float | None = None, value_gbp: float | None = None) -> dict:
    """Minimal row shape that the cohort helper reads.

    Mirrors what `load_backtest_csv` produces for the underscore-prefixed
    fields plus enough of the raw fields for a `scope_filter_fn`.
    """
    return {
        "_fired_at":   fired_at,
        "_car_t1":     None,
        "_car_t30":    car_t30,
        "_car_t90":    car_t90,
        "_car_t365":   None,
        "_value_gbp":  value_gbp,
        "signal_id":   signal_id,
    }


# A simple, deterministic group_fn / label_fn pair used in most tests.
def _group_by_signal(row: dict):
    sid = row.get("signal_id")
    if sid in ("t1_ceo_cfo_buy", "t2_exec_buy"):
        return sid
    return None  # drop everything else from the cohort


def _label_passthrough(key: str) -> str:
    return key.upper()


class TestBuildCohortTableShape(unittest.TestCase):
    """The output shape contract — exact keys at every level."""

    def test_01_top_level_keys_are_horizons(self):
        today = date(2026, 5, 18)
        rows = [_mk("2026-05-01", "t1_ceo_cfo_buy", 0.05)]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t1", "t30", "t90", "t365"],
            lookbacks=edj.LOOKBACKS,
            today=today,
        )
        self.assertEqual(set(out.keys()), {"t1", "t30", "t90", "t365"})

    def test_02_inner_keys_are_lookback_labels(self):
        today = date(2026, 5, 18)
        rows = [_mk("2026-05-01", "t1_ceo_cfo_buy", 0.05)]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=edj.LOOKBACKS,
            today=today,
        )
        self.assertEqual(
            set(out["t30"].keys()),
            {"30d", "90d", "6m", "1y", "all"},
        )

    def test_03_bucket_row_fields_are_correct(self):
        today = date(2026, 5, 18)
        rows = [
            _mk("2026-05-01", "t1_ceo_cfo_buy", 0.10),
            _mk("2026-05-01", "t1_ceo_cfo_buy", -0.05),
            _mk("2026-05-01", "t1_ceo_cfo_buy", 0.02),
        ]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        bucket = out["t30"]["all"]
        self.assertEqual(bucket["total_n"], 3)
        self.assertEqual(len(bucket["rows"]), 1)
        row = bucket["rows"][0]
        # vw_mean_car is additive (B-078) -- check required keys are present
        required = {"key", "label", "n", "hit_pct", "median_car"}
        self.assertTrue(
            required.issubset(set(row.keys())),
            "Missing keys: " + str(required - set(row.keys()))
        )
        self.assertEqual(row["key"], "t1_ceo_cfo_buy")
        self.assertEqual(row["label"], "T1_CEO_CFO_BUY")
        self.assertEqual(row["n"], 3)
        # 2 of 3 values are positive → hit_pct = 66.7 (round 1dp).
        self.assertEqual(row["hit_pct"], 66.7)
        # median(0.10, -0.05, 0.02) = 0.02 → median_car = 2.0
        self.assertEqual(row["median_car"], 2.0)


class TestBuildCohortTableFiltering(unittest.TestCase):
    """Lookback windowing, scope filtering, and group_fn=None drop behaviour."""

    def test_04_scope_filter_excludes_rows(self):
        """scope_filter_fn runs BEFORE grouping — rows it rejects must not
        affect the cohort, including their CAR distribution."""
        today = date(2026, 5, 18)
        rows = [
            _mk("2026-05-01", "t1_ceo_cfo_buy", 0.10),
            # This T3 row would distort median_car if it leaked through.
            _mk("2026-05-01", "t3_ned_buy",     -0.50),
        ]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=lambda r: r["signal_id"],
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
            scope_filter_fn=lambda r: r["signal_id"] in (
                "t1_ceo_cfo_buy", "t2_exec_buy"
            ),
        )
        bucket = out["t30"]["all"]
        self.assertEqual(bucket["total_n"], 1)
        self.assertEqual(len(bucket["rows"]), 1)
        self.assertEqual(bucket["rows"][0]["key"], "t1_ceo_cfo_buy")

    def test_05_group_fn_returning_none_drops_row(self):
        """Rows where group_fn(row) is None must be excluded from the cohort
        — this is how `classify_role`'s None catch-all is omitted."""
        today = date(2026, 5, 18)
        rows = [
            _mk("2026-05-01", "t1_ceo_cfo_buy", 0.10),  # included
            _mk("2026-05-01", "f1_first_time_buy", -0.30),  # excluded (group_fn -> None)
        ]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=_group_by_signal,   # returns None for f1
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        bucket = out["t30"]["all"]
        self.assertEqual(bucket["total_n"], 1)
        self.assertEqual(len(bucket["rows"]), 1)
        self.assertEqual(bucket["rows"][0]["key"], "t1_ceo_cfo_buy")

    def test_06_lookback_window_excludes_old_firings(self):
        """A firing 100 days ago is inside `1y` and `all` but outside `90d`."""
        today = date(2026, 5, 18)
        rows = [
            _mk("2026-02-07", "t1_ceo_cfo_buy", 0.05),  # ~100 days back
            _mk("2026-05-01", "t1_ceo_cfo_buy", 0.10),  # ~17 days back
        ]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=edj.LOOKBACKS,
            today=today,
        )
        self.assertEqual(out["t30"]["90d"]["total_n"], 1)
        self.assertEqual(out["t30"]["1y"]["total_n"], 2)
        self.assertEqual(out["t30"]["all"]["total_n"], 2)


class TestBuildCohortTableEdgeCases(unittest.TestCase):
    """Outlier flag, empty input, idempotency."""

    def test_07_outlier_flag_emitted_when_any_firing_exceeds_threshold(self):
        """Rupert Q2: outlier_flag=True if any firing in the bucket has
        |CAR| > 2.0 (i.e. > 200%)."""
        today = date(2026, 5, 18)
        rows = [
            _mk("2026-05-01", "t1_ceo_cfo_buy", 0.05),
            _mk("2026-05-01", "t1_ceo_cfo_buy", 2.5),   # |CAR| > 200% → trips flag
        ]
        out = edj.build_cohort_table(
            rows=rows,
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        row = out["t30"]["all"]["rows"][0]
        self.assertTrue(row.get("outlier_flag"))

        # A clean bucket (no firing exceeds 2.0) must NOT carry the flag.
        out_clean = edj.build_cohort_table(
            rows=[_mk("2026-05-01", "t1_ceo_cfo_buy", 0.10)],
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t30"],
            lookbacks=[("all", None)],
            today=today,
        )
        self.assertNotIn("outlier_flag", out_clean["t30"]["all"]["rows"][0])

    def test_08_empty_input_returns_empty_cohorts_no_crash(self):
        """Idempotency + empty-input safety — same inputs always give same
        output, and no rows means empty `rows` arrays everywhere."""
        today = date(2026, 5, 18)
        out_a = edj.build_cohort_table(
            rows=[],
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t1", "t30", "t90", "t365"],
            lookbacks=edj.LOOKBACKS,
            today=today,
        )
        # Every horizon × lookback combination must exist and be empty.
        for h in ("t1", "t30", "t90", "t365"):
            for lb in ("90d", "6m", "1y", "all"):
                self.assertEqual(out_a[h][lb]["total_n"], 0)
                self.assertEqual(out_a[h][lb]["rows"], [])

        # Idempotency: a second identical call returns equal output.
        out_b = edj.build_cohort_table(
            rows=[],
            group_fn=_group_by_signal,
            label_fn=_label_passthrough,
            horizons=["t1", "t30", "t90", "t365"],
            lookbacks=edj.LOOKBACKS,
            today=today,
        )
        self.assertEqual(out_a, out_b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
