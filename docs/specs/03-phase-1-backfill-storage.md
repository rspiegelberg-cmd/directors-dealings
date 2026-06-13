# Spec: Phase 1 — Backfill + storage

**Status:** Approved v1.1 — decisions locked 2026-05-05, ready to execute. **D1 = Option A** (dual-write). **DB path = `.data/directors.db`.**
**Owner:** Rupert
**Target ship:** Weeks of 2026-05-12 / 2026-05-19 (two focused weekends)
**Source:** `backlog.md` rows P1-1 through P1-6; `Directors-Dealings-PM-Brief.docx` Phase 1
**Author:** PM/back-end planning pass, 2026-05-05

---

## Goal

Move from a single-file JSON store with two weeks of live data to a SQLite-backed historical archive holding ~5 years (~50,000 transactions) plus daily prices for every ticker that has ever appeared in a filing — without breaking the existing dashboard read path.

Phase 1's value is purely setup. There's no user-visible change at the end of this phase. The payoff is in Phase 3 when the backtest engine finally has data to run against.

## What's already in the codebase (verified 2026-05-05)

**`dealings-log.json`** is the canonical transaction store. Schema v1, currently 207 transactions. Each row has:

- Identity: `fingerprint` (= `date|ticker|director|type|shares`), `first_seen`, `last_seen`, `seen_count`
- Transaction core: `date`, `ticker`, `company`, `director`, `role`, `type`, `shares`, `price`, `value`
- Filing meta: `context`, `url`, `announced_at`
- Derived: `cluster_id`, `first_time_buy` (computed by `detect_clusters.py`)

**`prices.json`** is the aggregate price store. Top-level `tickers` dict, per ticker `yahoo_symbol`, `timestamps[]`, `closes[]`. **Closes only — not OHLCV.** Yahoo's `chart` endpoint does return OHLCV but the current fetcher only persists closes.

**`.price_cache/{TICKER}.json`** is per-ticker cache, 6mo of data, 20h TTL.

**`.scripts/state.json`** tracks `last_investegate_cursor`, `last_run_at`, `last_price_refresh`.

**`refresh.py`** does dedup-and-upsert in Python by mutating the JSON dict in memory. The `upsert_log()` function at line 102 is the canonical write path — every change to `dealings-log.json` goes through it.

**Scraper (`scrape_investegate.py`)** walks the Investegate index forward from the cursor. Uses stdlib only.

---

## Decisions (pre-locked)

I've made the calls that are pure engineering preference; one is loaded enough that I want your sign-off.

### Pre-locked, no question

- **DB location:** `.data/directors.db` — new hidden directory, separate from `.scripts/` (which is tools) and `.price_cache/` (which is a derived cache). Safe to delete and rebuild from JSON + cache.
- **No third-party deps:** stdlib `sqlite3` only, per the back-end-engineer profile. No SQLAlchemy, no sqlite-utils.
- **Dashboard read path unchanged:** `directors-dealings-dashboard.html` keeps reading from `dealings-log.json`. SQLite becomes the canonical store; JSON becomes a derived export written on every refresh. Zero front-end change.
- **Schema design specs OHLCV:** the `prices` table has open/high/low/close/volume columns, but Phase 1's initial backfill only populates `close` and `volume` (since that's what Yahoo's chart endpoint returns by default). Future fetchers can fill the rest without schema migration.
- **Stdlib `sqlite3` UPSERT:** `INSERT INTO transactions (...) VALUES (...) ON CONFLICT(fingerprint) DO UPDATE SET ...` mirrors the existing `upsert_log()` semantics 1:1.
- **Backfill walks back in years, not all-at-once:** scrape Investegate one year at a time, cache HTML, persist after each year. Resumable across runs and crashes.

### Decision D1 (needs your sign-off) — Migration strategy

How do we go from "JSON is canonical" to "SQLite is canonical" without putting your data at risk?

**Option A — Dual-write, then flip (recommended).** `refresh.py` writes to both stores during a transition period. JSON stays the source of truth for the dashboard. SQLite is read-only mirror. Once we've verified parity on every refresh for, say, a week, flip the flag and JSON becomes the derived export.
**Pros:** lowest risk. If SQLite logic has a bug, the JSON copy is untouched and the dashboard keeps working.
**Cons:** a few weeks of running two stores. Slightly slower refresh.

**Option B — Big bang.** Run migration, verify parity once, switch refresh.py to read/write SQLite, regenerate JSON from SQLite on every refresh. Drop dual-write.
**Pros:** cleaner. Done in one weekend.
**Cons:** if SQLite write logic has a subtle bug we don't catch in the parity test, the JSON export will inherit it. The fingerprint key makes this hard to mess up but not impossible.

