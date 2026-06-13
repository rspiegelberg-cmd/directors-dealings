# Stage 3 — Historic prices + sector benchmarks

**Status:** Plan v1.3 — 2026-05-13. ALL decisions locked. Close + volume only. 13-month window default. Per-ticker smart logic added: NEW tickers (no rows in `prices` yet) auto-fetch full 13 months; EXISTING tickers fetch incrementally from MAX(date)+1 to today. Same script does initial backfill AND daily refresh — Rupert just runs it after every daily scrape.
**Owner:** Rupert
**Target ship:** One focused session (~3.5–4 hours of build), plus ~12 minutes of operational backfill wall clock.
**Source:** `docs/specs/03-phase-1-backfill-storage.md` (P1-5 OHLCV backfill blueprint), `docs/specs/05-phase-3-signal-engine.md` (CAR / benchmark / AIM consumer), `docs/specs/stage-01-plan.md` (locked DB schema, decisions S1–S5), `docs/specs/stage-02-plan.md` (D-YSYM bare-LSE ticker convention).
**Author:** Planning pass, 2026-05-13.

---

## Goal

Stage 3 populates the price layer so Stage 4's signal engine can compute T+1 / T+21 / T+90 / T+252 cumulative abnormal returns (CAR) vs a sector-matched benchmark, AND so Stage 5's per-company dashboard pages can render a price chart with daily volume bars and director-transaction dots overlaid.

Scope (locked v1.1):
- Pull **adjusted close + daily volume only** from Yahoo's anonymous chart API for every distinct ticker in the `transactions` table.
- Skip open, high, low — Stage 4 only needs close-to-close returns, and Stage 5's chart is a simple line + volume bar (no candlesticks). The `prices.open / high / low` columns stay in the schema but are written as NULL.
- Write rows into the existing `prices` table (bare-LSE symbol, no `.L`).
- Resolve each ticker to a sector / sector-benchmark / AIM flag via a curated static map (with `^FTAS` as the universal fallback).
- Pull the same close + volume for each benchmark series.
- Persist all of it through `db.connect()` and `db.iso_now()` from Stage 1.

Stdlib only — no `yfinance`, no `pandas`, no `requests`. No schema changes. Idempotent on every re-run. Resumable across crashes.

Stage 3's payoff: Stage 4 (CAR + costs + signal evaluation) gets the close prices it needs, and Stage 5 (dashboard) gets close + volume per ticker to draw the per-company chart with overlaid director-transaction markers.

---

## Files to create

| Path (absolute Windows) | Purpose |
|---|---|
| `C:\Dev\DirectorsDealings\.scripts\fetch_prices.py` | Single-ticker Yahoo OHLCV fetcher. Handles `.L` suffix add/strip, GBp -> GBP normalisation, per-ticker JSON cache (20h TTL), polite rate limit, 429 backoff, 404 -> `delisted` flag. |
| `C:\Dev\DirectorsDealings\.scripts\backfill_prices.py` | Orchestrator. Reads distinct tickers from `transactions`, drives `fetch_prices.py`, writes rows to `prices` via `INSERT OR IGNORE`. Resumable via `_price_progress.json`. CLI: `--from`, `--to`, `--ticker`, `--dry-run`, `--rate-limit`, `--verbose`. |
| `C:\Dev\DirectorsDealings\.scripts\fetch_sectors.py` | Ticker -> sector/benchmark/AIM resolver. Reads `sector_map.csv` + `benchmark_symbols.json`. Writes `tickers_meta`. `^FTAS` fallback. |
| `C:\Dev\DirectorsDealings\.scripts\backfill_benchmarks.py` | Fetches the sector indices themselves (every distinct `^…` symbol referenced by `tickers_meta.benchmark_symbol`). Writes to `prices` with `^…` symbol as-is. |
| `C:\Dev\DirectorsDealings\.scripts\sector_map.csv` | Curated static lookup: `ticker,sector,benchmark_symbol,is_aim`. Bundled in repo. |
| `C:\Dev\DirectorsDealings\.scripts\benchmark_symbols.json` | Map FTSE sector name -> Yahoo symbol. |
| `C:\Dev\DirectorsDealings\.scripts\test_stage_03.py` | Smoke + unit + integration test, ≥15 cases. Mocks Yahoo via `unittest.mock.patch`. |

Runtime-created (gitignored): `_price_cache/{ticker}.json` (20h TTL) and `_price_progress.json` for resumability.

---

## Decision points

### D-YAHOO-ENDPOINT — chart endpoint host
**Recommendation: `query1.finance.yahoo.com/v8/finance/chart/{symbol}` (anonymous).** Explicit `period1/period2` epochs (deterministic). UA = `DirectorsDealings-Research/0.3 (+contact: rspiegelberg@gmail.com)`. Use `indicators.adjclose[0].adjclose[]` for the close (split/div-adjusted).

