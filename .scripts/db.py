"""Stage 1/2 DB connector for the Directors Dealings rebuild.

Dual-backend (B-176): SQLite (default, unchanged) OR Postgres/Supabase,
selected at runtime by the ``DD_DATABASE_URL`` env var.

  * No ``DD_DATABASE_URL`` set  -> SQLite, exactly as before. Stdlib only;
    zero new dependencies. This is the local dev / FUSE-safe path.
  * ``DD_DATABASE_URL`` set      -> Postgres via psycopg v3 (lazily imported,
    so the SQLite path never needs psycopg installed).

The public surface is identical to the SQLite-only version so the ~67 caller
scripts keep working unchanged. (Porting caller SQL placeholders ?->%s /
INSERT OR REPLACE is a SEPARATE ticket, B-179 — out of scope here.)

Public surface:
    DB_DIR        -- Path to the .data\\ directory at the project root.
    DB_PATH       -- Path to the SQLite file (.data\\directors.db).
    SCHEMA_PATH   -- Path to the canonical SQLite SQL sidecar
                     (.scripts\\db_schema.sql).
    PG_SCHEMA_PATH-- Path to the consolidated Postgres schema
                     (.scripts\\pg_schema.sql).
    backend()     -- "postgres" if DD_DATABASE_URL is set, else "sqlite".
    iso_now()     -- UTC timestamp string, "%Y-%m-%dT%H:%M:%SZ".
    connect()     -- Open a live connection with the schema applied. Returns a
                     sqlite3.Connection (SQLite) or a psycopg.Connection with
                     row_factory=dict_row (Postgres). Both expose
                     ``.execute(sql, params).fetchall()`` and ``.commit()``.
    migrate(c)    -- Apply the schema to an open connection (idempotent).
                     SQLite: db_schema.sql + chained migrations. Postgres:
                     pg_schema.sql (the consolidated idempotent head).
    set_meta(c,k,v) / get_meta(c,k) -- Upsert / fetch key-value rows in `meta`.
    upsert_transaction(c,row,parser_source) -- idempotency core (fingerprint PK).
"""
from __future__ import annotations

import json
import os
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
PG_SCHEMA_PATH: Path = ROOT / ".scripts" / "pg_schema.sql"
MIGRATIONS_DIR: Path = ROOT / ".scripts" / "schema_migrations"

# The env var that flips the backend. Set it to a Postgres DSN
# (postgresql://user:pwd@host:5432/dbname) to use Postgres/Supabase.
DSN_ENV: str = "DD_DATABASE_URL"


def backend() -> str:
    """Return the active backend: 'postgres' if DD_DATABASE_URL is set, else 'sqlite'.

    Read live from the environment each call so tests can flip it with
    ``os.environ`` / ``unittest.mock.patch.dict`` without re-importing db.

    B-181 SAFETY GUARD: never let the test suite write to the cloud DB. Even if
    ``DD_DATABASE_URL`` is set, force SQLite when running under unittest
    (``sys.argv[0]`` is the unittest module) or when ``DD_FORCE_SQLITE`` is set.
    The real pipeline runs scripts directly (``argv[0]=.../<script>.py``), so
    production is unaffected. Added after a test run leaked fixtures into
    Supabase (2026-06-22).
    """
    if not os.environ.get(DSN_ENV):
        return "sqlite"
    import sys as _sys
    _argv0 = _sys.argv[0] if _sys.argv else ""
    if os.environ.get("DD_FORCE_SQLITE") or "unittest" in _argv0:
        return "sqlite"
    return "postgres"


def _ph() -> str:
    """Return the parameter placeholder for db.py's OWN internal SQL.

    SQLite uses ``?``; psycopg (Postgres) uses ``%s``. This applies ONLY to
    the queries written inside this module (set_meta/get_meta/upsert_transaction
    /excluded_ticker_set/_run_migration_step). Caller scripts port their own
    placeholders under B-179.
    """
    return "%s" if backend() == "postgres" else "?"


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


def connect():
    """Open a live DB connection with the schema applied (idempotent).

    Backend selected by ``DD_DATABASE_URL`` (see ``backend()``):

      * SQLite (default): identical to the historical behaviour — FKs on,
        row_factory = sqlite3.Row, schema + migrations applied.
      * Postgres: ``psycopg.connect(dsn, row_factory=dict_row)`` then
        ``migrate()`` (applies the consolidated pg_schema.sql idempotently).

    Both return a connection whose ``.execute(sql, params).fetchall()`` and
    ``.commit()`` work, and whose rows behave like dicts
    (``row["col"]``, ``row.keys()``, ``dict(row)``).
    """
    if backend() == "postgres":
        return _connect_postgres()
    return _connect_sqlite()


