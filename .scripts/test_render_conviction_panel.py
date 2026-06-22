"""Tests for the B-171 Conviction Score dashboard panel (render side, no DB).

Exercises render_helpers band/factor helpers and render_index._conviction_card
against synthetic payloads. No SQLite, no network — pure string assembly, per
the CLAUDE.md Zone-A rules.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from dashboard import render_helpers as h  # noqa: E402
from dashboard import render_index  # noqa: E402


def _pick(**over):
    base = {
        "score": 72.0,
        "band": "High",
        "rank": 1,
        "date": "2026-06-15",
        "ticker": "ABC",
        "company": "Alpha Beta plc",
        "director": "Jane Director",
        "role": "Chief Executive Officer",
        "value_gbp": 2_500_000.0,
        "sector_multiplier": 1.0,
        "sector_caution": False,
        "inputs_missing": [],
        "factors": [
            {"id": "f1_who", "label": "Who", "value": 1.0, "unknown": False},
            {"id": "f2_buy_size", "label": "Buy size", "value": 0.8,
             "unknown": False},
            {"id": "f3_company_size", "label": "Company size", "value": 1.0,
             "unknown": False},
            {"id": "f4_earnings_timing", "label": "Earnings timing",
             "value": None, "unknown": True},
            {"id": "f5_past_performance", "label": "Past performance",
             "value": 0.5, "unknown": False},
        ],
    }
    base.update(over)
    return base


class TestBandBadge(unittest.TestCase):
    def test_band_colours(self):
        self.assertIn("emerald", h.conviction_band_badge("High", 72))
        self.assertIn("slate", h.conviction_band_badge("Low", 12))
        self.assertIn("amber", h.conviction_band_badge("Moderate", 50))
        self.assertIn("bg-emerald-600", h.conviction_band_badge("Exceptional", 90))

    def test_score_shown_with_band(self):
        out = h.conviction_band_badge("High", 72.4)
        self.assertIn("High", out)
        self.assertIn("72", out)  # rounded score visible

    def test_unknown_band_falls_back(self):
        out = h.conviction_band_badge("", None)
        self.assertIn("slate", out)


class TestFactorBar(unittest.TestCase):
    def test_known_value_renders_width(self):
        out = h.conviction_factor_bar("Who", 1.0)
        self.assertIn("100%", out)
        self.assertIn("Who", out)

    def test_unknown_value_shows_unknown_not_zero(self):
        out = h.conviction_factor_bar("Company size", None, unknown=True)
        self.assertIn("unknown", out)
        self.assertNotIn("0%", out)  # never a misleading 0


class TestConvictionRow(unittest.TestCase):
    def test_row_has_core_fields(self):
        out = render_index._conviction_row(_pick())
        self.assertTrue(out.startswith("<tr"))   # table row, not a card
        self.assertIn("ABC", out)
        self.assertIn("Alpha Beta", out)
        self.assertIn("Jane Director", out)
        self.assertIn("&pound;2.5m", out)
        self.assertIn("High", out)         # band label
        self.assertIn("72", out)           # score

    def test_unknown_factor_renders_unknown(self):
        out = render_index._conviction_row(_pick())
        # Earnings timing is unknown in the fixture.
        self.assertIn("unknown", out)

    def test_sector_caution_flag_shown_when_discounted(self):
        out = render_index._conviction_row(
            _pick(sector_multiplier=0.7, sector_caution=True))
        self.assertIn("hot sector", out)

    def test_no_caution_when_calm(self):
        out = render_index._conviction_row(_pick())
        self.assertNotIn("hot sector", out)

    def test_missing_value_em_dash(self):
        out = render_index._conviction_row(_pick(value_gbp=None))
        self.assertIn("&mdash;", out)


class TestPanelRender(unittest.TestCase):
    def _signals(self, top10):
        return {
            "active_clusters": [],
            "conviction_top10": top10,
            "conviction_window_days": 28,
            "conviction_window_start": "2026-05-21",
            "conviction_window_end": "2026-06-18",
            "companies_index": [],
        }

    def _dealings(self):
        return {"as_of_date": "2026-06-18", "today": [], "this_week": [],
                "generated_at": "2026-06-18T00:00:00Z"}

    def test_panel_present_with_picks(self):
        html_out = render_index.render(
            self._signals([_pick()]), self._dealings())
        self.assertIn("Strongest director buys", html_out)
        self.assertIn("last 4 weeks", html_out)
        self.assertIn("2026-05-21", html_out)  # window start in label
        self.assertIn("ABC", html_out)

    def test_renders_up_to_ten_table_rows(self):
        # The renderer must render every pick it is given as a clickable <tr>
        # in the conviction table (up to 10 supplied by the exporter).
        picks = [_pick(rank=i + 1, ticker=f"T{i}") for i in range(10)]
        html_out = render_index.render(self._signals(picks), self._dealings())
        # Each conviction row links to its company page; 10 distinct tickers.
        for i in range(10):
            self.assertIn(f"companies/T{i}.html", html_out)

    def test_empty_state_when_no_buys(self):
        html_out = render_index.render(self._signals([]), self._dealings())
        self.assertIn("Strongest director buys", html_out)
        self.assertIn("No director buys recorded in the last 4 weeks", html_out)


if __name__ == "__main__":
    unittest.main()
