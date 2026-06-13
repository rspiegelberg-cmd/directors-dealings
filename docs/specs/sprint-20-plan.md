# Sprint 20 — "Trust the numbers" (data correctness)

**STATUS: CLOSED 2026-06-03.** In-scope items done. B-093: 304 suppressed buys reclassified (classifier widened + `--include-unknown` backfill). Year-as-shares + value-as-price: source-verified by the data-integrity auditor; the guards (R6/R3) already existed, so the fix was a **scoped** reparse (`--only-rns`, 14 fixed/removed + WOSG, 4697→4691) plus a 15-row surgical delete of no-source `shares==own-year` rows (`fix_sprint20_delete_nourl_yearshares.py`); 16 genuine ~2,000-share holdings kept. +4232% F1 outlier: already gone. **Deferred (agreed):** ~13 pence/pounds + ~21 multi-leg "1.0" price errors → future parser slice. Full record in the ✅ Shipped log in `docs/backlog.md`.

**Date opened:** 2026-06-03
**Theme:** confirm and fix the known-wrong data before any feature or signal work reads more meaning into it.
**Discipline:** audit-first (Claude, read-only against a `/tmp` copy of the DB) → fix (Rupert runs the write-path script). Diff-first on anything that shifts firing counts. QA gate before Rupert applies.
**Source of scope:** `sprint-plan-2026-06-03-sprints-20-onward.md` §Sprint 20.

---

## Phase 0 — read-only audits (DONE 2026-06-03, Claude)

DB copied to `/tmp/audit.db` (integrity OK, 4,697 transactions). Findings below.

### Finding 1 — B-060 price units: REAL, ~4% of rows, mixed causes

- `value = shares × price` is internally consistent across **all 4,056** priced rows (0 mismatches). So there is **no 100× error introduced by the value calculation** — wherever a price is wrong, the stored `value` is wrong by the same factor, and so are the £-thresholds that gate signals (e.g. T1a ≥ £100k).
- Cross-checking `transactions.price` against the Yahoo `prices.close` for the same ticker/date (3,778 matched rows): **~96% agree (ratio ≈ 1)**; **~166 rows (≈4.4%) disagree**, splitting into:
  - **~25 rows ≈ ×100** — price stored in **pence** where pounds expected (e.g. `PAGE` stored 136.63 vs £1.39).
  - **~43 rows ≈ ×0.01** — price implausibly small, mostly **failed extractions defaulting to `1.0` / `0.01`** (e.g. `LSEG` price 1.0 vs £86; `CRDA` 1.0 vs £28; `MKS` 0.01 vs £3.33).
  - ~98 rows in the 2–50 / 0.02–0.5 bands — a mix of genuine differences, splits, and Yahoo's own GBp/GBP quirks; **needs source verification**, not a blind rule.
- **Conclusion:** not a single systemic unit bug; a tail of ~166 rows with 2–3 distinct causes. Fixing needs categorisation + source verification, not one global multiply.

### Finding 2 — Year-as-shares: 30 candidate rows, source verification required

- 30 rows have `shares` equal to a recent year (2024/2025/2026). **All are internally consistent** (`value = shares × price`), so consistency alone can't separate a genuine "bought 2025 shares" from the parser bug (the bug also makes value consistent with the wrong share count).
- **Suspicious recurrence** (a strong tell the bug is still live for some layouts): `EMAN`/Charles Dorfman ×2 both = 2026; `REC`/Thomas Arnold ×3 (SIP); `TST`/Lynden Jones ×2; `TRUE`/Trevor Brown ×2; `LSL` ×3 (GRANT, price 0); `TKO` ×3 (EXERCISE, price 0). Memory already records `EMAN`/Dorfman as **source-verified wrong**.
- Genuine-looking: penny-stock buys of exactly 2025 shares (e.g. `SFOR`, `SCLP`) are plausible and must not be deleted blindly.
- **Conclusion:** hand off the 30 to the data-integrity-auditor for source check; fix only the confirmed-wrong rows.

