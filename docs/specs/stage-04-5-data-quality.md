# Stage 4.5 — Data quality sweep

**Status:** CLOSED — 2026-05-14. All acceptance criteria met.
**Owner:** Rupert
**Trigger:** Spec 07 open issue (the +4232% F1 outlier in the per-signal CAR table on 2026-05-06).
**Gate:** Stage 5 dashboard numbers should not be exposed to Rupert until this sweep is green. ✅ Green.
**Report:** `.data/_data_quality_report.md`
**Excluded records:** `.data/_excluded.csv` (2 rows — HDD, is_aim metadata error)

> ⚠️ **P0 blocker for Stage 5:** `directors.db` is corrupted (FUSE truncation, 2026-05-14).
> The backtest CSV and price cache are intact. The DB must be rebuilt before any pipeline
> runs. Steps: delete `directors.db` → run `python .scripts/refresh_all.py` → run
> `python .scripts/backtest.py`. See report Section 6 for full details.

---

## Why this exists

A +4232% mean-CAR outlier on F1 surfaced in the per-signal performance table during Stage 4. Median CAR for the same signal was -12.7%, which is the truer reading. The +4232% reading is almost certainly a **data-quality artefact** — most likely one of:

- Unadjusted stock split (e.g. a 10:1 split treated as a 10x price jump)
- Delisted-then-recovered ticker that returned to a higher price
- Currency conversion error (foreign listing priced in pence-vs-pounds confusion)
- A merger / corporate-action share class swap

If these errors persist into Stage 5, the dashboard's per-signal performance grid is misleading by construction — a single outlier per signal type can swing the mean by hundreds of percent, and Rupert will be making kill/keep decisions on bad data.

## In scope

1. **Find the F1 +4232% record.** Backtrace it from `_backtest_results.csv` to the underlying `transactions` row and the `prices` rows that produced the impossible return. Identify the corporate action that caused it.
2. **Sweep for similar outliers across all signals.** Any signal-firing where |CAR| > 200% at any horizon (T+1, T+21, T+90, T+252) is suspect. Output a CSV of candidates with the ticker, signal_id, dates, and price ratios.
3. **Decide remediation policy.** Three options to choose between:
   - **(a) Exclude.** Drop suspect firings from backtest results, log them in a `_excluded.csv` for transparency.
   - **(b) Adjust.** Use Yahoo's `adjclose` instead of `close` so splits and dividends are pre-applied. Re-run the full backfill.
   - **(c) Cap.** Winsorise CARs at ±100% (a hack, but lets the median continue to do the heavy lifting).
   - Recommendation: **(b) is the right structural fix.** Yahoo already provides split-adjusted prices; the current fetcher just isn't using them. The OHLCV backfill (P1-5) needs a one-line change.
4. **Re-run Stage 4 backtest** after remediation. Verify the per-signal table looks plausible (means within an order of magnitude of medians).

## Out of scope

- Foreign-currency rows (`price = 0`) — already filtered upstream.
- Director name normalisation — that's a separate hygiene pass.
- Tick-size or rounding errors at the kilo-pound level — under the noise floor for our purposes.

## Effort estimate

~2–4 hours total. Most of it is the re-backfill after switching to `adjclose`. The detective work for the +4232% record is ~30 minutes.

## Acceptance criteria

- The F1 +4232% record is explained (one of split / delisting / FX / corp-action).
- No signal in the backtest table has |mean CAR| more than 3× its |median CAR| (a heuristic sanity bound).
- The fix is committed: either `adjclose` is used end-to-end, or an exclusion list is documented.
- A single page in `_data_quality_report.md` records: what was found, what was fixed, what was deliberately left.

## Why this gates Stage 5

The dashboard's job 2 is "is each signal type actually generating excess return?". If the inputs to that question are wrong, the dashboard is worse than no dashboard — it's a confidence multiplier on bad analysis. Stage 5 ships only after Stage 4.5 is green.
