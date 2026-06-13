# Stage 2 — RNS feed (Investegate scraper + PDMR parser + LLM fallback + multi-year backfill)

**Status:** Plan v1.2 — 2026-05-12. ALL decisions locked. D-LLM = include now, D-WIN = backfill from 2024-01-01 to today, fixtures = auto-pick, Q-ENV = `.env` file in project root. Build agent dispatched.
**Owner:** Rupert
**Target ship:** Multi-session. Daily-incremental scraper + LLM ships in one weekend (~8–10 hours of build); the 2024→today backfill is an overnight run (~5–7 hours of scrape time + ~$20–$30 of Anthropic API spend).
**Source:** `docs/specs/02-phase-0-stabilisation.md` (parser blueprint), `docs/specs/03-phase-1-backfill-storage.md` (scraper design), `docs/specs/04-phase-2-llm-fallback.md` (deferred), `docs/specs/stage-01-plan.md` (locked schema).
**Author:** Planning pass, 2026-05-12.

---

## Goal

After Stage 2 ships, two commands exist:

1. **`python .scripts\run_scrape.py --days 60`** — the daily-incremental command. Walks the Investegate "Director Deals" index for the last 60 days (configurable), fetches PDMR-style RNS filings into a local HTML cache, parses them with a stdlib-only **regex parser** first, falls back to a paid-API **LLM parser** when the regex returns warnings, and writes clean rows into the `transactions` table via `db.connect()`. Filings the LLM also can't clean go to the JSON pending-review queue.
2. **`python .scripts\backfill_filings.py --from 2024-01-01 --to today`** — the historic backfill. Resumable across crashes via a progress manifest. Drives the same scraper + parser pipeline as the daily command, but spread across an overnight run with a hard LLM cost ceiling (default $50/run) so a stuck loop can't drain the API balance.

Re-running either command produces the same DB state (idempotent). Stage 2's QA gate: (a) ≥50 transactions in the daily-incremental smoke, (b) 5 random rows spot-checkable against source URLs, (c) regex-only pending rate (LLM disabled) under 30%, (d) end-to-end (regex + LLM) pending rate under 10%, (e) the multi-year backfill completes with at least 2,500 transactions in the DB covering 2024–2026.

This stage produces no signals, no UI, no price data, and no `tickers_meta` rows. Its payoff is that Stages 3–5 finally have a populated `transactions` table to work against.

---

## Files to create

