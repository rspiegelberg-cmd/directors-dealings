# B-168 — Salary-multiple conviction feature: build spec

**Status:** Scoped 2026-06-13, ready for next sprint. GREENLIT by Rupert (B-163
CONDITIONAL-GO). Linear: DIR-99. Estimate **5–8 pts** (4 phases).
**Source of truth for the feasibility evidence:** `docs/research/b163-spike-memo.md`,
`b163-spike-extraction-notes.md`, `b163-spike-sample.csv` (20-company spike).

---

## 1. What this builds (and what it deliberately does not)

A new **conviction feature**: for each director buy, how big was the purchase relative
to that director's annual pay. The published insider-dealing literature says the alpha
lives in a high-conviction subset, and buy-size-relative-to-pay is the canonical
conviction measure. Our own alpha research (2026-06-10) found **no positive factor in
the current data** — so a new conviction feature is the credible path to a signal.

This lands as a **feature column only** — exactly like short interest (B-164) and
resulting-shares (B-156). It does **not** fire a new signal tier. It feeds the next
alpha factor scan; a signal is only built if the scan shows it works. This is the
project's established "feature column first" discipline and keeps the risk contained
(the honest downside, per the memo, is that — like every factor so far — it works only
as a *negative* filter).

**Out of scope:** full 627-ticker coverage; any new firing signal; PCA / non-board
PDMR pay (structurally ~27% of BUY rows — board directors only).

---

## 2. Scope of collection

**DECIDED (Rupert 2026-06-13): all firing tickers.** Measured from the live snapshot:
**387 tickers** carry a buy-side signal, giving **~952 distinct (ticker, director)
pairs** to collect a pay figure for. (Top-~100 was the memo's cheaper recommendation;
Rupert chose the wider sample for a stronger alpha scan.)

**Implication — Phase 2 is the long pole.** ~952 director-FY records at the spike's
median ~3 operations each is materially more collection than the memo modelled. Mitigations
baked into the build: (a) the `_pay_cache/` makes the run fully resumable, so it can be
done across several sessions; (b) **collect in firing-frequency order** — highest-signal
tickers first — so a usable sample exists early and the long tail fills in over time;
(c) the new-appointee / out-of-scope buckets short-circuit cheaply (no PDF fetch).
Estimate moves to the top of the band (see §10).

---

## 3. Data model — migration 015, new table `director_pay`

Schema head is currently **14**; this is **015_director_pay.sql** → head "15".

