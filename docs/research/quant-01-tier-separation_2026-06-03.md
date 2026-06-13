# Quant research — Do the live signal tiers separate winners from losers out-of-sample?

## ⚠️ RE-GRADE (cleaned corpus) — 2026-06-03 (second pass)

**Brief:** `docs/specs/quant-research-brief-01-tier-separation.md`
**Agent:** Quant Researcher · **Date:** 2026-06-03 · **Type:** validation re-grade (no new features, no code/DB writes)
**Data:** `_backtest_results.csv` **run `bt_20260603T191419Z`** (FRESH, 1,942 firing rows / 966 datable events), read fresh. DB attributes (not needed for Brief 01) from the freshest *readable* backup — see note in the companion report.

### What changed vs the first pass

This is a re-grade after 24 STRICT_BUY rows that were actually **comp events** (deferred-bonus / DBP / scrip / ESPP / SIP) were reclassified to MIXED and demoted out of the signal layer; signals + backtest were re-run. The headline did **not** change. Specifics of what moved:

- **No verdict flipped.** Every signal that was NOISE or INSUFFICIENT-N before is NOISE or INSUFFICIENT-N now. Nothing crossed into EDGE or PRELIMINARY. **Zero validated positive edge — same conclusion as the first pass.**
- **Split date moved** from 2025-12-09 to **2025-11-19** (the firing set shrank/re-timed: 966 datable events vs 987; 60/40 split lands earlier). Test half = 387 events (was 393).
- **Baseline got slightly *more* negative**, not less: test-half net CAR T+21 is now **−2.55%** (was −1.85%), median −3.24%, hit 34%. Removing the comp events did **not** rescue the base rate — the average flagged buy still lags its own sector benchmark, by a touch more than before. So the contamination was not the cause of the negative base rate.
- **The four testable tiers (F1, S1, T3, T4) are unchanged in character:** all still sit at or below baseline. F1 +0.10% vs baseline (was +0.24%), S1 −0.49% (was −1.00%), T3 −0.26% (was −1.57%), T4 −0.86% (was +0.47%). Small wobbles, same NOISE verdict.
- **Ordering:** still does not support seniority. This pass the role groups are essentially flat-to-inverted in the test half (T1 −3.0%, T2 −3.5%, T3 −2.8%, T4 −3.4%) — no monotonic T1≥T2≥T3≥T4 gradient. The dramatic −15% T1a print from the first pass was partly comp-event noise and has moderated, but seniority still earns **no** premium.

**Bottom line for Rupert: cleaning the 24 comp events did not change the story.** No tier shows validated positive out-of-sample edge; the seniority ordering still does not hold; the base rate is still negative. The cleanup was correct hygiene, but the "no validated positive edge" conclusion stands — if anything the base rate is marginally worse, confirming the comp events were not what was dragging performance down.

---

## Headline answers (read these first)

1. **Which tiers survive out-of-sample? — None.** Of 13 live signals, only four have ≥30 matured firings in the test half at T+21 (F1 n=332, S1 n=237, T3 n=73, T4 n=30); the other nine are **INSUFFICIENT-N**. Not one of the four testable signals beats the baseline "average flagged buy." At T+90 only F1 (n=140) and S1 (n=100) are testable, and neither beats baseline. **Zero tiers show incremental, out-of-sample edge over a generic director buy.**

2. **Does the seniority ordering hold? — No.** The taxonomy asserts T1 ≥ T2 ≥ T3 ≥ T4 and that T0 is strongest. Observed test-half net CAR is flat-to-inverted: T1 −3.0%, T2 −3.5%, T3 −2.8%, T4 −3.4% — no monotonic gradient, and the senior tiers are small-N. T0 ("cluster combo", claimed best) prints −0.7% on just n=11. **No support for seniority paying.**

3. **Single most important keep/kill. — Do not refine the tiers yet; fix the base rate first.** The baseline is **negative**: the average flagged director buy returns **−2.55% net / −1.55% gross** vs its sector benchmark at T+21, hit-rate 34%, deepening to −3.3% net by T+90. Removing the comp events did not lift it. Refining tiers (Brief 02 features) on a base that is negative and non-separating out-of-sample is refining noise. First explain *why* the average flagged buy lags its benchmark — regime, universe selection, or residual data-timing — before any tier is trusted, re-tuned, or extended.

