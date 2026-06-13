# QA review — performance-page-redesign-v1

**Reviewer:** independent agent
**Date:** 2026-05-18
**Verdict:** APPROVE WITH FIXES — the redesign is sound, but several concrete drifts between spec and mockups, and one feasibility ambiguity, need resolving before code starts.

## Critical (would break or seriously hurt v1 if shipped)

1. **Cohort-tile lookback dropdown options differ from the spec.** Spec §1.1 / §1.5 / §5.1 lock the lookbacks to `[90d, 6m, 1y, all]` and pre-compute all four in the JSON. All three tiles in `performance-preview.html` instead expose `[90 d, 365 d, 1 y, all]` (the default selected is "365 d", not 90d). `365d` is not in the JSON spec, and "365 d" + "1 y" are effectively duplicates. Pick one set — currently the renderer would have no payload to fill the mockup's default selection.

2. **Drill-down lookback dropdowns repeat the same drift.** Bucket and role mockups default the lookback to `365 d`; spec §1.5 says default 90d. Sector mockup correctly uses `[90 d, 6 m, 1 y, all]` with 90d default. Three drill-down pages must not have three different lookback vocabularies.

3. **Role mockup omits the Tier column from the firings panels.** Spec §2.3 column list is six cells (Date / Ticker / Director / Tier badge / Value / CAR). `performance-role-preview.html` renders five — no Tier badge. Either the spec is wrong (you don't need a tier badge on a single-role page) or the mockup is wrong. Bucket and sector mockups include the Tier column. Decide and unify.

4. **`role_class` reliability is unverified and the spec's mapping has a flaw.** `_backtest_results.csv` writes `role_class` only when the signal's metadata carries it (`backtest.py` line 320–327 — falls back to empty string). For T3/T4/S1/F1 firings the `role_class` cell may be empty. The §5.4 regex fallback to `transactions.role` therefore matters — and the regex itself has a bug: the `other_exec` rule `\b(Chair|Group|Executive|COO|CTO)\b` matches a "Chief Executive" string and would mis-bucket a CEO as `other_exec` if the first rule didn't already catch it. With case-insensitive `(?i)` "Executive" inside "Chief Executive" matches. The order-dependent precedence (CEO/CFO checked first) saves it in practice but the spec should make the precedence explicit, not implicit on author discipline. **Add the §8 "unit-test the regex on the corpus" step to the Critical implementation gate, not just a back-end task** — without it Sprint 5 ships with mis-classified rows.

5. **Status pill thresholds disagree across the three mockups in ways §3 doesn't sanction.** Spec §3.2 (role) says "Below benchmark when hit% < base × 0.85, Above when > base × 1.05, At otherwise". Role mockup shows the **"At-benchmark performance"** pill with NED hit% 51.9% — but no base rate is given on the page so the reader can't verify. Spec §2.2 says the pill is "shown only when the cohort sits outside ±50% of the FTSE A-S base rate, otherwise hidden". 51.9% vs ~50% base is well inside ±50%, so by §2.2 the pill should be **hidden**, not shown as "At-benchmark". Spec §2.2 (±50% rule) and §3.2 (×0.85 / ×1.05 rule) are inconsistent. Pick one rule and apply it uniformly.

## Important (degrades UX or utility — fix before v1.1)

6. **Bucket tile sub-line "T1 + T2 buys only" is in the mockup but the spec only carries it on the drill-down (§3.1).** Spec §1.1 wireframe shows no sub-line under "By transaction value". The mockup adds one. Decide whether the tile body needs the sub-line (recommended — current page is silently filtered; clarity is the whole point of the redesign).

7. **`N=91 over 365 d` caption is wrong by spec.** §1.1 says the caption is "365d basis (or whatever lookback is selected)" — but "365d" is not a valid lookback per §1.5. Plus the mockup contradicts the description "↘ each row clickable" with the actual text "↘ rows clickable" (minor wording drift, but it appears the spec was authored without a final pass against the mockup).

8. **Breadcrumb left-arrow / "Today" inconsistency.** Spec §2.6 says cohort breadcrumb: `Today › Performance › {leaf}` with "Today" linking to `index.html`. All three drill-down mockups link "Today" to `performance-preview.html` instead. The cohort page `performance-preview.html` itself has `← Today` as a back-link, not a breadcrumb. That's two different navigation patterns on adjacent pages.

9. **Bucket / role drill-downs do not show the "fewer than N" edge case the spec hints at.** §2.3 covers the sector page (Materials has only 2 negative firings — beautifully done in the mockup). Bucket and role pages would hit the same problem (a role page for "Other exec" in a thin sub-period could have <10 negatives). The spec implies "sector only" treatment; in practice all three pages need the same edge-case handling. Confirm or expand §2.3.

10. **`benchmark_car_pct` in the JSON spec §5.2 is a single scalar, but the firing row schema §5.3 also carries `bench_car`.** Are these the same number (sector benchmark CAR for the period) or different (per-firing benchmark)? Spec is ambiguous. Mockups all show "FTSE A-S benchmark: +1.1%" once in the page header — implying the cohort-aggregate scalar — but `bench_car` per-firing is unused in any mockup. Either drop the per-firing field or specify what the UI does with it.

11. **`outlier_flag` on the firing-row schema (§5.3) and the bucket row (§5.1) is not rendered anywhere in the mockups.** Spec §1.2 says N-band glyphs are amber `⚠` — the outlier glyph is undefined. Either tie outlier flag to a different glyph (the per-signal scoreboard uses `⚠` for both N<20 and outlier-dominated, conflating two states) or drop the field from v1's JSON contract.

12. **Per-tile `total_n` caption is the global N across all buckets / roles / sectors, but the sector tile shows only 5 of 73 — what does "total_n" mean for the sector case?** The mockup says "5 of 73 sectors" while the spec footer caption template says "N=91 / 365d basis". Inconsistent — the sector tile's caption is a count of sectors, the bucket tile's is a count of firings. Clarify both.

13. **Keyboard accessibility unaddressed.** `<tr class="clickable">` rows with JS `click` listeners are not keyboard-focusable and do not announce as buttons. No `tabindex`, no `role="button"`, no Enter-key handler. Rupert reviews on desktop with a mouse so this won't bite v1, but flag for v1.1 — and a single `role`/`tabindex` pattern is cheap to add now.

## Nice-to-have (defer-able improvements)

14. Bucket mockup hardcodes `onclick="window.location.href='../../../outputs/companies/AAL.html'"` on only the first few rows; remaining rows rely on the auto-wire script. Cosmetic inconsistency in the mockup, but the production renderer needs one wiring approach (data-href everywhere, per §1.5 / §2.5).

15. The cohort tile's chevron `›` markup uses `<span class="chev">` in the mockup but `<span class="absolute right-2 …">` in the spec §1.1's HTML snippet. Same visual, different class plumbing — pick one and document.

16. The mockup banner ("MOCKUP — visual design preview") is hardcoded into every preview file; trivially removed at render time but worth flagging the spec doesn't say it goes away.

17. The bucket-page rollup includes a row "— 17 more tickers below (click 'N' to sort) —". The spec §2.4 implies a fully-rendered table with a divider for N<3. Make explicit whether the page shows all tickers or truncates and asks user to sort.

18. URL parameter naming inconsistency: spec §3.2 says `?role={ceo_cfo|other_exec|ned}` (snake_case), but the wireframe in §0 says the leaf displays "Director role: NED" — fine, but document the canonical-key → display-label mapping (where does "NED" come from in the URL `?role=ned`?). Currently implicit.

## Sanity check — does the spec hang together?

Mostly, yes. The shared-template framing in §2 holds — the three drill-downs are genuinely the same skeleton with three filters, one new field (sector benchmark), and one degenerate state (fewer than 10 losers). The data-shape decision to expand the JSON to `cohort × horizon × lookback` is sound (~100 KB, well within budget) and the chosen "three aggregated files" trade-off in §5.5 is correctly reasoned. The feasibility win is that `tickers_meta.benchmark_symbol` IS populated in `backfill_benchmarks.py` / `fetch_sectors.py` — so the sector-specific benchmark column in §3.3 is real, not aspirational. The main risk: the spec's §3 thresholds, the §2.2 ±50% rule, and the mockup pill texts use three different rules. Pick one before code.

## Counter-arguments to the model-assessment recommendation

The proposed v1.1 kill rule ("N≥20 AND median net CAR < −2% at T+21") is defensible but **mis-calibrated for a tiny corpus**. At T+21 with N=27 (T1) one extreme outlier moves the median by ~3.7pp. The rule will mis-kill a working signal whose typical trade slightly underperforms but whose 20% best trades carry the alpha — exactly the F1-style "fat-right-tail" signature you want to keep. A second guardrail (e.g., "AND p75 net CAR < 0") would avoid mis-killing fat-tail-positive distributions. Also: redefining the kill rule away from T+90 to T+21 abandons the original 90-day horizon for which the cost model (50bps spread + 0.5% stamp) was sized — re-running the cost model at T+21 is implied work that the spec doesn't flag. Finally, killing on a 12-month single-regime backtest is structurally premature regardless of horizon; suspend the deprecate-toast entirely until 2x non-overlapping regime windows are available, rather than swapping horizons.

---

**File written:** `C:\Dev\DirectorsDealings\docs\specs\performance-page-redesign-v1-qa-review.md`
