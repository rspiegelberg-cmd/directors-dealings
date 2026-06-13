# Sprint 63 — Conviction Flags — Plan

Sprint kicked off 2026-06-11. Items: B-155 (this section), B-159, B-161 (sections to
be added when each is planned). Precondition (Sprint 62 deploy) cleared 2026-06-11.

---

# B-155 (DIR-86) — Routine vs Opportunistic Trader Flag — Implementation Plan (Phase A)

**Sized: 3 pts. Phase A additive only — no signal-firing change, no dashboard change, no DB migration.**

## 0. What the data actually supports (measured 2026-06-11)

From `.data/_snapshots/transactions.csv` (bash served 4,875 of the true 6,383 rows — treat as approximate but directionally solid):

- Effective history is **one year**: 2025 = 3,052 rows, 2026 = 1,804; only 19 rows date pre-2025. Earliest trade date 2022-07-14 is an outlier, not coverage.
- 1,017 distinct (director, ticker) BUY pairs: **864 have buys in only 1 distinct calendar year, 152 in 2, exactly 1 in 3.**
- (director, ticker, month) cells with same-month buys in >=2 distinct years: **35**. In >=3 years: **0**.
- Name noise is real but small: 5 pairs collapse under lower/trim — drivers are **case variants** ("MURRAY MCGOWAN" / "Murray McGowan") and **non-breaking spaces** ("Serpil\xa0Timuray"). NBSP handling is mandatory, not optional.

Consequence: the literature's canonical rule (Cohen-Malloy-Pomorski 2012: same calendar month >=3 consecutive years) classifies **zero** directors today. The flag must have a third state and thresholds that activate at 2 years, tightening naturally as history accrues.

## 1. Exact definition of "routine"

Three-valued, walk-forward, per (director-key, ticker), evaluated per transaction:

For a BUY transaction `tx` with visibility timestamp `A = COALESCE(NULLIF(announced_at,''), date)` (same convention as `_select_firings`):

1. **History set H** = all BUY rows with the same (director-key, ticker) whose own effective announced-at is **strictly < A**. Visibility gates on announced-at; the calendar pattern itself uses the trade `date` column (the habit is about when they deal, not when the RNS prints).
2. `n_years` = count of distinct calendar years among trade dates in H.
3. **`insufficient_history`** if `n_years < ROUTINE_MIN_YEARS` (= **2**).
4. **`routine`** if there exists any calendar month `m` such that the number of distinct years in H containing a buy in month `m` is `>= max(2, floor(n_years/2) + 1)` — i.e. a strict majority of prior years, never fewer than 2. Exact month match, **no +- tolerance** (CMP/Ali-Hirshleifer use exact month; a tolerance parameter is unjustifiable on 35 candidate cells).
5. **`opportunistic`** otherwise.

Justification of thresholds: with `ROUTINE_MIN_YEARS = 2`, both prior years must hit the same month (2 of 2). At 3 prior years, 2 of 3 suffices. This is the loosest defensible reading of "most years"; today it can flag at most ~35 cells routine and marks ~85% of pairs `insufficient_history` — which is honest. **Auto-improvement:** the rule is parameterised only by distinct prior years, so as the feed accumulates 2027, 2028... data, directors graduate out of `insufficient_history` and the majority test gains discriminating power with zero code change. Leave a comment to revisit `ROUTINE_MIN_YEARS = 3` (full CMP) once ~3 years of feed history exist (~mid-2027).

Director-level semantics per the issue: the flag classifies the **director** (per ticker) as of the transaction; the transaction's own month is not part of the test, and the transaction itself is excluded from H (strict `<` also drops same-timestamp siblings from the same RNS — conservative, no intra-announcement leakage).

Non-BUY firing rows: emit empty cell (precedent: `_holding_pct_increase` returns None for non-BUY).

## 2. Where it lives, keying, and storage decision

**New module `.scripts/routine_flag.py`** (not inline in backtest.py), because Phase B signal gating must reuse the identical classifier from `.scripts/signals/*.py`, and a pure module is unit-testable without a backtest harness. Contents:

