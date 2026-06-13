# Spec: Conviction-weighted position sizing (Phase 5 research enhancement)

**Status:** Draft v1.0 — 2026-05-06. Three decisions open for Rupert sign-off (D1 / D2 / D3).
**Owner:** Rupert
**Target ship:** Earliest mid-to-late May 2026 — gated on a minimum two weeks of flat-sized briefing usage first.
**Source:** chat session 2026-05-06 (PM scoping conversation following the per-signal CAR breakdown).
**Author:** PM / back-end planning pass, 2026-05-06.
**Backlog row:** `backlog.md` P5-6.

---

## Goal

Replace the current £1,000-flat position sizing with sizing proportional to the **director's actual transaction value** (their conviction, expressed in £). Re-export the paper-trade book under both flat and conviction-weighted sizing in parallel, then compare the per-signal CAR and hit rate tables side by side in the briefing.

The point is **not** primarily realism, nor preparation for real-money sizing. It is a research question: **does insider alpha scale with insider conviction?** If it does, that materially revises what the per-signal performance table is telling us — a signal whose trade-count-weighted CAR is negative may still be positively predictive at the upper end of the conviction spectrum.

Output: evidence to feed the kill/keep decision in Phase 3 (P3-7), not a default for live trading.

---

## Why now, and why not yet

**Why this is worth doing.** The 2026-05-06 per-signal CAR table showed all three live signals in negative territory under flat sizing (S1 mean −2.7% / median −7.0%, F1 mean −5.0% / median −12.7%, T3 mean −6.0% / median −10.9%). Before concluding the signals don't work, we should test whether the negativity is **uniform across conviction** or **concentrated in small-£ trades**. The plumbing is small (~5h total), and the existing schema (`paper_trades.notional_gbp` is already a per-row column) supports it without migration.

**Why not yet.** The flat-sized briefing went live 2026-05-05 and the Phase 5.0 observation period has just started ticking. A premature switch throws away the comparability of every prior screenshot, note, and stat. Two weeks of using the flat briefing first will:

- characterise actual usage friction (or its absence);
- accumulate live data under the original sizing for a meaningful comparison set;
- ensure we ship this as a deliberate test of a specific hypothesis, not a "let's see what happens" exploration.

Earliest sensible re-evaluation: **2026-05-20**.

---

## Current state (the baseline)

In `.scripts/paper_trader.py`:

- `DEFAULT_NOTIONAL_GBP = 1000.0` applies to every paper trade.
- `cmd_open_new` writes that flat figure into `paper_trades.notional_gbp`.
- `shares = notional_gbp / entry_close` at fill time.
- Every briefing aggregate ("Aggregate MTM (open)", "Closed avg net CAR", per-signal stats) implicitly assumes equal weight per trade.

The `paper_trades` schema already stores `notional_gbp` per row. Per-row variation needs no migration.

---

## Decisions (3 open for sign-off)

### D1 — Sizing function

Three candidates, all bounded.

**Option A — Log-scaled (recommended).**
```
notional = clamp(FLOOR, CAP, BASE × log10(director_value_gbp / REF))
```
Starting parameters: `BASE = 1000`, `REF = 50000`, `FLOOR = 500`, `CAP = 5000`. Each 10× of director value adds £1,000 of notional, smoothly. Mathematically well-behaved at the extremes.

**Option B — Tier-based.**
```
£10k–£50k     → £500
£50k–£250k    → £1,000
£250k–£1M     → £2,000
£1M+          → £5,000
```
Easier to read in the briefing ("which tier is this in?") but creates discontinuities at boundaries that look arbitrary in plots.

**Option C — Capped linear.**
```
notional = clamp(£500, £5,000, director_value × 0.005)
```
0.5% sizing factor — a £200k buy maps to £1k notional. Saturates above £1M. Simplest.

**Recommendation: A.** Log-scaled is the right default for a quantity that spans four orders of magnitude.

### D2 — Bounds

Independent of D1's shape, the floor and cap matter.

- **Floor £500** (recommended) — below this the position is too small to move per-trade stats.
- **Cap £5,000** (recommended) — above this, single-trade outliers begin to dominate aggregate £ figures.

Tighter bounds (£500–£3,000) compress the conviction signal further; looser (£100–£10,000) lets conviction speak louder but invites whale-trade dominance.

### D3 — Portfolio-level constraint

Without a portfolio cap, months with many large insider buys can produce an open book whose aggregate notional balloons unrealistically.

- **Option A — No portfolio cap (recommended).** Per-trade bounds (D2) are sufficient.
- **Option B — Hard portfolio cap of £100k.** New entries above the cap get scaled down or skipped.

**Recommendation: A.** Per-trade bounds are enough; portfolio caps introduce fairness questions (which trade gets clipped?) that don't earn their complexity for a research run.

---

## Pre-locked engineering decisions

