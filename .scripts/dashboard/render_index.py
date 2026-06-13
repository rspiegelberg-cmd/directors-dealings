"""Render outputs/index.html -- the daily-action surface.

Reads `dashboard/data/signals.json` + `dashboard/data/dealings.json`.

Layout (locked, stage-05-design-final.md s 1.1):
  - Header: title + nav link to performance.html
  - Top strip: 3 tiles (signals today, active clusters, open paper P&L)
  - Main grid: today's table (8 cols) + brewing/active clusters panel (4 cols)
  - Footer: generated-at + build sha
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import render_helpers as h
from . import templates


def _format_time(time_utc: str, today_str: str) -> str:
    """B-010: render today's-rows as "Today" rather than the filing HH:MM.

    Older rows fall through to the bare day-month (e.g. "18 May"). The
    HH:MM rendering was inconsistent with every other table; consolidate
    on the shared dashboard date helper instead.
    """
    return h.format_dashboard_date(time_utc, today_str)



def _sort_week(rows):
    """This Week sub-table sort: chronological, freshest first.

    Rupert decision 2026-05-18 (post-Sprint-3): the This Week table
    spans multiple dates, so tier-then-value ordering visually scrambled
    the timeline (a Tuesday cluster filing ended up above a Thursday
    one). Switch to date DESC, with value DESC as the tiebreak inside
    a single date. The underlying SQL already sorts this way; this
    render-layer function just stops the previous override.
    """
    def key(r):
        return (
            r.get("time_utc", "") or "",
            float(r.get("value_gbp") or 0),
        )
    return sorted(rows, key=key, reverse=True)


def _brewing_sparkline_svg(values, width=84, height=20) -> str:
    """B-132: tiny inline sparkline for the 8-week brewing-cluster trend.

    Self-contained SVG polyline normalised to the series min/max; inherits the
    enclosing text colour via currentColor. Returns "" for <2 points.
    """
    vals = [v for v in (values or []) if isinstance(v, (int, float))]
    if len(vals) < 2:
        return ""
    vmax, vmin = max(vals), min(vals)
    span = (vmax - vmin) or 1
    n = len(vals)
    step = width / (n - 1)
    pts = []
    for i, v in enumerate(vals):
        x = i * step
        y = height - 2 - ((v - vmin) / span) * (height - 4)
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'class="inline-block align-middle" preserveAspectRatio="none" '
        f'aria-hidden="true"><polyline points="{" ".join(pts)}" fill="none" '
        f'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" '
        f'stroke-linecap="round"/></svg>'
    )


def _row_html(row, today_str: str) -> str:
    ticker = h.esc(row.get("ticker"))
    company = row.get("company") or ""
    company_trim = company if len(company) <= 32 else company[:30] + "..."
    director = row.get("director") or "-"
    role = row.get("role") or ""
    role_normalized = row.get("role_normalized")
    txn_type = row.get("txn_type") or ""
    if txn_type and txn_type.upper() != "BUY":
        return ""  # defensive: spec says skip non-BUY rows in v1
    value = row.get("value_gbp") or 0
    value_cls = "text-slate-400" if (value == 0 or value is None) else ""
    sigs = row.get("signals_fired") or []
    # B-098: use abs_return_pct (gross, no cost deduction) for Stock Rtn column.
    # mtm_pct (net) is still in the JSON for paper-book / backward compat.
    abs_ret = row.get("abs_return_pct")
    if abs_ret is None:
        mtm_html = '<span class="text-slate-400">-</span>'
    else:
        glyph = "&#9650;" if abs_ret > 0 else ("&#9660;" if abs_ret < 0 else "")
        cls = h.car_color_class(abs_ret)
        mtm_html = f'<span class="{cls}">{glyph} {h.esc(h.pct(abs_ret))}</span>'
    # B113: sector benchmark return over same period.
    bench_html = h.car_cell(row.get("bench_return_pct"))
    ticker_link = (
        f'<a href="companies/{ticker}.html" '
        f'class="text-blue-600 hover:underline font-mono">{ticker}</a>'
    )
    time_utc = row.get("time_utc") or ""
    abs_ret_val = abs_ret if abs_ret is not None else ""
    # B-110: live/ageing/closed chip from time_utc (proxy for fired_at on
    # the This Week table — signals fire same day as RNS).
    live_chip = h.signal_live_chip(time_utc, today_str)
    live_chip_html = (" " + live_chip) if live_chip else ""
    # B-111 / B-114: pre-earnings conviction flag. A buy landing within 60 days
    # BEFORE an upcoming confirmed results date (earnings / trading statement /
    # interim / etc.) is a higher-conviction signal. Forward-only (uses the
    # upcoming confirmed date from reporting_dates) — no historical CAR, no
    # lookahead concern. B-114 elevates the passive B-111 "~results" badge into a
    # prominent conviction chip and tags the row (data-pe) so This-Week can filter
    # to pre-earnings buys only. "(est)" appended when the date is synthetic.
    near_rd = row.get("near_reporting_date")
    pe_flag = "1" if near_rd else "0"
    if near_rd:
        _est = bool(row.get("near_reporting_est"))
        report_badge = (
            ' <span class="pe-chip inline-flex items-center gap-0.5 text-[9px] '
            'px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 font-semibold '
            'uppercase tracking-wide align-middle" '
            f'title="Pre-earnings buy: results due {h.esc(near_rd)} (within 60 days '
            f'of this buy){" - estimated date" if _est else ""}">'
            f'&#9889; pre-earnings{" (est)" if _est else ""}</span>'
        )
    else:
        report_badge = ""
    # B-120: mark rows whose price failed the B-060 market-data audit so the
    # value is visibly flagged as unconfirmed (not silently shown as fact).
    unverified_chip = (
        ' <span class="inline-flex items-center text-[9px] px-1 py-0.5 rounded '
        'bg-slate-200 text-slate-600 font-semibold uppercase tracking-wide '
        'align-middle" title="Price could not be verified against market data '
        '(B-060 audit) - value is unconfirmed">&#9888; unverified</span>'
        if row.get("unverified") else ""
    )
    # B-146: market cap display.
    mc_val = row.get("market_cap_gbp")
    mc_sort = mc_val if mc_val is not None else ""
    return (
        f'<tr data-pe="{pe_flag}" '
        f'class="border-t border-slate-100 hover:bg-indigo-50 cursor-pointer" '
        f'onclick="window.open(\'companies/{ticker}.html\',\'_blank\')">'
        # B-103: data-sv = sort value for client-side JS sort.
        f'<td class="px-3 py-2 text-slate-600" data-sv="{h.esc(time_utc)}">'
        f'{h.esc(_format_time(time_utc, today_str))}{live_chip_html}</td>'
        f'<td class="px-3 py-2 font-medium text-slate-900" data-sv="{ticker}">{ticker_link}{report_badge}</td>'
        f'<td class="px-3 py-2 text-slate-700 truncate max-w-[18rem]" title="{h.esc(company)}" data-sv="{h.esc(company)}">{h.esc(company_trim)}</td>'
        f'<td class="px-3 py-2 text-slate-700" data-sv="{h.esc(director)}">{h.esc(director)}</td>'
        f'<td class="px-3 py-2">{h.role_chip(role, role_normalized)}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums {value_cls}" data-sv="{value}">{h.gbp(value)}{unverified_chip}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums text-slate-500" data-sv="{mc_sort}">{h.fmt_mktcap(mc_val)}</td>'
        f'<td class="px-3 py-2">{h.render_badges_row(sigs)}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums" data-sv="{abs_ret_val}">{mtm_html}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums">{bench_html}</td>'
        f'</tr>'
    )


def _render_table_body(rows, today_str: str) -> str:
    rendered = [_row_html(r, today_str) for r in rows]
    return "".join([r for r in rendered if r])


def _table_sort_js() -> str:
    """B-103: Client-side table sort wired to data-sort <th> elements.

    Each sortable <th> carries data-sort, data-col (0-based td index),
    data-type ('str'|'num'|'date'). Each sortable <td> carries data-sv
    (sort value). Click cycles: none -> asc -> desc -> asc.
    State is per-visit only (no localStorage).
    """
    return r"""<script>
