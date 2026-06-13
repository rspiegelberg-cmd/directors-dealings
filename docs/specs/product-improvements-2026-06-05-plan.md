# Product improvements — scoping & implementation plan (2026-06-05)

Source: Rupert's brainstorm 2026-06-05. This doc scopes the agreed items into
buildable units with surfaces touched, approach, effort, risk and a per-item
test plan. Two items have their own deeper spec:

- **#5 upcoming earnings dates** → `upcoming-earnings-dates-plan.md` (read that
  before building #5 or idea B — it carries a real data-source decision).
- **idea D conviction sizing** → existing `07-conviction-sizing.md`.

## Decisions locked in the brainstorm

| # | Item | Decision |
|---|------|----------|
| 1 | Absolute return column next to CAR | **Build.** Horizon-matched to the selected CAR horizon. |
| 2 | Sort toggles on every column | **Dropped** (sortable headers already shipped B-103). |
| 3 | Monthly activity: click-through + 12mo totals + rolling trend | **Build.** Totals by **volume and value**; trend = this-month trailing-12 vs last-month trailing-12. |
| 4 | Remove model-assessment box | **Keep for now.** Superseded by idea A (add hit-rate *alongside*, don't replace). |
| 5 | Earnings-within-60-days badge in all boxes | **Build.** Needs a new forward-looking data source — see dedicated spec. |
| 6 | Remove Today box, keep This Week | **Build.** Rename page to "This Week". |
| A | Per-tier live hit-rate | **Build.** |
| B | Earnings proximity as a signal multiplier | **Build** (after #5). |
| C | Cluster conviction score | **Build.** |
| D | Buy size vs skin-in-the-game | **Build** (spec 07 already exists). |
| E | Signal "still live?" indicator | **Build.** |
| F | Morning push digest | **Build.** |

## Effort & sequencing

Effort: **S** ≈ <½ day, **M** ≈ 1–2 days, **L** ≈ 3+ days / needs new data.

| Sprint | Items | Theme | Effort |
|--------|-------|-------|--------|
| 32 | #6, #1, A | Cheap cleanup + high-value display (data already exists) | S + S + M |
| 33 | #5 (Phase A), E | Forward earnings dates + signal-live window | L + S |
| 34 | #3, C | Monthly activity depth + cluster score | M + M |
| 35 | B, D, F | Earnings multiplier, conviction sizing, push digest | M + M + M |

Rationale: front-load the items where the data is already computed (#1, A, #6)
for fast visible wins; isolate the one real data-engineering job (#5) into its
own sprint; defer items that *depend* on #5 (B) until it has landed.

---

## Shared architecture notes (read once)

- **Render layers.** Dashboard HTML is built by `.scripts/dashboard/render_index.py`
  (Today/This-Week page), `render_performance.py` (performance page),
  `render_company.py` (per-company page), with shared helpers in
  `render_helpers.py`. Data is shaped upstream by `export_dashboard_json.py`
  and assembled by `build_dashboard.py`.
- **Three-layer rule for any new signal id / field** (memory
  `feedback_signal_id_three_layer_surface`): a new id touches (1) the Python
  render helper constants, (2) `export_dashboard_json` `SIGNAL_ORDER`, and
  (3) the JS-side `SIDS` array. Budget for all three.
- **Zone discipline (FUSE).** Claude edits the `.py`/`.html` render + export
  code (Zone A). Rupert runs the write-path scripts from PowerShell
  (`export_dashboard_json`, `build_dashboard`, and any backfill) — Zone B.
  Every item below ends with "Rupert runs: …".
- **Default-sort semantics.** `_sort_today` (tier-first) and `_sort_week`
  (chronological) are intentionally split in `render_index.py` — don't merge.

---

## Item 1 — Absolute return (since transaction) column next to CAR

**Goal.** Next to each CAR figure, show the raw stock return since the
transaction, **measured over the same horizon as the displayed CAR** so the two
numbers are comparable (CAR is benchmark-relative; absolute is the raw move).

**Why.** A +5% CAR on a stock that fell 10% (sector fell 15%) is a "good
relative, bad absolute" outcome. Showing both stops a relative number being
mistaken for money made.

**Surfaces.**
- The building block already exists on the company page:
  `render_company.py` computes `abs_ret_pct = latest_close / price − 1` (~line 378).
- Missing on the **main dashboard CAR table** and the **performance cohort
  table**. The CAR cell is rendered in `render_helpers._firing_row` (`car_html`).
- Data: each firing row already carries the entry price and CAR per horizon in
  the exported JSON; absolute return per horizon = `close_at_horizon / entry − 1`.
  Confirm whether `close_at_horizon` is already exported per row; if only
  `latest_close` is present, add per-horizon closes in `export_dashboard_json`.

**Approach.**
1. In `export_dashboard_json`, ensure each firing row exposes
   `abs_return_t1 / _t21 / _t90` alongside the existing `car_t*`.
2. In `render_helpers`, add an "Abs %" column adjacent to "CAR %"; on the
   horizon toggle (existing `horizonChange` event), swap both columns together.
3. Label explicitly: header "Abs (since txn, T+21)" so the window is never
   ambiguous.

**Effort.** S–M (M if per-horizon closes aren't yet exported).

**Risks.** Window-mismatch is the whole trap — the absolute column MUST follow
the horizon toggle, not silently show "to today". QA must verify the two
columns change horizon in lockstep.

**Test plan.** Unit: a row with known entry/benchmark/closes returns correct
`abs_return_t*` and `car_t*`. Visual: toggle T+1/21/90 and confirm both columns
move together; a known sector-down case shows +CAR / −Abs.

**Rupert runs:** `export_dashboard_json` → `build_dashboard`.

---

## Item 3 — Monthly trading activity: click-through + 12-month totals + rolling trend

**Goal.** Three additions to the monthly buy/sell activity chart:
1. Click a buy or sell block → drill to the individual transactions behind it.
2. Show 12-month **buy and sell totals**, by **volume** (count) **and value (£)**.
3. Show whether the trailing-12-month total is **growing or declining**:
   compare *this month's* trailing-12 vs *last month's* trailing-12.

**Why.** Turns a descriptive bar chart into a trend signal — is insider buying
accelerating or fading across the market?

**Surfaces.**
- Monthly buy/sell data is built in `export_dashboard_json` (the `by_month` /
  buy-sell series; B-102) and rendered on the performance page
  (`render_performance.py`).
- Drill-down infra already exists: `render_performance_drilldown.py` +
  `cohort_drilldown` pattern (B-075 added CSV download on drill-downs).

**Approach.**
1. **Click-through:** reuse the existing drilldown payload shape; emit a
   `month_iso → {buys:[txn…], sells:[txn…]}` map keyed to the activity chart and
   wire the same modal/table the cohort drilldown uses.
2. **12-month totals:** in `export_dashboard_json`, aggregate trailing-12 buys
   and sells as both count and £ value. £ value is the meaningful "conviction"
   measure but the dirtier input (depends on clean price×shares) — show count as
   the secondary, reliable figure.
3. **Rolling trend:** compute `trailing12(this_month)` and
   `trailing12(prev_month)`; render an up/down glyph + delta% (reuse the
   existing ▲/▼ convention). **Direction is decided on £ value** (Rupert
   2026-06-05); show volume alongside as the secondary figure.

**Effort.** M. This is the only item of the batch that is real scoping work —
give it the most QA attention.

**Risks.** "Totals of what?" ambiguity — answered: volume + value. £ value
depends on clean transaction amounts; flag/exclude rows with null value rather
than zero-fill (would understate). Rolling comparison needs ≥13 months of data
to be meaningful — show "n/a" until the window fills.

**Test plan.** Unit: trailing-12 sums for a fixture with a known month boundary;
trend sign correct when this-month > last-month and vice versa; null-value rows
excluded from £ but counted in volume. Visual: click a block → correct txns;
trend glyph matches the numbers.

**Rupert runs:** `export_dashboard_json` → `build_dashboard`.

---

## Item 5 — Upcoming earnings badge (the real gap)

**Diagnosis (confirmed in code).** `backfill_reporting_dates.py` scrapes
Investegate for **past** results announcements (Prelim / Interim / Trading
Statement) and stores them in `reporting_dates`. `build_dashboard.py` (~lines
264–309) correctly asks for `report_date >= today` to draw the 60-day badge —
but the table holds only *historical* dates, so the future set is essentially
empty and **the badge is permanently dark**. The "past earnings" Rupert can see
is this historical data; there is no forward-looking earnings calendar anywhere
in the system.

**Fix.** Requires a forward-looking source. Full plan in
`upcoming-earnings-dates-plan.md`. Headline:
- **Primary (build first):** scrape the **lse.co.uk Financial Diary** — a free,
  central, forward-looking calendar that is date-addressable, carries a ticker on
  every event, and covers AIM small-caps + main market. Scrape by **date** (a
  dozen fetches covers the next quarter) on a nightly/fortnightly batch; write
  confirmed future dates. This is the real source Rupert asked for.
- **Fallbacks:** "Notice of Results" RNS for any gaps; a per-company IR-page
  helper for ad-hoc lookups; synthetic "(est)" projection only as a last resort.

**Effort.** L (Phase A is M; Phase B is M and optional).

**Rupert runs:** new `backfill_lse_diary` (Zone B) → `export_dashboard_json` →
`build_dashboard`. Exact sequence in the dedicated spec.

---

## Item 6 — Remove the Today box, keep This Week, rename the page

**Goal.** Drop the "Today's buy signals" table; keep the "This week" sub-table;
rename the page "This Week".

**Why.** UK RNS PDMR volume is low, so the Today table is empty most days, and —
per Rupert's observation — today's firings already appear in the This Week table.
Dead weight.

**Surfaces.** `render_index.py`: `today_section` builds both tables
(~lines 341–353); page `<title>` "Directors Dealings - Today" (~line 499); the
top-strip "Signals today" tile (~line 269).

**Approach.**
1. Remove the Today table render; keep the This-Week sub-table (promote it to the
   section's main table).
2. Rename `<title>` and the visible H2 to "This Week".
3. **Decided (Rupert 2026-06-05):** **keep** the "Signals today" count tile in
   the top strip — it's a single freshness number, not the redundant table.

**Effort.** S.

**Risks.** Don't orphan the page label from content (the rename is the point).
`_sort_today` becomes unused — delete it or leave with a deprecation note;
grep callers first (memory `feedback_grep_all_callers_before_edit`).

**Test plan.** Visual: page renders This Week only, title correct, no empty Today
block. Unit: existing `render_index` tests updated for the removed table.

**Rupert runs:** `build_dashboard`.

---

## Idea A — Per-tier live hit-rate (alongside model assessment, not replacing it)

**Goal.** Show, per signal tier, the share of fired signals with positive
abnormal return at a horizon, e.g. "T1a buys: 62% positive at T+21 (n=48)".

**Why.** This is the evidence that makes a tier trustworthy — converts "here are
signals" into "here are signals that have historically worked".

**Surfaces.** The data is **already computed**: `export_dashboard_json` emits
`hit_rate_t21`, `hit_rate_t21_rolling_6m` (and t1/t90/t252) per monthly cohort,
plus `horizon_aggregates` per signal. Performance page `render_performance.py`.

**Approach.** Add a compact per-tier hit-rate panel/table on the performance page,
reading the existing aggregates; respect the existing horizon toggle. Because #4
is now "keep", this sits **next to** the model-assessment box, not in place of it.

**Effort.** M (mostly presentation; data exists).

**Risks.** Small-n noise — show n alongside % and grey/flag tiers with n<10
(reuse the existing low-N styling already used on the cohort chart).

**Test plan.** Unit: panel rows match the aggregates for a fixture. Visual: hit-
rate updates on horizon toggle; low-n tiers visibly de-emphasised.

**Rupert runs:** `export_dashboard_json` (if any new aggregate added) →
`build_dashboard`.

---

## Idea B — Earnings proximity as a signal multiplier

**Goal.** A buy landing shortly *before* earnings is a stronger signal — let
earnings-proximity escalate the tier (or add a conviction modifier), not just
paint a neutral badge.

**Depends on #5** (needs reliable forward dates). Scope after #5 lands.

**Surfaces.** Signal engine (`.scripts/signals/`), `eval_signals.py`, and the
badge layer. New modifier id → remember the three-layer rule.

**Approach (sketch).** Define a pre-earnings window (e.g. buy 0–30 days before a
confirmed/expected results date) and either (a) bump tier by one notch, or
(b) attach a "pre-earnings" conviction flag scored separately so its performance
is measurable independently (per `feedback_per_bucket_signal_granularity` —
prefer (b)). Phase-gate with a diff-first report (per
`feedback_phase_gated_diff_first`) since it shifts firing counts.

**Effort.** M. **Risks.** Don't silently change historical firing counts without
the diff report. **Test plan.** Lookahead-bias test (P3-6 discipline) must pass —
the multiplier may only use information available at transaction time.

---

## Idea C — Cluster conviction score

**Goal.** Rank the existing Active-clusters panel by a single conviction score
combining: number of distinct directors, % of board, aggregate £, and time-
compression of the buys.

**Why.** Multi-director clustered buying is the strongest signal in the
literature; today the panel lists clusters but doesn't rank their intensity.

**Surfaces.** Cluster definition already exists (S1; `export_dashboard_json`
header notes: 2+ distinct directors, all BUYs, within 30 days). Cluster panel in
`render_index.py`; spec `01-cluster-detector.md`.

**Approach.** Compute a score in the export from fields already on the cluster
object; sort the panel by it; show the score as a small chip. Keep the formula
transparent (documented, not a black box).

**Effort.** M. **Risks.** % of board needs board size — if unavailable, score on
the three fields we have and note the omission. **Test plan.** Unit: score
ordering for fixtures with known director-count/£/compression.

**Rupert runs:** `export_dashboard_json` → `build_dashboard`.

---

## Idea D — Buy size vs skin-in-the-game

Existing spec: **`07-conviction-sizing.md`** — build per that. Headline: a £5k
CEO buy is noise, £500k is conviction; size each buy relative to prior holding
(and/or comp if available). Confirm spec 07 is still current before starting;
re-scope only the delta.

**Effort.** M. **Rupert runs:** per spec 07.

---

## Idea E — Signal "still live?" indicator

**Goal.** Show how much of a signal's actionable horizon remains (or grey out
signals whose window has closed) so stale signals aren't chased.

**Why.** A signal is only actionable for a window; today nothing distinguishes a
2-day-old signal from a 200-day-old one at a glance.

**Surfaces.** `render_helpers` (firing row / badge), `render_index` table.
Pure display from `fired_at` + today; no new data.

**Approach.** Compute `days_since_fired`; map to a small state — e.g. "live"
(within chosen horizon), "ageing", "closed (>90d)". Render as a subtle chip or
row de-emphasis. Reuse `h.days_since`.

**Effort.** S. **Risks.** Choose the window that defines "live" deliberately
(tie to the dominant horizon, e.g. T+90). **Test plan.** Unit: state mapping for
boundary ages (0, 89, 90, 200 days).

**Rupert runs:** `build_dashboard`.

---

## Idea F — Morning push digest

**Goal.** A 7am email: "3 new director buys overnight — 1 is a pre-earnings T1a
cluster at XYZ." Turns the dashboard from pull to push.

**Why.** High leverage for someone trading off it — the signal finds you.

**Recipient (Rupert 2026-06-05):** send to **rspiegelberg@gmail.com**.

**Approach.** Out of the dashboard-render path. Two options:
1. **In-app scheduled task** (a `create_scheduled_task` running a summary over
   the latest export JSON) — simplest if Rupert wants it inside this assistant.
2. **Standalone script** Rupert schedules via Windows Task Scheduler that reads
   the freshest `dealings.json` and sends mail.
Still to decide: the **send mechanism** (which option above; if standalone, the
SMTP/app-password setup for the gmail send). This depends on the pipeline having
run that morning — sequence after refresh.

**Effort.** M. **Risks.** Don't send if the morning pipeline failed (stale data) —
gate on export freshness. **Test plan.** Dry-run mode prints the digest without
sending; verify "no new signals" path produces a sensible (or suppressed) email.

---

## Decisions captured 2026-06-05

- **#6:** keep the "Signals today" count tile. ✓
- **#3:** rolling trend direction decided on **£ value** (volume shown alongside). ✓
- **F:** digest sent to **rspiegelberg@gmail.com**. ✓
- **#5:** use a real central free source (lse.co.uk Financial Diary) on a
  nightly/fortnightly batch — not synthetic estimates. ✓ Full strategy in
  `upcoming-earnings-dates-plan.md`.

## Open questions still outstanding

1. **F:** send mechanism — in-assistant scheduled task vs a standalone Windows
   script (and, if standalone, the gmail SMTP/app-password setup)?
2. **#5 cadence:** nightly (recommended, still cheap) or fortnightly?
3. **#5 quarterly results:** keep `Q1–Q4` as a distinct type or fold into the
   trading-statement badge bucket?
4. **#5 fallback:** allow the synthetic "(est)" gap-filler, or only ever show
   confirmed dates and leave gaps blank?
