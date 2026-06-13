# Performance page — cohort redesign + drill-downs (v1)

**Author:** dashboard-designer (Claude)
**Date:** 2026-05-18 (amended v1.2 same day — QA review findings applied + three Rupert decisions locked)
**Status:** spec for front-end / back-end implementation
**Inputs honoured:** Rupert's locked scope decisions A–E (see brief 2026-05-18) plus same-day amendment to add bucket and role drill-down pages with top 10 / bottom 10 firings panels.
**Amendment v1.2 (post-QA, 2026-05-18):** status-pill rule locked to ±50% only (§2.2); kill mechanism suspended entirely v1 (see Model Assessment); regex precedence locked (§5.4); accessibility minima added (§7); benchmark scalar vs per-firing clarified (§5.3); outlier_flag deferred to v1.1; bucket+role pages share the "fewer than 10 losers" edge-case treatment (§2.3).
**Surface affected:** `dashboard/performance.html` (cohort section only; rest of page untouched) + **three** new pages `performance-sector.html`, `performance-bucket.html`, `performance-role.html`. The per-ticker drill-down reuses the existing company page at `outputs/companies/{TICKER}.html` — no new page needed.

---

## 0. Summary of what's changing

The current Performance page ships two cohort tiles (transaction value, sector) that have known correctness and UX defects:

| Defect | Today | Fix |
|---|---|---|
| Value tile silently filtered to T1+T2 → £1–25k always null | Misleading title | Re-title, add N + Hit %, allow all-tier toggle later (out of scope v1) |
| Value tile shows median only — no N, no hit % | Half a picture | Three numeric columns: N, Hit %, Median CAR |
| Sector tile hard-coded T+21 + 90d window + N≥10 + top-5-by-hit | Survivor bias | Listen to horizon dropdown; per-tile lookback control; show top **and** bottom |
| No drill-through anywhere | Dead end | Rows clickable → three new drill-down pages (bucket / role / sector); each links onward to the existing `companies/{TICKER}.html` |
| No director-role cut | Major analytical gap given the tier taxonomy is role-driven | New third tile + dedicated drill-down page |
| No view of best/worst firings within a cohort | Aggregates hide which trades are doing the work | Shared Top 10 / Bottom 10 firings panel on every drill-down page |

**Out of scope v1** (deliberate, do not retro-fit): inline expansion / drawers, per-company price charts with benchmark overlay (locked decision B — the company page already covers this where relevant), CSV export, mobile design beyond responsive stack, signal-filtered drill-downs ("show me only T1 trades for ABC"), hover sparklines in tables, saved views / URL state beyond `?bucket=`, `?role=`, `?sector=`.

---

## 1. Design spec — `performance.html` cohort section (THREE tiles)

### 1.1 Layout (ASCII wireframe)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  COHORT CUTS                                            (header H2 above tile row, m-6) │
├────────────────────────────┬────────────────────────────┬────────────────────────────────┤
│  By transaction value      │  By director role          │  By sector (top + bottom)      │
│  ──────────────────────    │  ──────────────────────    │  ──────────────────────        │
│  Lookback ▾ 90d            │  Lookback ▾ 90d            │  Lookback ▾ 90d                │
│                            │                            │                                │
│  bucket    N   Hit% Median │  role     N   Hit%  Median │  sector       N  Hit%  Median  │
│  £1-25k   12  41.7%  -1.2% │  CEO/CFO  27  52.6%  +0.1% │  Materials  ▲ 12 100%  +4.1%  │ ← clickable row
│  £25-100k 33  54.5%  +0.4% │  Other ex 71  56.3%  +0.2% │  Technology  10 50.0%  -0.3%  │
│  £100-500k 28 39.3% ⚠-1.9% │  NED     212  51.9%  +0.1% │  Industrials 18 44.4%  -0.7%  │
│  £500k+   18  44.4%  -1.7% │                            │  Utilities ▼ 23 21.7%  -2.1%  │
│                            │                            │  Health Care 10  0.0%  -3.4%  │
│                            │                            │  ……click for full list →      │
│  N=91 / 365d basis         │  N=310 / 365d basis        │  Showing 5 sectors / 73 total │
│  ↘ each row clickable      │  ↘ each row clickable      │  ↘ each row clickable          │
└────────────────────────────┴────────────────────────────┴────────────────────────────────┘
        click → bucket page          click → role page              click → sector page