### D-YAHOO-AUTH — anonymous chart vs crumb-authenticated quoteSummary
**Recommendation: anonymous chart only.** Crumb dance is fragile; we don't need it for OHLCV. Sector mapping comes from `sector_map.csv` instead.

### D-SECTOR-SOURCE — where ticker→sector map comes from
**Recommendation: static CSV bundled in the repo,** with `^FTAS` fallback for unmapped tickers. Inspectable in a text editor. A future stage can upgrade to live Yahoo lookups without schema changes.

### D-BENCHMARK-MAP — sector → FTSE Yahoo symbol
**Recommendation: hand-curated `benchmark_symbols.json`** for 8–12 most common LSE sectors (Financials → `^FTNMX5710`, Energy → `^FTNMX0500`, etc.), `_default: ^FTAS`. Build agent verifies each symbol via one-shot probe before commit; dead symbols pruned (forcing fallback).

### D-AIM-FLAG — AIM detection
**Recommendation: `is_aim` column in `sector_map.csv`,** seeded by build agent from the LSE published AIM list. Penalising bias (errant AIM=0 charges stamp duty in Stage 4) is the safer default than flattering bias.

### D-CACHE — per-ticker cache file
**Recommendation: 20h TTL.** Stored as `_price_cache/{ticker}.json` with `{ticker, yahoo_symbol, currency, fetched_at, window, rows}`. Currency stored as-received from Yahoo; pence normalisation happens at write-to-DB time.

### D-FAIL-LOUD — Yahoo 404 / empty / broken
**Recommendation: mark in `tickers_meta` as delisted, log, continue.** `fetch_prices.py` returns `DelistedTicker` sentinel; `fetch_sectors.py` writes row with `benchmark_symbol = NULL`. Stage 4 filters these out via `benchmark_symbol IS NULL`.

### D-RATE-LIMIT-BUDGET — polite delay
**Recommendation: 0.5s default.** ~100 tickers × 0.5s = ~50s. CLI `--rate-limit FLOAT` for overrides. 429 backoff: 30s → 60s → 120s, max 3 retries.

### D-FX — pence normalisation
**Recommendation: divide by 100 when `currency=='GBp'`.** Treat USD/EUR depositary lines as `unsupported_currency` (same outcome as delisted).

### D-WINDOW — backfill window (LOCKED)
**Rupert's call (2026-05-13): 13 months back from today (2025-04-13 → 2026-05-13).** Reasoning: gives the oldest filings (May 2025) enough forward-price runway to compute T+252 returns, plus a small safety margin for transaction-vs-announcement date gap. Tighter than the 24-month nice-to-have, aligns with "only download what we need" discipline.

---

## Smoke-test cases (≥15)

| # | What | What it asserts |
|---|---|---|
| 1 | `fetch("BARC", from, to)` mocked Yahoo response, currency=GBP | status=ok, one row, yahoo_symbol='BARC.L' |
| 2 | Re-call cache present (1h old) | cache_hit=True, zero network calls |
| 3 | Re-call cache present, window extended | cache_hit=False, one network call |
| 4 | Cache 21h old | cache_hit=False, TTL respected |
| 5 | Mock 404 | status='delisted', no exception bubbles |
| 6 | Mock 429 twice then 200 | Backoff schedule respected, final fetch ok |
| 7 | Mock GBp, close=21420 | Stored close=214.20 (÷100) |
| 8 | Mock GBP, close=214.20 | Stored close=214.20 (no division) |
| 9 | `resolve("BARC")` with map → Financials | benchmark_symbol='^FTNMX5710' |
| 10 | `resolve("UNKNOWN")` no map | benchmark_symbol='^FTAS' (fallback) |
| 11 | `resolve("AIMTKR")` with is_aim=1 | tickers_meta.is_aim==1 |
| 12 | `backfill_benchmarks.py` end-to-end mocked `^FTAS` | COUNT(*) WHERE ticker='^FTAS' > 0 |
| 13 | `backfill_prices.py` run twice | Row count unchanged (idempotent) |
| 14 | `--from 2026-01-01 --to 2026-01-05` (3 trading days) | Exactly 3 rows |
| 15 | Killed mid-list + `--resume` | completed_tickers skipped on re-run; no dupes |
| 16 | currency='USD' | status='unsupported_currency', no rows written |
| 17 | `yahoo_symbol_for("^FTAS")` | Returns "^FTAS" unchanged |
| 18 | `db_ticker_for("BARC.L")` | Returns "BARC" |

---

## Edge cases

