-- Migration 011: add small_cap to tickers_meta (B-138).
--
-- small_cap = 1 if market_cap_gbp < £300m threshold (classify_small_cap.py)
--           = 0 if market_cap_gbp >= threshold
--           = NULL / 0 (default) if market_cap_gbp is not yet populated
--
-- Additive nullable-with-default column -> plain ALTER, no table rebuild.
-- Idempotency handled by db._run_migration_step (skips if column already exists).
--
-- B-138 -- Sprint 53 -- 2026-06-07. Zone B: applied by db.connect() on the next
-- pipeline run (Rupert runs it).

ALTER TABLE tickers_meta ADD COLUMN small_cap INTEGER DEFAULT 0;