A separate table (not a `transactions` column) because pay is **per-director-per-financial-year**
and is reused across many buys by the same director. Joined to transactions at
compute time on the normalised director key.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ticker` | TEXT | |
| `director_key` | TEXT | NFKC + casefold normalised — must match the identity key already used by `routine_flag.py` / `reversal_flag.py` so it joins to transactions |
| `director_name_raw` | TEXT | as printed in the report |
| `fy_end` | TEXT (ISO date) | financial year the figure covers |
| `ar_published_at` | TEXT (ISO date) | when the annual report was published — **drives the lookahead guard** (see §6) |
| `pay_native` | REAL | figure in its reporting currency |
| `currency` | TEXT | GBP / USD / EUR |
| `fx_rate` | REAL | applied rate (native→GBP) |
| `fx_date` | TEXT | FY-end date used for the rate |
| `pay_gbp` | REAL | `pay_native × fx_rate` |
| `pay_type` | TEXT | `single_figure_total` / `base_salary` / `ned_fees` / `fee_waiver_zero` / `nominal` |
| `role_class` | TEXT | from the 8-tier role taxonomy at the time of the pay year |
| `pay_status` | TEXT | `ok` / `new_appointee_no_disclosure` / `out_of_scope` / `extraction_fail` |
| `source_rung` | TEXT | `a` (Companies House) / `b` (issuer AR/DRR PDF) / `c` (press/aggregator) |
| `source_url` | TEXT | |
| `confidence` | TEXT | high / medium-high / medium |
| `machine_readable` | INTEGER | 1 if pulled from a clean audited table, 0 if narrative/press |
| `fetched_at` | TEXT | ISO timestamp |

Unique key (idempotent upsert): **`(ticker, director_key, fy_end, pay_type)`** — 4-part,
because the dual-denominator decision stores two rows (total + base) per director-FY.
No-figure outcomes are recorded as a single row with `pay_type='none'`, `fy_end=''`, and
the reason in `pay_status` (keeps the unique key clean).

**As-built (Phase 1, 2026-06-13):** migration `015_director_pay.sql` + `db.py` chain step
14→15 + `director_pay.py` helpers (`convert_to_gbp`, `classify_nominal`, `salary_multiple`,
`upsert_director_pay`, `latest_pay_before`, reusing `routine_flag.director_key`) +
`test_b168_director_pay.py` (21 tests, all green against the real schema).

---

## 4. The metric — `salary_multiple`

**DECIDED (Rupert 2026-06-13): build both denominators, decide later.** Collect, for each
director-FY, BOTH the single-figure total remuneration AND the base salary wherever
separable, stored as two `director_pay` rows (`pay_type=single_figure_total` and
`pay_type=base_salary`). Compute two metrics:

- `salary_multiple_total = buy_value_gbp / single_figure_total_gbp` — available for ~all
  directors (15/15 in the spike), noisier (includes bonus/LTIP).
- `salary_multiple_base  = buy_value_gbp / base_salary_gbp` — cleaner, but base salary was
  separable for only ~half the spike sample, so expect more NULLs on this one.

Both land as feature columns; the alpha scan decides which (if either) carries signal.
Cost note: collecting both adds review effort but little fetch effort — both figures sit
in the same audited table once the PDF is open.

NULL (not zero) when `pay_status != ok`.

NULL (not zero, not a number) when `pay_status != ok` — i.e. new appointees, fee-waiver
£0, and nominal pay all produce a NULL multiple in their own bucket, never a divide-by-zero
or a fake number.

---

## 5. Collection pipeline — `backfill_director_pay.py` (ZONE B — Rupert runs)

**As-built (Phase 2, 2026-06-13) — three lanes, all preview/`--confirm`, 14 tests green:**
- `--worklist` → `.data/_pay_worklist.csv`: the prioritised target list from the DB
  (387 in-scope (ticker, director) pairs, firing-frequency order, PCA-only + excluded
  issuers dropped, deduped by `director_key`). The spine the collection works down.
- `--from-sources` (auto, rung b): reads `.data/_pay_sources.csv`
  (`ticker, director, fy_end, ar_url, ar_published_at, currency`), downloads each AR PDF
  (cached in `.scripts/_pay_cache/`), `pdftotext -layout`, `extract_pay_figures()` pulls
  the single-figure total + base salary, FX-converts, classifies, upserts both rows.
- `--from-manual` (human, rungs c / JS-portal / nominal / new-appointee): reads
  `.data/_pay_manual.csv` (`ticker, director, fy_end, pay_native, currency, pay_kind,
  status, ar_published_at, source_url, source_rung, confidence, machine_readable`) →
  same validated `build_record()` path. `pay_kind` ∈ total|base|ned_fees|none;
  `status` carries new_appointee_no_disclosure / out_of_scope / extraction_fail.
- Audit: `.data/_director_pay_backfill.log` (JSONL append). End-of-run coverage report.
- **Honest limit:** auto AR-URL *discovery* is out of v1 scope (the brittle part — JS
  portals, no clean registry). The worklist + manual lane carry the names the auto lane
  can't reach; populating source URLs / figures is the human (or Claude-web-research) step.

Follows the `backfill_resulting_shares.py` / `backfill_short_interest.py` pattern:
preview / `--confirm`, fingerprint-matched idempotent upsert, JSONL audit, resumable cache.

For each in-scope (ticker, director) carrying a buy signal:

1. **Appointment date** via Companies House web filing-history (keyless) → if appointed
   < 1 FY before the buy and no AR yet covers them → write `pay_status=new_appointee_no_disclosure`
   and stop (this is the dominant failure mode — 4 of 5 spike misses — and must be a
   bucket, not an error).
2. **Rung (b): issuer AR / DRR PDF.** Locate the report URL, `curl` it, `pdftotext -layout`,
   grep the single-figure table. The spike proved this works on PDFs up to 15.5 MB and
   collapses the cost from ~5 min manual reading to ~30 s scripted. (Plain web_fetch
   truncates at ~125 k chars — fine for a standalone DRR, not a full AR — so use
   curl+pdftotext for anything large.)
3. **Rung (c) fallback: web search / press / aggregator** (CityAM, ii.co.uk, gurufocus,
   Simply Wall St) for the single figure. Tag `confidence=medium`, `machine_readable=0`.
4. **FX**: if the report is USD/EUR (5/15 in the spike), convert at the **FY-end** rate,
   storing currency, rate and date.
5. Cache every fetched document/result under `.scripts/_pay_cache/` so re-runs are free
   and the annual refresh only fetches new FYs.

**Cost:** mostly £0 (curl/pdftotext/search). A few $ at most if an LLM is used to parse
messy table text into the figure. Median 3 operations/name in the spike → ~100 names is
a session or two of supervised running.

**Human-in-loop:** rung-(c) press figures are medium-confidence; recommend a quick review
pass on those before they count (see decision D4). Rung-(b) audited-table figures need
no review.

**Edge cases the script must handle (all observed in the 20-name spike):**
- Zero / nominal pay → `fee_waiver_zero` / `nominal` bucket, NULL multiple (ASC £0; KZG £5k).
- NED fee vs exec package → store `role_class`; a £37k chair fee and a £3.1m CEO package
  are not comparable multiples — segment by role downstream, don't blend.
- Part-year / role-transition years (ULVR, BKG) → flag in notes, never average.
- Staleness → store `fy_end`; pay lags the buy 3–15 months, which is fine as long as the
  lookahead guard (§6) is respected.

---

## 6. Backtest wiring + the lookahead guard (non-negotiable, per P3-6)

Add to `backtest.py` HEADER: `salary_multiple_total`, `salary_multiple_base`,
`pay_total_gbp`, `pay_base_gbp`, `pay_fy_end`, `pay_confidence`, `pay_status`. Join
`director_pay` → transactions on `(ticker, director_key)`.

**Lookahead guard:** attribute to a buy only the latest pay figure whose
`ar_published_at <= buy announcement date`. Using `fy_end` alone is wrong — the figure
isn't *knowable* until the annual report is published (3–6 months after FY-end). This is
the same strictly-prior discipline that QA caught on B-164's short interest. A unit test
must assert no future-published pay leaks into a buy's feature row.

Feature only — no change to `eval_signals` firing logic.

---

## 7. Tests (sandbox-safe, Claude runs)

- FX conversion (USD/EUR → GBP at FY-end rate).
- Zero / nominal / fee-waiver bucketing → NULL multiple, correct `pay_status`.
- `director_key` normalisation matches the existing routine/reversal key.
- **Lookahead guard**: a pay row published after the buy is excluded.
- Upsert idempotency on `(ticker, director_key, fy_end)`.
- HEADER column presence (the project's standard HEADER-count guard suites).

---

## 8. Deploy sequence (Rupert, Windows — Zone B)

**Confirm the collected pay first (with an eyeball gate), then deploy.**

Step A — confirm pay into `director_pay`. Use the helper `confirm_director_pay.bat`
(preview → **review** → apply), or run manually:
```
python .scripts\backfill_director_pay.py --from-manual            # PREVIEW only
REM  >>> EYEBALL the preview before applying <<<
REM  The collector's figures are auto-gathered; a wrong row will pollute the
REM  alpha feature. Reject any row where a CFO total exceeds its CEO at the
REM  same company, or any figure that looks wrong for the company's size.
python .scripts\backfill_director_pay.py --from-manual --confirm  # apply
python .scripts\snapshot_db.py
```

Step B — make the feature live in the backtest (only once a worthwhile batch is in):
```
python .scripts\backtest.py
python .scripts\eval_signals.py --rebuild
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

