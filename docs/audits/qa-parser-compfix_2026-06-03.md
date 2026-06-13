# QA — Parser comp-event + pence fix (2026-06-03)

**QA agent:** independent verifier (did not write the code; re-derived all results)
**Subject:** `.scripts/parse_pdmr.py` two edits + `.scripts/test_parser_compfix.py`
**Scope doc:** `docs/specs/parser-fix-comp-events-and-pence-2026-06-03.md`
**Trigger audit:** `docs/audits/reparse-buy-insert-verification_2026-06-03.md`

---

## GATE VERDICT: 🔴 RED — DO NOT BACKFILL

- **Fix 1 (comp-event → NON_BUY/MIXED): GREEN.** Correct, complete, zero genuine buys demoted, no regression to the existing strictness suite.
- **Fix 2 (bare-number → pence): RED.** Its core assumption — "a genuine pound price ALWAYS carries an explicit £/GBP, so any bare number ≥ 1.0 is pence" — is **falsified by the live corpus**. Multiple genuine pound-quoted buys store the price as a bare number in the structured price cell (the £ appears only in the prose narrative). The fix silently divides them by 100, collapsing multi-million-pound buys to ~£0.

**One defect (Fix 2) blocks the backfill.** Fix 1 is shippable on its own.

---

## Per-check results

| # | Check | Result |
|---|-------|--------|
| 1 | File integrity (AST, regions present, tail intact) | PASS |
| 2 | Positive set — comp events NOT STRICT_BUY | PASS |
| 3 | Negative set (wide) — genuine buys stay STRICT_BUY | PASS (21/21) |
| 4 | Pence false-divide investigation | **FAIL — false-divide cases found** |
| 5 | MIXED-gating (MIXED + NON_BUY both suppressed) | PASS (confirmed in signal code) |
| 6 | Regressions (compfix suite + existing strictness expectations) | PASS (env-only artifacts separated) |

---

## 1. File integrity

- `python3 -c "import ast; ast.parse(...)"` → **OK** for `parse_pdmr.py`.
- Both edited regions present and complete (verified via the Read tool, Windows-direct ground truth):
  - `_NON_BUY_RE` comp-event additions at lines ~630–657 (DBP, deferred bonus/shares, bonus deferral, forfeitable shares, scrip, in-lieu-of-dividend, dividend accruing/equivalent, ESPP, employee stock purchase plan, share purchase plan, partnership/matching/free shares).
  - `_parse_price_vol` bare-number branch at lines ~817–831 (val ≥ 1.0 → val/100; sub-£1 left as-is; £/GBP and `p`/pence branches untouched).
- Tail of file intact past line 2769 (Read tool). NOTE: Claude's Linux bash served a stale/truncated 2601-line view and a stale `.pyc`; running the test directly off the FUSE mount produced 6 spurious failures. Re-running from a fresh `/tmp` copy with clean bytecode resolved them — those failures were FUSE artifacts, NOT real. All results below are from the `/tmp` clean-bytecode run.

## 2. Positive set — comp events demoted (PASS)

`_classify_buy_strictness` on the auditor's scoped nature cells:

| Filing | Nature cell | Label |
|--------|-------------|-------|
| BAE 9490111 | Purchase of deferred shares under the DBP. | NON-firing (≠ STRICT) |
| ULVR 9555689 grant | grant of Bonus Deferral Award … restrictions | NON-firing |
| ULVR 9555689 | Purchase of Bonus Deferral Award forfeitable shares | NON-firing (MIXED) |
| BATS 8871006 | …quarterly dividend equivalent shares… | NON-firing |
| PRU 8890342 | …dividends accruing to deferred share awards | NON-firing |
| HAS 9517985 | …US Employee Stock Purchase Plan | NON-firing (MIXED) |
| PRU 9566410 | …All Employee Share Purchase Plan | NON-firing |
| RR 9467833 | …share purchase plan for Non-Executive Directors | NON-firing (MIXED) |
| CNA 9196719 | …Share Purchase Agreement… | NON-firing |

All 9 → NON_BUY_ONLY or MIXED. None remain STRICT_BUY. **PASS.**

## 3. Negative set — wider independent sample (PASS, 21/21)

Pulled all confirmed-clean discretionary buys from the audit N=24 (the ~54% clean band) plus the real nature-cell token forms. Every one stays STRICT_BUY:

SYS1, ARBB, BRBY, ZEN, AUTO, QLT, VLG, IAG, BNZL, YNGA, NIOX, STB, CTEC, ACSO/Cowie, ACSO/Long-Path, IGP — all "Purchase of [ordinary] shares" → **STRICT_BUY**.
Plus token forms: `Purchase`, `PURCHASE`, `Purchase of 345 PLC shares`, `On-market purchase of 5,000 ordinary shares`, `has purchased 33,657 ordinary shares` → all **STRICT_BUY**.

**Demoted: NONE.** The comp patterns are plan-token-specific and do not catch the generic "purchase of shares" verb. Also re-derived all existing `test_buy_strictness.py` expectations (7 STRICT, 14 NON_BUY, 2 MIXED) against the patched function: **0 mismatches** — no regression to Fix 1.

## 4. Pence false-divide investigation — FAIL (RED)

