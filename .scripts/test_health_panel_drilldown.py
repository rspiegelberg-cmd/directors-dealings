"""Tests for the clickable drill-down + inline fix in render_health_panel.py.

Pure Zone A: render_health_panel takes a plain dict (the audit report) and
returns HTML. No DB, no filesystem. Safe to run in the sandbox.

Run under:
    python .scripts/test_health_panel_drilldown.py
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_health_panel as rhp  # noqa: E402


def _fail_report() -> dict:
    """An audit report with one failing invariant (I4) and one anomaly row."""
    return {
        "generated_at": "2026-06-13T07:36:00Z",
        "total_transactions": 6437,
        "signals_rows": 2675,
        "overall": "FAIL",
        "summary": {
            "I1": {"name": "Date format: YYYY-MM-DD", "pass": True, "ok": 6437, "bad": 0},
            "I2": {"name": "No future-dated transactions", "pass": True, "ok": 6437, "bad": 0},
            "I3": {"name": "Tx date <= announced_at + 7d", "pass": True, "ok": 5493, "bad": 0},
            "I4": {"name": "Tx date >= announced_at - 3y", "pass": False, "ok": 6436, "bad": 1},
            "I5": {"name": "Signal rows have valid dates", "pass": True, "ok": 2675, "bad": 0},
        },
        "anomalies": {
            "I1": [], "I2": [], "I3": [],
            "I4": [{
                "fingerprint": "abcdef0123456789",
                "date": "2019-04-01",
                "announced_at": "2026-04-01",
                "gap_days": -2557,
                "ticker": "TST",
                "director": "Jane <b>Doe</b>",
                "type": "BUY",
                "issue": "date_too_old",
            }],
            "I5": [],
        },
    }


def _pass_report() -> dict:
    r = _fail_report()
    r["overall"] = "PASS"
    r["summary"]["I4"] = {"name": "Tx date >= announced_at - 3y",
                          "pass": True, "ok": 6437, "bad": 0}
    r["anomalies"]["I4"] = []
    return r


class TestDrilldown(unittest.TestCase):
    def test_fail_panel_lists_anomaly_row(self):
        html = rhp.render_panel(_fail_report())
        # The offending row's identifying data is surfaced
        self.assertIn("TST", html)
        self.assertIn("2019-04-01", html)   # bad tx date
        self.assertIn("2026-04-01", html)   # filing date
        self.assertIn("7.0 yrs before filing", html)  # the "why"

    def test_fail_row_is_expandable_with_fix_controls(self):
        html = rhp.render_panel(_fail_report())
        self.assertIn("<details", html)            # click-to-expand
        self.assertIn("ddqStageFix", html)         # inline stage button
        self.assertIn("ddqApplyFixes", html)       # apply button
        self.assertIn("api/stage-edit", html)      # reuses existing endpoint
        self.assertIn("api/apply-edits", html)
        self.assertIn('href="review#fp=abcdef0123456789"', html)  # deep link

    def test_fix_input_prefilled_with_filing_date(self):
        html = rhp.render_panel(_fail_report())
        # The date <input> is pre-loaded with announced_at, the sane anchor.
        self.assertIn('value="2026-04-01"', html)

    def test_director_html_is_escaped(self):
        html = rhp.render_panel(_fail_report())
        self.assertNotIn("<b>Doe</b>", html)
        self.assertIn("&lt;b&gt;Doe&lt;/b&gt;", html)

    def test_passing_invariants_are_not_expandable(self):
        html = rhp.render_panel(_fail_report())
        # I1 passes — it should appear as a plain PASS line, not a <details>.
        # Exactly one <details> block (the single failing invariant I4).
        self.assertEqual(html.count("<details"), 1)

    def test_all_pass_uses_green_banner_unchanged(self):
        html = rhp.render_panel(_pass_report())
        self.assertIn("Data quality OK", html)
        self.assertNotIn("<details", html)
        self.assertNotIn("ddqStageFix", html)

    def test_missing_report_neutral_banner(self):
        html = rhp.render_panel(None)
        self.assertIn("audit has not run yet", html)

    def test_singular_anomaly_grammar(self):
        html = rhp.render_panel(_fail_report())
        self.assertIn("1 anomaly", html)
        self.assertNotIn("1 anomalies", html)

    def test_fix_buttons_disabled_until_server_detected(self):
        html = rhp.render_panel(_fail_report())
        # Stage-fix button ships disabled and tagged for the detector to enable.
        self.assertIn("ddq-fix-btn", html)
        self.assertRegex(html, r'ddqStageFix[^>]*disabled|disabled[^>]*ddq-fix-btn')
        # Apply button also disabled at render time.
        self.assertRegex(html, r'id="ddq-apply-btn"[^>]*disabled')
        # Detector pings /api/status and there is a note element to update.
        self.assertIn("ddqDetectServer", html)
        self.assertIn("api/status", html)
        self.assertIn('id="ddq-server-note"', html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
