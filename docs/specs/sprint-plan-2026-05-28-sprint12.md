# Sprint 12 Plan — Data Correctness + Dashboard Polish

**Date drafted:** 2026-05-28
**Base state:** 4,572 transactions / 2,952 signals / 343 tests green
**Sprint 13 prerequisite:** B-025 Phase B (role_normalized signal firing) — confirmed already shipped 2026-05-20. Sprint 13 (Behavioural Signals B1/B2) is unblocked pending Sprint 12 gate-close.

---

## Pre-flight: Already shipped — remove from scope

The backend engineer audited the live codebase before scoping. The following
items from the backlog are **already complete** and require no work:

| Item | Status | Evidence |
|------|--------|----------|
| B-025 Phase B — signal logic on role_normalized | **DONE** | `signals/roles.py` line 3 and `classify_role.py` — both use `normalize_role()` dict lookup, shipped 2026-05-20 |
| B-021 — classify_issuers.py flag reset | **DONE** | `classify_issuers.py` Source E reads `_excluded_it_cef.csv` audit log, re-flags previously-excluded tickers on every run |
| B-024 — stale-backup warning | **DONE** | `start.bat` has `auto-seal 24`, `warn-stale 24`, `fail-stale 48` subcommands fully wired |
| B-019 — CAR chart per-series toggle + solo | **DONE** | `render_performance.py` lines 1056-1152: click-to-toggle, dblclick-to-solo, show-all, localStorage |
| B-009 — CAR chart 12-month sparkline | **DONE** | `export_dashboard_json.py` `_sparkline()` uses 13 monthly buckets with `None` gaps |
| A.1 — Type filter on company-page table | **DONE** | `render_company.py` has `data-txn-type` attributes and filter chip JS (Sprint 11) |

These save approximately 2 Claude sessions. Do not revisit them.

---

## Sprint theme and goal

**Theme: Data Correctness First**

Fix the parser gaps that are silently writing wrong prices, wrong share counts,
and wrong transaction types into the database — then close the dashboard
polish items that were deferred from Sprint 11.

**User need:** As a portfolio manager watching UK director dealings, I want every
transaction's price, share count, and type to be correctly parsed and stored in
consistent units, so that signal scores and CAR calculations reflect reality rather
than data artefacts.

---

## What is IN scope

### Phase 0 — Fast hygiene (Claude autonomous, no gate)

#### B-043: Non-cp1252 Unicode in two pipeline scripts

**Why first:** 15-minute fix. If `check_announced_at_coverage.py` or
`repair_pending_review.py` ever get wired into `refresh_all.py` subprocess
before Sprint 13, the pipeline crashes silently on Windows. Eliminate the risk
now.

**Files:** `.scripts/check_announced_at_coverage.py` (lines 97, 101),
`.scripts/repair_pending_review.py` (lines 144, 179, 183)

**Fix:** Replace `→`, `✓`, `✗`, `⚠` with `->`, `[ok]`, `[fail]`, `[warn]`
in all `print()` statements. Leave docstrings and comments unchanged.

**Acceptance criteria:**
- `grep -Pn "[^\x00-\x7F]"` on both files returns zero hits in print() calls
- `python -m py_compile` on both files exits zero

---

#### B-012: Fix or retire ~4 failing tests in older test suites

**Why now:** Failing tests undermine confidence in "343 tests green." Clean
baseline before parser work adds more tests.

**Process:**
1. Claude runs `python -m unittest discover -s .scripts -p "test_*.py" -v` from
   `/tmp/` mirror (to avoid FUSE staleness)
2. Triage each failure:
   - `ImportError` → install missing dep and re-run (sandbox issue, not a code bug)
   - `AssertionError` on a count → genuine regression; trace and fix
   - `SyntaxError` on a just-edited file → FUSE staleness; use /tmp mirror
   - Test for a removed feature → delete with one-line comment explaining why

**Acceptance criteria:**
- `python -m unittest discover -s .scripts -p "test_*.py"` exits zero
- Any retired test has a comment explaining removal — no silent deletes

---

### Phase 1 — Parser fixes Phase A (code only — no signal-count shift, Rupert gate before Phase 2)

