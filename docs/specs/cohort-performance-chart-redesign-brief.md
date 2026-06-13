# Cohort performance chart — redesign brief

**Status:** Design brief, ready for dashboard-designer agent
**Author:** Rupert (PM) + Claude (sparring partner)
**Date:** 2026-05-27
**Surface affected:** Performance page → "Cumulative net CAR @ T+21 (~1 month) — trailing 12 months" panel
**Replaces:** existing trailing-12-month CAR line chart by signal group

## Context

The current chart on the performance page plots a 12-month trailing mean CAR per signal
group as a single smooth line, displayed only over the last 12 calendar months. With our
current signal volume per group per month (often 3–10), this chart misleads in three ways:

1. The smooth spline interpolation invents trend between sparse data points.
2. A single outlier ticker can swing the line several percentage points and the user
   has no way to see that from the chart alone.
3. The 12-month display window throws away earlier history — making it impossible to
   read the longer-run trajectory of the edge.

Rupert is using these signals as entry points for real-money trades. He needs the chart
to tell him three things, honestly:

- **Is the edge real?** (Reliability assessment over full history.)
- **Is the edge improving or decaying?** (Trend assessment over time.)
- **What is driving any given month's number?** (Single-cohort interrogation.)

The cumulative inception-to-date stats already live in the performance table — this chart
is the *over-time* view that table cannot give.

## Design decisions (locked)

The following have been agreed and should not be re-litigated by the designer:

1. **Expanding window since inception**, not trailing 12 months. X-axis grows as the
   database grows. Never drop old data.
2. **Each point = one calendar-month cohort**, not a rolling-window mean. Per-month dots
   stand on their own.
3. **Min/max whiskers per month**, not a statistical confidence band. Whiskers represent
   real trades the user can click into.
4. **No smooth spline.** Connect means with straight segments (faded) or omit the
   connector entirely. The eye should read dots, not curves.
5. **Visual N-discount.** Months where N is below 5 must be visibly distinguished
   without the user having to look at the N strip. Recommended: open ring (hollow dot)
   + dashed whisker.
6. **Drill-down on click.** Every monthly cohort point is clickable and opens a panel
   showing every signal that contributed.
7. **Companion rolling hit-rate panel.** A secondary, smaller chart sits below the main
   chart, plotting the rolling 6-month hit rate (% of signals beating their sector
   benchmark at T+21). Smoother than mean CAR; less outlier-fragile.
8. **3-month moving average overlay through the means.** Faint line overlay through
   the per-month dots, smoothed at 3 months trailing. Light enough to read as a guide
   but visible enough to give the eye a sense of direction. (Decided 2026-05-27.)
9. **Single-ticker dominance marker on the main chart.** Months where one ticker
   accounts for more than 50% of cohort weight get a small `!` glyph above the
   whisker top. Reinforces the "drag" risk visually without requiring the user to
   click into the drill-down. (Decided 2026-05-27.)
10. **Dynamic y-axis per signal group.** When the user toggles between groups, the
    chart rescales to the data range of the active selection (plus 10% headroom). We
    accept the on-switch rescale disorientation as the price of using the full chart
    height for each group's data. (Decided 2026-05-27.)
11. **Two-level navigation architecture.** Level 1 is the existing performance table,
    augmented with sparkline + trend columns and made row-clickable (see new section
    below). Level 2 is the detailed cohort chart described in this brief. A click on
    any Level 1 row opens Level 2 for that signal. We do NOT need a separate
    "all-signals-overlaid" chart — that role is now played by the augmented table.
    The detailed chart shows ONE signal at a time, removing the visual chaos of
    overlapping monthly dots and whiskers across 11 signals. (Decided 2026-05-27.)

## Functional requirements

### Level 1 — performance table augmentation (the new overview)

The existing performance table on the performance page becomes the new Level 1
overview. It already lists every signal with N, mean CAR at T+1/T+21/T+90, hit
rate, etc. Two new columns and one interaction change make it a proper navigator:

- **Sparkline column.** A tiny line chart showing the monthly mean CAR trajectory
  for that signal since inception. No axes, no labels, no whiskers — pure shape so
  the user can scan trajectory across 11 rows in seconds. Width ~120px, height
  ~30px. Zero baseline drawn faintly. Same colour as the signal tier. Endpoints
  shown as small filled dots so the eye picks up start and most-recent values.