| Path (absolute) | Purpose |
|---|---|
| `C:\Dev\DirectorsDealings\.scripts\scrape_investegate.py` | Walks the Investegate Director-Deals index AND the `/announcement-archive` for historic dates. Fetches HTML, caches it, tracks progress. Exposes `iter_filings(start, end)` and `fetch_one(rns_id)`. |
| `C:\Dev\DirectorsDealings\.scripts\parse_pdmr.py` | Stdlib-only regex PDMR parser. Returns `(extracted_list, warnings_list, parser_source='regex')`. Handles ordinal-date regex, foreign-currency detection, bundled-PDMR refusal, SIP/GRANT/EXERCISE classification (per Barclays fixture). |
| `C:\Dev\DirectorsDealings\.scripts\llm_parser.py` | Anthropic API fallback parser. Called only when the regex parser returns warnings. Sends `html_to_text` output + a focused prompt to Claude Sonnet via the Anthropic SDK; expects a JSON response in the same shape as the regex extracted_dict. Adds `parser_source='llm'`. Records cost per call into `_llm_cost.json`. |
| `C:\Dev\DirectorsDealings\.scripts\llm_cost.py` | Cost tracker and ceiling guard. Maintains `_llm_cost.json` (running total per run + lifetime). Aborts the orchestrator if the current-run spend exceeds `--llm-budget-usd` (default 50.00). Prints a per-call cost line. |
| `C:\Dev\DirectorsDealings\.scripts\run_scrape.py` | Daily-incremental orchestrator CLI. `--days 60` (default), `--from/--to`, `--rns-id`, `--dry-run`, `--no-llm` (skip LLM fallback), `--llm-budget-usd 50.00`, `--verbose`. |
| `C:\Dev\DirectorsDealings\.scripts\backfill_filings.py` | Multi-year backfill orchestrator. `--from 2024-01-01 --to today` (default), `--resume`, `--llm-budget-usd 50.00`, `--no-llm`, `--verbose`. Walks the Investegate archive page-by-page, day-by-day. Persists progress after every filing so an overnight crash resumes cleanly. |
| `C:\Dev\DirectorsDealings\.scripts\test_stage_02.py` | Smoke + unit + integration test. Self-cleaning via temp-DB monkey-patch. Includes an LLM-mock fixture (no real API calls during tests). Exit 0 / non-zero. |
| `C:\Dev\DirectorsDealings\.scripts\fixtures\README.md` | Documents each HTML fixture's source URL and retrieval date. |
| `C:\Dev\DirectorsDealings\.scripts\fixtures\clean_buy_*.html` | At least one clean single-director discretionary BUY (auto-picked from smoke scrape). |
| `C:\Dev\DirectorsDealings\.scripts\fixtures\clean_sell_*.html` | At least one clean single-director SELL (auto-picked from smoke scrape). |
| `C:\Dev\DirectorsDealings\.scripts\fixtures\bundled_pdmr_9540067.html` | Schroders bundled multi-PDMR filing (confirmed via spec 02 + index sample 2026-05-12). The parser must classify this as bundled and refuse to split. |
| `C:\Dev\DirectorsDealings\.scripts\fixtures\sip_barclays_9564893.html` | Barclays SIP filing (Taalib Shaah, Group CRO; 554 shares acquired by SIP trustee 2026-05-07). The parser must classify this as `type=SIP`, not BUY — exercises the non-discretionary detection logic. |
| `C:\Dev\DirectorsDealings\.scripts\schema_migrations\002_add_parser_source.sql` | Adds `parser_source TEXT NOT NULL DEFAULT 'regex'` column to `transactions`. Bumps `meta.schema_version` to `'2'`. Idempotent via `IF NOT EXISTS` check pattern. |

Runtime-created artefacts (not committed, gitignored alongside `.data/`):

| Path | Created by | Purpose |
|---|---|---|
| `C:\Dev\DirectorsDealings\.scripts\_scrape_cache\{rns_id}.html` | scraper on first fetch | Per-filing HTML cache; lets the parser be iterated without re-fetching. Estimated ~750 MB after the 2024→today backfill. |
| `C:\Dev\DirectorsDealings\.scripts\_scrape_progress.json` | scraper | `{last_rns_id, last_run_at, window_start, window_end}` — daily-incremental resumability state. |
| `C:\Dev\DirectorsDealings\.scripts\_backfill_progress.json` | backfill_filings.py | `{started_at, current_date, completed_dates: [...], filings_seen, transactions_written, pending_count}` — multi-year backfill resumability state. |
| `C:\Dev\DirectorsDealings\.scripts\_pending_review.json` | orchestrators | `{generated_at, count, items[...]}` — filings BOTH parsers couldn't clean. |
| `C:\Dev\DirectorsDealings\.scripts\_llm_cost.json` | llm_cost.py | `{lifetime_usd, runs: [{started_at, finished_at, calls, tokens_in, tokens_out, usd}, ...]}` — Anthropic API spend ledger. |
| `C:\Dev\DirectorsDealings\.env` | Rupert (manual, one-time) | Holds `ANTHROPIC_API_KEY=sk-ant-...`. Never committed (added to `.gitignore`). Loaded by `llm_parser.py` via stdlib `os.environ` (with optional `.env` parsing — see open question Q-ENV below). |

---

## Decision points

### D-LLM — LLM fallback in Stage 2?

