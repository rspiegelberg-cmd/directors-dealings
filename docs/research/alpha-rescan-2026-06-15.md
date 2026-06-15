# Alpha Re-scan — Focused Delta (2026-06-15)

**Type:** focused *delta* scan, not a full 367-cell re-run. Rationale: only **+8 distinct
fingerprints** have accrued since the 11 Jun scan (1,443 vs 1,435) — four days of new
outcomes cannot move the grid — so this run tests **only what is genuinely new since
11 Jun** and re-verifies the existing avoid-filter under a stricter inference bar.

**Input (read-only):** `.data/_backtest_results.csv` (regenerated **Jun 15 08:18**, 2,656
signal rows / 71 cols), deduped to **1,443 distinct fingerprints** by coalescing non-blank
fields across each fingerprint's signal rows. Sector joined from `.data/_snapshots/
tickers_meta.csv` (Jun 13). No Zone-B script was run; this is pure analysis over text exports.

**Method change (the fix flagged on 11 Jun):** **ticker-clustered t is now the primary
bar** (collapse to one mean per ticker, one-sample t on the cluster means). Row-level t is
shown alongside but is no longer trusted on its own — it was inflating significance via
repeat buys in single names (the PRU/RR artifact). Primary outcome = `net_car_t90` (net of
dynamic costs), in percent. Baseline: `docs/research/alpha-rescan-2026-06-11.md`.

What was actually new in the data since 11 Jun:
- **Sector (B-170):** 85 → 557 tickers classified. Not a backtest column, so joined here —
  gives a **sector axis the prior scans never had.** This is the main new capability.
- **role_class:** ~5% → ~25% populated (659/2,656 signal rows). Partially **restores the
  seniority axis** that was unreconstructable on 11 Jun.
- **Salary-multiple (B-168):** schema is live but only **35 deduped rows populated**, all in
  a 0.00–0.07× band (near-zero variance). Built, **not yet analysable** — collection pending.
- **Opportunistic flag:** cohort grew (87 → 118 signal rows) — re-checked here.

---

## 1. Headline verdict

**Core conclusion is unchanged: no tradeable positive cell, the dataset supports an
avoid-filter.** Whole-sample net CAR T+90 = **−2.64%** (n=992, row t −4.2, **clustered
t −1.73**) — statistically indistinguishable from the 11 Jun −2.46%. Directors' UK buys
lagged the benchmark again this year regardless of slicing.

But the new axes did produce three things worth recording:

1. **The new sector axis gives a cleaner avoid-list than the size axis did**, and clustering
   shows several old "robust" avoid cells were partly single-name artifacts (see §4).
2. **One positive sector lead survives clustering: Energy** (+21%, 10 tickers, clustered
   t +2.19) — but it is almost certainly 2025 oil-sector beta, not director skill (§2).
3. **The one hopeful lead from 11 Jun — opportunistic buys — has faded to neutral** as its
   cohort matured (§3). Downgrade.

---

## 2. New axis: SECTOR (the headline new capability), net CAR T+90

Sector populated on 1,434/1,443 deduped rows. Clustered t is the bar.

| Sector | n | tickers | mean | hit | clustered t | read |
|---|--:|--:|--:|--:|--:|---|
| **Energy** | 19 | 10 | **+21.2%** | 79% | **+2.19** | only positive sector that survives clustering — but see caveat |
| Financials | 216 | 50 | +1.1% | 52% | +0.66 | flat, not robust |
| Communication Svcs | 46 | 15 | +0.8% | 48% | −0.63 | flat |
| Industrials | 194 | 54 | −1.3% | 45% | −0.68 | flat |
| Consumer Staples | 108 | 23 | −2.3% | 29% | **−3.07** | robust negative |
| Health Care | 35 | 21 | −4.1% | 31% | −1.05 | negative, not robust |
| Consumer Discretionary | 118 | 41 | −5.5% | 31% | −1.43 | **row t −3.6 collapses under clustering** |
| Materials | 85 | 23 | −5.9% | 40% | +0.46 | **sign flips under clustering — artifact** |
| **Real Estate** | 52 | 16 | **−11.6%** | 4% | **−2.98** | robust negative (4% hit rate) |
| **Technology** | 82 | 31 | **−11.9%** | 20% | **−2.63** | robust negative |

**Robust negative sector cells (clustered |t| ≥ 2):** Technology, Real Estate, Consumer
Staples; and at the sector×size level **Real Estate × large-cap −8.0% (t −4.94)**,
**Consumer Discretionary × large-cap −5.1% (t −2.14)**, **Materials × small-cap −20.8%
(t −2.00)**. Director buys in UK Tech and Real Estate were a strong fade this year.

**Energy caveat (important):** +21% is inflated by EOG (+95%, 2 buys), but the breadth is
real — 8 of 10 tickers positive (SQZ +38, ITH +34, HTG +13, ZPHR +11, HBR +9…), spread
across May-2025→Feb-2026, not one window. The clustered t already neutralises the EOG
repeats and still clears +2.19. **However:** n=10 tickers, one rising-oil regime, and
"director buys an energy stock while energy rallies" is sector beta, not demonstrated stock
selection. Treat as a watch-item, not a buy-list — re-test when the window lengthens.

## 3. New axis: SENIORITY (role_class restored to ~25%), net CAR

The T2 ("other exec") cell that was **unreconstructable on 11 Jun is now measurable.**

| Cohort | n | tickers | mean | clustered t | read (horizon) |
|---|--:|--:|--:|--:|---|
| **T3 (NED)** | 202 | 126 | **−2.30%** | **−2.13** | robust negative (T+90); also T+30 −2.15%, t −2.95 |
| **T7 (Chair)** | 41 | 31 | −3.89% | **−2.56** | robust negative at **T+30**; T+90 not robust |
| T4 (other) | 71 | 33 | −4.45% | −1.95 | borderline negative |
| T1b (CFO) | 14 | 10 | −8.59% | −1.49 | negative-leaning, not robust, thin |
| T1a (CEO/founder) | 29 | 25 | −3.36% | −0.90 | flat-negative |
| **T2 (other exec)** | 29 | 14 | **+5.10%** | +0.88 | **restored, positive-leaning, NOT significant** |

**Read:** the largest seniority group, **NEDs (T3), robustly lag** at both T+30 and T+90 —
a usable addition to the avoid-filter. The headline "T2 exec buys carry information" cell is
finally visible and *leans positive*, but at n=29 / 14 tickers it does not clear the bar —
inconclusive, worth re-testing as role_class coverage and maturity grow. CFO×small-cap and
T2×small-cap remain too thin (n=1, n=5) to read.

## 4. Salary-multiple (B-168): built, not yet analysable

35/1,443 deduped rows populated, all in a **0.00–0.07× annual-pay band** (median 0.009) —
near-zero variance, 6 distinct tickers. T+90: +1.35% (n=22, t +0.61); T+30: −1.09% (n=33).
**No conclusion possible.** The factor needs the pending pay collection (per-company AR-PDF
pass, ~20–30h, currently 7 manual seeds) before it can be tested. Diarise with the
opportunistic re-test.

## 5. Opportunistic flag — the 11 Jun lead has faded

| Horizon | n | mean | clustered t | vs 11 Jun |
|---|--:|--:|--:|---|
| T+1 | 87 | −0.71% | −1.83 | now negative |
| T+30 | 72 | −0.87% | +0.43 | **was +0.6% → now slightly negative** |
| T+90 | 20 | +0.18% | −0.01 | **was +1.3% → now flat** |

As the cohort grew, the positive lean **decayed toward neutral/negative.** Still immature at
T+90 (n=20), but it is no longer the clearly-positive lead it looked like on 11 Jun.
**Downgrade.** Keep the ~Sep-2026 re-test diarised, but expectations should be lower.

## 6. Avoid-filter re-verified under clustering — what survives, what was an artifact

This is the methodological payoff of switching to ticker-clustered t.

| Cell | n | mean | row t | **clustered t** | verdict |
|---|--:|--:|--:|--:|---|
| mid-cap × tiny value (<£10k) | 26 | −9.6% | −3.8 | **−3.05** | **survives — robust** |
| holding-increase bottom | 8 | −10.0% | −3.2 | **−3.03** | survives but n=8 (thin) |
| 6m-momentum ≤ 0 | 415 | −5.4% | −5.9 | **−2.25** | **survives — robust** |
| 52wk near-low (≤10%) | 142 | −4.6% | −2.7 | **−2.03** | **survives — robust** |
| small-cap × tiny value | 162 | −10.3% | −5.9 | −1.76 | **weakened** under clustering |
| micro-cap × 6m-mom ≤ 0 | 74 | −6.2% | −2.0 | −1.22 | **weakened** |
| **short ≥1% × small-cap** | 96 | −10.5% | −3.7 | **+0.03** | **COLLAPSES — was a few-ticker artifact** |

**Lesson confirmed:** row-level t over-stated several avoid cells. `short≥1%×small-cap`
(a flagged robust-negative on 11 Jun) is a repeat-name artifact — gone under clustering.
The **durable, regime-independent avoid signal** is the combination of **small/mid cap +
negative 6-month momentum + price near its 52-week low + negligible holding increase**.
Add **NED (T3) buys**, **UK Tech and Real Estate** to the sector-level avoid list.

---

## 7. Caveats (unchanged in spirit from 11 Jun)

- Still a **~13-month, single rising-market regime** (May-2025 → Jun-2026). This dominates
  everything, the Energy lead most of all.
- Sector and role coverage, while much improved, are **maturity-skewed**: newly-classified
  rows are recent, so several sector×size and role cells are thin at T+90.
- Clustering uses cluster-collapsed means (one obs/ticker); with 10–50 tickers per sector,
  cluster t is conservative — small-n positives (Energy, T2) will stay "not robust" until
  more names accrue, by design.
- Salary-multiple effectively absent; opportunistic still pre-maturity at T+90.

## 8. What moves the needle next (revised)

1. **Time, still #1** — but the opportunistic re-test now matters *less* (lead faded);
   the higher-value maturation is **sector × size** and **NED/exec** cells reaching T+90 n.
2. **Finish the B-168 pay collection** — until then salary-multiple is dead weight in the export.
3. **Pre-2025 history** — the only escape from the single energy-up regime that makes the
   Energy and T2 leads currently unfalsifiable. Still the structural fix.
4. Ticker-clustered t is now in this harness — keep it as the default bar for all future scans.

---

**Bottom line vs 11 Jun:** verdict holds — avoid-filter, no buy-list. The new **sector axis**
is the real gain: it sharpens the avoid-list (Tech, Real Estate, Materials-small, NEDs) and,
via clustering, exposes that `short×small-cap` was an artifact. **Energy is the only positive
that survives clustering, but it reads as oil-sector beta, not skill.** The opportunistic lead
has faded to neutral. Salary-multiple is not yet testable.
