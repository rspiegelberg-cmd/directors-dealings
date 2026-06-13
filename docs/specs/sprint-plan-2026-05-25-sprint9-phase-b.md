# Sprint 9 Phase B — build plan (approved at Gate 2)

> Theme: **flip the plausibility gate to reject mode + ship four parser
> bug fixes + reparse the corpus.**
>
> Status: **APPROVED at Gate 2 on 2026-05-25.** This document is the
> build plan. The original 2026-05-22 sprint plan and the QA spot-check
> report (links below) are the inputs; this doc supersedes Section 5 of
> the original.

**Companion docs (read alongside):**

- [`sprint-plan-2026-05-22-sprint9.md`](./sprint-plan-2026-05-22-sprint9.md) — original sprint plan; Sections 1, 2, 3, 6, 7, 8 still in force; Section 5 is REPLACED by this document.
- [`sprint-9-phase-a-qa-spot-check.md`](./sprint-9-phase-a-qa-spot-check.md) — QA spot-check of 29 verified rows that produced the FP rates and bug taxonomy this plan acts on.
- [`b-001-b-004-table-aware-parser-sprint-plan.md`](./b-001-b-004-table-aware-parser-sprint-plan.md) — the table-aware parser model the fixes plug into.

**Source of truth on Phase A results:**

- 7,024 cached filings parsed; 873 unique rows flagged (~12.4% of the corpus).
- Aggregate FP rate **~24%** (verified on n=29 hand-checked rows).
- Five bug classes confirmed; three additional classes (USD, par-value, type-mislabel) deferred to Sprint 10.

---

## Section A — Gate 2 decisions log

Rupert confirmed 2026-05-25 (in this order):

| # | Question | Decision |
|---|----------|----------|
| 1 | Phase B scope — ship 2 fixes or all 4? | **All 4 parser fixes.** Original 2 (nested-table, date-bleed) + new 2 (silent price-extraction, director-field narrative). |
| 2 | R5 in Phase B — flip to reject, tighten, or leave warn-only? | **Keep R5 as-is (warn-only).** R5 stays a passive logger; only R1-R4 flip to reject. |
| 3 | Initial allowlists? | **OK.** `INSTITUTIONAL_BLOCK_ALLOWLIST = {"HBR"}` for R4; `HIGH_PRICED_TRUST_ALLOWLIST = {"LTI"}` for R3. |

Classes 6 (USD), 7 (par-value), 8 (type-mislabel) from the QA spot-check are **deferred to Sprint 10**. Phase B reject mode prevents them polluting the signal engine in the meantime.

**One judgment call carried in from the QA spot-check that needs your ack:** the QA agent strongly recommended adding a nil-cost grant carve-out to R1 — exempt rows where `type IN ('GRANT','EXERCISE') AND value == 0`. Without it, R1 FP rate is 18%; with it, ~5%. I am including this in the build plan because it's logically the same pattern as the R3/R4 allowlists, but flag here so you can veto in Section H if you disagree.

---

## Section B — Goal and exit criteria

**Goal.** Move from "parser silently emits implausible rows that pollute the signal engine" to "parser rejects implausible rows at source, with a small, auditable, allowlist-managed exception list."

**Exit criteria (measurable, all must be true to close Phase B):**

1. All four parser bug fixes implemented in `.scripts/parse_pdmr.py` and covered by fixture tests in `.scripts/test_parser.py`.
2. Plausibility gate flipped: R1, R2, R3, R4 reject (with carve-out and allowlists applied); R5 still logs but does not reject.
3. Full `unittest discover` green on Windows side. Test count up by ~10 (8 new tests from the build plan + ~2 from existing tests that need to be adjusted for the rejection semantics).
4. Rupert runs `reparse_corpus.py --preview` then `--confirm` and the diff report `.data/_reparse_diff_sprint-09.json` is generated.
5. Post-reparse audit: zero transactions in DB with `value < £1 AND type NOT IN ('SIP','DIVIDEND','GRANT')` AND not in the GRANT/EXERCISE carve-out.
6. Post-reparse audit: zero transactions with `price_gbp > 200` except those whose ticker is in `HIGH_PRICED_TRUST_ALLOWLIST`.
7. Post-reparse audit: zero transactions with `value > £100m` except those whose ticker is in `INSTITUTIONAL_BLOCK_ALLOWLIST`.

---

## Section C — Files touched

