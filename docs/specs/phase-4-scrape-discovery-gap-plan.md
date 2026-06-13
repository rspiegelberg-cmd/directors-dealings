# Phase 4 — Scrape Discovery Gap Fix Plan

**Date:** 2026-06-02
**Status:** SHIPPED 2026-06-02. Option A (trust-category + tiered denylist) plus the date-window fix, widened filing-link regex (BZW/EQS/GNW PIPs), URL-slug ticker resolution and real pagination all landed; tests in `.scripts/test_phase_4_discovery.py`, dry-run preview tool `.scripts/discover_preview.py`. Residual = parser-layout work for newly-discovered non-RNS providers (tracked as B-074 / Sprint 21). See memory `project-ingest-gate-incident-2026-06-02`.
**Original status (superseded):** PLAN — not implemented. Diagnose-and-design only. Awaiting Rupert's gate decision.
**Origin:** Problem D from `docs/specs/ingest-gate-incident-fix-plan-2026-06-02.md` (§4 Phase 4, §6 Phase-4 diagnosis), and backlog item **B-073** (historical never-scraped backfill).
**Trigger:** On 1–2 Jun the live Investegate directors-dealings feed carried ~68 PDMR announcements; ~11 tickers were **never fetched** by the daily scraper — a true *discovery* gap, distinct from the parse/gate issues fixed in the ingest-gate sprint.
**Investigation:** Read-only source review of the live scraper code (`scrape_investegate.py`) and its caller (`run_scrape.py`). No write-path scripts run; no writes to `.data/` or cache dirs.
**Inputs:** `scrape_investegate.py`, `run_scrape.py`, `ingest-gate-incident-fix-plan-2026-06-02.md`, `CLAUDE.md` (FUSE two-zone rule, QA-before-gate, diff-first rule, truncation discipline).

---

## 1. Plain-English summary (for Rupert)

The daily scraper has been **quietly throwing away real director dealings before it ever downloads them.** Each morning it loads one page listing the day's filings, then decides which rows to fetch by checking whether each headline contains one of about a dozen fixed phrases (e.g. "PDMR Dealing", "Director/PDMR"). Any filing worded differently — "Director Dealing", "Exercise of Share Options", "Conditional LTIP Awards", "Share Incentive Plan Purchase", bare "PDMR transaction notification" — fails that text-match and is **dropped before download.** Because it was never downloaded, it never reached the parser, never reached the holding file (`_pending_review.json`), and so the recent ingest-gate "drain" **cannot recover it.** These filings simply do not exist anywhere in our system.

On 1–2 Jun this dropped **~11 of ~68** feed announcements (AT., BRES, FDM, MAB1, MICC, NFG, ONDO, QUBE, TTE, VTU, and Metlen/MTLN). That is roughly **1 in 6 of the day's director dealings, lost silently, every day.**

The irony: Investegate's directors-dealings **category page is already a pre-filtered list of PDMR filings.** The page only contains director-dealing announcements. Our scraper then *re-filters* that already-filtered list by matching headline text — a redundant step that adds nothing but throws away anything phrased unusually. **The right fix is to trust the source's own classification** (the category page) and stop second-guessing it by headline keyword, keeping only a tiny "obviously-not-a-dealing" denylist as a safety net.

A second, latent problem: the scraper loads only one page of up to 300 entries and ignores the date window it is handed. On an unusually busy day the day's filings could be pushed past entry 300 and lost. Not the cause of the 1–2 Jun gap (68 << 300), but worth closing while we are in here.

**Important caveat for honesty:** widening discovery means **more filings will be ingested and more signals may fire.** That changes the numbers on the dashboard, so — per the project rule — this must ship **diff-first**: we produce a report of exactly what newly appears, you review it, *then* we wire it live.

This plan also requires a follow-on **scoped historical re-scrape** (B-073) to backfill everything the keyword filter silently dropped over the project's life — the drain does not reach these because they were never downloaded.

---

## 2. Confirmed root cause (with file:line)

The gap is entirely in the **daily** index walk `scrape_investegate.iter_index` — the path `run_scrape.py` uses. (The multi-year backfill path `iter_archive` is a different function and is *not* the cause of the daily gap, though it shares the same lossy headline re-filter — see §2.3.)

