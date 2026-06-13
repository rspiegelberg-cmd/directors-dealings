# Stage 5 — UX improvement roadmap v2
**Status:** Updated 2026-05-14. Replaces v1 (data-feature ideas). Three pure front-end usability fixes — no schema changes, no new exporter keys, no new pages.
**Owner:** Rupert.

---

## Summary

| # | Fix | Page(s) | Effort | Spec location |
|---|-----|---------|--------|---------------|
| U1 | Move Signals column to first position; merge Role into Director | Today | XS | `stage-05-build-spec.md §Today's buy signals table`, `stage-05-design-final.md §1.1.3` |
| U2 | Clickable legend on diagnostics chart — highlight one line, fade others | Performance | XS | `stage-05-build-spec.md §Per-signal diagnostics chart`, `stage-05-design-final.md §1.2.3` |
| U3 | Data-freshness indicator in header, colour-coded by age | Both | S | `stage-05-design-final.md §1.1.1, §1.2.1, §1.1.5` |
| U4 | Vivid transaction marker palette on annotated price chart | Company page | XS | `stage-05-1-company-page.md §3` |

Effort: XS = single component change, < 30 min · S = touches header component on two pages, ~1 hour.

All four should ship as part of the initial v1 build, not as a later increment. They fix design decisions that will frustrate daily use from day one.

---

## U1 — Signals column first; Role merged into Director

**Problem.** The signal tier (T0, T1, S1, etc.) is in column 7 of 8. A user's eye reads left to right, so they process Time, Ticker, Company, Director, Role, and £ Value before reaching the single most important piece of information on the page. T0 signals — the highest-conviction fires — are buried.

**Fix.** Move Signals to column 1 (immediately after Time). Merge Role into Director as a secondary line or chip within the same cell, eliminating the separate Role column. Net result: 7 columns → 6, with the most important column first.

**New column order:** Signals · Time · Ticker · Company · Director + role · £ Value · MTM

**Director cell rendering:**
```html
<td>
  <div class="text-slate-700">{director}</div>
  <span class="text-[10px] px-1.5 py-0.5 rounded {roleClass}">{role}</span>
</td>
```
Role chip colours unchanged from the existing spec (CEO/CFO = indigo, Chair = violet, NED = slate).

**Sort is unchanged:** by lowest-severity signal in `signals_fired` ascending (T0 = 0), then by `value_gbp` desc. With Signals in column 1, the sort order is now visually obvious — T0 rows are at the top and the badge is the first thing you see.

**Applies to:** Today's buy signals table (both Today section and This week section). The column spec in `stage-05-build-spec.md` and the full column spec in `stage-05-design-final.md §1.1.3` should be updated to reflect this order.

---

## U2 — Clickable legend on diagnostics chart

**Problem.** The diagnostics chart on Performance page renders 8 datasets simultaneously (T0, T1, T2, T3, T4, S1, F1, FTSE All-Share). Several share visually similar colours (T0 = dark red, T1 = red, T2 = amber). With real data the chart becomes a tangle of overlapping lines. There is no mechanism to isolate a single signal and ask "is S1 improving?"

**Fix.** Make each legend item clickable. Clicking a swatch highlights that dataset (full opacity, slightly thicker stroke) and fades all others to 15% opacity. Clicking the active item again resets all to full opacity. The FTSE All-Share baseline is never faded — it stays at 60% opacity at all times as the reference line.

**Implementation.** Chart.js already supports per-dataset visibility via `chart.data.datasets[i].borderWidth` and `chart.update()`. No library upgrade needed.

```javascript
// Replace the static HTML legend with an interactive version
function buildDiagLegend(chart) {
  const el = document.getElementById('diagLegend');
  el.innerHTML = '';
  chart.data.datasets.forEach((ds, i) => {
    const item = document.createElement('span');
    item.className = 'leg-item'; // existing class
    item.style.cursor = 'pointer';
    item.innerHTML = `<span class="swatch" style="background:${ds.borderColor}"></span>${ds.label}`;
    item.addEventListener('click', () => {
      const isActive = item.classList.contains('active');
      // reset all
      chart.data.datasets.forEach((d, j) => {
        d.borderWidth = j === chart.data.datasets.length - 1 ? 1 : 2; // FTSE stays thin
        d.borderColor = d._origColor;
      });
      el.querySelectorAll('.leg-item').forEach(x => x.classList.remove('active'));
      if (!isActive) {
        // fade all except clicked + FTSE
        chart.data.datasets.forEach((d, j) => {
          const isFtas = j === chart.data.datasets.length - 1;
          const isTarget = j === i;
          d.borderWidth = isTarget ? 3 : (isFtas ? 1 : 2);
          d.borderColor = isTarget ? d._origColor
                        : isFtas  ? d._origColor + '99'  // 60% opacity via hex alpha
                        : d._origColor + '26';           // 15% opacity
        });
        item.classList.add('active');
      }
      chart.update('none'); // skip animation on toggle
    });
    el.appendChild(item);
  });
}
// Store original colours before any mutation
chart.data.datasets.forEach(d => d._origColor = d.borderColor);
buildDiagLegend(chart);
```

Active legend item: add a bottom-border underline `border-bottom: 1.5px solid currentColor` to show selection state.

**Applies to:** `stage-05-build-spec.md §Per-signal diagnostics chart` and `stage-05-design-final.md §1.2.3`. The custom legend section in those specs should reference this interactive behaviour.

---

## U3 — Data-freshness indicator in header

