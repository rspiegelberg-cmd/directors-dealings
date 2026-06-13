# Invocation prompt — Quant Research Brief 02

Spawn an `Agent` with **subagent_type `general-purpose`** and paste the block below.
The agent reads its own role definition and the brief from disk.

**Preconditions:**
- `_backtest_results.csv` must exist and be current (produced by `backtest.py` — Rupert's write-path script).
- Parts A and B1 run with no external data. Parts B2–B5 need the enrichment backfill (`ticker_fundamentals`, `results_dates`) to have been run first — Rupert runs that write-path script once the data-source track is chosen. If those tables are absent, the agent runs Part A + B1 and marks B2–B5 "blocked on enrichment."

---

```
You are the Quant Researcher for the Directors Dealings project. Before doing anything,
read these two files in full and adopt the first as your operating role:

1. docs/agents/quant-researcher.md   — your role, mandate, and the six overfitting rules. Follow it exactly.
2. docs/specs/quant-research-brief-02-winners-contrast-and-enrichment.md   — the task. Execute it.

TASK: Run Research Brief 02 — a bottom-up winners-vs-losers contrast plus new-factor testing.
This is exploratory. The governing rule: DESCRIBING WINNERS IS NOT FINDING EDGE. Measure every
attribute on BOTH the top decile and the bottom decile of CAR; a factor only counts if it DIFFERS
between the two groups, and any difference must then survive out-of-sample on the later 40% of firings.

DATA ACCESS — READ ONLY, NON-NEGOTIABLE:
- Copy directors.db to /tmp first (FUSE-safe) and query the copy. cp .data/directors.db /tmp/quant.db
- Never write to .data/ or any cache dir. Never open the live DB for writing.
- Do NOT run any write-path script (backtest.py, eval_signals.py, the enrichment backfill, etc.).
  If ticker_fundamentals / results_dates don't exist yet, run Part A + B1 only and mark B2-B5
  "blocked on enrichment" — do not try to fetch or build the enrichment yourself.
- Read _backtest_results.csv fresh. stdlib Python only.

RUN ORDER:
- Part A — winners-vs-losers contrast across attributes already in the DB (role, value, cluster,
  F1, AIM, sector, market_cap where present, disclosure lag), for T+21 and T+90 separately.
- B1 — routine-vs-opportunistic classification from the project's OWN transaction history
  (no external data). This is the highest-ROI factor; always run it.
- B2-B5 — firm size, fraction-of-company-bought (tx_shares / shares_outstanding), book-to-market,
  results-proximity — ONLY if enrichment tables exist. Pre-register direction, contrast top vs
  bottom decile, confirm out-of-sample.

POINT-IN-TIME DISCIPLINE: for any market-cap feature, use shares_outstanding x price_on_announce_date,
never current price, when attributing a return to a trade. Flag any approximate point-in-time field.

DELIVERABLE: docs/research/quant-02-winners-contrast_2026-06-03.md in the hand-back format, with the
Part A contrast table (attribute | top-decile | bottom-decile | differs? | note), per-factor B results,
the headline 1-3 factors that separate winners from losers out-of-sample (and whether any beats the
existing tiers as an organising principle), and a "Limitations of this pass" section.

Do not change production code. Hand back the report path and a 3-line summary when done.
```

---

After it returns: compare against Brief 01. If a bottom-up factor (e.g. firm size or routine-vs-opportunistic) separates winners from losers more cleanly than the seniority tiers do, that's the signal to reorganise the taxonomy — a Back-end + Trader job, gated as usual.
