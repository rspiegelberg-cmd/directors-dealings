# Directors Dealings — Persistent Session Memory

Cross-session facts that are too volatile for CLAUDE.md but too important to lose.
Append a new block after each session. Newest at the top.

---

## 2026-06-09 session — Earnings display + corrupt-row bug fix

### Schema
- **Schema version: v12** (head as of 2026-06-09). Migration 012 added `source_url TEXT` (nullable) to `reporting_dates`. Applied via `db._apply_schema_migrations()` step 11→12; SQL in `.scripts/schema_migrations/012_reporting_dates_source_url.sql`.

### Data pipeline
- **`backfill_reporting_dates.py`** now extracts and stores a full absolute Investegate filing URL in `source_url` (e.g. `https://www.investegate.co.uk/index.aspx?id=1234567`). Re-running with `--no-cache` back-fills any NULL `source_url` rows (existing confirmed rows only; does not re-scrape if TTL cache is warm).
- **`build_dashboard.py`** `all_reporting_dates` query fetches `source_url`; passes it through to each company's dict under key `"source_url"`.

### Display
- **`_filings_section(company)`** added to `render_company.py` — the last item in `render()`'s `sections` list. Renders past reporting dates (≤ today) in an amber/slate table. Confirmed rows get amber badge + "View filing ↗" link using `source_url`; estimated rows get grey badge + "(est)" label + Investegate company search fallback. Returns `""` if no past dates.
- **`_FILING_TYPE_LABELS`** dict in `render_company.py` maps DB type codes → human strings (mirrors JS-side `_EARNINGS_TYPE_LABELS`).
- Earnings date **chart markers** on the company price chart: vertical dashed amber/grey lines + diamond scatter dataset (`__earnings` label, `rectRot` point) at each earnings date's closing price. `afterDraw` plugin draws the lines; Chart.js hit detection handles tooltip. `__` prefix on earnings dataset label prevents it appearing in the legend via the `filter` function.
- **"(est)"** badge suffix on reporting-date badges throughout the page (header next-results badge, per-transaction near-results badge) when `confidence = 'est'`.

### Bug: corrupted reporting_dates rows (June 4th bad scraper run)
- **Root cause:** on 2026-06-04 `backfill_reporting_dates.py` used `Index.aspx?searchtype=RNSType&searchterm=...&searchrns={TICKER}` which returns the **general all-companies feed** (not per-company). Every ticker visited got `2026-06-04, INTERIM` written. 594 rows corrupted.
- **Fix:** `.scripts/fix_corrupted_reporting_dates.py` — DELETE WHERE `report_date = '2026-06-04' AND source = 'investegate' AND fetched_at BETWEEN '2026-06-04T00:00:00Z' AND '2026-06-05T00:00:00Z'`. Preserves 6 legitimate June 4th rows (AURR, CMCX, FIN, GTLY, MTO, SPR — fetched by the corrected June 8th scraper). **Zone B — Rupert ran from PowerShell.**
- **Scraper fix (already live since 2026-06-08):** per-company URL `investegate.co.uk/company/{TICKER}` replaces the old RNS-type feed URL.
- **Re-backfill with `--no-cache`** required after delete (30-day TTL cache would otherwise skip already-cached tickers).
- **Pattern to remember:** `INSERT OR IGNORE` semantics mean existing corrupt rows block re-insertion. Always check `fetched_at` when diagnosing unexpected dates — it reveals which scraper run wrote the data.

### PowerShell inline Python quoting gotcha (documented for future)
- Inline `python -c "... WHERE field LIKE '%pattern%' ..."` fails in PowerShell — `%` and identifiers after it are interpreted as PS tokens. Workaround: write a real `.py` script file. Use `>=`/`<` date-range comparisons instead of LIKE to avoid the `%` issue entirely.

### Linear issues created this session
- DIR-79 = B-146 (chart markers), DIR-80 = B-147 (Earnings History box), DIR-81 = B-148 (corrupt-row cleanup). All Done, Sprint 32 Cycle 1.

### Next B-number
- **B-149** is the next free number.

---

## Earlier sessions

*(No prior MEMORY.md entries — this is the inaugural file. Historical facts live in `docs/backlog.md` shipped log and `docs/specs/roadmap-2026-06-05.md` §3.)*