(function(){
  'use strict';
  function wireTable(table){
    var ths = table.querySelectorAll('th[data-sort]');
    if (!ths.length) return;
    var state = {};  // col -> 'asc'|'desc'
    ths.forEach(function(th){
      th.style.userSelect = 'none';
      th.addEventListener('click', function(){
        var col = parseInt(th.getAttribute('data-col'), 10);
        var dtype = th.getAttribute('data-type') || 'str';
        var dir = state[col] === 'asc' ? 'desc' : 'asc';
        state = {};
        state[col] = dir;
        // Update indicators.
        table.querySelectorAll('th[data-sort] .sort-ind').forEach(function(s){
          s.textContent = '';
        });
        var ind = th.querySelector('.sort-ind');
        if (ind) ind.textContent = dir === 'asc' ? ' ▲' : ' ▼';
        // Sort tbody rows.
        var tbody = table.querySelector('tbody');
        if (!tbody) return;
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        rows.sort(function(a, b){
          var tds_a = a.querySelectorAll('td');
          var tds_b = b.querySelectorAll('td');
          var va = (tds_a[col] && tds_a[col].getAttribute('data-sv')) || '';
          var vb = (tds_b[col] && tds_b[col].getAttribute('data-sv')) || '';
          var cmp;
          if (dtype === 'num'){
            var na = parseFloat(va), nb = parseFloat(vb);
            cmp = (isNaN(na) ? -Infinity : na) - (isNaN(nb) ? -Infinity : nb);
          } else {
            cmp = va.localeCompare(vb);
          }
          return dir === 'asc' ? cmp : -cmp;
        });
        rows.forEach(function(r){ tbody.appendChild(r); });
      });
    });
  }
  document.querySelectorAll('table').forEach(wireTable);
})();
</script>"""


def _classify_cluster(cluster: dict, today_str: str):
    """Return 'active' (s1_active=true) or 'brewing' (false, last_buy 30-90d back) or None (stale)."""
    if cluster.get("s1_active"):
        return "active"
    last = cluster.get("last_buy_date")
    days = h.days_since(last, today_str)
    if days is None:
        return None
    if days <= 90:
        return "brewing"
    return None


def _cluster_card(cluster: dict, kind: str) -> str:
    ticker = h.esc(cluster.get("ticker") or "-")
    company = cluster.get("company") or ""
    company_short = company if len(company) <= 28 else company[:26] + "..."
    dc = cluster.get("director_count") or 0
    agg = cluster.get("aggregate_value_gbp") or 0
    fb = cluster.get("first_buy_date") or "-"
    lb = cluster.get("last_buy_date") or "-"
    conviction = cluster.get("conviction")
    if kind == "active":
        badge = ('<span class="inline-flex px-2 py-0.5 rounded-full text-[10px] '
                 'font-semibold bg-blue-100 text-blue-700">S1</span>')
    else:
        badge = ('<span class="inline-flex px-2 py-0.5 rounded-full text-[10px] '
                 'font-semibold bg-amber-100 text-amber-700">brewing</span>')
    if conviction is not None:
        cv_chip = (
            f'<span class="text-[10px] tabular-nums font-mono text-slate-400" '
            f'title="Conviction score: directors×3 + value tier×2 + '
            f'date compression×2">'
            f'cv:{conviction}</span>'
        )
    else:
        cv_chip = ""
    agg_str = f"{(agg or 0) / 1000:.1f}k"
    ticker_link = (
        f'<a href="companies/{ticker}.html" '
        f'class="text-blue-600 hover:underline font-mono">{ticker}</a>'
    )
    return (
        f'<div class="border-b border-slate-100 px-4 py-3 hover:bg-indigo-50 '
        f'cursor-pointer" onclick="window.open(\'companies/{ticker}.html\',\'_blank\')">'
        f'<div class="flex items-center justify-between">'
        f'<div class="font-medium text-sm text-slate-900">{ticker_link}</div>'
        f'<div class="flex items-center gap-1.5">{badge}{cv_chip}</div>'
        f'</div>'
        f'<div class="text-xs text-slate-600 truncate" title="{h.esc(company)}">'
        f'{h.esc(company_short)}</div>'
        f'<div class="text-[11px] text-slate-500 mt-1 tabular-nums">'
        f'{int(dc)} dirs &middot; &pound;{h.esc(agg_str)} &middot; {h.esc(fb)} - {h.esc(lb)}'
        f'</div></div>'
    )


def render(signals_data: dict, dealings_data: dict,
           build_sha: str = "local",
           health_panel_html: str = "") -> str:
    today_str = dealings_data.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()

    # B-059 — company search box: pull index from signals JSON.
    companies_index = signals_data.get("companies_index") or []
    companies_index_json = json.dumps(companies_index, separators=(",", ":"))

    signals_today_count = dealings_data.get("signals_today_count") or 0
    delta = dealings_data.get("signals_today_delta_vs_avg") or 0
    if delta > 0:
        delta_glyph, delta_cls = "&#9650;", "text-emerald-600"
        delta_str = f"+{delta}"
    elif delta < 0:
        delta_glyph, delta_cls = "&#9660;", "text-rose-600"
        delta_str = f"{delta}"
    else:
        delta_glyph, delta_cls = "-", "text-slate-500"
        delta_str = "0"

    clusters = signals_data.get("active_clusters") or []
    active = []
    brewing = []
    for c in clusters:
        kind = _classify_cluster(c, today_str)
        if kind == "active":
            active.append(c)
        elif kind == "brewing":
            brewing.append(c)
    active = sorted(active, key=lambda c: -(c.get("aggregate_value_gbp") or 0))
    brewing = sorted(brewing, key=lambda c: -(c.get("aggregate_value_gbp") or 0))
    n_active = len(active)
    n_brewing = len(brewing)

    # B-132: brewing-cluster trend (count vs ~30d avg + 8-week sparkline).
    brew_trend = signals_data.get("cluster_brewing_trend") or {}
    brew_weekly = brew_trend.get("weekly") or []
    brew_avg = brew_trend.get("avg_30d")
    if brew_weekly and brew_avg is not None:
        if n_brewing > brew_avg:
            _b_arrow, _b_cls = "&#9650;", "text-emerald-600"
        elif n_brewing < brew_avg:
            _b_arrow, _b_cls = "&#9660;", "text-rose-600"
        else:
            _b_arrow, _b_cls = "", "text-slate-400"
        brew_trend_html = (
            f' <span class="{_b_cls}" title="Brewing clusters now ({n_brewing}) '
            f'vs trailing-30-day average ({brew_avg:g})">{_b_arrow} '
            f'<span class="text-slate-400">vs {brew_avg:g} avg</span></span> '
            f'<span class="text-slate-400" title="Brewing clusters, last 8 weeks">'
            f'{_brewing_sparkline_svg(brew_weekly)}</span>'
        )
    else:
        brew_trend_html = ""

    # B-151: holding-basket counter.
    total_companies = signals_data.get("total_companies") or 0
    pending_classification = signals_data.get("pending_classification") or 0

    paper_pnl = signals_data.get("paper_pnl_open") or 0.0
    paper_open = signals_data.get("paper_trades_open") or 0
    paper_closed = signals_data.get("paper_trades_closed") or 0
    if paper_pnl > 0:
        pnl_cls = "text-emerald-600"
        pnl_str = f"+&pound;{paper_pnl:,.0f}"
    elif paper_pnl < 0:
        pnl_cls = "text-rose-600"
        pnl_str = f"-&pound;{abs(paper_pnl):,.0f}"
    else:
        pnl_cls = "text-slate-500"
        pnl_str = "&pound;0"

    # Top strip tiles.
    quiet = ('<div class="text-[10px] text-slate-400 mt-1">- quiet day -</div>'
             if signals_today_count == 0 else "")
    tile1 = (
        '<div class="bg-slate-50 border border-slate-200 rounded-lg p-4">'
        '<div class="text-xs uppercase tracking-wide text-slate-500">Signals today</div>'
        f'<div class="text-3xl font-semibold tabular-nums text-slate-900 mt-1" '
        'title="Count of distinct PDMR transactions today that fired >=1 signal.">'
        f'{signals_today_count}</div>'
        f'<div class="text-xs mt-1 {delta_cls}">{delta_glyph} {delta_str} vs 7d avg</div>'
        f'{quiet}'
        '</div>'
    )
    tile2 = (
        '<div class="bg-slate-50 border border-slate-200 rounded-lg p-4">'
        '<div class="text-xs uppercase tracking-wide text-slate-500">Active clusters</div>'
        '<div class="text-3xl font-semibold tabular-nums text-slate-900 mt-1" '
        'title="Active = S1-firing (>=2 directors, most recent buy <=30d). Brewing = 30-90d.">'
        f'{n_active}</div>'
        f'<div class="text-xs text-slate-500 mt-1">{n_brewing} brewing{brew_trend_html}</div>'
        '</div>'
    )
    # B-153: relabel T1 option to avoid confusion with T+1 time horizon.
    _tier_labels = {
        "all": "All", "t0": "T0", "t1": "T1 — CEO/CFO/Founder",
        "t2": "T2", "t3": "T3", "t4": "T4", "s1": "S1", "f1": "F1",
    }
    tile3_opts = "".join(
        f'<option value="{k}">{_tier_labels[k]}</option>'
        for k in ("all", "t0", "t1", "t2", "t3", "t4", "s1", "f1")
    )
    paper_caveat = ('<div class="text-[10px] text-slate-400 mt-1">'
                    'Paper tracking starts when Stage 6 ships.</div>'
                    if paper_open == 0 and paper_closed == 0 else "")
    # B-153: horizon toggle — Now / T+1 / T+21 / T+90 (JS-driven).
    _hz_btn = (
        '<div class="flex gap-1 mt-1">'
        '<button class="hz-btn active bg-slate-700 text-white text-[10px] '
        'rounded px-1.5 py-0.5 leading-tight" data-hz="mkt">Now</button>'
        '<button class="hz-btn text-[10px] border border-slate-300 rounded '
        'px-1.5 py-0.5 text-slate-600 leading-tight" data-hz="t1">T+1</button>'
        '<button class="hz-btn text-[10px] border border-slate-300 rounded '
        'px-1.5 py-0.5 text-slate-600 leading-tight" data-hz="t21">T+21</button>'
        '<button class="hz-btn text-[10px] border border-slate-300 rounded '
        'px-1.5 py-0.5 text-slate-600 leading-tight" data-hz="t90">T+90</button>'
        '</div>'
    )
    tile3 = (
        '<div class="bg-slate-50 border border-slate-200 rounded-lg p-4">'
        '<div class="text-xs uppercase tracking-wide text-slate-500">Open paper P&amp;L</div>'
        f'<div id="paperPnlDisplay" class="text-3xl font-semibold tabular-nums {pnl_cls} mt-1" '
        'title="Mark-to-market of all open paper positions, net of costs.">'
        f'{pnl_str}</div>'
        '<select class="text-xs border border-slate-300 rounded px-2 py-1 mt-1 bg-white" '
        'id="paperFilter">'
        f'{tile3_opts}</select>'
        f'{_hz_btn}'
        f'<div id="paperCountsDisplay" class="text-[11px] text-slate-500 mt-1">'
        f'{int(paper_open)} open &middot; {int(paper_closed)} closed</div>'
        f'{paper_caveat}'
        '</div>'
    )

    # B-152: Capital Deployed row — 3 panels (All / Small Cap / Large Cap).
    _cd = signals_data.get("capital_deployed") or {}

    def _cap_panel(label: str, series_key: str, ma_key: str, canvas_id: str,
                   count_key: str = "", ma_count_key: str = "") -> str:
        series = _cd.get(series_key) or []
        ma = _cd.get(ma_key) or 0
        current = series[-1] if series else 0
        # Value trend: current vs 3-month average.
        if ma > 0 and current != ma:
            pct = (current - ma) / ma * 100
            t_glyph = "&#9650;" if pct > 0 else "&#9660;"
            t_cls = "text-emerald-600" if pct > 0 else "text-rose-600"
            t_str = f'{t_glyph} {abs(pct):.0f}% vs 3m avg'
        elif ma == 0 and current == 0:
            t_cls = "text-slate-400"
            t_str = "no positions yet"
        else:
            t_cls = "text-slate-500"
            t_str = "&#8776; 3m avg"
        cur_str = f'&pound;{int(current):,}' if current else '&pound;0'
        # Volume count row (count_key supplied by caller).
        vol_html = ""
        if count_key:
            cnt_series = _cd.get(count_key) or []
            cnt_ma = _cd.get(ma_count_key) or 0.0
            cnt_cur = cnt_series[-1] if cnt_series else 0
            cnt_str = f'{cnt_cur} trade{"s" if cnt_cur != 1 else ""}'
            if cnt_ma > 0 and cnt_cur != cnt_ma:
                cnt_pct = (cnt_cur - cnt_ma) / cnt_ma * 100
                c_glyph = "&#9650;" if cnt_pct > 0 else "&#9660;"
                c_cls = "text-emerald-600" if cnt_pct > 0 else "text-rose-600"
                c_trend = f'{c_glyph} {abs(cnt_pct):.0f}% vs 3m avg'
            elif cnt_ma == 0 and cnt_cur == 0:
                c_cls = "text-slate-400"
                c_trend = ""
            else:
                c_cls = "text-slate-500"
                c_trend = "&#8776; 3m avg"
            vol_html = (
                f'<div class="text-[10px] font-medium text-slate-600 mt-1.5">'
                f'{cnt_str}</div>'
                + (f'<div class="text-[10px] {c_cls}">{c_trend}</div>'
                   if c_trend else "")
            )
        return (
            f'<div class="bg-slate-50 border border-slate-200 rounded-lg p-3">'
            f'<div class="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">'
            f'Capital Deployed &mdash; {label}</div>'
            f'<div class="text-xl font-semibold tabular-nums text-slate-800">'
            f'{cur_str}</div>'
            f'<div class="text-[10px] {t_cls} mt-0.5">{t_str}</div>'
            + vol_html +
            f'<canvas id="{canvas_id}" width="120" height="28" '
            f'class="w-full mt-1 block"></canvas>'
            f'</div>'
        )

    capital_deployed_row = (
        '<div class="grid grid-cols-3 gap-3 px-6 pb-3">'
        + _cap_panel("All",       "all",   "ma3m_all",   "capChart_all",
                     count_key="all_count",   ma_count_key="ma3m_all_count")
        + _cap_panel("Small Cap", "small", "ma3m_small", "capChart_small",
                     count_key="small_count", ma_count_key="ma3m_small_count")
        + _cap_panel("Large Cap", "large", "ma3m_large", "capChart_large",
                     count_key="large_count", ma_count_key="ma3m_large_count")
        + '</div>'
    )

    # This Week table: chronological (Rupert decision 2026-05-18 post-Sprint-3).
    week_rows = _sort_week(
        [r for r in (dealings_data.get("this_week") or [])
         if (r.get("txn_type") or "BUY").upper() == "BUY"])
    # B-114: count of pre-earnings buys (within 60d before an upcoming results
    # date) for the This-Week filter toggle label.
    pe_count = sum(1 for r in week_rows if r.get("near_reporting_date"))

    # B-103: data-sort attributes drive the client-side sort JS.
    # data-col is the td index (0-based); data-type is 'str'|'num'|'date'.
    headers = (
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left w-[10%] cursor-pointer select-none" '
        'data-sort data-col="0" data-type="date" title="Sort by time">Time <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-left w-[8%] cursor-pointer select-none" '
        'data-sort data-col="1" data-type="str" title="Sort by ticker">Ticker <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-left w-[28%] cursor-pointer select-none" '
        'data-sort data-col="2" data-type="str" title="Sort by company">Company <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-left w-[18%] cursor-pointer select-none" '
        'data-sort data-col="3" data-type="str" title="Sort by director">Director <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-left w-[12%]">Role</th>'
        '<th class="px-3 py-2 text-right w-[10%] cursor-pointer select-none" '
        'data-sort data-col="5" data-type="num" title="Sort by value">&pound; Value <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-right w-[7%] cursor-pointer select-none" '
        'data-sort data-col="6" data-type="num" title="Sort by market cap (B-146)">Mkt Cap <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-left w-[8%]">Signals</th>'
        '<th class="px-3 py-2 text-right w-[4%] cursor-pointer select-none" '
        'data-sort data-col="8" data-type="num" title="Sort by stock return. B-098: Gross stock return since T+1 close after signal date. No benchmark, no cost deduction. See CAR on Performance page for risk-adjusted return.">Stock Rtn* <span class="sort-ind"></span></th>'
        '<th class="px-3 py-2 text-right w-[4%]" '
        'title="B113: Sector benchmark return over the same period as Stock Rtn. Compares to CAR (risk-adjusted) on Performance page.">Bmk</th>'
        '</tr></thead>'
    )
    week_body = _render_table_body(week_rows, today_str)

    today_section = (
        # overflow-x-auto (not -hidden): on narrower viewports the 8-col table
        # is wider than this 2/3-width panel; -hidden silently clipped the
        # rightmost MTM column off-screen. -auto lets it scroll into view.
        '<div class="col-span-8 bg-white border border-slate-200 rounded-lg overflow-x-auto">'
        # B-114: header with a "pre-earnings only" filter toggle (forward-only
        # conviction category — buys 0-60d before an upcoming results date).
        '<div class="flex items-center justify-between px-4 py-3 '
        'border-b border-slate-100">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500">'
        'This week\'s buy signals</h2>'
        + ('<label class="flex items-center gap-1 text-[10px] text-amber-800 '
           'cursor-pointer select-none" title="Show only buys within 60 days before '
           'an upcoming results date (pre-earnings conviction).">'
           '<input type="checkbox" id="peOnly" class="accent-amber-500">'
           f'<span>&#9889; Pre-earnings only ({pe_count})</span></label>'
           if pe_count else '')
        + '</div>'
        '<table class="w-full min-w-[640px] text-xs tabular-nums">'
        f'{headers}'
        f'<tbody>{week_body}</tbody></table>'
        + (h.empty_state("No signals fired this week. Check back after the next RNS refresh.", py=12)
           if not week_body else "")
        + '<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">'
        '*Stock Rtn = gross stock return from T+1 close after RNS to latest close '
        '(no benchmark, no cost deduction). Bmk = sector benchmark return over same period. '
        'CAR on the Performance page is benchmark-adjusted + net of costs. '
        'Click any row for the company page.</div>'
        # B-114: pre-earnings filter — hide non-pre-earnings rows when checked.
        '<script>(function(){'
        'var cb=document.getElementById("peOnly");if(!cb)return;'
        'var box=cb.closest(".col-span-8");'
        'cb.addEventListener("change",function(){'
        'var on=cb.checked;'
        'box.querySelectorAll("tbody tr[data-pe]").forEach(function(tr){'
        'tr.style.display=(on&&tr.getAttribute("data-pe")!=="1")?"none":"";});'
        '});})();</script>'
        '</div>'
    )

    # Clusters panel with two tabs.
    tab_header = (
        '<div class="flex border-b border-slate-200 text-xs">'
        '<button data-tab="active" '
        'class="border-b-2 border-indigo-600 text-indigo-700 px-4 py-2 font-medium '
        'tab-btn">'
        f'Active ({n_active})</button>'
        '<button data-tab="brewing" '
        'class="text-slate-500 px-4 py-2 hover:text-slate-700 tab-btn">'
        f'Brewing ({n_brewing})</button>'
        '</div>'
    )
    active_cards = "".join(_cluster_card(c, "active") for c in active)
    brewing_cards = "".join(_cluster_card(c, "brewing") for c in brewing)
    if not active_cards:
        active_cards = h.empty_state("No active clusters right now.", py=8)
    if not brewing_cards:
        brewing_cards = h.empty_state(
            "No brewing clusters right now. New fresh clusters are in the Active tab.", py=8)
    clusters_section = (
        '<aside class="col-span-4 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
        'border-b border-slate-100">Brewing &amp; active clusters</h2>'
        f'{tab_header}'
        f'<div id="tab-active">{active_cards}</div>'
        f'<div id="tab-brewing" class="hidden">{brewing_cards}</div>'
        '<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">'
        '>=2 distinct directors buying same ticker, <=30d apart. Brewing = most '
        'recent buy 30-90d back; stale clusters hidden.</div>'
        '</aside>'
    )

    # B-145: Upcoming Events panel — rolling 2-week forward-look from
    # reporting_dates. Displayed below the main 12-col grid as a full-width box.
    upcoming_events = dealings_data.get("upcoming_events") or []
    if upcoming_events:
        ue_rows_html = "".join(
            f'<tr class="border-t border-slate-100 hover:bg-indigo-50">'
            f'<td class="px-3 py-2 tabular-nums text-slate-600">{h.esc(evt["date"])}</td>'
            f'<td class="px-3 py-2 font-mono text-xs">'
            f'<a href="companies/{h.esc(evt["ticker"])}.html" '
            f'class="text-blue-600 hover:underline">{h.esc(evt["ticker"])}</a></td>'
            f'<td class="px-3 py-2 text-slate-700">{h.esc(evt["company"])}</td>'
            f'<td class="px-3 py-2 text-slate-500 tabular-nums text-right">'
            f'{h.fmt_mktcap(evt.get("market_cap_gbp"))}</td>'
            f'<td class="px-3 py-2 text-slate-600">{h.esc(evt["event_type"])}</td>'
            f'</tr>'
            for evt in upcoming_events
        )
        ue_body = (
            '<table class="w-full text-xs tabular-nums">'
            '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
            '<tr>'
            '<th class="px-3 py-2 text-left w-[10%]">Date</th>'
            '<th class="px-3 py-2 text-left w-[8%]">Ticker</th>'
            '<th class="px-3 py-2 text-left w-[45%]">Company</th>'
            '<th class="px-3 py-2 text-right w-[10%]">Mkt Cap</th>'
            '<th class="px-3 py-2 text-left w-[27%]">Event</th>'
            '</tr></thead>'
            f'<tbody>{ue_rows_html}</tbody></table>'
        )
    else:
        ue_body = h.empty_state("No upcoming events in the next 14 days.", py=6)

    upcoming_events_section = (
        '<div class="px-6 pb-3">'
        '<div class="bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<div class="px-4 py-3 border-b border-slate-100">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500">'
        'Upcoming Events <span class="text-slate-400 normal-case font-normal">'
        '&#183; next 14 days</span></h2>'
        '</div>'
        f'{ue_body}'
        '</div>'
        '</div>'
    )

    # B-059 — search input + dropdown (sits in top strip, no template changes needed).
    search_block = (
        '<div class="px-6 pt-3 pb-1 relative inline-block">'
        '<input id="companySearch" type="text" '
        'placeholder="Search ticker or company&hellip;" '
        'class="w-72 text-sm border border-slate-200 rounded px-3 py-1.5 bg-white '
        'focus:outline-none focus:ring-1 focus:ring-indigo-400" '
        'autocomplete="off">'
        '<ul id="companyDropdown" '
        'class="hidden absolute z-20 bg-white border border-slate-200 rounded '
        'shadow-md w-72 max-h-64 overflow-y-auto text-sm list-none mt-0.5">'
        '</ul>'
        '</div>'
    )

    search_js = f"""