- `ROUTINE_MIN_YEARS = 2` (module constant, with revisit comment).
- `director_key(name) -> str` — canonical join key: NFKC-fold non-breaking/odd spaces to ASCII space, collapse whitespace runs, `strip()`, `casefold()`. Deliberately does **not** reuse `parse_pdmr._normalise_director_name` (that produces display Title-Case for storage; we need a match key, and importing parse_pdmr drags in heavy parser deps). Handles all 5 observed live collisions. Joint-PCA strings ("LESLIE VAN DE WALLE AND Domitille...") remain distinct keys — correct, they are distinct reporting entities.
- `build_buy_history_index(conn) -> dict[(director_key, ticker), sorted list of (effective_announced_at, trade_date)]` — one SELECT over `transactions WHERE type='BUY'`, built once per backtest run. 6,383 rows; trivial memory. Avoids a per-firing SQL query (contrast F1, which queries per-tx and matches `director = ?` exactly, case-sensitively — a known limitation we are not inheriting).
- `classify_routine(index, director, ticker, effective_announced_at) -> tuple[str|None, int|None]` returning `(routine_flag, routine_prior_buy_years)` — pure function, bisects the sorted list at the strict cutoff. This is where the lookahead guard lives.

**Storage: backtest CSV columns only. No DB column, no migration 015.** Reasons:

1. B-160 precedent: all six 52wk/momentum columns are compute-at-row-time CSV columns, zero migration, worked cleanly.
2. The flag is **as-of-relative and non-monotone**: a director classified `insufficient_history` today becomes `routine` or `opportunistic` next year, and `opportunistic` can flip to `routine`. A static `transactions` column would be permanently stale and need recurrent Zone-B backfills. (Contrast `first_time_buy`, which IS a DB column — but that flag is monotone once set; this one is not. Wrong shape for a column.)
3. Zero Zone-B write risk, no touch to `reparse_corpus.py` / `apply_edits.py` insert paths, lowest token cost.
4. Phase B computes it live inside the signal engine from the same module — it never needed to be in the DB.

## 3. Files to touch

1. **NEW `.scripts/routine_flag.py`** — as specified in section 2. Pure stdlib, no db.py import needed (takes a conn). ASCII-only prints (none expected), `encoding="utf-8"` if any file IO (none expected).

2. **`.scripts/backtest.py`** — four small edits:
   - Defensive import of `routine_flag` (pattern: the B-164 `backfill_short_interest` try/except at line ~57; if import fails, columns emit empty).
   - `_select_firings` (line ~383): add `t.director` to the SELECT list (it is not currently selected; `tx_type` and `effective_announced_at` already are).
   - `HEADER` (line ~99): append `"routine_flag", "routine_prior_buy_years"` after `"short_pct_at_announcement"`, before `"windows_available"` (exact B-164/B-160 placement pattern), with a `# B-155:` comment block.
   - `run_backtest`: build the index once before the firings loop (next to the `has_short_data` guard, ~line 450); per row call `classify_routine(...)` using `r["effective_announced_at"]`; append the two values to `writer.writerow` at the matching position (~line 655). Note: firings rows are `sqlite3.Row` — index by key, never `.get()`.

3. **NEW `.scripts/test_b155_routine_flag.py`** — see section 4. Model on `test_b160_52wk_momentum.py` (in-memory sqlite, `HERE` sys.path insert, HEADER position/length assertions).

No changes to `eval_signals.py`, `export_dashboard_json.py`, `build_dashboard.py`, schema, or any signal module. **Explicitly:** the "new signal_id touches 3 display layers" rule does not bite — Phase A adds no signal_id, so dashboard display is deferred to a future item if ever wanted.

## 4. Test plan (`test_b155_routine_flag.py`)

In-memory sqlite with a minimal `transactions(fingerprint, date, ticker, director, type, announced_at)` table. All ASCII prints, utf-8, no subprocess (so no `sys.executable` concern).

