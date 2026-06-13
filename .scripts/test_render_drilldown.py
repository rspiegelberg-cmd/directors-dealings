"""FE Sprint 2 tests — drill-down page renderer + per-cohort-key output.

Pinned to spec §2 (shared structure), §2.2 (status pill ±50% rule),
§2.3 (top/bottom + edge-case note), §2.4 (rollup ordering), §2.5
(keyboard accessibility), §2.6 (breadcrumb), §3 (per-page variants).

Tests use synthetic payload fixtures matching the §5.2 drill-down JSON
shape so the renderer is exercised without touching live JSON.

Run under:
    python .scripts/test_render_drilldown.py
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_performance_drilldown as rpd  # noqa: E402
from dashboard import render_helpers as h                   # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _mk_firing(date="2026-05-14", ticker="AAL", company="Anglo American Plc",
               director="Duncan Wanblad", role_class="T1",
               signal_tier="t1", value_gbp=312_000, car=8.2):
    return {
        "date": date, "ticker": ticker, "company": company,
        "director": director, "role": "Chief Executive Officer",
        "role_class": role_class, "signal_tier": signal_tier,
        "value_gbp": value_gbp, "car": car,
    }


def _mk_rollup_row(ticker="AAL", company="Anglo American Plc",
                   n=3, hit_pct=66.7, mean_car=5.1,
                   latest_fire="2026-05-14"):
    return {
        "ticker": ticker, "company": company, "n": n,
        "hit_pct": hit_pct, "mean_car": mean_car,
        "latest_fire": latest_fire,
    }


def _mk_drill_block(total_firings=15, distinct_tickers=10,
                     hit_pct=55.0, median_car=1.2,
                     benchmark_car_pct=1.1, top_firings=None,
                     bottom_firings=None, rollup=None,
                     tickers_with_n3=4):
    if top_firings is None:
        top_firings = [_mk_firing()]
    if bottom_firings is None:
        bottom_firings = [_mk_firing(car=-3.5, ticker="BT.A",
                                     director="A. Kirkby")]
    if rollup is None:
        rollup = [_mk_rollup_row()]
    return {
        "benchmark_car_pct": benchmark_car_pct,
        "total_firings": total_firings,
        "distinct_tickers": distinct_tickers,
        "tickers_with_n3": tickers_with_n3,
        "hit_pct": hit_pct,
        "median_car": median_car,
        "top_firings": top_firings,
        "bottom_firings": bottom_firings,
        "rollup": rollup,
    }


def _mk_cohort_data(label="£100–500k", scope_note="T1 + T2 buys only",
                    drill_block=None, benchmark_symbol=None):
    cohort = {"label": label, "scope_note": scope_note}
    if benchmark_symbol:
        cohort["benchmark_symbol"] = benchmark_symbol
    block = drill_block if drill_block is not None else _mk_drill_block()
    for hor in ("t1", "t30", "t90", "t365"):
        cohort[hor] = {lb: block for lb in ("30d", "90d", "6m", "1y", "all")}
    return cohort


def _mk_payload(cohort_type="bucket", cohort_key="100k-500k",
                cohort_data=None, generated_at="2026-05-19T10:00:00Z"):
    if cohort_data is None:
        cohort_data = _mk_cohort_data()
    container = {"bucket": "buckets", "role": "roles",
                 "sector": "sectors"}[cohort_type]
    return {
        "generated_at": generated_at,
        "schema_version": "1.0",
        container: {cohort_key: cohort_data},
    }


# ---------------------------------------------------------------------------
# Status-pill helper tests (spec §2.2)
# ---------------------------------------------------------------------------

class TestStatusPillForCohort(unittest.TestCase):

    def test_01_strong_cohort_renders_green_pill(self):
        # hit_pct >= base_rate * 1.5 → green pill
        html = h.status_pill_for_cohort(hit_pct=80.0, base_rate=50.0,
                                        cohort_type="bucket")
        self.assertIn("bg-emerald-100", html)
        self.assertIn("Top bucket", html)

    def test_02_weak_cohort_renders_red_pill(self):
        # hit_pct <= base_rate * 0.5 → red pill
        html = h.status_pill_for_cohort(hit_pct=20.0, base_rate=50.0,
                                        cohort_type="sector")
        self.assertIn("bg-rose-100", html)
        self.assertIn("Bottom sector this period", html)

    def test_03_middle_cohort_renders_nothing(self):
        # hit_pct in the boring middle → no pill
        html = h.status_pill_for_cohort(hit_pct=50.0, base_rate=50.0,
                                        cohort_type="role")
        self.assertEqual(html, "")

    def test_04_per_cohort_copy_differs(self):
        b = h.status_pill_for_cohort(80.0, 50.0, "bucket")
        r = h.status_pill_for_cohort(80.0, 50.0, "role")
        s = h.status_pill_for_cohort(80.0, 50.0, "sector")
        self.assertIn("Top bucket", b)
        self.assertIn("Strong role cohort", r)
        self.assertIn("Top sector this period", s)


# ---------------------------------------------------------------------------
# Drill-page shape tests (spec §2.1)
# ---------------------------------------------------------------------------

class TestDrillPageShape(unittest.TestCase):

    def test_05_bucket_page_breadcrumb_leaf(self):
        payload = _mk_payload("bucket", "100k-500k",
                              _mk_cohort_data(label="£100–500k"))
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Breadcrumb leaf per spec §2.6.
        self.assertIn("Transaction size: £100–500k", html)
        # Header has both Today and Performance links.
        self.assertIn('href="index.html"', html)
        self.assertIn('href="performance.html"', html)

    def test_06_role_page_breadcrumb_leaf(self):
        payload = _mk_payload("role", "ceo_cfo",
                              _mk_cohort_data(label="CEO / CFO"))
        html = rpd.render_drilldown_page("role", "ceo_cfo", payload)
        self.assertIn("Director role: CEO / CFO", html)

    def test_07_sector_page_breadcrumb_leaf(self):
        payload = _mk_payload("sector", "Materials",
                              _mk_cohort_data(label="Materials"))
        html = rpd.render_drilldown_page("sector", "Materials", payload)
        self.assertIn("Materials", html)

    def test_08_stats_line_has_all_fields(self):
        block = _mk_drill_block(total_firings=28, distinct_tickers=21,
                                hit_pct=39.3, median_car=-1.9,
                                benchmark_car_pct=1.1)
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("bucket", "100k-500k", cohort)
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Stats line per spec §5.3 caption format.
        self.assertIn("28 firings", html)
        self.assertIn("21 distinct tickers", html)
        # Hit % shows with 1dp (no plus sign for negative-or-percent values).
        self.assertIn("39.3%", html)

    def test_09_lookback_dropdown_has_four_options(self):
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Lookback dropdown (id="drillLookback") with 4 options.
        self.assertIn('id="drillLookback"', html)
        for v in ('value="30d"', 'value="90d"', 'value="6m"', 'value="1y"', 'value="all"'):
            self.assertIn(v, html)


# ---------------------------------------------------------------------------
# Top / bottom firings panels (spec §2.3)
# ---------------------------------------------------------------------------

class TestFiringsPanels(unittest.TestCase):

    def test_10_top_panel_renders_emerald_header_and_winners_label(self):
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        self.assertIn("border-emerald-200", html)
        self.assertIn("Top 10 firings", html)
        self.assertIn("winners", html)

    def test_11_bottom_panel_renders_rose_header_and_losers_label(self):
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        self.assertIn("border-rose-200", html)
        self.assertIn("Bottom 10 firings", html)
        self.assertIn("losers", html)

    def test_12_firing_row_carries_keyboard_accessibility_attrs(self):
        """Spec §2.5 — every clickable row needs tabindex / role / aria-label."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        self.assertIn('tabindex="0"', html)
        self.assertIn('role="link"', html)
        self.assertRegex(html, r'aria-label="View [A-Z\.]+ company page"')

    def test_13_fewer_than_10_losers_renders_edge_case_note(self):
        """Spec §2.3 — when fewer than 10 losers, show the explanation note."""
        block = _mk_drill_block(
            total_firings=15,
            bottom_firings=[
                _mk_firing(ticker="BT.A", director="A. Kirkby", car=-3.5),
                _mk_firing(ticker="VOD", director="M. Read", car=-2.1),
            ],
        )
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("bucket", "100k-500k", cohort)
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Edge-case note should appear in the bottom panel.
        self.assertIn("Only 2 of 15 firings", html)
        self.assertIn("negative CAR", html)