---

## Pre-registration (written before pulling CARs — unchanged from first pass)

| Tier | Economic claim | Predicted sign vs benchmark | Predicted rank |
|------|----------------|------------------------------|----------------|
| T1a/T1b (CEO/Founder, CFO) | Most-informed insiders; highest conviction | strongly positive | highest of role tiers |
| T2 (other exec) | Informed, slightly less than C-suite | positive | ≥ T3 |
| T3 (NED) | Less operational insight | mildly positive | ≥ T4 |
| T4 (other/catch-all) | Weakest role information | ~flat to mildly positive | lowest role tier |
| S1 (cluster buy) | Multiple insiders agreeing = stronger | positive | ≥ generic buy |
| T0 (cluster combo) | Cluster **and** senior role = best of both | strongly positive | **highest overall** |
| F1 (first-time buy) | New conviction > routine top-ups | positive | ≥ generic buy |

Null tested: each tier's test-half CAR is **positive, beats the generic-buy baseline, and respects the asserted ordering.**

**Hypotheses in this pass:** 13 signals × 2 windows = **26 comparisons.** Multiple-comparison haircut applied (see Robustness).

---

## Cohort & method

- **Universe:** 966 distinct datable firing events (one fingerprint can trigger several signals; CAR is identical per fingerprint, so the baseline dedupes to one row per fingerprint). 1,942 signal-rows in the CSV; 1 row has an empty `fired_at` and is dropped from the time split.
- **Effective date:** `fired_at` (the disclosure/firing timestamp).
- **Time split (60/40 by event):** train = events before **2025-11-19T12:20:07Z**, test = on/after. Firing range 2025-06-02 → 2026-03-19.

| Half | Events (T+21 avail) | Events (T+90 avail) |
|------|--------------------|--------------------|
| Train (<2025-11-19) | 579 | 578 |
| Test (≥2025-11-19) | 386 | **172** |

- **Baseline = pooled distinct firing events** ("the average system-flagged director buy"), judged on the same split and windows. (Same definitional choice and caveat as the first pass: the `prices` table is FUSE-unreadable this session, so I did not re-derive CARs for non-firing buys; the flagged-buy pool is the conservative comparator. Documented as a limitation.)
- **Cost model:** non-AIM = 100bps (50bps round-trip spread + 50bps stamp); AIM = 50bps — applied via the CSV's `net_car_*` columns, exactly as the brief specifies.

---

## Baseline profile (the comparator every tier must beat)

| | Train T+21 | Test T+21 | Train T+90 | Test T+90 |
|---|---|---|---|---|
| **Net CAR mean** | −1.99% | **−2.55%** | −5.65% | −3.28% |
| Median | −2.48% | −3.24% | −6.18% | −7.50% |
| Hit-rate | 38% | 34% | 33% | 27% |
| Mean, top removed | −2.1% | −3.05% | −5.8% | −4.53% |
| N | 579 | 386 | 578 | 172 |

Gross test T+21 = −1.55%. The baseline is **not outlier-driven** (top-removed barely moves it) and is negative vs each name's *own sector benchmark* — not simply "the market fell." **It is marginally more negative than the first pass (−2.55% vs −1.85% net), confirming the comp events were not propping up — or dragging down — the base rate.**

---

## Per-tier summary table — TEST half, T+21, net of cost

