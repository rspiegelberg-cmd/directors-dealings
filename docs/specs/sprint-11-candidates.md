# Sprint 11 candidates

> Captured 2026-05-26. Stub document — items collected from
> Rupert's session notes plus follow-ups flagged during Sprint 10
> Phase 6. Not yet a plan; needs Gate 1 scoping before build.

## A — Company-page UX fixes (Rupert, 2026-05-26)

### A.1 — Type filter on company-page transaction table

**Where:** `.scripts/dashboard/render_company.py`, transaction table at ~line 305 (`<th class="px-3 py-2 text-left">Type</th>`).

**What:** Add a filter widget (dropdown or per-value checkboxes) above the transaction table that lets the user filter rows by `type` column. The set of values comes from the data itself but typically includes: BUY, SELL, GRANT, EXERCISE, SIP, DRIP, DIVIDEND.

**Open scoping questions for Gate 1:**

1. UI shape — single-select dropdown ("Show only: BUY") vs multi-select checkboxes (toggle each type on/off)? Multi-select is more useful; dropdown is simpler.
2. Default state — show all types, or filter to BUY+SELL by default (matches Rupert's preference seen in item A.3 below)?
3. Persistence — should the filter selection survive page reload, or reset every visit?

### A.2 — Price column label: "(p)" → "(£)"

**Where:** `.scripts/dashboard/render_company.py` line 307 (`<th class="px-3 py-2 text-right">Price (p)</th>`).

**What:** Rupert wants the column header to read "Price (£)" instead of "Price (p)".

**⚠️ Open question for Gate 1:** Is this a label-only change, or do the underlying values also need conversion? The current label "(p)" implies pence. If the actual stored `price` values are pence (e.g. 150 for £1.50/share), changing the label to "(£)" without converting would create a 100× display error.

Sub-tasks needed regardless of unit answer:

- Inspect a sample of `price` values in `transactions` for a known ticker. Compare against the source filing.
- Memory note `B-060` (in `docs/backlog.md`) already flags the broader "pence vs pounds inconsistency across pages" concern — this is a slice of that.
- If conversion is needed: divide by 100 on display, OR add a `price_gbp` column at ingest time (cleaner long-term).

### A.3 — Chart markers: only BUY and SELL

**Where:** `.scripts/dashboard/render_company.py` ~line 620 (price chart markers; `markerDatasets` iteration).

**What:** The price chart on company pages currently shows markers for all transaction types (BUY, SELL, GRANT, EXERCISE, SIP, DRIP, etc.). Rupert wants to restrict markers to BUY and SELL only — these are the informationally-meaningful events; the others are mostly routine grants and dividend reinvestments.

**Open scoping questions for Gate 1:**

1. Permanent filter or user toggle? (Recommendation: permanent filter — keeps the chart clean.)
2. Cluster rings — currently shown on top of markers. If the underlying marker is filtered out (e.g. cluster fires on a GRANT), should the ring still appear? (Recommendation: no — if the trigger is filtered, the ring should be too.)

## B — Classifier robustness (Claude, flagged 2026-05-26)

### B.1 — AIC website 404 fallback

**Where:** `.scripts/classify_issuers.py` — Source A (AIC scrape).

**What:** During Sprint 10 Phase 6 (2026-05-26 12:08Z) the AIC scrape hit `HTTP Error 404: Not Found` on `https://www.theaic.co.uk/aic/find-investment-company`. The classifier degraded gracefully (sticky-flag preserved 130 historical exclusions), but the long-term gap is real: new IT IPOs won't be caught by AIC.

**Open scoping questions:**

1. Has the AIC website moved to a new URL? (Quick web search before any code work.)
2. Alternative source — LSE's official "Investment Trust" sector list? Morningstar? Hargreaves Lansdown's IT screener?
3. If no programmatic source is reliable, accept "audit log + name regex" as the steady-state and add a monthly manual top-up via `--manual-include CSV`?

## C — Sprint 9 deferred parser bugs (Sprint 10 carry-over)

These were explicitly deferred from Sprint 9 Phase B per its QA spot-check. Three bug classes confirmed at ~3 rows each in the n=29 hand-checked sample, projected to ~30-90 rows across the full ~7000-filing corpus.

### C.1 — USD / non-GBP transaction parsed as GBP-zero

Example tickers: AZN ADSs ($71.655), BNZL ESPP ($27.13), BNC Santander ADRs ($7.205). The parser sees a USD-quoted price, fails to convert, and writes `price = 0`, `value = 0`.

**Fix direction:** detect a `$` or `USD` token in the price block; flag as foreign-currency and route to pending (where `_reject_foreign_currency` already handles it).

### C.2 — Par-value "of N pence each" captured as shares

Examples: SGE ("1 4/77 pence" → shares=14), GFRD ("50p each" → shares=50). Parser matches the par-value description thinking it's a share count.

**Fix direction:** post-match validator — if the captured `shares` value is suspiciously round AND the preceding text contains "pence each" / "p each" / "ordinary shares of", reject and route to pending.

### C.3 — Type mis-classification on nil-cost legs

Examples: INCH 8881206 (Exercise tagged SELL), AZN 9573813 (vest tagged SELL), INCH 9557729. On multi-transaction filings, the parser picks the wrong leg.

**Fix direction:** look at adjacent rows in the same filing; if a row has `value=0` and the cell text contains "nil-cost" / "vesting" / "exercise of", override the type to GRANT or EXERCISE.

## Sequencing recommendation (for the eventual Sprint 11 plan)

| Tier | Item | Effort | Risk |
|------|------|--------|------|
| 1 (cheap, high-value) | A.2 — Price column label investigation | 30 min | Low |
| 1 | A.3 — Chart markers BUY/SELL only | 30 min | Low |
| 1 | A.1 — Type filter on transaction table | 1–2 h | Low |
| 2 (medium) | B.1 — AIC website 404 investigation | 1 h | Low |
| 3 (parser work) | C.1 + C.2 + C.3 parser fixes | 3–5 h | Medium (re-parse risk) |

The A-tier items are all dashboard surface work — small, contained, no DB impact. The C-tier items need the same Phase-A-then-Phase-B-with-diff gating that Sprint 9 used (memory: `feedback_phase_gated_diff_first`). Recommend Sprint 11 ships A-tier as a self-contained release, treats C-tier as a separate Sprint 12.

## Not in scope here

- B-016 (RNS scraper role-field garbage) — already covered in `backlog-scopes-2026-05-18.md`.
- B-017 (parser writes director name into company field) — bundled into B-001+B-004 (Sprint 3 work).
- Other Tier-3 redesign-v1.1 items (B-017 drill dropdowns, B-018 per-tile filtered cohorts, B-019 CSV export) — Sprint 12+ candidates.

---

*Captured 2026-05-26. Ready for Gate 1 scoping when Rupert is ready to plan Sprint 11.*