- **director_key:** case collapse (MURRAY MCGOWAN == Murray McGowan), NBSP `\xa0` collapse (Serpil Timuray case), multi-space collapse, idempotence, empty/None round-trip.
- **routine:** buys in Mar-2023, Mar-2024, Mar-2025; tx in 2026 -> (`routine`, 3). Majority variant: Mar-2023, Mar-2024, Jun-2025 -> 2 of 3 -> `routine`.
- **opportunistic:** buys scattered Jan-2024, Jul-2025, tx 2026 -> 2 prior years, no common month -> `opportunistic`. Edge: 2 prior years, same month in only 1 -> `opportunistic` (the `max(2, ...)` floor).
- **insufficient_history:** zero prior buys; one prior year only.
- **Lookahead guard (critical):** seed the index with a buy whose effective announced-at is **after** the tx being flagged, in exactly the month/year that would flip the result to `routine` — assert it is excluded and the flag stays `insufficient_history`/`opportunistic`. Also: a sibling row with **identical** announced-at timestamp is excluded (strict `<`). This is the B-155 equivalent of the P3-6 lookahead test and is non-negotiable.
- **announced_at fallback:** history row with empty `announced_at` uses `date` for visibility, mirroring the SQL COALESCE.
- **Non-BUY:** classify path through backtest emits empty for a SELL firing.
- **HEADER contract:** the two new columns exist at the expected position; HEADER length equals the writerow field count (B-160 tests 10-11 pattern).
- Cross-key isolation: same director on a different ticker does not contribute to H (per-(director, ticker) keying).

Gate: full sweep `python -m unittest discover -s .scripts -p "test_*.py"` green (~1,550 + new). Claude runs this from the sandbox; fall back to the /tmp mirror dance only if FUSE staleness produces an anomalous failure (per CLAUDE.md).

## 5. Deploy steps for Rupert (Zone-B, Windows PowerShell, exact order)

Claude first verifies every edited file with the Read tool (truncation check) and runs the full unittest sweep from the sandbox. Then:

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\backtest.py --verbose
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

Notes: `eval_signals.py` is **not required** — no CAR math or signal definitions changed (the backtest-before-eval rule applies only when CAR columns change; these are additive feature columns). `backtest.py` still writes a `backtest_runs` row, so the snapshot step is mandatory per the standing rule. Export/build are included because they read `_backtest_results.csv`; the new columns are additive and ignored, but rerunning keeps run_id references coherent.

