# Cohort performance chart — Level 1 design spec

**Author:** dashboard-designer (Claude)
**Date:** 2026-05-29
**Brief:** `docs/specs/cohort-performance-chart-redesign-brief.md` (decisions 8–11)
**Companion:** `docs/specs/cohort-performance-chart-design-spec.md` (Level 2 detailed chart)
**Surface:** `outputs/performance.html` — the existing performance table, augmented
**Status:** Ready for implementation hand-off. Completes the Level-1 half left open when decision 11 introduced the two-level architecture.

---

## Decisions locked by Rupert (2026-05-29) — supersede designer defaults below

1. **Row-click behaviour: FOCUS MODE** (table dims out, chart takes the page). This
   OVERRIDES the designer's inline-expand recommendation in the "Row-click
   interaction" section below. Phase 3 must implement focus-mode. The designer's
   inline-expand rationale/wireframe is retained below for traceability only — do
   not build it. If a focus-mode wireframe/scroll spec is needed at Phase 3, the
   front-end engineer requests it from the dashboard-designer agent.
2. **Sparkline null-month contract: emit explicit `null`** for empty months
   (calendar-true x-spacing). Confirmed — resolves open question 1.
3. **Trend column for young signals (no 6-month window): grey flat-arrow + em-dash.**
   Confirmed — resolves open question 2.

---

## Why this doc exists

The Level-2 spec (companion file) fully covers the detailed single-signal cohort
chart. Decision 11 then split the surface into two levels: **Level 1 = the
augmented performance table (this doc)**, **Level 2 = the detailed chart (companion
doc)**. This spec defines the two new table columns (sparkline + trend), the
row-click interaction that opens Level 2, and reconciles the upstream data fields.

Conventions, palette and Tailwind idioms match the Level-2 spec exactly. The
per-tier hex codes below are copied verbatim from `render_helpers.TIER_PALETTE` as
quoted in the Level-2 spec (s.230) — single source of truth, no new colours.

```
T0  #dc2626   T1A #ef4444   T1B #f43f5e   T2  #f59e0b   T3  #10b981   T4  #94a3b8
T5  #fb923c   T6  #cbd5e1    T7  #8b5cf6   S1  #3b82f6   F1  #a855f7
```

---

## Design spec — Level 1 performance table augmentation

### Layout

The existing table keeps every current column (Signal, N, hit rate, mean CAR at
T+1 / T+21 / T+90, etc.). We insert two new columns just before any trailing
actions column: **Trajectory** (sparkline) and **3m trend**. The whole row becomes
clickable to open Level 2.

```
+----------------------------------------------------------------------------------------------------+
|  Signal performance — inception to date            (click any row to open the cohort chart)        |
+------------+-----+--------+---------+---------+---------+----------------------+---------+----------+
|  Signal    |  N  | hit %  | CAR T+1 | CAR T+21| CAR T+90|     Trajectory       | 3m trend|          |
+------------+-----+--------+---------+---------+---------+----------------------+---------+----------+
|  [T3] NED  | 226 |  38%   |  +0.4%  |  +3.6%  |  +7.9%  |  /\__/\    .------.   |  ^ +2.4%|   >      |
|            |     |        |         |         |         | *      \__/      o    | (green) |          |
+------------+-----+--------+---------+---------+---------+----------------------+---------+----------+
|  [S1] clus | 751 |  29%   |  -0.9%  |  -2.1%  |  -5.3%  | *--.               o  |  v -1.8%|   >      |
|            |     |        |         |         |         |    `--.____.-----.    | (red)   |          |
+------------+-----+--------+---------+---------+---------+----------------------+---------+----------+
|  [F1] 1st  |1037 |  31%   |  -0.1%  |  -0.2%  |  -1.1%  | *~~~~~~~~~~~~~~~~~~o   |  - +0.3%|   >      |
|            |     |        |         |         |         |   (flat, near zero)   | (grey)  |          |
+------------+-----+--------+---------+---------+---------+----------------------+---------+----------+
   ^ tier badge       ^ existing cols stay as-is        ^ 120x30 SVG       ^ arrow+delta   ^ chevron
                                                          faint zero line                  affordance