```

**Grid:** replaces the existing `grid grid-cols-2 gap-4` with `grid grid-cols-1 md:grid-cols-3 gap-4`. Below `md` (768px) tiles stack — three tiles is too narrow to fit side-by-side under that width, even on laptops in split-screen.

**Per-tile structure** (identical skeleton across all three):

1. `h2` tile title (locked palette: `text-xs uppercase tracking-wide text-slate-500`)
2. Top-right inline `<select>` lookback control — `[90d, 6m, 1y, all]` — default 90d
3. Single `<table>`, no chart. **Bar chart is killed v1.** See §1.4 for justification.
4. Footer caption — total N in the table + "365d basis" (or whatever lookback is selected) + "rows clickable" affordance

**Row click target:** entire `<tr>` clickable via `cursor-pointer hover:bg-indigo-50` and a `data-href="performance-{bucket|role|sector}.html?key=..."` attribute that JS reads on click. The renderer sets the href per row; the script wires each `<section data-tile="...">`'s rows to its destination.

**Click-through is telegraphed by:**
- `cursor-pointer` on every data row
- subtle right-aligned `›` chevron on hover (`absolute right-2 opacity-0 group-hover:opacity-60`)
- hover background `bg-indigo-50`
- caption strip at the bottom: "↘ each row clickable"

### 1.2 N-band visual treatment (must be distinct from "no data" and "real zero")

| State | Visual | Rendered |
|---|---|---|
| **N = 0** (bucket exists, no firings) | gray row, em-dashes throughout, italic "no firings" sub-text | `text-slate-300 italic` |
| **N < 20** (data present but preliminary) | normal text + amber `⚠` glyph next to N + tooltip "N<20 — preliminary, wait for more firings" | `text-amber-600` on the `⚠` only |
| **N ≥ 20** (data trustworthy) | normal text, no decoration | default `text-slate-700` |
| **bucket key not present in JSON** | omit row entirely (do not render a "—" row for buckets the back-end didn't emit) | n/a |

This mirrors the per-signal scoreboard's existing `<20 → ⚠` convention so users only have to learn one rule across the page. The italic-zero treatment is new but matches the design language at the per-signal scoreboard's footnote ("N too small").

### 1.3 Sector tile: "top + bottom", not "top 5 by hit%"

Default ranking: **highest hit%** descending, but show **top 3 + bottom 2** by hit% (with N≥10 to make either end), separated by a thin slate divider row labelled "…lowest performers below":

```
Materials   12  100%  +4.1%  ▲ top
Technology  10   50%  -0.3%
Industrials 18   44%  -0.7%
────────── ────── ────────── ── lowest performers ─────
Utilities   23   22%  -2.1%
Health Care 10    0%  -3.4%  ▼ bottom
```

Reason: showing only winners is the dishonest cut. Bottom-two surfaces sectors to *avoid*, which is half the alpha.

### 1.4 Why no bar chart per tile (judgment call)

Today's value tile uses a bar chart + table. With four buckets and N-amount information now baked into the table, the bar adds zero information density — the table already colour-codes mean CAR via Tailwind text classes (emerald/rose/slate) which is functionally a "row-shaped bar". A dedicated chart canvas costs ~140 lines of Chart.js init per tile × 3 tiles for marginal benefit. **Decision: tables only, colour-coded numerics, no Chart.js in the cohort section.** This also removes a class of "horizon-change race condition" bugs we'd otherwise pick up (the bar would have to listen to the lookback dropdown + the page horizon dropdown both).

The diagnostics chart higher up the page still uses Chart.js — that one earns its keep with 8 overlapping lines, which a table can't replicate.

### 1.5 Tailwind classes (key bits)

```html
<!-- Tile shell (× 3, identical skeleton — data-tile drives the click destination) -->
<section class="bg-white border border-slate-200 rounded-lg p-4" data-tile="role">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-xs uppercase tracking-wide text-slate-500">By director role</h2>
    <select class="lookback-select text-[11px] border border-slate-300 rounded px-1.5 py-0.5
                   bg-white tabular-nums text-slate-600">
      <option value="90d" selected>90 d</option>
      <option value="6m">6 m</option>
      <option value="1y">1 y</option>
      <option value="all">all</option>
    </select>
  </div>

  <!-- Table — same shape for all three tiles -->
  <table class="w-full text-xs tabular-nums">
    <thead class="text-slate-500 uppercase tracking-wide text-[10px] border-b border-slate-200">
      <tr>
        <th class="text-left px-2 py-1.5 font-medium w-[40%]">Role</th>
        <th class="text-right px-2 py-1.5 font-medium w-[15%]">N</th>
        <th class="text-right px-2 py-1.5 font-medium w-[20%]">Hit %</th>
        <th class="text-right px-2 py-1.5 font-medium w-[25%]">Median CAR</th>
      </tr>
    </thead>
    <tbody>
      <tr class="group border-t border-slate-100 cursor-pointer hover:bg-indigo-50 relative"
          data-href="performance-role.html?role=ceo_cfo">
        <td class="px-2 py-2 text-slate-700 truncate">CEO / CFO</td>
        <td class="px-2 py-2 text-right">27</td>
        <td class="px-2 py-2 text-right text-emerald-600">52.6%</td>
        <td class="px-2 py-2 text-right text-emerald-600">+0.1%
          <span class="absolute right-2 top-1/2 -translate-y-1/2 opacity-0
                       group-hover:opacity-60 text-slate-400">›</span>
        </td>
      </tr>
    </tbody>
  </table>

  <p class="text-[10px] text-slate-400 mt-2">N=310 over 365 d · ↘ rows clickable</p>
