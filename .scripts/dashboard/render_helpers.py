"""Shared HTML/SVG helpers for the Stage 5 dashboard renderers.

Stdlib-only. All helpers return strings (HTML / SVG fragments). They are
imported by templates.py, render_index.py, render_performance.py, and
render_company.py to keep palette / formatting decisions in one place.

Locked palette (final spec stage-05-design-final.md s 1.4):
    T0 bg-red-600 (highest conviction, red-orange)
    T1 bg-red-500
    T2 bg-amber-500
    T3 bg-emerald-500
    T4 bg-slate-400
    S1 bg-blue-500
    F1 bg-purple-500
    Positive CAR text-emerald-600; Negative text-rose-600; Neutral text-slate-500.
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timezone

# B-010: %-d strips the leading zero on Linux/macOS; %#d is the Windows
# equivalent. Detect once at import so the date helper just picks the
# right token.
_DAY_FMT = "%#d" if sys.platform == "win32" else "%-d"

# Signal-tier palette. Tailwind classes (uniform: bg-* text-white) + hex
# for sparkline strokes which must inline the colour.
#
# B-025 Phase B (2026-05-20): added per-bucket tiers (t1a, t1b, t5, t6,
# t7) and removed the legacy combined t1. Colour family is preserved
# within each role category:
#   - red family: T0 (cluster combo) and T1a/T1b (top-of-pyramid)
#   - violet: T7 (chair — board-level, distinct from execs)
#   - amber: T2 (other exec)
#   - emerald: T3 (NED)
#   - slate: T4 (catch-all), T6 (company sec)
#   - rose/orange: T5 (PCA — distinct warm tone)
TIER_PALETTE = {
    "t0":  {"bg": "bg-red-600",     "fg": "text-white", "hex": "#dc2626"},
    "t1a": {"bg": "bg-red-500",     "fg": "text-white", "hex": "#ef4444"},
    "t1b": {"bg": "bg-rose-500",    "fg": "text-white", "hex": "#f43f5e"},
    "t7":  {"bg": "bg-violet-500",  "fg": "text-white", "hex": "#8b5cf6"},
    "t2":  {"bg": "bg-amber-500",   "fg": "text-white", "hex": "#f59e0b"},
    "t3":  {"bg": "bg-emerald-500", "fg": "text-white", "hex": "#10b981"},
    "t5":  {"bg": "bg-orange-400",  "fg": "text-white", "hex": "#fb923c"},
    "t6":  {"bg": "bg-slate-300",   "fg": "text-white", "hex": "#cbd5e1"},
    "t4":  {"bg": "bg-slate-400",   "fg": "text-white", "hex": "#94a3b8"},
    "s1":  {"bg": "bg-blue-500",    "fg": "text-white", "hex": "#3b82f6"},
    "f1":  {"bg": "bg-purple-500",  "fg": "text-white", "hex": "#a855f7"},
    "b1":  {"bg": "bg-cyan-500",    "fg": "text-white", "hex": "#06b6d4"},
    "b2":  {"bg": "bg-red-600",     "fg": "text-white", "hex": "#dc2626"},
}

# Tooltip table — refreshed for B-025 Phase B.
TIER_TOOLTIPS = {
    "t0":  ("T0 - Cluster + opportunistic combo. High-conviction buy "
            "(T1a/T1b/T7) inside a multi-director cluster (S1) within "
            "30 days. Highest conviction."),
    "t1a": ("T1a - CEO / Founder buy >= GBP100k. Top-of-pyramid "
            "decision-maker conviction signal."),
    "t1b": ("T1b - CFO buy >= GBP100k. Financial-knowledge insider "
            "signal; often precedes earnings beats."),
    "t7":  ("T7 - Chair (executive or non-executive) buy >= GBP25k. "
            "Board-level conviction; largest tier by aggregate "
            "value."),
    "t2":  ("T2 - Other senior exec buy (Other Chief, Exec Director, "
            "Divisional Exec, President/VP) >= GBP25k. Mid-conviction."),
    "t3":  ("T3 - NED (non-executive director, incl. SID + Supervisory "
            "Board) buy >= GBP10k. Lower-conviction."),
    "t5":  ("T5 - PCA (Person Closely Associated — spouse / family "
            "trust / connected party) buy >= GBP10k. Indirect "
            "insider signal."),
    "t6":  ("T6 - Company Secretary / General Counsel buy >= GBP10k. "
            "Institutional role; weak signal."),
    "t4":  "T4 - Other discretionary buy >= GBP1k. Catch-all.",
    "s1":  ("S1 - Cluster. >=2 distinct directors buying same ticker, "
            "dates within 30 days."),
    "f1":  "F1 - First-time buy. Director's first-ever buy of this ticker.",
    "b1":  ("B1 - Lone Conviction Buy. Single director, value >= GBP200k, "
            "no other buyers within 30 days, stock not in moderate weakness."),
    "b2":  ("B2 - Crowded Cluster Kill. >=4 distinct directors buy the same "
            "ticker within 30 days. All other signals suppressed for 60 days "
            "on this ticker — cluster is too crowded to be a clean conviction trade."),
}

# Severity rank (lower = stronger). Used for sort order on today's table
# and for orchestrator tier dedup (matches signals.__init__.TIER_RANK).
TIER_SEVERITY = {
    "t0":  0,
    "t1a": 1, "t1b": 2,
    "t7":  3,
    "t2":  4,
    "t3":  5,
    "t5":  6, "t6": 7,
    "t4":  8,
    "s1":  9, "f1": 10, "b1": 11, "b2": 12,
}

# Map long signal_id (DB / backtest CSV) -> short JSON key.
# B-025 Phase B: t1_ceo_cfo_buy split into t1a + t1b; new entries for
# t5/t6/t7. Legacy "t1_ceo_cfo_buy" intentionally REMOVED — anything
# still emitting that signal_id is stale and should be re-eval'd.
SIGNAL_LONG_TO_SHORT = {
    "t0_cluster_combo":     "t0",
    "t1a_ceo_founder_buy":  "t1a",
    "t1b_cfo_buy":          "t1b",
    "t7_chair_buy":         "t7",
    "t2_exec_buy":          "t2",
    "t3_ned_buy":           "t3",
    "t5_pca_buy":           "t5",
    "t6_company_sec_buy":   "t6",
    "t4_other_buy":         "t4",
    "s1_cluster_buy":          "s1",
    "f1_first_time_buy":       "f1",
    "b1_lone_conviction_buy":      "b1",
    "b2_crowded_cluster_kill":     "b2",
}
SIGNAL_SHORT_TO_LONG = {v: k for k, v in SIGNAL_LONG_TO_SHORT.items()}

# Fixed display order on the scoreboard / diagnostics chart.
# Order matches tier-rank conviction: cluster combo first, then per-
# bucket tier signals from highest conviction down, then derived
# signals (cluster, first-time).
SIGNAL_DISPLAY_ORDER = [
    "t0", "t1a", "t1b", "t7", "t2", "t3", "t5", "t6", "t4", "s1", "f1", "b1", "b2",
]


# ---------------------------------------------------------------------------
# Horizon / lookback constants (single source of truth — Sprint 7 fix #4)
#
# Three modules used to maintain their own copies of these lists:
#   render_performance.py     (HORIZON_LABELS + LOOKBACK_LABELS)
#   render_performance_drilldown.py (same)
#   export_dashboard_json.py  (LOOKBACKS = [(key, days), ...])
# Drift between them silently breaks dropdowns. Consolidating here so
# adding a 6th lookback option (or renaming a horizon) is a one-line
# change in one file.
# ---------------------------------------------------------------------------

HORIZON_LABELS = {
    "t1":   "T+1 (next session)",
    "t30":  "T+30 (~1 month)",
    "t90":  "T+90 (~3 months)",
    "t180": "T+180 (~6 months)",
    "t365": "T+365 (~1 year)",
}

# Canonical order — shortest to longest. Render-side dropdowns and
# export-side aggregates must agree on this order.
LOOKBACK_KEYS = ["30d", "90d", "6m", "1y", "all"]

LOOKBACK_DISPLAY = {
    "30d": "30 d",
    "90d": "90 d",
    "6m":  "6 m",
    "1y":  "1 y",
    "all": "all",
}

# Day-count thresholds for the within-lookback filter in
# export_dashboard_json.py:LOOKBACKS. None = no lower bound.
LOOKBACK_DAYS = {
    "30d": 30,
    "90d": 90,
    "6m":  183,
    "1y":  365,
    "all": None,
}

# Pre-derived (key, display) pairs for direct use in dropdown templates.
LOOKBACK_LABELS = [(k, LOOKBACK_DISPLAY[k]) for k in LOOKBACK_KEYS]
# Pre-derived (key, days) pairs for export_dashboard_json's LOOKBACKS.
LOOKBACKS = [(k, LOOKBACK_DAYS[k]) for k in LOOKBACK_KEYS]

# Chart.js stroke colours per signal (uses tier hex for consistency).
DIAG_COLORS = {sid: TIER_PALETTE[sid]["hex"] for sid in SIGNAL_DISPLAY_ORDER}
DIAG_COLORS["ftas"] = "#888780"  # FTSE All-Share benchmark, dashed grey.


def esc(s) -> str:
    """HTML-escape a value, coercing None to empty string."""
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def render_badge(sid: str, extra_class: str = "") -> str:
    """Return a signal-tier badge span with native tooltip.

    `sid` should be the short code (t0/t1/.../f1). Long-form ids are
    accepted defensively via SIGNAL_LONG_TO_SHORT.
    """
    short = SIGNAL_LONG_TO_SHORT.get(sid, sid).lower()
    if short not in TIER_PALETTE:
        return ('<span class="inline-flex items-center px-1.5 py-0.5 '
                'rounded text-[10px] font-semibold bg-slate-300 text-white">'
                f'{esc(sid)}</span>')
    pal = TIER_PALETTE[short]
    tip = TIER_TOOLTIPS[short]
    label = short.upper()
    cls = (f'cursor-help inline-flex items-center px-1.5 py-0.5 rounded '
           f'text-[10px] font-semibold {pal["bg"]} {pal["fg"]} {extra_class}').strip()
    return (f'<span title="{esc(tip)}" class="{cls}">{label}</span>')


def render_badges_row(sids, sort: bool = True) -> str:
    """Render a row of badges with gap-1, sorted by severity by default."""
    if not sids:
        return '<span class="text-slate-300">-</span>'
    keys = list(sids)
    if sort:
        keys = sorted(keys, key=lambda s: TIER_SEVERITY.get(
            SIGNAL_LONG_TO_SHORT.get(s, s).lower(), 99))
    parts = [render_badge(s) for s in keys]
    return '<span class="inline-flex flex-wrap gap-1">' + "".join(parts) + '</span>'


def pct(value, dp: int = 2, plus_sign: bool = True) -> str:
    """Format a number as percent. `value` is already a percent (e.g. 12.5)."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if plus_sign and v > 0 else ""
    return f"{sign}{v:.{dp}f}%"


