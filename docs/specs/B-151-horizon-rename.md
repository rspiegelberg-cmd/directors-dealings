# B-151 — Horizon Rename & Expansion

**Status:** QA review complete 2026-06-09 — ready to build  
**Raised:** 2026-06-09  
**Scope:** `backtest.py`, `export_dashboard_json.py`, all `render_*.py`, `close_paper_trades.py`, `render_helpers.py`, all test suites, schema migration (none needed — metrics live in CSV not DB)

---

## 1. What Rupert asked for

> "Change T+21 to 30 days (calendar days), rename to T+30. Do the same for T+90
> to 90 calendar days. Create T+180 (180 calendar days). Change T+252 to T+365
> (365 calendar days). Change all DB items and all toggles on every page."

---

## 2. Current state vs target — the critical mapping

The backtest engine (`backtest.py`, line 54) measures CARs at **trading-day** offsets:

```python
OFFSETS = (1, 21, 90, 252)   # trading days
```

Trading days and calendar days are not the same:

| Current label | Current offset | Real-world duration | Target label | Target calendar days | Target trading days (approx) | Change type |
|---|---|---|---|---|---|---|
| T+1 | 1 trading day | 1 day | T+1 | — | 1 | **No change** |
| T+21 | 21 trading days | ~30 calendar days | **T+30** | 30 cal | ~21 td | Label rename + paper book threshold |
| T+90 | 90 trading days | **~126 calendar days** | **T+90** | 90 cal | ~63 td | ⚠️ **Genuinely shorter window** |
| *(none)* | — | — | **T+180** | 180 cal | ~126 td | New horizon (≈ current T+90 real time) |
| T+252 | 252 trading days | ~365 calendar days | **T+365** | 365 cal | ~252 td | Label rename |

### ⚠️ Decision required: T+90 gets shorter

This is the most important thing in this spec. Keeping the name T+90 but
redefining it as 90 **calendar** days means:

- **Current T+90** measures ~4.5 months of price history (90 trading days)
- **New T+90** would measure ~3 months (63 trading days)

That is a 30% shorter measurement window. All existing T+90 signal statistics
would be recalculated against a meaningfully earlier price point. The new
T+180 (180 calendar days ≈ 126 trading days) would fill roughly the same
real-world slot as the current T+90.

**Please confirm:** is this what you want — a new shorter T+90 (3 months /
~63 trading days) alongside a new T+180 (6 months / ~126 trading days)? Or
should the current T+90 measurement simply be *relabeled* to T+180, and the
new T+90 be the shorter 3-month window?

Recommended interpretation (cheaper, lower data-quality risk):

| New label | Trading-day offset | Calendar equivalent |
|---|---|---|
| T+1 | 1 | ~1 day |
| T+30 | 21 | ~30 cal days |
| T+90 | 63 | ~90 cal days (NEW data) |
| T+180 | 126 | ~180 cal days (≈ old T+90) |
| T+365 | 252 | ~365 cal days |

This adds one genuinely new measurement (T+90 at 63 td), renames two
(T+21→T+30, T+252→T+365), and repurposes T+180 as the old T+90 data.

---

## 3. Schema impact

**Good news: no DB schema migration needed.**

T+21/T+90/T+252 metrics are not stored in `directors.db`. They live entirely
in `.data/_backtest_results.csv` (written by `backtest.py`) and derived
JSON files under `outputs/data/`. The DB schema has no CAR columns.

What *does* change in `_backtest_results.csv`:

| Old column | New column |
|---|---|
| `t21_close` | `t30_close` |
| `benchmark_t21` | `benchmark_t30` |
| `raw_return_t21` | `raw_return_t30` |
| `benchmark_return_t21` | `benchmark_return_t30` |
| `car_t21` | `car_t30` |
| `net_car_t21` | `net_car_t30` |
| `t90_close` | `t90_close` (content recalculated at 63 td) |
| `t252_close` | `t365_close` |
| `benchmark_t252` | `benchmark_t365` |
| `raw_return_t252` | `raw_return_t365` |
| `benchmark_return_t252` | `benchmark_return_t365` |
| `car_t252` | `car_t365` |
| `net_car_t252` | `net_car_t365` |
| *(none)* | `t180_close` + full set of `_t180` columns (new) |

