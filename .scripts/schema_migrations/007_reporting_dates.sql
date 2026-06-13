-- Migration 007: create reporting_dates table.
-- Idempotent: CREATE TABLE IF NOT EXISTS.
--
-- Stores known and upcoming financial results dates per ticker.
-- Source: Yahoo Finance calendarEvents module (backfill_reporting_dates.py).
--
-- report_type:
--   INTERIM        -- half-year / interim results announcement
--   FINAL          -- preliminary / full-year results announcement
--   TRADING_UPDATE -- trading statement / IMS
--   EARNINGS       -- generic earnings date from Yahoo (type not yet known)
--
-- Populated by backfill_reporting_dates.py (Zone B -- Rupert runs).
-- Sprint 26 -- 2026-06-04.
CREATE TABLE IF NOT EXISTS reporting_dates (
    ticker          TEXT    NOT NULL,
    report_date     TEXT    NOT NULL,           -- ISO YYYY-MM-DD
    report_type     TEXT    NOT NULL DEFAULT 'EARNINGS'
                    CHECK (report_type IN ('INTERIM','FINAL','TRADING_UPDATE','EARNINGS')),
    source          TEXT    NOT NULL DEFAULT 'yahoo',
    fetched_at      TEXT    NOT NULL,
    PRIMARY KEY (ticker, report_date, report_type)
);
CREATE INDEX IF NOT EXISTS idx_reporting_dates_ticker ON reporting_dates (ticker);
CREATE INDEX IF NOT EXISTS idx_reporting_dates_date ON reporting_dates (report_date);
