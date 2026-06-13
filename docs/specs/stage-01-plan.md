# Stage 1 — SQLite foundation

**Status:** Plan v1.0 — 2026-05-12. Awaiting Rupert sign-off before implementation.
**Owner:** Rupert
**Target ship:** One focused sitting (~2–3 hours).
**Source:** `docs/specs/03-phase-1-backfill-storage.md` (schema), `docs/specs/07-conviction-sizing.md` (paper_trades), `docs/specs/05-phase-3-signal-engine.md` (sector benchmark + AIM context).
**Author:** Planning pass, 2026-05-12.

---

## Goal

Stand up the empty SQLite store that every later stage will read from and write to. No scraping, no parsing, no signals, no UI. Just: a database file, a SQL sidecar, a Python connector module, a smoke test. After Stage 1, the on-disk shape is in place and Stages 2–5 can land independently against it.

This stage has zero user-visible value on its own. Its payoff is that nothing downstream has to invent storage as it goes.

---

## Files to create

| Path (absolute) | Purpose |
|---|---|
| `C:\Users\Rupert Spiegelberg\Documents\Claude\Projects\Directors Dealings\.data\directors.db` | Empty SQLite database with the Stage 1 schema applied. Created on first `connect()`. |
| `C:\Users\Rupert Spiegelberg\Documents\Claude\Projects\Directors Dealings\.scripts\db_schema.sql` | The canonical SQL — tables, indices, initial `meta` rows. Human-readable sidecar so Rupert can open it and see the whole schema in one place. |
| `C:\Users\Rupert Spiegelberg\Documents\Claude\Projects\Directors Dealings\.scripts\db.py` | Stdlib-only Python module. Exposes `DB_PATH`, `connect()`, `migrate(conn)`, `set_meta`/`get_meta`, `iso_now()`. |
| `C:\Users\Rupert Spiegelberg\Documents\Claude\Projects\Directors Dealings\.scripts\test_db_smoke.py` | Runnable smoke test against a throwaway DB. Prints `PASS` / `FAIL` lines. Self-cleaning. |

