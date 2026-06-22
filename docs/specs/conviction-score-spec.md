# Spec — Weekly Conviction Score

**Proposed backlog item:** B-171 (needs Linear ticket)
**Status:** Draft for review
**Date:** 2026-06-18
**Owner:** Rupert / Claude
**Related:** `05-phase-3-signal-engine.md` (7-signal taxonomy), B-155 (routine/opportunistic), B-159 (reversal), B-161 (post-results), B-114 (pre-earnings), B-168 (salary-multiple — **dropped from this spec**)

---

## 1. The problem

The dashboard now carries a lot of signals, badges and flags. Each one fires **binary** — a threshold is crossed, a badge appears. The analysis has become hard to read at a glance, and a firing badge tells you *that* something happened, not *how strongly*.

What's missing is a single answer to one question:

> **"Of everything directors bought this week, which one or two buys are the strongest, and how strong?"**

## 2. Core principle — strength, not threshold

This is the heart of the spec, and it is different from how the engine works today.

- **Today (binary):** a signal fires if a buy clears a fixed bar. A £50k buy and a £5m buy both just "fire" the same badge.
- **This score (graded):** every buy gets a continuous **0–100 conviction score** built from *how far* it sits along each factor, then combined. A buy can score 22 or 88. Two buys that both "fired" a badge can score very differently.

A buy can score high **even if no single signal formally fires**, because it's the *combination* of moderately strong factors that matters. That combination is the thing you've been describing.

### Worked example (Rupert's)

> *A large commitment, to a small-cap, by the Chair (or a PCA tied to the Chair), ~31 days before earnings, in a given sector.*

Each of those is a *dial*, not a switch:

| Factor | This case | Strength read |
|---|---|---|
| Who | Chair / senior-linked PCA | High |
| Buy size | Large vs the company's normal volume | High |
| Company size | Small-cap | High |
| Earnings timing | 31 days out = just **before** the legal close-period lockout | Elevated |
| Sector | Materials | **Context/caution** (see §6) |
| Past performance | (depends — dip vs run-up) | TBD per case |

High dials on five of six → a high composite score → surfaced as a candidate of the week.

## 3. The six factors and how each is scored

Every factor maps its raw value to a **0.0–1.0 strength** via a simple, monotonic curve. No factor is yes/no.

**F1 — Who is buying (role strength).**
Continuous by seniority. Chair / CEO / Founder ≈ 1.0; CFO ≈ 0.9; other exec ≈ 0.7; Co Sec / GC ≈ 0.5; NED ≈ 0.3. **Any PCA at the company counts** (decision 2026-06-18) — not only PCAs formally linked to a named director. A PCA buy **inherits the strength of the most senior director at that company**, so a PCA buying where the Chair sits scores near a Chair. *Note: our own scans show NEDs lag — keep their weight low.*

**F2 — Size of buy.**
Two inputs: absolute £, and **£ relative to the stock's normal daily volume** (the relative measure is what makes a small company's buy comparable to a big one's). Log-scaled, scored by percentile against history. Bigger = stronger.

**F3 — Company size (market cap).**
Smaller = stronger. Inverse-log of market cap, normalised. Micro-cap ≈ 1.0; large-cap ≈ 0.1. (Rationale: insiders are more likely to know more than the market in small, under-covered names.)

**F4 — Earnings proximity.**
A *curve* over days-to-next-results, not a flag:
- **Inside the ~30-day close period → invalid** (directors can't legally deal; if we ever see one, it's a data error to investigate, not a buy).
- **Just before the lockout (~31–45 days out) → elevated** — they bought right before going dark. *This is your example.*
- **Just after results → high** — they saw the numbers and still bought.
- **Mid-cycle → low.**
Inputs exist via B-114 (pre-earnings) and B-161 (post-results), but our notes flag post-results timing as **coverage-confounded** → measure, don't assume.

**F5 — Past stock performance (reversal bias).**
Trailing 1–3 month return, **inverted**: a director buying *after a fall* (a dip-buy / contrarian conviction) scores higher than one buying *into a rally*. Reuses B-159 (reversal) logic. Capped so a crash doesn't max it out automatically.

**F6 — Sector.**
**Not an additive booster — a guardrail.** Used to *discount* a score when the whole sector is running, so we don't mistake sector beta for director skill. *Honest flag for your example: our scans found Materials-small-cap robustly **negative**, and Energy's apparent edge was just the oil price. So sector should pull scores toward caution, not lift them, until forward data says otherwise.*

## 4. The composite score

```
conviction = 100 × ( w1·F1 + w2·F2 + w3·F3 + w5·F5 + w_timing·F4 )
                   × sector_guardrail(F6)
```

- Weights `w1..` sum to 1 before the sector multiplier.
- **Starting weights (provisional, judgment-based — see §5):**
  Who 0.25 · Buy size 0.25 · Company size 0.20 · Earnings timing 0.15 · Past performance 0.15.
- `sector_guardrail` is a 0.7–1.0 multiplier that trims the score when sector beta is elevated.

**Strength bands:** 0–40 Low · 40–60 Moderate · 60–80 High · 80–100 Exceptional.

## 5. Honest calibration stance (read this twice)

"Strong based on past data" is the goal — but **our past data does not currently support strong positive priors.** Every robust backtest cell to date has been flat or negative once properly clustered.

Therefore:
- **v1 weights are priors set by judgment, explicitly provisional — NOT fitted to our in-sample returns** (fitting to a negative sample would just overfit noise).
- Weights get revised **only on out-of-sample forward data** (§7), using clustered t-tests, never on a single eye-catching week.
- The score's honest label in v1 is **"signal strength / research priority,"** not "expected return." We earn the right to call it a buy signal only if the forward record proves it.