- **Trend column.** Simple arrow + delta. Compute the last 3 calendar months' mean
  CAR at T+21 minus the prior 3 months' mean CAR at T+21. Render as: green
  up-arrow if delta > +1%, red down-arrow if delta < −1%, grey flat-arrow
  otherwise. Show the numeric delta next to the arrow (e.g. "+2.4%" or "−1.1%").
- **Clickable rows.** Each row's primary click action opens the Level 2 detailed
  cohort chart for that signal. Designer to recommend whether this is an
  inline-expand (chart slides open below the row, table stays visible above) or a
  focus mode (table dims out, chart takes the page). Inline-expand keeps context
  but adds vertical scroll; focus mode is cleaner but loses the comparison view.

The rest of the existing performance table columns (N, hit rate, mean CAR at
each horizon, etc.) stay as-is. We are augmenting, not replacing.

### Main chart

- One point per calendar month, from the first month with any signal data to the latest
  completed month.
- For each signal group: per-month mean CAR, min CAR, max CAR (all at T+21, net of costs,
  already computed upstream).
- Faded straight-segment polyline connects the means (low opacity, ~0.3, same colour as
  the dots) to suggest direction. No bezier, no smoothing.
- Dot rendering rules:
  - N ≥ 5: filled dot, radius 4px.
  - N < 5: open ring (white fill, coloured stroke), radius 3px.
- Whisker rendering: vertical line from min to max with small horizontal caps. Solid for
  N ≥ 5, dashed for N < 5.
- Y-axis: % CAR, symmetric about zero, with the zero line dashed.
- Y-axis range: dynamic, capped at the data range plus 10% headroom. Don't fix at
  ±25% — let the data drive the scale per signal group.

### N strip (immediately below main chart, shared x-axis)

- One stacked bar per month, height proportional to total N for the selected signal
  group(s).
- N value shown numerically above each bar (helps with low-N months especially).
- Bars where N < 5 should be dashed-outlined and the N label coloured red (or amber)
  to reinforce the "discount this month" signal already shown above.

### Hit-rate panel (below the main chart card, smaller)

- One point per month, plotting rolling 6-month hit rate (% of signals in window that
  beat their sector benchmark CAR at T+21).
- Same x-axis as main chart.
- 50% baseline shown dashed (random performance reference).
- Use a different colour from the main series (suggest teal/green) to make it visually
  distinct from the CAR chart.

### Drill-down panel (opens on month click)

- Header: `M-N cohort · {N} signals · mean {x.x}% · range {min}% to {max}%`
- Table columns: Ticker, Director (name + role), Fire date, CAR @ T+1, CAR @ T+21,
  CAR @ T+90, Sector benchmark, Net of costs (CAR – benchmark – costs),
  **Contribution to mean** (= r_i / (N × cohort_mean), expressed as a %; sums to
  100% across the cohort). Replaces the original placeholder "Weight" column —
  see note in Out of scope below for why "weight" was the wrong framing.
- Sortable by any column. Default sort: Contribution to mean descending (biggest
  contributors first). This is also the sort that directly answers "which ticker
  is pulling the line."
- Footer text: a one-line verdict computed in upstream Python, e.g.
  "1 ticker drove 71% of the cohort mean" or "Cohort outcomes were broadly consistent
  (no trade contributed >40% of the mean)." This avoids the user having to do the
  arithmetic to spot a one-stock-pulling situation.
- Closes via X or click-outside.

### Signal-group selection in Level 2

The detailed chart shows ONE signal at a time. Multi-select overlays are
explicitly out of v1 scope (overlapping monthly dots and whiskers across 11
signals would be visual chaos).

The user enters Level 2 by clicking a row in the Level 1 performance table. To
switch signals without going back to the table, the Level 2 view should show a
small dropdown or pill row in its header listing all available signals — clicking
another signal swaps the chart in place. A "back to overview" link in the same
header dismisses Level 2 entirely.

Cross-signal visual comparison (small multiples grid) is deferred to v2 — see
"Out of scope" below.

## Visual requirements

- Colour per signal group: re-use the existing dashboard colour palette already assigned
  to each signal tier (T1A=pink, T1B=red/coral, T2=amber, T3=teal, T4=grey, T5=orange,
  S1=blue, F1=purple, T0=light-coral, T6=light-grey, T7=light-purple). Don't introduce
  new colours.
- Background: white card with 0.5px border, radius-lg.
- The two charts (main + hit-rate) sit in two stacked cards, not one combined card.
  Clear separation reinforces "two different metrics."
- Mockup reference: see widget mockup in associated brainstorm session (M-2 low-N
  example showing open ring + dashed whisker + red callout).

## Data fields required from upstream

