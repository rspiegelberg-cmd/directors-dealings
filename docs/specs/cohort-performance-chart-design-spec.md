# Cohort performance chart — design spec

**Author:** dashboard-designer (Claude)
**Date:** 2026-05-27
**Brief:** `docs/specs/cohort-performance-chart-redesign-brief.md`
**Surface:** `outputs/performance.html` — replaces the trailing-12-month CAR line chart
**Status:** Ready for implementation hand-off (upstream JSON + renderer split per brief s.184)

---

## Design spec — cohort performance chart

### Layout

#### State A — default view (single signal group selected, e.g. T3)

```
+------------------------------------------------------------------------------------+
|  Cumulative net CAR @ T+21 by monthly cohort - inception to date                   |
|  Signal: [T1A] [T1B] [T2] (T3) [T4] [T5] [T6] [T7] [T0] [S1] [F1]   <- pill row    |
|  Header strip:  N=156 since inception   mean +7.9%   hit rate 38%                  |
|------------------------------------------------------------------------------------|
|  +25% |                                                                            |
|       |                                                  |                         |
|  +10% |        |             !                       |   |                         |
|    0% |- - - - * - - - - - - * - - - * - - - - - - - * - * - - dashed zero line -  |
|       |        |          ___|       |       O           |                         |
|  -10% |                    |         |                   |                         |
|       |                              |                                             |
|  -25% +----+----+----+----+----+----+----+----+----+----+----+----+                |
|        Jun  Jul  Aug  Sep  Oct  Nov  Dec  Jan  Feb  Mar  Apr  May                  |
|                                                                                    |
|       Legend:  * filled dot (N>=5)   O open ring (N<5)   ! one-ticker dominance    |
|                |  solid whisker (N>=5)    : dashed whisker (N<5)                   |
+------------------------------------------------------------------------------------+
| N strip (shared x-axis, ~80px tall):                                               |
|   N: 12   18   23    9    14   21   17    3   16   19   14   10                    |
|       []   []   []   []   []   []   []   ::   []   []   []   []                    |
|                              ^red label for N=3 (dashed bar outline)                |
+------------------------------------------------------------------------------------+

+------------------------------------------------------------------------------------+
|  Rolling 6-month hit rate @ T+21 (% beating sector benchmark)                      |
|------------------------------------------------------------------------------------|
|   80% |                                                                            |
|   65% |                       .-.        .-.                                       |
|   50% |- - - - - - - - - - -*   *- - - -*   *- - - - - - - dashed 50% baseline -   |
|   35% |       .-.                                                                  |
|   20% |                                                                            |
|       +----+----+----+----+----+----+----+----+----+----+----+----+                |
|        Jun  Jul  Aug  Sep  Oct  Nov  Dec  Jan  Feb  Mar  Apr  May                  |
+------------------------------------------------------------------------------------+
```

#### State B — hover on a dot

```
                              .-----------------------------------------------.
                              | M-3 (Sep 2025)   T3 NED buy                   |
                              |   N = 9 signals  (low — discount)             |
                              |   mean net CAR @ T+21:  -2.3%                 |
                              |   range: -18.4% .. +12.1%                     |
                              |   hit rate this cohort: 33%                   |
                              |   click to see contributing trades            |
                              `-----------------------------------------------`
                                       v
       0% |- - - * - - - * - - - O - - * - - - * - - - - - dashed zero -
                              ___|
                                |
                                * (highlighted: 6px filled, 1.5px stroke)
                                |
                              (low-N month: open ring + dashed whisker)
```

Tooltip is rendered as an absolutely-positioned `div` (NOT a Chart.js native tooltip — we need richer DOM than Chart.js tooltips give us). Anchored top-right of hovered dot. Native cursor changes to `pointer`.

#### State C — low-N callout visible (the M-2 example from the mockup)

```
              !
              |       <- top-cap of whisker (dashed)
              :
              :
              :
              O      <- open ring (white fill, red-500 stroke)
              :
              :_     <- bottom-cap (dashed)

         N=3 (red label, dashed bar in strip below)
         Sep 2025

  callout banner (toast-style, top-right of chart card, dismissible):
  +------------------------------------------------------------+
  |  !  Sep 2025 has only 3 signals - mean is noisy.            |
  |     One ticker accounts for 78% of cohort weight.           |
  |                                              [dismiss] [x]  |
  +------------------------------------------------------------+
```

Callout is shown for the most recent low-N month only (avoid noise). Renders if `latest_complete_month.n_signals < 5` OR `latest_complete_month.single_ticker_weight > 0.5`.

#### State D — drill-down panel open

```
+========================================================================================+
|  M-3 cohort - Sep 2025 - T3 NED buy                                            [x]    |
|  9 signals  -  mean net CAR @ T+21: -2.3%  -  range -18.4% to +12.1%                 |
|                                                                                       |
|  > 1 ticker drove 78% of the cohort weight (single-ticker dominance flag set)         |
|                                                                                       |
|  +----------+------------------+----------+---------+----------+----------+--------+  |
|  | Ticker v | Director         | Fire date| CAR T+1 | CAR T+21 | CAR T+90 | Net v  |  |
|  +----------+------------------+----------+---------+----------+----------+--------+  |
|  | TIN      | Smith, J (NED)   | 11-Sep   |   +2.1% |   -18.4% |   +94.3% |  -19.5%|  |
|  | EOG      | Patel, R (NED)   | 5-Sep    |   -0.4% |   +12.1% |  +166.6% |  +11.2%|  |
|  | SAGA     | Wright, K (NED)  | 29-Sep   |   +1.0% |   +6.4%  |   +94.3% |  +5.5% |  |
|  | ...                                                                              |  |
|  +----------+------------------+----------+---------+----------+----------+--------+  |
|                                                                                       |
|  Sort default: |Net CAR| descending. Click any column header to re-sort.              |
+========================================================================================+
        ^ overlay backdrop (slate-900/40 with backdrop-blur-sm), click-outside closes
```

### Tailwind classes (key bits)