I recommend **Option A**. The risk asymmetry is real — Phase 1 has no user-visible benefit, so any bug here is pure regression. Worth the extra few weeks of dual-write.

---

## Schema (locked unless D1 changes)

```sql
-- transactions: 1:1 mirror of dealings-log.json, fingerprint as PK
CREATE TABLE IF NOT EXISTS transactions (
    fingerprint     TEXT    PRIMARY KEY,         -- date|ticker|director|type|shares
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    seen_count      INTEGER NOT NULL DEFAULT 1,
    date            TEXT    NOT NULL,            -- YYYY-MM-DD
    ticker          TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    director        TEXT    NOT NULL,
    role            TEXT,
    type            TEXT    NOT NULL,            -- BUY|SELL|SELL_TAX|EXERCISE|GRANT|SIP
    shares          INTEGER NOT NULL,
    price           REAL    NOT NULL DEFAULT 0,  -- 0 means undisclosed
    value           REAL    NOT NULL DEFAULT 0,
    context         TEXT,
    url             TEXT,
    announced_at    TEXT,                        -- "YYYY-MM-DD HH:MM UTC"
    cluster_id      TEXT,
    first_time_buy  INTEGER NOT NULL DEFAULT 0   -- 0/1, derived
);
CREATE INDEX IF NOT EXISTS idx_tx_ticker_date ON transactions (ticker, date);
CREATE INDEX IF NOT EXISTS idx_tx_director ON transactions (director);
CREATE INDEX IF NOT EXISTS idx_tx_type_date ON transactions (type, date);

-- prices: daily OHLCV per ticker. Composite PK on (ticker, date).
CREATE TABLE IF NOT EXISTS prices (
    ticker          TEXT    NOT NULL,
    date            TEXT    NOT NULL,            -- YYYY-MM-DD
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL    NOT NULL,
    volume          INTEGER,
    source          TEXT    NOT NULL DEFAULT 'yahoo',
    fetched_at      TEXT    NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices (date);

-- signals: versioned signal evaluations. Filled in Phase 3.
CREATE TABLE IF NOT EXISTS signals (
    signal_id       TEXT    NOT NULL,
    signal_version  TEXT    NOT NULL,
    fingerprint     TEXT    NOT NULL,            -- the transaction that triggered
    fired_at        TEXT    NOT NULL,
    confidence      TEXT,                        -- T1/T2/T3/T4/S1
    metadata        TEXT,                        -- JSON blob
    PRIMARY KEY (signal_id, signal_version, fingerprint),
    FOREIGN KEY (fingerprint) REFERENCES transactions(fingerprint)
);

-- backtest_runs: Phase 3 output. Empty in Phase 1.
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          TEXT    PRIMARY KEY,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    signal_id       TEXT,
    signal_version  TEXT,
    metadata        TEXT,                        -- JSON blob with config + results
    universe        TEXT,                        -- JSON: list of tickers
    period_start    TEXT,
    period_end      TEXT
);

-- meta: schema version + housekeeping. One-row table.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

The schema lives in `.scripts/db_schema.sql`. `db.py` loads and applies it idempotently on every connect.

---

## Per-item plan

### P1-2 — Schema design (build deliverable)

**Output.** `.scripts/db_schema.sql` (the SQL above) plus a short rationale doc at `specs/03a-sqlite-schema-rationale.md`.

**Token estimate.** ~15k Sonnet (mostly already drafted above).

### P1-3 — Stand up SQLite store + migrations

**Output.** `.scripts/db.py` providing:

- `DB_PATH = ROOT / ".data" / "directors.db"`
- `connect()` → returns a `sqlite3.Connection`, applies schema if not present
- `migrate(conn)` → idempotent; runs `db_schema.sql` (uses `CREATE TABLE IF NOT EXISTS` so safe to run on every refresh)
- `set_meta(conn, key, value)` / `get_meta(conn, key)`
- Small helper `iso_now()` for timestamp consistency

**Test.** `.scripts/test_p1_3_db_smoke.py` — connects, migrates, inserts a synthetic row, reads it back, deletes the test DB. Self-cleaning.

**Token estimate.** ~25k.

### P1-4 — Migrate dealings-log.json into SQLite

**Output.** `.scripts/migrate_log_to_db.py` — one-shot script:

```text
python .scripts\migrate_log_to_db.py             # dry-run report
python .scripts\migrate_log_to_db.py --apply     # actually write
python .scripts\migrate_log_to_db.py --apply --reset  # drop tables, rebuild
```

For each transaction in `dealings-log.json`, runs `INSERT … ON CONFLICT(fingerprint) DO UPDATE …`. Logs counts: inserted / updated / unchanged / skipped (with reason).

**Idempotent:** running twice produces the same DB state. Safe to re-run after parser fixes change historical rows.

**Token estimate.** ~30k.

### P1-6 — Parity test

**Output.** `.scripts/test_p1_parity.py`:

- Loads every row from `dealings-log.json` and `transactions` table.
- For each fingerprint, asserts the row exists in both stores.
- For each shared row, asserts every column matches.
- Reports any drift; exits non-zero on any mismatch.

This becomes the gating check before flipping D1 from dual-write to SQLite-canonical.

**Token estimate.** ~25k.

### P1-1 — Extend Investegate scraper to walk back to Jan 2019

**Output.** Extension to `.scripts/scrape_investegate.py` plus a new orchestrator `.scripts/backfill_filings.py`:

```text
python .scripts\backfill_filings.py --from 2025-01-01 --to 2026-04-01
python .scripts\backfill_filings.py --year 2024
```

- Walks the Investegate index in date-bucketed batches (one month or one year at a time).
- Caches each filing's HTML in `.scripts/_scrape_cache/{rns_id}.html`.
- Hands clean parses to the SQL upsert; ambiguous filings to `_pending_review.json` (same flow as the live pipeline).
- Resumable: a manifest at `.scripts/_backfill_progress.json` tracks (year, month, last_rns_id seen) so a crash mid-backfill picks up cleanly.

**Risk.** Investegate may have changed URL structure or pagination over 5 years. Mitigation: do 2025 first as a smoke test, then 2024, then walk back. Bail and ask for help if a year fails consistently.

**Token estimate.** ~80k (mostly Claude reading parser output and triaging on the fly during backfill — the actual scrape itself is deterministic).

### P1-5 — Backfill OHLCV for ~500 tickers, 5 years

**Output.** Extension to `.scripts/fetch_prices.py` plus a new `.scripts/backfill_prices.py`:

- Reads distinct tickers from `transactions` table.
- For each ticker, fetches Yahoo `range=5y&interval=1d` (returns ~1300 rows per ticker).
- Persists into `prices` table with `INSERT OR IGNORE` on `(ticker, date)`.
- Skips tickers where we already have ≥250 trading days of history.

Yahoo's response format: `chart.result[0].indicators.quote[0]` has `open`, `high`, `low`, `close`, `volume` arrays — easy to expand from current closes-only fetcher.

**Token estimate.** ~50k.

---

## Order of execution

```
P1-2 (schema design)   ──┐
                         ├──► P1-3 (db.py + smoke test)
                         │           │
                         │           ▼
                         │      P1-4 (migrate JSON into SQLite)
                         │           │
                         │           ▼
                         │      P1-6 (parity test — gating check)
                         │           │
                         │           ▼
                         │      → start dual-write in refresh.py (D1 option A)
                         │           │
                         ┴───────────┴──► P1-1 (filings backfill — runs against SQLite)
                                         P1-5 (OHLCV backfill — runs against SQLite)
