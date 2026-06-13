"""FE Sprint 1 tests — three-tile cohort cuts on performance.html.

Pinned to spec §1 (layout), §1.2 (N-band visuals), §1.3 (sector top-3+bottom-2),
§1.5 (Tailwind / click-through markup).

The redesigned page reads from `signals.cohorts_v2` (new shape from backend
Sprint 5). Legacy `_cohort_value_section` / `_cohort_sector_section`
functions remain in the module as dead code for backwards-import safety
and are NOT exercised by these tests.

Run under:
    python .scripts/test_render_performance_v2.py
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

from dashboard import render_performance as rp  # noqa: E402
from dashboard import render_helpers as h        # noqa: E402


def _mk_drill_block(n=25, hit_pct=55.0, median_car=1.2,
                    rows=None, total_n=None, key="100k-500k",
                    label="£100–500k"):
    """One cohorts_v2 drill cell: rows + total_n."""
    if rows is None:
        rows = [{"key": key, "label": label, "n": n,
                 "hit_pct": hit_pct, "median_car": median_car}]
    return {"rows": rows, "total_n": total_n if total_n is not None else sum(r.get("n", 0) for r in rows)}


def _mk_cohorts_v2(by_value_bucket=None, by_role=None, by_sector=None):
    """Build a minimal cohorts_v2 block with the 4-horizon × 4-lookback grid."""
    def _grid(rows):
        cell = {"rows": rows, "total_n": sum(r.get("n", 0) for r in rows)}
        return {
            "t1":   {"30d": cell, "90d": cell, "6m": cell, "1y": cell, "all": cell},
            "t30":  {"30d": cell, "90d": cell, "6m": cell, "1y": cell, "all": cell},
            "t90":  {"30d": cell, "90d": cell, "6m": cell, "1y": cell, "all": cell},
            "t365": {"30d": cell, "90d": cell, "6m": cell, "1y": cell, "all": cell},
        }
    out = {}
    if by_value_bucket is not None:
        out["by_value_bucket"] = _grid(by_value_bucket)
    if by_role is not None:
        out["by_role"] = _grid(by_role)
    if by_sector is not None:
        out["by_sector"] = _grid(by_sector)
    return out


def _mk_signals(cohorts_v2, base_rate=50.0):
    return {
        "horizon_aggregates": {
            "t30": {"base_rate": base_rate, "signals": {}},
        },
        "cohorts": {"by_value_bucket": {}, "by_sector": []},
        "cohorts_v2": cohorts_v2,
        "active_clusters": [],
        "paper_pnl_open": 0.0,
        "paper_trades_open": 0,
        "paper_trades_closed": 0,
        "pending_diagnostics": {"total": 0, "generated_at": "2026-05-19T00:00:00Z",
                                "categories": []},
    }


class TestNBandCell(unittest.TestCase):
    """render_helpers.n_band_cell — spec §1.2 N-band visual rules."""

    def test_01_n_zero_renders_italic_em_dash(self):
        html = h.n_band_cell(0)
        self.assertIn("italic", html)
        self.assertIn("—", html)

    def test_02_n_none_renders_italic_em_dash(self):
        html = h.n_band_cell(None)
        self.assertIn("italic", html)

    def test_03_n_below_20_renders_amber_warning_glyph(self):
        html = h.n_band_cell(12)
        self.assertIn("12", html)
        self.assertIn("text-amber-600", html)
        # The ⚠ glyph (HTML entity).
        self.assertIn("&#9888;", html)

    def test_04_n_at_or_above_20_renders_plain_number(self):
        html = h.n_band_cell(20)
        self.assertEqual(html, "20")
        html2 = h.n_band_cell(212)
        self.assertEqual(html2, "212")


class TestCohortTileShape(unittest.TestCase):
    """_cohort_tile + _cohort_section: the redesigned three-tile section."""

    def test_05_three_tiles_render(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "100k-500k", "label": "£100–500k",
                              "n": 28, "hit_pct": 39.3, "median_car": -1.9}],
            by_role=[{"key": "ceo_cfo", "label": "CEO / CFO",
                      "n": 27, "hit_pct": 52.6, "median_car": 0.1}],
            by_sector=[{"key": "Materials", "label": "Materials",
                        "n": 12, "hit_pct": 100.0, "median_car": 4.1}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        # All three tile section blocks present.
        self.assertIn('data-tile="bucket"', html)
        self.assertIn('data-tile="role"', html)
        self.assertIn('data-tile="sector"', html)
        # Header label present.
        self.assertIn("Cohort cuts", html)
        # Responsive grid layout per spec §1.1.
        self.assertIn("grid-cols-1", html)
        self.assertIn("md:grid-cols-3", html)

    def test_06_lookback_dropdown_present_per_tile_with_four_options(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "100k-500k", "label": "£100–500k",
                              "n": 28, "hit_pct": 39.3, "median_car": -1.9}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        # Each tile has a select.lookback-select with 4 options.
        select_blocks = re.findall(r'<select class="lookback-select[^>]*>.*?</select>',
                                   html, re.DOTALL)
        self.assertEqual(len(select_blocks), 3)
        for sb in select_blocks:
            for opt in ('value="30d"', 'value="90d"', 'value="6m"',
                        'value="1y"', 'value="all"'):
                self.assertIn(opt, sb)
        # Default selection is 90d (B-054 added 30d as opt-in option,
        # revert 2026-05-22 — 30d default broke T+90/T+252 intersection).
        self.assertIn('value="90d" selected', select_blocks[0])

    def test_07_n_band_amber_glyph_renders_for_low_n_bucket(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "1k-25k", "label": "£1–25k",
                              "n": 12, "hit_pct": 41.7, "median_car": -1.2}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        # n=12 should trigger the amber ⚠ glyph.
        self.assertIn("&#9888;", html)
        self.assertIn("text-amber-600", html)

    def test_08_clickable_rows_have_data_href_tabindex_role_aria(self):
        """Spec §2.5 accessibility minima — every clickable row needs all four.

        FE2 patch (2026-05-19): drill_href moved from query-string URLs
        (`performance-role.html?role=ceo_cfo`) to filename-encoded URLs
        (`performance-role-ceo_cfo.html`) because FE2 emits one HTML file
        per cohort key. Sectors with spaces get slugified ("Health Care"
        → "Health-Care"); plain alphanumeric keys pass through unchanged.
        """
        cohorts_v2 = _mk_cohorts_v2(
            by_role=[{"key": "ceo_cfo", "label": "CEO / CFO",
                      "n": 27, "hit_pct": 52.6, "median_car": 0.1}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        # Find the CEO / CFO row — filename-encoded URL after FE2.
        self.assertIn(
            'data-href="performance-role-ceo_cfo.html"',
            html,
        )
        self.assertIn('tabindex="0"', html)
        self.assertIn('role="link"', html)
        self.assertIn('aria-label="View CEO / CFO drill-down"', html)


class TestSectorTopBottomSlice(unittest.TestCase):
    """Spec §1.3 — sector tile shows top 3 + bottom 2 with divider."""

    def test_09_five_or_more_sectors_get_top3_bottom2_slice(self):
        rows = [
            {"key": "A", "label": "A", "n": 25, "hit_pct": 90.0, "median_car": 3.0},
            {"key": "B", "label": "B", "n": 25, "hit_pct": 75.0, "median_car": 2.0},
            {"key": "C", "label": "C", "n": 25, "hit_pct": 60.0, "median_car": 1.0},
            {"key": "D", "label": "D", "n": 25, "hit_pct": 45.0, "median_car": -0.5},
            {"key": "E", "label": "E", "n": 25, "hit_pct": 30.0, "median_car": -2.0},
            {"key": "F", "label": "F", "n": 25, "hit_pct": 10.0, "median_car": -3.5},
        ]
        sliced = rp._sector_slice_top3_bottom2(rows)
        # Length should be 3 + divider + 2 = 6.
        self.assertEqual(len(sliced), 6)
        labels = [r.get("key") for r in sliced if not r.get("divider")]
        # Top 3 by hit% desc, then bottom 2 by hit% asc within the bottom slice.
        self.assertEqual(labels[:3], ["A", "B", "C"])
        self.assertEqual(set(labels[3:]), {"E", "F"})
        # Divider sentinel in the middle.
        self.assertTrue(sliced[3].get("divider"))

    def test_10_fewer_than_five_sectors_no_slice_no_divider(self):
        rows = [
            {"key": "A", "label": "A", "n": 25, "hit_pct": 80.0, "median_car": 2.0},
            {"key": "B", "label": "B", "n": 25, "hit_pct": 40.0, "median_car": -1.0},
        ]
        sliced = rp._sector_slice_top3_bottom2(rows)
        self.assertEqual(len(sliced), 2)
        for r in sliced:
            self.assertFalse(r.get("divider"))


class TestClientDataPayload(unittest.TestCase):
    """`window.__perfData` must carry `cohorts_v2` so the auto-wire script
    can re-render table bodies on lookback / horizon changes."""

    def test_11_cohorts_v2_embedded_in_perfdata_script(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_role=[{"key": "ned", "label": "NED",
                      "n": 212, "hit_pct": 51.9, "median_car": 0.1}],
        )
        html = rp._client_data_payload(_mk_signals(cohorts_v2), {})
        self.assertIn("window.__perfData", html)
        self.assertIn("cohorts_v2", html)
        self.assertIn("by_role", html)


class TestRegressionLegacyCohortsKeyStillTolerated(unittest.TestCase):
    """If cohorts_v2 is absent (e.g., reading a pre-Sprint-5 signals.json),
    the section must render an empty state — not crash."""

    def test_12_missing_cohorts_v2_renders_empty_state_no_crash(self):
        signals = _mk_signals({})           # empty cohorts_v2
        try:
            html = rp._cohort_section(signals)
        except Exception as e:
            self.fail(f"empty cohorts_v2 should not crash: {e!r}")
        # Sections still emit (with empty body).
        self.assertIn('data-tile="bucket"', html)
        # Sprint 7 fix #3 (2026-05-22): empty-state copy now identifies the
        # horizon × lookback combination so the user knows what to change.
        self.assertIn("No firings at", html)
        self.assertIn("try a longer lookback or shorter horizon", html)


class TestMedianCarRendering(unittest.TestCase):
    """Regression for FE Sprint 1 bug (caught in Rupert's visual sanity
    check): median_car=0.4 must render as +0.40%, NOT +0.00%. The cohorts_v2
    JSON emits median_car already in percent-terms (e.g. 0.4 = 0.4%), and
    `h.car_cell` / `h.pct` expect percent inputs. Earlier code divided by
    100, rendering every value an order of magnitude too small."""

    def test_13_small_positive_median_car_renders_correct_percent(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "100k-500k", "label": "£100–500k",
                              "n": 28, "hit_pct": 55.6,
                              "median_car": 0.4}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        # The bucket row's median CAR cell should contain "0.40%".
        self.assertIn("0.40%", html)
        # And must NOT contain a value that looks 100x too small.
        # (The legacy bug rendered "0.00%" for median_car=0.4.)
        # We check that NO median_car cell shows "0.00%" when input is 0.4.
        # The negation check: the "0.40%" string is present (test above)
        # AND the row was emitted — implicit because the bucket key
        # "100k-500k" is in the HTML.
        self.assertIn("100k-500k", html)

    def test_14_large_positive_median_car_renders_emerald_color(self):
        """median_car=6.8 should render as +6.80% in emerald (since
        6.8 > 0.05 threshold per car_color_class)."""
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "500k+", "label": "£500k+",
                              "n": 25, "hit_pct": 60.0,
                              "median_car": 6.8}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        self.assertIn("6.80%", html)
        # Emerald color applied (threshold check uses percent values).
        # The colored span wraps the +6.80% text.
        self.assertRegex(
            html,
            r'class="text-emerald-600">\+6\.80%',
        )

    def test_15_negative_median_car_renders_rose_color(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "100k-500k", "label": "£100–500k",
                              "n": 30, "hit_pct": 30.0,
                              "median_car": -2.5}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        self.assertIn("-2.50%", html)
        self.assertRegex(
            html,
            r'class="text-rose-600">-2\.50%',
        )

    def test_16_zero_median_car_renders_plain(self):
        cohorts_v2 = _mk_cohorts_v2(
            by_value_bucket=[{"key": "100k-500k", "label": "£100–500k",
                              "n": 30, "hit_pct": 50.0,
                              "median_car": 0.0}],
        )
        html = rp._cohort_section(_mk_signals(cohorts_v2))
        # 0.0% formats as "0.00%" via pct() default 2 dp.
        self.assertIn("0.00%", html)
        # No plus sign for non-positive values per pct() implementation.


if __name__ == "__main__":
    unittest.main(verbosity=2)