**Post-deploy verification (Claude):** Read `.data/_backtest_results.csv` header + sample rows via the Read tool; confirm the two columns; confirm distribution sanity — expect a large majority `insufficient_history`, a meaningful `opportunistic` share, and a small `routine` count (upper bound ~35 director-month cells qualify in today's data). If `routine` count is 0, that is plausible but warrants a spot-check of one known 2-year same-month pair.

## 6. Out of scope (explicit)

- **Phase B** — using the flag to gate or down-weight signal firing (e.g. suppressing T-tier firings where `routine_flag == "routine"`, or a routine-suppressed signal variant). Separate, later, diff-first deliverable: it must ship with a before/after firing-count diff and goes through the per-bucket granularity rule.
- Dashboard display of the flag (would touch the 3 display layers; deferred).
- B-159, B-161 (separate Sprint 63 items).
- Any change to `first_time_buy`, F1's exact-match director comparison, or director name storage.

## 7. Estimate

**3 points**, confirming the original sizing. One new ~120-line pure module, four surgical backtest edits with two strong in-file precedents (B-160, B-164), one test file, no migration, no pipeline-order complexity. Main risk is test-sweep friction under FUSE staleness, already mitigated by the documented /tmp workaround.

**Outcome (deployed 2026-06-11):** bt_20260611T144931Z — 2,518 insufficient_history /
118 opportunistic / 9 routine. Distribution exactly as predicted.

---

# B-159 (DIR-90) — Net-Seller-Reversal Flag — Implementation Plan (Phase A)

**Sized: 2 pts. Phase A additive only — no signal-firing change, no dashboard change, no DB migration, no new feed.**

Literature basis: Cohen-Malloy-Pomorski (2012) conviction mechanism (alpha-research 2026-06-10 item #5): a director who was a **net seller** of this stock over the trailing 12 months and now **buys** is reversing a revealed negative stance — a high-conviction event.

## 0. What the data actually supports (measured 2026-06-11)

From `.data/_snapshots/transactions.csv` (bash served 4,875 of the true 6,383 rows — FUSE truncation, treat as approximate; ground truth 1,872 BUY / 1,321 SELL / 41 SELL_TAX):

- Of 1,636 BUY rows read: **79 had at least one prior-12m SELL** by the same (director-key, ticker); **64 had strictly negative net shares** (sells > buys) in the window. Scaling to the full 1,872 BUYs: **~70-75 BUY transactions expected to flag**, ~90 with any prior-12m sell. Backtest rows are signal firings (a transaction can fire several signals), so expect roughly **80-120 flagged CSV rows**. Healthy — enough for a first-cut alpha-scan split, not for significance claims.
- Field quality drives the metric choice: in the sampled rows, `shares` is populated and non-zero on **100%** of BUY, SELL and SELL_TAX rows; `value` is missing/zero on **~6% of SELLs and ~45% of SELL_TAXs**.

## 1. Exact definition

For a BUY transaction with visibility timestamp `A = COALESCE(NULLIF(announced_at,''), date)` (same convention as `_select_firings` and B-155):

1. **Window W** = all transactions with the same `(director_key, ticker)` whose own effective announced-at `eff` satisfies `eff_date >= A_date - 365 days` (inclusive lower bound, flat 365 days, leap-year off-by-one accepted and documented) **and** `eff < A` (strict full-string upper bound — the P3-6 lookahead guard, identical tuple-bisect discipline to `classify_routine`; same-timestamp RNS siblings and the transaction itself are excluded). `eff` may be `"YYYY-MM-DD"` or `"YYYY-MM-DDTHH:MM:SSZ"`; the lower bound compares the first 10 characters (`eff[:10] >= lo` where `lo = (date.fromisoformat(A[:10]) - timedelta(days=365)).isoformat()`), the upper bound compares full strings (lexical ISO compare; mixed-length behaviour matches B-155: a date-only history row on the announcement day counts as prior, a timestamped one does not).
2. **`net_shares_prior_12m`** = sum of shares of **BUY** rows in W minus sum of shares of **SELL** rows in W. Rows with NULL/zero/unparseable shares are skipped from the sums (measured zero such rows, defensive only).
3. **`seller_reversal_flag`** = `1` if `net_shares_prior_12m < 0`, else `0`. No history at all → `(0, 0)` (the first-buy case is already covered by F1/`first_time_buy`; this flag is specifically the reversal). Non-BUY firing rows emit **empty** for both columns (precedent: `_holding_pct_increase`, `classify_routine`).

**Metric: shares, not £ value.** Three reasons: (a) measured completeness — `value` is missing on ~6% of SELLs and ~45% of SELL_TAXs, `shares` on none; (b) the issue asks for net **position** change, and share count is the position — £ value confounds the position change with price moves between the sell and the buy; (c) shares need no price-audit dependency (`price_audit` shows 308 unresolved + 1,484 none). Known caveat: a share split inside the window would distort the sum — rare on a 12-month horizon, accepted and documented in the module docstring.

**Type taxonomy decision.** Sells = **SELL only; SELL_TAX excluded.** SELL_TAX is non-discretionary tax-withholding on vesting; Cohen et al.'s conviction logic is about a *chosen* negative stance being *chosen* to reverse — mechanical sells reveal nothing. Buys side = **BUY only**. **EXERCISE / GRANT / SIP excluded from both sides**: GRANT and SIP are non-discretionary inflows that would mask genuine net-seller status; EXERCISE is option mechanics. The SELECT filters `type IN ('BUY','SELL')` so the others never enter the index.

## 2. Where it lives

**New sibling module `.scripts/reversal_flag.py`**, importing `director_key` from `routine_flag` (do **not** reinvent it; do **not** copy it). Rationale for a sibling over extending `routine_flag.py`: B-155 shipped and QA'd today — re-opening it to generalise would force edits to a just-verified module plus its test file for zero functional gain. The cost of separation is one extra full-table SELECT per backtest run (~6.4k rows, milliseconds). Do not over-generalise: B-161 keys off `reporting_dates`, not this index, so a "universal trade index" has no third customer. Contents:

- `WINDOW_DAYS = 365` (module constant).
- `build_trade_history_index(conn) -> dict[(director_key, ticker), sorted list of (eff, type, shares)]` — one SELECT: `director, ticker, type, shares, COALESCE(NULLIF(announced_at,''), date) AS eff FROM transactions WHERE type IN ('BUY','SELL')`. Shares coerced to float at build time; bad values stored as `None` and skipped in sums. `sqlite3.Row` indexed by key, never `.get()`. Entries sorted; tuple bisect at `(str(A), "")` gives the strict-< cutoff exactly as in `classify_routine` (`""` sorts before any type string at equal eff).
- `classify_reversal(index, director, ticker, effective_announced_at) -> tuple[int|None, float|None]` returning `(seller_reversal_flag, net_shares_prior_12m)`; `(None, None)` on missing director/ticker/timestamp (same contract as `classify_routine`). Pure function; the caller gates on `tx_type == "BUY"`.

**Storage: backtest CSV columns only — no DB column, no migration.** Same shape argument as B-155: the value is as-of-relative and changes as the window slides; a static column would be permanently stale.

## 3. Files to touch

1. **NEW `.scripts/reversal_flag.py`** — as section 2. Pure stdlib (`datetime`, `bisect`, plus the `routine_flag` import). ASCII-only.

2. **`.scripts/backtest.py`** — four small edits:
   - Defensive import: `from reversal_flag import build_trade_history_index, classify_reversal` in its own try/except mirroring the B-155 one; on ImportError both names → `None`, columns emit empty.
   - `HEADER`: insert `"seller_reversal_flag", "net_shares_prior_12m"` **after** `"routine_prior_buy_years"`, **before** `"windows_available"`, with a `# B-159:` comment. 60 → **62**.
   - `run_backtest`: build `trade_index = build_trade_history_index(conn) if ... else None` alongside `routine_index`. Per-row: gate identically — `if trade_index is not None and classify_reversal is not None and tx_type == "BUY" and tx_director:` call `classify_reversal(trade_index, tx_director, ticker, announced)`; else `reversal_flag_val, net_shares_prior = None, None`. Append both to `writer.writerow` after `routine_years`, before `windows_available`.
   - `_select_firings`: **no change** — `t.director`, `t.type AS tx_type`, `effective_announced_at` already selected (B-155/B-156).

3. **Test assertion updates — all four HEADER-pinning suites** (every one asserts length 60 and positions relative to `windows_available`):
   - `test_b155_routine_flag.py` — length 60→62; positions: `idx_wa-1 == "net_shares_prior_12m"`, `-2 == "seller_reversal_flag"`, `-3 == "routine_prior_buy_years"`, `-4 == "routine_flag"`, `-5 == "short_pct_at_announcement"`.
   - `test_b160_52wk_momentum.py` — shift the position ladder back by 2; length 60→62.
   - `test_b156_resulting_shares.py` — same ladder shift; rename length test to `test_header_length_62`.
   - `test_short_interest.py` — rename to `test_header_has_62_columns`; ladder shift.

4. **NEW `.scripts/test_b159_reversal_flag.py`** — section 4.

No changes to `eval_signals.py`, `export_dashboard_json.py`, `build_dashboard.py`, schema, signal modules, or `routine_flag.py` (import only).

## 4. Test plan (`test_b159_reversal_flag.py`, ~25 tests)

Mirror `test_b155_routine_flag.py`: in-memory sqlite `transactions` table extended with a `shares` column, `HERE` sys.path insert, helper `_classify(rows, director, ticker, asof)`.

- **Reversal true:** SELL 10,000 six months before, BUY now → `(1, -10000.0)`.
- **Net-zero:** prior BUY 10k + SELL 10k → `(0, 0.0)`.
- **Net-buyer:** prior buys > sells → `(0, positive)`.
- **No history:** → `(0, 0)` (not None — first-buy is F1's job).
- **Window boundary (critical):** SELL with eff exactly `A_date - 365d` → **counted** (inclusive); SELL at `A_date - 366d` → not counted; for a timestamped `A`, the boundary computed off `A[:10]`.
- **Lookahead guard (critical, P3-6):** back-dated SELL announced after `A` → excluded; identical-timestamp sibling (`eff == A`) → excluded; `eff` one second before `A` → included.
- **SELL_TAX:** window containing only a SELL_TAX → `(0, 0)`; SELL_TAX + SELL mixed → only the SELL contributes to net.
- **EXERCISE / GRANT / SIP:** present in the table, absent from the index / no effect on net.
- **Name noise:** case-variant and NBSP-variant history merges (`director_key` reuse); joint-PCA string stays distinct.
- **Cross-key isolation:** other ticker / other director does not count.
- **announced_at fallback:** empty/None announced_at uses `date` for both visibility and window membership.
- **Shares hygiene:** NULL/zero-shares row skipped from sums.
- **Unusable inputs:** missing director/ticker/timestamp → `(None, None)`.
- **Backtest integration:** imports not None; HEADER contract — `idx_wa-1/-2` are the B-159 pair, `-3/-4` the B-155 pair, length 62; **source-level BUY gate** on `bt.run_backtest` (pattern: `test_run_backtest_gates_on_buy`).

Gate: full sweep `python -m unittest discover -s .scripts -p "test_*.py"` green. Read-tool truncation check on every edited file.

## 5. Deploy steps for Rupert (Zone-B, Windows PowerShell, exact order)

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\backtest.py --verbose
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

`eval_signals.py` **not required** (no CAR math or signal definitions change). Snapshot mandatory (backtest writes a `backtest_runs` row).

**Post-deploy verification (Claude):** 62 columns, pair in position; count `seller_reversal_flag == 1` rows — expect roughly **80-120** (below ~40 or above ~250 warrants a spot-check); confirm non-BUY rows emit empty; spot-check one flagged row's director against the transactions snapshot.

## 6. Out of scope (explicit)

No signal firing, no dashboard column, no DB migration, no SELL_TAX inclusion toggle, no £-value variant, no split adjustment, no Phase B threshold tuning (the alpha scan decides whether magnitude or binary earns a signal).

## 7. Estimate

**2 pts:** module ~80 lines (heavy reuse of B-155 patterns), backtest.py 4 small edits, 4 mechanical test-assertion updates, one new ~300-line test file, deploy is a rerun of the B-155 block.

**Outcome (deployed 2026-06-11):** bt_20260611T152313Z — 107 flagged / 2,538 not
(inside the predicted 80-120 band); median net -27,180 shares; many flagged rows
pair with f1_first_time_buy (first-ever buy after prior selling). 1,606 tests green.

---

# B-161 (DIR-92) — "First Window After Results" Flag — Implementation Plan (Phase A)

**Sized: 2 pts. Phase A additive only — no signal-firing change, no dashboard change, no DB migration, no new feed.**

Literature basis: alpha-research 2026-06-10 item #7 — UK MAR Art. 19(11) bans PDMR dealing in the 30 days before results, so the US "buy before earnings" signal barely exists here; the high-information moment is a buy in the **first dealing window just after results**. Transaction-level flag: BUY announcement falls within the window immediately after a confirmed `report_date` in `reporting_dates` (B-147 Investegate filings + B-111 LSE diary + B-096 Yahoo).

## 0. What the data actually supports (measured 2026-06-11)

From `.data/_snapshots/reporting_dates.csv` (2,676 rows) and `transactions.csv`:

- `reporting_dates` schema (migrations 008/009/012): `ticker, report_date (ISO date-only), report_type, source, fetched_at, confidence ('confirmed'|'est'), source_url`. **2,391 confirmed** (2,211 investegate — actual results RNS, ground truth; 180 lse_diary — scheduled diary dates) vs **285 est** (all synthetic EARNINGS estimates from backfill_expected_reporting_dates.py). 569 distinct tickers; range 2023-01-24 → 2026-12-05; **166 confirmed rows are future-dated** (all lse_diary, scheduled).
- Confirmed types: TRADING_STMT 1,147 / PRELIM 670 / INTERIM 564 / QUARTERLY 10.
- Join reality (sampled BUYs, calendar-day gap to most recent prior confirmed date, same-day inclusive): 85% of BUYs have ticker coverage; **43% have a prior confirmed date**; **280/1,636 within 14 calendar days** (~10 trading days). Week-0 alone holds 174 of the 700 with-prior-date BUYs (25%) — the post-results clustering the flag isolates.
- Scaled to 1,872 BUYs: **~320 flagged BUY transactions**; expect roughly **350-500 flagged CSV rows**.

**Decision — report_type scope: ALL confirmed types, including TRADING_STMT.** The issue says "a confirmed report_date", no type restriction; UK dealing codes routinely open the window after any scheduled results-type announcement, and for many small caps the trading statement IS the de facto results event; doubles the flag count (280 vs 135); the factor scan can re-split by type post-hoc. Revisit note in module docstring.

## 1. Exact definition

For a BUY firing with visibility timestamp `A = effective_announced_at` (standing COALESCE convention), compared on the date component `A[:10]`:

1. **D** = sorted, de-duplicated `report_date` values for the ticker where `confidence = 'confirmed'` (any report_type, any source).
2. **`last_results`** = max `d` in D with `d <= A[:10]` (**same-day inclusive**).
3. **`days_since_results`** = calendar days from `last_results` to `A[:10]` (integer >= 0).
4. **`post_results_flag`** = `1` if `days_since_results <= WINDOW_CALENDAR_DAYS` (= **14**), else `0`.
5. **No-coverage / no-prior-date** (ticker absent, or every known date is future relative to A): **both columns empty** `(None, None)`.

**Calendar days, not trading days.** The issue's "N=10 trading days" is implemented as **14 calendar days ≈ 10 trading days** (module constant, documented): (a) the ticker's own price-date calendar is wrong for thinly-traded tickers (sparse prices rows stretch "10 trading days" arbitrarily); (b) a ^FTAS calendar only spans the price backfill while report dates reach back to 2023; (c) project precedent is uniformly calendar days (B-159 flat 365d, B-096 60-day badge, B-111 0-60d); (d) the magnitude column makes the binary threshold non-binding — the scan can re-bucket `days_since_results` at any cutoff.

**Same-day buy counts (day 0 = in window).** UK results go out at 07:00 London; the MAR window OPENS at that announcement, and a same-day PDMR buy is precisely the canonical first-window trade (89 of the 280 sampled in-window BUYs are week-0). `report_date` is date-only so intraday ordering is unobservable; the theoretical dealing-before-results-same-day case is accepted and documented (a MAR-compliant PDMR cannot deal before the window opens).

**Lookahead reasoning (explicit).** A results announcement is public the moment it happens, so conditioning on a *past* report_date relative to a later buy is NOT lookahead even though our row was ingested later — the information existed publicly at decision time. The actual risks and mitigations:
  1. **`est` rows** — synthetic forecast dates. Excluded at SELECT time (`confidence='confirmed'`). This is the B-161 P3-6 guard.
  2. **Future-dated confirmed rows** (166 lse_diary scheduled) — a buy cannot be "after" results that haven't happened. Excluded by construction (`d <= A[:10]`).
  3. **Past lse_diary dates rescheduled after scraping** — data-accuracy error, not lookahead; bounded at 180/2,391; accepted and documented.

Non-BUY firing rows emit empty for both columns.

## 2. Where it lives

**New sibling module `.scripts/results_window_flag.py`.** Ticker-level — no director_key, no routine_flag import, no prices calendar. Pure stdlib (`bisect`, `datetime.date`):

- `WINDOW_CALENDAR_DAYS = 14` (constant; issue spec N=10 trading days implemented as 14 calendar; revisit only if the scan shows boundary sensitivity).
- `build_results_date_index(conn) -> dict[ticker, sorted list of report_date str]` — one SELECT: `SELECT DISTINCT ticker, report_date FROM reporting_dates WHERE confidence = 'confirmed'`; per-ticker sorted lists.
- `classify_post_results(index, ticker, effective_announced_at) -> tuple[int|None, int|None]` returning `(post_results_flag, days_since_results)`; `(None, None)` on missing/unknown ticker, missing timestamp, no prior confirmed date, or unparseable dates. `bisect_right(dates, A[:10])` → most recent prior-or-equal → date subtraction. Caller gates on `tx_type == "BUY"`.

**Empty-vs-zero contract:** both columns empty or both populated; `flag=1 iff days<=14`. "No coverage" is missing data, not an informative zero — follows the **B-164 semantics (empty = no data)**, not B-159's `(0,0)` (where no-history genuinely means not-a-reversal). `mean(flag)` over non-empty rows is then the honest in-window rate among covered tickers. Expect ~55-60% of BUY rows empty.

**Storage: backtest CSV columns only — no DB column, no migration.** Same shape argument as B-155/B-159.

## 3. Files to touch

1. **NEW `.scripts/results_window_flag.py`** — as section 2. ASCII-only.

2. **`.scripts/backtest.py`** — four small edits:
   - Defensive import (after the B-159 block): own try/except; on ImportError both names → None.
   - `HEADER`: insert `"post_results_flag", "days_since_results"` after `"net_shares_prior_12m"`, before `"windows_available"`, `# B-161:` comment (empty = no coverage). 62 → **64**.
   - `run_backtest`: index build next to trade_index, guarded BOTH by import success AND a `sqlite_master` check for the `reporting_dates` table (B-164 has_short_data analogue — old test fixtures don't create the table). Per row: gate `tx_type == "BUY"` only — **no tx_director requirement**. Append pair to writerow after `net_shares_prior`, before `windows_available`.
   - `_select_firings`: no change.

3. **Test assertion updates — all FIVE HEADER-pinning suites** (shift ladders by 2, length 62→64): test_b155_routine_flag.py, test_b159_reversal_flag.py, test_b160_52wk_momentum.py, test_b156_resulting_shares.py, test_short_interest.py. New ladder top: `idx_wa-1 == "days_since_results"`, `-2 == "post_results_flag"`, `-3 == "net_shares_prior_12m"`, `-4 == "seller_reversal_flag"`, `-5 == "routine_prior_buy_years"`, `-6 == "routine_flag"`, `-7 == "short_pct_at_announcement"`.

4. **NEW `.scripts/test_b161_results_window_flag.py`** — section 4.

No changes to eval_signals.py, exporters, schema, signal modules, the other flag modules, or any backfill script. The existing 60-day *pre*-results dashboard badge (B-096/B-111) is untouched — different direction, different surface.

## 4. Test plan (`test_b161_results_window_flag.py`, ~26 tests)

Mirror test_b159: in-memory sqlite with migration-009-shaped `reporting_dates` table, helper `_classify(rows, ticker, asof)`.

- **Index build:** confirmed-only (est excluded); DISTINCT dedup; sorted; mixed sources both included.
- **Flag true:** results 5 days before buy → `(1, 5)`.
- **Window boundary (critical):** gap 14 → `(1, 14)`; gap 15 → `(0, 15)`.
- **Same-day buy (critical):** `report_date == A[:10]` → `(1, 0)`.
- **Lookahead — future confirmed date (critical, P3-6):** only-future date → `(None, None)`; future + old prior → matches prior only.
- **Lookahead — est exclusion (critical, P3-6):** est row inside the window must not flag; with no other dates → `(None, None)`.
- **Multiple prior dates:** picks most recent; two prior both outside window → `(0, gap_to_most_recent)`.
- **No coverage:** → `(None, None)`; **outside window:** `(0, 47)` — disambiguation asserted explicitly.
- **Timestamped A** compares on `[:10]`; **unusable/malformed inputs** → `(None, None)`; **cross-ticker isolation**.
- **Invariant:** both-None or both-populated; `flag == (days <= 14)`.
- **Backtest integration:** imports; HEADER contract (B-161 pair, then B-159, then B-155; length 64); source-level BUY gate (no tx_director); missing-table guard emits empty (B-164 analogue).

Gate: full sweep green; Read-tool truncation check on every edited file.

## 5. Deploy steps for Rupert (Zone-B, Windows PowerShell, exact order)

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\backtest.py --verbose
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

`eval_signals.py` not required. Snapshot mandatory.

**Post-deploy verification (Claude):** 64 columns, pair in position; flagged count roughly **350-500** (below ~150 or above ~800 → spot-check); both-empty share of BUY rows ~55-60%; non-BUY empty; days never negative; spot-check one flagged row against reporting_dates.csv (matched row confirmed, not est).

## 6. Out of scope (explicit)

No signal firing, no dashboard badge, no migration, no trading-day calendar, no report_type sub-flags (the scan re-derives type by joining the snapshot), no backfill changes, no coverage-improvement work, no Phase B.

## 7. Estimate

**2 points.** One ~70-line pure module (simplest of the three flags — single-key lookup), four surgical backtest edits, five mechanical ladder updates, one ~300-line test file, deploy is a rerun of the standing block.