def _connect_sqlite() -> sqlite3.Connection:
    """Open the Directors Dealings SQLite DB with the schema applied."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def _escape_literal_percent(sql: str) -> str:
    """Double every literal ``%`` to ``%%`` so psycopg's parameter parser does
    not mis-read it as a placeholder (B-180 fix).

    psycopg scans ``%`` across the ENTIRE query (including inside SQL string
    literals) whenever parameters are bound, so ``LIKE 'FOO%'`` must become
    ``LIKE 'FOO%%'`` or it raises "only '%s','%b','%t' are allowed as
    placeholders". We leave an existing ``%s``/``%b``/``%t`` placeholder or an
    already-escaped ``%%`` untouched (db.py's own queries use ``%s`` and pass
    through this same path).
    """
    out = []
    i, n = 0, len(sql)
    while i < n:
        if sql[i] == "%":
            nxt = sql[i + 1] if i + 1 < n else ""
            if nxt in ("s", "b", "t", "%"):
                out.append(sql[i])
                out.append(nxt)
                i += 2
                continue
            out.append("%%")
            i += 1
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def translate_placeholders(sql: str) -> str:
    """Rewrite SQLite ``?`` placeholders to psycopg ``%s`` for the Postgres path.

    B-179: the caller scripts are written with SQLite ``?`` placeholders. Rather
    than hand-edit ~137 placeholders across ~40 files, the Postgres connection
    wraps every ``execute`` and rewrites the SQL on the way through. The SQLite
    path never calls this (it keeps native ``?``).

    SAFE rewrite — a ``?`` is only a placeholder when it is OUTSIDE a string
    literal. This tokenizer walks the SQL character by character and skips any
    ``?`` that sits inside:

      * a single-quoted string literal  '...'  (with '' escape)
      * a double-quoted identifier      "..."  (with "" escape)
      * a dollar-quoted block           $tag$...$tag$  (Postgres)
      * a line comment                  -- ... \\n
      * a block comment                 /* ... */

    This preserves literal ``?`` characters such as GLOB patterns
    (``'????-??-??*'``) and any JSON ``?`` operator inside a quoted string.
    Literal ``%`` ESCAPING (B-180): psycopg DOES interpret ``%`` across the whole
    query (even inside SQL string literals) when parameters are bound, so a
    literal ``%`` — e.g. ``LIKE 'FOO%'`` — is doubled to ``%%`` first via
    ``_escape_literal_percent`` (existing ``%s`` placeholders are left alone).
    Caveat: a LIKE pattern that literally needs ``%s`` (``LIKE '%s%'``) would be
    mis-read — none exist in this codebase. This function is only invoked when
    parameters are bound (see ``_PgCursor.execute``); with no params psycopg
    does not parse ``%`` and the SQL is passed through untouched.
    """
    sql = _escape_literal_percent(sql)
    out = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # ---- single-quoted string literal '...' (SQL standard '' escape) ----
        if ch == "'":
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == "'":
                    # '' is an escaped quote inside the literal.
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("''")
                        i += 2
                        continue
                    out.append("'")
                    i += 1
                    break
                out.append(sql[i])
                i += 1
            continue
        # ---- double-quoted identifier "..." ("" escape) ----
        if ch == '"':
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        out.append('""')
                        i += 2
                        continue
                    out.append('"')
                    i += 1
                    break
                out.append(sql[i])
                i += 1
            continue
        # ---- dollar-quoted block $tag$...$tag$ (Postgres) ----
        if ch == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                tag = sql[i:j + 1]            # e.g. "$$" or "$body$"
                end = sql.find(tag, j + 1)
                if end == -1:
                    out.append(sql[i:])
                    i = n
                    continue
                out.append(sql[i:end + len(tag)])
                i = end + len(tag)
                continue
            out.append(ch)
            i += 1
            continue
        # ---- line comment -- ... ----
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            nl = sql.find("\n", i)
            if nl == -1:
                out.append(sql[i:])
                i = n
                continue
            out.append(sql[i:nl + 1])
            i = nl + 1
            continue
        # ---- block comment /* ... */ ----
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                out.append(sql[i:])
                i = n
                continue
            out.append(sql[i:end + 2])
            i = end + 2
            continue
        # ---- a real placeholder ----
        if ch == "?":
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _attach_sql_note(exc, sql, params) -> None:
    """Attach the failing SQL (+ params) to an exception so Postgres-port
    dialect errors are instantly locatable (B-180). Uses ``exc.add_note``
    (Python 3.11+); a no-op on older interpreters."""
    try:
        note = "[db.py] failing SQL: " + " ".join(str(sql).split())[:600]
        if params is not None:
            note += "\n[db.py] params: " + repr(params)[:300]
        add = getattr(exc, "add_note", None)
        if callable(add):
            add(note)
    except Exception:
        pass


class _PgCursor:
    """Thin wrapper over a psycopg cursor that rewrites ``?`` -> ``%s``.

    Delegates everything else (fetchone/fetchall/rowcount/description/iteration/
    context-manager) to the wrapped psycopg cursor so caller code that uses
    ``conn.cursor()`` keeps working unchanged on Postgres.
    """

    __slots__ = ("_cur",)

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        # psycopg only interprets % / placeholders when params are bound. With
        # no params, pass the SQL through untouched (nothing to bind, and a
        # literal % must NOT be doubled). With params, translate ?->%s and
        # escape literal % so psycopg's parser doesn't choke (B-180).
        run_sql = sql if params is None else translate_placeholders(sql)
        try:
            if params is None:
                self._cur.execute(run_sql)
            else:
                self._cur.execute(run_sql, params)
        except Exception as exc:
            # B-180: attach the failing SQL (+ params) so dialect errors during
            # the Postgres port are instantly locatable instead of opaque.
            _attach_sql_note(exc, run_sql, params)
            raise
        return self

    def executemany(self, sql, seq_of_params):
        run_sql = translate_placeholders(sql)
        try:
            self._cur.executemany(run_sql, seq_of_params)
        except Exception as exc:
            _attach_sql_note(exc, run_sql, "<executemany>")
            raise
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()

    def __iter__(self):
        return iter(self._cur)

    def __enter__(self):
        self._cur.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cur.__exit__(*exc)

    def __getattr__(self, name):
        # rowcount, description, close, etc.
        return getattr(self._cur, name)


class _PgConnection:
    """Thin wrapper over a psycopg connection that gives caller scripts the
    same surface they use on SQLite, while transparently rewriting ``?`` -> ``%s``.

    Exposes ``.execute(sql, params)`` returning a cursor (sqlite3-style),
    ``.executemany(...)``, ``.cursor()`` returning a wrapped cursor, and
    delegates ``.commit()`` / ``.rollback()`` / ``.close()`` / everything else
    to the underlying psycopg connection.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = _PgCursor(self._conn.cursor())
        return cur.execute(sql, params)

    def executemany(self, sql, seq_of_params):
        cur = _PgCursor(self._conn.cursor())
        return cur.executemany(sql, seq_of_params)

    def cursor(self, *args, **kwargs):
        return _PgCursor(self._conn.cursor(*args, **kwargs))

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _connect_postgres():
    """Open a psycopg v3 connection to Postgres/Supabase with the schema applied.

    psycopg is imported lazily here so the SQLite path never requires it.
    ``row_factory=dict_row`` makes rows behave like dicts — index by column
    name (``row["col"]``), ``.keys()``, ``dict(row)``, and — unlike
    sqlite3.Row — ``.get()`` all work.

    B-179: the raw psycopg connection is wrapped in ``_PgConnection`` so caller
    SQL written with SQLite ``?`` placeholders runs unchanged (the wrapper
    rewrites ``?`` -> ``%s`` per-statement). ``migrate()`` is called on the
    wrapped connection — pg_schema.sql is parameter-free DDL so the rewrite is
    a no-op for it.
    """
    try:
        import psycopg  # psycopg v3
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            'Postgres backend selected (DD_DATABASE_URL set) but psycopg is '
            'not installed. Run:  pip install "psycopg[binary]"'
        ) from exc

    dsn = os.environ[DSN_ENV]
    raw = psycopg.connect(dsn, row_factory=dict_row)
    conn = _PgConnection(raw)
    migrate(conn)
    return conn


