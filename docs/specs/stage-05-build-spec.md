# Stage 5 — Dashboard build spec

**Status:** Build-ready 2026-05-13. Gated by Stage 4 + Stage 4.5 completion.
**Owner:** Rupert. Designer: `docs/agents/dashboard-designer.md`.
**Reads:** the locked decisions in `docs/specs/stage-05-design-notes.md`.

## Architecture

- Single-file HTML at `dashboard/directors-dealings-dashboard.html`.
- Static page. No build step. Tailwind CDN + Chart.js CDN.
- Reads two JSON exports written by a Stage 4 / Stage 4.6 exporter:
  - `dashboard/data/signals.json` — per-signal aggregates by horizon + active clusters + paper P&L.
  - `dashboard/data/dealings.json` — today + this-week transaction feed with joined signals.
- Loaded via `fetch()` on page open. Manual reload only — no polling.

## Data contracts (Stage 4 produces, Stage 5 reads)

### signals.json
```
{
  generated_at: ISO,
  horizon_aggregates: {
    t1|t21|t90|t252: {
      base_rate: float,
      signals: {
        t0|t1|t2|t3|t4|s1|f1: {
          trades: int,
          hit_pct: float,
          median_car: float,
          mean_car: float,
          edge: float,
          sparkline: [9 floats — rolling 12w median CAR],
          status: "live" | "review" | "kill?" | "gated",
          outlier_flag: bool
        }
      }
    }
  },
  active_clusters: [
    { ticker, company, director_count, aggregate_value_gbp,
      first_buy_date, last_buy_date, s1_active: bool }
  ],
  paper_pnl_open: float,
  paper_trades_open: int,
  paper_trades_closed: int
}
```

### dealings.json
```
{
  generated_at: ISO, as_of_date: YYYY-MM-DD,
  signals_today_count: int,
  signals_today_delta_vs_avg: int,
  today: [
    { time_utc, ticker, company, director, role, txn_type,
      value_gbp, signals_fired: [strings], mtm_pct: float|null }
  ],
  this_week: [ same shape, last 7 days excluding today ]
}
```

Stage 4's backtest writes `_backtest_results.csv`. A new exporter (`.scripts/export_dashboard_json.py`) aggregates that CSV plus the live transactions table into the two JSON files above. Owner of the exporter: Stage 4.6 task, to be specced separately.

## Component specs

### Top strip (3 tiles)

Wrapper: `<div class="grid grid-cols-3 gap-2 mb-4">`. Each tile: `<div class="bg-slate-100 rounded-md p-3">`.
- Tile 1: signals_today_count + delta. Green delta if positive, red if negative, glyph + sign.
- Tile 2: active_clusters.length, sub-label "2 new this week" when new clusters present.
- Tile 3: paper_pnl_open formatted as £±N, with a `<select>` filter (All / per-signal). Filter persists in localStorage key `dd_paper_filter`.

### Per-signal scoreboard (centrepiece)

Wrapper: `<div class="bg-white border border-slate-200 rounded-lg overflow-hidden">`.
Table: `<table class="w-full text-xs" style="font-variant-numeric: tabular-nums;">`.
Header columns (widths in %): 8 / 8 / 13 / 12 / 12 / 10 / 13 / 11 / 13.
Iterate signals in fixed order: T0, T1, T2, T3, T4, S1, F1.

Per row:
- **Badge cell:** `<span title="{tooltip}" class="cursor-help" style="background:{bg};color:{fg};font-size:10px;padding:2px 6px;border-radius:10px;{border}">{id}</span>`. Tooltip text from the table in design-notes.md. Tier colours from the tier-colour table below.
- **Trades cell:** integer, right-aligned.
- **Hit % / base:** show as `"{hit}% / {base}%"`. hit_pct in green if ≥ base_rate, red otherwise.
- **Median CAR:** pct() formatted. Green if > +0.05%, red if < −0.05%, neutral otherwise.
- **Mean CAR:** same. For F1 only, append `<span title="Outlier — Stage 4.5">⚠</span>` after the number while outlier_flag is true.
- **Edge:** pct() formatted, same colour rule.
- **Sparkline:** inline SVG 64×18, polyline of the 9 sparkline points normalised into the viewBox (min → y=17, max → y=1). Stroke: emerald if linear-fit slope ≥ +0.5%, amber if between, rose if ≤ −0.5%.
- **Status pill:** `● live / review / kill? / gated` with colours emerald / amber / rose / amber.
- **Deprecate button:** disabled if status === "gated". Click → confirm() then a toast saying "Edit `.scripts/signals/{id}_v{n}.py` and set SIGNAL_VERSION = 'deprecated'". V1 ships with no `/api/deprecate` endpoint — the button is an instruction, not an action.