**Cards (two stacked, separated):**
```html
<!-- Main chart card -->
<section class="bg-white border border-slate-200 rounded-lg shadow-sm p-5 mb-4">
  <header class="flex items-baseline justify-between mb-3">
    <h2 class="text-base font-semibold text-slate-800">
      Cumulative net CAR @ T+21 by monthly cohort
      <span class="ml-1 text-xs font-normal text-slate-400">inception to date</span>
    </h2>
    <span class="text-xs text-slate-500" id="cohort-header-strip">
      N=<b class="text-slate-800">156</b> &middot;
      mean <b class="text-slate-800">+7.9%</b> &middot;
      hit rate <b class="text-slate-800">38%</b>
    </span>
  </header>
  <!-- pill row -->
  <div class="flex flex-wrap gap-1.5 mb-4" id="cohort-group-toggle"><!-- chips --></div>
  <!-- chart canvas wrapper -->
  <div class="relative h-[360px]"><canvas id="cohortMainChart"></canvas></div>
  <!-- N strip -->
  <div class="relative h-[80px] mt-1"><canvas id="cohortNStrip"></canvas></div>
</section>

<!-- Hit-rate card -->
<section class="bg-white border border-slate-200 rounded-lg shadow-sm p-5 mb-4">
  <header class="mb-3">
    <h2 class="text-sm font-semibold text-slate-800">
      Rolling 6-month hit rate @ T+21
      <span class="text-xs font-normal text-slate-400">
        % of signals beating sector benchmark
      </span>
    </h2>
  </header>
  <div class="relative h-[180px]"><canvas id="cohortHitRateChart"></canvas></div>
</section>
```

**Pill chips (re-use existing badge palette):**
```html
<!-- selected -->
<button class="cohort-chip selected px-2 py-0.5 rounded text-xs font-semibold
               bg-emerald-500 text-white ring-2 ring-emerald-300 ring-offset-1">T3</button>
<!-- unselected -->
<button class="cohort-chip px-2 py-0.5 rounded text-xs font-semibold
               bg-emerald-100 text-emerald-700 hover:bg-emerald-200">T3</button>
```
Unselected uses the `*-100/*-700` tonal pair of each tier's colour family
(emerald for T3, amber for T2, rose for T1B, red for T1A, violet for T7, etc.) so
the user always sees the colour identity even when not selected.

**Low-N callout (toast-style):**
```html
<div class="absolute top-3 right-3 max-w-xs bg-amber-50 border border-amber-200
            text-amber-900 text-xs px-3 py-2 rounded shadow-sm flex items-start gap-2">
  <span class="text-amber-600 font-bold">!</span>
  <div class="flex-1">
    Sep 2025 has only 3 signals - mean is noisy.
    One ticker accounts for 78% of cohort weight.
  </div>
  <button class="text-amber-600 hover:text-amber-900" aria-label="dismiss">x</button>
</div>
```

**Drill-down modal backdrop and panel:**
```html
<div id="cohort-drilldown" class="fixed inset-0 z-50 hidden">
  <div class="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
       data-close-on-click></div>
  <div role="dialog" aria-modal="true"
       class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2
              w-[min(1000px,95vw)] max-h-[85vh] overflow-hidden
              bg-white border border-slate-200 rounded-lg shadow-2xl flex flex-col">
    <header class="px-5 py-3 border-b border-slate-200 flex items-start justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-800" id="drillTitle"></h3>
        <p class="text-xs text-slate-500 mt-0.5" id="drillSummary"></p>
        <p class="text-xs text-amber-700 mt-1" id="drillVerdict"></p>
      </div>
      <button class="text-slate-400 hover:text-slate-700" data-close-modal>x</button>
    </header>
    <div class="overflow-auto"><table class="w-full text-xs" id="drillTable">
      <!-- thead/tbody rendered by JS -->
    </table></div>
  </div>
</div>
```

**Tooltip (hover bubble):**
```html
<div id="cohort-tooltip"
     class="hidden absolute z-10 bg-white border border-slate-300 rounded
            shadow-md px-3 py-2 text-xs text-slate-700 pointer-events-none
            min-w-[200px]">
  <div class="font-semibold text-slate-900" id="tt-month"></div>
  <div id="tt-n"></div>
  <div id="tt-mean"></div>
  <div id="tt-range"></div>
  <div id="tt-hitrate"></div>
  <div class="text-slate-400 mt-1 text-[10px]">click to drill down</div>
</div>
```

**Per-tier hex colours (inline for canvas drawing, sourced from `render_helpers.TIER_PALETTE`):**
T0 `#dc2626` &middot; T1A `#ef4444` &middot; T1B `#f43f5e` &middot; T2 `#f59e0b` &middot;
T3 `#10b981` &middot; T4 `#94a3b8` &middot; T5 `#fb923c` &middot; T6 `#cbd5e1` &middot;
T7 `#8b5cf6` &middot; S1 `#3b82f6` &middot; F1 `#a855f7`.

Note: the brief lists T1A as "pink" but the codebase palette has it as `red-500`
with T1B as `rose-500`. Spec follows the codebase (single source of truth =
`render_helpers.TIER_PALETTE`). If the brief's colour is preferred, update
`render_helpers.py` once and both surfaces follow.

### Chart.js shape (if applicable)

**Library recommendation: Chart.js with a custom plugin for whiskers.**

Rationale: Chart.js doesn't ship a min/max whisker out of the box, but writing a 40-line plugin is much cheaper than introducing D3 (steep learning curve, no other use in the project) or Recharts (React, project is plain JS). The project already loads Chart.js for the diagnostics chart on the same page — no new CDN. The plugin draws raw lines + caps directly onto the chart's canvas via the Chart.js plugin lifecycle, layered on top of the standard scatter dots.

#### Main chart — `cohortMainChart`

