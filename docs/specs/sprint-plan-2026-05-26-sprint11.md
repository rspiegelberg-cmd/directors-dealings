# Sprint 11 plan — Company-page UI fixes (zero data-pipeline touch)

> Theme: **three small, contained dashboard UX fixes on the company
> page, with a hard constraint that NO change in this sprint touches
> the scrape, parser, ingest, backfill, classify, signal-eval,
> backtest, or DB-schema layers.**
>
> Status: **APPROVED at Gate 1 on 2026-05-26.** Build authorised. See
> Section 9 for the decisions log.
>
> Date drafted: 2026-05-26. Author: Claude (main session).
> This sprint is single-file scope: only
> `.scripts/dashboard/render_company.py` is touched (plus optionally
> `.scripts/dashboard/render_helpers.py` for any shared utility).

**Companion docs:**

- [`sprint-11-candidates.md`](./sprint-11-candidates.md) — full
  candidate list. Sprint 11 takes the A-tier subset (A.1, A.2, A.3).
  B.1 (AIC 404) and C-tier (Sprint-9 deferred parser bugs) are
  **explicitly deferred to Sprint 12** to honour the data-pipeline
  safety constraint.
- [`sprint-plan-2026-05-25-sprint10.md`](./sprint-plan-2026-05-25-sprint10.md)
  — preceding sprint; closed 2026-05-26. Used as structure
  reference.
- [`sprint-10-backtest-diff.md`](./sprint-10-backtest-diff.md) —
  baseline state coming into Sprint 11 (132 IT/CEF exclusions,
  3087 signal rows, 2602 backtest CAR rows).

**Estimate units:** Rupert-time = wall-clock attention; gates =
mandatory sign-off pauses; risk = Low / Medium / High.

---

## Section 0 — The binding constraint (read first)

Rupert's instruction 2026-05-26: **"make sure it is thoroughly QA'd
in particular so as not to cause any adverse effects on data
download."**

Interpreting that constraint operationally for this sprint:

| File / area | Touch allowed? |
|-------------|----------------|
| `.scripts/scrape_investegate.py` | NO |
| `.scripts/parse_pdmr.py` | NO |
| `.scripts/run_scrape.py` | NO |
| `.scripts/backfill_prices.py` | NO |
| `.scripts/backfill_benchmarks.py` | NO |
| `.scripts/fetch_prices.py` | NO |
| `.scripts/refresh_all.py` (STEPS list) | NO |
| `.scripts/classify_issuers.py` | NO |
| `.scripts/exclude_investment_trusts.py` | NO |
| `.scripts/eval_signals.py` | NO |
| `.scripts/backtest.py` | NO |
| `.scripts/export_dashboard_json.py` | NO |
| `.scripts/db.py` / `db_schema.sql` / migrations | **NO** |
| `.scripts/audit_dates.py` | NO |
| `.scripts/dashboard/render_company.py` | YES (primary) |
| `.scripts/dashboard/render_helpers.py` | YES (only if shared utility needed) |
| `.scripts/dashboard/templates.py` | YES (only if shared CSS/JS needed) |
| New unit-test file under `.scripts/test_*.py` | YES |

If any phase of this sprint requires touching a NO-row file to land
the feature, that phase is **deferred to a separate future sprint**
where it can be scoped with proper data-pipeline regression review.

The QA agent's primary lens at Gate 2 will be: "does any code change
in this sprint touch a NO-row file?"

---

## Section 1 — Goal & success criteria

**Goal.** Ship three contained UI fixes on the company page,
producing a measurably cleaner dashboard experience without altering
any aspect of how data flows into the system.

**Success criteria (measurable):**

1. **A.1 — Type filter:** company page renders a filter widget above
   the transactions table. Clicking a filter toggle visibly
   hides/shows rows by `type` column. Default state and persistence
   behaviour confirmed at Gate 1.
2. **A.2 — Price column:** column header reads "Price (£)" instead
   of "Price (p)". The displayed values match the new header — i.e.
   if stored values turn out to be pence, they are divided by 100 at
   render time. Investigation step (Section 5.A.2) determines
   which.