Below the table: footer caption `"Window: {label} · base rate {base}% = % of random {label} FTSE All-Share holds positive. Hit % shows percent of trades that beat that base."`

**Tier-colour table** (used by badges everywhere):

| Signal | bg | fg | border |
|---|---|---|---|
| T0 | #534AB7 | #fff | — |
| T1 | #3C3489 | #fff | — |
| T2 | #7F77DD | #fff | — |
| T3 | #AFA9EC | #26215C | — |
| T4 | #EEEDFE | #534AB7 | 0.5px #AFA9EC |
| S1 | #C0DD97 | #173404 | — |
| F1 | #FAC775 | #412402 | — |

### Horizon dropdown — shared state

Single top-level state `state.horizon`, dropdown above scoreboard mutates it, dispatch a `CustomEvent('horizonChange')`. Both the scoreboard and the diagnostics chart listen and re-render. Options: T+1 (next session), T+21 (≈1 month), T+90 (≈4.5 months), T+252 (≈1 year). Default: t21.

### Today's buy signals table

Columns: **Signals · Time · Ticker · Company · Director+Role · £ Value · MTM*.**
(UX fix U1 2026-05-14: Signals moved to column 1 — highest-priority information first. Role merged into Director cell as a chip, eliminating the separate Role column. Net: 8 → 6 columns.)
Sort default: by maximum severity of `signals_fired` ascending (T0=0 strongest). Within tie, by value_gbp descending.
Row click: `window.open('companies/' + ticker + '.html', '_blank')`. Set `cursor: pointer` on every row.
Signal-badges use the shared component (tooltips identical to scoreboard).
Director+Role cell: director name on top line, role chip below (`CEO/CFO` = indigo, `Chair` = violet, `NED/Non-Exec` = slate).
MTM null → render `—` in `text-slate-400`. Non-null → green / red with chevron glyph.
Footer micro-text: `"*MTM = mark-to-market from T+1 close after RNS, net of 50bps spread + stamp (non-AIM)."`

### Active clusters panel

Right column on desktop. One card per cluster (card spec):
```
{ticker} · {company truncated to 18 chars}
{S1 badge if s1_active else "brewing" amber badge}
{director_count} dirs · £{aggregate_value_gbp/1000}k · {first_buy_date} – {last_buy_date}
```
Card click → `window.open('companies/' + ticker + '.html', '_blank')`.
Below the panel: caption `"≥2 distinct directors buying same ticker, ≤30d apart, most recent buy ≤90d."`

### Per-signal diagnostics chart

Chart.js line chart. 8 datasets (T0, T1, T2, T3, T4, S1, F1, FTSE All-Share). Border colours per the tier table; FTSE A-S is dashed grey `#888780`, `borderDash: [6, 4]`. Y axis is cumulative CAR at `state.horizon`. X axis: 13 points labelled `M-12 ... Now`.
Container: `<div class="bg-white border border-slate-200 rounded-lg p-3"><div class="relative h-44"><canvas></canvas></div></div>`.
Reacts to horizonChange events.

**Interactive legend (UX fix U2 2026-05-14):** Custom HTML legend below canvas. Each swatch is clickable — clicking highlights that dataset (full opacity, borderWidth 3) and fades all others to 15% opacity (hex alpha `26`). FTSE All-Share baseline never fades below 60% opacity — it is the reference line. Clicking the active swatch again resets all datasets to full opacity. Active swatch indicated by a 1.5px bottom-border underline. Store original `borderColor` on each dataset as `_origColor` before any mutation. Call `chart.update('none')` on toggle to skip animation. See `stage-05-roadmap-v1.md §U2` for full implementation snippet.

