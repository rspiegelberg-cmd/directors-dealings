"""Render outputs/performance.html -- the trailing-analytics deep dive.

Layout (locked, stage-05-design-final.md s 1.2):
  - Header + horizon dropdown
  - Per-signal scoreboard (centrepiece, 7 rows, deprecate button per row)
  - Diagnostics chart (Chart.js, 8 lines, listens to horizonChange event)
  - Cohort cuts (by value bucket bar chart + by sector hit bars)
  - Model assessment panel (kill candidates auto-computed)
  - Footer
"""
from __future__ import annotations

import json
from pathlib import Path

from . import render_helpers as h
from . import templates

# HORIZON_LABELS + LOOKBACK_LABELS now live in render_helpers (single source of
# truth — Sprint 7 fix #4). Re-export the same names to avoid touching every
# usage site in this module.
HORIZON_LABELS = h.HORIZON_LABELS


def _row_for_signal(sid: str, h_data: dict, base_rate, status_overrides: dict,
                    horizon_key: str, t90_signals: dict | None = None,
                    cohort_groups: dict | None = None) -> str:
    row_data = (h_data.get("signals") or {}).get(sid) or {}
    # Phase 0 (cohort-chart sprint): mean-vs-median divergence warning. The
    # trigger is ALWAYS evaluated at T+90 regardless of which horizon the
    # scoreboard is showing, so we read mean/median from the T+90 aggregates
    # passed in by `_scoreboard`. This flags single-outlier contamination
    # (e.g. T3 NED mean +7.9% / median -5.0% at T+90 driven by one TIN trade).
    t90_row = ((t90_signals or {}).get(sid) or {})
    div_fired, div_gap_pp = h.divergence_warning(
        t90_row.get("mean_car"), t90_row.get("median_car")
    )
    trades = row_data.get("trades") or 0
    hit_pct = row_data.get("hit_pct")
    median_car = row_data.get("median_car")
    mean_car = row_data.get("mean_car")
    edge = row_data.get("edge")
    status = (row_data.get("status") or "review").lower()
    outlier = bool(row_data.get("outlier_flag"))
    deprecated = sid in status_overrides
    if deprecated:
        status = "deprecated"

    # Cell HTMLs.
    trades_html = f'{int(trades)}'
    if trades < 20 and trades > 0:
        trades_html += ('<span class="ml-1 text-amber-600" '
                        'title="N<20 - preliminary, wait for more firings before '
                        'acting on numeric edge.">&#9888;</span>')
    elif trades == 0:
        trades_html = '<span class="text-slate-300">-</span>'

    # Hit / Base.
    if hit_pct is None or base_rate is None:
        hit_html = '<span class="text-slate-300">-</span>'
    else:
        if hit_pct >= base_rate:
            hit_cls = "text-emerald-600"
        elif hit_pct < base_rate * 0.85:
            hit_cls = "text-rose-600"
        else:
            hit_cls = "text-slate-700"
        hit_html = (f'<span class="{hit_cls}">{hit_pct:.1f}%</span> '
                    f'/ <span class="text-slate-500">{base_rate:.1f}%</span>')

    median_html = h.car_cell(median_car)
    mean_html = h.car_cell(mean_car)
    # B-107: mean gross stock return (abs_rtn = car + benchmark).
    mean_abs_return = row_data.get("mean_abs_return")
    abs_rtn_html = h.car_cell(mean_abs_return)
    if outlier and mean_car is not None:
        mean_html += ('<span class="ml-1 text-amber-600" '
                      'title="Outlier - top single trade dominates; see Stage 4.5 '
                      'fix.">&#9888;</span>')
    # Phase 0: divergence badge (mean vs median @ T+90). Only emitted when the
    # trigger fires AND a mean is shown, and only once even if the outlier_flag
    # badge above is also present (different signals, but avoid double amber).
    if div_fired and not outlier:
        mean_html += h.divergence_badge(div_gap_pp)
    edge_html = h.car_cell(edge)
    status_html = h.status_pill(status)
    disabled_attr = ' disabled' if status == "gated" else ''
    action = "deprecate" if not deprecated else "reactivate"
    btn_label = "Reactivate" if deprecated else "Deprecate"
    btn_html = (
        f'<button class="text-[10px] px-2 py-1 rounded border border-slate-300 '
        f'text-slate-600 hover:border-rose-400 hover:text-rose-600 '
        f'disabled:opacity-40 disabled:cursor-not-allowed" '
        f'data-signal-id="{h.esc(sid)}" data-action="{action}" '
        f'onclick="onDeprecateClick(event)"{disabled_attr} '
        f'title="Stop new evaluations of this signal. Existing firings preserved.">'
        f'{btn_label}</button>'
    )

    # Sprint 14 Phase 3 (B-067): Level-1 trajectory sparkline + 3m trend.
    # Reads from cohort_performance.json's per-group entry (keyed by short
    # sid). B-099 (Sprint 24): b1 is now in the cohort export. b2 is a
    # suppression signal — no CAR series; show suppression count instead.
    grp = (cohort_groups or {}).get(sid) or {}
    color_hex = grp.get("color_hex") or h.TIER_PALETTE.get(sid, {}).get("hex", "#94a3b8")
    label = grp.get("label") or sid.upper()
    if sid == "b2":
        # B2 suppresses S1 signals; it fires no buy signals of its own so a
        # CAR sparkline would always be empty. Show suppression count from
        # the scoreboard row_data instead (trades = number of S1 suppressions).
        suppress_n = int(trades) if trades else 0
        if suppress_n:
            traj_html = (
                '<span class="text-[10px] text-slate-500 tabular-nums">'
                f'{suppress_n} S1 suppressed</span>'
            )
        else:
            traj_html = '<span class="text-slate-300">&mdash;</span>'
        trend_html = '<span class="text-slate-300">&mdash;</span>'
    else:
        traj_html = h.cohort_sparkline_svg(grp.get("sparkline_points") or [], color_hex)
        trend_html = h.cohort_trend_cell_inner(grp.get("trend_3m_vs_prior3m_t30"))

    return (
        f'<tr class="cohort-row border-t border-slate-100 cursor-pointer '
        f'hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-300" '
        f'data-signal-tile data-signal-id="{h.esc(sid)}" '
        f'data-signal-group="{h.esc(sid)}" '
        f'data-signal-label="{h.esc(label)}" '
        f'tabindex="0" role="button" aria-label="Open {h.esc(label)} cohort chart">'
        f'<td class="px-3 py-2">{h.render_badge(sid)}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums">{trades_html}</td>'
        f'<td class="px-3 py-2 tabular-nums">{hit_html}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums">{median_html}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums">{mean_html}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums">{abs_rtn_html}</td>'
        f'<td class="px-3 py-2 text-right tabular-nums">{edge_html}</td>'
        f'<td class="px-2 py-1 align-middle cohort-traj">{traj_html}</td>'
        f'<td class="px-2 py-1 whitespace-nowrap cohort-trend">{trend_html}</td>'
        f'<td class="px-3 py-2">{status_html}</td>'
        f'<td class="px-3 py-2 text-right">{btn_html}</td>'
        f'<td class="px-2 py-2 text-slate-300 cohort-chev">'
        f'<span aria-hidden="true">&#9656;</span></td>'
        f'</tr>'
    )


def _scoreboard(signals_data: dict, status_overrides: dict,
                horizon_key: str = "t30",
                cohort_groups: dict | None = None) -> str:
    horizons = signals_data.get("horizon_aggregates") or {}
    h_data = horizons.get(horizon_key) or {}
    base_rate = h_data.get("base_rate")
    # Phase 0: the divergence warning is always computed at T+90, independent
    # of the displayed horizon. Pull the T+90 per-signal aggregates once.
    t90_signals = (horizons.get("t90") or {}).get("signals") or {}
    rows = "".join(
        _row_for_signal(sid, h_data, base_rate, status_overrides, horizon_key,
                        t90_signals=t90_signals, cohort_groups=cohort_groups)
        for sid in h.SIGNAL_DISPLAY_ORDER
    )
    base_str = "-" if base_rate is None else f"{base_rate:.1f}%"
    caption = (
        '<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">'
        f'Window: {h.esc(HORIZON_LABELS.get(horizon_key, horizon_key))} &middot; '
        f'base rate {base_str} = % of random {h.esc(horizon_key.upper())} FTSE All-Share '
        'holds positive. Hit % shows percent of trades that beat that base. '
        '&#9888; = N<20 or single-outlier domination. Base rate at T+90 is '
        'artifactually high due to trailing market regime - interpret '
        'hit-% comparison cautiously.</div>'
    )
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        # B-058 (2026-05-22): all-time toggle. Checked = use
        # horizon_aggregates_all (no firing-date cutoff). Unchecked =
        # use horizon_aggregates (trailing 365d, original behaviour).
        # Default unchecked so existing reads of the scoreboard don't
        # shift; older firings from B-023's recovered bundled filings
        # are now accessible via opt-in toggle.
        '<div class="flex items-center justify-between px-4 py-3 '
        'border-b border-slate-100 flex-wrap gap-2">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500">'
        'Per-signal scoreboard</h2>'
        '<label class="text-[11px] text-slate-500 inline-flex items-center '
        'gap-1.5 cursor-pointer">'
        '<input type="checkbox" id="scoreboardAllTime" '
        'class="rounded border-slate-300" />'
        'Include all-time firings (default: trailing 365 d)'
        '</label>'
        '</div>'
        '<div class="overflow-x-auto">'
        '<table class="w-full min-w-[700px] text-xs tabular-nums" id="scoreboard">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left w-[7%]">Signal</th>'
        '<th class="px-3 py-2 text-right w-[6%]">N</th>'
        '<th class="px-3 py-2 text-left w-[11%]">Hit / Base</th>'
        '<th class="px-3 py-2 text-right w-[9%]">Median CAR</th>'
        '<th class="px-3 py-2 text-right w-[9%]">Mean CAR</th>'
        '<th class="px-3 py-2 text-right w-[8%]" '
        'title="B-107: mean gross stock return at this horizon (CAR + benchmark). '
        'A positive CAR with negative Abs means the sector also fell.">Abs Rtn</th>'
        '<th class="px-3 py-2 text-right w-[8%]" title="edge = mean_car - benchmark mean">Edge</th>'
        '<th class="px-2 py-2 text-left w-[21%]" '
        'title="Trajectory of mean CAR @ T+30 by monthly cohort, inception to date. '
        'Path breaks at months with no fired signals.">Trajectory</th>'
        '<th class="px-2 py-2 text-left w-[9%]" '
        'title="Last-3-months mean CAR @ T+30 minus prior-3-months.">3m trend</th>'
        '<th class="px-3 py-2 text-left w-[8%]">Status</th>'
        '<th class="px-3 py-2 text-right w-[10%]">Deprecate</th>'
        '<th class="px-2 py-2 w-[2%]"><span class="sr-only">Open chart</span></th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table></div>{caption}'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Sprint 14 Phase 7 (B-071): feature flag for the LEGACY trailing-12-month
# cumulative-net-CAR line chart (the `#diagChart` section below). The Sprint 14
# cohort view (augmented performance table -> focus-mode Level-2 chart ->
# drill-down) is the intended replacement for this chart. During the A/B period
# BOTH render so the old chart stays available for comparison.
#
# Flip this to False (one-line change, then re-run build_dashboard.py) to retire
# the legacy chart and leave the cohort view as the sole CAR surface. Default
# True = nothing changes until the release call is made. When False, render()
# omits the section and rebuildDiag() (in _chart_js) no-ops on the missing
# canvas, so flipping the flag never throws.
SHOW_LEGACY_CAR_LINE_CHART = True


def _diagnostics_chart_section() -> str:
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg p-4">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3" '
        'id="diagTitle">Cumulative net CAR - trailing 12 months</h2>'
        '<div class="relative h-64"><canvas id="diagChart"></canvas></div>'
        '<div id="diagLegend" class="flex flex-wrap gap-3 text-[10px] mt-2"></div>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Cohort tiles (Performance page redesign v1 — FE Sprint 1)
#
# Three tiles in a `grid-cols-1 md:grid-cols-3` layout. Each tile reads from
# `signals.cohorts_v2` (new shape from backend Sprint 5). Lookback dropdown
# per tile dispatches a `lookbackChange` custom event scoped by `data-tile`;
# the auto-wire script swaps the table body client-side using data embedded
# in `window.__perfData.cohorts_v2`.
#
# Spec: docs/specs/performance-page-redesign-v1.md §1 + §1.2 + §1.5
# Mockup target: docs/specs/mockups/performance-preview.html
# ---------------------------------------------------------------------------

# Default initial horizon / lookback for server-side first render.
COHORT_DEFAULT_HORIZON  = "t30"
# B-054 (2026-05-22): default lookback REVERTED to 90d on the same day.
# The 30d default surfaced an empty intersection with T+90 / T+252 horizons
# (no firing can be both <30 days old AND >90 days mature) and also hid
# the bulk of B-023's recovered bundled-filing data, which is dated months
# to years old. 30d remains in the dropdown as an opt-in for recent-only
# views; default stays at 90d so longer-horizon tiles have data.
COHORT_DEFAULT_LOOKBACK = "90d"

LOOKBACK_LABELS = h.LOOKBACK_LABELS  # Sprint 7 fix #4 — see render_helpers

# Tile config — keeps render() callable concise.
# Drill-href patterns use filename-encoded URLs (one HTML file per cohort key,
# rendered by render_performance_drilldown.py via build_dashboard.py). Sectors
# whose names contain spaces / punctuation are slugified — see `_slug_for_url`.
COHORT_TILES = [
    {
        "tile_id":          "bucket",
        "title":            "By transaction size",
        "scope_note":       None,   # B-104: all buy signals now included
        "col_label":        "Bucket",
        "drill_href":       "performance-bucket-{key}.html",
        "caption_unit":     "firings",
    },
    {
        "tile_id":          "role",
        "title":            "By director role",
        "scope_note":       None,            # role labels are self-explanatory
        "col_label":        "Role",
        "drill_href":       "performance-role-{key}.html",
        "caption_unit":     "firings",
    },
    {
        "tile_id":          "sector",
        "title":            "By sector",
        "scope_note":       "Top 3 + bottom 2",
        "col_label":        "Sector",
        "drill_href":       "performance-sector-{key}.html",
        "caption_unit":     "sectors",
    },
]


def _slug_for_url(key) -> str:
    """Convert a cohort key into a filename-safe slug.

    Examples:
        '100k-500k'         → '100k-500k'    (unchanged — already safe)
        'ceo_cfo'           → 'ceo_cfo'      (unchanged)
        'Communication Services' → 'Communication-Services'  (spaces → hyphen)
        'Health Care'       → 'Health-Care'
    """
    import re
    s = str(key)
    # Replace runs of non-alphanumeric (except hyphen / underscore) with hyphen.
    return re.sub(r'[^A-Za-z0-9_-]+', '-', s).strip('-')


def _hit_color_class(hit_pct, base_rate):
    """Hit% colour rule per spec §1.5."""
    if hit_pct is None:
        return "text-slate-500"
    try:
        hp = float(hit_pct)
        br = float(base_rate)
    except (TypeError, ValueError):
        return "text-slate-700"
    if hp >= br:
        return "text-emerald-600"
    if hp < br * 0.85:
        return "text-rose-600"
    return "text-slate-700"


def _sector_slice_top3_bottom2(rows):
    """Sector tile: top 3 + bottom 2 by hit% (per spec §1.3). Inserts a
    divider sentinel row `{"divider": True}` between the two groups. Used
    only when the cohort_id is 'sector' AND at least 5 rows exist.
    """
    if len(rows) < 5:
        return list(rows)
    sorted_desc = sorted(
        rows, key=lambda r: (-(r.get("hit_pct") or 0), r.get("key") or "")
    )
    top3 = sorted_desc[:3]
    bottom2 = sorted_desc[-2:]
    return top3 + [{"divider": True}] + bottom2


def _render_cohort_row(r, base_rate, drill_href_pattern):
    """One <tr> for a cohort tile. Reads {key, label, n, hit_pct, median_car}."""
    if r.get("divider"):
        return (
            '<tr class="border-t border-slate-200 bg-slate-50">'
            '<td colspan="4" class="px-2 py-1 text-[10px] text-slate-400 italic '
            'text-center uppercase tracking-wide">'
            '— lowest performers below —'
            '</td></tr>'
        )
    key = r.get("key") or ""
    label = r.get("label") or key
    n = r.get("n")
    hit_pct = r.get("hit_pct")
    median_car = r.get("median_car")
    # Slug-encode the key for filename-safe URLs (e.g. "Health Care" →
    # "Health-Care"). Plain bucket / role keys pass through unchanged.
    drill_url = drill_href_pattern.format(key=h.esc(_slug_for_url(key)))
    aria_label = f"View {label} drill-down"
    hit_cls = _hit_color_class(hit_pct, base_rate)
    if hit_pct is None:
        hit_text = "—"
    else:
        hit_text = f"{float(hit_pct):.1f}%"
    # `median_car` from cohorts_v2 is already in percent terms (e.g. 0.4 = 0.4%)
    # — `h.car_cell` / `h.pct` expect percent inputs, NOT fractions. Earlier
    # version divided by 100 here, which rendered every value an order of
    # magnitude too small ("+0.40%" became "+0.00%"). Test 13 pins this.
    car_html = h.car_cell(
        None if median_car is None else float(median_car)
    )
    return (
        '<tr class="clickable group border-t border-slate-100 cursor-pointer '
        'hover:bg-indigo-50 relative" '
        f'data-href="{h.esc(drill_url)}" tabindex="0" role="link" '
        f'aria-label="{h.esc(aria_label)}">'
        f'<td class="px-2 py-2 text-slate-700 truncate">{h.esc(label)}</td>'
        f'<td class="px-2 py-2 text-right tabular-nums text-slate-700">'
        f'{h.n_band_cell(n)}</td>'
        f'<td class="px-2 py-2 text-right tabular-nums {hit_cls}">{hit_text}</td>'
        f'<td class="px-2 py-2 text-right tabular-nums relative">'
        f'{car_html}'
        '<span class="chev absolute right-2 top-1/2 -translate-y-1/2 '
        'opacity-0 group-hover:opacity-60 text-slate-400">&rsaquo;</span>'
        '</td></tr>'
    )


def _cohort_tile(tile_cfg: dict, cohorts_v2: dict, base_rate: float) -> str:
    """Render one cohort tile (bucket / role / sector). Pure — no DB.

    Initial server render uses `COHORT_DEFAULT_HORIZON` × `COHORT_DEFAULT_LOOKBACK`.
    The auto-wire script in `_cohort_section_script()` re-renders the tbody
    on lookback or horizon changes by reading from `window.__perfData.cohorts_v2`.
    """
    tile_id = tile_cfg["tile_id"]
    tile_data = (cohorts_v2 or {}).get(
        {"bucket": "by_value_bucket",
         "role":   "by_role",
         "sector": "by_sector"}[tile_id], {}
    )
    cell = (tile_data.get(COHORT_DEFAULT_HORIZON) or {}).get(
        COHORT_DEFAULT_LOOKBACK
    ) or {"rows": [], "total_n": 0}
    rows = cell.get("rows") or []
    if tile_id == "sector":
        rows = _sector_slice_top3_bottom2(rows)
    total_n = cell.get("total_n") or 0

    # Lookback dropdown.
    options = "".join(
        f'<option value="{lb_key}"'
        f'{" selected" if lb_key == COHORT_DEFAULT_LOOKBACK else ""}>'
        f'{lb_label}</option>'
        for lb_key, lb_label in LOOKBACK_LABELS
    )

    # Body rows (initial render — JS will re-render on dropdown change).
    if rows:
        body_html = "".join(
            _render_cohort_row(r, base_rate, tile_cfg["drill_href"])
            for r in rows
        )
    else:
        # Sprint 7 fix #3 (2026-05-22): context-aware empty state. Tells
        # the user which horizon × lookback is empty so they know what
        # to try next (often: switch to a longer lookback or shorter
        # horizon).
        body_html = (
            f'<tr><td colspan="4" class="px-2 py-6 text-center '
            f'text-[11px] text-slate-400 italic">'
            f'No firings at <span class="font-medium">'
            f'{COHORT_DEFAULT_HORIZON.upper()} &times; {COHORT_DEFAULT_LOOKBACK}'
            f'</span> &mdash; try a longer lookback or shorter horizon.'
            f'</td></tr>'
        )

    # Scope_note line (optional).
    scope_html = ""
    if tile_cfg.get("scope_note"):
        scope_html = (
            f'<p class="text-[10px] text-slate-400 italic mt-0.5">'
            f'{h.esc(tile_cfg["scope_note"])}</p>'
        )

    # Footer caption.
    caption = (
        f'N={total_n} over {COHORT_DEFAULT_LOOKBACK} '
        f'&middot; &searr; rows clickable'
    )

    return (
        '<section class="bg-white border border-slate-200 rounded-lg p-4" '
        f'data-tile="{tile_id}" '
        f'data-drill-pattern="{h.esc(tile_cfg["drill_href"])}" '
        f'data-base-rate="{float(base_rate):.2f}">'
        '<div class="flex items-center justify-between mb-3">'
        '<div>'
        '<h3 class="text-xs uppercase tracking-wide text-slate-500">'
        f'{h.esc(tile_cfg["title"])}</h3>'
        f'{scope_html}'
        '</div>'
        f'<select class="lookback-select text-[11px] border border-slate-300 '
        'rounded px-1.5 py-0.5 bg-white tabular-nums text-slate-600">'
        f'{options}'
        '</select>'
        '</div>'
        '<table class="w-full text-xs tabular-nums">'
        '<thead class="text-slate-500 uppercase tracking-wide text-[10px] '
        'border-b border-slate-200">'
        '<tr>'
        f'<th class="text-left px-2 py-1.5 font-medium w-[40%]">'
        f'{h.esc(tile_cfg["col_label"])}</th>'
        '<th class="text-right px-2 py-1.5 font-medium w-[15%]">N</th>'
        '<th class="text-right px-2 py-1.5 font-medium w-[20%]">Hit %</th>'
        '<th class="text-right px-2 py-1.5 font-medium w-[25%]">Median CAR</th>'
        '</tr></thead>'
        f'<tbody class="cohort-tbody">{body_html}</tbody>'
        '</table>'
        f'<p class="tile-caption text-[10px] text-slate-400 mt-2">{caption}</p>'
        '</section>'
    )


def _signal_overview_section(cohort_groups: dict) -> str:
    """B-073: small-multiples signal grid.

    One card per signal in SIGNAL_DISPLAY_ORDER. Each card has:
      - server-rendered badge + N label (populated by JS)
      - a skeleton <canvas> 64px tall (Chart.js mini line chart drawn by JS)
      - signal short label
    Clicking a card opens focus mode for that signal.
    JS looks for the grid by id `signalOverviewGrid` and populates canvases.
    """
    cards = []
    for sid in h.SIGNAL_DISPLAY_ORDER:
        badge_html = h.render_badge(sid)
        label = sid.upper()
        grp = (cohort_groups or {}).get(sid) or {}
        full_label = grp.get("label") or label
        cards.append(
            f'<div class="bg-white border border-slate-200 rounded-lg p-2 '
            f'cursor-pointer hover:border-indigo-400 hover:shadow-sm '
            f'transition-all" '
            f'data-signal-group="{h.esc(sid)}" '
            f'data-signal-label="{h.esc(full_label)}">'
            f'<div class="flex items-center justify-between mb-1">'
            f'{badge_html}'
            f'<span class="text-[10px] text-slate-400 tabular-nums overview-n"></span>'
            f'</div>'
            f'<canvas height="64" style="display:block;width:100%;height:64px"></canvas>'
            f'<div class="mt-1 text-[10px] text-slate-500 truncate">'
            f'{h.esc(full_label)}</div>'
            f'</div>'
        )
    cards_html = "".join(cards)
    return (
        '<div class="mx-6 mb-2">'
        '<h2 class="text-[11px] uppercase tracking-wider text-slate-500 '
        'font-semibold">Signal overview</h2>'
        '</div>'
        '<div id="signalOverviewGrid" '
        'class="mx-6 mb-4 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 '
        'lg:grid-cols-6 gap-3">'
        f'{cards_html}'
        '</div>'
    )


