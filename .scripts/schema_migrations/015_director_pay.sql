-- Migration 015: create director_pay table (B-168).
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- Director annual remuneration, per (ticker, director_key, financial year,
-- pay_type), used to compute the "salary multiple" conviction feature
-- (buy value / director pay). Feature column only -- no firing signal.
-- Spec: docs/specs/b168-salary-multiple-plan.md.  Sprint 64 -- 2026-06-13.
--
-- DUAL DENOMINATOR (Rupert decision 2026-06-13): we store BOTH the audited
-- single-figure total remuneration AND base salary where separable, as two
-- rows sharing (ticker, director_key, fy_end) but differing in pay_type, so
-- two multiples can be computed downstream and the alpha scan picks the winner.
--
-- ROW CONVENTIONS
--   * Real figure found   -> pay_status='ok', pay_type in
--       ('single_figure_total','base_salary','ned_fees'), pay_native/pay_gbp set.
--   * Zero / nominal pay   -> pay_status='ok', pay_type in
--       ('fee_waiver_zero','nominal'); the salary multiple is deliberately NULL
--       for these (divide-by-zero / meaningless), but the finding is recorded.
--   * No figure available  -> ONE row with pay_type='none' and fy_end='' (empty
--       string, not NULL, so the UNIQUE key stays clean), pay_native/pay_gbp NULL,
--       pay_status one of: 'new_appointee_no_disclosure' | 'extraction_fail'
--       | 'out_of_scope'. Never a hard error -- the new-appointee gap is the
--       dominant, structural miss (see the B-163 spike memo).
--
-- director_key joins to transactions via routine_flag.director_key()
-- (NFKC + casefold + whitespace-collapse) -- the same key routine_flag.py /
-- reversal_flag.py use, so the feature attaches to the right director.
--
-- LOOKAHEAD: ar_published_at is when the figure became public (3-6 months
-- after fy_end). The backtest attaches pay to a buy only when
-- ar_published_at <= buy announcement date (P3-6 lookahead discipline).
-- NULL ar_published_at means "publication date unknown" -> excluded by the guard.
--
-- Populated by backfill_director_pay.py (Zone B -- Rupert runs).
CREATE TABLE IF NOT EXISTS director_pay (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT    NOT NULL,
    director_key      TEXT    NOT NULL,           -- routine_flag.director_key()
    director_name_raw TEXT,                       -- as printed in the report
    fy_end            TEXT    NOT NULL DEFAULT '',-- ISO YYYY-MM-DD; '' = unknown
    ar_published_at   TEXT,                       -- ISO; drives lookahead guard
    pay_native        REAL,                       -- figure in reporting currency
    currency          TEXT,                       -- GBP / USD / EUR
    fx_rate           REAL,                       -- native -> GBP
    fx_date           TEXT,                       -- FY-end date used for the rate
    pay_gbp           REAL,                       -- pay_native * fx_rate
    pay_type          TEXT    NOT NULL
                      CHECK (pay_type IN ('single_figure_total','base_salary',
                                          'ned_fees','fee_waiver_zero','nominal',
                                          'none')),
    role_class        TEXT,                       -- 8-tier role taxonomy
    pay_status        TEXT    NOT NULL DEFAULT 'ok'
                      CHECK (pay_status IN ('ok','new_appointee_no_disclosure',
                                            'out_of_scope','extraction_fail')),
    source_rung       TEXT    CHECK (source_rung IN ('a','b','c') OR source_rung IS NULL),
    source_url        TEXT,
    confidence        TEXT,                       -- high / medium-high / medium
    machine_readable  INTEGER NOT NULL DEFAULT 0, -- 1 = clean audited table
    fetched_at        TEXT    NOT NULL,
    UNIQUE (ticker, director_key, fy_end, pay_type)
);
CREATE INDEX IF NOT EXISTS idx_director_pay_join
    ON director_pay (ticker, director_key);
CREATE INDEX IF NOT EXISTS idx_director_pay_status
    ON director_pay (pay_status);