```js
new Chart(ctx, {
  type: 'scatter',                    // dots; whiskers added by plugin
  data: {
    datasets: [
      {
        label: 'T3 mean',
        data: cohort.months.map(m => ({
          x: m.month_iso,
          y: m.mean_car_t21 * 100,
          // custom props the plugin reads:
          _min:   m.min_car_t21  * 100,
          _max:   m.max_car_t21  * 100,
          _n:     m.n_signals,
          _lowN:  m.n_signals < 5,
          _domTick: m.single_ticker_weight > 0.5,
          _monthIso: m.month_iso,
        })),
        // standard dot styling — overridden per-point by plugin for low-N rings
        pointRadius:        ctx => ctx.raw._lowN ? 3 : 4,
        pointStyle:         ctx => ctx.raw._lowN ? 'circle' : 'circle',
        pointBackgroundColor: ctx => ctx.raw._lowN ? '#ffffff' : '#10b981',
        pointBorderColor:    '#10b981',
        pointBorderWidth:    ctx => ctx.raw._lowN ? 2 : 1,
        // faded straight-segment connector through means
        showLine: true,
        borderColor: 'rgba(16,185,129,0.3)',
        borderWidth: 1,
        tension: 0,                    // STRAIGHT, no smoothing
      },
      // a second dataset is appended per additionally-selected group
      // with horizontal jitter built in upstream (m.month_iso_jitter)
      // and the trailing-3m MA overlay as a third faded line.
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'nearest', intersect: true },
    plugins: {
      legend: { display: false },     // pill row replaces it
      tooltip: { enabled: false },    // we render our own DOM tooltip
      whiskerPlugin: { enabled: true },  // custom — see below
      zeroLine: { enabled: true },    // custom — dashed y=0
      dominanceMarkers: { enabled: true },  // custom — '!' glyph above _max
    },
    onClick: (evt, items) => onCohortClick(evt, items, chart),
    onHover: (evt, items) => onCohortHover(evt, items, chart),
    scales: {
      x: {
        type: 'time',
        time: { unit: 'month', displayFormats: { month: 'MMM yy' } },
        grid: { color: '#f1f5f9' },
        ticks: { color: '#64748b', font: { size: 11 } },
      },
      y: {
        type: 'linear',
        // dynamic per active selection (decision 10)
        suggestedMin: -(yMax * 1.1),
        suggestedMax:   yMax * 1.1,
        grid: { color: '#f1f5f9' },
        ticks: {
          color: '#64748b',
          callback: v => v.toFixed(0) + '%',
        },
      },
    },
  },
  plugins: [whiskerPlugin, zeroLinePlugin, dominanceMarkersPlugin],
});
```

**Custom plugin sketch (40 lines, lives in renderer template):**
```js
const whiskerPlugin = {
  id: 'whiskerPlugin',
  afterDatasetsDraw(chart) {
    const {ctx} = chart;
    chart.data.datasets.forEach((ds, dsIdx) => {
      const meta = chart.getDatasetMeta(dsIdx);
      const stroke = ds.borderColor.replace(/,?\s*[0-9.]+\)$/, ',1)') || ds.pointBorderColor;
      ds.data.forEach((pt, i) => {
        const el = meta.data[i];
        if (!el || pt._min == null || pt._max == null) return;
        const x = el.x;
        const yMin = chart.scales.y.getPixelForValue(pt._min);
        const yMax = chart.scales.y.getPixelForValue(pt._max);
        ctx.save();
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 1;
        if (pt._lowN) ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(x, yMin); ctx.lineTo(x, yMax);                 // whisker shaft
        ctx.moveTo(x - 4, yMin); ctx.lineTo(x + 4, yMin);          // bottom cap
        ctx.moveTo(x - 4, yMax); ctx.lineTo(x + 4, yMax);          // top cap
        ctx.stroke();
        ctx.restore();
      });
    });
  },
};
```

**Click-handler signature (per brief):**
```js
function onCohortClick(evt, items, chart) {
  if (!items.length) return;
  const pt = items[0];
  const ds = chart.data.datasets[pt.datasetIndex];
  const data = ds.data[pt.index];
  openCohortDrilldown({
    signalGroup: ds._signalGroup,   // 't3'
    monthIso:    data._monthIso,    // '2025-09'
    n:           data._n,
    meanCarT21:  data.y / 100,
    minCarT21:   data._min / 100,
    maxCarT21:   data._max / 100,
  });
}
```

`openCohortDrilldown(args)` fetches `cohorts[group][monthIso].signals[]` from the
pre-baked JSON blob, populates the table, sorts default by `Math.abs(net)` desc,
and reveals the modal. Closes on `.data-close-on-click` or `[data-close-modal]`
or `Esc` key.

#### N strip — `cohortNStrip`

```js
new Chart(ctx, {
  type: 'bar',
  data: {
    datasets: [{
      data: cohort.months.map(m => ({x: m.month_iso, y: m.n_signals})),
      backgroundColor: ctx => ctx.raw.y < 5 ? 'transparent' : '#cbd5e1',
      borderColor:     ctx => ctx.raw.y < 5 ? '#f59e0b' : 'transparent',
      borderWidth: ctx => ctx.raw.y < 5 ? 1 : 0,
      borderDash:  ctx => ctx.raw.y < 5 ? [3, 3] : [],
    }],
  },
  options: {
    plugins: {
      legend: { display: false },
      datalabels: {                    // requires chartjs-plugin-datalabels
        anchor: 'end', align: 'top',
        color: ctx => ctx.raw.y < 5 ? '#dc2626' : '#475569',
        font: { size: 10 },
        formatter: v => v.y,
      },
    },
    scales: {
      x: { display: false },           // x already shown on main
      y: { display: false, beginAtZero: true },
    },
  },
});
```

If `chartjs-plugin-datalabels` isn't already in the project, render N values
via a small overlay div instead of pulling in another dep — confirm before
implementation.

#### Hit-rate chart — `cohortHitRateChart`

```js
new Chart(ctx, {
  type: 'line',
  data: {
    datasets: [{
      label: 'Rolling 6m hit rate',
      data: cohort.months.map(m => ({x: m.month_iso, y: m.hit_rate_t21_rolling_6m * 100})),
      borderColor: '#0d9488',          // teal-600, distinct from CAR series
      backgroundColor: 'rgba(13,148,136,0.08)',
      fill: true,
      tension: 0,
      pointRadius: 2,
    }],
  },
  options: {
    plugins: {
      legend: { display: false },
      baseline50: { enabled: true },   // dashed grey line at y=50
    },
    scales: {
      x: { type: 'time', time: { unit: 'month' } },
      y: { min: 0, max: 100, ticks: { callback: v => v + '%' } },
    },
  },
  plugins: [baseline50Plugin],
});
```

### Data needed from JSON

The renderer will read a single new blob, `cohort_performance.json`, with this shape:

