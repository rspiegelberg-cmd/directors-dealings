"""Performance page redesign v1 — drill-down page renderer (FE Sprint 2).

Single parameterised function `render_drilldown_page` that renders all three
cohort drill-down page types (bucket / role / sector). The orchestrator in
`build_dashboard.py` iterates over each cohort_type's JSON keys and calls
this renderer once per cohort key — 4 buckets + 3 roles + 11 sectors =
~18 HTML files per build.

Spec: `docs/specs/performance-page-redesign-v1.md` §2 (shared structure),
§2.2 (status pill ±50% rule), §2.3 (top/bottom + edge-case note),
§2.4 (rollup table), §2.5 (keyboard accessibility), §2.6 (breadcrumb),
§3 (per-page variants).

Mockups: `docs/specs/mockups/performance-{bucket,role,sector}-preview.html`.

Pure — no DB / file I/O. Takes pre-loaded `payload` dict from
`dashboard/data/performance_{bucket,role,sector}.json`.
"""
from __future__ import annotations

import json
from typing import Iterable

from . import render_helpers as h
from . import templates


# ---------------------------------------------------------------------------
# Constants — per spec §3 per-page variants
# ---------------------------------------------------------------------------

# Per-cohort-type display name for the page H1 and breadcrumb leaf.
COHORT_TYPE_LABEL = {
    "bucket": "Transaction size",
    "role":   "Director role",
    "sector": None,                # sector pages use the sector name directly
}

# Per-cohort-type URL query-string key (`?bucket=...`, `?role=...`, `?sector=...`).
COHORT_TYPE_URL_KEY = {
    "bucket": "bucket",
    "role":   "role",
    "sector": "sector",
}

# Per-cohort-type page-filename stem.
COHORT_TYPE_PAGE_STEM = {
    "bucket": "performance-bucket",
    "role":   "performance-role",
    "sector": "performance-sector",
}

# Bucket key → display label (matches export_dashboard_json.VALUE_BUCKET_LABELS).
BUCKET_LABELS = {
    "1k-25k":    "£1–25k",
    "25k-100k":  "£25–100k",
    "100k-500k": "£100–500k",
    "500k+":     "£500k+",
}

# Role key → display label (matches export_dashboard_json.ROLE_LABELS).
# B-025 Phase B (2026-05-20): 6 per-tier rows instead of 3 combined.
ROLE_LABELS = {
    "t1a": "CEO + Founder",
    "t1b": "CFO",
    "t7":  "Chair",
    "t2":  "Other exec",
    "t3":  "NED",
    "t5":  "PCA",
    # Legacy keys for backward compat with any stale cached JSON.
    "ceo_cfo":    "CEO / CFO",
    "other_exec": "Other exec",
    "ned":        "NED",
}

# Sprint 7 fix #4 — HORIZON_LABELS + LOOKBACK_LABELS imported from
# render_helpers so all three render modules share one source of truth.
from . import render_helpers as _h_const  # noqa: E402

HORIZON_LABELS = _h_const.HORIZON_LABELS
LOOKBACK_LABELS = _h_const.LOOKBACK_LABELS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_drilldown_page(cohort_type: str, cohort_key: str, payload: dict,
                          horizon: str = "t30", lookback: str = "90d",
                          existing_company_pages: Iterable[str] | None = None,
                          build_sha: str = "local",
                          generated_at_iso: str | None = None) -> str:
    """Render one cohort drill-down HTML page.

    Args:
        cohort_type: 'bucket' | 'role' | 'sector'
        cohort_key:  the cohort identifier (e.g. '100k-500k', 'ceo_cfo', 'Materials')
        payload:     the `performance_{cohort_type}.json` payload dict
        horizon:     't1' | 't30' | 't90' | 't180' | 't365' (default t30)
        lookback:    '90d' | '6m' | '1y' | 'all' (default 90d)
        existing_company_pages: set/list of ticker strings whose companies/{T}.html
            exists. Used to mark dead-link firing rows italic-faded per spec §8 smoke
            test. None means assume all tickers have company pages (legacy behaviour).
        build_sha:   passed through to base_page footer
        generated_at_iso: payload generated_at timestamp

    Returns the full HTML document string.
    """
    cohort_data = _get_cohort(payload, cohort_type, cohort_key)
    if cohort_data is None:
        return _render_not_found_page(cohort_type, cohort_key, build_sha,
                                      generated_at_iso)

    drill_block = _get_drill_block(cohort_data, horizon, lookback)

    existing_company_pages_set = (
        set(existing_company_pages) if existing_company_pages is not None
        else None
    )

    # Per-page header text + breadcrumb leaf.
    display_label, breadcrumb_leaf, h1_html = _build_h1(
        cohort_type, cohort_key, cohort_data
    )

    # Top of header — breadcrumb + horizon dropdown.
    header_html = _build_header(cohort_type, cohort_key, breadcrumb_leaf,
                                horizon, lookback)

    base_rate = _base_rate_for_horizon(payload, horizon)

    # Page-header card with stats line + status pill + lookback dropdown.
    page_header_card = _build_page_header_card(
        cohort_type, cohort_key, cohort_data, drill_block,
        h1_html, horizon, lookback,
        base_rate_horizon=base_rate,
    )

    # Top / bottom firings panels.
    top_panel, bottom_panel = _build_firings_panels(
        drill_block, horizon, existing_company_pages_set
    )

    # All-tickers rollup table.
    rollup_section = _build_rollup_section(
        drill_block, cohort_type, existing_company_pages_set
    )

    # Auto-wire script (clickable rows + keyboard accessibility).
    # Must come before client-side re-renderer so window.__rewireClickableRows
    # is defined when applyDrillView() runs.
    wire_script = _autowire_script()

    # B-057 (Sprint 8): client-side renderer. Reads ?horizon / ?lookback
    # from the URL on load and re-renders the four dynamic regions
    # against the embedded JSON payload. Falls back gracefully to the
    # server-rendered t30 x 90d view if JS is disabled or errors.
    client_script = _client_render_script(
        cohort_type=cohort_type,
        cohort_key=cohort_key,
        cohort_data=cohort_data,
        existing_pages=existing_company_pages_set,
        base_rate=base_rate,
        default_horizon=horizon,
        default_lookback=lookback,
    )

    body = (
        header_html
        + page_header_card
        + '<div class="m-6 grid grid-cols-1 lg:grid-cols-2 gap-4">'
        + top_panel + bottom_panel
        + '</div>'
        + rollup_section
        + _csv_export_script(cohort_type, cohort_key, display_label)
        + wire_script
        + client_script
    )

    title = f"Performance · {display_label}"
    return templates.base_page(
        title=title,
        body=body,
        generated_at_iso=generated_at_iso or payload.get("generated_at"),
        build_sha=build_sha,
        include_chartjs=False,    # drill pages have no charts (spec §2.7)
        nav_links=[
            ("Today",     "index.html"),
            ("Small Cap", "performance_small.html"),
            ("Large Cap", "performance_large.html"),
            ("All",       "performance.html"),
            ("Baskets",   "baskets.html"),
            ("Review",    "/review"),
        ],
    )


