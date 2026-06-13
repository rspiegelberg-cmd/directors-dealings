# Spec: Phase 3 — Signal engine + backtest

**Status:** Approved v1.1 — taxonomy locked 2026-05-05. 5 base signals + 2 add-ons (**T0** combo, **F1** first-time-buy). Thresholds as proposed in v1.0.
**Owner:** Rupert
**Target ship:** Weeks of 2026-05-26 / 2026-06-02 (split across multiple sessions)
**Source:** `backlog.md` rows P3-1 through P3-7; `Directors-Dealings-PM-Brief.docx` Phase 3
**Author:** PM/back-end planning pass, 2026-05-05

---

## Goal

Turn the data we now have (~250 transactions in the live log, growing) into a measurable trading signal. By the end of Phase 3 every signal definition has a published Sharpe ratio, hit rate, and CAR profile. Tiers that don't show edge in *our* data get killed; the survivors become the basis of Phase 5's paper-trading flow.

This is the phase that justifies all of Phases 0-2.

## Phasing within Phase 3

Splitting into three landable chunks:

- **Phase 3.0 — Foundation.** Signal definitions as versioned Python functions; minimal event-study harness (T+1, T+21, T+90 returns vs FTSE All-Share); lookahead-bias unit test. Single command produces a CSV with one row per signal-firing showing the future returns.
- **Phase 3.1 — Costs + cohorts.** Net-of-costs modelling, cohort cuts (seniority, market cap, sector, value, regime). Re-run the harness with these features.
- **Phase 3.2 — Explorer + decision.** Backtest-explorer HTML page; user decision on which tiers to keep or kill.

This spec covers all three. Phase 3.0 is the next session.

---

## Pre-locked decisions

- **Lazy/on-demand evaluation.** A separate script (`.scripts/eval_signals.py`) reads the `transactions` table, evaluates every signal, writes results to the `signals` table. Run after every backfill or `update.py` invocation. NOT integrated into `refresh.py` itself — keeps the daily flow zero-LLM and zero-extra-compute, and lets us iterate signal definitions without touching the upsert path.
- **Signal definitions are versioned Python functions.** Each lives in `.scripts/signals/{signal_id}_v{n}.py` and exports `evaluate(tx_row, conn) -> SignalResult | None`. `signal_version` is in the table PK alongside `signal_id` and `fingerprint` so old + new definitions coexist.
- **Walk-forward discipline.** The signal evaluator is given an "as-of" date and may only see transactions with `announced_at <= as_of`, prices with `date <= as_of`. Any code that touches future data is a unit-test failure.
- **Effective date = `announced_at`, not transaction `date`.** PDMR filings have a 1-7 day disclosure lag. The market reaction starts when the filing is public, not when the trade was placed.
- **Benchmark = FTSE All-Share Index** (Yahoo `^FTAS`). Need to add this to `prices` table; trivial.
- **Costs (Phase 3.1):** 50bps round-trip spread + 0.5% UK stamp duty on buys. No stamp on AIM stocks (need a separate column on `transactions` to mark AIM listings, or fetch from a static list).
- **Trade entry = T+1 close, exit = T+21 / T+90 / T+252 close.** Standard event-study windows.
- **Universe.** Every ticker in the `transactions` table that also has at least 30 trading days of price history.

## Decision (needs your sign-off) — Signal taxonomy

The brief sketched Tier 1-4 + S1 but the table didn't extract cleanly from the docx. Below is my proposed taxonomy, derived from the brief's text and the cited academic foundation (Cohen, Malloy, Pomorski 2012 "Decoding Inside Information"). I'd like a sanity-check before I lock these as the v1 signal definitions.

| Signal | What it fires on | Rationale |
|---|---|---|
| **T1 — Opportunistic CEO/CFO buy** | `type=BUY` AND role contains `chief executive|chief financial|CEO|CFO` AND value ≥ £100,000 | Highest-conviction insider signal. CEO/CFO buying their own stock with significant own-capital is the strongest insider edge in the academic literature. |
| **T2 — Opportunistic exec buy** | `type=BUY` AND role contains other exec markers (Chair, Group, Director) AND value ≥ £25,000, NOT already T1 | Mid-conviction. Other named executives buying meaningful size. |
| **T3 — Opportunistic NED buy** | `type=BUY` AND role contains `non-exec|non executive|NED` AND value ≥ £10,000, NOT already T1/T2 | Lower-conviction but still discretionary. NEDs have different information access than execs. |
| **T4 — Other discretionary buy** | `type=BUY` AND value ≥ £1,000, NOT already T1/T2/T3 | Catch-all for opportunistic buys that don't fit higher tiers (small execs, small NEDs, role unclear). |
| **S1 — Cluster buy** | ≥2 distinct directors at same ticker, all `type=BUY`, dates within 30 days of each other | Already implemented in `detect_clusters.py`. The single most-evidenced subset of insider alpha (per the existing `specs/01-cluster-detector.md`). |
| **T0 — Cluster + opportunistic combo** | A transaction firing **both** S1 **and** any of T1/T2 within the cluster's 30-day window | Highest conviction. The brief's "all three aligned" setup — mirrors the canonical literature finding that opportunistic insider buying confirmed by other insiders at the same firm is the strongest known equity-buy signal. |
| **F1 — First-time buy** | `type=BUY` AND no prior `BUY` row for `(director, ticker)` in the transactions table at any earlier `announced_at` | The director's first-ever buy of this ticker. Possibly stronger than the same director's nth buy (no calendar / DRIP pattern). The `first_time_buy` flag column on transactions is reserved for this signal's output but the F1 evaluator computes it on-the-fly via SQL — no backfill step needed. |