**Rupert's call (2026-05-12): (a) INCLUDE NOW.** Reasoning: he wants the highest possible coverage on the 2024→today backfill so the signal engine in Stage 4 has the densest possible dataset to evaluate. The marginal cost (~$20–$30 one-time for the backfill, ~$15/year steady-state) is acceptable.

**Implications baked into this plan:**
- New files: `llm_parser.py`, `llm_cost.py`, `schema_migrations/002_add_parser_source.sql`.
- New column on `transactions`: `parser_source TEXT NOT NULL DEFAULT 'regex'`. Values: `regex`, `llm`, `manual`. Schema version bumps 1 → 2.
- New env var: `ANTHROPIC_API_KEY`. Loaded from environment OR `.env` file (see Q-ENV below).
- Cost ceiling: `--llm-budget-usd 50.00` default per run. `llm_cost.py` aborts the orchestrator if a single run exceeds this. Lifetime spend tracked in `_llm_cost.json`.
- LLM is called ONLY when the regex parser returns warnings. Clean regex parses never call the API.
- LLM is gated behind `--no-llm` (skip) and `--dry-run` (parse cache only, no API calls or DB writes).
- Tests do NOT call the real API. `test_stage_02.py` mocks `llm_parser.parse_with_llm` to return canned responses.

### D-PEND — Pending-review storage: JSON vs SQLite

Options: (a) `.scripts\_pending_review.json` per spec; (b) a new `pending_review` table in SQLite.

**Recommendation: (a) JSON file.** Rupert is a beginner; the queue is a triage artefact he will physically open and read. JSON in a hand-editable file with a stable shape is inspectable in any text editor, diffable, and trivially backupable. A SQLite table is more queryable but Stage 2 doesn't have a query use case for it. Keeping `transactions` schema completely unchanged from Stage 1 is a feature.

### D-WIN — Scrape window

**Rupert's call (2026-05-12): backfill 2024-01-01 → today (i.e. ~28 months of history).** Reasoning: he wants meaningful historic depth for the Stage 4 signal engine and Stage 5 performance tracker. A 60-day smoke is insufficient to backtest the 7-signal taxonomy with statistical confidence.

