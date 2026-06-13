# Year-as-shares parser refix — plan (2026-05-31)

**Status:** approved by Rupert 2026-05-31; decisions locked below. Build = Steps A + B. Step C deferred.

## Why this exists

The year-as-shares bug (originally found 2026-05-27, "fixed" in Sprint 11 / B-061) has
**regressed**. A read-only audit (`.scripts/audit_year_shares.py`) found **32 rows** whose
`shares` value is a 4-digit year, clustering at `shares=2026` (18 rows) and `shares=2025`
(11 rows). The `data-integrity-auditor` source-verified two of them against the live RNS:

| Filing | Dashboard claim | Truth (RNS) |
|--------|-----------------|-------------|
| EMAN / Charles Dorfman, 20 May 2026 ([9581012](https://www.investegate.co.uk/announcement/rns/everyman-media-group--eman/pdmr-shareholding/9581012)) | 2,026 shares, £679 | **127,083** shares (50,000 + 5,583 + 71,500), £42.6k |
| EMAN / Charles Dorfman, 27 May 2026 ([9592451](https://www.investegate.co.uk/announcement/rns/everyman-media-group--eman/pdmr-dealing/9592451)) | 2,026 shares, £719 | **67,649** shares, £24.0k |

These rows were ingested **after** Sprint 11 shipped (2026-05-28), so this is a genuine gap in
the fix, not stale un-reparsed data.

## Root cause (verified by Plan agent against cached HTML; build must re-confirm line numbers)

1. Both EMAN tables **lack a director-name column**. `_find_transaction_table` matches the
   table, but `_extract_via_table` drops every row (`could_not_extract_PDMR_name` →
   `required_fields_missing`). With zero surviving table rows, `parse_announcement` falls through
   to the **legacy regex path**.
2. In the legacy path, `_VOLUME_LABEL_RE`'s bare `\bShares?\b` alternative matches narrative prose
   ("…purchased 127,083 Ordinary **Shares** at 33.5 pence … 2026"). The number harvester then
   picks up `2026`.
3. `_looks_like_date_bleed` **Trigger 2** (year rejection) has an escape hatch: it does *not* fire
   when other integers are present in the block — which is always true in prose. So `2026` is
   accepted as the volume.
4. The final `_plausibility_check` rule R5 (date-component-in-shares) is **warn-only** and gated on
   `value < £100`, so it never rejects these rows.

## Locked decisions

1. **Row shape — one aggregated row per filing.** Tranches within a single announcement are summed:
   `shares` = sum of tranche volumes; `price` = **volume-weighted average** (total value ÷ total
   shares), or the filing's aggregated price when given. Tranches are NOT separate rows.
   Separate *announcements* (e.g. Dorfman's 20 May vs 27 May filings) remain separate rows.
2. **Signals fire once per filing** — one disclosed event, one signal. Keeps tier-CAR per-event.
3. **Rejected / untrusted volumes are surfaced for manual handling** — never silently dropped,
   never silently filled with a year. Mechanism (two layers): the parser logs the filing to
   `_suspect_filings.jsonl` and returns empty-with-warnings; the ingest layer
   (`backfill_filings.py` / `reparse_corpus.py`) then routes any zero-row/warning filing into
   `_pending_review.json`. This is where the 3 low-confidence outliers
   (BRK/BWY `shares`=2021/2024) will surface for Rupert's eyeball.
4. **Step C deferred** (tightening `_VOLUME_LABEL_RE` against prose) — highest regression risk,
   and A + B fix both confirmed layouts. Revisit only if new prose-bleed cases appear.

## Build — Step A (hard year guard)

- In `_looks_like_date_bleed`, make the year rejection (Trigger 2) reject any value in
  `1990..2099` **unconditionally** — remove the "other integers present" escape hatch (or narrow
  it so a 4-digit year is never exempted).
- Add a value-independent guard in `_plausibility_check`: any `shares` exactly equal to a 4-digit
  year (1990..2099), *especially* matching the filing/transaction year, is **rejected** (not warned)
  and the filing is routed to pending-review.
- Rationale: a 4-digit year is overwhelmingly a bleed; the rare genuine ~2,0xx-share holding is
  better surfaced for manual confirmation than silently trusted or silently dropped.

## Build — Step B (table-aware aggregate / tranche-sum, name from detail table)

- Let a `Price|Volume`-only or `Date|Price|Volume`-only table (no name/position column) qualify as a
  transaction table; take the PDMR name from the KV detail block (`a) Name → …`) via the existing
  `_find_kv_in_soup`, not from a table column.
- Read the labelled `Aggregated volume:` line. If it parses to an integer, use it. If it reads
  `N/A`/blank, **sum the per-tranche `Volume` cells**.
- Emit **one** transaction per PDMR per filing: summed volume, VWAP price, dated to the latest
  tranche (preserves the audit-expected `2026-05-20` / `2026-05-27` dates).
- Result: 9581012 → 127,083 @ blended price; 9592451 → 67,649 @ 35.5p. Neither falls through to the
  year-bleeding legacy path.

## Tests

- New fixtures under `.scripts/fixtures/parser/` built from the cached HTML:
  - `eman_9581012_multitranche` → 1 row, `shares=127083`, date `2026-05-20`, asserts `shares != 2026`
    and no `required_fields_missing` warning.
  - `eman_9592451_two_col_na` → 1 row, `shares=67649`, date `2026-05-27`, asserts tranche-sum-on-N/A.
- Unit tests: bare-year rejection; prose-bleed rejection (the regression assertion — currently
  returns False); tranche-sum helper; aggregate-label reader (value vs N/A fallback); plausibility
  rejection of `shares=2026, value=678.71`.
- Run: `python -m unittest discover -s .scripts -p "test_*.py"`.

## Reparse + verification (Rupert, PowerShell — Zone B, Claude must NOT run)

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\audit_year_shares.py            # baseline
python .scripts\reparse_corpus.py --preview     # writes nothing; emits diff CSV
# --- MANUAL GATE: review preview; ~29 high-confidence rows flip; BRK/BWY outliers land in pending-review ---
python .scripts\reparse_corpus.py --confirm
python .scripts\eval_signals.py
python .scripts\build_dashboard.py
python .scripts\audit_year_shares.py            # expect ~0 suspect rows
```

## Blast radius / caveats

- ~29 high-confidence rows (18×2026, 11×2025) corrected or routed to pending-review; well under the
  `SAFETY_MAX_AFFECTED_FRACTION = 0.5` guard.
- Contaminated rows may already have signals/paper-trades against the wrong volume — tier-level CAR
  is **provisional** until reparse + re-eval + rebuild complete.
- The 3 outliers (BRK 2021/2025-date, BWY 2021/2025-date, BWY 2024/2026-date) need individual
  source verification; with pending-review routing they surface there rather than being auto-deleted.

## Claude-safe vs Rupert-run

- **Claude-safe:** edit `parse_pdmr.py`, `test_parser.py`, add fixtures (Zone A, text); run the test
  suite; verify every edit with the **Read tool** (FUSE staleness rule).
- **Rupert-run (Zone B):** `reparse_corpus.py`, `eval_signals.py`, `build_dashboard.py`.
