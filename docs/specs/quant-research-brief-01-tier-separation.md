# Quant Research Brief 01 — Do the existing tiers actually separate winners from losers?

**Status:** Draft for execution
**Owner:** Rupert
**Agent:** Quant Researcher (`docs/agents/quant-researcher.md`)
**Created:** 2026-06-03
**Type:** Signal-review / validation pass (NOT new-feature ideation)

---

## Why this is the first brief

Before the Quant Researcher proposes a single new data point, we need to know whether the signal taxonomy we *already* ship has genuine, out-of-sample edge — or whether the tiers are decorative. Every downstream research idea (relative buy size, % of market cap, cluster breadth) is a *refinement* of these tiers. If the base tiers don't separate winners from losers once you leave the in-sample period, we're refining noise, and that is the expensive mistake to avoid.

This brief is deliberately a **validation pass, not a discovery pass.** The goal is to harden or kill what exists, not to mine for something new.

## The one-sentence question

> Out-of-sample, do T1, T2, T3, T4, S1, T0 and F1 each produce a CAR profile that is (a) positive, (b) meaningfully different from a random buy, and (c) ordered the way the taxonomy claims (T1 ≥ T2 ≥ T3 ≥ T4; T0 ≥ its components)?

## Scope — what this pass covers

- All seven live signals: T1, T2, T3, T4, S1, T0, F1 (versions currently marked live in the `signals` table).
- Windows T+21 and T+90 (T+1 reported as context only — it's mostly announcement noise).
- CAR net of nothing first (gross), then a second cut net of the 50bps + 0.5% stamp model. Report both; gross tells us if there's signal, net tells us if it survives friction.

## Scope — what this pass deliberately excludes

- No new features. (That's Brief 02 onward.)
- No SELL-side work.
- No threshold *re-tuning* — this pass measures the current cuts; if they fail, re-tuning is a separate, gated decision.
- No production code changes. Output is a written verdict per tier.

## Method — the required steps

Apply the agent's six overfitting rules. Concretely:

1. **Pre-register the expectation per tier.** Write down, before pulling CARs, the predicted sign and rough ordering (the taxonomy already asserts T1 ≥ T2 ≥ T3 ≥ T4; T0 is highest; F1 ≥ generic buy). This is the null we're testing against.
2. **Build the train/test split by time.** Train = earliest 60% of firings by `announced_at`; test = latest 40%. State the exact split date and the N on each side. If a tier has fewer than ~30 firings in the test half, it cannot be validated this pass — mark it "insufficient N" and move on (do not stretch a conclusion out of 8 trades).
3. **Establish the baseline.** Compute the CAR profile of a *generic discretionary BUY* (every `type=BUY` with adequate price history) over the same windows and same split. Every tier must be judged against this baseline, not against zero. A tier that matches the average buy has no incremental edge even if its CAR is positive.
4. **Per tier, report the full stat block** on the **test** half: mean, median, hit-rate, N, and the mean with the single top contributor removed. In-sample numbers are reported alongside only to show the train→test shrinkage.
5. **Ordering check.** Does the test-half mean/median actually rank T1 ≥ T2 ≥ T3 ≥ T4? Plot the four means with their N. If the order inverts or collapses out-of-sample, that is the headline finding.
6. **Multiple-comparison honesty.** We are testing 7 signals × 2 windows = 14 comparisons. Apply the haircut: treat a tier as "validated" only if its test-half edge over baseline is large *and* directionally consistent with train — not on a single lucky window.

## Data (real schema — query a /tmp copy, read-only)

- `transactions` — `announced_at` (effective date), `type`, `role`, `value`, `cluster_id`, `first_time_buy`, `fingerprint`.
- `signals` — which fingerprints fired which `signal_id` / `signal_version`.
- `_backtest_results.csv` — per-firing T+1/T+21/T+90/T+252 returns and CARs vs benchmark. Read fresh.
- Cost model for the net cut: 50bps round-trip + 0.5% stamp on non-AIM buys (`tickers_meta.is_aim`). Flag to Trader that 50bps understates small-cap spread.

Walk-forward is already enforced at the data layer — do not override it. If any step appears to need future data, stop and flag it.

## Deliverable

A single markdown report saved to `docs/research/quant-01-tier-separation_2026-06-03.md`, using the agent's hand-back format, with one stat block per tier and a summary table:

| Signal | Test N | Mean CAR T+21 (net) | Median | Hit-rate | vs baseline | Verdict |
|--------|--------|---------------------|--------|----------|-------------|---------|

…where **Verdict** ∈ {EDGE, PRELIMINARY, NOISE, INSUFFICIENT-N}.

Plus, up top, the three headline answers:

1. **Which tiers survive out-of-sample** (clear incremental edge over a generic buy).
2. **Whether the ordering holds** (does seniority actually pay?).
3. **The single most important kill/keep recommendation** for the next taxonomy review.

## Definition of done

- Every live tier has a verdict, or a stated reason it couldn't be judged (N too small).
- The train/test split date and per-side N are explicit.
- The baseline generic-buy profile is shown, and every tier is judged relative to it.
- A "Limitations of this pass" section is present (always).
- No production code or DB writes occurred.

## What happens next

Rupert reads the report and decides, per tier: keep as-is, re-tune threshold (separate gated brief), or deprecate. Only *after* the base tiers are validated do we move to Brief 02 (new-feature ideation — relative buy size and value-as-%-of-market-cap are the leading candidates).