- **Dual export.** Run both flat and conviction-weighted in parallel — `paper-trades-flat.js` and `paper-trades-weighted.js` — so the briefing can toggle between them. Keep flat as the default for at least a month after weighted ships, preserving the original reference analysis.
- **Edge cases.** Grants (price = 0), undisclosed-price transactions, foreign-currency transactions: already excluded from BUY-class signals upstream. No special handling here.
- **Re-export, no schema migration.** `paper_trades.notional_gbp` is already per-row. Change is in the sizing helper and `cmd_open_new`, not the schema.

---

## Architecture

```
.scripts/paper_trader.py
  ├── new helper: position_size(tx_value_gbp, sizing="flat"|"log"|"tier"|"linear")
  └── cmd_open_new — calls position_size() per signal firing
.scripts/db.py — no change
daily-briefing.html
  ├── new "Size £" column on new firings panel
  ├── rename "Aggregate MTM (open)" → "Aggregate MTM (capital-weighted)"
  └── small toggle/banner showing which sizing scheme is active
```

---

## Per-item plan

### S1 — Sizing helper (~30 min, ~10k tokens)

Add `position_size(value_gbp, sizing)` to `paper_trader.py`. Pure function, no DB calls. Unit-tested with synthetic values across the range £5k → £20M.

### S2 — Wire into cmd_open_new (~30 min, ~10k tokens)

`cmd_open_new` reads each signal's underlying transaction `value` from the joined `signals`/`transactions` row and passes it to `position_size`. `notional_gbp` written into `paper_trades` becomes per-row.

### S3 — Dual export (~1 h, ~25k tokens)

Add `--sizing` argument to `paper_trader.py advance` and `export-json` (default `"flat"`). When set to `"log"`/`"tier"`/`"linear"`, run uses `position_size` and writes to a sizing-suffixed export filename. `advance --all-sizings` refreshes both books in one go.

### S4 — Briefing HTML (~1.5 h, ~40k tokens)

- Add "Size £" column on the new firings panel between `Bought (p)` and `Last (p)`.
- Rename "Aggregate MTM (open)" → "Aggregate MTM (capital-weighted)".
- Add a toggle (radio buttons or query string) top-right: `[ Flat £1k | Conviction-weighted ]`. Toggle swaps which `paper-trades-*.js` is loaded.
- Note next to the per-signal table when weighted is active: "weighted by director transaction value (D1 setting)".

### S5 — Comparison view (~1 h, ~25k tokens)

Small additional section below the per-signal performance table titled "Sizing comparison" — same `Avg CAR` / `Median CAR` / `Hit rate` columns for each signal under flat vs weighted, side by side. This is the **actual research output**. Without it, the user has to mentally diff two pages.

### S6 — Sanity tests (~30 min, ~10k tokens)

`.scripts/test_p5_6_sizing.py` — sanity tests on the `position_size` helper, plus an end-to-end check that weighted reproduces flat when sizing is constant, plus checks that aggregate weighted notional on the open book stays within reasonable bounds (e.g. £200k–£1.5M).

---

## Honest cost expectations

| Item | Time | Tokens |
|---|---|---|
| S1 helper | 30 min | ~10k |
| S2 wiring | 30 min | ~10k |
| S3 dual export | 1 h | ~25k |
| S4 briefing UI | 1.5 h | ~40k |
| S5 comparison view | 1 h | ~25k |
| S6 sanity tests | 30 min | ~10k |
| D1/D2/D3 discussion | 30 min | trivial |
| **Total** | **~5 h** | **~120k** |

Cost: well under £1 in dev tokens. No new runtime API costs (no LLM calls).

---

## What's deliberately out of scope

- **Real-money sizing recommendations.** This is a research feature on the paper book. Real-cash sizing comes after the 6-month observation window.
- **Conviction signals beyond £ value.** Director seniority (CEO vs NED), share-of-net-worth estimates, option-vs-direct-share distinctions could all be richer conviction signals. Out of scope. This iteration tests crude £-conviction; deeper signals come later if useful.
- **Per-issuer sizing tweaks.** One global function, one set of bounds.

---

## What we need from Rupert before code changes

1. **D1 sign-off** — sizing function (recommend A: log-scaled with the listed parameters).
2. **D2 sign-off** — bounds (recommend £500 floor, £5,000 cap).
3. **D3 sign-off** — portfolio cap (recommend A: no cap).

Once decided, S1–S6 ship in a single sitting.

---

## Open issue worth flagging

The +4232% F1 outlier surfaced in the per-signal CAR table on 2026-05-06 is almost certainly a data-quality bug — most likely an unadjusted stock split or a delisted-then-recovered ticker. It distorts the mean CAR (median is unaffected). **Fixing it is a separate, smaller piece of work that should probably ship before this spec**, because the flat-vs-weighted comparison is cleaner if the comparison isn't confounded by a single broken record.

Estimate: ~1 h to find, ~30 min to fix. Suggest tracking as a Phase 0 follow-up rather than bundling into this work.