</section>
```

**Hit % colour rule** (table cell, locked palette):
- `text-emerald-600` if `hit_pct >= base_rate`
- `text-rose-600`    if `hit_pct < base_rate * 0.85`
- `text-slate-700`   otherwise

**Median CAR colour rule** (already in the codebase as `h.car_cell`):
- `text-emerald-600` if `> 0.05`
- `text-rose-600`    if `< -0.05`
- `text-slate-500`   otherwise (and `text-slate-300` if null)

**Lookback dropdown:** dispatches a `lookbackChange` custom event scoped by `data-tile`. The renderer hooks each tile's lookback to refetch from the JSON sub-object keyed `{ "90d": {...}, "6m": {...}, "1y": {...}, "all": {...} }` — no network round-trip; the exporter pre-computes all four lookbacks per tile.

---

## 2. Drill-down pages — shared structure

All three drill-down pages (`performance-bucket.html`, `performance-role.html`, `performance-sector.html`) follow the **same** template. They differ only in (a) the page title + breadcrumb leaf, (b) the filter applied to the firings + ticker rollup, and (c) the small contextual stats. By sharing the template we keep the page-design and template-helper code paths single.

### 2.1 Shared page anatomy

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Today  ›  Performance  ›  {cohort name}                            Horizon ▾ T+21        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  {COHORT NAME}                                                      Lookback ▾ 90d       │
│  ──────────────────────                                                                  │
│  N firings · K distinct tickers · Hit % · Median CAR · Benchmark (sector or FTSE)        │
│  [status pill: "Top sector this period" / "Underperforming bucket" / etc]                │
│                                                                                          │
│  ┌── Top 10 firings ─────────────────┐  ┌── Bottom 10 firings ─────────────────┐         │
│  │ Date  Ticker  Director  Tier  £  CAR │  │ Date  Ticker  Director  Tier  £  CAR │         │
│  │ ...                                  │  │ ...                                   │         │
│  └──────────────────────────────────────┘  └──────────────────────────────────────┘         │
│                                                                                          │
│  ┌── All tickers in this {cohort} ─────────────────────────────────────────────────┐    │
│  │ Ticker  Company  N  Hit%  Mean CAR  Latest firing                                │    │
│  │ ...                                                                              │    │
│  └──────────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Page-header context strip — variant per page

| Page | h1 | Stats line includes | Status pill |
|---|---|---|---|
| Bucket | `Transaction size: £{label}` + sub-line "T1 + T2 buys only" | N, K tickers, Hit %, Median CAR, FTSE A-S benchmark | per §2.2 single rule below |
| Role | `Director role: {label}` + sub-line "CEO/CFO" / "Non-executive directors" etc | N, K tickers, Hit %, Median CAR, FTSE A-S benchmark | per §2.2 single rule below |
| Sector | `{Sector name}` | N, K tickers, Hit %, Median CAR, **sector-specific benchmark** | per §2.2 single rule below |

**Status pill — single locked rule (Rupert 2026-05-18):**

The pill is shown **only** when the cohort's hit % sits **outside ±50% of the FTSE A-S base rate**, otherwise hidden. There is no "at benchmark" / "above benchmark" middle band — that creates pill-clutter for unremarkable cohorts.

- Hit % ≥ base_rate × 1.5 → green pill, copy varies by page: "Top sector this period" / "Top bucket" / "Strong cohort"
- Hit % ≤ base_rate × 0.5 → red pill, copy varies by page: "Bottom sector this period" / "Underperforming bucket" / "Weak cohort"
- Otherwise → no pill rendered

Per-page copy table for the pill (replaces the implicit copy in the original table):

| Page | Green-pill copy | Red-pill copy |
|---|---|---|
| Bucket | "Top bucket" | "Underperforming bucket" |
| Role | "Strong role cohort" | "Underperforming role cohort" |
| Sector | "Top sector this period" | "Bottom sector this period" |

### 2.3 Shared Top 10 / Bottom 10 firings panel

This is the centrepiece of every drill-down page. Two side-by-side panels on `lg`+ viewports (`grid-cols-2`), stacked on narrow.

**Layout** (one of two — they're mirror images, identical columns):

```
┌── ↑ TOP 10 FIRINGS — best CAR @ T+21 ──────────────────────────┐
│  Date     Ticker   Director         Tier      Value     CAR   │
│  14 May   AAL      D. Wanblad       [T1]      £312k    +8.2%  │ ← clickable → companies/AAL.html
│  22 Apr   AZN      P. Soriot        [T1]      £485k    +6.4%  │
│  ...                                                          │
└───────────────────────────────────────────────────────────────┘
```

**Columns** (six cells, all rows):
1. Date (DD MMM YY, slate-700)
2. Ticker (font-mono, slate-700) — the value the click handler reads
3. Director (truncated to first-initial + surname if >18 chars, slate-700)
4. Signal tier badge (T0/T1/T2/T3/T4/S1/F1 — locked palette via `renderBadge()`)
5. Value GBP (right-aligned, abbreviated `£312k` not `£312,400` — saves a column)
6. CAR @ current page horizon — colour-coded (emerald `>5bp`, rose `<-5bp`, slate otherwise), with hover-chevron

**Click target:** entire row → `companies/{TICKER}.html` (the existing per-company page). Wired by an auto-script that reads the font-mono cell.

**Sort:** locked. Top panel sorts by CAR descending, bottom panel by CAR ascending. No user sort toggle on these panels (sort is the panel's purpose).

**Header palette:**
- Top panel: `bg-emerald-50` header strip, `border-emerald-200`, title in `text-emerald-700`, hover `bg-emerald-50` on rows
- Bottom panel: `bg-rose-50` header strip, `border-rose-200`, title in `text-rose-700`, hover `bg-rose-50` on rows

**Edge case — applies to all three drill-down pages (sector / bucket / role):** if there are fewer than 10 losing firings (e.g. Materials sector has only 2 negative trades, or a role page in a thin sub-period has < 10 negatives), the bottom panel renders what's available and shows a small italic note: *"Only N of M {cohort} firings had negative CAR in this period — this is why the cohort tile shows a high hit rate."* This is a deliberate teaching moment: it confirms the cohort tile's optimistic number rather than hiding the explanation. Same template for the symmetric case (top panel with < 10 positives — e.g. a very-bad bucket).

### 2.4 Shared "All tickers" rollup table

Below the top/bottom panels. Same as the current sector mockup's main table:

| Ticker | Company | N | Hit % | Mean CAR | Latest firing |
|---|---|---|---|---|---|

- Tickers with N≥3 firings shown first, sorted by hit % descending by default
- Tickers with N<3 shown below a dashed-border divider, italic / faded (`text-slate-500 italic`)
- Sort UI: four clickable column headers (Hit %, N, Mean CAR, Latest), arrow on the active column
- Row click → `companies/{TICKER}.html` (same handler as the firings panels)

### 2.5 Shared Tailwind snippets (key bits)

```html
<!-- Top/bottom panel header -->
<section class="bg-white border border-emerald-200 rounded-lg overflow-hidden">
  <h2 class="px-4 py-3 text-xs uppercase tracking-wide text-emerald-700 border-b
             border-emerald-100 bg-emerald-50 flex items-center justify-between">
    <span><i class="ti ti-trending-up"></i> Top 10 firings — best CAR @ T+21</span>
    <span class="text-[10px] text-emerald-600 italic">winners</span>
  </h2>
  ...