The renderer needs the following per (signal_group, month) tuple:

- `month_iso` — e.g. "2025-09"
- `n_signals` — int
- `mean_car_t1`, `mean_car_t21`, `mean_car_t90` — floats (already net of costs)
- `min_car_t21`, `max_car_t21` — floats
- `hit_rate_t21` — float, 0–1 (% beating sector benchmark)
- For the drill-down: a foreign-key-able list of signal IDs that contributed to this
  cohort (so the click handler can fetch trade detail without a separate aggregate)

And per (signal_group) overall (header strip):

- `n_total_signals` — int since inception
- `mean_car_t21_overall` — float
- `hit_rate_t21_overall` — float

The rolling-6m hit rate is a separate aggregate keyed by (signal_group, month_end):

- `hit_rate_t21_rolling_6m` — float

For the Level 1 table augmentation (sparkline + trend columns), the renderer
additionally needs per signal_group:

- `sparkline_points` — ordered list of `{month_iso, mean_car_t21}` from inception
  to latest month. Reuses the same per-cohort means already computed for the
  Level 2 chart, so no extra compute.
- `trend_3m_vs_prior3m_t21` — float. Last 3 calendar months' mean CAR at T+21
  minus the prior 3 months' mean CAR at T+21. Same horizon and net-of-costs
  convention as the main chart.

All of these should be computable from `signal_fires` × `signal_performance` joined by
`signal_id`, grouped by `signal_group` and `month_iso`. No new schema work expected.

## Out of scope

- Cost methodology changes. Net-of-cost numbers are already in upstream; designer
  shouldn't touch that.
- Confidence intervals / statistical inference UI. Min/max whiskers replace this.
- Cross-signal correlation analysis.
- Equity-curve / cumulative-£-of-£1-per-trade view. Rupert decided to keep the
  performance table as the cumulative view; this chart is the over-time view.
- Mobile responsive breakpoints below 600px (the existing dashboard is desktop-first;
  a mobile pass can be a follow-up brief).
- **Small multiples grid** (all 11 signals shown as mini-charts side by side, each a
  thumbnail of the Level 2 detailed view). Deferred as a candidate v2 enhancement —
  evaluate need after Rupert has lived with Level 1 + Level 2 for a few weeks. Don't
  pre-build.
- **Multi-signal overlay** in the Level 2 detailed chart. The overlapping-dots-and-
  whiskers problem makes this visually unworkable for 11 signals. Cross-signal
  comparison is served by the Level 1 table's sparkline column instead.
- **Volume-weighted cohort means** (i.e. weighting each signal firing by the £
  value of the director's purchase rather than treating every signal equally).
  This is a real methodology question — a £5m CFO buy may carry more conviction
  than a £20k one — but it affects every CAR number across the entire dashboard,
  not just this drill-down. Out of scope here; raise as a separate methodology
  spec if Rupert wants to evaluate. For now: backtest remains equal-weighted
  (each signal firing = one trade in the cohort mean), and the drill-down's
  "Contribution to mean" column tells the user how concentrated that mean is.

## Open questions for the designer

All three previously open questions were resolved by Rupert on 2026-05-27 and are
now recorded as locked design decisions 8, 9 and 10 above. There are no remaining
open questions for the designer at brief-acceptance time. If the designer surfaces
new ones during wireframing, they should be appended below.

## What this brief delivers when complete

- A wireframe (low-fi or mid-fi) of the redesigned panel showing all states:
  default view, hover state on a dot, low-N callout, drill-down panel open.
- A structured implementation spec covering: chart library choice (Chart.js extension,
  custom SVG, or D3), Tailwind class catalogue for the new components, JSON shape the
  renderer should produce, and a list of methods/handlers needed.
- Snippets for the tricky bits: the dot-vs-ring conditional rendering, the click-to-
  drill-down handler signature, and the rolling hit-rate computation outline.
- What's deliberately out of scope, documented for traceability.

## Hand-off

Once the designer's deliverable lands, the implementation can be split into:

1. Upstream: extend `export_dashboard_json.py` to emit the per-cohort fields above
   (additive; no breaking changes).
2. Renderer: replace the existing line-chart partial in `render_index.py` /
   `render_helpers.py` with the new component. Keep the old chart available behind a
   feature flag for one release for A/B comparison.
3. Drill-down: implement the click-modal in plain HTML + Chart.js or D3; pull trade
   detail from a new endpoint or pre-baked per-cohort JSON blob.

Test plan and gating to follow standard QA-agent-before-gate practice from CLAUDE.md.
