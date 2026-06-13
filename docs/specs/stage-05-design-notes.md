# Stage 5 — dashboard design notes (locked decisions)

**Status:** In flight. Wireframe v2 reviewed 2026-05-13. Locked decisions below; formal Stage 5 spec to follow.
**Owner:** Rupert
**Agent:** `docs/agents/dashboard-designer.md`

## Locked design decisions (this session)

**Purpose.** Dual-mode: daily check for actionable signals AND trailing analytics tool for assessing whether each signal is generating excess return. Diagnostics surface gets ~60% of the page.

**Top strip.** Three small tiles only: signals today (with 7d delta), active clusters, open paper P&L (with signal-type filter).

**Centrepiece — per-signal scoreboard.** One row per signal type (T0, T1, T2, T3, T4, S1, F1). Columns: Signal · Trades · Hit % vs base rate · Median CAR · Mean CAR · Edge · 12-week sparkline · Status · Deprecate button.

**Horizon dropdown** (locked options):
- T+1 (next session)
- T+21 (≈1 month)
- T+90 (≈4.5 months)
- T+252 (≈1 year)

All four are already produced by the spec 05 backtest harness — no upstream change needed. T+1 added for immediate-reaction reads. Same dropdown also drives the multi-line diagnostics chart below.

**Status auto-computation.**
- ● live = median CAR positive vs base rate for ≥2 review cycles
- ● review = negative for 1 cycle
- ● kill? = negative for ≥2 cycles
- ● gated = blocked by upstream data quality (F1 until Stage 4.5 ships)

**Deprecate button.** One click per row. Sets `signal_version = "deprecated"` per spec 05 convention. Old firings preserved; new evaluations stop. Disabled for ● gated rows.

**Today's signals table.** Columns: time · ticker · company · director (role chip) · £ value · signal badges · MTM. Sort default: signal severity descending. **Row click opens a per-ticker company page in a new tab** (price chart, OHLCV volume, transaction history, signal-firing history for that ticker). No in-line drill-down panel.

**MTM definition.** Mark-to-market from T+1 close after `announced_at`, net of 50bps spread + 0.5% stamp duty on non-AIM buys. NOT director's trade price, NOT the announcement price.

**Active clusters panel.** Definition (per spec 01): ≥2 distinct directors at the same ticker, all BUYs, dates within 30 days of each other (connected-component), most recent buy within 90 days. Card click → company page. "Brewing" badge for clusters not yet matured.

**Per-signal diagnostics chart.** Multi-line: one line per signal type plus FTSE All-Share. Cumulative CAR over trailing 12 months. Honours the horizon dropdown.

**Cohort cuts.**
- By director's transaction value (£1–25k, £25–100k, £100–500k, £500k+) — tests spec 07 conviction hypothesis.
- By sector — hit rate paired with base rate.

## v1 scope decisions (locked)

| Topic | Decision |
|---|---|
| Sizing | Flat £1k default. Conviction-weighted toggle ships in v1.1 (after 2 weeks of flat reference data) |
| Alerts | None in v1 |
| Mobile | Deferred — desktop-first |
| Filters | None on the today table. "Less is more — sort by severity, look at top options" |
| RNS link | Open in new tab |
| Sells | Excluded from v1 |
| Refresh | Manual only |
| F1 | Gated until Stage 4.5 (+4232% outlier fix) |

## Upstream dependencies

- Stage 4 backtest harness must compute CAR at T+1, T+21, T+90, T+252 (already in spec 05).
- Stage 4.5 outlier remediation must land before Stage 5 ships — otherwise F1 mean is a lie at every horizon.
- The company-page route is its own deliverable. Not yet specced; needed before row click in Stage 5 is real.

## Sparkline spec (scoreboard "12w trend" column)

- Data: rolling 12-week median CAR for that signal type at the currently selected dropdown horizon. Switching horizon re-renders every sparkline.
- Geometry: 64x18 SVG, 9 data points (~1.3 weeks per step). No axes, no labels.
- Colour: computed from the slope of a linear fit across the 9 points — emerald for clearly positive slope, amber for flat or mildly negative, rose for steep negative.
- Purpose: detect edge decay. A positive median CAR with a falling sparkline means today's good number is the residue of better past performance; the signal is fading and may drop to "review" before the median catches up.
- Why it earns the column: point-in-time medians lag trend changes by months. Sparkline closes that gap.

## Signal-badge tooltips

Every signal badge (scoreboard, today's table, active-clusters panel, drill-down) must show a hover tooltip with the signal definition. Source: spec 05 taxonomy.

| Signal | Tooltip text |
|---|---|
| T0 | Cluster + opportunistic combo. CEO/CFO/exec buy (T1/T2) inside a multi-director cluster (S1) within 30 days. Highest conviction. |
| T1 | CEO/CFO buy >= £100k. Strongest single-trade insider signal. |
| T2 | Other exec buy (Chair, Group, Director) >= £25k. Mid-conviction. |
| T3 | NED (non-executive director) buy >= £10k. Lower conviction. |
| T4 | Other discretionary buy >= £1k. Catch-all. |
| S1 | Cluster — >=2 distinct directors buying same ticker, dates within 30 days. |
| F1 | First-time buy — director's first-ever buy of this ticker. |

- v1 implementation: native HTML `title` attribute. Works everywhere, accessible, no JS.
- v1.x upgrade: custom HTML tooltip with bold signal name on first line, rule below. 200ms hover delay.
- Mobile (deferred): tap surfaces a small bottom-anchored sheet.

## What comes next

1. Resolve remaining column tooltip detail (formula pop on the Edge header).
2. Write the formal Stage 5 design spec (full IA, exact Tailwind classes, copy strings, data fields per panel, out-of-scope items).
3. Hand to general-purpose agent for production HTML implementation.

Do not start production HTML until the Stage 5 spec is gated by Rupert.
