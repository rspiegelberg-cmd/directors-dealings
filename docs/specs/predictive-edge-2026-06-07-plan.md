# Predictive Edge — Market-Cap Enrichment + Basket Report (Sprint 53)

**Date:** 2026-06-07 · **Milestone:** Sprint 53 — Predictive Edge · **Linear:** DIR-64 → DIR-69 (B-137 → B-142)

## Why

The dashboard today is **descriptive** — it measures the CAR of signals that already fired. Rupert's goal is **predictive**: identify the director-buy patterns ("baskets") that will perform well going forward. This sprint builds the measurement surface (Basket Report) and unblocks the data needed to test the chosen predictive theses.

**Success basis (Rupert, 2026-06-07):** a basket is judged on whether it **beats the sector benchmark, net of costs** (net CAR) — not absolute return. Absolute is shown for context.

## The finding that drives the sprint

The flagship thesis — **conviction chairman buys in small-caps** — is currently **unmeasurable**, because we cannot identify which stocks are small-cap:

| Basket | n (unique buy firings) | Net CAR T+21 | Net CAR T+90 |
|---|---|---|---|
| All director buys (baseline) | 1,264 | −2.3% | −6.2% |
| PCA buys | 36 | +0.1% | −9.1% |
| Chairman buys (any cap) | 104 | −2.7% | −13.3% |
| **Chairman, small-cap (AIM)** | **1** | — | — |
| **Conviction chairman small-cap** | **0** | — | — |

Root cause: market cap is known for only **31/605** tickers (all large, hand-curated), and only **24/1,264** firings are tagged AIM. Yahoo does not return market cap for `.L` symbols. Until we enrich market cap, the thesis cannot be confirmed or killed.

Two honest caveats carried forward into the page:
- **Regime:** the market surged over the window, so nearly every basket is negative on a benchmark basis *by construction*. Judge baskets on their edge vs the baseline, not the sign of CAR.
- **Median over mean:** means are outlier-driven (e.g. NED buys: mean +6.2% but median −0.3%). Median is the headline statistic.

## Data-source decisions (research 2026-06-07)

- **Shares outstanding / market cap:** scrape lse.co.uk SharePrice page (`?shareprice=TICKER`) — "Shares in Issue" + "Market Cap" sit in the page meta tag; verified on a £6.5m AIM micro-cap. Store shares as source of truth, compute cap = shares × daily price. Fallback/cross-check: RNS Total Voting Rights via Investegate (already ingested). **No paid API needed** — all free API tiers are US-only or rate-capped.
- **Forward earnings dates:** keep lse.co.uk financial diary (best free source; covers AIM; all results types). Fallback gap-filler: Investegate "Notice of Results" RNS. Structural limit: micro-caps often confirm dates only ~2–4 weeks ahead — true of every free source.

## Work items

| B-ID | Linear | Item | Pri | Pts | Depends on |
|------|--------|------|-----|-----|-----------|
| B-137 | DIR-64 | Market-cap & shares-in-issue enrichment (lse.co.uk scrape; `backfill_market_cap.py`) | High | 5 | — |
| B-138 | DIR-65 | Small-cap classification + basket filter inputs (widen `is_aim`, `small_cap` flag) | Med | 3 | B-137 |
| B-139 | DIR-66 | Backtest re-run + validate baskets populate (incl. AIM benchmark fix) | Med | 3 | B-137, B-138 |
| B-140 | DIR-67 | Basket Report production page (`render_baskets.py` + config + nav + nightly build) | Med | 5 | B-139 |
| B-141 | DIR-68 | Basket validation discipline (pre-registration + n≥30 gate) | Low | 2 | — |
| B-142 | DIR-69 | Automate daily LSE diary scrape (`daily_diary_scrape.bat` shipped; schtasks) | Med | 1 | — |

## Sequencing

1. **B-137** market-cap enrichment — the unblock.
2. **B-138** small-cap classification on top of it.
3. **B-139** re-run backtest; flagship baskets now populate with CAR.
4. **B-140** wire the Basket Report into the live dashboard (preview already built: `outputs/baskets.html`).
5. **B-141 / B-142** run in parallel — discipline doc + scrape automation (B-142 just needs the one-time schtasks registration).

## Already done this session (Zone A)

- Dashboard-designer spec for the Basket Report page.
- Standalone working preview: `outputs/baskets.html` (ranked by net CAR, baseline pinned, n<30 flagged).
- `daily_diary_scrape.bat` (the B-142 script).

## Out of scope (v1)

In-browser basket CRUD, per-basket drill-down pages, compound filter builder UI, statistical-significance tests beyond the n-gate, opportunistic-vs-routine and first-time-buy signals (need 2–3 yrs history; `first_time_buy` field also unpopulated).
