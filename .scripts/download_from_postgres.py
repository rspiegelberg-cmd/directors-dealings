"""B-194 — Reverse migrator: Supabase Postgres -> local SQLite.

The mirror image of `migrate_to_postgres.py`. It bulk-DOWNLOADS every table
from Supabase into a fresh local SQLite database, so the heavy compute steps
(eval_signals / backtest / conviction) can run in-process against local SQLite
on a GitHub runner — avoiding the transatlantic per-query latency that times
the pipeline out when it talks to Supabase directly.

Daily-job flow (B-194):
    download_from_postgres.py        # Supabase -> local .data/directors.db
    refresh_all.py  (DD_FORCE_SQLITE=1)   # compute in-process, fast
    migrate_to_postgres.py           # local .data/directors.db -> Supabase

USAGE (PowerShell / runner):
    $env:DD_DATABASE_URL = "postgresql://...pooler.supabase.com:5432/postgres"
    python .scripts\\download_from_postgres.py                 # -> .data/directors.db
    python .scripts\\download_from_postgres.py --sqlite-path /tmp/test.db   # safe test

Behaviour:
  * Builds the target SQLite schema fresh via db.migrate() (head schema), then
    DELETEs + bulk-INSERTs every table in FK-safe order (parents first).
  * Ends with a row-count PARITY report (Postgres vs SQLite) and exits non-zero
    on any mismatch.
  * Reads Postgres directly via psycopg (NOT db.connect, which would pick the
    Postgres backend). Only the LOCAL sqlite is written.

Safe to test: pass --sqlite-path to write a throwaway DB instead of the real
local .data/directors.db. Reads Supabase read-only.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_SQLITE_PATH = ROOT / ".data" / "directors.db"
DSN_ENV = "DD_DATABASE_URL"

# Same FK-safe order as migrate_to_postgres.py (parents before children).
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


def pg_columns(pgcon, table: str) -> list[str]:
    with pgcon.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def pg_table_exists(pgcon, table: str) -> bool:
    with pgcon.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return cur.fetchone() is not None


def sqlite_columns(scon: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in scon.execute(f"PRAGMA table_info({table})").fetchall()]


def plan_columns(pcols: list[str], scols: list[str]) -> list[str]:
    """Columns present in BOTH, ordered as in Postgres."""
    sset = set(scols)
    return [c for c in pcols if c in sset]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Postgres -> SQLite reverse migrator (B-194)")
    ap.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH),
                    help="target SQLite file (default .data/directors.db; use /tmp/x.db to test)")
    ap.add_argument("--batch", type=int, default=10000, help="rows per insert batch")
    args = ap.parse_args(argv)

    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        sys.exit(f"[FAIL] {DSN_ENV} not set — set the Supabase connection string first.")

    import psycopg

    target = Path(args.sqlite_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Build the target schema fresh (head). db.migrate applies db_schema.sql +
    # the migration chain on a raw sqlite connection.
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    # Force the LOCAL target to be built as SQLite even though DD_DATABASE_URL is
    # set (we need that URL for the psycopg SOURCE connection). Without this,
    # db.migrate() sees backend()=="postgres" and tries the Postgres schema path
    # on our sqlite connection. db.connect() inside the pipeline also honours this.
    os.environ["DD_FORCE_SQLITE"] = "1"
    import db  # noqa: PLC0415
    scon = sqlite3.connect(str(target))
    scon.row_factory = sqlite3.Row
    db.migrate(scon)
    scon.execute("PRAGMA foreign_keys = OFF")  # bulk load; FK order handled by TABLE_ORDER

    print("=" * 68)
    print("DATA DOWNLOAD — Postgres -> SQLite")
    print(f"target: {target}")
    print("=" * 68)

    pgcon = psycopg.connect(dsn, connect_timeout=30)
    try:
        tables = [t for t in TABLE_ORDER if pg_table_exists(pgcon, t)]
        for t in tables:
            pcols = pg_columns(pgcon, t)
            scols = sqlite_columns(scon, t)
            cols = plan_columns(pcols, scols)
            sel = ", ".join(f'"{c}"' for c in cols)
            ph = ", ".join("?" for _ in cols)
            ins = f'INSERT INTO {t} ({", ".join(cols)}) VALUES ({ph})'

            scon.execute(f"DELETE FROM {t}")  # clean slate (clears any seed rows)
            copied = 0
            with pgcon.cursor(name=f"dl_{t}") as cur:  # server-side cursor = streamed
                cur.itersize = args.batch
                cur.execute(f"SELECT {sel} FROM {t}")
                while True:
                    rows = cur.fetchmany(args.batch)
                    if not rows:
                        break
                    scon.executemany(ins, rows)
                    copied += len(rows)
                    if copied % (args.batch * 5) == 0:
                        print(f"    {t}: {copied:,} rows...", end="\r")
            scon.commit()
            print(f"[ok] {t:<18} downloaded {copied:,} rows" + " " * 12)

        # Parity report
        print("-" * 68)
        print("PARITY CHECK (Postgres vs SQLite row counts)")
        ok = True
        with pgcon.cursor() as cur:
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                p_n = cur.fetchone()[0]
                s_n = scon.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                match = "OK " if p_n == s_n else "MISMATCH"
                if p_n != s_n:
                    ok = False
                print(f"  [{match}] {t:<18} pg={p_n:>8,}  sqlite={s_n:>8,}")
        print("=" * 68)
    finally:
        pgcon.close()
        scon.close()

    if ok:
        print("RESULT: PASS — local SQLite mirrors Supabase. Ready for in-process compute.")
        return 0
    print("RESULT: FAIL — row-count mismatch above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
