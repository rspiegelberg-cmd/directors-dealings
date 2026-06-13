-- Migration 006: add shares_outstanding to tickers_meta.
-- Idempotent: db.py checks PRAGMA table_info before applying.
--
-- shares_outstanding -- Yahoo quoteSummary defaultKeyStatistics.sharesOutstanding
--                       Used for "buy as % of float" conviction normalisation (B-097).
--
-- Populated by backfill_ticker_meta.py (Zone B -- Rupert runs).
-- Sprint 26 -- 2026-06-04.
ALTER TABLE tickers_meta ADD COLUMN shares_outstanding REAL;
