"""B-172 — Supabase connectivity + analysis-path spike.

Proves the Python data layer can talk to a hosted Supabase Postgres, which is
the foundation for the whole migration. Round-trips a tiny throwaway table.

Prerequisites (Rupert):
  1. Create a Supabase project (free tier).
  2. pip install "psycopg[binary]"
  3. Set the connection string as an env var. In the Supabase dashboard:
     Project Settings -> Database -> Connection string -> "URI" (psycopg).
     PowerShell:   $env:DD_DATABASE_URL = "postgresql://postgres:<pwd>@<host>:5432/postgres"

Then run:   python .scripts/spike_supabase_conn.py

After it passes, open the Supabase SQL Editor in the browser and run:
     select * from dd_spike_test;
...to confirm the data-analysis path (browser SQL) sees the same data. The
script leaves one row behind for exactly that check, then you can drop the
table with:  drop table dd_spike_test;

Exit 0 = PASS, exit 1 = FAIL (with the reason printed).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

DSN_ENV = "DD_DATABASE_URL"


def main() -> int:
    print("=" * 64)
    print("B-172 SUPABASE CONNECTIVITY SPIKE")
    print("=" * 64)

    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        print(f"[FAIL] env var {DSN_ENV} is not set.")
        print("       Set it to your Supabase connection URI (see file header).")
        return 1

    try:
        import psycopg  # psycopg v3
        from psycopg.rows import dict_row
    except ImportError:
        print('[FAIL] psycopg not installed. Run:  pip install "psycopg[binary]"')
        return 1

    stamp = datetime.now(timezone.utc).isoformat()
    try:
        with psycopg.connect(dsn, connect_timeout=15) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "create table if not exists dd_spike_test "
                    "(id bigint generated always as identity primary key, "
                    " note text, created_at text)"
                )
                cur.execute(
                    "insert into dd_spike_test (note, created_at) values (%s, %s)",
                    ("hello from spike_supabase_conn.py", stamp),
                )
                cur.execute(
                    "select id, note, created_at from dd_spike_test "
                    "order by id desc limit 1"
                )
                row = cur.fetchone()
            conn.commit()
    except Exception as exc:
        print(f"[FAIL] connection/round-trip error: {type(exc).__name__}: {exc}")
        return 1

    # dict_row proves the sqlite3.Row shim works: index by column name.
    print(f"[PASS] round-trip OK. Latest row id={row['id']} note={row['note']!r}")
    print("-" * 64)
    print("Next: open the Supabase SQL Editor and run:")
    print("        select * from dd_spike_test;")
    print("If you see the row there too, the data-analysis path is proven.")
    print("Clean up later with:  drop table dd_spike_test;")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
