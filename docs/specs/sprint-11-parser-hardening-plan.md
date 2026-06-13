# Sprint 11 plan — Parser Hardening + Comprehensive Reparse

> Theme: **clean the corpus.** Sprint 9's plausibility gates are correctly blocking new bad ingests, but a ~7,000-row backfill on 2026-05-22 imported pre-gate data that's still contaminating the signal engine. Plus three NEW bug classes the auditor discovered on 2026-05-27 that Sprint 9's rules don't cover.
>
> Date drafted: 2026-05-27. Author: data-integrity-auditor + Claude (main). Gate 1 pending Rupert's threshold confirmation (Section 9).

**Companion docs:**

- [`docs/audits/audit_2026-05-27_initial.md`](../audits/audit_2026-05-27_initial.md) — source audit that prompted this sprint
- [`docs/specs/sprint-plan-2026-05-22-sprint9.md`](./sprint-plan-2026-05-22-sprint9.md) — predecessor sprint that landed the plausibility gates we're extending
- [`docs/agents/data-integrity-auditor.md`](../agents/data-integrity-auditor.md) — agent that produced the audit and will verify the fixes

**Estimate units:** Rupert-time = wall-clock attention; gates = mandatory sign-off pauses; risk = Low / Medium / High.

---

## Section 1 — Goal & success criteria

**Goal.** Bring the production corpus into a state where every row in `transactions` is either (a) accurately extracted from its source filing, or (b) explicitly carve-listed and audit-trailed. Eliminate all signal firings that are currently based on demonstrably wrong shares, price, or value.

**Success criteria (measurable, post-reparse):**

1. **Zero rows** in `transactions` match the "year-as-shares" pattern (`shares ∈ [1990,2099] AND CAST(SUBSTR(date,1,4) AS INT) = shares`). Currently 38 rows.
2. **Zero rows** with `price > £200` survive without being on an explicit `_price_outlier_allowlist.json`. Currently 70 rows.
3. **Zero rows** where `extracted_price == extracted_shares` (the FAN / TKO duplicate-extraction pattern). Currently 5+ rows.
4. **Zero BUY rows with `price == 0`** in `transactions` (parser must drop these; current count is 149 — all of which currently fire signals).
5. **Director field never matches** `{Person closely associated, Trustee of, PDMR, …}` boilerplate or is shorter than 4 chars. Currently 5 rows.
6. **Role field never** starts with lowercase letter, comma, or period. Currently 17 rows.
7. **Director name capitalisation normalised** — `Title Case` is canonical; the 9 currently-duplicated identities collapse. Affects ~25 rows + cluster_id assignments.
8. **Auditor re-run** (`data-integrity-auditor` against same heuristics) reports zero MAJOR_MISMATCH on the year-match, price>£200, and price=0+BUY patterns.
9. `unittest discover` green on Windows (current baseline 329; new tests push count up by 8–10).
10. Backtest mean-CAR per signal recomputed; **delta between pre- and post-reparse mean CAR < 5pp on any signal with N≥20**, OR a written acknowledgement of which signals' performance materially changes (Analyst sign-off).

---

## Section 2 — Out of scope

- LLM-fallback path (`parser_source = 'llm'`). All audit findings were on `regex` path rows; LLM rows show no signs of these specific bugs. Separate item if needed.
- The 2,862 rows with empty `company` field. This is a historical backfill gap, not a parser bug — needs a separate company-backfill ticket (B-064 candidate).
- The 3,096 rows missing `announced_at`. Same — backfill gap, not a parser bug.
- Foreign-currency rejection rules — Sprint 9 covered this; no regression detected.
- The day-of-month-as-shares bucket (106 rows). 40+ are legitimate SIP allocations; the suspect subset (~38 BUY rows) is handled by the existing Sprint 9 R5 rule once the corpus is reparsed.
- Dashboard surfacing of the new plausibility flag. Future ticket if Rupert wants it visible.

---

## Section 3 — Files touched

