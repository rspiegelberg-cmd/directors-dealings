# Sprint 9 plan — Parser plausibility gate + extreme-value bug fixes

> Theme: **stop the parser silently emitting nonsense values.**
>
> QA audit on 2026-05-22 (snapshot `directors.db.bak-pre-b028-20260521-135307`,
> n=2,110 transactions) found ~38% of rows have value < £1k and ~5% have
> value > £1m. A material subset of both buckets are confirmed parser
> misreads (not legitimate trades). Two compounding bugs in
> `.scripts/parse_pdmr.py` — header-row mis-detection inside nested
> tables, and a too-permissive volume/price label regex on the legacy
> fallback path.
>
> Date drafted: 2026-05-22. Author: Plan agent + Claude (main). Gate 1
> pending Rupert's threshold confirmation (Section 9).

**Companion docs:**

- [`b-001-b-004-table-aware-parser-sprint-plan.md`](./b-001-b-004-table-aware-parser-sprint-plan.md) — original table-aware parser sprint; this plan extends the same Path A / Path B model.
- [`sprint-plan-2026-05-22-sprint8.md`](./sprint-plan-2026-05-22-sprint8.md) — most recent sprint, structure reference.
- [`date-integrity-test-strategy.md`](./date-integrity-test-strategy.md) — Layer-2 fixture-test pattern reused below.

**Estimate units:** Rupert-time = wall-clock attention; gates = mandatory sign-off pauses; risk = Low / Medium / High.

---

## Section 1 — Goal & success criteria

**Goal.** Eliminate the two confirmed parser bugs that produce wildly wrong `shares` / `price` / `value` rows on the legacy regex fallback path, and add a plausibility gate that flags (Phase A) then rejects (Phase B) any row whose numbers can't be true.

**Success criteria (measurable):**

1. Three new fixture tests pass against the fixed parser:
   - `GRG-9576177` (Smothers): 1,615 shares @ 16.93p = £273
   - `WOSG-9454462` (Romberg): 12,853 shares @ £5.185 = £66,643
   - `ULTP-9529301` (Bloomfield): 32,000 shares @ 47p = £15,040
2. Post-reparse, the audit query `value < £1 AND type NOT IN ('SIP','DIVIDEND','GRANT')` returns **zero** rows.
3. Post-reparse, the audit query `price_gbp > 200` returns zero rows (or every survivor is hand-blessed in a `_price_outlier_allowlist.json`).
4. Phase A produces a flagged-row count + 50-row sample for Rupert to confirm thresholds before Phase B flips the gate.
5. `unittest discover` green on Windows (273+ tests; new tests push the count up by 5–8).

---

## Section 2 — Out of scope

- LLM-fallback empty-URL rows (e.g. TKO Hallbauer with `parser_source = LLM`). Separate item — file as **B-059** if not already.
- Foreign-currency conversion accuracy (existing `foreign_currency` rejection already covers the worst cases).
- Re-architecting the legacy regex path. It still has to exist for foreign templates and unusual SIP layouts; we're only making it stop emitting impossible numbers.
- Investment-trust / CEF re-exclusion sweep (B-011-class work, already shipped).
- Dashboard surfacing of the new `plausibility_warning` field. Phase C if Rupert wants it.

---

## Section 3 — Files touched

