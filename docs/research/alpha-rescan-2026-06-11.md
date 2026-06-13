# Alpha Re-scan — Sprint 63 Conviction Columns (2026-06-11)

**Run:** `bt_20260611T212842Z` — 2,645 signal-level rows, 64 columns, verified clean read (NUL-stripped FUSE path). All 2,645 rows are BUY-driven signals (every row carries the Sprint 63 flags). **Deduplicated to 1,435 distinct fingerprints** for factor work; signal-level set used only where noted. Entry dates 2025-05-02 → 2026-06-11, half-sample split at the median entry date **2025-12-03**. Outcome coverage: T+90 n=981, T+30 n=1,253, T+1 n=1,428, T+180 n=732 (second half nearly empty — unusable for half-sample tests), T+365 n=144 (unusable).

**Cells tested: 367** (67 targeted hypothesis cells + 300 grid cells over 25 single factors and all pairs). 126 had n>=30. **67 passed the robustness bar** (|t|>=2.5 + same-sign halves): **66 negative, 1 positive — and the positive one fails ticker-clustered inference (see below).**

Baseline for comparison: `docs/research/alpha-research-2026-06-10.md` (142 cells, 49 robust, all negative, overall net CAR T+90 -3.5%).

---

## 1. Headline verdict

**Still no tradeable positive cell.** One cell technically cleared the robustness bar with a positive mean — **large-cap (>£1b) × tiny value (<£10k)**: n=249, +3.3%, 61% hit, t=+3.9, positive in both halves. But it is an artifact of pseudo-replication, not alpha:

- 249 rows come from only **49 tickers**; PRU (64 rows), RR (37) and BATS (23) are half the sample. The PRU rows are ~£383 median monthly purchases — share-plan-style drip buys, not conviction trades.
- Re-tested with **one observation per ticker** (clustered): t = **+0.84** — not significant. Excluding PRU/RR/BATS: +0.91%, t=+0.70. The "signal" is three mega-caps that happened to rally (RR alone contributed +357 percentage-points of summed CAR).

In plain language: dozens of tiny repeat purchases in Rolls-Royce and Prudential during a year those stocks went up. Per-row t-stats mistake repetition for evidence. **Verdict unchanged from the baseline: the dataset supports an avoid-filter, not a buy-list.** The whole-sample ticker-clustered mean is -2.46% (t=-1.7) — directors' picks lagged the benchmark this year regardless of slicing.

The genuinely encouraging (but unprovable yet) result is **opportunistic buys** (H1): every measurable cut is positive (+0.6% to +1.7% at T+30, 55-62% hit), but the flag only starts populating in Feb-2026, so n is tiny at T+90 (16) and no half-sample test is possible. This is the one cell worth re-testing in 3-6 months.

## 2. Best cells (positive or least-negative), net CAR T+90 unless noted

| Cell | n | mean | hit | t | halves (h1/h2) | robust? |
|---|--:|--:|--:|--:|---|---|
| large>£1b × value<£10k | 249 | +3.3% | 61% | +3.9 | +4.3% / +1.0% | row-level YES — **fails clustered t (+0.84): ARTIFACT** |
| same, ex-PRU/RR/BATS | 125 | +0.9% | 54% | +0.7 | — | no |
| opportunistic (T+30) | 66 | +0.6% | 55% | +0.7 | all in h2 | untestable (flag starts Feb-26) |
| opportunistic × large-cap (T+30) | 48 | +1.7% | 62% | — | all in h2 | untestable |
| opportunistic (T+90) | 16 | +1.3% | 62% | +0.5 | all in h2 | n too small |
| nearest-high 52wk quartile | 295 | +1.0% | 51% | +1.3 | +2.4% / -3.3% | no — halves flip; clustered t -0.81 |
| holding-increase mid tercile | 39 | +1.3% | 36% | +0.3 | +0.6% / +2.7% | no — t tiny |
| large>£1b overall | 500 | +0.7% | 49% | +1.1 | +1.3% / -1.0% | no — halves flip |

