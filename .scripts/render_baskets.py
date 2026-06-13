"""Render outputs/baskets.html -- Basket Report, small-cap conviction page.

Reads ``.data/baskets.json`` (written by export_baskets_json.py).
Writes ``outputs/baskets.html``.

Layout:
  - Same nav as other dashboard pages (Home / Performance / Baskets / Audit)
  - Title: "Basket Report -- Small-Cap Conviction"
  - Subtitle with benchmark caveat
  - 4 basket cards ranked by median net CAR at T+90 (highest first)
  - Footer caveat + last-updated timestamp

CLI:
    python .scripts/render_baskets.py [--out PATH] [--baskets-json PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dashboard import render_helpers as h  # noqa: E402
from dashboard import templates             # noqa: E402

BASKETS_JSON_PATH = ROOT / ".data" / "baskets.json"
OUT_PATH          = ROOT / "outputs" / "baskets.html"

# Nav links -- same on every page (Baskets added here, templates.py default
# updated separately for all pages).
NAV_LINKS = [
    ("Today",     "index.html"),
    ("Small Cap", "performance_small.html"),
    ("Large Cap", "performance_large.html"),
    ("All",       "performance.html"),
    ("Baskets",   "baskets.html"),
    ("Review",    "/review"),
]


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _car_big(value, label: str) -> str:
    """Large CAR display block for the basket card header stats."""
    if value is None:
        return (
            f'<div class="text-center px-4">'
            f'<div class="text-xs text-slate-500 mb-0.5">{h.esc(label)}</div>'
            f'<div class="text-2xl font-bold tabular-nums text-slate-300">-</div>'
            f'</div>'
        )
    cls = h.car_color_class(value)
    sign = "+" if value > 0 else ""
    return (
        f'<div class="text-center px-4">'
        f'<div class="text-xs text-slate-500 mb-0.5">{h.esc(label)}</div>'
        f'<div class="text-2xl font-bold tabular-nums {cls}">'
        f'{sign}{value:.2f}%</div>'
        f'</div>'
    )


def _pct_pos_cell(value, label: str) -> str:
    """% positive mini-stat cell."""
    if value is None:
        text = "-"
        cls = "text-slate-300"
    else:
        text = f"{value:.0f}%"
        cls = "text-emerald-600" if value >= 55 else ("text-rose-600" if value < 45 else "text-slate-700")
    return (
        f'<div class="text-center px-3">'
        f'<div class="text-xs text-slate-500 mb-0.5">{h.esc(label)}</div>'
        f'<div class="text-base font-semibold tabular-nums {cls}">{h.esc(text)}</div>'
        f'</div>'
    )


def _n_chip(n: int, proven: bool, early_data: bool) -> str:
    """n= chip with proven/early-data colouring."""
    if proven:
        cls = "bg-emerald-50 text-emerald-700 border border-emerald-200"
        label = f"n = {n}"
    else:
        cls = "bg-amber-50 text-amber-700 border border-amber-300"
        label = f"n = {n}"
    return (
        f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs '
        f'font-medium {cls}">{h.esc(label)}</span>'
    )


def _early_data_warning(n: int, threshold: int) -> str:
    """Caveat block shown when n < threshold."""
    return (
        f'<div class="mt-3 px-3 py-2 rounded bg-amber-50 border border-amber-200 '
        f'text-xs text-amber-800 flex items-start gap-2">'
        f'<span class="font-bold shrink-0">&#9888;</span>'
        f'<span>Insufficient data for statistical confidence '
        f'(n = {n}, need n &ge; {threshold}). '
        f'Stats shown for completeness only.</span>'
        f'</div>'
    )


def _format_mktcap(v) -> str:
    if v is None:
        return "-"
    try:
        mc = float(v)
    except (TypeError, ValueError):
        return "-"
    if mc >= 1_000_000_000:
        return f"&pound;{mc / 1_000_000_000:.1f}bn"
    if mc >= 1_000_000:
        return f"&pound;{mc / 1_000_000:.0f}m"
    return f"&pound;{mc / 1_000:.0f}k"


def _format_value(v) -> str:
    if v is None:
        return "-"
    try:
        vv = float(v)
    except (TypeError, ValueError):
        return "-"
    if vv >= 1_000_000:
        return f"&pound;{vv / 1_000_000:.1f}m"
    if vv >= 1_000:
        return f"&pound;{vv / 1_000:.0f}k"
    return f"&pound;{int(round(vv))}"


def _firings_table(firings: list[dict]) -> str:
    """Render the latest-10-firings mini table."""
    if not firings:
        return '<p class="text-xs text-slate-400 mt-2">No matured firings yet.</p>'

    rows_html = ""
    for f in firings:
        ticker  = h.esc(f.get("ticker") or "-")
        fired   = h.esc((f.get("fired_at") or "-")[:10])
        value   = _format_value(f.get("value_gbp"))
        car21   = h.car_cell(f.get("net_car_21"), with_glyph=True)
        car90   = h.car_cell(f.get("net_car_90"), with_glyph=True)
        mktcap  = _format_mktcap(f.get("market_cap_gbp"))
        ticker_link = (
            f'<a href="companies/{ticker}.html" '
            f'class="text-blue-600 hover:underline font-mono text-xs">'
            f'{ticker}</a>'
        )
        rows_html += (
            f'<tr class="border-t border-slate-100 hover:bg-slate-50">'
            f'<td class="py-1 px-2 text-xs text-slate-500">{fired}</td>'
            f'<td class="py-1 px-2">{ticker_link}</td>'
            f'<td class="py-1 px-2 text-xs text-right tabular-nums">{value}</td>'
            f'<td class="py-1 px-2 text-xs text-right tabular-nums">{car21}</td>'
            f'<td class="py-1 px-2 text-xs text-right tabular-nums">{car90}</td>'
            f'<td class="py-1 px-2 text-xs text-right tabular-nums text-slate-500">{mktcap}</td>'
            f'</tr>'
        )

    return (
        '<div class="mt-3 overflow-x-auto">'
        '<table class="w-full text-sm">'
        '<thead>'
        '<tr class="text-left text-[11px] text-slate-400 uppercase tracking-wide">'
        '<th class="py-1 px-2 font-medium">Date</th>'
        '<th class="py-1 px-2 font-medium">Ticker</th>'
        '<th class="py-1 px-2 font-medium text-right">Value</th>'
        '<th class="py-1 px-2 font-medium text-right">T+21 CAR</th>'
        '<th class="py-1 px-2 font-medium text-right">T+90 CAR</th>'
        '<th class="py-1 px-2 font-medium text-right">Mkt Cap</th>'
        '</tr>'
        '</thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table>'
        '</div>'
    )


def _basket_card(basket: dict, rank: int, proven_threshold: int) -> str:
    """Render one basket card."""
    label       = basket.get("label") or basket.get("id") or ""
    description = basket.get("description") or ""
    n           = basket.get("n") or 0
    proven      = basket.get("proven", False)
    early_data  = basket.get("early_data", False)
    car21       = basket.get("median_net_car_21")
    car90       = basket.get("median_net_car_90")
    pct_pos21   = basket.get("pct_positive_21")
    pct_pos90   = basket.get("pct_positive_90")
    firings     = basket.get("latest_firings") or []

    n_chip_html     = _n_chip(n, proven, early_data)
    rank_badge      = (
        f'<span class="text-[10px] font-medium text-slate-400 '
        f'bg-slate-100 rounded px-1.5 py-0.5">#{rank}</span>'
    )
    early_warn_html = _early_data_warning(n, proven_threshold) if not proven else ""

    # Stats row
    stats_html = (
        f'<div class="flex items-center gap-0 divide-x divide-slate-100 mt-3">'
        f'{_car_big(car21, "Median net CAR T+21")}'
        f'{_car_big(car90, "Median net CAR T+90")}'
        f'<div class="flex items-center gap-0 divide-x divide-slate-100">'
        f'{_pct_pos_cell(pct_pos21, "% pos T+21")}'
        f'{_pct_pos_cell(pct_pos90, "% pos T+90")}'
        f'</div>'
        f'</div>'
    )

    firings_html = _firings_table(firings)

    return (
        f'<div class="bg-white rounded-lg border border-slate-200 shadow-sm p-5 mb-4">'
        f'<div class="flex items-start justify-between gap-3">'
        f'<div class="min-w-0">'
        f'<div class="flex items-center gap-2 mb-0.5">'
        f'{rank_badge} {n_chip_html}'
        f'</div>'
        f'<h2 class="text-base font-semibold text-slate-900 mt-1">'
        f'{h.esc(label)}</h2>'
        f'<p class="text-xs text-slate-500 mt-0.5">{h.esc(description)}</p>'
        f'</div>'
        f'</div>'
        f'{stats_html}'
        f'{early_warn_html}'
        f'<details class="mt-3">'
        f'<summary class="text-xs text-slate-500 cursor-pointer hover:text-slate-700 '
        f'select-none">Latest {len(firings)} firing{"s" if len(firings) != 1 else ""} '
        f'&#9660;</summary>'
        f'{firings_html}'
        f'</details>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------

def render_baskets_page(baskets_data: dict, build_sha: str = "local") -> str:
    """Build the full baskets.html page from the baskets.json payload."""
    generated_at    = baskets_data.get("generated_at") or ""
    proven_threshold = baskets_data.get("proven_threshold") or 30
    baskets         = baskets_data.get("baskets") or []

    cards_html = ""
    for rank, basket in enumerate(baskets, start=1):
        cards_html += _basket_card(basket, rank, proven_threshold)

    if not cards_html:
        cards_html = '<p class="text-slate-400 py-8 text-center">No basket data available. Run export_baskets_json.py first.</p>'

    body = f"""