</section>

<!-- Firing row (six cells, ticker is the click key) -->
<tr class="clickable border-t border-slate-100 cursor-pointer hover:bg-emerald-50">
  <td class="px-2 py-1.5 text-slate-700">14 May 26</td>
  <td class="px-2 py-1.5 font-mono text-slate-700">AAL</td>
  <td class="px-2 py-1.5 text-slate-700">D. Wanblad</td>
  <td class="px-2 py-1.5"><span class="badge" style="background:#ef4444">T1</span></td>
  <td class="px-2 py-1.5 text-right">£312k</td>
  <td class="px-2 py-1.5 text-right text-emerald-600 font-medium">+8.2%<span class="chev">›</span></td>
</tr>

<!-- Auto-wire script (one block per page) — includes keyboard accessibility -->
<script>
document.querySelectorAll('tr.clickable').forEach(tr => {
  const tickerCell = tr.querySelector('td.font-mono');
  if (!tickerCell) return;
  const ticker = tickerCell.textContent.trim();
  const go = () => { window.location.href = 'companies/' + ticker + '.html'; };
  // Keyboard accessibility: rows are focusable and respond to Enter / Space
  tr.setAttribute('tabindex', '0');
  tr.setAttribute('role', 'link');
  tr.setAttribute('aria-label', 'View ' + ticker + ' company page');
  tr.addEventListener('click', go);
  tr.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
  });
});
</script>
```

**Accessibility minima (locked v1):** every `tr.clickable` row across the dashboard must carry `tabindex="0"`, `role="link"`, an `aria-label`, and respond to both Enter and Space keys. The renderer emits these attributes server-side (so the markup is correct before JS runs), and the auto-wire script is the fallback. Same pattern applies to the cohort-tile rows on `performance.html`. Cost: ≈ 6 lines of HTML attributes per renderer.

### 2.6 Breadcrumb pattern (shared)

Sticky top header (matches existing `performance.html` `<header class="border-b border-slate-200 bg-white px-6 h-12 ...">`), with a 3-level breadcrumb on the left:

```html
<nav class="flex items-center gap-2 text-xs">
  <a href="index.html" class="text-indigo-600 hover:underline">Today</a>
  <span class="text-slate-300">›</span>
  <a href="performance.html" class="text-indigo-600 hover:underline">Performance</a>
  <span class="text-slate-300">›</span>
  <span class="text-slate-700 font-medium">{cohort leaf}</span>   <!-- the current page, not a link -->
