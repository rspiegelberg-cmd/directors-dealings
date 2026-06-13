# Quant research — Winners-vs-losers contrast (Part A) + Routine-vs-Opportunistic (Factor B1)

## ⚠️ RE-GRADE (cleaned corpus) — 2026-06-03 (second pass)

**Brief:** `docs/specs/quant-research-brief-02-winners-contrast-and-enrichment.md`
**Agent:** Quant Researcher · **Date:** 2026-06-03 · **Type:** exploratory / hypothesis-generation re-grade
**Scope this pass:** Part A (winners-vs-losers contrast on in-DB attributes) + **Factor B1 only**. **B2–B5 remain BLOCKED ON ENRICHMENT** — `ticker_fundamentals` / `results_dates` still don't exist and `market_cap_gbp` is empty for every ticker.
**Data:** `.data/_backtest_results.csv` **run `bt_20260603T191419Z`** (FRESH, 966 datable firings), joined by `fingerprint` to a `dd`-sequential copy of `directors.db.pre-strictness-20260603-185953.bak` (17:59, `integrity_check`=ok, 5,550 tx) — see Limitations on DB read path.

### What changed vs the first pass

Re-grade after 24 comp-event rows were demoted out of the signal layer.

- **Part A headline is unchanged: cluster breadth is the only attribute that separates winners from losers and replicates out-of-sample — in the "wrong" (crowded = worse) direction.** Top-decile (best) firings sit in narrow clusters (breadth ≈ 2.0–2.2), bottom-decile (worst) in crowded clusters (≈ 2.8–3.9). Holds full-sample and OOS at T+21; sign collapses/flips at T+90. Same as first pass.
- **Everything else is still noise:** AIM, £-value, first-time-buy, role/seniority and sector are equally present in winners and losers. Removing the comp events did not surface any new separating attribute.
- **B1 (routine vs opportunistic) is still NOISE — but the read is now *cleaner*, not outlier-propped.** This pass I classified routine by **coefficient-of-variation of inter-trade gaps** (a more standard regularity measure than the first pass's modal-calendar-month rule), which yields a larger routine cohort (90 test firings vs 29) and removes the dependence on a single +194% trade. Result: routine −1.46% vs opportunistic −1.58% (test T+21, gross) — **both negative, near-identical, neither carries positive edge.** The canonical Cohen-Malloy-Pomorski result (opportunistic carries the alpha) is **not reproduced**: opportunistic is not better than routine here. Verdict stays NOISE / cannot-confirm, now on firmer footing (no single-trade artifact).
- **Decile separation is slightly wider** post-cleanup (full T+21 top +17.6% / bottom −16.9%; OOS top +18.9% / bottom −16.4%) — the contrast is genuine.

**Bottom line for Rupert: cleaning the comp events did not change Brief 02 either.** The only stable, replicating signal is *negative* (crowded clusters → worse, already encoded as `b2_crowded_cluster_kill`). No positive "buy this" factor emerges; B1 cannot be confirmed in this corpus. Consistent with Brief 01.

---

## Headline answers (read these first)

1. **The one attribute that separates losers from winners and replicates OOS is cluster breadth — in the crowded = worse direction.** Bottom-decile firings sit in bigger clusters (mean ≈ 2.8–3.9 directors) than top-decile (≈ 2.0–2.2). Holds full-sample **and** in the held-out test half at T+21. **Crowded clusters predict underperformance** — opposite of the naive "more insiders agreeing = stronger," consistent with the `b2_crowded_cluster_kill` flag's design.

2. **Almost everything else is noise.** AIM status, £-value, first-time-buy, role/seniority and sector are equally present in winners and losers. Disclosure lag differs weakly and unstably (winners slightly slower-disclosed at T+21, reverses at T+90) — not a reliable factor.

3. **Factor B1 is NOISE / cannot-confirm.** With a cleaner CV-based routine classifier (no single-trade artifact this pass), routine and opportunistic cohorts are both negative and near-identical (−1.46% vs −1.58%, test T+21). The CMP "opportunistic carries the alpha" result does not appear. Not disproven — the corpus is a single down-regime window — but **not confirmed and not buildable.**

> **No factor beats the existing tiers as a positive organising principle.** The only stable, replicating signal is *negative* (crowded clusters → worse). Read alongside Brief 01 (no live tier shows positive OOS edge), the message holds: this window contains no clean positive "buy this" factor, but it does contain a directional "avoid the crowded cluster" caution.

---

## The discipline governing this brief

Describing winners is not finding edge. Every attribute below is measured on **both** the top decile and the bottom decile; a factor only counts if it **differs between them**, and any difference must then **replicate out-of-sample** (later 40% of firings). All six overfitting rules from `quant-researcher.md` apply.

---

## Cohort & split

- **Universe:** 966 distinct datable BUY firings (one row per `fingerprint`) from the fresh CSV, with gross CAR at T+21 and T+90.
- **Time split** (same as Brief 01): train = `fired_at` < `2025-11-19T12:20:07Z`, test = on/after. Test half = 387 firings (386 with T+21 CAR); 172 have a matured T+90.
- **DB join coverage:** **966 / 966 firings (100%)** matched to transaction attributes via the readable backup (cluster_id, director history). Sector + is_aim come from `tickers_meta` (sparse — see Limitations). `market_cap_gbp` populated for **0** tickers (→ B2 dead this pass).
- **Decile sizes:** full-sample T+21 decile = 96 each; T+90 decile = 75 each; test-half T+21 decile = 38 each.

---

## Part A — Winners-vs-losers contrast

### Full sample, ranked by CAR T+21 (decile N=96 each)
Top-decile mean CAR = **+17.6%**, bottom-decile = **−16.9%** (deciles genuinely far apart — contrast is meaningful).

| Attribute | Top decile | Bottom decile | Differs? | Note |
|---|---|---|---|---|
| AIM share | 4% | 1% | **N** | both almost entirely Main-Market |
| first_time_buy flag | 0% | 0% | **N** | column dead (all-zero) |
| in-cluster share | 58% | 71% | weak | losers slightly more clustered |
| **mean cluster breadth** | **2.22** | **3.94** | **Y** | losers in much bigger clusters |
| median transaction value | £19,996 | £20,845 | **N** | near-identical |
| mean disclosure lag (days) | 1.30 | 0.86 | weak | winners slightly slower-disclosed |
| role mix | mixed (T3/T4 sprinkle) | mixed | **N** | role_class sparse in CSV; same |
| sector mix | sparse | sparse | weak/N | small-N per sector |

### Full sample, ranked by CAR T+90 (decile N=75 each)
Top mean = **+40.4%**, bottom = **−38.5%**.

| Attribute | Top decile | Bottom decile | Differs? | Note |
|---|---|---|---|---|
| AIM share | 1% | 3% | N | both Main-Market |
| in-cluster share | 61% | 63% | N | flat |
| **mean cluster breadth** | **2.16** | **1.97** | **N (collapses)** | breadth no longer separates at T+90 |
| median transaction value | £17,558 | £18,700 | weak | losers slightly bigger £ |
| mean disclosure lag | 0.73 | 0.95 | weak | winners faster (opposite of T+21) |
| role mix | mixed | mixed | N | same |

### Out-of-sample confirmation — TEST half, CAR T+21 (decile N=38 each)
Top mean = **+18.9%**, bottom = **−16.4%**.

| Attribute | Top decile | Bottom decile | Differs? | Replicates full-sample? |
|---|---|---|---|---|
| AIM share | 3% | 3% | N | — |
| in-cluster share | 55% | 66% | **Y** | yes (losers more clustered) |
| **mean cluster breadth** | **2.00** | **2.79** | **Y** | **YES — strongest, most consistent factor** |
| median transaction value | £18,667 | £20,808 | weak | losers slightly bigger £ (weak) |
| mean disclosure lag | 2.32 | 1.00 | Y but noisy | winners slower-disclosed (matches T+21 full-sample) |
| role mix | mixed | mixed | N | same |

### Part A reading
- **Cluster breadth is the only attribute that differs AND replicates out-of-sample at T+21** — direction: **crowded clusters underperform.** Top-decile winners average 2.0–2.2 directors; bottom-decile losers 2.8–3.9. Robust full-sample and OOS at T+21.
- **At T+90 the breadth effect collapses** (2.16 vs 1.97 — no separation). So the breadth effect is **horizon-specific (a T+21 phenomenon)** and must not be over-claimed.
- **Disclosure lag** weakly suggests slower-disclosed buys do better at T+21 — economically odd, outlier-sensitive, reverses at T+90; **weak/unreliable hypothesis, not a factor.**
- **Everything the taxonomy leans on — role/seniority, £-value tiers, AIM, first-time-buy — does NOT separate the deciles.** Independently corroborates Brief 01: those tiers are descriptive labels, not winner/loser discriminators. **Unchanged by the comp-event cleanup.**

---

## Factor B1 — Routine vs Opportunistic directors

### Pre-registration
- **Feature:** classify each director from the regularity of their historical trade timing. **Rule this pass (changed for robustness):** a director is **ROUTINE** if they have **≥3 historical BUYs** AND the **coefficient of variation of their inter-trade gaps < 0.5** (regular, calendar-like spacing). Otherwise **OPPORTUNISTIC** (includes directors with <3 trades). Point-in-time-safe — uses only the director's own past timing, no price/return. *(The first pass used a modal-calendar-month rule that produced only 29 routine test firings and a single-trade artifact; the CV-of-gaps rule here is a more standard regularity measure and yields a larger, cleaner cohort — methodology note, not a result change.)*
- **Economic rationale:** Cohen, Malloy & Pomorski (*Decoding Inside Information*) — predictable-calendar insiders carry ≈0 alpha; opportunistic trades hold the predictive power.
- **Predicted direction:** **OPPORTUNISTIC buys should out-CAR routine buys.**
- **Hypotheses tested (Part A + B1):** ~8 in-DB attributes + 1 B-factor — wide surface; demand OOS replication + outlier-robustness.

### Result (gross CAR)
| Cohort | Half | Window | N | Mean | Median | Hit | Top-removed |
|---|---|---|---|---|---|---|---|
| Routine | TRAIN | T+21 | 165 | −0.57% | +0.64% | 52% | −0.81% |
| Opportunistic | TRAIN | T+21 | 414 | −1.16% | −1.71% | 38% | −1.25% |
| Routine | TEST | T+21 | 90 | **−1.46%** | −1.17% | 33% | −1.61% |
| Opportunistic | TEST | T+21 | 296 | **−1.58%** | −2.30% | 37% | −2.24% |
| Routine | TEST | T+90 | 44 | −2.00% | −3.49% | 39% | −2.59% |
| Opportunistic | TEST | T+90 | 128 | −2.38% | −6.89% | 29% | −4.06% |

### Reading
- **Both cohorts are negative and near-identical in the test half** (routine −1.46% vs opportunistic −1.58% at T+21). Routine is *marginally* less negative — the **opposite** of the pre-registered CMP direction, but the gap (0.12pp) is trivially small relative to the ~1.5pp standard error and routine's hit-rate is *lower* (33% vs 37%).
- **No single-trade artifact this pass.** Top-removed means barely move (routine −1.46%→−1.61%), unlike the first pass where +194% drove the whole routine mean. So the "cannot confirm" verdict now rests on a clean, larger cohort, not on outlier fragility.
- The CMP hypothesis — opportunistic > routine — is **not reproduced**; if anything the data leans the other way, but not significantly.

### Verdict: **NOISE / cannot-confirm**
Neither cohort carries positive edge; both lag their benchmark. The pre-registered direction does not appear. With a single down-regime window and effectively-negative base rates in both cohorts, **the CMP result cannot be tested here** — there is no positive signal in either bucket to differentiate. Reported as cannot-conclude, not as a finding in either direction. **Same verdict as the first pass, now on cleaner footing (larger cohort, no outlier dependence).**

### Recommendation
**Test-further, do not build.** B1 remains the highest-prior literature factor and worth revisiting once (a) the corpus spans more than one regime and (b) there is a positive base rate to split. The CV-of-gaps classifier above is sound and reusable. Do **not** promote B1 to a signal module on this pass.

---

## Does anything beat the existing tiers as an organising principle?

No positive factor does — unchanged by the cleanup. The only factor that *cleanly and repeatably separates* winners from losers OOS is **cluster breadth, negatively** — already encoded as `b2_crowded_cluster_kill`. The most defensible "new organising principle" is a **refinement of an existing suppressor**, not a new positive signal:

> **Hypothesis to carry forward (PRELIMINARY, not edge):** within cluster buys (S1), *narrow* clusters (1–2 directors) out-perform *crowded* clusters (3+) at T+21. This is a conditioning of S1, not a new signal_id — consistent with the agent's preference for improving a tier's conditioning over minting a new ID. Needs its own pre-registered, OOS-confirmed pass before any keep decision; loop in the Trader (crowded clusters may correlate with larger, more-liquid names where the cost model is least wrong).

---

## Limitations of this pass

1. **B2–B5 blocked on enrichment.** `ticker_fundamentals` / `results_dates` don't exist and `market_cap_gbp` is empty, so firm-size, fraction-of-company, book-to-market and results-proximity could not be tested — the literature's strongest UK factors (esp. firm size) remain untested by necessity.
2. **B1 is a single-regime read.** The whole corpus sits in a window where director buys lagged their benchmark, so both cohorts are negative and there is no positive base rate to split. The CMP hypothesis is *untested*, not disproven — re-run across a second, ideally up-regime, window.
3. **DB read path.** Live `directors.db` and its same-timestamp `.bak` (06:38) read "database disk image is malformed" via the FUSE mount this session. A `dd`-sequential copy of `directors.db.pre-strictness-20260603-185953.bak` (17:59, `integrity_check`=ok, 5,550 tx) was the freshest readable snapshot and is the source of the in-DB attributes. **Caveat:** this backup was taken *before* the 24 comp-event demotion, so its `buy_strictness` flags are pre-cleanup — but Part A/B1 use **transaction attributes (cluster_id, director, value, date, role)**, which the demotion did NOT touch, and the firing set is governed by the **fresh post-cleanup CSV**. The cleanup reclassified *signal eligibility*, not the underlying transaction rows joined here, so the attribute join is valid. Rupert should still run a Windows-side `PRAGMA integrity_check` on the live DB.
4. **Dead / sparse columns.** `first_time_buy` all-zero (F1 contrast row uninformative); `is_aim` set for only 10/784 tickers; `market_cap_gbp` empty; `role_class` sparsely populated in the CSV (most firings land in a '?' bucket, so the role-mix contrast is weaker than ideal). Flagged for the Data Integrity Auditor.
5. **Sector and T+90 deciles are small/unmatured.** Per-sector decile counts are single digits — sector "differences" not powered. T+90 deciles are unmatured and breadth collapses there — the breadth finding is a **T+21** result only.
6. **Read-only, no writes.** All analysis ran against a `dd` backup copy and a fresh CSV read. No write-path scripts run; no `.data/` writes.