def car_color_class(value) -> str:
    """Return Tailwind text colour class for a percent CAR value."""
    if value is None:
        return "text-slate-400"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "text-slate-400"
    if v > 0.05:
        return "text-emerald-600"
    if v < -0.05:
        return "text-rose-600"
    return "text-slate-500"


def car_cell(value, with_glyph: bool = False) -> str:
    """Return a coloured <span> for a CAR percent value."""
    if value is None:
        return '<span class="text-slate-300">-</span>'
    cls = car_color_class(value)
    glyph = ""
    if with_glyph:
        try:
            v = float(value)
            glyph = "&#9650; " if v > 0 else ("&#9660; " if v < 0 else "")
        except Exception:
            glyph = ""
    return f'<span class="{cls}">{glyph}{esc(pct(value))}</span>'


def status_pill_for_cohort(hit_pct, base_rate, cohort_type: str) -> str:
    """Spec §2.2 — drill-down page status pill (locked ±50% rule).

    Renders a green pill when `hit_pct >= base_rate * 1.5`, a red pill when
    `hit_pct <= base_rate * 0.5`, and **nothing** otherwise (no middle pill,
    per Rupert's locked decision to prevent pill-clutter on unremarkable
    cohorts).

    Copy varies by cohort type (`bucket`/`role`/`sector`).
    """
    if hit_pct is None or base_rate is None:
        return ""
    try:
        hp = float(hit_pct)
        br = float(base_rate)
    except (TypeError, ValueError):
        return ""
    if br <= 0:
        return ""
    cohort_type = (cohort_type or "").lower()
    if hp >= br * 1.5:
        copy = {
            "bucket": "Top bucket",
            "role":   "Strong role cohort",
            "sector": "Top sector this period",
        }.get(cohort_type, "Strong cohort")
        return (
            '<span class="inline-flex items-center px-2 py-0.5 '
            'rounded-full text-[10px] font-medium bg-emerald-100 '
            'text-emerald-700">'
            '<span class="inline-block w-1.5 h-1.5 rounded-full '
            'bg-emerald-500 mr-1"></span>'
            f'{esc(copy)}</span>'
        )
    if hp <= br * 0.5:
        copy = {
            "bucket": "Underperforming bucket",
            "role":   "Underperforming role cohort",
            "sector": "Bottom sector this period",
        }.get(cohort_type, "Weak cohort")
        return (
            '<span class="inline-flex items-center px-2 py-0.5 '
            'rounded-full text-[10px] font-medium bg-rose-100 '
            'text-rose-700">'
            '<span class="inline-block w-1.5 h-1.5 rounded-full '
            'bg-rose-500 mr-1"></span>'
            f'{esc(copy)}</span>'
        )
    return ""


