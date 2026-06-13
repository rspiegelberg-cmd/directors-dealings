-- Migration 002: add parser_source column to transactions
-- Idempotent: db.py checks PRAGMA table_info before applying.
ALTER TABLE transactions ADD COLUMN parser_source TEXT NOT NULL DEFAULT 'regex';