def migrate(conn) -> None:
    """Apply the schema to `conn` (idempotent on both backends).

    SQLite path (Step 1 + Step 2):
      Step 1: apply the base Stage 1 schema (idempotent CREATE/INSERT IGNORE).
      Step 2: walk the migration chain in `.scripts/schema_migrations/`.

    Postgres path:
      Execute the consolidated `pg_schema.sql` head in one shot. Every
      statement is `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT
      EXISTS` / `INSERT ... ON CONFLICT DO NOTHING`, so re-running on every
      connect() is safe. The migration-chain replay is a SQLite artefact;
      on Postgres we start from the consolidated head and add new
      migrations going forward (none yet).
    """
    if backend() == "postgres":
        _migrate_postgres(conn)
        return
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _apply_schema_migrations(conn)
    conn.commit()


def _migrate_postgres(conn) -> None:
    """Apply pg_schema.sql to a psycopg connection in one execute().

    psycopg v3 has no ``executescript``; instead, a single ``execute`` with
    NO parameters can contain multiple ``;``-separated statements (libpq
    simple-query protocol). pg_schema.sql is parameter-free DDL, so this
    works and stays idempotent.
    """
    sql = PG_SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
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

    SQLite-only: the migration chain is the SQLite schema-evolution
    mechanism. Postgres starts from the consolidated pg_schema.sql head and
    never calls this.
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
    Sprint 26 -- 2026-06-04. SQLite-only (see _run_migration_step note).
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
    """Chain forward-only schema migrations. Idempotent per step. SQLite-only.

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


def set_meta(conn, key: str, value: str) -> None:
    """Insert or update a row in `meta`.

    The ON CONFLICT ... DO UPDATE form is valid on both SQLite and Postgres;
    only the parameter placeholder differs (``?`` vs ``%s``).
    """
    ph = _ph()
    conn.execute(
        f"INSERT INTO meta (key, value) VALUES ({ph}, {ph}) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(conn, key: str) -> str | None:
    """Return the value stored in `meta` for `key`, or None if absent."""
    ph = _ph()
    row = conn.execute(
        f"SELECT value FROM meta WHERE key = {ph}", (key,)
    ).fetchone()
    return row["value"] if row else None


def table_exists(conn, name: str) -> bool:
    """Return True if a table named `name` exists. Backend-aware.

    B-179: replaces the ``SELECT 1 FROM sqlite_master WHERE type='table' AND
    name=?`` guards used by backtest.py (and similar). On Postgres ``sqlite_master``
    does not exist, so we query ``information_schema.tables`` instead. Both
    paths return a plain bool so callers can use ``if db.table_exists(...)``.
    """
    ph = _ph()
    if backend() == "postgres":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            f"WHERE table_schema = 'public' AND table_name = {ph}",
            (name,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT 1 FROM sqlite_master WHERE type='table' AND name = {ph}",
            (name,),
        ).fetchone()
    return row is not None


def excluded_ticker_set(conn) -> set[str]:
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


def upsert_transaction(conn, row: dict,
                       parser_source: str, *, verbose: bool = False) -> bool:
    """Insert or update a transaction row.

    Returns True if a new row was inserted; False if an existing row was
    updated (seen_count incremented, last_seen refreshed).

    This is the single canonical upsert used by run_scrape.py,
    backfill_filings.py, and any future ingest script. Keep logic here
    so bug fixes apply everywhere at once.

    Backend-aware: the SQL is identical apart from the parameter
    placeholder (``?`` for SQLite, ``%s`` for Postgres). The
    select-then-insert-or-update pattern (rather than a single
    INSERT ... ON CONFLICT) is kept verbatim so the seen_count/last_seen
    and COALESCE(resulting_shares) semantics are byte-for-byte identical
    on both backends.

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
    ph = _ph()
    now = iso_now()
    cur = conn.execute(
        f"SELECT seen_count FROM transactions WHERE fingerprint = {ph}",
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
        # 22 columns total; seen_count is the literal 1, so 21 bound params:
        # 3 before the literal (fingerprint, first_seen, last_seen) + 18 after.
        tail = ", ".join([ph] * 18)
        conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, role_normalized, type, shares, price, "
            "value, context, url, announced_at, cluster_id, first_time_buy, "
            "parser_source, buy_strictness, resulting_shares"
            f") VALUES ({ph}, {ph}, {ph}, 1, {tail})",
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
        f"UPDATE transactions SET last_seen = {ph}, seen_count = seen_count + 1, "
        f"resulting_shares = COALESCE(resulting_shares, {ph}) "
        f"WHERE fingerprint = {ph}",
        (now, resulting_shares, row["fingerprint"]),
    )
    # B-028: no per-row commit. Caller commits at filing boundary.
    if verbose:
        print(f"  ~ bump  {row['fingerprint']} (seen_count++)")
    return False