def _signal_overview_script() -> str:
    """B-073: JS that populates the signal overview grid canvases.

    Reads window.__cohortData, renders a mini Chart.js line chart per signal,
    respects the active horizon (window.__cohortActiveHorizon), and listens to
    horizonChange to rebuild all thumbnails.
    Clicking a card calls openFocus(group, label) if defined.
    """
    return r"""<script>
(function(){
  'use strict';
  var GRID = document.getElementById('signalOverviewGrid');
  if (!GRID) return;
  var BLOB = window.__cohortData || {order: [], groups: {}};
  var GROUPS = BLOB.groups || {};
  // Map of sid -> Chart instance for cleanup on rebuild.
  var _miniCharts = {};

  function activeHorizon(){
    return window.__cohortActiveHorizon || 't30';
  }

  function metricKey(h){
    return 'mean_car_' + h;
  }

  function buildSignalOverview(){
    var h = activeHorizon();
    var mk = metricKey(h);
    var cards = GRID.querySelectorAll('[data-signal-group]');
    cards.forEach(function(card){
      var sid = card.getAttribute('data-signal-group');
      var grp = GROUPS[sid] || {};
      var months = grp.months || [];
      var hex = grp.color_hex || '#94a3b8';

      // Update N label.
      var nEl = card.querySelector('.overview-n');
      if (nEl){
        var totalN = months.reduce(function(acc, m){
          return acc + (m.n_signals || 0);
        }, 0);
        nEl.textContent = totalN ? 'N=' + totalN : '';
      }

      var canvas = card.querySelector('canvas');
      if (!canvas) return;

      // Destroy prior chart if any.
      if (_miniCharts[sid]){ _miniCharts[sid].destroy(); delete _miniCharts[sid]; }

      // Extract data points — null for gaps.
      var pts = months.map(function(m, i){
        var v = m[mk];
        return {x: i, y: (v == null ? null : v * 100)};
      });
      var hasData = pts.some(function(p){ return p.y !== null; });
      if (!hasData){
        // Empty state: grey background, no chart.
        canvas.style.background = '#f8fafc';
        return;
      }
      canvas.style.background = '';

      if (typeof Chart === 'undefined') return;

      _miniCharts[sid] = new Chart(canvas, {
        type: 'line',
        data: {
          datasets: [{
            data: pts,
            showLine: true,
            tension: 0,
            spanGaps: false,
            borderColor: hex,
            borderWidth: 1.5,
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false
          }]
        },
        options: {
          responsive: false,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: { display: false },
            tooltip: { enabled: false }
          },
          scales: {
            x: { type: 'linear', display: false },
            y: { display: false }
          }
        }
      });
    });
  }

  // Wire card clicks to openFocus.
  GRID.querySelectorAll('[data-signal-group]').forEach(function(card){
    card.addEventListener('click', function(){
      var sid = card.getAttribute('data-signal-group');
      var label = card.getAttribute('data-signal-label') || sid;
      if (typeof window.openFocus === 'function'){
        window.openFocus(sid, label);
      } else {
        // openFocus is defined inside an IIFE in _cohort_focus_script;
        // expose it on window so we can call it from here.
        var rows = document.querySelectorAll('tr.cohort-row[data-signal-group="' + sid + '"]');
        if (rows.length) rows[0].click();
      }
    });
  });

  // Listen to horizonChange to rebuild.
  document.addEventListener('horizonChange', function(){
    buildSignalOverview();
  });

  // Initial build.
  if (typeof Chart !== 'undefined'){
    buildSignalOverview();
  } else {
    window.addEventListener('load', buildSignalOverview);
  }
})();
</script>"""


def _cohort_section(signals_data: dict) -> str:
    """Render the three-tile cohort section. Reads `signals.cohorts_v2`.

    If `cohorts_v2` is missing (e.g., reading a pre-Sprint-5 signals.json),
    falls back to an empty state so the page still renders.
    """
    cohorts_v2 = signals_data.get("cohorts_v2") or {}
    # Use the t30 base rate (the page's default horizon) for hit-color comparisons.
    base_rate = (
        ((signals_data.get("horizon_aggregates") or {})
         .get(COHORT_DEFAULT_HORIZON) or {}).get("base_rate") or 50.0
    )
    tiles_html = "".join(
        _cohort_tile(cfg, cohorts_v2, base_rate) for cfg in COHORT_TILES
    )
    # B-078: equal-weighted / value-weighted toggle. Default = EW (existing).
    # JS reads vw_mean_car from window.__perfData.cohorts_v2 rows and swaps
    # the median_car column when VW is active. No data model change needed --
    # both fields are already in the JSON.
    vw_toggle_html = (
        '<label class="text-[11px] text-slate-500 inline-flex items-center '
        'gap-1.5 cursor-pointer">'
        '<input type="checkbox" id="cohortVWToggle" '
        'class="rounded border-slate-300" />'
        'Value-weighted means (default: equal-weighted)'
        '</label>'
    )
    header_html = (
        '<div class="m-6 mb-2 flex items-center justify-between">'
        '<h2 class="text-[11px] uppercase tracking-wider '
        'text-slate-500 font-semibold">Cohort cuts</h2>'
        + vw_toggle_html
        + '</div>'
    )
    grid_html = (
        '<div class="m-6 mt-2 grid grid-cols-1 md:grid-cols-3 gap-4">'
        f'{tiles_html}'
        '</div>'
    )
    # B-078: script to wire the VW toggle. Intercepts re-renders so the
    # median_car column is swapped to vw_mean_car when the checkbox is checked.
    # Additive -- does not touch any existing auto-wire logic.
    vw_script = r"""<script>
(function(){
  'use strict';
  var chk = document.getElementById('cohortVWToggle');
  if (!chk) return;
  // Patch the renderRow function defined in the cohort auto-wire block to
  // respect the VW flag. We do this by overriding window.__vwActive and
  // re-rendering all tiles on toggle change.
  window.__vwActive = false;
  chk.addEventListener('change', function(){
    window.__vwActive = !!chk.checked;
    // Re-render all tiles using current horizon + lookback values.
    var horizon = window.__currentHorizon || 't30';
    document.querySelectorAll('section[data-tile]').forEach(function(section){
      var dropdown = section.querySelector('select.lookback-select');
      var lookback = dropdown ? dropdown.value : '90d';
      var tile = section.getAttribute('data-tile');
      var pattern = section.getAttribute('data-drill-pattern');
      var base = parseFloat(section.getAttribute('data-base-rate') || '50');
      var TILE_TO_KEY = {bucket:'by_value_bucket', role:'by_role', sector:'by_sector'};
      var pd = window.__perfData || {};
      var v2 = (pd.cohorts_v2 || {})[TILE_TO_KEY[tile]] || {};
      var cell = ((v2[horizon] || {})[lookback]) || {rows:[], total_n:0};
      var rows = cell.rows || [];
      // Top3+bottom2 slice for sector tile.
      if (tile === 'sector' && rows.length >= 5) {
        var sorted = rows.slice().sort(function(a,b){
          var ha = a.hit_pct == null ? -Infinity : a.hit_pct;
          var hb = b.hit_pct == null ? -Infinity : b.hit_pct;
          return hb !== ha ? hb - ha : (a.key||'').localeCompare(b.key||'');
        });
        rows = sorted.slice(0,3).concat([{divider:true}], sorted.slice(-2));
      }
      var tbody = section.querySelector('tbody.cohort-tbody');
      if (!tbody) return;
      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="px-2 py-6 text-center text-[11px] text-slate-400 italic">No firings at <span class="font-medium">' + horizon.toUpperCase() + ' &times; ' + lookback + '</span> &mdash; try a longer lookback or shorter horizon.</td></tr>';
        return;
      }
      function esc(s){ return String(s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[c]; }); }
      function carColor(v){ if (v==null) return 'text-slate-300'; if (v>0.05) return 'text-emerald-600'; if (v<-0.05) return 'text-rose-600'; return 'text-slate-500'; }
      function hitColor(hp,base){ if (hp==null) return 'text-slate-500'; if (hp>=base) return 'text-emerald-600'; if (hp<base*0.85) return 'text-rose-600'; return 'text-slate-700'; }
      function nBandHTML(n){ if (!n) return '<span class="text-slate-300 italic">-</span>'; if (n<20) return n+' <span class="text-amber-600">&#9888;</span>'; return String(n); }
      tbody.innerHTML = rows.map(function(r){
        if (r.divider) return '<tr class="border-t border-slate-200 bg-slate-50"><td colspan="4" class="px-2 py-1 text-[10px] text-slate-400 italic text-center uppercase tracking-wide">- lowest performers below -</td></tr>';
        var slug = String(r.key||'').replace(/[^A-Za-z0-9_-]+/g,'-').replace(/^-+|-+$/g,'');
        var url = pattern.replace('{key}', slug);
        // B-078: use vw_mean_car when toggle is active and data present
        var carVal = (window.__vwActive && r.vw_mean_car != null) ? r.vw_mean_car : r.median_car;
        var colHeader = window.__vwActive ? 'VW Mean' : 'Median';
        var carText = carVal == null ? '<span class="text-slate-300">-</span>' : '<span class="' + carColor(carVal) + '">' + carVal.toFixed(2) + '%</span>';
        var hp = r.hit_pct;
        var hitText = hp == null ? '-' : hp.toFixed(1) + '%';
        return '<tr class="clickable group border-t border-slate-100 cursor-pointer hover:bg-indigo-50 relative" data-href="' + esc(url) + '" tabindex="0" role="link" aria-label="View ' + esc(r.label||r.key) + ' drill-down">' +
          '<td class="px-2 py-2 text-slate-700 truncate">' + esc(r.label||r.key) + '</td>' +
          '<td class="px-2 py-2 text-right tabular-nums text-slate-700">' + nBandHTML(r.n) + '</td>' +
          '<td class="px-2 py-2 text-right tabular-nums ' + hitColor(hp,base) + '">' + hitText + '</td>' +
          '<td class="px-2 py-2 text-right tabular-nums relative">' + carText +
          '<span class="chev absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-60 text-slate-400">&rsaquo;</span>' +
          '</td></tr>';
      }).join('');
      // Update header column label to reflect EW vs VW
      var th = section.querySelector('th:last-child');
      if (th) th.textContent = window.__vwActive ? 'VW Mean CAR' : 'Median CAR';
      // Re-wire click handlers
      section.querySelectorAll('tr.clickable').forEach(function(tr){
        if (tr.dataset.wired === '1') return;
        tr.dataset.wired = '1';
        tr.addEventListener('click', function(){ var href = tr.getAttribute('data-href'); if (href) window.location.href = href; });
        tr.addEventListener('keydown', function(e){ if (e.key==='Enter'||e.key===' '){ e.preventDefault(); var href = tr.getAttribute('data-href'); if (href) window.location.href = href; } });
      });
    });
  });
})();
</script>"""
    return header_html + grid_html + vw_script


def _cohort_section_script() -> str:
    """Auto-wire script for cohort tiles: lookback dropdowns + clickable rows
    + keyboard accessibility. Single delegated block, idempotent."""
    return r"""<script>
(function() {
  'use strict';
  var TILE_TO_KEY = { bucket: 'by_value_bucket', role: 'by_role', sector: 'by_sector' };
  function hitColor(hp, base) {
    if (hp == null) return 'text-slate-500';
    if (hp >= base) return 'text-emerald-600';
    if (hp < base * 0.85) return 'text-rose-600';
    return 'text-slate-700';
  }
  function carColor(v) {
    if (v == null) return 'text-slate-300';
    if (v > 0.05) return 'text-emerald-600';
    if (v < -0.05) return 'text-rose-600';
    return 'text-slate-500';
  }
  function nBandHTML(n) {
    if (n == null || n === 0)
      return '<span class="text-slate-300 italic">—</span>';
    if (n < 20)
      return n + ' <span class="text-amber-600" title="N<20 preliminary, wait for more firings">&#9888;</span>';
    return String(n);
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"})[c];
    });
  }
  function renderRow(r, base, pattern) {
    if (r.divider) {
      return '<tr class="border-t border-slate-200 bg-slate-50">' +
             '<td colspan="4" class="px-2 py-1 text-[10px] text-slate-400 italic text-center uppercase tracking-wide">' +
             '— lowest performers below —' +
             '</td></tr>';
    }
    // Slug-encode the key (matches Python `_slug_for_url`): non-alphanumeric
    // characters (except hyphen / underscore) become a single hyphen.
    var slug = String(r.key || '').replace(/[^A-Za-z0-9_-]+/g, '-')
                                  .replace(/^-+|-+$/g, '');
    var url = pattern.replace('{key}', slug);
    var hp = r.hit_pct;
    var md = r.median_car;
    // `md` is already a percent value (e.g. 0.4 = 0.4%) — pass directly to
    // carColor whose thresholds (0.05 / -0.05) are also percent-terms.
    var carText = (md == null) ? '<span class="text-slate-300">-</span>'
                                : '<span class="' + carColor(md) + '">' + md.toFixed(2) + '%</span>';
    var hitText = (hp == null) ? '—' : hp.toFixed(1) + '%';
    return '<tr class="clickable group border-t border-slate-100 cursor-pointer hover:bg-indigo-50 relative" ' +
           'data-href="' + esc(url) + '" tabindex="0" role="link" aria-label="View ' + esc(r.label || r.key) + ' drill-down">' +
           '<td class="px-2 py-2 text-slate-700 truncate">' + esc(r.label || r.key) + '</td>' +
           '<td class="px-2 py-2 text-right tabular-nums text-slate-700">' + nBandHTML(r.n) + '</td>' +
           '<td class="px-2 py-2 text-right tabular-nums ' + hitColor(hp, base) + '">' + hitText + '</td>' +
           '<td class="px-2 py-2 text-right tabular-nums relative">' + carText +
           '<span class="chev absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-60 text-slate-400">&rsaquo;</span>' +
           '</td></tr>';
  }
  function sliceTopBottom(rows, tile) {
    if (tile !== 'sector' || rows.length < 5) return rows.slice();
    var sorted = rows.slice().sort(function(a, b) {
      var ha = a.hit_pct == null ? -Infinity : a.hit_pct;
      var hb = b.hit_pct == null ? -Infinity : b.hit_pct;
      if (hb !== ha) return hb - ha;
      return (a.key || '').localeCompare(b.key || '');
    });
    return sorted.slice(0, 3).concat([{divider: true}], sorted.slice(-2));
  }
  function rerender(section, horizon, lookback) {
    var tile = section.getAttribute('data-tile');
    var pattern = section.getAttribute('data-drill-pattern');
    var base = parseFloat(section.getAttribute('data-base-rate') || '50');
    var pd = window.__perfData || {};
    var v2 = (pd.cohorts_v2 || {})[TILE_TO_KEY[tile]] || {};
    var cell = ((v2[horizon] || {})[lookback]) || {rows: [], total_n: 0};
    var rows = sliceTopBottom(cell.rows || [], tile);
    var tbody = section.querySelector('tbody.cohort-tbody');
    if (rows.length === 0) {
      // Sprint 7 fix #3 (2026-05-22): context-aware empty state mirrors
      // the server-side version — tells the user exactly which
      // horizon × lookback combo is empty.
      tbody.innerHTML = '<tr><td colspan="4" class="px-2 py-6 text-center text-[11px] text-slate-400 italic">No firings at <span class="font-medium">' + horizon.toUpperCase() + ' &times; ' + lookback + '</span> &mdash; try a longer lookback or shorter horizon.</td></tr>';
    } else {
      tbody.innerHTML = rows.map(function(r) { return renderRow(r, base, pattern); }).join('');
    }
    var cap = section.querySelector('.tile-caption');
    if (cap) cap.textContent = 'N=' + (cell.total_n || 0) + ' over ' + lookback + ' · ↘ rows clickable';
  }
  function wireClickable(tr) {
    if (tr.dataset.wired === '1') return;
    tr.dataset.wired = '1';
    tr.addEventListener('click', function() {
      var href = tr.getAttribute('data-href');
      if (href) window.location.href = href;
    });
    tr.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        var href = tr.getAttribute('data-href');
        if (href) window.location.href = href;
      }
    });
  }
  // Wire each tile.
  document.querySelectorAll('section[data-tile]').forEach(function(section) {
    var dropdown = section.querySelector('select.lookback-select');
    if (dropdown) {
      dropdown.addEventListener('change', function() {
        var horizon = window.__currentHorizon || 't30';
        rerender(section, horizon, dropdown.value);
        section.querySelectorAll('tr.clickable').forEach(wireClickable);
      });
    }
  });
  // Page-level horizon change re-renders all tiles.
  document.addEventListener('horizonChange', function(e) {
    window.__currentHorizon = (e.detail && e.detail.horizon) || 't30';
    document.querySelectorAll('section[data-tile]').forEach(function(section) {
      var dropdown = section.querySelector('select.lookback-select');
      var lookback = dropdown ? dropdown.value : '90d';
      rerender(section, window.__currentHorizon, lookback);
      section.querySelectorAll('tr.clickable').forEach(wireClickable);
    });
  });
  // Initial wire-up of server-rendered rows.
  document.querySelectorAll('section[data-tile] tr.clickable').forEach(wireClickable);
})();
</script>"""


def _cohort_value_section(signals_data: dict) -> str:
    bucket = (signals_data.get("cohorts") or {}).get("by_value_bucket") or {}
    keys = ["1k-25k", "25k-100k", "100k-500k", "500k+"]
    labels = {"1k-25k": "GBP 1-25k", "25k-100k": "GBP 25-100k",
              "100k-500k": "GBP 100-500k", "500k+": "GBP 500k+"}
    vals = [bucket.get(k) for k in keys]
    rows = []
    for k in keys:
        entry = bucket.get(k)
        # by_value_bucket only carries mean CAR per bucket in current shape;
        # render mean only and dash for N / hit if upstream doesn't supply them.
        if isinstance(entry, dict):
            n = entry.get("n")
            mean_car = entry.get("mean_car")
            hit_pct = entry.get("hit_pct")
        else:
            n = None
            mean_car = entry
            hit_pct = None
        rows.append(
            '<tr class="border-t border-slate-100 odd:bg-slate-50">'
            f'<td class="px-3 py-2 text-slate-700">{h.esc(labels[k])}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums text-slate-700">'
            f'{"-" if n is None else int(n)}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums">{h.car_cell(mean_car)}</td>'
            f'<td class="px-3 py-2 text-right tabular-nums text-slate-700">'
            f'{"-" if hit_pct is None else f"{float(hit_pct):.1f}%"}</td>'
            '</tr>'
        )
    table_html = (
        '<table class="w-full text-xs tabular-nums mt-3 border border-slate-200 rounded">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left">Bucket</th>'
        '<th class="px-3 py-2 text-right">N</th>'
        '<th class="px-3 py-2 text-right">Mean CAR</th>'
        '<th class="px-3 py-2 text-right">Hit %</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )
    return (
        '<section class="bg-white border border-slate-200 rounded-lg p-4">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3">'
        'By director\'s transaction value</h2>'
        '<div class="relative h-40"><canvas id="cohortValue"></canvas></div>'
        f'<script>window.__cohortValue = {json.dumps(vals)};</script>'
        f'{table_html}'
        '</section>'
    )


def _cohort_sector_section(signals_data: dict) -> str:
    rows = (signals_data.get("cohorts") or {}).get("by_sector") or []
    if not rows:
        body = ('<li class="text-xs text-slate-400">Sector mapping not yet '
                'wired - see Stage 4.6.</li>')
        table_html = ""
    else:
        parts = []
        for r in rows:
            sector = h.esc(r.get("sector") or "Unknown")
            hp = r.get("hit_pct") or 0
            base = r.get("base_rate") or 0
            n = r.get("n") or 0
            bar_cls = "bg-emerald-400" if hp >= base else "bg-rose-400"
            parts.append(
                '<li class="flex items-center gap-2 text-xs">'
                f'<span class="w-32 truncate text-slate-700" title="{sector}">{sector}</span>'
                '<div class="flex-1 h-3 bg-slate-100 rounded-sm relative">'
                f'<div class="absolute inset-y-0 left-0 rounded-sm {bar_cls}" '
                f'style="width:{min(max(hp,0),100):.1f}%"></div>'
                f'<div class="absolute inset-y-0" style="left:{min(max(base,0),100):.1f}%; '
                'width:1px; background:#475569"></div>'
                '</div>'
                f'<span class="tabular-nums text-slate-700 w-16 text-right">'
                f'{hp:.1f}% (N={int(n)})</span>'
                '</li>'
            )
        body = "".join(parts)
        trows = []
        for r in rows:
            sector = h.esc(r.get("sector") or "Unknown")
            hp = r.get("hit_pct")
            n = r.get("n")
            mean_car = r.get("mean_car")
            trows.append(
                '<tr class="border-t border-slate-100 odd:bg-slate-50">'
                f'<td class="px-3 py-2 text-slate-700">{sector}</td>'
                f'<td class="px-3 py-2 text-right tabular-nums text-slate-700">'
                f'{"-" if n is None else int(n)}</td>'
                f'<td class="px-3 py-2 text-right tabular-nums">{h.car_cell(mean_car)}</td>'
                f'<td class="px-3 py-2 text-right tabular-nums text-slate-700">'
                f'{"-" if hp is None else f"{float(hp):.1f}%"}</td>'
                '</tr>'
            )
        table_html = (
            '<table class="w-full text-xs tabular-nums mt-3 border border-slate-200 rounded">'
            '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">'
            '<tr>'
            '<th class="px-3 py-2 text-left">Sector</th>'
            '<th class="px-3 py-2 text-right">N</th>'
            '<th class="px-3 py-2 text-right">Mean CAR</th>'
            '<th class="px-3 py-2 text-right">Hit %</th>'
            '</tr></thead>'
            f'<tbody>{"".join(trows)}</tbody></table>'
        )
    return (
        '<section class="bg-white border border-slate-200 rounded-lg p-4">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3">'
        'By sector - hit % vs base</h2>'
        f'<ul class="space-y-1">{body}</ul>'
        f'{table_html}'
        '</section>'
    )


_TIER_LABEL = {
    "t0": "T0 cluster+combo",
    "t1": "T1 CEO/CFO buy",
    "t2": "T2 exec buy",
    "t3": "T3 NED buy",
    "t4": "T4 other buy",
    "s1": "S1 cluster",
    "f1": "F1 first-time buy",
}


def _model_assessment(signals_data: dict) -> str:
    """Auto-compute kill / keep / watch / preliminary + narrative from T+90.

    Brief logic (stage-05-design-final.md s 1.2.5 + QA brief):
      - N>=20 and mean<0 and hit<base_rate -> KILL
      - N>=20 and mean>0 and hit>base_rate -> KEEP
      - N>=20 and (mean<0 xor hit<base_rate) -> WATCH (mixed signal)
      - 0<N<20 -> PRELIMINARY
      - N==0 -> excluded
    Plus caveats list for outlier_flag firings.
    """
    horizons = signals_data.get("horizon_aggregates") or {}
    t90 = horizons.get("t90") or {}
    base_rate = t90.get("base_rate") or 0
    sigs = t90.get("signals") or {}
    kill, keep, watch, preliminary, caveats = [], [], [], [], []
    kill_strongest = None  # tuple(sid, mean, hit, n) for narrative

    def _fmt_line(sid, d):
        mean = d.get("mean_car")
        hit = d.get("hit_pct")
        n = d.get("trades") or 0
        label = _TIER_LABEL.get(sid, sid.upper())
        return (f"{label}: mean {mean:+.2f}% T+90, hit {hit:.1f}% "
                f"(N={int(n)}, base {base_rate:.1f}%)")

    for sid in h.SIGNAL_DISPLAY_ORDER:
        d = sigs.get(sid) or {}
        n = d.get("trades") or 0
        mean = d.get("mean_car")
        hit = d.get("hit_pct")
        outlier = d.get("outlier_flag")
        if n >= 20 and mean is not None and hit is not None:
            mean_neg = mean < 0
            hit_under = hit < base_rate
            mean_pos = mean > 0
            hit_over = hit > base_rate
            if mean_neg and hit_under:
                kill.append((sid, _fmt_line(sid, d)))
                # Strongest kill = most negative mean.
                if kill_strongest is None or mean < kill_strongest[1]:
                    kill_strongest = (sid, mean, hit, n)
            elif mean_pos and hit_over:
                keep.append((sid, _fmt_line(sid, d)))
            else:
                watch.append((sid, _fmt_line(sid, d)))
        elif 0 < n < 20:
            label = _TIER_LABEL.get(sid, sid.upper())
            mean_s = "-" if mean is None else f"{mean:+.2f}%"
            hit_s = "-" if hit is None else f"{hit:.1f}%"
            preliminary.append(
                (sid,
                 f"{label}: mean {mean_s}, hit {hit_s} "
                 f"(N={int(n)}) - needs more data"))
        if outlier:
            caveats.append(f"{_TIER_LABEL.get(sid, sid.upper())}: "
                           "outlier_flag set (top single trade dominates)")

    def _badge(text, fg, bg):
        return (f'<span class="inline-flex items-center px-1.5 py-0.5 rounded '
                f'text-[10px] font-semibold {bg} {fg}">{text}</span>')

    def _block(title, items, title_cls, badge_text, badge_cls):
        if not items:
            return ""
        lis = []
        for sid, text in items:
            lis.append(
                f'<li class="flex items-start gap-2">'
                f'{_badge(badge_text, "text-white", badge_cls)}'
                f'<span class="text-xs">{h.esc(text)}</span></li>'
            )
        return (f'<div class="mb-3">'
                f'<div class="text-xs font-semibold {title_cls} mb-1">{title}</div>'
                f'<ul class="space-y-1">{"".join(lis)}</ul></div>')

    blocks = []
    blocks.append(_block("Kill candidates", kill,
                        "text-rose-700", "KILL", "bg-rose-600"))
    blocks.append(_block("Keep candidates", keep,
                        "text-emerald-700", "KEEP", "bg-emerald-600"))
    blocks.append(_block("Watch", watch,
                        "text-amber-700", "WATCH", "bg-amber-500"))
    blocks.append(_block("Preliminary (N<20)", preliminary,
                        "text-slate-700", "PRELIMINARY", "bg-slate-500"))
    if caveats:
        items = "".join(f'<li class="text-slate-600">&#9679; {h.esc(s)}</li>' for s in caveats)
        blocks.append(f'<div class="mb-3">'
                      f'<div class="text-xs font-semibold text-slate-700 mb-1">Caveats</div>'
                      f'<ul class="text-xs space-y-1">{items}</ul></div>')

    if not any([kill, keep, watch, preliminary, caveats]):
        blocks.append('<div class="text-xs text-emerald-700">'
                      'All signals within tolerance.</div>')

    # Narrative line.
    narrative = ""
    if kill_strongest is not None:
        sid, mean, hit, n = kill_strongest
        label = _TIER_LABEL.get(sid, sid.upper())
        narrative = (
            f'<p class="text-xs text-slate-700 mb-3 leading-relaxed">'
            f'<span class="font-semibold">{h.esc(label)}</span> '
            f'is the strongest kill candidate at this horizon '
            f'(mean {mean:+.2f}%, hit {hit:.1f}%, N={int(n)}, vs benchmark '
            f'{base_rate:.1f}% positive).'
            f'</p>')
    elif keep and not kill and not watch:
        narrative = (
            f'<p class="text-xs text-slate-700 mb-3 leading-relaxed">'
            f'No kill candidates at T+90. {len(keep)} signal(s) clear the '
            f'keep bar (N&ge;20, mean&gt;0, hit&gt;{base_rate:.1f}%).'
            f'</p>')

    return (
        '<section class="m-6 bg-amber-50 border border-amber-200 rounded-lg p-4">'
        '<h2 class="text-xs uppercase tracking-wide text-amber-700 mb-3">'
        'Model assessment</h2>'
        + narrative
        + "".join(blocks)
        + '</section>'
    )


def _hit_rate_panel(signals_data: dict) -> str:
    """Per-tier live hit-rate panel.

    Server-renders the t30 snapshot.  Listens to horizonChange and
    rebuilds tiles client-side from window.__perfData.horizon_aggregates.
    Tiles with N<10 are greyed out; green/red when >3 pp above/below
    the benchmark base rate.
    """
    horizons = signals_data.get("horizon_aggregates") or {}
    t30 = horizons.get("t30") or {}
    base_rate = float(t30.get("base_rate") or 50.0)
    sigs = t30.get("signals") or {}

    def _tile_classes(hit, n, br):
        if n < 10:
            return "bg-slate-50 border-slate-100", "text-slate-300"
        if hit is not None and hit > br + 3:
            return "bg-emerald-50 border-emerald-200", "text-emerald-600"
        if hit is not None and hit < br - 3:
            return "bg-rose-50 border-rose-200", "text-rose-600"
        return "bg-white border-slate-200", "text-slate-600"

    def _tile(sid: str, sig_data: dict, br: float) -> str:
        n = int(sig_data.get("trades") or 0)
        hit = sig_data.get("hit_pct")
        mean = sig_data.get("mean_car")
        tile_cls, hit_cls = _tile_classes(hit, n, br)
        hit_s = f"{hit:.1f}%" if hit is not None else "—"
        mean_s = f"{mean:+.1f}%" if mean is not None else "—"
        badge = h.render_badge(sid)
        return (
            f'<div class="hit-rate-tile rounded border {tile_cls} p-2 text-center" '
            f'data-sid="{h.esc(sid)}">'
            f'<div class="mb-1 flex justify-center">{badge}</div>'
            f'<div class="hit-rate-val text-sm font-bold {hit_cls}">{h.esc(hit_s)}</div>'
            f'<div class="text-[10px] text-slate-500 mt-0.5">'
            f'mean <span class="hit-rate-mean">{h.esc(mean_s)}</span></div>'
            f'<div class="text-[10px] text-slate-400">'
            f'N=<span class="hit-rate-n">{n}</span></div>'
            '</div>'
        )

    tiles = "".join(
        _tile(sid, sigs.get(sid) or {}, base_rate)
        for sid in h.SIGNAL_DISPLAY_ORDER
    )

    js = (
        '<script>'
        '(function(){'
        'function rebuildHitRatePanel(horizon){'
        'var ha=(window.__perfData&&window.__perfData.horizon_aggregates)||{};'
        'var hd=ha[horizon]||{};'
        'var br=typeof hd.base_rate==="number"?hd.base_rate:50;'
        'var sigs=hd.signals||{};'
        'document.querySelectorAll(".hit-rate-tile").forEach(function(tile){'
        'var sid=tile.dataset.sid;'
        'var sig=sigs[sid]||{};'
        'var n=sig.trades||0;'
        'var hit=(sig.hit_pct!==undefined&&sig.hit_pct!==null)?sig.hit_pct:null;'
        'var mean=(sig.mean_car!==undefined&&sig.mean_car!==null)?sig.mean_car:null;'
        'var dim=n<10;'
        'tile.querySelector(".hit-rate-val").textContent='
        'hit!==null?hit.toFixed(1)+"%":"—";'
        'tile.querySelector(".hit-rate-mean").textContent='
        'mean!==null?(mean>=0?"+":"")+mean.toFixed(1)+"%":"—";'
        'tile.querySelector(".hit-rate-n").textContent=String(n);'
        'tile.classList.remove("bg-slate-50","border-slate-100",'
        '"bg-emerald-50","border-emerald-200",'
        '"bg-rose-50","border-rose-200","bg-white","border-slate-200");'
        'var valEl=tile.querySelector(".hit-rate-val");'
        'valEl.classList.remove("text-slate-300","text-emerald-600",'
        '"text-rose-600","text-slate-600");'
        'if(dim){'
        'tile.classList.add("bg-slate-50","border-slate-100");'
        'valEl.classList.add("text-slate-300");'
        '}else if(hit!==null&&hit>br+3){'
        'tile.classList.add("bg-emerald-50","border-emerald-200");'
        'valEl.classList.add("text-emerald-600");'
        '}else if(hit!==null&&hit<br-3){'
        'tile.classList.add("bg-rose-50","border-rose-200");'
        'valEl.classList.add("text-rose-600");'
        '}else{'
        'tile.classList.add("bg-white","border-slate-200");'
        'valEl.classList.add("text-slate-600");'
        '}'
        '});'
        'var hdg=document.getElementById("hit-rate-heading");'
        'if(hdg&&window.__perfData&&window.__perfData.horizon_labels){'
        'hdg.textContent="Signal hit-rate at "'
        '+(window.__perfData.horizon_labels[horizon]||horizon);'
        '}'
        '}'
        'document.addEventListener("horizonChange",function(e){'
        'rebuildHitRatePanel(e.detail&&e.detail.horizon?e.detail.horizon:"t30");'
        '});'
        '})();'
        '</script>'
    )

    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg p-4">'
        '<h2 id="hit-rate-heading" class="text-xs uppercase tracking-wide '
        'text-slate-500 mb-1">Signal hit-rate at T+30 (~1 month)</h2>'
        '<p class="text-[10px] text-slate-400 mb-3">Proportion of signals with positive '
        'CAR at the selected horizon. Green/red = above/below benchmark base rate '
        'by &gt;3 pp. Grey = N&lt;10 resolved trades.</p>'
        f'<div class="grid grid-cols-4 sm:grid-cols-7 gap-2">{tiles}</div>'
        '</section>'
        + js
    )