Both `.data\` and `.scripts\` are new hidden directories at the project root. `db.py` creates `.data\` on first run if it doesn't exist (`Path.mkdir(parents=True, exist_ok=True)`).

---

## Schema decisions

### Decision S1 — sector benchmarks: special tickers in `prices`, not a separate table

The signal engine (Phase 3 spec, section "Pre-locked decisions") locks `FTSE All-Share` as the global benchmark (Yahoo `^FTAS`). Rupert has chosen **sector-matched benchmarks** for the per-ticker reference series — e.g. an FTSE All-Share Financials index for a financials stock.

Two options:

- **(a) Store benchmarks as rows in `prices` with the index symbol as `ticker`** (e.g. `^FTAS`, `^FTNMX5710`). One table, one query pattern, one fetcher.
- **(b) Separate `benchmarks` table** with the same columns as `prices`.

**Pick: (a).** Reasoning — the columns are identical, the price-fetching path is identical, and the only distinction between "ticker price" and "benchmark price" is whether the symbol starts with `^`. A union query across stock + benchmark is one `SELECT` instead of a `JOIN`. The CAR computation in Stage 4 reads ticker price and benchmark price the same way. A separate `benchmarks` table earns no clarity and costs join cost on every CAR row.

The `prices` table therefore holds both stock and index series. The benchmark-symbol convention is documented in the schema comment block. No schema change for Stage 1; just the comment.

### Decision S2 — `tickers_meta` table: schema in Stage 1, populate in Stage 3

A new table `tickers_meta` is needed so a stock ticker can be mapped to its sector and to its sector-benchmark index. The mapping is read by Stage 4 (signal engine) to look up the right benchmark for each transaction's ticker.

**Pick: define the schema in Stage 1; leave it empty until Stage 3.** Reasoning — adding the table later means a migration. Adding it empty now is free and removes a sequencing constraint. Stage 3 (the price/sector backfill) is the right time to populate it because that's when sector lookups happen.

Columns: `ticker TEXT PRIMARY KEY`, `sector TEXT`, `benchmark_symbol TEXT`, `is_aim INTEGER NOT NULL DEFAULT 0`, `market_cap_gbp REAL`, `updated_at TEXT NOT NULL`. The `is_aim` flag also covers the Phase 3 stamp-duty exemption requirement (no UK stamp on AIM stocks) — putting it on `tickers_meta` rather than on `transactions` means one row per ticker rather than one per filing.

### Decision S3 — no `aim` flag on `transactions`

Phase 3 spec mentions an AIM flag, sometimes phrased as "a separate column on `transactions`". With `tickers_meta.is_aim` in place this isn't needed — Stage 4 joins on ticker. Keeps `transactions` a clean mirror of the filing data.

### Decision S4 — `paper_trades` schema (inferred from Phase 5 spec)

Phase 7 spec (`07-conviction-sizing.md`) names `notional_gbp` as the per-row column and mentions `entry_close`, `shares = notional / entry_close`, `status` (`planned | open | closed | skipped`), `signal_id`, `signal_version`, plus the underlying transaction `fingerprint`. Inferred full shape:

```
paper_trades
  trade_id          TEXT PRIMARY KEY    -- e.g. "{signal_id}|{fingerprint}|{sizing_scheme}"
  signal_id         TEXT NOT NULL
  signal_version    TEXT NOT NULL
  fingerprint       TEXT NOT NULL       -- FK -> transactions.fingerprint
  sizing_scheme     TEXT NOT NULL       -- "flat" | "log" | "tier" | "linear"
  notional_gbp      REAL NOT NULL       -- per-row, allows conviction-weighted variation
  entry_date        TEXT                -- T+1 trading day (YYYY-MM-DD)
  entry_close       REAL                -- price at entry
  shares            REAL                -- notional / entry_close (float because conviction sizing may not divide cleanly)
  exit_date         TEXT                -- nullable until closed
  exit_close        REAL                -- nullable until closed
  status            TEXT NOT NULL       -- 'planned' | 'open' | 'closed' | 'skipped'
  opened_at         TEXT NOT NULL       -- when the row was created
  updated_at        TEXT NOT NULL
  notes             TEXT                -- free-form
```

Index: `(signal_id, signal_version)` and `(status)` for the briefing's "new firings" panel filter.

### Decision S5 — `schema_version` meta key, start at `1`

Per the brief. `migrate()` reads `meta.schema_version`; if absent, sets to `'1'`. Future migrations (Stage 2 onwards) increment.

---

## Schema (full SQL, lives in `db_schema.sql`)

```sql
-- schema_version: 1
-- Comments at top of file explain:
--   * benchmark series live in prices with leading '^' on the symbol (S1)
--   * tickers_meta carries sector, benchmark_symbol, is_aim (S2/S3)
--   * fingerprint = date|ticker|director|type|shares (mirrors current refresh.py)