| File | Lines | Change |
|------|-------|--------|
| `.scripts/parse_pdmr.py` | 542–546 | Tighten `_VOLUME_LABEL_RE` — drop bare `\bShares?\b` alternation OR add post-match validation (see Gate 1 Q4) |
| `.scripts/parse_pdmr.py` | 559–644 | `_parse_price_vol` — reject volume candidates that look like dates; reject price candidates that look like a total-value |
| `.scripts/parse_pdmr.py` | 826–867 | `_find_transaction_table` — validate candidate header against neighbouring `<tr>` cell-count consistency |
| `.scripts/parse_pdmr.py` | ~870 | New `_plausibility_check(row, allowlist) -> (ok, reasons)` helper near `_validate_director_cell` |
| `.scripts/parse_pdmr.py` | ~1289 / ~1349 / ~1444–1476 | Insert plausibility gate call at all three emission paths (section / table / legacy) |
| `.scripts/test_parser.py` | new tests | Three new fixture tests (GRG / WOSG / ULTP); two new unit tests for `_plausibility_check`; one for tightened `_find_transaction_table` |
| `.scripts/fixtures/parser/` | new | `grg_9576177_nested_table.html` + `.expected.json`; `wosg_9454462_price_swap.html` + `.expected.json`; `ultp_9529301_volume_swap.html` + `.expected.json` |
| `.scripts/audit_suspect_filings.py` | new | Read-only CLI; reads `.data/_suspect_filings.json`, prints summary by rule, samples 50 rows |
| `.scripts/reparse_corpus.py` | new ~40 LOC | Emit `_reparse_diff_sprint-09.json` after `--confirm` |

No changes outside `.scripts/`. No DB schema change. No Zone-B writes from Claude.

---

## Section 4 — Phase A: plausibility gate as warning logger

### What changes

1. New helper `_plausibility_check(row, allowlist) -> (ok, reasons)` placed near the validators (around line 870). Rules — **flag for Rupert's threshold confirmation at Gate 1**:

   | Rule | Trigger | Catches |
   |------|---------|---------|
   | R1 | `value < £1.00` AND `type NOT IN {SIP, DIVIDEND, GRANT}` | the £3 GRG-class rows |
   | R2 | `shares < 100` AND `price < £1` AND `type != SIP` | the "19 shares" date-bleed class |
   | R3 | `price_gbp > 200.0` | no UK-listed share trades at this per-share price |
   | R4 | `(shares * price) > £100,000,000` AND `ticker NOT IN INSTITUTIONAL_BLOCK_ALLOWLIST` | the WOSG £856m class |
   | R5 | `shares in {1..31}` OR `shares in {1990..2099}` AND `value < £100` | wide-divergence date-component sanity check |

