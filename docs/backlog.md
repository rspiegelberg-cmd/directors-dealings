# Backlog — known glitches and follow-ups

Living list of known issues, parser gaps, and improvement opportunities.
Add new items at the bottom of the relevant priority section; mark items
as `RESOLVED` with the date when fixed rather than deleting (keeps an
audit trail).

Priority key:
- **P1** — real data is wrong or missing; affects trustworthiness of figures.
- **P2** — nice-to-have; improves robustness or developer experience.
- **P3** — exploratory; not urgent.

> **Forward planning lives in `docs/specs/sprint-plan-2026-06-03-sprints-20-onward.md`** —
> the single source of truth for what's grouped into Sprint 20+. Read it before scoping.
>
> **⚠ B-number collision (resolve before Sprint 20).** Three docs independently reused
> **B-072→B-077** (29-May backlog, Sprint-15 draft, 2-Jun incident). The agreed fix: keep
> the 29-May numbers as originals; the 2-Jun incident follow-ups are renumbered to a clean
> **B-090+ block** (awaiting Rupert's confirmation):
> - **B-090** — bundled multi-PDMR recovery (~2,271 filings).
> - **B-091** — historical never-scraped backfill (unblocked by the 2-Jun discovery fix).
> - **B-092** — non-RNS provider (BZW/EQS/GNW) parser-layout check.
> - **B-093** — UTL/UIL £69,922 NED buy stores BUY but fires no `t3_ned_buy` — confirm IT-exclusion vs threshold/role edge.

---

## 2026-06-06 batch — Sprints 50–52 (B-122 → B-133)

From the 2026-06-06 product-fixes review — 12 Rupert-reported items, each diagnosed
read-only by a specialist agent. Full diagnoses + agreed designs:
`docs/specs/product-fixes-2026-06-06-plan.md`. Sprint plan + Linear map:
`docs/specs/roadmap-2026-06-06.md`. Linear issues DIR-45 → DIR-56.

**Sprint 50 SHIPPED 2026-06-06** — B-129, B-131, B-122, B-133, B-126, B-127 coded (Zone-A only) + ground-truth verified; DIR-45→50 = Done. Green test sweep + deploy outstanding on Rupert's Windows side (see roadmap-2026-06-06.md "SHIPPED" notes).

| B-ID | Linear | Item | Sprint | Pri | Pts |
|------|--------|------|--------|-----|-----|
| ✅ B-129 | DIR-45 | Review "Apply & publish" stuck (stale `_apply_status.json` lock — 409s every apply + perpetual spinner). Immediate unblock: delete the file; then apply-reset endpoint + stale-aware guard + terminal status + real logs. **Keystone.** | 50 | P1 | 3 |
| ✅ B-131 | DIR-46 | Published review items don't drop off the page (mostly downstream of B-129; plus `done`-handler never re-calls `loadPendingReview()`). | 50 | P1 | 1 |
| ✅ B-122 | DIR-47 | This-Week "Signals Today" undercounts — counts by dealing date not announcement date; COALESCE-bucket on `announced_at`. | 50 | P1 | 1 |
| ✅ B-133 | DIR-48 | Company-page BMK column always blank — `build_dashboard.py` never writes the `bench` key the renderer reads; emit it (fallback `^FTAS`). | 50 | P1 | 1 |
| ✅ B-126 | DIR-49 | Cohort table CAR vs Net-of-Costs confusion — math correct (CAR already removes benchmark; net = CAR − cost); relabel + tooltips. | 50 | P2 | 1 |
| ✅ B-127 | DIR-50 | Cohort table: add absolute stock-return column, measured **from announcement date**. | 50 | P2 | 2 |
| ✅ B-123 | DIR-51 | £10k-per-signal strategy tracker vs FTSE All-Share. **DONE** (verified already-built 2026-06-06). | 51 | P2 | 5 |
| ✅ B-124 | DIR-52 | Paper-book signal filter — row badge as live filter. **DONE.** | 51 | P2 | 2 |
| ✅ B-125 | DIR-53 | Monthly Activity: remove corporate/PCA sell volume. **DONE.** | 51 | P1 | 2 |
| ✅ B-128 | DIR-54 | Sector data source (FMP, ticker-keyed). **DONE + DEPLOYED 2026-06-06** — 212/511 sectors written; remainder fills over next 1–2 daily runs as quota resets. | 52 | P2 | 3 |
| ✅ B-130 | DIR-55 | Review-form parser prefill. **DONE + DEPLOYED 2026-06-06** — 2,132 prefills (1,191 recovered + 941 best-guess). | 52 | P2 | 2 |
| ✅ B-132 | DIR-56 | Cluster-brewing trend = count of brewing clusters vs 30-day avg + 8-week sparkline. **DONE** (built 2026-06-06). | 52 | P2 | 3 |
| ✅ B-090C | DIR-18 | Multi-tranche SIP parser (aggregated-row reader). **DONE + DEPLOYED 2026-06-06** — +203 SIP recovered. | 38 | P2 | 3 |
| ✅ B-121 | DIR-44 | PCA/corporate parser — Mode-3 false-bundled recovery. **DONE + DEPLOYED 2026-06-06** — +56 individual buys (+94 signals). | 38 | P2 | 5 |
| ✅ B-136 | DIR-63 | Scope scoring to individuals' straightforward buys/sells (exclude SIP/awards + corporate holders). **DONE + DEPLOYED 2026-06-06.** | — | P2 | 3 |
| ⏸ B-119 | DIR-42 | Backfill prices for `no_market` tickers then re-reconcile. **PARKED 2026-06-06** (Rupert) — data-availability limited; display already mitigated by the B-120 unverified chip. Lowest value on the board. | — | P3 | 2 |

---

## 2026-06-07 batch — Sprint 53 (B-137 → B-142)

Predictive-edge epic: the pivot from descriptive (what worked) to predictive (what will
work). Unblocks the small-cap conviction baskets, currently **unmeasurable** — market cap
is known for only 31/605 tickers (all large) and just 24/1,264 firings are AIM-tagged.
Success basis (Rupert 2026-06-07): **beat the benchmark, net of costs (net CAR)**.
Full scope: `docs/specs/predictive-edge-2026-06-07-plan.md`. Linear DIR-64 → DIR-69.

| B-ID | Linear | Item | Sprint | Pri | Pts |
|------|--------|------|--------|-----|-----|
| B-137 | DIR-64 | Market-cap & shares-in-issue enrichment (lse.co.uk SharePrice scrape; `backfill_market_cap.py`). **Critical-path blocker.** | 53 | P1 | 5 |
| B-138 | DIR-65 | Small-cap classification + basket filter inputs (widen `is_aim`, `small_cap` flag). Depends B-137. | 53 | P2 | 3 |
| B-139 | DIR-66 | Backtest re-run + validate small-cap/chairman/PCA baskets populate (incl. AIM benchmark fix). Depends B-137/138. | 53 | P2 | 3 |
| B-140 | DIR-67 | Basket Report production page (`render_baskets.py` + config + nav + nightly build; ranked by net CAR). Depends B-139. | 53 | P2 | 5 |
| B-141 | DIR-68 | Basket validation discipline (pre-registration + n≥30 "not proven" gate; median headline). | 53 | P3 | 2 |
| B-142 | DIR-69 | Automate daily LSE diary scrape (`daily_diary_scrape.bat` shipped; one-time `schtasks`). Todo. | 53 | P2 | 1 |
| ✅ B-143 | DIR-70 | Duplicate transaction detection + removal — exact fingerprint dedup + near-dedup (same director/value, date ±7 days). `.scripts/dedup_transactions.py`, 455 lines. DB: 6,454→6,338 (116 removed). **DONE 2026-06-07.** | 53 | P2 | 3 |
| ✅ B-144 | DIR-71 | Strategy tracker: T+1/T+21/T+90/T+252 horizon toggle pills + per-signal chip toggles (11 buy signals). Multi-horizon compute in `export_dashboard_json.py`; interactive Chart.js panel in `render_performance.py`. **DONE + DEPLOYED 2026-06-07.** | 53 | P2 | 3 |
| B-145 | DIR-72 | **Upcoming Events box on This Week page** — rolling 2-week forward look at reporting calendar events. Reads from `reporting_dates` table. Displays: ticker, company name, event type (interim/full/AGM), date. Sorted date-ascending (nearest first). Zone A: `export_dashboard_json.py` + `render_index.py`. | 53 | P2 | 2 |

---

## Sprint 61 — New Data Feeds (kicked off 2026-06-10, Cycle 4 — CLOSED 2026-06-11)

Scope confirmed by Rupert: **B-156** (resulting-holding parse → % stake increase, the #2
ranked data gap), **B-163** (salary multiple — **feasibility spike only**: 20-report
sample + overlap check vs B-156, go/no-go deliverable; full pipeline stays gated),
**B-164** (FCA short-interest CSV ingest, feature column first). All three → Todo,
Cycle 4, milestone "Sprint 61 — New Data Feeds (Conviction + Short Interest)".

**Closed 2026-06-11, all three Done.** Deploy: 1,490 tests OK, schema v14. B-164 PASS —
107,312 rows ingested, 34.9% BUY-ticker coverage, 13yr holder-level history banked before
the 13-Jul FCA cutoff. B-156 missed its ≥10% bar (2.9%) — diagnostic found precision
clean (6/6 verified, 0 false positives) but recall narrow + 46% of BUYs have no url:
follow-ups **B-166** (pattern widening → ~17% projected) + **B-167** (no-url population).
B-163 verdict CONDITIONAL-GO (94% in-scope extraction; redundancy test inconclusive at
n=1) → **B-168** (scoped build, awaiting Rupert). Memo: docs/research/b163-spike-memo.md.

| B-ID | Linear | New follow-up item | Pri |
|------|--------|--------------------|-----|
| B-166 | DIR-97 | Widen B-156 resulting-holding patterns (anchor "purchase/acquisition/trades" + predicate forms + Families 3/4) → projected ~17% BUY coverage. Precision guards unchanged. | P1 |
| B-167 | DIR-98 | 859 BUY rows (46%) have empty url — unreachable by cache-based backfills; investigate ingest path + url restoration vs metric exclusion. | P1 |
| B-168 | DIR-99 | Salary-multiple scoped build (top ~100 signal tickers, annual refresh) per B-163 CONDITIONAL-GO memo. Gated on Rupert's read of the memo. | P2 |
| B-169 | DIR-100 | mktcap scraper dot-suffix slug retry + CSV seed for 13 BUY tickers missing market cap (AI., TW. etc 404s; JUST/SXS/SOLG page misses; BRSC/SEQI are ITs — exclude). | P1 |

---

## Sprint 63 — Conviction Flags (kicked off 2026-06-11, Cycle 4 — CLOSED 2026-06-11)

**Closed same day, all three Done + deployed.** HEADER 58→64 across the sprint
(three CSV-only column pairs, no migrations, schema stays v14). Final state
(bt_20260611T212842Z): routine 9 / opportunistic 118 / insufficient 2,518;
seller-reversal 107 flagged; post-results 515 flagged (day 0-1 dominate).
Six new conviction columns banked for the next alpha factor scan.

Scope confirmed by Rupert: three alpha-research feature flags computed entirely from
existing data — no new feeds, no Zone-B scraping. All three → Todo, Cycle 4, milestone
"Sprint 63 — Conviction Flags (Routine / Reversal / Post-Results)". ~7–8 pts.

| B-ID | Linear | Item | Pri |
|------|--------|------|-----|
| ✅ B-155 | DIR-86 | **Routine vs opportunistic trader flag.** New `.scripts/routine_flag.py` (director_key NFKC/casefold, strict-< lookahead guard, majority rule, ROUTINE_MIN_YEARS=2); backtest HEADER 58→60 (`routine_flag`, `routine_prior_buy_years`); CSV-only, no migration. 27 tests + 3 HEADER suites updated; QA PASS. **DONE + DEPLOYED 2026-06-11** (bt_20260611T144931Z: 2,518 insufficient_history / 118 opportunistic / 9 routine). | P2 |
| ✅ B-159 | DIR-90 | **Net-seller-reversal flag.** New `.scripts/reversal_flag.py` (shares-based net, SELL-only sells, 365d inclusive window, strict-< lookahead); backtest HEADER 60→62 (`seller_reversal_flag`, `net_shares_prior_12m`); CSV-only. 29 tests; 1,606 sweep green; QA PASS. **DONE + DEPLOYED 2026-06-11** (bt_20260611T152313Z: 107 flagged / 2,538 not — in predicted 80-120 band; many pair with F1 = first-buy-after-selling, the high-conviction cohort). | P2 |
| ✅ B-161 | DIR-92 | **First-window-after-results flag.** New `.scripts/results_window_flag.py` (confirmed-only dates, 14-cal-day window, same-day inclusive, empty=no-coverage); backtest HEADER 62→64 (`post_results_flag`, `days_since_results`); CSV-only. 26 tests; 1,633 sweep green; QA PASS. **DONE + DEPLOYED 2026-06-11** (bt_20260611T212842Z: 515 flagged / 683 outside / 1,447 no-coverage; day 0-1 dominate — post-results clustering confirmed; CKN day-0 spot-check verified). | P2 |

**Preconditions:** ✅ CLEARED 2026-06-11 — Sprint 62 deploy run by Rupert and
verified via snapshot (6,383 tx, schema v14): no-url BUYs 46% → ~8%;
resulting_shares on ~14.9% of BUYs (clears the ≥10% B-156 bar). Sprint 63 build
unblocked.
**B-168** (salary-multiple build) stays gated on Rupert's read of
`docs/research/b163-spike-memo.md`. Board hygiene: DIR-89 (B-158) closed in Linear
2026-06-11 — was already shipped 2026-06-10.

---

## 2026-06-10 batch — Alpha-research data points (B-155 → B-164)

From the quant + trader agent review (`docs/research/alpha-research-2026-06-10.md`).
Quant verdict: no positive factor combination in current data (all 49 robust cells
negative — avoid-filter only); PCA hypothesis rejected. Path to alpha = new conviction
data points + honest cost model + longer history. Linear DIR-86 → DIR-95.

| B-ID | Linear | Item | Pri | Notes |
|------|--------|------|-----|-------|
| B-155 | DIR-86 | Routine vs opportunistic trader flag (computed from own trade history; Cohen-Malloy-Pomorski). | P2 | No new feed; strengthens with B-162 |
| B-156 | DIR-87 | Parse "resulting holding" from RNS → % increase in director's stake. #2 data gap. | P2 | **→ Sprint 61** Parser + migration + reparse (Zone B) |
| ✅ B-157 | DIR-88 | **Dynamic per-stock spread cost (Corwin-Schultz from OHLCV) replacing flat cost_bps.** `backtest.py`: `_ticker_ohlc` cache + `_cs_spread_bps()`; 762 tickers backfilled (203k H/L rows); 99% dynamic costs (5–450 bps). **DONE 2026-06-10.** | P1 | Diff-first; rerun backtest→eval→export→build |
| ✅ B-158 | DIR-89 | **Sector coverage backfill: 24% → ~73% (493/675 tickers).** `backfill_sectors.py` FMP API; 3-day free-tier run; AIM guard (`is_aim=1` preserves `^FTSC`). **DONE 2026-06-10.** | P1 | Extends B-147/DIR-74 theme |
| ✅ B-165 | DIR-96 | **Sector name normalisation: FMP → project-canonical GICS names.** `SECTOR_NORMALISE` dict + `normalise_existing()` in `backfill_sectors.py`; `--normalise` one-shot DB fix mode. Collapses 5 duplicate buckets ("Financial Services"→"Financials" etc). 22 tests. **DONE 2026-06-10.** | P1 | Follow-on to B-158; run `--normalise` then rebuild |
| B-159 | DIR-90 | Net-seller-reversal flag (extends F1 first-buy). | P2 | No new feed |
| ✅ B-160 | DIR-91 | **Distance from 52-wk low/high + 1m/3m/6m momentum features.** `backtest.py`: `_rolling_hl()` + `_prior_close()` + 6 new HEADER cols (55 total). 17/17 tests green. **DONE 2026-06-10.** | P2 | Deploy: backtest → eval → export → build |
| B-161 | DIR-92 | "First window after results" flag (UK MAR timing — buys cluster post-results). | P2 | Uses reporting_dates |
| ✅ B-162 | DIR-93 | **Extend price + benchmark history backward 5 yrs (Yahoo `--extend` flag).** `backfill_prices.py`: `_default_from()` 1827 days; `--extend` bypasses smart-range. **DONE 2026-06-10.** | P2 | Zone B: run `backfill_prices --extend` then full rebuild |
| B-163 | DIR-94 | Director salary multiple (annual-report remuneration scrape). Feasibility spike first; may be redundant after B-156. | P3 | **→ Sprint 61 (spike only)** |
| B-164 | DIR-95 | Short interest ingestion (FCA daily disclosures CSV, ≥0.5% only). | P3 | **→ Sprint 61** Exploratory feature column |

---

## ✅ Shipped log (canonical — newest first)

Back-dated record of what's actually been delivered, so we never again disagree about
what's done. Add a row here the day something ships. This is the single source of truth;
the old "Recently shipped" table at the bottom is folded into this list.

| Date | What shipped |
|------|--------------|
| 2026-06-11 | **Sprint 62 — Data Quality built & QA-passed (B-166/167/169, DIR-97/98/100 Done; deploy pending Rupert).** B-166: widened resulting-holding extraction (anchor: purchase/acquisition/trades/transfers/SIPP/dealing; predicates F1–F5; anchorless Families 3+4; window-clip fix; corporate-subject guard) — verified on 20+ real filings; recall measurement 197/904 NULL filings now yield (was 10) → projected 13.1% BUY population pre-B-167. **B-167 root cause: `reparse_corpus.py` derived url only from existing DB rows** — the June recovery reparses mass-inserted 4,501 rows with blank url/announced_at/buy_strictness. Fixed at source (og:url+dateCreated fallback, _apply_insert now writes buy_strictness/role_normalized, _apply_update NULLIF heals) + new `backfill_urls.py` (fingerprint replay → og:url; 3,647/4,501 restorable = 721/859 BUYs; 854 parser-drift rows formally unreachable). ⚠️ ORDERING: backfill_urls BEFORE any reparse_corpus run; backfill_resulting_shares AFTER backfill_urls. B-169: mktcap slug-variant retry (404 fast-fail; dot-suffix) + 7 CSV seeds (AGR/JUST/LIFS/PHLL/SOLG/SXS/ULTP — 6 are delistings, not slug bugs). QA: PASS-WITH-FIXES — caught+fixed corporate head-noun precision bug (BOG Group Employee Trust attach risk); 67+16+50+159+41+17 tests green. Deploy block in shipped-log / Claude chat. |
| 2026-06-10 | **Sprint 61 — B-156 + B-164 built & QA-passed (DIR-87/95 Done); B-163 spike data collected (DIR-94 in progress).** B-156: migration 013 `transactions.resulting_shares`; `parse_pdmr.py` narrative+table capture (all 4 emission paths, fingerprint untouched, 6 real filings verified); `backfill_resulting_shares.py` (preview/`--confirm`, fingerprint-matched UPDATE in place, JSONL audit); backtest HEADER +`resulting_shares`,`holding_pct_increase`. Corpus reality: only ~13% of purchase filings state the figure (MAR template lacks the field) — acceptance bar ≥10% of BUYs. B-164: migration 014 `short_positions`+`isin_ticker_map`; `backfill_short_interest.py` (FCA daily XLSX, historic sheet 106,725 rows ingestable, idempotent upsert, name-match→OpenFIGI→override mapping, coverage log); backtest HEADER +`short_pct_at_announcement` with **strictly-prior (`inclusive=False`) semantics** after QA caught a 1-day lookahead. ⚠️ FCA regime change 13-Jul-2026 kills holder-level feed — run historic ingest before then. Prospective BUY-ticker coverage 32.5% (144/443). B-163 spike: 15/20 raw (94% for established directors); failure mode = recently-appointed directors; memo awaits B-156 overlap test post-deploy. QA: 155 tests 0F/0E sandbox + 3 fixes incl. pre-existing `test_p3_lookahead` t0 fixture (legacy signal id) — now 3/3. Backtest reads `signals` table (not vice versa); deploy order backfill→backtest→eval→export→build confirmed. **Rupert: Windows `unittest discover` = final gate, then deploy blocks in `docs/specs/sprint-61-plan.md` §4 (incl. one-time `pip install openpyxl`).** |
| 2026-06-10 | **B-157 + B-158 + B-165 — Dynamic costs, sector backfill, sector normalisation (DIR-88/89/96).** B-157: `backtest.py` Corwin-Schultz spread model (`_ticker_ohlc` OHLCV cache + `_cs_spread_bps()`); 762 tickers backfilled (203k H/L rows); 99% of signals now use dynamic costs (5–450 bps vs flat 50+50). B-158: `backfill_sectors.py` FMP API backfill; 493/675 tickers (73%) now have a sector (was 24%); AIM guard preserves `^FTSC` benchmark for AIM tickers. B-165: `SECTOR_NORMALISE` dict + `normalise_existing()` + `--normalise` CLI mode added to `backfill_sectors.py`; collapses 5 duplicate By-Sector buckets ("Financial Services"→"Financials", "Consumer Cyclical"→"Consumer Discretionary", "Consumer Defensive"→"Consumer Staples", "Basic Materials"→"Materials", "Healthcare"→"Health Care"); 22 tests green. Deploy: `backfill_sectors --normalise` → `snapshot_db` → `export_dashboard_json` → `build_dashboard`. |
| 2026-06-09 | **B-146/B-147/B-148 — Earnings chart markers, Earnings History box, corrupt-row cleanup (DIR-79/80/81, Zone A).** B-146: vertical dashed lines + diamond scatter dataset + hover tooltips on company price charts at each earnings date; confirmed dates amber, estimated dates grey + "(est)" badge suffix; `render_company.py` + `build_dashboard.py`. B-147: `_filings_section()` Earnings History section on every company page — amber/grey type badges, direct Investegate filing links via new `source_url` column (migration 012, schema v12→was v11); `backfill_reporting_dates.py` now extracts and stores absolute filing URLs; `build_dashboard.py` serves `all_reporting_dates` with `source_url`. B-148 (P1 bug fix): deleted 594 corrupted `reporting_dates` rows (every ticker wrote `2026-06-04 INTERIM` during the old bad-URL scraper run); `fix_corrupted_reporting_dates.py` one-shot cleanup; 6 legitimate June 4th rows preserved; re-backfill with `--no-cache`. All three marked Done in Linear Sprint 32, Cycle 1. |
| 2026-06-07 | **B-143 dedup + B-144 strategy tracker toggles (DIR-70/71, Zone A).** B-143: `.scripts/dedup_transactions.py` — exact fingerprint dedup + near-dedup (same director/value ±5%, date ±7 days); DB 6,454→6,338 (116 removed), Rupert ran + confirmed. B-144: strategy tracker extended with T+1/T+21/T+90/T+252 horizon pill toggles + 11 per-signal chip toggles (T1A/T1B/T2/T3/T4/T5/T6/T7/S1/F1/B1); multi-horizon compute in `export_dashboard_json.py`; interactive Chart.js panel in `render_performance.py`. Both **DEPLOYED** 2026-06-07. Both marked Done in Linear Sprint 53. |
| 2026-06-06 (deployed) | **Backlog batch DEPLOYED + verified.** Reparse (B-090C SIP + B-121 Mode-3) landed: transactions **6,089→6,454** (+365 net: +203 SIP, +56 BUY, +66 GRANT, +24 EXERCISE, +16 SELL — reparse reported 3,808 insert-attempts, INSERT OR IGNORE deduped to +365). Signals **2,963→3,057** (+94, from the recovered individual buys). B-136 corporate exclusion live via `eval_signals --rebuild` (arms-length corps fire 0 signals; PCAs/family trusts kept). B-118 (reporting_dates 724), B-120 + B-132 (export/build), B-130 (**2,132** review prefills: 1,191 fully-recovered + 941 best-guess) all live. **DIR-18/44/55/56/41/43/51/63 → Done.** Pre-reparse safety gate correctly refused (62.5% of rows touched) → `--force-safety-override` after spot-checking recovery quality. **B-128 sectors (DIR-54)** — FMP backfill deployed; **212/511** sectors written today (fixed a cache-write-on-429 bug where the run stopped on the first rate-limit and wrote 0); remaining ~280 fill over the next 1–2 daily runs as the free-tier quota resets. **Full batch now Done — 8/8.** |
| 2026-06-06 | **Backlog-clear pass + decisions.** Cleared **B-123** (verified already-built — strategy tracker live), **B-118** (`backfill_expected_reporting_dates.py` synthetic "(est)" forward earnings, 11 tests green), **B-120** (⚠ "unverified" price chip on This-Week + company tables), **B-132** (brewing-cluster trend: count vs 30-day avg + 8-week sparkline, walk-forward exporter). **B-090C** SIP multi-tranche parser BUILT + independently QA-passed (aggregated-row reader in `_extract_via_sections`; GPE 8857578 → Courtauld 147/£150.75, Sanderson 144/£147.66, Nicholson 147/£150.75). **Decisions locked (Rupert):** scoring = individuals' straightforward buys/sells only → exclude SIP/awards + corporate holders from CAR/performance/signals (new **B-136/DIR-63**); B-090C deploy gated on that + Windows fixture run; **B-121** rescoped (exclude corporates, recover individual false-bundled); **B-128** rescoped to an alt ticker-keyed sector source (Yahoo blocked); **B-130** best-guess passover approved; **Gated B-078ph/B-080/B-081/B-082 (DIR-36/37/38/39) ERASED** (cancelled). FUSE blocked the sandbox sweep → Rupert runs `unittest discover` + deploy (display items now; hold `reparse_corpus` until B-136). |
| 2026-06-05 | **Sprint 35 closed (9/10 issues; 1 deferred).** Tech-debt sweep: B-013, B-016/17, B-021, B-022, B-023, B-112, B-113 all VERIFIED ALREADY-SHIPPED (closed without rebuild, B-001 pattern — file:line evidence + passing tests in the 1185-green sweep). **B-020 triaged-closed:** `_diag_orphan_retriage.py` re-ran today's parser over the 126 residual orphans → 107 are REAL filings the parser can't read (PCA/corporate-holder + SIP/EBT layouts), 16 mismatch, 3 now-reproduced; **none are garbage — do NOT `--delete-orphans` them.** Conclusion: keep all; the real work is parser coverage → logged **B-121** (P2). **B-090 Layout C** (the one genuine remaining build) + **B-121** moved to new milestone **Sprint 38 — Parser Coverage (PCA/SIP/EBT)**, Cycle 7. Follow-ups also logged from B-060: **B-119** (price coverage for `no_market`), **B-120** (unverified-display marker). |
| 2026-06-05 | **Sprint 35 B-060 "Pence/pounds value fix."** Root cause: `parse_pdmr.py` stored a bare numeric price (no £/p marker) as pounds, but UK RNS tables quote pence → `value = price×shares` inflated 100× (e.g. IGP £103k → £10.26m). Fix = market-close reconciliation: new `price_reconcile.py` compares the pounds vs pence reading against `prices.close` (already pounds) within ±35%; `backfill_price_units.py` (Zone B) corrects clean pence rows in place and flags garbage/unverifiable rows via new `transactions.price_audit` (migration 010, schema head 9→10). Flagged rows excluded from signals (`eval_signals` candidate filter) and from £ metrics (read-time CASE-null in `export_dashboard_json` cluster + monthly queries). Run on live corpus: 27 corrected_pence, 316 unresolved, 293 no_market flagged; signals re-graded (f1 1377→1273, s1 995→941). Full sweep **1185 green**. Tests: `test_b060_pence_reconcile` (16), `test_b060_backfill` (5). **Follow-ups logged: B-119** (backfill prices for `no_market` tickers then re-reconcile to *correct* not just flag) and **B-120** (display "unverified" marker for flagged rows). |
| 2026-06-04 | **Sprint 30 "CSV seed + B-002 sweep + hotfixes."** CSV seed: `_company_market_caps.csv` extended 46→78 rows; `_company_websites.csv` extended 48→80 rows; all top-50 active buy tickers now covered. B-002 LLM sweep (`run_pending_sweep --target stuck-on-date --budget-usd 3.00`): 35 target entries processed, 2 recovered (RVRB, MICC), 33 confirmed `bundled_multi_PDMR` (unrecoverable by LLM — need B-090 Layout C parser fix). B-078 Phase B deferred (Option A — wait for t1b/b1/t6 ≥30 T+21 firings, ~Q3 2026). Hotfixes: (1) `eval_signals.py::_open_paper_trade` — `sqlite3.Row` has no `.get()`; fixed with `dict(tx)` conversion at entry; (2) `run_pending_sweep.py::_run_pipeline` was missing `export_dashboard_json.py` between eval_signals and build_dashboard; (3) `test_sprint29.py` 3× `INSERT INTO tickers_meta` fixtures missing `updated_at` NOT NULL column (added in Sprint 26 schema v8). 207 bash tests green. |
| 2026-06-04 | **Sprint 27 "Bundled filing recovery."** B-090: `_extract_via_sections` in `parse_pdmr.py` now recovers ~84% of ~2,250 refused bundled filings via Fix-A (look-ahead within section rows for inline price/volume, Polar Capital pattern) and Fix-B (adjacent sibling price table capture, Layout-B pattern). `test_b090_bundled_layouts.py` 24 tests — all pass. `test_b023_bundled_sections.py` regression clean. B-106: `\d` → `\\d` in `export_dashboard_json.py` docstring (SyntaxWarning on 3.12+). B-043: verified already clean (no Unicode chars in print()). B-084: exhaustive hidden+flex audit — CLEAN, no fixes needed. Hardcoded session path in `test_pdmr_editor_phase1.py` fixed to use `REPO / "outputs" / "review.html"`. **Zone-B deploy:** Rupert runs `reparse_corpus.py` then `refresh_all.py` to harvest recovered rows. |
| 2026-06-04 | **Sprint 25 Phases 3 & 4 "PDMR editor" — fully shipped, 77 tests green (Phases 2+3+4 combined).** Phase 3: fixed dead-code duplicate `elif action == "add"` in `apply_edits.py` run() — `resolved_rns_ids` now correctly populated so `remove_from_pending_queue` fires after manual adds; wired `run_pending_sweep.py` sticky protection (`_load_already_resolved_ids` reads `_rejected_rns_ids.json` + `_manual_rns_ids.json`, `_select_candidates` filters both `all` and `stuck-on-date` targets); `test_pdmr_editor_phase3.py` 21 tests. Phase 4: `apply_delete` action in `apply_edits.py` (removes `parser_source='manual'` rows + signals cascade; safeguard blocks non-manual rows); `GET /api/audit-log` endpoint in `server.py` (reads `_edit_audit.jsonl`, last 50 entries most-recent-first); Audit & Undo collapsible panel in `review.html` (last 20 applied edits, Undo button: update→inverse update, add→delete, delete→re-add, reject→info); phase badge updated to "Phase 4 — Complete"; `test_pdmr_editor_phase4.py` 22 tests. |
| 2026-06-04 | **`export_dashboard_json.py` Phase 3/4 gap fixes.** `build_pending_review_export`: (1) now exports `extracted` array (first 3 rows) alongside `extracted_count` — Tab A pre-fill form was always blank before this; (2) new `_load_resolved_rns_ids()` helper filters out `_rejected_rns_ids.json` + `_manual_rns_ids.json` items before building the export (belt-and-suspenders on top of `remove_from_pending_queue`). Deployed: 4,577 pending items, 5,582 tx. |
| 2026-06-04 | **Review link added to all dashboard nav bars.** `render_index.py`, `render_performance.py`, `render_company.py`, `render_performance_drilldown.py` (both call-sites), and the `templates.py` default all now include `("Review", "/review")`. Absolute `/review` path works at any page depth. Rebuild required. |
| 2026-06-03 | **Sprint 24 "Performance lens polish" — all items shipped, 277 tests green. Rupert runs `export_dashboard_json.py` → `build_dashboard.py` to deploy.** B-083: dead `render_helpers.sparkline_svg` removed (test cleaned up). B-072: T+1/T+21/T+90/T+252 horizon toggle added to Level-2 cohort chart focus overlay — toggle buttons, dynamic hit-rate header, `_LEVEL2_MONTH_KEYS` extended (data was already in the JSON). B-076: localStorage persistence for signal + horizon selection; auto-restores focus mode on reload. B-099: B1 (`b1_lone_conviction_buy`) added to `COHORT_SIGNAL_GROUPS` — sparkline will populate after re-export. B2 excluded from cohort chart (no CAR series); scoreboard shows suppression count annotation instead. B-075: "↓ Download CSV" button on all drill-down pages, client-side only. B-085: 277-test green run. |
| 2026-06-03 | **Dashboard debugging (Rupert-reported issues) — code complete, pending rebuild.** **#1 MTM column "missing":** not a data/build bug — the 8-col This Week table overflowed the 2/3-width panel and `overflow-hidden` clipped the rightmost MTM column; changed to `overflow-x-auto` (render_index.py). Confirmed via zoom-out. **#2 (B-095) TIN +1451%:** root cause = unadjusted ~15:1 consolidation in Cornish Metals' price series (entry £0.078 vs T+21 £1.215). Added a split/consolidation guard to `backtest.py` — nulls any CAR whose window/entry ratio is >4× or <¼×, logs each to `_split_guard_flagged.csv` (diff-first). **#3:** false alarm — TIN's company page exists; was really #4. **#4:** cohort drill-down modal rendered tickers as plain text; now linked to `companies/{ticker}.html` (render_performance.py). Rebuild (`backtest → export_dashboard_json → build_dashboard`) applies all four. |
| 2026-06-03 | **Sprint 22 Phase 1 "Recover the lost filings" (B-091 + B-094).** Historical backfill (B-091): `backfill_filings.py` (archive walker, now sharing the Phase-4 discovery fix) re-scraped launch→today and recovered the operating-company filings the old keyword filter silently dropped. B-094 (new bug, found + fixed during validation): the backfill lacked the IT/CEF ingest filter, so the first validation run re-imported 68 investment-trust rows; ported the CSV-aware `_load_excluded_tickers` + per-row drop + `_excluded_at_ingest.log`; re-validation showed `excluded_at_ingest=68`, and the 68 already-inserted ITs were purged (`classify_issuers`→`exclude_investment_trusts --confirm`, 0 signals affected). **Deferred to a dedicated Recovery sprint:** B-092 (non-RNS provider layouts — prn/gnw/bzw/eqs, ~200 filings, fragmented-HTML layout, mostly non-buy grants → low signal value) and B-090 (bundled multi-PDMR, ~2,349). |
| 2026-06-03 | **Sprint 21 "Process insurance" (verify-and-close).** Audit confirmed B-024 (self-healing backup) and B-015 (sweep announced_at safety) were already fully implemented — verified full `db_health` backup/seal coverage across every write-path script + `start.bat` stale defences, and the `announced_at`-from-HTML recovery + ±60-day guard in `run_pending_sweep.py`; both marked RESOLVED. B-084 (hidden+flex trap) confirmed clean. B-012 (stale tests) **RESOLVED**: Windows-side `unittest discover` = 732 tests, the old "stale" suites all pass; the only 2 failures were regressions from the B-093 regex widening (added "acquisition" which mis-labelled vestings; comma excluded from the number class). Fixed: `_STRICT_BUY_RE` now matches only "purchase/purchased" and tolerates commas; speculative acquisition test removed; 14/14 in-memory re-validation. No signal-firing impact (MIXED/NON_BUY both non-firing); a backfill re-run mops up residual labels. B-085 (browser smoke-check) deferred to Sprint 24. One consistency line added (`seal()` in the Sprint 20 delete script). |
| 2026-06-03 | **Sprint 20 "Trust the numbers" (data correctness).** B-093: widened strict-buy rule (`_STRICT_BUY_RE`) recognises bare "Purchase"/"PURCHASE"/"Purchase of N PLC shares"; `backfill_buy_strictness.py --include-unknown` reclassified 304 wrongly-suppressed buys (incl. UTL/ULVR/LGEN/ABF/IMB NED buys). Data-integrity audit (`docs/audits/audit_2026-06-03_*`) source-verified the year-as-shares + price errors. Confirmed the year-guard (R6) + value-as-price guard (R3) already existed — fix was application, not new code. Added `reparse_corpus.py --only-rns` for surgical scoping; scoped reparse fixed 14 year-as-shares + WOSG (9 corrected, 14 removed, 4697→4691). New `fix_sprint20_delete_nourl_yearshares.py` deletes the 15 no-source `shares==own-year` rows (16 genuine ~2,000-share holdings deliberately kept). **Deferred to a later slice:** ~13 pence/pounds + ~21 multi-leg "1.0" price errors (need parser fixes #1/#3). |
| 2026-06-02 | **Ingest-gate suppression incident — all four problems.** A: over-strict gate split into blocking vs advisory. B: `_pending_review.json` drain 4,352→4,281 (`drain_pending.py`). C: buy/sell type-flip scoped to the right cell + 5-buy reparse (JMAT/GEN/UTL/CAD/PSN → BUY). D: scrape discovery gap — keyword allow-list replaced with trust-category+denylist, date window engaged, link regex widened to all PIPs, real pagination. |
| 2026-05-29 | **Sprint 14 — cohort / CAR-chart redesign (Phases 0–6).** Mean-vs-median divergence warning, trajectory sparkline + 3m-trend + focus mode, per-month `cohort_performance.json` export, Level-2 scatter chart with whiskers/N-strip/MA overlay, rolling-6m hit-rate panel, drill-down modal. |
| ~2026-05-28 | **Company search box (spec 09 / B-059).** Header typeahead in `outputs/index.html` (`companySearch`) — jump to any company by ticker or name. *(Date approximate — confirm if needed.)* |
| 2026-05-28 | **Sprint 11 — parser hardening.** 5 parser fixes, full corpus reparse, 187 dirty rows removed, 343 tests green. Resolved **B-061** (year-as-shares, partial — see regression note), **B-062** (price-extreme), **B-063** (director-name capitalisation). |
| ~2026-05-25 | **Behavioural signals + universe cleanup (spec 08).** B1 lone-conviction-buy, B2 anti-crowded-cluster kill filter, strict-buy column (migration 005), and IT/CEF/VCT/REIT exclusion all live and wired into `eval_signals.py`. *(Dates span Sprint 11–13.)* |
| 2026-05-25 | **Sprint 10 — B-011 finish-up + pipeline resilience.** 329 tests green. |
| 2026-05-20 | **B-025 — role normalization.** 14 canonical role buckets + 8 signal tiers (Phase A additive + Phase B gated cutover). |
| 2026-05-18 | **Sprint 2 — IT/CEF purge** (130 tickers / 452 tx / 789 signals removed). **Sprint 3 — table-aware parser** (B-001 + B-004 + B-016 + B-017): BeautifulSoup rewrite, multi-row inserts, boilerplate + director-name-in-company defences. |
| 2026-05-15 | **Date-integrity audit + dashboard health panel** (5-invariant check, green/red banner, hard-error gate). **Parser fix** for cross-cell dot-separated dates. **`repair_dates.py` FK-ordering fix.** **I5 threshold relaxed** 365→1,460 days for legitimate late filings. |

---

## P2 — Sprint 26 data-source blockers (schema + display shipped; data not yet populated)

### B-096b — Reporting dates: replace Yahoo calendarEvents with Investegate scraper
**Discovered:** 2026-06-04 during Sprint 26 deployment.
**Status:** Schema + 60-day badge display code fully shipped (migration 008, `reporting_dates` table, `render_company.py` badge). `backfill_reporting_dates.py` exists but returns HTTP 401 on every ticker — Yahoo's `v10/finance/quoteSummary?modules=calendarEvents` endpoint is now blocked for anonymous access, same as all other v10 modules.
**Impact:** The `reporting_dates` table is empty. No pre-results badges appear on company pages. No data loss — purely a missing enrichment.
**Recommended fix:** Scrape reporting dates from Investegate itself. Investegate RNS announcements include "Preliminary Results", "Half Year Results", and "Trading Statement" announcement types. Filter the existing `_scrape_cache/` or run a targeted Investegate search per ticker for those headline types. This matches the project's existing scraper pattern exactly and costs nothing extra.
**Alternative:** A free financial calendar API (e.g. FMP free tier has UK earnings dates). Spike first to check coverage for UK small-caps before committing.
**Effort:** M. Assign to Sprint 28 data-enrichment pass (moved from Sprint 27, 2026-06-04).

### B-097b — Market cap: Yahoo v8 chart meta returns None for all UK stocks
**Discovered:** 2026-06-04 during Sprint 26 deployment.
**Status:** `market_cap_gbp` and `shares_outstanding` columns exist in `tickers_meta` (migration 006). `backfill_ticker_meta.py` runs successfully but Yahoo's `v8/finance/chart` meta block returns `marketCap: None` for every UK `.L` stock. The `tickers_meta.market_cap_gbp` column is empty across all 784 tickers.
**Impact:** No market cap chip on company pages. No "buy as % of float" conviction normalisation (quant research item). No FTSE 100/250 benchmark assignment (which was planned to use market cap as a proxy). No data loss — purely a missing enrichment.
**Recommended fix:** Use `v7/finance/quote` endpoint which sometimes returns `marketCap` for UK stocks without auth. Spike with 5 tickers first. If blocked, consider scraping from LSE company pages (each ticker page shows market cap) — the same Chrome-based scraper used for AIM constituent discovery could fetch it.
**Effort:** S (spike) + M (implementation if source found). Assign to Sprint 28 (moved from Sprint 27, 2026-06-04).

### B-101b — Website URL: no unauthenticated Yahoo source available
**Discovered:** 2026-06-04 during Sprint 26 deployment.
**Status:** `website_url` column exists in `tickers_meta` (migration 007). `render_company.py` displays it as a link and falls back to a Google IR search link when NULL. Yahoo's `assetProfile.website` field (the intended source) is in `v10/quoteSummary` which requires auth.
**Impact:** All company pages show the Google IR fallback link rather than the direct company website. Functional but suboptimal.
**Recommended fix:** Maintain a manually-curated `_company_websites.csv` file (ticker → URL) seeded from the LSE constituent page or a one-time scrape. Low volume, rarely changes. Alternatively check if the LSE company page HTML contains a structured website link.
**Effort:** XS once a data source is agreed. Assign to Sprint 28 (moved from Sprint 27, 2026-06-04).

---

## P1 — data correctness

### B-094 — backfill_filings.py missing the IT/CEF ingest filter — RESOLVED 2026-06-03
**RESOLVED:** re-validation showed `excluded_at_ingest=68` (filter working); the 68 already-inserted IT rows were purged via `classify_issuers` → `exclude_investment_trusts --confirm` (0 signals affected, all 39 tickers confirmed genuine ITs/VCTs/CEFs).
**Discovered:** 2026-06-03 (Sprint 22 Phase 1 validation window). `backfill_filings.py` (the historic archive re-scrape, drives `iter_archive`) had **no exclusion filter**, unlike `run_scrape.py` and `reparse_corpus.py`. Step-1 validation (seen=1326, written=340) re-imported investment trusts Sprint 2 had purged (FGT, EDIN, NCYF, BVT, SSIT, UEM, MIGO, NAIT, BNKR, SST, WWH…). **Fix:** ported the CSV-aware `_load_excluded_tickers` (is_excluded_issuer + `_excluded_it_cef.csv`) + per-row drop + `_excluded_at_ingest.log` + summary counter. **Pending:** Rupert purges the already-inserted ITs (`classify_issuers` → `exclude_investment_trusts --confirm`) and re-runs Step 1 to confirm `excluded_at_ingest > 0`. See `sprint-22-plan.md`.



### B-001 — Multi-row bulk filings drop transactions 2 through N
**Discovered:** 2026-05-15
**Severity:** Real data loss; under-counts your true insider activity.
**Evidence:** Investegate filing 9541612 (National Grid, Jacqueline Agg)
contains 4 separate transactions in a single HTML table — dated
2024-08-13, 2025-02-12, 2025-08-13 and 2026-01-14 — but only the first
row was extracted into the DB. The AI summary in the filing itself
spells out all 4 transactions. See cached HTML around line 437 for the
multi-row table structure.

**Likely affects:** Investment Trust dividend reinvestment plans (DRIPs),
SIP / SAYE bulk filings, year-end compliance review disclosures. These
are the filings where companies disclose many small accumulated
purchases on a single day.

**Suggested fix:** Update `parse_pdmr.py` to detect a multi-row
transaction table after the "Date of transaction / Name / Position /
Price / Volume" header row, and emit one extracted dict per data row
rather than just the first.

**Effort:** ~2 hours. Needs a focused session with the cached HTML
files (9541612 is a good fixture; there are probably 10–20 more).

**Cost:** £0 to fix; restoring missed rows would re-parse cached HTML
so no LLM needed.

---

### B-002 — LLM sweep over stuck-on-date pending filings — RESOLVED 2026-06-04
**Discovered:** 2026-05-15. **Resolved:** 2026-06-04.
**Original scope:** 7 filings with JSE/SIP layout date-parse failures.
**Outcome:** Population had grown to 35 by the time the sweep ran. `run_pending_sweep --target stuck-on-date --budget-usd 3.00` processed all 35:
- **2 recovered** (RVRB, MICC) — written to `transactions` with `parser_source='llm'`.
- **33 confirmed `bundled_multi_PDMR`** — no named PDMR transaction extractable. These are multi-person table filings, not single-PDMR date-parse failures. LLM correctly rejected them.
- **1 date-out-of-window reject** (9557439, 9568964 × 2) — extracted date >60d from `announced_at`.

**Remaining work:** The 33 `bundled_multi_PDMR` entries need **B-090 Layout C** (multi-tranche SIP parser), not further LLM sweeps. Assign to Sprint 31+.

**Hotfix also shipped:** `run_pending_sweep.py::_run_pipeline` was missing `export_dashboard_json.py` between eval_signals and build_dashboard — fixed in this sprint.

---

### B-011 — Exclude investment trusts and closed-end funds from the dataset
**Discovered:** 2026-05-18
**Severity:** Real data correctness. Investment trusts (ITs), VCTs, REITs and
closed-end funds (CEFs) have very different insider-dealing dynamics from
operating companies — board members often buy at NAV-discount as a routine
governance signal, not because they have informational edge. Including them
in clusters and signal scoring pollutes the buy-side signals (especially
S1 cluster_buy and T2 exec_buy).

**Decision (Rupert, 2026-05-18):** Hard delete from the database — remove
all IT / CEF / VCT / REIT rows from `transactions`, do not include them
in any future scrapes, exclude them from all tables, performance tracker,
and the Brewing / Active Clusters panel.

**Suggested approach:**
1. Build a classifier for IT / CEF / VCT / REIT issuers. Options, cheapest first:
   - Name-pattern match: "Investment Trust", "VCT", "REIT", "Real Estate
     Investment Trust", " Trust plc", "Capital Trust". Fast and free, but
     will miss some (e.g. "Scottish Mortgage") and may false-positive on
     names like "Trustpilot".
   - AIC (Association of Investment Companies) member list — the
     authoritative source for UK ITs. ~400 members, easy CSV scrape.
   - LSE security-type lookup via Yahoo (`quoteType` field) — flags ETF /
     CEF / mutualfund cleanly.
   - Recommendation: combine AIC list (primary) + Yahoo quoteType (sweep)
     + name regex (catch-all). Materialise as a `is_excluded_issuer`
     column in `tickers_meta` so the classification is auditable.
2. One-time deletion script in `.scripts/exclude_investment_trusts.py`:
   - Print preview list before deleting (Rupert approves first)
   - Delete from `signals`, `paper_trades`, `transactions` in correct
     FK order (per B-006 lessons)
   - Log all deleted fingerprints to `.data/_excluded_it_cef.csv` for
     audit / reversal if needed
3. Add the exclusion check into the scrape pipeline so future filings
   from these issuers are dropped at ingest, not after.
4. Update `index.html` clusters / table queries to also filter (defensive
   double-layer — even if a row sneaks in, it won't render).

**Side effect — backtest invalidation:** Existing
`.data/_backtest_results.csv` was computed including IT / CEF rows. After
deletion, the backtest must be re-run (`backtest.py`) so the performance
tracker reflects only operating-company signals. Rupert: budget an extra
~10 minutes for this step.

**Effort:** ~3 hours total (classifier + delete script + scrape filter +
dashboard filter + backtest re-run). Possible to split into two
sessions: classify first (read-only), delete in a second pass after
Rupert has reviewed the preview list.

**Cost:** £0 if using AIC list + Yahoo metadata. Up to £0.20 in LLM
classification cost if we need to disambiguate edge cases.

---

### B-060 — Transaction value column may mix pence and pounds across pages
**RESOLVED 2026-06-06** (DIR-35 Done). Migration **010** (`010_price_audit.sql`,
schema head → "10") added a `price_audit` column; `price_reconcile.py` +
`backfill_price_units.py` reconcile each priced txn vs the nearest market close.
Applied: ok_pounds 4464, unresolved 316 (value nulled), no_market 291 (value
nulled), **corrected_pence 1**. Tests `test_b060_backfill.py` +
`test_b060_pence_reconcile.py`; full sweep green (1210). The actual contamination
was a single row — P1 risk was largely theoretical, now provably flagged.
**Discovered:** 2026-05-22 (Rupert observation)
**Severity:** Real data correctness. If any prices are stored in pence (GBX) but treated as pounds (GBP) during value calculation — or vice versa — the displayed transaction value will be wrong by a factor of 100. Affects every page that shows a monetary value column (Today, Performance, Cohort cuts, Company pages).

**Background:** UK equities on the LSE are quoted in pence (GBX) by convention, but RNS filings may report the price in either pence or pounds depending on the issuer and filing template. AIM stocks in particular are inconsistent. The parser currently extracts the raw price string from the HTML; it is not clear whether a normalisation step converts GBX → GBP before storing, or whether the `price` column in `transactions` is a mixture.

**Suggested audit approach:**
1. Inspect the `price` column distribution in `transactions` — look for prices < 1.0 (likely already in pounds) vs prices > 100 (likely in pence). A bimodal distribution around these ranges would confirm mixing.
2. Cross-check 5–10 known transactions against their Investegate source filings: verify the raw price in the HTML vs what's stored in the DB vs what's displayed on the dashboard.
3. Check `parse_pdmr.py` for any pence-to-pounds conversion — is there one? Is it applied consistently across all layout variants (Layout A, Layout B, section-aware bundled)?
4. Check `export_dashboard_json.py` — does the value column compute `price × shares`? If price is sometimes in pence, the value will be 100× too high.
5. Check `backtest.py` — does the cost-basis calculation use the same price field? A pence/pounds mix here would corrupt all CAR calculations.

**Scope of fix (once audit confirms the issue):**
- Add a normalisation step in `parse_pdmr.py` (or `db.upsert_transaction`) that converts pence to pounds when the currency indicator is GBX or when the price is implausibly large for a per-share figure.
- Add an invariant to `audit_dates.py` (or a new `audit_prices.py`) that flags any transaction where `price × shares` produces a value that looks implausibly large or small.
- Re-run `reparse_corpus.py --confirm` to correct any rows already in the DB with wrong price units.

**Pages affected if confirmed:** Today dealings table, Performance cohort value tiles, Cohort cuts by-value-bucket panel, all 563 Company pages.

**Effort:** ~1 hour audit + ~1–2 hours fix depending on how widespread the mixing is.

**Cost:** £0.

---

### B-061 — Year-as-shares contamination (legacy parser Trigger 2 gap)
**Discovered:** 2026-05-27 (data-integrity-auditor)
**Severity:** Real data correctness. 38 rows had `shares` = the calendar year (e.g. 2025, 2026) because the legacy regex path's `_looks_like_date_bleed` Trigger 2 failed to reject the year value when other date-bleed integers (day-of-month, par value) were also present in the block.
**Fixed:** 2026-05-28 — Sprint 11 Fix #1 tightened Trigger 2 to exclude day-of-month integers from the "other_ints" set. Full corpus reparse with `--delete-orphans` removed all contaminated rows. Post-cleanup count: 0.
**RESOLVED 2026-05-28**

---

### B-062 — Price-extreme: total transaction value stored as per-share price
**Discovered:** 2026-05-27 (data-integrity-auditor)
**Severity:** Real data correctness. 38+ rows had `price > £200` for non-allowlist tickers because the parser captured the total consideration (e.g. £49,592 for a 2-share UU trade) as the per-share price. Wildly inflated prices corrupted any value-based signal thresholds.
**Fixed:** 2026-05-28 — Sprint 11 added `HIGH_PRICED_TRUST_ALLOWLIST` (LTI, NXT, AZN, GAW) and the D.3 gate. Cleanup script `phase11_cleanup.py` removed all 38 non-allowlist price>£200 rows. Post-cleanup count: 0.
**RESOLVED 2026-05-28**

---

### B-063 — Director name capitalisation not normalised (duplicate identities in clusters)
**Discovered:** 2026-05-27 (data-integrity-auditor)
**Severity:** Signal correctness. 9 director identities were duplicated due to inconsistent capitalisation (e.g. "JOHN SMITH" vs "John Smith"), causing cluster membership under-counting and inflated distinct_directors counts.
**Fixed:** 2026-05-28 — Sprint 11 Fix #5 added `_normalise_director_name()` helper (Title Case, UK particle exceptions via `_director_name_exceptions.json`). Applied during full corpus reparse; 107 director fixes logged to `_reparse_director_fixes.log`.
**RESOLVED 2026-05-28**

---

## P2 — robustness and developer experience

### B-003 — No unit tests on the parser or data pipeline
**Discovered:** 2026-05-15 (during date-integrity work)
**Severity:** Every parser change is "fingers crossed" — no automated
safety net. Latent bugs (like the FK-delete-order issue in
`repair_dates.py`) sit undetected until they fire in production.

**Evidence:** Today's session uncovered four distinct bugs that
trivial unit tests would have caught instantly:
- Parser regex couldn't read dot-separated dates ("05.05.26")
- Parser `_TX_DATE_LABEL_RE` couldn't reach across `<td>` cells
- Silent `max(candidates)` fallback picked up page chrome timestamps
- `repair_dates.py` deleted from `transactions` before `signals`,
  violating the foreign key constraint

**Suggested fix:** Implement Layer 2 of the date-integrity strategy
document (`docs/specs/date-integrity-test-strategy.md`):
- `.scripts/test_dates.py` — stdlib unittest, ~30–50 boundary cases
- `.scripts/fixtures/dates/` — ~10 small HTML fixtures, one per known
  bug pattern, each with an `.expected.json` for what should be
  extracted

**Effort:** ~2 hours.

---

### B-004 — Director-name extraction garbles foreign / SIP layouts
**Discovered:** 2026-05-15
**Severity:** Cosmetic on the dashboard, but indicative of the same
template-variant gap as B-001 / B-002.

**Evidence:** Before today's fix, some rows had `director` values like:
- "Kingfisher plc\nb"
- "National Grid plc\nLEI"

The name regex was matching across cell boundaries and capturing
"<company name>\n<next field label>" instead of the actual director.
Most of these rows are now in pending review (B-002), so this isn't
currently visible on the dashboard — but the underlying regex weakness
remains.

**Suggested fix:** Bundle with B-001 (multi-row table support); both
need a smarter table-aware parser.

---

### B-005 — `_DATE_FMTS` accepts ambiguous `%d.%m.%y` 2-digit years
**Discovered:** 2026-05-15 (introduced today as part of the fix)
**Severity:** Low. Python's `strptime` pivots at year 68/69 — so "26"
becomes 2026 (good) but "69" would become 1969. Unlikely to fire on
modern RNS filings but worth knowing.

**Suggested fix:** Add a sanity check after `_try_one_date` returns:
if the parsed year is `< 1990`, log a warning and return None.
Cheap defence in depth.

**Effort:** ~10 minutes.

---

### B-006 — `repair_dates.py` doesn't refresh `pending` reverse-deletes
**Discovered:** 2026-05-15
**Severity:** Low / future hazard.

**Evidence:** When `repair_dates.py` moves a row to pending (Case B),
it deletes from `signals`, `paper_trades`, and `transactions`. But
the `_pending_review.json` write only happens inside the same
function; if the script crashes mid-loop after a delete but before the
JSON write, the DB has lost a row that isn't yet in pending.

**Suggested fix:** Write to a temp pending file after each Case B
deletion, not in a single batch at the end. Or use a transaction
spanning both writes.

**Effort:** ~30 minutes.

---

### B-009 — Cumulative net CAR chart says "trailing 12 months" but shows ~2
**Discovered:** 2026-05-15 (during dashboard review)
**Severity:** Misleading visualization. Underlying CAR data is fine; the
chart is the problem. Looks like a date issue but isn't.

**Evidence:** The chart in `render_performance.py:_diagnostics_chart_section()`
is labelled "Cumulative net CAR - trailing 12 months" and has 13 x-axis
labels (M-12 through Now). But `export_dashboard_json.py:_sparkline()`
only generates 9 weekly buckets (weeks t-12 through t-4). Specifically:

- Sparkline returns 9 values covering ~2 months of weekly windows
- Chart pads to 13 slots by `unshift(null)`-ing four nulls at the front
- Empty buckets are forward-filled from `last_val` (initialised to 0.0)
- Result: chart shows `null` for M-12..M-9, then a flat line at 0% from
  M-8 to Now because most recent firings haven't matured to T+90 yet
  (T+90 takes 4.5 months to mature, so the trailing buckets are mostly
  empty and forward-fill 0).

The dashed line the user noticed is actually the FTSE All-Share
benchmark dataset (the only one with `borderDash: [6,4]`), made visible
in the M-12..M-9 range because the signal datasets there are null. The
solid red line from M-8 onwards is the signal lines overlapping the
benchmark line at 0%.

**Two distinct fixes needed:**

1. **Fix the data**: extend `_sparkline()` to return MONTHLY buckets
   covering a full 12 months, not weekly buckets covering 2 months.
   The function name and chart label both promise 12 months — match
   that.

2. **Fix the forward-fill**: replace `last_val` initialisation with
   `None` so empty buckets show as gaps (not as misleading 0%). With
   `spanGaps: true` already on the Chart.js config, this will give a
   visually honest "no data here" gap rather than a false 0% reading.

**Effort:** ~1 hour. Touches `_sparkline()` in `export_dashboard_json.py`
plus possibly the schema check in `render_performance.py`.

**Why this looked like a date problem (and wasn't):** The dashed/solid
transition at M-8 looks suspicious because it's a sharp break. It feels
like missing data from a specific date range, which is exactly what
a date-shift bug would produce. But it's actually the artifact of
9-vs-13 slot mismatch plus forward-fill from 0%. The dates in the
underlying backtest CSV are fine.

---

### B-012 — Older Stage 2 / 4 / 5 test suites surface ~4 errors under `unittest discover`
**Discovered:** 2026-05-18 (during Sprint 1 verification)
**Severity:** Low / cosmetic — pre-existing, not introduced by Sprint 1.
The two new Sprint 1 test files (`test_sparkline.py`,
`test_repair_dates_atomicity.py`) pass cleanly on their own; the
discovered errors come from older test files
(`test_stage_02.py`, `test_stage_04.py`, `test_stage_05.py`,
`test_p3_lookahead.py`, `test_db_smoke.py`).

**Evidence:** Running `python -m unittest discover -s .scripts -p
"test_*.py"` on 2026-05-18 produced `Ran 49 tests in 3.010s` with
`FAILED (errors=6)`. One of the six was the file-handle leak in
`repair_dates.py` (now fixed). The other ~4 are unknown until we read
the full failure list.

**Why this matters:** If we ever want a "run every test, must be
green" CI gate (sensible after Sprint 3), these need to be either
fixed or marked as known-stale and skipped. Today they're just noise.

**Suggested approach:** One focused session:
1. Run discover with `-v` and capture the full failure list.
2. Triage each: (a) genuine regression → fix the code or update the
   test; (b) fixture drift → refresh the fixture; (c) genuinely
   obsolete test → delete with a one-line note in this backlog
   item documenting why.
3. Re-run discover; expect `OK`.

**Effort:** ~1 hour, possibly less. Tests are small and self-contained.

**Cost:** £0.

**Update 2026-06-05:** the ~4 errors have grown to **26** (see B-117, which
supersedes and expands this item with the full categorised list).

---

### B-117 — Test-suite health: repair pre-existing full-sweep reds (26 stale assertions)
**Discovered:** 2026-06-05 (during Sprint 32 verification — Linear DIR-40).
**Sprint:** 33 — folded in 2026-06-05 as the **opening job** (gate must be
trustworthy before the B-107–B-110 display wins land on top).
**RESOLVED 2026-06-05** — full `unittest discover` sweep green (1149 tests OK,
skipped=6); was 26 red. Zero production code changed (every fix was a stale
assertion/fixture). Notable: the 11-failure phase1 cluster was one Windows-only
harness bug (`python3` Store stub + cp1252 `open()` on a UTF-8 file) — now uses
`sys.executable` + `encoding="utf-8"` and raises loudly on subprocess failure.
The full sweep — not per-file runs — is now the gate.
**Severity:** P2 — QA-gate trustworthiness. Pre-existing; **none** introduced by
Sprint 32 / B-074 (that file is 65/65 green). Expands and supersedes B-012.

The full `python -m unittest discover -s .scripts -p "test_*.py"` sweep is
**26 red (19 failures + 7 errors)** — stale-test drift where assertions were
never updated as the code evolved. Per-sprint "N tests green" figures were
targeted file runs, not the full sweep, so this rot accumulated unnoticed.

**Categories (update assertions/fixtures to match current code; do NOT change
production behaviour without separate sign-off):**
1. **Schema-version pins** — `test_role_backfill.test_migration_adds_column`
   expects `schema_version '5'`, now `'8'`.
2. **Cohort export shape** — `test_cohort_performance_export`: `signal_groups
   12 != 11`; `test_t6_pending_month_has_all_horizons_pending` missing `t1`.
3. **Sprint-24 dynamic-horizon string drift** — `test_phase8_pending.test_10`
   (`m.mean_car_t21 == null`), `test_15` (un-split tooltip), and
   `test_phase3_level1_table.test_24` (`Back to overview` vs `Performance
   overview`). Update literals to the `cohortPick` / `aHLabel` reality.
4. **prices.fetched_at NOT NULL** — `test_sprint29.TestB098AbsReturn` fixtures
   insert without `fetched_at`.
5. **render_company / review.html evolution** — `test_pdmr_editor_phase0`
   (`_render_transaction_rows` renamed/removed); `test_pdmr_editor_phase1`
   (11 review.html field/endpoint asserts).
6. **Misc** — `test_sprint29` B-009 sparkline (`<polyline` 0 != 2), B-100
   paper-close (`closed 0`); `test_sprint33` `mean_abs_return` None.

**Out of scope:** the `ResourceWarning: unclosed database` flood (that is B-013).
Any change that alters production output rather than a stale assertion must be
flagged, not silently "fixed to pass".

**Acceptance:** full `unittest discover` sweep green (or every remaining red
explicitly justified + ticketed); adopt the full sweep — not per-file runs — as
the gate going forward.

**Size:** M. **Cost:** £0.

---

### B-013 — Audit DB-touching scripts for connection-leak hygiene
**Discovered:** 2026-05-18 (during Sprint 1 — `repair_dates.py` had this gap)
**Severity:** Low / hygiene. The recently-fixed `repair_dates.py` bug
was: `conn = db.connect()` followed by a body that could raise before
reaching `conn.close()`. On Windows this leaks the SQLite file lock
until the process exits, which can block the next `start.bat` run
from opening the DB cleanly. Same pattern likely lurks in other scripts
that haven't been exercised by tests.

**Files to audit:**
- `.scripts/refresh_all.py`
- `.scripts/backfill_filings.py`
- `.scripts/backfill_prices.py`
- `.scripts/backfill_announced_at.py`
- `.scripts/eval_signals.py`
- `.scripts/detect_clusters.py`
- `.scripts/build_dashboard.py`
- `.scripts/export_dashboard_json.py`
- `.scripts/scrape_investegate.py` (if it opens conns)

**Suggested fix:** For each, ensure `db.connect()` is paired with
either a `try/finally: conn.close()` or a `with closing(...) as conn`
context manager. `run_pending_sweep.py` (Sprint 1) and the now-fixed
`repair_dates.py` are the reference patterns.

**Effort:** ~30 minutes — mostly grep + small edits. No new tests
needed; the pattern is mechanical.

**Cost:** £0.

---

### B-016 — Parser writes regulatory-disclosure boilerplate into the `company` field
**Discovered:** 2026-05-18 (during Sprint 2 / B-011 ticker review)
**Severity:** Real data correctness. ~30 tickers show a `company` value of
`", emission allowance market participant, auction platform, auctioneer or
auction monitor"` instead of the actual company name. Affected tickers
include (incomplete list, all with this exact mangled string):

`SPT, YOU, ZTF, NET, EBQ, CAU, CKT, GELN, DFIJ, EEE, JAR, NAR, AAZ, CAM,
CHF, CHRT, GOT, JEL, LIFS, LIKE, PAF, QHE, RFX, SAL, SOLG, STS, VEIL, AMS,
PBEE`

**Evidence:** During the Sprint 2 IT/CEF review, all 650 tickers in
`.data/_review_candidates.csv` were scanned. ~30 displayed the same
regulatory-disclosure boilerplate from MAR Article 19 in the company
column. The phrase comes from a standard disclosure block at the bottom
of PDMR filings; the parser is mis-locating the company-name cell when
the filing layout puts it somewhere unexpected.

**Suggested fix:** Same root cause as B-001 + B-004 — regex on flat text
crossing cell boundaries. Fold into the Sprint 3 table-aware parser
rewrite. The B-001 + B-004 implementation should add an assertion that
the extracted `company` does not contain "emission allowance market
participant" and other known boilerplate sentinels; if it does, fall
back to the headline-derived company hint or move the row to pending.

**Effort:** Folded into B-001 + B-004 (Sprint 3) — no extra time budget.

**Cost:** £0.

---

### B-017 — Parser writes director name into the `company` field
**Discovered:** 2026-05-18 (during Sprint 2 / B-011 ticker review)
**Severity:** Real data correctness. Cosmetic on the dashboard but a
clear signal of the same template-variant gap as B-001 / B-004 (the
mirror case).

**Evidence:** During the Sprint 2 review, the following tickers showed
a director name in the `company` field:

| Ticker | Mangled `company` value |
|--------|-------------------------|
| AAL    | "Monique Carter"        |
| GLE    | "Stefan"                |
| SSPG   | "Karina Deacon"         |
| PCTN   | "Robert Clift"          |
| RPI    | "Richard"               |

Same root cause as B-004 (regex crossing `<td>` boundaries), but the
mirror version — the company-name regex extends into the director cell
instead of staying in the company cell.

**Suggested fix:** Bundle with B-001 + B-004 (Sprint 3). The table-aware
parser should anchor company extraction on the "Issuer name" header cell
and read only the next single cell; same logic as the B-004 director
fix.

**Effort:** Folded into B-001 + B-004 (Sprint 3).

**Cost:** £0.

---

### B-019 — Cumulative net CAR chart needs per-series toggle + solo mode
**Discovered:** 2026-05-18 (Rupert request, post-Sprint-2)
**Severity:** Usability. After Sprint 1's B-009 fix the CAR chart shows
~7 signal series (T1, T2, T3, T4, S1, F1, T0) plus the FTSE All-Share
benchmark — too many overlapping lines to read any individual series
clearly.

**What Rupert wants:** Click a legend entry to hide that series; double-
click a legend entry to **solo** it (hide every other series so only the
clicked one and the benchmark line remain). This is the standard "focus
on one signal type at a time" UX pattern.

**Suggested fix:**
1. Enable Chart.js native legend click-to-toggle in
   `render_performance.py:_diagnostics_chart_section()` — set
   `options.plugins.legend.onClick` to the default (or just remove any
   override that's disabling it). One line of JS.
2. Add a double-click solo handler: on `dblclick` of a legend entry,
   `setDatasetVisibility(true)` on the clicked dataset + the benchmark
   dataset, `setDatasetVisibility(false)` on everything else, then
   `chart.update()`. ~10 lines of JS.
3. Add a small "Show all" link / button below the chart that resets all
   datasets to visible.
4. The benchmark line (FTSE All-Share) should be exempt from solo-mode
   hiding — it's the reference line, always shown.
5. Persist the user's last-selected solo state in `localStorage` so a
   page reload keeps the focused view. Keyed by chart id so the
   per-page setting doesn't bleed between pages.

**Files to touch:**
- `.scripts/dashboard/render_performance.py` —
  `_diagnostics_chart_section()` Chart.js config block.
- Possibly `.scripts/dashboard/templates.py` if the chart JS is shared
  helper code.

**Validation:**
- Open Performance page. Click each legend entry — that series hides.
  Click again — re-appears.
- Double-click T1 — only T1 + benchmark visible.
- "Show all" — back to default view.
- Reload page — last solo state preserved.

**Effort:** ~1 hour. ~30 min of JS, ~30 min of testing across browsers.

**Cost:** £0.

**Out of scope:**
- Toggling individual S1 clusters (one chart line per specific buy
  cluster) — that's a different, larger feature. Flag B-019b if needed
  later.
- Chart legend redesign / repositioning.

---

### B-010 — Transaction tables not sorted chronologically; today's rows show time, not date
**Discovered:** 2026-05-18
**Severity:** Usability — current sort order makes it hard to scan for
what's new. Affects Today, Performance, and per-Company pages.

**Evidence:** Transaction tables across the dashboard surface aren't
consistently sorted with the most recent dealing at the top. Additionally,
when a transaction is dated today, the date column currently shows the
hour-level timestamp of the filing rather than just the date, which is
visually noisy.

**Suggested fix:**
1. Apply a uniform sort across all transaction tables on Today,
   Performance, and Company pages: `ORDER BY date DESC, announced_at DESC`
   so the freshest dealing is always at the top.
2. In the date column renderer, format today's rows as the date only
   (e.g. "18 May" or "Today") rather than the time-of-day. Yesterday and
   earlier rows continue to show the date (already correct).
3. Verify the same ordering applies to the cluster expand-out views.

**Effort:** ~30 minutes. Touches the table renderer in
`export_dashboard_json.py` (or wherever the row data is sorted before
serialisation) and the per-page templates in `.scripts/render_*.py`.

**Cost:** £0.

---

## P3 — exploratory / nice-to-have

### B-007 — Add an "informational" I6 invariant for late filings
**Discovered:** 2026-05-15
**Severity:** None — purely a visibility enhancement.

**Idea:** Today we relaxed I5 to allow legitimate late filings (up to
1,460 days). But it would be useful to see a count of how many late
filings exist as a separate badge on the data-quality panel — not as
a failure, just as information. "12 late-disclosed transactions" would
tell you if a particular reporting period had unusual back-dated
activity.

**Effort:** ~30 minutes. Add `_run_I6` to `audit_dates.py` and a new
row to the health panel that always shows as a neutral grey badge.

---

### B-014 — `_pending_review.json` accumulates 4,000+ unrecoverable entries
**Discovered:** 2026-05-18 (during B-002 LLM sweep investigation)
**Severity:** Low / housekeeping. Not blocking anything, but the file
is harder to reason about than it should be.

**Evidence:** As of 2026-05-18, `_pending_review.json` holds **4,171
items**. Investigation showed only ~10 of those are the original
B-002 target (`could_not_parse_tx_date`). The remaining ~4,160 break
down roughly as:

| Warning prefix                              | Approx count |
|---------------------------------------------|--------------|
| `bundled multi-PDMR filing`                 | ~2,276       |
| `required_fields_missing`                   | 1,693        |
| `zero_shares_non_grant`                     | 1,001        |
| `could_not_classify_type`                   | 527          |
| `multiple_distinct_prices`                  | 520          |
| `could_not_separate_price_volume`           | 441          |
| `foreign_currency`                          | 350          |
| `could_not_extract_PDMR_name`               | 247          |
| `could_not_extract_company`                 | 166          |
| `llm_error:LLMParserError` (prior attempt)  | 43           |

Most of these will never be auto-recovered by parser improvements
alone — bundled multi-PDMR filings need a fundamentally different
extraction approach (one row → many directors); foreign-currency rows
are intentionally rejected; many `required_fields_missing` entries
are non-PDMR filings that got into the pending file by mistake.

**Why this matters:** Today `run_pending_sweep.py --target stuck-on-date`
has to filter through all 4,171 entries just to find its ~10
candidates. Cheap (read-only filter, sub-second), so not urgent. The
real cost is mental: when you look at "4,171 pending" it sounds like
real data hiding in there, when most of it is permanently parked.

**Suggested approach:**
1. Add a `_archived_pending.json` companion file.
2. One-time triage script: any entry whose warnings contain
   `bundled multi-PDMR filing`, `foreign_currency` (with no
   `could_not_parse_tx_date`), or `llm_error:LLMParserError` moves
   to archive. Resulting pending file is ~hundreds, not thousands.
3. New invariant in `audit_dates.py` (or a sibling `audit_pending.py`):
   warn if pending count climbs above N (e.g. 200) so we notice if
   the pipeline starts dropping rows again.
4. Dashboard data-quality panel: surface "X pending, Y archived" as
   neutral grey badges so you can see at a glance what's actually
   outstanding.

**Effort:** ~1 hour for the triage script + archive; ~30 minutes for
the dashboard badge.

**Cost:** £0.

---

### B-015 — Sweep cannot safety-check rows whose pending entry lacks announced_at — RESOLVED 2026-06-03
**RESOLVED 2026-06-03 (Sprint 21 verification):** `run_pending_sweep.py` now derives `announced_at` from the cached HTML JSON-LD `dateCreated` (`_extract_announced_at_from_html`) before the LLM call, and the ±60-day `SANITY_WINDOW_DAYS` guard (`_date_within_window`) fires against that anchor; entries with no anchor are counted (`missing_announced_at`) rather than blind-accepted. Original report retained below for history.


**Discovered:** 2026-05-18 (during Sprint 1 QA review)
**Severity:** High — silently bypassed safety check.

**Evidence:** `run_pending_sweep.py` validates LLM-returned dates by
checking they're within ±60 days of the entry's `announced_at`. But
pending entries written by `repair_dates.py` (Case B path, lines
281-289) only carry `url`, `warnings`, `extracted`, `parser_source`,
`repair_note` — no `announced_at`. All 6 currently-stuck-on-date
candidates and the 1 row already recovered (CPIC, China Pacific
Insurance, date `2026-04-10` accepted blind 2026-05-18) went through
the LLM with no anchor to validate against. The sanity guard was a
placebo for repair-sourced entries.

The B-002 sweep is now temporarily blocked in code: any pending
entry without `announced_at` is skipped at the pre-LLM gate (no LLM
spend, no blind acceptance). The 6 remaining rows stay in pending
until this item ships.

**Suggested fix:** Derive `announced_at` for sweep input from one of:
1. **The cached HTML** at `.scripts/_scrape_cache/{rns_id}.html`. The
   Investegate page contains the announcement timestamp in its header
   block (typically "Released: DD MMM YYYY HH:MM"). Add a helper to
   `parse_pdmr.py` (or a small util in `scrape_investegate.py`) that
   extracts this and call it from `run_pending_sweep.py` before the
   LLM step.
2. **The scraper's archive index** if it persists per-rns_id metadata.
3. Fall back to the file mtime of the cached HTML — coarser but
   bounded.

Once derived, the existing ±60-day guard works as documented.

**Also audit:** the CPIC row recovered 2026-05-18 should be
eyeballed against its Investegate URL to confirm the LLM-returned
date (2026-04-10) is correct. If wrong, delete the row from
`transactions` (Windows-side, via PowerShell) and re-process after
B-015 ships. URL:
`https://www.investegate.co.uk/announcement/rns/china-pacific-insurance-group-co-ltd--cpic/pdmr-shareholding/9519110`

**Bonus consideration:** CPIC is a Chinese-listed (Shanghai)
issuer with CNY-denominated transactions. Even with a verified date,
foreign-issuer rows may not belong in the dataset — same logic as
B-011 (IT/CEF exclusion) but for foreign listings. Open question for
Rupert: should foreign issuers also be excluded? Possibly a future
B-NNN item.

**Effort:** ~1 hour. ~30 min for the HTML extract helper + tests,
~30 min for integration into the sweep + re-run on the 6 stranded
rows.

**Cost:** £0 to fix; recovers up to 7 rows currently stranded.

---

### B-018 — Classifier needs a sustainable periodic-refresh data source
**Discovered:** 2026-05-18 (during Sprint 2 / B-011 execution)
**Severity:** None right now (Sprint 2 was a one-shot purge), but a real
problem if you ever want quarterly re-classification.

**Evidence:** Sprint 2 attempted to use the AIC website
(theaic.co.uk) as the authoritative IT/VCT membership list. The page is
JavaScript-rendered via a Drupal frontend that loads data from a
Morningstar API client-side — `urllib` + BeautifulSoup sees an empty
shell with no member data. We also tried Yahoo Finance's `v7/quote`
endpoint for `quoteType` lookup; that's been walled off behind
authentication (returns 401 Unauthorized) since some time in 2024-25.

Sprint 2 worked around the gap with `--no-aic` and a hand-curated
`.scripts/manual_include.csv` (97 entries) on top of the conservative
name regex. Adequate for the one-shot purge, but fragile for any
ongoing refresh — when new ITs/VCTs come to market we have to spot
them by hand.

**Options for a sustainable data source:**

1. **LSE monthly "Listed Investment Funds" Excel** — published monthly
   by the LSE; contains every UK-listed CEF/IT/VCT/REIT with EPIC
   tickers. URL pattern:
   `docs.londonstockexchange.com/sites/default/files/reports/Listed%20Investment%20Funds%20List<Month><Year>.xlsx`.
   Most reliable. Needs an Excel-reader dependency (openpyxl is
   already in `xlsx`/`pdf` pipelines elsewhere; not in this project yet).
2. **Claude in Chrome to render the AIC page** — runs in Claude's
   session, uses the rendered DOM, extracts member tickers. Slower
   per-refresh but uses the authoritative source. Awkward to put in
   `refresh_all.py`.
3. **Paid AIC data feed** — annual subscription via Morningstar; cleanest
   but a cost commitment we don't need to make yet.
4. **Maintain `.scripts/manual_include.csv` by hand** — current state.
   Acceptable if classifier is only re-run when a new well-known IT
   comes to LSE (handful per year).

**Suggested approach:** Option 1 (LSE Excel) if/when this becomes a real
need. For now, keep the manual CSV; periodic ad-hoc refresh by reviewing
`.data/_review_candidates.csv` once or twice a year is probably enough
for a 2,000-row dataset.

**Effort:** ~3 hours for Option 1 (Excel downloader + parser +
classifier integration). £0 cost.

---

### B-008 — Performance tracker uses `bisect_right` on string-sorted dates
**Discovered:** 2026-05-15 (during code review)
**Severity:** Currently OK because I1 now guarantees ISO format, but
fragile.

**Evidence:** `backtest.py:_first_trading_date_after()` uses
`bisect_right(dates, key)` on a sorted list of date strings. This
relies on every date being in YYYY-MM-DD format so lexicographic sort
== chronological sort. If a non-ISO date ever sneaks in (which I1
prevents at insert time but only because of today's fix), the bisect
would silently return the wrong entry day.

**Suggested fix:** Add an `assert ISO_DATE_RE.match(d)` at the top of
the loop that builds `dates`. Cheap belt-and-braces.

**Effort:** ~10 minutes.

---

### B-020 — Triage 334 orphan candidates from Sprint 3 reparse
**Discovered:** 2026-05-18 (Sprint 3 preview review)
**Severity:** Medium — data hygiene. These are existing transactions
that the new table-aware parser doesn't reproduce when re-running
against their cached HTML. We chose `--confirm` (no orphan deletion)
in Sprint 3 to avoid touching 50–80 clean-name orphans (Rachel
Lawrence, David Bloomfield, Graham Charlton, etc.) we couldn't
explain. They split into two groups:

1. **Multi-line label garbage** — JDW filings with director value
   `'Role\nNo. of ordinary shares purchased\nTom Ball\nPeople
   Director'`, GLE with `'Graham\nb'`, RPI with `'Daniel\nb'`. Truly
   bad data, safe to delete.
2. **Clean-name orphans (~50–80)** — Rachel Lawrence (STB 9528991),
   David Bloomfield (ULTP 9529301), Graham Charlton (SCT 9424267),
   Richard Howell (PHP 9018223), and similar. Director name looks
   fine in DB; new parser produces zero rows for these filings.
   Either the new parser fails on a layout variant, or the cached
   HTML genuinely lacks what the old parser somehow extracted.

**Suggested approach:**
1. Iterate the orphan list. For each, run the new parser standalone
   against the cached HTML and dump `_extract_via_table` output.
2. Filings with no transaction-table header detected → consider
   extending `_find_transaction_table` to handle the variant.
3. Filings with a real transaction table but rows failing validation
   → log the warnings and decide row-by-row.
4. After analysis, run `reparse_corpus.py --confirm --delete-orphans
   --limit <list-of-known-bad-rns-ids>` to surgically clean only the
   truly bad rows.

**Effort:** ~2 hours triage + ~30 min per parser layout fix.

**Cost:** £0.

---

### B-021 — `classify_issuers.py` resets `is_excluded_issuer` flags on every run
**Discovered:** 2026-05-18 (Sprint 3 pre-flight; caused FCIT/LTI/SCF
to nearly be re-imported during reparse).
**Severity:** Medium — silent correctness risk. The classifier opens
each run with `UPDATE tickers_meta SET is_excluded_issuer=0` then
re-applies flags from the four sources. If a ticker's transactions
have been deleted (Sprint 2 IT/CEF purge), Source C (name regex)
can't re-flag it because the regex matches on company names from
`_load_companies(conn)`, which only sees tickers with current
transactions. So the 33 name-regex-only IT tickers (FCIT, LTI,
SCF, etc.) silently lose their exclusion flag on the next
classifier run.

**Mitigation in place:** `reparse_corpus.py:_load_excluded_tickers`
now reads `.data/_excluded_it_cef.csv` (the append-mode audit log)
as a supplementary source.

**Suggested fix:**
1. Add every previously-excluded ticker to `.scripts/manual_include.csv`
   so Source D re-flags them regardless of whether transactions exist.
2. Or: change the classifier's reset rule so it doesn't zero a flag
   that wasn't explicitly re-classified during the current run
   (more invasive).
3. Apply the same defensive pattern to any future filter (the
   scrape ingest filter, the dashboard query filter, anything that
   needs "is this ticker excluded?").

**Effort:** ~30 min for option 1. ~1 hour for option 2.

**Cost:** £0.

---

### B-022 — Filings without a labelled issuer KV row produce empty company
**Discovered:** 2026-05-18 (Sprint 3 anchor test, NET 8998766 sample).
**Severity:** Low — empty is acceptable; it's a polish improvement.

**Evidence:** NET (Netcall) filing 8998766's HTML has no `['Name',
'Netcall plc']` 2-cell row and no `[*, 'Full name of the entity',
*]` 3-cell row. Instead, the company name "Netcall plc" floats as
`cells[0]` of an unlabelled header row. The table-aware parser
can't pick it up via KV semantics. Result: `company=''` in the DB.

**Suggested approach:** Add a Pass 3 fallback to
`_find_company_in_soup` that scans header-like rows in Table 0 for
a cell ending in "plc" / "Ltd" / "Limited" — but be conservative
(only fire when no Pass 1 / Pass 2 match was found, and only on
strong-looking corporate-form strings). Alternative: derive from
the URL slug or filing headline (both contain the company name).

**Effort:** ~30 min.

**Cost:** £0.

---

### B-023 — Bundled-PDMR detection doesn't fire on AAL "PCA" pattern
**Discovered:** 2026-05-18 (Sprint 3 anchor test, AAL 8950385 sample).
**Severity:** Medium — partial mis-extraction. AAL 8950385 has three
PDMR sections (Stuart Chambers, Magali Anderson, Nonkululeko Nyembezi)
but the parser returns only Stuart Chambers (first match) via the
legacy regex path. The Sprint 3 PCA-pattern fix to
`_bundled_name_warning` didn't appear to fire — investigate why.

**Suggested approach:** Trace `_bundled_name_warning` against AAL's
flat-text. The PCA pattern `Details\s+of\s+(?:the\s+)?(?:...|PDMR\s*/\s*person\s+closely\s+associated|...)`
should match "Details of PDMR / person closely associated (PCA)" —
verify, fix if not, add a unit test in the next sprint's parser tests.

**Effort:** ~30 min triage + fix.

**Cost:** £0.

---

### B-025 — Normalize director `role` field into 14 canonical buckets + 8 signal tiers
**Discovered:** 2026-05-20 (DB inspection during signal-bucket scoping)
**Status:** Phase A + Phase B CODE COMPLETE 2026-05-20. Awaiting Rupert's backfill + eval_signals re-run.
**Severity:** P1 — blocks role-conditional signal firing (CEO+CFO combos, Chair+NED clusters, Founder-only signals).
**Evidence:** `transactions.role` has 627 distinct strings across 2,108 rows. Same role appears under 4+ case variants ("Non-Executive Director" / "Non-executive Director" / "Non-executive director" / "NON-EXECUTIVE DIRECTOR"). ~3% of rows are PDF parser fragments ("Nature of the transaction", "Number of shares acquired", "| Partnership Shares |"). Two existing classifiers (`signals/roles.py` and `classify_role.py`) silently misfire on case variants.

**Approach (two phases):**

**Phase A (Now, ~4 hours, additive — no historical shift):**
- New `transactions.role_normalized` column (migration 004).
- New `.scripts/role_normalize.py` with deterministic `normalize_role()` mapping to 14 canonical buckets: CEO · CFO · Other Chief · Chair (executive) · Non-Exec Chair · NED · Executive Director · Divisional / Regional Exec · Founder · President / VP · Company Secretary / General Counsel · PCA · PDMR-only · Other. Plus a "Parser fragment" data-quality flag.
- Backfill script (Zone B — Rupert runs from PowerShell). Single transaction. Pre-snapshot `.bak`. Integrity check before and after.
- Wire into `db.upsert_transaction` so future inserts populate normalized alongside raw.
- Update cosmetic chip code on dashboard (`render_helpers.role_chip`, `render_company.py` colour logic, `dashboard/index.html` `roleChipCls`).
- Update JSON export and backtest CSV to emit `role_normalized` (keep raw `role` for audit).
- Unit + integration tests. Bucket-distribution sanity floor (≥350 CEO, ≥450 NED, ≥200 CFO; ≤5% Other; ≤5% Parser fragment).
- Signal logic (`signals/roles.py`, `classify_role.py`) stays on raw `role` — no historical T1–T4 firing counts shift.

**Phase B (Next, ~1–2 hours, gated):**
- Generate diff CSV showing how T1–T4 firing counts move when signal logic cuts over to `role_normalized`.
- Rupert reviews the diff and signs off in writing.
- Cut `signals/roles.py` and `classify_role.py` over to `role_normalized` (dict lookup, no more regex on raw text).
- Re-run backtest. Replace historical performance numbers on the dashboard.
- Update Performance page "Top buys by role" tiles (currently bucket as `ceo_cfo / other_exec / ned`).
- Unblocks Spec 08 (behavioural signals) — required prerequisite for any role-combo signal (CEO+CFO, Chair+NED, Founder-only).

**Spec:** `docs/specs/role-normalization-pass.md`.

**Effort:** Phase A ≈ 4 hours code + ~5 min Rupert-time to run the backfill. Phase B ≈ 2 hours + Rupert sign-off gate.

**Cost:** £0.

---

### B-146 — Market-cap column on all ticker-bearing views (Sprint 54, P2)

**Added 2026-06-07.** Rupert: "add a market cap column to all areas where a company ticker appears."

Now that B-137 has populated `tickers_meta.market_cap_gbp` for 617 tickers (98% coverage), the data should be surfaced wherever a ticker is shown.

**Views to update:**
1. Main dealings table (Today / This Week tabs) — "Mkt Cap" column after Ticker
2. Performance tracker table — "Mkt Cap" column alongside Ticker
3. Company drill-down header — market cap as a summary stat
4. Upcoming Events box (B-145) — add Mkt Cap to the 4-column table

**Format:** `£214.9m` below £1bn; `£1.2bn` at or above £1bn. Show `—` for NULL.

**Data flow:** `tickers_meta.market_cap_gbp` → `export_dashboard_json.py` (per-ticker map) → `render_index.py` / `render_company.py` / JS.

**Agent:** dashboard-designer. **DIR:** DIR-73. **Est:** 3 pts. **Dep:** B-137 (Done).

---

### B-151 — Horizon rename: T+21→T+30, T+90→90cal, new T+180, T+252→T+365
**Added:** 2026-06-09. **Status:** ✅ DONE 2026-06-09 (Sprint 57). **DIR:** DIR-83. **Pri:** P1. **Est:** 7 pts. **Agent:** general-purpose.

Rename all CAR measurement horizons from trading-day to calendar-day labels across the entire app. T+21→T+30 (21 td ≈ 30 cal days), T+90 redefined as 90 cal days (~63 td, shorter window — old data dropped), new T+180 (180 cal days ≈ 126 td), T+252→T+365 (252 td ≈ 365 cal days). Paper book exit: 21 cal → 30 cal days. ~260 change sites across ~25 files. No DB migration needed (metrics live in CSV). Full spec: `docs/specs/B-151-horizon-rename.md`. QA review complete 2026-06-09.

**Execution (Rupert, after code merge):** `python backtest.py` → `python eval_signals.py --rebuild` → `python export_dashboard_json.py` → `python build_dashboard.py` → `python .scripts/snapshot_db.py`

---

### B-153 — Paper P&L tile: wire signal-tier filter + add time-horizon toggle
**Added:** 2026-06-09. **Status:** Done (Sprint 58, 2026-06-10, B-001 pattern — verified already built). **DIR:** DIR-82. **Pri:** P3. **Est:** 3 pts. **Agent:** dashboard-designer.
*(Renumbered from B-151 to make room for horizon rename)*

The "Open Paper P&L" tile on the dashboard index has a signal-tier dropdown (t0/t1/t2/t3/t4/s1/f1) that currently only persists a localStorage preference — it never recalculates the £ P&L figure. Additionally, there is no way to view P&L at different time horizons (T+1 / T+21 / T+90).

**Work required:**
1. **Wire the signal-tier filter** — when the dropdown changes, recompute the P&L total using only open paper trades whose `signal_id` matches the selected tier. Touches `render_index.py` + `export_dashboard_json.py` (pre-group P&L by tier in the JSON so the JS can filter client-side without a reload).
2. **Add a T+1 / T+21 / T+90 horizon toggle** — mirror the existing cohort-table horizon toggle pattern; show unrealised P&L valued at the chosen horizon's price (requires per-trade horizon prices in the JSON export).
3. **Relabel "T1" option** in the dropdown to "T1 — CEO/CFO/Founder" to avoid confusion with the T+1 time horizon.

**Files:** `render_index.py`, `export_dashboard_json.py`, `server.py` (if API endpoint needed).
**Dep:** None.

---

### B-152 — Capital Deployed trending on Today page (All/Small/Large split, 3mo MA + mini chart)
**Added:** 2026-06-09. **Status:** Done (Sprint 58, 2026-06-10, B-001 pattern — verified already built). **DIR:** DIR-84. **Pri:** P2. **Est:** 3 pts. **Agent:** dashboard-designer + general-purpose.

New row below existing tile strip showing capital deployed split by All / Small-cap (<£500m) / Large-cap (≥£500m). Each panel: live £ notional, 3-month MA of weekly delta, mini 12-week sparkline. Data: retrocompute 13 weekly snapshots from `paper_trades` table — no new schema. Small/large via `tickers_meta.small_cap`. Hold-period: 30 cal days (B-151).

**Design decisions confirmed 2026-06-09:** Placement = Option A (row below tile strip). Threshold = £500m always.

**Deps:** B-148 ✅, B-151 ✅. **Spec:** `docs/specs/B-152-capital-deployed-today.md`.

---

### B-155-A — Capital Deployed panels: add position count + delta (Today page)
**Added:** 2026-06-10. **Status:** Done (Sprint 59, 2026-06-10). **DIR:** —. **Pri:** P2. **Est:** 1 pt. **Agent:** general-purpose.

Each Capital Deployed panel on the Today page now shows a position count ("12 trades") below the £ value, plus a count trend vs 3-month average (▲/▼ N%) matching the existing value-trend treatment. Changes: `export_dashboard_json.build_capital_deployed()` extended with `all_count`/`small_count`/`large_count` + 3m mean arrays; `render_index._cap_panel()` extended with optional `count_key`/`ma_count_key` params.

**Deploy:** `python .scripts/export_dashboard_json.py` + `python .scripts/build_dashboard.py`

---

### B-155-B — Live Paper Book: filter by cap size on small/large performance pages
**Added:** 2026-06-10. **Status:** Done (Sprint 59, 2026-06-10). **DIR:** —. **Pri:** P2. **Est:** 1 pt. **Agent:** general-purpose.

Small Cap performance page now shows only trades where `market_cap_gbp < £500m` in the Live Paper Book table; Large Cap shows only `>= £500m`. Summary stat strip (open positions, capital deployed, MTM, winners/losers) is recomputed from the filtered set. Change in `render_performance._paper_book_section(size_band=...)` — reads `size_band` already threaded in from `render()` / `render_to_file()`.

**Deploy:** `python .scripts/export_dashboard_json.py` + `python .scripts/build_dashboard.py`

---

### B-154 — Test suite debt cleanup (schema version + API drifts)
**Added:** 2026-06-09. **Status:** Done (Sprint 58, 2026-06-10). **DIR:** DIR-85. **Pri:** P2. **Est:** 3 pts. **Agent:** general-purpose.

Fix 8 failures + 9 errors in the test suite that are pre-existing drift (not real bugs). Zone A only — test file updates only, no code/logic changes.

Items: (1) Schema version hardcoded '10', real '12' (Sprints 55–56). (2) `test_sprint28` `backfill_reporting_dates` API drift. (3) `test_fixture_04` bundled PDMR assertions. (4) `test_market_cap` AIM logic. (5) `test_car_values_in_html` basket render / CAR column names. (6) Layout ordering after B-149 perf split. (7) `lse_diary` KeyError 'matched'.

**Goal:** `python -m unittest discover -s .scripts -p "test_*.py"` → 0 failures, 0 errors.

---

### B-024 — Self-healing auto-backup is broken — RESOLVED 2026-06-03
**RESOLVED 2026-06-03 (Sprint 21 verification):** mechanism works and coverage is complete. `db_health` exposes backup/restore/guard/seal + stale defences (`warn_if_stale`/`fail_if_stale`/`auto_seal_if_stale`); `start.bat` wires `restore` → `auto-seal 24` → `warn-stale 24` → `fail-stale 48`; every write-path script (eval_signals, backtest, drain_pending, reparse_corpus, backfill_*, classify_issuers, exclude_investment_trusts, delete_triaged_orphans, fix_incident_buys, fix_sprint20_delete) calls `db_health.backup()` pre-flight and `db_health.seal()` on success. Confirmed live: Rupert's 2026-06-03 reparse wrote a fresh `.bak`. Original report retained below for history.


**Discovered:** 2026-05-18 (Sprint 3 pre-flight; caught the FUSE
truncation event only because the manual `.pre-it-purge.bak` was
clean).
**Severity:** High — eliminates a critical safety net. The
documented "self-healing backup" story (CLAUDE.md) is currently
non-functional.

**Evidence:** `.data/directors.db.bak` was dated 2026-05-16, identical
size and identical 13-page FUSE truncation pattern to the live
`directors.db` discovered today. Multiple successful pipelines have
run since 16 May (Sprint 1 + Sprint 2 + Sprint 3) and `.bak` did not
refresh.

**Suggested approach:**
1. Audit `.scripts/db_health.py` — is the backup routine being
   invoked? Is it writing to the right path? Does it survive a FUSE
   re-mount?
2. Confirm `start.bat`'s "restore from backup if integrity fails"
   path is exercised by an integration test (manually delete pages
   from a test DB, run start.bat, assert recovery).
3. Add a "stale-backup" warning to `start.bat`: `.bak` older than
   24 hours → warn loud, older than 48 hours → fail loud.

**Effort:** ~2 hours.

**Cost:** £0.

---

### B-106 — SyntaxWarning: invalid escape sequence `\d` in `export_dashboard_json.py` docstring
**Discovered:** 2026-06-04 (Sprint 25 deploy run)
**Severity:** P2 cosmetic. Line 2509 has `\d` inside a triple-quoted docstring (not a raw string), producing `SyntaxWarning: invalid escape sequence '\d'` on Python 3.12+. Harmless but noisy in pipeline output.
**Fix:** Change `\d{5,12}` to `\\d{5,12}` in that docstring line.
**Effort:** 2 minutes.

---

### B-043 — Non-cp1252 Unicode chars in pipeline `print()` statements — RESOLVED 2026-06-04
**RESOLVED 2026-06-04 (Sprint 27 audit):** Exhaustive grep of all `.scripts/*.py` files confirms zero `→ ✓ ✗ ⚠` in any `print()` call. Fixed in an earlier sprint; backlog entry was stale.
**Discovered:** 2026-05-21 (Sprint 4 — first refresh after shipping the pre-run backup pattern failed because `db_health.backup()`'s `[db_health] backup written → ...` print blew up under cp1252).
**Severity:** P2 hygiene. The immediate offender (`db_health.py`) was fixed on 2026-05-21 as a hotfix. This item covers the **remaining surface** — scripts that aren't currently called via subprocess but could trip the same trap if they ever get pulled into the pipeline.

**Evidence:**
- `.scripts/check_announced_at_coverage.py` lines 97, 101 — uses `→` in stdout `print()`.
- `.scripts/repair_pending_review.py` lines 144, 179 — uses `⚠`; line 183 — uses `✓`.

Both run interactively today, where Python uses the console codec (UTF-8 on modern Windows shells) and the chars print fine. If either gets wired into `refresh_all.py` STEPS or invoked via `subprocess.run(..., capture_output=True, text=True)`, Python falls back to the system locale codec (`cp1252` on Windows-en), the print raises `UnicodeEncodeError`, and any broad `except Exception` upstream silently returns False — which made Sprint 4's refresh report "Refresh failed".

**Suggested approach:**
1. Replace `→ ✓ ✗ ⚠` with ASCII equivalents (`->`, `[ok]`, `[fail]`, `[warn]`) in both files' `print()` statements.
2. Leave docstrings and comments alone — those never reach stdout.
3. While we're here, add a one-line note in `CLAUDE.md` under the FUSE section: "Pipeline scripts must avoid non-cp1252 Unicode in `print()` — see `feedback_avoid_non_cp1252_in_subprocess_prints.md`."

**Effort:** ~15 min. Trivial mechanical edits.

**Cost:** £0.

**Related memory:** `feedback_avoid_non_cp1252_in_subprocess_prints.md` (written 2026-05-21).

---

### B-059 — Company finder search box
**Discovered:** 2026-05-22 (Rupert request)
**Severity:** Usability. With 563 company pages, there is no way to navigate directly to a company without scrolling or knowing its URL. A search box would let users jump to any company by name or ticker in one step.

**Suggested approach:**
- Add a search input to `index.html` (header or Brewing panel — wherever it's most reachable). The 563 companies and their tickers are already in the exported JSON payload (`window.__data` or similar), so no extra data fetch is needed.
- JS on keyup: filter a pre-built list of `{ticker, company, url}` objects and surface the top N matches (e.g. 8) as a dropdown beneath the input. Match on ticker prefix first, then company name substring.
- Clicking a result navigates to the company's `company_{ticker}.html` page.
- No server-side change needed — purely client-side JS + a small HTML block.
- Build-side change: `export_dashboard_json.py` (or `build_dashboard.py`) needs to emit a `companies_index` list `[{ticker, company, url}, ...]` if not already present in the JSON payload.

**Files to touch:**
- `.scripts/dashboard/render_index.py` — add search input HTML + inline JS.
- `.scripts/export_dashboard_json.py` — emit `companies_index` array (if not already embedded).
- Optionally add the same search widget to `performance.html` header for cross-page reachability.

**Effort:** ~1 hour. Build-side addition is small; JS is straightforward.

**Cost:** £0.

---

## Sprint 51 — in flight (2026-06-06)

**B-134 — [P1/Urgent] Apply & publish aborts whole batch on duplicate 'add'** (DIR-57, agent:general-purpose)
Reported 2026-06-06: clicking Apply & publish errored on a staged Add-TX whose fingerprint
('378c518d1bbb6a44' / RKT / Marybeth Hays / BUY / 340) was already in the DB, rolling back all
~19 queued edits. Fix shipped (code): `apply_edits.py` raises non-fatal `AddTxAlreadyExists`,
`run()` skips+resolves the duplicate and keeps applying the batch. Test added. Awaiting Rupert's
Windows sweep + `server.py` restart.

**B-135 — [P1/Urgent] Apply & publish: FOREIGN KEY constraint failed** (DIR-58, agent:general-purpose)
Surfaced right after B-134 deployed. `signals` AND `paper_trades` both FK to
`transactions(fingerprint)` with no cascade (foreign_keys=ON). The reject / delete / update-key
paths cleared `signals` but not `paper_trades`, so rejecting a tx that had a paper_trade aborted
the batch. Fix shipped: `DELETE FROM paper_trades` added before the tx delete in all 3 paths +
regression test (`TestCascadeClearsPaperTrades`). Awaiting Rupert's Windows sweep + server restart.

**B-136 — [P1/Urgent] Apply & publish: KeyError '<rns>' on duplicate RNS** (DIR-59, agent:general-purpose)
Third apply error in the chain. 5 'add' edits shared RNS 9595811, so `remove_from_pending_queue`
got the id 5× and the 2nd `del items[r]` raised KeyError — post-commit, so the DB had already
committed but the run mislabelled it "rolled back" and skipped queue-clear + pipeline. Fix: dedupe
+ `items.pop(r, None)`. Test added. Re-run reconciles (adds skipped as dup via B-134). Follow-up
B-137: don't report "rolled back" after a successful commit.

**B-125 — [P2/High] Monthly Activity: remove corporate/PCA sell volume** (DIR-53) — code shipped.
`export_dashboard_json.build_monthly_buysell` now drops associated corporate holders / PCAs via
`_is_corporate_or_pca` (role_normalized=='PCA', PCA in role text, or corporate-name token).
Diff (snapshot): 187 rows / £385.7M sell + £120.5M buy removed; Mar excl-sell £200.8M, May £106.3M.
Excludes both buy & sell sides (flagged for Rupert — sell-only is a 1-line change). Awaiting deploy.

**B-124 — [P2/Med] Paper-book signal filter** (DIR-52) — code shipped. Row badges in the Live
paper book are now live filters (`data-paper-sid` + inline JS; click to filter, again to clear).
Awaiting export+build deploy.

**B-123 — [P2/Med] £10k strategy tracker vs FTSE** (DIR-51) — spec drafted at
`docs/specs/b-123-strategy-tracker-plan.md`; awaiting Rupert's approval (3 decision points) before code.

---

## Recently shipped (for context)

**Moved to the canonical ✅ Shipped log at the top of this file** (newest first). Record
new ships there, not here, so there's one history rather than two.

---

## How to use this file

- Bring it up at the start of each working session — "anything to clear off the backlog today?"
- New glitches go to the bottom of the relevant priority section with B-NNN auto-increment.
- When something ships, **add a row to the ✅ Shipped log at the top** (not a second list) — gives a rolling history of what's been done.
- Forward planning + sprint grouping lives in `docs/specs/sprint-plan-2026-06-03-sprints-20-onward.md`.
- Quarterly: review P3 items and either promote (still relevant) or close (no longer relevant).