### 2.1 PRIMARY — redundant, lossy headline allow-list (`_row_is_pdmr`)

`scrape_investegate.py:275-294` — `_row_is_pdmr(headline)` keeps a row only if the index headline contains one of **11 fixed substrings**:

```python
def _row_is_pdmr(headline: str) -> bool:
    """Cheap pre-filter on the index headline."""
    if not headline:
        return False
    if any(rx.search(headline) for rx in _OFFSCOPE_HEADLINES):
        return False
    h = headline.lower()
    return any(kw in h for kw in (
        "director/pdmr",
        "director / pdmr",
        "pdmr dealing",
        "pdmr shareholding",
        "notification of transactions of directors",
        "person closely associated",
        "grant of conditional share",
        "long term incentive plan - grant",
        "grant of share options",
        "vesting of",
        "tvr",
    ))
```

This is called inside `iter_index` at **`scrape_investegate.py:325`**:

```python
if not _row_is_pdmr(headline):
    continue
```

Any PDMR filing whose headline does not contain one of those exact substrings is **`continue`-skipped before `fetch_filing` is ever called** — i.e. never downloaded. That is the precise mechanism of the "never even fetched" symptom.

The `_OFFSCOPE_HEADLINES` denylist (`scrape_investegate.py:269-272`) only contains two EBT-share-purchase patterns, so it is not the problem; the problem is the **allow-list** being a closed set of 11 phrases applied to an open-ended universe of real PDMR headline wordings.

### 2.2 The source is already pre-filtered — re-filtering is the bug

`iter_index` fetches the **directors-dealings category index** (`scrape_investegate.py:50, 316`):

```python
INDEX_URL = f"{BASE_URL}/category/directors-dealings"
...
page_url = f"{INDEX_URL}?show=300"
```

Every row on `/category/directors-dealings` is, by Investegate's own categorisation, a director-dealing announcement. The headline-substring re-filter is therefore **redundant** against a source that has already done the classification — and it is **lossy** because the allow-list cannot anticipate every wording. This is the core design error: we are re-deriving a classification the source already gives us, and getting it wrong.

### 2.3 LATENT — single `?show=300` page, date window ignored, `max_pages` dead

`iter_index` signature (`scrape_investegate.py:307`):

```python
def iter_index(start_date: str, end_date: str, max_pages: int = 5) -> Iterator[dict]:
```

- **`start_date` / `end_date` are never read** anywhere in the function body (`scrape_investegate.py:307-344`). The caller computes a real window — `window_start, window_end = _resolve_window(args)` (`run_scrape.py:239`) — and passes it in at `run_scrape.py:316`, but `iter_index` ignores both. No date filtering happens; the function returns whatever is on the single page regardless of window.
- **`max_pages=5` is declared but never used** — there is no pagination loop. The body fetches exactly one URL (`?show=300`) at `scrape_investegate.py:316-317` and parses it once.
- Consequence: on a day with >300 director-dealing entries (across all categories the `?show=300` view may include, or a genuinely huge filing day), the oldest entries fall off the single page and are lost. **This is not what caused the 1–2 Jun gap** (only ~68 announcements that day, well under 300) — confirmed latent, not active. But it is a real silent-loss risk under load and a correctness gap (args that lie about what the function does).

### 2.4 Why the drain cannot recover these (interaction with the ingest-gate fix)

The ingest-gate plan's Phase 2 drain replays `_pending_review.json`. Filings dropped by `_row_is_pdmr` were **never fetched**, so they never entered the parser and never landed in `_pending_review.json`. **The drain provably cannot reach them.** Recovery requires re-discovery (a re-scrape), which is why B-073 exists as a separate follow-on. This plan fixes the *forward* gap; B-073 backfills the *historical* gap.

**Correction to prior diagnosis:** the prior plan's two-point diagnosis (incomplete allow-list = primary; `?show=300` single-page + ignored date window = latent) is **confirmed correct.** The only refinement this pass adds is the framing in §2.2: the fix is not "widen the allow-list" so much as "stop re-classifying a source that is already classified" — trust the category page, replace the allow-list with a small denylist.

---

## 3. What is being dropped (characterisation from the live feed)

