-- Migration 016: create conviction_scores table (B-171, Phase 3 shadow log).
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- Conviction Score (spec: docs/specs/conviction-score-spec.md). One row per
-- scored director BUY per pipeline run. The exporter
-- (export_dashboard_json.build_conviction_picks) upserts every buy in the
-- rolling trailing-28-day window here — NOT just the surfaced top 10 — so the
-- measure-forward loop (§7) can later regress forward CAR on score across the
-- WHOLE distribution, a far stronger test than watching the picks alone.
--
-- This is a DERIVED VIEW, not a signal_id: it is deliberately kept OUT of the
-- 3-layer signal-id contract (SIGNAL_ORDER / SIGNAL_SHORT / JS SIDS). It sits
-- next to active_clusters as a ranked panel.
--
-- COLUMNS
--   fingerprint        joins to transactions(fingerprint); the buy being scored
--   window_end         end (= run) date ISO (YYYY-MM-DD) of the rolling 28d window
--   scored_at          ISO timestamp the row was written (audit / re-score)
--   score              0-100 composite conviction score
--   band               Low / Moderate / High / Exceptional (§4 strength bands)
--   f1_who .. f6_sector_mult  the six 0.0-1.0 sub-scores (f6 is a 0.7-1.0 mult)
--   weights_used       JSON of the post-renormalise additive weights actually used
--   earnings_dropped   1 when F4 was dropped & weights re-normalised (§11 dec 3)
--   rank_in_window     1-based rank across the WHOLE window's buy distribution
--   surfaced           1 for the 10 highest-scored buys of the window, else 0 (§6)
--   inputs_missing     JSON list of factor names with no underlying data
--
-- LOOKAHEAD: every price/return/turnover/sector input feeding these scores is
-- computed strictly BEFORE the buy's effective announcement day (P3-6), in
-- conviction_pipeline.py. This table only stores the result.
--
-- Populated by export_dashboard_json.py during the dashboard build (Zone B --
-- Rupert runs). Read back by conviction_outcomes.py for the T+21/T+90 join.
CREATE TABLE IF NOT EXISTS conviction_scores (
    fingerprint        TEXT    NOT NULL,
    window_end         TEXT    NOT NULL,        -- rolling 28d window end (run) date, ISO
    scored_at          TEXT    NOT NULL,
    score              REAL,
    band               TEXT,
    f1_who             REAL,
    f2_buy_size        REAL,
    f3_company_size    REAL,
    f4_earnings_timing REAL,
    f5_past_performance REAL,
    f6_sector_mult     REAL,
    weights_used       TEXT,                    -- JSON object
    earnings_dropped   INTEGER NOT NULL DEFAULT 0,
    rank_in_window     INTEGER,
    surfaced           INTEGER NOT NULL DEFAULT 0,
    inputs_missing     TEXT,                    -- JSON array
    PRIMARY KEY (fingerprint, window_end),
    FOREIGN KEY (fingerprint) REFERENCES transactions(fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_conviction_window
    ON conviction_scores (window_end);
CREATE INDEX IF NOT EXISTS idx_conviction_surfaced
    ON conviction_scores (surfaced);