Scanned all 7,277 cached HTML files for price cells that the new bare-number branch would divide. After excluding cells carrying `p`/`pence`/`£`/`GBP`/foreign markers, **18 filings present a truly markerless price cell**. Of these, the bare-number branch divides any value ≥ 1.0 by 100. Inspecting the high-impact ones against their prose narrative (which DOES state the currency):

**False-divide cases (genuine pounds wrongly divided):**

- **9580273** — structured cell `Price 32.52  Volume 82,987  Total 2,698,737.24`. Prose: total `£2,698,737.24`; 32.52 × 82,987 = £2.70m. The price is genuinely **£32.52 (pounds)**. Parser end-to-end grabs the bare `32.52` cell (the £ lives only in the narrative, not the price block) → divides to **£0.3252** → value collapses from £2.7m to ~£27k. **100× under-count.** (Sinead Gorman / Shell CFO.)
- **9508394** — structured cell `Price 40.45780`; prose `GBP 35.25802 … Sinead Gorman acquired …`. Genuine **£40 pound** price → divided to £0.40. Same Shell-class high-value buy. Wrong.

**Correctly handled (no false divide):**

- IGP 8946247 `Price: 177.82` (truly markerless) → **£1.7782**, value ~£103k, not £10.26m. The fix's target case — **correct**, matches the audit.
- GBP-marked sub-£1 prices (8947613 `0.7401 GBP`, 9056455 `0.9 GBP`) → kept as pounds. Correct.
- Sub-£1 bare prices protected by the `val >= 1.0` guard (9557297 £0.8192, 9564003 £0.825, 9573923 £0.787 — all genuine pounds, prose-confirmed) → left as-is. **These happen to be safe only because they are below £1**; a bare pound price between £1 and ~£1,000 is the unguarded danger zone, and 9580273 / 9508394 sit squarely in it.

**Conclusion:** the fix correctly converts the IGP-class pence cells, but its blanket "bare ≥ 1.0 ⇒ pence" rule mis-converts genuine bare-pound cells. The corpus does NOT honour the "pounds always carry £" assumption: the structured price cell can be bare pounds with the £ only in prose. At least 2 confirmed high-value false-divides (both Shell CFO, multi-million £), with up to ~18 markerless filings to re-audit individually. A single 100× value error can dominate a CAR mean — the same class of bug this fix was meant to remove, now flipped in the opposite direction.

## 5. MIXED-gating confirmation (PASS)

Both buy signals require `STRICT_BUY` exactly; MIXED, NON_BUY_ONLY, UNKNOWN and NULL all fail the gate, so demoted comp events cannot leak:

- `b1_lone_conviction_buy_v1.py:118` — `if tx["buy_strictness"] != "STRICT_BUY": return None`
- `b2_crowded_cluster_kill_v1.py:67` — `if tx["buy_strictness"] != "STRICT_BUY": return None`
- Window SQL in both modules and `eval_signals.py:194` — `COALESCE(buy_strictness,'STRICT_BUY') = 'STRICT_BUY'` (NULL treated as strict in the *window* only; the evaluated row itself is gated by the `!=` check above).

No signal fires on MIXED. **PASS.** Fix 1's demotions are genuinely gated out.

## 6. Regressions — real vs env

- `test_parser_compfix.py` (new): **7/7 PASS** (clean-bytecode `/tmp` run). Note: this suite asserts IGP→£1.71 and £-prefixed-untouched, but does NOT cover the bare-pound case that fails check 4 — it tests only the happy path of the assumption. Soft finding: the test set is too narrow to have caught the defect.
- Existing strictness expectations: **0 mismatches** (re-derived).
- `test_parser.py` / `test_buy_strictness.py` raw FUSE run: failures are **env-only** — missing fixtures (`fixtures/parser/tlw_9585916_real.html`) and a `cp`-truncated source line; not code defects.

---

## Required before this can go GREEN

1. **Fix 2 must distinguish bare-pence from bare-pounds.** Options for the engineer (not QA's call): (a) consult the prose narrative / `Total Consideration` to back-out whether `price × shares ≈ stated £total` and only divide when it implies pence; (b) restrict the /100 conversion to the specific IGP-class table layout where the cell is provably markerless AND no £-total reconciles to pounds; (c) add a sanity gate: if `value` after the divide is wildly below the prose-stated consideration, do not divide. A `price × shares` vs prose-`£total` reconciliation is the most robust.
2. **Re-audit the 18 markerless filings** individually (9580273 and 9508394 confirmed wrong; the rest need eyeballing) and add them as negative-set tests to `test_parser_compfix.py` so the bare-pound case is covered.
3. Re-run this QA pass; expect GREEN once no genuine pound price is divided.

**Fix 1 (comp events) may be backfilled independently and safely** if Rupert wants to unblock the comp-event contamination ahead of the pence work — it is GREEN in isolation. Fix 2 must not ship as written.

---

## Limitations of this QA pass

- `.data/directors.db` not consulted (reads malformed in sandbox — FUSE artifact). All checks are pure-function + cached-HTML, per the dispatch.
- Corpus scan used a regex approximation of the price cell; the two RED cases were confirmed end-to-end through the real `_parse_price_vol`, but the full count of affected rows across the corpus is not pinned down (≥2 confirmed, ≤~18 candidate markerless filings).
- The audit CSV `docs/audits/reparse_buy_insert_sample_2026-06-03.csv` named in the brief does not exist on disk; I used the N=24 sample table embedded in the verification MD as the negative set.