```json
{
  "generated_at": "2026-05-27T14:45:43Z",
  "horizon": "t21",
  "signal_groups": ["t1a", "t1b", "t2", "t3", "t4", "t5", "t6", "t7", "t0", "s1", "f1"],
  "groups": {
    "t3": {
      "label": "T3 NED buy",
      "color_hex": "#10b981",
      "header": {
        "n_total_signals": 156,
        "mean_car_t21_overall": 0.079,
        "hit_rate_t21_overall": 0.38
      },
      "months": [
        {
          "month_iso": "2025-06",
          "n_signals": 12,
          "mean_car_t1":  0.004,
          "mean_car_t21": 0.012,
          "mean_car_t90": 0.034,
          "min_car_t21": -0.083,
          "max_car_t21":  0.121,
          "hit_rate_t21": 0.50,
          "hit_rate_t21_rolling_6m": 0.42,
          "single_ticker_weight": 0.18,
          "ma3_mean_car_t21": null,
          "signal_ids": [101, 102, 103, ...]
        },
        ... one per month since inception, ascending
      ]
    },
    ... one entry per signal group
  },
  "cohort_drilldown": {
    "t3": {
      "2025-09": {
        "verdict": "1 ticker drove 78% of the cohort weight",
        "signals": [
          {
            "signal_id": 1042,
            "ticker": "TIN",
            "director": "Smith, J",
            "role_short": "NED",
            "fire_date": "2025-09-11",
            "car_t1": 0.021,
            "car_t21": -0.184,
            "car_t90": 0.943,
            "benchmark_t21": 0.011,
            "net_car_t21": -0.195,
            "cohort_weight": 0.78
          },
          ...
        ]
      },
      ...
    },
    ...
  }
}
```

**Exact JSON keys consumed by the renderer:**

Per `groups[grp].months[]`:
`month_iso`, `n_signals`, `mean_car_t21`, `min_car_t21`, `max_car_t21`,
`hit_rate_t21`, `hit_rate_t21_rolling_6m`, `single_ticker_weight`,
`ma3_mean_car_t21` (3-month trailing MA of mean_car_t21, null for first 2
months), `signal_ids`.

Per `groups[grp].header`:
`n_total_signals`, `mean_car_t21_overall`, `hit_rate_t21_overall`.

Per `cohort_drilldown[grp][month_iso]`:
`verdict` (string, pre-computed by upstream Python), `signals[]` with
`ticker`, `director`, `role_short`, `fire_date`, `car_t1`, `car_t21`,
`car_t90`, `benchmark_t21`, `net_car_t21`, `cohort_weight`.

**Upstream verdict logic (one-liner for `verdict` string):**
```python
def cohort_verdict(signals):
    if not signals: return ""
    weights = [s["cohort_weight"] for s in signals]
    top = max(weights)
    if top > 0.5:
        top_signal = next(s for s in signals if s["cohort_weight"] == top)
        return f"1 ticker ({top_signal['ticker']}) drove {top*100:.0f}% of the cohort weight"
    spread = max(s["net_car_t21"] for s in signals) - min(s["net_car_t21"] for s in signals)
    if spread < 0.05:
        return "Cohort outcomes were broadly consistent."
    return ""
```

**Rolling-6m hit rate computation outline (upstream Python):**
```python
# in export_dashboard_json.py — new function
def rolling_hit_rate(group_rows, window_months=6):
    """For each month-end, compute % of signals fired in trailing window
    whose net_car_t21 > 0 (i.e. beat sector benchmark, since cost-adj already
    applied)."""
    from collections import deque
    by_month = sorted({r["month_iso"] for r in group_rows})
    out = {}
    for m in by_month:
        cutoff_lo = month_minus(m, window_months - 1)  # inclusive window
        window = [r for r in group_rows
                  if cutoff_lo <= r["month_iso"] <= m
                  and r["net_car_t21"] is not None]
        if not window:
            out[m] = None
        else:
            out[m] = sum(1 for r in window if r["net_car_t21"] > 0) / len(window)
    return out
```

### Out of scope

- **Mobile / < 600px breakpoints.** Desktop-first per brief s.165.
- **Cost methodology changes.** Net-of-cost is upstream — designer doesn't touch.
- **Confidence intervals / standard-error UI.** Whiskers replace them.
- **Equity-curve / cumulative-PnL view.** Performance table covers cumulative.
- **Cross-signal correlation.**
- **Animation transitions on group-toggle.** Re-scale snaps instantly per
  decision 10's accepted "rescale disorientation" tradeoff.
- **Persisting selected group in URL or localStorage.** Stateful per page load only.
- **Exporting drill-down table to CSV.** Add as a follow-up brief if asked.
- **chartjs-plugin-datalabels** addition. If not already in project, use an
  overlay div for N labels.
- **A/B feature flag implementation.** Per brief s.190, but flag logic itself
  is an upstream concern, not a design decision.

---

## Model assessment (this invocation)

### Per-signal CAR @ T+21 and T+90 (live data — backtest run `bt_20260527T144543Z`, 2607 rows)

| signal_id              | N (t21) | mean t21 | N (t90) | mean t90  |
|------------------------|--------:|---------:|--------:|----------:|
| f1_first_time_buy      |   1,037 |   -0.18% |     723 |    -1.15% |
| s1_cluster_buy         |     751 |   -2.05% |     529 |    -5.29% |
| t0_cluster_combo       |      31 |   -3.85% |      18 |    -5.17% |
| t1a_ceo_founder_buy    |      30 |   -2.56% |      23 |    -7.94% |
| t1b_cfo_buy            |      15 |   -3.04% |      10 |   -15.49% |
| t2_exec_buy            |      36 |   -4.44% |      21 |    -8.25% |
| **t3_ned_buy**         |   **226** | **+3.56%** | **156** | **+7.90%** |
| t4_other_buy           |      78 |   -0.71% |      51 |    -1.65% |
| t5_pca_buy             |      48 |   +0.74% |      33 |    -9.89% |
| t6_company_sec_buy     |       4 |   -4.50% |       2 |    -5.23% |
| t7_chair_buy           |      55 |   -4.33% |      35 |    -2.14% |

### Kill candidates (N>=20 and mean net CAR @ T+90 < 0)

