"""Read-only DB -> text snapshot, so Claude can audit the data without touching
the SQLite binary over the FUSE mount (which serves truncated/stale reads).

WHY: `directors.db` is a binary file with random-access writes; the Windows->Linux
FUSE bridge mangles binary reads/writes. TEXT files (CSV/JSON) cross the bridge
cleanly. This script (run on Windows by Rupert) reads the DB **read-only** and
writes plain-text snapshots into `.data/_snapshots/`, which Claude then reads.

It opens the DB with `mode=ro` (URI), so it NEVER writes to the DB and NEVER runs
a migration. Output is text only. Safe to run any time.

    python .scripts/snapshot_db.py          # core tables + summary
    python .scripts/snapshot_db.py --full   # also dump the full prices table

Claude reads the result from:  .data/_snapshots/*.csv  and  summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".data" / "directors.db"
OUT_DIR = ROOT / ".data" / "_snapshots"

# Tables dumped in full (small, text-friendly). prices is summarised separately.
FULL_TABLES = ["transactions", "signals", "tickers_meta", "reporting_dates",
               "paper_trades", "meta"]


def _ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _dump_query(conn, query, params, out_path: Path) -> int:
    cur = conn.execute(query, params or [])
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    return len(rows)


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only DB -> text snapshot")
    ap.add_argument("--full", action="store_true",
                    help="also dump the full prices table (large)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = _ro_connect(DB_PATH)
    try:
        counts = {}
        for t in FULL_TABLES:
            if _table_exists(conn, t):
                counts[t] = _dump_query(conn, f"SELECT * FROM {t}", None,
                                        OUT_DIR / f"{t}.csv")

        # prices: coverage summary (per-ticker min/max/count), not the full table.
        if _table_exists(conn, "prices"):
            counts["prices_coverage"] = _dump_query(
                conn,
                "SELECT ticker, COUNT(*) AS n_rows, MIN(date) AS first_date, "
                "MAX(date) AS last_date FROM prices GROUP BY ticker ORDER BY ticker",
                None, OUT_DIR / "prices_coverage.csv")
            if args.full:
                counts["prices_full"] = _dump_query(
                    conn, "SELECT * FROM prices", None, OUT_DIR / "prices.csv")

        # summary.json — the at-a-glance numbers an audit usually starts from.
        summary = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "db_path": str(DB_PATH),
            "row_counts": counts,
        }
        if _table_exists(conn, "meta"):
            sv = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            summary["schema_version"] = sv[0] if sv else None
        if _table_exists(conn, "transactions"):
            summary["tx_by_type"] = dict(Counter(
                r[0] for r in conn.execute("SELECT type FROM transactions")))
            cols = {d[1] for d in conn.execute("PRAGMA table_info(transactions)")}
            if "price_audit" in cols:
                summary["tx_by_price_audit"] = dict(Counter(
                    (r[0] or "(none)") for r in
                    conn.execute("SELECT price_audit FROM transactions")))
            # price magnitude buckets (GBP/share)
            buckets = Counter()
            for (p,) in conn.execute(
                    "SELECT price FROM transactions WHERE price IS NOT NULL"):
                if p <= 0:
                    buckets["0"] += 1
                elif p < 1:
                    buckets["<1"] += 1
                elif p < 10:
                    buckets["1-10"] += 1
                elif p < 50:
                    buckets["10-50"] += 1
                elif p < 200:
                    buckets["50-200"] += 1
                else:
                    buckets[">200"] += 1
            summary["price_buckets_gbp"] = dict(buckets)
    finally:
        conn.close()

    with open(OUT_DIR / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"Snapshot written (read-only) -> {OUT_DIR}")
    for k, v in counts.items():
        print(f"  {k:18s} {v} rows")
    print(f"  schema_version: {summary.get('schema_version')}")
    print("\nClaude reads: .data/_snapshots/*.csv  and  summary.json")


if __name__ == "__main__":
    main()