Row hover:  bg-slate-50, cursor-pointer, the chevron darkens.
Open row :  FOCUS MODE (locked decision 1) — table dims, chart takes the page. (The "Row-click interaction" section below still describes inline-expand; that is SUPERSEDED — do not build it.)
```

The leading `*` in each sparkline is the inception (start) endpoint dot; the
trailing `o` is the most-recent-month endpoint dot. Both are described precisely
in the sparkline spec below.

---

## 1. Sparkline column spec

**Goal (per brief s.79–84):** a tiny pure-shape line, ~120×30px, no axes, no
labels, no whiskers, faint zero baseline, signal-tier colour, small filled dots
at the start and most-recent points. The user scans trajectory across 11 rows in
seconds.

### Rendering decision: inline server-side SVG (not Chart.js)

Chart.js is the right tool for the interactive Level-2 chart, but spinning up 11
Chart.js canvas instances purely for static 120×30 shapes is wasteful and slow on
page load. The project already renders server-side in Python, so each sparkline
is emitted as a self-contained inline `<svg>` string baked into the table HTML at
render time. Zero client JS, zero canvas, prints straight into the row.

### Geometry and constants

| Constant | Value | Note |
|----------|-------|------|
| `W` (viewBox width)  | 120 | px |
| `H` (viewBox height) | 30  | px |
| `PAD_X` | 3 | left/right inset so endpoint dots aren't clipped |
| `PAD_Y` | 3 | top/bottom inset |
| stroke width | 1.5 | the trajectory line |
| endpoint dot radius | 2.0 | filled, tier colour |
| zero-baseline | 0.5px, `#e2e8f0` (slate-200), `stroke-dasharray="2 2"` | faint |

The drawable area is `x in [PAD_X, W-PAD_X]`, `y in [PAD_Y, H-PAD_Y]`.

### Y-scaling

Scale each sparkline to its **own** min/max mean-CAR across its months (plus a tiny
symmetric pad), independent of other rows. We deliberately do **not** share a
common y-scale across the 11 rows: shapes are read as *relative trajectory per
signal*, not cross-signal magnitude (that comparison job belongs to the existing
numeric CAR columns sitting right beside the sparkline). This matches the brief's
"pure shape" intent and decision 10's per-signal dynamic-scaling philosophy.

Map data value `v` to pixel y with the standard flip (SVG y grows downward):
`y = PAD_Y + (vmax - v) / (vmax - vmin) * (H - 2*PAD_Y)`.
Guard the degenerate `vmax == vmin` case by drawing a flat mid-height line.

### Zero baseline

Draw a faint dashed horizontal line at the pixel y that corresponds to value `0`,
**only if 0 falls within `[vmin, vmax]`**. If the whole series is one-sided (all
positive or all negative), the zero line would sit on the chart edge and add
noise — omit it in that case. This is a small judgement call the brief doesn't
spell out; rationale: a baseline you can't cross tells the eye nothing.

### None / gap-month handling

`sparkline_points` is `{month_iso, mean_car_t21}` from inception. Two gap cases:

1. **A month with no signals fired** (cohort genuinely empty that month). Upstream
   may emit it with `mean_car_t21: null`, or omit the month entirely.
2. **A leading/trailing run** is always real data (inception is by definition the
   first non-empty month).

**Rule:** treat `null` as a gap. Do **not** interpolate across it and do **not**
draw a connecting segment through it — that would invent trend, the exact sin the
brief is trying to kill (s.16). Instead, **break the path** into separate polyline
runs split on each `null`. A single isolated real point between two gaps renders
as just its dot (a 1px run draws nothing visible, so emit a tiny dot for any run
of length 1). The endpoint dots (start, most-recent) are placed on the first and
last **non-null** points, never on a gap.

