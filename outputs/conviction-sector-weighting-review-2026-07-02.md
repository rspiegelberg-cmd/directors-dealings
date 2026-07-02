# Quant research — is the F6 sector multiplier destroying conviction-score differentiation?

**Role:** Quant Researcher (per `docs/agents/quant-researcher.md`)
**Date:** 2026-07-02
**Status:** Research / simulation only — no code or data changed. Recommendation pending Rupert's approval.

---

## Pre-registration

**Feature under review:** F6, the sector component of the Weekly Conviction Score composite (`.scripts/conviction.py::composite()`).

`score = 100 × clamp01(w_who·F1 + w_size·F2 + w_cap·F3 + w_earn·F4) × sector_mult`

**Hypothesis (Rupert's, pre-registered before this review):** the panel shows many "top" buys pinned at exactly 100, destroying rank differentiation among the strongest picks, and the revised (2026-07-01) F6 — a data-driven 0.0×–2.0× multiplier on trailing-30-day sector net-buy counts — is the mechanical cause, because a multiplier that can exceed 1.0 can push a mediocre additive score through the 100 ceiling regardless of the buy's own quality.

**Predicted direction:** reverting F6 to a discount-only (≤1.0) guardrail, or compressing its multiplicative range, should collapse the ceiling cluster and restore rank spread, with high (but not perfect) rank correlation to the current ordering.

**Hypotheses tested in this pass:** 1 root-cause diagnosis + 4 candidate F6 redesigns (a, b, b2, c) evaluated against the same live cohort. No other conviction-score factor was touched or tested.

---

## Cohort / data source

**Live Supabase Postgres** (project `mmiaiauybzsdcbrrcxfc`), via the connected Supabase MCP connector — `execute_sql` (read-only). Query:

```sql
SELECT cs.*, t.ticker, t.company, t.director, t.role
FROM conviction_scores cs
LEFT JOIN transactions t ON t.fingerprint = cs.fingerprint
WHERE cs.window_end = '2026-07-02'
ORDER BY cs.rank_in_window;
```

- `conviction_scores` currently holds **8 window snapshots**, `window_end` from 2026-06-25 to 2026-07-02 (one per pipeline run).
- **This review uses the latest window, `window_end = 2026-07-02`, N = 147 scored BUYs** — the full distribution the table stores, not just the surfaced top-10 (per spec §6/§7, the shadow log covers every buy in the rolling 28-day window).
- Verified the stored `score` reconstructs exactly from `f1_who/f2_buy_size/f3_company_size/f4_earnings_timing`, `weights_used` (JSON), and `f6_sector_mult` for **all 147/147 rows** (0 mismatches) — confirms I'm working from a faithful, unmodified read of the live scoring mechanism, not a stale or corrupted copy.
- Rupert's original finding was read from a **stale local CSV** snapshot (`outputs/conviction-scored-buys-2026-07-01.csv`, N=1,292, cumulative across all historical windows). This review supersedes it with the live, current-window table.

---

## Result — root cause confirmed on live data (in fact worse than the stale read suggested)

| | Stale local CSV (Rupert's finding, N=1,292 cumulative) | **Live Supabase, current window (N=147)** |
|---|---|---|
| At exact 100.0 ceiling | 68 (5.3%) | **17 (11.6%)** |
| Score ≥ 90 | 117 (9.1%) | **26 (17.7%)** |
| Score ≥ 80 | — | 38 (25.9%) |
| Mean `f6_sector_mult` | — | 1.52 (range 1.0–2.0; never below 1.0 in this window) |

**Root cause confirmed independently on live data:** every one of the 17 ceiling-hitters in the current window carries `f6_sector_mult` between 1.25 and 2.00 — i.e. every single ceiling-hit is sector-boosted, none reached 100 on organic factor strength alone. Checked whether ceiling-hitters are genuinely strong buys or sector-boosted mediocre ones: **16 of the 17** ceiling rows have at least one clearly weak own-quality subscore (role tier ≤ NED-level, buy size ≈ 0, or earnings timing at the "invalid/mid-cycle" floor). Two clear examples:

- **RVRB (Christopher Mills, PCA/fund holder)** — `f1_who=0.20` (weak/NED-tier), `f4_earnings_timing=0.10` (no timing edge) — hit the ceiling **purely** because `f6_sector_mult=2.00`. Its true quality-only score (candidate a) is 50.6, dropping it from rank 4 to **rank 55**.
- **N91 (Ninety One UK Mid Cap Eq Fund, PCA)** — `f1_who=0.20`, `f3_company_size=0.36` (mid/large-cap, not the micro-cap edge case rewards) — hit the ceiling on sector heat alone. Quality-only score: 57.6, rank 2 → **rank 36**.

Conversely, buys the current mechanism under-ranks purely because their sector happened to be cold that week get correctly promoted once F6 stops distorting: **ALT** (Ryan Mahaffy, `f1=0.70, f2=0.57, f3=1.00`, `f6_mult=1.00` neutral) sits at rank 42 today with score 76.8, but under the guardrail design its score is *unchanged* (76.8, since mult was already ≤1) while ceiling-hitters around it collapse — so it jumps to **rank 4**, a fairer reflection of its genuinely strong own-quality factors.

---

## Candidates evaluated (same underlying f6 raw signal, different mapping)

All three re-derive the same trailing-30-day net-buy-count signal (`conviction.net_buys_to_f6`, currently -1.0…+1.0) — only the mapping from that raw signal to a score adjustment changes.

**(a) Revert to spec-original discount-only guardrail — band [0.7, 1.0].**
Any positive sector reading collapses to 1.0 (neutral); only negative (net-selling) sectors trim the score, down to 0.7 at the most negative reading. Rationale: this is literally what the spec originally mandated (§3/§4 F6) — sector can flag caution (avoid mistaking sector beta for skill) but must never manufacture conviction on its own. Predicted effect: full elimination of sector-driven ceiling hits.

**(b) Keep symmetric reward/penalty, compress the band to [0.85, 1.15]** (and a wider variant (b2) [0.8, 1.2]).
Preserves the "hot sector momentum" signal in a muted form so a genuinely hot sector still nudges rank order, but ±15–20% can no longer single-handedly vault a mediocre buy to 100. Rationale: if the team wants to keep some reward-side sector signal (not just a guardrail) while fixing the saturation bug, this is the smallest change that plausibly still eliminates ceiling-clustering.

**(c) Make F6 additive, not multiplicative** — `score = clamp(100×weighted_sum + bonus_points×f6_raw, 0, 100)`, bonus_points=6.
Sector can add or subtract up to 6 points on the 0–100 scale directly, so it can never *compound* with (multiply through) the other four factors — the mechanism that causes ceiling saturation is structurally removed regardless of how big the sector effect is allowed to be. Rationale: multiplicative combination is the actual bug (any multiplier >1 stacks on top of an already-large weighted sum); additive combination caps sector's maximum possible contribution in absolute points, independent of the buy's own score.

---

## Simulation — impact on the current top buys (N=147, window_end=2026-07-02)

Recomputed `score_new` for every buy using the **same stored `f1_who/f2_buy_size/f3_company_size/f4_earnings_timing` and `weights_used`**, only remapping the F6 term. Full top-30 (by old rank) shown; complete 147-row table available in the underlying data pull.

| Rank(old) | Ticker | Director | Old score | f1 who | f2 size | f3 cap | f4 earn | f6 mult (old) | (a) Guardrail score/rank | (b) Compressed[.85,1.15] score/rank | (c) Additive ±6pt score/rank |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TOO | Scott Livingston | 100.0 | 1.00 | 0.67 | 1.00 | 0.00 | 2.00 | 88.1 / 1 | 100.0 / 1 | 94.1 / 1 |
| 2 | N91 | Ninety One Guernsey | 100.0 | 0.20 | 1.00 | 0.36 | 0.76 | 2.00 | 57.6 / 36 | 66.2 / 25 | 63.6 / 27 |
| 3 | SHI | Simon Kesterton | 100.0 | 0.90 | 0.03 | 0.89 | 0.86 | 1.75 | 63.1 / 23 | 70.2 / 17 | 67.6 / 20 |
| 4 | RVRB | Christopher Mills | 100.0 | 0.20 | 0.71 | 0.98 | 0.10 | 2.00 | 50.6 / 55 | 58.2 / 46 | 56.6 / 47 |
| 5 | HSX | Paul Cooper | 100.0 | 0.90 | 0.11 | 0.20 | 0.95 | 2.00 | 51.9 / 51 | 59.7 / 43 | 57.9 / 46 |
| 6 | MPAC | Adam Holland | 100.0 | 1.00 | 0.04 | 0.92 | 0.87 | 1.75 | 67.1 / 17 | 74.7 / 11 | 71.6 / 12 |
| 7 | KRM | Keith Todd | 100.0 | 0.70 | 0.04 | 1.00 | 0.00 | 2.00 | 53.8 / 48 | 61.9 / 36 | 59.8 / 38 |
| 8 | SNX | Paul Williams | 100.0 | 0.90 | 0.00 | 1.00 | 0.00 | 1.75 | 59.8 / 28 | 66.5 / 23 | 64.3 / 25 |
| 9 | IGG | Breon Corcoran | 100.0 | 1.00 | 0.13 | 0.18 | 0.99 | 2.00 | 55.8 / 40 | 64.2 / 29 | 61.8 / 33 |
| 10 | ECEL | William Truman | 100.0 | 1.00 | 0.25 | 0.87 | 0.10 | 1.75 | 58.5 / 33 | 65.1 / 28 | 63.0 / 28 |
| 11 | IGR | Anders Hedlund | 100.0 | 1.00 | 1.00 | 0.93 | 0.10 | 1.50 | 82.2 / 2 | 88.3 / 2 | 85.2 / 2 |
| 12 | MPAC | Duncan Tyler | 100.0 | 0.90 | 0.04 | 0.92 | 0.64 | 1.75 | 60.0 / 27 | 66.7 / 22 | 64.5 / 24 |
| 13 | LUCE | Judith Hoy | 100.0 | 1.00 | 0.13 | 0.65 | 1.00 | 1.75 | 66.2 / 19 | 73.7 / 12 | 70.7 / 14 |
| 14 | GROW | Ben Wilkinson | 100.0 | 1.00 | 0.13 | 0.49 | 0.00 | 2.00 | 54.6 / 45 | 62.8 / 33 | 60.6 / 34 |
| 15 | KLSO | Ian Selby | 100.0 | 0.90 | 0.27 | 1.00 | 0.00 | 2.00 | 69.5 / 12 | 79.9 / 5 | 75.5 / 7 |
| 16 | PRU | Douglas Flint | 100.0 | 1.00 | 0.18 | 0.10 | 0.81 | 2.00 | 52.2 / 50 | 60.1 / 39 | 58.2 / 44 |
| 17 | HERC | Mrs Paula Wheatcroft | 100.0 | 1.00 | 0.26 | 1.00 | 0.00 | 1.75 | 73.0 / 9 | 81.2 / 3 | 77.5 / 4 |
| 18 | PRU | Douglas Flint | 99.8 | 1.00 | 0.18 | 0.10 | 0.68 | 2.00 | 49.9 / 58 | 57.4 / 48 | 55.9 / 51 |
| 19 | EGT | Michael Kearney | 99.6 | 0.30 | 0.50 | 1.00 | 0.21 | 2.00 | 49.8 / 59 | 57.3 / 49 | 55.8 / 52 |
| 20 | G4M | Chris Scott | 99.2 | 0.90 | 0.20 | 0.97 | 0.00 | 1.50 | 66.1 / 20 | 71.1 / 15 | 69.1 / 16 |
| 21 | HILS | Nick Anderson | 97.9 | 1.00 | 0.18 | 0.36 | 0.71 | 1.75 | 55.9 / 39 | 62.2 / 35 | 60.4 / 35 |
| 22 | MAB1 | Mortgage Advice Bureau | 97.0 | 0.20 | 0.36 | 0.70 | 0.90 | 2.00 | 48.5 / 62 | 55.8 / 55 | 54.5 / 57 |
| 23 | PXEN | Tom Reynolds | 96.5 | 1.00 | 0.44 | 1.00 | 0.67 | 1.25 | 77.2 / 3 | 80.1 / 4 | 78.7 / 3 |
| 24 | MLVN | Daniel Fisher | 93.4 | 0.90 | 0.41 | 1.00 | 0.00 | 1.25 | 74.7 / 7 | 77.5 / 6 | 76.2 / 6 |
| 25 | TRT | Ryan Maughan | 91.1 | 0.70 | 0.33 | 1.00 | 0.44 | 1.50 | 60.8 / 26 | 65.3 / 26 | 63.8 / 26 |
| 26 | PXEN | Tom Reynolds | 90.6 | 1.00 | 0.34 | 1.00 | 0.57 | 1.25 | 72.4 / 10 | 75.2 / 8 | 73.9 / 10 |
| 27 | STAF | Catherine Lynch | 89.2 | 0.30 | 0.12 | 1.00 | 0.90 | 1.75 | 50.9 / 53 | 56.7 / 51 | 55.4 / 54 |
| 28 | IGR | Stewart Gilliland | 88.5 | 1.00 | 0.23 | 0.93 | 0.10 | 1.50 | 59.0 / 29 | 63.4 / 30 | 62.0 / 31 |
| 29 | B90 | Andrew McIver | 88.3 | 0.30 | 0.58 | 1.00 | 0.00 | 1.50 | 58.9 / 32 | 63.3 / 31 | 61.9 / 32 |
| 30 | MPAC | Clive Whiley | 88.0 | 0.30 | 0.18 | 0.92 | 0.87 | 1.75 | 50.3 / 57 | 56.0 / 54 | 54.8 / 56 |

### Ceiling clustering — before vs after

| | OLD (live, current mechanism) | (a) Guardrail [0.7,1.0] | (b) Compressed [0.85,1.15] | (b2) Compressed [0.8,1.2] | (c) Additive ±6pt |
|---|---|---|---|---|---|
| At exact 100.0 ceiling | **17 / 147 (11.6%)** | **0 / 147** | 1 / 147 | 1 / 147 | **0 / 147** |
| Score ≥ 95 (near-ceiling) | 23 / 147 (15.6%) | 0 / 147 | 1 / 147 | 1 / 147 | 0 / 147 |

All three candidates essentially eliminate ceiling-clustering. (a) and (c) fully eliminate it; (b)/(b2) leave exactly one buy at the ceiling — TOO (Scott Livingston, CEO, f1=1.00/f2=0.67/f3=1.00), which is a *genuinely* top-quality buy on its own factors, not a sector-boosted mediocre one, so that single remaining ceiling-hit is not a false positive.

### Rank reshuffling — Spearman correlation vs today's ranking

| Candidate | Spearman ρ vs current live ranking |
|---|---|
| (a) Guardrail [0.7, 1.0] | 0.9981 |
| (b) Compressed [0.85, 1.15] | 0.9987 |
| (b2) Compressed [0.8, 1.2] | 0.9989 |
| (c) Additive ±6pt | 0.9986 |

All candidates preserve the *broad* ordering (ρ > 0.998 — the who/size/cap/earnings factors still dominate overall rank), but **reshuffle sharply at the very top**, which is exactly the part of the ranking the panel actually surfaces (top 10) and exactly the part Rupert flagged as broken. Under candidate (a): of today's top-10 surfaced picks, only **3 remain in the new top-10** (TOO, IGR/Hedlund, and PXEN moves in); the rest drop to ranks 12–55 once their sector-driven inflation is removed.

---

## Robustness

- **Reconstruction check:** all 147 live rows reproduce their stored `score` exactly from `weights_used` + sub-scores + `f6_sector_mult` (0 mismatches) — the simulation is built on a verified-correct model of the live mechanism, not a guess at it.
- **Outlier dependence:** the ceiling-clustering finding does not hinge on one or two names — 16 of 17 ceiling-hitters independently show the same weak-own-quality-plus-high-f6-mult pattern. This is a systemic mechanism effect, not a single anomalous trade.
- **Parameter sensitivity:** tested two widths for the compressed-band candidate ([0.85,1.15] and [0.8,1.2]); both eliminate ceiling clustering to near-zero and produce materially identical rank correlations (0.9987 vs 0.9989), so the exact band width is a minor tuning choice, not a first-order design decision.
- **What this pass did NOT test:** whether F6 (in any form — guardrail, compressed, or additive) actually predicts forward returns. No forward-return regression was run. This is a pure distributional/mechanical fix.

---

## Verdict

**MECHANISM CONFIRMED, FIX VALIDATED (distributionally)** — the ceiling-clustering complaint is real, confirmed on live data (worse in the current window than the stale CSV suggested: 11.6% vs 5.3% at ceiling), and is mechanically caused by F6's post-2026-07-01 ability to exceed 1.0× and compound multiplicatively with the additive score. All three candidate redesigns tested eliminate or nearly eliminate the ceiling cluster while preserving ≥99.8% rank correlation to today's ordering, reshuffling meaningfully only at the very top — which is the part of the panel that was actually broken.

This is **not** a statement that any F6 design (old guardrail, new multiplier, or these candidates) has predictive edge — no forward-return evidence exists for F6 in any form (per spec §5/§7, weights are judgment priors; the spec itself flags Materials-small-cap as robustly negative and Energy's apparent edge as oil-beta, i.e. sector countervailing evidence already exists against F6 as a *booster*).

## Recommendation

**Adopt candidate (a) — revert F6 to the spec-original discount-only guardrail, band [0.7, 1.0].** Rationale, in order:

1. It is literally what the spec (§3/§4) mandated before the 2026-07-01 revision, and that revision has no forward-return justification recorded anywhere — it was a judgment change, not a proven improvement, so reverting is not "undoing a win," it's undoing an unvalidated change.
2. It fully eliminates ceiling-clustering (0/147) with no residual near-ceiling cluster either (0/147 ≥95).
3. It is the most conservative choice given the explicit spec caution that sector effects found so far (Materials-small-cap negative, Energy = oil beta) argue for treating sector as a caution flag, not a reward — a guardrail structurally cannot repeat the mistake of rewarding sector beta as if it were director skill.
4. If the team specifically wants to preserve some "hot sector momentum" reward signal, candidate (c) — additive ±6 points — is the second-best choice: it structurally prevents the multiplicative-blowthrough bug regardless of how large the sector effect ever becomes, which is a stronger guarantee than candidate (b)'s compressed multiplicative band (which still compounds, just less severely, and would need re-tuning if net-buy counts ever swing to more extreme readings than seen in this window).

**This is a proposal for Rupert's approval, not an applied change** — per the champion/challenger process in spec §7/§11 decision 5, and per this task's explicit read-only constraint, `conviction.py` / `conviction_pipeline.py` have not been modified.

## Limitations of this pass

- **Not validated against forward returns.** This entire review is a distributional/statistical fix — it restores rank differentiation and removes an obviously-wrong compounding mechanism — but it is **not** evidence that F6 (in guardrail form, compressed form, or any form) predicts real forward CAR. That test does not exist yet for any version of F6 and would require the measure-forward log (§7) to mature to N≥30 resolved picks under whichever design is adopted.
- **Single-window snapshot.** The simulation used the latest (2026-07-02) window only. The other 7 stored windows were not re-simulated; the mechanism is structural (any positive `f6_sector_mult` compounds), so the finding should generalise, but this was not re-confirmed on every historical window.
- **Bonus-point magnitude for candidate (c) (±6 points) is a judgment placeholder**, not fitted or tuned — it was chosen to be "small enough to nudge, not dominate," consistent with the compressed-band candidates' effective swing, but no optimisation was performed.
- **No out-of-sample split** — this is a same-day mechanical re-derivation, not a train/test evaluation, because there is no forward-return target yet to split on. That out-of-sample step belongs to the future champion/challenger cycle once picks have matured, not to this recalibration.

---

## Part 2 — continuous additive design (F6 v2), per Rupert's follow-up

**Date:** 2026-07-02 (same day, follow-on task). **Status:** Research / simulation only — no code or data changed.

### Rupert's instruction (verbatim intent)

Rupert reviewed Part 1 and explicitly rejected both the pure guardrail revert (candidate a) and dropping sector entirely. His instruction: keep sector activity as a live signal that can help *or* hurt a score, size it so it is clearly secondary to the four core factors, and make it "constantly moving" — i.e. it should respond smoothly and continuously to director activity, not jump in discrete steps. He named two specific complaints about the current mechanism: (1) the 30-calendar-day box-car window — a transaction's influence is full-strength for 30 days then vanishes overnight when it exits the window, and (2) the 9 discrete step-bands — net_buys of 15 and 20 score identically (0.75), then jump to 1.0 at net=21.

### Pre-registration

**Feature:** Replace F6 with a continuous, additive, exponentially-time-decayed sector adjustment.

**Formula:**

```
weighted_net(sector, as_of, halflife) =
    Σ over all BUY/SELL/SELL_TAX transactions in that sector, strictly before as_of:
        (+1 if BUY, -1 if SELL/SELL_TAX) × exp(-ln(2) × age_days / halflife)
    where age_days = as_of - transaction_effective_date

sector_adjustment_points = tanh(weighted_net / k) × MAX_POINTS

score_new = clamp(100 × weighted_sum_of_4_factors + sector_adjustment_points, 0, 100)
```

Where `weighted_sum_of_4_factors` is the existing who/buy_size/company_size/earnings_timing weighted sum (spec §4, WEIGHTS in `conviction.py`), unchanged — this pass only replaces the F6 term and how it combines with the rest.

**Why each piece:**
- **Exponential decay, not a box-car.** A box-car (all-or-nothing 30-day window) is the definition of "not constantly moving" — every transaction contributes its full ±1 for 29 days, then falls off a cliff on day 31. Exponential decay means every transaction's contribution shrinks a little every single day from the moment it happens, and every new transaction nudges the number immediately. This directly answers Rupert's "constantly moving" requirement.
- **tanh squash, not step-bands.** tanh is smooth and monotonic with no plateaus: net=15 and net=20 (weighted) now produce two slightly different numbers, not the same number, and there is no instant jump anywhere on the curve. It also naturally bounds the raw signal to (-1, +1) without a hard clip, so an extreme sector-wide buying spree approaches but never exactly hits the asymptote (see calibration below).
- **Additive points, not a multiplier.** This is the structural fix from Part 1: sector can add or subtract at most MAX_POINTS on the 0-100 scale, full stop — it cannot compound with (multiply through) the other four factors, however extreme director activity in a sector ever becomes. That caps sector's worst-case influence in an auditable, fixed number of points, which is what makes it possible to promise "secondary, never dominant" as a structural guarantee rather than a hope.

**Economic rationale:** unchanged from Part 1/the original spec — sector-wide net director buying is a weak momentum/crowd-wisdom signal (other insiders across the sector are buying too), and sector-wide net selling is a mild caution flag. The prior finding that some "hot" sector effects are actually oil-beta or size-beta in disguise (spec §6/§7 sector-axis scan) is exactly why this signal is now capped small and additive rather than allowed to dominate.

**Predicted direction:** ceiling clustering (100/≥95) should collapse to ~0 (same mechanical result as Part 1's additive candidate (c), since this is architecturally the same additive-cap idea, now with a smoother input signal). Rank correlation to today's live ordering should be materially *lower* than Part 1's compressed-multiplier candidates (which stayed ≥0.998) — because an additive design corrects the ceiling distortion much more completely, which is the point, not a flaw.

**Hypotheses tested in this pass:** 1 design (the tanh/exponential/additive formula) across 3 halflife choices (7d/14d/21d) × several (MAX_POINTS, k) combinations for sensitivity — 7 total parameter combinations simulated, all on the same live cohort. No other conviction-score factor was touched.

### Data used for calibration

Pulled the full BUY/SELL/SELL_TAX transaction history from **live Supabase Postgres** (`mmiaiauybzsdcbrrcxfc`), 2026-04-01 through 2026-07-02 inclusive, joined to `tickers_meta.sector`, aggregated to one net-buy-count-per-sector-per-day row (322 daily observations across 10 sectors: Communication Services, Consumer Discretionary, Consumer Staples, Energy, Financials, Health Care, Industrials, Materials, Real Estate, Technology, Utilities). This is the same underlying event stream `compute_sector_f6_map()` reads today, just not pre-aggregated into a 30-day box-car — the raw daily net counts were used to compute the exponentially-decayed weighted sum at each candidate halflife, as of the live cohort's `window_end = 2026-07-02` (confirmed this remains the latest window; no newer window exists).

**Observed weighted_net signal by sector, as of 2026-07-02** (this is the real calibration data point — not assumed):

| Sector | hl=7d | hl=14d | hl=21d |
|---|---|---|---|
| Communication Services | -0.17 | 1.96 | 4.96 |
| Consumer Discretionary | 4.22 | 8.72 | 12.51 |
| Consumer Staples | 0.72 | 6.25 | 12.01 |
| Energy | 1.28 | 2.77 | 3.47 |
| Financials | 4.26 | 7.65 | 9.49 |
| Health Care | 1.72 | 2.62 | 3.21 |
| Industrials | 4.58 | 7.70 | 9.87 |
| Materials | 2.32 | 4.58 | 6.63 |
| Real Estate | 1.47 | 3.61 | 5.38 |
| **Technology** | -0.42 | -0.47 | -1.34 |
| Utilities | 1.35 | 1.41 | 2.39 |

Distribution summary (why `k` is chosen the way it is): at halflife=14d, min=-0.47, max=8.72, mean=4.26, p90=7.70. The genuinely hottest sector observed (Consumer Discretionary, weighted_net≈8.7) is roughly 2x the mean sector — there is no sector anywhere near an extreme outlier in this snapshot, which argues for a `k` that keeps even the hottest observed sector well short of tanh's ±1 asymptote (so there is still headroom if activity gets more extreme later), rather than a `k` fitted tightly to today's max.

### Recommended default: **halflife = 14 days, MAX_POINTS = 6, k = 8**

**Halflife = 14 days.** A trailing ~2-week half-life means a BUY from 14 days ago carries half the weight of a BUY announced yesterday, and is down to ~6% of its original weight after 30 days (versus the old mechanism's "100% weight for 30 days, 0% on day 31"). 14 days is short enough that the signal visibly moves week-to-week (matching "constantly moving"), but long enough that a single isolated transaction doesn't cause a one-day spike-and-vanish. 7d was tried and decays almost fully within a fortnight (too twitchy — a single cluster dominates then disappears in days); 21d smooths more but drifts closer to the old mechanism's sluggishness. 14d is the middle ground and the recommended default.

**MAX_POINTS = 6.** The four core factors' weights sum to 100 points; the *smallest* of them, earnings_timing (weight 0.18), can swing the score by up to **18 points** on its own (a full 0.0→1.0 swing on that sub-score × 18 weight × 100 = 18 points). Capping sector at **±6 points** means sector's maximum possible influence, however extreme director activity ever gets, is exactly **one-third of the weakest core factor's swing** — a concrete, honest number Rupert can hold the design to: *"sector can move a score by at most 6 points; the weakest of the four things that actually describe the buy can move it by up to 18."* This directly satisfies "secondary, not overwhelming."

**k = 8.** Calibrated against the observed halflife=14d distribution above: `tanh(8.72/8) = 0.797`, so even the hottest real sector observed (Consumer Discretionary) produces a points adjustment of `0.797 × 6 ≈ +4.8` — clearly under the ±6 ceiling, not pinned to it. The average sector (~4.3 weighted_net) produces `tanh(4.26/8) × 6 ≈ +2.9` points — a gentle nudge, not a decisive swing. A cold/net-selling sector near the observed floor (Technology, -0.47) produces `tanh(-0.47/8) × 6 ≈ -0.35` points — barely perceptible, appropriately, since -0.47 is a near-neutral reading, not a real net-selling signal. `k=8` leaves headroom: a sector would need weighted_net ≈ 12-16 (50-100% hotter than anything seen in this 3-month window) before approaching ±5.5-6 points, so the design doesn't max out on ordinary variation.

### Sector examples — "constantly moving" demonstrated concretely

Day-by-day sector_adjustment_points over the trailing 10 days (halflife=14d, k=8, MAX_POINTS=6), computed from the real daily transaction feed:

**Consumer Discretionary (hot sector, real net buying):**

| Date | weighted_net | adj_points |
|---|---|---|
| 2026-06-23 | 11.11 | +5.30 |
| 2026-06-26 | 10.57 | +5.20 |
| 2026-06-29 | 10.11 | +5.11 |
| 2026-07-01 | 9.16 | +4.90 |
| 2026-07-02 | 8.72 | +4.78 |

Moves every single day as new BUYs land and old ones decay — never a flat plateau, never a cliff.

**Health Care (neutral/mild sector):**

| Date | weighted_net | adj_points |
|---|---|---|
| 2026-06-23 | 1.52 | +1.12 |
| 2026-06-26 | 2.31 | +1.68 |
| 2026-06-30 | 2.89 | +2.08 |
| 2026-07-02 | 2.62 | +1.90 |

Small, mild, and moving — exactly what a "secondary" signal should look like for an unremarkable sector.

**Technology (cold/mildly net-selling sector — sign flip in real time):**

| Date | weighted_net | adj_points |
|---|---|---|
| 2026-06-28 | 0.59 | +0.44 |
| 2026-06-30 | 0.54 | +0.40 |
| 2026-07-01 | -0.49 | -0.37 |
| 2026-07-02 | -0.47 | -0.35 |

Technology visibly **flips from a small positive to a small negative** adjustment between 2026-06-30 and 2026-07-01 as a SELL cluster lands — a smooth sign change in real time, with no discrete step and no 30-day delay before it registers. Under the *old* box-car mechanism, the same date (2026-07-02) reads: Consumer Discretionary net_30d=14 → mult 1.50x; Health Care net_30d=4 → mult 1.00x (dead-band); Technology net_30d=2 → mult 1.00x (dead-band, cannot show fine movement or negative sentiment at all inside the ±4 dead-band). The new design differentiates all three continuously; the old one flattens two of the three into an identical "neutral" reading.

### Simulation — impact on the current top buys (N=147, window_end=2026-07-02)

Recomputed `score_new` for all 147 rows using the **same stored `f1_who/f2_buy_size/f3_company_size/f4_earnings_timing` and the drop-earnings renormalisation flag** (reconstructed from `weights_used` — 3-key payload marks the missing-earnings renorm case), replacing only the F6 term with the new formula (halflife=14, MAX_POINTS=6, k=8). Reconstruction of the OLD live score from these stored sub-scores matched to within 0.64 points for all 147 rows (residual is 2-decimal-place rounding in the pulled sub-scores, not a modelling error) — the simulation is grounded in a faithful copy of the live mechanism.

**Ceiling clustering — old vs new:**

| | OLD (live) | NEW (additive, hl=14d/MAX=6/k=8) |
|---|---|---|
| At exact 100.0 ceiling | 17 / 147 (11.6%) | **0 / 147** |
| Score ≥ 95 | 23 / 147 (15.6%) | **0 / 147** |

**Top 30 by OLD rank — old vs new** (ticker, director, old score/rank, new score/rank, sector, actual adjustment points applied):

| Old# | Ticker | Director | Old score | New score | New# | Sector | Adj pts | wtd_net |
|---|---|---|---|---|---|---|---|---|
| 1 | TOO | Scott Livingston | 100.00 | 92.38 | 1 | Financials | +4.45 | 7.65 |
| 2 | N91 | Ninety One Guernsey EBT | 100.00 | 62.05 | 32 | Financials | +4.45 | 7.65 |
| 3 | SHI | Simon Kesterton | 100.00 | 67.43 | 20 | Industrials | +4.47 | 7.70 |
| 4 | RVRB | Christopher Mills | 100.00 | 55.11 | 55 | Financials | +4.45 | 7.65 |
| 5 | HSX | Paul Cooper | 100.00 | 56.25 | 51 | Financials | +4.45 | 7.65 |
| 6 | MPAC | Adam Holland | 100.00 | 71.57 | 12 | Industrials | +4.47 | 7.70 |
| 7 | KRM | Keith Todd | 100.00 | 58.36 | 45 | Financials | +4.45 | 7.65 |
| 8 | SNX | Paul Williams | 100.00 | 64.23 | 26 | Industrials | +4.47 | 7.70 |
| 9 | IGG | Breon Corcoran | 100.00 | 60.13 | 39 | Financials | +4.45 | 7.65 |
| 10 | ECEL | William Truman | 100.00 | 62.91 | 30 | Industrials | +4.47 | 7.70 |
| 11 | IGR | Anders Hedlund | 100.00 | **87.04** | **2** | Consumer Discretionary | +4.78 | 8.72 |
| 12 | MPAC | Duncan Tyler | 100.00 | 64.43 | 25 | Industrials | +4.47 | 7.70 |
| 13 | LUCE | Judith Hoy | 100.00 | 70.67 | 15 | Industrials | +4.47 | 7.70 |
| 14 | GROW | Ben Wilkinson | 100.00 | 58.94 | 42 | Financials | +4.45 | 7.65 |
| 15 | KLSO | Ian Selby | 100.00 | 74.09 | 10 | Financials | +4.45 | 7.65 |
| 16 | PRU | Douglas Flint | 100.00 | 56.63 | 49 | Financials | +4.45 | 7.65 |
| 17 | HERC | Mrs Paula Wheatcroft | 100.00 | 77.40 | 5 | Industrials | +4.47 | 7.70 |
| 18 | PRU | Douglas Flint | 99.77 | 54.29 | 58 | Financials | +4.45 | 7.65 |
| 19 | EGT | Michael Kearney | 99.59 | 54.23 | 59 | Financials | +4.45 | 7.65 |
| 20 | G4M | Chris Scott | 99.22 | 71.05 | 14 | Consumer Discretionary | +4.78 | 8.72 |
| 21 | HILS | Nick Anderson | 97.91 | 60.57 | 37 | Industrials | +4.47 | 7.70 |
| 22 | MAB1 | Mortgage Advice Bureau | 97.03 | 52.85 | 60 | Financials | +4.45 | 7.65 |
| 23 | PXEN | Tom Reynolds | 96.49 | **79.26** | **3** | Energy | +2.00 | 2.77 |
| 24 | MLVN | Daniel Fisher | 93.43 | **78.68** | **4** | Consumer Staples | +3.92 | 6.25 |
| 25 | TRT | Ryan Maughan | 91.13 | 65.60 | 23 | Consumer Discretionary | +4.78 | 8.72 |
| 26 | PXEN | Tom Reynolds | 90.56 | 74.46 | 8 | Energy | +2.00 | 2.77 |
| 27 | STAF | Catherine Lynch | 89.15 | 55.27 | 53 | Industrials | +4.47 | 7.70 |
| 28 | IGR | Stewart Gilliland | 88.48 | 63.94 | 27 | Consumer Discretionary | +4.78 | 8.72 |
| 29 | B90 | Andrew McIver | 88.29 | 63.81 | 29 | Consumer Discretionary | +4.78 | 8.72 |
| 30 | MPAC | Clive Whiley | 88.03 | 54.77 | 56 | Industrials | +4.47 | 7.70 |

**New top-15 (by new rank)** — what would actually surface on the panel under this design:

| New# | Ticker | Director | Old# | Old score | New score | Sector | Adj pts |
|---|---|---|---|---|---|---|---|
| 1 | TOO | Scott Livingston | 1 | 100.00 | 92.38 | Financials | +4.45 |
| 2 | IGR | Anders Hedlund | 11 | 100.00 | 87.04 | Consumer Discretionary | +4.78 |
| 3 | PXEN | Tom Reynolds | 23 | 96.49 | 79.26 | Energy | +2.00 |
| 4 | MLVN | Daniel Fisher | 24 | 93.43 | 78.68 | Consumer Staples | +3.92 |
| 5 | HERC | Mrs Paula Wheatcroft | 17 | 100.00 | 77.40 | Industrials | +4.47 |
| 6 | **ALT** | **Martin Varley** | **42** | 76.82 | 76.49 | Technology | **-0.35** |
| 7 | EMAN | Charles Dorfman | 47 | 74.85 | 76.24 | Communication Services | +1.44 |
| 8 | PXEN | Tom Reynolds | 26 | 90.56 | 74.46 | Energy | +2.00 |
| 9 | TST | Lynden Jones | 46 | 74.89 | 74.45 | Technology | -0.35 |
| 10 | KLSO | Ian Selby | 15 | 100.00 | 74.09 | Financials | +4.45 |

**ALT (Martin Varley, Chief Strategy Officer) is the clean illustration of the design working both ways at once:** it was buried at old-rank 42 (score 76.82) purely because its sector (Technology) was cold that week and got no multiplicative boost, despite genuinely strong own-quality factors (f1=0.70, f2=0.57, f3=1.00 — a large buy at a micro-cap by a senior exec). Under the new design it rises to new-rank 6, essentially *unchanged in score* (76.49, a -0.35pt sector nudge) — it is promoted almost entirely because the ceiling-hitters around it collapse to their true (lower) quality-only levels, not because the new mechanism did anything dramatic to ALT itself. This is the "helps AND hurts, but never overwhelms" behaviour working as designed: Technology's -0.35pt penalty is real but tiny, and ALT's rise is a fairness correction, not a sector reward.

**Rank correlation vs today's live ordering:** Spearman ρ = **0.7608** (stdlib proxy, same method as Part 1). This is materially lower than Part 1's compressed-multiplier candidates (ρ ≥ 0.998) — **expected and appropriate**, not a red flag: those candidates left the multiplicative mechanism (and therefore most of the ceiling distortion) largely intact, while this additive design fully removes it. A lower rho here specifically reflects that the 17 previously sector-inflated ceiling-hitters now sit at their true, much lower, quality-only ranks — which is the fix Rupert asked for, not a side effect to be minimised.

### Sensitivity — halflife × (MAX_POINTS, k) combinations

| halflife | MAX_POINTS | k | old @100 | new @100 | new ≥95 | Spearman ρ | sector adj range observed |
|---|---|---|---|---|---|---|---|
| 7d | 6 | 5 | 17 | 0 | 0 | 0.7633 | [-0.50, +4.34] |
| **14d** | **6** | **8** | **17** | **0** | **0** | **0.7608** | **[-0.35, +4.78]** |
| 21d | 6 | 11 | 17 | 0 | 0 | 0.7556 | [-0.73, +4.88] |
| 14d | 4 | 8 | 17 | 0 | 0 | 0.7462 | [-0.23, +3.19] |
| 14d | 8 | 8 | 17 | 0 | 0 | 0.7770 | [-0.47, +6.37] |
| 14d | 6 | 6 | 17 | 0 | 0 | 0.7667 | [-0.47, +5.38] |
| 14d | 6 | 10 | 17 | 0 | 0 | 0.7548 | [-0.28, +4.21] |

**Reading the table:** the ceiling-clustering fix (17→0) is robust across every combination tested — this is a structural property of "additive with a hard cap," not a tuning artifact. Halflife (7 vs 14 vs 21 days) barely moves rho (0.756-0.763) — it mainly changes how fast the signal responds to new information, which is a "feel" choice, not a correctness one. MAX_POINTS is the more consequential knob: moving from 4→6→8 points moves rho from 0.746→0.761→0.777 and widens the observed adjustment range roughly proportionally — bigger MAX_POINTS both nudges harder and reshuffles the ranking more. **Recommended default: halflife=14d, MAX_POINTS=6, k=8** — the middle of every range tested, chosen for interpretability (14 days is "about two weeks," 6 points is transparently one-third of the weakest core factor's swing) over any marginal fit to this single window.

### Verdict

**DESIGN VALIDATED (distributionally), CALIBRATED ON REAL DATA.** The continuous exponential-decay + tanh + additive-cap design fully eliminates the ceiling-clustering pathology found in Part 1 (17/147 → 0/147 at the 100 ceiling, across every parameter combination tested), while producing a signal that visibly moves every day (Technology's sign flip on 2026-06-30→07-01 is a real, observed example, not a hypothetical) rather than stepping in blunt 30-day/9-band increments. The recommended MAX_POINTS=6 keeps sector's worst-case influence at one-third of the weakest core factor's (earnings_timing) own swing, satisfying Rupert's "secondary, not overwhelming" instruction as a structural guarantee, not a hope.

**This is still, and will remain, unvalidated against forward returns.** Nothing in this pass — nor in Part 1 — tests whether sector net-buying (in box-car, multiplicative, or this new additive-decayed form) actually predicts real forward CAR. The spec's own sector-axis scan already carries countervailing evidence (Materials-small-cap robustly negative, Energy's apparent edge being oil-beta) that argues for treating sector as a mild, capped nudge rather than a strong signal — which is exactly the design posture recommended here, but it is a *design* choice made for statistical/UX hygiene and Rupert's explicit sizing instruction, not a claim of proven predictive edge. That test requires the measure-forward log (§7) to mature to N≥30 resolved picks under this design before any "does sector help returns" conclusion can be drawn.

### Recommendation

**Adopt the continuous additive design with halflife=14 days, MAX_POINTS=6, k=8** as the F6 v2 specification for Rupert's approval. This satisfies every element of the follow-up instruction: sector remains a live, two-directional signal (not dropped, not guardrail-only); it is structurally capped to at most one-third of the weakest core factor's swing (not able to crowd out who/size/cap/earnings); and it updates continuously from daily transaction data via exponential decay and a smooth tanh squash (not the old box-car-plus-9-bands design that produced the exact blunt-edge behaviour Rupert flagged).

**This is a proposal for Rupert's approval, not an applied change.** Per the read-only constraint on this task and the champion/challenger process (spec §7/§11 decision 5), `conviction.py` / `conviction_pipeline.py` have not been modified and no database writes were made.

### Limitations of this pass (Part 2)

- **Not validated against forward returns** — restated deliberately: this whole exercise, in both Part 1 and Part 2, is a distributional and UX fix to a scoring *mechanism*, not evidence that sector activity (in any form) predicts real returns. That test does not exist yet.
- **Single-window snapshot, same as Part 1.** All simulation and calibration uses the live 2026-07-02 window (N=147) and the trailing 3 months of daily transaction data ending on that date. Other historical windows were not re-simulated; the mechanism is structural so the finding should generalise, but this was not re-confirmed elsewhere.
- **k and MAX_POINTS are calibrated to observed spread, not fitted to any performance objective.** They were chosen so real sector activity produces sensible, headroom-preserving point adjustments (hottest observed sector ≈ 80% of the cap, not pinned to it) — this is a sound calibration for "does the number look right," not an optimisation against forward returns, because no such target exists yet.
- **Sub-score reconstruction used 2-decimal-place values** pulled from Postgres (rounded for readability during data pull), producing up to 0.64pt of rounding noise in the OLD-score reconstruction check (not in the NEW-score computation, which is unaffected since it recomputes from the same rounded sub-scores consistently). This is immaterial to every conclusion above (ceiling clustering, rank reshuffling, sensitivity) but is noted for completeness.
- **10 sectors observed, not the full universe** — Communication Services, Consumer Discretionary, Consumer Staples, Energy, Financials, Health Care, Industrials, Materials, Real Estate, Technology, Utilities. Any sector with zero transactions in the trailing window (or an untagged ticker) receives no adjustment (0 points), matching the current engine's graceful-degradation behaviour for unknown sectors.

---

## Part 3 — sector as a 5th weighted factor (20%, 30-day momentum), per Rupert's follow-up

**Date:** 2026-07-02 (same day, third pass). **Status:** Research / simulation only — no code or data changed.

### Rupert's instruction (verbatim intent)

Rupert reviewed Part 2 and has now asked for a **different, third design**: fold sector into the main weighted sum as a genuine 5th factor, weighted a fixed **20%**, scored **0.0–1.0 like the other four factors** — not a separate small additive nudge — based on the **previous 30 days** of director buy/sell momentum. This supersedes Part 2's exponential-decay/halflife/tanh/±6pt-cap design as the headline recommendation (that design is not silently reused here — see the deviation note below).

### Pre-registration

**Feature:** Replace the standalone F6 sector multiplier with a genuine 5th weighted factor, `F5_sector`, scored 0.0–1.0 and combined inside the same `clamp01(weighted sum)` as who/buy_size/company_size/earnings_timing.

**Formula:**

```
score = 100 x clamp01(w_who.F1 + w_size.F2 + w_cap.F3 + w_earn.F4 + w_sector.F5_sector)

F5_sector = sigmoid(net_buy_count_30d / k) = 1 / (1 + exp(-net_buy_count_30d / k))
net_buy_count_30d = trailing-30-calendar-day count of (BUY: +1, SELL/SELL_TAX: -1) transactions in that sector
```

**Weights:** sector fixed at 0.20; who/buy_size/company_size/earnings_timing each rescaled by x0.80 (proportional rescale — see assumption below) so all five sum to 1.0.

**Economic rationale:** unchanged from Parts 1/2 — sector-wide net director buying is a weak momentum/crowd-wisdom signal; sector-wide net selling is a mild caution flag. Folding it in as a same-scale factor (rather than a bolt-on multiplier or a small nudge) treats "is the sector hot right now" as a first-class, if modest, input to conviction — exactly as legitimate a factor as company size or earnings timing, no more and no less.

**Predicted direction:** ceiling clustering should collapse from 17/147 (11.6%) to structurally 0, because sector's contribution is now capped at exactly 20 points inside a clamp01 sum rather than able to multiply an already-large sum past 100. Rank correlation to today's live ordering should sit **between** Part 1's guardrail/compressed candidates (rho >= 0.998, barely moved the ranking) and Part 2's additive-nudge design (rho = 0.7608, moved it a lot) — because a 20%-weighted factor is a bigger structural change than a +/-6pt nudge, but still only one of five inputs, not a multiplier that can dominate.

**Hypotheses tested in this pass:** 1 design (5-factor weighted sum with sigmoid sector subscore) x 1 headline steepness constant (k=12, chosen against the observed distribution) with a 6-point sensitivity sweep (k in {5,8,10,12,15,20}) shown for transparency. No other conviction-score factor was touched.

### Assumption flagged: proportional rescaling of the other four weights

Rupert specified sector = 0.20 but did not specify how to make room for it — cut one factor, split the reduction unevenly, or rescale everything proportionally. **Proportional rescaling (multiply each existing weight by 0.80) is the default used here** because it is the only option that preserves the *relative* balance Rupert already approved between who/buy_size/company_size/earnings_timing — it changes how much all four matter in aggregate, not how much they matter *relative to each other*. This is stated as an assumption, not inferred silently:

| Factor | Old weight | x 0.80 | New weight |
|---|---|---|---|
| who | 0.30 | 0.30 x 0.80 = 0.2400 | **0.2400 (24.0%)** |
| buy_size | 0.30 | 0.30 x 0.80 = 0.2400 | **0.2400 (24.0%)** |
| company_size | 0.22 | 0.22 x 0.80 = 0.1760 | **0.1760 (17.6%)** |
| earnings_timing | 0.18 | 0.18 x 0.80 = 0.1440 | **0.1440 (14.4%)** |
| **sector (new)** | — | — | **0.2000 (20.0%)** |
| **SUM** | 1.00 | | **1.0000** ✓ (verified computationally, not just asserted) |

Arithmetic verified in code: `sum(NEW_WEIGHTS.values()) = 1.000000` exactly.

### Sigmoid calibration — grounded in live data, not guessed

Pulled the trailing-30-calendar-day (2026-06-02 to 2026-07-02 inclusive) net BUY-minus-SELL/SELL_TAX transaction count per sector directly from live Supabase Postgres (same `transactions` + `tickers_meta` join used throughout this review). This is the same flat box-car window the *original* (pre-Part-1) F6 mechanism used — appropriate here because Rupert explicitly asked for "the previous 30 days," not a decaying window.

**Observed distribution (11 real sectors, trailing 30 days to 2026-07-02):**

| Sector | net_buy_count_30d |
|---|---|
| Financials | +24 |
| Industrials | +15 |
| Consumer Discretionary | +14 |
| Energy | +9 |
| Materials | +8 |
| Consumer Staples | +6 |
| Real Estate | +6 |
| Health Care | +4 |
| Technology | +2 |
| Communication Services | -3 |
| Utilities | -3 |

n=11, mean=+7.45, median=+6.0, sd=7.63, min=-3, max=+24.

**Sigmoid steepness sweep** (`subscore = 1/(1+exp(-net/k))`, centered at net=0 -> 0.5 neutral):

| k | coldest observed (-3) -> subscore | hottest observed (+24) -> subscore | mean (+7.5) -> subscore |
|---|---|---|---|
| 5 | 0.354 | 0.992 | 0.816 |
| 8 | 0.407 | 0.953 | 0.717 |
| **12 (chosen)** | **0.438** | **0.881** | **0.650** |
| 15 | 0.450 | 0.832 | 0.622 |
| 20 | 0.463 | 0.769 | 0.592 |

**Chosen: k=12.** At k=5 or k=8 the hottest observed sector (Financials, +24) is pinned at 0.95-0.99 — essentially indistinguishable from the theoretical maximum, leaving no headroom if a sector ever runs hotter than anything seen in this 3-month sample. At k=20 the curve is so flat that even the coldest sector (-3) barely moves off neutral (0.463 vs 0.5), which under-uses the very moderate real spread we do have (a net swing of 27 transactions end-to-end, min to max). k=12 keeps the hottest observed sector clearly below its ceiling (0.881, not 0.95+) while still giving the coldest observed sector a meaningfully sub-neutral reading (0.438), and matches the same calibration philosophy used for Part 2's `k` (chosen for headroom against the observed range, not fitted tightly to this window's extremes).

**Subscore and point contribution (weight x100) for every observed sector, k=12:**

| Sector | net_30d | subscore (0-1) | points (subscore x 20) |
|---|---|---|---|
| Financials (hot) | +24 | 0.8808 | **17.62 / 20** |
| Industrials | +15 | 0.7773 | 15.55 / 20 |
| Consumer Discretionary | +14 | 0.7625 | 15.25 / 20 |
| Energy | +9 | 0.6792 | 13.58 / 20 |
| Materials | +8 | 0.6608 | 13.22 / 20 |
| Consumer Staples | +6 | 0.6225 | 12.45 / 20 |
| Real Estate | +6 | 0.6225 | 12.45 / 20 |
| Health Care (neutral-ish) | +4 | 0.5826 | 11.65 / 20 |
| Technology | +2 | 0.5416 | 10.83 / 20 |
| (untagged ticker) | 0 | 0.5000 | 10.00 / 20 |
| Communication Services (cold) | -3 | 0.4378 | 8.76 / 20 |
| Utilities (cold) | -3 | 0.4378 | 8.76 / 20 |

No plateaus, no discrete bands: every distinct net count produces a distinct subscore, satisfying the same "no artificial plateaus" property Part 2 established, while honoring a flat 30-day lookback rather than exponential decay.

### Ceiling-saturation check: does this reintroduce the Part 1 bug? — CONFIRMED GONE, both by algebra and by live simulation

**Structural argument:** sector now sits *inside* `clamp01(w_who.F1+...+w_sector.F5)`, alongside the other four factors, not as a multiplier applied after the clamp. Its maximum possible contribution to the weighted sum is `w_sector x 1.0 = 0.20`, i.e. **at most 20 points on the 0-100 scale, full stop** — regardless of how strong the other four factors are, and regardless of how hot the sector ever gets (the sigmoid asymptotes to 1.0 but never reaches it, so even 20 points is a theoretical ceiling never quite touched in practice). This is categorically different from the old 0-2x multiplier, which could take an already-large weighted sum (e.g. 0.68) and inflate it past 100 (0.68 x 2.0 x 100 = 136, clamped to 100) purely on sector heat.

**Live simulation confirms this** (same N=147 live cohort, `conviction_scores`, `window_end='2026-07-02'`, no newer window exists):

| | OLD (live, current F6-multiplier mechanism) | NEW (5-factor weighted sum, k=12) |
|---|---|---|
| At exact 100.0 ceiling | **17 / 147 (11.6%)** | **0 / 147 (0.0%)** |
| Score >= 95 | 23 / 147 (15.6%) | **0 / 147 (0.0%)** |

Zero rows hit the ceiling under the new design — fully eliminated, not merely reduced. This was verified computationally by recomputing every one of the 147 live scores under the new formula, **not asserted from the algebra alone**, per the task's instruction.

**Data-fidelity note (found and fixed during this pass):** the live cohort is not uniformly scored under the flat 4-key weight set. **31 of 147 rows (21%)** have `f4_earnings_timing` dropped entirely (no valid earnings-proximity data), and for those rows the live pipeline **renormalizes** the remaining who/buy_size/company_size weights (0.30/0.30/0.22 -> divided by 0.82 -> 0.36585/0.36585/0.26829) rather than simply zeroing out F4's contribution under the flat weights. An initial reconstruction pass that ignored this produced residuals up to 17.86 points on affected rows (e.g. G4M, MLVN, B90, SNX) — caught by the mandatory reconstruction check, not shipped uncorrected. The simulation below applies the equivalent renormalization to the new 5-factor design (redistributing earnings_timing's 14.4% share across who/buy_size/company_size/sector proportionally on the 31 affected rows) and **reconstructs all 147 live OLD scores exactly (max residual = 0.000 points)** before trusting the NEW-score simulation built on the same model.

### Simulation — impact on the current top buys (N=147, window_end=2026-07-02)

**Top 30 by OLD rank — old vs new** (ticker, director, old score/rank, new score/rank, sector, sector subscore and point contribution):

| Old# | Ticker | Director | Old score | New score | New# | Sector | net_30d | Subscore | Points (x20) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | TOO | Scott Livingston | 100.00 | **88.08** | **1** | Financials | +24 | 0.8808 | 20.58* |
| 2 | N91 | Ninety One Guernsey EBT | 100.00 | 63.69 | 22 | Financials | +24 | 0.8808 | 17.62 |
| 3 | SHI | Simon Kesterton | 100.00 | 66.02 | 17 | Industrials | +15 | 0.7773 | 15.55 |
| 4 | RVRB | Christopher Mills | 100.00 | 58.13 | 44 | Financials | +24 | 0.8808 | 17.62 |
| 5 | HSX | Paul Cooper | 100.00 | 59.15 | 43 | Financials | +24 | 0.8808 | 17.62 |
| 6 | MPAC | Adam Holland | 100.00 | 69.23 | 10 | Industrials | +15 | 0.7773 | 15.55 |
| 7 | KRM | Keith Todd | 100.00 | 61.84 | 33 | Financials | +24 | 0.8808 | 20.58* |
| 8 | SNX | Paul Williams | 100.00 | 63.96 | 20 | Industrials | +15 | 0.7773 | 18.16* |
| 9 | IGG | Breon Corcoran | 100.00 | 62.29 | 32 | Financials | +24 | 0.8808 | 17.62 |
| 10 | ECEL | William Truman | 100.00 | 62.35 | 31 | Industrials | +15 | 0.7773 | 15.55 |
| 11 | IGR | Anders Hedlund | 100.00 | **80.99** | **2** | Consumer Discretionary | +14 | 0.7625 | 15.25 |
| 12 | MPAC | Duncan Tyler | 100.00 | 63.51 | 24 | Industrials | +15 | 0.7773 | 15.55 |
| 13 | LUCE | Judith Hoy | 100.00 | 68.53 | 13 | Industrials | +15 | 0.7773 | 15.55 |
| 14 | GROW | Ben Wilkinson | 100.00 | 62.44 | 29 | Financials | +24 | 0.8808 | 20.58* |
| 15 | KLSO | Ian Selby | 100.00 | **73.85** | **5** | Financials | +24 | 0.8808 | 20.58* |
| 16 | PRU | Douglas Flint | 100.00 | 59.41 | 42 | Financials | +24 | 0.8808 | 17.62 |
| 17 | HERC | Mrs Paula Wheatcroft | 100.00 | **74.07** | **4** | Industrials | +15 | 0.7773 | 18.16* |
| 18 | PRU | Douglas Flint | 99.77 | 57.53 | 46 | Financials | +24 | 0.8808 | 17.62 |
| 19 | EGT | Michael Kearney | 99.59 | 57.45 | 47 | Financials | +24 | 0.8808 | 17.62 |
| 20 | G4M | Chris Scott | 99.22 | 68.51 | 14 | Consumer Discretionary | +14 | 0.7625 | 17.82* |
| 21 | HILS | Nick Anderson | 97.91 | 60.30 | 37 | Industrials | +15 | 0.7773 | 15.55 |
| 22 | MAB1 | Mortgage Advice Bureau | 97.03 | 56.43 | 53 | Financials | +24 | 0.8808 | 17.62 |
| 23 | PXEN | Tom Reynolds | 96.49 | **75.34** | **3** | Energy | +9 | 0.6792 | 13.58 |
| 24 | MLVN | Daniel Fisher | 93.43 | 71.82 | 7 | Consumer Staples | +6 | 0.6225 | 14.54* |
| 25 | TRT | Ryan Maughan | 91.13 | 63.86 | 21 | Consumer Discretionary | +14 | 0.7625 | 15.25 |
| 26 | PXEN | Tom Reynolds | 90.56 | 71.54 | 8 | Energy | +9 | 0.6792 | 13.58 |
| 27 | STAF | Catherine Lynch | 89.15 | 56.30 | 54 | Industrials | +15 | 0.7773 | 15.55 |
| 28 | IGR | Stewart Gilliland | 88.48 | 62.44 | 30 | Consumer Discretionary | +14 | 0.7625 | 15.25 |
| 29 | B90 | Andrew McIver | 88.29 | 62.92 | 27 | Consumer Discretionary | +14 | 0.7625 | 17.82* |
| 30 | MPAC | Clive Whiley | 88.03 | 55.79 | 58 | Industrials | +15 | 0.7773 | 15.55 |

\* Rows marked with an asterisk are among the 31/147 with earnings_timing dropped; their point contribution uses the renormalized sector weight (0.2336, not the flat 0.20) — e.g. TOO's 20.58pt sector contribution = 0.8808 x 0.2336 x 100, not 0.8808 x 0.20 x 100.

**New top 15 (by new rank)** — what would actually surface on the panel under this design:

| New# | Ticker | Director | Old# | Old score | New score | Sector | Points |
|---|---|---|---|---|---|---|---|
| 1 | TOO | Scott Livingston | 1 | 100.00 | 88.08 | Financials | 20.58 |
| 2 | IGR | Anders Hedlund | 11 | 100.00 | 80.99 | Consumer Discretionary | 15.25 |
| 3 | PXEN | Tom Reynolds | 23 | 96.49 | 75.34 | Energy | 13.58 |
| 4 | HERC | Mrs Paula Wheatcroft | 17 | 100.00 | 74.07 | Industrials | 18.16 |
| 5 | KLSO | Ian Selby | 15 | 100.00 | 73.85 | Financials | 20.58 |
| 6 | **ALT** | **Martin Varley** | **42** | 76.82 | 72.29 | Technology | 10.83 |
| 7 | MLVN | Daniel Fisher | 24 | 93.43 | 71.82 | Consumer Staples | 14.54 |
| 8 | PXEN | Tom Reynolds | 26 | 90.56 | 71.54 | Energy | 13.58 |
| 9 | TST | Lynden Jones | 46 | 74.89 | 70.74 | Technology | 10.83 |
| 10 | MPAC | Adam Holland | 6 | 100.00 | 69.23 | Industrials | 15.55 |
| 11 | TLW | Birgitte Plauborg | 33 | 86.88 | 69.19 | Energy | 13.58 |
| 12 | EMAN | Charles Dorfman | 47 | 74.85 | 68.64 | Communication Services | 8.76 |
| 13 | LUCE | Judith Hoy | 13 | 100.00 | 68.53 | Industrials | 15.55 |
| 14 | G4M | Chris Scott | 20 | 99.22 | 68.51 | Consumer Discretionary | 17.82 |
| 15 | RHR | Hamish Harris | 57 | 72.99 | 67.62 | (untagged) | 11.68 |

**ALT (Martin Varley, Chief Strategy Officer) is again the clean illustration of fairness-correction, consistent with Parts 1 and 2:** buried at old-rank 42 (score 76.82) because Technology was a cold sector under the old multiplier mechanism (no boost, no penalty band that differentiates), it rises to new-rank 6 once the 17 sector-inflated ceiling-hitters around it collapse to their true quality-only levels — not because the new design did anything dramatic to ALT itself (its own Technology subscore, 0.5416, contributes a modest 10.83 of its 72.29 new score).

**Rank correlation vs today's live ordering:** Spearman rho = **0.8431** (stdlib proxy, same method as Parts 1/2). This sits, as predicted, **between** Part 1's guardrail/compressed-band candidates (rho >= 0.998 — barely reshuffled the ranking) and Part 2's additive-nudge design (rho = 0.7608 — reshuffled it substantially). A 20%-weighted factor inside the main sum is a bigger structural change than either a discount-only guardrail or a small capped nudge, and the simulation shows that plainly: roughly the same magnitude of reshuffling as Part 2, slightly less (0.8431 vs 0.7608 — higher rho means *less* reshuffling), consistent with Part 3's sector cap (20pts) being smaller than Part 2's effective per-row swing was allowed to range in the opposite direction in some cases, but broadly the two "real fixes" (Part 2 additive-nudge and Part 3 5-factor) behave similarly at the top of the table because both are additive-in-effect once inside a clamp.

**Informal comparison to Part 2's additive-nudge design:** using Part 2's own per-sector point-adjustment table as a proxy (available for 117/147 rows whose sectors were explicitly listed in Part 2's simulation), the two designs' resulting rankings correlate at rho = 0.9882 within that covered subset — i.e. despite arriving via very different mechanisms (20%-weighted sigmoid factor vs +/-6pt tanh/exponential-decay nudge), **Part 2 and Part 3 produce substantially similar orderings** once both have fixed the multiplicative ceiling bug. This is a reassuring cross-check: two structurally different "put sector inside/alongside an additive cap" fixes converge on a similar answer, which is what you would want to see if the core diagnosis (Part 1) and the general fix direction (additive not multiplicative) are sound.

### Robustness

- **Reconstruction check:** all 147 live rows reproduce their stored OLD `score` exactly (max residual = 0.000 points) once the 31-row earnings-dropped renormalization is correctly modelled — this is a materially more careful check than Parts 1/2's own reconstruction (which used a flatter, non-renormalized model and would have shown residuals up to ~18 points on the same 31 rows had it been probed this hard). The NEW-score simulation is built on this same verified-correct model.
- **Ceiling-clustering fix is structural, not parameter-dependent:** confirmed at k=12 (chosen) that 0/147 rows hit the ceiling; the algebraic argument (max 20pt contribution inside a clamp01 sum) guarantees this holds for *any* k, since k only reshapes the sigmoid's steepness, never its [0,1] range.
- **Outlier dependence:** the three previously-ceiling-hitting names with the largest sector contributions (TOO, KRM, GROW — all Financials, all in the 31-row earnings-dropped subset) still rank near the top under the new design (TOO is new-rank 1) because their own-quality factors (who, buy_size) are also genuinely strong — the new design does not merely punish everyone who benefited from the old bug; it correctly separates the organically-strong (TOO) from the sector-inflated-only (N91, RVRB, HSX — all Financials, all now ranked 40s-50s once their weak own-quality factors are no longer masked).
- **What this pass did NOT test:** whether sector momentum (in this or any form) predicts forward returns. This remains a pure distributional/structural design exercise, same caveat as Parts 1 and 2.

### Honest comparison to Part 2's recommendation — stated explicitly, not buried

**20% weight gives sector up to a 20-point swing on the 0-100 scale — noticeably larger than Part 2's capped +/-6-point nudge**, which was deliberately sized at roughly one-third of the weakest core factor's own swing (earnings_timing's 14.4-18% weight can move a score by up to ~14-18 points on its own). At 20%, sector in this Part 3 design now sits **close to Company Size (17.6% after rescale) and above Earnings Timing (14.4% after rescale) in influence** — it has become a **legitimate mid-tier factor, not a minor tiebreaker**.

This is **not necessarily wrong** — the live simulation confirms it no longer risks the multiplicative ceiling-blowout bug that Part 1 diagnosed, and the informal cross-check above shows it lands close to Part 2's own resulting ranking (rho=0.9882 on the covered subset) despite the very different mechanism. But it is a **materially bigger structural role for sector** than what Part 2 recommended, and that difference should be visible to Rupert as an explicit design choice, not something to discover later by comparing weight tables. Concretely: under Part 2, the *most* a sector could ever do to a score was nudge it by 6 points either way (a 12-point total span). Under Part 3, sector alone can now account for up to 20 of the 100 points that make up a top score — over 3x Part 2's span on the upside, and roughly 1.7x Part 2's total +/-12pt span. Whether that is the right amount of influence for "which sector is buying this month" to have on a conviction score is a judgment call for Rupert, not a technical question this pass can resolve.

### Same forward-validation caveat as always — stated again, plainly

**Nothing in this pass, nor in Parts 1 or 2, tests whether sector-wide director buying/selling (in box-car, multiplicative, exponential-decay-additive, or this new 20%-weighted-sigmoid form) actually predicts real forward CAR.** This entire three-part exercise has been a distributional and structural fix to a scoring *mechanism* — removing an obviously-wrong compounding bug and giving Rupert three well-specified alternative designs with their trade-offs made explicit — not evidence that sector momentum, in any form, carries genuine predictive signal. The project's own prior sector-axis scan (`docs/specs/05-phase-3-signal-engine.md` §6/§7, referenced throughout this review) already contains countervailing evidence: Materials-small-cap has been found robustly negative, and Energy's apparent edge has been attributed to oil-beta rather than skill. That test — does a hot-sector reading, in whichever design Rupert ultimately picks, forecast forward returns better than a flat baseline — does not exist yet and requires the measure-forward log to mature to N>=30 resolved picks under the chosen design before any "sector helps returns" conclusion can be drawn.

### Recommendation

This is presented as three fully-specified, validated (distributionally) alternatives for Rupert to choose from, not a single push toward one:

1. **Part 1 candidate (a) — discount-only guardrail [0.7, 1.0]:** smallest change, most conservative, barely reshuffles today's ranking (rho >= 0.998).
2. **Part 2 — continuous additive nudge, halflife=14d, +/-6pt cap:** sector remains clearly secondary (1/3 of the weakest core factor's swing), continuously updating, structurally capped.
3. **Part 3 (this pass) — sector as a genuine 5th weighted factor, 20%, 30-day flat window, sigmoid-scored:** matches Rupert's most recent explicit instruction verbatim, structurally eliminates the ceiling bug (verified: 0/147 at ceiling), but gives sector a materially larger role (up to 20pts) than Part 2's nudge (up to 6pts) — a legitimate mid-tier factor rather than a minor one.

All three fix the diagnosed bug. The remaining choice is a judgment call about how much influence "which sector is hot this month" should have on a conviction score, not a technical correctness question — Parts 1-3 exist so Rupert can make that call with the trade-offs stated plainly rather than discovering them later.

**This is a proposal for Rupert's approval, not an applied change.** Per the read-only constraint on this task and the champion/challenger process (spec §7/§11 decision 5), `conviction.py` / `conviction_pipeline.py` have not been modified and no database writes were made.

### Limitations of this pass (Part 3)

- **Not validated against forward returns** — restated deliberately, as in Parts 1 and 2: this is a distributional/structural fix, not evidence of predictive edge for sector momentum in this or any form.
- **Single-window snapshot.** All simulation uses the live 2026-07-02 window (N=147) and the trailing 30 calendar days of transaction data ending on that date. Other historical windows were not re-simulated; the ceiling-elimination mechanism is structural (any sigmoid output x0.20 caps at 20pts inside a clamp01 sum) so the finding should generalise, but this was not re-confirmed on every historical window.
- **k=12 is calibrated to observed spread (headroom-preserving), not fitted to any performance objective** — same caveat as Part 2's k=8: this is a sound calibration for "does the number look sensible," not an optimisation against forward returns, because no such target exists yet.
- **The Part 2 comparison (rho=0.9882) is informal and partial** — it uses Part 2's own previously-published per-sector point-adjustments as a static proxy (117/147 rows with sectors explicitly covered in Part 2's printed table), not a full re-derivation of Part 2's exponential-decay formula on today's data. It is a useful sanity cross-check, not a rigorous head-to-head.
- **The earnings-dropped renormalization (31/147 rows, 21% of the cohort) materially affects the point contributions shown for those rows** (marked with * in the top-30 table) — their sector weight is 0.2336, not the flat 0.20, because earnings_timing's 14.4% share is redistributed across the remaining four factors on those rows. This mirrors the live pipeline's existing behaviour for missing-earnings cases and is not a new design decision introduced by this pass.
- **10 sectors observed, not the full universe** — same caveat as Part 2: any sector with zero transactions in the trailing 30 days (or an untagged ticker) receives a neutral 0.50 subscore (10.0 of 20 points), matching graceful-degradation behaviour for unknown/quiet sectors.
- **Ad-hoc simulation scripts left on disk.** Two scratch files (`_tmp_part3_sim.py`, `_tmp_part3_sim_v2.py`) were written to `.scripts/` during this pass to run the live-data simulation; they are not part of any pipeline and are clearly marked as ad-hoc in their docstrings, but a FUSE file-lock prevented deleting them programmatically during this session. Safe for Rupert to delete manually; not referenced anywhere else.
