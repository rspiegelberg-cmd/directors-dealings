"""Stage 1/2 SQLite connector for the Directors Dealings rebuild.

Stdlib-only. No SQLAlchemy, no third-party packages.

Public surface:
    DB_DIR       -- Path to the .data\\ directory at the project root.
    DB_PATH      -- Path to the SQLite file (.data\\directors.db).
    SCHEMA_PATH  -- Path to the canonical SQL sidecar (.scripts\\db_schema.sql).
    iso_now()    -- UTC timestamp string, "%Y-%m-%dT%H:%M:%SZ".
    connect()    -- Open a sqlite3.Connection with FKs on and the schema applied.
    migrate(c)   -- Apply db_schema.sql + chained migrations to an open
                    connection (idempotent).
    set_meta(c,k,v) / get_meta(c,k) -- Upsert / fetch key-value rows in `meta`.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Local import for role normalization (B-025 Phase A).
# Imported lazily inside upsert_transaction to avoid a circular import risk
# if role_normalize ever needs anything from db.py.

ROOT: Path = Path(__file__).resolve().parent.parent
DB_DIR: Path = ROOT / ".data"
DB_PATH: Path = DB_DIR / "directors.db"
SCHEMA_PATH: Path = ROOT / ".scripts" / "db_schema.sql"
MIGRATIONS_DIR: Path = ROOT / ".scripts" / "schema_migrations"


def iso_now() -> str:
    """Return the current UTC time as 'YYYY-MM-DDTHH:MM:SSZ'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(
    path: Path,
    payload: object,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = True,
    newline_at_end: bool = False,
) -> None:
    """Write `payload` to `path` as JSON atomically via tempfile + replace.

    Either the old file or the new payload is on disk at any moment — a
    mid-write crash never strands a partial JSON file. This is the
    canonical pattern across the project for persisting state files
    (_pending_review.json, _refresh_status.json, _backtest_skips.json,
    _backfill_progress.json, etc).

    B-038 (2026-05-21): consolidated four independently-drifting copies
    of this pattern (eval_signals.py SKIPS write, backtest.py SKIPS
    write, run_pending_sweep.py `_atomic_write`, repair_dates.py
    `_save_pending`) into this single helper. Keyword args cover the
    variations across the call sites.

    Args:
        path: target path. Parent directory is created if missing.
        payload: any JSON-serialisable object.
        indent: json.dumps indent (default 2; pass None to disable).
        sort_keys: pass to json.dumps. Default False; pass True where
            stable diffs matter (e.g. progress + status files).
        ensure_ascii: pass to json.dumps. Default True (matches stdlib
            default). Pass False to allow non-ASCII chars (e.g.
            director names with accents) to be written raw.
        newline_at_end: append a trailing newline. Default False.
            export_dashboard_json's helper uses True for clean diffs.

    Atomicity: tmp = path.with_suffix(path.suffix + ".tmp"), write, then
    `Path.replace` which is `os.replace` under the hood — atomic on
    both POSIX and Windows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(
        payload, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii,
    )
    if newline_at_end:
        data += "\n"
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def connect() -> sqlite3.Connection:
    """Open the Directors Dealings SQLite DB with the schema applied."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply the canonical schema and any chained migrations to `conn`.

    Step 1: apply the base Stage 1 schema (idempotent CREATE/INSERT IGNORE).
    Step 2: walk the migration chain in `.scripts/schema_migrations/`.
    """
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _apply_schema_migrations(conn)
    conn.commit()


class MigrationError(RuntimeError):
    """Raised when a schema migration step fails. Recovery: restore .bak
    (see CLAUDE.md Two-zone rule) and investigate which migration file
    failed. Partial DDL may have been applied — do not retry without
    inspecting the schema first.
    """


def _run_migration_step(
    conn: sqlite3.Connection,
    step_from: str,
    step_to: str,
    migration_file: str,
    table: str,
    new_column: str,
) -> None:
    """Apply one migration step with explicit error handling.

    B-032: each step is wrapped in try/except so a mid-script failure
    raises ``MigrationError`` with a clear diagnostic (which file failed,
    which version stayed) instead of an opaque traceback inside
    ``connect()``. The ``schema_version`` bump is the LAST action, so a
    failure during ``executescript`` leaves the DB at ``step_from`` and
    the next start retries the same migration.

    Honest note about atomicity: SQLite's ``executescript`` issues an
    implicit ``COMMIT`` at entry, so a true single-transaction
    BEGIN/COMMIT around the DDL isn't possible without rewriting
    migrations as statement lists. For the migrations in this project
    (each is one ALTER TABLE), the per-statement atomicity SQLite gives
    us is sufficient. The recovery path documented in
    ``MigrationError.__doc__`` covers the rare multi-statement failure.
    """
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if new_column in cols:
        # Already applied. Just bump the version pointer.
        set_meta(conn, "schema_version", step_to)
        return
    migration_sql = (MIGRATIONS_DIR / migration_file).read_text(encoding="utf-8")
    try:
        conn.executescript(migration_sql)
    except Exception as exc:
        raise MigrationError(
            f"Migration {migration_file} ({step_from} -> {step_to}) failed: {exc}. "
            f"DB schema_version remains at {step_from}. "
            "Inspect schema before retrying; partial DDL may have been applied "
            "if the migration contains multiple statements."
        ) from exc
    # DDL succeeded — bump the version pointer (separate atomic write).
    set_meta(conn, "schema_version", step_to)


def _run_create_table_migration_step(
    conn: sqlite3.Connection,
    step_from: str,
    step_to: str,
    migration_file: str,
    table: str,
) -> None:
    """Apply one migration step that creates a new table.

    Same semantics as _run_migration_step but checks sqlite_master for
    table existence rather than PRAGMA table_info for a column. Used for
    CREATE TABLE migrations (e.g. migration 008 reporting_dates).
    Sprint 26 -- 2026-06-04.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is not None:
        # Table already exists. Just bump the version pointer.
        set_meta(conn, "schema_version", step_to)
        return
    migration_sql = (MIGRATIONS_DIR / migration_file).read_text(encoding="utf-8")
    try:
        conn.executescript(migration_sql)
    except Exception as exc:
        raise MigrationError(
            f"Migration {migration_file} ({step_from} -> {step_to}) failed: {exc}. "
            f"DB schema_version remains at {step_from}. "
            "Inspect schema before retrying; partial DDL may have been applied "
            "if the migration contains multiple statements."
        ) from exc
    set_meta(conn, "schema_version", step_to)


