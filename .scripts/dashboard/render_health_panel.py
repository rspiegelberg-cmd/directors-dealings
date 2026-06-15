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


def _pass_row_line(key: str, summary_row: dict) -> str:
    """A non-clickable PASS line (no anomalies to drill into)."""
    name = summary_row.get("name") or key
    ok = summary_row.get("ok") or 0
    total = ok + (summary_row.get("bad") or 0)
    badge = (
        '<span class="inline-block px-2 py-0.5 text-xs font-semibold '
        'bg-emerald-100 text-emerald-700 rounded">PASS</span>'
    )
    count = f'<span class="text-slate-500 tabular-nums">{ok:,} / {total:,}</span>'
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


def _gap_label(gap_days) -> str:
    """Human-friendly description of how far the tx date is from the filing."""
    try:
        g = int(gap_days)
    except (TypeError, ValueError):
        return ""
    yrs = abs(g) / 365.0
    direction = "before filing" if g < 0 else "after filing"
    if abs(g) >= 365:
        return f"{yrs:.1f} yrs {direction}"
    return f"{abs(g)} days {direction}"


def _anomaly_table(key: str, rows: list) -> str:
    """Build the inline table of bad rows for one failing invariant.

    Each row shows the offending data, an editable date field pre-loaded
    with the filing date (the usual correct value when a tx date was
    mis-parsed too far in the past), and actions that hand off to the
    existing server.py edit pipeline. Pure display — no DB writes here.
    """
    body = ""
    for r in rows:
        fp = h.esc(str(r.get("fingerprint") or ""))
        ticker = h.esc(str(r.get("ticker") or "—"))
        director = h.esc(str(r.get("director") or "—"))
        ttype = h.esc(str(r.get("type") or "—"))
        tx_date = h.esc(str(r.get("date") or "—"))
        ann = str(r.get("announced_at") or "")
        ann_disp = h.esc(ann[:10]) if ann else "—"
        gap = _gap_label(r.get("gap_days"))
        inp_id = f"ddq-date-{fp}"
        # Pre-fill the correction with the filing date (announced_at) when we
        # have one — that is the sane anchor for a "date too old" parser error.
        prefill = h.esc(ann[:10]) if ann else tx_date
        body += (
            '<tr class="border-t border-rose-100 align-top">'
            f'<td class="py-2 pr-3 font-semibold text-slate-800">{ticker}</td>'
            f'<td class="py-2 pr-3 text-slate-600">{director}</td>'
            f'<td class="py-2 pr-3 text-slate-600">{ttype}</td>'
            f'<td class="py-2 pr-3 tabular-nums text-rose-700 font-semibold">{tx_date}'
            f'<div class="text-[11px] font-normal text-rose-500">{h.esc(gap)}</div></td>'
            f'<td class="py-2 pr-3 tabular-nums text-slate-600">{ann_disp}</td>'
            '<td class="py-2 pr-3 whitespace-nowrap">'
            f'<input id="{inp_id}" type="date" value="{prefill}" '
            'class="border border-slate-300 rounded px-1.5 py-0.5 text-sm tabular-nums" />'
            f'<button type="button" onclick="ddqStageFix(\'{fp}\',\'{inp_id}\')" '
            'disabled class="ddq-fix-btn ml-1 px-2 py-0.5 text-xs font-semibold rounded '
            'bg-indigo-600 text-white hover:bg-indigo-700 '
            'disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-indigo-600">'
            'Stage fix</button>'
            f'<a href="review#fp={fp}" '
            'class="ml-1 text-xs text-indigo-600 hover:underline">Open in Review →</a>'
            f'<div id="ddq-msg-{fp}" class="text-[11px] mt-0.5"></div>'
            '</td>'
            '</tr>'
        )
    return (
        '<div class="overflow-x-auto mt-1 mb-2 rounded border border-rose-100 bg-white">'
        '<table class="w-full text-sm">'
        '<thead><tr class="text-left text-xs uppercase tracking-wide '
        'text-slate-400 bg-slate-50">'
        '<th class="py-1.5 px-3 font-medium">Ticker</th>'
        '<th class="py-1.5 px-3 font-medium">Director</th>'
        '<th class="py-1.5 px-3 font-medium">Type</th>'
        '<th class="py-1.5 px-3 font-medium">Tx date</th>'
        '<th class="py-1.5 px-3 font-medium">Filing date</th>'
        '<th class="py-1.5 px-3 font-medium">Correct &amp; fix</th>'
        '</tr></thead>'
        f'<tbody>{body}</tbody>'
        '</table></div>'
    )


