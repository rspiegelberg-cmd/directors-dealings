"""render_health_panel.py -- Render the date-integrity panel for index.html.

Reads `.data/_date_audit_report.json` (written by `audit_dates.py`) and
returns a self-contained HTML block.

Layout (locked):

  [GREEN BANNER when overall=PASS]
  +---------------------------------------------------------------+
  |  [tick] Data quality OK  -  2,630 transactions, 412 signals    |
  |        Last checked: 14:02 UTC                                 |
  +---------------------------------------------------------------+

  [RED BANNER when overall=FAIL]
  +---------------------------------------------------------------+
  |  [cross] Data quality issues found  -  2,630 transactions       |
  |                                                                |
  |  [PASS] Date format: YYYY-MM-DD          2,630 / 2,630          |
  |  [PASS] No future-dated transactions      0 future-dated         |
  |  [FAIL] Tx date <= announced_at + 7d      3 anomalies            |
  |  [PASS] Tx date >= announced_at - 3y     2,627 / 2,627          |
  |  [PASS] Signal rows have valid dates       412 / 412             |
  |                                                                |
  |  [view detail] Full row-level report ->                          |
  +---------------------------------------------------------------+

The renderer is stdlib-only and produces Tailwind-styled HTML matching
the rest of the dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import render_helpers as h


def _fmt_dt(s: str) -> str:
    """Render an ISO UTC timestamp as 'HH:MM UTC' or a fallback."""
    if not s:
        return "-"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%H:%M UTC")
    except Exception:
        return h.esc(s[:16])


def load_report(report_path: Path) -> dict | None:
    """Load the audit report. Returns None on missing/unreadable file."""
    if not report_path or not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _row_line(key: str, summary_row: dict) -> str:
    pass_ = summary_row.get("pass", False)
    name = summary_row.get("name") or key
    ok = summary_row.get("ok") or 0
    bad = summary_row.get("bad") or 0
    total = ok + bad
    if pass_:
        badge = (
            '<span class="inline-block px-2 py-0.5 text-xs font-semibold '
            'bg-emerald-100 text-emerald-700 rounded">PASS</span>'
        )
        count = f'<span class="text-slate-500 tabular-nums">{ok:,} / {total:,}</span>'
    else:
        badge = (
            '<span class="inline-block px-2 py-0.5 text-xs font-semibold '
            'bg-rose-100 text-rose-700 rounded">FAIL</span>'
        )
        count = (
            f'<span class="text-rose-700 font-semibold tabular-nums">'
            f'{bad:,} anomalies</span>'
        )
    return (
        f'<div class="flex items-center justify-between py-1 text-sm">'
        f'<div class="flex items-center gap-2">'
        f'<span class="font-mono text-xs text-slate-400">{key}</span>'
        f'{badge}'
        f'<span class="text-slate-700">{h.esc(name)}</span>'
        f'</div>'
        f'{count}'
        f'</div>'
    )


def render_panel(report: dict | None) -> str:
    """Return a Tailwind-styled HTML block for index.html."""
    if report is None:
        # Audit hasn't run yet -- show a neutral grey banner so the user
        # knows the check exists but isn't current.
        return (
            '<div class="mb-4 p-3 rounded-lg border border-slate-200 '
            'bg-slate-50 text-slate-600 text-sm">'
            '<strong class="font-semibold">Data quality:</strong> '
            'audit has not run yet. Run '
            '<code class="px-1 bg-slate-200 rounded">python .scripts/audit_dates.py --verbose</code> '
            'to populate this panel.'
            '</div>'
        )

    overall = report.get("overall", "FAIL")
    total = report.get("total_transactions", 0)
    sigs = report.get("signals_rows", 0)
    generated_at = report.get("generated_at", "")
    summary = report.get("summary") or {}

    if overall == "PASS":
        return (
            '<div class="mb-4 p-3 rounded-lg border border-emerald-200 '
            'bg-emerald-50 flex items-center justify-between text-sm">'
            '<div class="flex items-center gap-3">'
            '<span class="inline-block w-3 h-3 rounded-full bg-emerald-500"></span>'
            f'<strong class="text-emerald-800 font-semibold">Data quality OK</strong>'
            f'<span class="text-emerald-700">'
            f'  {total:,} transactions, {sigs:,} signal rows all passed integrity checks.'
            f'</span>'
            '</div>'
            f'<span class="text-emerald-600 text-xs">Checked {_fmt_dt(generated_at)}</span>'
            '</div>'
        )

    # FAIL path: expanded panel with per-invariant breakdown.
    rows_html = ""
    for key in ("I1", "I2", "I3", "I4", "I5"):
        if key in summary:
            rows_html += _row_line(key, summary[key])

    return (
        '<div class="mb-4 rounded-lg border border-rose-300 bg-rose-50">'
        '<div class="px-4 py-3 border-b border-rose-200 flex items-center justify-between">'
        '<div class="flex items-center gap-3">'
        '<span class="inline-block w-3 h-3 rounded-full bg-rose-600"></span>'
        '<strong class="text-rose-900 font-semibold">Data quality issues detected</strong>'
        f'<span class="text-rose-800 text-sm">'
        f' &mdash; {total:,} transactions audited.'
        f' Review below before trusting the figures.'
        f'</span>'
        '</div>'
        f'<span class="text-rose-700 text-xs">Checked {_fmt_dt(generated_at)}</span>'
        '</div>'
        f'<div class="px-4 py-2 space-y-0.5">{rows_html}</div>'
        '<div class="px-4 py-2 border-t border-rose-200 text-xs text-rose-800">'
        'Row-level detail: <code class="px-1 bg-rose-100 rounded">.data/_date_audit_report.json</code>'
        '</div>'
        '</div>'
    )


def render_panel_from_path(report_path: Path) -> str:
    """Convenience: load the JSON and render in one call. Safe on missing file."""
    return render_panel(load_report(report_path))