## 6. Surfacing rules

- Compute the score for **every** director buy, and persist it (the shadow log).
- Surface a **permanent table showing the top-scoring buys over a rolling trailing 4 weeks (28 days)** (revised decision 2026-06-18). The table refreshes every pipeline run — it is a living leaderboard, *not* a once-a-week snapshot. A buy enters when its dealing date falls inside the trailing 28-day window and drops out when it ages past it.
- Each row shows the **0–100 score, strength band, ticker/company/director/role, and the factor breakdown** (why it scored where it did), plus the sector-beta caution where relevant, and "unknown" for any missing factor input.
- The score does the honest work: a window whose top rows all sit Low/Moderate visibly signals a weak month — no buy is dressed up as strong. Never imply a Low-band buy is a conviction call.
- **Row count and minimum-score bar: see Decisions #1 / #1b.**

## 7. Measure-forward (the safety mechanism)

- Log every surfaced pick with its score **and** its six sub-scores at the moment of firing.
- Also log a **shadow ranking of all buys** (not just the top 2), so we can later test whether the score correlates with returns across the *whole* distribution — a far stronger test than just watching the picks.
- Track each at **T+21 and T+90** vs the sector-matched benchmark (existing `backtest.py` path).
- After ~3 months / N≥30 picks, regress forward CAR on conviction score. If slope is flat/negative, the recipe is rejected cheaply. If positive and robust under clustering, *then* re-weight and promote.

### Adaptive weighting — champion / challenger (decision 2026-06-18)

The weights **learn from realised performance, but every change is proposed for Rupert's approval — never applied silently.**

- The live weight set is the **champion**. It is frozen until explicitly replaced.
- On a **quarterly** cadence (not continuous — avoids chasing monthly noise), the system fits a **challenger** weight set on the accumulated pick history.
- **Walk-forward validation only:** the challenger is scored exclusively on outcomes it was *not* fitted on. No in-sample self-grading.
- A challenger is **proposed to Rupert** only if it beats the champion **out-of-sample, under a clustered statistical test, with a minimum sample** (target N≥30–50 resolved picks). Rupert sees "challenger beat champion by X over N out-of-sample picks — adopt?" and decides. Nothing changes the live score without that approval.
- **If picks are underperforming**, that is itself the trigger to surface a proposed adjustment — the loop exists precisely so a weak recipe gets revised rather than left running on false confidence.
- **Seatbelts:** per-factor weight bounds (no single factor dominates), a hard minimum-sample gate before any re-fit, and regularisation so the challenger can't contort to noise.
- Honest framing holds throughout: until a challenger has *repeatedly* beaten the champion on fresh data, the score stays "signal strength," not "expected return."

## 8. Data inputs — what exists vs what's needed

| Factor | Data status |
|---|---|
| F1 Who | **Exists** — `role_normalize.py` tiers; PCA-inheritance is new logic |
| F2 Buy size | **Exists** — £ in DB; relative-to-volume needs the volume join |
| F3 Company size | **Exists** — market cap (B-097/B-148), ~79% sector/cap coverage after B-170 |
| F4 Earnings timing | **Partial** — B-114/B-161 flags exist; forward earnings dates are the known coverage ceiling (~23%) |
| F5 Past performance | **Exists** — price history; reuse B-159 trailing-return calc |
| F6 Sector guardrail | **Exists** — sector tags + benchmark series |

The earnings-date coverage gap (F4) is the one real data constraint. v1 can run with F4 weighted 0 where dates are missing and the score re-normalised, rather than blocking the whole feature.

## 9. Build phases

1. **Aggregation layer** — a `conviction.py` that reads existing flags/fields and emits the 0–100 score + sub-scores per buy. (Bulk of the value; low risk; Zone A code only.)
2. **Exporter + weekly panel** — top 1–2 with factor breakdown on the dashboard.
3. **Measure-forward log** — pick log + shadow ranking + T+21/T+90 join into `backtest.py`.
4. **Adaptive calibration loop** (deferred ~3 months, until forward data exists) — quarterly champion/challenger re-fit with walk-forward validation; surfaces a *proposed* weight change for Rupert's approval; never auto-applies. Phase 3 logging must capture every sub-score + outcome so this loop has what it needs. *(§7)*

## 10. Out of scope

- Salary-multiple (B-168) — dropped per decision 2026-06-18.
- Any "expected return" or position-sizing claim in v1.
- Sell-side / disposal scoring — buys only.
- Automated trading or alerts — surfacing only.

## 11. Decisions (resolved 2026-06-18)

1. **Surfacing** — a **permanent table of the top 10 buys by score over a rolling trailing 4 weeks (28 days)**, refreshed every pipeline run (revised 2026-06-18; supersedes the earlier weekly-top-3 and Monday-anchor decisions). *(§6)*
1b. **Minimum-score bar:** none — **top 10 regardless of strength**; the scores/bands themselves reveal a thin month (honesty stance). *(§6)*
2. **PCA inheritance** — **any PCA at the company** counts; inherits the most senior director's strength. *(§3 F1)*
3. **F4 when earnings date unknown** — **drop that factor to 0 and re-normalise** the remaining weights, so a missing date doesn't block or penalise the buy. *(§4, §8)*
4. **Weekly cadence** — anchored to the **calendar week (Monday)**.
5. **Adaptive weighting** — weights learn from realised performance via a **champion/challenger** loop, but every change is **proposed for Rupert's approval, never auto-applied**. Underperformance is itself a trigger to propose a revision. Walk-forward validation, quarterly cadence, minimum-sample gate. *(§7)*