| File | Lines | Change |
|------|-------|--------|
| `.scripts/parse_pdmr.py` | 1281–1293 (`_parse_volume_cell`) | **NEW**: port `_looks_like_date_bleed()` defence into the table-aware path. Mirror the legacy-path logic at line 645–700. |
| `.scripts/parse_pdmr.py` | ~1500 (after volume extraction in `_extract_via_table`) | **NEW**: post-extraction guard — if `extracted_price == extracted_shares` and both > 100, treat as duplicate-extraction failure, set both to zero, add warning `duplicate_number_pull`. |
| `.scripts/parse_pdmr.py` | ~510 (`_extract_director`) + `_validate_director_cell` | **NEW**: reject director cells matching `^(Person closely associated|Trustee of|PDMR|Director|Notifier|The Company)` and director cells with `len(director.strip()) < 4`. |
| `.scripts/parse_pdmr.py` | ~1500 (role assignment) | **NEW**: reject role cell if it starts with `[a-z,\.;]` — bleed indicator. Set role to `None` and add warning `role_prose_bleed`. |
| `.scripts/parse_pdmr.py` | new helper `_normalise_director_name()` | **NEW**: title-case the director name on emission. Preserve "de", "van", "of" lowercase per UK convention. Used by both regex and table-aware paths. |
| `.scripts/db.py` | post-insert hook OR migration | **NEW**: run `_normalise_director_name()` once during reparse to collapse existing duplicates (Phase 11.3). |
| `.scripts/test_parser.py` | new tests | 8 new tests (year-as-shares fixture, price==shares duplicate, director prose-bleed, role prose-bleed, name normalisation, plus 3 regression fixtures for the legacy-path bugs to confirm Sprint 9 fixes still hold) |
| `.scripts/fixtures/parser/` | new HTML | `tlw_9585916_year_as_shares.html` + `.expected.json`; `fan_2025-10-14_price_equals_shares.html` + `.expected.json`; `oxb_2025-08-11_director_equals_company.html` + `.expected.json` |
| `.scripts/reparse_corpus.py` | additive | Add `--target year-as-shares|price-extreme|director-bleed|all` flag for surgical reparse. Default behaviour unchanged. |
| `.data/_price_outlier_allowlist.json` | new | Seed with `{"NXT": "genuine £100+ stock", "AZN": "genuine £100+ stock", "GAW": "genuine £100+ stock"}` plus Rupert's additions at Gate 1. |

No DB schema change. No new tables. No Zone-B writes from Claude.

---

## Section 4 — Phase 11.1: NEW parser fixes (Back-end engineer)

Five distinct fixes; each independently testable. Recommend doing them in order so partial progress is still useful if the sprint stalls.

### Fix #1 — Tighten `_looks_like_date_bleed` Trigger 2 + add prose-bleed guard (REVISED 2026-05-27 after QA)

**Original diagnosis was wrong.** Initial audit claimed the bug was a missing defence in `_parse_volume_cell` (table-aware path). QA traced an actual bad row (Tullow filing 9585916) through the post-fix parser and showed it goes through the **legacy regex path** (`parser_source = 'regex'`), not the table-aware path. All 38 year-as-shares rows in the live DB are legacy-path failures.

**Actual problem.** The legacy `_VOLUME_LABEL_RE` matches narrative text via the bare `\bShares?\b` alternation, capturing blocks like `"shares of 10p each on May 22, 2026..."`. Inside that block: integers `10` (par value), `22` (day), `2026` (year). The existing `_looks_like_date_bleed` defence rejects `10` and `22` via Trigger 1 — but **Trigger 2 fails to reject `2026`** because Trigger 2 says "year-only is a bleed if no other integer is in the block", and `10` and `22` count as "other integers" even though they were just dismissed as date-bleed.

**Fix — two parts:**

A. **Tighten Trigger 2** in `_looks_like_date_bleed` (around line 685-696): when collecting `other_ints` to test the "year is the only integer" condition, exclude integers that would themselves be rejected by Trigger 1 (day-of-month with a month word nearby). Pseudocode:
```python
if 1990 <= val <= 2099:
    other_ints = []
    for m2 in NUMBER_RE.finditer(block):
        try: other_val = int(float(m2.group("num").replace(",","")))
        except (ValueError, TypeError): continue
        if other_val == val: continue
        # NEW: skip integers that are themselves date-bleeds (day-of-month near a month word)
        if 1 <= other_val <= 31 and _MONTH_WORD_RE.search(block):
            continue
        other_ints.append(other_val)
    if not other_ints:
        return True
```

B. **Keep the table-aware patch from the original Fix #1 build** as defence-in-depth. It's not wrong — it just doesn't catch the *current* corpus bugs. Future filings that hit the table path with a year-bled volume cell will be protected.