The CSV is atomically replaced on each `backtest.py` run — no migration, just
a full rebuild.

---

## 4. File-by-file change list

### 4a. backtest.py

- Change `OFFSETS = (1, 21, 90, 252)` → `OFFSETS = (1, 21, 63, 126, 252)`
- Update `FIELDNAMES` list: rename `_t21` → `_t30`, `_t252` → `_t365`;
  add full `_t180` column set; update `_t90` content (offset now 63 not 90)
- Update header comment (line 4) from "T+1 / T+21 / T+90 / T+252 trading days"
- `_STRAT_EXIT_OFFSET = 21` → `_STRAT_EXIT_OFFSET = 21` (stays — this is
  correctly labeled as trading days in strategy tracker; no rename needed here)
- **Rupert must run:** `python backtest.py` (full rebuild, ~5-10 min)

### 4b. export_dashboard_json.py

- `HORIZONS = ["t1", "t21", "t90", "t252"]` → `["t1", "t30", "t90", "t180", "t365"]`
- All `_HORIZ_MAP` / `_per_horizon_stats` / month-entry aggregation loops:
  rename keys `t21`→`t30`, `t252`→`t365`; add `t180`; recalculate `t90`
- All per-horizon stat keys: `mean_car_t21` → `mean_car_t30`, etc. (~40 key
  renames in the aggregation block)
- Backward-compat single-ticker-weight key `single_ticker_weight` (no suffix,
  currently T+21 alias) → rename to `single_ticker_weight_t30`
- `_PAPER_BOOK_HOLD_DAYS = 21` → `30` (paper book exit now 30 calendar days)
- Trend calculation key: `trend_3m_vs_prior3m_t21` → `trend_3m_vs_prior3m_t30`
- Header rollup keys: `*_t21_overall` → `*_t30_overall`, `*_t252_overall` →
  `*_t365_overall`; add `*_t180_overall`

### 4c. render_helpers.py

- `HORIZON_LABELS` dict:
  ```python
  # Before
  {"t1": "T+1", "t21": "T+21 (~1 month)", "t90": "T+90 (~4.5 months)", "t252": "T+252 (~1 year)"}
  # After
  {"t1": "T+1", "t30": "T+30 (~1 month)", "t90": "T+90 (~3 months)",
   "t180": "T+180 (~6 months)", "t365": "T+365 (~1 year)"}
  ```

### 4d. render_performance.py

- `COHORT_DEFAULT_HORIZON = "t21"` → `"t30"`
- JS `H_LABELS` dict: `{t1:'T+1', t21:'T+21', t90:'T+90', t252:'T+252'}` →
  `{t1:'T+1', t30:'T+30', t90:'T+90', t180:'T+180', t365:'T+365'}`
- JS `validH` array: `['t1', 't21', 't90', 't252']` →
  `['t1', 't30', 't90', 't180', 't365']`
- JS `HORIZON_LABELS_COHORT` dict: same rename as H_LABELS
- `window.__cohortActiveHorizon` default: `'t21'` → `'t30'`
- All field-access keys in cohort chart data binding: `mean_car_t21` →
  `mean_car_t30` etc. (~15 references)
- Paper book section note: "T+21 = exit horizon" → "T+30 = exit horizon (30 calendar days)"

### 4e. render_performance_drilldown.py

- Default horizon arg: `"t21"` → `"t30"`
- `validH` list: rename same as above
- `base_rate_for_horizon()` mapping: add `t30`, `t180`, `t365`; rename `t21`,
  `t252` keys

### 4f. render_company.py

- Company-page CAR horizon list (line 611):
  ```python
  # Before
  [("car_t1", 1), ("car_t21", 21), ("car_t90", 90), ("car_t252", 252)]
  # After — integers are TRADING-DAY offsets (used to compute "matures on" date)
  [("car_t1", 1), ("car_t30", 21), ("car_t90", 63), ("car_t180", 126), ("car_t365", 252)]
  ```
  ⚠️ **QA correction:** the integers must be trading-day values (21, 63, 126, 252), not
  calendar-day values (30, 90, 180, 365). They drive the `entry_date + h_days` "matures on"
  date calculation using Yahoo's trading-day price offsets.