3. **A.3 — Chart markers:** company-page price chart shows markers
   ONLY for `type IN ('BUY', 'SELL')`. Markers for GRANT, EXERCISE,
   SIP, DRIP, DIVIDEND, etc. are filtered out before the chart is
   built.
4. Full `unittest discover` green on Windows; test count unchanged
   or +1 (if a render-output test is added for A.2 unit handling).
5. **A full pipeline run (`start.bat` → Refresh) completes with
   identical row counts compared to a pre-Sprint-11 run.** This is
   the data-download safety check.

---

## Section 2 — Out of scope (explicit data-pipeline guard rails)

Items explicitly out of scope for Sprint 11:

- **Adding a `price_gbp` column** to `transactions`. This would be
  the "cleaner long-term" fix for A.2 (see candidates doc) but
  requires a schema migration AND an ingest-time conversion in
  `parse_pdmr.py`. Both forbidden in Sprint 11. If pence-to-£
  conversion is needed, it happens at render time only.
- **Any change to how prices are scraped, parsed, or written to the
  DB.** A.2 is display-only.
- **B.1 — AIC website 404 fix.** Classifier-side change; could
  affect what gets flagged is_excluded_issuer and therefore what
  ingest filter drops. Sprint 12 scope.
- **C-tier (Sprint 9 deferred parser bugs).** Touches `parse_pdmr.py`
  + potential `reparse_corpus.py`. Sprint 12 scope.
- **Adding `type` filter persistence to the URL or cookies.** If
  needed, ship as Sprint 12 follow-up — Sprint 11 keeps it in-memory
  per-page-visit to minimise change surface.
- **Filtering chart markers via the same widget as A.1.** A.3 is a
  permanent filter (BUY+SELL only); not user-toggleable in Sprint 11.

---

## Section 3 — Files touched

| File | Change type | Detail |
|------|-------------|--------|
| `.scripts/dashboard/render_company.py` | Edit | All three items; ~80 LOC added across `_transactions_table`, `_price_chart`, and a small JS block. |
| `.scripts/dashboard/render_helpers.py` | Possible edit | If A.2 needs a shared `format_price_gbp(v)` helper. Negligible. |
| `.scripts/test_render_company.py` | New file (small) | One snapshot test for the rendered transactions table HTML after the changes — proves output stays sensible. |

**No DB writes. No new dependencies. No new pipeline steps. No new
config files. No deletion of any existing function.**

---

## Section 4 — Build phases

### Phase 1 — A.2 unit investigation (read-only)

**Why first:** A.2's correctness depends on whether `transactions.price`
is stored as pence or pounds. Until we know, we can't write A.2
correctly. This phase is pure investigation — zero code change.

**Steps:**

1. Pick three known recent BUY rows from the DB (different sectors).
2. For each, look up the source RNS filing via the row's `url`
   field. Note the price-per-share quoted in the original PDMR
   notification.
3. Compare against the `price` value stored in `transactions`.
4. **Conclusion expected:** either (a) `price` is in pence (e.g.
   stored as `150.0` for £1.50/share) — A.2 needs a `/100` at
   render time; or (b) `price` is in pounds — A.2 is label-only;
   or (c) **mixed** — some rows pence, some pounds.
5. **Mixed-units check (Phase 1 hard exit condition):** if Phase 1
   reveals mixed units, A.2 is NOT a safe Sprint 11 item — it
   becomes a Sprint 12 candidate requiring an ingest-time
   normalisation (which is a NO-row file change). Phases 2-onward
   for A.2 are aborted; Sprint 11 ships A.1 + A.3 only. The mixed-
   units case is the realistic worst-case because UK PDMR filings
   inconsistently quote in pence vs pounds depending on the issuer
   template — but in practice, `parse_pdmr.py` is supposed to
   normalise. Phase 1 verifies whether it actually does.

