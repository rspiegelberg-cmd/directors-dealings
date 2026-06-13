-- Migration 003: add IT/CEF/VCT/REIT exclusion columns to tickers_meta.
-- Idempotent: db.py checks PRAGMA table_info before applying.
--
-- is_excluded_issuer: 1 if the ticker is an investment trust, VCT, REIT, or
--                     closed-end fund and should be excluded from the
--                     dataset for signal scoring.
-- excluded_source:    Provenance string. Comma-joined letters when more
--                     than one source matched. Letters:
--                       A = AIC (Association of Investment Companies) member list
--                       B = Yahoo Finance quoteType (ETF / MUTUALFUND / CLOSEDENDFUND)
--                       C = Name regex (conservative: 'Investment Trust',
--                           'VCT', 'REIT', 'Capital Trust', 'Real Estate
--                           Investment Trust')
--                     NULL when the ticker is not excluded.
-- classified_at:      ISO timestamp when the classifier last ran for this ticker.
ALTER TABLE tickers_meta ADD COLUMN is_excluded_issuer INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tickers_meta ADD COLUMN excluded_source TEXT;
ALTER TABLE tickers_meta ADD COLUMN classified_at TEXT;