- Special formatting reference for `"car_t21"` → `"car_t30"`
- Stat label strings at lines 666–667: `'Hit % @ T+21'` → `T+30`, `'Median CAR @ T+21'` → `T+30`

### 4g. export_baskets_json.py

- Load keys: `_net_car_t21` → `_net_car_t30`, `_net_car_t90` stays
- Output keys: `"net_car_21"` → `"net_car_30"`, `"net_car_90"` stays

### 4h. build_dashboard.py

- Default view string `"t21 × 90d"` → `"t30 × 90d"` (display only)
- Any `car_t21`, `car_t90`, `car_t252` format references → rename accordingly

### 4i. close_paper_trades.py

- CLI `--horizon 21|90` arg: the `21` default maps to trading days. Given the
  paper book exit is now 30 calendar days (not trading days), this arg is
  separately managed. No change needed here — the paper book status calculation
  in `export_dashboard_json.py` is what controls the display. The
  `close_paper_trades.py` script handles Phase B paper_trades table and
  can remain at `--horizon 21` trading days (they are equivalent).

### 4j. render_helpers.py `HORIZON_LABELS` (also COHORT_DEFAULT_HORIZON)

Covered under 4c above.

### 4k. inspect_ticker.py / phase11_analyst_check.py / eval_signals.py

Diagnostic / standalone scripts — update display references but no
logic change. Low priority; can be done in a follow-up.

---

## 5. Test suite changes

The following test files contain hard-coded horizon keys or column names that
will need updating. Each file is a rename-only change (no logic changes):

| Test file | What to update |
|---|---|
| `test_cohort_performance_export.py` | `_row()` helper + all assertions on `_net_car_t21`, `_net_car_t252` etc. |
| `test_cohort_table.py` | Horizon grid list `["t1","t21","t90","t252"]` |
| `test_drill_payload.py` | `_mk_row()` helper + horizon iteration |
| `test_export_dashboard_json.py` | CSV fixture column headers |
| `test_phase4_cohort_chart.py` | `cohortActiveHorizon` default + H_LABELS map |
| `test_phase6_drilldown.py` | `_sig()` helper |
| `test_phase8_pending.py` | pending_horizons null-handling |
| `test_render_drilldown.py` | drill payload horizon list |
| `test_sprint28.py` | paper book + cohort table fixtures |
| `test_sprint29.py` | `close_paper_trades` horizon arg |
| `test_sprint31.py` | sparse horizon data |
| `test_sprint33.py` | `_matured_abs()` horizons |
| `test_baskets.py` | CSV column set |
| `test_stage_04_6.py` | CSV column headers |

Estimated ~80-120 individual string substitutions across tests, all mechanical.

---

## 6. Execution sequence

Once approved and code is written, Rupert runs these in order:

```
# Step 1 — full backtest rebuild (new OFFSETS, new CSV column names)
python backtest.py

# Step 2 — re-evaluate all signals against new backtest results
python eval_signals.py --rebuild

# Step 3 — export JSON (new horizon keys flow through)
python export_dashboard_json.py

# Step 4 — rebuild dashboard HTML
python build_dashboard.py

# Step 5 — snapshot DB for verification
python .scripts/snapshot_db.py
```

Then Rupert runs `python -m unittest discover -s .scripts -p "test_*.py"` to
confirm the full test suite is green.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Backward-compat breakage: `single_ticker_weight` (no suffix) is a T+21 alias consumed by the JS cohort chart | Explicitly rename to `single_ticker_weight_t30` in both exporter and JS |
| `pending_horizons` list in per-month data gates maturity display | Update its string values: `"t21"` → `"t30"`, `"t252"` → `"t365"` |
| Baskets report uses `net_car_21` / `net_car_90` as JSON keys (no underscore suffix) | Update `export_baskets_json.py` + any consumer |
| T+90 real-world duration shrinks (90 trading → 63 trading) — historic data changes | This is intentional per Rupert's request; flag clearly in CHANGELOG |
| All-time `_backtest_results.csv` column set changes → old CSV unusable | Not a problem: backtest atomically replaces the file on each run |

