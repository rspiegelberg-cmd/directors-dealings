# Sprint plan — 2026-05-22 (Sprint 8)

> Same-day follow-on to [`sprint-plan-2026-05-22.md`](sprint-plan-2026-05-22.md)
> (Sprint 7). Sprint 7 shipped 5 items + Gate-1 folds + a Windows
> fsync hotfix; this Sprint 8 plan closes the friction surfaced during
> Sprint 7 execution and post-ship use.
>
> Theme: **make the dashboard feel like it works**. All four items are
> user-visible dashboard polish, no data-pipeline or schema changes.

**Companion docs:**
- [`sprint-plan-2026-05-22.md`](sprint-plan-2026-05-22.md) — Sprint 7 plan; full backlog index lives there and is still the source of truth.
- [`sprint-plan-2026-05-20.md`](sprint-plan-2026-05-20.md) — Sprint 6 plan; deeper history.
- Sprint 7 dropdown audit (2026-05-22) — inline in the chat log; not separately filed because findings are captured in B-056, B-057, B-058 below plus the Sprint-7-fix #2/#3/#4 changes already shipped.

**Estimate units:** Same as previous plans. Rupert-time = wall-clock attention; gates = mandatory sign-off pauses; risk = Low / Medium / High.

---

## Section 1 — Backlog updates since 2026-05-22 (Sprint 7)

### Shipped 2026-05-22 (Sprint 7 + post-Sprint-7 dashboard polish)

| ID | Title | Notes |
|----|-------|-------|
| B-052 | ResourceWarning `csv_path.open` without with-block | Mechanical wrap |
| B-053 | Company-page chart marker visibility | Radii 5/6/7/11 → 8/9/10/14, hover +3 |
| B-054 | 30d cohort cut option | Added, then **default reverted to 90d** same day (30d caused T+90/T+252 empty intersection) |
| B-023 | Section-aware extractor for bundled multi-PDMR filings | `_extract_via_sections` in parse_pdmr.py + 7-assertion unit test. Recovered ~3,000 rows from the corpus |
| B-015 | Sweep `announced_at` recovery from cached HTML | Discovered already-implemented in a prior session — just needed the Zone B sweep run |
| Sprint 7 Fix #2 | `horizonChange` JS event dispatch bug | Was dispatching on `window` with bare-string detail; cohort tiles ignored it. Fixed to `document.dispatchEvent` with `{horizon: ...}` shape |
| Sprint 7 Fix #3 | Context-aware empty-state copy on cohort tiles | "No firings at T21 × 30d — try a longer lookback or shorter horizon" |
| Sprint 7 Fix #4 | LOOKBACK_LABELS + HORIZON_LABELS consolidated into render_helpers.py | Single source of truth across 3 render modules + export_dashboard_json |

### New items added 2026-05-22 (post-Sprint-7)

| ID | Title | Severity | Source |
|----|-------|----------|--------|
| B-055 | Triage 120 reparse-corpus orphan candidates | LOW | Sprint 7 reparse |
| B-056 | Lookback dropdown does not work on sector page | LOW (UX) | Rupert visual check 2026-05-22 |
| B-057 | Drilldown horizon + lookback dropdowns are inert | MEDIUM | Sprint 7 dropdown audit |
| B-058 | Per-signal scoreboard 365d window hard-coded | LOW | Sprint 7 dropdown audit |

P3 backlog still parked: B-007, B-014, B-018, B-019, B-020, B-022, B-041, B-042, B-045 through B-051 (LLM hygiene + parser polish).

---

## Section 2 — Prioritized open items (delta only)