def firing_row_html(firing: dict, panel_color: str,
                    company_page_exists: bool = True) -> str:
    """Render one §5.3 firing row for the Top/Bottom firings panels.

    `firing` is the dict from `top_firings` / `bottom_firings` in the drill
    payload. `panel_color` controls hover-row colour: 'emerald' for the
    Top panel, 'rose' for the Bottom panel. `company_page_exists` — when
    False, the row renders italic-faded with a tooltip (spec §8 smoke test
    requires every clickable ticker to resolve to an existing company page).
    """
    ticker = firing.get("ticker") or ""
    company = firing.get("company") or ""
    director = firing.get("director") or ""
    role_class = firing.get("role_class") or ""
    signal_tier = firing.get("signal_tier") or ""
    value_gbp = firing.get("value_gbp")
    car = firing.get("car")
    date = firing.get("date") or ""

    # Tier badge — fall back to plain text if unknown tier.
    if signal_tier:
        badge_html = render_badge(signal_tier)
    else:
        badge_html = '<span class="text-slate-400">—</span>'

    # Director display: shorten if >18 chars (first-initial + surname).
    director_display = director
    if len(director) > 18:
        parts = director.split()
        if len(parts) >= 2:
            director_display = f"{parts[0][0]}. {parts[-1]}"

    # Value GBP — abbreviated form (£312k, £1.5m).
    if value_gbp is None:
        value_html = '<span class="text-slate-300">—</span>'
    else:
        try:
            v = float(value_gbp)
            if abs(v) >= 1_000_000:
                value_html = f"&pound;{v / 1_000_000:.1f}m"
            elif abs(v) >= 1_000:
                value_html = f"&pound;{int(round(v / 1_000))}k"
            else:
                value_html = f"&pound;{int(round(v))}"
        except (TypeError, ValueError):
            value_html = '<span class="text-slate-300">—</span>'

    car_html = car_cell(car)
    abs_return = firing.get("abs_return")
    abs_html = car_cell(abs_return)
    # B113: benchmark return at same horizon.
    bench_html = car_cell(firing.get("bench_return"))
    hover_bg = "hover:bg-emerald-50" if panel_color == "emerald" else "hover:bg-rose-50"

    if company_page_exists and ticker:
        href = f"companies/{ticker}.html"
        return (
            '<tr class="clickable group border-t border-slate-100 '
            f'cursor-pointer {hover_bg} relative" '
            f'data-href="{esc(href)}" tabindex="0" role="link" '
            f'aria-label="View {esc(ticker)} company page">'
            f'<td class="px-2 py-1.5 text-slate-700">{esc(date)}</td>'
            f'<td class="px-2 py-1.5 font-mono text-slate-700">{esc(ticker)}</td>'
            f'<td class="px-2 py-1.5 text-slate-700" title="{esc(director)}">'
            f'{esc(director_display)}</td>'
            f'<td class="px-2 py-1.5">{badge_html}</td>'
            f'<td class="px-2 py-1.5 text-right tabular-nums">{value_html}</td>'
            f'<td class="px-2 py-1.5 text-right tabular-nums relative">'
            f'{car_html}<span class="chev absolute right-2 top-1/2 '
            '-translate-y-1/2 opacity-0 group-hover:opacity-60 text-slate-400">'
            f'&rsaquo;</span></td>'
            f'<td class="px-2 py-1.5 text-right tabular-nums">{abs_html}</td>'
            f'<td class="px-2 py-1.5 text-right tabular-nums">{bench_html}</td>'
            '</tr>'
        )
    # Company page missing — render italic-faded, non-clickable.
    return (
        '<tr class="border-t border-slate-100 text-slate-400 italic" '
        'title="Company page not generated for this ticker">'
        f'<td class="px-2 py-1.5">{esc(date)}</td>'
        f'<td class="px-2 py-1.5 font-mono">{esc(ticker)}</td>'
        f'<td class="px-2 py-1.5">{esc(director_display)}</td>'
        f'<td class="px-2 py-1.5">{badge_html}</td>'
        f'<td class="px-2 py-1.5 text-right tabular-nums">{value_html}</td>'
        f'<td class="px-2 py-1.5 text-right tabular-nums">{car_html}</td>'
        f'<td class="px-2 py-1.5 text-right tabular-nums">{abs_html}</td>'
        f'<td class="px-2 py-1.5 text-right tabular-nums">{bench_html}</td>'
        '</tr>'
    )


