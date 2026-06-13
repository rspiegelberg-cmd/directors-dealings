# Basket Validation Rules — Directors Dealings

**Status:** Pre-registered  
**Registered:** 2026-06-07  
**Author:** B-141 (DIR-68), Sprint 55  
**Purpose:** Codify the statistical discipline that governs the Basket Report so results cannot be data-mined or cherry-picked after the fact.

---

## 1. Pre-registration principle

All basket definitions (signal IDs, size filter, direction, benchmark) **must be fixed before any performance results are examined**. The rule: define what you're measuring, then look at the numbers — never the reverse.

In practice this means:

- The basket config lives in `.data/baskets_config.json`. This file is the source of truth.
- A basket's `signal_ids`, `require_small_cap`, `label`, and `description` fields are locked from the moment the basket is first added to the config.
- **Do not add, remove, or redefine a basket's filter criteria after running `backtest.py` and viewing results.** If you want to test a variant hypothesis, add a *new* basket with a new `id`.
- `early_data: true` in a basket's config entry documents that the basket was defined when n was still below the proven threshold — this is not a disqualifier, it is a transparency flag.

### What constitutes a valid basket addition

Before adding a basket to `baskets_config.json`, the following must be stated in writing (in a B-NNN Linear issue or in the commit message):

1. **Hypothesis**: what director behaviour is being measured and why it should be predictive.
2. **Filter spec**: which `signal_ids`, size class, and direction.
3. **Success bar**: the minimum effect size that would constitute a positive result (e.g. "median net CAR T+90 > +2%, pct_positive_90 > 55%, n ≥ 30").
4. **Date of registration**: must pre-date the first `backtest.py` run that covers these firings.

---

## 2. Statistical gate: n ≥ 30 ("proven" threshold)

Baskets with fewer than 30 matured T+90 firings are classified as **"not proven"** and must be treated as indicative only.

### Implementation (verified 2026-06-07)

`export_baskets_json.py` enforces this automatically:

```python
proven_threshold = config.get("n_proven_threshold", 30)  # from baskets_config.json
proven = n >= proven_threshold                            # per basket
```

`render_baskets.py` reflects the proven/not-proven state:

- **Proven (n ≥ 30)**: n-chip rendered in green; no warning block.
- **Not proven (n < 30)**: n-chip in amber; warning block displayed:
  *"Insufficient data for statistical confidence (n = N, need n ≥ 30). Stats shown for completeness only."*

### Rules

- The `n_proven_threshold` value in `baskets_config.json` is **30** and must not be lowered.
- If a basket reaches n ≥ 30 it is automatically promoted to "proven" on the next `backtest.py` + export run — no manual action required.
- Do not interpret stats from a not-proven basket as confirmation of a hypothesis. Use them only to monitor whether the basket is trending toward significance.

---

## 3. Headline statistic: median (not mean)

All headline CAR figures on the Basket Report are **median net CAR**, not mean. This is intentional and must be preserved.

### Rationale

Director dealing returns have fat tails — a small number of outsized winners (or losers) can dominate a mean. Median is the outlier-resistant summary that best represents the "typical" outcome for a director buying in this basket.

### Implementation (verified 2026-06-07)

`export_baskets_json.py`:

```python
def _median_pct(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(statistics.median(vals) * 100.0, 2)

median_net_car_21 = _median_pct(car21_vals)
median_net_car_90 = _median_pct(car90_vals)
```

`render_baskets.py` labels the headline block:

```python
_car_big(car21, "Median net CAR T+21")
_car_big(car90, "Median net CAR T+90")
```

### Rules

- **Do not switch to mean** without explicit decision and re-registration.
- Mean may be shown as a secondary stat in a future expansion, but median must remain the headline.
- % positive (pct_positive_21, pct_positive_90) is a complementary metric that is not subject to the mean/median rule.

---

## 4. Benchmark and cost deduction

All net CAR figures are computed as:

> **net_car = stock_return − benchmark_return − spread_cost − stamp_duty**