Nothing positive survives both the robustness bar and clustering. Winsorizing at 1%/99% changes no conclusion anywhere (largest shift ~0.4pp).

## 3. Per-hypothesis findings

**H1 — Routine/opportunistic.** Fingerprint counts: 1,342 insufficient_history / 87 opportunistic / 6 routine (routine far too small, as expected). Opportunistic is **directionally positive everywhere it can be measured** (T+30 +0.62% vs insufficient -1.49%; two-sample t=+1.33, p=0.20 — not significant). Critical mechanical caveat: the flag requires prior-history depth our corpus only reached in **Feb-2026**, so all 87 opportunistic firings sit in the last 4 months — no T+90 maturity (n=16), no half-sample test. Promising, unproven. **Action: re-run this cut once the Feb-Jun 2026 cohort matures (Sep-Oct 2026).**

**H2 — Seller reversal.** 61 deduped flagged (107 at signal level). T+90: -1.75% (n=29, ns); T+30: -0.30% (n=47, ns); halves disagree in sign. Interaction with f1 rows (signal level, n=18): -4.4%, ns. Two-sample vs non-reversal: p=0.75. **No detectable effect either way at current n.**

**H3 — Post-results.** Headline numbers look damning — flag=1: -6.9% (n=130, robust); day 0-1: **-8.4%** (n=45, robust); day 2-7: -7.1% (robust) — but they are **confounded by coverage**: tickers with reporting-dates coverage have median cap **£331m vs £3.67b** for uncovered (59% vs 25% small-cap). The flag is currently a small-cap proxy, and small-cap is the known drag. The no-coverage group sits at -0.2%. Within-coverage, day-0/1 is no better than day->14 (-8.4% vs -7.5%). **Honest read: the canonical first-window trade shows no lift on our data, but the test is contaminated until reporting-date coverage extends to large caps.**

**H4 — Holding % increase.** 172 populated. Terciles: low (~0% increase) **-7.1%, 19% hit, robust negative** — token top-ups are a genuine avoid signal; mid +1.3% (ns); high -0.6% (ns, halves flip). The conviction story (big stake increase -> alpha) does **not** show up; the inverse (negligible stake increase -> underperformance) does.

**H5 — Short interest.** No discrimination. Populated -3.1% vs missing -1.7% (a size proxy — FCA disclosure skews to larger/shorted names). Short >=1%: -2.9%; <1%: -4.1%; both robust negative, statistically indistinguishable. One nasty sub-cell: short>=1% × small-cap = **-10.5% (n=94, robust)**.

**H6 — 52wk distance + momentum. The cleanest gradient in the scan.** dist_52wk_low quartiles: nearest-low **-6.2% (robust)** -> q2 -5.5% (robust) -> q3 -1.5% -> nearest-high +1.0% (not robust; clustered t -0.8). Momentum same shape: mom_6m<=0 **-5.4% (robust)** vs >0 -0.5%; mom_3m<=0 -4.2% (robust) vs >0 -1.4%. **Settled for our data: directors catching falling knives underperform; the negative leg is robust, the positive leg is not.** This is an avoid-filter, not a buy signal.

**H7 — Stacked conviction (the headline question): NO.** Stack of {opportunistic, reversal, post-results, top-tercile holding} is **monotonically worse**: score 0 = -2.3%, score 1 = -4.0%, score >=2 = -6.4% (17% hit). No pair or triple of flags produces a positive cell at any n; most combos have n<10 (opportunistic barely overlaps anything because of its Feb-2026 start). The stack fails because three of the four flags currently correlate with small-cap/coverage, so stacking concentrates the small-cap drag. A real conviction stack needs the opportunistic flag matured and post-results de-confounded first.

**H8 — Baseline avoid cells under dynamic costs: intact.** CFO×small-cap -12.9% (was -14.7, still robust); mid-cap×tiny-value -13.3% (was -13.2, robust, t=-7.7); small-cap×cluster -9.1% (was -10.2, robust); micro-cap×cluster -15.1% (was -14.2) but **loses the half-sample check** (h2 = +2.4% on n=10 — keep on the list, flag as weakened); T2-exec×small-cap not reconstructable (role_class is ~95% blank in this CSV — worth a look at the exporter). Honest costs did not rescue any avoid cell.