def n_band_cell(n) -> str:
    """Render an N-cell with the spec §1.2 N-band visual rules:

      * N=0 or None → gray italic em-dash (preliminary / no data)
      * N<20        → number + amber ⚠ glyph + tooltip
      * N≥20        → normal styling

    Used across the three cohort tiles on performance.html so the N-band
    rendering stays consistent. The amber ⚠ glyph mirrors the existing
    per-signal scoreboard convention.
    """
    if n is None or n == 0:
        return '<span class="text-slate-300 italic">—</span>'
    try:
        n_int = int(n)
    except (TypeError, ValueError):
        return '<span class="text-slate-300 italic">—</span>'
    if n_int < 20:
        return (
            f'{n_int} '
            '<span class="text-amber-600" '
            'title="N&lt;20 — preliminary, wait for more firings">&#9888;</span>'
        )
    return str(n_int)


def divergence_warning(mean_car, median_car):
    """Phase 0 interim reliability check (cohort-chart sprint, Phase 0).

    Detects when a signal's mean CAR is being dragged away from its median
    by a single-outlier trade (the canonical case: T3 NED shows mean +7.9%
    at T+90 but median -5.0%, driven by one TIN trade at +1218.9%).

    Trigger rule (applied at the T+90 horizon by the caller):

        fire if  |mean - median| > 5pp           (absolute divergence)
            AND  |mean - median| > 0.5 * |mean|   (relative divergence)

    Both conditions in conjunction. The first catches absolute divergence;
    the second avoids flagging tiny means (e.g. mean +0.5%, median 0% — a
    0.5pp gap we should ignore). A flat-and-bad signal (e.g. F1 mean -1.15%
    with a tiny mean-median gap and large N) does NOT fire: that is a real
    edge problem, not outlier contamination.

    Returns a (fired: bool, gap_pp: float) tuple. `gap_pp` is |mean-median|
    in percentage points. When either input is missing, returns
    (False, 0.0).
    """
    if mean_car is None or median_car is None:
        return (False, 0.0)
    try:
        mean = float(mean_car)
        median = float(median_car)
    except (TypeError, ValueError):
        return (False, 0.0)
    gap_pp = abs(mean - median)
    fired = (gap_pp > 5.0) and (gap_pp > 0.5 * abs(mean))
    return (fired, gap_pp)