The 11 missed tickers on 1–2 Jun carried headlines that are unmistakably PDMR dealings but do not match any of the 11 allow-list substrings:

| Ticker | Example headline (live feed) | Why the allow-list missed it |
|--------|------------------------------|------------------------------|
| AT. | "Conditional LTIP Awards" | "conditional **share**" is in the list, but not "conditional LTIP awards" |
| BRES | "Exercise of Share Options" | list has "grant of share options", not "exercise of" |
| FDM | "Director Shareholding & Block Listing Application" | "director shareholding" ≠ "pdmr shareholding"; compound headline |
| MTLN (Metlen) | "PDMR transaction notification" | bare "PDMR" + "transaction notification" — no listed substring matches |
| ONDO | "Share Incentive Plan Purchase" | SIP purchases not represented in the list at all |
| QUBE | "Director Dealing" | "director dealing" ≠ "pdmr dealing" (the list requires the literal "pdmr") |
| MAB1, MICC, NFG, TTE, VTU | (various: "Director/PCA Dealing", "Directorate Dealing", option/award variants) | wording drift from the 11 fixed phrases |

**Pattern:** the misses cluster around (a) the word "Director" used *instead of* "PDMR" ("Director Dealing", "Director Shareholding"), (b) award/option **lifecycle** events other than the two listed grant phrasings (LTIP awards, option *exercise*, SIP purchases), and (c) compound or company-styled headlines. None of these are edge cases — they are routine, high-frequency PDMR wordings. A closed 11-phrase allow-list will always lag the open set of real headlines.

This confirms the design conclusion: **re-filtering an already-PDMR-filtered category page by headline substring is the redundant, lossy step.** The fix is to trust the category/source classification.

---

## 4. Candidate fixes (ranked)

All three options widen discovery and therefore shift ingest/firing counts → **all are diff-first per the project rule.** A, B, C are independent and can ship together or separately.

### Option A — Trust the category page; replace the allow-list with a denylist *(RECOMMENDED)*

Stop calling `_row_is_pdmr` as an allow-list inside `iter_index`. Because `/category/directors-dealings` is already a PDMR-only feed, **keep every row** the link-regex finds on that page, except those matching a small, explicit **denylist** of clearly-non-dealing announcement types (the existing `_OFFSCOPE_HEADLINES` EBT patterns, plus any narrow additions the calibration in §5 proves necessary — e.g. pure "Total Voting Rights" housekeeping if it is found to ride this category and is genuinely out of scope).

- **Pros:**
  - Closes the primary gap completely — no headline wording can be silently dropped.
  - Matches the source's own classification instead of re-deriving it (removes the whole class of bug, not just today's instances).
  - Denylist is *additive and reviewable*: we only ever add a pattern after seeing it pull in genuine non-PDMR noise, so over-capture is bounded and visible.
  - Smallest conceptual surface: one filter inverted.
- **Cons / risk:**
  - **Over-capture risk:** if the category page contains anything that is *not* a tradeable PDMR dealing (e.g. TVR/Total-Voting-Rights notices, block-listing-only filings, holding-company admin), we now fetch and attempt to parse it. Mitigation: the downstream ingest gate (just rebuilt in the ingest-gate sprint) already routes unparseable/incomplete rows to pending rather than the dashboard, so noise lands in pending, not in front of Rupert. The §5 calibration quantifies the noise before go-live.
  - Slightly more fetches per day (the dropped ~11 + any new noise) — negligible against the polite-sleep budget (~0.8s × a handful of extra rows).
  - Shifts firing counts → diff-first mandatory.

### Option B — Add real pagination to `iter_index`

Make `iter_index` actually use `max_pages` (or a date-bounded loop): walk page 1, 2, … of the category index until the window is fully covered or a page returns zero in-window rows, instead of a single `?show=300` hit.

- **Pros:** closes the latent >300-entry overflow; makes the function's args honest.
- **Cons / risk:** does **not** fix the 1–2 Jun gap (that was the allow-list, not overflow). More requests per run. Only worth doing alongside A. Lower urgency.

### Option C — Apply the date window inside `iter_index`

Actually read `start_date`/`end_date` and filter rows to the window (and use the window to decide when to stop paginating if B is also done).

