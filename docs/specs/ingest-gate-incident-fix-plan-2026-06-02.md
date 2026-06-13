# Ingest-Gate Suppression — Incident Fix Plan

**Date:** 2026-06-02
**Status:** SHIPPED 2026-06-02. Problems A (gate split), B (drain), C (type-flip) and D (discovery gap) all fixed and verified. Zone-A code by Claude; write-path scripts run by Rupert. See memory `project-ingest-gate-incident-2026-06-02` for the as-shipped record. Open follow-ups tracked in §6 (bundled multi-PDMR recovery, historical backfill, non-RNS layouts, UTL T3 check) and re-grouped into Sprint 20+ (`docs/specs/sprint-plan-2026-06-03-sprints-20-onward.md`).
**Original status (superseded):** PLAN — not implemented. Awaiting Rupert's gate decision.
**Trigger:** Rupert noticed only ~1 signal loaded for today while Investegate showed many PDMR filings (example: Johnson Matthey CEO buy, RNS 9598140, stored as a SELL and invisible).
**Investigation:** Three read-only specialist-agent passes (DB + scrape-cache + parser replay). No write-path scripts run; no writes to `.data/` or cache dirs.

---

## 1. Plain-English summary (for Rupert)

The dashboard has been showing far less than it should — and not just today. For most of the project's life, roughly **three out of four** discovered filings never reached the database. **Important distinction:** the *majority* of those (~4,068) are filings the parser refuses **by design** — overwhelmingly bundled multi-director announcements it won't risk mis-attributing. The **bug** is narrower: a smaller set of perfectly-good filings (**284 filings / 350 rows**, recoverable now) were held back purely because of a harmless warning. So "74% suppressed" is real but is mostly deliberate design-refusal, not the gate bug. Today's empty-looking dashboard is what finally made the gate bug visible.

There are **four distinct problems**. They are independent — fixing one does not fix the others:

| # | Problem | Plain description | Size of impact |
|---|---------|-------------------|----------------|
| **A** | **Over-strict ingest gate** | A filing is saved *only if* it has zero warnings. Harmless advisory warnings (e.g. "couldn't read the company name" when the ticker was fine) throw the **whole filing** into a holding file instead of the dashboard. | **Dominant.** ~74% of all filings ever discovered were held back this way. |
| **B** | **The holding file was never drained** | `_pending_review.json` accumulates trapped filings and is never emptied. It now holds **4,352 filings**, of which **284 (350 transaction rows) are cleanly recoverable** — already parsed, just blocked by a soft warning. | 350 rows recoverable now; 162 of them from May 2026. |
| **C** | **Buy/sell type can flip** | The parser sometimes reads the *whole web page* and a stray word in the page's news ticker ("…asset **disposal**…") flips a **buy** into a **sell**. | 4 filings on 1–2 Jun (JMAT, Genuit, UIL, Cadogan); 2 would have fired T3 signals. |
| **D** | **Scrape discovery gap** | ~11 companies on 1–2 Jun were never even fetched from Investegate. The index walk isn't finding them. | ~11 filings/day not discovered. |

**Not in this fix:** the remaining ~4,068 trapped filings extracted *nothing* — overwhelmingly bundled multi-director announcements the parser refuses by design, plus a few unreadable layouts. Recovering those is a separate, larger parser project (see §6), not a quick gate change.

**Headline correction to give Rupert honestly:** this is a long-standing under-population, not a new regression. The recent Sprint 9/11 hardening did not start it; it increased the share of trapped filings that are cleanly recoverable.

---

## 2. Confirmed root causes (with file:line)

