-- schema_version: 1
--
-- Directors Dealings — Stage 1 SQLite schema.
--
-- Conventions documented here so the file is self-explanatory:
--   * Benchmark series live in the `prices` table with a leading '^' on the
--     symbol (decision S1 in stage-01-plan.md). e.g. '^FTAS' for the FTSE
--     All-Share, '^FTNMX5710' for an FTSE sector index. One table, one
--     query pattern, one fetcher for both stock prices and benchmark
--     prices.
--   * `tickers_meta` carries per-ticker sector, the benchmark_symbol used
--     to look up the right '^...' row in `prices`, and an `is_aim` flag
--     (decisions S2 / S3). The AIM flag lives here rather than on
--     `transactions` so it's one row per ticker, not one per filing. It
--     also satisfies the Phase 3 stamp-duty exemption requirement (no UK
--     stamp duty on AIM stocks).
--   * `fingerprint` is the natural key for a filing and mirrors the
--     existing `refresh.py` definition: date|ticker|director|type|shares.
--   * Foreign keys are enforced per-connection by `db.py` (sqlite3 keeps
--     FK enforcement OFF by default).
--   * This file is applied via `executescript()` from `db.py:migrate()`.
--     Every statement uses `IF NOT EXISTS` / `INSERT OR IGNORE` so the
--     script is idempotent and safe to run on every `connect()`.

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
    signal_version  TEXT    NOT NULL,
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