</nav>
```

Cohort leaf text per page:
- Bucket page: `Transaction size: £{label}` (e.g. `Transaction size: £100–500k`)
- Role page: `Director role: {label}` (e.g. `Director role: NED`)
- Sector page: `{sector name}` (e.g. `Materials`)

**The page-header horizon dropdown stays on the right** (per locked decision E). Lookback per-page sits in the page-header section below.

### 2.7 Chart.js — none on these pages

Deliberate: each drill-down page is text-and-table heavy and reads faster without a chart. The colour-coded CAR cells already function as visual rails. The shared template uses zero Chart.js — saves load time and removes another horizon-change race condition.

---

## 3. Per-page variants — what's filter-driven

This is the only material difference between the three drill-down pages.

**All three pages use the §2.2 single status-pill rule (±50%).** Per-page details below cover only what varies.

### 3.1 `performance-bucket.html`

- URL: `?bucket={1k-25k|25k-100k|100k-500k|500k+}` (snake-case bucket keys, URL-friendly)
- Filter applied to firings + ticker rollup: only T1 + T2 buys in the requested value bucket
- Page sub-line under the H1: *"T1 + T2 buys only"* (T1 = CEO/CFO ≥£100k, T2 = exec ≥£25k)
- Bucket-key → display-label mapping (used in breadcrumb and h1):
  - `1k-25k`   → `£1–25k`
  - `25k-100k` → `£25–100k`
  - `100k-500k` → `£100–500k`
  - `500k+`   → `£500k+`

### 3.2 `performance-role.html`

- URL: `?role={ceo_cfo|other_exec|ned}` (snake-case keys)
- Filter applied: all firings whose director role matches the bucket per the mapping rule in §5.4
- Page sub-line under the H1: "CEO/CFO" → *"Chief Executive Officers and Chief Financial Officers"*; "Other exec" → *"Chair, group executive, COO, CTO, divisional director"*; "NED" → *"Non-executive directors"*
- Role-key → display-label mapping (used in breadcrumb and h1):
  - `ceo_cfo`    → `CEO / CFO`
  - `other_exec` → `Other exec`
  - `ned`        → `NED`
- **Tier column is included** in the top/bottom 10 firings tables (decision Rupert 2026-05-18 — consistency with bucket + sector pages). In practice ≥90% of NED rows will show T3, but the column stays for visual parity.

### 3.3 `performance-sector.html`

- URL: `?sector={Materials|Technology|...}` (sector strings as they appear in `tickers_meta.sector` — URL-encoded)
- Filter applied: all firings on tickers whose `tickers_meta.sector` matches
- Page sub-line under the H1: none
- Benchmark column on firings + rollup uses the **sector-specific benchmark** (via `tickers_meta.benchmark_symbol`, FTSE A-S fallback)

---

## 4. Per-ticker drill-down — uses the existing company page

**No new page is built for the per-ticker drill-down.** Every clickable ticker in any drill-down page navigates to `outputs/companies/{TICKER}.html` — the per-company page already generated by `.scripts/gen_company_pages.py` (Stage 5 Sprint 4).

This decision (taken 2026-05-18 by Rupert) means:
- No `performance-ticker.html` design or implementation work
- No `performance_ticker.json` payload (the per-ticker data already flows through `gen_company_pages.py` from the DB directly)
- Consistent navigation experience across the dashboard — every "drill into a company" path lands on the same page
- A small UX gap: the existing company page's breadcrumb is `Today › {ticker}`, not `Today › Performance › Materials › {ticker}`. We can either (a) leave it inconsistent for v1, or (b) detect the referrer and adapt the breadcrumb client-side. **Recommendation: leave it for v1**, revisit if Rupert finds it jarring after a week of use.

---

## 5. Data spec — exact JSON keys the exporter must add

The exporter (`.scripts/export_dashboard_json.py`) needs three new top-level keys under `cohorts`, plus **three** new aggregated files for the drill-down pages. **No schema change to the DB itself** — everything is derivable from `transactions` + `tickers_meta` + `_backtest_results.csv`.

### 5.1 Cohort tile shape (new) — `signals.json`

Replaces the current `cohorts.by_value_bucket` and `cohorts.by_sector` shapes.

```jsonc
"cohorts": {
  "by_value_bucket": {
    "t21": {
      "90d": {
        "rows": [
          { "key": "1k-25k",    "label": "£1–25k",    "n": 12, "hit_pct": 41.7, "median_car": -1.2 },
          { "key": "25k-100k",  "label": "£25–100k",  "n": 33, "hit_pct": 54.5, "median_car":  0.4 },
          { "key": "100k-500k", "label": "£100–500k", "n": 28, "hit_pct": 39.3, "median_car": -1.9, "outlier_flag": true },
          { "key": "500k+",     "label": "£500k+",    "n": 18, "hit_pct": 44.4, "median_car": -1.7 }
        ],
        "total_n": 91
      },
      "6m":  { "rows": [...], "total_n": ... },
      "1y":  { "rows": [...], "total_n": ... },
      "all": { "rows": [...], "total_n": ... }
    },
    "t1": {...}, "t90": {...}, "t252": {...}
  },
  "by_role":   { "t1": {...}, "t21": {...}, "t90": {...}, "t252": {...} },
  "by_sector": { "t1": {...}, "t21": {...}, "t90": {...}, "t252": {...} }
}
```

Each `txx` value is the `{ "90d": {...}, "6m": {...}, "1y": {...}, "all": {...} }` shape. Adds a `len(HORIZONS) * 4 = 16` multiplier to each cohort sub-payload. Even with 20 rows each, that's ~960 small objects — JSON payload bloat is negligible (< 100 KB total).

### 5.2 Drill-down page payloads — three new files

One file per cohort type, keyed by the cohort identity.

#### `dashboard/data/performance_bucket.json`

```jsonc
{
  "generated_at": "2026-05-18T14:00:21Z",
  "schema_version": "1.0",
  "buckets": {
    "100k-500k": {
      "label": "£100–500k",
      "scope_note": "T1 + T2 buys only",
      "t21": {
        "90d": {
          "benchmark_car_pct":   1.1,
          "total_firings":      28,
          "distinct_tickers":   21,
          "tickers_with_n3":     8,
          "hit_pct":            39.3,
          "median_car":         -1.9,
          "top_firings":    [ /* up to 10 firings, CAR desc */ ],
          "bottom_firings": [ /* up to 10 firings, CAR asc  */ ],
          "rollup": [
            { "ticker": "AAL", "company": "Anglo American Plc", "n": 3, "hit_pct": 66.7, "mean_car":  5.1, "latest_fire": "2026-05-14" },
            { "ticker": "BT.A","company": "BT Group Plc",        "n": 2, "hit_pct":  0.0, "mean_car": -7.1, "latest_fire": "2026-03-17" }
          ]
        },
        "6m":  {...},
        "1y":  {...},
        "all": {...}
      },
      "t1": {...}, "t90": {...}, "t252": {...}
    },
    "25k-100k": {...},
    "1k-25k":   {...},
    "500k+":    {...}
  }
}
```

#### `dashboard/data/performance_role.json`

Same shape with `roles` as the top-level key:

```jsonc
{
  "roles": {
    "ceo_cfo":    { "label": "CEO / CFO",  "t21": { "90d": {...}, ... }, ... },
    "other_exec": { "label": "Other exec", ... },
    "ned":        { "label": "NED",        ... }
  }
}
```

#### `dashboard/data/performance_sector.json`

Same shape with `sectors` as the top-level key:

```jsonc
{
  "sectors": {
    "Materials":   { "t21": { "90d": {...}, ... }, ... },
    "Technology":  {...},
    ...
  }
}
```

The sector variant additionally carries `benchmark_symbol` per sector in the top-level entry (used for the sector-specific benchmark column).

### 5.3 Firing-row schema — shared across `top_firings`, `bottom_firings`

```jsonc
{
  "date":          "2026-05-14",
  "ticker":        "AAL",
  "company":       "Anglo American Plc",
  "director":      "Duncan Wanblad",
  "role":          "CEO",
  "role_class":    "T1",
  "signal_tier":   "t1",          // for the badge colour — locked palette
  "value_gbp":     312400,
  "car":            8.2           // CAR at the requesting horizon (already filtered server-side)
}
```

**Two fields explicitly dropped from v1 (post-QA decision):**

- `bench_car` (per-firing benchmark) — the cohort-level scalar `benchmark_car_pct` in the parent object (§5.2) is enough. The mockups don't render per-firing benchmark. Re-add to schema if the UI ever shows a "vs same-day index" column.
- `outlier_flag` (per-firing outlier) — undefined glyph in §1.2, defer to v1.1. The cohort-level outlier_flag in `by_value_bucket.rows` stays (drives the ⚠ on bucket median CAR).

The server pre-filters by horizon — the front-end doesn't pick a `car_t21` field, the payload already has the right number. This simplifies client code at the cost of slightly more JSON; an acceptable trade-off given top/bottom-10 lists are tiny (≤10 rows × 4 lookbacks × 4 horizons × N cohorts).

**Caption semantics (clarified post-QA):**
- Bucket / role tile footer caption format: `N={total_n} over {lookback_label} · ↘ rows clickable` (count of firings)
- Sector tile footer caption format: `{shown}/{total} sectors · ↘ rows clickable` (count of sectors, since not every sector clears the N≥10 threshold)
- Drill-down page-header stats line format (all three pages): `{N} firings · {K} distinct tickers · Hit %: {x} · Median CAR @ {horizon}: {y} · Benchmark: {z}`

### 5.4 Role bucket definitions (back-end mapping rule, locked)

**Precedence is part of the rule — apply these in order, first match wins. Do not reorder.** This is non-negotiable because `(?i)\b(Executive)\b` would match "Chief Executive" if the CEO/CFO rule weren't applied first.

```python
def classify_role(role_class: str | None, role_str: str) -> str | None:
    """Returns 'ceo_cfo' | 'other_exec' | 'ned' | None.

    role_class may be empty/None for ~70% of firings (T3/T4/S1/F1
    typically lack role_class in _backtest_results.csv). The regex
    fallback is therefore the main path, not the edge case.
    """
    # Rule 1 — CEO/CFO (checked FIRST)
    if role_class == 'T1':
        return 'ceo_cfo'
    if re.search(r'(?i)\b(CEO|CFO|Chief Executive|Chief Financial)\b', role_str):
        return 'ceo_cfo'
    # Rule 2 — NED (checked BEFORE other_exec to avoid Executive matching NED titles)
    if role_class == 'T3':
        return 'ned'
    if re.search(r'(?i)\b(Non-Executive|NED|Senior Independent)\b', role_str):
        return 'ned'
    # Rule 3 — Other exec (catch-all for any remaining executive role)
    if role_class == 'T2':
        return 'other_exec'
    if re.search(r'(?i)\b(Chair|Group|Executive|COO|CTO)\b', role_str):
        return 'other_exec'
    return None  # T4 / catch-all — excluded from role tile per scope