- **Pros:** makes the daily run deterministic w.r.t. its window; prevents re-processing stale rows; required for a clean **scoped historical re-scrape (B-073)** to be date-bounded rather than "whatever is on page 1 today."
- **Cons / risk:** the index row timestamp (`_INDEX_TIME_RE`, `scrape_investegate.py:262-265`) is best-effort and sometimes absent; date filtering must **fail-open** (keep rows with no parseable date) to avoid re-introducing silent drops. Must be designed carefully so it does not become a *new* lossy filter.

### Recommended approach

**Ship A as the core fix, bundle C, and include B if the calibration shows any day approaching the 300-entry ceiling.**

- **A** is the actual fix for the observed gap and removes the bug class.
- **C** is a prerequisite for the B-073 historical re-scrape to be cleanly date-windowed, and is low cost if it fails-open on missing dates. Without C the historical backfill cannot be bounded.
- **B** is only strictly needed if days can exceed the single-page cap; the §5 calibration will tell us. Include it if in doubt — it is cheap insurance and makes `max_pages` honest.

Net: invert the filter (allow-list → trust-source + denylist), make the date window real and fail-open, and page the index. All behind a diff-first gate.

---

## 5. Test / validation plan (read-only, DRY first)

Goal: prove the fix discovers **all ~68** 1–2 Jun feed announcements (including the 11 misses) **without pulling in non-PDMR noise** — before anything writes to the DB.

1. **Capture ground truth (read-only).** Re-pull the live `/category/directors-dealings?show=300` page for 1–2 Jun (or use any already-cached copy under `.scripts/_scrape_cache/` if present — read-only) and hand-label the full set of director-dealing announcements for those two days. Expect ~68. This is the target set.
2. **DRY-run discovery harness (Claude-safe, no DB writes).** Write a standalone diagnostic that imports `scrape_investegate.iter_index`, runs it with the proposed Option-A filter logic over the 1–2 Jun window **in dry/no-fetch mode** (parse the index page only; do **not** call `fetch_filing`, do **not** touch `.data/` or caches), and emits the list of `(rns_id, ticker_hint, headline)` it *would* fetch. This is read-only and safe in Claude's sandbox.
3. **Coverage assertion.** Confirm the dry-run set ⊇ the 68 ground-truth announcements, and specifically contains all 11 previously-missed tickers (AT., BRES, FDM, MAB1, MICC, NFG, ONDO, QUBE, TTE, VTU, MTLN).
4. **Noise assertion.** Confirm the dry-run set does **not** materially exceed 68 — i.e. the denylist + downstream gate keep non-PDMR rows out. Any row in the dry-run set that is *not* in ground truth is inspected by hand; if it is genuine non-PDMR noise, add a narrow denylist pattern and re-run. Iterate until the set is {all 68 PDMR} with no junk.
5. **Latent-overflow check (informs Option B).** Count director-dealing rows present on the busiest single day in the calibration window. If any day approaches ~300, Option B (pagination) is required, not optional.
6. **Regression: existing test suites.** Run `python -m unittest discover -s .scripts -p "test_*.py"` (Claude-safe; no DB writes) and the Stage-2 scraper suite (`test_stage_02.py`) to confirm no existing scraper behaviour regressed. Add new unit tests for the inverted filter using a fixture index page containing the awkward headlines from §3.
7. **Live wiring only after diff-first sign-off.** Once the dry-run set is clean, the actual forward fix goes live via `run_scrape.py` (**write-path — Rupert runs it from PowerShell**); the first live run is itself reviewed as the diff deliverable before the rebuild sequence.

All steps 1–6 are read-only / Claude-safe. Step 7 is Zone B (Rupert runs).

---

## 6. Interaction with shipped work and other phases

