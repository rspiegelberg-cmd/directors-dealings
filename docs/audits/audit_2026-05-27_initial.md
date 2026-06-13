# Data Integrity Audit — 2026-05-27 (initial)

**Auditor:** data-integrity-auditor (first invocation)
**Trigger:** Rupert spotted a Tullow Oil (TLW) row on the dashboard showing 2,026 shares × £0.17 = £344 when the source filing clearly shows **115,000 shares × £0.17 = £19,512.05**.
**DB snapshot:** `directors.db` copied to `/tmp/audit/directors.db` (FUSE-safe). 4,763 transactions, 1,671 with source URLs.

## Headline

**Confirmed parser bug.** At minimum 38 rows in the DB have `shares == year(transaction_date)` (i.e. "2025" or "2026" stored as the share count). Two source-verifications confirmed this is a parser bug, not a coincidence:

| Ticker | Source filing | DB row | Result |
|---|---|---|---|
| TLW (Tullow Oil) | 115,000 shares | 2,026 shares | ❌ MAJOR_MISMATCH |
| GRG (Greggs) | 1,615 shares | 2,026 shares | ❌ MAJOR_MISMATCH |

**Blast radius — signal engine:** 20 of the 38 bad rows had at least one signal fire against them. **24 total signal firings** are based on contaminated `shares` values, which propagates into value-banding, percentile-rank logic, and any signal threshold that uses `shares` or `value`.

## Field-level findings (static heuristic only — no source fetches required to identify)

| Pattern | Rows | Confidence bug? | Signal firings on bad rows |
|---|---|---|---|
| `shares == year(date)`, year ∈ [1990,2099] | **38** | 🔴 Very high (2/2 spot checks confirmed) | 24 |
| `shares` in [1990,2099] but ≠ year(date) | 19 | 🟡 Medium — worth verifying | (not separately counted) |
| `shares == day(date)`, shares ≤ 31, with non-zero price | up to 95 | 🟡 Mixed — small SIP allocations can legitimately equal day numbers | 44 |
| `shares == month(date)`, shares ≤ 12 | 53 | 🟢 Mostly coincidental (1, 5, 10 are real volumes) | — |
| `value ≠ shares × price` (>5% deviation) | 0 | — | Parser is internally consistent ← this is *why* the bug stayed silent |

## Root cause (parser code)

The bug lives in `.scripts/parse_pdmr.py`, function `_parse_volume_cell` (line 1281).

```python
def _parse_volume_cell(text: str) -> tuple:
    if not text:
        return 0, ["could_not_separate_price_volume"]
    for m in NUMBER_RE.finditer(text):
        num_str = m.group("num").replace(",", "")
        try:
            val = float(num_str)
        except ValueError:
            continue
        if val >= 1 and abs(val - int(val)) < 1e-6:
            return int(val), []     # ← returns first integer, no year check
    return 0, ["could_not_separate_price_volume"]
```

Compare to the **legacy regex extraction path** (line 633-653) which has had a `_looks_like_date_bleed()` defence in place since Sprint 9 Phase B (line 665). That defence rejects:
- Day-of-month integers (1-31) when a month word appears AND price < £1
- **Year-only integers (1990-2099) when they're the only integer in the block**
- Tiny share counts (< 10) with no companion price

**The table-aware extraction path (added Sprint 3) never inherited this protection.** For 38 filings the table extractor pulled a "volume" cell whose text was actually a date or a year-only cell (likely because of BeautifulSoup flattening nested tables — see comment at line 1392-1395 acknowledging the nested-table fragility), and `_parse_volume_cell` happily returned the year.

## Recommended fix (ranked)

**Tier 1 — minimum viable fix** (~30 mins, low risk)

Port `_looks_like_date_bleed()` into `_parse_volume_cell`. When the first candidate volume is a year (1990-2099) AND no other plausible share count exists in the cell, return `(0, ["could_not_separate_price_volume_year_bleed"])` instead of the year.

**Tier 2 — structural fix** (~2 hrs, medium risk)

When the table extractor populates `volume_cell`, validate that the cell does NOT also contain typical date markers ("May", "/", "-" between digits). If it does, treat the table as malformed and fall through to the legacy regex path (which has the protection).

**Tier 3 — defence in depth** (~30 mins, no risk)

Add a final assertion in `parse_announcement` before emitting each row: if `1990 <= shares <= 2099 and int(date[:4]) == shares`, append a warning and drop the row. This is a "loud failure" net that catches anything the parser-level fixes miss.

Recommend **Tier 1 + Tier 3 together** for this sprint.

## Reparse scope

Once the parser fix lands, `reparse_corpus.py` must re-extract every row currently in the year-match bucket — at minimum these 38 fingerprints (`docs/audits/year_match_rows_2026-05-27.csv`). After reparse:

- Any row that re-extracts to a new (different) `shares` value will get a new fingerprint and the old bad row should be deleted (existing logic per `memory/project_sprint3_fingerprint_decision.md`).
- Any row that *still* re-extracts to the year value means the source filing genuinely has only a year in the volume cell — escalate manually (likely fewer than 5 cases).