Windows paths via `pathlib.Path`. GBp normalisation uniform per fetch. Yahoo 429 → exponential backoff. Delisted → `tickers_meta` row with NULL benchmark. USD/EUR depositary lines → unsupported_currency. Weekends/holidays → Yahoo omits, we trust. Yahoo adjusted close handles splits/dividends. AIM list staleness (rare events; quarterly refresh). `.L` suffix collisions (benchmarks start with `^`, skip suffix). Atomic writes for cache + progress (tempfile + os.replace). No concurrent runs.

---

## Rollback

Delete the 7 new files + `.scripts/_price_cache/` + `.scripts/_price_progress.json`. Optional `DELETE FROM prices;` + `DELETE FROM tickers_meta;`. Stage 1 + 2 untouched.

---

## Acceptance criteria

1. `python .scripts\backfill_prices.py --from 2025-05-13 --to 2026-05-13` exits 0.
2. ≥85% ticker coverage in `prices`.
3. Each non-delisted ticker has ≥220 trading days.
4. `tickers_meta` row for every distinct ticker in `transactions`.
5. ≥5 benchmark series in `prices` (including `^FTAS`).
6. `python .scripts\test_stage_03.py` exits 0, ≥15 PASS.
7. Stage 1 + Stage 2 smoke tests still pass.
8. `requirements.txt` unchanged.
9. Only 7 new Stage-3 files in `.scripts/`.
10. Idempotent re-run produces zero row delta.
11. `prices.ticker` for stocks is bare LSE (no `.L`); benchmarks start with `^`.

---

## Effort estimate

| Phase | Time | Tokens (Sonnet) |
|---|---|---|
| `fetch_prices.py` (Yahoo + cache + GBp + 404/429) | 55 min | ~22k |
| `backfill_prices.py` (orchestrator + resume + CLI) | 35 min | ~14k |
| `fetch_sectors.py` (CSV/JSON loader + upsert) | 25 min | ~10k |
| `backfill_benchmarks.py` (thin orchestrator) | 20 min | ~7k |
| `sector_map.csv` (seed from transactions + hand-fill) | 25 min | ~5k |
| `benchmark_symbols.json` (verify each symbol) | 20 min | ~4k |
| `test_stage_03.py` (15+ cases) | 60 min | ~22k |
| End-to-end run + debug | 25 min | ~6k |
| Spot-check prices + AIM flags | 20 min | ~4k |
| QA gate verification | 20 min | ~3k |
| **Build total** | **~3 h 45 m** | **~97k** |

**Operational total: ~12 min wall clock, $0** (Yahoo anonymous API is free at our scale).

---

## Out of scope (but flagged as Stage 5 dependencies)

- `transactions.parser_source` updates (Stage 2 owns).
- Signal evaluation, cluster detection, CAR computation (Stage 4).
- **Per-company price chart with director-transaction overlays (Stage 5).** Stage 3 produces the data: close + volume per ticker. Stage 5 will render the Chart.js line + volume bar + buy/sell-marker dots on each company page. Data shape needed: `SELECT date, close, volume FROM prices WHERE ticker = ? ORDER BY date` joined with `SELECT date, type, shares, value, director FROM transactions WHERE ticker = ? ORDER BY date`.
- **Daily share-trading volume bars on the chart (Stage 5).** Comes from `prices.volume` which Stage 3 populates.
- Conviction-weighted position sizing (Phase 5 territory).
- Pre-2025-05-13 prices.
- US tickers / foreign exchanges.
- Real-time intraday quotes.
- Yahoo crumb endpoints (deferred).
- Live `quoteSummary` sector mapping (deferred).
- `market_cap_gbp` population (column exists, NULL in v1).
- Concurrent fetching (single-threaded by design).
- Special-case corporate actions beyond Yahoo's adjusted-close handling.

---

## Locked decisions (v1.2, 2026-05-13)

All v1.0 open questions resolved. Defaults from the plan apply unless noted:

1. **Q-SECTORMAP-SEED** = build agent hand-curates from transactions ticker list (default)
2. **Q-RATE-LIMIT** = 0.5s polite delay (default)
3. **Q-DELISTED-RECORD** = `tickers_meta` row with NULL benchmark (default; schema frozen)
4. **Q-FX-SCOPE** = normalise GBp→GBP only; USD/EUR = unsupported_currency (default)
5. **Q-CACHE-TTL** = 20 hours (default)
6. **Q-WINDOW-DEFAULT** = **13 months back from today (2025-04-13 → 2026-05-13)** — Rupert's call
7. **Q-BENCHMARK-COVERAGE-TARGET** = ≥5 benchmark series (default; raise to "one per populated sector" if Stage 4 needs)
8. **Q-AIM-LIST-SOURCE** = seed `is_aim` from LSE AIM list (default)
9. **Data scope** = close + volume only (Rupert's call). Skip open/high/low; columns stay nullable.
10. **Stage 5 chart hooks** = data layer must support price line + daily share-volume bars + director-transaction marker dots. Stage 3 produces the data; rendering is Stage 5.