### Finding 3 — B-093 UTL/UIL T3 not firing: ROOT CAUSE FOUND (and it's bigger than UTL)

- UTL is **not** an excluded issuer (sector Utilities, `is_excluded_issuer = 0`); the 2026-06-02 buy is correctly a **NED BUY of £69,922** — well over the £10k T3 threshold; role normalises to `NED`. None of the suspected causes.
- The real cause: that row's **`buy_strictness = 'UNKNOWN'`**. The signal gate (`eval_signals.py:194`) fires only when `COALESCE(buy_strictness,'STRICT_BUY') = 'STRICT_BUY'` — i.e. **NULL (never classified) passes, but `UNKNOWN` (classifier ran, couldn't decide) is suppressed.**
- Scope: **285 non-excluded BUY rows are `UNKNOWN` and fired no signal**, of which **81 are NED buys ≥ £10k** that would otherwise fire T3 — spread across every recent month (28 in Mar-26, 13 in May-26, 3 in Jun-26). This is a steady, ongoing signal-suppression leak, not a one-off.
- **Conclusion:** decision needed (see Phase 1) — treat `UNKNOWN` like `NULL` at the gate, or re-run/improve the strict-buy classifier so these resolve to `STRICT_BUY`. Either way it shifts firing counts → diff-first.

### Finding 4 — +4232% F1 outlier: already gone, downgrade

- The dramatic +4232% from the 2026-05-06 note is **no longer in the data** (cleaned by a prior reparse/purge). The only remaining extreme in `_backtest_results.csv` is **`SML` at +337% t+90** (one fingerprint, £14.5k F1/T3). Worth a quick unadjusted-split check, but this item is largely closed — drop it down the priority order.

---

## Phase 1 — fixes (decisions for Rupert, then write-path runs)

| # | Item | Recommended fix | Decision for Rupert | Diff-first? |
|---|------|-----------------|---------------------|-------------|
| 1 | **B-093 UNKNOWN suppression** (highest value — 285 buys / 81 NED) | **DECIDED: classifier fix. BUILT 2026-06-03.** Root cause: the "Nature of the transaction" cell often reads just "Purchase"/"PURCHASE", or "Purchase of 345 *PLC* shares", or prose "purchased 33,657 ordinary shares" — none matched the old `_STRICT_BUY_RE`, so they fell to `UNKNOWN` and the gate suppressed them. Widened `_STRICT_BUY_RE` in `parse_pdmr.py`; 6 new cases in `test_buy_strictness.py` (14/14 in-memory validation pass); `backfill_buy_strictness.py` gained `--include-unknown` to re-classify existing UNKNOWN/MIXED rows. | Done. Apply commands below. | Yes — preview is the diff |
| 2 | **B-060 price units** (~166 rows) | Categorise the 166: (a) ×100 pence rows → divide by 100; (b) `1.0`/`0.01` failed extractions → re-extract from cached HTML; (c) ambiguous band → source-verify. Add an `audit_prices.py` invariant so new ones are caught. | Approve the source-verification pass before any correction. | Yes (value/threshold shifts) |
| 3 | **Year-as-shares** (30 rows) | Data-integrity-auditor verifies each against the cached filing; correct only confirmed-wrong rows (in-place per the Sprint-3 fingerprint precedent). | Approve auditor pass. | Yes |
| 4 | **SML +337% outlier** | Quick split/again check; correct or accept. Low priority. | — | minor |

**Write-path note:** every actual correction (re-classify, price fix, share fix) and the downstream rebuild (`eval_signals → backtest → export_dashboard_json → build_dashboard`) is **Rupert-run from PowerShell**. Claude prepares the exact commands and the diff report; Claude never writes `.data/`.

### B-093 apply sequence (Rupert, PowerShell)