All three parser fixes follow the mandatory Phase A / Phase B split
(`feedback_phase_gated_diff_first`). Phase 1 is **Phase A only** — additive
changes that do not alter existing correctly-parsed rows. Phase B (running the
corpus reparse and reviewing the diff CSV) is the gate between Phase 1 and Phase 2.

#### C.2: Par-value "N pence each" captured as share count

**File:** `.scripts/parse_pdmr.py`, function `_parse_volume_cell()`

**Root cause:** The parser matches the share par-value description (e.g. "1 pence
each") as a share volume, producing nonsense counts like 1 or 50.

**Fix:** Add a module-level regex and a post-match guard:
```python
# Near existing module-level regexes (~line 76)
_PAR_VALUE_RE = re.compile(
    r"pence\s+each|p\s+each|ordinary\s+shares?\s+of\b",
    re.IGNORECASE,
)

# Inside _parse_volume_cell(), after computing int_val, alongside existing
# _looks_like_date_bleed check:
if int_val < 100 and _PAR_VALUE_RE.search(text):
    return 0, ["par_value_captured_as_shares"]
```

**New unit tests (in `test_parser.py`):**
```python
def test_par_value_captured_as_shares(self):
    result, warns = _parse_volume_cell("1 pence each")
    self.assertEqual(result, 0)
    self.assertIn("par_value_captured_as_shares", warns)

def test_par_value_large_volume_not_rejected(self):
    # 500 >= 100, so par-value guard does not fire
    result, warns = _parse_volume_cell("500 shares at 210 pence each")
    self.assertEqual(result, 500)
    self.assertNotIn("par_value_captured_as_shares", warns)
```

**Acceptance criteria:** Both unit tests green. Signal firing delta = 0 (Phase A).

---

#### C.3: Nil-cost exercise legs mis-tagged as SELL

**File:** `.scripts/parse_pdmr.py`, function `_classify_type()`

**Root cause:** On multi-transaction filings, vesting and exercise rows get tagged
as SELL, inflating the sell signal count and misrepresenting individual directors.

**Fix:** Add a module-level regex and a post-classification override:
```python
# Near other module-level regexes
_NIL_COST_RE = re.compile(
    r"nil[\-\s]cost|vesting\s+of|exercise\s+of",
    re.IGNORECASE,
)

# Inside _classify_type(), after matching "SELL":
def _classify_type(text: str) -> tuple:
    if not text:
        return None, ["could_not_classify_type"]
    for label, rgx in _TYPE_KEYWORDS:
        if rgx.search(text):
            if label == "SELL" and _NIL_COST_RE.search(text):
                return "EXERCISE", []
            return label, []
    return None, ["could_not_classify_type"]
```

**New unit test:**
```python
def test_nil_cost_exercise_not_classified_as_sell(self):
    text = "Disposal of shares arising from nil-cost option vesting"
    tx_type, warns = _classify_type(text)
    self.assertEqual(tx_type, "EXERCISE")
    self.assertEqual(warns, [])
```

**Acceptance criteria:** Unit test green. Dry-run spot-check: Rupert verifies 5
changed rows against source on Investegate before confirming. Signal firing delta
reviewed at gate.

---

#### C.1: USD/foreign-currency transactions routed to pending (not silently dropped)

**File:** `.scripts/parse_pdmr.py`

**Root cause:** `_parse_price_cell()` already detects USD/EUR correctly but drops
the row silently. The row never appears in `_pending_review.json`, making it
invisible to Rupert.

**Fix:** In `parse_announcement()`, in both the Path A (~line 1935) and Path A2
(~line 1849) blocks where `"foreign_currency" in r_w` causes a `continue`:
```python
# Before:
if "foreign_currency" in r_w:
    warnings.append("foreign_currency")
    continue

# After:
if "foreign_currency" in r_w:
    warnings.append("foreign_currency_detected")
    _log_suspect_filing(
        {k: r.get(k) for k in ("date", "ticker", "company", "director", "type", "shares", "price")},
        ["foreign_currency_detected"],
        url, rns_id,
    )
    continue
```

This uses the existing `_log_suspect_filing()` appender — sequential JSONL
append, safe on FUSE. No new file paths.

**New unit test:**
```python
def test_foreign_currency_logged_to_pending(self):
    # monkeypatch _log_suspect_filing, confirm it is called on a USD price block
    ...
```

**Acceptance criteria:** Rows with USD/EUR prices appear in `_suspect_filings.jsonl`
with `foreign_currency_detected` rather than vanishing silently.

---

#### B-001 re-audit: Multi-row bulk filings + DRIP/SIP signal exclusion

**Files:** `.scripts/parse_pdmr.py` (`_extract_via_table()`), `.scripts/eval_signals.py`

**Status:** The Sprint 3 multi-row loop fix is present (line 1711 iterates all
`data_rows`). This is primarily a **verification item** — no parser code change
expected.

**Process:**
1. Claude runs the existing parser tests against a multi-row fixture to confirm
   the Sprint 3 fix holds. If any test shows only 1 row extracted from a
   known 3-row filing, patch `_extract_via_table()` to fix the regression.
2. **DRIP/SIP exclusion check (Rupert decision 2026-05-28):** Confirm that
   `eval_signals.py` excludes `DRIP` and `SIP` transaction types from signal
   scoring. Grep for the type-exclusion list in `eval_signals.py`. If `DRIP`
   or `SIP` are not already in it, add them before the Phase 1 corpus reparse
   runs — otherwise newly-recovered DRIP/SIP rows from B-001 will fire signals.

**Acceptance criteria:**
- Existing multi-row parser tests pass
- `eval_signals.py` type-exclusion list confirmed to include `DRIP` and `SIP`
- If fix needed: added and unit test confirms DRIP/SIP rows produce zero signals

---

### Phase 1 gate — Rupert reviews parser diff CSV

**After Phase 1 code is complete, Rupert runs from PowerShell:**

```powershell
cd C:\Dev\DirectorsDealings

# Dry-run first — inspect changes without touching the DB
python .scripts\reparse_corpus.py --dry-run 2>&1 | Tee-Object -FilePath .data\sprint12-phase1-dryrun.txt

# Review the output:
# - par_value_captured_as_shares warnings: expect <50, none previously in DB
# - SELL -> EXERCISE reclassifications: spot-check 5 against Investegate source
# - foreign_currency_detected entries in _suspect_filings.jsonl
# - Transaction count delta (should be small and explainable)

# If satisfied:
python .scripts\reparse_corpus.py --confirm
python .scripts\eval_signals.py
python .scripts\export_dashboard_json.py
python .scripts\backtest.py
python .scripts\build_dashboard.py
```

**Gate decision:** Rupert reviews `.data/sprint12-phase1-dryrun.txt` and the
diff CSV. Confirms SELL→EXERCISE changes look correct by spot-checking 5 rows on
Investegate. Only then does Phase 2 open.

---

### Phase 2 — Price unit audit (Claude runs read-only, then Rupert gate)

#### B-060: Pence vs pounds audit

**Why first in Phase 2:** A.2 (price column label) is conditional on this audit.
C.1/C.2/C.3 have already landed on a cleaner price baseline. Now diagnose whether
any prices are stored in the wrong unit.

**Claude runs from bash (read-only, safe):**
```bash
cp /sessions/<session-id>/mnt/DirectorsDealings/.data/directors.db /tmp/audit_b060.db

# Price distribution by order of magnitude
sqlite3 /tmp/audit_b060.db "
  SELECT
    CASE WHEN price < 5   THEN 'likely_pounds'
         WHEN price < 50  THEN 'ambiguous'
         ELSE                  'likely_pence'
    END AS bucket,
    COUNT(*) AS n,
    ROUND(MIN(price),2) AS min_p,
    ROUND(MAX(price),2) AS max_p
  FROM transactions
  WHERE type IN ('BUY','SELL') AND price > 0
  GROUP BY bucket;
"

# Top 20 outliers
sqlite3 /tmp/audit_b060.db "
  SELECT ticker, director, date, price, shares, type
  FROM transactions
  WHERE type IN ('BUY','SELL') AND price > 500
  ORDER BY price DESC LIMIT 20;
"
```

Cross-check 5–10 known tickers (BARC, LLOY, NXT, HSBA, BP.) against known price
ranges to calibrate.

**Expected finding:** `_parse_price_cell()` already converts `post in {"p", "pence"}`
→ `val / 100.0`. Risk is bare-number rows with no suffix (Investegate occasionally
omits "p") that land as raw pence (e.g. 210 for £2.10/share).

**Claude produces a written diagnostic report** (transaction-count breakdown,
sample of suspicious rows, recommendation). Rupert reviews before any code change.

**If widespread mixing confirmed (>5% of BUY/SELL rows likely wrong unit):**
Add pence-normalisation to `parse_pdmr.py:_parse_price_cell()` — if price > 500
and no £ suffix, divide by 100 and flag. Then Rupert runs a corpus reparse (same
PowerShell sequence as Phase 1 gate).

**If clean (< 5% affected or all within allowlist like AZN, NXT):**
No parser change. Note any known high-price tickers in a new `_HIGH_PRICE_ALLOWLIST`.

---

#### A.2: Price column label "(p)" → "(£)"

**File:** `.scripts/dashboard/render_company.py`

**Gated on:** B-060 audit confirming storage units.

- If prices confirmed in pounds: change `"(p)"` → `"(£)"`. Single-line edit.
- If prices confirmed in pence and a normalisation fix was applied in B-060:
  change `"(p)"` → `"(£)"` as part of the same rebuild.
- If prices are a mix and no corpus reparse happened yet: leave label as `"(p)"`
  with a code comment; revisit after B-060 corpus fix.

**Acceptance criteria:** Label matches confirmed storage unit. Dashboard rebuilds
cleanly. No 100× display errors.

---

### Phase 3 — Dashboard polish (independent of parser work, no gate)

Both items are pure front-end changes — no Python data pipeline or DB touch.
They can run in either order and do not require a Rupert gate (Rupert reviews
visually by opening the dashboard in a browser).

#### A.3: BUY/SELL-only filter on company page price chart

**File:** `.scripts/dashboard/render_company.py`, `markerDatasets` loop (~lines 671-696)

**Fix:** Add a constant and a type guard inside the marker loop:
```javascript
// NED buys included per Rupert decision 2026-05-28
var CHART_MARKER_TYPES = new Set(['exec_buy', 'ned_buy', 'sell']);

markers.forEach(function(m) {
    var mtype = m.mtype || 'exec_buy';
    // Cluster rings always shown (they overlay BUY markers by design)
    if (mtype !== 'cluster' && !CHART_MARKER_TYPES.has(mtype)) return;
    // ... rest of existing loop unchanged
});
```

**Pre-check:** Grep `mtype` in `render_company.py` to confirm the exact string
values Python sets for NED buys (expected: `ned_buy`) and exec buys (expected:
`exec_buy`). Adjust the set if the actual strings differ.

**After edit:** Rupert runs `python .scripts\build_dashboard.py` from PowerShell,
opens a company page, confirms GRANT/EXERCISE/SIP markers are gone.

**Acceptance criteria:** Company page price chart shows only BUY and SELL markers
(plus cluster rings). GRANT, EXERCISE, SIP, DRIP markers do not appear.

---

#### B-059: Company search box on index page

**Two files, one feature:**

**`export_dashboard_json.py`:** Add `companies_index` to the signals JSON export:
```python
company_rows = conn.execute(
    "SELECT DISTINCT ticker, company FROM transactions "
    "WHERE ticker IS NOT NULL ORDER BY ticker"
).fetchall()
out["companies_index"] = [
    {"ticker": r["ticker"], "company": r["company"] or "",
     "url": f"companies/{r['ticker']}.html"}
    for r in company_rows
]
```

Confirm which top-level key in the signals JSON dict to add this to (read
`_export_signals()` first to verify structure).

**`render_index.py`:** Add a search input to the header HTML block, plus inject
the companies index as a JS variable and a `keyup` handler:
```javascript
const COMPANIES_INDEX = {{ companies_index_json }};
const searchEl = document.getElementById('companySearch');
const dropEl   = document.getElementById('companyDropdown');

searchEl.addEventListener('keyup', function() {
    const q = this.value.trim().toLowerCase();
    if (!q) { dropEl.classList.add('hidden'); return; }
    const matches = COMPANIES_INDEX.filter(function(c) {
        return c.ticker.toLowerCase().startsWith(q) ||
               c.company.toLowerCase().includes(q);
    }).slice(0, 8);
    if (!matches.length) { dropEl.classList.add('hidden'); return; }
    dropEl.innerHTML = matches.map(function(c) {
        return '<li><a href="' + c.url + '">' +
               c.ticker + ' — ' + c.company + '</a></li>';
    }).join('');
    dropEl.classList.remove('hidden');
});
document.addEventListener('click', function(e) {
    if (!searchEl.contains(e.target)) dropEl.classList.add('hidden');
});
```

Ticker-prefix match ranks above company name substring by filter order.

**After edit:** Rupert runs:
```powershell
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

**New unit test:** In `test_export_dashboard_json.py`:
```python
def test_companies_index_present_in_export(self):
    data = load_signals_json()
    self.assertIn("companies_index", data)
    self.assertIsInstance(data["companies_index"], list)
    if data["companies_index"]:
        self.assertIn("ticker", data["companies_index"][0])
        self.assertIn("url", data["companies_index"][0])
```

**Acceptance criteria:** `companies_index` key present in signals JSON. Search
box visible on index page. Typing "BARC" or "Barclays" returns the correct company
page link. Clicking outside the dropdown closes it.

---

## What is OUT of scope

| Item | Reason deferred |
|------|----------------|
| B-015 — pending-sweep safety check | Recovers 6 stranded transactions — valuable but low volume impact. Separate housekeeping sprint. |
| B-014 — archive 4,100 unrecoverable pending entries | Housekeeping. Not data correctness. |
| B-020 — triage 334 orphan candidates | 2.5h triage work. Needs its own scoping session. |
| B-023 — bundled-PDMR AAL "PCA" pattern | Edge case. One filing. Defer. |
| B.1 — AIC website 404 / sustainable IT classifier source | Research task. The manual CSV workaround is adequate for now. |
| **Sprint 13 Behavioural Signals (B1 + B2)** | Explicitly out of scope. Sprint 13 opens after Sprint 12 gate-close. B-025 Phase B is confirmed already shipped — the prerequisite is met. |

**Scope creep flag (for Rupert):** If B-060 reveals widespread pence/pounds
mixing requiring a corpus reparse, that reparse becomes this sprint's biggest
work item. Budget a separate Rupert session for it rather than combining it with
the Phase 1 reparse. Two corpus reparsing events in one sprint is manageable but
requires clean sequencing: Phase 1 reparse confirmed and pipeline rebuilt before
Phase 2 reparse starts.

---

## Sprint structure summary

```
Phase 0  │ B-043 + B-012 (hygiene)           │ Claude autonomous     │ ~1.5h Claude
         │                                    │                       │
Phase 1A │ C.2 + C.3 parser code             │ Claude autonomous     │ ~1.5h Claude
Phase 1B │ C.1 parser code                   │ Claude autonomous     │ ~30min Claude
Phase 1C │ B-001 re-audit                    │ Claude autonomous     │ ~20min Claude
         │                                    │                       │
GATE 1   │ Rupert reviews dry-run diff CSV   │ Rupert runs reparse   │ ~30min Rupert
         │                                    │                       │
Phase 2  │ B-060 audit (read-only)           │ Claude autonomous     │ ~40min Claude
Phase 2A │ A.2 label fix (conditional)       │ Claude autonomous     │ ~15min Claude
         │                                    │                       │
GATE 2   │ Rupert reviews B-060 report       │ Rupert decision       │ ~10min Rupert
         │ (+ runs reparse if fix needed)    │                       │
         │                                    │                       │
Phase 3  │ A.3 + B-059 dashboard polish      │ Claude autonomous     │ ~2h Claude
         │                                    │                       │
FINAL    │ Full test suite green             │ Claude runs           │ ~30min Claude
```

Two hard Rupert gates:
1. **Gate 1** — after Phase 1 parser code lands. Rupert reviews dry-run diff CSV,
   spot-checks SELL→EXERCISE changes on Investegate, then runs `reparse_corpus.py --confirm`
   and the 4-step pipeline rebuild.
2. **Gate 2** — after B-060 audit report. Rupert decides whether a corpus reparse
   is needed for pence/pounds normalisation. If yes, runs it before Phase 3.

---

## Recommended session split

| Session | Phases | Rupert involvement |
|---------|--------|-------------------|
| Session 1 | Phase 0 + Phase 1A + 1B + 1C + B-001 re-audit | None during build; Gate 1 at end |
| Session 2 | Phase 2 audit + A.2 + Phase 3 (A.3 + B-059) | Gate 2 between audit and Phase 3 |

---

## Effort estimate

| Phase | Claude time | Rupert time |
|-------|-------------|-------------|
| Phase 0 (B-043, B-012) | ~1.5h | ~5min (run test suite) |
| Phase 1 (C.1, C.2, C.3, B-001) | ~2.5h | ~30min (Gate 1: review diff + PowerShell runs) |
| Phase 2 (B-060 audit + A.2) | ~1h | ~10min (Gate 2: review report) + optional reparse |
| Phase 3 (A.3, B-059) | ~2h | ~10min (visual review in browser) |
| **Total** | **~7h Claude** | **~60min Rupert** |

---

## Open questions — RESOLVED 2026-05-28

1. **NED buys on company chart (A.3):** ✅ **Include NED buys.**
   `ned_buy` is to be added to `CHART_MARKER_TYPES` alongside `exec_buy` and
   `sell`. Both executive and non-executive buy markers appear on the chart.

2. **B-001 scope — DRIP/SIP signal firing:** ✅ **DRIP and SIP rows must NOT fire signals.**
   Any transaction row recovered by B-001 with type `DRIP` or `SIP` must be
   excluded from signal scoring. Implementation: `eval_signals.py` already
   filters by type; confirm `DRIP` and `SIP` are in the exclusion list. If not,
   add them before the Phase 1 corpus reparse runs. B-001 Phase B (signals from
   recovered rows) does not open — DRIP/SIP rows are data-complete but
   signal-inert.

3. **Corpus reparse sequencing:** ✅ **Confirmed acceptable.**
   Two reparse events in one sprint (Phase 1 gate + potential B-060 fix) are
   approved. Rupert will run them as separate PowerShell sessions.

---

## Definition of done

Sprint 12 is complete when all of the following are true:

- [ ] **B-043:** Zero non-cp1252 chars in `print()` calls in both pipeline scripts. `py_compile` green.
- [ ] **B-012:** `python -m unittest discover -s .scripts -p "test_*.py"` exits zero.
- [ ] **C.2:** `_parse_volume_cell` par-value guard in place. Two new unit tests green.
- [ ] **C.3:** Nil-cost SELL → EXERCISE override in place. One new unit test green.
- [ ] **C.1:** Foreign-currency rows routed to `_suspect_filings.jsonl` rather than silently dropped. One new unit test green.
- [ ] **B-001:** Re-audit complete. Either confirmed working or regression fixed.
- [ ] **Gate 1:** Rupert has reviewed dry-run diff CSV and confirmed `reparse_corpus.py --confirm` + 4-step pipeline rebuild.
- [ ] **B-060:** Written diagnostic report produced and Rupert has reviewed it.
- [ ] **A.2:** Price column label matches confirmed storage unit.
- [ ] **Gate 2:** B-060 recommendation accepted. If corpus reparse needed, Rupert has run it.
- [ ] **A.3:** Company page price chart shows BUY and SELL markers only. GRANT/SIP/DRIP markers absent.
- [ ] **B-059:** Company search box live on index page. `companies_index` key present in signals JSON. Unit test green.
- [ ] **Test count:** ≥ 348 tests passing (net +5 minimum from new parser + export tests).
- [ ] **Sprint 13 gate:** Confirmed B-025 Phase B already shipped. Sprint 13 (B1 + B2 Behavioural Signals) is formally unblocked.

---

## Reference

- `docs/backlog.md` — source of truth for all bug descriptions
- `docs/specs/sprint-11-candidates.md` — Sprint 11 A-tier / C-tier candidate details
- `docs/specs/08-phase-4-behavioural-signals.md` — Sprint 13 scope (B1 Lone Conviction Buy, B2 Anti-Crowded Cluster Kill)
- `docs/specs/sprint-plan-2026-05-26-sprint11.md` — preceding sprint; structure reference
