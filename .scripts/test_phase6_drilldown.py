"""Sprint 14 Phase 6 (B-070) tests -- the cohort drill-down modal.

The Chart.js canvas + DOM interaction are not headless-testable, so these
tests pin the testable contract:

  1. window.__cohortData now carries the `drilldown` blob (grp -> month_iso ->
     {verdict, signals[]}) alongside the existing groups/order threading.
  2. The modal HTML scaffold exists (backdrop + dialog + table mount + close
     affordances) and is hidden via inline style.display (NOT the hidden+flex
     trap from Phase 4).
  3. onCohortClick is no longer a no-op and routes to openCohortDrilldown.
  4. The table renderer defines the 9 columns incl. "Share of cohort movement"
     (and NOT the old "Contribution to mean"), defaults to share-desc sort, and
     headers are clickable to re-sort.
  5. Close on X / backdrop / Esc.

Spec: docs/specs/cohort-performance-chart-design-spec.md (State D + the
"Drill-down modal backdrop and panel" snippet + the 2026-05-29 metric ruling).

Run under:
    python -m unittest .scripts.test_phase6_drilldown -v
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_performance as rp  # noqa: E402
from dashboard import render_helpers as h        # noqa: E402


# --- Fixtures --------------------------------------------------------------

def _sig(ticker, director, role, fire, t1, t30, t90, bench, net, weight):
    return {
        "fingerprint": ticker + "00",
        "ticker": ticker, "director": director, "role_short": role,
        "fire_date": fire,
        "car_t1": t1, "car_t30": t30, "car_t90": t90,
        "benchmark_t30": bench, "net_car_t30": net, "cohort_weight": weight,
        "signal_ids": [1, 2],   # must NOT be threaded
    }


def _mk_cohort_data():
    groups = {
        "t3": {
            "label": "T3 NED buy", "color_hex": "#10b981",
            "months": [
                {"month_iso": "2025-06", "n_signals": 3, "mean_car_t30": 0.01,
                 "min_car_t30": -0.08, "max_car_t30": 0.12,
                 "hit_rate_t30": 0.5, "hit_rate_t30_rolling_6m": 0.42,
                 "single_ticker_weight": 0.6, "ma3_mean_car_t30": None},
            ],
        },
    }
    drilldown = {
        "t3": {
            "2025-09": {
                "verdict": ("1 ticker (TIN) was the largest single drag on the "
                            "cohort (78% of total movement, -19.5% net CAR)."),
                "signals": [
                    _sig("TIN", "Smith, J", "NED", "2025-09-11",
                         0.021, -0.184, 0.943, 0.011, -0.195, 0.78),
                    _sig("EOG", "Patel, R", "NED", "2025-09-05",
                         -0.004, 0.121, 1.666, 0.011, 0.112, 0.15),
                    _sig("SAGA", "Wright, K", "NED", "2025-09-29",
                         0.010, 0.064, 0.943, 0.009, 0.055, 0.07),
                ],
            },
            "2025-10": {"verdict": "", "signals": [
                _sig("ABC", "Doe, A", "NED", "2025-10-01",
                     0.0, 0.02, 0.05, 0.01, 0.01, 1.0),
            ]},
        },
    }
    return {"signal_groups": list(groups.keys()), "groups": groups,
            "cohort_drilldown": drilldown}


def _mk_signals():
    sigs = {
        sid: {"trades": 30, "mean_car": 1.0, "median_car": 0.9,
              "hit_pct": 50.0, "sparkline": [0.1, 0.2, 0.3]}
        for sid in h.SIGNAL_DISPLAY_ORDER
    }
    return {
        "horizon_aggregates": {
            "t30": {"base_rate": 50.0, "signals": sigs},
            "t90": {"base_rate": 55.0, "signals": sigs},
        },
        "cohorts": {"by_value_bucket": {}, "by_sector": []},
        "cohorts_v2": {},
        "pending_diagnostics": {"total": 0, "categories": []},
    }


def _extract_cohort_blob(html_or_script):
    m = re.search(r"window\.__cohortData\s*=\s*(\{.*?\});", html_or_script,
                  re.DOTALL)
    assert m, "window.__cohortData assignment not found"
    return json.loads(m.group(1))


# --- Drill-down threading ---------------------------------------------------

class TestDrilldownThreading(unittest.TestCase):

    def setUp(self):
        cd = _mk_cohort_data()
        self.script = rp._cohort_client_data(cd["groups"],
                                             cd["cohort_drilldown"])
        self.blob = _extract_cohort_blob(self.script)

    def test_01_blob_carries_drilldown(self):
        self.assertIn("drilldown", self.blob)
        self.assertIn("t3", self.blob["drilldown"])
        self.assertIn("2025-09", self.blob["drilldown"]["t3"])

    def test_02_drilldown_entry_has_verdict_and_signals(self):
        entry = self.blob["drilldown"]["t3"]["2025-09"]
        self.assertIn("verdict", entry)
        self.assertIn("drag", entry["verdict"])
        self.assertEqual(len(entry["signals"]), 3)

    def test_03_signal_rows_carry_display_columns(self):
        s = self.blob["drilldown"]["t3"]["2025-09"]["signals"][0]
        for k in ("ticker", "director", "role_short", "fire_date",
                  "car_t1", "car_t30", "car_t90", "benchmark_t30",
                  "net_car_t30", "cohort_weight"):
            self.assertIn(k, s)

    def test_04_signal_ids_not_threaded(self):
        for s in self.blob["drilldown"]["t3"]["2025-09"]["signals"]:
            self.assertNotIn("signal_ids", s)
            self.assertNotIn("fingerprint", s)

    def test_05_groups_and_order_still_present(self):
        # Additive -- must not disturb Phase 4 threading.
        self.assertIn("groups", self.blob)
        self.assertIn("order", self.blob)
        self.assertIn("t3", self.blob["groups"])

    def test_06_empty_drilldown_yields_empty_object(self):
        blob = _extract_cohort_blob(
            rp._cohort_client_data(_mk_cohort_data()["groups"], None))
        self.assertEqual(blob["drilldown"], {})

    def test_07_threading_is_pure_ascii(self):
        self.script.encode("ascii")


# --- Modal HTML scaffold ----------------------------------------------------

class TestModalScaffold(unittest.TestCase):

    def setUp(self):
        self.modal = rp._cohort_drilldown_modal()

    def test_08_modal_container_present(self):
        self.assertIn('id="cohort-drilldown"', self.modal)
        self.assertIn("fixed inset-0 z-50", self.modal)

    def test_09_backdrop_with_close_on_click(self):
        self.assertIn("bg-slate-900/40", self.modal)
        self.assertIn("backdrop-blur-sm", self.modal)
        self.assertIn("data-close-on-click", self.modal)

    def test_10_dialog_with_aria(self):
        self.assertIn('role="dialog"', self.modal)
        self.assertIn('aria-modal="true"', self.modal)

    def test_11_header_elements_present(self):
        self.assertIn('id="drillTitle"', self.modal)
        self.assertIn('id="drillSummary"', self.modal)
        self.assertIn('id="drillVerdict"', self.modal)

    def test_12_table_mount_and_close_button(self):
        self.assertIn('id="drillTable"', self.modal)
        self.assertIn("data-close-modal", self.modal)

    def test_13_hidden_via_style_display_not_flex_trap(self):
        # MUST hide via inline style.display, NOT a bare `hidden` class on a
        # flex container (the Phase-4 bug). The wrapper carries display:none
        # inline and no `hidden`/`flex` display utility on the toggle target.
        self.assertIn('style="display:none"', self.modal)
        # The toggle target (#cohort-drilldown) must not rely on a `hidden`
        # class that a `flex` utility would defeat.
        self.assertNotIn('id="cohort-drilldown" class="', self.modal)

    def test_14_modal_is_pure_ascii(self):
        self.modal.encode("ascii")


# --- Modal script: click routing, table, sort, close ------------------------

class TestModalScript(unittest.TestCase):

    def setUp(self):
        self.script = rp._cohort_drilldown_script()
        self.level2 = rp._cohort_level2_script()

    def test_15_exposes_open_entry_point(self):
        self.assertIn("window.openCohortDrilldown", self.script)

    def test_16_onclick_routes_to_open(self):
        # Phase 4's no-op stub is gone; onCohortClick now calls the opener.
        self.assertIn("window.openCohortDrilldown", self.level2)
        # gap (null) months do not open a modal
        self.assertIn("_monthIso == null", self.level2)
        # the means dataset point carries the raw month key
        self.assertIn("_monthIso: m.month_iso", self.level2)

    def test_17_nine_columns_including_share(self):
        # B-126 relabelled the CAR/benchmark/net columns (maths unchanged).
        for label in ("Ticker", "Director", "Fire date",
                      "Excess vs benchmark (T+1)", "Excess vs benchmark (T+30)",
                      "Excess vs benchmark (T+90)", "Benchmark return (already removed)",
                      "Excess, after costs", "Share of cohort movement"):
            self.assertIn(label, self.script)

    def test_18_no_contribution_to_mean_column(self):
        self.assertNotIn("Contribution to mean", self.script)

    def test_19_default_sort_is_share_desc(self):
        self.assertIn("sortKey: 'cohort_weight'", self.script)
        self.assertIn("sortDir: 'desc'", self.script)
        # the opener resets to share-desc each open
        self.assertIn("state.sortKey = 'cohort_weight'", self.script)
        self.assertIn("state.sortDir = 'desc'", self.script)

    def test_20_headers_clickable_to_resort(self):
        self.assertIn("data-sort-key", self.script)
        self.assertIn("th[data-sort-key]", self.script)
        # asc/desc toggle
        self.assertIn("'asc'", self.script)
        self.assertIn("'desc'", self.script)

    def test_21_reads_drilldown_from_blob(self):
        self.assertIn("window.__cohortData", self.script)
        self.assertIn(".drilldown", self.script)
        self.assertIn(".signals", self.script)

    def test_22_show_hide_via_style_display(self):
        self.assertIn("modal.style.display = 'block'", self.script)
        self.assertIn("modal.style.display = 'none'", self.script)

    def test_23_close_affordances_x_backdrop_esc(self):
        self.assertIn("[data-close-modal]", self.script)
        self.assertIn("[data-close-on-click]", self.script)
        self.assertIn("e.key === 'Escape'", self.script)

    def test_24_signed_pct_and_share_pct_formatting(self):
        # signed % for CAR/benchmark/net; bounded % for share.
        self.assertIn("signedPct", self.script)
        self.assertIn("sharePct", self.script)

    def test_25_verdict_empty_hides_line(self):
        # empty verdict -> the verdict element is hidden, not blank-but-visible.
        self.assertIn("verdictEl.style.display = 'none'", self.script)

    def test_26_script_is_pure_ascii(self):
        self.script.encode("ascii")


# --- Full render integration ------------------------------------------------

class TestFullRenderIntegration(unittest.TestCase):

    def setUp(self):
        self.html = rp.render(_mk_signals(), {}, build_sha="test",
                              cohort_data=_mk_cohort_data())

    def test_27_page_carries_modal_and_drilldown_blob(self):
        self.assertIn('id="cohort-drilldown"', self.html)
        self.assertIn("window.openCohortDrilldown", self.html)
        blob = _extract_cohort_blob(self.html)
        self.assertIn("drilldown", blob)
        self.assertIn("2025-09", blob["drilldown"]["t3"])

    def test_28_renders_without_drilldown(self):
        # cohort_data with no cohort_drilldown -> empty drilldown, page builds.
        cd = {"groups": _mk_cohort_data()["groups"]}
        html = rp.render(_mk_signals(), {}, build_sha="test", cohort_data=cd)
        self.assertIn('id="cohort-drilldown"', html)
        blob = _extract_cohort_blob(html)
        self.assertEqual(blob["drilldown"], {})

    def test_29_full_page_new_blocks_ascii(self):
        # Our injected blocks must be ASCII-clean (cp1252 print rule); the
        # whole page may carry HTML entities elsewhere.
        rp._cohort_drilldown_modal().encode("ascii")
        rp._cohort_drilldown_script().encode("ascii")
        blob = _extract_cohort_blob(self.html)
        json.dumps(blob).encode("ascii")


if __name__ == "__main__":
    unittest.main(verbosity=2)