```

P1-2 → P1-3 → P1-4 → P1-6 is the critical path. Until parity is green, don't touch the backfills.

---

## Out of scope for Phase 1

- Switching the dashboard to read from SQLite directly (deferred — JSON export keeps that as a Phase 1.5 polish item).
- Backfilling cluster_id and first_time_buy across the 5-year history (these are derived; Phase 3's signal engine will recompute them from the transactions table).
- Multi-exchange data (US Form 4, etc.) — explicitly v2.
- A per-row "parser_source" tag (regex vs LLM). That's a Phase 2 concern when the LLM fallback ships.

---

## Token budget rollup

| Item | Est. tokens (Sonnet) | Notes |
|---|---|---|
| P1-2 | ~15k | Schema mostly drafted in this spec |
| P1-3 | ~25k | db.py + smoke test |
| P1-4 | ~30k | Migration script |
| P1-6 | ~25k | Parity test |
| P1-1 | ~80k | Backfill filings — bulk of cost |
| P1-5 | ~50k | OHLCV backfill |
| **Total** | **~225k** | Original backlog estimate was ~310k. Lower because the schema lives in one place and dual-write minimises rework |

---

## What we need from Rupert before code changes

1. **D1 sign-off:** Option A (dual-write then flip) or Option B (big bang). Recommended: A.
2. **Confirm `.data/` is acceptable as the DB folder.** Alternative: `.scripts/directors.db`. I prefer `.data/` for separation of concerns; happy to use `.scripts/` if you'd rather keep all infrastructure in one folder.

Once those are answered, P1-2 (schema) and P1-3 (db.py) can ship in one sitting and we'll have a working empty SQLite by end-of-day.