Estimated DB write volume: ~38 row updates + ~38 row deletes + signal recomputation on ~24 signals. Trivial.

## Signal-engine action

The 24 contaminated signal firings should be **invalidated and re-evaluated** after reparse. Specifically check if any of the firings are propped up by an artificially small `value` (£344 instead of £19,512 makes a T2 signal look like a T4, etc.). Recommend invoking the `analyst` agent post-reparse to recompute these signals' backtest contribution.

## Limitations of this audit

1. **Only static heuristics ran.** Source verification was done on 2 rows (Tullow, Greggs). The other 36 year-match rows are *highly likely* the same bug pattern but not individually confirmed.
2. **Pattern 4 (day-of-month) needs deeper triage.** 41 of 106 day-match rows are SIP transactions — small share counts (1-31) are normal in SIP scheme allocations and most likely real. The 38 BUY rows in this bucket are the higher-concern subset.
3. **No audit done on `director` or `role` fields** — the audit only covered `shares`. A wider field audit (director / role / type / company) needs a separate run.
4. **No audit on the 3,092 rows without URLs** — these can't be ground-truth verified. Worth investigating why so many rows lack URLs (URL backfill may have skipped pre-Sprint-2 imports).
5. **Statistical confidence:** N=2 spot checks gives near-zero formal confidence on the prevalence rate — but the consistency of the pattern (year value, same direction) plus the static heuristic count of 38 makes the qualitative conclusion robust.

## Artefacts

- `docs/audits/year_match_rows_2026-05-27.csv` — all 38 year-as-shares rows
- `docs/audits/day_match_rows_2026-05-27.csv` — all 106 day-match rows for triage
- This report

## EXPANDED SCAN — additional silent failures found (2026-05-27 follow-up)

After completing the year-as-shares finding, ran an exhaustive static scan for other silent failure patterns. Findings ranked by signal-contamination impact:

### Tier 1 — Confirmed/very-likely bugs with signal exposure

| # | Pattern | Rows | Signals on bad rows | Notes |
|---|---|---|---|---|
| 1 | **Pence-as-pounds (price > £100)** | 106 (across 28 of 30 distinct tickers) | 71 | UU avg £14,247; BHP £81,390; IDOX £1,295,172; STG £1,425,258 — clearly 100x off. Only NXT and AZN sit in genuine GBP > £100 range. |
| 2 | **BUY with price=0 (signals fire anyway)** | 149 | **149** | Every BUY+price=0 row fires a signal. Sprint 9 D.3 was supposed to suppress these. Examples: SOLI BUY 10 sh, BNZL BUY 47 sh, CAN BUY 5000 sh — all £0 value. |
| 3 | **price == shares (duplicate extraction)** | 5+ | (subset of above) | FAN GRANT 41,444 sh × £41,444 = £1.72B; TKO EXERCISE 6,000 × £6,000 = £36M. Parser pulled same number twice. |
| 4 | **Year-as-shares** | 38 | 24 | Original finding — fix above. |
| 5 | **Tiny BUY shares (<5) on high-priced stocks** | 37 | 57 | BA BUY 3 shares, RR BUY 1 share at £8.83 — likely day-of-month bleed. |
| 6 | **Sub-penny prices (< £0.001)** | 11 | 14 | AEG 5.88M × £0.00085 — unit confusion at the small end. |

**Combined Tier 1 signal contamination: ~330 firings affected** (some overlap; out of 750 total = up to ~40% of signal-engine output is built on dirty data).

### Tier 2 — Confirmed display/clustering issues, low signal impact

| Pattern | Rows | Notes |
|---|---|---|
| Role field prose bleed | 30 | "in this regard", "s are intended to create value", "PDMR of the Company" |
| Director name capitalisation variants | 9 distinct names | Kate Rock / KATE ROCK, Phil Bentley / PHIL BENTLEY — affects cluster dedup |
| Director truncated to 2-3 chars | 3 | "Bl" (HLN), "Ant" (LGEN x2) — extraction failed mid-name |
| Director == company (B-017 class residual) | 2 | OXB rows with director field = company-style string |
| Director boilerplate ("Person closely associated") | 2 | Parser pulled the section header as the name |
| Tickers with multiple company strings | 24 | AAL, SSPG, DSCV etc. include "emission allowance market participant" boilerplate, "a)", and director names mixed in |

### Tier 3 — Coverage gaps, not bugs but worth knowing

| Pattern | Rows | Notes |
|---|---|---|
| company field empty | 2,862 (60%) | 1,444 signals fired on rows with no company. Likely historical/backfill gap. |
| announced_at missing | 3,096 (65%) | Affects any signal that uses announcement-to-transaction window (T0). |
| url missing | 3,092 (65%) | Limits future source-verification capability. |

### Outliers needing manual sanity check