def _client_data_payload(signals_data: dict, status_overrides: dict) -> str:
    payload = {
        "horizon_aggregates": signals_data.get("horizon_aggregates") or {},
        "deprecated": list(status_overrides.keys()),
        "tier_hex": {k: v["hex"] for k, v in h.TIER_PALETTE.items()},
        "tier_tooltips": h.TIER_TOOLTIPS,
        "horizon_labels": HORIZON_LABELS,
        "diag_color_ftas": h.DIAG_COLORS["ftas"],
        # Performance page redesign v1 (FE Sprint 1): expose cohorts_v2 so
        # the cohort-tile auto-wire script can re-render table bodies
        # client-side on lookback/horizon changes without a network round-trip.
        "cohorts_v2": signals_data.get("cohorts_v2") or {},
    }
    return ('<script>window.__perfData = '
            + json.dumps(payload, separators=(",", ":"))
            + ';</script>')


# Keys threaded into the browser for the Level-2 cohort chart (Phase 4).
# Only the chart-consumed subset of each month is sent to keep the page
# payload lean (signal_ids[] are NOT needed until the Phase-6 drill-down).
#
# B-072 (Sprint 24): extend to include t1 / t90 / t365 per-horizon fields so
# the existing JS cohortPick(m, h, metric) helper can read them on horizon
# change. The JS already subscribes to horizonChange and calls cohortPick —
# this is the Python-side data thread that feeds those reads.
_LEVEL2_MONTH_KEYS = (
    # --- shared ---
    "month_iso", "n_signals", "pending", "pending_horizons",
    # --- t30 (primary label horizon, ~1 month / 21 trading days) ---
    "mean_car_t30", "min_car_t30", "max_car_t30",
    "hit_rate_t30", "hit_rate_t30_rolling_6m",
    "single_ticker_weight", "single_ticker_weight_t30",
    "ma3_mean_car_t30",
    # --- t1 ---
    "mean_car_t1", "min_car_t1", "max_car_t1",
    "hit_rate_t1", "hit_rate_t1_rolling_6m",
    "single_ticker_weight_t1", "ma3_mean_car_t1",
    # --- t90 ---
    "mean_car_t90", "min_car_t90", "max_car_t90",
    "hit_rate_t90", "hit_rate_t90_rolling_6m",
    "single_ticker_weight_t90", "ma3_mean_car_t90",
    # --- t180 ---
    "mean_car_t180", "min_car_t180", "max_car_t180",
    "hit_rate_t180", "hit_rate_t180_rolling_6m",
    "single_ticker_weight_t180", "ma3_mean_car_t180",
    # --- t365 ---
    "mean_car_t365", "min_car_t365", "max_car_t365",
    "hit_rate_t365", "hit_rate_t365_rolling_6m",
    "single_ticker_weight_t365", "ma3_mean_car_t365",
)


# Keys threaded into the browser per drill-down signal row (Phase 6, B-070).
# Only the columns the modal table renders are sent (fingerprint is kept as a
# stable row key but not displayed). Matches cohort_performance.json's
# cohort_drilldown[grp][month].signals[] shape verbatim.
_DRILLDOWN_SIGNAL_KEYS = (
    "ticker", "director", "role_short", "fire_date",
    "car_t1", "car_t30", "car_t90", "benchmark_t30", "net_car_t30",
    "cohort_weight",
)


def _cohort_drilldown_payload(cohort_drilldown: dict) -> dict:
    """Sprint 14 Phase 6 (B-070): shape the cohort_drilldown blob for the
    browser. Keyed `[grp][month_iso] -> {verdict, signals: [ {..subset..} ]}`.

    Pure pass-through of the pre-baked export (the per-trade `cohort_weight` is
    already the bounded abs-share per the 2026-05-29 metric ruling). We project
    each signal row to the display columns + `cohort_weight` to keep the page
    payload lean and avoid shipping `signal_ids` / internal fields. Additive --
    only the new modal reads this blob.
    """
    out = {}
    for grp, months in (cohort_drilldown or {}).items():
        if not isinstance(months, dict):
            continue
        out[grp] = {}
        for month_iso, entry in months.items():
            if not isinstance(entry, dict):
                continue
            sigs = []
            for s in (entry.get("signals") or []):
                if not isinstance(s, dict):
                    continue
                sigs.append({k: s.get(k) for k in _DRILLDOWN_SIGNAL_KEYS})
            out[grp][month_iso] = {
                "verdict": entry.get("verdict") or "",
                # Sprint 14 pending-month ruling (B-072): the export marks a
                # not-yet-matured cohort's drilldown entry pending; the modal
                # reads it to show the slate header note, suppress the verdict,
                # and em-dash the unmeasured CAR/Net/Share columns. Falls back
                # to deriving pending from the months[] flag if absent.
                "pending": bool(entry.get("pending")),
                "signals": sigs,
            }
    return out


def _cohort_client_data(cohort_groups: dict,
                        cohort_drilldown: dict | None = None) -> str:
    """Sprint 14 Phase 4 (B-068): expose the per-group monthly cohort series
    to the browser as `window.__cohortData`.

    Phase 3 only consumed `sparkline_points` / trend server-side; the Level-2
    chart needs the full `months[]` client-side (means, min/max for whiskers,
    n_signals for the strip + low-N styling, rolling hit rate, ma3, and the
    single-ticker dominance weight). Additive — nothing else reads this blob.

    Phase 6 (B-070): the same blob now ALSO carries `drilldown` --
    `cohort_drilldown[grp][month_iso]` = {verdict, signals[]} -- so the
    click-to-open modal can render the contributing-trades table without a
    network round-trip. Threaded additively from render()'s already-loaded
    cohort_data; absent (older export) -> empty object, modal simply finds no
    rows.

    Shape: {
      "order": [...],
      "groups": { "<grp>": {"label", "color_hex", "months": [...] } },
      "drilldown": { "<grp>": { "<month_iso>": {"verdict", "signals": [...]} } }
    }
    Months are passed through verbatim (ascending, including null-gap months
    where n_signals == 0) so the chart can break lines at gaps.
    """
    out = {}
    for grp, gd in (cohort_groups or {}).items():
        if not isinstance(gd, dict):
            continue
        months = []
        for m in (gd.get("months") or []):
            months.append({k: m.get(k) for k in _LEVEL2_MONTH_KEYS})
        out[grp] = {
            "label": gd.get("label") or grp.upper(),
            "color_hex": (gd.get("color_hex")
                          or h.TIER_PALETTE.get(grp, {}).get("hex", "#94a3b8")),
            "months": months,
        }
    # Display order for the pill switcher (only groups that actually exist).
    order = [s for s in h.SIGNAL_DISPLAY_ORDER if s in out]
    drilldown = _cohort_drilldown_payload(cohort_drilldown or {})
    return ('<script>window.__cohortData = '
            + json.dumps({"order": order, "groups": out,
                          "drilldown": drilldown},
                         separators=(",", ":"))
            + ';</script>')


_RECOVERABLE_BADGE = {
    "no":         ("No",         "bg-slate-200 text-slate-700"),
    "v2-fx":      ("v2 (FX)",    "bg-blue-100 text-blue-800"),
    "v2-fanout":  ("v2 (fan-out)", "bg-blue-100 text-blue-800"),
    "manual":     ("Manual",     "bg-amber-100 text-amber-800"),
    "unknown":    ("Unknown",    "bg-slate-100 text-slate-600"),
}


def _recoverable_badge_html(recoverable: str) -> str:
    label, cls = _RECOVERABLE_BADGE.get(
        recoverable, _RECOVERABLE_BADGE["unknown"]
    )
    return (
        '<span class="inline-flex items-center px-1.5 py-0.5 rounded '
        f'text-[10px] font-medium {cls}">{h.esc(label)}</span>'
    )


def _pending_diagnostics_section(signals_data: dict) -> str:
    """Render the Pending review panel.

    Reads ``signals_data["pending_diagnostics"]``. If absent, renders an
    empty state pointing at the exporter.
    """
    diag = signals_data.get("pending_diagnostics")
    if not diag or not isinstance(diag, dict) or not diag.get("categories"):
        return (
            '<section class="m-6 bg-white border border-slate-200 rounded-lg '
            'p-4">'
            '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-2">'
            'Pending review</h2>'
            '<div class="text-xs text-slate-500">'
            'Pending diagnostics unavailable -- run '
            '<code class="text-slate-700">export_dashboard_json.py</code> '
            'to refresh.</div>'
            '</section>'
        )

    total = int(diag.get("total") or 0)
    categories = diag.get("categories") or []

    rows_html: list[str] = []
    for cat in categories:
        name = h.esc(cat.get("name") or "Other")
        count = int(cat.get("count") or 0)
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
            f'<td class="px-3 py-2">{_recoverable_badge_html(recoverable)}'
            '</td>'
            '</tr>'
        )

    caption = (
        '<div class="text-[10px] text-slate-500 px-4 py-2 border-t '
        'border-slate-100">'
        'The parser couldn\'t cleanly extract these; mostly genuine edge '
        'cases. Recoverable column flags which buckets v2 work could '
        'unlock vs. which are case-by-case manual review.'
        '</div>'
    )

    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg '
        'overflow-hidden">'
        '<h2 class="px-4 py-3 text-xs uppercase tracking-wide text-slate-500 '
        'border-b border-slate-100">'
        f'Pending review &mdash; {total:,} filings excluded from signals'
        '</h2>'
        '<p class="px-4 pt-3 pb-1 text-xs text-slate-600 leading-relaxed">'
        '&#8627; The parser couldn\'t cleanly extract these; mostly genuine '
        'edge cases. See breakdown below.'
        '</p>'
        '<table class="w-full text-xs tabular-nums" id="pendingDiag">'
        '<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide '
        'text-[10px]">'
        '<tr>'
        '<th class="px-3 py-2 text-left w-[55%]">Category</th>'
        '<th class="px-3 py-2 text-right w-[12%]">Count</th>'
        '<th class="px-3 py-2 text-right w-[10%]">%</th>'
        '<th class="px-3 py-2 text-left w-[23%]">Recoverable?</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table>'
        f'{caption}'
        '</section>'
    )


def _cohort_focus_overlay() -> str:
    """Sprint 14 Phase 3 (B-067): focus-mode shell.

    Locked decision 1 (Rupert, 2026-05-29): row-click = FOCUS MODE (the table
    dims out, the chart takes the page) -- NOT inline-expand. This is the
    SHELL only: a header (signal label + back-to-overview affordance) and an
    empty mount where Phase 4's Level-2 cohort chart will render. The Level-2
    chart itself is out of scope for this phase.
    """
    return (
        '<section id="cohortFocus" hidden '
        'class="fixed inset-0 z-40 bg-white overflow-y-auto" '
        'role="region" aria-label="Cohort chart focus view">'
        '<div class="max-w-5xl mx-auto p-6">'
        '<div class="flex items-center justify-between mb-4 border-b '
        'border-slate-200 pb-3">'
        '<h2 class="text-sm font-semibold text-slate-700">'
        '<span class="text-slate-400 uppercase tracking-wide text-[10px] '
        'block mb-0.5">Cohort chart</span>'
        '<span id="cohortFocusLabel">Signal</span></h2>'
        '<div class="flex items-center gap-2">'
        '<a href="index.html" '
        'class="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded '
        'border border-slate-300 text-slate-600 hover:border-slate-400 '
        'hover:text-slate-800 focus:outline-none focus:ring-2 '
        'focus:ring-slate-300">'
        '<span aria-hidden="true">&larr;</span> Dashboard</a>'
        '<button type="button" id="cohortFocusBack" '
        'class="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded '
        'border border-slate-300 text-slate-600 hover:border-slate-400 '
        'hover:text-slate-800 focus:outline-none focus:ring-2 '
        'focus:ring-slate-300">'
        'Performance overview</button>'
        '</div>'
        '</div>'
        # Phase 4 (B-068): signal pill switcher. Populated client-side from
        # window.__cohortData.order; clicking a pill swaps the chart in place.
        '<div class="flex items-center gap-2 mb-1">'
        '<span class="text-[10px] uppercase tracking-wide text-slate-400">'
        'Signal:</span>'
        '<div id="cohortPills" class="flex flex-wrap gap-1.5" '
        'role="tablist" aria-label="Select signal group"></div>'
        '</div>'
        # B-074: discoverability hint for the overlay interaction. The pills
        # support click-to-add/remove (overlay multiple signals) and
        # double-click-to-solo; without this line that is invisible.
        '<div class="text-[10px] text-slate-400 mb-4 -mt-0.5">'
        'Click to add or remove a signal &middot; double-click to show one on '
        'its own. Comparing 2+ signals shows mean CAR only.</div>'
        # B-072 (Sprint 24): horizon toggle buttons for the Level-2 chart.
        # Clicking a button updates window.__cohortActiveHorizon and dispatches
        # horizonChange so the Level-2 chart + hit-rate panel both rebuild.
        # Separate from the page-level #horizon dropdown so the user can
        # switch horizons inside focus mode without leaving the overlay.
        '<div class="flex items-center gap-2 mb-4">'
        '<span class="text-[10px] uppercase tracking-wide text-slate-400">'
        'Horizon:</span>'
        '<div id="cohortHorizonBtns" class="flex gap-1" role="group" '
        'aria-label="Select CAR horizon">'
        '<button class="cohort-h-btn text-[10px] px-2 py-0.5 rounded border '
        'border-slate-300 text-slate-600 hover:border-indigo-400 '
        'hover:text-indigo-700 transition-colors" data-h="t1">T+1</button>'
        '<button class="cohort-h-btn text-[10px] px-2 py-0.5 rounded border '
        'border-indigo-500 bg-indigo-50 text-indigo-700 font-medium" '
        'data-h="t30" aria-pressed="true">T+30</button>'
        '<button class="cohort-h-btn text-[10px] px-2 py-0.5 rounded border '
        'border-slate-300 text-slate-600 hover:border-indigo-400 '
        'hover:text-indigo-700 transition-colors" data-h="t90">T+90</button>'
        '<button class="cohort-h-btn text-[10px] px-2 py-0.5 rounded border '
        'border-slate-300 text-slate-600 hover:border-indigo-400 '
        'hover:text-indigo-700 transition-colors" data-h="t180">T+180</button>'
        '<button class="cohort-h-btn text-[10px] px-2 py-0.5 rounded border '
        'border-slate-300 text-slate-600 hover:border-indigo-400 '
        'hover:text-indigo-700 transition-colors" data-h="t365">T+365</button>'
        '</div>'
        '</div>'
        # Phase 4 (B-068): Level-2 cohort chart mount. The chart (main scatter
        # + whiskers + dominance markers + zero line, plus the N strip below)
        # is built client-side by buildCohortLevel2() the first time focus
        # opens and re-built in place on pill switch. data-signal-group is set
        # by the focus opener so the builder knows which group to render.
        '<div id="cohort-level2-mount" data-signal-group="" '
        'class="relative">'
        # header strip (N / mean / hit) — filled client-side
        '<div id="cohortL2Strip" class="text-xs text-slate-500 mb-2"></div>'
        # main scatter chart (means + whiskers)
        '<div class="relative h-[360px]"><canvas id="cohortMainChart">'
        '</canvas></div>'
        # N strip (shared x, bar heights ~ n_signals; N labels via overlay)
        # B-074: id on the wrapper so the multi-signal overlay can hide it.
        '<div id="cohortNStripWrap" class="relative h-[80px] mt-1">'
        '<canvas id="cohortNStrip"></canvas>'
        '<div id="cohortNLabels" class="pointer-events-none absolute '
        'inset-0"></div>'
        '</div>'
        # Phase 5 (B-069): compact static legend/key strip. Explains the
        # chart's visual language (filled vs hollow dot, solid vs dashed
        # whisker, 3m MA line, '!' dominance glyph, N-strip bars). Static
        # HTML -- no JS. Kept deliberately quiet (text-[10px] slate) so it
        # never competes with the chart. ASCII / HTML-entity glyphs only.
        '<div id="cohortLegend" class="flex flex-wrap items-center gap-x-3 '
        'gap-y-1 mt-2 text-[10px] text-slate-400">'
        # filled dot = N>=5
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-2 h-2 rounded-full bg-slate-400"></span>'
        '5+ signals</span>'
        # hollow ring = N<5 (low N)
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-2 h-2 rounded-full bg-white '
        'border border-slate-400"></span>under 5 (low N)</span>'
        # Sprint 14 pending ruling (B-072): hollow slate-400 diamond = a month
        # whose T+21 window has not yet matured. Swatch is a rotated hollow
        # square (border, white fill) so it matches the on-plot floor diamond.
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-2 h-2 bg-white border border-slate-400" '
        'style="transform:rotate(45deg)"></span>'
        'pending (fired, T+30 not yet matured)</span>'
        # solid whisker = N>=5
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-px h-3 bg-slate-400"></span>'
        'solid whisker = 5+</span>'
        # dashed whisker = N<5
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-px h-3 border-l border-dashed '
        'border-slate-400"></span>dashed whisker = under 5</span>'
        # dashed line = 3-month average
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-4 h-px border-t border-dashed '
        'border-slate-400"></span>dashed line = 3-month average</span>'
        # '!' = single-ticker dominance
        '<span class="inline-flex items-center gap-1">'
        '<span class="font-bold text-rose-500">!</span>'
        'one ticker drove &gt;50% of the cohort</span>'
        # grey bar = N>=5 in the N strip
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-2 h-3 bg-slate-300"></span>'
        '5+ signals</span>'
        # hollow amber bar = N<5 in the N strip
        '<span class="inline-flex items-center gap-1">'
        '<span class="inline-block w-2 h-3 bg-white border border-dashed '
        'border-amber-500"></span>under 5</span>'
        '</div>'
        # empty / no-data state (toggled by the builder)
        '<div id="cohortL2Empty" style="display:none" '
        'class="absolute inset-0 flex items-center justify-center '
        'text-center p-8 text-slate-400 bg-white">'
        '<div><div class="text-3xl mb-2" aria-hidden="true">&#9696;</div>'
        '<p class="text-sm font-medium text-slate-500">'
        'No cohort data for <span id="cohortFocusLabelInline" '
        'class="font-medium">this signal</span>.</p></div>'
        '</div>'
        '</div>'
        # DOM tooltip (Phase 4): richer than Chart.js native; positioned by JS.
        '<div id="cohortTooltip" '
        'class="hidden absolute z-50 bg-white border border-slate-300 rounded '
        'shadow-md px-3 py-2 text-xs text-slate-700 pointer-events-none '
        'min-w-[200px]">'
        '<div class="font-semibold text-slate-900" id="ttMonth"></div>'
        '<div id="ttN"></div><div id="ttMean"></div>'
        '<div id="ttRange"></div><div id="ttHit"></div>'
        '<div class="text-slate-400 mt-1 text-[10px]">click to drill down</div>'
        '</div>'
        '</div>'
        # Phase 5 (B-069): SECOND, smaller stacked card -- the rolling 6-month
        # hit-rate panel. Separate visual block below the main chart + N strip
        # + legend (per the design spec's "two stacked cards"). The chart is
        # built/destroyed inside build(group) alongside the main + N charts so
        # it swaps on pill change. Shares the SAME category month labels as the
        # main chart (all months incl. gaps) so the two align; null rolling-hit
        # values break the line.
        # B-074: id on the rolling-hit section so the overlay can hide it.
        '<section id="cohortHitSection" class="bg-white border '
        'border-slate-200 rounded-lg shadow-sm p-4 mt-4">'
        '<header class="mb-3">'
        '<h3 class="text-sm font-semibold text-slate-700">'
        # B-072: cohortHitRateHorizon span is updated by JS when horizon changes.
        'Rolling 6-month hit rate @ <span id="cohortHitRateHorizon">T+30</span> '
        '<span class="text-xs font-normal text-slate-400">'
        '% of signals beating sector benchmark</span></h3>'
        '</header>'
        '<div class="relative h-[180px]">'
        '<canvas id="cohortHitRateChart"></canvas></div>'
        '</section>'
        '</div>'
        '</section>'
    )