**H9 — Cost model impact.** Overall mean net CAR T+90 is now **-2.66%** (n=981) vs the baseline's -3.5% — but the sample also changed (859->981 matured firings), so not all of the 0.8pp is the cost model. Decomposition on this run: gross CAR T+90 -1.31%; dynamic cost drag averages **135bps round-trip at T+90** (median 105bps; cost_bps mean 139, p10 55, p90 271). The flat-50bps model was *under*-charging small names and over-charging large ones; net effect on the average is modest, but cell-level economics (especially AIM micro-caps at 200-450bps) are now honest. The strategy's problem remains the **gross** number, not the costs.

## 4. Updated avoid-filter (all robust negative, T+90 net)

| Cell | n | mean | hit | t |
|---|--:|--:|--:|--:|
| micro-cap × 3m-momentum<=0 | 75 | -9.7% | 31% | -2.7 |
| mid-cap × tiny value (<£10k) | 76 | -13.3% | 14% | -7.7 |
| CFO × small-cap | 41 | -12.9% | 17% | -3.5 |
| short>=1% × small-cap | 94 | -10.5% | 15% | -3.6 |
| small-cap × cluster | 191 | -9.1% | 25% | -5.6 |
| small-cap × tiny value | 160 | -10.3% | 21% | -5.9 |
| holding-increase bottom tercile | 47 | -7.1% | 19% | -2.6 |
| 52wk nearest-low quartile | 223 | -6.2% | 35% | -4.4 |
| 6m-momentum <= 0 | 410 | -5.4% | 33% | -5.9 |
| micro-cap × cluster (weakened: h2 flipped, n2=10) | 41 | -15.1% | 20% | -2.8 |

New additions vs baseline: momentum/52wk-low cells, bottom-tercile holding increase, short>=1%×small-cap. The post-results cells are robust negative on paper but coverage-confounded — do not act on them yet.

## 5. Caveats

- **Window is still ~13 months** (May-2025 -> Jun-2026), one rising-market regime; the baseline's dominant caveat stands unchanged.
- **367 cells tested** — at this count, ~9 cells would clear |t|>=2.5 by chance alone; the half-sample and clustering checks are what matter, and they killed the only positive.
- **Ticker clustering matters**: repeat buys in one name (PRU 64 rows) inflate row-level t-stats project-wide, not just in the artifact cell. Recommend the next scan adopts ticker-clustered t as the primary bar.
- New-flag n is thin: opportunistic 87 (all post-Feb-26), reversal 61, holding_pct 172, routine 6.
- post_results_flag is a small-cap proxy until reporting-date coverage reaches large caps.
- T+180 half-sample untestable (h2 n=17); T+365 n=144, first-half only.
- role_class is blank on ~95% of rows in this CSV — exporter gap worth a ticket.

## 6. What would move the needle next

1. **Time** — the single highest-value input. The opportunistic cohort (87 firings, all positive-leaning) matures to T+90 by ~Sep-2026. Diarise a re-scan; no build needed.
2. **Reporting-date coverage for mid/large caps** (extend the LSE-diary backfill) — de-confounds H3, currently the most contaminated test.
3. **Pre-2025 price + filing history** — escapes the single-regime window; repeatedly deferred, still the structural fix.
4. **Ticker-clustered stats in the scan harness** — costs nothing, prevents the next PRU/RR artifact.
5. **Exporter fix for role_class** — restores the seniority axis (T2×small-cap unreconstructable today).

---

**Bottom line vs 2026-06-10 baseline:** dynamic costs moved the overall T+90 from -3.5% to -2.66% (partly sample growth), the avoid-filter survived and grew, the six new conviction columns produced **no positive robust cell** — the one that cleared the bar is a repeat-purchase artifact — and the opportunistic flag is the only genuinely hopeful lead, retestable once its cohort matures around September 2026.