2. `INSTITUTIONAL_BLOCK_ALLOWLIST = set()` initially. Defined as a module-level constant; Rupert seeds it at Gate 1 (Open Question #3 below).

3. In `parse_announcement`, after each of the three emission paths (section path ~line 1289, table path ~line 1349, legacy path ~line 1460), call `_plausibility_check(extracted_row, INSTITUTIONAL_BLOCK_ALLOWLIST)`. Behaviour in Phase A:
   - If `ok` → emit row unchanged.
   - If not `ok` → emit row unchanged **AND** append `{rns_id, url, fingerprint, reasons, row}` to `.data/_suspect_filings.json` (atomic append: read → mutate → write via `rb+` + `fsync`, per the Sprint 6 hotfix pattern).

4. New CLI: `python .scripts/audit_suspect_filings.py --summary` to print counts by rule and sample 50 rows. Reads `.data/_suspect_filings.json`. Read-only — safe in Claude bash.

### What doesn't change

- No DB rows change.
- No reject decisions yet.
- Existing warnings list / behaviour preserved.
- `reparse_corpus.py` not touched in Phase A.

### Phase A deliverable

A count + sample table:

```
rule R1 (sub-£1 value):     ~N rows
rule R2 (<100 shares):      ~N rows
rule R3 (price > £200):     ~N rows
rule R4 (value > £100m):    ~N rows
rule R5 (date-component):   ~N rows
total unique rows:           ~N
overlap with existing pending_review: ~N
```

Plus 50-row CSV sample at `.data/_suspect_filings_sample.csv` for eyeball review.

### Gate criteria for moving to Phase B

Rupert confirms (at Gate 2, between Phase A and Phase B):

- R1–R5 thresholds are correct, OR which to relax/tighten based on the audit list.
- The seed institutional-block ticker allowlist (rule R4 exception).
- That Phase B should proceed *before* or *after* the B-024 backup audit (Open Question #2).

---

## Section 5 — Phase B: bug fixes + reparse + diff

### Bug fix #1 — `_find_transaction_table` (line 826)

**Problem.** When the transaction `<table>` is nested inside a KV-table `<td>`, BeautifulSoup's flat `.find_all("tr")` walk surfaces both the outer KV row AND the inner data rows. The outer row contains all the flattened header tokens, so the column-detection block at line 842 happily maps "date", "price", "volume" into a single mega-row of (say) 18 cells. The data rows below it have only 6 cells → `len(cells) <= max_idx` at line 1183 silently skips every real row → fall-through to legacy regex.

**Fix.** After line 858 (`if {"date", "price", "volume"} <= col_map.keys():`), before accepting the header, add a sibling-consistency check:

```python
following_rows = rows[hi+1:]
candidate_data_widths = [
    len(tr.find_all(["th","td"]))
    for tr in following_rows[:5]
]
if not candidate_data_widths:
    continue
# Header is plausible only if at least one of the next 5 rows has
# the same cell count (allow ±1 for empty trailing cells).
if not any(abs(w - len(cells)) <= 1 for w in candidate_data_widths):
    continue
# Also bound: a true Investegate transaction header is rarely > 8 cells.
if len(cells) > 12:
    continue
```

Both checks are conservative — a real header always has at least one same-width data row immediately following it, and a flattened-KV-row pretender almost never does.

### Bug fix #2 — `_parse_price_vol` + `_VOLUME_LABEL_RE` (lines 542 / 559)

**Problem A.** `\bShares?\b` in `_VOLUME_LABEL_RE` matches the word "shares" anywhere in narrative text (e.g. "...acquired 1,615 ordinary shares on May 19, 2026..."). The 80-char window after the match then grabs the first integer it sees — often a day-of-month (1–31) or a year (1990–2099).

**Problem B.** No price-direction sanity. A £-prefixed total appearing near the price label (e.g. "Total consideration £15,040") is captured into `price_gbp`, swapping the price and value semantics.

**Fix A — volume validator (post-match, recommended — see Gate 1 Q4).** Inside `_parse_price_vol` (around line 624–635), after extracting an integer candidate `val`, reject it if:

- `val in range(1, 32)` AND any of the strings `{"January","February",...,"December","Jan","Feb",...,"Dec"}` appears within ±30 chars of the match position AND value is < 100 (so a real 19-share grant on 19 May survives by virtue of the price block context).
- `val in range(1990, 2100)` AND `val` is the only integer in the block.
- `val < 10` AND `price_gbp == 0.0` (a "shares" hit that produced no companion price almost certainly grabbed a date fragment).

Implementation shape (pseudocode):

```python
def _validate_volume_candidate(val, block, position, price_gbp):
    if val in MONTH_DAY_RANGE and _has_month_word_nearby(block, position):
        return False
    if val in YEAR_RANGE and _only_integer_in_block(block):
        return False
    if val < 10 and price_gbp == 0.0:
        return False
    return True
```

**Fix B — price validator.** In the price loop (lines 602–622), reject any price candidate where `val > 1000.0` AND there's a comma-separated thousands grouping in the raw match (`"15,040"` parses to 15040.0; £15 per share is implausible but `£15,040` is a total). Add:

```python
if val > 1000.0 and "," in num_str:
    # Likely a total-consideration figure, not a per-share price.
    continue
```

Edge case: bona fide high-priced shares (NXT 9000p, RIO £5000+) — but these are denominated in pence and have no thousands comma at pence resolution. Confirm with a fixture before locking in.

**Fix A alternative (regex-level).** Drop `\bShares?\b` from `_VOLUME_LABEL_RE` entirely — keep only the labelled variants (`Number of shares`, `Aggregate volume`, etc.). This is more aggressive but removes the failure class. **Rupert decision at Gate 1: post-match validator OR regex tightening?** Recommend post-match validator — preserves recall on weird templates.

### Plausibility gate flip

Change the Phase A `if not ok: log_and_emit(row)` to `if not ok: append_to_pending_review_json(row, reasons); continue`. The row never reaches the caller, so it never enters `transactions`. `_pending_review.json` is already the canonical "needs human eyeball" sidecar — see `db.py:52` and `repair_pending_review.py`.

### Reparse step (Zone B — Rupert only)

Per CLAUDE.md, Claude **must not** run `reparse_corpus.py`. Paste-and-run for Rupert:

```powershell
cd C:\Dev\DirectorsDealings
Copy-Item .data\directors.db .data\directors.db.pre-sprint-9.bak -Force
python -c "import sqlite3; print(sqlite3.connect('.data/directors.db.pre-sprint-9.bak').execute('PRAGMA integrity_check').fetchone())"
python .scripts/reparse_corpus.py --preview
# Rupert reviews .data/_reparse_corpus_preview.csv
python .scripts/reparse_corpus.py --confirm --delete-orphans
```

### Diff report shape

After `--confirm`, emit `.data/_reparse_diff_sprint-09.json`:

```json
{
  "sprint": 9,
  "generated_at": "2026-05-22T...Z",
  "summary": {
    "rows_before": N,
    "rows_after": N,
    "rows_updated_in_place": N,
    "rows_newly_rejected": N,
    "rows_inserted": N,
    "orphans_deleted": N
  },
  "by_rule": {"R1": N, "R2": N, "R3": N, "R4": N, "R5": N},
  "value_distribution_shift": {
     "lt_1k_pct_before": 0.38, "lt_1k_pct_after": 0.??,
     "gt_1m_pct_before": 0.05, "gt_1m_pct_after": 0.??
  },
  "samples": {
     "newly_rejected": [10 rows...],
     "updated_in_place": [10 rows...]
  }
}
```

Add the diff-emission code to `reparse_corpus.py`. ~40 LOC.

---

## Section 6 — Test plan

### Existing tests that need updates

- `.scripts/test_parser.py` — three new fixture entries (see `fixtures/parser/` additions in Section 3). Live-cache smoke test stays as-is.
- `.scripts/test_stage_03.py` — may have a "legacy regex parses this text" assertion that depended on the loose `\bShares?\b` behaviour. Audit and tighten if so.
- `.scripts/test_b023_bundled_sections.py` — should be unaffected (section path doesn't touch `_parse_price_vol`). Confirm by running.

### New tests to add

1. `test_parser.py::test_grg_9576177_nested_table` — asserts 1,615 shares, 0.1693 price, £273 value.
2. `test_parser.py::test_wosg_9454462_price_swap` — asserts 12,853 shares, 5.185 price, £66,643 value; that `15,040` doesn't get captured as price.
3. `test_parser.py::test_ultp_9529301_volume_swap` — asserts 32,000 shares, 0.47 price; that £15,040 doesn't enter price.
4. `test_parser.py::test_find_transaction_table_rejects_flattened_kv` — synthetic HTML with a nested KV containing the header words; asserts `_find_transaction_table` returns `(None, None)`.
5. `test_parser.py::test_plausibility_R1_sub_pound_value` — direct unit test on `_plausibility_check`.
6. `test_parser.py::test_plausibility_R4_excessive_value_with_allowlist` — confirms allowlist bypass works.
7. `test_parser.py::test_volume_candidate_rejects_date_component` — direct unit test on `_validate_volume_candidate` with "19 May 2026" → False.

Fingerprint stability for the three real fixtures: GRG / WOSG / ULTP currently exist in the DB with wrong shares — the new shares value changes the fingerprint. `reparse_corpus.py` Option-A reconciliation (match on `date, ticker, type` and **excluding** shares) handles this: see `reparse_corpus.py` docstring lines 9–17 and `[[project_sprint3_fingerprint_decision]]`. Confirm by running `--preview` and reading the relevant rows of `_reparse_corpus_preview.csv`.

---

## Section 7 — Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | **Reparse shifts signal firing counts.** Some signals (T1/T2 buy-clusters) are value-weighted; corrected values may newly fire or un-fire a signal. Cross-ref `[[feedback_phase_gated_diff_first]]` — Rupert must diff `eval_signals` output before/after. | High | Medium | Mandatory: produce `_reparse_diff_sprint-09.json` AND a `signals_diff_sprint-09.json` after Rupert's eval_signals re-run. Do not promote any fix-fired signal as "new conviction" until manually reviewed. |
| 2 | **False rejections on real low-value trades.** A legit £18 SIP topup or a tiny advisor grant might trip R1/R2. | Medium | Low | R1 excludes SIP/DIVIDEND/GRANT; R2 has the `price < £1` AND `< 100 shares` joint condition. Phase A audit reveals the false-positive rate before Phase B locks in rejection. |
| 3 | **Header-row sibling check rejects a real one-row transaction table.** Some SIP and grant filings have exactly one data row. | Medium | Medium | Sibling check requires "at least one of the next 5 rows matches width" — a single-row table satisfies this with just 1 sibling. Add a fixture test for a known one-row legitimate table (pick one from `_scrape_cache/`) and lock it as a regression test. |

---

## Section 8 — Rollback

Phase B writes to DB; rollback is `.bak`-restore.

1. `Copy-Item .data\directors.db.pre-sprint-9.bak .data\directors.db -Force`
2. `python -c "import sqlite3; print(sqlite3.connect('.data/directors.db').execute('PRAGMA integrity_check').fetchone())"`
3. Revert the parser code: review the Sprint 9 edits in `.scripts/parse_pdmr.py` and unwind by hand (local-only workflow — no git ceremony beyond Rupert's habit).
4. Delete `.data/_suspect_filings.json` and `.data/_reparse_diff_sprint-09.json`.
5. Re-run `start.bat` Refresh — pipeline returns to pre-Sprint-9 state.

The Phase A logger is a **pure observation layer** — if Phase A alone reveals the rules are wrong, rollback is "revert the parse_pdmr.py edits"; no DB rollback needed because Phase A doesn't write to the DB.

---

## Section 9 — Open questions for Rupert (Gate 1 — blocks Phase A start)

1. **Plausibility thresholds (R1–R5).** Confirm the proposed thresholds or tune. Particular request: confirm R3 (`price > £200`) — Berkshire-class £500k+ shares don't list on LSE, but I want explicit ack rather than assumption.
2. **B-024 backup audit ordering.** Should Phase B reparse wait for B-024's backup-audit work to land, or proceed independently? My read is independent — `.bak` snapshot at sprint start is sufficient — but flag if B-024 has a hard dependency.
3. **Institutional-block ticker allowlist (R4 exception).** Shape proposal: `INSTITUTIONAL_BLOCK_ALLOWLIST = {"AAL", "RIO", "BHP", "HSBA", "BP", "SHEL", ...}` as a module constant. Confirm initial members. (Without this, any legit £100m+ insider buy in a mega-cap trips R4.)
4. **Fix A choice.** Post-match volume validator (recommended) vs. regex-level removal of `\bShares?\b`? Recommend the validator — more surgical.
5. **Rupert-time budget.** Estimated ~2 hrs (45 min Phase A audit review at Gate 1; 60 min Phase B reparse + diff inspection; 15 min final smoke). Within the ≤2 hr discipline. Confirm or push back.

---

## Recommended next move

Hit Gate 1 immediately — questions above are blockers for Phase A. Once Rupert signs off thresholds and the allowlist, Claude can land the Phase A code + the new fixtures in one session (~90 min Claude time). Phase B is a separate session because reparse is Zone-B Rupert-only.

### Critical files for implementation

- `C:\Dev\DirectorsDealings\.scripts\parse_pdmr.py`
- `C:\Dev\DirectorsDealings\.scripts\reparse_corpus.py`
- `C:\Dev\DirectorsDealings\.scripts\test_parser.py`
- `C:\Dev\DirectorsDealings\.scripts\fixtures\parser\` (new fixture HTML + .expected.json triples)
- `C:\Dev\DirectorsDealings\.scripts\db.py` (only if the suspect-filings JSON gets a helper there)