# ---------------------------------------------------------------------------
# Section builders (private)
# ---------------------------------------------------------------------------

def _get_cohort(payload: dict, cohort_type: str, cohort_key: str):
    """Lookup the cohort sub-dict in the payload. None if missing."""
    container_key = {
        "bucket": "buckets",
        "role":   "roles",
        "sector": "sectors",
    }.get(cohort_type)
    if not container_key:
        return None
    return (payload.get(container_key) or {}).get(cohort_key)


def _get_drill_block(cohort_data: dict, horizon: str, lookback: str) -> dict:
    """Return the §5.2 drill block for the requested (horizon, lookback).
    Falls back to an empty shape if missing."""
    block = (cohort_data.get(horizon) or {}).get(lookback)
    if not block:
        return {
            "benchmark_car_pct": None,
            "total_firings":    0,
            "distinct_tickers": 0,
            "tickers_with_n3":  0,
            "hit_pct":          None,
            "median_car":       None,
            "top_firings":      [],
            "bottom_firings":   [],
            "rollup":           [],
        }
    return block


def _base_rate_for_horizon(payload: dict, horizon: str) -> float:
    """Find the FTSE A-S base rate at this horizon for status_pill / hit colour.
    Drill payloads don't carry horizon_aggregates; the renderer is given a
    sensible default. Caller (build_dashboard) may override via the
    `generated_at_iso` channel later if base_rate needs to flow through —
    for v1 we use a deterministic 50.0 fallback so tests are stable."""
    return 50.0


def _build_h1(cohort_type: str, cohort_key: str, cohort_data: dict):
    """Return (display_label, breadcrumb_leaf, h1_html)."""
    cohort_label = cohort_data.get("label") or cohort_key
    if cohort_type == "bucket":
        display_label = BUCKET_LABELS.get(cohort_key, cohort_label)
        breadcrumb_leaf = f"Transaction size: {display_label}"
        h1_html = (
            '<h1 class="text-2xl font-semibold text-slate-900 tracking-tight">'
            f'Transaction size: <span class="font-mono">{h.esc(display_label)}'
            '</span></h1>'
        )
    elif cohort_type == "role":
        display_label = ROLE_LABELS.get(cohort_key, cohort_label)
        breadcrumb_leaf = f"Director role: {display_label}"
        h1_html = (
            '<h1 class="text-2xl font-semibold text-slate-900 tracking-tight">'
            f'Director role: {h.esc(display_label)}</h1>'
        )
    else:  # sector
        display_label = cohort_label
        breadcrumb_leaf = display_label
        h1_html = (
            '<h1 class="text-2xl font-semibold text-slate-900 tracking-tight">'
            f'{h.esc(display_label)}</h1>'
        )
    return display_label, breadcrumb_leaf, h1_html


def _build_header(cohort_type: str, cohort_key: str, breadcrumb_leaf: str,
                  horizon: str, lookback: str) -> str:
    """Top-of-page breadcrumb + horizon dropdown (sticky).

    B-057 (Sprint 8): the change handler is installed by the
    `_client_render_script` block — this function only emits the
    static markup. Previously emitted a URL-reload handler that
    forced a full page load on every dropdown change; that handler
    is gone.
    """
    # Build horizon options.
    horizon_opts = "".join(
        f'<option value="{h_key}"{" selected" if h_key == horizon else ""}>'
        f'{h.esc(h_label)}</option>'
        for h_key, h_label in HORIZON_LABELS.items()
    )
    return (
        '<header class="border-b border-slate-200 bg-white px-6 h-12 '
        'flex items-center justify-between sticky top-0 z-40">'
        '<nav class="flex items-center gap-2 text-xs">'
        '<a href="index.html" class="text-indigo-600 hover:underline">Today</a>'
        '<span class="text-slate-300">&rsaquo;</span>'
        '<a href="performance.html" class="text-indigo-600 hover:underline">'
        'Performance</a>'
        '<span class="text-slate-300">&rsaquo;</span>'
        f'<span class="text-slate-700 font-medium">{h.esc(breadcrumb_leaf)}'
        '</span></nav>'
        '<div class="flex items-center gap-2">'
        '<span class="text-xs text-slate-500">Horizon:</span>'
        '<select id="drillHorizon" class="text-xs border border-slate-300 '
        'rounded px-2 py-1 bg-white tabular-nums">'
        f'{horizon_opts}'
        '</select></div></header>'
    )


