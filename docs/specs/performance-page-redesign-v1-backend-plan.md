# Backend implementation plan — performance-page-redesign-v1
**Author:** plan agent
**Date:** 2026-05-18
**Status:** plan for review by Rupert before execution
**Spec:** `docs/specs/performance-page-redesign-v1.md` (v1.2)
**QA review applied:** `docs/specs/performance-page-redesign-v1-qa-review.md`

---

## 0. Summary

We are reshaping the JSON contract behind the Performance page. `signals.json`'s
`cohorts` block grows from "two thin scalars" to a horizon-keyed × lookback-keyed
table covering three tiles (bucket / role / sector), and three new aggregated
files (`performance_bucket.json`, `performance_role.json`, `performance_sector.json`)
are emitted to drive the new drill-down pages. The change is contained in
`export_dashboard_json.py` plus one new role-classifier helper and a new test
suite. No DB schema changes. Front-end work is a separate plan and must NOT start
until the corpus-diagnostic gate at §6 passes. The biggest single risk is the role
regex mis-bucketing edge titles — explicitly mitigated by a mandatory corpus
diagnostic that Rupert eyeballs before merge.

---

## 1. Files to modify / create

| Path | Action | Purpose | LOC estimate |
|---|---|---|---|
| `.scripts/export_dashboard_json.py` | **modify** | Replace `cohort_value_buckets()` + `cohort_by_sector()` with the new horizon × lookback shape; add three `build_*_payload()` functions; wire three new outputs through `run()` | +350 / -40 |
| `.scripts/role_classifier.py` | **create** | Locked-precedence role classifier (`classify_role()`) + the corpus diagnostic CLI | ~110 |
| `.scripts/test_role_classifier.py` | **create** | Unit tests for the regex precedence + edge cases | ~120 |
| `.scripts/test_stage_05_cohorts.py` | **create** | Cohort-shape tests against the new JSON contract (extends `test_stage_04_6.py` patterns) | ~280 |
| `.scripts/diagnose_role_classifier.py` | **create** | Standalone diagnostic — prints `(classify_role, raw role_str)` frequency table from the live corpus; **read-only** | ~70 |
| `docs/specs/performance-page-redesign-v1-backend-plan.md` | (this file) | n/a |

All paths above are Zone A (code files). No edits to `.data/`, no DB writes.
The exporter writes Zone B (`.json` under `dashboard/data/`) — Rupert runs the
exporter from PowerShell, never Claude bash.

---

## 2. Function-level decomposition

### 2.1 Helpers in `export_dashboard_json.py`

```python
LOOKBACKS = [("90d", 90), ("6m", 183), ("1y", 365), ("all", None)]
# (None = no lower bound — use all CSV rows)

HORIZONS = ["t1", "t21", "t90", "t252"]   # already exists
```

**`_within_lookback(fired_at: str, today: date, days: int | None) -> bool`**
Returns True if `fired_at` falls within the trailing `days` window (None = always
True). Pure function, used by every cohort builder.

**`_bucket_for_value(value_gbp: float | None) -> str | None`**
Returns the bucket key (`"1k-25k"` etc) or None. Refactor from the existing
`cohort_value_buckets()` — extracted so role and sector payloads can reuse the
same bucket assignment.

**`_load_sector_map(conn) -> dict[str, dict]`**
One query, builds `{ticker: {"sector": ..., "benchmark_symbol": ..., "company": ...}}`.
The `company` is sourced from the most recent `transactions.company` for that
ticker (single subquery) — used to populate the rollup tables. Cached for the
whole exporter run.

**`_signal_tier_for(conn, fingerprint: str) -> str`**
Returns the highest-tier signal short name (`t0`/`t1`/.../`f1`) fired against
this fingerprint. Precedence: `SIGNAL_ORDER` (already defined). Used for the
firing-row `signal_tier` field.

### 2.2 Three cohort-tile aggregators (signals.json)

**`build_cohorts_value_bucket(rows, today, horizons, lookbacks) -> dict`**
Returns:
```
{
  "t1":   { "90d": {"rows": [...], "total_n": N}, "6m": {...}, "1y": {...}, "all": {...} },
  "t21":  {...}, "t90": {...}, "t252": {...}
}
```
Each `rows` entry: `{"key", "label", "n", "hit_pct", "median_car", "outlier_flag?"}`.
Filters to T1+T2 buys (`signal_id in {"t1_ceo_cfo_buy","t2_exec_buy"}`) — same as
today. `outlier_flag` set when any firing in the bucket has `|car| > OUTLIER_ABS_CAR`.

**`build_cohorts_role(rows, today, horizons, lookbacks) -> dict`**
Same shape. Uses `classify_role(row["role_class"], row["role"])` to group. Keys
in `rows`: `ceo_cfo`, `other_exec`, `ned` (T4 / None deliberately dropped per
§5.4 scope).