If after dropping nulls there are **fewer than 2** real points, render a dash
placeholder (`<span class="text-slate-300">—</span>`) instead of an SVG. Don't
draw a one-point "trajectory."

### Path-building approach (for the back-end engineer)

Build runs (lists of consecutive non-null points), convert each run to an SVG
`points`/`d` string, emit one `<polyline>` per run, then the zero baseline, then
two endpoint `<circle>`s. Pseudocode:

```python
def sparkline_svg(points, color_hex):
    # points: list of {"month_iso": str, "mean_car_t21": float | None}
    vals = [p["mean_car_t21"] for p in points if p["mean_car_t21"] is not None]
    if len(vals) < 2:
        return '<span class="text-slate-300">&mdash;</span>'

    W, H, PAD_X, PAD_Y = 120, 30, 3, 3
    vmin, vmax = min(vals), max(vals)
    pad = (vmax - vmin) * 0.08 or 0.01      # small symmetric breathing room
    lo, hi = vmin - pad, vmax + pad
    span = (hi - lo) or 1.0
    n = len(points)

    def px(i):
        return PAD_X + (i / (n - 1)) * (W - 2 * PAD_X) if n > 1 else W / 2

    def py(v):
        return PAD_Y + (hi - v) / span * (H - 2 * PAD_Y)

    # split into runs on null gaps (no interpolation across gaps)
    runs, cur = [], []
    for i, p in enumerate(points):
        v = p["mean_car_t21"]
        if v is None:
            if cur:
                runs.append(cur); cur = []
        else:
            cur.append((px(i), py(v)))
    if cur:
        runs.append(cur)

    parts = []

    # faint dashed zero baseline, only if 0 is inside the data band
    if lo <= 0.0 <= hi:
        zy = py(0.0)
        parts.append(
            f'<line x1="{PAD_X}" y1="{zy:.1f}" x2="{W - PAD_X}" y2="{zy:.1f}" '
            f'stroke="#e2e8f0" stroke-width="0.5" stroke-dasharray="2 2"/>'
        )

    # one polyline per run; single-point runs get a tiny dot so they're visible
    for run in runs:
        if len(run) == 1:
            x, y = run[0]
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.2" fill="{color_hex}"/>')
        else:
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in run)
            parts.append(
                f'<polyline points="{pts}" fill="none" '
                f'stroke="{color_hex}" stroke-width="1.5" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )

    # endpoint dots on the first and last NON-NULL points
    first = runs[0][0]
    last = runs[-1][-1]
    parts.append(f'<circle cx="{first[0]:.1f}" cy="{first[1]:.1f}" r="2" fill="{color_hex}"/>')
    parts.append(f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="2" fill="{color_hex}"/>')

    inner = "".join(parts)
    return (
        f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'class="block" role="img" aria-label="trajectory sparkline" '
        f'preserveAspectRatio="none">{inner}</svg>'
    )
```

Notes for the engineer:
- All colours come from `TIER_PALETTE[group]` — pass the hex in, don't hardcode.
- `aria-label` keeps the SVG accessible; the numeric CAR columns beside it carry
  the real values for screen readers, so the sparkline can stay decorative-ish.
- `preserveAspectRatio="none"` lets the 120×30 box stay fixed regardless of zoom;
  acceptable because there are no axes to distort.
- Pure ASCII output — safe under the project's subprocess-print cp1252 constraint.

### Tailwind for the cell

```html
<td class="px-2 py-1 align-middle">
  <!-- inline SVG string from sparkline_svg() injected here -->
</td>
```
No extra classes needed inside; the SVG carries its own dimensions. Keep the
`<td>` narrow (the SVG is fixed at 120px) so the trend column sits close.

---

## 2. Trend column spec