- **A — `run_scrape.py:274`** — `if extracted and not warnings:` → success path. The `else:` (~L306) diverts the entire filing (valid rows included) to `_pending_review.json`. All-or-nothing; no distinction between *blocking* and *advisory* warnings.
- **B — `run_scrape.py:210`** — `pending = _load_pending()` loads and appends; the success path never removes a recovered filing, so `_pending_review.json` grows monotonically (4,301 entries on 29 May → 4,352 today). It is therefore a near-complete ledger of everything suppressed.
- **C — `parse_pdmr.py:2452`** — fallback path passes the *whole stripped page text* to `_classify_type()` (L528, first-keyword-wins; SELL `\bdisposal\b` at L143 is tested before BUY `\bacquisition\b`). The correct scoped path at `parse_pdmr.py:1799` reads only the "Nature of the transaction" cell.
- **D — scraper index walk** (`scrape_investegate.py` / `run_scrape.py`) — the directors-dealings index isn't surfacing ~11 of the day's announcements; pagination / index-URL coverage suspect.
- **Secondary defect (note, not a fix target this pass):** LLM-path rows get a blank `announced_at` (`run_scrape.py:256`) — EWG/MGAM/KGF are in the DB with empty `announced_at`.

---

## 3. Scope of impact (measured)

- **Project-life:** 1,505 filings ingested vs 4,352 trapped → **~74% suppression**, steady 65–85% since launch (~May 2025). No step-down inflection; the gate always behaved this way.
- **Cleanly recoverable now:** 284 filings hold extracted rows, but after the safe gate (foreign_currency BLOCKING + the zero-value guard) the genuinely-ingestable set is **71 filings / 90 rows** (~33 already in the DB → **~57 net-new inserts**; ~80 rows from May 2026). The earlier "350 rows" figure was the gross pre-guard count; the ~260 excluded are FX/zero-value rows the parser was right to hold back. Confirmed by the drain `--dry-run` and an independent QA re-derivation.
- **Not cheap wins:** ~4,068 entries extracted nothing (bundled multi-PDMR ×2,271; `required_fields_missing` ×1,718; layout failures). Separate workstream.
- **Type-inversion (C):** 4 rows on 1–2 Jun. JMAT (£95.7k) misses the T1a £100k threshold even when corrected, but should appear as a CEO buy; Genuit + UIL would each fire T3.

*Confidence: HIGH on counts (measured against a /tmp DB copy + the pending file). MEDIUM on the historical weekly dating (the empty entries carry no date; interpolated from RNS-ID order).*

---

## 4. Fix plan (phase-gated, prioritised)

Each phase is independently shippable and independently QA'd. Diff-first before any change that shifts ingest or firing counts (per project rule).

### Phase 1 — Split the ingest gate (Problem A) — *highest impact, lowest risk*
- In `run_scrape.py`, classify warnings into **BLOCKING** vs **ADVISORY**:
  - **BLOCKING** (route filing/row to pending): `required_fields_missing`, `could_not_parse_tx_date`, `could_not_extract_ticker`, `could_not_extract_PDMR_name`, `could_not_classify_type`, `could_not_separate_price_volume`, `zero_shares_non_grant`, **`foreign_currency`**, bundled-multi-PDMR, any `plausibility_rejected:*`.
  - **ADVISORY** (ingest the row anyway): `could_not_extract_company`, LLM prose notes.
- **Hard per-row guard (non-negotiable, added after QA):** never ingest a **non-grant / non-exercise** row with `price == 0` or `value == 0`, regardless of which warning is attached. The parser deliberately drops these (`parse_pdmr.py:2474-2479` for `foreign_currency`, L2485-2489 for `zero_price_non_grant`) — a USD-priced trade written as price/value 0 is as wrong as a mis-typed one and would be invisible to every value-gated signal (e.g. T1a `value >= 100_000`) while still polluting the table. This guard is what makes relaxing `could_not_extract_company` safe.
- **Why `foreign_currency` is BLOCKING, not advisory (QA correction):** of the rows the naive relaxed gate would admit, ~183 have `price=0/value=0`, 173 of them FX cases. Admitting them injects broken zero-value trades. FX rows need real conversion handling — a separate task — so they stay in pending for now.
- Decision rule changes from per-*filing* to per-*row*: ingest any row whose own required fields are complete (including non-zero price/value for non-grant types); only route genuinely-incomplete rows to pending.
- **Diff-first deliverable:** a report of exactly which historical/today rows newly qualify under the relaxed gate, before it is wired live.

