"""Render outputs/companies/{TICKER}.html -- per-ticker detail.

Inputs: a `company` dict pre-baked by build_dashboard.py from the DB +
backtest CSV. Shape:

    company = {
        "ticker": "DNLM",
        "company": "Dunelm Group plc",
        "sector": "Consumer Discretionary",
        "is_aim": False,
        "latest_close": 1234.0,
        "prev_close": 1224.0,
        "generated_at": "2026-05-14T16:36:13Z",
        "active_cluster": { ... } | None,
        "recent_firing": { ... } | None,
        "prices": [{"date": "...", "close": 100.0, "volume": 1234, "bench": 5000.0}, ...],
        "transactions": [{
            "fingerprint":..., "date":..., "director":..., "role":...,
            "txn_type":..., "shares":..., "price":..., "value":...,
            "signals":[...], "url":..., "announced_at":...
        }, ...],
        "firings": [{
            "fired_at":..., "signal_id":..., "director":..., "car_t1":...,
            "car_t30":..., "car_t90":..., "car_t365":..., "entry_date":..., ...
        }, ...],
        "clusters": [{
            "cluster_id":..., "first_buy_date":..., "last_buy_date":...,
            "director_count":..., "aggregate_value":..., "active":bool
        }, ...]
    }
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone, date as date_cls
from pathlib import Path

from . import render_helpers as h
from . import templates


def _fmt_date_human(s, today_iso: str | None = None) -> str:
    """Format a date for company-page display.

    B-010: when `today_iso` is supplied (e.g. for the transactions
    table) and the row's date is today, render "Today" instead of the
    raw date. Older rows / callers that don't supply today fall
    through to the existing "%d %b %Y" format.
    """
    if not s:
        return "-"
    iso_prefix = str(s)[:10] if isinstance(s, str) else ""
    if today_iso and iso_prefix == today_iso:
        return "Today"
    try:
        d = h.parse_iso_date(s)
        return d.strftime("%d %b %Y") if d else h.esc(s)
    except Exception:
        return h.esc(s)


def _market_cap_chip(market_cap_gbp) -> str:
    """Sprint 26 B-097: render a market-cap chip for the company header."""
    if market_cap_gbp is None:
        return ""
    try:
        mc = float(market_cap_gbp)
    except (TypeError, ValueError):
        return ""
    if mc >= 1_000_000_000:
        label = f"&pound;{mc / 1_000_000_000:.1f}bn"
    elif mc >= 1_000_000:
        label = f"&pound;{mc / 1_000_000:.0f}m"
    else:
        label = f"&pound;{mc / 1_000:.0f}k"
    return (
        f'<span class="text-[10px] px-2 py-0.5 rounded bg-blue-50 text-blue-700 ml-2" '
        f'title="Market cap">mktcap {label}</span>'
    )


def _reporting_date_badge(next_reporting_date: str | None, today_iso: str,
                          is_est: bool = False) -> str:
    """Sprint 26 B-096: yellow badge when a results date is within 60 days.
    Appends '(est)' when is_est=True (synthetic estimate, not a confirmed date).
    """
    if not next_reporting_date:
        return ""
    try:
        rd = date_cls.fromisoformat(next_reporting_date)
        tod = date_cls.fromisoformat(today_iso)
        days = (rd - tod).days
    except (ValueError, TypeError):
        return ""
    if days < 0 or days > 60:
        return ""
    if days == 0:
        label = "Results today"
    elif days == 1:
        label = "Results tomorrow"
    else:
        label = f"Results in {days}d"
    if is_est:
        label += " (est)"
    est_note = " — estimated date, not confirmed" if is_est else ""
    return (
        f'<span class="text-[10px] px-2 py-0.5 rounded bg-amber-100 text-amber-800 ml-2 '
        f'font-medium" title="Upcoming results on {h.esc(next_reporting_date)}{est_note}">'
        f'&#9888; {label}</span>'
    )


def _website_link(website_url: str | None, ticker: str) -> str:
    """Sprint 26 B-101: link to company website (from Yahoo enrichment)."""
    if website_url:
        return (
            f'<a href="{h.esc(website_url)}" target="_blank" rel="noopener" '
            f'class="text-[10px] px-2 py-0.5 rounded border border-slate-200 '
            f'text-slate-500 hover:text-indigo-600 hover:border-indigo-400 ml-2" '
            f'title="{h.esc(website_url)}">Website &#8599;</a>'
        )
    # Fallback: Google search link for the company.
    search_q = urllib.parse.quote_plus(ticker + " investor relations")
    return (
        f'<a href="https://www.google.com/search?q={search_q}" '
        f'target="_blank" rel="noopener" '
        f'class="text-[10px] px-2 py-0.5 rounded border border-slate-200 '
        f'text-slate-400 hover:text-indigo-500 ml-2" title="Search investor relations">IR &#8599;</a>'
    )


def _header(company: dict) -> str:
    ticker = h.esc(company.get("ticker"))
    name = h.esc(company.get("company") or "")
    sector = company.get("sector") or "Unknown sector"
    is_aim = bool(company.get("is_aim"))
    latest = company.get("latest_close")
    prev = company.get("prev_close")
    today_iso = datetime.now(timezone.utc).date().isoformat()
    aim_badge = (
        '<span class="text-[10px] px-2 py-0.5 rounded bg-amber-100 text-amber-700 ml-2">AIM</span>'
        if is_aim else
        '<span class="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-700 ml-2">Main</span>'
    )
    sector_chip = (
        f'<span class="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-700 ml-2">'
        f'{h.esc(sector)}</span>'
    )
    # Sprint 26 enrichment chips.
    mktcap_chip = _market_cap_chip(company.get("market_cap_gbp"))
    website_html = _website_link(company.get("website_url"), company.get("ticker") or "")
    reporting_badge = _reporting_date_badge(
        company.get("next_reporting_date"), today_iso,
        is_est=bool(company.get("next_reporting_is_est")),
    )
    if latest is None:
        price_html = '<div class="text-sm tabular-nums">Latest close: -</div>'
    else:
        try:
            if prev is not None and prev != 0:
                dp = (latest - prev) / prev * 100.0
            else:
                dp = None
        except Exception:
            dp = None
        if dp is None:
            delta_html = '<span class="text-slate-400 ml-2">-</span>'
        elif dp > 0:
            delta_html = f'<span class="text-emerald-600 ml-2">&#9650; +{dp:.2f}%</span>'
        elif dp < 0:
            delta_html = f'<span class="text-rose-600 ml-2">&#9660; {dp:.2f}%</span>'
        else:
            delta_html = '<span class="text-slate-500 ml-2">0.00%</span>'
        price_html = (f'<div class="text-sm tabular-nums">Latest close: '
                      f'{latest:.2f}p {delta_html}</div>')
    gen_at = company.get("generated_at") or ""
    return (
        '<header class="border-b border-slate-200 bg-white px-6 py-4">'
        '<div class="flex items-center justify-between mb-1">'
        '<div class="flex items-center flex-wrap gap-y-1">'
        '<a href="../index.html" class="text-xs text-indigo-600 hover:text-indigo-700 mr-4">'
        '&larr; Dashboard</a>'
        f'<h1 class="text-lg font-semibold text-slate-900 tabular-nums">{ticker}</h1>'
        f'<span class="text-sm text-slate-600 ml-2">{name}</span>'
        f'{sector_chip}{aim_badge}{mktcap_chip}{reporting_badge}'
        '</div>'
        f'<div class="flex items-center">{website_html}</div>'
        '</div>'
        '<div class="flex items-center justify-between">'
        f'{price_html}'
        f'<div class="text-[10px] text-slate-400">Last refresh: {h.esc(gen_at)}</div>'
        '</div>'
        '</header>'
    )


def _banner(company: dict) -> str:
    ac = company.get("active_cluster")
    if ac:
        kind = "Active S1 cluster" if ac.get("s1_active") else "Brewing cluster"
        cls = ("bg-emerald-50 border-emerald-500 text-emerald-800"
               if ac.get("s1_active") else
               "bg-amber-50 border-amber-500 text-amber-800")
        return (f'<div class="{cls} border-l-4 px-6 py-3 text-sm">'
                f'{kind} &middot; {int(ac.get("director_count") or 0)} directors '
                f'&middot; &pound;{(ac.get("aggregate_value_gbp") or 0)/1000:.1f}k '
                f'&middot; {h.esc(ac.get("first_buy_date") or "")} - '
                f'{h.esc(ac.get("last_buy_date") or "")}</div>')
    rf = company.get("recent_firing")
    if rf:
        return (f'<div class="bg-indigo-50 border-l-4 border-indigo-500 text-indigo-800 '
                f'px-6 py-3 text-sm">{h.esc(rf.get("signal_id") or "").upper()} fired '
                f'{int(rf.get("days_ago") or 0)} days ago - {h.esc(rf.get("director") or "")} '
                f'&pound;{int(rf.get("value") or 0):,}</div>')
    return ""


def _price_chart_section(company: dict) -> str:
    prices = company.get("prices") or []
    if not prices:
        return (
            '<section class="m-6 bg-white border border-slate-200 rounded-lg p-4">'
            '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3">Price</h2>'
            f'{h.empty_state("No price history available for " + h.esc(company.get("ticker") or ""), py=12)}'
            '</section>'
        )
    # Pre-marshal data for client-side Chart.js (lightweight slice in payload).
    dates = [p.get("date") for p in prices]
    closes = [p.get("close") for p in prices]
    volumes = [p.get("volume") for p in prices]
    # Annotations from transactions: only those whose date falls within the price range.
    # U4 vivid marker palette: exec buy=green-600, NED buy=green-400,
    # sell=red-600, grant/SIP/exercise=amber-600, cluster ring=violet-600.
    txn_markers = []
    if dates:
        date_set = set(dates)
        for t in (company.get("transactions") or []):
            d = (t.get("date") or "")[:10]
            if d in date_set:
                # B-025 Phase A: bucket-driven exec vs NED classification.
                # Falls back to the original substring heuristic on raw `role`
                # when `role_normalized` is missing (old cached payloads).
                bucket = t.get("role_normalized")
                role = (t.get("role") or "").lower()
                # Buckets that count as "exec buy" (saturated green).
                _EXEC_BUCKETS = {
                    "CEO", "CFO", "Other Chief", "Chair (executive)",
                    "Founder", "Executive Director", "Divisional / Regional Exec",
                    "President / VP",
                }
                # Buckets that count as "NED buy" (lighter green).
                _NED_BUCKETS = {"NED", "Non-Exec Chair"}
                ttype = (t.get("txn_type") or "").upper()
                if ttype == "BUY":
                    if bucket in _EXEC_BUCKETS:
                        color = "#16a34a"   # exec buy green-600
                        mtype = "exec_buy"
                    elif bucket in _NED_BUCKETS:
                        color = "#4ade80"   # NED buy green-400
                        mtype = "ned_buy"
                    elif bucket is None:
                        # Fallback to substring matching (old payloads)
                        if any(k in role for k in ("ceo", "cfo", "chief", "chair")):
                            color = "#16a34a"
                            mtype = "exec_buy"
                        elif "ned" in role or "non-exec" in role or "non exec" in role:
                            color = "#4ade80"
                            mtype = "ned_buy"
                        else:
                            color = "#16a34a"
                            mtype = "exec_buy"
                    else:
                        # PCA, PDMR-only, Other, Parser fragment → exec colour
                        # is wrong; use NED-style lighter green
                        color = "#4ade80"
                        mtype = "ned_buy"
                elif ttype == "SELL":
                    color = "#dc2626"       # sell red-600
                    mtype = "sell"
                else:
                    color = "#d97706"       # grant/SIP/exercise amber-600
                    mtype = "grant"
                txn_markers.append({
                    "date": d, "type": ttype, "mtype": mtype, "color": color,
                    "director": t.get("director") or "",
                    "role": t.get("role") or "",
                    "role_normalized": t.get("role_normalized"),
                    "shares": t.get("shares") or 0,
                    "price": t.get("price") or 0,
                    "value": t.get("value") or 0,
                    "signals": t.get("signals") or [],
                    "fingerprint": t.get("fingerprint") or "",
                })
        # S1 cluster ring markers: one ring per S1 firing whose date is in the
        # price window. Placed on top of any existing transaction marker.
        cluster_dates_seen: set = set()
        for f in (company.get("firings") or []):
            if f.get("signal_id") == "s1":
                fd = (f.get("fired_at") or f.get("entry_date") or "")[:10]
                if fd and fd in date_set and fd not in cluster_dates_seen:
                    cluster_dates_seen.add(fd)
                    txn_markers.append({
                        "date": fd, "type": "CLUSTER", "mtype": "cluster",
                        "color": "#7c3aed",
                        "director": "", "role": "", "shares": 0,
                        "price": 0, "value": 0,
                        "signals": ["s1"], "fingerprint": "",
                    })
    # Earnings date markers: all reporting dates (past + future) within the
    # price window.  Confirmed = amber dashed line; est = grey dashed line.
    earnings_markers = []
    if dates:
        date_set = set(dates)
        for rd in (company.get("all_reporting_dates") or []):
            if rd.get("date") in date_set:
                earnings_markers.append({
                    "date": rd["date"],
                    "confidence": rd.get("confidence", "confirmed"),
                    "type": rd.get("type", "EARNINGS"),
                })
    payload = {
        "dates": dates, "closes": closes, "volumes": volumes,
        "markers": txn_markers, "earnings_markers": earnings_markers,
    }
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg p-4">'
        '<div class="flex justify-between items-center mb-3">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500">Price (trailing window)</h2>'
        # Sprint 11 A.3: user-toggleable marker filter. Default Buy+Sell on,
        # Other off. Cluster rings always visible (Gate 1 Q4). Click toggles
        # the corresponding chart dataset visibility. Per-visit only — no
        # localStorage. All filter logic is inline JS; no shared utilities
        # touched.
        '<div class="flex flex-wrap gap-1 items-center text-[10px]">'
        '<span class="text-slate-400 mr-1 uppercase tracking-wide">Markers:</span>'
        '<button type="button" data-mtype="buy" '
        'class="chart-marker-filter chart-marker-filter--active '
        'px-2 py-0.5 rounded border border-emerald-600 bg-emerald-600 text-white">'
        'Buy</button>'
        '<button type="button" data-mtype="sell" '
        'class="chart-marker-filter chart-marker-filter--active '
        'px-2 py-0.5 rounded border border-rose-600 bg-rose-600 text-white">'
        'Sell</button>'
        '<button type="button" data-mtype="other" '
        'class="chart-marker-filter '
        'px-2 py-0.5 rounded border border-slate-300 bg-white text-slate-500 hover:bg-slate-50">'
        'Other</button>'
        '</div>'
        '</div>'
        '<div class="relative h-72"><canvas id="priceChart"></canvas></div>'
        '<div class="relative h-16 mt-1"><canvas id="volumeChart"></canvas></div>'
        f'<script>window.__priceData = {json.dumps(payload, separators=(",", ":"))};</script>'
        '</section>'
    )


def _transactions_table(company: dict, today: date_cls) -> str:
    rows = company.get("transactions") or []
    if not rows:
        return (
            '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
            '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
            'border-b border-slate-100">Transactions</h2>'
            f'{h.empty_state("No PDMR transactions on file for this ticker.", py=8)}'
            '</section>'
        )
    today_iso = today.isoformat()
    # B-098: latest close for gross return calculation vs deal price.
    latest_close = company.get("latest_close")
    # B113: build a date→bench_close lookup from prices for the Bmk column.
    _prices = company.get("prices") or []
    _bench_by_date = {}
    _bench_dates_sorted = []
    for _p in _prices:
        _pd = _p.get("date") or ""
        _bv = _p.get("bench")
        if _pd and _bv is not None:
            try:
                _bench_by_date[_pd] = float(_bv)
                _bench_dates_sorted.append(_pd)
            except (TypeError, ValueError):
                pass
    # bench_latest = most recent bench value in the prices series.
    _bench_latest = _bench_by_date.get(_bench_dates_sorted[-1]) if _bench_dates_sorted else None

    def _bench_entry_for_date(txn_date_str: str):
        """Return bench close on txn_date or the next available date."""
        if not _bench_dates_sorted or txn_date_str > _bench_dates_sorted[-1]:
            return None
        for d in _bench_dates_sorted:
            if d >= txn_date_str:
                return _bench_by_date.get(d)
        return None

    rendered = []
    # Sprint 11 A.1: collect distinct transaction types as we render so we
    # can emit one filter chip per type seen in THIS company's rows. Empty
    # / unknown types collapse to "OTHER".
    distinct_types: set = set()
    for t in rows:
        fp = h.esc(t.get("fingerprint") or "")
        date_human = _fmt_date_human(t.get("date") or t.get("announced_at"),
                                     today_iso=today_iso)
        ttype = (t.get("txn_type") or "").upper()
        ttype_data = ttype or "OTHER"  # Sprint 11 A.1: data-txn-type attribute
        distinct_types.add(ttype_data)
        if ttype == "BUY":
            tcls = "bg-emerald-100 text-emerald-700"
        elif ttype == "SELL":
            tcls = "bg-rose-100 text-rose-700"
        else:
            tcls = "bg-slate-100 text-slate-500"
        ttype_html = (f'<span class="px-1.5 py-0.5 rounded text-[10px] '
                      f'font-semibold {tcls}">{h.esc(ttype or "-")}</span>')
        shares = t.get("shares") or 0
        price = t.get("price") or 0
        value = t.get("value") or 0
        # B-098: gross return vs deal price (not vs T+1 close like Today page).
        txn_date_str = (t.get("date") or t.get("announced_at") or "")[:10]
        try:
            if latest_close and price and float(price) > 0:
                abs_ret_pct = round((float(latest_close) / float(price) - 1.0) * 100.0, 1)
                rtn_glyph = "&#9650;" if abs_ret_pct > 0 else ("&#9660;" if abs_ret_pct < 0 else "")
                rtn_cls = h.car_color_class(abs_ret_pct)
                rtn_cell = (f'<span class="{rtn_cls}">{rtn_glyph} {h.esc(h.pct(abs_ret_pct))}</span>'
                            if ttype == "BUY" else
                            f'<span class="text-slate-400 text-[10px]">-</span>')
            else:
                rtn_cell = '<span class="text-slate-400">-</span>'
        except (TypeError, ValueError):
            rtn_cell = '<span class="text-slate-400">-</span>'
        # B113: benchmark return over same period as Rtn (BUY rows only).
        try:
            if ttype == "BUY" and txn_date_str and _bench_latest is not None:
                _be = _bench_entry_for_date(txn_date_str)
                if _be and float(_be) > 0:
                    bench_ret_pct = round((float(_bench_latest) / float(_be) - 1.0) * 100.0, 1)
                    bench_cell = h.car_cell(bench_ret_pct)
                else:
                    bench_cell = '<span class="text-slate-400">-</span>'
            else:
                bench_cell = '<span class="text-slate-400 text-[10px]">-</span>'
        except (TypeError, ValueError):
            bench_cell = '<span class="text-slate-400">-</span>'
        url = t.get("url") or ""
        if url:
            src = (f'<a href="{h.esc(url)}" target="_blank" rel="noopener" '
                   f'class="text-slate-400 hover:text-indigo-600" title="{h.esc(url)}">&#8599;</a>')
        else:
            src = ('<span class="text-slate-200 cursor-not-allowed" '
                   'title="no RNS link on file">&#8599;</span>')
        # Sprint 25 Phase 0: pencil icon → PDMR review surface (read-only).
        # Points to /review?tab=b&fp={fingerprint} so the side-by-side viewer
        # pre-loads the correct transaction. The link is relative to the server
        # root (works whether the page is served from /companies/*.html or /).
        review_link = (
            f'<a href="/review?tab=b&fp={h.esc(fp)}" '
            f'class="text-slate-300 hover:text-indigo-500" '
            f'title="Open in PDMR review">&#9998;</a>'
            if fp else ""
        )
        # Sprint 11 A.1: data-txn-type drives the inline-JS filter visibility.
        # Sprint 26 B-096: near-results badge on transaction row.
        near_rd = t.get("near_reporting_date")
        if near_rd:
            try:
                rd = date_cls.fromisoformat(near_rd)
                txn_d = date_cls.fromisoformat((t.get("date") or "")[:10])
                days_before = (rd - txn_d).days
                _near_est = bool(t.get("near_reporting_est"))
                _near_label = "~results (est)" if _near_est else "~results"
                _near_title = (
                    f"Results estimated ~{days_before} days after this transaction"
                    if _near_est else
                    f"Results announced ~{days_before} days after this transaction"
                )
                near_badge = (
                    f'<span class="inline-block text-[9px] px-1.5 py-0.5 rounded '
                    f'bg-amber-100 text-amber-800 font-medium" '
                    f'title="{_near_title}">'
                    f'{_near_label}</span>'
                )
            except (ValueError, TypeError):
                near_badge = ""
        else:
            near_badge = ""
        # B-120: unverified-price marker (B-060 audit) on the value cell.
        unverified_chip = (
            ' <span class="inline-flex items-center text-[9px] px-1 py-0.5 '
            'rounded bg-slate-200 text-slate-600 font-semibold uppercase '
            'tracking-wide align-middle" title="Price could not be verified '
            'against market data (B-060 audit) - value is unconfirmed">'
            '&#9888; unverified</span>'
            if t.get("unverified") else ""
        )
        rendered.append(
            f'<tr id="txn-{fp}" data-txn-type="{h.esc(ttype_data)}" '
            f'class="txn-row border-t border-slate-100">'
            f'<td class="px-3 py-2 text-slate-600">'
            f'{date_human}{(" " + near_badge) if near_badge else ""}</td>'
            f'<td class="px-3 py-2 text-slate-700">{h.esc(t.get("director") or "-")}</td>'
            f'<td class="px-3 py-2">{h.role_chip(t.get("role") or "", t.get("role_normalized"))}</td>'
            f'<td class="px-3 py-2">{ttype_html}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{int(shares):,}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{float(price):.2f}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{h.gbp(value)}{unverified_chip}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{rtn_cell}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{bench_cell}</td>'
            f'<td class="px-3 py-2">{h.render_badges_row(t.get("signals") or [])}</td>'
            f'<td class="px-3 py-2 text-center">{src}</td>'
            f'<td class="px-3 py-2 text-center">{review_link}</td>'
            f'</tr>'
        )
    body = "".join(rendered)
    # Sprint 11 A.1: filter-chip strip + inline JS. One chip per distinct
    # type encountered. All chips start active (Gate 1 Q1). Click toggles
    # row visibility by `data-txn-type` attribute. Per-visit only — no
    # localStorage (Gate 1 Q2). Independent from chart filter (Q3-extra).
    type_order = ["BUY", "SELL"]  # show Buy/Sell first if present
    sorted_types = ([t for t in type_order if t in distinct_types]
                    + sorted(t for t in distinct_types if t not in type_order))
    chip_classes = {
        "BUY":  "bg-emerald-600 text-white border-emerald-600",
        "SELL": "bg-rose-600 text-white border-rose-600",
    }
    default_other_classes = "bg-slate-600 text-white border-slate-600"
    chip_html = "".join(
        f'<button type="button" data-txn-filter="{h.esc(tk)}" '
        f'class="txn-filter txn-filter--active '
        f'px-2 py-0.5 rounded border text-[10px] '
        f'{chip_classes.get(tk, default_other_classes)}">{h.esc(tk.title())}</button>'
        for tk in sorted_types
    )
    filter_bar_html = (
        '<div class="px-4 py-2 flex flex-wrap gap-1 items-center '
        'border-b border-slate-100 bg-slate-50/50">'
        '<span class="text-[10px] uppercase tracking-wide text-slate-400 mr-1">Filter:</span>'
        f'{chip_html}'
        '</div>'
    )
    filter_js = (
        '<script>(function(){'
        'var btns = document.querySelectorAll(".txn-filter");'
        'var rowsEl = document.querySelectorAll(".txn-row");'
        'var state = {};'
        'btns.forEach(function(b){'
        '  state[b.getAttribute("data-txn-filter")] = true;'
        '  b.addEventListener("click", function(){'
        '    var k = b.getAttribute("data-txn-filter");'
        '    state[k] = !state[k];'
        '    b.classList.toggle("txn-filter--active");'
        '    if (!state[k]) {'
        '      b.classList.add("opacity-40");'
        '    } else {'
        '      b.classList.remove("opacity-40");'
        '    }'
        '    rowsEl.forEach(function(r){'
        '      var rk = r.getAttribute("data-txn-type");'
        '      r.style.display = state[rk] === false ? "none" : "";'
        '    });'
        '  });'
        '});'
        '})();</script>'
    )
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
        'border-b border-slate-100">Transactions</h2>'
        # Sprint 11 A.1: filter-chip strip + inline JS wiring (defined above).
        f'{filter_bar_html}'
        '<table class="w-full text-xs tabular-nums">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left">Date</th>'
        '<th class="px-3 py-2 text-left">Director</th>'
        '<th class="px-3 py-2 text-left">Role</th>'
        '<th class="px-3 py-2 text-left">Type</th>'
        '<th class="px-3 py-2 text-right">Shares</th>'
        '<th class="px-3 py-2 text-right">Price (&pound;)</th>'
        '<th class="px-3 py-2 text-right">Value (&pound;)</th>'
        '<th class="px-3 py-2 text-right" title="B-098: Gross stock return vs the director\'s deal price. Raw stock move only, no benchmark or cost adjustment.">Rtn*</th>'
        '<th class="px-3 py-2 text-right" title="B113: Sector benchmark return over the same period as Rtn (deal date to latest close). BUY rows only.">Bmk**</th>'
        '<th class="px-3 py-2 text-left">Signals</th>'
        '<th class="px-3 py-2 text-center">Source</th>'
        '<th class="px-3 py-2 text-center" title="Open in PDMR review">Review</th>'
        '</tr></thead>'
        f'<tbody>{body}</tbody></table>'
        '<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">'
        '*Rtn: gross stock return from director\'s deal price to today\'s close (BUY rows only). '
        '**Bmk: sector benchmark return over the same period. '
        'Neither adjusts for costs. '
        'See Signal-firing history below for CAR, which uses market entry price and a fixed past horizon &mdash; '
        'divergence between Rtn and CAR is expected.'
        '</div>'
        f'{filter_js}'
        '</section>'
    )


def _firings_table(company: dict, today: date_cls) -> str:
    rows = company.get("firings") or []
    if not rows:
        return (
            '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
            '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
            'border-b border-slate-100">Signal-firing history</h2>'
            f'{h.empty_state("No signals have fired for this ticker.", py=8)}'
            '</section>'
        )
    horizons = [("car_t1", 1), ("car_t30", 21), ("car_t90", 63), ("car_t365", 252)]
    rendered = []
    n = 0
    hit_count = 0
    median_vals = []
    for r in rows:
        n += 1
        sid = r.get("signal_id") or ""
        entry_date = h.parse_iso_date(r.get("entry_date") or r.get("fired_at"))
        cells = []
        for key, h_days in horizons:
            val = r.get(key)
            if val is None:
                if entry_date is not None:
                    mature_on = entry_date.toordinal() + h_days
                    md = date_cls.fromordinal(mature_on)
                    cells.append(f'<td class="px-3 py-2 text-right tabular-nums">'
                                 f'<span class="text-slate-300" '
                                 f'title="Matures {md.isoformat()}">-</span></td>')
                else:
                    cells.append('<td class="px-3 py-2 text-right tabular-nums">'
                                 '<span class="text-slate-300">-</span></td>')
            else:
                cells.append(f'<td class="px-3 py-2 text-right tabular-nums">'
                             f'{h.car_cell(val)}</td>')
            if key == "car_t30" and val is not None:
                median_vals.append(val)
                if val > 0:
                    hit_count += 1
        rendered.append(
            f'<tr class="border-t border-slate-100">'
            f'<td class="px-3 py-2 text-slate-600">{_fmt_date_human(r.get("fired_at"))}</td>'
            f'<td class="px-3 py-2">{h.render_badge(sid)}</td>'
            f'<td class="px-3 py-2 text-slate-700">{h.esc(r.get("director") or "-")}</td>'
            + "".join(cells)
            + '</tr>'
        )
    # Stats card.
    if n < 5:
        stats = (f'<div class="bg-slate-50 border-t border-slate-100 px-4 py-3 '
                 f'text-xs text-slate-400 italic">Not enough firings (N={n}) for '
                 f'a meaningful per-ticker stat.</div>')
    else:
        hit_pct = (hit_count / max(1, len(median_vals)) * 100.0) if median_vals else None
        median_v = sorted(median_vals)[len(median_vals) // 2] if median_vals else None
        hit_html = (f'<span class="font-medium tabular-nums '
                    f'{h.car_color_class(hit_pct)}">{hit_pct:.1f}%</span>'
                    if hit_pct is not None else '<span class="text-slate-400">-</span>')
        med_html = (h.car_cell(median_v) if median_v is not None
                    else '<span class="text-slate-400">-</span>')
        stats = (
            '<div class="bg-slate-50 border-t border-slate-100 px-4 py-3 text-xs flex gap-6">'
            f'<div><span class="text-slate-500">Firings:</span> '
            f'<span class="font-medium tabular-nums">{n}</span></div>'
            f'<div><span class="text-slate-500">Hit % @ T+30:</span> {hit_html}</div>'
            f'<div><span class="text-slate-500">Median CAR @ T+30:</span> {med_html}</div>'
            '</div>'
        )
    body = "".join(rendered)
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
        'border-b border-slate-100">Signal-firing history</h2>'
        '<table class="w-full text-xs tabular-nums">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left">Fired</th>'
        '<th class="px-3 py-2 text-left">Signal</th>'
        '<th class="px-3 py-2 text-left">Director</th>'
        '<th class="px-3 py-2 text-right" '
        'title="Net CAR at T+1: cumulative abnormal return vs sector benchmark at 1 trading day '
        'after market entry. Entry = first market close after announcement date. '
        'Net of 50-100bps trading costs.">T+1</th>'
        '<th class="px-3 py-2 text-right" '
        'title="Net CAR at T+30: ~1 month (21 trading days) from market entry. '
        'Entry = first market close after announcement. Net of costs.">T+30</th>'
        '<th class="px-3 py-2 text-right" '
        'title="Net CAR at T+90: ~3 months (63 trading days) from market entry. '
        'Entry = first market close after announcement. Net of costs.">T+90</th>'
        '<th class="px-3 py-2 text-right" '
        'title="Net CAR at T+365: ~1 year (252 trading days) from market entry. '
        'Entry = first market close after announcement. Net of costs.">T+365</th>'
        '</tr></thead>'
        f'<tbody>{body}</tbody></table>'
        f'{stats}'
        '<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">'
        'CAR = Cumulative Abnormal Return vs sector benchmark, net of trading costs '
        '(50bps AIM / 100bps main market). '
        'Entry price = market close on first trading day after announcement &mdash; '
        '<em>not</em> the director\'s deal price. '
        'T+N is a fixed past horizon, not today. '
        'The Rtn column in Transactions uses the director\'s deal price as entry and '
        'today\'s close as exit &mdash; different start, different end, so divergence '
        'from CAR is normal and expected.'
        '</div>'
        '</section>'
    )


def _clusters_table(company: dict) -> str:
    rows = company.get("clusters") or []
    if not rows:
        return (
            '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
            '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
            'border-b border-slate-100">Cluster history</h2>'
            f'{h.empty_state("No clusters detected for this ticker.", py=8)}'
            '</section>'
        )
    rendered = []
    for c in rows:
        status = c.get("active")
        if status:
            pill = '<span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-700 text-[10px]">Active</span>'
        else:
            pill = '<span class="px-2 py-0.5 rounded bg-slate-100 text-slate-600 text-[10px]">Historical</span>'
        rendered.append(
            '<tr class="border-t border-slate-100">'
            f'<td class="px-3 py-2 text-slate-600">{h.esc(c.get("cluster_id") or "-")}</td>'
            f'<td class="px-3 py-2 text-slate-700">{_fmt_date_human(c.get("first_buy_date"))} - '
            f'{_fmt_date_human(c.get("last_buy_date"))}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{int(c.get("director_count") or 0)}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{h.gbp(c.get("aggregate_value"))}</td>'
            f'<td class="px-3 py-2">{pill}</td>'
            '</tr>'
        )
    body = "".join(rendered)
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
        'border-b border-slate-100">Cluster history</h2>'
        '<table class="w-full text-xs tabular-nums">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left">Cluster ID</th>'
        '<th class="px-3 py-2 text-left">Date range</th>'
        '<th class="px-3 py-2 text-right">Directors</th>'
        '<th class="px-3 py-2 text-right">Aggregate &pound;</th>'
        '<th class="px-3 py-2 text-left">Status</th>'
        '</tr></thead>'
        f'<tbody>{body}</tbody></table>'
        '</section>'
    )


_RECOVERABLE_BADGE_CO = {
    "no":         ("No",            "bg-slate-200 text-slate-700"),
    "v2-fx":      ("v2 (FX)",       "bg-blue-100 text-blue-800"),
    "v2-fanout":  ("v2 (fan-out)",  "bg-blue-100 text-blue-800"),
    "manual":     ("Manual",        "bg-amber-100 text-amber-800"),
    "unknown":    ("Unknown",       "bg-slate-100 text-slate-600"),
}


def _recoverable_badge_html_co(recoverable: str) -> str:
    label, cls = _RECOVERABLE_BADGE_CO.get(
        recoverable, _RECOVERABLE_BADGE_CO["unknown"]
    )
    return (
        '<span class="inline-flex items-center px-1.5 py-0.5 rounded '
        f'text-[10px] font-medium {cls}">{h.esc(label)}</span>'
    )


def _pending_review_section(company: dict) -> str:
    """Render the per-ticker Pending review panel with summary table.

    Reads ``company["pending_review"]`` shape::

        {"total": N,
         "categories": [{"id", "name", "count", "pct",
                         "recoverable", "description"}, ...]}

    Returns "" (omit panel entirely) when there are zero pending items for
    this ticker, or when the key is absent / malformed -- a company page
    should not be cluttered with an empty diagnostics panel.
    """
    pending = company.get("pending_review")
    if not pending or not isinstance(pending, dict):
        return ""
    total = int(pending.get("total") or 0)
    categories = pending.get("categories") or []
    if total <= 0 or not categories:
        return ""

    ticker = h.esc(company.get("ticker") or "")

    rows_html: list[str] = []
    for cat in categories:
        count = int(cat.get("count") or 0)
        if count <= 0:
            continue  # Per-ticker panel hides empty buckets.
        name = h.esc(cat.get("name") or "Other")
        pct = cat.get("pct")
        try:
            pct_f = float(pct) if pct is not None else 0.0
        except (TypeError, ValueError):
            pct_f = 0.0
        recoverable = (cat.get("recoverable") or "unknown").lower()
        desc = cat.get("description") or ""
        rows_html.append(
            '<tr class="border-t border-slate-100 odd:bg-slate-50">'
            f'<td class="px-3 py-2 text-slate-700" title="{h.esc(desc)}">'
            f'{name}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums text-slate-700">'
            f'{count:,}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums text-slate-700">'
            f'{pct_f:.1f}%</td>'
            f'<td class="px-3 py-2">'
            f'{_recoverable_badge_html_co(recoverable)}</td>'
            '</tr>'
        )

    if not rows_html:
        return ""

    plural = "s" if total != 1 else ""
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg '
        'overflow-hidden">'
        '<h2 class="px-4 py-3 text-xs uppercase tracking-wide text-slate-500 '
        'border-b border-slate-100">'
        f'Pending review &mdash; {total:,} {ticker} filing{plural} '
        'excluded from signals'
        '</h2>'
        '<p class="px-4 pt-3 pb-1 text-xs text-slate-600 leading-relaxed">'
        '&#8627; PDMR announcements for this ticker the parser couldn\'t '
        'cleanly extract &mdash; usually genuine edge cases. Summary below.'
        '</p>'
        '<table class="w-full text-xs tabular-nums" id="pendingDiagTicker">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide '
        'text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left w-[55%]">Category</th>'
        '<th class="px-3 py-2 text-right w-[12%]">Count</th>'
        '<th class="px-3 py-2 text-right w-[10%]">%</th>'
        '<th class="px-3 py-2 text-left w-[23%]">Recoverable?</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table>'
        '<div class="text-[10px] text-slate-500 px-4 py-2 border-t '
        'border-slate-100">'
        'Recoverable column flags which buckets v2 parser work could unlock '
        'vs. which are case-by-case manual review.'
        '</div>'
        '</section>'
    )


def _chart_js_for_company() -> str:
    return """