| Signal | Test N | Mean CAR T+21 (net) | Median | Hit-rate | vs baseline | Train mean | Verdict | Change vs 1st pass |
|--------|-------:|--------------------:|-------:|---------:|------------:|-----------:|---------|--------------------|
| F1 first-time buy | 332 | −2.45% | −3.11% | 35% | +0.10% | −1.77% | **NOISE** | unchanged |
| S1 cluster buy | 237 | −3.03% | −3.24% | 35% | −0.49% | −1.96% | **NOISE** | unchanged |
| T3 NED buy | 73 | −2.80% | −3.41% | 25% | −0.26% | −2.35% | **NOISE** | unchanged |
| T4 other buy | 30 | −3.41% | −3.30% | 27% | −0.86% | −0.83% | **NOISE** | unchanged |
| T2 exec buy | 16 | −3.51% | −0.85% | 44% | −0.96% | −0.62% | INSUFFICIENT-N | unchanged (was −7.0%; less extreme) |
| T5 PCA buy | 15 | −4.46% | −2.38% | 33% | −1.92% | +0.58% | INSUFFICIENT-N | flipped negative (was +0.72%) |
| T7 chair buy | 12 | −5.32% | −4.02% | 17% | −2.77% | −4.94% | INSUFFICIENT-N | unchanged |
| T0 cluster combo | 11 | −0.72% | −0.33% | 45% | +1.82% | −0.62% | INSUFFICIENT-N | much less negative (was −8.7%) |
| t1b CFO | 10 | −1.09% | +0.35% | 50% | +1.45% | −4.76% | INSUFFICIENT-N | improved (was −5.2%) |
| b1 lone conviction | 10 | −2.82% | −6.52% | 20% | −0.27% | −0.11% | INSUFFICIENT-N | improved (was −7.5%) |
| t1a CEO/founder | 9 | −5.21% | +0.05% | 56% | −2.67% | −1.03% | INSUFFICIENT-N | much improved (was −15.5%; comp-event noise removed) |
| b2 crowded-cluster kill | 7 | −9.37% | −8.45% | 0% | −6.83% | −4.29% | INSUFFICIENT-N | more negative |
| t6 company sec | 1 | +5.58% | +5.58% | 100% | +8.13% | +1.57% | INSUFFICIENT-N | n=1, meaningless |

*Baseline (test, T+21, net): N=386, mean −2.55%, median −3.24%, hit 34%.*

**At T+90 (net, test):** only F1 (N=140, −3.40%) and S1 (N=100, −4.27%) clear N≥30 — both **NOISE** (≈baseline −3.28% or below). Every other signal is INSUFFICIENT-N at T+90.

### Verdict key
- **EDGE** — positive, beats baseline, directionally consistent train→test, survives outlier removal, N≥30. *(Awarded to nothing this pass.)*
- **PRELIMINARY** — promising but fails one robustness leg. *(Nothing this pass.)*
- **NOISE** — N≥30 but no incremental edge over baseline.
- **INSUFFICIENT-N** — fewer than ~30 matured test-half firings; cannot be judged.

---

## Ordering check (does seniority pay?)

Pooled net CAR T+21, by role group:

| Group | Train | Test | N (test) |
|-------|------:|-----:|---------:|
| T1 (CEO/Founder + CFO) | −2.0% | −3.0% (med 0.0%) | 19 |
| T2 (Exec) | −0.6% | −3.5% (med −0.8%) | 16 |
| T3 (NED) | −2.3% | −2.8% (med −3.4%) | 73 |
| T4 (Other) | −0.8% | −3.4% (med −3.3%) | 30 |

The claimed order (T1 ≥ T2 ≥ T3 ≥ T4) is **not present** — the four groups are bunched between −2.8% and −3.5% in the test half with no seniority gradient. The first pass showed a clean *inversion* (T4 best, T1 worst at −10%); after removing comp events the inversion has **flattened into "no separation"** rather than reversing into the predicted order. T0 (asserted strongest) is −0.7% on n=11 — small-N, not a positive result. **No window rewards seniority.**

---

## Robustness

- **Outlier dependence:** baseline and the two large tiers barely move on top-removal — F1 −2.45% → −3.04%, S1 −3.03% → −3.18%, baseline −2.55% → −3.05%. The negative read is **central, not tail-driven.** (F1's max single trade is +192.8%, yet removing it makes F1 *more* negative — the positive tail is not what's holding the mean up.)
- **Concentration:** the 966 events are far from independent — **PRU appears 127×, BATS 75×, MTO 63×, RR 54×, SGE 53×, MSLH 49×.** Effective N is well below headline N; every CI is wider than the counts imply.
- **Regime:** CAR subtracts a sector-matched benchmark, so uniformly negative CAR is names lagging *their own sector index*, not "the market fell." Holds across train and test.
- **Multiple comparisons:** 26 looks. No tier clears even an uncorrected bar over baseline at N≥30, so the haircut is academic — it would only have killed lone positives that don't exist this pass.