**Goal (per brief s.85–88):** arrow + numeric delta driven by
`trend_3m_vs_prior3m_t21`. Green up if `> +1%`, red down if `< -1%`, grey flat
otherwise. Show the delta, e.g. `+2.4%`.

### Threshold rule (exact)

Let `d = trend_3m_vs_prior3m_t21` (a float; the brief's convention is a raw
fraction, so `0.024` means +2.4%). Compare against ±0.01:

| Condition | State | Arrow | Colour family |
|-----------|-------|-------|---------------|
| `d > 0.01`  | improving | up   | green  (`emerald-600` text, `emerald-50` chip) |
| `d < -0.01` | decaying  | down | red    (`rose-600` text, `rose-50` chip) |
| otherwise (incl. `None`) | flat | flat | grey (`slate-500` text, `slate-100` chip) |

`None` (a signal too young to have 6 months of history) renders as the grey flat
state with an em-dash instead of a number — see HTML below. This is the one
ambiguity the brief leaves open; flat-grey-with-dash is the least-misleading
choice (a young signal has no trend, not a zero trend).

### Arrows via HTML entities (cp1252-safe)

Do **not** emit raw Unicode arrows (↑ ↓ →) — they break under the project's
subprocess-piped `print()` cp1252 constraint (MEMORY: avoid non-cp1252 in
subprocess prints). Use HTML numeric entities, which are pure ASCII in the Python
string and render as arrows in the browser:

- Up arrow: `&#9650;` (▲ black up-pointing triangle)
- Down arrow: `&#9660;` (▼ black down-pointing triangle)
- Flat arrow: `&#9644;` (▬ black rectangle) — reads as "sideways / no change"

Triangles (not chevrons) chosen for legibility at 11px and to echo the
filled/hollow dot language of the Level-2 chart.

### Exact HTML per state

**Improving (green up):**
```html
<td class="px-2 py-1 whitespace-nowrap">
  <span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold
               bg-emerald-50 text-emerald-600">
    <span aria-hidden="true">&#9650;</span>+2.4%
  </span>
</td>
```

**Decaying (red down):**
```html
<td class="px-2 py-1 whitespace-nowrap">
  <span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold
               bg-rose-50 text-rose-600">
    <span aria-hidden="true">&#9660;</span>-1.8%
  </span>
</td>
```

**Flat (grey), with a value:**
```html
<td class="px-2 py-1 whitespace-nowrap">
  <span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium
               bg-slate-100 text-slate-500">
    <span aria-hidden="true">&#9644;</span>+0.3%
  </span>
</td>
```

**Flat (grey), no history (`None`):**
```html
<td class="px-2 py-1 whitespace-nowrap">
  <span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium
               bg-slate-100 text-slate-400">
    <span aria-hidden="true">&#9644;</span>&mdash;
  </span>
</td>
```

Delta formatting: `f"{d*100:+.1f}%"` gives the signed one-decimal percent
(`+2.4%`, `-1.8%`). The `+` from the `:+` format spec is intended for both up and
flat-positive states.

---

## 3. Row-click interaction — RECOMMENDATION

> **SUPERSEDED — see locked decision 1 at top of doc. Rupert chose FOCUS MODE on
> 2026-05-29. Build focus-mode (table dims out, chart takes the page); do NOT build
> the inline-expand design described in this section. The material below is retained
> for traceability only.**

### Recommendation: **inline-expand** (chart slides open in a row directly below the clicked row; the table stays visible above and below).

**One-sentence rationale:** Rupert's documented workflow is *scan all 11 signals,
then interrogate one* — inline-expand keeps the other 10 rows on screen as the
anchor he just scanned, so he can read the Level-2 chart for T3 while T3's numeric
row (and its neighbours) stay in view for context, then collapse and move to the
next candidate without losing his place.

### Why not focus-mode