def _fail_row_block(key: str, summary_row: dict, anomalies: dict) -> str:
    """A clickable FAIL line that expands to show + fix its anomaly rows."""
    name = summary_row.get("name") or key
    bad = summary_row.get("bad") or 0
    rows = (anomalies or {}).get(key) or []
    badge = (
        '<span class="inline-block px-2 py-0.5 text-xs font-semibold '
        'bg-rose-100 text-rose-700 rounded">FAIL</span>'
    )
    count = (
        f'<span class="text-rose-700 font-semibold tabular-nums">'
        f'{bad:,} {"anomaly" if bad == 1 else "anomalies"}</span>'
    )
    summary_line = (
        '<summary class="flex items-center justify-between py-1 text-sm '
        'cursor-pointer list-none hover:bg-rose-100/50 rounded px-1 -mx-1">'
        '<div class="flex items-center gap-2">'
        '<span class="text-rose-400 text-xs ddq-caret">▶</span>'
        f'<span class="font-mono text-xs text-slate-400">{key}</span>'
        f'{badge}'
        f'<span class="text-slate-700">{h.esc(name)}</span>'
        '<span class="text-[11px] text-indigo-600">— click to review &amp; fix</span>'
        '</div>'
        f'{count}'
        '</summary>'
    )
    capped = ""
    if len(rows) < bad:
        capped = (
            f'<div class="text-[11px] text-rose-500 px-1 pb-1">Showing first '
            f'{len(rows):,} of {bad:,}. Fix these, re-run the audit, repeat for the rest.</div>'
        )
    return (
        '<details class="ddq-detail border border-rose-100 rounded mb-1">'
        f'<div class="px-1">{summary_line}</div>'
        f'<div class="px-2 pb-2">{_anomaly_table(key, rows)}{capped}</div>'
        '</details>'
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

    # FAIL path: expanded panel with per-invariant breakdown. PASS lines stay
    # static; FAIL lines become click-to-expand blocks with inline fix controls.
    anomalies = report.get("anomalies") or {}
    rows_html = ""
    for key in ("I1", "I2", "I3", "I4", "I5"):
        if key not in summary:
            continue
        if summary[key].get("pass", False):
            rows_html += _pass_row_line(key, summary[key])
        else:
            rows_html += _fail_row_block(key, summary[key], anomalies)

    return (
        '<div class="mb-4 rounded-lg border border-rose-300 bg-rose-50">'
        '<div class="px-4 py-3 border-b border-rose-200 flex items-center justify-between">'
        '<div class="flex items-center gap-3">'
        '<span class="inline-block w-3 h-3 rounded-full bg-rose-600"></span>'
        '<strong class="text-rose-900 font-semibold">Data quality issues detected</strong>'
        f'<span class="text-rose-800 text-sm">'
        f' &mdash; {total:,} transactions audited.'
        f' Click any red row to review &amp; fix.'
        f'</span>'
        '</div>'
        f'<span class="text-rose-700 text-xs">Checked {_fmt_dt(generated_at)}</span>'
        '</div>'
        f'<div class="px-4 py-2 space-y-0.5">{rows_html}</div>'
        + _apply_bar()
        + _panel_script()
        + '</div>'
    )


def _apply_bar() -> str:
    """Footer with the 'apply staged fixes' button + status line."""
    return (
        '<div class="px-4 py-2 border-t border-rose-200 flex flex-wrap '
        'items-center gap-3 text-xs text-rose-800">'
        '<button type="button" id="ddq-apply-btn" onclick="ddqApplyFixes()" '
        'disabled class="px-3 py-1 font-semibold rounded bg-emerald-600 text-white '
        'hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed '
        'disabled:hover:bg-emerald-600">Apply staged fixes</button>'
        '<span id="ddq-apply-status" class="text-slate-600"></span>'
        '<span id="ddq-server-note" class="text-amber-700">'
        'Checking for the review server…</span>'
        '<span class="ml-auto text-slate-500">Full report: '
        '<code class="px-1 bg-rose-100 rounded">.data/_date_audit_report.json</code>'
        '</span>'
        '</div>'
    )


def _panel_script() -> str:
    """Scoped JS for staging + applying fixes via the local server.py API.

    Every write goes through the existing /api/stage-edit and /api/apply-edits
    endpoints (served by server.py on the user's machine). If the server is not
    running — e.g. the static GitHub Pages copy — the fetch fails and the user
    is told to start it. No DB write happens in this file or the browser.
    """
    return (
        '<script>\n'
        '(function(){\n'
        '  // Server detection: fix controls stay disabled until a ping to the\n'
        '  // local server.py (/api/status) succeeds. On the static GitHub Pages\n'
        '  // copy this fails, so the buttons stay greyed out with an explanation.\n'
        '  function ddqDetectServer(){\n'
        '    var note=document.getElementById("ddq-server-note");\n'
        '    fetch("api/status",{method:"GET"}).then(function(r){\n'
        '      if(!r.ok) throw new Error("bad status");\n'
        '      document.querySelectorAll(".ddq-fix-btn").forEach(function(b){b.disabled=false;});\n'
        '      var ab=document.getElementById("ddq-apply-btn"); if(ab) ab.disabled=false;\n'
        '      if(note){note.textContent=""; note.className="";}\n'
        '    }).catch(function(){\n'
        '      if(note){note.textContent="Review server not running — start it with: python server.py (then reload) to enable fixes.";}\n'
        '    });\n'
        '  }\n'
        '  if(document.readyState!=="loading"){ddqDetectServer();}\n'
        '  else{document.addEventListener("DOMContentLoaded",ddqDetectServer);}\n'
        '  function msg(fp, text, ok){\n'
        '    var el=document.getElementById("ddq-msg-"+fp); if(!el)return;\n'
        '    el.textContent=text; el.className="text-[11px] mt-0.5 "+(ok?"text-emerald-600":"text-rose-600");\n'
        '  }\n'
        '  function noServer(e){\n'
        '    return "Could not reach the review server. Start it with: python server.py";\n'
        '  }\n'
        '  window.ddqStageFix=function(fp,inpId){\n'
        '    var inp=document.getElementById(inpId); if(!inp)return;\n'
        '    var d=inp.value;\n'
        '    if(!/^\\d{4}-\\d{2}-\\d{2}$/.test(d)){msg(fp,"Enter a valid date first.",false);return;}\n'
        '    msg(fp,"Staging…",true);\n'
        '    fetch("api/stage-edit",{method:"POST",headers:{"Content-Type":"application/json"},\n'
        '      body:JSON.stringify({action:"update",target_fingerprint:fp,fields:{date:d}})})\n'
        '      .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})\n'
        '      .then(function(res){\n'
        '        if(res.ok&&res.j.ok){msg(fp,"Staged ("+res.j.queue_length+" queued). Click \\u201cApply staged fixes\\u201d below.",true);}\n'
        '        else{msg(fp,(res.j.error||"Stage failed")+(res.j.field_errors?": "+res.j.field_errors.join("; "):""),false);}\n'
        '      }).catch(function(e){msg(fp,noServer(e),false);});\n'
        '  };\n'
        '  window.ddqApplyFixes=function(){\n'
        '    var btn=document.getElementById("ddq-apply-btn");\n'
        '    var st=document.getElementById("ddq-apply-status");\n'
        '    btn.disabled=true; st.textContent="Applying…";\n'
        '    fetch("api/apply-edits",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})\n'
        '      .then(function(r){return r.json().then(function(j){return {code:r.status,j:j};});})\n'
        '      .then(function(res){\n'
        '        if(res.code===400){st.textContent="Nothing staged yet — stage a fix first."; btn.disabled=false; return;}\n'
        '        if(res.code===409){st.textContent="An apply is already running — wait, then reload."; return;}\n'
        '        if(res.code!==202){st.textContent=(res.j.error||"Apply failed to start."); btn.disabled=false; return;}\n'
        '        ddqPoll();\n'
        '      }).catch(function(e){st.textContent=noServer(e); btn.disabled=false;});\n'
        '  };\n'
        '  function ddqPoll(){\n'
        '    var st=document.getElementById("ddq-apply-status");\n'
        '    fetch("api/apply-status").then(function(r){return r.json();}).then(function(s){\n'
        '      if(s.status==="running"){st.textContent="Applying… "+(s.step_label||s.step||""); setTimeout(ddqPoll,1500); return;}\n'
        '      if(s.status==="error"){st.textContent="Apply error: "+(s.error||"see _apply_last.log"); document.getElementById("ddq-apply-btn").disabled=false; return;}\n'
        '      st.innerHTML="Done. <a class=\\"text-indigo-600 underline\\" href=\\".\\">Reload</a> to see corrected data (re-run the audit to clear the banner).";\n'
        '    }).catch(function(e){st.textContent="Applied, but status poll failed — reload to check."; });\n'
        '  }\n'
        '})();\n'
        '</script>'
    )


def render_panel_from_path(report_path: Path) -> str:
    """Convenience: load the JSON and render in one call. Safe on missing file."""
    return render_panel(load_report(report_path))