| signal_id              |   N | mean t90 | severity |
|------------------------|----:|---------:|----------|
| f1_first_time_buy      | 723 |   -1.15% | medium — large N, clearly negative |
| s1_cluster_buy         | 529 |   -5.29% | **high — large N, very negative** |
| t1a_ceo_founder_buy    |  23 |   -7.94% | **high — flagship "top-of-pyramid" signal is losing money** |
| t2_exec_buy            |  21 |   -8.25% | **high** |
| t4_other_buy           |  51 |   -1.65% | medium |
| t5_pca_buy             |  33 |   -9.89% | high |
| t7_chair_buy           |  35 |   -2.14% | medium |

**Seven of the eleven signals with N>=20 are negative at T+90.** Only T3 NED shows
a positive mean — and as the optimism check below shows, that's a single trade.

### Optimism checks

1. **T3 NED is a single-trade illusion.** Mean +7.90% at T+90 across N=156. Top
   trade is **TIN at +1218.9% at T+90** (the lone outlier dominates the sum to
   the tune of 99%). Excluding only that one trade: mean drops to **+0.08%**.
   Excluding top 3 trades: mean drops to **-3.21%**. **Median is -5.01%** —
   the typical NED buy loses money over 90 days. The whiskers + drill-down +
   "1 ticker drove X%" verdict in this new chart will surface exactly this kind
   of distortion. Without them, the performance table currently tells Rupert
   "T3 NED is your best signal." It is not.

2. **Regime concentration risk.** All 2,607 trades fire between **2025-06 and
   2026-05** — twelve months of data, late-cycle UK equities, post-Brexit
   reflation regime. Negative aggregate at T+90 across most signals is consistent
   with this being a tough regime for "buy-the-insider" strategies broadly, not
   necessarily a verdict on the signal design. The chart's "inception to date"
   x-axis is the right framing precisely because as the dataset grows past
   regime boundaries, the picture can change. Right now the x-axis will be
   12 months wide — Rupert needs to expect it to look sparser/noisier than the
   eventual chart.

3. **Small-N caveats (N<20 with N>=5):** T6 (N=2 at T+90), T1B (N=10), T0
   (N=18). These genuinely don't have enough data to assess and the chart must
   make that visible. T1B at **-15.5% mean** would be alarming with N>=30 but
   at N=10 it could easily wash out. The low-N visual treatment in this new
   chart (open ring + dashed whisker + red N label) is doing real work for
   these tiers.

4. **F1 has high N (723) and is near-zero (-1.15%).** This is the most honest
   reading in the dataset — first-time buy doesn't seem to add edge, and the
   sample is large enough to trust. Strong candidate for deprecation after the
   chart goes live and Rupert can see the per-month spread.

---

## Metric correction ruling (2026-05-29)