Focus-mode (dim the table, chart takes the page) is cleaner per pixel, but it
severs the chart from the row that motivated the click. For an interrogation loop
across 11 candidates, the repeated "open → lose the table → close → re-find my
place" cycle is exactly the friction we're removing. The brief itself frames the
table as the *navigator*; a navigator you hide on every drill-down stops
navigating. Focus-mode wins when there's one object of attention for a long
session — not our case.

The Level-2 spec's own signal-switching affordance (the in-header pill row /
dropdown, companion s.148–157) still applies **inside** the expanded panel, so the
user can swap signals without collapsing — but with inline-expand he rarely needs
to, because the other rows are right there to click instead.

### Accessibility / scroll trade-off we are accepting

Inline-expand pushes the rows below the clicked row down the page, and the
expanded panel is tall (the Level-2 main chart + N strip + hit-rate card together
run ~620px). **Accepted cost:** on a long table the click can scroll content the
user wasn't looking at, and a second click elsewhere can cause a visible reflow.

Mitigations baked into the spec:
- **Only one row open at a time.** Opening row B auto-collapses row A. Prevents an
  accordion of stacked 620px panels and unbounded page growth.
- **Scroll-into-view on open.** After expand, `scrollIntoView({block: "nearest"})`
  on the expanded panel so the chart is visible without yanking the clicked row to
  the top.
- **Keyboard + ARIA.** The clickable row is a `<tr>` with `tabindex="0"`,
  `role="button"`, `aria-expanded="true|false"`, and `aria-controls` pointing at
  the panel row's id; Enter/Space toggle it, Esc collapses. The expanded panel row
  gets `role="region"` with an `aria-label` naming the signal.
- **Click-target discipline.** The whole row is the target, but any future
  in-row interactive element (none today) would need `stopPropagation`.

The Level-2 drill-down *modal* (month-cohort click, companion State D) is
unchanged — it still opens as an overlay on top, layered above the inline panel.
So we have: table (Level 1) → inline-expanded chart (Level 2) → modal drill-down
(Level 2 month detail). Three layers, each a clear step deeper.

### Expanded-row markup sketch

```html
<!-- the signal's normal row -->
<tr tabindex="0" role="button" aria-expanded="false"
    aria-controls="cohort-panel-t3"
    class="cursor-pointer hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-300"
    data-signal-group="t3">
  <!-- ... existing cells + sparkline + trend + chevron ... -->
  <td class="px-2 py-1 text-slate-300"><span aria-hidden="true">&#9656;</span></td>
  <!-- chevron rotates 90deg via a `.is-open` class when expanded -->
</tr>

<!-- the expansion row: hidden until opened; spans all columns -->
<tr id="cohort-panel-t3" role="region" aria-label="T3 NED cohort chart" hidden>
  <td colspan="99" class="p-0 bg-slate-50/60 border-t border-slate-200">
    <div class="p-4">
      <!-- Level-2 component renders here: main chart card + N strip + hit-rate card -->
      <!-- see companion spec for the inner cards -->
    </div>
  </td>
</tr>
```

The chevron glyph `&#9656;` (▸) is cp1252-safe and rotates to ▾ via a CSS
`rotate-90` toggle on the `.is-open` class.

---

## 4. Upstream data fields for Level 1 (reconciliation)

The brief already promised both fields this level needs (brief s.196–204):

| Field | Per | Already promised? | Notes |
|-------|-----|-------------------|-------|
| `sparkline_points` | `signal_group` | **Yes** (brief s.199) | ordered `[{month_iso, mean_car_t21}]` from inception to latest. Reuses the per-cohort means already computed for Level 2 — no extra compute. |
| `trend_3m_vs_prior3m_t21` | `signal_group` | **Yes** (brief s.202) | float; last-3-months mean CAR @ T+21 minus prior-3-months. Net-of-costs, same horizon as the chart. |

The existing table columns (N, hit rate, mean CAR @ T+1/T+21/T+90) are already
emitted today — no change.