**Deliverable:** a one-paragraph addendum to this plan recording the
sample tickers checked and the conclusion (one of a / b / c). No
code change yet.

**Estimate:** ~15 min Rupert time (he eyeballs the three RNS
filings); ~10 min Claude time (DB queries + comparison write-up).

### Phase 2 — A.2 implementation (display-only)

Based on Phase 1's verdict:

- **If label-only:** change line 307 from `Price (p)` to
  `Price (&pound;)`. Done.
- **If `/100` needed:** change the same line AND change the cell
  formatter at line 288 from `{float(price):.2f}` to either
  `{float(price)/100.0:.2f}` or `{h.gbp(float(price)/100.0)}`
  (the latter gives proper £-prefix formatting).

**Estimate:** ~20 min Claude time.

### Phase 3 — A.3 chart-marker filter

In `_price_chart()` (around line 230), modify how `txn_markers` is
constructed: filter the source list to `t.type IN ('BUY', 'SELL')`
before passing into the payload.

**Why Python-side (not JS-side):** smaller change surface; the
payload JSON arriving at the browser is already filtered, so no
chart-rendering JavaScript modification needed. Lower data-flow risk.

**Estimate:** ~30 min Claude time + test.

### Phase 4 — A.1 type filter widget

Adds a row of filter chips ABOVE the transactions table. Each chip
represents one transaction type seen in the current company's
transactions (e.g. BUY, SELL, GRANT). Clicking toggles visibility
of rows whose `type` column matches.

Implementation choice (locked at Gate 1):

- **Recommended:** small inline JS block in `_transactions_table`
  output. Adds `data-txn-type="BUY"` (etc.) to each `<tr>`.
  Filter chip clicks toggle CSS `display:none` on non-matching rows.
  Pure client-side. No new JS library.
- **Alternative:** add to existing `templates.py` shared JS block —
  but that touches a file shared across all pages, raising the blast
  radius. Recommendation: keep it inline in `render_company.py`.

**Estimate:** ~60 min Claude time + test.

### Phase 5 — Pipeline-safety regression check (Gate 3)

Before declaring Sprint 11 closed, Rupert runs:

```powershell
python -m unittest discover -s .scripts -p "test_*.py"
# Expected: 329 + 1 (new render_company test) = 330 tests green.

start.bat
# Click Refresh in the dashboard UI. Expected: pipeline completes
# with identical row counts to the pre-Sprint-11 state — same
# scrape input handling, same parser output, same signal counts,
# same backtest counts. The only differences should be on the
# rendered HTML.
```

This is the explicit data-download safety check. Any deviation in
signal counts, backtest counts, or scrape outcomes between pre- and
post-Sprint-11 runs flags a Sprint 11 defect, since the sprint
should be a NO-OP from the data-pipeline's perspective.

**Estimate:** ~10 min Rupert time.

---

## Section 5 — Detailed specs

### 5.A.1 — Type filter widget

Above the existing `<table>` element in `_transactions_table`, emit:

```html
<div class="px-4 py-2 flex flex-wrap gap-2 border-b border-slate-100 bg-slate-50/50">
  <span class="text-[10px] uppercase tracking-wide text-slate-500 self-center">Filter:</span>
  <button class="txn-filter active" data-filter="ALL">All</button>
  <button class="txn-filter" data-filter="BUY">Buy</button>
  <button class="txn-filter" data-filter="SELL">Sell</button>
  ...one button per distinct type seen in this company's rows...
</div>
```

Plus a small inline `<script>` block (4-6 lines of JS) that wires
click handlers to toggle `<tr>` visibility based on `data-txn-type`.

**Edge cases to handle:**

- A company with only one transaction type — still render the filter
  bar (consistency), but only show "All" + that one type.
- An "ALL" toggle that resets — needed so users can recover from any
  filter state without reload.
- No persistence across reload (per Section 2 out-of-scope).

### 5.A.2 — Price column

**After Phase 1 investigation, one of:**

Option label-only (line 307):
```python
'<th class="px-3 py-2 text-right">Price (&pound;)</th>'
```