<script>
(function(){{
  var COMPANIES_INDEX = {companies_index_json};
  var searchEl = document.getElementById('companySearch');
  var dropEl   = document.getElementById('companyDropdown');
  if (!searchEl || !dropEl) return;
  searchEl.addEventListener('keyup', function(){{
    var q = this.value.trim().toLowerCase();
    if (!q) {{ dropEl.classList.add('hidden'); return; }}
    var matches = COMPANIES_INDEX.filter(function(c){{
      return c.ticker.toLowerCase().startsWith(q) ||
             c.company.toLowerCase().includes(q);
    }}).slice(0, 8);
    if (!matches.length) {{ dropEl.classList.add('hidden'); return; }}
    dropEl.innerHTML = matches.map(function(c){{
      return '<li class="px-3 py-2 hover:bg-slate-50"><a class="block" href="' + c.url + '">' +
             '<span class="font-mono text-xs font-semibold text-indigo-600">' + c.ticker + '</span>' +
             ' &mdash; ' + c.company + '</a></li>';
    }}).join('');
    dropEl.classList.remove('hidden');
  }});
  document.addEventListener('click', function(e){{
    if (!searchEl.contains(e.target) && !dropEl.contains(e.target)){{
      dropEl.classList.add('hidden');
    }}
  }});
}})();
</script>
"""

    tab_js = """
