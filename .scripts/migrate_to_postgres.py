"""B-177 — One-shot SQLite -> Postgres (Supabase) data migrator.

Reads the LOCAL SQLite database (`.data/directors.db`) and bulk-loads every
table into the Supabase Postgres whose schema was already created by
`pg_schema.sql` (B-175). Run this from Rupert's Windows PC — it needs the
local .db file, which only exists there.

USAGE (PowerShell, from C:\\dev\\directorsdealings):
    pip install "psycopg[binary]"                       # once
    $env:DD_DATABASE_URL = "postgresql://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:5432/postgres"
    python .scripts\\migrate_to_postgres.py --dry-run    # inspect plan, no writes
    python .scripts\\migrate_to_postgres.py              # do the load

Behaviour:
  * --dry-run: opens the SQLite DB read-only, prints per-table row counts and
    the column mapping. Touches Postgres only to read its column list (skipped
    if DD_DATABASE_URL is unset). NO writes anywhere.
  * full run: TRUNCATE ... RESTART IDENTITY CASCADE on all target tables, then
    COPY every row in. Re-runnable (truncates first). Resets identity sequences
    for tables with a generated `id`. Ends with a row-count PARITY report
    (SQLite count vs Postgres count per table) and exits non-zero on mismatch.

Source is opened read-only via sqlite3 (NOT db.connect) so no migrate() side
effects touch the local DB. FUSE-safe: pure reads of the local file.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Local sqlite path (same convention as db.py).
ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / ".data" / "directors.db"

DSN_ENV = "DD_DATABASE_URL"

# FK-safe load order: parents before children. `transactions` is the parent of
# signals / paper_trades / conviction_scores (FK on fingerprint).
TABLE_ORDER = [
    "transactions",
    "tickers_meta",
    "prices",
    "meta",
    "reporting_dates",
    "short_positions",
    "isin_ticker_map",
    "director_pay",
    "backtest_runs",
    "signals",
    "paper_trades",
    "conviction_scores",
]

# Tables whose `id` is a Postgres IDENTITY column needing a sequence reset.
IDENTITY_TABLES = ["short_positions", "director_pay"]


def open_sqlite() -> sqlite3.Connection:
    if not SQLITE_PATH.exists():
        sys.exit(f"[FAIL] local SQLite DB not found at {SQLITE_PATH}")
    con = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def sqlite_count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def pg_columns(pgcon, table: str) -> list[str]:
    with pgcon.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def plan_columns(scols: list[str], pcols: list[str]) -> list[str]:
    """Columns present in BOTH, ordered as in the SQLite table."""
    pset = set(pcols)
    return [c for c in scols if c in pset]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SQLite -> Postgres data migrator (B-177)")
    ap.add_argument("--dry-run", action="store_true", help="inspect plan, no writes")
    ap.add_argument("--batch", type=int, default=5000, help="COPY batch size for progress logging")
    args = ap.parse_args(argv)

    scon = open_sqlite()
    # which of the expected tables actually exist in the source
    existing = {
        r["name"]
        for r in scon.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    tables = [t for t in TABLE_ORDER if t in existing]
    missing = [t for t in TABLE_ORDER if t not in existing]
    if missing:
        print(f"[note] source has no rows/table for: {', '.join(missing)} (skipped)")

    dsn = os.environ.get(DSN_ENV)

    # ---- DRY RUN -----------------------------------------------------------
    if args.dry_run:
        print("=" * 68)
        print("DRY RUN — SQLite source inspection (no writes)")
        print(f"source: {SQLITE_PATH}")
        print("=" * 68)
        pgcon = None
        if dsn:
            import psycopg
            pgcon = psycopg.connect(dsn, connect_timeout=20)
        total = 0
        for t in tables:
            scols = sqlite_columns(scon, t)
            n = sqlite_count(scon, t)
            total += n
            if pgcon is not None:
                pcols = pg_columns(pgcon, t)
                cols = plan_columns(scols, pcols)
                dropped = [c for c in scols if c not in set(pcols)]
                extra = [c for c in pcols if c not in set(scols)]
                note = ""
                if dropped:
                    note += f"  SQLite-only(skip): {dropped}"
                if extra:
                    note += f"  PG-only(default): {extra}"
                print(f"  {t:<18} {n:>8,} rows | {len(cols)} cols copied{note}")
            else:
                print(f"  {t:<18} {n:>8,} rows | cols: {len(scols)} (set DD_DATABASE_URL to check mapping)")
        print("-" * 68)
        print(f"TOTAL source rows: {total:,}")
        if pgcon is not None:
            pgcon.close()
        scon.close()
        return 0

    # ---- REAL LOAD ---------------------------------------------------------
    if not dsn:
        sys.exit(f"[FAIL] {DSN_ENV} not set — set the Supabase connection string first.")
    import psycopg

    print("=" * 68)
    print("DATA MIGRATION — SQLite -> Postgres")
    print(f"source: {SQLITE_PATH}")
    print("=" * 68)

    with psycopg.connect(dsn, connect_timeout=30) as pgcon:
        # 1) clean slate (re-runnable). CASCADE handles FK order; RESTART IDENTITY
        #    zeroes the id sequences before we insert explicit ids.
        with pgcon.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
            )
        pgcon.commit()
        print(f"[ok] truncated {len(tables)} target tables")

        # 2) per-table COPY
        for t in tables:
            scols = sqlite_columns(scon, t)
            pcols = pg_columns(pgcon, t)
            cols = plan_columns(scols, pcols)
            collist = ", ".join(f'"{c}"' for c in cols)
            src = scon.execute(f"SELECT {collist} FROM {t}")
            copied = 0
            with pgcon.cursor() as cur:
                with cur.copy(f'COPY {t} ({collist}) FROM STDIN') as cp:
                    for row in src:
                        cp.write_row(tuple(row[c] for c in cols))
                        copied += 1
                        if copied % args.batch == 0:
                            print(f"    {t}: {copied:,} rows...", end="\r")
            pgcon.commit()
            print(f"[ok] {t:<18} copied {copied:,} rows" + " " * 12)

        # 3) reset identity sequences so future inserts don't collide
        with pgcon.cursor() as cur:
            for t in IDENTITY_TABLES:
                if t not in tables:
                    continue
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1), true)"
                )
        pgcon.commit()
        print("[ok] identity sequences reset")

        # 4) parity report
        print("-" * 68)
        print("PARITY CHECK (SQLite vs Postgres row counts)")
        ok = True
        with pgcon.cursor() as cur:
            for t in tables:
                s_n = sqlite_count(scon, t)
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                p_n = cur.fetchone()[0]
                match = "OK " if s_n == p_n else "MISMATCH"
                if s_n != p_n:
                    ok = False
                print(f"  [{match}] {t:<18} sqlite={s_n:>8,}  pg={p_n:>8,}")
        print("=" * 68)

    scon.close()
    if ok:
        print("RESULT: PASS — all tables match. Data is in Supabase.")
        return 0
    print("RESULT: FAIL — row-count mismatch above. Investigate before relying on the cloud DB.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