23 rows have value > £10M. Top suspicious ones (price == shares pattern):
- FAN 2025-10-14 GRANT — sh=41,444, pr=41,444, val=£1.72B
- FAN 2025-10-14 GRANT — sh=29,001, pr=29,001, val=£841M
- TKO 2026-01-29 EXERCISE — sh=6,000, pr=6,000, val=£36M
- TKO 2026-03-04 EXERCISE — sh=4,000, pr=4,000, val=£16M

These 4 alone inflate total trading value by ~£2.6B of artefact.

## Revised recommendation — coordinated fix sprint

**Do NOT ship the year-as-shares fix in isolation.** The corpus has multiple independent silent failures contaminating signal-engine output. Recommend a single Parser Hardening sprint covering:

1. Port `_looks_like_date_bleed()` into `_parse_volume_cell` (year-as-shares fix)
2. Detect pence-vs-pounds at parse time: if instrument description says "10p each" / "1p each" / "ordinary shares of [X]p" AND extracted price > £10 AND price is integer-like (≥100, no fractional pence), divide by 100
3. Reject any row where extracted `shares == extracted_price` (duplicate-number pull)
4. Fix the BUY-with-price=0 leak (the D.3 gate must apply to *all* extraction paths including the table-aware path that B1 rows came from)
5. Reject director cells matching the prose-bleed patterns ("Person closely associated", "Trustee of", etc.)
6. Reject role cells starting with lowercase or punctuation
7. Strip "emission allowance market participant…" boilerplate from company field
8. Normalise director name capitalisation (Title-Case) at write time

**Then one big reparse covers everything in one pass.**

Estimated work: ~4-6 hrs Back-end + QA + tests. Worth scoping as a dedicated Sprint 11 ("Parser Hardening + Reparse") rather than tacking onto current sprint.

## REVISED Next actions (replaces section above)

1. **Rupert decides scope**: full Parser Hardening sprint or year-as-shares only? Strong recommendation: full sprint.
2. **Source-verify 3 pence-as-pounds candidates** (UU, BHP, BLND) before bulk-reparse — make sure my interpretation is right and these aren't genuine GBP-priced micro-caps
3. **Spec Sprint 11** with back-end engineer (with this audit report as input)
4. **QA-gate the parser changes** before any reparse
5. **One coordinated reparse_corpus.py run** over the union of all affected fingerprints
6. **Re-invoke data-integrity-auditor** to confirm zero regression
7. **Analyst recheck**: re-run backtest, see how per-signal mean CAR changes once dirty firings are removed. Possible that some signals that look strong today are propped up by these artefacts.

## Limitations updated

- **No source verification yet on pence-as-pounds rows** — heuristic only. Need 3-5 hand-checks before declaring it a bug class with confidence.
- **price-as-pence and pence-as-pounds are symmetric concerns** — a row showing 0.0005 might be £0.0005 (real, small) or 0.05p stored wrong. Hard to distinguish without source.
- **director/role bleed patterns are illustrative not exhaustive** — many more variants likely exist; needs an LLM-assisted cell-purity classifier for full coverage.

## 2026-05-27 follow-up — Yahoo cross-validation of price>£100 bucket

Rupert asked for source-verification of 3 pence-as-pounds candidates (UU, BHP, BLND). **None had URLs** — all from a 2026-05-22 bulk backfill via legacy regex path with no source link captured. Substituted a stronger method: cross-validate every row with `price > £100` against the Yahoo `prices` table where we have OHLCV close data for the same date.

**Result across 106 suspect rows:**

| Verdict | Count | Pattern |
|---|---|---|
| OK — genuine £100+ stock | 8 | NXT, AZN, GAW (verified ratio ≈ 1.0 against Yahoo close) |
| BUG-100x (pence-as-pounds) | 19 | BLND, BRK, CVSG, ELIX, EMG, FVA, GNS, IGP, PAGE — clean 100x error |
| BUG-10x (decimal-shift) | 16 | All PRU rows — exact 10x error |
| BUG-1000x | 6 | UU, BHP, TKO — extra-pence shift |
| BUG-extreme (>1200x) | 33 | BGO, IDOX (£1.4M!), STG (£1.4M!), SMIN, FAN, CSN, WOSG — parser pulled a non-price number entirely |
| No Yahoo data | 12 | Foreign tickers, can't verify |
| Other unclear | ~12 | Ratios in 24x-770x range |

**Verdict: pence-as-pounds + extreme-extraction bug class fully confirmed.** 74 of 106 suspect rows definitively bugged, plus another ~12 likely-bugged. Cross-validation against price data is a stronger method than source-fetching — recommend the data-integrity-auditor agent add this technique to its standard toolkit.

**Critical context for Sprint 11:** All 70 R3-violation rows (price > £200) have `first_seen` between 2026-05-15 and 2026-05-22 and all use `parser_source = 'regex'`. Sprint 9's plausibility gate landed AFTER these were ingested and is now correctly blocking new violations. **No bad rows have entered the corpus post-2026-05-22.** Sprint 11 is therefore a corpus-cleanup operation, not a parser-rewrite. The new fixes needed (year-as-shares in table path, prose bleed, capitalisation) are the only NEW work; the existing Sprint 9 gates handle the rest once a reparse is run.