def _cohort_focus_script() -> str:
    """Wire the scoreboard rows to open focus-mode, and the back button /
    Escape key to restore the table. Idempotent, no deps. (Phase 3 shell.)
    B-072 (Sprint 24): also wires the horizon toggle buttons inside the focus
    overlay and syncs their active state with window.__cohortActiveHorizon.
    B-076 (Sprint 24): saves / restores selected signal + horizon to
    localStorage so the user's last-viewed state persists across page loads.
    """
    return r"""<script>
(function() {
  'use strict';
  var focus = document.getElementById('cohortFocus');
  if (!focus) return;
  var labelEl = document.getElementById('cohortFocusLabel');
  var labelInlineEl = document.getElementById('cohortFocusLabelInline');
  var mount = document.getElementById('cohort-level2-mount');
  var backBtn = document.getElementById('cohortFocusBack');
  var scoreboard = document.getElementById('scoreboard');

  // B-076: localStorage keys for cross-session persistence.
  var LS_GROUP   = 'dd_cohort_group';
  var LS_HORIZON = 'dd_cohort_horizon';

  // B-072: horizon labels for the hit-rate panel header.
  var H_LABELS = {t1:'T+1', t30:'T+30', t90:'T+90', t180:'T+180', t365:'T+365'};

  // B-072: update the active-button styling and the hit-rate panel header.
  function syncHorizonButtons(h) {
    document.querySelectorAll('.cohort-h-btn').forEach(function(btn) {
      var isActive = btn.getAttribute('data-h') === h;
      btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      if (isActive) {
        btn.className = 'cohort-h-btn text-[10px] px-2 py-0.5 rounded border ' +
          'border-indigo-500 bg-indigo-50 text-indigo-700 font-medium';
      } else {
        btn.className = 'cohort-h-btn text-[10px] px-2 py-0.5 rounded border ' +
          'border-slate-300 text-slate-600 hover:border-indigo-400 ' +
          'hover:text-indigo-700 transition-colors';
      }
    });
    var hrEl = document.getElementById('cohortHitRateHorizon');
    if (hrEl) hrEl.textContent = H_LABELS[h] || h.toUpperCase();
  }

  function openFocus(group, label) {
    if (labelEl) labelEl.textContent = label || group;
    if (labelInlineEl) labelInlineEl.textContent = label || group;
    if (mount) mount.setAttribute('data-signal-group', group || '');
    // Dim the underlying page content; the focus section overlays it.
    if (scoreboard) scoreboard.setAttribute('aria-hidden', 'true');
    focus.hidden = false;
    document.body.classList.add('overflow-hidden');
    // Sync horizon buttons to current active horizon.
    syncHorizonButtons(window.__cohortActiveHorizon || 't30');
    // Phase 4 (B-068): render the Level-2 chart for the selected group. The
    // builder is defined in _cohort_level2_script(); guard in case that
    // script failed to load so focus mode still opens.
    if (typeof window.buildCohortLevel2 === 'function') {
      window.buildCohortLevel2(group);
    }
    // B-076: persist the selected group.
    try { localStorage.setItem(LS_GROUP, group); } catch(e) {}
    if (backBtn) backBtn.focus();
  }
  function closeFocus() {
    focus.hidden = true;
    document.body.classList.remove('overflow-hidden');
    if (scoreboard) scoreboard.removeAttribute('aria-hidden');
  }

  // B-072: wire horizon toggle buttons.
  document.querySelectorAll('.cohort-h-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var h = btn.getAttribute('data-h');
      if (!h) return;
      window.__cohortActiveHorizon = h;
      syncHorizonButtons(h);
      // Dispatch page-level horizonChange so Level-2 chart + scoreboard rebuild.
      document.dispatchEvent(new CustomEvent('horizonChange', {detail: {horizon: h}}));
      // B-076: persist chosen horizon.
      try { localStorage.setItem(LS_HORIZON, h); } catch(e) {}
    });
  });

  if (backBtn) backBtn.addEventListener('click', closeFocus);
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape' || focus.hidden) return;
    // Phase 6 (B-070): if the drill-down modal is open on top of focus mode,
    // let Esc close ONLY the modal (its own handler does that) -- don't also
    // collapse focus mode underneath it.
    var dd = document.getElementById('cohort-drilldown');
    if (dd && dd.style.display !== 'none') return;
    closeFocus();
  });

  document.querySelectorAll('tr.cohort-row').forEach(function(tr) {
    function open() {
      openFocus(tr.getAttribute('data-signal-group'),
                tr.getAttribute('data-signal-label'));
    }
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    });
  });

  // B-076: restore last-viewed horizon from localStorage on load.
  // Horizon is restored silently; focus overlay is NOT auto-opened so that
  // navigating to this page always lands on the performance table first.
  // (Auto-open was removed: it made the page appear to be a separate
  // "Cohort Chart page" on every navigation.)
  (function restoreFromStorage() {
    try {
      var savedH = localStorage.getItem(LS_HORIZON);
      var validH = ['t1', 't30', 't90', 't180', 't365'];
      if (savedH && validH.indexOf(savedH) !== -1) {
        window.__cohortActiveHorizon = savedH;
        // Also sync the page-level horizon dropdown if it exists.
        var pgHorizon = document.getElementById('horizon');
        if (pgHorizon) pgHorizon.value = savedH;
        syncHorizonButtons(savedH);
      }
    } catch(e) {}
  })();
})();
</script>"""


