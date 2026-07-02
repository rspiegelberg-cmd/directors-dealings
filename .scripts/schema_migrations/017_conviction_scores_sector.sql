-- Migration 017: add sector column to conviction_scores.
-- Added to Postgres directly on 2026-07-02 (ALTER TABLE via Supabase MCP).
-- This migration keeps local SQLite in sync for CI runs.
ALTER TABLE conviction_scores ADD COLUMN sector TEXT;