Where:
- Benchmark: `^FTSC` (FTSE Small Cap) for AIM-listed tickers; `^FTAS` (FTSE All-Share) for Main Market tickers. See `signals/benchmarks.py` and B-147 (AIM coverage fix, 2026-06-07).
- Spread: 50 bps (0.50%) on entry.
- Stamp duty: 0.50% on Main Market buys (not applicable to AIM).

### Rules

- Do not change cost assumptions retrospectively. If the spread assumption changes, add a new basket variant or document the change date and apply it only to firings from that date forward.
- Benchmark assignment must be based on `is_aim` in `tickers_meta`, which is populated by `backfill_ticker_meta.py`. If `is_aim` is NULL, `^FTAS` is used as fallback — this is conservative.

---

## 5. Regime caveat

The backtest period (2021–2026) includes the post-COVID rate surge (2022–2023) and a subsequent recovery. **Absolute CAR figures should be interpreted as edge vs the benchmark, not as absolute return.**

A basket showing negative median net CAR during a period of sharp market re-rating does *not* mean the signal has no alpha — it may simply mean the benchmark captured most of the upside and the signal's edge was too small to overcome the spread/stamp cost. The honest question is: *did directors in this basket outperform their sector benchmark?*

The benchmark-relative framing is the primary lens. Absolute return is secondary.

### On the current data (as of 2026-06-07)

The Basket Report as initially built has relatively few T+90-matured firings because the project only began scraping in 2026 and the backtest uses historical data with coverage gaps. Results should be read as early-stage evidence, not as a validated edge.

The forward paper-book (Section 6) is the honest validation test going forward.

---

## 6. Forward paper-book = the honest validation test

The backtest is inherently in-sample for signal design: the signals were calibrated on data from the same period the backtest covers. The only statistically clean evidence of edge is **out-of-sample performance**.

The paper-book (`paper_trades` table, managed by `eval_signals.py` and `backtest.py`) provides this:

- Every signal fired after the paper-book was initialised (B-100, Sprint 29, ~2026-06-04) generates a `paper_trade` row.
- T+21 and T+90 outcomes are written when the holding period matures.
- The Basket Report will incorporate paper-trade performance once enough trades have matured (target: n ≥ 30 per basket from paper-book only).

### Rules

- Do not conflate backtest stats and paper-book stats. Label them separately.
- A basket is only "validated" when n ≥ 30 paper-trade outcomes show consistent edge (median net CAR > 0, pct_positive > 50%, statistically distinguishable from zero at 90% confidence).
- The paper-book is the primary validation metric for any forward investment decision.

---

## 7. Small-cap threshold

The small-cap boundary for basket membership is **£500m market cap** (set in `baskets_config.json` → `small_cap_threshold_gbp`). A ticker with `small_cap = 1` in `tickers_meta` qualifies for basket membership.

### Threshold fix (shipped B-141, 2026-06-07)

`manual_classify.py` previously used `THRESHOLD = 500_000_000` (£500m), which conflicted with `classify_small_cap.py`'s `DEFAULT_THRESHOLD_GBP = 300_000_000` (£500m). Fixed in this sprint: `manual_classify.py` now uses `THRESHOLD = 300_000_000`.

**Effect of fix**: AWE (Alphawave IP, ~£450m) is now correctly classified as large cap and does not qualify for basket membership. ADT1 (£350m) is similarly large cap. Both were previously being marked `small_cap = 1` incorrectly.

Rupert should re-run `manual_classify.py` from PowerShell after this fix — existing rows that already have market_cap_gbp populated will not be updated (the script skips non-NULL rows). Re-run `classify_small_cap.py` to correct any `small_cap` flags for affected tickers, then re-run the backtest pipeline.

---

## 8. Change log

| Date | Change | Author |
|------|--------|--------|
| 2026-06-07 | Initial pre-registration. Baskets f1_small, s1_small, t3_small, t7_small registered. | B-141 Sprint 55 |