def _cohort_level2_script() -> str:
    """Sprint 14 Phase 4 (B-068): the Level-2 cohort chart.

    Builds, into #cohort-level2-mount, a Chart.js scatter (one dot per month =
    mean CAR @ T+21, filled for N>=5 / open ring for N<5, straight faded
    connector) with three custom plugins:

      * whiskerPlugin   -- per-month min->max vertical line + caps (solid N>=5,
                           dashed N<5), tier colour.
      * zeroLinePlugin  -- dashed horizontal line at y=0.
      * dominanceMarkers-- '!' glyph above the whisker top where
                           single_ticker_weight > 0.5.

    Plus a faint 3-month MA overlay (broken at the first 2 months / nulls), a
    separate N-strip bar chart sharing the x-range with N labels drawn via an
    absolutely-positioned overlay div (NO new dep -- chartjs-plugin-datalabels
    is NOT in the project), a pill switcher (one signal at a time per brief),
    and a DOM tooltip (Chart.js native tooltip disabled).

    Out of scope (clean seams left): the rolling-6m hit-rate panel (Phase 5)
    and the click-to-drilldown modal (Phase 6) -- onCohortClick is a no-op stub.
    """
    return r"""
<script>
(function(){
  'use strict';
  var BLOB = window.__cohortData || {order: [], groups: {}};
  var GROUPS = BLOB.groups || {};
  var ORDER  = (BLOB.order && BLOB.order.length)
               ? BLOB.order : Object.keys(GROUPS);
  var mainChart = null, nChart = null, hitChart = null, currentGroup = null;

  // Sprint 24: active horizon for the cohort chart. Defaults to t30 so the
  // chart behaves identically to pre-Sprint-24 until the user changes the
  // horizon dropdown.
  window.__cohortActiveHorizon = window.__cohortActiveHorizon || 't30';
  var HORIZON_LABELS_COHORT = {t1:'T+1', t30:'T+30', t90:'T+90', t180:'T+180', t365:'T+365'};

  // cohortPick(m, h, metric): read m[metric + '_' + h], e.g.
  // cohortPick(m, 't90', 'mean_car') -> m.mean_car_t90.
  // Special-cases: single_ticker_weight (old key, no suffix) when h='t30'.
  function cohortPick(m, h, metric){
    if (metric === 'single_ticker_weight' && h === 't30'){
      // backward compat: old key has no suffix
      return (m['single_ticker_weight_t30'] != null)
        ? m['single_ticker_weight_t30']
        : m['single_ticker_weight'];
    }
    return m[metric + '_' + h] != null ? m[metric + '_' + h] : null;
  }

  // IIFE-level month formatter. Shared by build() (single-group) and
  // buildOverlay() (multi-group). B-074: buildOverlay previously referenced a
  // monthLabel that only existed inside build(), so the overlay path threw a
  // ReferenceError and never rendered. Hoisted here as the single source.
  function monthLabel(iso){
    var p = (iso || '').split('-');
    var mi = parseInt(p[1], 10) - 1;
    var names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep',
                 'Oct','Nov','Dec'];
    if (isNaN(mi) || mi < 0 || mi > 11) return iso;
    return names[mi] + ' ' + (p[0] || '').slice(2);
  }

  // Show/hide the single-group-only sub-panels (N strip, whisker legend,
  // rolling-hit section). B-074: these are meaningless in a means-only overlay,
  // so the overlay hides them and the detailed build() restores them.
  function setSingleGroupChromeVisible(on){
    ['cohortNStripWrap','cohortLegend','cohortHitSection'].forEach(function(id){
      var el = document.getElementById(id);
      if (el) el.style.display = on ? '' : 'none';
    });
  }

  // Subscribe to the page-level horizonChange event so the cohort chart
  // rebuilds when the user picks a different horizon. B-074: stay in overlay
  // mode when more than one signal is selected, instead of collapsing back to
  // a single detailed chart.
  document.addEventListener('horizonChange', function(e){
    var h = (e.detail && e.detail.horizon) || 't30';
    window.__cohortActiveHorizon = h;
    if (visibleSet && visibleSet.length > 1){
      buildOverlay(visibleSet);
    } else if (currentGroup){
      build(currentGroup);
    }
  });

  function pctTxt(v, dp){
    if (v == null) return '-';
    dp = (dp == null) ? 1 : dp;
    return (v > 0 ? '+' : '') + (v * 100).toFixed(dp) + '%';
  }
  // tier colour at a given alpha (hex -> rgba)
  function rgba(hex, a){
    var h = (hex || '#94a3b8').replace('#','');
    if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
    var r = parseInt(h.slice(0,2),16), g = parseInt(h.slice(2,4),16),
        b = parseInt(h.slice(4,6),16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  // -- Custom plugins ------------------------------------------------------
  // Whisker: vertical min->max with horizontal caps; solid N>=5, dashed N<5.
  var whiskerPlugin = {
    id: 'whiskerPlugin',
    afterDatasetsDraw: function(chart){
      var ctx = chart.ctx;
      var meta = chart.getDatasetMeta(0);   // dataset 0 = the means
      var ds = chart.data.datasets[0];
      if (!meta || !ds) return;
      ds.data.forEach(function(pt, i){
        var el = meta.data[i];
        if (!el || pt._min == null || pt._max == null) return;
        var x = el.x;
        var yMin = chart.scales.y.getPixelForValue(pt._min);
        var yMax = chart.scales.y.getPixelForValue(pt._max);
        ctx.save();
        ctx.strokeStyle = chart.$tierHex || '#94a3b8';
        ctx.lineWidth = 1;
        if (pt._lowN) ctx.setLineDash([3,3]); else ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(x, yMin); ctx.lineTo(x, yMax);          // shaft
        ctx.moveTo(x - 4, yMin); ctx.lineTo(x + 4, yMin);  // bottom cap
        ctx.moveTo(x - 4, yMax); ctx.lineTo(x + 4, yMax);  // top cap
        ctx.stroke();
        ctx.restore();
      });
    }
  };
  // Dashed horizontal zero line.
  var zeroLinePlugin = {
    id: 'zeroLine',
    afterDatasetsDraw: function(chart){
      var y = chart.scales.y;
      if (!y) return;
      var ctx = chart.ctx, area = chart.chartArea;
      var yp = y.getPixelForValue(0);
      ctx.save();
      ctx.strokeStyle = '#94a3b8';
      ctx.lineWidth = 1;
      ctx.setLineDash([4,4]);
      ctx.beginPath();
      ctx.moveTo(area.left, yp); ctx.lineTo(area.right, yp);
      ctx.stroke();
      ctx.restore();
    }
  };
  // '!' glyph above the whisker top where single_ticker_weight > 0.5.
  var dominanceMarkers = {
    id: 'dominanceMarkers',
    afterDatasetsDraw: function(chart){
      var ctx = chart.ctx;
      var meta = chart.getDatasetMeta(0);
      var ds = chart.data.datasets[0];
      if (!meta || !ds) return;
      ds.data.forEach(function(pt, i){
        if (!pt._domTick || pt._max == null) return;
        var el = meta.data[i];
        if (!el) return;
        var yMax = chart.scales.y.getPixelForValue(pt._max);
        ctx.save();
        ctx.fillStyle = '#dc2626';
        ctx.font = 'bold 13px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('!', el.x, yMax - 8);
        ctx.restore();
      });
    }
  };
  // Sprint 14 pending ruling (B-072): pending-month marker. For each point
  // where _pending === true (export's `pending`, or derived mean==null &&
  // n>0), paint a faint full-height slate band BEHIND everything
  // (beforeDatasetsDraw) and a small HOLLOW slate-400 diamond pinned 12px
  // ABOVE the bottom plot edge in PIXEL space (afterDatasetsDraw) -- never on
  // the data scale, so it can never read as a 0% return. The means dataset
  // already emits a null y for pending points (no dot, connector breaks) and
  // the whiskerPlugin no-ops on null _min/_max, so the band + floor diamond
  // are the only things drawn for that month.
  var PENDING_SLATE = '#94a3b8';      // slate-400
  function pendingPixelX(chart, idx){
    // category scale -> centre pixel for the point's index.
    var x = chart.scales.x;
    if (!x) return null;
    return x.getPixelForValue(idx);
  }
  var pendingMarkers = {
    id: 'pendingMarkers',
    beforeDatasetsDraw: function(chart){
      var ds = chart.data.datasets[0];
      var area = chart.chartArea;
      if (!ds || !area) return;
      var ctx = chart.ctx;
      // band half-width: half a category slot, capped so it stays a thin band.
      var x = chart.scales.x;
      var slot = (x && x.width && ds.data.length)
        ? (x.width / ds.data.length) : 24;
      var half = Math.min(slot * 0.45, 14);
      ds.data.forEach(function(pt, i){
        if (!pt || !pt._pending) return;
        var px = pendingPixelX(chart, i);
        if (px == null) return;
        ctx.save();
        ctx.fillStyle = 'rgba(148,163,184,0.06)';   // slate-400 @ 6%
        ctx.fillRect(px - half, area.top, half * 2, area.bottom - area.top);
        ctx.restore();
      });
    },
    afterDatasetsDraw: function(chart){
      var ds = chart.data.datasets[0];
      var area = chart.chartArea;
      if (!ds || !area) return;
      var ctx = chart.ctx;
      var cy = area.bottom - 12;        // centre fixed 12px above the floor
      var r = 4.5;                      // ~9px bounding box (hollow diamond)
      ds.data.forEach(function(pt, i){
        if (!pt || !pt._pending) return;
        var px = pendingPixelX(chart, i);
        if (px == null) return;
        ctx.save();
        ctx.strokeStyle = PENDING_SLATE;
        ctx.fillStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(px, cy - r);          // top
        ctx.lineTo(px + r, cy);          // right
        ctx.lineTo(px, cy + r);          // bottom
        ctx.lineTo(px - r, cy);          // left
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      });
    }
  };

  // Phase 5 (B-069): dashed grey 50% baseline on the rolling-hit-rate chart =
  // the random-performance reference (a coin-flip beats the benchmark half the
  // time). Mirrors zeroLinePlugin but anchored at y=50. Registered only on the
  // hit-rate chart.
  var baseline50Plugin = {
    id: 'baseline50',
    afterDatasetsDraw: function(chart){
      var y = chart.scales.y;
      if (!y) return;
      var ctx = chart.ctx, area = chart.chartArea;
      var yp = y.getPixelForValue(50);
      ctx.save();
      ctx.strokeStyle = '#94a3b8';
      ctx.lineWidth = 1;
      ctx.setLineDash([4,4]);
      ctx.beginPath();
      ctx.moveTo(area.left, yp); ctx.lineTo(area.right, yp);
      ctx.stroke();
      ctx.restore();
    }
  };

  // -- Tooltip (DOM, not Chart.js native) ----------------------------------
  var tip = document.getElementById('cohortTooltip');
  // Short "Mon YYYY" label for the pending tooltip/title (no parenthetical iso).
  function monthYear(iso){
    var p = (iso || '').split('-');
    var mi = parseInt(p[1], 10) - 1;
    var names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep',
                 'Oct','Nov','Dec'];
    if (isNaN(mi) || mi < 0 || mi > 11) return iso;
    return names[mi] + ' ' + (p[0] || '');
  }
  function showTip(pt, canvas){
    if (!tip) return;
    var ttN = document.getElementById('ttN');
    var ttMean = document.getElementById('ttMean');
    var ttRange = document.getElementById('ttRange');
    var ttHit = document.getElementById('ttHit');
    var ttHelp = tip.querySelector('.mt-1');     // 'click to drill down' line
    if (pt._pending){
      // Sprint 14 pending ruling (B-072): single-line variant. Only the
      // sentence shows; the mean/range/hit rows are null and hidden, and the
      // 'click to drill down' helper is suppressed (the sentence says Click).
      document.getElementById('ttMonth').textContent = monthYear(pt._monthIso);
      var aH = window.__cohortActiveHorizon || 't30';
      var aHLabel = HORIZON_LABELS_COHORT[aH] || aH.toUpperCase();
      ttN.textContent = monthYear(pt._monthIso) + ': ' + (pt._n || 0)
        + ' signals fired - ' + aHLabel + ' return not yet matured. Click to see the trades.';
      ttN.style.display = '';
      ttN.style.whiteSpace = 'normal';
      ttMean.style.display = 'none';
      ttRange.style.display = 'none';
      ttHit.style.display = 'none';
      if (ttHelp) ttHelp.style.display = 'none';
    } else {
      document.getElementById('ttMonth').textContent = pt._monthLabel;
      var nNote = pt._lowN ? '  (low - discount)' : '';
      ttN.style.display = '';
      ttN.style.whiteSpace = '';
      ttN.textContent = 'N = ' + pt._n + ' signals' + nNote;
      ttMean.style.display = '';
      var aHLabel2 = HORIZON_LABELS_COHORT[window.__cohortActiveHorizon || 't30']
        || (window.__cohortActiveHorizon || 't30').toUpperCase();
      ttMean.textContent = 'mean net CAR @ ' + aHLabel2 + ': ' + pctTxt(pt._meanFrac);
      ttRange.style.display = '';
      ttRange.textContent =
        'range: ' + pctTxt(pt._min/100) + ' .. ' + pctTxt(pt._max/100);
      ttHit.style.display = '';
      ttHit.textContent = 'hit rate this cohort: ' +
        (pt._hit == null ? '-' : Math.round(pt._hit*100) + '%');
      if (ttHelp) ttHelp.style.display = '';
    }
    // position relative to the mount (tooltip lives inside the mount wrapper)
    var mountEl = document.getElementById('cohort-level2-mount');
    var crect = canvas.getBoundingClientRect();
    var mrect = mountEl.getBoundingClientRect();
    tip.style.left = (crect.left - mrect.left + pt._px + 12) + 'px';
    tip.style.top  = (crect.top  - mrect.top  + pt._py - 10) + 'px';
    tip.classList.remove('hidden');
  }
  function hideTip(){ if (tip) tip.classList.add('hidden'); }

  // Phase 6 (B-070): resolve the clicked dot -> (currentGroup, monthIso) and
  // open the drill-down modal. Chart.js passes (event, elements, chart); we
  // also fall back to a hit-test in case the active elements list is empty.
  // Gap (null) points carry _monthIso === null and must NOT open a modal.
  // Sprint 14 pending ruling (B-072): the pending floor diamond lives in PIXEL
  // space (off the data scale), so Chart.js nearest/intersect hit-testing never
  // finds it. Map an event's canvas x to the nearest category index and, if
  // that month is pending, return its data point. Returns null otherwise.
  function pendingPointAtX(chart, evt){
    if (!chart) return null;
    var x = chart.scales.x, area = chart.chartArea;
    if (!x || !area) return null;
    var rect = chart.canvas.getBoundingClientRect();
    var px = (evt.clientX != null)
      ? (evt.clientX - rect.left)
      : (evt.offsetX != null ? evt.offsetX : null);
    if (px == null || px < area.left || px > area.right) return null;
    var ds = chart.data.datasets[0];
    if (!ds) return null;
    var best = null, bestD = Infinity;
    ds.data.forEach(function(pt, i){
      if (!pt || !pt._pending) return;
      var cx = x.getPixelForValue(i);
      var d = Math.abs(cx - px);
      if (d < bestD){ bestD = d; best = {pt: pt, idx: i, cx: cx}; }
    });
    // accept the click only if within ~half a category slot of a pending x.
    var slot = (x.width && ds.data.length) ? (x.width / ds.data.length) : 28;
    if (best && bestD <= Math.max(slot * 0.5, 16)) return best;
    return null;
  }

  function onCohortClick(evt, elements, chart){
    var c = chart || mainChart;
    if (!c) return;
    var els = (elements && elements.length) ? elements
      : c.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, false);
    // Only the means dataset (index 0) is clickable; ignore the 3m-MA line.
    var hit = null;
    if (els && els.length){
      for (var k = 0; k < els.length; k++){
        if (els[k].datasetIndex === 0){ hit = els[k]; break; }
      }
    }
    if (hit){
      var raw = c.data.datasets[0].data[hit.index];
      if (!raw || raw._monthIso == null) return;   // gap month -> no modal
      if (typeof window.openCohortDrilldown === 'function'){
        window.openCohortDrilldown(currentGroup, raw._monthIso);
      }
      return;
    }
    // No real dot hit -> test the off-scale pending diamond's x-band.
    var native = (evt && evt.native) ? evt.native : evt;
    var pend = pendingPointAtX(c, native);
    if (pend && pend.pt._monthIso != null
        && typeof window.openCohortDrilldown === 'function'){
      window.openCohortDrilldown(currentGroup, pend.pt._monthIso);
    }
  }

  // -- N strip labels (overlay div, no extra label plugin) -----------------
  function drawNLabels(){
    var box = document.getElementById('cohortNLabels');
    if (!box || !nChart) return;
    box.innerHTML = '';
    var meta = nChart.getDatasetMeta(0);
    var ds = nChart.data.datasets[0];
    var canvas = nChart.canvas;
    ds.data.forEach(function(pt, i){
      var el = meta.data[i];
      if (!el) return;
      if (!pt.y) return;            // gap month (n=0): no label, just the slot
      var span = document.createElement('span');
      span.textContent = pt.y;
      span.style.position = 'absolute';
      span.style.left = el.x + 'px';
      span.style.top = (el.y - 14) + 'px';
      span.style.transform = 'translateX(-50%)';
      span.style.fontSize = '10px';
      // Sprint 14 pending ruling (B-072): pending N label is slate-600 (in
      // progress), not red (which means low-N) and not the matured grey.
      span.style.color = pt._pending ? '#475569'
        : ((pt.y < 5) ? '#dc2626' : '#475569');
      box.appendChild(span);
    });
  }

  // -- Build / rebuild the Level-2 chart for one group ---------------------
  function build(group){
    var mount = document.getElementById('cohort-level2-mount');
    var empty = document.getElementById('cohortL2Empty');
    var strip = document.getElementById('cohortL2Strip');
    if (!mount) return;
    var g = GROUPS[group];
    var months = (g && g.months) ? g.months : [];
    var hex = (g && g.color_hex) || '#94a3b8';
    currentGroup = group;
    if (mount) mount.setAttribute('data-signal-group', group || '');

    // Brief decisions 1 & 2: expanding window since inception, one calendar
    // month per x-slot, gaps VISIBLE. The x-axis spans ALL months (the full
    // ascending months[] array, gaps included); only the VALUED months
    // (n_signals>0 && mean_car_{h}!=null) carry a dot/whisker -- gap months
    // emit a null y so Chart.js draws no dot and breaks the connecting line.
    var activeH = window.__cohortActiveHorizon || 't30';
    var all = (months || []).filter(function(m){ return !!m; });
    var real = all.filter(function(m){
      return m.n_signals > 0 && cohortPick(m, activeH, 'mean_car') != null;
    });

    // T+365 empty-state: when all months have null mean for t365.
    var t365EmptyMsg = document.getElementById('cohortT365Empty');
    if (t365EmptyMsg) t365EmptyMsg.style.display = 'none';
    if (activeH === 't365' && real.length === 0 && all.length > 0){
      if (empty) empty.style.display = 'flex';
      var emptyMsg = empty && empty.querySelector('p');
      if (emptyMsg) emptyMsg.textContent =
        'T+365 data will build over the coming year; fewer than 252 '
        + 'trading days of history exist for most signals.';
      if (strip) strip.textContent = '';
      var inl = document.getElementById('cohortFocusLabelInline');
      if (inl && g) inl.textContent = g.label || group;
      return;
    }

    if (mainChart) { mainChart.destroy(); mainChart = null; }
    if (nChart) { nChart.destroy(); nChart = null; }
    if (hitChart) { hitChart.destroy(); hitChart = null; }  // Phase 5 leak guard
    var nbox = document.getElementById('cohortNLabels');
    if (nbox) nbox.innerHTML = '';

    // Empty-state fires ONLY when the group has zero valued months at all.
    // A group with gaps but >=1 valued month renders the chart.
    if (!real.length){
      if (empty) empty.style.display = 'flex';
      if (strip) strip.textContent = '';
      var inl = document.getElementById('cohortFocusLabelInline');
      if (inl && g) inl.textContent = g.label || group;
      return;
    }
    if (empty) empty.style.display = 'none';
    // B-074: restore the single-group sub-panels (overlay mode hides them).
    setSingleGroupChromeVisible(true);

    // Header strip: N total / mean / hit across the real months.
    // B-079: meanAll = sum(month_mean * month_n) / total_n, which equals a
    // simple mean of all individual net CARs -- same maths as
    // mean_car_t30_overall in the export JSON.
    var nTot = real.reduce(function(a,m){ return a + m.n_signals; }, 0);
    var meanAll = real.reduce(function(a,m){
      return a + cohortPick(m, activeH, 'mean_car') * m.n_signals; }, 0) / (nTot || 1);
    var hitVals = real.filter(function(m){
      return cohortPick(m, activeH, 'hit_rate') != null; });
    var hitAll = hitVals.length
      ? hitVals.reduce(function(a,m){
          return a + cohortPick(m, activeH, 'hit_rate'); },0)/hitVals.length
      : null;
    var hLabel = HORIZON_LABELS_COHORT[activeH] || activeH.toUpperCase();
    if (strip){
      strip.innerHTML = 'N=<b class="text-slate-800">' + nTot + '</b> since inception'
        + ' &middot; <span title="Mean net CAR since inception (all fired signals, net of 50bps spread + stamp duty). Equivalent to mean_car_' + activeH + '_overall in the export JSON.">mean net CAR</span> @ ' + hLabel
        + ' <b class="text-slate-800">' + pctTxt(meanAll) + '</b>'
        + ' &middot; hit rate <b class="text-slate-800">'
        + (hitAll == null ? '-' : Math.round(hitAll*100) + '%') + '</b>';
    }

    // Category x-axis (NO time adapter is loaded -- only Chart.js core +
    // annotation are on the page, and the brief forbids a new CDN). We use a
    // 'category' scale keyed on formatted month labels; points are indexed by
    // position. This matches how the diagnostics chart already avoids the
    // date adapter.
    // B-074: monthLabel() is now hoisted to IIFE scope (shared with overlay).
    // Labels span EVERY calendar month (gaps included) so the axis is a true
    // expanding window since inception.
    var labels = all.map(function(m){ return monthLabel(m.month_iso); });

    // Means dataset (carry per-point custom props the plugins/tooltip read).
    // Gap months (mean_car_{h}==null) emit a null y: Chart.js draws no dot and
    // the connector line BREAKS across the gap (spanGaps is unset -> false).
    var pts = all.map(function(m, i){
      var meanH = cohortPick(m, activeH, 'mean_car');
      var valued = (m.n_signals > 0 && meanH != null);
      // Sprint 14 pending ruling (B-072): a month is pending for the active
      // horizon when: the export flags it in pending_horizons (Sprint 24) OR
      // (mean null && n>0). Fall back to m.pending for t30 backward compat.
      var pendingH;
      if (m.pending_horizons){
        pendingH = m.pending_horizons.indexOf(activeH) >= 0;
      } else {
        pendingH = (activeH === 't30')
          ? (m.pending === true)
          : (meanH == null && (m.n_signals || 0) > 0);
      }
      if (!valued){
        return {
          x: i, y: null,
          _meanFrac: null, _min: null, _max: null,
          _n: m.n_signals || 0, _lowN: false, _domTick: false, _hit: null,
          _pending: pendingH,
          _monthIso: pendingH ? m.month_iso : null,
          _monthLabel: monthLabel(m.month_iso) + ' (' + m.month_iso + ')'
        };
      }
      var minH  = cohortPick(m, activeH, 'min_car');
      var maxH  = cohortPick(m, activeH, 'max_car');
      var hitH  = cohortPick(m, activeH, 'hit_rate');
      var stwH  = cohortPick(m, activeH, 'single_ticker_weight');
      return {
        x: i,
        y: meanH * 100,
        _meanFrac: meanH,
        _min: minH == null ? null : minH * 100,
        _max: maxH == null ? null : maxH * 100,
        _n: m.n_signals,
        _lowN: m.n_signals < 5,
        _domTick: (stwH != null && stwH > 0.5),
        _hit: hitH,
        _pending: false,
        _monthIso: m.month_iso,
        _monthLabel: monthLabel(m.month_iso) + ' (' + m.month_iso + ')'
      };
    });
    // 3-month MA overlay (null for first 2 months / gap months -> break).
    var maPts = all.map(function(m, i){
      var ma = cohortPick(m, activeH, 'ma3_mean_car');
      return {x: i, y: (ma == null) ? null : ma * 100};
    });

    // Dynamic symmetric-ish y range = data extent (min of mins, max of maxes)
    // + 10% headroom. Falls back to the means if min/max absent.
    var lo = Infinity, hi = -Infinity;
    real.forEach(function(m){
      var meanH = cohortPick(m, activeH, 'mean_car');
      var minH  = cohortPick(m, activeH, 'min_car');
      var maxH  = cohortPick(m, activeH, 'max_car');
      var mn = (minH == null ? meanH : minH) * 100;
      var mx = (maxH == null ? meanH : maxH) * 100;
      if (mn < lo) lo = mn;
      if (mx > hi) hi = mx;
    });
    if (!isFinite(lo) || !isFinite(hi)) { lo = -5; hi = 5; }
    var span = (hi - lo) || 10;
    var pad = span * 0.10;
    var yMin = lo - pad, yMax = hi + pad;

    var ctx = document.getElementById('cohortMainChart');
    mainChart = new Chart(ctx, {
      type: 'scatter',
      data: { labels: labels, datasets: [
        {
          label: ((g && g.label) || group) + ' (' + hLabel + ')',
          data: pts,
          showLine: true, tension: 0, spanGaps: false,  // break at gap months
          borderColor: rgba(hex, 0.3),         // faded straight connector
          borderWidth: 1,
          pointRadius: function(c){ return c.raw._lowN ? 3 : 4; },
          pointHoverRadius: function(c){ return c.raw._lowN ? 5 : 6; },
          pointBackgroundColor: function(c){ return c.raw._lowN ? '#ffffff' : hex; },
          pointBorderColor: hex,
          pointBorderWidth: function(c){ return c.raw._lowN ? 2 : 1; },
          order: 1
        },
        {
          label: '3m MA',
          data: maPts,
          showLine: true, tension: 0, spanGaps: false,  // break at nulls
          borderColor: rgba(hex, 0.55),
          borderWidth: 1, borderDash: [2,2],
          pointRadius: 0,
          order: 2
        }
      ]},
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'nearest', intersect: true },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false }            // we render our own DOM tip
        },
        onClick: onCohortClick,
        scales: {
          // category axis -> no date adapter required (none is loaded)
          x: { type: 'category', labels: labels,
               offset: true,
               grid: { color: '#f1f5f9' },
               ticks: { color: '#64748b', font: { size: 11 } } },
          y: { type: 'linear', min: yMin, max: yMax,
               grid: { color: '#f1f5f9' },
               ticks: { color: '#64748b',
                        callback: function(v){ return v.toFixed(0) + '%'; } } }
        }
      },
      plugins: [pendingMarkers, whiskerPlugin, zeroLinePlugin, dominanceMarkers]
    });
    mainChart.$tierHex = hex;

    // Hover -> DOM tooltip + pointer cursor.
    ctx.onmousemove = function(evt){
      var els = mainChart.getElementsAtEventForMode(
        evt, 'nearest', { intersect: true }, false);
      if (els.length){
        var e = els[0];
        if (e.datasetIndex !== 0) { hideTip(); ctx.style.cursor = 'default'; return; }
        var raw = mainChart.data.datasets[0].data[e.index];
        var el = mainChart.getDatasetMeta(0).data[e.index];
        raw._px = el.x; raw._py = el.y;
        showTip(raw, ctx);
        ctx.style.cursor = 'pointer';
        return;
      }
      // Sprint 14 pending ruling (B-072): no dot hit -> test the off-scale
      // pending diamond band. Pointer + single-line pending tooltip when over
      // a pending month's x-slot; the tooltip anchors on the floor diamond.
      var pend = pendingPointAtX(mainChart, evt);
      if (pend){
        var area = mainChart.chartArea;
        pend.pt._px = pend.cx;
        pend.pt._py = area.bottom - 12;
        showTip(pend.pt, ctx);
        ctx.style.cursor = 'pointer';
        return;
      }
      hideTip(); ctx.style.cursor = 'default';
    };
    ctx.onmouseleave = function(){ hideTip(); ctx.style.cursor = 'default'; };

    // N strip -- shares the same category x-axis (offset:true) so bars align
    // with the main chart's dots. Spans EVERY calendar month (same `all`
    // array) so each month keeps its x-slot and the two charts stay aligned;
    // gap months have n_signals 0 -> a zero-height (absent) bar.
    // Sprint 14 pending ruling (B-072): carry _pending per bar so a pending
    // month's bar takes the distinct slate-400 @ 35% tint (in progress) instead
    // of the solid-grey matured (>=5) or amber-dashed low-N treatment.
    var nPts = all.map(function(m, i){
      var pendingN;
      if (m.pending_horizons){
        pendingN = m.pending_horizons.indexOf(activeH) >= 0;
      } else {
        pendingN = (activeH === 't30')
          ? (m.pending === true)
          : (cohortPick(m, activeH, 'mean_car') == null && (m.n_signals || 0) > 0);
      }
      return {x: i, y: m.n_signals || 0, _pending: pendingN};
    });
    var nctx = document.getElementById('cohortNStrip');
    nChart = new Chart(nctx, {
      type: 'bar',
      data: { labels: labels, datasets: [{
        data: nPts,
        backgroundColor: function(c){
          if (c.raw._pending) return 'rgba(148,163,184,0.35)';  // slate-400 @35%
          return c.raw.y < 5 ? 'rgba(0,0,0,0)' : '#cbd5e1';
        },
        borderColor: function(c){
          if (c.raw._pending) return 'rgba(0,0,0,0)';
          return c.raw.y < 5 ? '#f59e0b' : 'rgba(0,0,0,0)';
        },
        borderWidth: function(c){
          if (c.raw._pending) return 0;
          return c.raw.y < 5 ? 1 : 0;
        },
        borderDash: function(c){
          if (c.raw._pending) return [];
          return c.raw.y < 5 ? [3,3] : [];
        }
      }]},
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        // Sprint 14 pending ruling (B-072): the N-strip bar is a real Chart.js
        // element, so it carries the click for a pending month as the
        // reinforcing route (the main-chart floor diamond is the primary one).
        onClick: function(evt, els){
          if (!els || !els.length) return;
          var raw = nChart.data.datasets[0].data[els[0].index];
          var m = all[els[0].index];   // same `all` array drives both charts
          if (raw && raw._pending && m && m.month_iso
              && typeof window.openCohortDrilldown === 'function'){
            window.openCohortDrilldown(currentGroup, m.month_iso);
          }
        },
        onHover: function(evt, els){
          var tgt = evt.native ? evt.native.target : null;
          if (!tgt) return;
          var on = els && els.length
            && nChart.data.datasets[0].data[els[0].index]
            && nChart.data.datasets[0].data[els[0].index]._pending;
          tgt.style.cursor = on ? 'pointer' : 'default';
        },
        scales: {
          x: { type: 'category', labels: labels, offset: true, display: false },
          y: { display: false, beginAtZero: true,
               suggestedMax: Math.max.apply(null,
                 nPts.map(function(p){ return p.y; })) * 1.3 }
        },
        animation: { onComplete: drawNLabels }
      }
    });
    drawNLabels();

    // Phase 5 (B-069): rolling 6-month hit-rate panel. Shares the SAME
    // category month labels as the main chart (the full `all` array, gaps
    // included) so the two charts align column-for-column. Months whose
    // hit_rate_t30_rolling_6m is null (insufficient trailing window, or gap
    // months) emit a null y -> spanGaps:false breaks the line there. Teal so
    // it reads as a distinct series from the CAR means. Dashed grey 50%
    // baseline (baseline50Plugin) is the random-performance reference.
    var hitPts = all.map(function(m, i){
      var hr = cohortPick(m, activeH, 'hit_rate') != null
        ? m['hit_rate_' + activeH + '_rolling_6m']
        : null;
      return {x: i, y: (hr == null) ? null : hr * 100};
    });
    var hctx = document.getElementById('cohortHitRateChart');
    if (hctx){
      hitChart = new Chart(hctx, {
        type: 'line',
        data: { labels: labels, datasets: [{
          label: 'Rolling 6m hit rate',
          data: hitPts,
          showLine: true, tension: 0, spanGaps: false,  // break at null months
          borderColor: '#0d9488',                  // teal-600
          backgroundColor: 'rgba(13,148,136,0.08)', // light teal fill
          fill: true,
          pointRadius: 2,
          pointHoverRadius: 4,
          pointBackgroundColor: '#0d9488',
          pointBorderColor: '#0d9488'
        }]},
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: {
            x: { type: 'category', labels: labels, offset: true,
                 grid: { color: '#f1f5f9' },
                 ticks: { color: '#64748b', font: { size: 11 } } },
            y: { type: 'linear', min: 0, max: 100,
                 grid: { color: '#f1f5f9' },
                 ticks: { color: '#64748b',
                          callback: function(v){ return v + '%'; } } }
          }
        },
        plugins: [baseline50Plugin]
      });
    }

    syncPills(group);
  }

  // -- B-019: pill toggle + solo mode ------------------------------------
  // visibleSet: array of group IDs currently displayed on the chart.
  // Single-click: toggle the group in/out. Double-click: solo (show only).
  // When visibleSet.length === 1 -> full detailed chart via build().
  // When visibleSet.length > 1  -> simplified multi-line overlay.
  var visibleSet = [];

  function syncPills(set){
    document.querySelectorAll('#cohortPills .cohort-pill').forEach(function(b){
      var grp = b.getAttribute('data-grp');
      var on = (set.indexOf(grp) >= 0);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      if (on){
        b.classList.add('text-white');
        b.style.background = b.getAttribute('data-hex');
        b.style.opacity = '1';
        b.classList.add('ring-2','ring-offset-1');
      } else {
        b.classList.remove('text-white','ring-2','ring-offset-1');
        b.style.background = '';
        b.style.opacity = '0.4';
      }
    });
  }

  // Simplified multi-group overlay chart (means only, no whiskers).
  // B-074: this path was dead code (ReferenceError on monthLabel + activeH) and
  // is now wired up. It reads the active horizon from window state and tears
  // down the single-group-only sub-charts/chrome so nothing stale lingers.
  function buildOverlay(set){
    var focusEl = document.getElementById('cohortFocus');
    if (!focusEl || focusEl.hidden) return;
    var empty = document.getElementById('cohortL2Empty');
    var strip = document.getElementById('cohortL2Strip');
    if (empty) empty.style.display = 'none';
    var activeH = window.__cohortActiveHorizon || 't30';

    // Tear down single-group artefacts: the N-strip + rolling-hit charts and
    // their labels, then hide the panels that only make sense for one signal.
    if (nChart) { nChart.destroy(); nChart = null; }
    if (hitChart) { hitChart.destroy(); hitChart = null; }
    var nbox = document.getElementById('cohortNLabels');
    if (nbox) nbox.innerHTML = '';
    setSingleGroupChromeVisible(false);
    currentGroup = null;

    // Use first visible group's month axis as the shared x-axis.
    var firstGrp = GROUPS[set[0]] || {};
    var all0 = (firstGrp.months || []);
    var labels = all0.map(function(m){ return monthLabel(m.month_iso); });
    if (!labels.length){ if (empty) empty.style.display = 'flex'; return; }

    var lo = Infinity, hi = -Infinity;
    var datasets = [];
    set.forEach(function(grp){
      var g = GROUPS[grp] || {};
      var hex = g.color_hex || '#94a3b8';
      var all = g.months || [];
      var pts = all.map(function(m, i){
        var meanH = cohortPick(m, activeH, 'mean_car');
        return { x: i, y: (meanH == null ? null : meanH * 100) };
      });
      pts.forEach(function(p){
        if (p.y !== null){ if (p.y < lo) lo = p.y; if (p.y > hi) hi = p.y; }
      });
      datasets.push({
        label: g.label || grp,
        data: pts,
        showLine: true, tension: 0, spanGaps: false,
        borderColor: hex, borderWidth: 2,
        pointRadius: 2, pointHoverRadius: 4,
        pointBackgroundColor: hex, pointBorderColor: hex,
        _groupId: grp
      });
    });
    if (!isFinite(lo)) { lo = -5; hi = 5; }
    var span = (hi - lo) || 10;
    var yMin = lo - span * 0.12, yMax = hi + span * 0.12;

    if (mainChart) { mainChart.destroy(); mainChart = null; }
    var ctx = document.getElementById('cohortMainChart');
    mainChart = new Chart(ctx, {
      type: 'scatter',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, position: 'top',
          labels: { boxWidth: 10, font: { size: 10 }, color: '#475569' } },
          tooltip: { enabled: true } },
        scales: {
          x: { type: 'category', labels: labels, offset: true,
               grid: { color: '#f1f5f9' },
               ticks: { color: '#64748b', font: { size: 11 } } },
          y: { type: 'linear', min: yMin, max: yMax,
               grid: { color: '#f1f5f9' },
               ticks: { color: '#64748b',
                        callback: function(v){ return v.toFixed(0) + '%'; } } }
        }
      },
      plugins: [zeroLinePlugin]
    });
    if (strip) strip.textContent = 'Overlay: ' + set.map(function(g){
      return (GROUPS[g] && GROUPS[g].label) || g; }).join(', ');
  }

  function _dispatch(set, lastGrp){
    var g = GROUPS[lastGrp] || {};
    var labelEl = document.getElementById('cohortFocusLabel');
    if (labelEl) labelEl.textContent = g.label || lastGrp;
    if (set.length === 1){
      build(set[0]);
    } else {
      buildOverlay(set);
    }
    syncPills(set);
  }

  function buildPills(){
    var box = document.getElementById('cohortPills');
    if (!box || box.dataset.built === '1') return;
    box.dataset.built = '1';
    box.innerHTML = ORDER.map(function(grp){
      var g = GROUPS[grp] || {};
      var label = (g.label || grp).split(' ')[0];   // short tier code
      var hex = g.color_hex || '#94a3b8';
      return '<button type="button" role="tab" class="cohort-pill px-2 py-0.5 '
        + 'rounded text-xs font-semibold bg-slate-100 text-slate-600 '
        + 'hover:bg-slate-200" data-grp="' + grp + '" data-hex="' + hex + '" '
        + 'title="Click to toggle series. Double-click to solo." '
        + 'aria-selected="false">' + label + '</button>';
    }).join('');
    var dblTimer = null;
    box.querySelectorAll('.cohort-pill').forEach(function(b){
      b.addEventListener('click', function(evt){
        // Double-click detection: if a second click arrives within 300ms -> solo.
        if (dblTimer !== null){
          clearTimeout(dblTimer);
          dblTimer = null;
          var grp = b.getAttribute('data-grp');
          visibleSet = [grp];
          _dispatch(visibleSet, grp);
          return;
        }
        dblTimer = setTimeout(function(){
          dblTimer = null;
          var grp = b.getAttribute('data-grp');
          var idx = visibleSet.indexOf(grp);
          if (idx >= 0){
            // Clicking an active pill: remove it. Keep at least 1.
            if (visibleSet.length > 1){
              visibleSet.splice(idx, 1);
            }
            // If already the only one, leave it (no toggle-off to zero).
          } else {
            visibleSet.push(grp);
          }
          _dispatch(visibleSet, grp);
        }, 300);
      });
    });
  }

  // Public entry point called by the focus opener.
  // B-019: initialise visibleSet to [group] on first open; subsequent pill
  // clicks toggle/solo without resetting the set.
  window.buildCohortLevel2 = function(group){
    if (typeof Chart === 'undefined') return;   // Chart.js not loaded
    buildPills();
    if (!group || !GROUPS[group]){
      group = ORDER[0];
    }
    // Reset visibleSet to the requested group on every new focus-open.
    visibleSet = [group];
    _dispatch(visibleSet, group);
  };
})();
</script>"""


def _cohort_drilldown_modal() -> str:
    """Sprint 14 Phase 6 (B-070): the cohort drill-down modal shell.

    Hidden by default. Per design-spec State D + the "Drill-down modal backdrop
    and panel" snippet: a fixed full-screen layer (z-50) with a blurred slate
    backdrop (click-to-close) and a centred dialog carrying a header (title +
    summary line + verdict line + close X) and a scrollable table body whose
    thead/tbody are rendered by JS.

    IMPORTANT (avoids the Phase-4 empty-overlay bug where a bare `hidden`
    attribute was defeated by a `flex` utility): visibility is toggled via
    inline `style="display:none"` by the script -- NOT a Tailwind `hidden`
    class on a flex container. The wrapper carries no display utility of its
    own, so the JS show/hide is authoritative.
    """
    return (
        '<div id="cohort-drilldown" style="display:none" '
        'class="fixed inset-0 z-50">'
        # backdrop -- click to close
        '<div class="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" '
        'data-close-on-click></div>'
        # dialog
        '<div role="dialog" aria-modal="true" aria-labelledby="drillTitle" '
        'class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 '
        'w-[min(1000px,95vw)] max-h-[85vh] overflow-hidden bg-white '
        'border border-slate-200 rounded-lg shadow-2xl flex flex-col">'
        # header
        '<header class="px-5 py-3 border-b border-slate-200 flex items-start '
        'justify-between">'
        '<div>'
        '<h3 class="text-base font-semibold text-slate-800" id="drillTitle">'
        '</h3>'
        '<p class="text-xs text-slate-500 mt-0.5" id="drillSummary"></p>'
        '<p class="text-xs text-amber-700 mt-1" id="drillVerdict"></p>'
        '</div>'
        '<button type="button" class="text-slate-400 hover:text-slate-700 '
        'text-lg leading-none px-1" data-close-modal '
        'aria-label="Close">&times;</button>'
        '</header>'
        # scrollable table body
        '<div class="overflow-auto">'
        '<table class="w-full text-xs tabular-nums" id="drillTable"></table>'
        '</div>'
        '</div>'
        '</div>'
    )