**Implications baked into this plan:**
- New file: `backfill_filings.py` — dedicated multi-year orchestrator, separate from the daily-incremental `run_scrape.py`. Both share the scraper, parser, and DB-write path.
- New progress file: `_backfill_progress.json` — persists state after every filing so an overnight crash resumes cleanly.
- Investegate archive pagination: the scraper must walk `https://www.investegate.co.uk/announcement-archive` page-by-page (the live index only shows the most recent ~300 filings). The build agent will probe the archive URL shape on first invocation and code against what's verified live.
- Disk usage: ~750 MB of cached HTML expected by end of backfill.
- Scrape time: ~5–7 hours of pure fetch time at the polite 0.8s ± 0.2s interval.
- Cost: ~$20–$30 one-time of LLM API calls (if `--no-llm` isn't passed).
- Fail-loud: if the scraper hits a structural change in Investegate's archive at any date, it aborts with a clear "stuck at YYYY-MM-DD; archive page returned 0 rows" message. All progress is preserved.
- The daily-incremental `run_scrape.py --days 60` still exists for the daily refresh after the backfill.

### D-POLITE — Rate limit + User-Agent + robots.txt

**Recommendation:** 0.8 seconds between requests with ±0.2s jitter (range 0.6–1.0s). User-Agent: `DirectorsDealings-Research/0.2 (+contact: rspiegelberg@gmail.com; personal research tool)`. Fetch `https://www.investegate.co.uk/robots.txt` once at start of each run and refuse to crawl any disallowed path. On HTTP 429/503: exponential backoff 30s → 60s → 120s, max 3 retries, then abort cleanly preserving cache + progress.

### D-CACHE — HTML cache location

**Recommendation:** confirm `.scripts\_scrape_cache\{rns_id}.html`. Underscore prefix signals "internal". `.gitignore` excludes it.

### D-RNSID — Stable filing ID derivation

**Recommendation:** prefer Investegate numeric ID where present. When absent, derive `h-` + `sha1(canonical_url).hexdigest()[:12]`. The `h-` prefix distinguishes derived IDs at a glance.

### D-STRICT — Parser strictness

**Recommendation:** confirm Stage 2 inherits spec 02 D1 verbatim. Bundled multi-PDMR filings return `([], [enriched_warning])` and go to pending review. The enriched warning carries the named PDMR list and roles. No aggregation, no division, no guessing — the contract is "the parser never silently mis-attributes a transaction." Also covers foreign-currency, multi-tranche price disagreement, and missing-required-field cases.

### D-YSYM — Yahoo symbol mapping

**Recommendation:** parser writes bare LSE ticker (`CHH`). Stage 3 fetcher maps to `CHH.L` when calling Yahoo. `tickers_meta.yahoo_symbol` will hold the divergence if any (populated in Stage 3).

### D-SCOPE — LSE main + AIM only

**Recommendation:** confirm. Off-scope filings (Aquis, certain ETFs) discarded silently at the index level.

---

## Per-file structure (pseudocode level)

### `.scripts\scrape_investegate.py`

- Module-level constants: Investegate base URL, index URL template, User-Agent, politeness sleep range, cache and progress paths (all via `Path(__file__).resolve().parent`).
- `_fetch(url) -> str` — polite GET via `urllib.request`, sets User-Agent and `Accept-Encoding: gzip, deflate`, handles gzip-decoded responses, decodes UTF-8 via `<meta charset>` sniffing, follows redirects up to 5 hops, sleeps the politeness interval before returning. Raises `RateLimitError` on 429/503 after backoff, `FetchError` on other non-2xx.
- `check_robots() -> None` — fetches and parses `/robots.txt` via `urllib.robotparser`. Raises `RobotsBlockedError` if our planned index path is disallowed. Called once at run start.
- `iter_index(start_date, end_date) -> Iterator[dict]` — walks the Director-Deals index, page by page. Yields lightweight dicts `{rns_id, url, headline, ticker_hint, announced_at}`. Stops cleanly when it reaches a page outside the window.
- `fetch_filing(rns_id, url) -> Path` — returns cache path. Cache hit returns immediately. Otherwise fetches, writes atomically (`.tmp` then `os.replace`), returns the path.
- `load_cached(rns_id) -> str | None` — reads cached HTML if present, else `None`.
- `update_progress(rns_id, window_start, window_end) -> None` — writes `_scrape_progress.json` atomically.
- `load_progress() -> dict | None` — reads progress file if present.
- `_filter_lse_aim(index_row) -> bool` — D-SCOPE gate.

Idempotency: every cache write is atomic. Progress file updated only after a filing is successfully written to cache *and* attempted-parsed.

### `.scripts\parse_pdmr.py`

Module-level regex constants:
- `_EMBEDDED_DATE_RE` — handles `27 April 2026`, `27th April 2026`, `April 27, 2026`, `April 27th, 2026`, `2026-04-27`, `27/04/2026`. Ordinal suffix `(?:st|nd|rd|th)?` built in from day one.
- `_DATE_FMTS` — list of `strptime` format strings, tried in order.
- `NUMBER_RE` — captures prices and values. Recognises `£`, `GBp`/`p` (pence), bare currency. Detects (does not coerce) `$`, `USD`, `EUR`, `€`, `CHF`, `JPY` → foreign-currency warning.
- `_BUNDLED_PDMR_RE` — detects "Notification 1 of N" / numbered PDMR sections.
- `_TYPE_KEYWORDS` — maps phrasing to one of `BUY/SELL/SELL_TAX/EXERCISE/GRANT/SIP`. Conservative.

Functions:
- `html_to_text(html: str) -> str` — strips HTML via `html.parser.HTMLParser`. Preserves paragraph breaks and table cell separators. Decodes HTML entities.
- `_try_one_date(s)` — strips ordinal suffix, tries each format in `_DATE_FMTS`, returns ISO `YYYY-MM-DD` on success.
- `parse_iso_date(text)` — public. Finds all `_EMBEDDED_DATE_RE` matches, parses each, returns the latest valid date.
- `_bundled_name_warning(text)` — returns enriched warning string (with named PDMR list and roles) when `_BUNDLED_PDMR_RE` fires, else None.
- `_extract_ticker(text, headline)` — pulls bare LSE ticker. Prefers headline.
- `_extract_director(text)` — returns (name, role).
- `_parse_price_vol(text)` — returns `(price_gbp, shares, warnings)`. Handles pence-to-pounds. Returns `(0, 0, ['foreign_currency'])` on non-GBP. Returns `(0, 0, ['multiple_distinct_prices'])` on multi-tranche disagreement.
- `_classify_type(text)` — returns the type and any warnings.
- `parse_announcement(html, url, rns_id, announced_at)` — public entry. Orchestrates: `html_to_text` → bundled-name check → extraction → required-field validation → fingerprint computation. Returns `(extracted_list, warnings_list)`. Dict shape matches `transactions` columns exactly.

CLI `__main__`: `python .scripts\parse_pdmr.py --rns-id 9540067 --html-path ...` prints parse result.

### `.scripts\run_scrape.py`

CLI via `argparse`:
- `--days N` — scrape last N days; default 60.
- `--from YYYY-MM-DD --to YYYY-MM-DD` — explicit window, overrides `--days`.
- `--rns-id ID` — re-parse a single cached filing.
- `--dry-run` — parse what's cached, write nothing.
- `--verbose` — verbose logging.

Main flow:
1. Resolve window.
2. If `--rns-id`: load cached HTML, parse, print, return.
3. Call `scrape_investegate.check_robots()`. Abort if blocked.
4. Open DB via `db.connect()`.
5. Load existing `_pending_review.json` if present.
6. Iterate `iter_index(start, end)`:
   - Skip if not LSE/AIM (D-SCOPE).
   - Fetch (or hit cache).
   - Parse.
   - On clean parse: upsert into `transactions` via `INSERT ... ON CONFLICT(fingerprint) DO UPDATE`. `first_seen` set on insert, untouched on conflict. `last_seen` always `db.iso_now()`. `seen_count` increments on conflict. `cluster_id` and `first_time_buy` left at defaults.
   - On warnings: append to pending list keyed by `rns_id`.
   - Update progress after each filing.
7. Write `_pending_review.json` atomically.
8. Print summary: filings seen, clean parses, pending parses, % pending. Warn loudly if pending % ≥ 30.

### `.scripts\test_stage_02.py`

Mirrors Stage 1's pattern: monkey-patch `db.DB_PATH` to temp dir, exercise the real parser + orchestrator paths against fixtures, clean up in `finally`. See smoke-test table below.

### `.scripts\fixtures\README.md`

Per fixture: filename, source URL, retrieval date, filing type, what the test asserts, notes.

---

## Smoke-test cases

| # | What | What it asserts |
|---|---|---|
| 1 | `parse_iso_date("27th April 2026")` | Returns `"2026-04-27"` (ordinal supported) |
| 2 | `parse_iso_date("1st May 2026")` | Returns `"2026-05-01"` |
| 3 | `parse_iso_date("April 27th, 2026")` | Returns `"2026-04-27"` (American ordinal) |
| 4 | `parse_iso_date("27 April 2026")` | Returns `"2026-04-27"` (no-ordinal regression) |
| 5 | `parse_iso_date("28 and 30 April 2026")` | Returns `"2026-04-30"` (latest-wins) |
| 6 | `NUMBER_RE` on GBP variants | Matches `£1,234.56`, `50p`, `GBp 50` |
| 7 | `NUMBER_RE` on foreign currency | Detects `$`, `EUR`, `€`, returns flagged |
| 8 | `_bundled_name_warning` on synthetic bundled | Returns non-None warning with PDMR names |
| 9 | `_bundled_name_warning` on single-PDMR | Returns None |
| 10 | `html_to_text` on small HTML fixture | Returns clean text with `\n` paragraph breaks |
| 11 | Parse `clean_buy_*.html` | One extracted dict, type=BUY, all required fields present |
| 12 | Parse `clean_sell_*.html` | One extracted dict, type=SELL |
| 13 | Parse `bundled_pdmr_*.html` | Returns `([], [warning])`; warning lists named PDMRs |
| 14 | End-to-end orchestrator against temp DB | Exactly one row written with correct fingerprint |
| 15 | Re-run end-to-end | `seen_count` increments, no second row created |
| 16 | Pending JSON shape | `{generated_at, count, items}`; items keyed by rns_id |
| 17 | After tests | Live `.data/directors.db` untouched (mtime check) |

---

## Edge cases

**Windows paths** via `pathlib.Path`. Cache filenames use only the rns_id, never the URL slug. **Ordinal-suffix dates** stripped via regex before `strptime`. **Bundled-name filings** refused with enriched warning per D-STRICT. **Foreign currency** detected but not coerced. **Pence vs pounds**: `50p` → `0.50`. **Multi-tranche** with disagreement → pending. **HTML encoding**: charset sniff → UTF-8 → latin-1 fallback. **Gzip** decoded transparently. **Resumable scrape** via `_scrape_progress.json` updated after each filing. **Cache stale** doesn't apply (RNS filings don't change). **Pagination changes** raise clear error rather than silent zero. **429** → backoff. **Redirects** followed up to 5 hops; original rns_id used for cache filename, final URL stored on the row. **User-agent rejection** → abort with diagnostic message.

