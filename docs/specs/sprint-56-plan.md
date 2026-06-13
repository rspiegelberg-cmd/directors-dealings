# Sprint 56 Plan — Performance Page Split (Small Cap / Large Cap)

**Date:** 2026-06-07  
**Status:** APPROVED — building Sprint 56 2026-06-07  
**Related issues:** B-149 / DIR-77 (performance split), B-150 / DIR-78 (new company workflow doc)  
**Note:** B-148 was already used (DIR-76 — market-cap pipeline completeness, Sprint 55).

---

## Objective

Replace the single `performance.html` with two separate pages:

- **`performance_small.html`** — all charts, stats, scoreboard, strategy tracker,
  cohort cuts, paper book filtered to `small_cap = 1` (market cap < £500m)
- **`performance_large.html`** — same sections filtered to `small_cap = 0`
  (market cap ≥ £500m)

Every box, chart, and stat on each page shows only its own size band's data.
The current combined `performance.html` is retained during the build phase for
safe comparison, then retired once both new pages are verified.

**Prerequisite: B-147 must be deployed first.** B-147 fixes the AIM benchmark
for small-cap firings (currently 95% of small-cap firings use `^FTAS` instead
of `^FTSC`). The small-cap performance page will show wrong edge numbers until
B-147 is live and `backtest.py` has been re-run.

---

## Key finding that simplifies the work

The backtest CSV (`.data/_backtest_results.csv`) **already has `small_cap`
and `market_cap_gbp` columns** populated at `backtest.py` run time.
`load_backtest_csv()` loads them into every row dict.

This means:
- Pre-filtering rows by size band is a one-liner in `export_dashboard_json.py`
- No DB join needed for the export layer
- `aggregate_signals`, `build_cohort_table`, and all `build_*_payload` functions
  can be called twice — once with small rows, once with large rows — with zero
  changes to their signatures

The only function requiring internal SQL changes is `build_strategy_tracker`,
which queries the DB directly.

---

## Phases

### Phase A — Export layer (Zone A, ~3 points)

**File:** `.scripts/export_dashboard_json.py`

1. After `load_backtest_csv()`, split rows:
   ```python
   all_rows   = load_backtest_csv(csv_path)
   small_rows = [r for r in all_rows if r.get("small_cap") == "1"]
   large_rows = [r for r in all_rows if r.get("small_cap") == "0"]
   ```

2. Run the full signals payload once per band:
   - `aggregate_signals(small_rows, today)` → into a `small` signals dict
   - `aggregate_signals(large_rows, today)` → into a `large` signals dict
   - Same for `build_cohort_table`, `build_bucket_payload`,
     `build_role_payload`, `build_sector_payload`

3. Write three output files to `dashboard/data/`:
   - `signals.json` — existing combined (unchanged, no regression)
   - `signals_small.json` — small-cap band only
   - `signals_large.json` — large-cap band only

4. Strategy tracker (`build_strategy_tracker`) — see Phase B.

**No schema changes. No Zone-B work.**

---

### Phase B — Strategy tracker filter (~2 points)

**File:** `.scripts/export_dashboard_json.py`

`build_strategy_tracker(conn, today)` builds its own SQL queries internally.
Add a `small_cap: int | None = None` parameter:

```python
def build_strategy_tracker(conn, today, *, small_cap: int | None = None):
```

When `small_cap` is set (0 or 1), the transaction query that seeds the
strategy adds `JOIN tickers_meta tm ON tm.ticker = t.ticker WHERE tm.small_cap = ?`.

Call three times from `main()`:
```python
build_strategy_tracker(conn, today)              # combined → signals.json
build_strategy_tracker(conn, today, small_cap=1) # small → signals_small.json
build_strategy_tracker(conn, today, small_cap=0) # large → signals_large.json
```

---

### Phase C — Render layer size_band param (~1 point)

**File:** `.scripts/dashboard/render_performance.py`

`render_to_file` gets a `size_band: str | None = None` parameter
(`"small"` | `"large"` | `None`):

- When `size_band="small"`:
  - Page `<title>` = "Performance — Small Cap"
  - Subtitle = "Signals fired on companies with market cap < £500m"
  - Nav active link = "Small Cap"

- When `size_band="large"`:
  - Page `<title>` = "Performance — Large Cap"
  - Subtitle = "Signals fired on companies with market cap ≥ £500m"
  - Nav active link = "Large Cap"