def _build_page_header_card(cohort_type: str, cohort_key: str,
                            cohort_data: dict, drill_block: dict,
                            h1_html: str, horizon: str, lookback: str,
                            base_rate_horizon: float) -> str:
    """The white page-header card with H1, scope_note, stats line, lookback
    dropdown, optional status pill."""
    scope_note = cohort_data.get("scope_note")
    scope_html = ""
    if scope_note:
        scope_html = (
            f'<p class="text-[11px] text-slate-400 italic mt-1">'
            f'{h.esc(scope_note)}</p>'
        )

    # Stats line.
    n_firings = drill_block.get("total_firings") or 0
    distinct = drill_block.get("distinct_tickers") or 0
    hit_pct = drill_block.get("hit_pct")
    median_car = drill_block.get("median_car")
    bench_car = drill_block.get("benchmark_car_pct")
    bench_symbol = cohort_data.get("benchmark_symbol")

    hit_html = (h.pct(hit_pct, dp=1, plus_sign=False)
                if hit_pct is not None else '<span class="text-slate-300">—</span>')
    hit_cls = h.car_color_class(hit_pct) if hit_pct is not None else "text-slate-500"
    car_html = h.car_cell(median_car)
    horizon_label = HORIZON_LABELS.get(horizon, horizon)

    bench_label = bench_symbol or "FTSE A-S"
    bench_value_html = h.car_cell(bench_car)

    # B-057: stats line wrapped in id="drillStatsLine" so client-side
    # renderer can swap the whole inner HTML on dropdown change.
    stats_line = (
        '<p id="drillStatsLine" class="text-xs text-slate-500 mt-2">'
        f'<span class="font-medium text-slate-700">{int(n_firings)} firings</span>'
        ' &middot; '
        f'<span class="font-medium text-slate-700">{int(distinct)} distinct '
        'tickers</span>'
        ' &middot; '
        f'Hit %: <span class="{hit_cls} font-medium">{hit_html}</span>'
        ' &middot; '
        f'Median CAR @ <span data-role="horizon-label">{h.esc(horizon_label)}'
        '</span>: '
        f'<span class="font-medium">{car_html}</span>'
        ' &middot; '
        f'{h.esc(bench_label)} benchmark: '
        f'<span class="font-medium">{bench_value_html}</span>'
        '</p>'
    )

    # Lookback dropdown — B-057: change handler installed by client-side
    # renderer (no URL-reload).
    lookback_opts = "".join(
        f'<option value="{lb_key}"{" selected" if lb_key == lookback else ""}>'
        f'{lb_label}</option>'
        for lb_key, lb_label in LOOKBACK_LABELS
    )
    lookback_dropdown = (
        '<div class="flex items-center gap-2">'
        '<span class="text-xs text-slate-500">Lookback:</span>'
        '<select id="drillLookback" class="text-xs border border-slate-300 '
        'rounded px-2 py-1 bg-white tabular-nums">'
        f'{lookback_opts}'
        '</select></div>'
    )

    status_pill = h.status_pill_for_cohort(hit_pct, base_rate_horizon, cohort_type)

    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg p-5">'
        '<div class="flex items-start justify-between mb-3">'
        '<div>'
        f'{h1_html}'
        f'{scope_html}'
        f'{stats_line}'
        '</div>'
        '<div class="flex flex-col items-end gap-2">'
        f'{lookback_dropdown}'
        f'<div id="drillStatusPillSlot">{status_pill}</div>'
        '</div></div></section>'
    )


def _build_firings_panels(drill_block: dict, horizon: str,
                          existing_pages: set | None):
    """Top 10 + Bottom 10 firings panels per spec §2.3."""
    top_firings = drill_block.get("top_firings") or []
    bottom_firings = drill_block.get("bottom_firings") or []
    total_firings = drill_block.get("total_firings") or 0
    horizon_label = HORIZON_LABELS.get(horizon, horizon)

    def _firings_table(firings, panel_color):
        if not firings:
            return (
                '<tr><td colspan="8" class="px-2 py-6 text-center '
                'text-[11px] text-slate-400 italic">'
                'No firings in this view</td></tr>'
            )
        rows = []
        for f in firings:
            ticker = f.get("ticker", "")
            page_exists = (
                existing_pages is None
                or (ticker in existing_pages)
            )
            rows.append(h.firing_row_html(f, panel_color, page_exists))
        return "".join(rows)

    # Edge-case note (spec §2.3): when fewer than 10 winners or losers.
    def _edge_case_note(firings, label, total):
        """Insert a small italic note when the panel has <10 entries AND
        we have more than 0 firings total."""
        if total == 0:
            return ""
        # Count negatives / positives in this panel.
        if not firings:
            return ""
        n_in_panel = len(firings)
        if n_in_panel >= 10:
            return ""
        # All firings of opposite sign that exist.
        if label == "losers":
            return (
                f'<p class="px-4 py-2 text-[10px] text-slate-400 italic '
                'border-t border-slate-100">'
                f'Only {n_in_panel} of {total} firings in this cohort '
                'had negative CAR in this period — this is why the cohort '
                'tile shows a high hit rate.</p>'
            )
        return (
            f'<p class="px-4 py-2 text-[10px] text-slate-400 italic '
            'border-t border-slate-100">'
            f'Only {n_in_panel} of {total} firings in this cohort '
            'had positive CAR in this period.</p>'
        )

    top_table_rows = _firings_table(top_firings, "emerald")
    bottom_table_rows = _firings_table(bottom_firings, "rose")

    # B-057: panel bodies, headings and edge-case note slots all carry
    # stable IDs so the client-side renderer can swap them on dropdown
    # change without touching the static chrome.
    top_panel = (
        '<section class="bg-white border border-emerald-200 rounded-lg '
        'overflow-hidden">'
        '<h2 class="px-4 py-3 text-xs uppercase tracking-wide '
        'text-emerald-700 border-b border-emerald-100 bg-emerald-50 '
        'flex items-center justify-between">'
        '<span>Top 10 firings &mdash; best CAR @ '
        f'<span id="drillTopHeadingHorizon">{h.esc(horizon_label)}</span></span>'
        '<span class="text-[10px] text-emerald-600 italic">winners</span>'
        '</h2>'
        f'{_firings_table_shell(top_table_rows, body_id="drillTopBody")}'
        f'<div id="drillTopEdgeNote">'
        f'{_edge_case_note(top_firings, "winners", total_firings)}'
        '</div>'
        '</section>'
    )
    bottom_panel = (
        '<section class="bg-white border border-rose-200 rounded-lg '
        'overflow-hidden">'
        '<h2 class="px-4 py-3 text-xs uppercase tracking-wide '
        'text-rose-700 border-b border-rose-100 bg-rose-50 '
        'flex items-center justify-between">'
        '<span>Bottom 10 firings &mdash; worst CAR @ '
        f'<span id="drillBottomHeadingHorizon">{h.esc(horizon_label)}</span></span>'
        '<span class="text-[10px] text-rose-600 italic">losers</span>'
        '</h2>'
        f'{_firings_table_shell(bottom_table_rows, body_id="drillBottomBody")}'
        f'<div id="drillBottomEdgeNote">'
        f'{_edge_case_note(bottom_firings, "losers", total_firings)}'
        '</div>'
        '</section>'
    )
    return top_panel, bottom_panel