# ---------------------------------------------------------------------------
# Rollup table (spec §2.4)
# ---------------------------------------------------------------------------

class TestRollupTable(unittest.TestCase):

    def test_14_rollup_renders_with_ticker_company_n_hitpct_meancar_latest(self):
        block = _mk_drill_block(
            rollup=[_mk_rollup_row(ticker="AAL", company="Anglo American Plc",
                                   n=3, hit_pct=66.7, mean_car=5.1,
                                   latest_fire="2026-05-14")],
        )
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("bucket", "100k-500k", cohort)
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        self.assertIn("All tickers in this bucket", html)
        self.assertIn("Anglo American Plc", html)
        self.assertIn(">AAL</td>", html)

    def test_15_rollup_n_lt_3_below_dashed_divider(self):
        block = _mk_drill_block(
            rollup=[
                _mk_rollup_row(ticker="AAL", n=3, hit_pct=66.7),
                _mk_rollup_row(ticker="XYZ", n=1, hit_pct=100.0),
            ],
        )
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("bucket", "100k-500k", cohort)
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Divider should mention 1 more ticker (the N<3 row).
        self.assertIn("1 more tickers below (N&lt;3)", html)


# ---------------------------------------------------------------------------
# Dead-link smoke test (spec §8)
# ---------------------------------------------------------------------------