Sprint 7 closed every P0/P1 item from the [Sprint 7 plan's prioritized list](sprint-plan-2026-05-22.md#section-2--prioritized-open-items-delta). The current open items are:

**P1 — user-facing dashboard friction:**

| ID | Title | Why it matters | Effort | Risk |
|----|-------|----------------|--------|------|
| B-057 | Drilldown dropdowns inert | Every cohort drill page lies to the user (dropdowns appear functional, don't filter anything) | L (~2 hrs) | Medium (touches JS rendering pipeline) |
| B-055 | 120 reparse orphan candidates | DB has ~120 stale-wrong rows (e.g. director field = company name) co-existing with new correct rows | M (~45 min) | Low (`.bak` restorable) |
| B-058 | Scoreboard 365d window hard-coded | Hides Sprint 7's recovered older firings from the scoreboard view | S (~30 min) | Low |
| B-056 | Sector dropdown UX | Likely already resolved by Sprint 7 Fix #3 empty-state copy; needs eyeball confirmation | XS (~15 min) | Low |

**P3 carry-forward (unchanged):** see [Sprint 7 plan §2](sprint-plan-2026-05-22.md).

---

## Section 3 — Sprint 8

### Sprint 8 — Dashboard polish + Sprint 7 follow-ups (Rupert time: ~2.5–3 hrs, gates: 1, risk: Low-Medium)

**Goal:** Close every item filed during Sprint 7 execution: verify B-056 is resolved by Fix #3, rebuild B-057's inert drilldown dropdowns, triage B-055's 120 orphans, and add B-058's scoreboard window opt-in. Theme: make the dashboard's interactive surface trustworthy end-to-end.

**Scope (4 items)**

| ID | Title | Code size | Sequence |
|----|-------|-----------|----------|
| B-056 | Verify sector dropdown resolved | XS | 1st — quick eyeball |
| B-057 | Drilldown dropdowns via client-side JS rendering (Path B) | L | 2nd — biggest item |
| B-055 | Orphan triage + cleanup | M | 3rd |
| B-058 | Scoreboard window opt-in | S | 4th |

**Sequence within sprint**

1. **B-056 first.** Open `localhost:5000` → performance page → sector tile. Switch lookback to 90d, 1y, all. Confirm rows populate at non-30d windows; confirm 30d×T21 shows the new "No firings at T21 × 30d — try a longer lookback or shorter horizon" empty-state. If both check out, mark B-056 closed (no code change). If sector still feels broken vs bucket/role, drop into devtools and look for a JS error scoped to `data-tile="sector"`. ~15 min.

2. **B-057 — Path B (client-side rendering).** Confirmed at Gate 1.
   - Modify `build_dashboard.py:_build_drill_pages` to emit one HTML per cohort key (not 20). The HTML contains both dropdowns and a placeholder `<tbody>` that JS populates client-side.
   - Add JS to read URL query params `?horizon=t21&lookback=90d` on page load AND on dropdown change, then fetch from `performance_{cohort_type}.json` (already produced by `export_dashboard_json.py`).
   - JS reads the same `cohorts_v2[horizon][lookback]` path the parent cohort tiles use — so the data shape is already correct.
   - Update the dropdown change handlers to also push to `history.replaceState` so the URL stays in sync (deep-linking still works).
   - Unit test: assert that the emitted HTML contains a JS function that reads `horizon` and `lookback` from URL params, and that the placeholder `<tbody>` has the expected `data-*` attributes.
   - ~2 hrs. Touches `render_performance_drilldown.py` + `build_dashboard.py` + adds JS rendering function.

3. **B-055 orphan triage.**
   - Re-run `python .scripts/reparse_corpus.py --preview --force-safety-override` to get a fresh preview CSV (state may have changed since the 2026-05-22 run).
   - Bucket the 120 orphans into:
     - **Safe-delete:** director field = company name (B-017 wrong-data pattern). Sample of 10 confirmed this is the majority pattern.
     - **Needs review:** legitimate-looking director with low share count or unusual transaction. ~30% per Sprint 7 sample.
   - Add an Excel sheet or CSV pipe to surface the "needs review" subset for Rupert eyeball.
   - Run `python .scripts/reparse_corpus.py --confirm --delete-orphans --force-safety-override` once the safe-delete bucket is confirmed.
   - ~45 min.

4. **B-058 scoreboard window opt-in.**
   - Add a scoreboard-level lookback dropdown (`<select>` in `render_performance.py:_scoreboard`) with options: 1y (default — current behaviour), 2y, all.
   - Wire to a JS handler that re-renders the scoreboard against the current selection. Requires `export_dashboard_json.py` to emit horizon_aggregates at multiple lookbacks (currently only the 365d cutoff is computed).
   - Compromise option if multi-lookback aggregates are too much work: keep 1y as default but expose an "all-time" toggle. Quicker, smaller blast radius.
   - ~30 min for the compromise option; ~1 hr for full multi-lookback. Pick compromise at Gate 2 if needed.

**Gates**

- **Gate 1 — Path B confirmed (already approved 2026-05-22).** No re-gate needed.
- **Gate 2 (optional, only if B-058 expands)** — if implementing full multi-lookback scoreboard requires changing the horizon_aggregates payload shape, pause for Rupert to confirm scope before refactoring.

**Rupert-time breakdown**

- 15 min: B-056 eyeball.
- 20 min: B-057 design check before code starts + diff review when done.
- 30 min: B-055 review the "needs review" subset.
- 10 min: B-058 confirm scoreboard dropdown UX before code.
- 30 min: final smoke (rebuild + hard-refresh + click through cohort drilldowns + scoreboard).

Total: ~105 min on Rupert side. Within the ≤2 hr discipline.

**Definition of done**

- Sector dropdown either confirmed-working or rebuilt — `data-tile="sector"` no longer reports broken.
- Every drilldown page's horizon + lookback dropdown actually filters the rendered data. Deep-linking via URL params works.
- 120 reparse orphans: safe-delete bucket cleaned up; needs-review bucket triaged.
- Scoreboard has either a lookback dropdown (full B-058) or an "include all-time" toggle (compromise option).
- `unittest discover` green on Windows (target 273+).
- `audit_dates.py` green.
- One full `start.bat` Refresh smoke run.

**Risk discussion**

Low-Medium. **B-057 is the biggest blast radius** — the client-side rendering pipeline touches every drilldown page. Mitigation: keep the server-side fallback HTML (with current `t21 × 90d` server render) in place so a JS failure degrades gracefully to today's state. **B-055 orphan deletion is reversible** via the pre-run `.bak`. **B-058's compromise option (all-time toggle)** keeps the existing 365d default so existing scoreboard interpretations don't shift.

**Kickoff checklist**

- [ ] Sprint 7 confirmed green on Windows (273 tests OK — verified 2026-05-22 after empty-state-test fix).
- [ ] Live dashboard reachable at `localhost:5000`.
- [ ] Engineer has the audit findings from the 2026-05-22 chat log as reference.
- [ ] `.data/_reparse_corpus_preview.csv` is the post-Sprint-7 version (not pre-).

---

## Recommended next move

**Schedule Sprint 8 in a single ~2.5–3 hr session.** The four items form a coherent dashboard-polish package — completing them removes every known UX rough edge from Sprint 7's ship. After Sprint 8, the natural Sprint 9 candidate is the parked **LLM hygiene programme** (B-045/B-046/B-047/B-048) — but only if LLM usage scales up enough to bite. Otherwise Sprint 9 can be an exploratory round on the existing P3 backlog (B-019 CAR chart toggle, B-041 CEO/Founder split if performance data has matured enough to show divergence).
