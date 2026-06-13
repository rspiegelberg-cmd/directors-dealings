# Sprint 22 — B-090 Plan: Bundled multi-director filing recovery

**Date:** 2026-06-03  
**Status:** APPROVED — building now  
**Scope:** Fix `_extract_via_sections` in `parse_pdmr.py` to recover ~84% of refused bundled filings

---

## Audit findings (2026-06-03)

Sampled 300 of 7,276 cached HTML files. 93 are truly refused bundled filings (0 rows + bundled warning). Extrapolates to ~2,250 total in the corpus.

### Layout breakdown

| Layout | Share | Description | Fix |
|--------|-------|-------------|-----|
| **A — Inline multi-row** | 46% | Price/volume split across rows 15/16 inside the KV table. Trigger row (row 14) is empty. Section extractor hits it, finds no data, emits `could_not_separate_price_volume`. | Look-ahead in same table rows |
| **B — Adjacent sibling table** | 38% | Price/volume in a separate 2-row table immediately after each director's KV table. Section extractor never looks there. | Capture next sibling table at build time |
| **C — Multi-tranche SIP** | 6% | Great Portland-style: multiple price/volume rows per director (partnership + matching shares). | Deferred |
| **Other** | 8% | Undetectable pattern; likely non-standard layouts. | Deferred |

Fix A + B targets **84% of the refused pile** with surgical changes inside one function.

### Example filings

- Layout A: `8857061` (Polar Capital, 7 directors, inline price rows 15/16 empty trigger)
- Layout B: `8857082` (adjacent 2-row `['Price(s)', 'Volume(s)']` sibling table)
- Layout C: `8857578` (Great Portland, partnership + matching share rows — deferred)

---

## Implementation plan

### Phase 1 — Fix `_extract_via_sections` (Zone A — Claude builds)

**Change 1 (Fix B): Capture sibling price table at section-build time**

In the sections-discovery loop, after each matching section table, check if the immediately following table is a price/volume table (≤4 rows, first row contains "Price" and "Volume" headers). Store as `(section_trs, sibling_trs_or_None)` pairs.

**Change 2 (Fix A): Look-ahead within section rows**

When the "Price(s) and volume(s)" trigger row has no data in expected cells, scan the next 4 rows of the same section for:
1. A sub-header row (cells containing "Price" and "Volume") — skip it
2. A data row with digit-containing cells — extract price + volume

**Change 3: Sibling table fallback**

After the main KV row scan completes with no price found, try the sibling table. The sibling's second row (index 1) holds price and volume as two cells.

### Phase 2 — Tests (Zone A — Claude builds)

New `test_b090_bundled_layouts.py` covering:
- Layout A: inline multi-row price extraction (Polar Capital pattern)
- Layout B: adjacent sibling table extraction
- Regression: existing AAL 8950385 (B-023) still works
- Regression: full parse_announcement returns rows for all three test filings

### Phase 3 — Reparse pending queue (Zone B — Rupert runs)

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\reparse_corpus.py --dry-run   # preview count
python .scripts\reparse_corpus.py             # apply
python .scripts\refresh_all.py                # re-eval signals + rebuild dashboard
```

### Phase 4 — Verify

Check signal count delta. Spot-check a Great Portland and Polar Capital row in the dashboard.

---

## Acceptance criteria

1. `test_b090_bundled_layouts.py` — all tests pass
2. B-023 regression suite (`test_b023_bundled_sections.py`) — still all green
3. Full `unittest discover` — no new failures
4. `reparse_corpus.py --dry-run` shows >500 new rows recoverable
5. Spot-check: a Polar Capital (POLR) and a NED bundle row appear in the dashboard after reparse

---

## Out of scope (this sprint)

- Layout C multi-tranche SIP within bundled filings (Great Portland pattern)
- Non-RNS provider layout work (B-092)
- Any changes to the signal engine or export pipeline