def _firings_table_shell(body_rows: str, body_id: str | None = None) -> str:
    """Shared table shell for the Top / Bottom firings panels.

    `body_id` (B-057) lets the client-side renderer target the tbody on
    dropdown change. Optional so the server-side default callers remain
    compatible.
    """
    body_attr = f' id="{body_id}"' if body_id else ""
    return (
        '<table class="w-full text-[11px] tabular-nums">'
        '<thead class="text-slate-500 uppercase tracking-wide text-[10px] '
        'border-b border-slate-200">'
        '<tr>'
        '<th class="text-left px-2 py-1.5 font-medium">Date</th>'
        '<th class="text-left px-2 py-1.5 font-medium">Ticker</th>'
        '<th class="text-left px-2 py-1.5 font-medium">Director</th>'
        '<th class="text-left px-2 py-1.5 font-medium">Tier</th>'
        '<th class="text-right px-2 py-1.5 font-medium">Value</th>'
        '<th class="text-right px-2 py-1.5 font-medium">CAR</th>'
        '<th class="text-right px-2 py-1.5 font-medium" '
        'title="Gross stock return since T+1 close after signal date to horizon. '
        'No benchmark deduction.">Abs %</th>'
        '<th class="text-right px-2 py-1.5 font-medium" '
        'title="B113: Sector benchmark return at the same horizon.">Bmk</th>'
        '</tr></thead>'
        f'<tbody{body_attr}>{body_rows}</tbody></table>'
    )


def _build_rollup_section(drill_block: dict, cohort_type: str,
                           existing_pages: set | None) -> str:
    """All-tickers rollup table per spec §2.4 — N≥3 first, then N<3 below
    a dashed divider."""
    rollup = drill_block.get("rollup") or []
    total_tickers = len(rollup)

    if not rollup:
        body = (
            '<tr><td colspan="6" class="px-3 py-6 text-center '
            'text-[11px] text-slate-400 italic">'
            'No tickers in this view</td></tr>'
        )
    else:
        n3_rows = [r for r in rollup if (r.get("n") or 0) >= 3]
        below_rows = [r for r in rollup if (r.get("n") or 0) < 3]
        parts = []
        for r in n3_rows:
            parts.append(_rollup_row_html(r, existing_pages, faded=False))
        if n3_rows and below_rows:
            parts.append(
                '<tr><td colspan="6" class="border-t border-dashed '
                'border-slate-200 py-2 text-[10px] text-slate-400 italic '
                'px-3 text-center">'
                f'&mdash; {len(below_rows)} more tickers below (N&lt;3) &mdash;'
                '</td></tr>'
            )
        for r in below_rows:
            parts.append(_rollup_row_html(r, existing_pages, faded=True))
        body = "".join(parts)

    label_by_type = {
        "bucket": "this bucket",
        "role":   "this role",
        "sector": "this sector",
    }.get(cohort_type, "this cohort")

    return (
        '<section class="m-6 bg-white border border-slate-200 '
        'rounded-lg overflow-hidden">'
        '<div class="px-4 py-3 border-b border-slate-100">'
        f'<h2 class="text-xs uppercase tracking-wide text-slate-500">'
        f'All tickers in {h.esc(label_by_type)}</h2></div>'
        '<table class="w-full text-xs tabular-nums">'
        '<thead class="bg-slate-50 text-slate-500 uppercase '
        'tracking-wide text-[10px] border-b border-slate-200">'
        '<tr>'
        '<th class="text-left px-3 py-2 font-medium w-[10%]">Ticker</th>'
        '<th class="text-left px-3 py-2 font-medium w-[30%]">Company</th>'
        '<th class="text-right px-3 py-2 font-medium w-[10%]">N</th>'
        '<th class="text-right px-3 py-2 font-medium w-[15%]">Hit %</th>'
        '<th class="text-right px-3 py-2 font-medium w-[15%]">Mean CAR</th>'
        '<th class="text-right px-3 py-2 font-medium w-[20%]">Latest firing</th>'
        '</tr></thead>'
        f'<tbody id="drillRollupBody">{body}</tbody></table>'
        '<div class="px-4 py-2 border-t border-slate-100 text-[10px] '
        f'text-slate-400"><span id="drillRollupCount">{total_tickers}</span>'
        ' tickers total &middot; click any '
        'row for full company history</div>'
        '</section>'
    )


def _rollup_row_html(row: dict, existing_pages: set | None,
                      faded: bool) -> str:
    ticker = row.get("ticker") or ""
    company = row.get("company") or ""
    n = row.get("n") or 0
    hit_pct = row.get("hit_pct")
    mean_car = row.get("mean_car")
    latest = row.get("latest_fire") or ""
    page_exists = existing_pages is None or (ticker in existing_pages)
    italic_cls = " italic text-slate-500" if faded else ""

    if page_exists and ticker:
        href = h.company_url(ticker)
        return (
            '<tr class="clickable group border-t border-slate-100 '
            f'cursor-pointer hover:bg-indigo-50{italic_cls}" '
            f'data-href="{h.esc(href)}" tabindex="0" role="link" '
            f'aria-label="View {h.esc(ticker)} company page">'
            f'<td class="px-3 py-2 font-mono text-slate-700">{h.esc(ticker)}</td>'
            f'<td class="px-3 py-2 text-slate-700">{h.esc(company)}</td>'
            f'<td class="px-3 py-2 text-right text-slate-700">{int(n)}</td>'
            f'<td class="px-3 py-2 text-right">{h.car_cell(hit_pct)}</td>'
            f'<td class="px-3 py-2 text-right">{h.car_cell(mean_car)}</td>'
            f'<td class="px-3 py-2 text-right text-slate-500">{h.esc(latest)}'
            '<span class="chev opacity-0 group-hover:opacity-60 '
            'text-slate-400">&nbsp;&rsaquo;</span></td></tr>'
        )
    return (
        '<tr class="border-t border-slate-100 text-slate-400 italic" '
        'title="Company page not generated for this ticker">'
        f'<td class="px-3 py-2 font-mono">{h.esc(ticker)}</td>'
        f'<td class="px-3 py-2">{h.esc(company)}</td>'
        f'<td class="px-3 py-2 text-right">{int(n)}</td>'
        f'<td class="px-3 py-2 text-right">{h.car_cell(hit_pct)}</td>'
        f'<td class="px-3 py-2 text-right">{h.car_cell(mean_car)}</td>'
        f'<td class="px-3 py-2 text-right">{h.esc(latest)}</td></tr>'
    )


def _autowire_script() -> str:
    """Wire every tr.clickable to its data-href, with keyboard accessibility
    (Enter / Space). Same pattern as the cohort tile auto-wire in FE1.

    B-057 (Sprint 8): `wire` and a `__rewireClickableRows` rescan helper
    are exposed on `window` so the client-side renderer can wire rows
    it injects after dropdown changes.
    """
    return r"""<script>
(function() {
  function wire(tr) {
    if (tr.dataset.wired === '1') return;
    tr.dataset.wired = '1';
    var go = function() {
      var href = tr.getAttribute('data-href');
      if (href) window.location.href = href;
    };
    tr.addEventListener('click', go);
    tr.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        go();
      }
    });
  }
  window.__wireClickableRow = wire;
  window.__rewireClickableRows = function() {
    document.querySelectorAll('tr.clickable').forEach(wire);
  };
  window.__rewireClickableRows();
})();
</script>"""