---

## Rollback

Stage 2 touches no existing file. To undo:

1. Delete `.scripts\_scrape_cache\`.
2. Delete `.scripts\_scrape_progress.json`, `.scripts\_pending_review.json`.
3. Delete `.scripts\scrape_investegate.py`, `.scripts\parse_pdmr.py`, `.scripts\run_scrape.py`, `.scripts\test_stage_02.py`.
4. Delete `.scripts\fixtures\`.
5. Optional: clear Stage 2 rows from DB: `DELETE FROM transactions WHERE first_seen >= '<stage-2-start-date>'`.

Stage 1 files (`db.py`, `db_schema.sql`, `directors.db`) untouched.

---

## Acceptance criteria

1. `python .scripts\run_scrape.py --days 60` exits 0 on a clean machine.
2. After the first run, `transactions` table contains ≥50 rows.
3. Five randomly-selected rows can be opened in a browser via their `url` and match the row.
4. `_pending_review.json` contains fewer items than 30% of total LSE/AIM filings seen.
5. Second consecutive run adds no new rows (only `last_seen` / `seen_count` updated). Idempotency verified.
6. `python .scripts\run_scrape.py --rns-id <known-id>` re-parses a single cached filing without re-fetching.
7. `python .scripts\test_stage_02.py` exits 0 and prints `17 passed, 0 failed`.
8. Test exits 0 on a second consecutive run.
9. No third-party package added; `requirements.txt` unchanged (or doesn't exist).
10. `.data\directors.db` schema unchanged from Stage 1.
11. Only the seven Stage-2 files in the table at the top exist in `.scripts/`.

---

## Effort estimate

### Build effort (one weekend, ~8–10 hours, ~190k Sonnet tokens)

| Phase | Time | Tokens (Sonnet) |
|---|---|---|
| `parse_pdmr.py` (regex helpers + parse_announcement + SIP/GRANT detection) | 90 min | ~35k |
| `scrape_investegate.py` (fetch, robots, iter, archive pagination, cache, progress) | 90 min | ~30k |
| `llm_parser.py` (Anthropic SDK call + prompt + response validation) | 60 min | ~20k |
| `llm_cost.py` (ledger + ceiling guard) | 30 min | ~8k |
| `schema_migrations/002_add_parser_source.sql` + db.py migration logic | 20 min | ~5k |
| `run_scrape.py` (daily-incremental orchestrator with `--no-llm` and `--llm-budget-usd`) | 60 min | ~18k |
| `backfill_filings.py` (multi-year orchestrator with resumability) | 75 min | ~22k |
| 4 real fixtures + `fixtures/README.md` | 40 min | ~6k |
| `test_stage_02.py` (20+ cases inc. LLM mock + SIP fixture) | 75 min | ~25k |
| First end-to-end daily-incremental run + debug | 75 min | ~15k |
| Spot-check 5 rows vs source URLs | 20 min | ~3k |
| Verify QA gate (pending % < 30 regex-only, < 10 with LLM) | 15 min | ~2k |
| **Build total** | **~10 hours** | **~190k** |

### Operational effort (one-time, post-build)

| Phase | Time | Cost |
|---|---|---|
| Multi-year backfill scrape (2024-01-01 → today, ~17–25k filings) | 5–7 hours of wall clock | $0 |
| Multi-year backfill LLM calls (~12% × ~21k = ~2,500 calls) | (overlaps with above) | ~$20–$30 |
| Manual review of post-LLM pending queue | 30 min | $0 |
| **Operational total** | **~6 hours wall clock** | **~$25** |

---

## Out of scope for Stage 2

- Price fetching, Yahoo integration (Stage 3).
- Populating `tickers_meta` (Stage 3).
- FX rates (v2 — non-GBP filings still route to pending).
- Multi-tranche price fan-out (v2 — multi-tranche disagreement still routes to pending).
- Dashboard/JSON export (no dashboard yet in the new build).
- Cluster detection and `first_time_buy` derivation (Stage 4).
- Signal evaluation (Stage 4).
- Paper-trade row creation (Stage 5).
- Integration with legacy `update.py` / `refresh.py` / `Open Dashboard.bat`.

---

## Investegate URL shapes (verified 2026-05-12 via one fetch)

- **Index URL:** `https://www.investegate.co.uk/category/directors-dealings`
- **Filing URL pattern:** `https://www.investegate.co.uk/announcement/{source}/{slug}/{headline-slug}/{rns_id}` where `{source}` is `rns` or `prn`, `{slug}` is `<lowercased-company-name>--<lowercased-ticker>` (the company and ticker are joined by a double-hyphen), and `{rns_id}` is the numeric Investegate RNS ID (last URL segment).
- **Sample filings observed:**
  - `https://www.investegate.co.uk/announcement/rns/rolls-royce-holdings--rr./director-pdmr-shareholding/9542322`
  - `https://www.investegate.co.uk/announcement/rns/schroders--sdr/director-pdmr-shareholding/9540067` (a known bundled-PDMR Schroders filing — perfect candidate for the bundled-test fixture; this is the one called out in spec 02)
  - `https://www.investegate.co.uk/announcement/rns/chesnara--csn/director-pdmr-shareholding/9540499`
  - `https://www.investegate.co.uk/announcement/prn/mondi--mndi/director-pdmr-shareholding/9541563`