- When `None`: existing combined behaviour unchanged.

No other changes to `render_performance.py` — all sections (scoreboard,
strategy tracker, cohort tiles, paper book, diagnostics chart) just read from
whatever `signals_path` is passed in.

---

### Phase D — build_dashboard wiring + nav (~1 point)

**File:** `.scripts/build_dashboard.py`

Add two render calls after the existing `performance.html` call:

```python
# performance_small.html
n = render_performance.render_to_file(
    signals_path=data_dir / "signals_small.json",
    status_path=status_path,
    out_path=out_dir / "performance_small.html",
    build_sha=build_sha,
    size_band="small",
)

# performance_large.html
n = render_performance.render_to_file(
    signals_path=data_dir / "signals_large.json",
    status_path=status_path,
    out_path=out_dir / "performance_large.html",
    build_sha=build_sha,
    size_band="large",
)
```

**Nav links** — update `NAV_LINKS` in `render_performance.py`,
`render_helpers.py` (or wherever the default nav lives), and
`render_baskets.py` to add the two new entries:

```
Today | Small Cap | Large Cap | Baskets | Review
```

Where "Small Cap" links to `performance_small.html` and "Large Cap" links
to `performance_large.html`. The combined `performance.html` is not linked
in the nav (kept as a silent fallback) until it's formally retired.

---

### Phase E — New company classification workflow (~1 point)

**Summary of current state (all automated):**

| Step | Script | When it runs |
|------|--------|--------------|
| Fetch market cap from Yahoo | `backfill_ticker_meta.py` | Inside `refresh_all.py` |
| Set `small_cap` flag | `classify_small_cap.py` | Inside `refresh_all.py` |
| Show unclassified count | `export_dashboard_json.py` | Dashboard index chip |
| Handle Yahoo-unresolvable tickers | `manual_classify.py` | **Manual** (Rupert) |

**The only manual step** is `manual_classify.py` for the ~20 tickers where
Yahoo Finance returns no market cap data. The dashboard index already shows an
"Unclassified" count chip (B-138 / task #4). When that chip shows > 0:

1. Run `python .scripts\backfill_ticker_meta.py --missing-only` (already
   deployed) to confirm Yahoo truly can't resolve the ticker.
2. Research the ticker manually (Yahoo Finance, LSE website, Reuters).
3. Add a hardcoded entry to `manual_classify.py`'s `MANUAL_CAP` dict.
4. Re-run `classify_small_cap.py` + `backtest.py` + deploy.

**Deliverable for Phase E:** A short operator guide at
`docs/guides/new-company-workflow.md` with the above steps written out,
plus a `--check` flag added to `manual_classify.py` that prints which tickers
in `tickers_meta` currently have `market_cap_gbp IS NULL`.

---

## What changes in Zone B (Rupert runs)

After this sprint is built, the complete deploy sequence becomes:

```powershell
# If B-147 not yet deployed, run backtest first:
python .scripts\backtest.py

# Standard export + build:
python .scripts\export_dashboard_json.py   # now writes signals_small.json + signals_large.json
python .scripts\build_dashboard.py         # now builds performance_small.html + performance_large.html
python .scripts\snapshot_db.py
```

No new Zone-B scripts are introduced — `backtest.py` already writes the
`small_cap` column to the backtest CSV, which is the sole source of truth
for the split.

---

## Sprint 56 issue list

| Issue | Linear | Description | Points | Agent |
|-------|--------|-------------|--------|-------|
| B-149 (Phase A) | DIR-77 | Export layer: write signals_small.json + signals_large.json | 3 | general-purpose |
| B-149 (Phase B) | DIR-77 | build_strategy_tracker small_cap filter | 2 | general-purpose |
| B-149 (Phase C) | DIR-77 | render_performance size_band param | 1 | dashboard-designer |
| B-149 (Phase D) | DIR-77 | build_dashboard wiring + nav links | 1 | dashboard-designer |
| B-150 (Phase E) | DIR-78 | New company workflow doc + manual_classify --check flag | 1 | general-purpose |

**Total: 8 points.**  
Note: B-148 was already used (DIR-76, Sprint 55 — market-cap pipeline completeness).

---

## Decisions locked 2026-06-07

1. Keep `performance.html` — nav label "All"
2. Nav labels: "Small Cap" and "Large Cap"
3. Strategy tracker filter: build in full in Sprint 56 (not deferred)