| File | Change type | Detail |
|------|-------------|--------|
| `.scripts/parse_pdmr.py` | Edit | Four bug fixes (C.1-C.4 below); gate config constants updated; reject semantics in the three emission paths |
| `.scripts/test_parser.py` | Edit | 8 new tests (3 fixture tests for new bug classes; 5 unit tests for gate config) |
| `.scripts/fixtures/parser/` | New files | 3 new fixture HTML + .expected.json triples — one per new bug class |
| `.scripts/reparse_corpus.py` | Edit | Add ~40 LOC to emit `_reparse_diff_sprint-09.json` after `--confirm` (already specified in original Section 5; restated here for completeness) |
| `.scripts/audit_suspect_filings.py` | No change | Phase A logger already supports R5 warn-only mode |

No DB schema change. No Zone-B writes from Claude. All edits in code files = Zone A only.

---

## Section D — Bug fixes (four)

### D.1 — Bug fix #1 — Nested-table mis-detection (`_find_transaction_table`)

Already specified in the original Section 5 Bug fix #1. Restated here as part of the unified Phase B scope:

After the header-detection block at line 858 in `_find_transaction_table`, validate header plausibility against immediately-following rows: the first 5 rows after the candidate header must contain at least one row whose cell count is within ±1 of the header's cell count. Also bound: a real transaction-table header has ≤12 cells.

Fixture: `wosg_9454462_price_swap.html` + `.expected.json` (already in the original plan).

### D.2 — Bug fix #2 — Date-component bleed in shares regex (`_parse_price_vol` + `_VOLUME_LABEL_RE`)

Already specified in the original Section 5 Bug fix #2. Recommend post-match volume validator (not regex tightening). Reject any volume candidate `val` if:

- `val in 1..31` AND a month-name token (`Jan`..`Dec` or full month) is within ±30 chars AND value < £100, OR
- `val in 1990..2099` AND `val` is the only integer in the block, OR
- `val < 10` AND `price_gbp == 0.0`.

Plus price-side: reject any price candidate > £1,000 that contains a thousands comma in its raw match — that's a total-consideration figure, not a per-share price.

Fixtures: `grg_9576177_nested_table.html` and `ultp_9529301_volume_swap.html` (already in the original plan).

### D.3 — Bug fix #3 — Silent price-extraction failure (NEW from Phase A)

**Symptom (from Phase A audit, 457 rows — largest bucket of the 873).** Parser successfully extracts `shares` and `value` but fails to populate `price`. The downstream code at the row emission point then sees `price = 0.0` or `price is None` and either:

- emits the row with `price = 0` (which is what trips R1 with `value < £1`), or
- emits the row with a stale `price` carried over from a prior block.

**Diagnosis.** The price-extraction loop in `_parse_price_vol` (lines 602-622) is conditional on a labelled price token appearing in the block. Some Investegate filings use free-text price disclosures (e.g. "at a price per share of GBP 5.185") where the label match fails. Result: the row reaches the emission point with `shares` and `value` set but `price = 0.0`.

**Fix.** Add a post-extraction reconciliation step in `_parse_price_vol` (right before the function returns):

```python
# After labelled extraction, if we have shares and value but no
# price, compute price = value / shares as a fallback. Plausibility
# gate (R3) will reject the row if the computed price is impossible.
if price_gbp == 0.0 and volume > 0 and value_gbp > 0:
    computed = value_gbp / volume
    # Sanity bound — only accept if computed lands in the plausible
    # per-share band. Otherwise leave price at 0 and let the gate
    # reject the whole row.
    if 0.0001 <= computed <= 500.0:
        price_gbp = computed
        # Audit trail — caller can see this was computed not parsed.
        notes.append("price_computed_from_value_and_shares")
```

Defensive: caps at £500/share so a class-3 nested-table mis-detection with a £856m value doesn't masquerade as a legit price. The gate (R3) catches anything that slips through.

**New fixture.** `inch_NNNN_silent_price.html` + `.expected.json` — picked from the Phase A audit sample; an INCH row where labelled price extraction fails but value and shares are recoverable.

### D.4 — Bug fix #4 — Director-field captures narrative (NEW from Phase A)

**Symptom (from Phase A QA spot-check).** The `director` cell ends up with paragraph text instead of a person name. Verified cases: AZN, BNC, TSCO, V3TC, BLND. Examples of bad output: `"Nature of the transaction"`, `"THIS NOTIFICATION..."`.