Option label + `/100` (lines 288 + 307):
```python
# Line 288 — divide by 100 since values are pence
f'<td class="px-3 py-2 text-right tabular-nums">{float(price) / 100.0:.2f}</td>'
# Line 307 — relabel
'<th class="px-3 py-2 text-right">Price (&pound;)</th>'
```

### 5.A.3 — Chart-marker filter (Python-side)

In `_price_chart()` (around line 220-230), find the loop that builds
`txn_markers`. Add a guard:

```python
ttype = (t.get("txn_type") or "").upper()
if ttype not in ("BUY", "SELL"):
    continue
```

That's it — JS code at line ~620 is unmodified; the payload it
receives just no longer contains non-BUY/SELL markers.

**Cluster ring consideration:** memory note (Section 2 out-of-scope)
defers the cluster-ring-on-filtered-trigger handling. If a cluster
fires on a GRANT row, the GRANT marker is filtered but the cluster
ring would still be drawn (because clusters are computed from
transactions, not markers). This is acceptable in Sprint 11 because
clusters in practice almost always fire on BUYs, not GRANTs. Sprint
12 can revisit if needed.

---

## Section 6 — Preconditions (Gate 1)

All preconditions for Sprint 11 build:

1. ✓ Sprint 10 fully closed (2026-05-26). 329 unittests green.
   Dashboard JSONs and HTML rebuilt with clean post-purge data.
2. ✓ Sprint 10 backtest diff documented (`sprint-10-backtest-diff.md`).
3. **Pending Rupert at Gate 1:**
   - A.1 default state — show all types, or default to BUY+SELL only?
     (Recommendation: show all by default — least surprising.)
   - A.1 persistence — survives reload, or per-visit only? (Recommendation:
     per-visit only — keeps scope tight.)
   - A.3 user toggle vs permanent filter — permanent per candidates
     doc. Confirm.
   - A.3 cluster-ring on filtered trigger — leave drawn (current
     behaviour, simplest), or hide if its underlying transaction is
     filtered? (Recommendation: leave drawn, Sprint 12 follow-up if
     needed.)

---

## Section 7 — Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | A change accidentally touches the data-pipeline layer (e.g. someone modifies `export_dashboard_json.py` to filter markers there instead of in `render_company.py`). | Medium-Low | **HIGH** (defeats the binding constraint) | Section 2 lists explicit out-of-scope items + file allowlist. QA agent at Gate 2 explicitly checks no NO-row file is touched. Phase 5 pipeline-regression run is the empirical safety net. |
| 2 | A.2's unit conversion is wrong direction (we divide when we should have left alone, or vice versa). | Low | Medium (visual error, not data error) | Phase 1 investigation runs FIRST; Phase 2 only proceeds after Phase 1 verdict. Snapshot test in Phase 4 catches obvious regressions. |
| 2b | A.2 — `price` column contains MIXED units across rows (some pence, some pounds). Render-time `/100` would be wrong for the pound-denominated half. | Low–Medium | Medium | Phase 1 investigation includes explicit mixed-units check (Section 4 Phase 1 step 5). If MIXED is the finding, A.2 is **aborted** for Sprint 11 and re-scoped as Sprint 12 (requires ingest-time normalisation, which is a NO-row file change). Sprint 11 ships A.1 + A.3 only in that case. |
| 3 | A.1 inline JS conflicts with existing JS on the page (event listeners or selectors). | Low | Low | Use scoped class names (`txn-filter`) and IDs that don't collide with existing dashboard JS. Test by clicking through after build. |
| 4 | A company has hundreds of transactions; client-side row hiding is slow. | Low | Low | Even 500 `<tr>` elements is well within native browser capability for `style.display` toggling. No virtualised table needed. |
| 5 | A.3 filtering hides legitimate analytical context (e.g. user wants to see when SIP buys happened relative to BUY clusters). | Low-Medium | Low | Permanent filter is per Rupert preference. If feedback arrives, Sprint 12 can add a toggle. |