- **Ingest-gate fix (Problems A/B, just shipped).** Complementary, not overlapping. The gate decides *what to keep once parsed*; this fix decides *what to download in the first place.* Together they close the funnel end-to-end. Newly-discovered filings flow through the **rebuilt** gate, so any that are unparseable/incomplete land in `_pending_review.json` (not the dashboard) — which is exactly why Option A's over-capture risk is contained.
- **The drain (Phase 2) does not reach these.** Re-stated for emphasis (§2.4): keyword-dropped filings were never fetched, so they are not in pending. Forward fix + B-073 historical re-scrape are the only routes to recovery.
- **Bundled multi-PDMR refusal (B-072).** Some newly-discovered filings will be bundled multi-director announcements that the parser **refuses to fan out by design.** That is correct behaviour — they will route to pending and be picked up (if ever) by the separate B-072 multi-PDMR sprint, **not** by this discovery fix. This fix should *discover* them (they are real PDMR filings); it should **not** attempt to change how they are parsed. Expect the post-fix pending count to rise as more bundled filings are discovered — that is expected, not a regression.
- **B-073 historical re-scrape.** This plan's Option C (real date window) is the prerequisite that lets B-073 run a clean, date-bounded re-scrape from launch → today to backfill every filing the old allow-list silently dropped. B-073 is the *historical* counterpart to this *forward* fix and is its own write-path job (Rupert runs it).
- **Type-inversion fix (Problem C).** Independent; no interaction.

---

## 7. Risks & guardrails

- **FUSE / two-zone rule.** All code changes are Zone A (`scrape_investegate.py`, tests). Claude edits with Edit/Write + mandatory Read-tool truncation check. Every write-path run (`run_scrape.py`, any re-scrape, `eval_signals` → `backtest` → `export_dashboard_json` → `build_dashboard`) is **run by Rupert from PowerShell**; Claude pastes exact commands and waits.
- **Diff-first (non-negotiable).** Discovery widening shifts ingest and firing counts. The §5 dry-run coverage/noise report **is the diff deliverable** and is reviewed before the live wiring in step 7.
- **Over-capture over-correction.** If the denylist is too narrow, non-PDMR rows get fetched. Mitigation: the downstream gate quarantines them in pending; the calibration in §5 quantifies the noise before go-live; the denylist is additive and reviewable.
- **Date-filter must fail-open (Option C).** Index timestamps are best-effort; rows with no parseable date must be **kept**, never dropped, or we re-introduce the very class of silent loss we are fixing.
- **Politeness budget.** A few extra fetches/day (the recovered ~11 + minor noise) is negligible against the 0.6–1.0s jittered sleep; pagination (Option B) adds at most a handful of page GETs/day. No robots concern (`check_robots` unchanged).
- **No new lossy filter.** The whole point is to *remove* a lossy filter; any guardrail added (denylist entry, date filter) must be demonstrably non-lossy against the §5 ground-truth set before shipping.

---

## 8. Out of scope

- **The historical backfill itself (B-073).** This plan fixes the forward daily gap and provides the date-window prerequisite; the scoped launch→today re-scrape to recover historically-dropped filings is its own job, run by Rupert, after this lands.
- **Bundled multi-PDMR fan-out (B-072).** Discovering them is in scope; changing how they are parsed/attributed is not.
- **The `iter_archive` backfill walker.** It shares the same lossy `_row_is_pdmr` re-filter (`scrape_investegate.py:462`) on top of its URL-hint filter, so it has the same latent gap — but it is not the daily path and not the cause of the 1–2 Jun incident. Worth fixing for B-073 consistency, but flagged here rather than scoped: if B-073 uses `iter_archive`, apply the same trust-source/denylist inversion there. Decision deferred to the B-073 sprint.
- **The secondary `announced_at`-blank-on-LLM-path defect** (noted in the ingest-gate plan §2). Unrelated.
- **Any change to signal thresholds or the parser's type/field logic.** Discovery only.

---

## 9. Open decisions for Rupert

1. **Bundle or stage?** Ship Option A alone first (closes the observed gap fastest), or A+B+C together (closes latent overflow and unlocks B-073 in one go)?
2. **Denylist starting set.** Begin with just the existing two EBT patterns and add only what the §5 calibration proves noisy (recommended), or pre-seed additional patterns (e.g. pure TVR notices) now?
3. **B-073 timing.** Schedule the historical re-scrape as the immediate fast-follow (so the dashboard backfills the historically-dropped filings), or land the forward fix first and let history accumulate? (Recommendation: forward fix + diff sign-off first, then B-073 as the next write-path job.)
4. **`iter_archive` parity.** Fix the same lossy re-filter in `iter_archive` now (needed for a clean B-073), or defer to the B-073 sprint?