class TestDeadLinkHandling(unittest.TestCase):

    def test_16_ticker_without_company_page_renders_italic_not_clickable(self):
        """If a firing's ticker has no companies/{TICKER}.html, the row must
        render italic-faded with a tooltip — NOT a clickable link."""
        block = _mk_drill_block(
            top_firings=[_mk_firing(ticker="DEAD")],
            rollup=[_mk_rollup_row(ticker="DEAD")],
        )
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("bucket", "100k-500k", cohort)
        # Pass an EXPLICITLY empty set — no tickers have company pages.
        html = rpd.render_drilldown_page(
            "bucket", "100k-500k", payload,
            existing_company_pages=set(),
        )
        # Dead-link row should NOT have data-href / tabindex / role=link.
        # It SHOULD have the italic + tooltip markers.
        self.assertNotIn('data-href="companies/DEAD.html"', html)
        self.assertIn("Company page not generated", html)


# ---------------------------------------------------------------------------
# Status pill on the page header (spec §2.2)
# ---------------------------------------------------------------------------

class TestPageHeaderStatusPill(unittest.TestCase):

    def test_17_strong_cohort_page_has_green_pill(self):
        block = _mk_drill_block(hit_pct=80.0)
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("sector", "Materials", cohort)
        # _build_page_header_card uses base_rate_t30=50 (the renderer's default).
        html = rpd.render_drilldown_page("sector", "Materials", payload)
        self.assertIn("Top sector this period", html)
        self.assertIn("bg-emerald-100", html)

    def test_18_middle_cohort_page_has_no_pill(self):
        block = _mk_drill_block(hit_pct=55.0)
        cohort = _mk_cohort_data(drill_block=block)
        payload = _mk_payload("sector", "Materials", cohort)
        html = rpd.render_drilldown_page("sector", "Materials", payload)
        # B-057 (Sprint 8): the client-side renderer JS contains the
        # pill copy as string literals (so it can re-render on dropdown
        # change). Plain `assertNotIn` would false-positive on those.
        # Check the actual server-side pill slot is empty instead.
        m = re.search(
            r'<div id="drillStatusPillSlot">(.*?)</div>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "drillStatusPillSlot must exist")
        slot_html = m.group(1)
        self.assertNotIn("Top sector this period", slot_html)
        self.assertNotIn("Bottom sector this period", slot_html)
        self.assertNotIn("bg-emerald-100", slot_html)
        self.assertNotIn("bg-rose-100", slot_html)


# ---------------------------------------------------------------------------
# Missing-cohort fallback
# ---------------------------------------------------------------------------

class TestMissingCohortFallback(unittest.TestCase):

    def test_19_unknown_cohort_key_renders_not_found_page(self):
        payload = _mk_payload("bucket", "100k-500k")
        # Render with a DIFFERENT key not in the payload.
        html = rpd.render_drilldown_page("bucket", "NONEXISTENT", payload)
        self.assertIn("Cohort not found", html)
        self.assertIn("performance.html", html)


# ---------------------------------------------------------------------------
# Sector benchmark surfacing (spec §3.3 + risk R3)
# ---------------------------------------------------------------------------

class TestSectorBenchmark(unittest.TestCase):

    def test_20_sector_page_includes_resolved_benchmark_symbol(self):
        cohort = _mk_cohort_data(
            label="Materials",
            benchmark_symbol="^FTNMX1770",
        )
        payload = _mk_payload("sector", "Materials", cohort)
        html = rpd.render_drilldown_page("sector", "Materials", payload)
        # The benchmark label should appear in the stats line.
        self.assertIn("^FTNMX1770", html)


# ---------------------------------------------------------------------------
# B-057 (Sprint 8) — Path B client-side rendering tests
#
# The horizon + lookback dropdowns used to reload the whole page on
# change (effectively dead — the build only emits one HTML per cohort
# at the default t30 x 90d). B-057 swaps in a client-side renderer
# that mutates four dynamic regions against the embedded JSON payload
# without reloading. These tests pin the new shape.
# ---------------------------------------------------------------------------

import json as _json


class TestClientSideRenderer(unittest.TestCase):

    def test_21_emits_embedded_drill_data_json_script(self):
        """The page must embed cohort_data as a JSON script tag so the
        client-side renderer can re-render on dropdown change without
        a full page reload."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        self.assertIn('<script type="application/json" id="drillData">', html)
        # Pull the JSON out and parse it — must be valid.
        m = re.search(
            r'<script type="application/json" id="drillData">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "Embedded JSON script tag must exist")
        # The renderer escapes </ as <\/ to keep the JSON safe inside a
        # script tag. Undo for the parse step.
        raw = m.group(1).replace("<\\/", "</")
        data = _json.loads(raw)
        # Spot-check the expected top-level keys.
        for key in ("cohort_type", "cohort_key", "default_horizon",
                    "default_lookback", "base_rate", "blocks",
                    "horizon_labels"):
            self.assertIn(key, data, f"drillData missing {key!r}")
        # All 4 horizons must be present in `blocks`.
        for hor in ("t1", "t30", "t90", "t365"):
            self.assertIn(hor, data["blocks"])

    def test_22_emits_applyDrillView_renderer_function(self):
        """The client script must define applyDrillView so dropdown
        change handlers have something to call."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Either an inline function declaration or window assignment is fine.
        self.assertRegex(html, r"\bapplyDrillView\s*\(")

    def test_23_dropdown_handlers_no_longer_reload_page(self):
        """B-057 inverts the dropdown behaviour: no more URL-reload.
        The old pattern `window.location.search=...` MUST NOT appear in
        any dropdown change handler emitted by the drill renderer."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # The dead pattern from v1 — must not appear anywhere in the
        # emitted HTML for either drillHorizon or drillLookback.
        self.assertNotIn("window.location.search=p.toString()", html)
        self.assertNotIn('window.location.search = p.toString()', html)

    def test_24_url_param_reader_falls_back_to_defaults(self):
        """The URL-param reader must gracefully default when ?horizon
        / ?lookback are missing or invalid. Spec: defaults are
        t30 x 90d, matching the server-side render."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # readUrlParams() should reference both default sources.
        self.assertIn("ctx.default_horizon", html)
        self.assertIn("ctx.default_lookback", html)
        # And the whitelist of valid keys should be in the script so an
        # invalid ?horizon=foo falls back to the default rather than
        # blowing up the renderer.
        self.assertIn("'t30'", html)
        self.assertIn("'90d'", html)

    def test_25_dropdown_change_calls_renderer_not_reload(self):
        """The new change handler must invoke applyDrillView (the
        client renderer). Equivalent to test_23 in reverse: positive
        evidence the right thing replaced the wrong thing."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        # Both selects' change handlers go through the same onChange ->
        # applyDrillView path. Check it's wired up.
        self.assertIn("horizonSel.addEventListener('change'", html)
        self.assertIn("lookbackSel.addEventListener('change'", html)

    def test_26_dynamic_regions_carry_stable_ids(self):
        """The four regions the JS re-renders need stable IDs."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        for region_id in (
            'id="drillStatsLine"',
            'id="drillStatusPillSlot"',
            'id="drillTopBody"',
            'id="drillBottomBody"',
            'id="drillRollupBody"',
        ):
            self.assertIn(region_id, html, f"missing {region_id}")

    def test_27_history_replaceState_is_used_for_url_sync(self):
        """Deep-linking — dropdown change should update the URL
        in-place (not reload). history.replaceState is the right tool;
        confirm it shows up in the emitted JS."""
        payload = _mk_payload()
        html = rpd.render_drilldown_page("bucket", "100k-500k", payload)
        self.assertIn("history.replaceState", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