```

**Critical implementation gate (do not skip):** before merging the backend exporter, run a corpus test on the existing `_backtest_results.csv` that prints a frequency table of `(classify_role result, raw role_str)` and have Rupert review for any wrong-bucketed CEOs / mis-classified Chairs. Past data has surprises — "Chief Executive Officer and Chairman" is a real string that must classify as `ceo_cfo`.

The "all other" / catch-all bucket (T4) is deliberately excluded from the role tile — see §7 out-of-scope. If T4 trade volumes are needed later, add a fourth row, but per Rupert's "CEO/CFO, other exec, NED" scope this is three rows v1.

### 5.5 Why three new aggregated files, not one bundle or per-key files

| Option | Pros | Cons |
|---|---|---|
| Inline everything into `signals.json` | Single fetch | Already 1100+ lines, adds ~200KB more, blocks per-tile lazy load |
| One file per bucket / role / sector (~20 files) | Lazy load per page | Cache-busting nightmare, harder for the exporter to write atomically |
| **Three aggregated files (chosen)** | One fetch per drill-down page type, atomic write per file, cache-busts cleanly when the dashboard refreshes | Marginally larger than per-key files |

Each file at current data volume estimates to 50–150 KB — well within browser-cache friendly size.

---

## 6. Mobile / narrow-window behaviour

The cohort grid changes from `grid-cols-2` to `grid-cols-1 md:grid-cols-3`:

- ≥768 px (`md`): three tiles side-by-side
- < 768 px: tiles stack vertically, full-width each

For drill-down pages:
- The top 10 / bottom 10 firings panels use `grid-cols-1 lg:grid-cols-2` — side-by-side on wide, stacked on narrow
- Inner tables use `overflow-x-auto` wrappers so they horizontally scroll on narrow viewports rather than squashing
- The breadcrumb wraps via `flex-wrap`

Mobile is **not** a v1 priority — Rupert reviews on desktop — but these defaults are cheap.

---

## 7. Out of scope for v1 (locked — do not retro-fit without re-spec)

- A separate `performance-ticker.html` page — drill into a ticker = link to existing `companies/{TICKER}.html` (Rupert's amendment, 2026-05-18)
- Inline expansion / drawers (drill-down is separate pages, locked decision C)
- Per-company price chart with benchmark overlay (locked decision B; the company page may grow this independently)
- CSV / clipboard export of any cohort or drill-down table
- Per-tile signal-filtered cohorts ("show me T1-only by value bucket")
- A "by signal tier" cohort tile — the per-signal scoreboard already covers that
- T4 row in the role tile (only CEO/CFO, other exec, NED per locked scope A)
- URL state for sort order on the rollup tables
- Sparklines inside the cohort table rows
- A "no firings" empty-state full-page illustration for cohorts with no data — render a plain `<p>` for v1
- Cross-tile linked highlight (hovering "CEO/CFO" highlighting CEO firings in the sector tile) — interesting but expensive
- Mobile / tablet-specific layouts beyond the responsive stack
- "Compare two sectors / two buckets / two roles" view — likely v2
- Breadcrumb continuity through to the existing company page — the company page keeps its own simpler breadcrumb in v1

---

## 8. Implementation order suggestion (front-end / back-end split)

**Back-end first** (one PR's worth):

1. Extend `export_dashboard_json.py` to emit the new `cohorts` shape — write a helper `cohort_table(rows, group_fn, horizons, lookbacks)` that does the four-lookback × four-horizon expansion in one pass so the three tiles share code.
2. Add `build_bucket_payload()`, `build_role_payload()`, and `build_sector_payload()` functions. They share most logic — extract a `build_drill_payload(filter_fn, key_fn, scope_note=None)` helper. Each writes its own `performance_*.json` via the existing `_atomic_write_json()` pattern.
3. **Unit-test the role-mapping regex** on the existing transactions corpus — easy to mis-classify a "Chief Operating Officer" if you're sloppy.

**Front-end second:**

4. Refactor `_cohort_value_section` / `_cohort_sector_section` into a single `_cohort_tile(tile_id, title, rows_by_horizon_by_lookback)` helper in `render_performance.py`. Add the third tile via the same helper.
5. Write `render_performance_drilldown.py` — ONE renderer function used by all three pages, parameterised on (cohort_type, title_template, scope_note, status_pill_thresholds). Output: three near-identical HTML files.
6. Add the three new pages to `build_dashboard.py`'s output list.

**Tests:**

7. Re-use `.scripts/test_stage_05.py` pattern. Smoke test: `python -m http.server` from `outputs/`, click through Today → Performance → cohort row → drill page → company page → back via breadcrumb. No data == no crash.
8. Smoke test that **every ticker** in every drill page links to an actual file in `outputs/companies/` (the per-company exporter may have skipped a ticker due to missing prices — those should not appear as clickable rows). Failing rows should render as non-clickable italic with a tooltip "company page not generated".

---

# Model assessment (this invocation)

### Per-signal CAR @ T+90 (live data from current `signals.json` `horizon_aggregates.t90`)

Base rate at T+90 = **98.9%** (huge trailing-bull artefact — see caveat below).

| Tier | N | Hit % | Mean | Median | Edge vs base | Outlier flag | Status |
|---|---:|---:|---:|---:|---:|:---:|---|
| T0 | 40  | 30.0% | −4.2% | −5.0% | −12.9% | no  | **KILL CANDIDATE** |
| T1 | 27  | 22.2% | −8.5% | −7.0% | −14.9% | no  | **KILL CANDIDATE** |
| T2 | 45  | 37.8% | −2.6% | −4.3% | −12.2% | no  | **KILL CANDIDATE** |
| T3 | 133 | 39.8% | +10.5% | −5.7% | −13.6% | YES | WATCH (outlier-dominated mean) |
| T4 | 39  | 28.2% | +5.0% | −3.9% | −11.8% | YES | **KILL CANDIDATE** (median negative) |
| S1 | 276 | 32.2% | −1.0% | −6.3% | −14.2% | YES | **KILL CANDIDATE** |
| F1 | 568 | 32.7% | +1.6% | −7.3% | −15.2% | YES | gated (F1 stays gated while `outlier_flag=true`) |

### Kill candidates (formal — N≥20, mean net CAR<0 at T+90)

Six of the seven tiers are kill candidates by the documented rule, and the only one that escapes (T3) does so on a mean inflated by a single outlier (median is −5.7%, below the −5% threshold).

**This is not a moment to ship a "kill this signal" toast for any one tier — it's a moment to question the entire taxonomy at this horizon.** The T+90 base rate of 98.9% means the FTSE All-Share rose almost monotonically over the lookback; in that regime, *anything but the index* underperforms by ~12–15 pp on average. The "edge" column showing −11.8 to −15.2 across the board is the screaming evidence: every tier's edge is within 3.4 pp of every other tier's edge — the signals are not differentiating, the regime is.

**Recommendation (Rupert locked 2026-05-18, post-QA):** **suspend the deprecate-toast mechanism entirely for v1.** Do not wire automatic kill verdicts on any horizon (not T+90, not T+21) until the dataset spans **2× non-overlapping regime windows** — i.e. at least one bull and one non-bull period of the relevant horizon's length.

Rationale — three reasons stacked:
1. **Regime singleness.** The 12-month backtest covers ~one regime; killing on single-regime data is structurally premature.
2. **The clever-but-wrong T+21 fix.** A v1.1 rule of "median CAR < −2% at T+21" was considered and rejected on the QA's pushback: at N=27 (T1) one outlier moves the median ~3.7pp, so the rule would mis-kill working signals whose fat-right-tail carries the alpha — precisely the F1-style distribution we want to keep. Also: switching the kill horizon to T+21 silently abandons the T+90 cost model (50bps + 0.5% stamp duty was sized for T+90 holds).
3. **The drill-downs are the right intervention now.** Human eyeballs on the top-10 / bottom-10 firings panels per cohort are more reliable at this data depth than any automated rule.

The model-assessment panel on the redesigned page should still display tier numbers, flag outliers, and surface caveats — but the Deprecate button in the per-signal scoreboard remains **manual-only** for v1. The button still works (writes to `signal_status.json` exactly as today); it just isn't triggered by any automatic rule, and no toast nudges a kill verdict.

Spec language for the model-assessment panel header:
> "T+90 base rate is artifactually high (trailing bull). Kill verdicts are suspended pending 2× regime windows of evidence. Use the cohort drill-downs to inspect individual trades."

### Optimism checks

1. **Outlier domination — T3, T4, S1, F1.** In every case the `mean_car` at T+90 is positive while the `median_car` is sharply negative. The mean is being pulled up by a small number of huge winners. **The new bucket / role / sector drill-down pages with top-10 / bottom-10 firings panels are the direct intervention for this**: a human can now eyeball *which* trades are doing it.
2. **Tiny N on T0/T1/T2 at T+90** (40, 27, 45) — borderline preliminary. Today's `N<20 = ⚠` rule passes these, but at 27 firings (T1) we're one bad month from N=24. The cohort tiles should keep the same `<20 ⚠` decoration.
3. **Regime concentration.** T+90 covers ≈4.5 trading months. The current backtest window covers ~12 months of firings. That's roughly 2.5 non-overlapping T+90 windows of evidence per signal — not enough to claim any tier is durably broken across regimes. Explicitly tag drill-down rows with the **base rate** for context (already in the JSON spec at §5.2).
4. **The sector tile's `Materials` 100% hit rate is suspicious.** N=12 + 100% hit + base rate 80.4% means the worst-case Materials trade was zero-or-positive net CAR while the index was up massively. The drill-down's Bottom panel will literally show "only 2 negative firings" — confirming or denying whether this is real strength or a tiny sample fluke. **The drill-down is the optimism-check itself.** Ship it.

### Kill / optimism summary for Rupert

- Don't act on T+90 kill candidates without a regime-normalised metric in hand. The redesigned page should ship with the cohort drill-downs **first** so we can see *why* T+90 looks the way it does, then reconsider the kill rule.
- The role tile's design choice to surface CEO/CFO vs NED is well-aimed: T1 (CEO/CFO buy ≥£100k) has the worst T+90 mean (−8.5%), but T1 is also the signal with the strongest *priors* in the academic literature. Either the priors are wrong, or the cost model is too punitive, or the regime is dominating. The drill-down lets a human eyeball the worst T1 trades and form a view — which is exactly the right intervention at this volume of data.
- **Smallest scope-creep risk to call out:** Rupert may be tempted to add a "compare two sectors" view as soon as he sees the sector page. That's a Performance-page v2 feature — keep it out of v1.
- **Reused company page is the right call.** A bespoke ticker drill-down would have duplicated work and split the user's mental model. Reusing `companies/{TICKER}.html` keeps everything consistent.