**Diagnosis.** `_extract_director` (or its column-mapping caller) picks the wrong `<td>` when the section-detection logic misidentifies which row is the director-info row. This happens on filings where the PDMR block is bundled inside a longer narrative announcement and the section parser's first heuristic grabs a sentence fragment.

**Fix.** Tighten the existing `_validate_director_cell` validator (referenced near line 870 in the original plan). Reject any director cell where:

- The cell text contains a sentence stopword from a list (`{"transaction", "notification", "announcement", "regulation", "subject", "purpose", "nature", "details", "this", "the company"}` — case-insensitive, whole-word), OR
- The cell text length is > 80 characters (no real PDMR name is this long; titles like "Director of Group Operations" stay under), OR
- The cell text contains 3+ commas or 2+ full stops (sentence structure rather than a name).

Behaviour on reject: drop the row entirely and add `"director_validation_failed"` to the warnings list. The plausibility gate will not get this row because we reject before it.

**New fixture.** `azn_NNNN_director_narrative.html` + `.expected.json` — assert parser returns `[]` (no rows emitted) for this filing and emits the `director_validation_failed` warning.

### Implementation order

D.1 and D.2 are the original 2026-05-22 bug fixes — already partly scaffolded. Land them first.

D.3 and D.4 are new. D.3 is mechanically simple (one if-block). D.4 needs a stopword list and a tail validator on `_extract_director`. Land both before the gate flip so the gate doesn't get rows that the validators would have killed anyway.

---

## Section E — Plausibility gate flip

### E.1 — Constants block (top of `parse_pdmr.py`)

Replace the Phase A constants:

```python
# Phase B (2026-05-25) — gate flipped to reject mode for R1-R4.
# R5 stays warn-only (Rupert decision at Gate 2 — too noisy to reject).
INSTITUTIONAL_BLOCK_ALLOWLIST: set = {"HBR"}     # exempt from R4
HIGH_PRICED_TRUST_ALLOWLIST: set = {"LTI"}       # exempt from R3
NON_TRADE_TYPES_FOR_PLAUSIBILITY = frozenset({"SIP", "DIVIDEND", "GRANT"})
NIL_COST_CARVEOUT_TYPES = frozenset({"GRANT", "EXERCISE"})  # R1 carve-out
```

### E.2 — `_plausibility_check` body changes

The function signature stays `(row, allowlist) -> (ok, reasons)` but the caller now passes both allowlists (refactor signature to keyword args or pass a config dict — recommend dict for forward compatibility):

```python
def _plausibility_check(row, *, block_allowlist, trust_allowlist):
    ok = True
    reasons = []
    shares = row.get("shares", 0) or 0
    price = row.get("price", 0.0) or 0.0
    value = row.get("value", 0.0) or 0.0
    tx_type = (row.get("type") or "").upper()
    ticker = (row.get("ticker") or "").upper()

    # R1 — sub-pound value (with nil-cost-grant carve-out)
    if value < 1.0 and tx_type not in NON_TRADE_TYPES_FOR_PLAUSIBILITY:
        if not (tx_type in NIL_COST_CARVEOUT_TYPES and value == 0.0):
            reasons.append("R1_sub_pound_value")

    # R2 — unchanged
    if shares < 100 and price < 1.0 and tx_type != "SIP":
        reasons.append("R2_tiny_shares_low_price")

    # R3 — price > £200 with trust allowlist
    if price > 200.0 and ticker not in trust_allowlist:
        reasons.append("R3_price_too_high")

    # R4 — value > £100m with institutional block allowlist
    if value > 100_000_000.0 and ticker not in block_allowlist:
        reasons.append("R4_excessive_value")

    # R5 — unchanged (warn-only)
    looks_like_date = (1 <= shares <= 31) or (1990 <= shares <= 2099)
    if looks_like_date and value < 100.0:
        reasons.append("R5_date_component_in_shares")

    if reasons:
        ok = False
    return ok, reasons
```

### E.3 — Wire-in at the three emission paths

In `parse_announcement`, change from Phase A's "log and emit" to Phase B's "reject for R1-R4, log for R5":

```python
ok, plaus_reasons = _plausibility_check(
    extracted_row,
    block_allowlist=INSTITUTIONAL_BLOCK_ALLOWLIST,
    trust_allowlist=HIGH_PRICED_TRUST_ALLOWLIST,
)
if not ok:
    # Always log to suspect-filings JSONL (audit trail preserved).
    _log_suspect_filing(extracted_row, plaus_reasons, url, rns_id)

    # Determine reject decision. Reject if any R1-R4 reason fires.
    # R5 alone is warn-only — emit the row anyway.
    reject_reasons = [r for r in plaus_reasons
                      if r != "R5_date_component_in_shares"]
    if reject_reasons:
        r_w.append("plausibility_rejected:" + ",".join(reject_reasons))
        continue   # skip emit — row is dropped from this filing's output
    else:
        # R5 fired alone → log only, still emit.
        r_w.append("plausibility_flagged:R5_date_component_in_shares")
```

