# Quant Research Brief 02 — Winners-vs-losers contrast + external-factor enrichment

**Status:** Draft for execution (has a data-sourcing prerequisite — see Part 0)
**Owner:** Rupert
**Agent:** Quant Researcher (`docs/agents/quant-researcher.md`)
**Created:** 2026-06-03
**Type:** Exploratory / hypothesis-generation pass — paired with new-feature enrichment
**Runs after:** Brief 01 (tier validation). 01 grades the tiers we built; 02 asks whether the data wanted to be organised differently.

---

## Why this brief exists

Brief 01 is top-down: "do my predefined tiers work?" Brief 02 is bottom-up: "forget the tiers — what did the actual winners have in common, and are there factors we don't yet hold that separate winners from losers?" The two are complementary. If 02 surfaces a cleaner organising principle than the tiers, that is a more valuable result than 01 passing.

## The one trap that governs this entire brief

**Describing winners is not finding edge.** If you take the top-decile performers and list what they share, you will always find commonalities — and most are worthless. The disqualifying question for every pattern is: *did the losers share it too?* So this brief is built around **contrast, not description**: every attribute is measured on the top decile **and** the bottom decile, and a factor only counts if it **differs between the two groups**. Then anything that separates them must survive out-of-sample, exactly as in Brief 01. All six overfitting rules in `quant-researcher.md` apply unchanged.

---

## Part 0 — Data-sourcing prerequisite (must clear before Parts A/B run)

Some factors below need data not in `directors.db`. Feasibility was probed 2026-06-03:

- **Yahoo fundamentals endpoints are blocked** (quoteSummary + v7 quote both return empty without a session crumb). Do not build against them.
- **Alpha Vantage** works anonymously-with-a-free-key, supports LSE via the `.LON` suffix, `OVERVIEW` returns market cap / shares outstanding / book-to-market, `EARNINGS` returns historical report dates. Limit ~25 calls/day (slow for a full backfill).
- **Financial Modeling Prep (FMP)** — free tier ~250 calls/day, BUT UK/LSE coverage is gated behind the paid Premium plan (free tier is mostly US), and it stores only *current* shares float, not history. **Rejected:** free tier won't cover the UK universe, and current-shares-only weakens point-in-time market cap.
- **EODHD** — the most comprehensive UK source: direct LSE contract, AIM coverage, historical fundamentals, and — uniquely valuable for backtesting — **delisted-company coverage** (protects against survivorship bias). Cost: Fundamentals Data Feed ≈ **$59.99/mo** (≈$49.99/mo paid annually). This is the "most comprehensive data" option but it is a real recurring subscription.
- **RNS feed we already scrape (recommended primary, free).** Two of the three high-value UK fields come through the Investegate feed at zero cost and with *better* point-in-time accuracy than any API snapshot: **Total Voting Rights** announcements give exact shares-in-issue at a historical date (→ B2/B3), and **results/interim-results** announcements give the earnings dates (→ B5). No new dependency, no rate limit, no subscription. Cost is parsing work, reusing the PDMR-parser pattern already in the project.

**DECISION (Rupert, 2026-06-03): Track 1 — Free / RNS selected.** Build the enrichment by reusing the Investegate feed for Total Voting Rights (shares outstanding) and results announcements (earnings dates). No subscription. B4 (book-to-market) is descoped for now; revisit EODHD only if a later pass shows B2/B3/B5 have edge and B4 is worth the spend.

**Options considered (this one costs money, so it was surfaced not assumed):**
- **Track 1 — Free / RNS (recommended).** Reuse the RNS feed for shares outstanding + results dates. Covers the strong factors (B2 firm size, B3 fraction-of-company, B5 results proximity) and the free B1. Omits book-to-market (B4, the weakest factor). £0/mo, most point-in-time-correct, more build effort.
- **Track 2 — Paid / EODHD (~$60/mo).** Adds book-to-market, delisted coverage, and one-call convenience for all fields. Most comprehensive; recurring cost.
- **Track 3 — Hybrid.** Ship Track 1 now (free, gets the strong factors moving), add EODHD later only if B4/delisted prove worth the subscription.
The enrichment schema below is source-agnostic, so the backfill script is the only part that differs by track. **B1 needs none of this and runs regardless.**

**Two-zone rule:** the Quant Researcher and Claude **design** the enrichment schema and **write** the backfill script (Zone A, code — safe). Rupert **runs** the backfill from PowerShell (Zone B, writes under `.data/`). The agent never scrapes-and-writes to the DB itself, and never runs the write-path script.

### Proposed enrichment schema (separate tables — keep new/less-trusted data isolated)

- `ticker_fundamentals(ticker, shares_outstanding, book_to_market, market_cap_gbp_current, source, fetched_at)` — one row per ticker.
- `results_dates(ticker, report_date, report_type, source)` — historical results/earnings announcement dates; many rows per ticker.

