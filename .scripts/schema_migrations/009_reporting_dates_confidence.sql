-- Migration 009: widen reporting_dates.report_type CHECK + add `confidence` (B-111).
--
-- SQLite cannot ALTER a CHECK constraint, so the table is rebuilt
-- (create new -> copy -> drop old -> rename). Idempotency is handled by
-- db._run_migration_step, which skips this step if the `confidence` column
-- already exists.
--
-- report_type: the original four (INTERIM, FINAL, TRADING_UPDATE, EARNINGS) PLUS
-- three new LSE-Diary types per B-111:
--   PRELIM        -- preliminary / full-year results (LSE Diary "Final Results")
--   QUARTERLY     -- Q1-Q4 results
--   TRADING_STMT  -- trading statement / trading announcement
--
-- confidence: NOT NULL DEFAULT 'confirmed'. Every reporting date is either a
-- 'confirmed' scrape (LSE Diary / Yahoo / Investegate) or a synthetic 'est'
-- estimate (backfill_expected_reporting_dates.py). Existing rows are copied as
-- 'confirmed' (additive-safe). The dashboard appends an "(est)" suffix ONLY when
-- confidence='est', so a synthetic date can never read as confirmed.
-- (Chosen NOT NULL over the spec's "nullable" so every row has a definite
-- confidence -- there is no meaningful "unknown" state.)
--
-- Wrapped in BEGIN/COMMIT so the multi-statement rebuild is atomic: executescript
-- issues an implicit COMMIT at entry, then this explicit transaction wraps the DDL
-- and rolls back as a unit on any failure (self-healing .bak restore covers the
-- rare case where the process dies mid-rebuild -- see CLAUDE.md Two-zone rule).
--
-- B-111 -- Sprint 34 -- 2026-06-05. Zone B: applied by db.connect() on the next
-- pipeline run (Rupert runs it).

BEGIN;

CREATE TABLE reporting_dates_new (
    ticker          TEXT    NOT NULL,
    report_date     TEXT    NOT NULL,           -- ISO YYYY-MM-DD
    report_type     TEXT    NOT NULL DEFAULT 'EARNINGS'
                    CHECK (report_type IN (
                        'INTERIM','FINAL','TRADING_UPDATE','EARNINGS',
                        'PRELIM','QUARTERLY','TRADING_STMT')),
    source          TEXT    NOT NULL DEFAULT 'yahoo',
    fetched_at      TEXT    NOT NULL,
    confidence      TEXT    NOT NULL DEFAULT 'confirmed'
                    CHECK (confidence IN ('confirmed','est')),
    PRIMARY KEY (ticker, report_date, report_type)
);

INSERT INTO reporting_dates_new
    (ticker, report_date, report_type, source, fetched_at, confidence)
    SELECT ticker, report_date, report_type, source, fetched_at, 'confirmed'
    FROM reporting_dates;

DROP TABLE reporting_dates;
ALTER TABLE reporting_dates_new RENAME TO reporting_dates;

CREATE INDEX IF NOT EXISTS idx_reporting_dates_ticker ON reporting_dates (ticker);
CREATE INDEX IF NOT EXISTS idx_reporting_dates_date ON reporting_dates (report_date);

COMMIT;