### One clarification flagged (not a new field, a null-contract)

`sparkline_points` must specify how empty months appear so the renderer's
gap-handling (section 1) is deterministic. **Requested contract:** include every
calendar month from inception to latest as an entry, using
`"mean_car_t21": null` for months with no fired signals (rather than omitting the
month). This keeps the x-spacing on the sparkline calendar-true (a 3-month gap
looks 3× as wide as a 1-month gap) and makes the gap-break logic trivial. If
upstream finds it cheaper to omit empty months, the renderer can still cope (it
just loses calendar-true spacing) — but **calendar-true with explicit nulls is
the preferred contract.** This is the one upstream nuance worth pinning before
implementation; it is not a new field, only a population rule on a promised one.

### Where these are emitted

Per brief s.205–207 these compute from `signal_fires` × `signal_performance`
joined on `signal_id`, grouped by `signal_group` and `month_iso` — no new schema.
The natural home is the same `cohort_performance.json` blob defined in the Level-2
spec (companion s.441): add `sparkline_points` and `trend_3m_vs_prior3m_t21`
inside each `groups[grp]` object, alongside the existing `header` and `months`.
The renderer reads them straight from there when building the table — no second
fetch.

```jsonc
"groups": {
  "t3": {
    "label": "T3 NED buy",
    "color_hex": "#10b981",
    "header": { "n_total_signals": 226, "mean_car_t21_overall": 0.0356, "hit_rate_t21_overall": 0.38 },
    "trend_3m_vs_prior3m_t21": 0.024,                      // <-- Level 1
    "sparkline_points": [                                   // <-- Level 1
      { "month_iso": "2025-06", "mean_car_t21": 0.012 },
      { "month_iso": "2025-07", "mean_car_t21": null },     // empty month -> gap
      { "month_iso": "2025-08", "mean_car_t21": -0.004 }
      // ... to latest, ascending
    ],
    "months": [ /* ... unchanged, drives Level 2 ... */ ]
  }
}
```

Reusing `mean_car_t21` from `months[]` for `sparkline_points` is encouraged, but
keep `sparkline_points` as its own array so the null-padded calendar-true series
is independent of any future filtering applied to `months[]`.

---

## 5. Wireframe — augmented row + open behaviour

```
DEFAULT (collapsed) — Level 1 table
+------------+-----+------+--------+--------+--------+----------------------+----------+----+
| Signal     |  N  | hit% | CAR T+1| CAR T21| CAR T90|     Trajectory       | 3m trend |    |
+------------+-----+------+--------+--------+--------+----------------------+----------+----+
| [T3] NED   | 226 | 38%  | +0.4%  | +3.6%  | +7.9%  | *\__/\_.----.__o     | [^ +2.4%]| >  |
| [S1] clstr | 751 | 29%  | -0.9%  | -2.1%  | -5.3%  | *--.___.------.__o    | [v -1.8%]| >  |
| [F1] 1st   |1037 | 31%  | -0.1%  | -0.2%  | -1.1%  | *~~~~~~~~~~~~~~~~~o    | [- +0.3%]| >  |
| ...        |     |      |        |        |        |                      |          |    |
+------------+-----+------+--------+--------+--------+----------------------+----------+----+

CLICK the [T3] row  ->  [SUPERSEDED wireframe — shows inline-expand; BUILD FOCUS MODE per locked decision 1, not this]
CLICK the [T3] row  ->  INLINE-EXPAND (one row open at a time; T3's row stays visible above)
+------------+-----+------+--------+--------+--------+----------------------+----------+----+
| [T3] NED   | 226 | 38%  | +0.4%  | +3.6%  | +7.9%  | *\__/\_.----.__o     | [^ +2.4%]| v  | <- aria-expanded=true, chevron down
+============================================================================================+
|  [ Level 2 component renders here, full-table-width, bg-slate-50/60 ]                       |
|                                                                                            |
|   Cumulative net CAR @ T+21 by monthly cohort — T3 NED      [signal pills: T1A T1B .. F1]  |
|   +----------------------------------------------------------------------------------+     |
|   |  +25% |                                            |                             |     |
|   |    0% |- - * - - * - - O - - * - - * - - * - - - - * - dashed zero - - - - - -    |     |
|   |  -25% +--+--+--+--+--+--+--+--+--+--+--+--+                                       |     |
|   |        Jun Jul Aug Sep Oct Nov Dec Jan Feb Mar Apr May                           |     |
|   +----------------------------------------------------------------------------------+     |
|   N strip:  12 18 23  9 14 21 17  3 16 19 14 10                                             |
|   +----------------------------------------------------------------------------------+     |
|   |  Rolling 6-month hit rate @ T+21  (teal line, 50% dashed baseline)               |     |
|   +----------------------------------------------------------------------------------+     |
+============================================================================================+
| [S1] clstr | 751 | 29%  | -0.9%  | -2.1%  | -5.3%  | *--.___.------.__o    | [v -1.8%]| >  | <- rows below pushed down
| [F1] 1st   |1037 | 31%  | -0.1%  | -0.2%  | -1.1%  | *~~~~~~~~~~~~~~~~~o    | [- +0.3%]| >  |
+------------+-----+------+--------+--------+--------+----------------------+----------+----+

CLICK a month dot inside the panel  ->  modal drill-down (Level 2 State D), layered on top.
CLICK the [S1] row                  ->  T3 panel auto-collapses, S1 panel opens in its place.
```