Both are additive; neither touches `transactions`.

### Market-cap calculation (per Rupert's method — both forms required)

- **Current market cap** = `shares_outstanding × current_price`. Simple; fine for ranking companies into size buckets (size rank is stable over time). This is the quick path and we store it in `ticker_fundamentals`.
- **Point-in-time market cap** = `shares_outstanding × price_on_announce_date` (price already in the `prices` table). Required wherever a CAR is attributed to a transaction, because **today's market cap is not the market cap at the time of the trade** — using current mcap on a 2024 buy bakes in look-ahead + survivorship bias.
- **Key simplification:** the "transaction value ÷ market cap" ratio reduces to **`tx_shares ÷ shares_outstanding`** = the fraction of the company the director bought. Price cancels, so this feature is almost immune to the point-in-time problem and needs only one number per company.

---

## Part A — Winners-vs-losers contrast (uses data already in the DB)

Runnable immediately, no enrichment required.

1. Rank all measurable BUY firings by CAR at T+21 and T+90 (run both windows separately).
2. Define **top decile** and **bottom decile** by CAR. State N in each.
3. For every attribute already in the DB, compare the distribution across the two groups and report whether it **differs**:
   - role / seniority tier
   - transaction value (£ buckets)
   - cluster membership (S1) and cluster breadth
   - first-time-buy (F1) flag
   - AIM vs main market (`tickers_meta.is_aim`)
   - sector (`tickers_meta.sector`)
   - existing market_cap_gbp where populated
   - disclosure lag (`announced_at − date`)
4. Headline: which attributes actually separate top from bottom, and which are equally present in both (i.e. noise).

## Part B — New factors (needs Part 0 enrichment, except B1 which needs nothing)

**B1 — Routine vs opportunistic (NO external data — highest ROI, do this first).**
The canonical finding (Cohen, Malloy & Pomorski, *Decoding Inside Information*): stripping out insiders who trade on a **predictable calendar** leaves the opportunistic trades that carry essentially all the predictive power (routine traders ≈ zero alpha). Computable entirely from your own transaction history: classify each director as routine vs opportunistic from the regularity of their past trade timing, then contrast CARs. Your F1 flag is a crude proxy for this; B1 is the real version.

**B2 — Firm size (needs market cap).** The most replicated finding across the UK literature: abnormal returns concentrate in **small** firms. Contrast top vs bottom decile by size bucket. ⚠️ Pair with the **Trader** — small-caps are exactly where the 50bps cost model is most wrong (real AIM spreads run 2–3%), so a small-cap edge may be untradeable net of friction.

**B3 — Fraction of company bought (needs shares outstanding).** `tx_shares ÷ shares_outstanding`. Does buying a bigger slice of the company predict bigger CAR? Point-in-time-safe (see Part 0).

**B4 — Book-to-market (needs fundamentals).** A named determinant of the announcement effect; value vs growth tilt.

**B5 — Proximity to results (needs `results_dates`).** Days from `announced_at` to the next results announcement, and whether the buy sits just after a close-period lift. UK PDMRs can't trade in close periods, so timing within the results cycle is itself a signal; the literature finds purchases lead good results by ~25 days on average.

For each B-factor: pre-register the predicted direction, contrast top vs bottom decile, then confirm out-of-sample on the later 40% of firings. No external feature is reported as "edge" on in-sample contrast alone.

---

## Deliverable

A single markdown report at `docs/research/quant-02-winners-contrast_2026-06-03.md`, using the agent's hand-back format, containing:

- Part A contrast table: attribute | top-decile profile | bottom-decile profile | differs? (Y/N) | note.
- Part B: per-factor pre-registration, top-vs-bottom contrast, and out-of-sample confirmation. B1 must be included even if Part 0 enrichment isn't ready.
- **Headline:** the 1–3 factors that most cleanly separate winners from losers out-of-sample, and whether any beats the existing tiers as an organising principle.
- A "Limitations of this pass" section (always).
- Explicit note wherever a finding depends on enrichment data whose point-in-time correctness is approximate.

## Definition of done

- Top/bottom decile N stated for both T+21 and T+90.
- Every attribute judged by **contrast**, never by describing winners alone.
- B1 (routine-vs-opportunistic) completed regardless of enrichment status.
- Any external-data finding confirmed out-of-sample and flagged for point-in-time caveats.
- No production code changes; no DB writes by the agent.

## Sequencing

1. Rupert picks the data source (Part 0 decision) and obtains a free FMP key if needed.
2. Claude/Back-end design `ticker_fundamentals` + `results_dates` and write the backfill script; Rupert runs it.
3. Agent runs Part A + B1 immediately (no enrichment needed), then B2–B5 once enrichment lands.