### Phase 2 — Drain the backlog (Problem B) — *no re-scrape needed*
- Build a **read-from-pending replay**: the 284 recoverable entries already hold parsed rows + fingerprints; re-validate them through the new gate and ingest. Cheaper and safer than re-scraping + re-hitting the LLM.
- Add a **prune-on-success** step so recovered filings leave `_pending_review.json` (currently they linger forever).
- **A full blind re-scrape is NOT justified for recovery** — pending is the ledger. Optional belt-and-braces: re-scrape `2026-04-20 → today` only; expect little beyond what pending holds.

### Phase 3 — Scope the type classifier (Problem C)
- At `parse_pdmr.py:2452`, never pass whole-page text to `_classify_type`. Classify the "Nature of the transaction" cell (as L1799 already does); fall back to a tightly-bounded transaction block, never page chrome.
- Defensive extra: strip the Investegate sidebar/news-ticker chrome in `_TextExtractor` before any text classification.
- Re-validates JMAT/GEN/UTL/CAD as buys.

### Phase 4 — Scrape discovery gap (Problem D)
- Diagnose why the index walk misses ~11/day; check the directors-dealings index URL + pagination for the date window. Likely the same single-page / entry-cap issue.

---

## 5. Reparse / backfill plan (the "pick up missed transactions" ask)

1. Land Phase 1 (gate split) + Phase 3 (type fix) in code.
2. **Drain `_pending_review.json`** (Phase 2) — recovers the 284 filings / 350 rows immediately, May-2026 data first.
3. Run the standard rebuild sequence so signals + dashboard reflect the recovered rows: **`eval_signals` → `backtest` → `export_dashboard_json` → `build_dashboard`** (the real 4-step order per `refresh_all.py:144-152`; `backtest` computes the CAR/performance numbers and must not be skipped). All **write-path — Rupert runs these**; Claude pastes exact commands.
4. Optional: targeted re-scrape `2026-04-20 → today` for belt-and-braces only.
5. Re-audit a sample with the data-integrity-auditor after the drain to confirm the recovered rows match source.

**What this does NOT recover:** the ~4,068 zero-row trapped filings (bundled multi-PDMR + hard layouts). Tracked as a separate workstream (§6).

---

## 6. Out of scope (separate future workstream)
- **Bundled multi-PDMR handling** (~2,271 filings): the parser refuses to fan these out rather than mis-attribute. Recovering them needs a deliberate multi-row extraction design with strong per-PDMR attribution + the data-integrity-auditor in the loop. High value (big names land here: Hikma, Dr Martens, Great Portland) but high risk — not a hotfix.
- **Unreadable transaction-table layouts** (III/SNR-style `could_not_separate_price_volume`): add layout recognisers.
- Secondary `announced_at` blank on LLM path.

### Backlog items added 2026-06-02 (post-build)
- **B-072 — Bundled multi-PDMR recovery sprint (~2,271 filings).** The drain
  (Phase 2) only recovers filings that already extracted ≥1 row. The largest
  trapped cohort (~2,271 bundled multi-director filings) extracted *nothing* —
  the parser refuses to fan them out rather than mis-attribute. Recovering them
  is a deliberate multi-row extraction project with strong per-PDMR attribution
  and the data-integrity-auditor in the loop. High value (Hikma, Dr Martens,
  Great Portland land here), high risk. Schedule as its own sprint.
- **B-073 — Historical never-scraped backfill.** Phase 4 confirmed the daily
  `iter_index` discovery gap (see §below): an incomplete `_row_is_pdmr` headline
  allow-list silently dropped ~11 filings/day at the index — these were never
  fetched, so they are NOT in `_pending_review.json` and the drain cannot
  recover them. After the discovery-gap fix lands, a **scoped historical
  re-scrape** (date-windowed, e.g. launch → today) is needed to backfill the
  filings that were never discovered in the first place. Drain-only does not
  reach them.
