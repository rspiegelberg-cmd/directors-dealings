-- Migration 018: create pending_filings (B-204).
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- WHY THIS EXISTS
-- ---------------
-- Filings the parser cannot cleanly ingest are routed to a "pending" queue for
-- manual review. Until now that queue lived ONLY in
-- `.scripts/_pending_review.json`, which is gitignored (the `.scripts/_*` rule)
-- and therefore exists only on whatever machine ran the scrape.
--
-- Since the 2026-06-25 cloud migration the scrape runs in GitHub Actions on a
-- fresh, disposable runner. Every daily run started with an EMPTY queue file,
-- wrote that day's unparseable filings into it, and then threw the runner away.
-- The queue was silently destroyed once per day, every day, for ~2 months, and
-- the review surface on the site had nothing to show. Confirmed 2026-08-21.
--
-- The queue is review-critical: an unparsed filing is a missed signal, and a
-- missed signal is the product. It belongs in the durable store.
--
-- SHAPE
-- -----
-- One row per RNS announcement, keyed by rns_id. `warnings` and `extracted`
-- carry JSON text, matching the existing convention on signals.metadata and
-- conviction_scores.weights_used (works identically on SQLite and Postgres, and
-- keeps the payload byte-identical to what run_scrape.py already builds).
--
-- STATUS lifecycle:
--   pending   -- awaiting review (the queue)
--   resolved  -- a human corrected it and the transaction was ingested
--   rejected  -- a human judged it junk/boilerplate/out-of-scope
-- Only 'pending' rows are loaded back into run_scrape.py's working set, so a
-- filing a human has already dispositioned never reappears in the queue. The
-- scraper's upsert deliberately does NOT touch `status` on conflict, so a
-- re-scrape of an already-rejected filing cannot resurrect it.
--
-- first_seen is preserved across re-scrapes so "how long has this been stuck?"
-- stays answerable; last_seen tracks the most recent scrape that saw it.
CREATE TABLE IF NOT EXISTS pending_filings (
    rns_id        TEXT PRIMARY KEY,
    url           TEXT,
    headline      TEXT,
    warnings      TEXT    NOT NULL DEFAULT '[]',   -- JSON array of warning strings
    extracted     TEXT    NOT NULL DEFAULT '[]',   -- JSON array of partial tx dicts
    parser_source TEXT,                            -- 'regex' | 'llm' | ''
    used_llm      INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'resolved', 'rejected')),
    resolution    TEXT,                            -- reason code when not pending
    resolved_by   TEXT,                            -- who dispositioned it
    resolved_at   TEXT,                            -- ISO8601 UTC
    first_seen    TEXT    NOT NULL,
    last_seen     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_filings_status
    ON pending_filings (status);
CREATE INDEX IF NOT EXISTS idx_pending_filings_first_seen
    ON pending_filings (first_seen);
