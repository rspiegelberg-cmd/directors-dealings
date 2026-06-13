-- Migration 014: create short_positions + isin_ticker_map tables (B-164).
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- FCA Short Selling Regulation daily disclosures (net short positions
-- >= 0.5% of issued share capital, 0.1% increments, each UK business day).
-- Source workbook: short-positions-daily-update.xlsx, two sheets matched
-- by name prefix ("Current Disclosures DD.MM.YYYY" / "Historic
-- Disclosures DD.MM.YYYY" -- ~106k historic rows back to ~2012).
--
-- REGIME CHANGE 13 Jul 2026 (PS26/5): individual holder-level disclosure
-- ends, replaced by anonymised aggregated per-issuer data (ANSP).
-- position_holder is therefore NULL-able so future ANSP rows can land in
-- the same table with source='ansp_monthly'. The historic sheet is a
-- one-shot backfill that becomes unfetchable after 13 Jul 2026.
--
-- net_short_pct: 0.0 rows are KEPT -- they mean the holder dropped below
-- the 0.5% threshold (an exit), which the as-of aggregate needs so closed
-- positions self-cancel.
--
-- ticker stays NULL until the mapping pass resolves the ISIN (name match
-- against transactions.company, OpenFIGI fallback, manual override CSV
-- .data/_isin_overrides.csv). Resolved mappings persist in isin_ticker_map.
--
-- Populated by backfill_short_interest.py (Zone B -- Rupert runs).
-- Sprint 61 -- 2026-06-10.
CREATE TABLE IF NOT EXISTS short_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_holder TEXT,                            -- NULL for future ANSP rows
    issuer_name     TEXT    NOT NULL,
    isin            TEXT    NOT NULL,
    ticker          TEXT,                            -- NULL until mapped
    net_short_pct   REAL    NOT NULL,                -- 0.0 = exit row, kept
    position_date   TEXT    NOT NULL,                -- ISO YYYY-MM-DD
    source          TEXT    NOT NULL DEFAULT 'ssr_daily',
    fetched_at      TEXT    NOT NULL,
    UNIQUE (position_holder, isin, position_date, source)
);
CREATE INDEX IF NOT EXISTS idx_short_positions_ticker_date
    ON short_positions (ticker, position_date);
CREATE INDEX IF NOT EXISTS idx_short_positions_isin
    ON short_positions (isin);

CREATE TABLE IF NOT EXISTS isin_ticker_map (
    isin      TEXT PRIMARY KEY,
    ticker    TEXT NOT NULL,
    method    TEXT NOT NULL
              CHECK (method IN ('name_match', 'openfigi', 'manual')),
    mapped_at TEXT NOT NULL
);