(backtest **before** eval; export **between** eval and build — the standard ordering.)

**Source policy (2026-06-13):** the scheduled collector records figures ONLY from
company annual-report / remuneration-report PDFs — press/aggregator figures proved
~12% misattributed in the first batch and are no longer accepted.

---

## 9. Refresh cadence

Annual, after AR season (most FY-ends Dec/Mar; ARs land Mar–Jul). Re-run
`backfill_director_pay.py` once a year; the cache means it only fetches new FYs, and
new-appointee NULLs fill in as their first AR publishes.

---

## 10. Phasing (8 pts — top of band, given the wide scope + dual denominators)

| Phase | Pts | Content | Zone |
|---|---|---|---|
| 1 | 2 | migration 015 + `director_pay` table (both pay_types) + db helpers + FX handling + tests | A (Claude) |
| 2 | 4 | `backfill_director_pay.py` collection (rungs a/b/c, cache, appointment-date bucket, dual-figure capture, upsert, audit); run across all 387 firing tickers / ~952 director-FY records **in firing-frequency order** | A build (Claude) / B run (Rupert) |
| 3 | 2 | backtest HEADER cols (both multiples) + lookahead pub-date guard + tests; deploy | A (Claude) + B deploy |
| 4 | 1 | re-run the memo §3.5 overlap test vs B-156 once n≥5 paired rows; refresh alpha-scan inputs | A (Claude) |

**Sprint-fit caveat:** at ~952 records the *collection run* (Phase 2) is longer than a
single sprint's worth of supervised execution. The code build still fits one sprint; the
data fill is resumable and proceeds in the background across sessions. Recommend treating
"feature built + top-firing tickers collected" as the sprint exit, with the long tail
filling in after. If that's not acceptable, fall back to the memo's top-~100 scope for v1.

---

## 11. Decisions to confirm before Phase 2

- **D1 — denominator.** ✅ RESOLVED 2026-06-13: build BOTH (single-figure total + base
  salary), two metrics, alpha scan decides. See §4.
- **D2 — scope.** ✅ RESOLVED 2026-06-13: ALL firing tickers (387 tickers / ~952
  director-FY records), collected in firing-frequency order. See §2 + §10 caveat.
- **D3 — acceptance bar.** Suggest **≥75% of in-scope (established-director) BUY rows**
  get a non-null multiple = success; below that, park the feature.
- **D4 — rung-(c) review.** Human review pass on medium-confidence press figures before
  they count? (Recommended: yes.)
- **D5 — FX source.** Small curated FY-end-rates table (cheap, auditable) vs a rates API.
  (Recommended: curated table — only a handful of currencies/years.)
