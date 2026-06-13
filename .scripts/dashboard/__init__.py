"""Stage 5 dashboard renderer package.

Stdlib-only. Tailwind CDN + Chart.js CDN, no build step.

Public surface:
    render_helpers  -- shared helpers (badges, CAR cells, footer, sparkline SVG).
    templates       -- HTML page chrome (base shell with CDN + nav + footer).
    render_index    -- builds outputs/index.html from signals.json + dealings.json.
    render_performance -- builds outputs/performance.html (scoreboard + diagnostics).
    render_company  -- builds outputs/companies/{TICKER}.html one per ticker.
"""
from __future__ import annotations

__all__ = [
    "render_helpers",
    "templates",
    "render_index",
    "render_performance",
    "render_company",
]