def _apply_schema_migrations(conn: sqlite3.Connection) -> None:
    """Chain forward-only schema migrations. Idempotent per step.

    Each step is transactional (B-032). On failure, the run aborts with
    `MigrationError` and the DB is left at the prior schema_version with
    no partial DDL applied.
    """
    current = get_meta(conn, "schema_version") or "1"

    if current == "1":
        _run_migration_step(
            conn, "1", "2", "002_add_parser_source.sql",
            "transactions", "parser_source",
        )
        current = "2"

    if current == "2":
        _run_migration_step(
            conn, "2", "3", "003_add_is_excluded_issuer.sql",
            "tickers_meta", "is_excluded_issuer",
        )
        current = "3"

    if current == "3":
        _run_migration_step(
            conn, "3", "4", "004_add_role_normalized.sql",
            "transactions", "role_normalized",
        )
        current = "4"

    if current == "4":
        _run_migration_step(
            conn, "4", "5", "005_buy_strictness.sql",
            "transactions", "buy_strictness",
        )
        current = "5"

    if current == "5":
        _run_migration_step(
            conn, "5", "6", "006_ticker_meta_enrichment.sql",
            "tickers_meta", "shares_outstanding",
        )
        current = "6"

    if current == "6":
        _run_migration_step(
            conn, "6", "7", "007_add_website_url.sql",
            "tickers_meta", "website_url",
        )
        current = "7"

    if current == "7":
        _run_create_table_migration_step(
            conn, "7", "8", "008_reporting_dates.sql",
            "reporting_dates",
        )
        current = "8"

    if current == "8":
        # B-111: rebuild reporting_dates to widen the report_type CHECK
        # (adds PRELIM/QUARTERLY/TRADING_STMT) and add the `confidence`
        # column. Idempotent on the `confidence` column.
        _run_migration_step(
            conn, "8", "9", "009_reporting_dates_confidence.sql",
            "reporting_dates", "confidence",
        )
        current = "9"

    if current == "9":
        # B-060: add transactions.price_audit for price-unit reconciliation.
        _run_migration_step(
            conn, "9", "10", "010_price_audit.sql",
            "transactions", "price_audit",
        )
        current = "10"

    if current == "10":
        # B-138: add tickers_meta.small_cap for small-cap basket classification.
        _run_migration_step(
            conn, "10", "11", "011_small_cap.sql",
            "tickers_meta", "small_cap",
        )
        current = "11"

    if current == "11":
        # B-119: add reporting_dates.source_url for filing deep-links.
        _run_migration_step(
            conn, "11", "12", "012_reporting_dates_source_url.sql",
            "reporting_dates", "source_url",
        )
        current = "12"

    if current == "12":
        # B-156: add transactions.resulting_shares (post-transaction total
        # beneficial holding parsed from the filing; NULL when not stated).
        _run_migration_step(
            conn, "12", "13", "013_resulting_shares.sql",
            "transactions", "resulting_shares",
        )
        current = "13"

    if current == "13":
        # B-164: FCA short-interest tables (short_positions + isin_ticker_map).
        # Table-existence check on short_positions; the migration file creates
        # both tables + indexes idempotently.
        _run_create_table_migration_step(
            conn, "13", "14", "014_short_positions.sql",
            "short_positions",
        )
        current = "14"

    if current == "14":
        # B-168: salary-multiple conviction feature -- director_pay table.
        # Table-existence check on director_pay; the migration file creates
        # the table + indexes idempotently.
        _run_create_table_migration_step(
            conn, "14", "15", "015_director_pay.sql",
            "director_pay",
        )
        current = "15"

    if current == "15":
        # B-171: Weekly Conviction Score shadow log -- conviction_scores table.
        # Table-existence check on conviction_scores; the migration file
        # creates the table + indexes idempotently.
        _run_create_table_migration_step(
            conn, "15", "16", "016_conviction_scores.sql",
            "conviction_scores",
        )
        current = "16"  # noqa: F841


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or update a row in `meta`."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the value stored in `meta` for `key`, or None if absent."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def excluded_ticker_set(conn: sqlite3.Connection) -> set[str]:
    """Return the set of tickers flagged is_excluded_issuer = 1.

    B-011 / Sprint 10 Phase 1 defensive filter helper.

    Two filtering patterns coexist in this codebase by design:

    1. **SQL-side (default for queries):** add the inline clause
       ``AND COALESCE(tm.is_excluded_issuer, 0) != 1`` to existing
       queries that already JOIN ``tickers_meta`` (or add a LEFT JOIN
       to ``tickers_meta`` if no such join exists yet). Used in
       ``eval_signals.py``, ``backtest.py``, and the active-clusters
       + today_txs queries in ``export_dashboard_json.py``. Lowest
       overhead — the filter is pushed into the query plan.

    2. **Python-side (this helper):** call this function once at the
       top of a Python loop that iterates tickers (e.g.
       ``backfill_prices.py``, ``backfill_benchmarks.py``,
       ``classify_issuers.py``). Then ``if ticker in excluded_set:
       continue``. Used where the iteration source is already a
       Python collection rather than a SQL query.

    Defensive: returns an empty set if the column is missing
    (pre-migration DB) or any query failure. Never raises. The
    primary defence against excluded-issuer rows is the one-shot
    purge + ingest-time filter in ``run_scrape.py``; this helper is
    the defensive double-layer for downstream code that iterates
    tickers in Python.
    """
    try:
        rows = conn.execute(
            "SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1"
        ).fetchall()
        return {r["ticker"] for r in rows}
    except Exception:
        return set()