**Verification.** New fixture must be derived from the REAL `.scripts/_scrape_cache/9585916.html` content (or an equally faithful reproduction), NOT a synthetic minimal HTML. QA's report explicitly flagged that the synthetic fixture in the first build wouldn't have caught the path-routing error. Test must:
- Run the actual Tullow filing HTML through `parse_announcement()` end-to-end
- Assert `parser_source = 'regex'` (confirms it routes through the legacy path as expected)
- Assert extracted `shares` is either `115000` (correct) or `0` with `volume_only_contained_dates` warning (acceptable failure mode — the row gets dropped, not silently lied about). Both outcomes are operationally clean; the FAIL mode is `shares=2026`.

**Risk.** Low. Trigger 2 tightening is conservative (more rows pass the Trigger 1 + Trigger 2 combined gate, none fewer). Real `19 shares grant on 19 May 2026` rows are still protected by Trigger 1 itself.

**Builder note:** The previous build also created 6 standalone unit tests for `_parse_volume_cell`. Keep those — they're useful documentation of the table-aware path's behaviour and don't conflict with the corrected fix.

### Fix #1 product trade-off — ACCEPTED 2026-05-27 by Rupert

Post-reparse outcome for the 38 year-as-shares rows: **DELETED, not corrected.** The real share counts (e.g. Tullow's 115,000) live in a cell the legacy regex path doesn't read. Recovery would require a separate sprint to fix table-aware-path routing for these filings.

Decision: accept the drop. 0.8% of corpus / 24 contaminated signals removed. Cleaner data, faster sprint. Recovery work scoped as backlog item **B-064 — Recover Tullow-class filings via table-aware routing fix** (Sprint 12 candidate, not blocking).

### Fix #2 — `price == shares` duplicate-extraction guard

**Problem.** For 5+ rows, the parser pulled the same number into both `price` and `shares` (FAN GRANT 41,444 × £41,444 = £1.72B). Cause: when the price cell and volume cell point to the same source text (likely due to a nested-table flattening edge case), both extractors run against the same input and return the same number.

**Fix — REVISED 2026-05-27 after pre-dispatch path check.** All 5 known duplicate-extraction rows come from `parser_source = 'regex'` (legacy path), NOT the table-aware path. Plus: the original `>100` threshold would false-positive on Games Workshop (GAW 2025-11-21: 184 shares at £184 each = £33,856 — Yahoo confirms GAW trades around £187, so this row is real). Threshold bumped to `>1000` to preserve GAW while still catching all 4 confirmed bad rows (FAN £41k×£41k, TKO £6k×£6k etc.).

Apply the guard in the legacy emission path in `parse_announcement` (around the same area where Sprint 9 plausibility rules R1-R5 fire), AND apply it defensively in `_extract_via_table` for future-proofing:

```python
# Sprint 11 Fix #2 — duplicate-number-pull guard.
# When the parser pulls the same number into both `price` and `shares`,
# it's almost always a nested-table extraction failure where the price
# and volume cells point to the same source text. Threshold of 1000 on
# both fields preserves real edge cases like GAW 184sh × £184 (Games
# Workshop trades around £187 — confirmed real).
if price > 1000.0 and shares > 1000 and abs(price - shares) < 0.5:
    price, shares = 0.0, 0
    warnings.append("duplicate_number_pull")
```

**Verification.** Use the real GAW filing (`url = https://www.investegate.co.uk/announcement/rns/games-workshop-group--gaw/director-pdmr-shareholding/9254618` — it's the only price==shares row with a URL we can save as a fixture) as a NEGATIVE test (must NOT trigger the guard; row preserves 184/184). Plus synthesised tests for the 4 positive cases (rejection triggers, both fields zeroed, warning emitted).

**Risk.** Low. Tight thresholds, parser-source-targeted, negative test in place to catch the GAW class.

### Fix #3 — Director cell validator

**Problem.** Director field captures boilerplate ("Person closely associated", "Trustee of the Kimberly A…") or truncates to 2-3 chars ("Bl", "Ant"). Both are silent extraction failures.

**Fix.** Extend `_validate_director_cell()` to reject:
- Cells matching `^(Person closely associated|Trustee of|PDMR|Notifier|The Company|Director)\b` (case-insensitive)
- Cells with `len(stripped) < 4` after trimming whitespace and punctuation

Return `None` in both cases. Caller treats `None` director as a parse failure and routes the row to `pending_review`.

**Verification.** Three unit tests against the existing bad rows (HLN "Bl", LGEN "Ant", GETB "Person closely associated").

**Risk.** Low–medium. There's a small risk of false-positive rejection on legitimate short surnames (e.g. "Wu"). Mitigation: only reject `len < 4` if `len == 2` AND no surname follows in subsequent words. (Defensive — current bad data only has 2- and 3-char first-fragment-only failures.)

### Fix #4 — Role cell validator (REVISED 2026-05-27 after pre-dispatch check)

**Problem.** Role field captures sentence fragments. Pre-dispatch audit found **25 bad rows** (not 17 — wider than initial audit), all `parser_source = 'regex'`. Examples: "s are intended to create value", ", at the date of grant", "in this regard", "of senior employees and directors", "ing Officer, who purchased...".

**False-positive traps found in pre-flight:**
- "interim Chief Financial Officer" (RENX × 2) — legitimate role; "interim CFO" is a real position designation
- "group Senior Executive Vice-President" (BNC × 3) — real title with wrong case from source

The original spec's blanket "reject if starts with lowercase" would kill these legitimate rows.

**Fix — three rules + a normalisation:**

```python
def _validate_role_cell(role):
    """Sprint 11 Fix #4 — reject prose-bleed roles, normalise case.

    The 25 known bad rows all share one or more of:
      - Start with punctuation (sentence-mid bleed)
      - Length > 80 chars (titles are not sentences)
      - Start with a sentence-fragment word ("ing Officer..." from
        capturing the tail of "purchasing Officer")

    Real lowercase-starting roles ("interim CFO", "acting CEO") are
    preserved by skipping the blanket lowercase-reject rule and
    title-casing the first char on emission instead.
    """
    if not role:
        return None
    r = role.strip()
    if not r:
        return None
    # Rule 1: starts with punctuation → always bleed
    if r[0] in ',.;:':
        return None
    # Rule 2: too long → not a title
    if len(r) > 80:
        return None
    # Rule 3: title-case the first char only (preserves "Chief Financial Officer" etc.)
    if r[0].islower():
        r = r[0].upper() + r[1:]
    return r
```

**Verification.** Unit tests covering:
- POSITIVE rejections (≥10 of the 25 bad-row patterns)
- NEGATIVE preservation: "interim Chief Financial Officer" → "Interim Chief Financial Officer"
- NEGATIVE preservation: "group Senior Executive Vice-President" → "Group Senior Executive Vice-President"
- NEGATIVE preservation: "Chief Executive Officer" → unchanged
- Boundary: role exactly 80 chars → preserved; role 81 chars → rejected

**Risk.** Low. Length-based rule could in theory reject a very long legitimate title (e.g. "Chief Financial Officer and Member of the Executive Committee for ...") but the corpus contains no real role longer than 78 chars by current count.

### Fix #5 — Director name normalisation (REVISED 2026-05-27 after pre-dispatch check)

**Problem.** 9 unique director identities are stored with inconsistent capitalisation (Kate Rock / KATE ROCK; Phil Bentley / PHIL BENTLEY). Pre-flight scan found 84 all-CAPS director rows in addition to the 9 variants — Fix #5 needs to handle the whole 84 + the 9. This silently splits one person into two cluster_ids and inflates cluster-count metrics.

**Edge cases discovered in pre-flight:**
- 14 apostrophe surnames (O'Brien, O'Donnell, O'Shea)
- Many Mc-prefixed names (McDonald, McLean, McCarthy) — algorithmic
- Mac-prefixed names — TRICKY. "MacKinnon" / "MacKenzie" use MacX cap; "Macaulay" / "Macé" are single words. Algorithmic Mac handling will over-capitalise. Use exception list.
- Hyphenated (Jean-Benoit, Seymour-Jackson, Latilla-Campbell)
- Post-nominals (CMG, OBE, CBE, MBE, KBE, DSc, PhD) — preserve uppercase
- Foreign characters (Benoît Macé) — `.capitalize()` is Unicode-aware

**Exception list location:** moved to `.scripts/_director_name_exceptions.json` (Zone A — Claude can write). Was originally specced as `.data/` but Claude is blocked from Zone B writes per CLAUDE.md.

**Fix.** New helper `_normalise_director_name(name) -> str`:

```python
_LOWERCASE_PARTICLES = {"of","the","van","de","der","von","la","le","du","da","di"}

def _normalise_director_name(name):
    if not name: return name
    words = name.strip().split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i > 0 and lw in _LOWERCASE_PARTICLES:
            out.append(lw)
        elif "-" in w:
            # Hyphenated names: Title-Case each segment
            out.append("-".join(p.capitalize() for p in w.split("-")))
        elif "'" in w:
            # O'Brien, D'Souza
            parts = w.split("'", 1)
            out.append(parts[0].capitalize() + "'" + parts[1].capitalize() if len(parts) > 1 else w.capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)
```

Applied at row emission in `parse_announcement` for both paths.

**Verification.** Unit tests for "KATE ROCK"→"Kate Rock", "Nicola Jane Mclean"→"Nicola Jane McLean" (the latter requires a manual exception list — defer to Phase 11.3 review), "John of Albemarle"→"John of Albemarle".

**Risk.** Medium. Risk is over-normalising names that have a real all-caps convention (e.g. surname-only "MCDONALD" → "Mcdonald" instead of "McDonald"). Mitigation: build a small manual-exception list in `.data/_director_name_exceptions.json` seeded during Phase 11.3 backfill.

---

## Section 5 — Phase 11.2: QA gate (mandatory)

Before Phase 11.3, **the QA agent runs a full pass**:

1. File integrity (`wc -l`, AST parse, tail check, sha round-trip) for every file modified.
2. Full test suite `python -m unittest discover -s .scripts -p "test_*.py"` on Windows — must be green, expected count 337+ (329 baseline + 8 new).
3. Confirm no regressions in `test_p3_lookahead.py` (most sensitive test on the project).
4. Stat-check DB integrity (row count + schema_version) — confirm Claude didn't accidentally touch `.data/directors.db`.
5. Code review with the `code-review` skill specifically scoped at the 5 fixes, looking for: edge cases, off-by-one, regex backtracking, perf regressions on the table-extraction path.

QA verdict file: `docs/specs/sprint-11-qa-gate.md`. Sprint 11 cannot proceed to Phase 11.3 without a `PASS` verdict.

---

## Section 6 — Phase 11.3: Corpus reparse (Zone B — Rupert only)

**Per CLAUDE.md, Claude must not run `reparse_corpus.py`.** Sequence Rupert runs from PowerShell:

```powershell
cd C:\Dev\DirectorsDealings

# 1. Pre-flight backup (db_health auto-backups too, but explicit is safer)
copy .data\directors.db .data\directors.db.bak-pre-sprint-11
python .scripts/db_health.py --integrity-check

# 2. Surgical reparse — start with the smallest, most-certain bucket
python .scripts/reparse_corpus.py --target year-as-shares --confirm

# 3. Inspect the diff report
type .data\_reparse_diff_year-as-shares.json

# 4. If diff looks clean, reparse the rest
python .scripts/reparse_corpus.py --target price-extreme --confirm
python .scripts/reparse_corpus.py --target director-bleed --confirm
python .scripts/reparse_corpus.py --target role-bleed --confirm
python .scripts/reparse_corpus.py --target name-normalisation --confirm

# 5. Recompute signals + backtest
python .scripts/eval_signals.py
python .scripts/backtest.py
python .scripts/export_dashboard_json.py
python .scripts/build_dashboard.py

# 6. Spot-check the original Tullow row that started all this
python -c "import sqlite3; c=sqlite3.connect('.data/directors.db').cursor(); print(list(c.execute(\"SELECT shares,price,value FROM transactions WHERE fingerprint='21fea6a08312de60'\")))"
```

Expected final output: `[(115000, 0.17, 19512.05)]` (or close, depending on parser's value-from-aggregate logic).

**Estimated reparse runtime:** ~15-30 mins for the affected fingerprints (~250-350 rows total across all five targets). Full-corpus reparse not required — the gates prevent any pre-Sprint-9 bug from re-entering.

---

## Section 7 — Phase 11.4: Verification + signal-engine reassessment

1. **Re-invoke data-integrity-auditor** with the same heuristic scans against the post-reparse DB. All Tier 1 bucket counts in `audit_2026-05-27_initial.md` should be 0 (or carve-listed).
2. **Analyst agent** runs:
   - Backtest re-run comparison: per-signal mean net CAR at T+21 and T+90, before vs after reparse.
   - Any signal whose mean CAR shifts by >5pp gets a written rationale: "was it propped up by dirty data, or did the dirty rows happen to be neutral?"
   - Output: `docs/analyses/sprint-11-signal-recheck.md`.
3. **dashboard-designer** re-runs the continuous model-assessment mandate: any kill-candidate signals that now drop below the threshold get flagged.

---

## Section 8 — Phase 11.5: New auditor scheduled check

Set up a weekly scheduled task (via `mcp__scheduled-tasks__create_scheduled_task`) that runs the data-integrity-auditor against the live DB every Monday morning and emails Rupert if any of the Tier 1 patterns return above zero. This is a regression net — if a new parser change reintroduces any of these failures, we catch it within 7 days.

---

## Section 9 — Gate 1 answers (Rupert, 2026-05-27)

1. ✅ **Director-name normalisation:** One-time backfill across the whole `director` column. Apply during reparse.
2. ✅ **Price outlier allowlist seed:** `{NXT, AZN, GAW}` confirmed. BHP excluded (Yahoo shows BHP at £20, so all DB rows at £20k+ are bugs).
3. ✅ **Reparse policy for unfixable rows:** Move to `pending_review` AND exclude from signal computation. (Different from my Option C — Rupert added the explicit "do not include in signal" requirement. Implementation note for back-end: the `signals` join must filter on `pending_review.status != 'pending'`.)
4. ✅ **Signal invalidation on superseded fingerprints:** Hard-delete the old signal rows.
5. ✅ **Phase 11.4 backtest threshold:** 3pp (Rupert revised from initial 5pp recommendation, 2026-05-27). Any signal with N≥20 whose T+90 mean CAR shifts by more than 3 percentage points between pre- and post-reparse needs Analyst sign-off (one-line written rationale per affected signal) before the dashboard republishes. Smaller shifts treated as noise and republish automatically.

**Gate 1 status: ALL ANSWERS LOCKED 2026-05-27.** Phase 11.1 cleared to start on Rupert's go-ahead.

---

## Section 10 — Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Parser fix breaks legitimate edge cases (e.g. real 19-share grants) | Low | Medium | Fixture tests against known-good rows BEFORE shipping. Sprint 9 already proved this works. |
| Reparse corrupts `.data/directors.db` via FUSE | Low | Critical | Rupert runs reparse on Windows; auto-backup already in place; explicit copy command in Section 6 step 1. |
| Director name normalisation collapses two real different people | Very low | Medium | Manual exception list `.data/_director_name_exceptions.json`; Analyst spot-checks after reparse. |
| Signal-engine deltas are large enough to invalidate published backtest narratives | Medium | Medium | Section 7 Analyst gate; explicitly do NOT republish dashboard until Analyst signs off. |
| QA spots additional bug classes I haven't anticipated | Medium | Low | Auditor re-run in Phase 11.4 is a backstop; expect to add a Sprint 12 candidate file. |

---

## Section 11 — Expected effort

| Phase | Owner | Wall-clock |
|------|-------|------------|
| Gate 1 (Rupert answers Section 9) | Rupert | 30 mins |
| Phase 11.1 (5 parser fixes + 8 tests) | Back-end engineer (delegated agent) | 3-4 hrs |
| Phase 11.2 (QA gate) | QA agent | 1 hr |
| Phase 11.3 (reparse) | Rupert (PowerShell) | 30-45 mins |
| Phase 11.4 (auditor + analyst reassessment) | data-integrity-auditor + analyst | 1-2 hrs |
| Phase 11.5 (scheduled task setup) | Claude (main) | 15 mins |
| **Total** | | **~7-9 hrs over 2 working sessions** |

## Section 12 — Definition of done

- [ ] All 10 Section-1 success criteria met
- [ ] `docs/specs/sprint-11-qa-gate.md` shows PASS
- [ ] `docs/analyses/sprint-11-signal-recheck.md` exists with Analyst sign-off
- [ ] Weekly auditor scheduled task confirmed running
- [ ] Memory note `project_sprint_11_shipped.md` saved
- [ ] `docs/backlog.md` updated — items B-061 (year-as-shares), B-062 (price-extreme), B-063 (director-bleed) marked Done