---

## 6. Out of scope / deferred (Level 1)

- **Multi-row simultaneous expand.** One open panel at a time, by design (scroll
  control). Not configurable.
- **Sparkline interactivity** — no hover tooltip, no click on the sparkline
  itself. The whole *row* is the click target; the sparkline is pure shape. (Hover
  detail lives in Level 2.)
- **Shared / common y-scale across sparkline rows.** Each sparkline self-scales;
  cross-signal magnitude comparison is the job of the numeric CAR columns.
- **Sparkline whiskers / min-max.** Brief s.81 explicitly excludes them; that
  detail belongs to Level 2.
- **Trend column horizon toggle** (e.g. trend at T+90 instead of T+21). Fixed at
  T+21 per brief; raise a follow-up if Rupert wants horizon-switchable trend.
- **Persisting which row is expanded** across page loads (URL / localStorage).
  Stateful per page load only — matches the Level-2 spec's same exclusion.
- **Sorting the table by trend or by sparkline shape.** Existing column sorts
  unchanged; adding a "sort by trend" is a small follow-up, not v1.
- **Mobile / < 600px** — desktop-first per brief s.165; the 120px sparkline and
  the wide inline panel both need a separate mobile pass.
- **Animated slide transition** on expand/collapse. A CSS height transition is a
  nice-to-have but the rescale-snaps-instantly philosophy (decision 10) says don't
  block the build on it; ship instant show/hide, add easing later if wanted.

---

## Open questions surfaced during wireframing

1. **`sparkline_points` null-month contract.** Resolved with a *requested
   contract* in section 4 (explicit `null` for empty months, calendar-true
   spacing), but it needs a one-word confirm from upstream that emitting nulls is
   acceptable vs. omitting empty months. Renderer copes either way; the question
   is only which gives the truer x-spacing. **Not blocking** — flagged for the
   upstream/`export_dashboard_json.py` engineer.

2. **Trend `None` rendering.** The brief doesn't say what a signal too young for a
   6-month window should show in the trend column. I've made a binding-enough
   default (grey flat arrow + em-dash, section 2) on the principle "no trend ≠ zero
   trend." Calling it out so Rupert can override if he'd rather suppress the column
   entirely for young signals.

No other ambiguities. Decisions 8–11 and the Level-2 spec resolve everything else.
