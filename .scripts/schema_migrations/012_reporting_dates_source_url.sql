-- Migration 012: add source_url column to reporting_dates (B-119).
-- Stores the direct filing URL (Investegate announcement link) so the
-- company page can link to the original RNS filing.  Nullable — LSE Diary
-- and synthetic estimate rows have no individual filing URL.
-- Simple ALTER TABLE (no rebuild needed — nullable column with no default).
-- Sprint 35 — 2026-06-08.
ALTER TABLE reporting_dates ADD COLUMN source_url TEXT;
