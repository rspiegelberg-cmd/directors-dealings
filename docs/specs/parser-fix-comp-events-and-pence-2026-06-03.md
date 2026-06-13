# Parser fix scope — comp-events-as-STRICT_BUY + pence/pounds value bug

**Status:** Scoped, plan-first — NOT yet built
**Owner:** Rupert
**Trigger:** Data Integrity Auditor AMBER verdict on the reparse BUY inserts (`docs/audits/reparse-buy-insert-verification_2026-06-03.md`), 2026-06-03
**Build agent:** Back-end Engineer (plan-first, QA-gated). Never auto-commit a parser change.
**Blocks:** the corpus reparse commit (task #6) and the re-grade (task #8).

---

## Problem statement

Two distinct parser defects, found while verifying the 685 BUY inserts:

1. **Comp events tagged `STRICT_BUY`.** The parser has a `buy_strictness` classifier (`_classify_buy_strictness`, `_NON_BUY_RE`, `_STRICT_BUY_RE` in `parse_pdmr.py`) and signals only fire on `STRICT_BUY`. But several remuneration forms slip past `_NON_BUY_RE` and are tagged `STRICT_BUY`, so they reach the signal engine as if they were conviction buys. Confirmed forms the auditor found:
   - "Purchase of **deferred shares** under the **DBP** / Deferred Bonus Plan" (BAE 9490111)
   - "grant of **Bonus Deferral Award**" (Unilever 9555689)
   - **dividend scrip** / shares in lieu of dividend (BATS, PRU)
   - all-employee **SIP / ESPP / SAYE** partnership/matching/free shares
2. **Pence-vs-pounds value misparse.** IGP 8946247: 171 pence read as £171 (not £1.71), inflating a real ~£103k buy to ~£10.26m — a 100× error in `value`. A misparse this size can dominate a CAR mean single-handedly.

Severity depends on the contamination diagnostic (`_diag_buy_contamination.py`): the count of existing `STRICT_BUY` rows carrying comp-event language, and how many fired signals, sizes defect #1's impact on Brief 01/02.

## Goal

- Remuneration events are classified `NON_BUY_ONLY` (so the strictness gate suppresses them from signals), **without** demoting any genuine on-market purchase.
- Pence-denominated bare-integer prices convert to pounds correctly, so `value = shares × price_gbp` is right.
- Existing corpus re-classified and the misparsed values corrected, so the live signal layer and the backtest are clean.

## Scope of change (precise)

### Fix 1 — extend non-buy recognition

In `parse_pdmr.py`, extend `_NON_BUY_RE` (≈ line 614) to match the confirmed comp forms. Candidate additions (final regex is the Back-end engineer's to write precisely):
`deferred bonus`, `bonus deferral`, `\bDBP\b`, `deferred shares?` (in a plan context), `share purchase plan`, `\bSAYE\b`, `\bESPP\b`, `scrip`, `dividend reinvest`, `in lieu of (a )?dividend`, `matching shares?`, `partnership shares?`, `free shares?`.

**Guard rail (non-negotiable):** `_STRICT_BUY_RE`'s own comment already warns that UK PDMR boilerplate describes vestings as "acquisition of shares", so over-broad demotion is the standing risk. Every new pattern must be (a) justified by an auditor-confirmed example and (b) proven on the negative set (genuine market buys) not to demote them. When STRICT and NON-BUY language co-occur, current logic returns `MIXED` — confirm MIXED is gated out of signals too, or decide its handling explicitly.

### Fix 2 — pence/pounds conversion

In `_parse_price_vol` (≈ line 720), handle the bare-integer pence case (e.g. a price cell reading `171` on a pence-quoted line) so it converts to £1.71. Inspect the IGP 8946247 cached HTML for the exact cell shape before touching the regex; do not broaden `£`-prefixed handling (those are already pounds).

### Out of scope (this pass)

- The 2,030 non-discretionary inserts (SIP/SELL/GRANT/EXERCISE) — they don't fire signals; leave them.
- `--delete-orphans` — still off; the 61 orphan BUYs are a separate investigation.
- Any signal-threshold change.

## Required tests (QA gate — before any DB write)

1. **Positive set (must classify NON_BUY_ONLY):** the auditor's confirmed examples — BAE 9490111 (DBP), Unilever 9555689 (Bonus Deferral), the BATS/PRU scrip rows, a SIP/ESPP row.
2. **Negative set (must REMAIN STRICT_BUY):** a basket of confirmed genuine on-market purchases across value bands (e.g. the clean ~54% of the audit sample). Zero of these may be demoted.
3. **Pence regression:** IGP 8946247 parses to ~£1.71 and value ≈ £103k, not £10.26m. Add a couple of other pence-quoted filings to be sure no genuine pound-quoted price is divided by 100.
4. **Lookahead/identity:** existing `test_p3_lookahead.py` and parser suites still green.

## Sequenced plan

1. **(this step) Run `_diag_buy_contamination.py`** to size defects on the live corpus (existing STRICT_BUY misses + signal blast radius; insert-side STRICT suspects). Rupert runs from PowerShell. Result decides urgency and the exact pattern list.
2. **Back-end engineer drafts the regex + pence fix** against the cached examples; writes the positive/negative test sets first (TDD).
3. **QA agent** runs the full suite + truncation check; verifies zero genuine buys demoted.
4. **Rupert runs the write-path backfills** (Windows): `backfill_buy_strictness.py --confirm` to re-classify existing rows, plus a targeted value re-parse for the pence-affected rows (scope TBD from the diagnostic — possibly a narrow `reparse_corpus --only-rns` set).
5. **Re-run the Data Integrity Auditor** on a fresh BUY-insert sample → expect GREEN.
6. **Then** unblock the reparse commit (task #6), pipeline rebuild (task #7), and signal re-grade (task #8).

## Definition of done

- New non-buy patterns demote the confirmed comp examples and demote **zero** genuine buys in the negative test set.
- IGP value parses correctly; no pound-quoted price regressed.
- Existing corpus re-classified; count of `STRICT_BUY` comp-event rows drops to ~0 on a re-run of the diagnostic.
- Auditor re-sample returns GREEN.
- Full test suite green; files truncation-checked via the Read tool.