def _client_render_script(*, cohort_type: str, cohort_key: str,
                          cohort_data: dict,
                          existing_pages: set | None,
                          base_rate: float,
                          default_horizon: str,
                          default_lookback: str) -> str:
    """Emit the embedded JSON payload + client-side renderer (B-057 / Sprint 8).

    Renders four dynamic regions when the horizon / lookback dropdowns
    change, without reloading the page:
      * #drillStatsLine — N, distinct, Hit %, median CAR, benchmark
      * #drillStatusPillSlot — green / red / no pill (spec §2.2 ±50% rule)
      * #drillTopBody / #drillBottomBody — firing rows
      * #drillRollupBody — N>=3 rows above dashed divider, N<3 below

    Server-side fallback is preserved: the page renders the default
    horizon x lookback view in Python, so if this script fails the user
    still gets a working page (just with dead dropdowns — same as v1).

    Args:
      cohort_data:   the full per-cohort sub-dict from
                     performance_{cohort_type}.json — already contains
                     every (horizon x lookback) drill_block.
      existing_pages: set of TICKER strings whose companies/{T}.html
                     exists. None means "assume all tickers have pages".
      base_rate:     T+21 base rate (50.0 default — see
                     _base_rate_for_horizon) used by the status pill.
      default_horizon / default_lookback: what the server-side render
                     used. Used to skip the initial client re-render
                     when URL params match.
    """
    # JSON payload — keep small by emitting only the keys the client
    # actually needs. The 4 horizons x 5 lookbacks = 20 drill_blocks per
    # cohort are all present in cohort_data already.
    payload = {
        "cohort_type": cohort_type,
        "cohort_key": cohort_key,
        "default_horizon": default_horizon,
        "default_lookback": default_lookback,
        "base_rate": base_rate,
        "benchmark_symbol": cohort_data.get("benchmark_symbol"),
        # Whitelist horizons -> lookbacks -> drill_block.
        "blocks": {
            hor: cohort_data.get(hor) or {}
            for hor in ("t1", "t30", "t90", "t180", "t365")
        },
        # null = "assume all tickers have pages" (legacy behaviour);
        # list = explicit whitelist.
        "existing_pages": (
            None if existing_pages is None else sorted(existing_pages)
        ),
        "horizon_labels": dict(HORIZON_LABELS),
    }
    # `</script>` inside a JSON string would break the script tag. The
    # default json.dumps doesn't escape forward slashes; we have to
    # post-process. Using ensure_ascii=False keeps unicode intact for
    # company names (e.g. £-prefixed bucket labels).
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload_json = payload_json.replace("</", "<\\/")

    # The renderer JS. Long but readable — split into small functions
    # mirroring the four Python section builders.
    js = r"""<script>
(function() {
  var dataEl = document.getElementById('drillData');
  if (!dataEl) return;
  var ctx;
  try { ctx = JSON.parse(dataEl.textContent); } catch (e) { return; }

  var existingPages = ctx.existing_pages; // null | array
  function hasCompanyPage(ticker) {
    if (existingPages === null || existingPages === undefined) return true;
    return existingPages.indexOf(ticker) !== -1;
  }

  // ---------- Small formatters (mirror render_helpers.py) ----------
  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fmtPct(v, dp, plusSign) {
    if (v === null || v === undefined || isNaN(v)) return '-';
    var sign = (plusSign && v > 0) ? '+' : '';
    return sign + Number(v).toFixed(dp == null ? 2 : dp) + '%';
  }
  function carColorClass(v) {
    if (v === null || v === undefined || isNaN(v)) return 'text-slate-400';
    if (v > 0.05)  return 'text-emerald-600';
    if (v < -0.05) return 'text-rose-600';
    return 'text-slate-500';
  }
  function carCell(v) {
    if (v === null || v === undefined || isNaN(v)) {
      return '<span class="text-slate-300">-</span>';
    }
    return '<span class="' + carColorClass(v) + '">' + esc(fmtPct(v, 2, true)) + '</span>';
  }
  function fmtValueGbp(v) {
    if (v === null || v === undefined || isNaN(v)) {
      return '<span class="text-slate-300">&mdash;</span>';
    }
    var av = Math.abs(v);
    if (av >= 1000000) return '&pound;' + (v / 1000000).toFixed(1) + 'm';
    if (av >= 1000)    return '&pound;' + Math.round(v / 1000) + 'k';
    return '&pound;' + Math.round(v);
  }

  // Tier badge palette mirrors render_helpers.TIER_PALETTE.
  var TIER_PAL = {
    t0:{bg:'bg-red-600',fg:'text-white'}, t1a:{bg:'bg-red-500',fg:'text-white'},
    t1b:{bg:'bg-rose-500',fg:'text-white'}, t7:{bg:'bg-violet-500',fg:'text-white'},
    t2:{bg:'bg-amber-500',fg:'text-white'}, t3:{bg:'bg-emerald-500',fg:'text-white'},
    t5:{bg:'bg-orange-400',fg:'text-white'}, t6:{bg:'bg-slate-300',fg:'text-white'},
    t4:{bg:'bg-slate-400',fg:'text-white'}, s1:{bg:'bg-blue-500',fg:'text-white'},
    f1:{bg:'bg-purple-500',fg:'text-white'}
  };
  function tierBadge(sid) {
    if (!sid) return '<span class="text-slate-400">&mdash;</span>';
    var key = String(sid).toLowerCase();
    var pal = TIER_PAL[key];
    if (!pal) {
      return '<span class="inline-flex items-center px-1.5 py-0.5 rounded ' +
             'text-[10px] font-semibold bg-slate-300 text-white">' +
             esc(sid) + '</span>';
    }
    return '<span class="inline-flex items-center px-1.5 py-0.5 rounded ' +
           'text-[10px] font-semibold ' + pal.bg + ' ' + pal.fg + '">' +
           key.toUpperCase() + '</span>';
  }

  // ---------- Region renderers ----------
  function getBlock(horizon, lookback) {
    var blocksByHorizon = ctx.blocks[horizon] || {};
    return blocksByHorizon[lookback] || null;
  }

  function statusPillHtml(hitPct) {
    if (hitPct === null || hitPct === undefined) return '';
    var br = ctx.base_rate;
    if (br === null || br === undefined || br <= 0) return '';
    var copyGreen = {
      bucket: 'Top bucket',
      role:   'Strong role cohort',
      sector: 'Top sector this period'
    }[ctx.cohort_type] || 'Strong cohort';
    var copyRed = {
      bucket: 'Underperforming bucket',
      role:   'Underperforming role cohort',
      sector: 'Bottom sector this period'
    }[ctx.cohort_type] || 'Weak cohort';
    if (hitPct >= br * 1.5) {
      return '<span class="inline-flex items-center px-2 py-0.5 rounded-full ' +
             'text-[10px] font-medium bg-emerald-100 text-emerald-700">' +
             '<span class="inline-block w-1.5 h-1.5 rounded-full ' +
             'bg-emerald-500 mr-1"></span>' + esc(copyGreen) + '</span>';
    }
    if (hitPct <= br * 0.5) {
      return '<span class="inline-flex items-center px-2 py-0.5 rounded-full ' +
             'text-[10px] font-medium bg-rose-100 text-rose-700">' +
             '<span class="inline-block w-1.5 h-1.5 rounded-full ' +
             'bg-rose-500 mr-1"></span>' + esc(copyRed) + '</span>';
    }
    return '';
  }

  function updateStatsLine(block, horizon) {
    var el = document.getElementById('drillStatsLine');
    if (!el) return;
    var n = block.total_firings || 0;
    var distinct = block.distinct_tickers || 0;
    var hitPct = block.hit_pct;
    var medianCar = block.median_car;
    var benchCar = block.benchmark_car_pct;
    var benchLabel = ctx.benchmark_symbol || 'FTSE A-S';
    var hitHtml = (hitPct === null || hitPct === undefined)
      ? '<span class="text-slate-300">&mdash;</span>'
      : esc(fmtPct(hitPct, 1, false));
    var hitCls = (hitPct === null || hitPct === undefined)
      ? 'text-slate-500' : carColorClass(hitPct);
    var horizonLabel = ctx.horizon_labels[horizon] || horizon;
    el.innerHTML =
      '<span class="font-medium text-slate-700">' + Math.floor(n) + ' firings</span>' +
      ' &middot; ' +
      '<span class="font-medium text-slate-700">' + Math.floor(distinct) +
      ' distinct tickers</span>' +
      ' &middot; Hit %: <span class="' + hitCls + ' font-medium">' + hitHtml + '</span>' +
      ' &middot; Median CAR @ <span data-role="horizon-label">' + esc(horizonLabel) +
      '</span>: <span class="font-medium">' + carCell(medianCar) + '</span>' +
      ' &middot; ' + esc(benchLabel) + ' benchmark: ' +
      '<span class="font-medium">' + carCell(benchCar) + '</span>';
  }

  function updateStatusPill(block) {
    var el = document.getElementById('drillStatusPillSlot');
    if (!el) return;
    el.innerHTML = statusPillHtml(block.hit_pct);
  }

  function firingRowHtml(f, panelColor) {
    var ticker = f.ticker || '';
    var company = f.company || '';
    var director = f.director || '';
    var sigTier = f.signal_tier || '';
    var valueGbp = f.value_gbp;
    var car = f.car;
    var absReturn = (f.abs_return !== undefined && f.abs_return !== null) ? f.abs_return : null;
    var date = f.date || '';
    var directorDisplay = director;
    if (director.length > 18) {
      var parts = director.split(/\s+/);
      if (parts.length >= 2) {
        directorDisplay = parts[0].charAt(0) + '. ' + parts[parts.length - 1];
      }
    }
    var pageExists = hasCompanyPage(ticker);
    var hoverBg = (panelColor === 'emerald') ? 'hover:bg-emerald-50' : 'hover:bg-rose-50';
    var badge = sigTier ? tierBadge(sigTier) : '<span class="text-slate-400">&mdash;</span>';
    var valHtml = fmtValueGbp(valueGbp);
    var carHtml = carCell(car);
    var absHtml = carCell(absReturn);
    // B113: benchmark return at same horizon.
    var benchReturn = (f.bench_return !== undefined && f.bench_return !== null) ? f.bench_return : null;
    var benchHtml = carCell(benchReturn);
    if (pageExists && ticker) {
      var href = 'company.html?ticker=' + encodeURIComponent(ticker);
      return '<tr class="clickable group border-t border-slate-100 ' +
             'cursor-pointer ' + hoverBg + ' relative" data-href="' + esc(href) +
             '" tabindex="0" role="link" aria-label="View ' + esc(ticker) +
             ' company page">' +
             '<td class="px-2 py-1.5 text-slate-700">' + esc(date) + '</td>' +
             '<td class="px-2 py-1.5 font-mono text-slate-700">' + esc(ticker) + '</td>' +
             '<td class="px-2 py-1.5 text-slate-700" title="' + esc(director) + '">' +
             esc(directorDisplay) + '</td>' +
             '<td class="px-2 py-1.5">' + badge + '</td>' +
             '<td class="px-2 py-1.5 text-right tabular-nums">' + valHtml + '</td>' +
             '<td class="px-2 py-1.5 text-right tabular-nums relative">' + carHtml +
             '<span class="chev absolute right-2 top-1/2 -translate-y-1/2 ' +
             'opacity-0 group-hover:opacity-60 text-slate-400">&rsaquo;</span></td>' +
             '<td class="px-2 py-1.5 text-right tabular-nums">' + absHtml + '</td>' +
             '<td class="px-2 py-1.5 text-right tabular-nums">' + benchHtml + '</td></tr>';
    }
    return '<tr class="border-t border-slate-100 text-slate-400 italic" ' +
           'title="Company page not generated for this ticker">' +
           '<td class="px-2 py-1.5">' + esc(date) + '</td>' +
           '<td class="px-2 py-1.5 font-mono">' + esc(ticker) + '</td>' +
           '<td class="px-2 py-1.5">' + esc(directorDisplay) + '</td>' +
           '<td class="px-2 py-1.5">' + badge + '</td>' +
           '<td class="px-2 py-1.5 text-right tabular-nums">' + valHtml + '</td>' +
           '<td class="px-2 py-1.5 text-right tabular-nums">' + carHtml + '</td>' +
           '<td class="px-2 py-1.5 text-right tabular-nums">' + absHtml + '</td>' +
           '<td class="px-2 py-1.5 text-right tabular-nums">' + benchHtml + '</td></tr>';
  }

  function edgeCaseNote(firings, panel, total) {
    if (!total || total <= 0) return '';
    if (!firings || firings.length === 0) return '';
    var n = firings.length;
    if (n >= 10) return '';
    var msg = (panel === 'losers')
      ? 'Only ' + n + ' of ' + total + ' firings in this cohort had ' +
        'negative CAR in this period &mdash; this is why the cohort tile ' +
        'shows a high hit rate.'
      : 'Only ' + n + ' of ' + total + ' firings in this cohort had ' +
        'positive CAR in this period.';
    return '<p class="px-4 py-2 text-[10px] text-slate-400 italic ' +
           'border-t border-slate-100">' + msg + '</p>';
  }

  function updateTopPanel(block, horizon) {
    var body = document.getElementById('drillTopBody');
    var heading = document.getElementById('drillTopHeadingHorizon');
    var note = document.getElementById('drillTopEdgeNote');
    if (heading) heading.textContent = ctx.horizon_labels[horizon] || horizon;
    var firings = block.top_firings || [];
    if (body) {
      if (firings.length === 0) {
        body.innerHTML = '<tr><td colspan="8" class="px-2 py-6 text-center ' +
          'text-[11px] text-slate-400 italic">No firings in this view</td></tr>';
      } else {
        body.innerHTML = firings.map(function(f) {
          return firingRowHtml(f, 'emerald');
        }).join('');
      }
    }
    if (note) note.innerHTML = edgeCaseNote(firings, 'winners', block.total_firings || 0);
  }

  function updateBottomPanel(block, horizon) {
    var body = document.getElementById('drillBottomBody');
    var heading = document.getElementById('drillBottomHeadingHorizon');
    var note = document.getElementById('drillBottomEdgeNote');
    if (heading) heading.textContent = ctx.horizon_labels[horizon] || horizon;
    var firings = block.bottom_firings || [];
    if (body) {
      if (firings.length === 0) {
        body.innerHTML = '<tr><td colspan="8" class="px-2 py-6 text-center ' +
          'text-[11px] text-slate-400 italic">No firings in this view</td></tr>';
      } else {
        body.innerHTML = firings.map(function(f) {
          return firingRowHtml(f, 'rose');
        }).join('');
      }
    }
    if (note) note.innerHTML = edgeCaseNote(firings, 'losers', block.total_firings || 0);
  }

  function rollupRowHtml(r, faded) {
    var ticker = r.ticker || '';
    var company = r.company || '';
    var n = r.n || 0;
    var hitPct = r.hit_pct;
    var meanCar = r.mean_car;
    var latest = r.latest_fire || '';
    var pageExists = hasCompanyPage(ticker);
    var italicCls = faded ? ' italic text-slate-500' : '';
    if (pageExists && ticker) {
      var href = 'company.html?ticker=' + encodeURIComponent(ticker);
      return '<tr class="clickable group border-t border-slate-100 ' +
             'cursor-pointer hover:bg-indigo-50' + italicCls +
             '" data-href="' + esc(href) + '" tabindex="0" role="link" ' +
             'aria-label="View ' + esc(ticker) + ' company page">' +
             '<td class="px-3 py-2 font-mono text-slate-700">' + esc(ticker) + '</td>' +
             '<td class="px-3 py-2 text-slate-700">' + esc(company) + '</td>' +
             '<td class="px-3 py-2 text-right text-slate-700">' + Math.floor(n) + '</td>' +
             '<td class="px-3 py-2 text-right">' + carCell(hitPct) + '</td>' +
             '<td class="px-3 py-2 text-right">' + carCell(meanCar) + '</td>' +
             '<td class="px-3 py-2 text-right text-slate-500">' + esc(latest) +
             '<span class="chev opacity-0 group-hover:opacity-60 text-slate-400">' +
             '&nbsp;&rsaquo;</span></td></tr>';
    }
    return '<tr class="border-t border-slate-100 text-slate-400 italic" ' +
           'title="Company page not generated for this ticker">' +
           '<td class="px-3 py-2 font-mono">' + esc(ticker) + '</td>' +
           '<td class="px-3 py-2">' + esc(company) + '</td>' +
           '<td class="px-3 py-2 text-right">' + Math.floor(n) + '</td>' +
           '<td class="px-3 py-2 text-right">' + carCell(hitPct) + '</td>' +
           '<td class="px-3 py-2 text-right">' + carCell(meanCar) + '</td>' +
           '<td class="px-3 py-2 text-right">' + esc(latest) + '</td></tr>';
  }

  function updateRollup(block) {
    var body = document.getElementById('drillRollupBody');
    var count = document.getElementById('drillRollupCount');
    if (!body) return;
    var rollup = block.rollup || [];
    if (count) count.textContent = String(rollup.length);
    if (rollup.length === 0) {
      body.innerHTML = '<tr><td colspan="6" class="px-3 py-6 text-center ' +
        'text-[11px] text-slate-400 italic">No tickers in this view</td></tr>';
      return;
    }
    var n3 = rollup.filter(function(r) { return (r.n || 0) >= 3; });
    var below = rollup.filter(function(r) { return (r.n || 0) < 3; });
    var parts = n3.map(function(r) { return rollupRowHtml(r, false); });
    if (n3.length > 0 && below.length > 0) {
      parts.push('<tr><td colspan="6" class="border-t border-dashed ' +
        'border-slate-200 py-2 text-[10px] text-slate-400 italic px-3 ' +
        'text-center">&mdash; ' + below.length +
        ' more tickers below (N&lt;3) &mdash;</td></tr>');
    }
    below.forEach(function(r) { parts.push(rollupRowHtml(r, true)); });
    body.innerHTML = parts.join('');
  }

  // ---------- Master applyDrillView ----------
  function emptyBlock() {
    return {
      benchmark_car_pct: null, total_firings: 0, distinct_tickers: 0,
      tickers_with_n3: 0, hit_pct: null, median_car: null,
      top_firings: [], bottom_firings: [], rollup: []
    };
  }
  function applyDrillView(horizon, lookback) {
    var block = getBlock(horizon, lookback) || emptyBlock();
    updateStatsLine(block, horizon);
    updateStatusPill(block);
    updateTopPanel(block, horizon);
    updateBottomPanel(block, horizon);
    updateRollup(block);
    if (typeof window.__rewireClickableRows === 'function') {
      window.__rewireClickableRows();
    }
  }

  // ---------- URL handling ----------
  function readUrlParams() {
    var p = new URLSearchParams(window.location.search);
    var h = p.get('horizon');
    var l = p.get('lookback');
    var validH = ['t1', 't30', 't90', 't180', 't365'];
    var validL = ['30d', '90d', '6m', '1y', 'all'];
    if (validH.indexOf(h) === -1) h = ctx.default_horizon;
    if (validL.indexOf(l) === -1) l = ctx.default_lookback;
    return { horizon: h, lookback: l };
  }
  function pushUrl(horizon, lookback) {
    var p = new URLSearchParams(window.location.search);
    p.set('horizon', horizon);
    p.set('lookback', lookback);
    var newUrl = window.location.pathname + '?' + p.toString();
    try { history.replaceState(null, '', newUrl); } catch (e) { /* ignore */ }
  }

  // ---------- Wire dropdowns ----------
  var horizonSel  = document.getElementById('drillHorizon');
  var lookbackSel = document.getElementById('drillLookback');
  function currentSelection() {
    return {
      horizon:  horizonSel  ? horizonSel.value  : ctx.default_horizon,
      lookback: lookbackSel ? lookbackSel.value : ctx.default_lookback
    };
  }
  function onChange() {
    var sel = currentSelection();
    pushUrl(sel.horizon, sel.lookback);
    applyDrillView(sel.horizon, sel.lookback);
  }
  if (horizonSel)  horizonSel.addEventListener('change', onChange);
  if (lookbackSel) lookbackSel.addEventListener('change', onChange);

  // ---------- Initial render ----------
  // Sync dropdowns to URL params, then re-render if the URL view
  // differs from the server-side default render. Skip the work
  // entirely when the URL already matches the default (avoids a
  // visible flash on the no-op path).
  var initial = readUrlParams();
  if (horizonSel)  horizonSel.value  = initial.horizon;
  if (lookbackSel) lookbackSel.value = initial.lookback;
  if (initial.horizon !== ctx.default_horizon ||
      initial.lookback !== ctx.default_lookback) {
    applyDrillView(initial.horizon, initial.lookback);
  }

  // Expose for tests / debugging.
  window.__drillCtx = ctx;
  window.applyDrillView = applyDrillView;
})();
</script>"""

    return (
        '<script type="application/json" id="drillData">'
        + payload_json
        + '</script>'
        + js
    )


