# Spec: Phase 4 — Behavioural buy signals (B1, B2) + universe cleanup

**Status:** Draft — for Rupert's review
**Author:** Brainstorm pass, 2026-05-15 (Cowork session)
**Source:** Analysis of `bt_20260515T174243Z` (2,348 firings) + scrape cache audit
**Target ship:** Stage 5.5 / next dedicated session

---

## TL;DR

After analysing 11 months of live data we have **two clear, evidence-based proposals** and one **honest limitation**.

**Proposals**

1. **Universe cleanup** — exclude closed-end funds and investment trusts entirely, and tighten what counts as a "buy" so vestings, LTIP grants, Sharesave, SIP, and DRIP rows can never leak into a signal basket.
2. **Two new behavioural signals to add to the existing taxonomy**:
   - **B1 — Lone Conviction Buy.** Single director acts alone (no other director firings within ±30 days), buys ≥ £200,000 on-market, and the stock isn't in the "falling-knife" momentum zone (-10% to -2% over 60 days).
     Result on backtest: **55% hit rate at T+21**, +1.63% median, +2.32% mean CAR, n=20. Versus baseline F1 = 33.5% hit.
   - **B2 — Anti-Crowded Cluster Kill Filter.** When ≥ 4 distinct directors fire buys on the same ticker within a 30-day window, suppress *all* signals on that ticker for 60 days.
     Result on backtest: the basket being suppressed has 22.8% hit rate (n=57) and -3.09% mean CAR. These are mostly directors "supporting" falling stocks (HAS, FOXT, TATE pattern). Suppressing them lifts every other signal's hit rate by removing the dilutive tail.

**The honest limitation**

Rupert's stated bar is 65% hit rate. **No clean cut on this 11-month dataset reaches 65% with n ≥ 20.** The tightest meaningful cut we found is B1 at 55%. Tightening further drops sample below 15, where small-sample variance dominates. The path to 65% almost certainly requires:

- More time (≥ 18–24 months of data for statistical power on a 5–10pp hit-rate lift), and/or
- New data dimensions not currently in the system: news/sentiment context, earnings-window proximity, sector-relative momentum, the director's full trading history beyond first-buy.