**`build_cohorts_sector(rows, conn, today, horizons, lookbacks) -> dict`**
Same shape. Uses `_load_sector_map()` to group by sector. Per §1.3 the tile
shows **top 3 + bottom 2** by hit %; the JSON itself emits ALL sectors with N≥1
(front-end is responsible for slicing to top-3/bottom-2 — keeps the JSON
agnostic so a future "all sectors" view doesn't need a re-export). `total_n`
in this case is `len(rows_emitted)` (sectors), per §5.3 clarification.

### 2.3 Three drill-down payload builders

**`build_drill_payload(csv_rows, conn, key_fn, label_fn, scope_filter_fn=None,
                       scope_note=None, sector_map=None) -> dict`**

The shared helper. Returns the inner cohort-dict:
```
{
  "<key>": {
    "label": "<display>",
    "scope_note": "<optional sub-line>",
    "benchmark_symbol": "^...",   # only when caller passes sector_map
    "t1": { "90d": <drill_block>, "6m": ..., "1y": ..., "all": ... },
    "t21": {...}, "t90": {...}, "t252": {...}
  },
  ...
}
```
Where `<drill_block>` is exactly the spec §5.2 shape:
```
{
  "benchmark_car_pct":  <scalar>,
  "total_firings":      N,
  "distinct_tickers":   K,
  "tickers_with_n3":    M,
  "hit_pct":            <pct>,
  "median_car":         <pct>,
  "top_firings":        [ /* up to 10, CAR desc */ ],
  "bottom_firings":     [ /* up to 10, CAR asc  */ ],
  "rollup":             [ /* per-ticker rollup */ ]
}
```

- `key_fn(row)` → bucket key string (e.g. `"100k-500k"`, `"ceo_cfo"`, or sector name).
- `label_fn(key)` → display label.
- `scope_filter_fn(row)` → True if the row belongs in this cohort at all (e.g.
  the bucket payload restricts to T1+T2; role + sector pass everything).
- `sector_map` → if passed, the function uses `sector_map[ticker]["benchmark_symbol"]`
  for the per-cohort `benchmark_symbol` field. The `benchmark_car_pct` per drill
  block is the median of `benchmark_return_<h>` over CSV rows in scope and
  within the lookback.

**`build_bucket_payload(rows, today) -> dict`** — thin wrapper:
- `scope_filter_fn` = T1 or T2 only
- `key_fn` = `_bucket_for_value(row["_value_gbp"])`
- `label_fn` = `{"1k-25k": "£1–25k", ...}`
- `scope_note` = "T1 + T2 buys only"
- emits `{"generated_at", "schema_version", "buckets": {...}}`

**`build_role_payload(rows, today) -> dict`** — thin wrapper:
- `scope_filter_fn` = `classify_role(...) is not None`
- `key_fn` = `classify_role(role_class, role)`
- `label_fn` = `{"ceo_cfo": "CEO / CFO", "other_exec": "Other exec", "ned": "NED"}`
- emits `{"generated_at", "schema_version", "roles": {...}}`

**`build_sector_payload(rows, today, conn) -> dict`** — thin wrapper:
- `scope_filter_fn` = ticker has a sector in `tickers_meta`
- `key_fn` = `sector_map[ticker]["sector"]`
- `label_fn` = identity
- `sector_map` passed in (so benchmark_symbol gets populated)
- emits `{"generated_at", "schema_version", "sectors": {...}}`

### 2.4 Firing row builder (shared)

**`_firing_row(csv_row, tx_lookup, sector_map, horizon: str) -> dict`**

Returns one row per the §5.3 shape:
```
{ "date", "ticker", "company", "director", "role", "role_class",
  "signal_tier", "value_gbp", "car" }
```
- `date` = first 10 chars of `fired_at`
- `company` from `sector_map` (or `transactions.company` fallback)
- `director`, `role` from `transactions` keyed by fingerprint
- `signal_tier` from `_signal_tier_for(conn, fingerprint)`
- `value_gbp` = int(round(_value_gbp))
- `car` = `_car_<horizon>` × 100, rounded 1dp
- **`bench_car` and `outlier_flag` deliberately omitted** (post-QA decision §5.3)

The `tx_lookup: dict[str, dict]` is built once per exporter run — one query
returning `{fingerprint: {"director", "role", "company", "ticker"}}`. Saves N+1
queries.

### 2.5 Top/bottom selection

**`_top_bottom(firings: list[dict], k: int = 10) -> tuple[list, list]`**
Sort once by `car` desc, take first `k` and last `k` (reversed for ascending).
Handles N<10 — bottom list may have <10 entries (front-end shows the edge-case
note per §2.3 of spec).

### 2.6 Rollup builder

**`_ticker_rollup(firings: list[dict]) -> list[dict]`**
Groups firings by ticker. For each ticker emits:
`{"ticker", "company", "n", "hit_pct", "mean_car", "latest_fire"}`.
Sorted: N≥3 tickers first by `hit_pct` desc, then N<3 tickers. The
emit-order is the rendered order — front-end inserts a divider when `n` drops
below 3.

### 2.7 `run()` modifications

After `build_payload()` completes, add:
```python
sector_map = _load_sector_map(conn)
bucket_payload = build_bucket_payload(rows, today)
role_payload   = build_role_payload(rows, today)
sector_payload = build_sector_payload(rows, today, conn)
# atomic writes through the existing helper
_atomic_write_json(out_dir / "performance_bucket.json", bucket_payload)
_atomic_write_json(out_dir / "performance_role.json",   role_payload)
_atomic_write_json(out_dir / "performance_sector.json", sector_payload)
```

The summary dict returned by `run()` gains three new counts:
`n_buckets`, `n_roles`, `n_sectors` (top-level key counts per file). Helpful
for the `--verbose` smoke check.

---

## 3. Role classifier — locked precedence + corpus diagnostic

### 3.1 The function (lives in `.scripts/role_classifier.py`)

```python
def classify_role(role_class: str | None, role_str: str | None) -> str | None:
    """Returns 'ceo_cfo' | 'other_exec' | 'ned' | None.

    Order is part of the contract — do not reorder rules. The first match wins.
    'Executive' inside 'Chief Executive' would mis-classify a CEO if rule 1
    didn't run first. Likewise NED titles often contain 'Executive' or 'Group'
    so rule 2 must beat rule 3.
    """
    rc = (role_class or "").strip().upper()
    rs = role_str or ""
    # Rule 1 — CEO / CFO
    if rc == "T1": return "ceo_cfo"
    if CEO_CFO_RE.search(rs): return "ceo_cfo"
    # Rule 2 — NED
    if rc == "T3": return "ned"
    if NED_RE.search(rs): return "ned"
    # Rule 3 — Other exec (catch-all for execs)
    if rc == "T2": return "other_exec"
    if OTHER_EXEC_RE.search(rs): return "other_exec"
    return None
```

Compiled regexes (module-level constants):
- `CEO_CFO_RE = re.compile(r"(?i)\b(CEO|CFO|Chief Executive|Chief Financial)\b")`
- `NED_RE = re.compile(r"(?i)\b(Non[- ]?Executive|NED|Senior Independent)\b")`
  - Note: `Non[- ]?Executive` covers "Non-Executive", "Non Executive", "NonExecutive".
- `OTHER_EXEC_RE = re.compile(r"(?i)\b(Chair|Chairman|Group|Executive|COO|CTO|CIO|COMMERCIAL OFFICER|OPERATING OFFICER)\b")`

**Why no `_RE.cache` decorators:** module-level `re.compile` is already cached;
extra plumbing wastes complexity.

### 3.2 Corpus diagnostic — `diagnose_role_classifier.py` (Zone A, safe)

Read-only — opens `.data/directors.db` for SELECT only and reads the CSV
through the existing `load_backtest_csv()` helper. Run by Rupert from
PowerShell **once** before merge, output reviewed by Rupert.

Output: a text-mode frequency table to stdout, e.g.:
```
classify_role result   role_str                                  count
---------------------  ----------------------------------------  -----
ceo_cfo                Chief Executive Officer                   142
ceo_cfo                Chief Financial Officer                    98
ceo_cfo                Chief Executive Officer and Chairman        3
other_exec             Chairman                                   77
other_exec             COO                                        21
ned                    Non-Executive Director                    312
ned                    Senior Independent Director                14
None                   <empty role_str AND empty role_class>      57
None                   Company Secretary                          11
...
```

Output is sorted: by classification (`ceo_cfo` / `other_exec` / `ned` / `None`),
then by `count` desc within each. Rupert's job at the gate is to read the table
and confirm:
1. No row classified as `other_exec` looks like a CEO/CFO.
2. No `None` row obviously belongs in one of the three buckets.
3. The `None` count is small (expected ≤10% of corpus).

**This is the §3 gate-test the QA flagged as Critical-item #4.**

---

## 4. JSON payload shapes

Anchored to spec §5 — re-stated here in compact form so the implementer doesn't
need both documents open.

### 4.1 Updated `signals.json` → `cohorts` block

```jsonc
"cohorts": {
  "by_value_bucket": {
    "t1":   { "90d": {"rows":[...],"total_n":N}, "6m":{...}, "1y":{...}, "all":{...} },
    "t21":  {...}, "t90": {...}, "t252": {...}
  },
  "by_role":   { "t1":..., "t21":..., "t90":..., "t252":... },
  "by_sector": { "t1":..., "t21":..., "t90":..., "t252":... }
}
```

Size estimate: 4 horizons × 4 lookbacks × ~10 rows × ~5 fields = ~800 small
floats / strings + skeleton; well under 50 KB added to `signals.json` (current
file is ~1.1 KLOC, post-change ~1.6 KLOC). Generation algorithm:

```
for each tile (value_bucket, role, sector):
    for each horizon h in t1/t21/t90/t252:
        for each lookback (90d/6m/1y/all):
            rows_in_window = filter csv_rows by:
                - signal_id in {t1,t2} (value bucket only)
                - matured at h (i.e. _car_<h> not None)
                - fired within lookback window from today
            group by key_fn(row)
            for each group:
                n = len(group)
                hit_pct = 100 * sum(v>0 for v) / n
                median_car = median(v) * 100
            emit {"rows": sorted_rows, "total_n": sum(n)}
```

### 4.2 `performance_bucket.json`

```jsonc
{
  "generated_at": "...",
  "schema_version": "1.0",
  "buckets": {
    "1k-25k":    { "label": "£1–25k",    "scope_note": "T1 + T2 buys only",
                   "t1":{...}, "t21":{...}, "t90":{...}, "t252":{...} },
    "25k-100k":  {...},
    "100k-500k": {...},
    "500k+":     {...}
  }
}
```

Each `txx` is `{"90d":<drill_block>, "6m":<drill_block>, "1y":<drill_block>, "all":<drill_block>}`.
Each `<drill_block>` is the §5.2 shape (10 fields). Size: 4 buckets × 4 horizons
× 4 lookbacks × ~3 KB/block ≈ 80 KB. Atomic write through `_atomic_write_json()`.

### 4.3 `performance_role.json`

Same shape, top-level key `roles`, three role keys (`ceo_cfo`, `other_exec`, `ned`).
Size: ~60 KB. Notably **no `scope_note`** at the top level of `roles.*` — the
sub-line wording is per-role and lives in the renderer (§3.2 of spec, per-role).
Plan choice: emit `scope_note` per role key to keep front-end stateless:
- `ceo_cfo.scope_note` = "Chief Executive Officers and Chief Financial Officers"
- `other_exec.scope_note` = "Chair, group executive, COO, CTO, divisional director"
- `ned.scope_note` = "Non-executive directors"

### 4.4 `performance_sector.json`

Same shape, top-level key `sectors`, ~30-80 sector keys (whatever
`tickers_meta.sector` distinct count returns). Size: ~150 KB worst case.
**Critical extra field** per sector entry: `benchmark_symbol`. The drill block
inside each horizon/lookback carries `benchmark_car_pct` which uses the
sector-specific benchmark (via `benchmark_return_<h>` on CSV rows for tickers
in that sector). If `benchmark_symbol` is null for any sector, fall back to
`^FTAS` and tag the cohort with `benchmark_symbol: "^FTAS"` so the front-end
can disclose the fallback.

### 4.5 Pseudocode for the inner drill block

```
for each horizon h, lookback L:
    in_scope = [r for r in csv_rows
                if scope_filter_fn(r)
                and r["_car_"+h] is not None
                and _within_lookback(r["_fired_at"], today, L)]
    keyed = group(in_scope, key_fn)
    for each key, rows in keyed:
        firings = [_firing_row(r, tx_lookup, sector_map, h) for r in rows]
        firings_sorted = sorted(firings, key=lambda f: f["car"], reverse=True)
        top10    = firings_sorted[:10]
        bottom10 = list(reversed(firings_sorted))[:10]
        rollup   = _ticker_rollup(firings)
        bench_vals = [r["benchmark_return_"+h] for r in rows
                      if r["benchmark_return_"+h] not in (None, "")]
        bench_car_pct = round(median(bench_vals)*100, 2) if bench_vals else None
        cars = [f["car"]/100 for f in firings]  # back to fraction for median
        emit {
          "benchmark_car_pct": bench_car_pct,
          "total_firings":    len(firings),
          "distinct_tickers": len({f["ticker"] for f in firings}),
          "tickers_with_n3":  count(rollup where r["n"]>=3),
          "hit_pct":          100*sum(v>0 for v in cars)/len(cars),
          "median_car":       round(median(cars)*100,1),
          "top_firings":      top10,
          "bottom_firings":   bottom10,
          "rollup":           rollup,
        }
```

---

## 5. Order of execution — paste-and-run playbook

Each step gives Rupert the exact PowerShell command and the verification check.
Steps 1–3 are pure Claude work (Zone A). Steps 4 onward are mixed.

### Step 1 — Create the role classifier module

Claude action: write `.scripts/role_classifier.py` with the three regexes and
`classify_role()` per §3.1.

Rupert verify: nothing to run yet — Claude proceeds to Step 2.

### Step 2 — Unit-test the role classifier

Claude action: write `.scripts/test_role_classifier.py`. Test cases:
- "Chief Executive Officer" → `ceo_cfo`
- "Chief Executive Officer and Chairman" → `ceo_cfo` (not `other_exec`)
- "Non-Executive Director" → `ned`
- "Senior Independent Director" → `ned`
- "Chairman" → `other_exec`
- "Group Executive Director" → `other_exec`
- "Chief Operating Officer" → `other_exec`
- `role_class="T1", role_str=""` → `ceo_cfo` (uses role_class fallback)
- `role_class="", role_str=""` → `None`
- `role_class=None, role_str=None` → `None`
- "Company Secretary" → `None`
- "Non Executive Director" (no hyphen) → `ned`

Claude runs (safe, no DB write):
```
python .scripts/test_role_classifier.py
```
Expected: all 12 cases PASS.

### Step 3 — Write the corpus diagnostic

Claude action: write `.scripts/diagnose_role_classifier.py`.

The script:
1. `import db; conn = db.connect()` — opens DB read-only via the existing helper.
2. `csv_rows = ex.load_backtest_csv(...)` for the role_class column.
3. Also runs `SELECT role, COUNT(*) FROM transactions GROUP BY role ORDER BY 2 DESC`.
4. For each (role_class, role) pair, classifies and prints the frequency table.
5. Reports totals: `Total classified ceo_cfo: X (Y%)`, etc.

Claude runs (safe — read-only, opens DB but does not INSERT/UPDATE):
```
python .scripts/diagnose_role_classifier.py > /tmp/role_diag.txt
cat /tmp/role_diag.txt
```

**STAGE GATE — Rupert reviews `/tmp/role_diag.txt` before continuing.**
If Rupert spots a mis-classification (e.g. a "Chief Operating Officer" landing
in `ceo_cfo`), iterate on the regex and re-run. Do not proceed to Step 4
until Rupert signs off.

### Step 4 — Modify `export_dashboard_json.py`

Claude action: edits per §2.1–2.7. Key edits in order:
1. Add `LOOKBACKS` constant near top.
2. Add helpers `_within_lookback`, `_bucket_for_value`, `_load_sector_map`,
   `_signal_tier_for`, `_firing_row`, `_top_bottom`, `_ticker_rollup`.
3. Replace `cohort_value_buckets()` with `build_cohorts_value_bucket(...)`.
4. Replace `cohort_by_sector()` with `build_cohorts_sector(...)`.
5. Add `build_cohorts_role(...)`.
6. Add `build_drill_payload(...)` and the three thin wrappers.
7. Modify `build_payload()` so `cohorts` block uses the new builders.
8. Modify `run()` to emit the three new JSON files via `_atomic_write_json()`.
9. Update `summary` dict to carry `n_buckets`/`n_roles`/`n_sectors`.

Truncation check: after the edit, **use the Read tool** to verify the file
ends at the same `__main__` block and that line count ≈ 1350 (was 1036).

### Step 5 — Add cohort tests

Claude action: write `.scripts/test_stage_05_cohorts.py` per §6.1 below.
Pattern: follow `test_stage_04_6.py` — seed DB + CSV via temp dir, call
`ex.run()`, assert on the JSON shape.

Claude runs (safe — temp DB only):
```
python .scripts/test_stage_05_cohorts.py
```
Expected: all cases PASS.

### Step 6 — Run the full exporter (Rupert, PowerShell)

This step writes Zone B. Rupert pastes:
```
cd C:\Dev\DirectorsDealings
python .scripts\export_dashboard_json.py --verbose
```

Expected output: summary dict with `n_buckets: 4`, `n_roles: 3`,
`n_sectors: 20-50` (depending on data).

Rupert verifies:
```
Get-ChildItem .\dashboard\data\performance_*.json | Format-Table Name, Length
```
Expected: three files, each 30–200 KB.

### Step 7 — Smoke-check the JSON contents

Rupert pastes (PowerShell — read-only):
```
python -c "import json; p=json.load(open('dashboard/data/performance_bucket.json','r',encoding='utf-8')); print(list(p['buckets'].keys())); print(list(p['buckets']['100k-500k']['t21']['90d'].keys()))"
```
Expected: `['1k-25k', '25k-100k', '100k-500k', '500k+']` and the 9 drill-block
keys.

Repeat for role and sector files. If any payload is missing a key, stop and fix
before moving on.

### Step 8 — Run existing test suite to confirm no regression

Claude runs (safe — temp DBs only):
```
python -m unittest discover -s .scripts -p "test_*.py" -v
```
Expected: all suites still pass. `test_stage_04_6.py::case_03_basic_signals_payload_shape`
still asserts the `cohorts` key is present (the keys *inside* it changed but
the top-level key is preserved).

### Step 9 — Take a manual DB backup before declaring done

Rupert pastes (PowerShell):
```
Copy-Item .\.data\directors.db .\.data\directors.db.bak -Force
```
(Per the MEMORY note about the broken auto-backup. The exporter itself doesn't
write the DB, but every "I've shipped something" moment is a good backup
moment until auto-backup is fixed.)

### Step 10 — Stage gate before front-end work

Rupert pastes the three JSON files (or their byte sizes + key lists) into the
follow-up planning session and confirms the front-end plan can start. See §9.

---

## 6. Tests

### 6.1 New: `.scripts/test_stage_05_cohorts.py`

Pattern mirrors `test_stage_04_6.py`. Required cases:

| # | Case | Asserts |
|---|---|---|
| C1 | New cohorts shape — top-level keys | `cohorts` has `by_value_bucket`, `by_role`, `by_sector` |
| C2 | Each cohort is horizon-keyed | All 4 horizons present in each cohort |
| C3 | Each horizon is lookback-keyed | All 4 lookbacks (`90d`/`6m`/`1y`/`all`) present |
| C4 | Bucket tile filters to T1+T2 | Seed T3 row → does not appear in `by_value_bucket` rows |
| C5 | Role classifier groups correctly | Seed CEO + NED rows → `by_role.t21.all.rows` has both keys |
| C6 | Sector tile uses sector_map | Seed two sectors → both appear, key matches `tickers_meta.sector` |
| C7 | Three drill-down files emitted | `performance_bucket.json`, `performance_role.json`, `performance_sector.json` exist |
| C8 | Drill payload — shape | Top-level `buckets` key; each bucket has `label`, `t21`, etc; each drill block has all 9 fields |
| C9 | Top/bottom firings — correct sort | 12 firings seeded; top 10 desc by CAR, bottom 10 asc, no overlap if N≥20 |
| C10 | Top/bottom firings — N<10 edge case | 4 firings seeded → top_firings has 4, bottom_firings has 4 (per spec §2.3) |
| C11 | Rollup — N≥3 then N<3 ordering | Seed 3 tickers with N=4,2,1 → rollup order is N=4 first, then N=2, then N=1 |
| C12 | Sector benchmark fallback | Sector with null `benchmark_symbol` → drill block uses `^FTAS` and `benchmark_symbol` field reflects it |
| C13 | Lookback filtering — boundary | Firing exactly 90 days ago is included in `90d`; firing 91 days ago is excluded |
| C14 | Empty CSV → empty cohorts, no crash | All `rows` arrays empty, all `total_n` = 0, files still emitted with valid JSON |
| C15 | Idempotency | Two runs (no-timestamp) produce byte-identical files |

All cases use `tempfile.mkdtemp()` for DB + CSV + output dir. No writes to
`.data/`. Safe for Claude bash.

### 6.2 New: `.scripts/test_role_classifier.py`

Pure unit tests on the regex — see Step 2 above for the 12 cases.

### 6.3 Corpus diagnostic gate

`.scripts/diagnose_role_classifier.py` — described in §3.2. Run once by
Rupert before merge, results reviewed by Rupert, no automated assertion.

### 6.4 Performance regression bound

Add an `import time` block at the bottom of `test_stage_05_cohorts.py`:
- Build a synthetic CSV with 5,000 rows (the project will not realistically
  exceed this in v1).
- Time the full `ex.run()` call.
- **Assert wall-clock < 10 seconds.**
This catches accidental O(N²) regressions if a future edit puts a per-row
SQLite query inside the cohort loop.

### 6.5 Smoke test — exercises run()

`test_stage_05_cohorts.py::case_smoke_end_to_end`: minimum 5 firings, seed
all tables, run `ex.run()`, load all four output JSON files (`signals.json`,
`dealings.json`, `performance_bucket.json`, `performance_role.json`,
`performance_sector.json`) and assert each is valid JSON. Asserts the
`generated_at` field is present in all five.

---

## 7. Backwards compatibility & rollback

### 7.1 What will break the moment the new shape ships (before FE update)

`render_performance.py::_cohort_value_section` reads
`signals_data["cohorts"]["by_value_bucket"]` and expects a dict like
`{"1k-25k": -1.2, ...}`. The new shape gives a dict like
`{"t21": {"90d": {"rows": [...], "total_n": N}, ...}}`. The current renderer's
`bucket.get("1k-25k")` will return None for every key → the value cohort tile
renders four dashed-out rows ("Data not available").

`render_performance.py::_cohort_sector_section` reads `cohorts["by_sector"]` as
a flat list of `{"sector", "hit_pct", "base_rate", "n"}`. The new shape gives
horizon × lookback keyed buckets. Same outcome: the sector tile renders an
empty/error state.

**Net effect of shipping back-end alone:** the two existing cohort tiles on
the performance page render as empty / dashed. The rest of the page is
unaffected. Pending diagnostics, paper-trade stats, signal scoreboard, signal
charts — all unchanged.

### 7.2 Migration plan to avoid the "FE blank" gap

Two options. **Recommended: option A.**

- **A. Hold the back-end change until front-end is ready.** Implement everything
  in this plan, run all the tests, generate the three new JSON files locally,
  but **do not edit `build_payload()` to replace the existing `cohort_value_buckets()` /
  `cohort_by_sector()` calls** until the front-end is also ready. Keep the new
  builders next to the old, write them under a `signals_payload["cohorts_v2"]`
  key, then swap when FE is ready (one-line change). Pros: no broken intermediate
  state. Cons: needs a deliberate cut-over.

  Implementation: in Step 4 §2.7, the modification to `build_payload()` writes
  BOTH shapes:
  ```
  cohorts_v1_legacy = {
      "by_value_bucket": cohort_value_buckets(rows),
      "by_sector":       cohort_by_sector(rows, conn, today, base_rate_t21),
  }
  cohorts_v2 = {...new shape...}
  signals_payload["cohorts"] = cohorts_v1_legacy           # FE today reads this
  signals_payload["cohorts_v2"] = cohorts_v2               # FE tomorrow reads this
  ```
  After FE is updated, a one-line edit promotes `cohorts_v2` to `cohorts` and
  the legacy fields are deleted. Three new files (`performance_*.json`) ship
  immediately (they don't break anything that doesn't read them).

- **B. Hard cut.** Ship the new shape now, accept that the cohort tiles look
  blank for the duration until FE catches up. Rupert sees ugly dashboards for
  N days. Don't do this.

### 7.3 Rollback

If the new exporter is buggy:
1. `Copy-Item .\.data\directors.db.bak .\.data\directors.db -Force` (restores DB
   in case anything weird happened; not strictly required since this exporter
   doesn't write the DB).
2. `git diff` is not in play — Rupert's local-only workflow means we rely on
   the file's own history. Practical rollback: revert `export_dashboard_json.py`
   to the version saved before the edit. Plan: before Step 4 begins, Claude
   creates a backup copy `.scripts/export_dashboard_json.py.bak_pre_perf_redesign`
   so reverting is `Copy-Item`.

---

## 8. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Role regex mis-classifies (e.g. CEO → other_exec, COO → ceo_cfo) | Medium | High — wrong numbers shown on Role tile + drill page | Locked precedence in §3.1; mandatory corpus-diagnostic gate (§3.2); 12-case unit test (§6.2); Rupert reviews frequency table before merge |
| R2 | JSON payload bloat — drill files exceed 1 MB and browser stalls | Low | Medium | Size projection done (60–150 KB per file); test C8 size-asserts at <500 KB per file; "all" lookback is the biggest, mitigated by 10-row top/bottom cap |
| R3 | Missing `benchmark_symbol` for some sectors → broken sector benchmark column | Medium | Medium | Sector payload's drill block carries the benchmark symbol it used; null → `^FTAS` fallback with explicit field; diagnose script (§3.2) prints "sectors missing benchmark_symbol: N" |
| R4 | Exporter regression — runtime explodes from N seconds to N minutes | Medium | Low (build pipeline only, not user-facing) | C4 explicit perf budget (§6.4); sector_map / tx_lookup pre-built once per run, no per-row queries |
| R5 | FUSE truncation when Rupert runs the exporter and a new 200KB JSON file is being written | Low | High — corrupted JSON crashes the FE | `_atomic_write_json()` writes to `.tmp` + `os.replace()` (already implemented); the new payloads use the SAME helper |
| R6 | Front-end ships before back-end gate complete → renders against placeholder data | Low | Medium | §7.2 migration plan emits both `cohorts` (legacy) and `cohorts_v2` (new); stage gate at §9 explicitly named |
| R7 | role_class field is unreliable (empty for T3/T4/S1/F1 firings per QA Critical #4) | High (already known) | Mitigated by design | Regex fallback IS the main path per §3.1 docstring; corpus diagnostic at §3.2 surfaces the actual coverage |

---

## 9. Stage gate — what Rupert must approve before front-end work starts

Acceptance criteria — every one must be a "yes":

- [ ] `python .scripts/test_role_classifier.py` — all 12 cases PASS
- [ ] `python .scripts/diagnose_role_classifier.py` output reviewed; no mis-classifications
- [ ] `python .scripts/test_stage_05_cohorts.py` — all 15 cases PASS
- [ ] `python -m unittest discover -s .scripts -p "test_*.py"` — no regressions
- [ ] `python .scripts\export_dashboard_json.py --verbose` runs under 10s on the full corpus
- [ ] `signals.json` has both `cohorts` (legacy) AND `cohorts_v2` (new) keys
- [ ] Three new files exist: `performance_bucket.json`, `performance_role.json`, `performance_sector.json`
- [ ] Each file's top-level shape matches §4 (spot-check via `python -c "import json; print(list(json.load(open(...))[...].keys()))"`)
- [ ] Rupert eyeballs one drill block — picks the largest sector, opens the file, reads `top_firings[0]`, checks the date / ticker / director look plausible against a known recent trade
- [ ] `.scripts/export_dashboard_json.py.bak_pre_perf_redesign` exists (rollback safety net)

Once all green, the front-end plan can be commissioned with the JSON files as
its input contract.

---

## 10. Open questions for Rupert

1. **Migration strategy choice.** §7.2 recommends Option A (emit both legacy
   and v2 cohorts shapes during the back-end-only window). Do you agree, or
   would you rather hard-cut and accept ugly tiles for a day? Recommendation:
   Option A — costs ~10 LOC, removes intermediate-state risk.

2. **Spec §5.3 `outlier_flag` on bucket rows.** Spec keeps it on the
   `by_value_bucket.rows` entries (drives the ⚠ glyph on the bucket tile per
   §1.2) but drops it from per-firing rows. Confirm: the back-end emits
   `outlier_flag: true` on a bucket row if ANY firing in that bucket has
   |car| > 200%? That's the only definition the spec implies. Recommendation:
   yes, this is consistent with the per-signal `outlier_flag` already in
   `aggregate_signals()` (line 311 of `export_dashboard_json.py`).

3. **Performance regression bound.** §6.4 proposes <10 seconds for 5,000 rows.
   The actual corpus today is ~1,200 rows so this is generous. Is 10s
   acceptable, or should we hold the bar tighter (5s) to keep room for growth?
   Recommendation: ship with 10s; revisit when corpus reaches 5,000.

4. **Role classifier — should "Chairman" definitely be `other_exec`?** Some
   chairs are non-executive (the common UK convention). The QA flagged that
   "Senior Independent" → NED is correct; "Chairman" is ambiguous. The current
   regex rule order says "Non-Executive Director" beats "Chairman" (because
   NED is rule 2, other_exec is rule 3), so a "Non-Executive Chairman" lands in
   `ned` — that's correct. But a plain "Chairman" with no further qualifier
   lands in `other_exec`. Is that the right call? Recommendation: yes for v1,
   monitor diagnostic output, refine in v1.1 if Rupert sees noise. Could
   alternatively split out a fourth `chair` bucket — but that breaks the
   locked three-row scope (§5.4) and would need a re-spec.

5. **`signal_tier` for the firing-row badge — which signal wins if a fingerprint
   fires multiple signals?** A firing can hit both T1 and S1 (CEO/CFO buy in a
   cluster). The plan uses the existing `SIGNAL_ORDER` precedence (`t0` first,
   then `t1`, … last `f1`). Confirm this is the desired tie-break for the badge.
   Recommendation: yes — matches the existing dealings-table behaviour.

6. **`tickers_meta.company` is not a column.** The schema only has `ticker`,
   `sector`, `benchmark_symbol`, `is_aim`, `market_cap_gbp`, `updated_at`.
   The rollup table requires `company`. The plan currently sources company from
   `transactions.company` (most-recent row per ticker via a subquery). Is that
   acceptable? Recommendation: yes — that's where the existing dashboard
   already gets it from. No schema change needed.

7. **Scope-creep flags spotted during planning** (none baked into the plan,
   listed here for Rupert):
   - The QA review's item #18 asks for a `?role=ned` → "NED" mapping table
     documented somewhere. The plan emits `label_fn(key)` so the JSON itself
     carries the display label, but a docs/README entry would be nice. Not
     done — not in spec.
   - The QA review's item #13 (keyboard accessibility) is front-end only;
     mentioned here so it doesn't fall through the cracks when the FE plan
     starts.
   - Spec §1.3 says the sector tile shows "top 3 + bottom 2" — the plan emits
     ALL sectors and lets the FE slice. If Rupert prefers a thinner JSON
     payload, we could slice server-side, but that hard-codes a presentation
     choice into the data contract. Recommendation: keep current design.