<div class="px-4 sm:px-6 py-6">

  <!-- Page header -->
  <div class="mb-6">
    <h2 class="text-xl font-bold text-slate-900">
      Basket Report &mdash; Small-Cap Conviction
    </h2>
    <p class="text-sm text-slate-500 mt-1">
      Pre-registered signal baskets, ranked by median net CAR at T+90
      vs FTSE All-Share / AIM benchmark. Small-cap = market cap &lt; &pound;500m.
    </p>
  </div>

  <!-- Basket cards -->
  <div class="max-w-4xl">
    {cards_html}
  </div>

  <!-- Footer caveat -->
  <div class="max-w-4xl mt-6 p-4 rounded bg-slate-50 border border-slate-200 text-xs text-slate-500">
    <strong class="text-slate-700">Methodology note:</strong>
    Performance is historical and net of 50 bps spread + 0.5% stamp duty on Main Market buys.
    Median used (not mean) to reduce outlier distortion. Baskets with n &lt; {proven_threshold}
    are marked with a warning badge and lack statistical significance.
    Not investment advice.
  </div>

</div>
"""

    return templates.base_page(
        title="Basket Report — Small-Cap Conviction",
        body=body,
        generated_at_iso=generated_at,
        build_sha=build_sha,
        include_chartjs=False,
        nav_links=NAV_LINKS,
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def render_to_file(
    baskets_json_path: Path = BASKETS_JSON_PATH,
    out_path: Path = OUT_PATH,
    build_sha: str = "local",
) -> int:
    """Read baskets.json and write baskets.html. Returns bytes written."""
    if not baskets_json_path.exists():
        print(f"[render_baskets] WARNING: {baskets_json_path} not found. "
              f"Writing placeholder page.")
        baskets_data = {
            "generated_at": "",
            "proven_threshold": 30,
            "baskets": [],
        }
    else:
        baskets_data = json.loads(baskets_json_path.read_text(encoding="utf-8"))

    html_text = render_baskets_page(baskets_data, build_sha=build_sha)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(html_text, encoding="utf-8")
    os.replace(tmp, out_path)
    n = len(html_text.encode("utf-8"))
    print(f"[render_baskets] Written {n} bytes -> {out_path}")
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render baskets.html.")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--baskets-json", type=Path, default=BASKETS_JSON_PATH)
    parser.add_argument("--build-sha", default="local")
    args = parser.parse_args(argv)
    render_to_file(
        baskets_json_path=args.baskets_json,
        out_path=args.out,
        build_sha=args.build_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