Recommended: ship B1 + B2 + universe cleanup *now* (they improve the system meaningfully even if they don't yet clear the 65% bar), and treat the gap to 65% as Phase 4.1's roadmap.

---

## 1. What changed in our understanding

Three things became clear from the analysis pass:

### 1.1 Hit rates across all existing signals are 27–45% at T+21

Cleaned of |CAR| > 50% outliers, every current signal (T0/T1/T2/T3/T4/S1/F1) shows a negative mean net CAR at T+21 and T+90. The "edge" the academic literature describes is not present in the current basket as-is. **Either we need to find the subset where edge exists, or kill the signals that don't deliver.**

### 1.2 Cluster intensity is currently anti-predictive

| Other directors buying same ticker within 30d | Hit rate T+21 | Sample |
|---|---|---|
| 0 (lone buyer) | 40.0% | 350 |
| 1 | 31.3% | 214 |
| 2 | 26.7% | 116 |
| 3 | 26.2% | 61 |
| ≥4 | 22.8% | 57 |
| ≥5 | 18.2% | 22 |

This is **the opposite of what S1 (cluster) is supposed to capture**. The "everyone is buying" basket in our UK 2025/26 data is contaminated by defensive purchases in distressed names. Hays plc (HAS) had 12 separate first-buy firings across multiple directors as the stock fell from 65p toward 50p — every one lost money. Foxtons (FOXT), Tate & Lyle (TATE), Bountiful Vehicle Trust (BVT), Card Factory (CARD) all show the same pattern.

This means the existing **S1 cluster signal is currently a negative signal** in the form we've coded it.

### 1.3 The lone, large, executive buy *does* work — but the sample is small

Filtering down to single buyers acting alone, with size that requires real own-capital deployment, with the stock not in a structural drift-down — produces the cleanest positive cohort we can identify.

This is B1 below.

---

## 2. Universe cleanup — non-negotiable pre-requisites

These two filter changes apply to **every** signal evaluator (not just B1/B2):

### 2.1 Investment-trust exclusion

Closed-end funds, REITs, BDCs, and venture-capital trusts have routine alignment-buying behaviour that contains no information about underlying value. Directors buy small parcels at the manager's encouragement, sometimes monthly. These are noise in our basket.

**Mechanism**:
- Add a `is_investment_trust BOOLEAN` column to the `companies` reference table (or to `transactions` directly).
- Seed from the existing `sector_map.csv` (Financials sector with ≥4-char ticker is a starting heuristic but produces false positives like Schroders, abrdn — these are managers, not trusts).
- Cross-reference with a fetched AIC member list (Association of Investment Companies publishes this); fall back to scanning the Investegate URL slug for "trust", "investment company", "investments plc", or matching known manager prefixes (JPM, BG, BR, F&C, M&G, abrdn, Henderson).
- **All signal evaluators must check this flag and skip the row if true.**

Preliminary list of 141 tickers identified from the cache during the brainstorm pass — included as `docs/specs/_trust_tickers_draft.txt` for review and curation before commit.

**Important caveat**: an audit pass to remove false positives is required. The draft list incorrectly tagged Secure Trust Bank (STB), Associated British Foods (ABF), BAT, abrdn, Schroders, St James's Place, Polar Capital — these are operating businesses. The draft has had these removed.

### 2.2 Strict-buy reclassification

Audit of the announcement HTML cache turned up multiple cases where vesting + tax-sell, LTIP grants, Sharesave option grants, SIP DRIP buys, and "PCA following PSP/RSP vesting" entries appear to be flowing into the first-time-buy basket as `type=BUY`. Examples:

- **HAS 2025-06-03** (Deborah Dorman): "The acquisition of 65,703 ordinary shares … following the vesting of an award … under the Restricted Share Plan, [and] the on-market sale of 30,958 ordinary shares" — this is a vest + sell-to-cover-tax. Not a discretionary buy.
- **TRST 2025-06-10**: "Grant of Long Term Incentive Plan Awards" — pure LTIP grant. No cash deployment. Not a buy.

**Mechanism** — update `parse_pdmr.py` classification rules:

```text
NON_BUY = matches ANY of:
  vesting of an award
  restricted share plan | RSP
  long term incentive plan | LTIP
  performance share plan | PSP
  deferred bonus plan
  sharesave | SAYE | save[- ]as[- ]you[- ]earn
  scrip dividend | dividend reinvestment | DRIP
  share incentive plan | SIP
  grant of (a|an|conditional|options?) (award|conditional)
  grant of options?
  exercise of options?
  nil[- ]cost option
  vested
  award of \d+
  employee benefit trust

STRICT_BUY = matches "on[- ]market purchase of (ordinary )?shares"
             OR matches "purchase of (ordinary )?shares" AND has Price > 0 in section (c)
             AND does NOT match any NON_BUY pattern
```

The classifier should write the rule output into a new `transactions.buy_strictness` column with values:
`STRICT_BUY` | `MIXED` (both buy and non-buy phrases present — needs LLM disambiguation) | `NON_BUY_ONLY` | `UNKNOWN`.

**Signal evaluators must require `buy_strictness = 'STRICT_BUY'` to fire any T/S/F/B signal.** Everything else is excluded.

---

## 3. Signal B1 — Lone Conviction Buy

### 3.1 Definition

A T+1-entry buy signal fires when **all** of the following are true:

| Condition | Value |
|---|---|
| `transactions.type` | `BUY` |
| `transactions.buy_strictness` (from §2.2) | `STRICT_BUY` |
| `companies.is_investment_trust` (from §2.1) | `FALSE` |
| Value of trade (£GBP) | `≥ 200,000` |
| Number of *other* directors firing buys on same ticker, within ±30 days of `announced_at` | `= 0` |
| 60-day trailing return at `announced_at` close vs same ticker 60 trading days prior | **NOT in [-10%, -2%]** (i.e., either > -2% OR < -10%) |

The 60-day trailing return excludes the "shallow downtrend" zone where directors typically buy out of optics rather than conviction (the falling-knife trap).

### 3.2 Rationale

This signal isolates the cohort the academic literature describes as opportunistic: a single director, deploying meaningful own-capital, with no surrounding "consensus buying" that signals defensiveness, in a stock whose momentum doesn't scream "in trouble". The cluster-intensity table (§1.2) shows lone buyers outperform every clustered configuration. The size threshold ensures we're not capturing token alignment purchases.

The momentum filter is the most subtle. Empirically the bimodal pattern dominates: stocks down >15% (capitulation bottoms) or stocks up >5% (momentum confirmation) both hit ~40%; the middle "drift down" zone collapses to 27%. We exclude only the bad zone, not require either extreme.

### 3.3 Expected performance (on current backtest data)

| Metric | Value |
|---|---|
| Firings over 11 months | **20** |
| Hit rate at T+21 (net of 100bps costs) | **55.0%** |
| Median net CAR T+21 | **+1.63%** |
| Mean net CAR T+21 | **+2.32%** |
| Hit rate at T+90 | ~37% (small sample, n=19) |

Annualised firing rate: ~22 per year. This is a **low-volume, high-conviction** signal — not designed to fill a backtest with frequent firings but to find the cohort with the cleanest evidence.

### 3.4 Limitations to declare upfront

- **Sample size of 20 is small.** A single concentrated bad month could flip the hit rate to <45%. The 55% should be quoted with a confidence interval of approximately ±15pp.
- **The £200k threshold is empirically derived from this dataset.** It should not be overfitted further. We should commit to it and re-evaluate after another 6 months of data, not tune it again.
- **Does not yet reach the 65% target.** This is the honest gap, see §6.

### 3.5 Implementation

Add new file `.scripts/signals/b1_lone_conviction_buy_v1.py`:

```python
SIGNAL_ID = "b1_lone_conviction_buy"
SIGNAL_VERSION = "1.0.0"

def evaluate(tx, conn, as_of):
    # Pre-conditions handled centrally before evaluate() is called:
    #   - tx['type'] == 'BUY'
    #   - tx['buy_strictness'] == 'STRICT_BUY'
    #   - companies.is_investment_trust == FALSE
    if tx['value_gbp'] < 200_000:
        return None

    # Count other directors firing on same ticker within ±30d of announced_at
    other_count = conn.execute("""
        SELECT COUNT(DISTINCT pdmr_name)
        FROM transactions
        WHERE ticker = ?
          AND type = 'BUY' AND buy_strictness = 'STRICT_BUY'
          AND date(announced_at) BETWEEN date(?, '-30 days') AND date(?, '+30 days')
          AND pdmr_name != ?
          AND announced_at <= ?
    """, (tx['ticker'], tx['announced_at'], tx['announced_at'],
          tx['pdmr_name'], as_of.isoformat())).fetchone()[0]
    if other_count > 0:
        return None

    # Momentum filter: trail60
    entry_close, prior_close = get_close_pair(conn, tx['ticker'], tx['announced_at'], 60)
    if entry_close is None or prior_close is None or prior_close == 0:
        return None
    trail60 = (entry_close - prior_close) / prior_close
    if -0.10 <= trail60 <= -0.02:  # falling-knife zone
        return None

    return {
        'signal_id': SIGNAL_ID,
        'signal_version': SIGNAL_VERSION,
        'confidence': 'high',
        'metadata': {'trail60': trail60, 'value_gbp': tx['value_gbp']},
    }
```

---

## 4. Signal B2 — Anti-Crowded Cluster (kill filter)

### 4.1 Definition

When a ticker has **≥ 4 distinct directors firing strict buys within a rolling 30-day window**, suppress every other signal (T1/T2/T3/T4/S1/T0/F1/B1) on that ticker for the **60 calendar days following the 4th director's firing**.

### 4.2 Rationale

The cluster-intensity slice in §1.2 is unambiguous: dense director clusters in our UK data are anti-signal, not signal. The pattern is "Chairman + 3 NEDs all show up to buy at the same time" — virtually always coordinated optics around a struggling stock. Hit rates in the 4+ cluster bucket are 18–23%; mean returns are -3% to -5%.

Rather than redefine S1 (which would need its own validation), this is implemented as a **negative gate** that overrides positive firings. It's defensive — designed to remove a known-bad sub-basket from production.

### 4.3 Expected effect on existing signals

Applying the kill filter to the 11-month backtest would suppress 57 firings (out of 798 non-trust F1) with 22.8% hit rate. Removing those from the F1 basket lifts F1 hit rate from 33.5% → ~34.4% — small but real, and more importantly it removes the worst-quality firings.

The bigger value is **trust**: when B1 fires, we have high confidence the basket is clean of the worst pathology.

### 4.4 Implementation

Add to `.scripts/signals/__init__.py` orchestration logic:

```python
def is_suppressed_by_b2(tx, conn, as_of):
    """Return True if this ticker is in a 60-day post-cluster suppression window."""
    suppression_start = conn.execute("""
        WITH window AS (
          SELECT announced_at, ticker,
                 COUNT(DISTINCT pdmr_name) OVER (
                     PARTITION BY ticker
                     ORDER BY announced_at
                     RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND CURRENT ROW
                 ) AS distinct_directors_30d
          FROM transactions
          WHERE ticker = ? AND type='BUY' AND buy_strictness='STRICT_BUY'
            AND announced_at <= ?
        )
        SELECT MAX(announced_at) FROM window
        WHERE distinct_directors_30d >= 4
    """, (tx['ticker'], as_of.isoformat())).fetchone()[0]

    if suppression_start is None:
        return False
    days_since = (date.fromisoformat(tx['announced_at'][:10])
                  - date.fromisoformat(suppression_start[:10])).days
    return 0 <= days_since <= 60
```

This gate is called once per `(tx, signal)` pair before each signal's `evaluate()`. SQLite doesn't have window functions with date ranges natively — the actual implementation should pre-compute the suppression-window table when `eval_signals.py` starts.

---

## 5. What this does to the existing signal taxonomy

The brief's existing T1–T4/S1/T0/F1 taxonomy stays in place — these are not being killed (yet — see §6.2 "Decisions Rupert needs to make").

But two things change:

1. **All signals get filtered through the universe cleanup** (no trusts, strict buys only). This will mechanically shrink every signal's basket by roughly 30–50%.
2. **B1 and B2 are additive.** B1 is a new, narrow, high-conviction signal. B2 is a defensive gate that suppresses everyone (including B1) in compromised contexts.

After the cleanup, we should expect basket sizes roughly:
- T1 (CEO/CFO ≥ £100k): ~20 firings/year, hit ~35–45%
- B1 (lone conviction): ~22 firings/year, hit ~50–55%
- S1 (cluster): may collapse to near-zero firings once B2 suppression bites — this is a feature, not a bug. We can then deprecate S1 cleanly.

---

## 6. The honest gap to 65%

The 65% target is achievable in principle — academic literature reports 55–60% for the strongest US insider baskets, and 65% is reachable on richer feature sets — but **not on the current data dimensions**. To close the gap, in priority order:

### 6.1 New data dimensions worth adding (priority order)

| # | Feature | What it would catch | Cost |
|---|---|---|---|
| 1 | **Days-since-results filter** | Buys in the 21-day window post-results (when director has fresh data) are higher conviction than mid-cycle. | Low — Yahoo + Companies House publish results dates. |
| 2 | **Concurrent-sell suppression** | If any director sold the same ticker in the previous 60 days, suppress the buy signal. Mixed insider signals are weak signals. | Low — already in `transactions` table; just needs a query. |
| 3 | **Director-history opportunistic flag** | The CMP 2012 paper classifies *directors* (not roles) as routine vs opportunistic based on their historical trading regularity. The opportunistic cohort carries virtually all the alpha. | Medium — needs longer trading history per director; viable now for repeat names. |
| 4 | **Sector-relative benchmark** | Currently all CAR vs FTSE All-Share (largecap-dominated). Small-cap director buys lose 2–3% per quarter purely to factor exposure. Fix the benchmark. | Medium — ETF proxies (ISF, MIDD, CUKS, AIME) are available. |
| 5 | **News-context overlay** | Buy + recent profit warning = value trap. Buy + recent positive update = momentum confirmation. LLM classifier on prior 30 days of RNS for the ticker. | High — token spend per firing, but highest ceiling. |

### 6.2 Decisions Rupert needs to make

1. **Approve the universe cleanup as a hard pre-requisite.** §2.1 (trust exclusion) and §2.2 (strict-buy classifier) must land first; B1/B2 depend on them.
2. **Approve B1 and B2 to ship.** Or push back on the definitions.
3. **Decide the 65% timeline.** Do we want to:
   - **(a)** Ship cleanup + B1 + B2 now and let the data accumulate for 6 months before another tuning pass; or
   - **(b)** Ship cleanup + B1 + B2 and immediately invest in dimensions #1 and #2 from the table above (low-cost data adds) targeting 60% hit rate; or
   - **(c)** Pause feature work and run a Phase 3.5 "data dimensions" project (#1–#4) before any new signal ships.
4. **Decide whether to deprecate any existing signals.** T3 (NED ≥ £10k) has 27% hit rate, no edge. T1 (CEO/CFO ≥ £100k) has 31% hit rate at n=35 — borderline. The brief explicitly authorised killing tiers that don't show edge. Worth using that authority before adding more.

---

## 7. Open questions

- The list of investment trusts in `docs/specs/_trust_tickers_draft.txt` is 141 tickers, drafted from URL slug pattern matching and known prefixes. It needs a human eyeball pass before commit. **Estimated time: 30 min of curation.**
- The £200,000 threshold in B1 is empirically chosen. Is there a principled lower bound based on average UK director annual cash compensation? £100,000 would give 41 firings at 44% hit; £200,000 gives 20 at 55%. The trade-off is sample size vs precision.
- The ±30 day "lone buyer" window in B1 — is 30 the right number? 14 would loosen (more firings, lower hit); 60 would tighten. Worth a quick parameter sweep before commit.
- Do we want B2's 60-day suppression window to also block *future* B1 firings, or only the legacy T/S signals? My recommendation: yes, suppress everything including B1, because a fresh "lone" buyer arriving the day after a 4-director cluster is almost certainly still part of the same defensive episode.

---

## 8. Reference data appended

- **Cluster intensity table** (§1.2) — derived from 798 non-trust F1 firings, cleaned of |CAR|>50% outliers.
- **Sector hit rates** — Materials 77%, Utilities 50%, Health Care 44%, Real Estate 7%, Financials 22% (small samples; for orientation only, not for signal design).
- **Backtest source**: `bt_20260515T174243Z` from `.data/_backtest_results.csv`, run after the date-integrity fix landed.
