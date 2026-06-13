# Product fixes — diagnosis & proposed fixes (2026-06-06)

Eleven items Rupert raised, each investigated read-only by a specialist agent.
This is a **plan-first proposal** — nothing has been built or changed. Read,
decide, then we build in priority order with QA + diff-first per the usual rules.

**Legend:** 🔴 = do first (blocking) · 🟠 = real bug · 🟢 = enhancement.
**Zone-B** = Rupert runs from Windows PowerShell (writes DB / regenerates dashboard).

---

## 🔴 #8 — Review "Apply & publish" is stuck (KEYSTONE — fixes #10 too)

**Root cause (confirmed from live state).** A crashed apply run from **June 4
06:46** left `.data\_apply_status.json` frozen on `"status":"running"`. Two
effects:
1. The server refuses to start any new apply while status is "running"
   (`server.py:393-398` returns HTTP 409) → your 19 staged edits can never commit.
2. The progress bar polls that same stale file and spins forever
   (`review.html:1130-1136, 306`) → the "Recomputing signals…" you see.

Why it died with no error: the apply subprocess is spawned with
`start_new_session=True` (POSIX-only — **a no-op on Windows**) and output sent to
DEVNULL, so closing the server window / sleep killed it mid-run and left no log
and no terminal status (`server.py:428-435`, `apply_edits.py:663-668`).
The single June-4 edit **did commit**; your 19 new edits are safe to re-apply.

**This is a genuine bug, not a Zone-B procedure gap** — the button IS wired to
commit via Windows-Python.

