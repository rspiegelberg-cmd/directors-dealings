# Stage 5 — final visual design spec

**Status:** Locked 2026-05-14. Hand-off brief for Front-end Engineer. Closes out all visual decisions before code is written.
**Owner:** Rupert. Designer: `docs/agents/dashboard-designer.md`.
**Reads:** `stage-05-design-notes.md`, `stage-05-build-spec.md`, `stage-05-1-company-page.md`.
**Locked decisions integrated:** (1) brewing-cluster definition pinned, (2) deprecate button = write-to-disk endpoint, (3) light mode default.

---

## Hand-off paragraph (read me first)

The dashboard is **three static pages** generated locally: `dashboard/index.html` (the daily index), `dashboard/companies/{TICKER}.html` (one per ticker, pre-rendered), `dashboard/performance.html` (the trailing-analytics deep dive — split out of the index to give diagnostics room to breathe). All three are single-file HTML with Tailwind CDN + Chart.js CDN, light mode, no build step. The **centrepiece** is the per-signal scoreboard on `index.html` — one row per signal type (T0, T1, T2, T3, T4, S1, F1), eight columns including a 12-week sparkline of rolling median CAR and a Deprecate button that POSTs to a tiny local Flask sidecar (see Section 1.4). Two JSON files feed it: `dashboard/data/signals.json` (per-signal aggregates by horizon, active clusters, paper P&L, cohorts) and `dashboard/data/dealings.json` (today / this-week feed). The company pages bake their own `<script>` payload at generation time. Signal-badge colour palette is locked (T0=red-orange, T1=red, T2=amber, T3=green, T4=grey, S1=blue, F1=purple) — Tailwind class equivalents in Section 1.3. CAR colours: green-600 positive, red-600 negative, slate-500 neutral. Tabular numerals everywhere. Every signal badge has a hover tooltip with the rule from `stage-05-design-notes.md`. Every page footer shows `Generated YYYY-MM-DD HH:MM UTC · build {sha}`.

---

# Section 1 — Final visual spec

## 1.0 Three-page IA

```
dashboard/
├── index.html            ← daily check + today's signals + brewing clusters
├── companies/
│   └── {TICKER}.html     ← per-ticker detail (price chart, txn history, firing history)
└── performance.html      ← diagnostics deep-dive (multi-line CAR chart, cohort cuts,
                            per-signal scoreboard with sparklines, kill-candidate panel)
```