**Author:** dashboard-designer (Claude)
**Trigger:** Phase 2 real-data spot-check of `cohort_performance.json` found the
locked "contribution to mean" metric `cohort_weight = r_i / (N * mean)` inverts
sign and exceeds 100% whenever the cohort mean is negative — which is *most*
months in this dataset. Live: T3 2026-01 verdict reads "1 ticker (VCP) drove
104% of the cohort weight" when VCP was the **worst** trade at -29.6%; 25 of 98
group-months show a single-ticker "weight" >100% (up to ~700%).
**Scope of this ruling:** the back-end engineer can implement directly from the
formulas, thresholds and literal strings below. This corrects *how the locked
intent is computed and worded* — it does NOT re-open the locked intent itself
(decisions 6 and 9: honestly surface single-ticker contamination / "which ticker
is pulling the line"). The signed formula defeats that intent on negative-mean
months by naming the biggest loser as the "driver" — so it is replaced where it
misleads, and kept (relabelled) only where it is genuinely informative.

### Why the signed formula fails the brief's own intent

`r_i / (N * mean)` is a *contribution decomposition*: each term is the share of
the mean that trade i accounts for, and the terms sum to exactly 100%. That is
mathematically honest as a decomposition. But it is the wrong tool for the two
*flagging* jobs (the `!` marker and the verdict), for three reasons a CEO
scanning for real-money decisions cannot be expected to reverse-engineer:

1. **Sign inversion on negative means.** When `mean < 0`, a trade that is *more*
   negative than the mean gets a *larger positive* share. The single worst loser
   is named as the "driver." This is the exact opposite of the truth the brief
   asked the chart to tell.
2. **Unbounded.** A "share" that reads 104%, 300%, 700% is not a share. It is
   visually alarming and semantically vacuous as a concentration measure.
3. **The `>0.5` threshold means nothing on a quantity that ranges to 7.0.** The
   dominance trigger fires (or fails to) on noise.

The fix is to separate the two jobs from the one display column:

- **Flagging concentration** ("is one ticker dominating?") needs a *bounded,
  sign-stable* magnitude share. Use absolute-magnitude share.
- **Decomposing the mean** ("how is this month's average built up?") is the only
  place the signed contribution is legitimately useful, and only if its label
  makes the signed/can-exceed-100% behaviour explicit.

### Ruling 1 — `single_ticker_weight` (the `!` dominance marker, decision 9)

**Metric:** bounded absolute-magnitude share.

```
abs_share_i = |net_car_t21_i| / Σ_j |net_car_t21_j|
single_ticker_weight = max over tickers of ( Σ abs_share_i for that ticker )
```

Always in [0, 1]. Never inverts. Sums to 100% across tickers by construction.

**Trigger threshold:** **unchanged at `> 0.5`** (one ticker carries more than
half the cohort's total absolute movement). The threshold is now meaningful
because the quantity is a true bounded share. The `!` glyph above the whisker
top, and the State-C low-N callout's "One ticker accounts for X% of cohort
weight" line, both read this field. With a bounded share the callout's wording
is already correct (it never prints >100%).

**Engineer note:** in `build_cohort_performance`, `single_ticker_weight` must be
computed from `abs_share`, NOT from `_cohort_contributions()`'s signed output.
The `by_ticker` summation loop stays; only the per-trade weight it sums changes.

### Ruling 2 — `cohort_verdict` (drill-down footer one-liner)

**Metric:** the same bounded absolute-magnitude share as Ruling 1, AND it must
state **direction** (drag vs contributor) so a -29.6% "driver" is never
described as if it helped.

**Threshold:** dominant ticker `abs_share > 0.5`.
**Direction:** determined by the **sign of that ticker's own net_car_t21**
(its summed net CAR if a ticker has multiple trades in the month): negative =
drag, positive = contributor.

**Literal strings (ASCII-safe — no non-ASCII in any print()):**

- Dominant ticker, negative outcome:
  `"1 ticker ({ticker}) was the largest single drag on the cohort ({share}% of total movement, {netpct}% net CAR)."`
- Dominant ticker, positive outcome:
  `"1 ticker ({ticker}) was the largest single contributor to the cohort ({share}% of total movement, +{netpct}% net CAR)."`
- No dominant ticker, tight spread (existing branch, unchanged trigger
  `max(net) - min(net) < 0.05`):
  `"Cohort outcomes were broadly consistent."`
- Otherwise: empty string `""` (no footer line).

Where `{ticker}` is the symbol, `{share}` = `round(single_ticker_weight*100)`,
`{netpct}` = that ticker's summed `net_car_t21 * 100` to one decimal (the
positive-branch template already carries the leading `+`, so format `{netpct}`
**without** a sign for the negative branch — the word "drag" plus the bare
negative number reads correctly, e.g. "-29.6% net CAR"; format the positive
branch's number with no leading sign too since the template supplies the `+`).

Reference implementation (engineer adapts the existing `cohort_verdict`):

```python
def cohort_verdict(signals):
    if not signals:
        return ""
    # abs-share per ticker (sign-stable, bounded)
    total_abs = sum(abs(s["net_car_t21"]) for s in signals
                    if s.get("net_car_t21") is not None)
    if total_abs == 0:
        return "Cohort outcomes were broadly consistent."
    by_ticker = {}
    for s in signals:
        r = s.get("net_car_t21")
        if r is None:
            continue
        by_ticker.setdefault(s["ticker"], 0.0)
        by_ticker[s["ticker"]] += r          # signed sum -> direction
    abs_by_ticker = {t: 0.0 for t in by_ticker}
    for s in signals:
        r = s.get("net_car_t21")
        if r is None:
            continue
        abs_by_ticker[s["ticker"]] += abs(r)
    top_tkr = max(abs_by_ticker, key=abs_by_ticker.get)
    share = abs_by_ticker[top_tkr] / total_abs
    if share > 0.5:
        net = by_ticker[top_tkr]
        share_pct = round(share * 100)
        net_pct = abs(net) * 100
        if net < 0:
            return (f"1 ticker ({top_tkr}) was the largest single drag on "
                    f"the cohort ({share_pct}% of total movement, "
                    f"-{net_pct:.1f}% net CAR).")
        return (f"1 ticker ({top_tkr}) was the largest single contributor to "
                f"the cohort ({share_pct}% of total movement, "
                f"+{net_pct:.1f}% net CAR).")
    nets = [s["net_car_t21"] for s in signals
            if s.get("net_car_t21") is not None]
    if nets and (max(nets) - min(nets)) < 0.05:
        return "Cohort outcomes were broadly consistent."
    return ""
```

### Ruling 3 — drill-down "Contribution to mean" column (`cohort_weight`)

**Decision: REPLACE the signed `r_i / (N * mean)` with the bounded absolute-share
`|r_i| / Σ|r_j|`, and rename the column.** Do NOT keep signed contribution as the
primary column, and do NOT show both.

**Rationale (the tradeoff, made explicit):**
- The signed decomposition is *technically* a true 100%-summing breakdown, and
  the locked brief chose it deliberately. But the brief's stated *purpose* for
  the column (brief s.137, s.143) is "which ticker is pulling the line" and it is
  the default-descending sort. On negative-mean months the signed column sorts
  the **worst loser to the top and labels it the biggest contributor** — it
  actively answers the question wrongly. A CEO scanning this for real-money
  entries would read the most-negative trade as the cohort's engine. That is the
  one outcome the whole redesign exists to prevent (see this spec's optimism
  check 1 on the TIN illusion). Misleading-by-default outweighs the elegance of a
  signed decomposition.
- Showing both columns was considered and rejected: two near-identical 0–100%
  columns that disagree on sign in a small modal table is *more* confusing for a
  non-technical reader, not less. One unambiguous column wins.
- The absolute-share column preserves the brief's actual intent perfectly: it is
  bounded 0–100%, sums to 100%, sorts the genuinely most-impactful ticker to the
  top regardless of sign, and the existing sort-default ("biggest first") now
  means "biggest mover first" — exactly "which ticker is pulling the line."
  Direction is already legible from the adjacent **Net of costs** column, which
  carries the sign for each row.

**Column header (replaces "Contribution to mean"):**
`Share of cohort movement`

**Tooltip / header help text (literal, ASCII-safe):**
`"Each ticker's share of the cohort's total absolute T+21 net CAR movement (sums to 100%). Bigger = more of the month's result, win or lose. See the Net column for direction."`

**JSON / field note for the engineer:** the field name `cohort_weight` may stay
as-is to avoid a rename across the export, the renderer JS, and tests — its
*meaning* changes to absolute-share. If a rename is cheap, `share_of_movement`
is clearer; otherwise keep `cohort_weight` and rely on the column header +
tooltip above to disambiguate. The drill-down default sort stays **descending
on `cohort_weight`** (now = biggest mover first), per the locked brief.

### Ruling 4 — `COHORT_MEAN_EPSILON` / `abs_fallback`

**Remove it.** Under Rulings 1–3 every surface (marker, verdict, drill-down
column) now uses absolute-magnitude share as the *primary* and *only* metric, so
there is no signed-near-zero-mean blow-up left to guard against. The
`COHORT_MEAN_EPSILON = 0.001` threshold and the dual-basis `_cohort_contributions`
function (and its `contribution_basis` / `"abs_fallback"` flag) were a patch over
the signed formula's instability; with the signed formula gone from these three
surfaces they are dead code. The analyst confirmed the fallback effectively never
fires in live data — that is because the failure mode is sign inversion on
clearly-negative means (|mean| well above epsilon), not near-zero means, so the
epsilon guard was aimed at the wrong failure all along. The single per-trade
weight everywhere becomes `|r_i| / Σ|r_j|` with one guard: if `Σ|r_j| == 0`
(every trade exactly flat), fall back to equal weight `1/N`. Drop
`COHORT_MEAN_EPSILON`, the `basis` return value, the `contribution_basis` field,
and the corresponding test assertions for `"abs_fallback"`.

### Implementation checklist for the engineer

1. `_cohort_contributions(net_cars)` -> return `|r_i| / Σ|r_j|` (equal-weight if
   total abs is 0). Drop the signed branch, `COHORT_MEAN_EPSILON`, and `basis`.
2. `single_ticker_weight` (months loop): compute from the new abs-share output
   (loop structure unchanged).
3. `cohort_verdict`: replace with the directional reference implementation above.
4. Drill-down `cohort_weight` per signal: write the abs-share value.
5. Renderer: rename the column header to **"Share of cohort movement"**, add the
   tooltip string, keep descending-on-`cohort_weight` default sort.
6. Tests: update `test_cohort_performance_export.py` — drop `abs_fallback`
   assertions; add (a) a negative-mean cohort asserting `single_ticker_weight`
   in [0,1] and the verdict contains "drag", (b) a positive-mean cohort whose
   verdict contains "contributor", (c) abs-share sums to ~1.0.
7. No non-ASCII in any `print()` or returned verdict string (CLAUDE.md cp1252
   rule).

---

## Pending-month marker ruling (2026-06-02)

**Author:** dashboard-designer (Claude)
**Confirmed with Rupert:** 2026-06-02 — newest month(s) must be MARKED AS PENDING
and CLICKABLE to reach the underlying transactions.
**Trigger:** The chart plots mean net CAR @ T+21 per calendar month. The newest
month(s) contain signals that have *fired* but whose 21-trading-day window has not
yet elapsed, so `mean_car_t21` is `null`. Today those months render a bar in the
N strip (e.g. 83 signals) but **no dot, no whisker, and no click target** — the
freshest, most actionable trades are invisible and unreachable. This ruling fixes
that without ever implying a 0% return.

**Scope:** this is a *display* ruling for the main chart, its legend, the N strip,
the tooltip, and the drill-down modal. It does not change the cohort maths — a
pending month is simply one where `mean_car_t21 == null` while `n_signals > 0`.
The back-end already emits these months (they carry `n_signals` and `signal_ids`);
the only new upstream need is the drill-down payload for them (see Ruling 4 below).

### Why "a dot at y=0" is forbidden

Placing any normal-looking marker on the zero line falsely asserts "this cohort
returned 0%." A pending month has **no return at all yet** — that is a different
state from "measured at zero." The treatment below deliberately removes the
marker from the y-axis entirely (it carries no y-value) and instead pins a
neutral "waiting" glyph to the **bottom plot edge**, so the eye never reads a
return off it.

### Ruling 1 — the pending-month marker

**Chosen treatment: a small hollow slate diamond pinned to the bottom plot edge,
directly beneath that month's x-position, paired with a faint full-height vertical
"pending" band behind it.** No dot, no whisker, no y-position on the data scale.

Exact treatment:

- **Glyph:** an open (unfilled) diamond (rotated square), `rotation 45deg`.
  - Size: 9px bounding box (~6px stroke-to-stroke), visually a touch smaller than
    a real 4px-radius dot's footprint so it never competes for "this is a value."
  - Stroke: `#94a3b8` (slate-400), `1.5px`. Fill: `#ffffff` (white) — i.e. hollow.
  - Position: centred on the month's x, with its **centre fixed 12px above the
    x-axis baseline** (bottom plot edge), NOT on the data scale. It does not move
    when the y-axis rescales. This is the single most important rule: the diamond
    lives in pixel space at the chart floor, so it can never be read as a return.
- **Pending band:** a faint vertical band spanning the full plot height at that
  month's x-slot.
  - Fill: `rgba(148,163,184,0.06)` (slate-400 at 6% alpha) — barely-there, just
    enough to say "this column is special / in progress."
  - Width: the month's category band width (or +/- ~10px around the x-tick on a
    time scale). Drawn `beforeDatasetsDraw` so it sits behind everything.
  - Optional 1px left+right edges at `rgba(148,163,184,0.18)` if the flat band
    reads as too weak in testing — band first, edges only if needed.

**Why this option over the alternatives weighed:**

- *vs. a clock / hourglass glyph:* a literal clock is cute but (a) at 9px it
  renders as mud on a canvas, (b) it imports an icon dependency or a fiddly path,
  and (c) the project's whole visual language is geometric (dot / ring / `!`),
  so a pictogram would be a tonal outlier. A diamond is drawable with the same
  canvas primitives already used for the ring and reads instantly as "a marker,
  but a different *kind* of marker."
- *vs. tinting the N-strip bar alone as the only affordance:* rejected as the
  *sole* signal because the N strip is secondary chrome — a user scanning the
  main plot for "what's new" would miss it. We DO also tint the N-strip bar (see
  Ruling 4) as a reinforcing cue, but the floor diamond + band is the primary
  on-plot marker so the pending month is legible in the main chart itself.
- *vs. a band with no glyph:* a band alone has nothing to click precisely and no
  legend-able symbol. The diamond gives a crisp hit target and a legend entry.

**Distinctness check (must read as none of the existing four states):**

| State | Marker | Y-position | Colour | Fill |
|-------|--------|-----------|--------|------|
| Matured, N>=5 | round dot | on data scale | tier colour | filled |
| Matured, low-N | round ring | on data scale | tier colour | hollow white |
| True gap (0 signals) | none | -- | -- | -- |
| Dominance | `!` glyph | above whisker top | tier colour | -- |
| **Pending (this ruling)** | **diamond** | **pinned to floor (pixel space)** | **slate-400** | **hollow white** |

The pending diamond differs from the low-N ring on **three** axes at once
(shape: diamond vs circle; position: floor vs data-scale; colour: slate-grey vs
tier colour), so the two cannot be confused. Slate-grey, not any tier colour, is
deliberate: grey says "no return yet / not yet part of the performance story,"
consistent with the N-strip greys.

**Canvas plugin note (engineer):** extend the existing custom-plugin pattern.
Add a `pendingMarkerPlugin` that, in `beforeDatasetsDraw`, paints the band for
each point where `_pending === true`, and in `afterDatasetsDraw`, strokes the
diamond at `(x, chartArea.bottom - 12)`. The point's data object gains
`_pending: m.mean_car_t21 == null && m.n_signals > 0`. Crucially, for pending
points set `pointRadius: 0` and skip the whisker (the whiskerPlugin already
no-ops when `_min == null`), so the only thing drawn for that month is the band +
floor diamond. The connector line must **break** across a pending month (use
`null` for its y so Chart.js does not draw a segment to/through it) — a line
sloping toward the floor would re-imply a value.

### Ruling 2 — click affordance, cursor, tooltip

- **Click target:** the pending month is clickable. Because the diamond sits in
  pixel space (off the data scale), Chart.js's `nearest/intersect` hit-testing
  will not find it. Add an explicit hit region: in `onClick` and `onHover`, if no
  dataset element is hit, compute the nearest month x-slot and, when that month is
  pending, treat it as the target. Simplest robust implementation: the N-strip
  bar for that month (which IS a real Chart.js element on its own canvas) carries
  the click, AND the floor-diamond area on the main canvas is tested by x-band.
  Engineer picks whichever is cleaner; both routes open the same drill-down.
- **Cursor:** `pointer` whenever the pointer is over a pending month's band, its
  floor diamond, or its N-strip bar — identical to a real dot.
- **Tooltip string (DOM tooltip, the same bubble used for dots; ASCII / HTML-entity
  safe):**

  `Aug 2026: 83 signals fired - T+21 return not yet matured. Click to see the trades.`

  Template (engineer):
  `"{Mon YYYY}: {n} signals fired - T+21 return not yet matured. Click to see the trades."`
  - `{n}` = `n_signals`. Use a hyphen `-`, not an en/em dash, per cp1252 rule.
  - The bubble for a pending month shows ONLY this line (no mean / range / hit-rate
    rows — they are null). Reuse `#cohort-tooltip` but render a single-line
    pending variant: hide `#tt-mean`, `#tt-range`, `#tt-hitrate`; set `#tt-month`
    to `"{Mon YYYY}"` and a single body line to the sentence above; keep the
    `click to drill down` helper hidden (the sentence already says "Click...").

### Ruling 3 — legend entry

The legend strip currently lists: `* filled dot (N>=5)  O open ring (N<5)  ! one-ticker dominance  | solid / : dashed whiskers`.

**Add one entry, after the open-ring entry, before the dominance `!`:**

`<> pending (fired, T+21 not yet matured)`

- Render the legend glyph as the same hollow slate-400 diamond (a `<>` ASCII
  stand-in in any text/ASCII context; the actual legend swatch is the drawn
  diamond). Keep the slate-400 colour so the legend swatch matches the on-plot
  marker exactly.
- ASCII-safe label text: `pending (fired, T+21 not yet matured)`.

Updated legend line (ASCII reference):
`* filled dot (N>=5)   O open ring (N<5)   <> pending (not yet matured)   ! one-ticker dominance   | solid : dashed whiskers`

### Ruling 4 — drill-down modal for a pending cohort

A pending month IS clickable and DOES open the drill-down. The cohort is not yet
measured, so the modal must present the trades that *can* be shown and explicitly
mark what cannot yet be computed — and must NOT print a verdict line (there is no
result to summarise).

**Header:**

- Title: same format as a measured month, e.g. `Aug 2026 cohort - T3 NED buy`.
- Summary line: `{n} signals fired - T+21 return not yet matured`
  (NO mean / range — those are null). ASCII hyphen.
- **Header note (replaces the verdict line):** a single neutral note, slate, not
  amber:
  `This cohort has not yet matured. T+21 (and T+90) returns are still pending; figures below are partial.`
  The `#drillVerdict` slot is reused but styled slate-600 instead of amber-700 for
  the pending case. **No `cohort_verdict` string is computed or shown** — share of
  cohort movement depends on net_car_t21, which is null, so there is no
  "drag/contributor" line.

**Columns — what shows vs what is em-dashed:**

| Column | Pending cohort |
|--------|----------------|
| Ticker | shown |
| Director | shown |
| Role | shown |
| Fire date | shown |
| CAR T+1 | **shown if matured** (>=1 trading day elapsed), else em-dash. T+1 matures days after firing, so most/all rows in a fresh month will have it. |
| CAR T+21 | em-dash `--` (not yet matured) |
| CAR T+90 | em-dash `--` |
| Net of costs | em-dash `--` (depends on T+21 vs benchmark) |
| Share of cohort movement | em-dash `--` (depends on net T+21; cannot be computed) |

Em-dash rendering: use the ASCII string `--` in any `print()`/JSON path (cp1252
rule); the renderer MAY display the typographic em-dash in the HTML cell via the
`&mdash;` entity since that is browser-side, not a piped print.

**Default sort:** the normal default (descending on `Share of cohort movement`)
is unavailable — that column is empty. **Sort pending cohorts by Fire date,
descending** (newest fired first). Rationale: for an unmeasured cohort the only
actionable ordering for live trading is recency — the most recently fired trades
are the ones still inside their actionable window. (Secondary option if Rupert
later prefers it: transaction value descending; Fire-date-desc is the chosen
default now.) All column headers remain clickable to re-sort; the em-dashed
columns sort with nulls last.

**Upstream payload note (engineer):** the export must include a
`cohort_drilldown` entry for pending months too, with `signals[]` carrying
`ticker`, `director`, `role_short`, `fire_date`, and `car_t1` (null if T+1 not
yet matured); `car_t21`, `car_t90`, `net_car_t21`, `cohort_weight` are `null`.
The month object also gains `pending: true` (or the renderer derives it from
`mean_car_t21 == null && n_signals > 0`). The N-strip bar for a pending month
takes a distinct **pending tint** as a reinforcing affordance: fill
`rgba(148,163,184,0.35)` (slate-400 @ 35%) with the N label in slate-600 — visibly
"in progress," distinct from both the solid-grey matured bar (`#cbd5e1`) and the
amber-dashed low-N bar.

### Ruling 5 — horizon dependency (traceability for the future toggle)

"Pending" is horizon-specific: this ruling defines it for **T+21** (`mean_car_t21
== null && n_signals > 0`); when the horizon toggle lands, the pending test, the
floor diamond, the tooltip wording, and which drill-down columns are em-dashed
must recompute against the active horizon (e.g. under T+90 a month is pending
until 90 trading days elapse, so far more recent months read as pending).