def divergence_badge(gap_pp) -> str:
    """Render the amber Phase 0 divergence warning badge for the Mean CAR
    cell. Uses the existing amber convention + HTML-entity warning glyph
    (no raw non-ASCII char, per the cp1252-subprocess rule). The caller is
    responsible for only emitting this when the trigger has fired.
    """
    try:
        g = float(gap_pp)
    except (TypeError, ValueError):
        g = 0.0
    tip = (f"Mean diverges from median by {g:.1f}pp - likely single-outlier "
           "contamination. Drill into the cohort before acting.")
    return (f'<span class="ml-1 text-amber-600" title="{esc(tip)}">'
            '&#9888;</span>')


def status_pill(status: str) -> str:
    """Return a status pill span for live / review / kill? / gated / deprecated."""
    status = (status or "review").lower()
    mapping = {
        "live":       ("bg-emerald-100 text-emerald-700", "text-emerald-500", "live"),
        "review":     ("bg-amber-100 text-amber-700",     "text-amber-500",   "review"),
        "kill?":      ("bg-rose-100 text-rose-700",       "text-rose-500",    "kill?"),
        "kill":       ("bg-rose-100 text-rose-700",       "text-rose-500",    "kill?"),
        "gated":      ("bg-slate-100 text-slate-500",     "text-slate-400",   "gated"),
        "deprecated": ("bg-slate-100 text-slate-500",     "text-slate-400",   "deprecated"),
    }
    bg, dot, label = mapping.get(status, mapping["review"])
    return (f'<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full '
            f'text-[10px] font-medium {bg}"><span class="{dot}">&#9679;</span>'
            f'{esc(label)}</span>')


# ---------------------------------------------------------------------------
# B-171 — Conviction Score band badge (single source of truth).
#
# Spec §4 strength bands: 0-40 Low / 40-60 Moderate / 60-80 High /
# 80-100 Exceptional. The score itself does the honest work (spec §6): a Low
# band is shown plainly, never dressed up — so the palette deliberately greys
# Low and only warms toward emerald for High/Exceptional.
# ---------------------------------------------------------------------------
_CONVICTION_BAND_CLASS = {
    "Low":         "bg-slate-100 text-slate-500",
    "Moderate":    "bg-amber-100 text-amber-700",
    "High":        "bg-emerald-100 text-emerald-700",
    "Exceptional": "bg-emerald-600 text-white",
}


def conviction_band_badge(band: str, score=None) -> str:
    """Render the Conviction Score strength-band badge (spec §4 / §6).

    `band` is one of Low / Moderate / High / Exceptional. When `score` is
    given (0-100) it is shown alongside the band label so the reader always
    sees the raw number — a Low-band buy is visibly weak, never dressed up.
    Unknown bands fall back to a neutral slate pill.
    """
    label = (band or "").strip() or "Low"
    cls = _CONVICTION_BAND_CLASS.get(label, "bg-slate-100 text-slate-500")
    if score is None:
        inner = esc(label)
    else:
        try:
            inner = f"{esc(label)} &middot; {float(score):.0f}"
        except (TypeError, ValueError):
            inner = esc(label)
    return (f'<span class="inline-flex items-center px-2 py-0.5 rounded-full '
            f'text-[10px] font-semibold {cls}">{inner}</span>')