---

## Verdict

**NOISE / INSUFFICIENT-N across the board — same as the first pass.** No live tier demonstrates out-of-sample edge over a generic flagged buy at T+21 or T+90. The seniority ordering is unsupported (now flat rather than inverted). The baseline test-half edge is −2.55% ± ~1.4% (2 s.e.); the four sufficient-N tiers (F1, S1, T3, T4) sit within or below that band. **Clean negative result. Removing the 24 comp events did not change any verdict, the ordering conclusion, or the headline.**

## Recommendation

- **Keep the data pipeline; do not trust the tiers as alpha.** Treat all role tiers (T1–T7), S1, T0 and F1 as **descriptive labels, not validated edge.**
- **Do not re-tune thresholds yet** — re-tuning a tier whose parent population is negative just fits the negative sample harder.
- **Before Brief 02 features are built**, run a focused diagnostic on the negative baseline: (a) is it a 2025-26 UK regime where insider-bought names lagged cap-weighted sector indices; (b) is it universe selection (corpus skews to PRU/BATS/MTO/RR mega-caps, and to Main-Market names); (c) residual entry-timing error (see Limitations). Loop in the **Trader** on (a)/(b) and the **Data Integrity Auditor** on (c). **The comp-event cleanup has now ruled out "contamination" as the base-rate cause — the negative base rate is real and needs (a)/(b)/(c) explained.**
- **If forced to simplify now**, the only data-supported move is to *collapse* the role tiers into one "director buy" flag — they do not separate — not to add granularity.

## If promote-to-spec
Nothing is promoted. This is a validation brief; the correct output is "do not build on this base yet."

---

## Limitations of this pass (always present)

1. **T+90 barely testable OOS.** Only 172 test-half events have matured to T+90; per-tier only F1/S1 reach N≥30. T+90 verdicts for 11 of 13 signals are unknowable until more time passes — re-run in ~3 months.
2. **Small, concentrated corpus.** ~966 events, but PRU/BATS/MTO/RR dominate; effective independent N is much lower. Every CI is wider than the counts suggest.
3. **Baseline is the pooled-firings set, not literally "every BUY."** `prices` was FUSE-unreadable this session, so I did not re-derive CARs for non-firing buys (would risk divergence from `backtest.py`). Conservative comparator; a true all-BUY baseline (built Windows-side) could shift it slightly.
4. **Data-quality flags (hand to Data Integrity Auditor):** (a) `transactions.first_time_buy` is **0 for all 5,550 rows** in the readable backup — column is dead; F1 computes first-time status elsewhere. (b) `tickers_meta.is_aim` set for only **10 of 784** tickers — almost certainly under-populated; if true-AIM names are mislabelled non-AIM, the 0.5% stamp is over-applied and net CARs are *slightly too negative* for those names (does not flip the sign). (c) `tickers_meta.market_cap_gbp` empty for all tickers — "value as % of market cap" (a lead Brief-02 idea) still cannot be tested.
5. **Entry-timing caveat.** `MEMORY.md` records an `announced_at`/MTM bug and parser issues partially fixed in this corpus. Raw returns look sane (symmetric tails), so gross entry pricing is not obviously broken, but a residual timing error cannot be fully excluded.
6. **DB read path.** Live `directors.db` and the most-recent `.bak` (06:38) read "malformed" via the FUSE mount this session; a `dd` (sequential) copy of `directors.db.pre-strictness-20260603-185953.bak` (17:59, integrity ok, 5,550 tx) was the freshest readable snapshot — but **Brief 01 needs only the CSV, which is the fresh post-cleanup run `bt_20260603T191419Z` (19:14), so the DB read issue does not affect any Brief-01 number.** Read-only; no write-path scripts executed.