### Cohort cuts (two blocks side by side)

Block A — director-transaction-value buckets:
4 vertical bars (£1–25k / £25–100k / £100–500k / £500k+). Bar height proportional to |median_car_t21|. Green if positive, rose if negative. Read from `signals.json.cohorts.by_value_bucket` (a new key Stage 4.6 exporter must produce).
Block B — sector hit rate:
5 rows, sorted descending. Hit % with colour vs 51% base rate. Read from `signals.json.cohorts.by_sector`.

### Signal-badge component (shared)

Single function `renderBadge(signalId, opts)`. Returns the `<span>` HTML used by scoreboard, today's table, active clusters, drill-down. Tooltip text embedded inline in the script — single source of truth, taken verbatim from `stage-05-design-notes.md` tooltip table.

## Build order

1. Create `dashboard/` folder + HTML skeleton + Tailwind CDN + Chart.js CDN.
2. Write `.scripts/mock_dashboard_data.py` producing realistic `signals.json` + `dealings.json` so dashboard renders before Stage 4 lands.
3. Implement `renderBadge()` + tooltip table.
4. Top strip (3 tiles).
5. Per-signal scoreboard + horizon dropdown + shared state.
6. Today's buy signals table.
7. Active clusters panel.
8. Per-signal diagnostics chart wired to horizonChange.
9. Cohort cuts.
10. Replace mock data with real Stage 4.6 exporter output.
11. Visual QA against wireframe v2.

After each step the designer agent QAs the slice against the wireframe before moving on.

## Out of scope for v1

- Conviction-weighted sizing toggle → v1.1, after 2 weeks of flat-sizing reference data.
- Mobile-responsive layout → deferred per Rupert.
- Alerts (push/email) → not in any current version.
- Sell signals → v2.
- Auto-refresh / polling → manual reload only.
- Dark/light toggle → ship one mode.
- Filter chips on today's table → explicitly out per Rupert ("less is more").
- F1 numeric display → gated until Stage 4.5 outlier fix lands; row stays visible with status "● gated", numbers replaced by `—`.
- `/api/deprecate` server endpoint → v1 ships with a manual code-edit instruction in the toast.
- Company-page route → separate spec, see `docs/specs/stage-05-1-company-page.md` (drafted 2026-05-13).

## Acceptance criteria

- Page renders with mock JSON in under 200ms on Rupert's machine.
- Every signal badge has a working hover tooltip with the rule from design-notes.md.
- Horizon dropdown change re-renders scoreboard, every sparkline, AND the diagnostics chart (single horizonChange event).
- Sort default on today's table is by signal severity ascending (T0 first), then by value desc.
- Row click on today's table opens the company page in a new tab.
- Status pill colour matches the auto-computation rule.
- Deprecate button is disabled on gated rows.
- Tabular numerals on every numerical column.
- Zero JS console errors with real Stage 4.6 JSON.

## Gates before implementation

1. **Stage 4 ships** — signal engine + backtest harness with T+1/T+21/T+90/T+252 outputs in `_backtest_results.csv`.
2. **Stage 4.5 ships** — F1 +4232% outlier fix (Yahoo `adjclose` switch). Without it F1 numbers are hidden behind the gated status.
3. **Stage 4.6 exporter specced + built** — converts Stage 4 CSV + live transactions table into the two JSON files in the data contract.
4. **Company-page spec drafted** at `docs/specs/stage-05-1-company-page.md` (done 2026-05-13). Row-click destination exists.

Implementation starts only when those four are green. Designer agent invoked to QA each panel before sign-off.

## Risks / open questions

- The sparkline data assumes a rolling weekly aggregation. Stage 4.6 must produce 9 points per signal per horizon — that's 7 × 4 × 9 = 252 floats per refresh. Trivial.
- "Brewing" cluster definition (cluster of 2+ directors but not yet S1-active because most recent buy is between 90 and 30 days back) is in the wireframe but not in spec 01. Confirm with Rupert before build or remove from v1.
- The deprecate button as v1 "instruction toast" is honest but a little awkward. Consider whether an actual write-to-disk endpoint is worth the small additional engineering. Default: not for v1.