def conviction_factor_bar(label: str, value, unknown: bool = False) -> str:
    """Render one labelled 0.0-1.0 sub-score as a thin bar (spec §6 breakdown).

    `value` is a 0.0-1.0 strength (f6 multiplier callers should pre-scale).
    When `unknown` is True (the factor had no underlying data — e.g. missing
    market cap / earnings date), the bar is rendered empty with an "unknown"
    label rather than a misleading 0%.
    """
    if unknown or value is None:
        return (
            '<div class="flex items-center gap-2 text-[10px]">'
            f'<span class="w-24 shrink-0 text-slate-500">{esc(label)}</span>'
            '<span class="flex-1 h-1.5 rounded bg-slate-100"></span>'
            '<span class="w-12 text-right text-slate-400 italic">unknown</span>'
            '</div>'
        )
    try:
        v = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        v = 0.0
    pct_w = f"{v * 100:.0f}%"
    return (
        '<div class="flex items-center gap-2 text-[10px]">'
        f'<span class="w-24 shrink-0 text-slate-500">{esc(label)}</span>'
        '<span class="flex-1 h-1.5 rounded bg-slate-100 overflow-hidden">'
        f'<span class="block h-full bg-indigo-400" style="width:{pct_w}"></span>'
        '</span>'
        f'<span class="w-12 text-right tabular-nums text-slate-600">{pct_w}</span>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Sprint 14 Phase 3 (B-067): Level-1 cohort-trajectory sparkline + 3m trend.
#
# These power the two new columns on the performance scoreboard. They read
# the per-group `sparkline_points` and `trend_3m_vs_prior3m_t30` fields from
# dashboard/data/cohort_performance.json (Phase 2). Spec:
# docs/specs/cohort-performance-chart-level1-design.md sections 1 + 2.
#
# Both emit PURE ASCII (HTML numeric entities for glyphs) so they are safe
# under the project's subprocess-print cp1252 constraint.
# ---------------------------------------------------------------------------

def cohort_sparkline_svg(points, color_hex: str, max_months: int = 12) -> str:
    """Inline ~120x30 SVG trajectory sparkline (spec section 1).

    `points`: ordered list of {"month_iso": str, "mean_car_t30": float|None}
    from inception to latest. `null` mean_car_t30 marks an empty month (a
    gap) -- the path BREAKS at gaps (no interpolation, never invents trend).

    B-009: capped to the most recent `max_months` (default 12). Gap months
    must carry explicit None (never 0.0 as a forward-fill substitute -- 0.0
    would make a flat line across a data gap, which misrepresents the series).

    Returns an em-dash placeholder if fewer than 2 real (non-null) points.
    """
    pts = list(points or [])[-max_months:]
    vals = [p.get("mean_car_t30") for p in pts
            if isinstance(p, dict) and p.get("mean_car_t30") is not None]
    if len(vals) < 2:
        return '<span class="text-slate-300">&mdash;</span>'

    W, H, PAD_X, PAD_Y = 120, 30, 3, 3
    vmin, vmax = min(vals), max(vals)
    pad = (vmax - vmin) * 0.08 or 0.01           # small symmetric breathing room
    lo, hi = vmin - pad, vmax + pad
    span = (hi - lo) or 1.0
    n = len(pts)

    def px(i):
        return PAD_X + (i / (n - 1)) * (W - 2 * PAD_X) if n > 1 else W / 2

    def py(v):
        return PAD_Y + (hi - v) / span * (H - 2 * PAD_Y)

    # Split into runs of consecutive non-null points (break path on each null).
    runs, cur = [], []
    for i, p in enumerate(pts):
        v = p.get("mean_car_t30") if isinstance(p, dict) else None
        if v is None:
            if cur:
                runs.append(cur)
                cur = []
        else:
            cur.append((px(i), py(v)))
    if cur:
        runs.append(cur)

    parts = []

    # Faint dashed zero baseline -- only if 0 falls inside the data band.
    if lo <= 0.0 <= hi:
        zy = py(0.0)
        parts.append(
            f'<line x1="{PAD_X}" y1="{zy:.1f}" x2="{W - PAD_X}" y2="{zy:.1f}" '
            f'stroke="#e2e8f0" stroke-width="0.5" stroke-dasharray="2 2"/>'
        )

    # One polyline per run; single-point runs get a tiny dot so they're visible.
    for run in runs:
        if len(run) == 1:
            x, y = run[0]
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.2" '
                f'fill="{color_hex}"/>'
            )
        else:
            pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in run)
            parts.append(
                f'<polyline points="{pts_str}" fill="none" '
                f'stroke="{color_hex}" stroke-width="1.5" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )

    # Endpoint dots on the first and last NON-NULL points.
    first = runs[0][0]
    last = runs[-1][-1]
    parts.append(
        f'<circle cx="{first[0]:.1f}" cy="{first[1]:.1f}" r="2" '
        f'fill="{color_hex}"/>'
    )
    parts.append(
        f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="2" '
        f'fill="{color_hex}"/>'
    )

    inner = "".join(parts)
    return (
        f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'class="block" role="img" aria-label="trajectory sparkline" '
        f'preserveAspectRatio="none">{inner}</svg>'
    )


def cohort_trend_cell_inner(trend) -> str:
    """Inner span for the 3m-trend column (spec section 2).

    Rule on d = trend_3m_vs_prior3m_t30 (raw fraction, e.g. 0.024 = +2.4%):
        d > 0.01    -> green up-arrow (&#9650;) + signed pct
        d < -0.01   -> red down-arrow (&#9660;) + signed pct
        otherwise   -> grey flat (&#9644;) + signed pct
        None (young) -> grey flat + em-dash (no trend != zero trend)

    Returns just the <span>...</span> (caller wraps it in a <td>). Pure ASCII.
    """
    if trend is None:
        return (
            '<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 '
            'text-xs font-medium bg-slate-100 text-slate-400">'
            '<span aria-hidden="true">&#9644;</span>&mdash;</span>'
        )
    try:
        d = float(trend)
    except (TypeError, ValueError):
        return (
            '<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 '
            'text-xs font-medium bg-slate-100 text-slate-400">'
            '<span aria-hidden="true">&#9644;</span>&mdash;</span>'
        )
    delta = f"{d * 100:+.1f}%"
    if d > 0.01:
        return (
            '<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 '
            'text-xs font-semibold bg-emerald-50 text-emerald-600">'
            f'<span aria-hidden="true">&#9650;</span>{delta}</span>'
        )
    if d < -0.01:
        return (
            '<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 '
            'text-xs font-semibold bg-rose-50 text-rose-600">'
            f'<span aria-hidden="true">&#9660;</span>{delta}</span>'
        )
    return (
        '<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 '
        'text-xs font-medium bg-slate-100 text-slate-500">'
        f'<span aria-hidden="true">&#9644;</span>{delta}</span>'
    )


# B-025 Phase A: chip colour by canonical bucket (role_normalized).
# Falls back to substring matching on raw role when role_normalized is
# missing (e.g. very old cached JSON during rollout, or pre-backfill data).
_ROLE_CHIP_CLASS_BY_BUCKET = {
    # Highest-conviction execs — strongest indigo
    "CEO": "bg-indigo-100 text-indigo-700",
    "CFO": "bg-indigo-100 text-indigo-700",
    "Other Chief": "bg-indigo-50 text-indigo-600",
    "Founder": "bg-emerald-100 text-emerald-700",
    # Board / chair — violet family
    "Chair (executive)": "bg-violet-100 text-violet-700",
    "Non-Exec Chair": "bg-violet-50 text-violet-600",
    # NED — slate (neutral / informational)
    "NED": "bg-slate-100 text-slate-600",
    # Other exec roles — sky / cyan family (still positive but lower conviction)
    "Executive Director": "bg-sky-100 text-sky-700",
    "Divisional / Regional Exec": "bg-sky-50 text-sky-600",
    "President / VP": "bg-sky-50 text-sky-600",
    "Company Secretary / General Counsel": "bg-slate-50 text-slate-500",
    # PCA — amber (informational, watch but lower weight)
    "PCA": "bg-amber-50 text-amber-700",
    "PDMR-only": "bg-slate-50 text-slate-500",
    "Other / unclassified": "bg-slate-50 text-slate-400",
    "Parser fragment": "bg-rose-50 text-rose-500",
}

# Short labels for chips — the canonical bucket string can be long.
_ROLE_CHIP_LABEL_BY_BUCKET = {
    "CEO": "CEO",
    "CFO": "CFO",
    "Other Chief": "C-suite",
    "Founder": "Founder",
    "Chair (executive)": "Chair",
    "Non-Exec Chair": "NE Chair",
    "NED": "NED",
    "Executive Director": "Exec Dir",
    "Divisional / Regional Exec": "Div Exec",
    "President / VP": "VP",
    "Company Secretary / General Counsel": "Co Sec",
    "PCA": "PCA",
    "PDMR-only": "PDMR",
    "Other / unclassified": "Other",
    "Parser fragment": "?",
}


def role_chip(role: str, role_normalized: str | None = None) -> str:
    """Render a role chip with palette by canonical bucket.

    Args:
        role: The raw role string from the RNS form (shown as tooltip).
        role_normalized: The canonical bucket (B-025 Phase A). When
            provided, drives the chip colour and short label.
            When omitted, falls back to substring matching on `role`
            for backward compatibility with old cached payloads.
    """
    if not role and not role_normalized:
        return '<span class="text-slate-300">-</span>'

    bucket = role_normalized
    # Fallback when role_normalized isn't available — use the original
    # substring heuristic so old payloads still render.
    if not bucket and role:
        r = role.lower()
        if "ceo" in r or "cfo" in r or "chief" in r:
            cls = "bg-indigo-100 text-indigo-700"
        elif "chair" in r:
            cls = "bg-violet-100 text-violet-700"
        elif "ned" in r or "non-exec" in r or "non exec" in r:
            cls = "bg-slate-100 text-slate-600"
        else:
            cls = "bg-slate-100 text-slate-600"
        return (f'<span class="text-[10px] px-1.5 py-0.5 rounded {cls}">'
                f'{esc(role)}</span>')

    cls = _ROLE_CHIP_CLASS_BY_BUCKET.get(
        bucket, "bg-slate-100 text-slate-600",
    )
    short = _ROLE_CHIP_LABEL_BY_BUCKET.get(bucket, bucket or "?")
    # Use the raw role string as the tooltip (full job title) and the
    # bucket short-label as the visible text.
    tooltip = esc(role) if role else esc(bucket)
    return (f'<span class="text-[10px] px-1.5 py-0.5 rounded {cls}" '
            f'title="{tooltip}">{esc(short)}</span>')


def gbp(value, with_currency: bool = True) -> str:
    """Format a GBP value with thousands separators."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if v < 0 else ""
    s = f"{abs(v):,.0f}"
    return (("&pound;" if with_currency else "") + sign + s)


def fmt_mktcap(v) -> str:
    """Format market_cap_gbp float as £214.9m / £1.2bn / — for NULL.

    B-146: displayed in dealings table, upcoming events, and paper book.
    Thresholds: >=1bn -> 'bn', >=1m -> 'm', >=1k -> 'k', else em-dash.
    """
    if v is None:
        return "—"
    try:
        mc = float(v)
    except (TypeError, ValueError):
        return "—"
    if mc >= 1_000_000_000:
        return f"£{mc / 1_000_000_000:.1f}bn"
    if mc >= 1_000_000:
        return f"£{mc / 1_000_000:.1f}m"
    if mc >= 1_000:
        return f"£{mc / 1_000:.0f}k"
    return "—"


def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def generated_at_footer(generated_at_iso: str | None, build_sha: str = "local") -> str:
    """Bottom-right footer with generated timestamp + build sha."""
    label = generated_at_iso or now_utc_str()
    # Format the ISO timestamp if it looks like one.
    if isinstance(label, str) and "T" in label:
        try:
            dt = datetime.strptime(label.replace("Z", "+00:00"),
                                   "%Y-%m-%dT%H:%M:%S%z")
            label = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass
    return (f'<footer class="px-6 py-3 text-right text-[10px] text-slate-400">'
            f'Generated {esc(label)} &middot; build {esc(build_sha)}</footer>')


def parse_iso_date(s):
    """Parse an ISO date or return None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def days_since(date_str: str, today_str: str | None = None) -> int | None:
    """Return |today - date_str| in days, or None on parse error."""
    d = parse_iso_date(date_str)
    t = parse_iso_date(today_str) if today_str else datetime.now(timezone.utc).date()
    if not d:
        return None
    return (t - d).days


def signal_live_chip(date_str: str, today_str: str | None = None) -> str:
    """B-110: live / ageing / closed chip based on days since signal fired.

    Boundaries (locked roadmap 2026-06-05):
      live:   0-30 d  -> green
      ageing: 31-89 d -> amber
      closed: >= 90 d -> grey

    `date_str` is the ISO-prefixed fired_at / time_utc. Returns empty
    string when date_str cannot be parsed.
    """
    age = days_since(date_str, today_str)
    if age is None:
        return ""
    if age <= 30:
        label, cls = "live", "bg-emerald-100 text-emerald-700"
    elif age <= 89:
        label, cls = "ageing", "bg-amber-100 text-amber-700"
    else:
        label, cls = "closed", "bg-slate-100 text-slate-500"
    return (
        f'<span class="inline-block text-[9px] font-semibold px-1 py-0.5 '
        f'rounded uppercase tracking-wide {cls}">{label}</span>'
    )


def empty_state(message: str, py: int = 8) -> str:
    return (f'<div class="px-4 py-{py} text-center text-slate-400 text-xs">'
            f'{esc(message)}</div>')


def format_dashboard_date(date_str, today_iso: str) -> str:
    """B-010: render a date for transaction-table display.

    Returns "Today" if `date_str`'s YYYY-MM-DD prefix equals `today_iso`,
    else "%d %b" with no leading zero (e.g. "18 May"). `date_str` may be
    a YYYY-MM-DD date or a full ISO datetime; only the date prefix is
    used. Empty / None returns "-".

    This is the single source of truth for dating rows on the Today
    page, the company-page transactions table, and the cluster
    expand-out. Older rows (not today) show the bare day-month.
    """
    if not date_str:
        return "-"
    s = str(date_str).strip()
    if not s:
        return "-"
    iso_prefix = s[:10]
    if iso_prefix == today_iso:
        return "Today"
    try:
        dt = datetime.strptime(iso_prefix, "%Y-%m-%d")
    except ValueError:
        return s
    return dt.strftime(f"{_DAY_FMT} %b")