- **Index row shape:** table with columns `Time | Source | Company | Announcement`. Company cell carries the ticker in brackets after the company name. The Announcement cell has the headline (used to classify type by keyword). The index controls a "Show 50/100/200/300 entries" selector — start with 300 to minimise pagination round-trips.
- **Headlines that indicate PDMR-style filings:** "Director/PDMR Shareholding" (most common), "PDMR Dealing", "Notification of Transactions of Directors & PDMRs", "Person Closely Associated with PDMR shareholding", "Director/PDMR Shareholding - acquisition of shares". Headlines like "Grant of Conditional Share Awards", "Long Term Incentive Plan - Grant of Awards", "Grant of Share Options" → classify as `GRANT`. "Vesting of Previously Granted RSUs and TVR" / "Security Based Compensation, Option Exercise / TVR" → `EXERCISE`. "EBT Share Purchase" / "Market Purchase of Shares for EBT" → company-level Employee Benefit Trust activity, **not** a PDMR transaction; skip these silently rather than route to pending (they're out of scope by design, not ambiguous).
- **Pagination/history:** the index page shows the most recent ~300 entries when "Show 300 entries" is selected. For history beyond that, the build agent must investigate the `/announcement-archive` page or the Advanced Search — open question, see below.

## Open questions for Rupert (v1.1)

Most defaults from v1.0 are confirmed. Only one question is genuinely open in v1.1:

### Q-ENV — How does the build agent get your Anthropic API key?

Three reasonable patterns, each with trade-offs:

- **(a) `.env` file in the project root (recommended).** Build agent creates a stub `.env.example` (with `ANTHROPIC_API_KEY=sk-ant-...`); Rupert renames to `.env` and pastes his real key. `.env` is gitignored. `llm_parser.py` reads via a tiny stdlib `.env` loader (~10 lines) so no extra package needed. **Pro:** clean, single source of truth, easy to rotate. **Con:** the key sits in plain text on disk.

- **(b) Environment variable, set in PowerShell each session.** Rupert runs `$env:ANTHROPIC_API_KEY="sk-ant-..."` before running scripts. `llm_parser.py` reads via `os.environ`. **Pro:** no key on disk. **Con:** has to re-set every PowerShell session, easy to forget; less convenient for the long backfill run.

- **(c) Windows Credential Manager + stdlib `keyring`-equivalent.** Most secure but `keyring` is a third-party package; would violate the stdlib-only constraint.

Plan defaults to **(a)** unless you override. Reply with `a`, `b`, `c`, or any other override.

### Operational decisions assumed (confirm or override)

- **D-LLM = include now** ✅ (Rupert 2026-05-12)
- **D-WIN = backfill 2024-01-01 → today** ✅ (Rupert 2026-05-12)
- **Fixtures = auto-pick** ✅ (Rupert 2026-05-12). Schroders 9540067 = bundled; Barclays 9564893 = SIP; clean BUY + clean SELL auto-selected from first smoke scrape.
- **D-PEND** = JSON pending file ✅ (default)
- **D-CACHE** = `.scripts\_scrape_cache\{rns_id}.html` ✅ (default)
- **D-RNSID** = use Investegate numeric ID (confirmed via URL inspection 2026-05-12); `h-` hash fallback unused in normal cases ✅
- **D-STRICT** = refuse bundled-PDMR ✅ (default, inherits spec 02 D1)
- **D-YSYM** = bare LSE ticker (no `.L`) ✅ (default)
- **D-SCOPE** = LSE main + AIM only ✅ (default)
- **D-POLITE** = 0.8s ± 0.2s, robots.txt check at start, UA = `DirectorsDealings-Research/0.2 (+contact: rspiegelberg@gmail.com)` ✅ (default)
- **QA gate denominator** = LSE/AIM filings only ✅ (default)
- **`.gitignore`** = build agent creates/updates with `.data/`, `.scripts/_scrape_cache/`, `.scripts/_*.json`, `.env` ✅ (default)
- **LLM budget default** = $50 per run; abort cleanly preserving progress when ceiling hit ✅ (default)
2. **D-LLM sign-off:** confirm defer.
3. **D-PEND sign-off:** confirm JSON file.
4. **D-WIN sign-off:** confirm 60-day smoke only.
5. **D-POLITE — UA contact email.** `rspiegelberg@gmail.com` ok?
6. **D-RNSID — hash prefix.** `h-` acceptable?
7. **D-SCOPE — LSE main + AIM only.** Any other venues?
8. **QA gate denominator.** LSE/AIM filings only (recommended), or all filings the scraper saw?
9. **Fixture sourcing.** Hand-pick or auto-pick the first three matching candidates from the smoke scrape?
10. **`.gitignore` update.** Build agent updates it for `_scrape_cache/`, `_scrape_progress.json`, `_pending_review.json`?
