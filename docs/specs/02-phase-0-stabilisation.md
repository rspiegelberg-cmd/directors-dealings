# Spec: Phase 0 — Stabilise the parser

**Status:** Approved v1.1 — decisions locked 2026-05-05, ready to execute
**Owner:** Rupert
**Target ship:** Week of 2026-05-05 (one focused sitting per item)
**Source:** `backlog.md` rows P0-1 through P0-5; `Directors-Dealings-PM-Brief.docx` Phase 0
**Author:** PM/back-end planning pass, 2026-05-05

## Decisions confirmed (2026-05-05)

- **D1 — bundled-PDMR handling:** Option (a). Keep refusing to auto-split; enrich the warning to include each named PDMR + role; provide a manual-split runbook. Data integrity over coverage.
- **D2 — foreign-currency:** Audit the 10 flagged items first (~30 min). If all genuinely non-GBP, drop P0-3 and defer to v2. If any look fixable, ship the narrow fix.
- **Commit policy:** One branch per P0 item (`p0-2-ordinal-date-regex`, `p0-4-bundled-pdmr-warning`, etc). Each can land independently.

---

## Goal

Drop the pending-review rate from ~30% (83 of ~285 filings) to ~15% (~40) without lowering parser strictness — every fix must keep the QA promise: "the parser never silently mis-attributes a transaction".

## State of play (verified 2026-05-05)

- **Parser:** `.scripts/parse_pdmr.py`, 1057 lines, well-organised with single-responsibility helpers.
- **Pending queue:** 83 items in `.scripts/_pending_review.json` (dict with `generated_at`, `count`, `items[]`).
- **Each pending item already has** an `extracted` array with whatever the parser *did* manage to pull, and a `warnings` array explaining what it couldn't. We can triage from this without re-fetching.
- **Cache is empty.** `.scripts/_scrape_cache/` has zero HTML files. Verification against real filings will need a fresh scrape (or live-fetch a small sample for testing). This contradicts CLAUDE.md's assumption that the cache is populated.
- **No `agents/qa-regressions.md`** yet. The QA profile expects one; we'll create it as a side artefact during P0-4.

### Warning histogram (pending queue, n=83)

| Count | Warning category | Phase 0 item that should clear it |
|---|---|---|
| 35 | could_not_extract_PDMR_name | P0-4 (most are bundled-name) |
| 30 | bundled_multi_PDMR | P0-4 |
| 22 | could_not_separate_price_volume | Partially P0-3, mostly out of scope |
| 16 | required_fields_missing | Cascaded from above |
| 15 | multiple_distinct_prices | Out of scope (multi-tranche, deferred) |
| 14 | zero_shares_non_grant | Out of scope (data-quality, not regex) |
| 13 | could_not_parse_tx_date | **P0-2** |
| 12 | no_numeric_values | Out of scope |
| 11 | could_not_classify_type | Out of scope |
| 10 | foreign_currency | **Decision point** — see P0-3 |

> Items often carry two warnings (e.g. `could_not_extract_PDMR_name` *and* `required_fields_missing`), so the column sums to >83.

### Honest expected impact

If we ship every Phase 0 item as scoped: **~50 of 83 items clear, leaving ~33 in the queue (~12% pending rate).** That meets the ~15% target. The residual 33 are genuinely out-of-scope failures (multi-tranche pricing, no-numeric-values, foreign-currency without FX) that need v2 handling.

---

## Decision points (need user sign-off before code changes)

### D1 — How to handle bundled-PDMR filings (affects P0-4)

The brief says "split into N records, one per named PDMR." But for the actual SDR rns_id 9540067 filing, the parser has already extracted: 1 transaction date, 1 type, 278 shares, price 50.0p, value £13,900 — but 4 named roles. There is no per-person breakdown available in the source. The shares are an aggregate across the named PDMRs.

Three options:

- **(a) Emit zero deltas, keep filing in pending** *(today's behaviour)*. Honest, never wrong, but leaves ~30 items in the queue.
- **(b) Emit one "aggregate" delta with `director="Multiple PDMRs"`** and the role list joined into the role field. Preserves the signal that something happened on this date, but inflates ticker-row count and breaks cluster-detection (clusters need distinct directors).
- **(c) Emit N deltas, dividing shares evenly across named PDMRs.** Matches the brief, but invents data — divides 278 shares by 4 people and pretends each got 69.5. Aggregate sums to the same total but per-person counts are fictional.
- **(d) Emit N deltas with full shares attributed to each named PDMR.** Worst — inflates aggregate share count by N×.

**Recommendation: (a) keep refusing, but improve the warning** so the user can do a one-shot manual split via `python run_daily.py --deltas '[…]'`. Update the runbook (P0-5) with a worked example. Reasoning: a market maker cares about signal integrity over coverage. We'd rather have 30 honest holes than 30 plausible-looking made-up rows.

### D2 — Foreign-currency prices (affects P0-3)

10 items in the queue are flagged `foreign_currency`. The current `NUMBER_RE` (line 219-227) already understands `$`, `EUR`, `USD` prefixes — the parser is correctly *detecting* foreign currency and refusing to coerce to GBP without an FX rate.

Three options:

- **(a) Drop P0-3 entirely.** The parser is doing the right thing; flagging foreign-currency to manual review is correct. Address foreign currency in v2 with a daily FX rate fetch. Saves Phase 0 effort.
- **(b) Add FX-rate fetcher.** New script that pulls GBP/USD, GBP/EUR daily, normalises non-GBP prices. Stack-constraint OK (Yahoo Finance precedent), but real new code, ~3h not 1h.
- **(c) Audit the actual P0-3 cases first.** Spend 30 min reading the 10 foreign_currency items + the 22 could_not_separate_price_volume items. If they're all genuinely non-GBP, do (a). If a chunk are misclassified GBp/pence formats, narrow the fix to those.

**Recommendation: (c) audit first, then almost certainly (a).** Keeps Phase 0 honest. If audit shows we're wrong, the fix becomes obvious.

---

## Per-item plans

### P0-2 — Regex fix: ordinal date format

**Goal.** Parse "27th April 2026" / "1st" / "2nd" / "3rd" / "21st" as valid transaction dates. Clears ~13 queue items.

**Root cause (verified).** `_EMBEDDED_DATE_RE` at `parse_pdmr.py:378-383` matches `\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b` — no allowance for the `st|nd|rd|th` suffix. `_try_one_date()` at `parse_pdmr.py:386-396` calls `datetime.strptime` with `_DATE_FMTS` (`parse_pdmr.py:365-372`); none of those format strings tolerate ordinal markers either.

**Files & lines.**

- `.scripts/parse_pdmr.py:378-383` — extend `_EMBEDDED_DATE_RE`.
- `.scripts/parse_pdmr.py:386-396` — strip ordinal suffix in `_try_one_date()` before `strptime`.

**Concrete change shape.**

```python
# Line 378-383: add (?:st|nd|rd|th)? after the day digits in the
# day-month-year and month-day-year patterns
_EMBEDDED_DATE_RE = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})\b"
    r"|\b([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b"
    r"|\b(\d{4}-\d{2}-\d{2})\b"
    r"|\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b",
    re.IGNORECASE,
)

# Line 388: strip ordinal suffix before format-matching
def _try_one_date(s: str) -> str | None:
    s = s.strip().rstrip(".,;").replace(",", "")
    s = re.sub(r"(\d{1,2})(?:st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)  # NEW
    s = re.sub(r"\s+", " ", s)
    s = s.replace("-", " ") if re.match(r"^\d{1,2}-[A-Za-z]", s) else s
    for fmt in _DATE_FMTS:
        ...
```

**Tests.**

- New unit-style test cases (inline pytest or a small script under `.scripts/test_parse_pdmr.py` if one exists; otherwise just ad-hoc `python -c "from parse_pdmr import parse_iso_date; assert parse_iso_date('27th April 2026') == '2026-04-27'"`):
  - `parse_iso_date("27th April 2026") == "2026-04-27"`
  - `parse_iso_date("1st May 2026") == "2026-05-01"`
  - `parse_iso_date("2nd May 2026") == "2026-05-02"`
  - `parse_iso_date("3rd May 2026") == "2026-05-03"`
  - `parse_iso_date("21st April, 2026") == "2026-04-21"` (comma + ordinal)
  - `parse_iso_date("April 21st, 2026") == "2026-04-21"` (American)
  - **Regression**: `parse_iso_date("30 April 2026") == "2026-04-30"` (existing format must still work)
  - **Regression**: `parse_iso_date("28 and 30 April 2026") == "2026-04-30"` (multi-date still picks latest)

**Verification.**

- Re-run parser against the 13 `could_not_parse_tx_date` items in `_pending_review.json`. Expectation: all clear, none introduce wrong dates.
- Spot-check via `python .scripts/parse_pdmr.py --url <url> --rns-id <id> --force` on one Chesnara-style filing once we have a test URL.

**Rollback.** Single commit; `git revert` if the test set regresses. No data migration.

**Token estimate.** ~10k Sonnet (read parser surface + write change + run tests).

---

### P0-3 — Currency / price patterns

**Goal.** Decide via a 30-minute audit whether any of the 10 `foreign_currency` or 22 `could_not_separate_price_volume` items can be unblocked by a regex fix. If yes, ship the narrow fix. If no, mark P0-3 as deferred-to-v2 with a written rationale.

**This is gated on Decision D2 above.** Recommended path: audit, then drop. ~30 min audit + 0–1.5h fix depending on findings.

**Audit method.** Read the 10 foreign-currency items' `extracted.price` and `warnings` fields. Pattern-match: are they all genuinely foreign denominated, or are some pence/GBp variants the parser misclassified? Same for the price/volume separation cases.

**Files possibly touched.** `.scripts/parse_pdmr.py:219-227` (`NUMBER_RE`) and `.scripts/parse_pdmr.py:514-628` (`_parse_price_vol`). Only edit if audit reveals a real regex gap.

**Token estimate.** ~5k for audit. ~15k if a narrow fix is warranted.

---

### P0-4 — Bundled-PDMR filings (Schroders style)

**Goal.** Improve handling of bundled multi-PDMR filings so the user can manually split them quickly, without changing the parser's "refuse rather than guess" stance. Clears ~30 queue items via a documented manual workflow, not via auto-parsing.

**This is gated on Decision D1 above.** Recommended path: option (a) — keep refusing, but enrich the warning + write a runbook that makes manual splits one-line.

**Concrete changes (option a).**

1. `.scripts/parse_pdmr.py:663-672` — extend `_bundled_name_warning()` to extract the named people and their numbered positions and include them in the warning string. Today it returns a single sentence; we want it to return e.g. `"bundled multi-PDMR filing — names: ['Richard Oldfield (Group Chief Executive)', 'Ariadne Forte (CFO)', ...]"` so the manual triage doesn't require re-reading the HTML.
2. `.scripts/parse_pdmr.py:838-840` — keep the `return [], [bundled_w]` early-out, just with the richer warning.
3. P0-5 runbook (separate file) — worked example: copy the named PDMRs from the warning, build the deltas dict by hand or with a one-line LLM helper, run `python run_daily.py --cursor <id> --deltas '<json>'`.

**Files & lines.**

- `.scripts/parse_pdmr.py:663-672` — enrich `_bundled_name_warning`.
- `.scripts/parse_pdmr.py:838-840` — no logic change, just confirm the new warning shape flows through.
- `agents/qa-regressions.md` — new file. Add SDR rns_id 9540067 (Schroders), Pharos PHAR rns_id 9541260 from the brief, and 2-3 other bundled cases from the queue as named regression cases.

**Tests.**

- The bundled detection test stays the same: `_bundled_name_warning` must return non-None for the SDR-style input.
- New: the warning string must include each numbered PDMR's name and role, parseable enough that the runbook example reads cleanly.

**Verification.**

- Re-run parser against the 30 bundled items. Confirm all 30 still flag (no false acceptances) but with the enriched warning.
- Walk the runbook end-to-end on one bundled filing and confirm `dealings-log.json` gets the expected N rows (N = number of PDMRs in the filing).

**Rollback.** `git revert`. No schema change to `dealings-log.json`.

**Token estimate.** ~25k Sonnet (parser change + runbook drafting + test the workflow once).

---

### P0-1 — Triage 83 pending-review filings

**Goal.** After P0-2 and P0-4 are merged, re-run the parser, then close the queue down to ~30 items.

**This task is blocked by P0-2 and P0-4.** Doing it earlier wastes effort because the regex fixes will auto-clear chunks.

**Method.**

1. Re-run `update.py` (or the parser specifically) against everything in `_pending_review.json` after the fixes are merged. Re-fetch `_pending_review.json` and diff.
2. For each remaining item, classify into one of: (a) genuine v2-worthy edge case (foreign currency, multi-tranche, missing data) → leave in pending with a `wontfix-v1` tag; (b) one-off manual split via the runbook → resolve via `run_daily.py --deltas`; (c) bug surfaced by triage → file as new P0-N row in the backlog.
3. After triage, the pending queue should have ~30 items, all tagged with the v2 reason. The dashboard footer can show "20 wontfix-v1, 10 needs-manual-split" so the queue is honest about its state.

**Token estimate.** ~80k Sonnet (re-running, reading 30+ items, manual splits). Lower than the original 150k estimate because the regex fixes will have cleared chunks before manual triage starts.

---

### P0-5 — Runbook: "add a new issuer template"

**Goal.** Document the workflows so Rupert can fix simple breaks without dispatching Claude.

**Output.** New file `docs/runbooks/parser-fixes.md` (or `agents/runbook-parser.md` to match the existing role-profile naming — pick at write time). Sections:

- **When to read this.** "I see a new filing in `_pending_review.json` with a warning I haven't seen before."
- **Workflow A — manual delta for a one-off.** Copy URL + rns_id from the queue item, hand-build the JSON shape from CLAUDE.md, run `python run_daily.py --cursor <id> --deltas '<json>'`. Worked example with a real bundled filing.
- **Workflow B — bundled-PDMR split.** Take the enriched warning, build N deltas, single `run_daily.py` call. Worked example.
- **Workflow C — adding a regex pattern.** Where in `parse_pdmr.py` to look (`_DATE_FMTS`, `NUMBER_RE`, `_BUNDLED_PDMR_RE`). How to add a unit-style test inline. How to re-run against the queue to confirm.
- **Workflow D — when to escalate to Claude.** New issuer template, ambiguous price/volume table, foreign currency.

**Token estimate.** ~25k Sonnet.

---

## Order of execution + dependencies

```
D1 + D2 (user sign-off)
    │
    ├──> P0-2 (ordinal date)         ──┐
    ├──> P0-3 (currency, gated D2)   ──┤
    └──> P0-4 (bundled, gated D1)    ──┤
                                       │
                                       ▼
                        P0-1 (triage residue)
                                       │
                                       ▼
                              P0-5 (runbook)
```

P0-2/P0-3/P0-4 are independent of each other so can ship as separate PRs in any order (or one bundled PR — your call). P0-1 must come after them. P0-5 can start in parallel with P0-1 once the parser changes are in.

---

## Out of scope for Phase 0 (Phase 1+ territory)

- Multi-tranche purchase splitting (15 queue items). Genuine multi-execution disclosure that today emits one delta — needs `_build_delta` to fan out.
- Foreign-currency normalisation with FX rates (10 items).
- LLM fallback parser. That's Phase 2 (P2-1) and clears the residual pending queue properly.
- Parser refactor for token efficiency, the cluster detector spec changes, anything dashboard-side.

---

## Token budget rollup (Phase 0 dev only)

| Item | Est. tokens (Sonnet) | Notes |
|---|---|---|
| P0-2 | ~10k | Smallest, well-scoped |
| P0-3 | ~5k audit + 0-15k fix | Depends on D2 |
| P0-4 | ~25k | Includes runbook example |
| P0-1 | ~80k | After fixes — much lower than original 150k |
| P0-5 | ~25k | Runbook drafting |
| **Total** | **~150k** | Original backlog estimate was ~245k. Plan is leaner because P0-3 may collapse to audit-only and P0-1 benefits from upstream fixes |

---

## What we need from Rupert before any code change

1. **D1 sign-off:** option (a), (b), (c), or (d) for bundled-PDMR handling. Recommended: (a).
2. **D2 sign-off:** audit-then-decide, or skip the audit and just defer P0-3 to v2. Recommended: audit first.
3. **Branch / commit policy:** one branch per P0 item, or one bundled `phase-0-stabilisation` branch? (Your call — both are reasonable.)

Once those three are answered I can start with P0-2 (smallest, most concrete win) and report back when it's green.