def _cohort_drilldown_script() -> str:
    """Sprint 14 Phase 6 (B-070): drill-down modal open/close/sort + table.

    Exposes `window.openCohortDrilldown(group, monthIso)` which the Level-2
    chart's onCohortClick calls. Reads rows from
    `window.__cohortData.drilldown[group][monthIso].signals` and renders the
    9-column table (Ticker | Director | Fire date | CAR T+1 | CAR T+21 |
    CAR T+90 | Sector benchmark | Net of costs | Share of cohort movement).

    Default sort: Share of cohort movement (cohort_weight) descending. Column
    headers are clickable to re-sort with an asc/desc toggle. Closes on the X
    button, a backdrop click ([data-close-on-click]), or Esc.

    Show/hide is driven by inline style.display (NOT the hidden+flex trap).
    """
    return r"""
<script>
(function(){
  'use strict';
  var modal = document.getElementById('cohort-drilldown');
  if (!modal) return;
  var titleEl   = document.getElementById('drillTitle');
  var summaryEl = document.getElementById('drillSummary');
  var verdictEl = document.getElementById('drillVerdict');
  var tableEl   = document.getElementById('drillTable');

  // 10 columns. `key` maps to the signal field; `share` flags the abs-share
  // column (rendered 0-100%); `pctwhole` is a value already in percent
  // (abs_return_ann). The CAR / benchmark / net columns are signed fractions.
  // B-126: relabel the CAR / benchmark / net columns. The maths is unchanged;
  // the old labels confused "abnormal return" (benchmark already removed) with
  // "net of costs". New labels say what each column actually is, with tooltips.
  // B-127: new 'Stock return' column = raw return from the announcement-date
  // close to the latest close (no benchmark, no costs); value already in %.
  var COLS = [
    { key: 'ticker',         label: 'Ticker',                  type: 'text' },
    { key: 'director',       label: 'Director',                type: 'director' },
    { key: 'fire_date',      label: 'Fire date',               type: 'date' },
    { key: 'car_t1',         label: 'Excess vs benchmark (T+1)',  type: 'pct',
      help: 'Stock return minus its sector benchmark, 1 trading day after the signal.' },
    { key: 'car_t30',        label: 'Excess vs benchmark (T+30)', type: 'pct',
      help: 'Stock return minus its sector benchmark, 21 trading days after the signal.' },
    { key: 'car_t90',        label: 'Excess vs benchmark (T+90)', type: 'pct',
      help: 'Stock return minus its sector benchmark, 90 trading days after the signal.' },
    { key: 'benchmark_t30',  label: 'Benchmark return (already removed)', type: 'pct',
      help: 'The sector benchmark return over the T+30 window. Shown for reference -- it is already subtracted inside the Excess columns.' },
    { key: 'net_car_t30',    label: 'Excess, after costs',     type: 'pct',
      help: 'Excess vs benchmark (T+30), minus 50bps spread (and a further 50bps stamp on non-AIM buys).' },
    { key: 'abs_return_ann', label: 'Stock return (since announcement)', type: 'pctwhole',
      help: 'Raw stock return from the announcement-date close to the LATEST close (lifetime-to-date, not a fixed window). No benchmark removed, no trading costs. NOTE: this spans a different period than the T+1/T+21/T+90 Excess columns -- do not subtract one from the other.' },
    { key: 'cohort_weight',  label: 'Share of cohort movement', type: 'share' }
  ];

  var state = { rows: [], sortKey: 'cohort_weight', sortDir: 'desc',
                title: '', summary: '', verdict: '', pending: false };
  // Em-dash used for unmeasured cells in a pending cohort (browser-side HTML
  // entity is allowed -- this is not a piped print(); cp1252 rule covers Python
  // prints only). Sprint 14 pending ruling (B-072).
  var EMDASH = '&mdash;';
  // Columns whose value cannot exist until the cohort matures (T+21-derived).
  // For a pending cohort these always render as an em-dash regardless of value.
  var PENDING_EMDASH_KEYS = {
    car_t30: 1, car_t90: 1, benchmark_t30: 1, net_car_t30: 1, cohort_weight: 1
  };

  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function monthLabel(iso){
    var p = (iso || '').split('-');
    var mi = parseInt(p[1], 10) - 1;
    var names = ['January','February','March','April','May','June','July',
                 'August','September','October','November','December'];
    if (isNaN(mi) || mi < 0 || mi > 11) return iso;
    return names[mi] + ' ' + (p[0] || '');
  }
  // signed percent, one decimal (e.g. +2.1%, -18.4%). Input is a fraction.
  function signedPct(v){
    if (v == null) return '-';
    return (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
  }
  // bounded 0-100% share (input is a fraction in [0,1]).
  function sharePct(v){
    if (v == null) return '-';
    return (v * 100).toFixed(0) + '%';
  }
  // B-127: signed percent for a value ALREADY in percent (e.g. 33.3 -> +33.3%).
  // The CAR/net columns store fractions (signedPct *100s them); abs_return_ann
  // is emitted directly in percent, so it has its own formatter.
  function signedPctWhole(v){
    if (v == null) return '-';
    return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
  }
  function pctClass(v){
    if (v == null) return 'text-slate-400';
    if (v > 0.0005) return 'text-emerald-600';
    if (v < -0.0005) return 'text-rose-600';
    return 'text-slate-600';
  }

  function sortRows(){
    var k = state.sortKey, dir = state.sortDir === 'asc' ? 1 : -1;
    state.rows.sort(function(a, b){
      var av = a[k], bv = b[k];
      // text columns -> locale compare; numeric -> nulls last.
      if (k === 'ticker' || k === 'director' || k === 'fire_date'){
        return String(av || '').localeCompare(String(bv || '')) * dir;
      }
      if (av == null && bv == null) return 0;
      if (av == null) return 1;        // nulls always last
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
  }

  function render(){
    sortRows();
    var head = '<thead class="bg-slate-50 text-slate-600 uppercase '
      + 'tracking-wide text-[10px] sticky top-0">'
      + '<tr>' + COLS.map(function(col){
        var isSorted = (col.key === state.sortKey);
        var caret = isSorted ? (state.sortDir === 'asc' ? ' &#9650;'
                                                        : ' &#9660;') : '';
        var align = (col.type === 'text' || col.type === 'director'
                     || col.type === 'date') ? 'text-left' : 'text-right';
        var help = '';
        if (col.help){
          // B-126/B-127: per-column tooltip text (quotes escaped for the attr).
          help = ' title="' + String(col.help).replace(/"/g, '&quot;') + '"';
        } else if (col.type === 'share'){
          help = " title=\"Each ticker's share of the cohort's total absolute T+21 "
            + "net CAR movement (sums to 100%). Bigger = more of the month's "
            + "result, win or lose. See the Net column for direction.\"";
        }
        return '<th class="px-3 py-2 ' + align + ' cursor-pointer '
          + 'hover:text-slate-900 select-none" data-sort-key="'
          + col.key + '"' + help + '>' + esc(col.label) + caret + '</th>';
      }).join('') + '</tr></thead>';

    var body = '<tbody>' + state.rows.map(function(r){
      return '<tr class="border-t border-slate-100 hover:bg-slate-50">'
        + COLS.map(function(col){
          var v = r[col.key];
          if (col.key === 'ticker'){
            // #4 (2026-06-03): link the ticker to its company page, matching
            // the Today/This-Week tables. Raw ticker mirrors those tables'
            // links; dotted tickers (rare) share the same known limitation.
            var tk = esc(v);
            // B-184: link to the dynamic company template (company.html?ticker=)
            // instead of the removed static companies/{TICKER}.html pages.
            return '<td class="px-3 py-2 text-left font-medium">'
              + (v ? '<a href="company.html?ticker=' + encodeURIComponent(v) + '" '
                   + 'class="text-blue-600 hover:underline font-mono">'
                   + tk + '</a>' : '')
              + '</td>';
          }
          if (col.type === 'text'){
            return '<td class="px-3 py-2 text-left font-medium text-slate-800">'
              + esc(v) + '</td>';
          }
          if (col.type === 'director'){
            var role = r.role_short ? ' <span class="text-slate-400">('
              + esc(r.role_short) + ')</span>' : '';
            return '<td class="px-3 py-2 text-left text-slate-700">'
              + esc(v) + role + '</td>';
          }
          if (col.type === 'date'){
            return '<td class="px-3 py-2 text-left text-slate-600">'
              + esc(v) + '</td>';
          }
          // Sprint 14 pending ruling (B-072): for a pending cohort the
          // T+21-derived columns are not yet computable -> render an em-dash
          // (slate-400) regardless of the row value.
          if (state.pending && PENDING_EMDASH_KEYS[col.key]){
            return '<td class="px-3 py-2 text-right text-slate-400">'
              + EMDASH + '</td>';
          }
          if (col.type === 'share'){
            return '<td class="px-3 py-2 text-right text-slate-700">'
              + sharePct(v) + '</td>';
          }
          if (col.type === 'pctwhole'){
            // B-127: value already in percent (announcement-date raw return).
            return '<td class="px-3 py-2 text-right ' + pctClass(v) + '">'
              + signedPctWhole(v) + '</td>';
          }
          // pct (signed, coloured)
          return '<td class="px-3 py-2 text-right ' + pctClass(v) + '">'
            + signedPct(v) + '</td>';
        }).join('') + '</tr>';
    }).join('') + '</tbody>';

    tableEl.innerHTML = head + body;
    // Re-wire header sort clicks (innerHTML wiped prior listeners).
    tableEl.querySelectorAll('th[data-sort-key]').forEach(function(th){
      th.addEventListener('click', function(){
        var key = th.getAttribute('data-sort-key');
        if (state.sortKey === key){
          state.sortDir = (state.sortDir === 'asc') ? 'desc' : 'asc';
        } else {
          state.sortKey = key;
          // text columns default asc; numeric/share default desc.
          var col = COLS.filter(function(c){ return c.key === key; })[0];
          state.sortDir = (col && (col.type === 'text'
            || col.type === 'director' || col.type === 'date'))
            ? 'asc' : 'desc';
        }
        render();
      });
    });
  }

  function open(){
    modal.style.display = 'block';
    document.body.classList.add('overflow-hidden');
    var x = modal.querySelector('[data-close-modal]');
    if (x) x.focus();
  }
  function close(){
    modal.style.display = 'none';
    // Don't strip overflow-hidden if focus-mode still wants it; focus-mode
    // re-adds it on its own. Removing here is safe because the modal opens
    // from within focus-mode, which re-applies the class when it (re)opens.
  }

  // Public entry point (called by the Level-2 chart's onCohortClick).
  window.openCohortDrilldown = function(group, monthIso){
    var blob = window.__cohortData || {};
    var dd = (blob.drilldown || {})[group] || {};
    var entry = dd[monthIso] || { verdict: '', signals: [] };
    var sigs = (entry.signals || []).slice();   // copy: we sort in place
    // Header title + summary line.
    var label = '';
    var g = (blob.groups || {})[group];
    if (g && g.label) label = g.label;

    // Sprint 14 pending ruling (B-072): a cohort is pending when the drilldown
    // entry flags it, OR the months[] blob flags the month pending, OR every
    // row's net_car_t30 is null while signals exist (derived fallback for an
    // older export that lacks the flag).
    var monthMeta = null;
    if (g && g.months){
      for (var mi = 0; mi < g.months.length; mi++){
        if (g.months[mi].month_iso === monthIso){ monthMeta = g.months[mi]; break; }
      }
    }
    var derivedPending = sigs.length > 0 && sigs.every(function(s){
      return s.net_car_t30 == null;
    });
    var pending = !!(entry.pending
      || (monthMeta && (monthMeta.pending === true
          || (monthMeta.mean_car_t30 == null && (monthMeta.n_signals || 0) > 0)))
      || derivedPending);

    state.rows = sigs;
    state.pending = pending;
    state.verdict = pending ? '' : (entry.verdict || '');
    // Default sort: matured -> Share of cohort movement desc; pending -> the
    // share column is empty, so order by Fire date desc (newest fired first).
    if (pending){
      state.sortKey = 'fire_date';
      state.sortDir = 'desc';
    } else {
      state.sortKey = 'cohort_weight';
      state.sortDir = 'desc';
    }

    titleEl.textContent = monthLabel(monthIso) + ' cohort - ' + (label || group);

    if (pending){
      // Summary carries no mean/range (those are null). ASCII hyphen.
      summaryEl.textContent = sigs.length
        + ' signals fired - T+30 return not yet matured';
      // Header note replaces the verdict line; slate (neutral), NOT amber.
      verdictEl.textContent = 'This cohort has not yet matured. T+30 (and T+90) '
        + 'returns are still pending; figures below are partial.';
      verdictEl.className = 'text-xs text-slate-600 mt-1';
      verdictEl.style.display = '';
    } else {
      // Summary: N signals - mean x.x% - range min% to max% (over net_car_t30).
      var nets = sigs.map(function(s){ return s.net_car_t30; })
                     .filter(function(v){ return v != null; });
      if (nets.length){
        var mean = nets.reduce(function(a, v){ return a + v; }, 0) / nets.length;
        var lo = Math.min.apply(null, nets);
        var hi = Math.max.apply(null, nets);
        summaryEl.textContent = sigs.length + ' signals - mean '
          + signedPct(mean) + ' - range ' + signedPct(lo) + ' to ' + signedPct(hi);
      } else {
        summaryEl.textContent = sigs.length + ' signals';
      }
      // Restore the amber verdict styling for measured cohorts.
      verdictEl.className = 'text-xs text-amber-700 mt-1';
      // Verdict line (empty string -> show nothing).
      if (state.verdict){
        verdictEl.textContent = state.verdict;
        verdictEl.style.display = '';
      } else {
        verdictEl.textContent = '';
        verdictEl.style.display = 'none';
      }
    }
    render();
    open();
  };

  // Close affordances: X button, backdrop click, Esc key.
  modal.querySelectorAll('[data-close-modal]').forEach(function(btn){
    btn.addEventListener('click', close);
  });
  modal.querySelectorAll('[data-close-on-click]').forEach(function(bg){
    bg.addEventListener('click', close);
  });
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape' && modal.style.display !== 'none') close();
  });
})();
</script>"""


def _strategy_pct_chart_block(pct: list, tier_meta: dict, _json,
                               pct_by_horizon=None, signal_meta=None) -> str:
    """Multi-line indexed-% strategy chart.

    When *pct_by_horizon* and *signal_meta* are supplied (from
    ``build_strategy_tracker``), renders an interactive version with
    T+1 / T+30 / T+90 / T+365 horizon pills and per-signal chip toggles.
    Falls back to the simple T+30 chart when the new data is absent.
    """
    note_html = (
        '<div class="px-4 pb-3 pt-2 text-[10px] text-slate-400">'
        'Each line = cumulative <b>excess return vs FTSE All-Share</b> for a '
        'flat-&pound;10k/signal book (same cash timing, so cash-drag cancels) '
        '&middot; <b>0% = FTSE benchmark</b>, above = beating the market '
        '&middot; entry T+1 after announcement, exit at selected horizon '
        '&middot; costs: 50bps spread + 0.5% stamp on non-AIM buys. '
        'Tier lines use value filters (PCA &gt;&pound;5k, CFO/Chair &gt;&pound;100k). '
        '<b>Small N &mdash; hypothesis to watch, not proven edge.</b>'
        '</div>'
    )

    # ------------------------------------------------------------------
    # Simple fallback: no multi-horizon data available
    # ------------------------------------------------------------------
    if not pct_by_horizon or not signal_meta:
        def _n(key):
            return (tier_meta.get(key) or {}).get("n", 0)
        labels_js = _json.dumps([p["date"] for p in pct])
        all_js  = _json.dumps([p["all"]  for p in pct])
        t5_js   = _json.dumps([p["t5"]   for p in pct])
        t1b_js  = _json.dumps([p["t1b"]  for p in pct])
        t7_js   = _json.dumps([p["t7"]   for p in pct])
        ftse_js = _json.dumps([p["ftse"] for p in pct])
        return (
            '<div class="px-4 py-3"><div style="position:relative;height:260px">'
            '<canvas id="strategyTrackerChart"></canvas></div></div>'
            + note_html
            + f'<script>(function(){{'
            f'var labels={labels_js};'
            f'var dAll={all_js};var dT5={t5_js};var dT1b={t1b_js};var dT7={t7_js};'
            f'var dFtse={ftse_js};'
            f'var nAll={_n("all")};var nT5={_n("t5")};var nT1b={_n("t1b")};var nT7={_n("t7")};'
            r"""
  function fmtPct(v){return (v>=0?'+':'')+v.toFixed(1)+'%';}
  var ctx=document.getElementById('strategyTrackerChart');
  if(ctx && typeof Chart!=='undefined'){
    var ds=[];
    function add(data,label,color,dash,width){
      if(!data.some(function(v){return v!=null;})) return;
      ds.push({label:label,data:data,borderColor:color,backgroundColor:color,
        borderWidth:width,borderDash:dash,pointRadius:0,tension:0.1,
        fill:false,spanGaps:true});
    }
    add(dAll,'All buy signals (n='+nAll+')','#2563eb',[],2);
    add(dT5,'T5 PCA >£5k (n='+nT5+')','#10b981',[],2);
    add(dT1b,'T1B CFO >£100k (n='+nT1b+')','#7c3aed',[],2);
    add(dT7,'T7 Chair >£100k (n='+nT7+')','#f59e0b',[],2);
    add(dFtse,'FTSE All-Share (benchmark = 0%)','#64748b',[4,3],1.5);
    new Chart(ctx,{type:'line',data:{labels:labels,datasets:ds},
      options:{responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{legend:{display:true,position:'top',
            labels:{boxWidth:10,font:{size:10},color:'#475569'}},
          tooltip:{callbacks:{label:function(c){
            return c.dataset.label+': '+(c.raw==null?'-':fmtPct(c.raw));}}}},
        scales:{x:{grid:{display:false},
            ticks:{color:'#94a3b8',font:{size:9},maxTicksLimit:8,maxRotation:0}},
          y:{grid:{color:'#f1f5f9'},
            ticks:{color:'#64748b',font:{size:10},
              callback:function(v){return v.toFixed(0)+'%';}}}}}});
  }
"""
            f'}})();</script>'
        )

    # ------------------------------------------------------------------
    # Interactive version: horizon pills + per-signal chip toggles
    # ------------------------------------------------------------------
    all_data_js  = _json.dumps(pct_by_horizon)
    sig_meta_js  = _json.dumps(signal_meta)
    tier_meta_js = _json.dumps(tier_meta)

    sig_chips = ""
    for sid, smeta in signal_meta.items():
        col   = smeta.get("color", "#64748b")
        lbl   = smeta.get("label", sid)
        n_sig = smeta.get("n", 0)
        sig_chips += (
            f'<button data-sid="{sid}" data-color="{col}" '
            'class="strat-sig-btn text-[10px] px-1.5 py-0.5 rounded border '
            'border-slate-200 text-slate-500 whitespace-nowrap">'
            f'{lbl} ({n_sig})</button>'
        )

    js_vars = (
        'var ALL_DATA=' + all_data_js + ';'
        + 'var SIG_META=' + sig_meta_js + ';'
        + 'var TIER_META=' + tier_meta_js + ';'
    )

    js_logic = r"""
  var panel=document.getElementById('strategyTrackerPanel');
  var ctx=document.getElementById('strategyTrackerChart');
  if(!ctx||typeof Chart==='undefined') return;
  var activeH='t30';
  var activeSigs={};

  function buildDs(){
    var hd=ALL_DATA[activeH]||{tiers:{},signals:{}};
    var dates=ALL_DATA.dates||[];
    var ds=[];
    function addT(key,label,color,dash,w){
      var d=hd.tiers&&hd.tiers[key];
      if(!d||!d.some(function(v){return v!=null;})) return;
      ds.push({label:label,data:d,borderColor:color,backgroundColor:color,
        borderWidth:w,borderDash:dash,pointRadius:0,tension:0.1,fill:false,
        spanGaps:true,_t:'tier',_key:key});
    }
    var tm=TIER_META||{};
    addT('all','All buy signals (n='+((tm.all||{}).n||0)+')','#2563eb',[],2.5);
    addT('t5',((tm.t5||{}).label||'T5 PCA')+' (n='+((tm.t5||{}).n||0)+')','#10b981',[],2);
    addT('t1b',((tm.t1b||{}).label||'T1B CFO')+' (n='+((tm.t1b||{}).n||0)+')','#7c3aed',[],2);
    addT('t7',((tm.t7||{}).label||'T7 Chair')+' (n='+((tm.t7||{}).n||0)+')','#f59e0b',[],2);
    ds.push({label:'FTSE (0%)',data:new Array(dates.length).fill(0),
      borderColor:'#94a3b8',backgroundColor:'#94a3b8',borderWidth:1.5,
      borderDash:[4,3],pointRadius:0,tension:0,fill:false,_t:'ftse'});
    Object.keys(SIG_META).forEach(function(sid){
      var sm=SIG_META[sid];
      var d=(hd.signals&&hd.signals[sid])||[];
      ds.push({label:sm.label+' (n='+(sm.n||0)+')',data:d,
        borderColor:sm.color,backgroundColor:sm.color,
        borderWidth:1.5,borderDash:[2,2],pointRadius:0,tension:0.1,
        fill:false,spanGaps:true,hidden:!activeSigs[sid],_t:'sig',_sid:sid});
    });
    return ds;
  }

  function fmtPct(v){return (v>=0?'+':'')+v.toFixed(1)+'%';}

  var chart=new Chart(ctx,{type:'line',
    data:{labels:ALL_DATA.dates||[],datasets:buildDs()},
    options:{responsive:true,maintainAspectRatio:false,animation:{duration:150},
      interaction:{mode:'index',intersect:false},
      plugins:{
        legend:{display:true,position:'top',
          labels:{boxWidth:10,font:{size:10},color:'#475569',
            filter:function(item,data){
              return data.datasets[item.datasetIndex]._t!=='sig';
            }
          }
        },
        tooltip:{callbacks:{label:function(c){
          if(c.dataset.hidden) return null;
          return c.dataset.label+': '+(c.raw==null?'-':fmtPct(c.raw));
        }}}
      },
      scales:{
        x:{grid:{display:false},
          ticks:{color:'#94a3b8',font:{size:9},maxTicksLimit:8,maxRotation:0}},
        y:{grid:{color:'#f1f5f9'},
          ticks:{color:'#64748b',font:{size:10},
            callback:function(v){return v.toFixed(0)+'%';}}}
      }
    }
  });

  function refresh(){
    var hd=ALL_DATA[activeH]||{tiers:{},signals:{}};
    chart.data.datasets.forEach(function(ds){
      if(ds._t==='tier'&&ds._key){
        ds.data=(hd.tiers&&hd.tiers[ds._key])||[];
      } else if(ds._t==='sig'&&ds._sid){
        ds.data=(hd.signals&&hd.signals[ds._sid])||[];
        ds.hidden=!activeSigs[ds._sid];
      }
    });
    chart.update('none');
  }

  // Horizon pill buttons
  var hBtns=panel.querySelectorAll('.strat-h-btn');
  hBtns.forEach(function(btn){
    btn.addEventListener('click',function(){
      hBtns.forEach(function(b){
        b.classList.remove('bg-blue-600','border-blue-600','text-white');
        b.classList.add('border-slate-300','text-slate-600');
      });
      btn.classList.remove('border-slate-300','text-slate-600');
      btn.classList.add('bg-blue-600','border-blue-600','text-white');
      activeH=btn.getAttribute('data-h');
      refresh();
    });
  });

  // Signal chip toggles
  panel.querySelectorAll('.strat-sig-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      var sid=btn.getAttribute('data-sid');
      var col=btn.getAttribute('data-color')||'#2563eb';
      if(activeSigs[sid]){
        delete activeSigs[sid];
        btn.style.borderColor='';btn.style.color='';btn.style.backgroundColor='';
      } else {
        activeSigs[sid]=true;
        btn.style.borderColor=col;btn.style.color=col;
        btn.style.backgroundColor=col+'1a';
      }
      refresh();
    });
  });
"""

    return (
        '<div id="strategyTrackerPanel">'
        '<div class="flex items-center gap-1 px-4 pt-3 pb-1">'
        '<span class="text-[10px] text-slate-400 mr-1 shrink-0">Horizon:</span>'
        '<button data-h="t1"   class="strat-h-btn text-[10px] px-2 py-0.5 rounded border border-slate-300 text-slate-600">T+1</button>'
        '<button data-h="t30"  class="strat-h-btn text-[10px] px-2 py-0.5 rounded border bg-blue-600 border-blue-600 text-white">T+30</button>'
        '<button data-h="t90"  class="strat-h-btn text-[10px] px-2 py-0.5 rounded border border-slate-300 text-slate-600">T+90</button>'
        '<button data-h="t180" class="strat-h-btn text-[10px] px-2 py-0.5 rounded border border-slate-300 text-slate-600">T+180</button>'
        '<button data-h="t365" class="strat-h-btn text-[10px] px-2 py-0.5 rounded border border-slate-300 text-slate-600">T+365</button>'
        '</div>'
        '<div class="flex flex-wrap items-center gap-1 px-4 pb-2">'
        '<span class="text-[10px] text-slate-400 mr-1 shrink-0">Signals:</span>'
        + sig_chips
        + '</div>'
        '<div class="px-4 py-2"><div style="position:relative;height:260px">'
        '<canvas id="strategyTrackerChart"></canvas>'
        '</div></div>'
        + note_html
        + '<script>(function(){' + js_vars + js_logic + '})();</script>'
        + '</div>'
    )