**Problem.** The "Generated at" timestamp is 10px text in the page footer — invisible unless you scroll to the bottom. For a trading signal tool this is operationally risky: a user looking at 7-hour-old signals and not knowing it may act on positions that have already played out. Freshness needs to be in the header, permanently visible, and colour-coded.

**Fix.** Add a freshness chip to the right side of every page header, between the page title and the nav link. Chip reads the `generated_at` ISO timestamp from whichever JSON file the page loads, computes age in minutes, and applies one of three states:

| Age | Colour | Text |
|-----|--------|------|
| < 2 hours | Green | `Updated {N} min ago` |
| 2–4 hours | Amber | `Updated {N} h ago — re-run refresh?` |
| > 4 hours | Red | `Updated {N} h ago — data may be stale` |

**Chip HTML:**
```html
<div id="freshness" class="text-xs flex items-center gap-1.5 px-3 py-1 rounded-full border {stateClass}">
  <i class="{icon}"></i>
  <span id="freshness-text"></span>
  <span class="text-[10px] opacity-60">{HH:MM UTC}</span>
</div>
```

State classes:
- Green: `bg-emerald-50 border-emerald-300 text-emerald-700`, icon `ti-refresh`
- Amber: `bg-amber-50 border-amber-300 text-amber-700`, icon `ti-clock`
- Red: `bg-rose-50 border-rose-300 text-rose-700`, icon `ti-alert-triangle`

**Computation (runs once on page load after JSON fetch):**
```javascript
function renderFreshness(generatedAt) {
  const ageMin = Math.floor((Date.now() - new Date(generatedAt)) / 60000);
  const ageH   = (ageMin / 60).toFixed(1);
  const utc    = new Date(generatedAt).toISOString().slice(11, 16) + ' UTC';
  let cls, icon, text;
  if (ageMin < 120) {
    cls = 'bg-emerald-50 border-emerald-300 text-emerald-700';
    icon = 'ti-refresh'; text = `Updated ${ageMin} min ago`;
  } else if (ageMin < 240) {
    cls = 'bg-amber-50 border-amber-300 text-amber-700';
    icon = 'ti-clock'; text = `Updated ${ageH} h ago — re-run refresh?`;
  } else {
    cls = 'bg-rose-50 border-rose-300 text-rose-700';
    icon = 'ti-alert-triangle'; text = `Updated ${ageH} h ago — data may be stale`;
  }
  const el = document.getElementById('freshness');
  el.className = `text-xs flex items-center gap-1.5 px-3 py-1 rounded-full border ${cls}`;
  el.querySelector('i').className = `ti ${icon} text-sm`;
  document.getElementById('freshness-text').textContent = text;
  el.querySelector('.utc').textContent = utc;
}
```

**JSON key consumed:** `signals.json.generated_at` (Today page) and same key (Performance page). Already in the data contract — no exporter change needed.

**Footer:** The `Generated …` footer line is kept for completeness/build-sha display but its font-size can increase to 11px now that it is no longer the only freshness signal.

**Applies to:** `stage-05-design-final.md §1.1.1` (Today header), `§1.2.1` (Performance header), `§1.1.5` (footer note update). Both pages share the same `renderFreshness()` function — define it once in a shared `<script>` block.

---

## U4 — Vivid transaction marker palette on annotated price chart

**Problem.** The current marker spec uses indigo triangles for exec buys, lighter indigo for NED buys, and grey squares for grants/SIP/exercise. Against a white chart with a coloured price line these are low-contrast and hard to distinguish at a glance — particularly the two indigo variants from each other, and the grey squares from the background.

**Fix.** Replace the palette with colours that are immediately legible and semantically intuitive. Apply a white halo (paint the marker shape in white first, then repaint the fill colour on top) so markers stand out wherever they overlap the price line.

**New marker palette:**

| Marker | Shape | Fill | Halo | Notes |
|--------|-------|------|------|-------|
| BUY exec (CEO/CFO/Chair) | ▲ triangle up | `#16a34a` green-600 | white, 3px | Saturated green = strong positive signal |
| BUY NED / other director | ▲ triangle up | `#4ade80` green-400 | white, 3px | Lighter green = same family, lower conviction |
| SELL | ▼ triangle down | `#dc2626` red-600 | white, 3px | Vivid red = immediate stop signal |
| GRANT / SIP / EXERCISE | ■ square | `#d97706` amber-600 | white, 2.5px | Warm amber = non-informative, still visible |
| Cluster firing | ○ ring around topmost marker | stroke `#7c3aed` violet-600 | — | 2.5px stroke, no fill — distinct from all fills |

**White halo technique (Chart.js `pointStyle` callback or canvas plugin):**
Draw each marker twice in the `beforeDraw` hook: first at size+3 in white (the halo), then at actual size in the fill colour. For Chart.js annotation plugin markers, set `backgroundColor` to the fill colour and `borderColor: '#ffffff'` with `borderWidth: 3`.

**Rationale.** Green = buy (universally understood in markets). Red = sell. Amber = non-informative corporate action. Violet ring = cluster (deliberately distinct from every fill colour so it doesn't compete with the markers it encircles). The exec/NED distinction is maintained within the same green family — same semantic direction, visually ranked by saturation to reflect conviction level.

**Applies to:** `stage-05-1-company-page.md §3 — Annotated price chart`. No other pages use transaction markers.

---

## What this replaces

The previous v1 roadmap (sector heatmap strip, aging column, kill-candidate panel, Sharpe toggle, cohort drilldown) proposed adding data and features. All five were deprioritised because they add complexity without fixing the usability baseline. They can be revisited once U1–U3 are live and the dashboard has been in daily use for a few weeks.