A signal can fire multiple rows (a CEO's first-time buy that's also part of a cluster fires T0, T1, S1, and F1). Each fires its own row in the `signals` table.

**What's deliberately excluded from signals:**

- `type=GRANT|SIP|EXERCISE|SELL_TAX` — non-discretionary, no informational content (per the brief).
- `type=SELL` — sell signals are weaker and decay faster than buys per Fidrmuc et al. 2006 (UK-specific). A separate sell-signal exploration belongs in v2; not Phase 3.0.
- Foreign-currency rows (price=0 from P0-7) — can't compute return without GBP price.

If you have a different mental model of the tiers, override below; otherwise I'll lock these.

---

## Per-item plans

### P3-1 — Signal definitions (Phase 3.0)

**Output.**

- `.scripts/signals/__init__.py` — registry of available signals, version-aware
- `.scripts/signals/t1_ceo_cfo_buy_v1.py`
- `.scripts/signals/t2_exec_buy_v1.py`
- `.scripts/signals/t3_ned_buy_v1.py`
- `.scripts/signals/t4_other_buy_v1.py`
- `.scripts/signals/s1_cluster_buy_v1.py` (wraps the existing detect_clusters.py logic)
- `.scripts/eval_signals.py` — orchestrator: walks the transactions table, evaluates every signal, writes `signals` table

Each signal module exports:

```python
SIGNAL_ID = "t1_ceo_cfo_buy"
SIGNAL_VERSION = "1.0.0"

def evaluate(tx: dict, conn, as_of: date) -> dict | None:
    """Return a SignalResult dict with confidence + metadata, or None.
    Walk-forward: caller passes as_of; evaluator must not look past it.
    """
```

**Test.** `.scripts/test_p3_signals.py` runs each signal against synthetic transactions covering positive + negative cases. Plus a lookahead-bias guard: synthetic store with prices for dates D and D+5; signal evaluation as-of D must not see the D+5 row.

**Token estimate.** ~50k.

### P3-2 — Event-study harness (Phase 3.0)

**Output.** `.scripts/backtest.py`:

```text
python .scripts\backtest.py --signal t1_ceo_cfo_buy
python .scripts\backtest.py --all-signals --output .scripts/_backtest_results.csv
python .scripts\backtest.py --signal t1_ceo_cfo_buy --version 1.0.0 --as-of 2025-04-01
```

For each signal-firing in the `signals` table:

1. Look up the ticker's price on `announced_at + 1 trading day` (entry).
2. Look up the ticker's price on entry + 21, +90, +252 trading days (exits).
3. Look up FTSE All-Share on the same dates.
4. Compute CAR (cumulative abnormal return) for each window: ticker_return − benchmark_return.
5. Output one row per firing with: signal_id, signal_version, fingerprint, entry_date, t+1, t+21, t+90, t+252 returns, t+1, t+21, t+90, t+252 CARs.

Skips firings where price data is incomplete (e.g. ticker delisted, less than 252 trading days of forward data).

**Token estimate.** ~70k.

### P3-6 — Lookahead-bias test (Phase 3.0)

**Output.** `.scripts/test_p3_lookahead.py`:

- Construct synthetic transactions + prices store: 100 trades, prices known for every trading day in 2024-2026.
- For every "as-of" date in the test set, run signal evaluation with hidden prices > as-of.
- Assert: any signal output that uses a hidden price = test failure.

This is the "any code path that touches future data is a bug" guard. Non-negotiable.

**Token estimate.** ~30k.

### P3-3, P3-4, P3-5, P3-7 (Phase 3.1 + 3.2)

Sketched only — these are next-session work after 3.0 lands.

- **P3-3 — Cohort cuts.** Add `seniority`, `market_cap_bucket`, `sector`, `value_bucket`, `regime` columns or joins. Recompute backtest stats sliced by each.
- **P3-4 — Costs.** Add `apply_costs(returns) -> returns_net` helper. 50bps round-trip + 0.5% stamp on non-AIM buys.
- **P3-5 — HTML explorer.** New page `backtest-explorer.html` reading from `_backtest_results.csv` (or a JSON export). Filter by signal/cohort/window. Charts via Chart.js (already in dashboard stack).
- **P3-7 — Decision review.** You read the explorer, decide which tiers have edge worth keeping. Kill the others by setting their signal_version to "deprecated".

---

## Order of execution (Phase 3.0 only — what we ship next session)

```
1. Add FTSE All-Share to prices table (one-shot fetch, ^FTAS)
2. Signal modules + eval_signals.py (P3-1)
3. Lookahead-bias test (P3-6)  -- gating check before any results are trusted
4. Backtest harness (P3-2)
5. Run, produce first result CSV, eyeball
```

Phase 3.0 ships with: every signal evaluated, every firing has CAR vs FTSE All-Share at T+1/+21/+90, lookahead-free. No costs, no cohorts, no HTML.

---

## What we need from Rupert before code

1. **Signal taxonomy sign-off** — are the 5 signals + thresholds above what you want, or do you have your own definitions? In particular the value thresholds (£100k for T1, £25k for T2, £10k for T3) are guesses.
2. **AIM ticker list** — for stamp duty (Phase 3.1). I can fetch a list from LSE or the market_cap reference data already in `mcap.json`. Question for later.
3. **Optional**: any signals you want that aren't on the list? E.g. "first-time buy" (the `first_time_buy` flag exists on transactions but no signal uses it yet) or "sell-after-buy" (insider sells within N days of own prior buy — a contradicting-signal flag).

Once you sign off on (1), Phase 3.0 ships in one focused session.
