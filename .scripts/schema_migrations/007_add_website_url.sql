-- Migration 007: add website_url to tickers_meta.
-- Idempotent: db.py checks PRAGMA table_info before applying.
--
-- website_url -- Yahoo quoteSummary assetProfile.website field (B-101).
--               Displayed as a link on the company page.
--
-- Populated by backfill_ticker_meta.py (Zone B -- Rupert runs).
-- Sprint 26 -- 2026-06-04.
ALTER TABLE tickers_meta ADD COLUMN website_url TEXT;
