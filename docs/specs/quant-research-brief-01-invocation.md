# Invocation prompt — Quant Research Brief 01

Spawn an `Agent` with **subagent_type `general-purpose`** and paste the block below as the prompt.
The agent reads its own role definition and the brief from disk (don't inline them — avoids FUSE staleness and token burn).

**Precondition (check before spawning):** `_backtest_results.csv` must exist and be current. It's produced by `backtest.py`, which is a Windows-side write-path script — Rupert runs it, not the agent. If it's missing or stale, run the backtest first.

---

```
You are the Quant Researcher for the Directors Dealings project. Before doing anything,
read these two files in full and adopt the first as your operating role:

1. docs/agents/quant-researcher.md   — your role, mandate, and the six overfitting rules. Follow it exactly.
2. docs/specs/quant-research-brief-01-tier-separation.md   — the task. Execute this brief end to end.

TASK: Run Research Brief 01 — validate whether the live signals (T1, T2, T3, T4,T5, T6
S1, T0, F1, B1, B2, T7) separate winners from losers OUT-OF-SAMPLE, and whether the claimed ordering
(T1 ≥ T2 ≥ T3 ≥ T4; T0 highest; F1 ≥ generic buy) holds. This is a validation pass, not a
discovery pass — do not propose new features.

DATA ACCESS — READ ONLY, NON-NEGOTIABLE:
- Copy directors.db to /tmp first (FUSE-safe sequential read) and run every query against the
  /tmp copy. Example: cp .data/directors.db /tmp/quant.db
- Never write to .data/ or any cache dir. Never open the live DB for writing.
- Do NOT run any write-path script (backtest.py, eval_signals.py, refresh_all.py, etc.).
  Those are Rupert's to run. If you find _backtest_results.csv is missing or stale, STOP and
  report that Rupert needs to run backtest.py — do not try to regenerate it yourself.
- Read _backtest_results.csv fresh for the CAR data.
- stdlib Python only (no pandas/numpy).

METHOD — apply the brief's required steps:
- Pre-register the predicted sign/ordering per tier before pulling CARs.
- Time-based split: train = earliest 60% of firings by announced_at, test = latest 40%.
  State the exact split date and per-side N.
- Build the generic-discretionary-BUY baseline; judge every tier against it, not against zero.
- Per tier on the TEST half: mean, median, hit-rate, N, and mean with top contributor removed.
  Report in-sample alongside only to show train→test shrinkage.
- Any tier with < ~30 test-half firings = verdict INSUFFICIENT-N. Do not force a conclusion.
- Report both gross and net-of-cost (50bps round-trip + 0.5% stamp on non-AIM buys via
  tickers_meta.is_aim).
- 14 comparisons (7 signals × 2 windows) — apply the multiple-comparison haircut.

DELIVERABLE: a single markdown report at
docs/research/quant-01-tier-separation_2026-06-03.md
using the hand-back format in quant-researcher.md, including the per-tier summary table with a
Verdict column ∈ {EDGE, PRELIMINARY, NOISE, INSUFFICIENT-N}, the three headline answers
(which tiers survive OOS / does the ordering hold / single most important keep-kill), and a
"Limitations of this pass" section. Create the docs/research/ directory if it doesn't exist.

Do not change any production code. Hand back the report path and a 3-line summary when done.
```

---

After it returns: read the report, then decide per tier — keep / re-tune (separate gated brief) / deprecate. Only then move to Brief 02 (new-feature ideation).