Apply this at all three emission paths (section ~1289, table ~1349, legacy ~1460).

---

## Section F — Test plan

### F.1 — Adjustments to existing tests

- `test_parser.py::PlausibilityCheckTest::test_R1_fires_on_sub_pound_buy` — assert the carve-out: row with `type='GRANT', value=0.0` should now return `ok=True, reasons=[]`.
- `test_parser.py::PlausibilityCheckTest::test_R3_fires_on_high_price` — add a second case asserting `ticker='LTI', price=830.0` returns `ok=True, reasons=[]`.
- `test_parser.py::PlausibilityCheckTest::test_R4_fires_on_huge_value` — add a second case asserting `ticker='HBR', value=153_000_000.0` returns `ok=True, reasons=[]`.

### F.2 — New tests (8 total)

1. `test_grg_9576177_nested_table` — full fixture round-trip, asserts 1,615 shares @ 16.93p = £273.
2. `test_wosg_9454462_price_swap` — fixture round-trip, asserts 12,853 shares @ £5.185 = £66,643 and that `15,040` is NOT captured as price.
3. `test_ultp_9529301_volume_swap` — fixture round-trip, asserts 32,000 shares @ £0.47, and `15,040` not captured as price.
4. `test_inch_silent_price_recovery` — fixture for D.3; asserts parser computes `price = value / shares` and emits one valid row.
5. `test_azn_director_narrative_rejected` — fixture for D.4; asserts parser emits `[]` and `director_validation_failed` warning.
6. `test_r1_nil_cost_carveout` — direct unit test on `_plausibility_check` with `type='GRANT', value=0`.
7. `test_r3_lti_allowlist` — direct unit test asserting R3 allowlist works.
8. `test_r4_hbr_allowlist` — direct unit test asserting R4 allowlist works.

### F.3 — Verification protocol (per CLAUDE.md mandatory truncation check)

After every code write to `parse_pdmr.py` and `test_parser.py`:

- Read tool (not bash) to verify file integrity.
- Check line count is within ±5 of expectation.
- Check tail-of-file is complete.
- Check key constants (`HIGH_PRICED_TRUST_ALLOWLIST`, `NIL_COST_CARVEOUT_TYPES`) are present at module level.

After all edits: Claude runs `python -m unittest discover -s .scripts -p "test_parser.py"` in bash sandbox. If FUSE-stale staleness suspected, Rupert runs the same in PowerShell.

---

## Section G — Reparse and diff (Zone B — Rupert runs)

Per CLAUDE.md, Claude must not run `reparse_corpus.py`. Paste-and-run for Rupert when Sections D, E, F are green:

```powershell
cd C:\Dev\DirectorsDealings

# 1. Belt-and-braces backup (db_health.backup runs automatically, but
#    explicit pre-sprint backup is cheap insurance)
Copy-Item .data\directors.db .data\directors.db.pre-sprint-9-phase-b.bak -Force

# 2. Integrity check the backup
python -c "import sqlite3; print(sqlite3.connect('.data/directors.db.pre-sprint-9-phase-b.bak').execute('PRAGMA integrity_check').fetchone())"

# 3. Preview the reparse — emits .data/_reparse_corpus_preview.csv
python .scripts/reparse_corpus.py --preview

# 4. Rupert reviews the CSV — sanity-check 10-20 rows
#    (especially Class 1 silent-price recoveries — these are the biggest change set)

# 5. Confirm reparse — emits .data/_reparse_diff_sprint-09.json
python .scripts/reparse_corpus.py --confirm --delete-orphans
```

Diff report shape (additive to original Section 5 spec):