def _csv_export_script(cohort_type: str, cohort_key: str,
                       display_label: str) -> str:
    """B-075 (Sprint 24): client-side CSV export of the drill-down rollup table.

    Adds a floating 'Download CSV' button (bottom-right of page). On click,
    serialises the current rollup[] from the embedded #drillData JSON payload
    using the currently-selected horizon + lookback, then triggers a browser
    download. No server round-trip required — data is already embedded.
    """
    safe_label = h.esc(display_label.replace('"', '').replace(',', ''))
    safe_cohort = h.esc(f"{cohort_type}_{cohort_key}")
    return (
        # Floating download button — appears bottom-right on drill pages only.
        f'<div class="fixed bottom-4 right-4 z-30">'
        f'<button id="drillCsvBtn" '
        f'class="inline-flex items-center gap-1.5 px-3 py-2 text-xs rounded-lg '
        f'border border-slate-300 bg-white text-slate-600 shadow-sm '
        f'hover:border-indigo-400 hover:text-indigo-700 hover:shadow transition-all" '
        f'title="Download the current rollup table as a CSV file">'
        f'&#8595; Download CSV'
        f'</button></div>'
        # Script: reads the embedded payload, serialises rollup to CSV.
        f"""<script>
(function() {{
  'use strict';
  var btn = document.getElementById('drillCsvBtn');
  if (!btn) return;
  btn.addEventListener('click', function() {{
    var dataEl = document.getElementById('drillData');
    if (!dataEl) return;
    var ctx;
    try {{ ctx = JSON.parse(dataEl.textContent); }} catch(e) {{ return; }}
    // Read current horizon + lookback from the dropdowns.
    var horizonEl  = document.getElementById('drillHorizon');
    var lookbackEl = document.getElementById('drillLookback');
    var horizon  = horizonEl  ? horizonEl.value  : ctx.default_horizon;
    var lookback = lookbackEl ? lookbackEl.value : ctx.default_lookback;
    var block = ((ctx.blocks[horizon] || {{}})[lookback]) || {{}};
    var rollup = block.rollup || [];
    // Build CSV rows.
    var header = ['Ticker', 'Company', 'N', 'Hit %', 'Mean CAR %', 'Latest firing'];
    var rows = [header];
    rollup.forEach(function(r) {{
      var hitPct = (r.hit_pct == null) ? '' : Number(r.hit_pct).toFixed(1);
      var meanCar = (r.mean_car == null) ? '' : Number(r.mean_car).toFixed(2);
      rows.push([
        r.ticker || '',
        (r.company || '').replace(/,/g, ' '),
        r.n || 0,
        hitPct,
        meanCar,
        r.latest_fire || '',
      ]);
    }});
    var csv = rows.map(function(r) {{ return r.join(','); }}).join('\\n');
    var blob = new Blob([csv], {{type: 'text/csv;charset=utf-8;'}});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'directors_dealings_{safe_cohort}_' + horizon + '_' + lookback + '.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(function() {{
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }}, 100);
  }});
}})();
</script>"""
    )


def _render_not_found_page(cohort_type: str, cohort_key: str,
                            build_sha: str, generated_at_iso) -> str:
    """Fallback when the cohort key is missing from the payload."""
    body = (
        '<section class="m-6 bg-white border border-slate-200 '
        'rounded-lg p-8 text-center">'
        '<h1 class="text-xl font-semibold text-slate-700 mb-2">'
        'Cohort not found</h1>'
        f'<p class="text-sm text-slate-500">No data for '
        f'<span class="font-mono">{h.esc(cohort_type)}={h.esc(cohort_key)}'
        '</span> in the latest export.</p>'
        '<p class="mt-4 text-xs text-slate-400">'
        '<a href="performance.html" class="text-indigo-600 hover:underline">'
        '&larr; Back to Performance</a></p>'
        '</section>'
    )
    return templates.base_page(
        title="Performance · Not found",
        body=body,
        generated_at_iso=generated_at_iso,
        build_sha=build_sha,
        include_chartjs=False,
        nav_links=[
            ("Today",     "index.html"),
            ("Small Cap", "performance_small.html"),
            ("Large Cap", "performance_large.html"),
            ("All",       "performance.html"),
            ("Baskets",   "baskets.html"),
            ("Review",    "/review"),
        ],
    )