- **B-074 — Non-RNS provider parser-layout check.** Phase 4 widened discovery to
  all Primary Information Providers (BZW/EQS/GNW etc.), so filings from
  TotalEnergies, Next 15, M&G Credit, Magnum Ice Cream and Ferguson are now
  fetched. Their filing-page layouts differ from RNS and may not parse on the
  first pass — anything unparseable routes safely to `_pending_review.json` (no
  loss, no dashboard noise). ACTION: after the next live `run_scrape`, scan the
  holding file for these names; for any that landed there, add a parser
  recogniser for that provider's layout. This is the last loose thread on
  "capture everything." Also watch the Ferguson (FERG, BZW) "Directorate/
  Executive…" headlines — confirm they are genuine dealings vs corporate-action
  noise correctly quarantined.
- **B-075 — UTL/UIL T3 NED-buy signal not firing.** After the 5-buy reparse, UTL
  (UIL Limited, Peter Durhager) stores correctly as a £69,922 NED BUY but does
  NOT fire `t3_ned_buy` (≥£10k threshold). Determine whether this is correct
  (UIL is a closed-end fund / investment trust and may now be `is_excluded_issuer`)
  or a role-classification / threshold edge that is wrongly suppressing a real
  T3 signal. Cross-check `is_excluded_issuer` for UTL and the normalised role for
  "Durhager". Low priority, but it sits in the signal-firing path so worth a
  definitive answer.

### Phase 4 diagnosis (discovery gap, Problem D) — diagnose-only, fix deferred
Root cause is in the **daily** `scrape_investegate.iter_index` (the path
`run_scrape` uses), not the archive walker:
1. **Incomplete `_row_is_pdmr` headline allow-list (primary).** `iter_index`
   keeps a row only if its index headline contains one of 11 fixed substrings.
   Real PDMR notifications with other phrasings ("Director Dealing",
   "Directorate Dealing", "Director Shareholding", bare "PDMR", "Acquisition by
   a PDMR", company-specific titles) fail the filter and are dropped *before*
   fetch — matching the "never even fetched" symptom exactly.
2. **`?show=300` single page, date window ignored (secondary/latent).**
   `iter_index` fetches one `?show=300` page and never applies its
   `start_date`/`end_date` args. On a very high-volume day the day's filings can
   be pushed past entry 300 and lost.
Proposed fix (needs judgement — deferred per build instructions): widen the
headline allow-list against a labelled sample of real PDMR headlines (guard
against pulling in non-PDMR noise), and/or page the index instead of a single
`?show=300` hit. Calibrate live before shipping; diff-first since it shifts
discovery counts.

---

## 7. Risks & guardrails
- **FUSE / two-zone rule:** all fixes are Zone A (code). Claude edits with Edit/Write + Read-tool truncation check. Every write-path script (`run_scrape`, the pending-drain, `eval_signals`, `export_dashboard_json`, `build_dashboard`, any reparse) is **run by Rupert from PowerShell** — Claude pastes commands and waits.
- **Diff-first** on Phase 1 and Phase 3 (they shift ingest/firing counts). The diff report is its own reviewable deliverable.
- **Gate-relaxation over-correction risk:** if an advisory code is mis-classified as advisory when it actually indicates a bad row, we'd ingest junk. Mitigation: the Phase 1 diff report is reviewed before going live, and the post-drain auditor sample catches field errors.
- **Pending-drain idempotency:** replay must match on the existing fingerprint scheme `(date, ticker, type, shares, price)` to avoid duplicate inserts; prune only on confirmed insert.
- **No-RMW-in-hotpath rule:** the prune step must not re-introduce an O(n²) read-modify-write of the 4,352-entry JSON array inside a per-row loop.

---

## 8. Open decisions for Rupert
1. **Scope of this pass:** all four phases in one planned sprint, or ship Phase 1+2 (recover the data) first and treat C/D as fast-follows?
2. **Belt-and-braces re-scrape:** drain-only (recommended), or also re-scrape 2026-04-20→today?
3. **Bundled multi-PDMR (§6):** schedule as the next sprint, or leave for now? (This is the bulk of the remaining 4,068 — big names but real risk.)