<script>
(function(){
  const btns = document.querySelectorAll('.tab-btn');
  btns.forEach(function(b){
    b.addEventListener('click', function(){
      const target = b.getAttribute('data-tab');
      document.getElementById('tab-active').classList.toggle('hidden', target !== 'active');
      document.getElementById('tab-brewing').classList.toggle('hidden', target !== 'brewing');
      btns.forEach(function(x){
        if (x.getAttribute('data-tab') === target) {
          x.classList.add('border-b-2','border-indigo-600','text-indigo-700','font-medium');
          x.classList.remove('text-slate-500');
        } else {
          x.classList.remove('border-b-2','border-indigo-600','text-indigo-700','font-medium');
          x.classList.add('text-slate-500');
        }
      });
    });
  });

  // B-153: Paper P&L tile — reactive tier filter + horizon toggle.
  (function(){
    var positions = (window.__paperPositions || []);
    var currentTier = localStorage.getItem('dd_paper_filter') || 'all';
    var currentHz  = localStorage.getItem('dd_paper_hz')     || 'mkt';

    // Restore persisted state.
    var sel = document.getElementById('paperFilter');
    if (sel) sel.value = currentTier;
    document.querySelectorAll('.hz-btn').forEach(function(b){
      var active = b.getAttribute('data-hz') === currentHz;
      b.classList.toggle('bg-slate-700', active);
      b.classList.toggle('text-white',   active);
      b.classList.toggle('border',      !active);
      b.classList.toggle('border-slate-300', !active);
      b.classList.toggle('text-slate-600',   !active);
    });

    function hzKey(hz){
      if (hz === 't1')  return 'close_t1';
      if (hz === 't21') return 'close_t21';
      if (hz === 't90') return 'close_t90';
      return 'current_close'; // 'mkt' = live mark-to-market
    }

    function recompute(){
      var tier = currentTier;
      var hz   = currentHz;
      var ck   = hzKey(hz);
      var filtered = positions.filter(function(p){
        if (p.status !== 'OPEN') return false;  // JSON uses uppercase
        if (tier === 'all') return true;
        return p.signal_id && p.signal_id.startsWith(tier);
      });

      var pnl = 0;
      filtered.forEach(function(p){
        var entry   = p.entry_close;
        var horizon = p[ck];
        var notl    = p.notional_gbp;
        if (entry && horizon && notl && entry > 0){
          pnl += notl * (horizon - entry) / entry;
        }
      });

      var display = document.getElementById('paperPnlDisplay');
      if (display){
        var txt, cls;
        if (pnl > 0){
          txt = '+£' + pnl.toFixed(0).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
          cls = 'text-emerald-600';
        } else if (pnl < 0){
          txt = '-£' + Math.abs(pnl).toFixed(0).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
          cls = 'text-rose-600';
        } else {
          txt = '£0';
          cls = 'text-slate-500';
        }
        display.textContent = txt;
        ['text-emerald-600','text-rose-600','text-slate-500'].forEach(function(c){
          display.classList.remove(c);
        });
        display.classList.add(cls);
      }

      var counts = document.getElementById('paperCountsDisplay');
      if (counts){
        counts.textContent = filtered.length + ' open (filtered)';
      }
    }

    // Wire tier dropdown.
    if (sel){
      sel.addEventListener('change', function(){
        currentTier = sel.value;
        localStorage.setItem('dd_paper_filter', currentTier);
        recompute();
      });
    }

    // Wire horizon buttons.
    document.querySelectorAll('.hz-btn').forEach(function(b){
      b.addEventListener('click', function(){
        currentHz = b.getAttribute('data-hz');
        localStorage.setItem('dd_paper_hz', currentHz);
        document.querySelectorAll('.hz-btn').forEach(function(x){
          var isActive = x.getAttribute('data-hz') === currentHz;
          x.classList.toggle('bg-slate-700', isActive);
          x.classList.toggle('text-white',   isActive);
          x.classList.toggle('border',      !isActive);
          x.classList.toggle('border-slate-300', !isActive);
          x.classList.toggle('text-slate-600',   !isActive);
        });
        recompute();
      });
    });

    // Initial render with persisted prefs.
    if (positions.length) recompute();
  })();

  // B-152: Capital Deployed sparklines (area, independent y-axis per panel).
  (function(){
    var cd = window.__capDeployed || {};
    var panels = [
      {key: 'all',   id: 'capChart_all'},
      {key: 'small', id: 'capChart_small'},
      {key: 'large', id: 'capChart_large'},
    ];
    panels.forEach(function(p){
      var canvas = document.getElementById(p.id);
      if (!canvas) return;
      var series = (cd[p.key] || []);
      if (!series.length) return;
      var ctx = canvas.getContext('2d');
      var W = canvas.offsetWidth  || canvas.width;
      var H = canvas.offsetHeight || canvas.height;
      canvas.width  = W;
      canvas.height = H;
      var n = series.length;
      var minVal = Math.min.apply(null, series);
      var maxVal = Math.max.apply(null, series);
      var range  = maxVal - minVal || 1;
      var pad = 2;
      function xAt(i){ return pad + i * (W - 2*pad) / (n - 1); }
      function yAt(v){ return H - pad - (v - minVal) / range * (H - 2*pad); }
      // Area fill.
      ctx.beginPath();
      ctx.moveTo(xAt(0), yAt(series[0]));
      for (var i = 1; i < n; i++) ctx.lineTo(xAt(i), yAt(series[i]));
      ctx.lineTo(xAt(n-1), H - pad);
      ctx.lineTo(xAt(0),   H - pad);
      ctx.closePath();
      ctx.fillStyle = 'rgba(99,102,241,0.15)';
      ctx.fill();
      // Line.
      ctx.beginPath();
      ctx.moveTo(xAt(0), yAt(series[0]));
      for (var j = 1; j < n; j++) ctx.lineTo(xAt(j), yAt(series[j]));
      ctx.strokeStyle = 'rgba(99,102,241,0.8)';
      ctx.lineWidth = 1.5;
      ctx.lineJoin = 'round';
      ctx.stroke();
    });
  })();
})();
</script>
"""

    # Health panel (date integrity audit) sits at the very top so the
    # user sees green/red BEFORE any signal numbers. Empty string when
    # no audit report is available -- the page just skips the panel.
    health_block = (
        f'<div class="px-6 pt-4">{health_panel_html}</div>'
        if health_panel_html else ""
    )

    # B-151: holding-basket strip — shows total companies in DB and how many
    # are awaiting size classification (small_cap IS NULL, not excluded).
    if pending_classification > 0:
        basket_cls = "bg-amber-50 border-amber-200 text-amber-800"
        basket_icon = (
            '<svg class="w-3.5 h-3.5 inline-block mr-1 text-amber-500" '
            'fill="currentColor" viewBox="0 0 20 20">'
            '<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 '
            '3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 '
            '0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 '
            '012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" '
            'clip-rule="evenodd"/></svg>'
        )
        basket_msg = (
            f'{basket_icon}'
            f'<span class="font-semibold">{pending_classification} '
            f'{"company" if pending_classification == 1 else "companies"}'
            f'</span> pending size classification &mdash; '
            f'not included in either performance page until resolved. '
            f'Run <code class="font-mono text-[11px] bg-amber-100 px-1 rounded">'
            f'python .scripts\\backfill_market_cap.py</code> to clear.'
        )
    else:
        basket_cls = "bg-emerald-50 border-emerald-200 text-emerald-800"
        basket_icon = (
            '<svg class="w-3.5 h-3.5 inline-block mr-1 text-emerald-500" '
            'fill="currentColor" viewBox="0 0 20 20">'
            '<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 '
            '16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 '
            '1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" '
            'clip-rule="evenodd"/></svg>'
        )
        basket_msg = (
            f'{basket_icon}'
            f'All <span class="font-semibold">{total_companies} companies</span> '
            f'fully classified &mdash; small and large-cap pages are complete.'
        )
    holding_basket_strip = (
        f'<div class="mx-6 mb-3 px-4 py-2.5 border rounded-lg text-[12px] {basket_cls} '
        f'flex items-center justify-between">'
        f'<span>{basket_msg}</span>'
        f'<span class="text-[11px] opacity-60 shrink-0 ml-4">'
        f'{total_companies} companies tracked</span>'
        f'</div>'
    )

    # B-153: inject open positions for JS P&L recompute (tier filter + horizon).
    _paper_positions = signals_data.get("paper_book", {}).get("positions", [])
    _paper_positions_json = json.dumps(_paper_positions)
    # B-152: inject capital deployed series for sparkline rendering.
    _cap_deployed_json = json.dumps(_cd)
    data_scripts = (
        f'<script>window.__paperPositions = {_paper_positions_json};</script>'
        f'<script>window.__capDeployed = {_cap_deployed_json};</script>'
    )

    body = (
        health_block
        + search_block                          # B-059 search input strip
        + '<div class="grid grid-cols-3 gap-3 p-6 pb-3">'
        f'{tile1}{tile2}{tile3}'
        '</div>'
        + capital_deployed_row                  # B-152 capital deployed sparklines
        + holding_basket_strip                  # B-151 holding-basket counter
        + '<div class="grid grid-cols-12 gap-3 px-6 pb-3">'
        f'{today_section}'
        f'{clusters_section}'
        '</div>'
        + upcoming_events_section              # B-145 upcoming events panel
        + data_scripts                         # B-152/B-153 position + cap data for JS
        + tab_js
        + search_js                            # B-059 search JS
        + _table_sort_js()                     # B-103 sortable headers
    )
    return templates.base_page(
        title="Directors Dealings - This Week",
        body=body,
        generated_at_iso=dealings_data.get("generated_at"),
        build_sha=build_sha,
        nav_links=[
            ("Small Cap", "performance_small.html"),
            ("Large Cap", "performance_large.html"),
            ("All",       "performance.html"),
            ("Baskets",   "baskets.html"),
            ("Review",    "/review"),
        ],
    )


def render_to_file(signals_path: Path, dealings_path: Path,
                   out_path: Path, build_sha: str = "local",
                   health_panel_html: str = "") -> int:
    signals_data = json.loads(Path(signals_path).read_text(encoding="utf-8"))
    dealings_data = json.loads(Path(dealings_path).read_text(encoding="utf-8"))
    html_text = render(signals_data, dealings_data, build_sha=build_sha,
                       health_panel_html=health_panel_html)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(html_text, encoding="utf-8")
    import os
    os.replace(tmp, out_path)
    return len(html_text.encode("utf-8"))