---

## 8. What is NOT changing

- DB schema — no migrations needed
- The `paper_trades` table and `close_paper_trades.py` Phase B logic
- Signal IDs, tier taxonomy, scoring logic
- T+1 horizon (unchanged throughout)
- `_STRAT_EXIT_OFFSET = 21` in the strategy tracker (stays at 21 trading days
  as the exit for the tracker, which is correct; this is not a display label)

---

## 9. Effort estimate

QA identified ~260 change sites across ~25 files (more than initially scoped).

| Layer | Complexity | Estimate |
|---|---|---|
| backtest.py OFFSETS + fieldnames + OFFSET_TO_HORIZON dict | Low-medium | 45 min |
| export_dashboard_json.py key renames (~65 sites) | Medium-high | 2.5 hr |
| render_*.py JS + Python labels (~80 sites) | Medium-high | 1.5 hr |
| Test suite mechanical renames (~100 sites, 16 files) | Medium-high | 2 hr |
| Integration run + verify | — | 30 min |
| **Total** | | **~7 hrs** |

Recommended approach: do in one sprint, no phases — the changes are
interdependent and a partial rename leaves the system in an inconsistent state.

---

## 10. QA findings — unforeseen impacts (2026-06-09)

QA review surfaced four items not in the original spec:

### U-1 🔴 Silent corruption: `backtest.py` `f"t{n}"` auto-format bug

`windows_available` (line 372) and the split-guard log (line 351) both use
`f"t{n}"` to convert an integer offset to a horizon name. After the change,
integer `21` formats as `"t21"` (not `"t30"`), `63` as `"t63"` (not `"t90"`),
etc. Every consumer of `windows_available` would silently receive wrong horizon
names with no error.

**Fix:** Add an explicit mapping dict in `backtest.py`:
```python
OFFSET_TO_HORIZON = {1: "t1", 21: "t30", 63: "t90", 126: "t180", 252: "t365"}
```
Use `OFFSET_TO_HORIZON[n]` everywhere `f"t{n}"` currently appears.

### U-2 🟡 `render_company.py` spec error (corrected above in §4f)

The trading-day integers in the `horizons` list must be `(1, 21, 63, 126, 252)`,
not calendar-day values `(1, 30, 90, 180, 365)`. These drive the "matures on"
date calculation. Already corrected in §4f.

### U-3 🟡 Strategy tracker prose must update

`render_performance.py` has user-visible text "T+21 exit horizon" in the
paper book section (approx line 3742–3744). Must become "T+30 exit horizon
(30 calendar days / 21 trading days)".

### U-4 🟡 Stale outputs/JSON after code deploy

After all code is merged, old `outputs/data/*.json` files still contain
`mean_car_t21` keys. The JS will find nothing and show blank charts until
the pipeline is re-run. **Execution sequence must be followed immediately
after code merge** — do not load the dashboard between code-write and
`build_dashboard.py`.

### U-5 🟢 LocalStorage horizon key (no code change needed)

Users who have `"t21"` saved in `localStorage` will have it rejected by the
`validH` guard on first page load and silently reset to `"t30"`. Correct
behaviour — no fix needed, but expected.

---

## 11. Confirmed decisions (Rupert, 2026-06-09)

1. **T+90 window shortening confirmed.** New T+90 = 90 calendar days (~63
   trading days, ~3 months). Old T+90 trading-day data is dropped entirely —
   no backward compatibility needed.

2. **T+180 is a genuinely new measurement.** 180 calendar days (~126 trading
   days, ~6 months). It does NOT reuse old T+90 data; it is computed fresh
   from prices at ~126 trading days after announcement.

3. **Default horizon toggle = T+30.** Cohort chart defaults to T+30 after rename.

4. **B-152 (Capital Deployed trending on Today page) is a separate sprint.**
   Not in scope for B-151.