```
# 0. confirm tests green Windows-side (FUSE-stale in Claude's sandbox)
python -m unittest discover -s .scripts -p "test_*.py"

# 1. DIFF-FIRST preview — no writes; shows how many UNKNOWN/MIXED rows reclassify
python .scripts\backfill_buy_strictness.py --include-unknown --verbose

#    >>> review the count + sample here before continuing <<<

# 2. apply the reclassification (.bak auto-written by db_health first)
python .scripts\backfill_buy_strictness.py --include-unknown --confirm

# 3. rebuild so signals + performance + dashboard reflect it (locked 4-step order)
python .scripts\eval_signals.py
python .scripts\backtest.py
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

After step 3, confirm UTL's 2026-06-02 £69,922 NED buy now fires `t3_ned_buy`, and eyeball the firing-count delta (expect ≈ +81 T3 plus other tiers/F1/S1 across the recovered ~285 rows).

---

## Phase 1 update (2026-06-03) — the two guards already exist; the fix is a reparse

Data-integrity audit + in-memory verification established that the two guards approved for this sprint are **already implemented** in `parse_pdmr._plausibility_check` (added 2026-05-31, never applied to the back-corpus):

- **Year-guard = R6** — rejects any `shares` equal to a 4-digit year (1990–2099). Verified to catch every confirmed year-as-shares row (WCAT, EMAN, REC, TRUE).
- **Value-as-price guard = R3/R4** — rejects price > £200 / value > £100m. Verified to catch the WOSG "total stored as price" case.
- The two patterns these guards do **not** catch (PAGE pence-as-pounds < £200; LSEG multi-leg `1.0` placeholder) are exactly the classes deferred from this sprint — scope confirmed correct.

**So no new parser code is needed.** The bad rows persist only because the corpus has not been reparsed since 2026-05-31. The fix is to re-apply the guards (+ the aggregate-volume re-extraction, Step B) via a reparse.

### Audit results (source-verified, `docs/audits/audit_2026-06-03_b060_yearshares.md`)
- **Year-as-shares:** 14/14 checkable rows wrong (parser stored the calendar year). 16 more match the pattern but have no source URL.
- **Prices:** of 80 checked, 42 wrong / 32 false-alarm (Yahoo unit quirk) / 6 ambiguous. 56 confirmed-wrong rows total; 21 had already fired signals.
- **Caveat:** 65% of the corpus has no source URL and cannot be verified — counts are floors.

### Fix sequence (Rupert, PowerShell — write-path)
```
# 1. DIFF-FIRST preview — no writes; shows what reparse would reject/change
python .scripts\reparse_corpus.py --preview

#    >>> review the rejected/changed counts before continuing <<<

# 2. apply: re-applies R3/R6 + aggregate re-extraction; removes superseded rows
python .scripts\reparse_corpus.py --confirm --delete-orphans

# 3. rebuild signals + performance + dashboard (locked 4-step order)
python .scripts\eval_signals.py
python .scripts\backtest.py
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

### Residual not covered by the reparse
- **~16 year-as-shares rows with no cached filing** — reparse cannot re-extract them (no source). `shares == own year` is unambiguous corruption, so a targeted delete is the safe action (small Claude-written script, Rupert-run — TBD on request).
- **Pence-as-pounds (~13) and multi-leg `1.0` (~21)** — deliberately deferred to the follow-up slice (auditor fixes #1 and #3).

## Acceptance criteria

- B-093: the 285 UNKNOWN buys are re-classified or re-gated; UTL's £69,922 NED buy fires `t3_ned_buy`; firing-count diff reviewed and signed off.
- B-060: every flagged row either corrected or source-confirmed correct; `value = shares × price` still holds; an invariant guards against new unit errors.
- Year-as-shares: 30 candidates each confirmed genuine or corrected; no row left where `shares` is silently a calendar year.
- One backtest re-run so the dashboard reflects all corrections; `.bak` confirmed before the run.

## Out of scope

Signal keep/kill decisions (Sprint 23), horizon toggle (Sprint 24), the PDMR editor (Sprint 25). Sprint 20 only makes the existing numbers correct.