---

## Section 8 — Rollback

Trivial — Sprint 11 doesn't touch the DB or the data pipeline. To
roll back:

1. Revert the three edits in `.scripts/dashboard/render_company.py`
   (and any small helper in `render_helpers.py`).
2. Delete the new `test_render_company.py` if added.
3. Re-run `python .scripts/build_dashboard.py` to regenerate HTML
   from the unchanged JSONs.

No DB backup / restore required because no DB writes were made. This
is one of the structural advantages of the data-pipeline-safety
constraint.

---

## Section 9 — Gate 1 decisions log (LOCKED 2026-05-26)

| # | Question | Decision |
|---|----------|----------|
| 1 | A.1 default state | **All types shown** by default. |
| 2 | A.1 persistence | **Per-visit only.** Filter resets to "all" every page load. No localStorage. |
| 3 | A.3 toggle vs permanent | **User-toggleable** chart-marker filter (similar widget to A.1, but on the chart). Default state: BUY+SELL only (matches A.3 intent); user can toggle to reveal other types. |
| 3-extra | Table filter and chart filter linking | **Independent.** Two separate widgets. Linked behaviour deferred to Sprint 12 if Rupert finds himself wanting it. |
| 4 | A.3 cluster-ring on filtered trigger | **Leave drawn.** Cluster rings remain regardless of marker filter state. |
| 5 | A.2 schema-change | **OFF the table for Sprint 11.** No `price_gbp` column, no migration. Display-only fix. Confirms data-pipeline safety constraint. |
| 6 | Phase 1 investigation | **Claude does the read** of three sample RNS filings + DB comparison. Rupert reviews findings, not source filings directly. |

**Gate 1 closed — build authorised. Begin Phase 1 (Section 4.1).**

**Implementation note from Q3 decision change:** Q3 became
user-toggleable rather than permanent, which means A.3's filter now
lives in inline JS (toggle visibility on marker datasets) rather
than as a Python-side filter on `txn_markers` before payload
serialisation. The Section 5.A.3 spec is therefore amended:

- Build the full `txn_markers` payload as today (no Python filter).
- Add the same chip-style filter widget above the chart (mirror of
  A.1's table widget structure).
- Inline JS toggles dataset visibility on the Chart.js instance.
- Default: BUY+SELL visible; clicking other-type chip reveals it.

---

## Section 10 — Estimated effort

| Phase | Claude time | Rupert time |
|-------|------------|-------------|
| 1. A.2 unit investigation | ~10 min | ~15 min |
| 2. A.2 implementation | ~20 min | ~5 min review |
| 3. A.3 chart-marker filter | ~30 min | ~5 min review |
| 4. A.1 type filter widget | ~60 min | ~10 min review |
| 5. Pipeline-safety regression | n/a | ~10 min |
| **Total** | **~2 hours** | **~45 min** |

Substantially smaller than Sprint 10 — single file, no DB impact, no
test-suite expansion beyond +1 file.

---

## Section 11 — Memory compliance

Cross-checked against `MEMORY.md`:

- `feedback_grep_all_callers_before_edit` — `_transactions_table`
  and `_price_chart` are private helpers in `render_company.py`,
  not callers of shared utilities. Low blast radius.
- `feedback_signal_id_three_layer_surface` — no signal IDs added.
  N/A.
- `feedback_no_rmw_json_in_hotpath` — no JSON writes in Sprint 11.
- `project_auto_backup_broken` — no Zone B writers added in Sprint
  11. The pipeline-regression run in Phase 5 uses existing
  refresh_all backups.
- `feedback_qa_agent_before_every_gate` — applies. QA agent
  reviews this plan BEFORE Rupert sees the gate-decision package
  for build. QA's primary check: Section 0 file allowlist
  compliance.
- `feedback_dashboard_table_sort` — A.1 filter must not change the
  existing transaction sort order. Filter operates on visibility,
  not on row reordering.

---

*End of Sprint 11 plan draft. Awaiting QA verdict, then Gate 1.*