<script>
(function(){
  const d = window.__priceData || {};
  const dates = d.dates || [];
  if (!dates.length) return;
  const closes = d.closes || [];
  const volumes = d.volumes || [];
  const markers = d.markers || [];

  // Build close-by-date map for marker yValue.
  const closeMap = {};
  dates.forEach(function(dt, i){ closeMap[dt] = closes[i]; });

  // U4 vivid marker palette — keyed by mtype.
  // exec_buy/ned_buy: triangle-up, white halo.
  // sell: triangle-down, white halo.
  // grant: square (rect), white halo.
  // cluster: circle ring, violet stroke, no fill, drawn BEHIND transactions.
  //
  // B-053 (2026-05-22): radii bumped ~40-50% over the original 5-7 set so
  // markers stand out more clearly against the price line. Cluster ring
  // scaled proportionally so it still encircles the transaction markers
  // without overlapping its neighbours.
  var MSTYLE = {
    exec_buy: { label:'Exec Buy',   bg:'#16a34a',        bc:'#ffffff', bw:3,   ps:'triangle', rot:0,   r:10, ord:1 },
    ned_buy:  { label:'NED Buy',    bg:'#4ade80',        bc:'#ffffff', bw:3,   ps:'triangle', rot:0,   r:9,  ord:1 },
    sell:     { label:'Sell',       bg:'#dc2626',        bc:'#ffffff', bw:3,   ps:'triangle', rot:180, r:10, ord:1 },
    grant:    { label:'Grant/SIP',  bg:'#d97706',        bc:'#ffffff', bw:2.5, ps:'rect',     rot:0,   r:8,  ord:1 },
    cluster:  { label:'S1 Cluster', bg:'rgba(0,0,0,0)',  bc:'#7c3aed', bw:2.5, ps:'circle',   rot:0,   r:14, ord:2 }
  };

  // Group markers by mtype.
  // A.3 — only BUY and SELL markers on the price chart.
  // GRANT / SIP / EXERCISE / DRIP markers (mtype === 'grant') are omitted;
  // cluster rings (mtype === 'cluster') always pass through so they overlay
  // the BUY markers they annotate.
  var CHART_MARKER_TYPES = new Set(['exec_buy', 'ned_buy', 'sell']);
  var markerDatasets = {};
  markers.forEach(function(m){
    var mtype = m.mtype || 'exec_buy';
    if (mtype !== 'cluster' && !CHART_MARKER_TYPES.has(mtype)) return;
    if (!markerDatasets[mtype]){
      var st = MSTYLE[mtype] || MSTYLE.exec_buy;
      markerDatasets[mtype] = {
        label: st.label,
        type: 'scatter',
        data: [],
        backgroundColor: st.bg,
        borderColor: st.bc,
        borderWidth: st.bw,
        pointStyle: st.ps,
        rotation: st.rot,
        pointRotation: st.rot,
        radius: st.r,
        hoverRadius: st.r + 3,
        showLine: false,
        order: st.ord,
        _markers: []
      };
    }
    var y = closeMap[m.date];
    markerDatasets[mtype].data.push({ x: m.date, y: y });
    markerDatasets[mtype]._markers.push(m);
  });

  const pCtx = document.getElementById('priceChart');
  if (!pCtx) return;

  // Price line first (order:0), then transaction markers (order:1),
  // then cluster rings on top (order:2 — drawn last so ring encircles marker).
  var markerList = Object.keys(markerDatasets)
    .sort(function(a,b){ return (markerDatasets[a].order||1)-(markerDatasets[b].order||1); })
    .map(function(k){ return markerDatasets[k]; });

  // Earnings dates as a scatter dataset: diamond at the close price on each
  // reporting date.  Label prefixed '__' so the marker-filter ignores it and
  // the legend (hidden) skips it.  Confirmed = amber, est = slate.
  var _EARNINGS_TYPE_LABELS = {
    'INTERIM':        'Interim Results',
    'FINAL':          'Final Results',
    'PRELIM':         'Preliminary Results',
    'TRADING_UPDATE': 'Trading Update',
    'TRADING_STMT':   'Trading Statement',
    'EARNINGS':       'Earnings',
    'QUARTERLY':      'Quarterly Results'
  };
  var earningsData = (d.earnings_markers || []);
  var earningsDataset = earningsData.length ? {
    label: '__earnings',
    type: 'scatter',
    data: earningsData.map(function(em){ return { x: em.date, y: closeMap[em.date] || null }; }),
    backgroundColor: earningsData.map(function(em){ return em.confidence === 'est' ? '#94a3b8' : '#f59e0b'; }),
    borderColor: '#ffffff',
    borderWidth: 2,
    pointStyle: 'rectRot',  // diamond
    radius: 6,
    hoverRadius: 9,
    showLine: false,
    order: 0.5,
    _earnings: earningsData
  } : null;

  const ds = [{
    label: 'Close',
    type: 'line',
    data: dates.map(function(dt, i){ return { x: dt, y: closes[i] }; }),
    borderColor: '#475569', borderWidth: 1.5, pointRadius: 0, tension: 0, order: 0
  }].concat(earningsDataset ? [earningsDataset] : []).concat(markerList);

  // Inline plugin: draw vertical dashed lines at earnings/results dates.
  // The diamond marker is handled by the scatter dataset above (tooltip-enabled).
  // Confirmed → amber (#f59e0b), est → slate (#94a3b8).
  var earningsLines = {
    id: 'earningsLines',
    afterDraw: function(chart) {
      var em = (d.earnings_markers || []);
      if (!em.length) return;
      var ctx2 = chart.ctx;
      var xScale = chart.scales.x;
      var yScale = chart.scales.y;
      if (!xScale || !yScale) return;
      var top = yScale.top;
      var bottom = yScale.bottom;
      em.forEach(function(ev) {
        var x = xScale.getPixelForValue(ev.date);
        if (x === undefined || isNaN(x)) return;
        var isEst = ev.confidence === 'est';
        var color = isEst ? '#94a3b8' : '#f59e0b';
        ctx2.save();
        ctx2.strokeStyle = color;
        ctx2.lineWidth = 1;
        ctx2.setLineDash(isEst ? [3, 3] : [5, 3]);
        ctx2.beginPath();
        ctx2.moveTo(x, top);
        ctx2.lineTo(x, bottom);
        ctx2.stroke();
        ctx2.restore();
      });
    }
  };

  window.__priceChart = new Chart(pCtx, {
    type: 'line',
    data: { datasets: ds },
    plugins: [earningsLines],
    options: {
      responsive: true, maintainAspectRatio: false,
      parsing: false,
      scales: {
        x: { type: 'category', labels: dates, ticks: { maxRotation: 0, font: { size: 10 } } },
        y: { ticks: { font: { size: 10 } } }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function(ctx){
              // Earnings date marker — show statement type + date.
              if (ctx.dataset.label === '__earnings') {
                var idx = ctx.dataIndex;
                var em = ctx.dataset._earnings[idx] || {};
                var typeStr = _EARNINGS_TYPE_LABELS[em.type] || em.type || 'Results';
                var confStr = em.confidence === 'est' ? ' (est)' : '';
                return [typeStr + confStr, em.date];
              }
              if (ctx.dataset.type !== 'scatter') return ctx.dataset.label + ': ' + ctx.parsed.y;
              var idx = ctx.dataIndex;
              var m = ctx.dataset._markers[idx] || {};
              if (m.mtype === 'cluster'){
                return ['S1 Cluster signal fired', 'Date: ' + m.date];
              }
              var lines = [];
              if (m.director) lines.push(m.director + (m.role ? ' (' + m.role + ')' : ''));
              lines.push(m.type + (m.shares ? ' ' + m.shares + ' sh @ £' + (m.price||0).toFixed(2) : ''));
              if (m.value) lines.push('£' + (m.value||0).toLocaleString());
              if (m.signals && m.signals.length) lines.push('Signal: ' + m.signals.join(', '));
              return lines;
            }
          }
        }
      },
      onClick: function(evt, els){
        if (!els.length) return;
        var el = els[0];
        var dataset = ds[el.datasetIndex];
        if (!dataset || dataset.type !== 'scatter') return;
        var m = dataset._markers[el.index];
        // Cluster rings have no table row — skip scroll.
        if (!m || !m.fingerprint || m.mtype === 'cluster') return;
        var row = document.getElementById('txn-' + m.fingerprint);
        if (row){
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
          row.classList.add('pulse-highlight');
          setTimeout(function(){ row.classList.remove('pulse-highlight'); }, 2000);
        }
      }
    }
  });

  // ── Sprint 11 A.3: marker-filter widget wiring ───────────────────────
  // The chips above the chart toggle visibility of marker datasets by
  // dataset label. Cluster rings (label "S1 Cluster") are excluded
  // from the filter and always remain visible (Gate 1 Q4 decision).
  // State is per-visit only — no localStorage.
  var __markerFilter = { buy: true, sell: true, other: false };
  function __applyMarkerFilter(){
    if (!window.__priceChart) return;
    window.__priceChart.data.datasets.forEach(function(dset){
      if (dset.type !== 'scatter') return;            // skip price line
      if (dset.label === 'S1 Cluster') return;        // always visible (Q4)
      if (dset.label.startsWith('__')) return;        // internal datasets (earnings etc.)
      var hidden;
      if (dset.label === 'Exec Buy' || dset.label === 'NED Buy') {
        hidden = !__markerFilter.buy;
      } else if (dset.label === 'Sell') {
        hidden = !__markerFilter.sell;
      } else if (dset.label === 'Grant/SIP') {
        hidden = !__markerFilter.other;
      } else {
        hidden = !__markerFilter.other;               // unknown -> "other"
      }
      dset.hidden = hidden;
    });
    window.__priceChart.update('none');
  }
  // Initial application — hides Other markers per default state.
  __applyMarkerFilter();
  // Click handlers on the chip buttons.
  document.querySelectorAll('.chart-marker-filter').forEach(function(btn){
    btn.addEventListener('click', function(){
      var key = btn.getAttribute('data-mtype');
      if (!(key in __markerFilter)) return;
      __markerFilter[key] = !__markerFilter[key];
      // Toggle active styling: when ON, coloured fill; when OFF, ghost outline.
      if (__markerFilter[key]) {
        btn.classList.add('chart-marker-filter--active');
        if (key === 'buy') {
          btn.classList.add('bg-emerald-600', 'text-white', 'border-emerald-600');
          btn.classList.remove('bg-white', 'text-slate-500', 'border-slate-300');
        } else if (key === 'sell') {
          btn.classList.add('bg-rose-600', 'text-white', 'border-rose-600');
          btn.classList.remove('bg-white', 'text-slate-500', 'border-slate-300');
        } else {
          btn.classList.add('bg-amber-600', 'text-white', 'border-amber-600');
          btn.classList.remove('bg-white', 'text-slate-500', 'border-slate-300');
        }
      } else {
        btn.classList.remove('chart-marker-filter--active');
        btn.classList.remove('bg-emerald-600', 'bg-rose-600', 'bg-amber-600',
                             'border-emerald-600', 'border-rose-600',
                             'border-amber-600', 'text-white');
        btn.classList.add('bg-white', 'text-slate-500', 'border-slate-300');
      }
      __applyMarkerFilter();
    });
  });

  const vCtx = document.getElementById('volumeChart');
  if (vCtx) {
    window.__volChart = new Chart(vCtx, {
      type: 'bar',
      data: { labels: dates, datasets: [{ data: volumes, backgroundColor: '#cbd5e1' }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false },
          y: { display: false }
        }
      }
    });
  }
})();
</script>
"""


_FILING_TYPE_LABELS: dict[str, str] = {
    "INTERIM":       "Interim Results",
    "FINAL":         "Final Results",
    "PRELIM":        "Preliminary Results",
    "TRADING_UPDATE":"Trading Update",
    "TRADING_STMT":  "Trading Statement",
    "EARNINGS":      "Earnings",
    "QUARTERLY":     "Quarterly Results",
}


def _filings_section(company: dict) -> str:
    """Historic earnings announcements box — date, type, link to filing."""
    all_rd = company.get("all_reporting_dates") or []
    ticker = h.esc(company.get("ticker") or "")
    # Filter to past dates only (anything < today); keep ordering DESC (already from query).
    today_iso = datetime.now(timezone.utc).date().isoformat()
    past = [rd for rd in all_rd if (rd.get("date") or "") <= today_iso]
    if not past:
        return ""

    # Investegate company search fallback URL (used when no source_url on record).
    raw_ticker = company.get("ticker") or ""
    inv_company_url = (
        f"https://www.investegate.co.uk/index.aspx?searchtype=companies&s={urllib.parse.quote(raw_ticker)}"
    )

    rows_html = []
    for rd in past:
        date_str = rd.get("date") or ""
        rtype = (rd.get("type") or "EARNINGS").upper()
        confidence = rd.get("confidence") or "confirmed"
        source_url = rd.get("source_url")

        type_label = _FILING_TYPE_LABELS.get(rtype, rtype.replace("_", " ").title())
        date_human = _fmt_date_human(date_str)

        # Badge colour: amber for confirmed, slate for est.
        if confidence == "est":
            badge_cls = "bg-slate-100 text-slate-500"
            est_suffix = ' <span class="text-[9px] text-slate-400">(est)</span>'
        else:
            badge_cls = "bg-amber-50 text-amber-700"
            est_suffix = ""

        # Link: use source_url if present, else fall back to Investegate company page.
        if source_url:
            link_html = (
                f'<a href="{h.esc(source_url)}" target="_blank" rel="noopener noreferrer" '
                f'class="text-amber-600 hover:text-amber-800 hover:underline text-[11px]">'
                f'View filing &#8599;</a>'
            )
        else:
            link_html = (
                f'<a href="{h.esc(inv_company_url)}" target="_blank" rel="noopener noreferrer" '
                f'class="text-slate-400 hover:text-slate-600 hover:underline text-[11px]">'
                f'Investegate &#8599;</a>'
            )

        rows_html.append(
            f'<tr class="hover:bg-slate-50 border-b border-slate-100 last:border-0">'
            f'<td class="px-4 py-2 text-[11px] text-slate-700 whitespace-nowrap">{h.esc(date_human)}</td>'
            f'<td class="px-4 py-2">'
            f'<span class="px-1.5 py-0.5 rounded text-[10px] font-medium {badge_cls}">'
            f'{h.esc(type_label)}</span>{est_suffix}'
            f'</td>'
            f'<td class="px-4 py-2 text-right">{link_html}</td>'
            f'</tr>'
        )

    rows_joined = "".join(rows_html)
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 '
        'border-b border-slate-100">Earnings History</h2>'
        '<table class="w-full text-left">'
        '<thead>'
        '<tr class="border-b border-slate-100">'
        '<th class="px-4 py-2 text-[10px] uppercase tracking-wide text-slate-400 font-medium">Date</th>'
        '<th class="px-4 py-2 text-[10px] uppercase tracking-wide text-slate-400 font-medium">Statement</th>'
        '<th class="px-4 py-2 text-[10px] uppercase tracking-wide text-slate-400 font-medium text-right">Filing</th>'
        '</tr>'
        '</thead>'
        f'<tbody>{rows_joined}</tbody>'
        '</table>'
        '</section>'
    )


def render(company: dict, build_sha: str = "local") -> str:
    today = datetime.now(timezone.utc).date()
    sections = [
        _banner(company),
        _price_chart_section(company),
        _transactions_table(company, today),
        _firings_table(company, today),
        _clusters_table(company),
        _pending_review_section(company),
        _filings_section(company),
    ]
    sections = [s for s in sections if s]
    footer_extra = (
        '<footer class="px-6 py-4 text-[10px] text-slate-400 border-t border-slate-200">'
        f'<p>Generated by build_dashboard.py {h.esc(company.get("generated_at") or "")}.</p>'
        '<p>Data: Investegate RNS feed &middot; Yahoo Finance &middot; FTSE All-Share benchmark.</p>'
        '<p class="mt-2">Historic insider-trading outcomes are not predictive. Net-of-cost '
        'CAR assumes 50bps round-trip + 0.5% UK stamp on non-AIM buys.</p>'
        '</footer>'
    )
    body = (
        _header(company)
        + "".join(sections)
        + footer_extra
        + _chart_js_for_company()
    )
    # Custom inline title for tab.
    title = f"{company.get('ticker') or ''} - {company.get('company') or ''}"
    return templates.base_page(
        title=title,
        body=body,
        generated_at_iso=company.get("generated_at"),
        build_sha=build_sha,
        nav_links=[
            ("Today",     "../index.html"),
            ("Small Cap", "../performance_small.html"),
            ("Large Cap", "../performance_large.html"),
            ("All",       "../performance.html"),
            ("Baskets",   "../baskets.html"),
            ("Review",    "/review"),
        ],
    )


def render_to_file(company: dict, out_path: Path, build_sha: str = "local") -> int:
    html_text = render(company, build_sha=build_sha)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(html_text, encoding="utf-8")
    import os
    os.replace(tmp, out_path)
    return len(html_text.encode("utf-8"))