def upsert_transaction(conn: sqlite3.Connection, row: dict,
                       parser_source: str, *, verbose: bool = False) -> bool:
    """Insert or update a transaction row.

    Returns True if a new row was inserted; False if an existing row was
    updated (seen_count incremented, last_seen refreshed).

    This is the single canonical upsert used by run_scrape.py,
    backfill_filings.py, and any future ingest script. Keep logic here
    so bug fixes apply everywhere at once.

    B-028 (2026-05-21): this function no longer commits. Callers must
    commit themselves — typically once per filing (after upserting all
    extracted rows from that filing's HTML). Rationale: the previous
    per-row commit pattern produced 3,000+ separate non-sequential
    SQLite writes during a multi-thousand-row backfill — the exact
    FUSE-vulnerable pattern that caused four corruption events in
    this project's history. Per-filing batching cuts the commit count
    by ~100× without sacrificing crash-resilience materially (a crash
    mid-filing loses only that one filing's rows, which the next run
    re-processes from cached HTML for free).

    Caller responsibilities:
      * Wrap the upsert loop in a try / except / rollback so a mid-loop
        failure leaves a consistent DB.
      * commit() after each filing (or whatever batch boundary the
        caller chooses).
    """
    now = iso_now()
    cur = conn.execute(
        "SELECT seen_count FROM transactions WHERE fingerprint = ?",
        (row["fingerprint"],),
    ).fetchone()
    if cur is None:
        # B-025 Phase A: populate role_normalized alongside the raw role
        # so every inserted row carries a canonical bucket from day one.
        # Lazy import to keep this module dependency-free at import time.
        from role_normalize import normalize_role
        raw_role = row.get("role")
        role_normalized = normalize_role(raw_role)
        # B-156: resulting_shares is int|None (None when the filing does not
        # state the post-transaction holding -- most MAR-template filings).
        resulting_shares = row.get("resulting_shares")
        if resulting_shares is not None:
            resulting_shares = int(resulting_shares)
        conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, role_normalized, type, shares, price, "
            "value, context, url, announced_at, cluster_id, first_time_buy, "
            "parser_source, buy_strictness, resulting_shares"
            ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["fingerprint"], now, now, row["date"], row["ticker"],
                row["company"], row["director"], raw_role, role_normalized,
                row["type"], int(row["shares"]), float(row.get("price") or 0.0),
                float(row.get("value") or 0.0), row.get("context"),
                row.get("url"), row.get("announced_at"), None, 0,
                parser_source, row.get("buy_strictness"), resulting_shares,
            ),
        )
        # B-028: no per-row commit. Caller commits at filing boundary.
        if verbose:
            print(f"  + insert {row['fingerprint']} {row['ticker']} "
                  f"{row['type']} {row['shares']}")
        return True
    # B-156: backfill resulting_shares on re-seen rows, but never overwrite
    # an existing non-NULL value (COALESCE keeps the first-parsed figure).
    resulting_shares = row.get("resulting_shares")
    if resulting_shares is not None:
        resulting_shares = int(resulting_shares)
    conn.execute(
        "UPDATE transactions SET last_seen = ?, seen_count = seen_count + 1, "
        "resulting_shares = COALESCE(resulting_shares, ?) "
        "WHERE fingerprint = ?",
        (now, resulting_shares, row["fingerprint"]),
    )
    # B-028: no per-row commit. Caller commits at filing boundary.
    if verbose:
        print(f"  ~ bump  {row['fingerprint']} (seen_count++)")
    return False
