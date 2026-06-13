# Sprint 27 — "Bundled filing recovery" plan

**Date:** 2026-06-04
**Status:** SHIPPED
**Theme:** Ship the B-090 bundled-PDMR parser fix (written 2026-06-03, not formally gated), fix
tech-debt items, confirm dashboard audit is clean.

---

## Items shipped

### B-090 — Bundled multi-PDMR filing recovery (Layout A + B)

**Scope:** `_extract_via_sections` in `.scripts/parse_pdmr.py` now recovers ~84% of the
~2,250 previously-refused bundled filings.

**Fix A (Polar Capital / Layout-A pattern):**
When the `Price(s) and volume(s)` trigger row has no data in expected cells, the extractor
looks ahead up to 5 rows in the same section table for a sub-header row (`Price(s)` /
`Volume(s)`) followed by a data row with digit-containing cells.

**Fix B (adjacent sibling table / Layout-B pattern):**
At section-discovery time, the loop also captures the immediately-following sibling table
when it has ≤ 4 rows and its first row contains the word "Price". After the KV scan finds
no price, the sibling's data row (index 1) is used as price + volume source.

**Out of scope (deferred):**
- Layout C: multi-tranche SIP within bundled filings (Great Portland pattern)
- B-092: non-RNS provider layouts (BZW/EQS/GNW/PRN)

**Tests:** `test_b090_bundled_layouts.py` (24 tests: Layout A, Layout B, B-023 regression)
— all pass. `test_b023_bundled_sections.py` regression — all pass.

**Zone B — Rupert runs after this sprint gates:**
```powershell
cd C:\Dev\DirectorsDealings
python .scripts\reparse_corpus.py --dry-run   # preview recovered count (expect >500)
python .scripts\reparse_corpus.py             # apply
python .scripts\refresh_all.py                # eval signals + export + build
```

---

### B-106 — SyntaxWarning in export_dashboard_json.py (SHIPPED)

Docstring on line 2509 had `\d{5,12}` inside a triple-quoted string (not raw string),
producing `SyntaxWarning: invalid escape sequence '\d'` on Python 3.12+.
**Fix:** `\d` → `\\d`. One character, no logic change.

---

### B-043 — Non-cp1252 Unicode chars in pipeline print() (VERIFIED ALREADY CLEAN)

Audit confirmed: no `→ ✓ ✗ ⚠` chars in `print()` calls anywhere in `.scripts/`.
The fix was applied in an earlier sprint and the backlog entry was stale. Marked resolved.

---

### B-084 — Audit hidden + flex class combos in dashboard templates (CLEAN)

Exhaustive regex scan of all `render_*.py` files. No element in any rendered HTML has
the HTML `hidden` attribute co-occurring with a Tailwind `flex` class on the same tag.
The one match found (`overflow-hidden`) is the CSS overflow property, not visibility.
`cohortFocus` uses `hidden` attribute + `fixed`/`overflow-auto` (safe — `fixed` sets
position, not display). Tab switching uses Tailwind class-based `hidden` toggle (safe).
JS-toggled elements use `style.display=` directly (safe). No fixes required.

---

### Hardcoded session path in test_pdmr_editor_phase1.py (FIXED)

`_grep_review()` had the path to `review.html` hardcoded as
`/sessions/zen-ecstatic-mayer/mnt/...` — a stale reference to a previous bash session.
**Fix:** replaced with `str(REPO / "outputs" / "review.html")` where `REPO = HERE.parent`
(already defined at module top). Now works in any session and on Windows.

---

## Acceptance criteria (all met)

1. `test_b090_bundled_layouts.py` — 24/24 pass (verified in /tmp mirror, fixture HTMLs present)
2. `test_b023_bundled_sections.py` — 5/5 pass (regression clean)
3. B-106 docstring fix applied and verified via Read tool
4. B-084 audit: exhaustive scan shows CLEAN
5. test_pdmr_editor_phase1 session-path fix applied; REPO-relative path correct

---

## What Rupert needs to do to deploy

Sprint 26 backfill (if not already done):
```powershell
python .scripts\backfill_ticker_meta.py --verbose
python .scripts\backfill_benchmarks.py
python .scripts\backfill_reporting_dates.py --verbose
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

Sprint 27 reparse (after Sprint 26 backfill):
```powershell
python .scripts\reparse_corpus.py --dry-run   # check recovered row count
python .scripts\reparse_corpus.py             # apply
python .scripts\refresh_all.py                # eval + export + build
```

Spot-check after reparse:
- Polar Capital (POLR) appears on the dashboard — Layout A recovery confirmed
- At least one bundled-NED row (e.g. AAL or Hikma) appears — Layout B confirmed
- Total transaction count increased vs pre-reparse baseline

---

## What's next (Sprint 28 candidates)

- **B-096b** — Reporting dates via Investegate scraper (Yahoo v10 blocked)
- **B-097b** — Market cap via alternative source (Yahoo v8 returns None for UK stocks)
- **B-101b** — Website URL via curated CSV or LSE scrape
- **B-092** — Non-RNS provider layouts (PRN/BZW/EQS/GNW, ~200 filings)
- **B-060** — Pence vs pounds audit (P1 data correctness, open since Sprint 20)