**Immediate unblock (Rupert, now, Windows PowerShell):**
```powershell
Remove-Item C:\Dev\DirectorsDealings\.data\_apply_status.json
```
Then click **Apply & publish** again. (Absent file → status reads "idle" →
lock clears. Safe: the 19 edits are new; June-4's edit already committed.)

**Permanent fix (code):**
- Add `/api/apply-reset` endpoint (mirror existing `/api/refresh-reset`) + a small
  "Reset" link in the progress panel.
- Make the running-guard **stale-aware**: treat `running` older than ~15 min as
  recoverable instead of locking forever.
- Guarantee a **terminal status** on any abnormal exit, and redirect the
  subprocess to `.data/_apply_last.log` instead of DEVNULL so the next failure is
  diagnosable.
- Operational note until that lands: **don't close the server window while the
  pipeline is running.**

**Confidence: High.**

---

## 🟠 #10 — Edited/committed items don't drop off the review page

**Root cause.** Mostly **downstream of #8**: the pipeline never finishes, so
`pending_review.json` is never regenerated and resolved items linger. The
drop-off machinery itself is built and correct (`apply_edits.remove_from_pending_queue()`
`apply_edits.py:243-282`; exporter also filters resolved IDs
`export_dashboard_json.py:3057-3122`).

Plus **one genuine frontend miss**: on a successful apply, the `done` handler
(`review.html:324-333`) refreshes the edit queue but **never re-calls
`loadPendingReview()`**, so Tab A keeps showing resolved items until a manual
page reload.

**Fix.** In the `done` branch, also call `loadPendingReview()` (cache-busted).
Note: "update" edits target existing Tab-B transactions and *should* stay (now
corrected) — the disappear expectation is for Tab-A add/reject resolutions.

**Confidence: High.** Fix #8 first, then this one-liner.

---

## 🟠 #1 — "Signals Today" undercounts current-day signals

**Root cause.** The KPI counts transactions by **dealing date** (`t.date`),
but a signal *fires* when the RNS is **announced** (`announced_at`), which lags
the deal by 1–4 business days. Only 8% of rows have a same-day gap, so most of
today's fired signals are excluded. Location: `export_dashboard_json.py`
`build_dealings()`, the `today_txs` query at **lines 1788-1798** (and the
7-day delta query 1817-1826). Evidence: on 2026-06-05, 8 buy-signals fired by
announce date vs only 5 by transaction date.

**Fix.** Bucket by fire-date = `announced_at` when present else `date`. Must
COALESCE (4,351/6,084 rows have blank `announced_at`):
```sql
WHERE date(COALESCE(NULLIF(t.announced_at,''), t.date)) = ?
```
(The exporter already uses this exact idiom at line 2797.)

**Open question:** the This-Week table and Today table also key on `t.date` —
switch those too for consistency? (Larger, diff-first change; the KPI fix alone
closes the reported bug.)

**Confidence: High.**

---

## 🟠 #4 — Oversized Sell bars in Monthly Activity (Mar/May)

**Root cause — NOT a £/pence units bug.** Prices and values are correct. The
big bars are **genuine large block disposals by associated corporate entities /
PCAs**, not individual directors:

| Month | "Director" | What it is | Value |
|---|---|---|---|
| Mar-26 | Potomac View Investments | PCA of HBR Chairman | £153.0M |
| Mar-26 | Advent Global Opportunities | PE fund, PCA of TRST NED | £46.2M |
| May-26 | DKL Energy Limited | corporate holder (ITH) | £61.9M |
| May-26 | Eni UK Limited | corporate holder (ITH) | £44.1M |

These 4 rows = 69% of March sell volume; 8 rows = 80% of May. Built in
`build_monthly_buysell()` (`export_dashboard_json.py:2757-2878`).

**Fix — scope/classification, not units.** Exclude associated-entity sellers so
the chart means "what *directors* are doing." Note: DKL/Eni are tagged
`'Other / unclassified'`, not `'PCA'`, so a PCA-only filter misses them:
```sql
AND COALESCE(t.role_normalized,'') NOT IN ('PCA','Other / unclassified')
```
This shifts firing counts → **diff-first** review.

**DECIDED (Rupert): remove corporate/PCA sell volume from the list.** Caveat to
handle at build time: DKL/Eni are tagged `'Other / unclassified'`, not `'PCA'`,
and excluding that bucket wholesale could drop some legit-but-unclassified
individual directors. Build approach: exclude `'PCA'` outright, and for
`'Other / unclassified'` exclude only rows that look like corporate holders
(entity-name heuristic / non-personal name) so we don't lose real directors —
present the diff for review. Long-term, B-121 parser coverage tags corporate
holders precisely.

**Confidence: High.**

---

## 🟠 #2 — "Open Paper P&L (~£57k)": what it actually is + FTSE comparator

**Critical finding: £57k is NOT a P&L — it's capital deployed.** The figure is
`open_notional_gbp` = the **sum of position sizes** across open positions
(`render_performance.py:3157,3184`). The panel's only return metric is
**"Mean MTM %"** — an *unweighted average of per-position % returns*. There is
**no aggregate £ profit number anywhere in this panel.** If the label reads
"Open Paper P&L", that's a mislabel.

**How it's built** (`build_paper_book_summary`, `export_dashboard_json.py:1299-1496`):
- **Sizing** (`sizing.py:41-75`): £-absolute, log scheme `1000×log10(value/50000)`,
  clamped **£500 floor / £5,000 cap**.
- **Which signals:** all 11 directional BUY signals (t1a…t7, s1, f1, b1);
  excludes b2/t0.
- **"Open"** = age ≤ 21 calendar days (not realised/unrealised).
- **Entry** = first close on/after fire date; **current** = latest close;
  `mtm% = (latest − entry)/entry`.
- Costs (50bps + stamp) are **not** deducted here.
- £57k ÷ ~£1-2k per position ⇒ roughly 30-55 open positions in the rolling
  21-day window.

**AGREED DESIGN (Rupert, 2026-06-06): a £10k-per-signal strategy tracker vs FTSE.**
This is a new panel, distinct from the current "open notional" stat. Rules:
- **Stake:** a fixed **£10,000 per buy signal** (all 11 directional buy signals,
  as the paper book already scopes). Not log-conviction sizing — flat £10k each.
- **Entry:** buy at the **announcement-date** close (`announced_at`, consistent
  with #6), not the dealing date.
- **Exit:** **sell at T+21** (21 trading days after announcement) — realised.
  Positions younger than 21 trading days are still open, marked to market at the
  latest close.
- **Portfolio value** at any date = realised cash from closed (T+21) trades +
  current MTM of still-open trades. Track as a time series so we can show it on a
  **trailing-30-day basis** with an up/down trend and a sparkline.
- **FTSE shadow (like-for-like):** for every signal, also "buy" £10,000 of
  `^FTAS` on the same announcement date and apply the **same T+21 exit and
  accounting**. The FTSE portfolio value is built identically. Headline =
  strategy portfolio value vs FTSE portfolio value (£ and %), plus the trailing-
  30-day trend of each.

**Data:** all present — `^FTAS` closes live in the `prices` table
(`backfill_benchmarks.py`); `announced_at` and per-ticker closes are available;
`backtest.py` already computes T+21 exits, so the realised-trade logic can reuse
its horizon machinery rather than re-deriving it.

**Build shape:** a new builder (e.g. `build_strategy_tracker()` in
`export_dashboard_json.py`) emits, per as-of date over the trailing window:
`{date, strategy_value_gbp, ftse_value_gbp}`, plus summary headline stats
(current strategy £, current FTSE £, £ and % excess, 30-day trend arrows for
both). Renderer adds a panel on the Performance page with the two-line value
series, a sparkline, and the trend chips (reuse existing sparkline helper).
Zone-B deploy (export + build). This likely also wants the Phase-B
`paper_trades` realised path rather than the read-only "open book" — to be
confirmed at spec time.

**Separately, still recommended:** **rename** the existing "Open Paper P&L /
£57k" stat, since it is capital deployed (notional), not a P&L — to avoid
confusion alongside the new tracker.

**Confidence: High** on data/feasibility. This is the largest single build of the
twelve and warrants its own short spec before coding.

---

## 🟠 #3 — Signal toggle on the Paper P&L view does nothing

**Root cause.** The Live-paper-book table is **fully static server-side HTML with
zero JS** (`render_performance.py:3129-3308`) — rows carry no `data-signal-id`,
there is no listener and no filter control in that panel. The toggle you're
clicking belongs to a **different** panel (the CAR-chart legend chips
`render_performance.py:3690-3742`, which toggle chart datasets only, or the
small-multiples grid which filters the cohort table) — neither is wired to the
paper book.

**Fix.** Add a dedicated, self-contained filter to the paper-book panel:
1. Tag each row with its short signal_id: `data-paper-sid="{signal_id}"`
   (already short-form from exporter line 1461).
2. Emit a chip bar above the table (reuse `SIGNAL_DISPLAY_ORDER` + `render_badge`)
   with an "All" reset.
3. Add a small inline `<script>` that shows/hides rows on click.

**Critical:** filter must compare against the **same short id** the exporter
writes and `render_badge` consumes — keep all three layers on the short id to
avoid the known signal_id three-layer mismatch.

**DECIDED (Rupert):** the control is the **badge in the paper-book row** — so the
fix is to make those badges the live filter. Clicking a row's signal badge
filters the paper book to that signal (toggle off to clear). Implementation as
above (tag rows with short signal_id, inline show/hide script), with the badge
itself as the click target rather than a separate chip bar.

**Confidence: High.**

---

## 🟠 #5 — Cohort table: why CAR ≠ Net-of-Costs (it's a labelling problem)

**Finding — correct math, confusing labels. Your mental model is inverted.**
From `backtest.py`:
- **CAR = cumulative *abnormal* return** = stock return **minus** sector
  benchmark (`backtest.py:355-360`). So the **benchmark is already removed inside
  CAR** — *that's* what's "already included," not costs.
- **Net of Costs** = CAR **minus** a flat trading cost (`backtest.py:362-367`):
  50bps (AIM) or 100bps (non-AIM = 50 spread + 50 stamp).

Your example resolves exactly: CAR T+21 +33.3%, benchmark −1.8% (already baked
into CAR, shown for reference), Net of Costs +32.3% = 33.3% − 1.0% (TRST is
non-AIM → 100bps). The columns differ by **only the ~1% cost**, so they look
like near-duplicates sitting side by side, and the "Sector benchmark" column
between them tempts a second (wrong) subtraction.

**Fix — relabel, no math change:**
- "CAR T+…" → **"Excess vs benchmark (T+…)"** + tooltip "stock return minus its
  sector benchmark."
- "Sector benchmark" → **"Benchmark return (already removed)"** / grey it as
  reference.
- "Net of Costs" → **"Excess, after costs"** + tooltip "above, minus 50bps spread
  (+50bps stamp on non-AIM)."
- Optional: **drop the raw CAR column** entirely and keep only cost-adjusted
  (what the rest of the dashboard standardises on) to kill the redundancy.

**Confidence: High.**

---

## 🟢 #6 — New column: absolute stock return since transaction

**Finding — data already on the row.** Raw return = `CAR + benchmark_return`
(the exporter already uses this identity elsewhere, `_firing_row` field
`abs_return`). The cohort `signals[]` rows just don't emit it yet.

**Fix (3 small edits):**
1. Exporter — add `abs_return_t21: _car_t21 + _bench_t21` in both signal-dict
   builders (`export_dashboard_json.py:~2611, ~2641`).
2. Renderer — add one COLS entry `{key:'abs_return_t21', label:'Stock return
   (T+21)', type:'pct'}` (`render_performance.py:2866-2876`); reuses existing pct
   cell renderer, no other JS.
3. Optional — em-dash it while cohort is un-matured.

**DECIDED (Rupert): measure absolute return from the ANNOUNCEMENT date**, not the
T+1 entry close. So the column = (latest close − announcement-date close) /
announcement-date close, raw (no benchmark, no costs). This is a different entry
basis than CAR, so it needs its own small calc: pull the close on/after
`announced_at` per ticker (helper already exists in the paper-book builder),
divide into latest close. Exporter field + one renderer column; no backtest
rerun. **Confidence: High.**

---

## 🟢 #7 — A database to populate SECTOR per stock

**Current state.** Sector comes **only** from a hand-maintained CSV
(`.scripts/sector_map.csv`); **691 of 800 tickers (86%) have no sector**
(snapshot 2026-06-05). The existing sector names are the **11 GICS sectors** —
which is exactly what Yahoo's `assetProfile.sector` returns, so a Yahoo source
needs no remapping.

**Recommendation (lowest effort, fits existing pipeline).** Add a sector step to
`backfill_ticker_meta.py` using Yahoo's `v10/quoteSummary?modules=assetProfile`,
unblocked with the standard cookie+crumb handshake (~15 new lines — Yahoo returns
401 anonymously, which is why it isn't fetched today). One call returns
`sector`, `industry` **and** `website` (also fixes the website gap). Guard with
"only overwrite when NULL" so curated CSV values win. Downstream already reads
`tickers_meta.sector`, so **no exporter changes** — populating the column lights
up the sector tile automatically. This is a **Zone-B write-path script**.

**Fallback if the crumb endpoint proves fragile:** Financial Modeling Prep free
tier (`/api/v3/profile/{TICKER}.L`, 250 req/day, no handshake) — needs a small
taxonomy→GICS map; good for residual AIM names Yahoo can't resolve.

**Confidence: High** on the gap; **Medium** on Yahoo staying unblocked (hence the
fallback). Open Q: want industry/sub-sector too?

---

## 🟢 #11 — Cluster-brewing TREND + 8-week sparkline (new feature)

**Today.** A "cluster" = 2+ distinct directors buying the same ticker with
consecutive buys ≤30 days apart (`detect_clusters.py`). The Active/Brewing panel
(`compute_active_clusters` `export_dashboard_json.py:1161-1229`, rendered
`render_index.py:191-232`) shows directors · £ · date-range · S1/brewing badge ·
conviction — but **"brewing" is just a static recency label; no trend, no
sparkline.** Data needed (weekly buy counts per ticker) is fully available in
`transactions`; no schema change. An SVG sparkline helper already exists to reuse
(`render_helpers.py:509-640`, `cohort_sparkline_svg` + `cohort_trend_cell_inner`).

**DECIDED metric (Rupert): the unit is the NUMBER OF CLUSTERS BREWING** — not
buy count, not £ value. This makes the trend a **single panel-level indicator on
the Active Cluster box header**, not a per-card sparkline:
- **Current value:** how many clusters are in the "brewing" state right now.
- **Comparable:** the **trailing-30-day average** number of brewing clusters.
  Shown as the current count vs that average (up/down arrow, e.g. "7 brewing,
  ▲ vs 30-day avg of 4.5").
- **8-week sparkline:** the weekly count of brewing clusters over the last 8
  weeks (one value per week), showing whether brewing activity is rising or
  falling.

Note: this needs the brewing-cluster count computed **as-of each historical
week**, i.e. re-evaluate the cluster/brewing definition (`detect_clusters.py`
window + `s1_active`/recency rule) at each weekly snapshot date over the trailing
8 weeks, and a daily count over the trailing 30 days for the average. The cluster
logic is walk-forward already (`detect_clusters.py` gates on `announced_at <=
as_of`), so an as-of count is well-defined.

**Build (additive, no migration):**
1. Exporter — add a `build_cluster_brewing_trend(conn, today)` helper that, for
   each of the trailing 30 days (for the average) and 8 week-ends (for the
   sparkline), counts clusters in brewing state as-of that date; emit
   `{current, avg_30d, weekly:[8]}` onto the payload (alongside `active_clusters`).
2. `render_helpers` — small `count_sparkline_svg()` wrapper around the existing
   SVG helper.
3. `render_index` — render the count + trend chip + sparkline on the Active
   Cluster **box header** (not per card).
4. Unit test mirroring `test_sparkline.py`.
Then export + build (Zone-B). **Confidence: High.** Open Q at spec time: exact
"brewing" definition to count (last buy 30–90d ago, per the current
`_classify_cluster` split) and whether to count only non-stale clusters.

---

## 🟠 #12 — Company page BMK** (benchmark) column always blank

**Root cause — a field the exporter never writes (all companies, always blank,
NOT a data/sector problem).** The company-page renderer reads each price row's
benchmark from a `bench` key (`render_company.py:358` `_p.get("bench")`), gates
the whole BMK cell on it (`:416`), and falls to `'-'` when absent (`:423`). But
the producer of that prices array — `build_dashboard.py:146-155` — only selects
`date, close, volume` and **never writes a `bench` key**. So `bench` is always
None → BMK is em-dash on every row of every company. RTN works because it only
needs `latest_close` + row price, computed independently (`render_company.py:403-407`).

The benchmark data fully exists (snapshot: `^FTAS` has 286 rows to 2026-06-04;
BATS maps to `^FTAS`) — so once the field is emitted, BMK populates for all
tickers via the universal `^FTAS` fallback.

**Fix.** In `build_dashboard.py` (prices block ~lines 144-155), resolve the
ticker's `benchmark_symbol` (already on `meta`; fall back to `^FTAS`), load a
`{date: close}` map for it, and add `"bench": bench_by_date.get(r["date"])` to
each price dict. The renderer already tolerates None bench values — **no renderer
change needed.** Exporter/build change → takes effect on next `build_dashboard.py`
(Zone-B).

**Confidence: Very High.** Minor note: RTN uses latest *ticker* close while the
benchmark cell uses latest *benchmark* close — identical for BATS/^FTAS (both end
2026-06-04), worth a glance on thinly-traded tickers.

---

## Suggested build order

1. **#8 now** — delete the stale status file (PowerShell one-liner above), unblock your 19 edits.
2. **#8 + #10 code fixes** together (review pipeline robustness) — highest pain.
3. **Quick exporter/build wins, one diff-first batch:** #1 (today count), #5 (relabel), #6 (abs-return column), #12 (company BMK column).
4. **#2 + #3** paper-book pass (real £ P&L + FTSE comparator + working filter + relabel).
5. **#4** monthly-activity scope (needs your decision: exclude vs split).
6. **#7** sector backfill (Zone-B script) — unlocks better benchmarking everywhere.
7. **#11** cluster-brewing trend (new feature, self-contained).

## Decisions — RESOLVED (Rupert, 2026-06-06)
- **#2:** New £10k-per-signal strategy tracker; **£10,000 flat stake per buy
  signal**, enter at announcement, **sell at T+21**; portfolio value (realised +
  open MTM) on a trailing-30-day basis with trend, vs an identical £10k-per-signal
  FTSE All-Share shadow. (Largest build — gets its own short spec.)
- **#3:** the live filter is the **badge in the paper-book row** itself.
- **#4:** **remove** corporate/PCA sell volume from Monthly Activity (entity-name
  heuristic for the `'Other / unclassified'` rows; diff-first).
- **#6:** absolute return measured **from the announcement date**.
- **#11:** trend unit = **number of clusters brewing** (panel-level), vs trailing-
  30-day average, with an 8-week sparkline of the brewing-cluster count.

No open decisions remain. Ready to spec/build on your go-ahead.