CREATE TABLE IF NOT EXISTS transactions (
    fingerprint     TEXT    PRIMARY KEY,
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    seen_count      INTEGER NOT NULL DEFAULT 1,
    date            TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    company         TEXT    NOT NULL,
    director        TEXT    NOT NULL,
    role            TEXT,
    type            TEXT    NOT NULL CHECK (type IN ('BUY','SELL','SELL_TAX','EXERCISE','GRANT','SIP')),
    shares          INTEGER NOT NULL,
    price           REAL    NOT NULL DEFAULT 0,
    value           REAL    NOT NULL DEFAULT 0,
    context         TEXT,
    url             TEXT,
    announced_at    TEXT,
    cluster_id      TEXT,
    first_time_buy  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tx_ticker_date ON transactions (ticker, date);
CREATE INDEX IF NOT EXISTS idx_tx_director ON transactions (director);
CREATE INDEX IF NOT EXISTS idx_tx_type_date ON transactions (type, date);

CREATE TABLE IF NOT EXISTS prices (
    ticker          TEXT    NOT NULL,    -- '^FTAS' / '^FTNMX5710' for benchmark series (S1)
    date            TEXT    NOT NULL,
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

CREATE TABLE IF NOT EXISTS tickers_meta (
    ticker            TEXT    PRIMARY KEY,
    sector            TEXT,
    benchmark_symbol  TEXT,                            -- the '^...' symbol used in prices for this ticker's sector
    is_aim            INTEGER NOT NULL DEFAULT 0,      -- 0/1; needed for stamp-duty exemption in Stage 4
    market_cap_gbp    REAL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id       TEXT    NOT NULL,
    signal_version  TEXT    NOT NULL,
    fingerprint     TEXT    NOT NULL,
    fired_at        TEXT    NOT NULL,
    confidence      TEXT,
    metadata        TEXT,
    PRIMARY KEY (signal_id, signal_version, fingerprint),
    FOREIGN KEY (fingerprint) REFERENCES transactions(fingerprint)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          TEXT    PRIMARY KEY,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    signal_id       TEXT,
    signal_version  TEXT,
    metadata        TEXT,
    universe        TEXT,
    period_start    TEXT,
    period_end      TEXT
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id        TEXT    PRIMARY KEY,
    signal_id       TEXT    NOT NULL,
    signal_version TEXT    NOT NULL,
    fingerprint     TEXT    NOT NULL,
    sizing_scheme   TEXT    NOT NULL CHECK (sizing_scheme IN ('flat','log','tier','linear')),
    notional_gbp    REAL    NOT NULL,
    entry_date      TEXT,
    entry_close     REAL,
    shares          REAL,
    exit_date       TEXT,
    exit_close      REAL,
    status          TEXT    NOT NULL CHECK (status IN ('planned','open','closed','skipped')),
    opened_at       TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    notes           TEXT,
    FOREIGN KEY (fingerprint) REFERENCES transactions(fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_paper_signal ON paper_trades (signal_id, signal_version);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades (status);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Seed row (only meta seed at Stage 1):
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');
```

---

## Per-file structure (pseudocode)

### `.scripts\db.py`

```
imports: sqlite3, pathlib.Path, datetime

ROOT = Path(__file__).resolve().parent.parent              # project root
DB_DIR = ROOT / ".data"
DB_PATH = DB_DIR / "directors.db"
SCHEMA_PATH = ROOT / ".scripts" / "db_schema.sql"

def iso_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)              # idempotent
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)                                          # applies on every connect
    return conn

def migrate(conn) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()
    # Schema uses CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE,
    # so calling twice is safe. No version-bump logic in Stage 1.

def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()

def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
```

No `__main__` block in Stage 1. Stage 2+ may add a CLI shim if helpful.

### `.scripts\test_db_smoke.py`

Tests run against a throwaway temp DB (monkeypatched `db.DB_PATH`), exercising the real `connect()` / `migrate()` / `set_meta` / `get_meta` paths. `shutil.rmtree` in `finally` cleans up regardless of pass/fail.

---

## Smoke-test cases (explicit list)

| # | What it inserts / does | What it asserts |
|---|---|---|
| 1 | Open DB, list `sqlite_master` | All 7 tables present (transactions, prices, tickers_meta, signals, backtest_runs, paper_trades, meta) |
| 2 | Read `meta.schema_version` | Equals `'1'` |
| 3 | Call `migrate()` twice more | `schema_version` still `'1'`; no exception |
| 4 | Insert one synthetic BUY transaction | Round-trip read returns same ticker + shares |
| 5 | Insert one stock price row (`CHH`, 2026-04-01) | Round-trip read returns same close |
| 6 | Insert one benchmark price row (`^FTAS`, 2026-04-01) | `SELECT COUNT(*) WHERE ticker LIKE '^%'` returns 1 — confirms decision S1 works |
| 7 | Insert one signal row keyed to the tx fingerprint | Round-trip read returns same signal_id; confirms FK accepts the row |
| 8 | Call `set_meta('smoke','yes')` then `set_meta('smoke','again')` | `get_meta('smoke')` returns `'again'` — upsert semantics |
| 9 | `PRAGMA foreign_keys` | Returns 1 — FKs are on |

---

## Edge cases

- **`.data\` already exists.** `Path.mkdir(parents=True, exist_ok=True)` is a no-op. Fine.
- **`.data\directors.db` already exists.** Re-running is safe; `CREATE TABLE IF NOT EXISTS` + `INSERT OR IGNORE`.
- **Windows paths.** All paths constructed with `pathlib.Path`. No raw `/` or `\`.
- **Working directory independence.** Everything keys off `Path(__file__).resolve().parent.parent`.
- **`db_schema.sql` encoding.** Read with `encoding="utf-8"` explicitly.
- **Foreign-key enforcement.** SQLite has FKs off by default per-connection. `PRAGMA foreign_keys = ON` set in `connect()`.
- **`type` and `status` CHECK constraints.** Reject typos early. Good for catching parser bugs in Stage 2.
- **Read-only consumer.** Deferred to Stage 5 if needed (`file:?mode=ro` URI).

---

## Rollback

Stage 1 touches no existing file. To undo:

1. Delete the directory `.data\` (drops `directors.db`).
2. Delete `.scripts\db.py`, `.scripts\db_schema.sql`, `.scripts\test_db_smoke.py`.
3. If `.scripts\` is now empty, delete it.

`server.py`, `update.py`, `cache.py`, `dealings_cache.db` and the existing dashboard are untouched.

---

## Acceptance criteria

Stage 1 is **done** when all of the following are true:

1. `.data\directors.db` exists at the project root and opens in `sqlite3` without error.
2. `python .scripts\test_db_smoke.py` exits 0 and prints `9 passed, 0 failed`.
3. Same smoke test exits 0 a second time (re-run after first pass): proves idempotency.
4. `db_schema.sql` is human-readable and the schema in the file matches the schema applied to the live DB (verified by `PRAGMA table_info`).
5. `meta.schema_version` returns `'1'`.
6. No third-party package was added; `requirements.txt` is unchanged.
7. The four files in the table at the top of this plan exist and only those files were created.

---

## Effort estimate

| Phase | Time | Tokens (Sonnet) |
|---|---|---|
| Draft `db_schema.sql` | 20 min | ~5k |
| Write `db.py` | 25 min | ~7k |
| Write `test_db_smoke.py` | 30 min | ~10k |
| Run / debug smoke test on Rupert's machine | 30 min | ~5k |
| Spot-check column-for-column in `sqlite3` shell | 10 min | ~3k |
| **Total** | **~2 hours** | **~30k** |

---

## Out of scope for Stage 1

- No Investegate scraping (Stage 2).
- No PDMR/RNS parser (Stage 2).
- No price fetcher, no Yahoo calls, no OHLCV backfill (Stage 3).
- No `tickers_meta` population — table exists, rows empty (Stage 3).
- No signal evaluators (Stage 4).
- No cluster detector, no first-time-buy flag computation (Stage 4).
- No dashboard, no HTML (Stage 5).
- No paper-trade row creation (Stage 5).
- No LLM fallback parser (later phase).
- No migration of the existing legacy `dealings_cache.db` (clean rebuild).
- No `update.py` integration, no `start.bat` change.

---

## Open questions for Rupert before implementation starts

1. **Is `.data\` confirmed as the DB folder name?** Alternative: `.scripts\directors.db`.
2. **`paper_trades.shares` as `REAL` or `INTEGER`?** Conviction sizing can produce fractional shares. Plan picks `REAL`.
3. **`trade_id` shape — `"{signal_id}|{fingerprint}|{sizing_scheme}"` acceptable?** Alternative: opaque UUID with `UNIQUE` constraint on the triple. Plan picks the natural-key string for legibility.
4. **`is_aim` lives on `tickers_meta` (Decision S3), not on `transactions`.** Confirm.
5. **Should `db.py` expose a read-only `connect(readonly=True)` mode now or wait until Stage 5 needs it?** Plan defers.