def _strategy_tracker_section(signals_data: dict) -> str:
    """B-123 -- £10k-per-signal strategy tracker vs ^FTSE All-Share shadow.

    Reads ``signals_data["strategy_tracker"]`` ({series, summary}) written by
    ``export_dashboard_json.build_strategy_tracker()``. Two-line chart over the
    full history + headline £/% excess and trailing-30-day trend chips.
    Omitted entirely when the builder returned no data.
    """
    import json as _json
    st = signals_data.get("strategy_tracker") or {}
    series = st.get("series") or []
    summary = st.get("summary") or {}
    if not series or not summary:
        return ""

    sv = summary.get("strategy_value_gbp") or 0
    fv = summary.get("ftse_value_gbp") or 0
    excess = summary.get("excess_gbp") or 0
    excess_pct = summary.get("excess_pct")
    n_pos = summary.get("n_positions") or 0
    deployed = summary.get("capital_deployed_gbp") or 0
    strat_trend = summary.get("strategy_trend_30d_pct")
    ftse_trend = summary.get("ftse_trend_30d_pct")

    def _gbp(v) -> str:
        v = v or 0
        a = abs(v)
        if a >= 1_000_000:
            return f"&pound;{v/1_000_000:.2f}m"
        if a >= 1_000:
            return f"&pound;{v/1_000:.0f}k"
        return f"&pound;{v:.0f}"

    def _trend_chip(pct) -> str:
        if pct is None:
            return ""
        arrow = "&#9650;" if pct >= 0 else "&#9660;"
        color = "text-emerald-600" if pct >= 0 else "text-rose-500"
        return (f'<span class="ml-1 text-[10px] {color} tabular-nums">'
                f'{arrow} {abs(pct):.1f}% 30d</span>')

    excess_cls = "text-emerald-600" if excess >= 0 else "text-rose-600"
    excess_sign = "+" if excess >= 0 else "-"
    pct_str = ("-" if excess_pct is None
               else f'{"+" if excess_pct >= 0 else ""}{excess_pct:.2f}%')

    def _stat(label, value, extra="", chip=""):
        return (
            '<div class="text-center">'
            f'<div class="text-[10px] uppercase tracking-wide text-slate-500 mb-0.5">{label}</div>'
            f'<div class="text-sm font-semibold tabular-nums {extra}">{value}{chip}</div>'
            '</div>'
        )

    stats_html = (
        '<div class="flex items-start justify-around gap-4 px-4 py-3 border-b border-slate-100">'
        + _stat("Strategy value", _gbp(sv), "text-slate-800", _trend_chip(strat_trend))
        + _stat("FTSE shadow", _gbp(fv), "text-slate-800", _trend_chip(ftse_trend))
        + _stat("Excess vs FTSE", f"{excess_sign}{_gbp(abs(excess))}", excess_cls)
        + _stat("Excess %", pct_str, excess_cls)
        + _stat("Positions", f"{n_pos} &middot; {_gbp(deployed)}", "text-slate-800")
        + '</div>'
    )

    # Indexed-% multi-tier chart when the exporter provides pct_series; else the
    # legacy £ two-line chart (also the path the render unit test exercises).
    pct = st.get("pct_series") or []
    tier_meta = st.get("tier_meta") or {}
    if pct:
        chart_block = _strategy_pct_chart_block(
            pct, tier_meta, _json,
            pct_by_horizon=st.get("pct_by_horizon"),
            signal_meta=st.get("signal_meta"),
        )
        subtitle = ('Cumulative excess return vs FTSE All-Share (alpha) per '
                    '&pound;10k/signal book &middot; toggle horizon &amp; signals below')
    else:
        subtitle = ('Portfolio value (realised + open MTM) vs an identical '
                    '&pound;10k/signal FTSE All-Share shadow')
        dates_js = _json.dumps([s["date"] for s in series])
        strat_js = _json.dumps([s["strategy_value_gbp"] for s in series])
        ftse_js = _json.dumps([s["ftse_value_gbp"] for s in series])
        note_html = (
            '<div class="px-4 pb-3 pt-2 text-[10px] text-slate-400">'
            'Flat &pound;10,000 staked on every transaction that fired a buy signal '
            '(deduped per transaction) &middot; enter the trading day after announcement, '
            'sell at T+30 &middot; costs charged: 50bps spread + 0.5% stamp on non-AIM buys '
            '(equity leg); the FTSE All-Share (^FTAS) shadow buys &pound;10,000 on the same '
            'dates with spread only &middot; un-deployed stake held as cash from inception. '
            'This is a flat-stake strategy &mdash; not the conviction-sized paper book below.'
            '</div>'
        )
        chart_block = (
            '<div class="px-4 py-3"><div style="position:relative;height:240px">'
            '<canvas id="strategyTrackerChart"></canvas></div></div>'
            + note_html
            + f'<script>(function(){{'
            f'var labels={dates_js};'
            f'var stratData={strat_js};'
            f'var ftseData={ftse_js};'
            r"""
  function fmtGbp(v){
    var a=Math.abs(v);
    if(a>=1000000) return '\xA3'+(v/1000000).toFixed(2)+'m';
    if(a>=1000) return '\xA3'+(v/1000).toFixed(0)+'k';
    return '\xA3'+v.toFixed(0);
  }
  var ctx=document.getElementById('strategyTrackerChart');
  if(ctx && typeof Chart!=='undefined'){
    new Chart(ctx,{
      type:'line',
      data:{labels:labels,datasets:[
        {label:'Strategy',data:stratData,borderColor:'#2563eb',
         backgroundColor:'rgba(37,99,235,0.08)',borderWidth:2,
         pointRadius:0,tension:0.1,fill:true},
        {label:'FTSE All-Share shadow',data:ftseData,borderColor:'#64748b',
         backgroundColor:'rgba(100,116,139,0.05)',borderWidth:1.5,
         pointRadius:0,borderDash:[4,3],tension:0.1,fill:false}
      ]},
      options:{responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{
          legend:{display:true,position:'top',
            labels:{boxWidth:10,font:{size:10},color:'#475569'}},
          tooltip:{callbacks:{
            label:function(c){return c.dataset.label+': '+fmtGbp(c.raw);},
            afterbody:function(items){
              if(items.length<2) return '';
              var s=items[0].raw, f=items[1].raw;
              if(s==null||f==null) return '';
              var d=s-f;
              return 'Excess: '+(d>=0?'+':'-')+fmtGbp(Math.abs(d));
            }
          }}
        },
        scales:{
          x:{grid:{display:false},
             ticks:{color:'#94a3b8',font:{size:9},maxTicksLimit:8,maxRotation:0}},
          y:{grid:{color:'#f1f5f9'},
             ticks:{color:'#64748b',font:{size:10},
                    callback:function(v){return fmtGbp(v);}}}
        }
      }
    });
  }
"""
            f'}})();</script>'
        )

    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<div class="px-4 py-3 border-b border-slate-100">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500">'
        'Strategy tracker &mdash; &pound;10k per buy signal vs FTSE All-Share</h2>'
        f'<p class="text-[10px] text-slate-400 mt-0.5">{subtitle}</p>'
        '</div>'
        + stats_html
        + chart_block
        + '</section>'
    )


_SMALL_CAP_THRESHOLD_GBP = 500_000_000  # £500m — matches classify_small_cap.py


def _paper_book_section(signals_data: dict,
                        size_band: str | None = None) -> str:
    """B-100 Phase A -- Live paper book panel.

    Reads ``signals_data["paper_book"]`` written by
    ``export_dashboard_json.build_paper_book_summary()``.
    Read-only: no paper_trades table required.
    Renders a compact summary stat strip + a scrollable positions table
    (OPEN positions first, then CLOSED, newest fired_at first within each).
    Absent or empty paper_book -> empty state pointing at the exporter.

    ``size_band`` -- when "small" or "large", filters positions by
    market_cap_gbp threshold (£500m) and recomputes summary stats from the
    filtered set so the stat strip reflects the scoped view.
    """
    pb = signals_data.get("paper_book")
    if not pb or not isinstance(pb, dict):
        return (
            '<section class="m-6 bg-white border border-slate-200 rounded-lg p-4">'
            '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-2">'
            'Live paper book</h2>'
            '<div class="text-xs text-slate-500">'
            'Paper book unavailable -- run '
            '<code class="text-slate-700">export_dashboard_json.py</code>'
            ' to refresh.</div>'
            '</section>'
        )

    positions = pb.get("positions") or []

    # Filter positions by cap size when rendering small/large performance pages.
    if size_band == "small":
        positions = [p for p in positions
                     if p.get("market_cap_gbp") is not None
                     and p["market_cap_gbp"] < _SMALL_CAP_THRESHOLD_GBP]
    elif size_band == "large":
        positions = [p for p in positions
                     if p.get("market_cap_gbp") is not None
                     and p["market_cap_gbp"] >= _SMALL_CAP_THRESHOLD_GBP]

    # Recompute summary stats from (potentially filtered) positions so the
    # stat strip matches the scoped table exactly.
    _open_pos = [p for p in positions if (p.get("status") or "OPEN") == "OPEN"]
    _mtm_vals = [p["mtm_pct"] for p in _open_pos if p.get("mtm_pct") is not None]
    open_n    = len(_open_pos)
    closed_n  = len([p for p in positions if (p.get("status") or "OPEN") == "CLOSED"])
    notional  = sum(p.get("notional_gbp") or 0 for p in _open_pos)
    mtm_mean  = (sum(_mtm_vals) / len(_mtm_vals)) if _mtm_vals else None
    winners   = sum(1 for v in _mtm_vals if v > 0)
    losers    = sum(1 for v in _mtm_vals if v <= 0)

    # Summary stat strip.
    def _stat(label, value, extra_cls=""):
        return (
            '<div class="text-center">'
            f'<div class="text-[10px] uppercase tracking-wide text-slate-500 mb-0.5">{label}</div>'
            f'<div class="text-sm font-semibold tabular-nums {extra_cls}">{value}</div>'
            '</div>'
        )

    mtm_cls = ""
    if mtm_mean is not None:
        mtm_cls = "text-emerald-600" if mtm_mean > 0 else "text-rose-600"
    mtm_str = ("-" if mtm_mean is None
               else (("+" if mtm_mean >= 0 else "") + f"{mtm_mean:.2f}%"))
    notional_str = ("£" + (f"{notional/1000:.0f}k" if notional >= 1000
                           else f"{notional:.0f}"))

    stats_html = (
        '<div class="flex items-start justify-around gap-4 px-4 py-3 '
        'border-b border-slate-100">'
        + _stat("Open positions", str(open_n))
        + _stat("Closed (T+30)", str(closed_n))
        + _stat("Capital deployed", notional_str)
        + _stat("Mean MTM", mtm_str, mtm_cls)
        + _stat("Winners / Losers",
                f'<span class="text-emerald-600">{winners}</span>'
                f' / <span class="text-rose-500">{losers}</span>')
        + '</div>'
    )

    # Positions table — open positions only.
    open_positions = [p for p in positions if (p.get("status") or "OPEN") == "OPEN"]
    if not open_positions:
        table_html = (
            '<div class="px-4 py-6 text-xs text-slate-400 italic text-center">'
            'No open positions -- signals either haven\'t fired yet or all are past their exit horizon.'
            '</div>'
        )
    else:
        rows_html = []
        for p in open_positions[:200]:   # cap at 200 rows for page weight
            status = p.get("status") or "OPEN"
            mtm = p.get("mtm_pct")
            entry_close = p.get("entry_close")
            current_close = p.get("current_close")
            notional_p = p.get("notional_gbp")

            # MTM cell.
            if mtm is None:
                mtm_html = '<span class="text-slate-300">-</span>'
            elif mtm > 0:
                mtm_html = f'<span class="text-emerald-600">+{mtm:.2f}%</span>'
            elif mtm < 0:
                mtm_html = f'<span class="text-rose-600">{mtm:.2f}%</span>'
            else:
                mtm_html = f'<span class="text-slate-500">{mtm:.2f}%</span>'

            entry_str = (f"{entry_close:.4f}" if entry_close is not None else "-")
            curr_str  = (f"{current_close:.4f}" if current_close is not None else "-")
            notional_str_p = ("-" if notional_p is None
                              else "£" + (f"{notional_p/1000:.0f}k"
                                          if notional_p >= 1000
                                          else str(int(notional_p))))
            hold_d = p.get("hold_days") or 0
            fired = (p.get("fired_at") or "")[:10]
            ticker = h.esc(p.get("ticker") or "")
            company = h.esc((p.get("company") or "")[:25])
            director = h.esc((p.get("director") or "")[:20])
            mktcap_str = h.fmt_mktcap(p.get("market_cap_gbp"))  # B-146
            sid = h.esc(p.get("signal_id") or "")          # B-124: filter key
            sig_badge = h.render_badge(p.get("signal_id") or "")
            # B-124: badge is a live filter — clicking filters the table to this
            # signal; clicking again clears. data-paper-sid keys each row.
            badge_btn = (
                f'<button type="button" data-pb-filter="{sid}" '
                f'class="pb-badge-btn cursor-pointer bg-transparent border-0 p-0" '
                f'title="Filter to this signal">{sig_badge}</button>'
            )

            search_val = f"{ticker} {company} {director}".lower()
            row_cls = "border-t border-slate-100 bg-white"
            rows_html.append(
                f'<tr class="{row_cls} text-xs" data-paper-sid="{sid}" data-paper-search="{h.esc(search_val)}">'
                f'<td class="px-2 py-1.5 whitespace-nowrap">{badge_btn}</td>'
                f'<td class="px-2 py-1.5 font-medium text-slate-800">{ticker}</td>'
                f'<td class="px-2 py-1.5 text-slate-500 truncate max-w-[120px]">{company}</td>'
                f'<td class="px-2 py-1.5 text-slate-500 tabular-nums text-right whitespace-nowrap">{mktcap_str}</td>'
                f'<td class="px-2 py-1.5 text-slate-500 truncate max-w-[100px]">{director}</td>'
                f'<td class="px-2 py-1.5 tabular-nums text-slate-500">{fired}</td>'
                f'<td class="px-2 py-1.5 tabular-nums text-right">{entry_str}</td>'
                f'<td class="px-2 py-1.5 tabular-nums text-right">{curr_str}</td>'
                f'<td class="px-2 py-1.5 tabular-nums text-right">{notional_str_p}</td>'
                f'<td class="px-2 py-1.5 tabular-nums text-right">{mtm_html}</td>'
                f'<td class="px-2 py-1.5 tabular-nums text-right text-slate-500">{hold_d}d</td>'
                '</tr>'
            )

        tbody_html = "".join(rows_html)
        table_html = (
            '<div class="overflow-x-auto">'
            '<table class="w-full text-xs tabular-nums">'
            '<thead class="bg-slate-50 text-slate-500 uppercase tracking-wide text-[10px]">'
            '<tr>'
            '<th class="px-2 py-2 text-left">Signal</th>'
            '<th class="px-2 py-2 text-left">Ticker</th>'
            '<th class="px-2 py-2 text-left">Company</th>'
            '<th class="px-2 py-2 text-right">Mkt Cap</th>'
            '<th class="px-2 py-2 text-left">Director</th>'
            '<th class="px-2 py-2 text-left">Fired</th>'
            '<th class="px-2 py-2 text-right">Entry</th>'
            '<th class="px-2 py-2 text-right">Current</th>'
            '<th class="px-2 py-2 text-right">Notional</th>'
            '<th class="px-2 py-2 text-right">MTM</th>'
            '<th class="px-2 py-2 text-right">Hold</th>'
            '</tr></thead>'
            f'<tbody>{tbody_html}</tbody>'
            '</table>'
            '</div>'
        )

    note_html = (
        '<div class="px-4 pb-2 text-[10px] text-slate-400">'
        'Open positions only. Entry = first close on/after signal fire date; '
        'notional = conviction-sized (spec 07 log scale, GBP 5k cap). '
        'Costs not deducted. Hold = calendar days since signal fired.'
        '</div>'
    )

    # B-124: clicking a row's signal badge filters the table to that signal.
    # Also wires the search box (ticker / company / director).
    filter_script = (
        '<script>(function(){'
        'var sec=document.currentScript.closest("section");if(!sec)return;'
        'var rows=sec.querySelectorAll("tr[data-paper-sid]");'
        'var statusEl=sec.querySelector("[data-pb-filter-status]");'
        'var searchEl=sec.querySelector("[data-pb-search]");'
        'var active=null;'
        'function apply(){'
        'var q=(searchEl?searchEl.value:"").toLowerCase().trim();'
        'rows.forEach(function(r){'
        'var sigMatch=(!active||r.getAttribute("data-paper-sid")===active);'
        'var srchMatch=(!q||(r.getAttribute("data-paper-search")||"").includes(q));'
        'r.style.display=(sigMatch&&srchMatch)?"":"none";});'
        'if(statusEl){'
        'if(active){statusEl.innerHTML="Filtered to <span class=\\"font-semibold\\">"'
        '+active+"</span> &middot; <button type=\\"button\\" data-pb-clear '
        'class=\\"underline\\">clear</button>";statusEl.style.display="";}'
        'else{statusEl.textContent="";statusEl.style.display="none";}}}'
        'sec.querySelectorAll("[data-pb-filter]").forEach(function(b){'
        'b.addEventListener("click",function(){'
        'var s=b.getAttribute("data-pb-filter");active=(active===s)?null:s;apply();});});'
        'sec.addEventListener("click",function(e){'
        'if(e.target.closest("[data-pb-clear]")){active=null;apply();}});'
        'if(searchEl)searchEl.addEventListener("input",apply);'
        '})();</script>'
    )
    return (
        '<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">'
        '<div class="px-4 py-3 border-b border-slate-100 flex items-start justify-between gap-4 flex-wrap">'
        '<div>'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500">'
        'Live paper book</h2>'
        '<p class="text-[10px] text-slate-400 mt-0.5">'
        'One entry per signal firing &middot; conviction-sized (spec 07 log scale, '
        'GBP 5k cap) &middot; T+30 exit horizon &middot; '
        '<span class="text-slate-500">click a signal badge to filter</span></p>'
        '<div data-pb-filter-status class="text-[10px] text-amber-700 mt-1" '
        'style="display:none"></div>'
        '</div>'
        '<input data-pb-search type="search" placeholder="Search ticker / company…" '
        'class="text-xs border border-slate-200 rounded px-2 py-1 w-48 focus:outline-none focus:ring-1 focus:ring-indigo-300">'
        '</div>'
        + stats_html
        + table_html
        + note_html
        + filter_script
        + '</section>'
    )


def _monthly_buysell_chart(signals_data: dict) -> str:
    """B-102 + sprint-34: Trailing 12-month buy/sell bar chart + totals + drilldown.

    Additions vs B-102:
    - Totals row above chart: 12mo buy/sell totals, counts, ratio, trend arrows.
    - Click handler: clicking any bar opens a drilldown panel below the chart
      showing that month's top transactions from monthly_txns.
    """
    import json as _json
    mbs = signals_data.get("monthly_buysell") or {}
    months = mbs.get("months") or []
    buy_vals = mbs.get("buy_values") or []
    sell_vals = mbs.get("sell_values") or []
    buy_cnts = mbs.get("buy_counts") or []
    sell_cnts = mbs.get("sell_counts") or []
    if not months:
        return ""
    has_data = any(v is not None for v in buy_vals) or any(v is not None for v in sell_vals)
    if not has_data:
        return ""

    # Sprint-34 extras.
    t12_buy   = mbs.get("trailing12_buy_total") or 0.0
    t12_sell  = mbs.get("trailing12_sell_total") or 0.0
    t12_buyn  = mbs.get("trailing12_buy_count") or 0
    t12_selln = mbs.get("trailing12_sell_count") or 0
    trend_buy_pct  = mbs.get("trend_buy_pct")
    trend_sell_pct = mbs.get("trend_sell_pct")
    monthly_txns   = mbs.get("monthly_txns") or {}

    def _gbp(v: float) -> str:
        a = abs(v)
        if a >= 1_000_000:
            return f"£{v/1_000_000:.1f}m"
        if a >= 1_000:
            return f"£{v/1_000:.0f}k"
        return f"£{v:.0f}"

    def _trend_chip(pct, positive_good: bool = True) -> str:
        if pct is None:
            return ""
        arrow = "&#9650;" if pct >= 0 else "&#9660;"
        color = "text-emerald-600" if (pct >= 0) == positive_good else "text-rose-500"
        return (
            f'<span class="ml-1 text-[10px] {color} tabular-nums">'
            f'{arrow} {abs(pct):.0f}% vs prev 3m</span>'
        )

    ratio_str = ""
    if t12_buy > 0 and t12_sell > 0:
        ratio = t12_buy / t12_sell
        ratio_str = f" &middot; buy:sell {ratio:.1f}x"

    # Totals bar HTML.
    totals_html = (
        '<div class="flex flex-wrap items-center gap-x-4 gap-y-0.5 mb-3 text-xs">'
        f'<span class="text-emerald-700 font-medium">'
        f'Buys: {_gbp(t12_buy)} ({t12_buyn} tx)</span>'
        f'{_trend_chip(trend_buy_pct, positive_good=True)}'
        f'<span class="text-rose-600 font-medium">'
        f'Sells: {_gbp(t12_sell)} ({t12_selln} tx)</span>'
        f'{_trend_chip(trend_sell_pct, positive_good=False)}'
        f'<span class="text-slate-400 text-[11px]">{ratio_str}</span>'
        '</div>'
    )

    # Format month labels as short "Mon 'YY".
    def _fmt_mo(iso: str) -> str:
        try:
            parts = iso.split("-")
            names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            return names[int(parts[1]) - 1] + " '" + parts[0][2:]
        except (IndexError, ValueError):
            return iso

    labels_js    = _json.dumps([_fmt_mo(m) for m in months])
    months_js    = _json.dumps(months)          # ISO keys for drilldown lookup
    buy_js       = _json.dumps(buy_vals)
    sell_js      = _json.dumps(sell_vals)
    buy_cnt_js   = _json.dumps(buy_cnts)
    sell_cnt_js  = _json.dumps(sell_cnts)
    txns_js      = _json.dumps(monthly_txns)    # {YYYY-MM: [{ticker,...}, ...]}

    return (
        '<section class="mx-6 mb-4 bg-white border border-slate-200 rounded-lg p-4">'
        '<h2 class="text-xs uppercase tracking-wide text-slate-500 mb-2">'
        'Monthly Activity — Trailing 12 months (£ value)</h2>'
        + totals_html +
        '<div style="position:relative;height:160px">'
        '<canvas id="buySellChart" style="cursor:pointer" '
        'title="Click a bar to see that month\'s top transactions"></canvas>'
        '</div>'
        '<!-- sprint-34 drilldown panel -->'
        '<div id="bscDrilldown" style="display:none" '
        'class="mt-3 border-t border-slate-100 pt-3">'
        '<div class="flex items-center justify-between mb-1">'
        '<span id="bscDrillTitle" class="text-xs font-semibold text-slate-700"></span>'
        '<button onclick="document.getElementById(\'bscDrilldown\').style.display=\'none\'" '
        'class="text-[10px] text-slate-400 hover:text-slate-600">&#x2715; close</button>'
        '</div>'
        '<div id="bscDrillBody" class="overflow-x-auto">'
        '<table class="w-full text-[11px] border-collapse">'
        '<thead><tr class="text-slate-400 border-b border-slate-100">'
        '<th class="text-left py-1 pr-2 font-medium">Ticker</th>'
        '<th class="text-left py-1 pr-2 font-medium">Company</th>'
        '<th class="text-left py-1 pr-2 font-medium">Director</th>'
        '<th class="text-center py-1 pr-2 font-medium">Type</th>'
        '<th class="text-right py-1 font-medium">Value</th>'
        '</tr></thead>'
        '<tbody id="bscDrillRows"></tbody>'
        '</table>'
        '</div>'
        '</div>'
        f'<script>(function(){{'
        f'var labels={labels_js};'
        f'var months={months_js};'
        f'var buyVals={buy_js};'
        f'var sellVals={sell_js};'
        f'var buyCnts={buy_cnt_js};'
        f'var sellCnts={sell_cnt_js};'
        f'var txns={txns_js};'
        r"""
  function fmtGbp(v){
    var a=Math.abs(v);
    if(a>=1000000) return '\xA3'+(v/1000000).toFixed(1)+'m';
    if(a>=1000) return '\xA3'+(v/1000).toFixed(0)+'k';
    return '\xA3'+v.toFixed(0);
  }
  function showDrill(idx){
    var mo=months[idx];
    var rows=txns[mo]||[];
    var title=labels[idx]+' ('+mo+') — top transactions (buys & sells)';
    document.getElementById('bscDrillTitle').textContent=title;
    var tbody=document.getElementById('bscDrillRows');
    if(!rows.length){
      tbody.innerHTML='<tr><td colspan="5" class="py-2 text-slate-400 text-center">No data</td></tr>';
    } else {
      tbody.innerHTML=rows.map(function(r){
        var typeCol=r.type==='BUY'
          ?'<td class="text-center py-1 pr-2 text-emerald-600 font-semibold">BUY</td>'
          :'<td class="text-center py-1 pr-2 text-rose-500 font-semibold">SELL</td>';
        return '<tr class="border-b border-slate-50 hover:bg-slate-50">'
          +'<td class="py-1 pr-2 font-mono text-blue-600">'
          +'<a href="company.html?ticker='+encodeURIComponent(r.ticker)+'" target="_blank" class="hover:underline">'+r.ticker+'</a></td>'
          +'<td class="py-1 pr-2 text-slate-700 truncate max-w-[120px]">'+r.company+'</td>'
          +'<td class="py-1 pr-2 text-slate-500 truncate max-w-[100px]">'+r.director+'</td>'
          +typeCol
          +'<td class="py-1 text-right tabular-nums text-slate-700">'+fmtGbp(r.value)+'</td>'
          +'</tr>';
      }).join('');
    }
    document.getElementById('bscDrilldown').style.display='block';
  }
  var ctx=document.getElementById('buySellChart');
  if(ctx && typeof Chart!=='undefined'){
    var chart=new Chart(ctx,{
      type:'bar',
      data:{labels:labels,datasets:[
        {label:'Buys',data:buyVals,backgroundColor:'rgba(16,185,129,0.75)',
         borderColor:'#10b981',borderWidth:1,barPercentage:0.7},
        {label:'Sells',data:sellVals,backgroundColor:'rgba(244,63,94,0.65)',
         borderColor:'#f43f5e',borderWidth:1,barPercentage:0.7}
      ]},
      options:{responsive:true,maintainAspectRatio:false,
        onClick:function(evt,elems){
          if(elems&&elems.length>0){showDrill(elems[0].index);}
        },
        plugins:{
          legend:{display:true,position:'top',labels:{boxWidth:10,font:{size:10},color:'#475569'}},
          tooltip:{callbacks:{
            label:function(ctx){
              var ds=ctx.datasetIndex;
              var i=ctx.dataIndex;
              var val=ctx.raw;
              if(val==null) return null;
              var cnt=ds===0?buyCnts[i]:sellCnts[i];
              return (ds===0?'Buys':'Sells')+': '+fmtGbp(Math.abs(val))+' ('+cnt+' tx) — click to drill';
            }
          }}
        },
        scales:{
          x:{grid:{display:false},ticks:{color:'#64748b',font:{size:10}}},
          y:{grid:{color:'#f1f5f9'},
             ticks:{color:'#64748b',font:{size:10},
                    callback:function(v){return fmtGbp(v);}}}
        }
      }
    });
  }
"""
        f'}})();</script>'
        '</section>'
    )