```json
{
  "sprint": 9,
  "phase": "B",
  "generated_at": "2026-05-25T...Z",
  "summary": {
    "rows_before": N,
    "rows_after": N,
    "rows_updated_in_place": N,
    "rows_newly_rejected": N,
    "rows_inserted": N,
    "orphans_deleted": N
  },
  "by_rule": {"R1": N, "R2": N, "R3": N, "R4": N, "R5": N},
  "by_fix": {
    "nested_table": N,
    "date_bleed": N,
    "silent_price_recovery": N,
    "director_narrative_rejected": N
  },
  "value_distribution_shift": {
    "lt_1k_pct_before": 0.38, "lt_1k_pct_after": 0.??,
    "gt_1m_pct_before": 0.05, "gt_1m_pct_after": 0.??
  },
  "allowlist_exemptions_fired": {
    "HBR_R4": N,
    "LTI_R3": N,
    "nil_cost_R1": N
  },
  "samples": {
    "newly_rejected": [10 rows...],
    "updated_in_place": [10 rows...],
    "allowlisted": [up to 10 rows...]
  }
}
```

---

## Section H — Risk register (Phase B-specific)

Original Section 7 risks (1, 2, 3) still in force. Adding three Phase B-specific risks:

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 4 | **D.3 silent-price recovery masks a bigger parser bug.** Computing price from value/shares hides cases where the value field itself is wrong. | Medium | Medium | Cap at £500/share computed price. R3 catches anything that slips through. The audit trail (`price_computed_from_value_and_shares` note) means we can grep for these in the diff report and spot-check. |
| 5 | **D.4 director-validator rejects legit edge cases.** Foreign directors with unusual names or hyphenated titles might trip the stopword list or length limit. | Low | Low | The stopword list is conservative (every term is unambiguous sentence-marker). 80-char limit is well above the longest real director-plus-title we've seen. If a real row is rejected, it lands in `_suspect_filings.jsonl` for audit — no silent data loss. |
| 6 | **Carve-out logic in R1 misclassifies a real bug.** Some Class-1 silent-price rows have type=GRANT and value=0 because the parser dropped the value; the carve-out would let these through. | Medium | Low | D.3 (silent-price recovery) fires before the gate. If value is recoverable from shares × price, the row is fixed not rejected. The carve-out only applies to genuinely zero-value transactions, which are real LTIP/DSBP rows. |

---

## Section I — Rollback

Same protocol as original Section 8:

1. `Copy-Item .data\directors.db.pre-sprint-9-phase-b.bak .data\directors.db -Force`
2. Integrity check via `PRAGMA integrity_check`
3. Revert parser edits manually (no git in this project)
4. Delete `.data/_reparse_diff_sprint-09.json`
5. Re-run `start.bat` Refresh

The Phase A logger stays in place during rollback — it's a passive observer and doesn't write to the DB.

---

## Section J — Open questions for Rupert (Gate 3 — closes Phase B)

These are the only things Rupert needs to confirm at the end, not during:

1. **R1 nil-cost-grant carve-out — keep or revert?** I've included it because the QA agent strongly recommended it and the FP-rate math is compelling. Override if you'd rather ship without it and tune in Sprint 10.
2. **D.3 sanity bound — £500/share computed price cap.** This is high enough for any UK ordinary share but low enough to catch class-3 nested-table mis-detection. Confirm.
3. **D.4 director-cell length limit — 80 chars.** No verified director-plus-title in the existing corpus exceeds 60. 80 gives headroom. Confirm.
4. **Post-reparse signal-engine re-run.** After Phase B reparse, you'll want to re-run `eval_signals.py` and diff against the pre-Phase-B firing counts. T2/T3 cohort counts may shrink ~5-10% because newly rejected rows were previously feeding clusters. Worth a separate `signals_diff_sprint-09.json` (~30 LOC addition to `eval_signals.py`)? Or eyeball it from `start.bat` output?

---

## Recommended execution sequence

Single Claude session for Sections D, E, F (estimated ~90 min Claude time). Single Rupert session for Section G (estimated ~30 min Rupert time, mostly spent reviewing the preview CSV). Rupert decisions in Section J close Gate 3.

Critical files for implementation:

- `C:\Dev\DirectorsDealings\.scripts\parse_pdmr.py`
- `C:\Dev\DirectorsDealings\.scripts\test_parser.py`
- `C:\Dev\DirectorsDealings\.scripts\fixtures\parser\` (3 new fixtures)
- `C:\Dev\DirectorsDealings\.scripts\reparse_corpus.py` (+40 LOC for diff)

Critical references:

- [Original Sprint 9 plan](./sprint-plan-2026-05-22-sprint9.md) Sections 1, 2, 3, 6, 7, 8 still in force.
- [Phase A QA spot-check](./sprint-9-phase-a-qa-spot-check.md) is the empirical basis for every threshold and allowlist in this plan.