Why split index/performance: the design notes give "~60% of the page" to diagnostics, but in practice the daily-check mode (top strip + today's table + brewing clusters) and the trailing-analytics mode want different information densities. Index = action surface. Performance = audit surface. One nav link between them, top-right of each page.

---

## 1.1 `index.html` — daily action surface

### Layout (12-column grid, 1440px target width)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Directors Dealings · Today                          [Performance →]     │  header (h-12)
├──────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐   │
│  │ Signals     │  │ Active      │  │ Open paper P&L                  │   │  top strip
│  │ today       │  │ clusters    │  │ £ ±N  [signal filter ▼]         │   │  (col-span 4 each)
│  │   N  +Δ7d   │  │   N  (2 new)│  │                                 │   │
│  └─────────────┘  └─────────────┘  └─────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────┐ ┌────────────────────────────┐ │
│  │ Today's buy signals                  │ │ Brewing & active clusters  │ │
│  │ ──────────────────────────────────── │ │ ────────────────────────── │ │
│  │ Time│Ticker│Co│Director│£│Sigs│MTM   │ │ DNLM · Dunelm           ●  │ │  index body
│  │ ──────────────────────────────────── │ │ S1 · 5 dirs · £4.9M       │ │  (8 / 4 split)
│  │ rows...                              │ │ 12–13 May                  │ │
│  │                                      │ │                            │ │
│  │ (sort: severity desc, then £ desc)   │ │ ABC · …                    │ │
│  │                                      │ │ brewing · 2 dirs · £180k   │ │
│  │ This week                            │ │ 14 Apr – 12 May            │ │
│  │ ─────────────────────                │ │ ...                        │ │
│  │ rows...                              │ │                            │ │
│  └──────────────────────────────────────┘ └────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────┤
│  Generated 2026-05-14 12:49 UTC · build a3f1d22                          │  footer (h-8)
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.1.1 Header

Wrapper: `<header class="border-b border-slate-200 bg-white px-6 h-12 flex items-center justify-between">`
- Left: title `<h1 class="text-sm font-semibold text-slate-900 tracking-tight">Directors Dealings · Today</h1>`
- Centre: **freshness chip** (UX fix U3 2026-05-14) — `<div id="freshness" class="text-xs flex items-center gap-1.5 px-3 py-1 rounded-full border {stateClass}"><i class="ti {icon} text-sm"></i><span id="freshness-text"></span><span class="utc text-[10px] opacity-60"></span></div>`. Populated by `renderFreshness(signals.generated_at)` after JSON fetch. Green if age < 2 h, amber 2–4 h, red > 4 h. See `stage-05-roadmap-v1.md §U3` for full implementation and state classes.
- Right: `<a href="performance.html" class="text-xs text-indigo-600 hover:text-indigo-700">Performance →</a>`

Empty-state copy: header never empties.

### 1.1.2 Top strip — 3 tiles

Wrapper: `<div class="grid grid-cols-3 gap-3 p-6 pb-3">`. Each tile: `<div class="bg-slate-50 border border-slate-200 rounded-lg p-4">`.

**Tile 1 — Signals today**
- Label: `<div class="text-xs uppercase tracking-wide text-slate-500">Signals today</div>`
- Big number: `<div class="text-3xl font-semibold tabular-nums text-slate-900 mt-1">{signals_today_count}</div>`
- Delta row: `<div class="text-xs mt-1">{glyph} {signed_int} vs 7d avg</div>` — class `text-emerald-600` if positive, `text-rose-600` if negative, `text-slate-500` if zero. Glyph: ▲ / ▼ / —.
- JSON keys: `dealings.json` → `signals_today_count`, `signals_today_delta_vs_avg`
- Empty state: `signals_today_count == 0` → render the 0 but with subdued copy `<span class="text-slate-400">— quiet day —</span>` underneath. No alarm.
- Hover/tooltip: title attr on big number: "Count of distinct PDMR transactions today that fired ≥1 signal."

**Tile 2 — Active clusters**
- Label: `Active clusters`
- Big number: count of `active_clusters[]` where `s1_active === true`
- Sub: `<div class="text-xs text-slate-500 mt-1">{brewing_count} brewing</div>` — brewing = `s1_active === false`
- JSON keys: `signals.json` → `active_clusters[]`
- Empty state: `0 active · 0 brewing` in muted text.
- Tooltip: "Active = S1-firing (≥2 directors, most recent buy ≤30d). Brewing = same cluster shape, most recent buy 30–90d back."

**Tile 3 — Open paper P&L**
- Label: `Open paper P&L`
- Big number: formatted `£±N,NNN` with `text-emerald-600` / `text-rose-600` / `text-slate-500` colour.
- Filter dropdown: `<select class="text-xs border border-slate-300 rounded px-2 py-1 mt-1 bg-white">` with options `All`, `T0`, `T1`, `T2`, `T3`, `T4`, `S1`, `F1`. Persist to `localStorage['dd_paper_filter']`.
- Sub: `{paper_trades_open} open · {paper_trades_closed} closed`
- JSON keys: `paper_pnl_open`, `paper_trades_open`, `paper_trades_closed`
- Empty state (no paper trades): `£0 · 0 open · 0 closed`, subdued. Sub-copy `<div class="text-xs text-slate-400">Paper tracking starts when Stage 6 ships.</div>`
- Tooltip: "Mark-to-market of all open paper positions, net of costs."

### 1.1.3 Today's buy signals table

Wrapper: `<div class="col-span-8 bg-white border border-slate-200 rounded-lg overflow-hidden">`
Table: `<table class="w-full text-xs tabular-nums">` with `<thead class="bg-slate-50 text-slate-600 uppercase tracking-wide text-[10px]">`.

**Column order (UX fix U1 2026-05-14 — Signals first):**
Signals 12% · Time 8% · Ticker 8% · Company 26% · Director+Role 22% · £ Value 14% · MTM 10%.
(Role column removed; role chip merged into Director cell. 8 columns → 6.)

Row: `<tr class="border-t border-slate-100 hover:bg-indigo-50 cursor-pointer">` with `onclick="window.open('companies/' + ticker + '.html', '_blank')"`.

Cells:
- **Signals**: list of badges, gap-1. `renderBadge(sid)` returns `<span title="{tooltip}" class="cursor-help inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold {bg} {fg}">{label}</span>`. Sort within row by tier rank (T0 first, F1 last).
- **Time**: `time_utc` formatted `HH:mm` if same-day, else `DD MMM`.
- **Ticker**: `<span class="font-medium text-slate-900">{ticker}</span>`
- **Company**: `<span class="text-slate-700 truncate" title="{full}">{trim 28}</span>`
- **Director+Role**: two-line cell — `<div class="text-slate-700">{director}</div><span class="text-[10px] px-1.5 py-0.5 rounded {roleClass}">{role}</span>`. Role classes: CEO/CFO → `bg-indigo-100 text-indigo-700`; Chair → `bg-violet-100 text-violet-700`; NED/Non-Exec → `bg-slate-100 text-slate-600`; other → `bg-slate-100 text-slate-500`.
- **£ Value**: right-align, `£{value_gbp formatted with thousands separators}`. `text-slate-400` if `value_gbp == 0` (legacy/missing-price rows).
- **MTM**: `pct(mtm_pct)` with glyph. `<span class="text-emerald-600">▲ +2.3%</span>` / `<span class="text-rose-600">▼ -1.1%</span>` / `<span class="text-slate-400">—</span>`.

JSON keys consumed: `dealings.json.today[]` → `time_utc`, `ticker`, `company`, `director`, `role`, `txn_type`, `value_gbp`, `signals_fired`, `mtm_pct`. (Skip rows where `txn_type !== 'BUY'` defensively — sells are out of v1.)

Sort: by lowest-severity signal in `signals_fired` ascending (T0 = severity 0, F1 = severity 6), then by `value_gbp` desc.

Sub-section: This week. Same shape, reads `dealings.json.this_week[]`. Heading `<h3 class="text-xs uppercase tracking-wide text-slate-500 px-4 pt-4 pb-2">This week</h3>` between the two table bodies.

Footer micro-text inside the card, `<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">*MTM = mark-to-market from T+1 close after RNS, net of 50bps spread + 0.5% stamp duty (non-AIM). Click any row for the company page.</div>`

Empty state today: `<div class="px-4 py-12 text-center text-slate-400 text-xs">No signals fired today. Check back after the next RNS refresh.</div>`
Empty state this week: same shape, `No signals fired in the trailing 7 days.`

### 1.1.4 Brewing & active clusters panel

Wrapper: `<aside class="col-span-4 bg-white border border-slate-200 rounded-lg overflow-hidden">`

Two-tab header: `<div class="flex border-b border-slate-200 text-xs">` with tabs `Active ({s1_active count})` and `Brewing ({brewing count})`. Active tab: `border-b-2 border-indigo-600 text-indigo-700 px-4 py-2 font-medium`. Inactive: `text-slate-500 px-4 py-2 hover:text-slate-700`. Default tab = Active.

Per-cluster card: `<div class="border-b border-slate-100 px-4 py-3 hover:bg-indigo-50 cursor-pointer" onclick="window.open('companies/' + ticker + '.html', '_blank')">`

```html
<div class="flex items-center justify-between">
  <div class="font-medium text-sm text-slate-900">{ticker}</div>
  <span class="{badgeClass}">{S1 or brewing}</span>
</div>
<div class="text-xs text-slate-600 truncate" title="{full}">{company truncated to 28}</div>
<div class="text-[11px] text-slate-500 mt-1 tabular-nums">
  {director_count} dirs · £{aggregate_value_gbp/1000 formatted}k · {first_buy_date} – {last_buy_date}
</div>
```

Status badge:
- S1 active: `inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-100 text-blue-700`
- Brewing: `inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700`

**Brewing-cluster definition (locked):** `s1_active === false` AND `most_recent_buy` is between 30 and 90 days back from today. <30d would be "fresh" but exporter currently emits those as `s1_active=true`, so brewing in v1 = `s1_active=false && (today - last_buy_date) <= 90`. Anything older than 90 days is **stale and not shown** — exporter filters those out.

JSON keys consumed: `signals.json.active_clusters[]` → `ticker`, `company`, `director_count`, `aggregate_value_gbp`, `first_buy_date`, `last_buy_date`, `s1_active`.

Sort: within tab, by `aggregate_value_gbp` desc.

Footer micro-text: `<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">≥2 distinct directors buying same ticker, ≤30d apart. Brewing = most recent buy 30–90d back; stale clusters hidden.</div>`

Empty state per tab: `<div class="px-4 py-8 text-center text-slate-400 text-xs">No {active|brewing} clusters right now.</div>`

Hover/tooltip on company truncation: full company name via `title=`.

### 1.1.5 Generated-at footer (every page)

`<footer class="px-6 py-3 text-right text-[10px] text-slate-400">Generated {YYYY-MM-DD HH:MM UTC} · build {sha7}</footer>`

Reads `signals.json.generated_at` and a small `<meta name="build-sha">` populated by the exporter (Stage 4.6 adds it). Format: ISO timestamp -> JS Date -> `formatUTC()`. Build sha: first 7 chars of git short hash, or `local` if unavailable.

---

## 1.2 `performance.html` — diagnostics deep-dive

### Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ← Today    Performance                            Horizon: [T+21  ▼]    │  header
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Per-signal scoreboard                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ Sig │ N  │ Hit/Base │ Med │ Mean │ Edge │ 12w spark │ Status │  ⌫  │  │  centrepiece
│  │ T0  │ 3  │ 67 / 73  │+1.34│-3.65 │ +0.3 │  ▁▂▃▂▁    │ review │ [×] │  │  scoreboard
│  │ T1  │ 3  │ ...                                                    │  │
│  │ ...                                                                │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  Diagnostics chart                                                       │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ [Cumulative net CAR — 8 lines, M-12 ... Now]                       │  │
│  │                                                                    │  │
│  │ Legend ──── T0 — T1 — T2 — T3 — T4 — S1 — F1 ┄┄ FTSE A-S          │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  Cohort cuts                                                             │
│  ┌──────────────────────────┐  ┌─────────────────────────────────────┐   │
│  │ By txn value bucket      │  │ By sector                            │   │
│  │ [bar chart]              │  │ [horizontal hit % rows]              │   │
│  └──────────────────────────┘  └─────────────────────────────────────┘   │
│                                                                          │
│  Model assessment (auto)                                                 │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ Kill candidates: s1 (mean -15%, hit 21%, N=24)                     │  │
│  │ Watch: f1 (hit 31% vs 100% base)                                   │  │
│  │ Caveats: outlier domination on t3/t4/f1; small N on t0/t1/t2/t4    │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  Generated 2026-05-14 12:49 UTC · build a3f1d22                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.2.1 Header

Same as index header, including **freshness chip** in centre position (UX fix U3 — identical `renderFreshness()` function, reads `signals.json.generated_at`). Plus a horizon dropdown on the right:
`<select id="horizon" class="text-xs border border-slate-300 rounded px-2 py-1 bg-white tabular-nums">` with options:
- `T+1 (next session)`
- `T+21 (≈1 month)` ← default
- `T+90 (≈4.5 months)`
- `T+252 (≈1 year)`

Change handler dispatches `window.dispatchEvent(new CustomEvent('horizonChange', {detail: 't21'}))`. Both scoreboard and chart subscribe.

Left back-nav: `<a href="index.html" class="text-xs text-indigo-600 hover:text-indigo-700">← Today</a>`

### 1.2.2 Per-signal scoreboard (centrepiece — diagnostics surface)

Wrapper: `<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden">`
Caption above: `<h2 class="px-4 py-3 text-xs uppercase tracking-wide text-slate-500 border-b border-slate-100">Per-signal scoreboard</h2>`
Table: `<table class="w-full text-xs tabular-nums">` with sticky header.

Columns (widths %): Signal 8 · Trades 8 · Hit / Base 13 · Median CAR 11 · Mean CAR 11 · Edge 10 · 12w trend 14 · Status 10 · Deprecate 15.

Iterate signals in fixed order: `['t0','t1','t2','t3','t4','s1','f1']`. Empty `state.horizon` slice = render a `—` row.

Per row:
- **Signal cell**: `renderBadge(sid)` (same shared component used on index).
- **Trades**: `signals.json.horizon_aggregates[h].signals[sid].trades`, right-aligned. If `< 20`, append `<span class="ml-1 text-amber-600" title="N<20 — preliminary, wait for more firings">⚠</span>`.
- **Hit / Base**: `{hit_pct}% / {base_rate}%`. Hit colour: `text-emerald-600` if `hit_pct >= base_rate`, `text-rose-600` if `< base_rate * 0.85`, else `text-slate-700`.
- **Median CAR**: pct() formatted. Colour: `text-emerald-600` if `> +0.05%`, `text-rose-600` if `< -0.05%`, else `text-slate-500`.
- **Mean CAR**: same. Append outlier glyph if `outlier_flag === true`: `<span class="ml-1 text-amber-600" title="Outlier — top single trade dominates; see Stage 4.5 fix">⚠</span>`.
- **Edge**: pct() formatted, same colour rule as Median. Header has a hover-formula popover `title="edge = mean_car − benchmark mean over same horizon"`.
- **12w trend (sparkline)**: inline SVG (see §1.3.5).
- **Status pill**: see §1.4.
- **Deprecate**: see §1.4.

Footer caption: `<div class="text-[10px] text-slate-400 px-4 py-2 border-t border-slate-100">Window: {label} · base rate {base}% = % of random {label} FTSE All-Share holds positive. Hit % shows percent of trades that beat that base. ⚠ = N<20 or single-outlier domination.</div>`

JSON keys consumed (per row): `signals.json.horizon_aggregates[state.horizon].signals[sid]` → `trades, hit_pct, median_car, mean_car, edge, sparkline, status, outlier_flag` PLUS `signals.json.horizon_aggregates[state.horizon].base_rate`.

Empty-state row (signal absent from horizon): all numeric cells render `—` in `text-slate-300`, sparkline renders as a flat grey baseline, status pill = `gated`, deprecate disabled.

### 1.2.3 Diagnostics chart

Wrapper: `<section class="m-6 bg-white border border-slate-200 rounded-lg p-4"><h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3">Cumulative net CAR @ {horizon_label} — trailing 12 months</h2><div class="relative h-64"><canvas id="diagChart"></canvas></div><div id="diagLegend" class="flex flex-wrap gap-3 text-[10px] mt-2"></div></section>`

Chart.js config:
```
type: 'line',
data: {
  labels: ['M-12','M-11',...'M-1','Now'],  // 13 points
  datasets: [
    { label: 'T0',  data: [...], borderColor: '#dc2626', borderWidth: 2, tension: 0.3, pointRadius: 0 },
    { label: 'T1',  data: [...], borderColor: '#ef4444', borderWidth: 2, tension: 0.3, pointRadius: 0 },
    { label: 'T2',  data: [...], borderColor: '#f59e0b', borderWidth: 2, tension: 0.3, pointRadius: 0 },
    { label: 'T3',  data: [...], borderColor: '#10b981', borderWidth: 2, tension: 0.3, pointRadius: 0 },
    { label: 'T4',  data: [...], borderColor: '#94a3b8', borderWidth: 1.5, tension: 0.3, pointRadius: 0 },
    { label: 'S1',  data: [...], borderColor: '#3b82f6', borderWidth: 2, tension: 0.3, pointRadius: 0 },
    { label: 'F1',  data: [...], borderColor: '#a855f7', borderWidth: 2, tension: 0.3, pointRadius: 0 },
    { label: 'FTSE A-S', data: [...], borderColor: '#888780', borderWidth: 1, borderDash: [6,4], tension: 0, pointRadius: 0 }
  ]
},
options: {
  responsive: true, maintainAspectRatio: false,
  scales: {
    x: { grid: { display: false }, ticks: { font: { size: 10 }, color: '#94a3b8' } },
    y: { grid: { color: '#f1f5f9' }, ticks: { font: { size: 10 }, color: '#64748b',
         callback: v => (v*100).toFixed(0) + '%' } }
  },
  plugins: {
    legend: { display: false },          // custom HTML legend below canvas
    tooltip: { mode: 'index', intersect: false,
               callbacks: { label: ctx => ctx.dataset.label + ': ' + (ctx.parsed.y*100).toFixed(2) + '%' } }
  },
  interaction: { mode: 'index', intersect: false }
}
```

**Interactive legend (UX fix U2 2026-05-14):** 8 swatches rendered as clickable `<span>` elements. Clicking a swatch highlights that dataset (borderWidth 3, full opacity) and fades all others to 15% opacity (hex alpha `26`). FTSE All-Share never fades below 60% opacity (`99` hex alpha) — it is always the reference. Clicking the active swatch again resets all to full. Active swatch: add `border-bottom: 1.5px solid currentColor`. Store each dataset's original colour as `_origColor` before any mutation. Call `chart.update('none')` to skip animation. Full `buildDiagLegend()` implementation in `stage-05-roadmap-v1.md §U2`.

JSON keys: `signals.json.horizon_aggregates[state.horizon].diagnostics_series` — **new required key for Stage 4.6 exporter**: `{ t0: [13 floats], t1: [...], ..., ftas: [13 floats] }`. If missing, render the canvas empty with overlay `<div class="absolute inset-0 flex items-center justify-center text-xs text-slate-400">Diagnostics series not yet exported — re-run dashboard exporter.</div>`.

Subscribes to `window.addEventListener('horizonChange', e => rebuildDiagChart(e.detail))`.

### 1.2.4 Cohort cuts

Two-column grid: `<div class="m-6 grid grid-cols-2 gap-4">`.

**Block A — By transaction value bucket**

`<section class="bg-white border border-slate-200 rounded-lg p-4"><h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3">By director's transaction value</h2><div class="relative h-40"><canvas id="cohortValue"></canvas></div></section>`

Chart.js bar:
```
type: 'bar',
data: {
  labels: ['£1–25k', '£25–100k', '£100–500k', '£500k+'],
  datasets: [{
    data: [v1, v2, v3, v4],
    backgroundColor: ctx => ctx.parsed.y >= 0 ? '#10b981' : '#ef4444'
  }]
},
options: {
  responsive: true, maintainAspectRatio: false,
  scales: {
    x: { grid: { display: false }, ticks: { font: { size: 10 } } },
    y: { ticks: { font: { size: 10 }, callback: v => (v*100).toFixed(1)+'%' } }
  },
  plugins: { legend: { display: false } }
}
```

JSON keys: `signals.json.cohorts.by_value_bucket["1k-25k"|"25k-100k"|"100k-500k"|"500k+"]`. Null buckets render as a faint grey bar at y=0 with title="N too small".

**Block B — By sector**

`<section class="bg-white border border-slate-200 rounded-lg p-4"><h2 class="text-xs uppercase tracking-wide text-slate-500 mb-3">By sector — hit % @ T+21</h2><ul class="space-y-1"></ul></section>`

Per sector row:
```html
<li class="flex items-center gap-2 text-xs">
  <span class="w-32 truncate text-slate-700" title="{full}">{sector}</span>
  <div class="flex-1 h-3 bg-slate-100 rounded-sm relative">
    <div class="absolute inset-y-0 left-0 rounded-sm {bgClass}" style="width: {hit_pct}%"></div>
    <div class="absolute inset-y-0" style="left: {base_rate}%; width: 1px; background: #475569"></div>
  </div>
  <span class="tabular-nums text-slate-700 w-12 text-right">{hit_pct}%</span>
</li>
```
Bar bg: `bg-emerald-400` if `hit_pct >= base_rate`, `bg-rose-400` otherwise. Black hairline at `base_rate` as reference.

JSON keys: `signals.json.cohorts.by_sector[]` — Stage 4.6 must populate. Each entry `{sector, hit_pct, n}`. Empty array → `<li class="text-xs text-slate-400">Sector mapping not yet wired — see Stage 4.6.</li>`.

### 1.2.5 Model assessment panel (auto-rendered from signals.json)

Wrapper: `<section class="m-6 bg-amber-50 border border-amber-200 rounded-lg p-4"><h2 class="text-xs uppercase tracking-wide text-amber-700 mb-3">Model assessment</h2>...</section>`

Three sub-blocks, each `<div class="mb-2 text-xs">`:
- **Kill candidates** (red bullet): names of signals where `N>=20 && mean_car<0 && hit_pct<base_rate` at `t90`.
- **Watch** (amber bullet): `N>=20 && (mean_car<0 || hit_pct<base_rate)` but not both.
- **Caveats** (slate bullet): outlier domination flags + small-N footnotes.

Computed client-side from `signals.json` — no extra data needed beyond what scoreboard already reads. Render only the sub-blocks that have content; if all clean, render `<div class="text-xs text-emerald-700">All signals within tolerance.</div>`.

---

## 1.3 `companies/{TICKER}.html` — per-ticker page

### Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ← Dashboard       {TICKER} · {Company}    [Sector chip] [AIM badge]     │  header strip
│                    Latest close: 1,234p ▲ +0.8%        Last refresh ...  │
├──────────────────────────────────────────────────────────────────────────┤
│  [conditional status banner — active S1 or recent firing]                │
├──────────────────────────────────────────────────────────────────────────┤
│  Price chart                                          [6m][1y▼][5y][All] │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  ▲                ▲                                                │  │
│  │       price line ▲     ▲▲                                          │  │
│  │                                                                    │  │
│  │  [volume bars below in coupled canvas]                             │  │
│  └────────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────┤
│  Transactions table                                                      │
│  Date │ Director │ Role │ Type │ Shares │ Price │ £ │ Signals │ ↗       │
│  ...                                                                     │
├──────────────────────────────────────────────────────────────────────────┤
│  Signal-firing history with outcomes                                     │
│  Fired │ Sig │ Director │ T+1 │ T+21 │ T+90 │ T+252                     │
│  ...                                                                     │
│  ┌──── tiny stats card: total firings, hit % @ T+21, median CAR @ T+21──┐│
├──────────────────────────────────────────────────────────────────────────┤
│  Cluster history                                                         │
│  ...                                                                     │
├──────────────────────────────────────────────────────────────────────────┤
│  Footer / disclaimer / generated-at                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.3.1 Header strip

Wrapper: `<header class="border-b border-slate-200 bg-white px-6 py-4">`

Two rows. Row 1:
- Left: `<a href="../index.html" class="text-xs text-indigo-600">← Dashboard</a>`
- Middle: `<h1 class="text-lg font-semibold text-slate-900 tabular-nums">{TICKER}</h1><span class="text-sm text-slate-600 ml-2">{company}</span>`
- Sector chip: `<span class="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-700 ml-2">{sector}</span>`
- AIM badge: if `is_aim` → `<span class="text-[10px] px-2 py-0.5 rounded bg-amber-100 text-amber-700">AIM</span>` else `<span class="text-[10px] px-2 py-0.5 rounded bg-slate-100 text-slate-700">Main</span>`

Row 2:
- Left: `<div class="text-sm tabular-nums">Latest close: {latest_close_p}p <span class="{colour}">{glyph} {delta_pct}</span></div>`
- Right: `<div class="text-[10px] text-slate-400">Last refresh: {timestamp UTC}</div>`

JSON keys baked into `<script>`: `header.ticker, header.company, header.sector, header.is_aim, header.latest_close, header.prev_close, header.generated_at`.

Empty-state: sector unknown → chip text `Unknown sector`, slate. No close price → `Latest close: —`, no delta.

### 1.3.2 Active status banner (conditional)

Render only if at least one condition true:
- `active_cluster` true → `<div class="bg-emerald-50 border-l-4 border-emerald-500 px-6 py-3 text-sm text-emerald-800">Active S1 cluster · {n} directors · £{aggregate}k · {first_buy_date} – {last_buy_date}</div>`
- Brewing cluster → same wrapper, `bg-amber-50 border-amber-500 text-amber-800`, copy `Brewing cluster · ...`
- Recent signal (last 7 days) → `bg-indigo-50 border-indigo-500 text-indigo-800`, copy `{T1} fired {n} days ago — CEO buy £{value}`. Click → scroll to firing history row.

If none: render nothing (no placeholder).

### 1.3.3 Annotated price chart

Wrapper: `<section class="m-6 bg-white border border-slate-200 rounded-lg p-4"><div class="flex justify-between items-center mb-3"><h2 class="text-xs uppercase tracking-wide text-slate-500">Price · {window}</h2><div class="inline-flex border border-slate-300 rounded-md text-xs"><button class="px-2 py-1">6m</button><button class="px-2 py-1 bg-indigo-600 text-white">1y</button><button class="px-2 py-1">5y</button><button class="px-2 py-1">All</button></div></div><div class="relative h-72"><canvas id="priceChart"></canvas></div><div class="relative h-16 mt-1"><canvas id="volumeChart"></canvas></div></section>`

Chart.js price chart:
```
type: 'line',
data: {
  labels: dates,
  datasets: [
    { label: 'Close', data: prices, borderColor: '#475569', borderWidth: 1.5, pointRadius: 0, tension: 0 },
    { label: 'Sector benchmark', data: bench_indexed, borderColor: '#888780',
      borderDash: [6,4], borderWidth: 1, pointRadius: 0, hidden: true }   // toggleable, off by default
  ]
},
options: {
  scales: { x: { ticks: { maxRotation: 0, font:{size:10} } },
            y: { ticks: { font:{size:10} } } },
  plugins: {
    legend: { display: false },
    annotation: { annotations: txnMarkers }   // chartjs-plugin-annotation
  }
}
```

`txnMarkers` shape — one annotation per transaction in window:
```
{ type: 'point', xValue: '2026-05-13', yValue: priceAt('2026-05-13'),
  backgroundColor: '#3C3489' /*exec*/ | '#AFA9EC' /*NED*/ | '#dc2626' /*sell*/ | '#94a3b8' /*grant*/,
  borderColor: 'white', borderWidth: 1.5,
  pointStyle: 'triangle', radius: 6,
  callbacks: { onClick: () => scrollToRow(txnId), onHover: () => showTooltip(...) }
}
```

Cluster firing markers: emerald circle (`pointStyle: 'circle', radius: 9, backgroundColor: 'transparent', borderColor: '#10b981', borderWidth: 2`) around the topmost marker in a cluster window.

Volume chart: separate canvas, same X axis (manually align labels), type `bar`, bar colour `#cbd5e1`, no grid.

Window toggle: 6m / 1y / 5y / All. On click rebuild both charts with sliced data.

Hover tooltip on marker:
```
{date}
{director}, {role}
{type} · {shares} sh @ {price}p
£ {value}
Signals: {badges...}
```

Click marker → `document.getElementById('txn-' + txnId).scrollIntoView({behavior:'smooth', block:'center'})` then pulse-highlight that row for 2s (`row.classList.add('bg-amber-100'); setTimeout(() => row.classList.remove('bg-amber-100'), 2000)`).

Empty state (no prices): canvas hidden, show `<div class="px-4 py-12 text-center text-slate-400 text-xs">No price history available for {TICKER}.</div>`.

### 1.3.4 Transactions table

Wrapper: `<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden"><h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 border-b border-slate-100">Transactions</h2><table class="w-full text-xs tabular-nums">...</table></section>`

Columns: Date · Director · Role · Type · Shares · Price (p) · Value (£) · Signals · ↗

Per row: `<tr id="txn-{id}" class="border-t border-slate-100">`.
- **Date**: `announced_at` → `DD MMM YYYY`.
- **Director**: `<span class="text-slate-700">{director}</span>`
- **Role**: chip (see index spec §1.1.3 role chip rules).
- **Type**: `<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold {colour}">{txn_type}</span>`. BUY → `bg-emerald-100 text-emerald-700`; SELL → `bg-rose-100 text-rose-700`; GRANT/SIP/EXERCISE → `bg-slate-100 text-slate-500`.
- **Shares**: right-align, thousands separator.
- **Price (p)**: right-align, 2dp.
- **Value (£)**: right-align, thousands separator.
- **Signals**: row of badges (shared component).
- **Source**: `<a href="{url}" target="_blank" rel="noopener" class="text-slate-400 hover:text-indigo-600" title="{url}">↗</a>`. If `url == null` → `<span class="text-slate-200 cursor-not-allowed" title="no RNS link on file">↗</span>`.

Show last 20 by default with expander `<button class="text-xs text-indigo-600 px-4 py-2 hover:bg-slate-50 w-full text-left">show all ({N})</button>`. Click toggles visibility on rows 21..N.

Empty state: `<div class="px-4 py-8 text-center text-slate-400 text-xs">No PDMR transactions on file for {TICKER}.</div>`.

Hover tooltip on row: native `title` showing announced timestamp UTC.

### 1.3.5 Signal-firing history with outcomes

Wrapper: `<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden"><h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 border-b border-slate-100">Signal-firing history</h2>...</section>`

Columns: Fired-on · Signal · Director · T+1 · T+21 · T+90 · T+252.

Per row:
- **Fired-on**: `DD MMM YYYY`.
- **Signal**: `renderBadge(sid)`.
- **Director**: `text-slate-700`.
- **CAR cells**: 4 columns. If matured (i.e. firing_date + horizon_days <= today AND outcome present): `pct()` with colour (green ≥+0.05%, red ≤−0.05%, slate else). If pending: `<span class="text-slate-300">—</span>`. Title attr on the dash: `Matures {expected_date}`.

Below the table, tiny stats card:
```html
<div class="bg-slate-50 border-t border-slate-100 px-4 py-3 text-xs flex gap-6">
  <div><span class="text-slate-500">Firings:</span> <span class="font-medium tabular-nums">{n}</span></div>
  <div><span class="text-slate-500">Hit % @ T+21:</span> <span class="font-medium tabular-nums {colour}">{hit}%</span></div>
  <div><span class="text-slate-500">Median CAR @ T+21:</span> <span class="font-medium tabular-nums {colour}">{med}</span></div>
</div>
```
If `n < 5`: replace stats line with `<div class="text-slate-400 italic">Not enough firings (N={n}) for a meaningful per-ticker stat.</div>`.

Empty state: `<div class="px-4 py-8 text-center text-slate-400 text-xs">No signals have fired for {TICKER}.</div>`.

### 1.3.6 Cluster history

Wrapper: `<section class="m-6 bg-white border border-slate-200 rounded-lg overflow-hidden"><h2 class="text-xs uppercase tracking-wide text-slate-500 px-4 py-3 border-b border-slate-100">Cluster history</h2>...</section>`

Per row: `<tr>` with cells Cluster ID · Date range · Directors · £ · Status. Status pill: `Active` (emerald-100) / `Historical` (slate-100).

Empty state: `<div class="px-4 py-8 text-center text-slate-400 text-xs">No clusters detected for {TICKER}.</div>`.

### 1.3.7 Footer

`<footer class="px-6 py-4 text-[10px] text-slate-400 border-t border-slate-200"><p>Generated by gen_company_pages.py {timestamp}.</p><p>Data: Investegate RNS feed · Yahoo Finance · FTSE All-Share benchmark.</p><p class="mt-2">Historic insider-trading outcomes are not predictive. Net-of-cost CAR assumes 50bps round-trip + 0.5% UK stamp on non-AIM buys.</p><p class="mt-2">Generated {YYYY-MM-DD HH:MM UTC} · build {sha7}</p></footer>`

---

## 1.4 Locked: colour palette (Tailwind class equivalents)

Signal-tier badge palette (locked per Rupert 2026-05-14 — supersedes the indigo palette in stage-05-build-spec.md §Tier-colour table). The build spec was incorrect; designer-canonical palette is:

| Signal | Hex (bg/fg) | Tailwind class |
|---|---|---|
| T0 | bg `#dc2626` / fg `#fff` | `bg-red-600 text-white` (red-orange/highest conviction) |
| T1 | bg `#ef4444` / fg `#fff` | `bg-red-500 text-white` (red) |
| T2 | bg `#f59e0b` / fg `#fff` | `bg-amber-500 text-white` (amber) |
| T3 | bg `#10b981` / fg `#fff` | `bg-emerald-500 text-white` (green) |
| T4 | bg `#94a3b8` / fg `#fff` | `bg-slate-400 text-white` (grey) |
| S1 | bg `#3b82f6` / fg `#fff` | `bg-blue-500 text-white` (blue) |
| F1 | bg `#a855f7` / fg `#fff` | `bg-purple-500 text-white` (purple) |

CAR / number colours:
- Positive (>+0.05%): `text-emerald-600` (`#059669`)
- Negative (<-0.05%): `text-rose-600` (`#e11d48`)
- Neutral / zero: `text-slate-500` (`#64748b`)
- Muted / missing: `text-slate-400`

Status pills:
- `live`: `bg-emerald-100 text-emerald-700` + `●` glyph in `text-emerald-500`
- `review`: `bg-amber-100 text-amber-700` + `●` in `text-amber-500`
- `kill?`: `bg-rose-100 text-rose-700` + `●` in `text-rose-500`
- `gated`: `bg-slate-100 text-slate-500` + `●` in `text-slate-400`

Backgrounds:
- Page: `bg-slate-50`
- Cards: `bg-white border border-slate-200 rounded-lg`
- Subtle row hover: `hover:bg-indigo-50`
- Highlight pulse (row scroll target): `bg-amber-100` for 2s.

Typography:
- Body: default Tailwind (`font-sans`), `text-xs` default for tables, `text-sm` for body text.
- Numbers everywhere: `tabular-nums`.
- Section headers: `text-xs uppercase tracking-wide text-slate-500`.
- Page title: `text-sm font-semibold text-slate-900` (small — this is a dense data tool, not a marketing page).

### 1.4.1 Locked: sparkline spec

30-day rolling-window CAR mini-chart per signal in the scoreboard. NOTE the design-notes call it "12-week" — they're the same artefact, just relabelled here for clarity: 9 data points each representing the median net CAR at the current horizon over a trailing 30-day window of firing dates. Stage 4.6 emits 9 points per (signal, horizon) under `signals.sparkline`.

Geometry: **inline SVG, 60px wide × 20px tall** (locked per task brief, supersedes the 64×18 in design-notes). 9 data points → polyline. No axes, no labels.

Render snippet (inline):
```
<svg width="60" height="20" viewBox="0 0 60 20" preserveAspectRatio="none">
  <polyline fill="none" stroke="{color}" stroke-width="1.5"
            points="{x0},{y0} {x1},{y1} ..." />
</svg>
```

Colour rule: **signal-tier colour** (locked per task brief — uniform within a row, makes the scoreboard easier to scan than the slope-coloured variant in design-notes). Use the badge `bg` hex from §1.4. The slope-colouring idea is moved to v1.1.

X positions: `xi = i * (60 / 8)` for `i in 0..8` (8 segments across 9 points).
Y positions: normalise data into `[1, 19]` — `yi = 19 - ((data[i] - min) / (max - min)) * 18`. Flat data (`max == min`): all y = 10 (middle).

Data source: `signals.json.horizon_aggregates[state.horizon].signals[sid].sparkline` (array of 9 floats, net CAR fractions e.g. `0.024`).

Empty / all-zero (current state): render a single faint horizontal line `<line x1="0" y1="10" x2="60" y2="10" stroke="#cbd5e1" stroke-width="1"/>` and title attr `"Sparkline series not yet populated — see Stage 4.6 sparkline emit."`.

Hover tooltip (native `title` on the `<svg>`): `"30d rolling median CAR @ {horizon}, latest = {pct(last)}"`.

### 1.4.2 Locked: deprecate-button flow (write-to-disk)

**Decision:** Option B — write-to-disk endpoint. Per task brief.

**Button position:** rightmost column of each scoreboard row.

Render:
```html
<button class="text-[10px] px-2 py-1 rounded border border-slate-300 text-slate-600 hover:border-rose-400 hover:text-rose-600 disabled:opacity-40 disabled:cursor-not-allowed"
        data-signal-id="{sid}" onclick="onDeprecateClick(event)"
        title="Stop new evaluations of this signal. Existing firings preserved.">
  Deprecate
</button>
```
Disabled if `status === 'gated'` or if already deprecated (`signal_status.json[sid] === 'deprecated'`).

**Confirmation modal copy:**
```
Title: Deprecate {SIGNAL_BADGE}?
Body:  This will write {sid} = "deprecated" to .data/signal_status.json.
       The signal engine will skip {sid} on its next eval pass.
       Existing fired rows in the signals table are preserved.
       This is reversible — edit signal_status.json by hand to undeprecate.
Buttons: [Cancel]  [Deprecate signal]
```

Modal Tailwind: `<div class="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50"><div class="bg-white rounded-lg shadow-xl max-w-md w-full p-6"><h3 class="text-sm font-semibold text-slate-900 mb-2">Deprecate {badge}?</h3><p class="text-xs text-slate-700 mb-4 leading-relaxed">{body}</p><div class="flex justify-end gap-2"><button class="px-3 py-1.5 text-xs border border-slate-300 rounded text-slate-600 hover:bg-slate-50">Cancel</button><button class="px-3 py-1.5 text-xs bg-rose-600 text-white rounded hover:bg-rose-700">Deprecate signal</button></div></div></div>`

**POST request shape:**
```
POST http://127.0.0.1:8765/api/deprecate
Content-Type: application/json
Body: { "signal_id": "t3", "deprecated_by": "dashboard-ui", "timestamp": "2026-05-14T12:49:35Z" }

Response 200:
  { "ok": true, "signal_id": "t3", "status": "deprecated", "written_at": "..." }

Response 4xx/5xx:
  { "ok": false, "error": "<message>" }
```

Server is a tiny local Flask sidecar started by `python -m dashboard.api`. Listens on `127.0.0.1:8765`, writes `.data/signal_status.json` atomically (write to `.tmp` then rename), responds with the new state. No CORS issues — same-origin if dashboard served from `python -m http.server` on `127.0.0.1`, otherwise add `flask-cors` to allow `http://localhost:*`. Sidecar is out-of-scope for Stage 5 implementation but the dashboard ships with the button wired to it — if sidecar is down, button toasts an error (see below) instead of writing.

**Optimistic UI:**
- On click → modal opens.
- On confirm → button disables (`disabled` attr) AND row greys (`opacity-50 pointer-events-none`) immediately.
- POST fires in background.
- On 200: status pill flips to `gated` style (deprecated reads same visually as gated) with `●` label "deprecated"; toast `<div class="fixed bottom-4 right-4 bg-emerald-600 text-white text-xs px-4 py-2 rounded shadow-lg">Signal {sid} deprecated.</div>` for 3s.
- On 4xx/5xx or network failure: revert button (`disabled=false`, `opacity-100`); toast `<div class="fixed bottom-4 right-4 bg-rose-600 text-white text-xs px-4 py-2 rounded shadow-lg">Could not deprecate {sid}: {error}. Edit .data/signal_status.json manually or check if dashboard.api is running.</div>` for 6s.

**Failure modes covered:**
1. Sidecar not running → fetch throws → toast "Sidecar not running. Run: python -m dashboard.api".
2. Disk full / permission denied → 500 from server → toast with server error message.
3. Concurrent click (button already in-flight) → button disabled prevents.
4. Page reload after deprecate → `signal_status.json` read on page load, deprecated rows render with "deprecated" status pill and disabled button.

**State on page load:** dashboard reads `.data/signal_status.json` (fetched alongside `signals.json` and `dealings.json`) and decorates the scoreboard accordingly. If file doesn't exist (first run), assume empty `{}`.

---

# Section 2 — Continuous model assessment (mandatory)

**Source:** `.data/_backtest_results.csv` (146 rows, generated 2026-05-14T11:32:46Z, entry dates 2025-06-03 to 2026-05-12). Per-signal aggregates cross-checked against `dashboard/data/signals.json`. All numbers below are NET CAR (after 50bps spread + 0.5% stamp where applicable).

**Critical data-quality caveat up front:** `signals.json` reports `base_rate @ T+90 = 100.0%`. This is **not a real base rate** — it's an artefact of the trailing-12-month window being a near-uniform bull run for the FTSE All-Share. Every hit% comparison at T+90 will look catastrophic against this base. The hit-rate test in the kill-candidate rule below uses the reported number as required, but the more meaningful test at T+90 right now is **mean net CAR < 0** alone. Note this clearly in any kill verdict.

## 2.1 Per-signal hit rate + mean CAR table (live data)

| Sig | Horizon | N | Mean net CAR | Median net CAR | Hit % | Base % |
|---|---|---:|---:|---:|---:|---:|
| T0 | T+1   | 3  | +2.09%  | +1.21%  | 66.7% | 54.8% |
| T0 | T+21  | 3  | −3.65%  | +1.34%  | 66.7% | 73.6% |
| T0 | T+90  | 2  | −25.54% | −25.54% | 0.0%  | 100.0% |
| T0 | T+252 | 0  | —       | —       | —     | 50.0% |
| T1 | T+1   | 3  | +2.09%  | +1.21%  | 66.7% | 54.8% |
| T1 | T+21  | 3  | −3.65%  | +1.34%  | 66.7% | 73.6% |
| T1 | T+90  | 2  | −25.54% | −25.54% | 0.0%  | 100.0% |
| T1 | T+252 | 0  | —       | —       | —     | 50.0% |
| T2 | T+1   | 3  | −0.59%  | −0.65%  | 33.3% | 54.8% |
| T2 | T+21  | 3  | +0.44%  | +1.50%  | 66.7% | 73.6% |
| T2 | T+90  | 2  | +9.74%  | +9.74%  | 100.0%| 100.0% |
| T2 | T+252 | 0  | —       | —       | —     | 50.0% |
| T3 | T+1   | 26 | −1.13%  | −1.04%  | 15.4% | 54.8% |
| T3 | T+21  | 22 | −5.06%  | −6.21%  | 22.7% | 73.6% |
| T3 | T+90  | 14 | −6.93%  | −11.50% | 14.3% | 100.0% |
| T3 | T+252 | 0  | —       | —       | —     | 50.0% |
| T4 | T+1   | 7  | −1.65%  | −1.38%  | 0.0%  | 54.8% |
| T4 | T+21  | 7  | +0.53%  | −1.17%  | 42.9% | 73.6% |
| T4 | T+90  | 5  | +56.95% | +0.48%  | 60.0% | 100.0% |
| T4 | T+252 | 0  | —       | —       | —     | 50.0% |
| S1 | T+1   | 44 | −0.57%  | −0.96%  | 20.5% | 54.8% |
| S1 | T+21  | 37 | −4.63%  | −3.58%  | 27.0% | 73.6% |
| S1 | T+90  | 24 | −15.43% | −13.62% | 20.8% | 100.0% |
| S1 | T+252 | 0  | —       | —       | —     | 50.0% |
| F1 | T+1   | 60 | −1.66%  | −1.19%  | 16.7% | 54.8% |
| F1 | T+21  | 54 | −2.09%  | −2.61%  | 35.2% | 73.6% |
| F1 | T+90  | 35 | +2.50%  | −10.25% | 31.4% | 100.0% |
| F1 | T+252 | 0  | —       | —       | —     | 50.0% |

## 2.2 Kill candidates

Strict rule (N≥20 at T+90, mean net CAR < 0 at T+90, hit % < base):

**S1 — `kill?` recommended.**
- N=24 (>=20 ✓)
- Mean net CAR @ T+90 = **−15.43%** (negative ✓)
- Median = −13.62% (most firings underperform, not a tail story)
- Hit % @ T+90 = 20.8% vs base 100% (catastrophic, but base is suspect; even adjusted, 20.8% hit rate is far below the implied <50% honest base)
- All three flags. **Verdict: deprecate after one more review cycle** — but pause on the trigger because S1 underpins T0. Killing S1 in v1 would also gut T0 (T0 requires an S1 leg). Recommend showing `kill?` status pill on the dashboard but leaving the deprecate button enabled for Rupert to pull manually. Mark in the model-assessment panel as "S1 is failing its own backtest; T0 depends on it — co-deprecation review needed."

**F1 — `watch`, not yet kill.**
- N=35 (>=20 ✓)
- Mean net CAR @ T+90 = **+2.50%** (positive — does NOT meet kill rule)
- Hit % @ T+90 = 31.4% < base 100% (would flag, but median is −10.25%, mean is +2.50% only because the top winner is +335%)
- Outlier-dominated, not robust. **Verdict: status `review`, leave deprecate button armed.** Until Stage 4.5 outlier remediation lands, F1 should also stay in `gated` for numeric display.

**T3 — `kill?` strong recommendation.**
- N=14 at T+90 — **just below the kill-rule N≥20 threshold.** Strict reading: don't kill yet.
- BUT at T+21 (N=22): mean = −5.06%, hit = 22.7% vs base 73.6%. Both kill flags trip at T+21 with adequate N.
- At T+1 (N=26): mean = −1.13%, hit = 15.4% vs base 54.8%. Both kill flags trip.
- Two horizons with adequate N both fire. **Verdict: status `kill?` and recommend deprecate.** This is the cleanest kill candidate in the dataset — its problem is structural (NEDs are catching falling knives), not outlier-driven.

**T2 — `keep, preliminary`** — only 2 firings at T+90; all numbers premature. T+21 mean is positive. Footnote N<20 on tile, no action.

**T0 / T1 — `keep, preliminary`** — N=2-3 at every horizon. Far too small to act. Note that T0 and T1 currently produce IDENTICAL backtest rows (every T0 firing also fires T1 by construction — T0 is T1+S1 combo); this is correct but means the rows aren't independent evidence. Footnote.

**T4 — `keep, preliminary, outlier-flagged`** — N=5 at T+90 with a single +335% winner that drags the mean to +57%. Median is +0.48%. Don't trust the mean. Footnote N<20 + outlier flag.

## 2.3 Optimism checks

### Outlier domination at T+90

| Signal | N | Top winner | Top-winner share of total positive return | Flag |
|---|---:|---:|---:|---|
| T3 | 14 | +79.66% | 86.2% | ⚠ |
| T4 | 5  | +335.71% | 99.2% | ⚠ |
| S1 | 24 | +12.71% | 49.8% | ⚠ |
| F1 | 35 | +335.71% | 69.8% | ⚠ |

**Reading:** T4 and F1 share the +335% winner — almost certainly the same underlying ticker firing both signals. This is the Stage 4.5 outlier the design notes flag (originally written up as +4232% raw before the `adjclose` switch). Until Stage 4.5 confirms what's left after the fix, F1 means and T4 means are not trustworthy at any horizon.

T3 87% concentration in one winner means 13 of 14 firings are at-or-below zero; one ticker carries the entire positive contribution. This reinforces the kill verdict.

### Regime concentration (% of firings from single month at T+90)

| Signal | N | Top month | Top-month firings | % | Flag |
|---|---:|---|---:|---:|---|
| T3 | 14 | 2025-10 | 5 | 35.7% | — |
| T4 | 5  | 2025-06 | 2 | 40.0% | borderline |
| S1 | 24 | 2025-10 | 7 | 29.2% | — |
| F1 | 35 | 2025-06 | 7 | 20.0% | — |

No single-month domination above 40%. T4 at 40% is borderline but N=5 makes the test weak. October 2025 was a busy month for T3 and S1 firings — probably a market regime where many directors bought at the same time and prices subsequently fell. Worth flagging on the diagnostics chart but not a deprecation trigger by itself.

### Small-N caveats (preliminary — wait for more data, footnote on tile)

Every signal except S1 (N=24) and F1 (N=35) has **N<20 at T+90**:
- T0, T1: N=2
- T2: N=2
- T3: N=14
- T4: N=5

Tile footnote required for all of these: `<span class="text-amber-600 text-[10px]">N<20 — preliminary, wait for more firings before acting on numeric edge.</span>`

### Base-rate sanity check

`signals.json` reports T+90 base rate = 100%. This is mechanical from the trailing window being a near-uniform up year; not a real probability. Show the number but caption the scoreboard footer with: `"Base rate at T+90 is artifactually high due to trailing market regime — interpret hit-% comparison cautiously."` This is the single most important caveat for someone reading the dashboard cold.

T+1 base = 54.8% and T+21 base = 73.6% are more plausible; comparisons there are more reliable.

## 2.4 Cohort observations (only where N supports)

- **AIM split:** Zero AIM-flagged firings in the backtest at T+90. `is_aim` is `0` for every matured row. Either the AIM flag isn't being populated by the exporter (Stage 4.6 candidate gap to verify) or no AIM tickers have reached T+90 yet in the loaded set. **Action: verify `is_aim` upstream — likely a bug.**
- **Role-class within T3 at T+90:** "Non-Executive Director" (N=4) mean +7.22% vs "Non-executive Director" (lowercase 'e', N=4) mean −21.33%. This is a director-name/role normalisation issue, not a signal — same role spelled two ways. Confirms the open hygiene flag in `stage-05-1-company-page.md`. Don't read it as a real cohort effect.
- **Volume by signal:** F1 has the most firings (N=60 at T+1), driven by the lifetime-of-data first-buy nature. S1 (N=44) is next. T1/T2/T0 are sparse (N=3 each); the dashboard's executive-buy tiers depend heavily on the inflow of high-£ exec buys, which has been thin in this 12-month window.

---

# Section 3 — Open visual questions for the front-end engineer

These are questions the designer can't fully resolve without seeing a render at the target screen size. Front-end is welcome to override with a screenshot + rationale.

1. **Scoreboard tile density on 1080p.** Recommendation: 9 columns at 8/8/13/11/11/10/14/10/15 percent widths means roughly 100-140px per column on a 1280px content width. If labels truncate, drop the "Trades" column header to just "N" and use icon-only for "Deprecate" (a small trash glyph). Don't shrink the badge cell.

2. **Sparkline 60×20 — is it visible enough?** On a HiDPI screen the 60×20 is fine; on a 1080p non-HiDPI the polyline at stroke-width 1.5 can blur. Recommendation: bump stroke-width to 2 if you see blur in QA. Don't enlarge the SVG — width is load-bearing for layout.

3. **Brewing-cluster threshold rendering.** The brewing tab will be empty for most weeks (the data shows almost all clusters have `last_buy_date == today`). Recommendation: show the empty state copy "No brewing clusters right now. New clusters fresh-firing today are in the Active tab." rather than redirecting back to Active automatically. Keep state predictable.

4. **Diagnostics chart legend on narrow viewports.** 8 series + dashed FTSE-A-S won't fit in a single row below 1100px. Recommendation: wrap to two rows, gap-y-1. If FE sees overflow, switch legend to two columns rather than a horizontal scroll.

5. **Status pill colour for `deprecated` vs `gated`.** Both currently render slate. Recommendation: leave them identical visually — the deprecate button itself is the only place a user can distinguish "blocked by upstream" (gated) from "killed by me" (deprecated). If a real distinction is needed, FE may add a small strikethrough on the badge for deprecated rows.

6. **Mobile.** Out of scope per Rupert. If FE wants to add `md:` breakpoints opportunistically (e.g. stacking the index 8/4 split on narrow viewports), that's fine — but no time should be spent on mobile-specific layouts in v1.

7. **Build sha resolution.** Front-end can either bake the sha into a `<meta>` tag at generation time (preferred, dynamic) or hardcode `local` if no git repo (per CLAUDE.md, this codebase is local-only). Recommendation: try `git rev-parse --short HEAD` in the exporter; fall back to `local`.

---

# Section 4 — Out of scope (explicit, per agent role)

Hard list of things this spec does NOT cover. Don't build them in v1:

- Auto-refresh / WebSocket / polling — manual page reload only.
- Mobile-specific layout — desktop-first.
- Dark mode toggle — light mode only in v1.
- Filter chips on today's table — explicitly out per Rupert ("less is more — sort by severity").
- Conviction-weighted sizing — flat £1k reference data only in v1; toggle ships v1.1.
- Sell signals — buys only.
- Alerts (push / email / Slack) — never in v1, possibly v2.
- Custom HTML tooltips with bold-name first line — v1 ships native `title=`; v1.x upgrade.
- Mobile bottom-sheet on tap — deferred with mobile.
- AI-generated commentary on per-ticker pages — out of v1.
- Director-level cross-link pages — separate concept, out.
- Trade-entry / portfolio-simulation UI — research tool, not a brokerage.
- Comparable-company side-by-side — out.
- Cluster-window shaded background on the price chart — visual-noise risk; revisit v1.1.
- Director-name normalisation in the UI — backend hygiene issue; out of dashboard scope.
- The Flask sidecar (`dashboard.api`) is referenced for the deprecate POST but is its own deliverable; this spec defines only the front-end contract.

---

## End of spec