def render(signals_data: dict, status_overrides: dict | None = None,
           build_sha: str = "local",
           cohort_data: dict | None = None,
           size_band: str | None = None) -> str:
    """Render the performance page HTML.

    ``size_band`` — Sprint 56 Phase C:
      * ``"small"``  → title / subtitle for small-cap page.
      * ``"large"``  → title / subtitle for large-cap page.
      * ``None``     → combined "All" behaviour (unchanged).
    """
    status_overrides = status_overrides or {}
    cohort_groups = (cohort_data or {}).get("groups") or {}
    # Phase 6 (B-070): the drill-down blob already lives in cohort_data; thread
    # it to the browser alongside the per-group months (additive).
    cohort_drilldown = (cohort_data or {}).get("cohort_drilldown") or {}

    horizon_options = "".join(
        f'<option value="{k}"{" selected" if k == "t30" else ""}>{h.esc(v)}</option>'
        for k, v in HORIZON_LABELS.items()
    )

    header_extra = (
        '<div class="px-6 py-3 flex items-center justify-end gap-3">'
        '<label class="text-xs text-slate-500">Horizon:</label>'
        '<select id="horizon" class="text-xs border border-slate-300 rounded '
        'px-2 py-1 bg-white tabular-nums">'
        f'{horizon_options}'
        '</select>'
        '</div>'
    )

    scoreboard = _scoreboard(signals_data, status_overrides, "t30",
                             cohort_groups=cohort_groups)
    # Phase 7 (B-071): gate the legacy trailing-12m CAR line chart behind the
    # feature flag. Default on (A/B period); flip SHOW_LEGACY_CAR_LINE_CHART to
    # False to retire it and leave the cohort view as the sole CAR surface.
    diag_section = (
        _diagnostics_chart_section() if SHOW_LEGACY_CAR_LINE_CHART else ""
    )
    # Performance page redesign v1 (FE Sprint 1): three-tile cohort cuts
    # reading from `cohorts_v2`. Replaces the old 2-tile section. The
    # legacy `_cohort_value_section` / `_cohort_sector_section` functions
    # remain in the module (dead code) for backwards-import safety —
    # nothing inside this codebase calls them after this rewire.
    cohorts = _cohort_section(signals_data)
    model = _model_assessment(signals_data)
    hit_rate = _hit_rate_panel(signals_data)
    pending = _pending_diagnostics_section(signals_data)
    # B-100 Phase A: live paper book (read-only snapshot from signals + prices).
    # Pass size_band so small/large pages filter their position lists.
    paper_book = _paper_book_section(signals_data, size_band=size_band)
    # B-123: £10k-per-signal strategy tracker vs FTSE All-Share shadow.
    strategy_tracker = _strategy_tracker_section(signals_data)
    # B-102: trailing 12-month buy/sell chart.
    buysell_chart = _monthly_buysell_chart(signals_data)
    payload = _client_data_payload(signals_data, status_overrides)
    # B-073: small-multiples signal overview grid (above cohort cuts, below pending).
    signal_overview = _signal_overview_section(cohort_groups)

    body = (
        header_extra
        + scoreboard
        + strategy_tracker
        + buysell_chart
        + diag_section
        + cohorts
        + model
        + hit_rate
        + pending
        + paper_book
        + signal_overview
        + _cohort_focus_overlay()
        # Phase 6 (B-070): the drill-down modal shell (hidden by default).
        + _cohort_drilldown_modal()
        + payload
        # Phase 4 (B-068): per-group monthly cohort series for the Level-2
        # chart, plus the chart builder script. Emitted before the focus
        # script so window.__cohortData + window.buildCohortLevel2 exist when
        # the focus opener calls them.
        # Phase 6 (B-070): the same blob now also carries `drilldown`.
        + _cohort_client_data(cohort_groups, cohort_drilldown)
        + _cohort_level2_script()
        # Phase 6 (B-070): the modal open/close/sort wiring + table renderer.
        + _cohort_drilldown_script()
        + _cohort_section_script()
        + _cohort_focus_script()
        + templates.toast_and_modal_js()
        + _chart_js()
        # B-073: signal overview mini-chart JS (after __cohortData is set).
        + _signal_overview_script()
    )

    # Sprint 56 Phase C: title, subtitle, and nav vary by size_band.
    _NAV_ALL = [
        ("Today",     "index.html"),
        ("Small Cap", "performance_small.html"),
        ("Large Cap", "performance_large.html"),
        ("All",       "performance.html"),
        ("Baskets",   "baskets.html"),
        ("Review",    "/review"),
    ]
    if size_band == "small":
        _title    = "Performance — Small Cap | Directors Dealings"
        _subtitle = (
            '<p class="text-xs text-slate-500 px-6 pb-1">'
            'Signals fired on companies with market cap &lt; &pound;500m</p>'
        )
    elif size_band == "large":
        _title    = "Performance — Large Cap | Directors Dealings"
        _subtitle = (
            '<p class="text-xs text-slate-500 px-6 pb-1">'
            'Signals fired on companies with market cap &ge; &pound;500m</p>'
        )
    else:
        _title    = "Directors Dealings - Performance"
        _subtitle = ""

    return templates.base_page(
        title=_title,
        body=_subtitle + body,
        generated_at_iso=signals_data.get("generated_at"),
        build_sha=build_sha,
        nav_links=_NAV_ALL,
    )


def _chart_js() -> str:
    """Returns the inline scripts that draw the diagnostics + cohort charts.

    Subscribes to a horizonChange custom event for the diagnostics chart and
    rebuilds the sparkline + scoreboard cells when the horizon dropdown
    changes. (Sparkline + scoreboard cells rebuild via DOM since we
    pre-rendered for t30.)

    SIDS is templated from `h.SIGNAL_DISPLAY_ORDER` so the JS chart and
    legend stay in sync with the Python-side display order
    (B-025 Phase B added t1a, t1b, t5, t6, t7 and removed t1).
    """
    sids_js = "[" + ",".join(f"'{s}'" for s in h.SIGNAL_DISPLAY_ORDER) + "]"
    return """
<script>
(function(){
  const data = window.__perfData || {};
  const tierHex = data.tier_hex || {};
  // B-058 (2026-05-22): two horizon-aggregate datasets. Default is the
  // 365d window (matches what the page rendered server-side). Toggle
  // switches to the all-time variant which includes firings older than
  // a year — useful for surfacing the recovered bundled-filing data
  // from Sprint 7's B-023.
  const horizons365d = data.horizon_aggregates || {};
  const horizonsAll = data.horizon_aggregates_all || data.horizon_aggregates || {};
  const SIDS = """ + sids_js + """;
  const select = document.getElementById('horizon');
  let state = { horizon: 't30', allTime: false };
  function currentHorizons() {
    return state.allTime ? horizonsAll : horizons365d;
  }

  function pct(v, dp){
    if (v === null || v === undefined) return '-';
    dp = (dp === undefined) ? 2 : dp;
    const sign = v > 0 ? '+' : '';
    return sign + v.toFixed(dp) + '%';
  }
  function carClass(v){
    if (v === null || v === undefined) return 'text-slate-400';
    if (v > 0.05) return 'text-emerald-600';
    if (v < -0.05) return 'text-rose-600';
    return 'text-slate-500';
  }
  // The legacy `spark()` SVG helper was removed with the 12-week sparkline
  // scoreboard column (superseded by the Phase 3 Trajectory cohort sparkline,
  // rendered server-side). The diagnostics chart consumes `d.sparkline`
  // directly via pad13() below, so the export field is retained.

  // ── B-019: legend click-to-toggle + dblclick-to-solo ─────────────────────
  // Diagnostics chart was hard to read with 7 signal series overlapping.
  // Custom legend chips (rendered into #diagLegend) are now buttons:
  //   * single click  → toggle that series' visibility
  //   * double click  → solo (hide every other series; FTSE benchmark stays)
  // The 'show all' link to the right of the chips resets to default. Hidden
  // state survives reloads via localStorage keyed by chart id so the user
  // doesn't lose their focus on every page refresh.
  const DIAG_HIDDEN_KEY = 'diagChart.hiddenSids.v1';
  const FTSE_LABEL = 'FTSE A-S';

  function loadHiddenSids(){
    try {
      const raw = localStorage.getItem(DIAG_HIDDEN_KEY);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch(e) { return new Set(); }
  }
  function saveHiddenSids(set){
    try { localStorage.setItem(DIAG_HIDDEN_KEY, JSON.stringify(Array.from(set))); }
    catch(e) { /* localStorage may be disabled; non-fatal */ }
  }

  function applyDiagVisibility(chart, hidden){
    if (!chart) return;
    chart.data.datasets.forEach(function(ds, i){
      // Benchmark line is the reference -- always visible regardless of solo.
      const shouldShow = (ds.label === FTSE_LABEL) || !hidden.has(ds.label);
      chart.setDatasetVisibility(i, shouldShow);
    });
    chart.update();
  }

  function renderDiagLegend(chart){
    const legendEl = document.getElementById('diagLegend');
    if (!legendEl || !chart) return;
    const hidden = loadHiddenSids();
    const chips = chart.data.datasets.map(function(ds){
      const isFtse = (ds.label === FTSE_LABEL);
      const isHidden = !isFtse && hidden.has(ds.label);
      const opacityCls = isHidden ? 'opacity-40' : '';
      const cursorCls = isFtse ? 'cursor-default' : 'cursor-pointer';
      const swatchOpacity = ds.borderDash ? ';opacity:0.7' : '';
      const title = isFtse
        ? 'FTSE All-Share benchmark (always visible)'
        : 'Click to toggle, double-click to focus on just this series';
      return '<button type="button" data-sid="' + ds.label + '" '
        + 'title="' + title + '" '
        + 'class="legend-chip inline-flex items-center gap-1 ' + cursorCls + ' ' + opacityCls
        + ' hover:opacity-100 transition-opacity bg-transparent border-0 p-0 select-none">'
        + '<span class="inline-block w-3 h-3 rounded-sm" style="background:'
        + ds.borderColor + swatchOpacity + '"></span>'
        + ds.label + '</button>';
    }).join('');
    // 'show all' link only shows when at least one series is hidden -- keeps
    // the legend uncluttered when no focus is in effect.
    const showAllHtml = (hidden.size > 0)
      ? ' <button type="button" id="diagShowAll" '
        + 'class="ml-2 text-indigo-600 hover:text-indigo-700 underline text-[10px] '
        + 'cursor-pointer bg-transparent border-0 p-0">show all</button>'
      : '';
    legendEl.innerHTML = chips + showAllHtml;

    // Wire chip handlers. Note: rebind every render -- innerHTML wipes prior listeners.
    legendEl.querySelectorAll('button.legend-chip').forEach(function(btn){
      const sid = btn.getAttribute('data-sid');
      if (sid === FTSE_LABEL) return;  // benchmark not toggleable

      btn.addEventListener('click', function(){
        const next = loadHiddenSids();
        if (next.has(sid)) next.delete(sid); else next.add(sid);
        saveHiddenSids(next);
        applyDiagVisibility(chart, next);
        renderDiagLegend(chart);
      });

      btn.addEventListener('dblclick', function(e){
        e.preventDefault();  // browsers select text on dblclick
        // Solo: hide every non-benchmark, non-clicked series.
        const next = new Set();
        chart.data.datasets.forEach(function(ds){
          if (ds.label !== FTSE_LABEL && ds.label !== sid) next.add(ds.label);
        });
        saveHiddenSids(next);
        applyDiagVisibility(chart, next);
        renderDiagLegend(chart);
      });
    });

    const showAllBtn = document.getElementById('diagShowAll');
    if (showAllBtn) {
      showAllBtn.addEventListener('click', function(){
        const empty = new Set();
        saveHiddenSids(empty);
        applyDiagVisibility(chart, empty);
        renderDiagLegend(chart);
      });
    }
  }

  function rebuildScoreboard(){
    // B-058: source dataset is toggled by `state.allTime`.
    const h = currentHorizons()[state.horizon] || {};
    const baseRate = h.base_rate;
    const sigs = h.signals || {};
    const rows = document.querySelectorAll('#scoreboard tbody tr');
    rows.forEach(function(tr){
      const sid = tr.getAttribute('data-signal-id');
      const d = sigs[sid] || {};
      const cells = tr.children;
      const trades = d.trades || 0;
      let nHtml = String(trades);
      if (trades > 0 && trades < 20) {
        nHtml += '<span class="ml-1 text-amber-600" title="N<20 - preliminary">&#9888;</span>';
      } else if (trades === 0) {
        nHtml = '<span class="text-slate-300">-</span>';
      }
      cells[1].innerHTML = nHtml;
      let hitHtml = '<span class="text-slate-300">-</span>';
      if (d.hit_pct !== null && d.hit_pct !== undefined && baseRate !== undefined) {
        let cls = 'text-slate-700';
        if (d.hit_pct >= baseRate) cls = 'text-emerald-600';
        else if (d.hit_pct < baseRate * 0.85) cls = 'text-rose-600';
        hitHtml = '<span class="' + cls + '">' + d.hit_pct.toFixed(1) + '%</span> / '
          + '<span class="text-slate-500">' + (baseRate||0).toFixed(1) + '%</span>';
      }
      cells[2].innerHTML = hitHtml;
      cells[3].innerHTML = d.median_car == null
        ? '<span class="text-slate-300">-</span>'
        : '<span class="' + carClass(d.median_car) + '">' + pct(d.median_car) + '</span>';
      let meanHtml = d.mean_car == null
        ? '<span class="text-slate-300">-</span>'
        : '<span class="' + carClass(d.mean_car) + '">' + pct(d.mean_car) + '</span>';
      if (d.outlier_flag) {
        meanHtml += '<span class="ml-1 text-amber-600" title="Outlier flagged">&#9888;</span>';
      }
      cells[4].innerHTML = meanHtml;
      // B-107: abs rtn column (cells[5]); edge shifts to cells[6].
      cells[5].innerHTML = d.mean_abs_return == null
        ? '<span class="text-slate-300">-</span>'
        : '<span class="' + carClass(d.mean_abs_return) + '">' + pct(d.mean_abs_return) + '</span>';
      cells[6].innerHTML = d.edge == null
        ? '<span class="text-slate-300">-</span>'
        : '<span class="' + carClass(d.edge) + '">' + pct(d.edge) + '</span>';
      // Legacy 12-week sparkline column removed (superseded by the Phase 3
      // Trajectory cohort sparkline). The Trajectory + 3m-trend cells are
      // rendered server-side from cohort_performance.json and intentionally
      // NOT rebuilt on horizon change, so no cells[] index past edge (5) is
      // touched here.
    });
  }

  function rebuildDiag(){
    // Phase 7 (B-071): when the legacy CAR chart is flagged off the #diagChart
    // canvas is absent. Bail early so new Chart(null) never throws and the
    // cohort view is unaffected.
    if (!document.getElementById('diagChart')) return;
    // B-058: source dataset is toggled by `state.allTime`.
    const h = currentHorizons()[state.horizon] || {};
    const sigs = h.signals || {};
    const labels = ['M-12','M-11','M-10','M-9','M-8','M-7','M-6','M-5','M-4','M-3','M-2','M-1','Now'];
    function pad13(arr){
      const a = (arr||[]).slice();
      while (a.length < 13) a.unshift(null);
      return a.slice(-13);
    }
    const datasets = SIDS.map(function(sid){
      const d = sigs[sid] || {};
      return {
        label: sid.toUpperCase(),
        data: pad13(d.sparkline),
        borderColor: tierHex[sid] || '#94a3b8',
        borderWidth: 2,
        tension: 0.3,
        pointRadius: 0,
        spanGaps: true
      };
    });
    datasets.push({
      label: 'FTSE A-S',
      data: new Array(13).fill(0),
      borderColor: data.diag_color_ftas || '#888780',
      borderWidth: 1,
      borderDash: [6,4],
      tension: 0,
      pointRadius: 0
    });
    const ctx = document.getElementById('diagChart');
    if (window.__diagChart) window.__diagChart.destroy();
    window.__diagChart = new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 10 }, color: '#94a3b8' } },
          y: { grid: { color: '#f1f5f9' }, ticks: { font: { size: 10 }, color: '#64748b',
               callback: function(v){ return v.toFixed(1) + '%'; } } }
        },
        plugins: {
          legend: { display: false },
          tooltip: { mode: 'index', intersect: false,
            callbacks: { label: function(c){ return c.dataset.label + ': '
              + (c.parsed.y == null ? '-' : c.parsed.y.toFixed(2) + '%'); } } }
        },
        interaction: { mode: 'index', intersect: false }
      }
    });
    // B-019: apply any previously-saved hide / solo state from localStorage,
    // then render the interactive legend (chips become click-to-toggle,
    // dblclick-to-solo buttons). Order matters: apply visibility first so
    // the chips that render reflect the actual chart state.
    applyDiagVisibility(window.__diagChart, loadHiddenSids());
    renderDiagLegend(window.__diagChart);
    const t = document.getElementById('diagTitle');
    if (t) t.textContent = 'Cumulative net CAR @ '
      + (data.horizon_labels[state.horizon] || state.horizon) + ' - trailing 12 months';
  }

  function buildCohortValue(){
    const vals = window.__cohortValue || [];
    const ctx = document.getElementById('cohortValue');
    if (!ctx) return;
    if (window.__cohortChart) window.__cohortChart.destroy();
    window.__cohortChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['GBP 1-25k','GBP 25-100k','GBP 100-500k','GBP 500k+'],
        datasets: [{
          data: vals.map(function(v){ return v == null ? 0 : v; }),
          backgroundColor: vals.map(function(v){
            if (v == null) return '#e2e8f0';
            return v >= 0 ? '#10b981' : '#ef4444';
          })
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 10 } } },
          y: { ticks: { font: { size: 10 }, callback: function(v){ return v.toFixed(1)+'%'; } } }
        },
        plugins: { legend: { display: false } }
      }
    });
  }

  // B-058 (2026-05-22): all-time toggle. Rerenders the scoreboard +
  // diagnostics chart against the alternate aggregates dataset when
  // checked. Does NOT affect the cohort tiles (they have their own
  // lookback dropdowns) or the horizon-aggregates-driven base rate
  // beyond the scoreboard view.
  const allTimeToggle = document.getElementById('scoreboardAllTime');
  if (allTimeToggle) {
    allTimeToggle.addEventListener('change', function(){
      state.allTime = !!allTimeToggle.checked;
      rebuildScoreboard();
      rebuildDiag();
    });
  }
  if (select) {
    select.addEventListener('change', function(){
      state.horizon = select.value;
      rebuildScoreboard();
      rebuildDiag();
      document.dispatchEvent(new CustomEvent('horizonChange', { detail: { horizon: state.horizon } }));
    });
  }
  document.addEventListener('DOMContentLoaded', function(){
    rebuildDiag();
    buildCohortValue();
  });
  if (document.readyState !== 'loading') {
    rebuildDiag();
    buildCohortValue();
  }
})();
</script>
"""


def render_to_file(signals_path: Path, status_path: Path | None,
                   out_path: Path, build_sha: str = 'local',
                   size_band: str | None = None) -> int:
    """Build one performance HTML page.

    ``size_band`` — Sprint 56 Phase C:
      * ``"small"``  → page title / subtitle scoped to small-cap (< £500m).
      * ``"large"``  → page title / subtitle scoped to large-cap (≥ £500m).
      * ``None``     → existing combined behaviour (performance.html / "All").
    """
    signals_data = json.loads(Path(signals_path).read_text(encoding='utf-8'))
    status_overrides = {}
    if status_path is not None and Path(status_path).exists():
        try:
            ss = json.loads(Path(status_path).read_text(encoding='utf-8'))
            for sid in ss.get('deprecated') or []:
                status_overrides[sid] = 'deprecated'
        except Exception:
            status_overrides = {}
    # Sprint 14 Phase 3 (B-067): load the Level-1 cohort blob (sparkline +
    # trend per group). Sits next to signals.json in dashboard/data/. If
    # absent (older build), the table renders em-dash placeholders for the
    # two new columns -- the page still builds.
    cohort_data = None
    # Sprint 56 Phase A fix: use band-specific cohort_performance when building
    # a band page so the signal-overview mini-charts show only that band's data.
    _base_dir = Path(signals_path).parent
    if size_band in ("small", "large"):
        _band_cohort = _base_dir / f'cohort_performance_{size_band}.json'
        cohort_path = _band_cohort if _band_cohort.exists() else _base_dir / 'cohort_performance.json'
    else:
        cohort_path = _base_dir / 'cohort_performance.json'
    if cohort_path.exists():
        try:
            cohort_data = json.loads(cohort_path.read_text(encoding='utf-8'))
        except Exception:
            cohort_data = None
    html_text = render(signals_data, status_overrides, build_sha=build_sha,
                       cohort_data=cohort_data, size_band=size_band)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + '.tmp')
    tmp.write_text(html_text, encoding='utf-8')
    import os as _os
    _os.replace(tmp, out_path)
    return len(html_text.encode('utf-8'))
