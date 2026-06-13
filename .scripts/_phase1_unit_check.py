"""Sprint 11 Phase 1 — A.2 unit investigation. Read-only.

Samples 5 random BUY transactions from well-known FTSE tickers and
prints shares / price / value with the multiplied check, so we can
determine whether `transactions.price` is stored in pence or pounds.

PRAGMA query_only = ON enforces read-only at the SQLite level. Safe
to run at any time — does not touch the pipeline.

Run:
    python .scripts\_phase1_unit_check.py

Delete this file after Phase 1 completes (it's a one-shot diagnostic).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE.parent / ".data" / "directors.db"

if not DB_PATH.exists():
    print(f"FATAL: not found: {DB_PATH}")
    sys.exit(1)

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA query_only = ON")

KNOWN_FTSE = (
    "TSCO", "BARC", "HSBA", "VOD", "RIO", "BP", "AAL", "SHEL",
    "ULVR", "LSEG", "BATS", "GSK", "AZN", "REL", "DGE",
)
placeholders = ",".join("?" * len(KNOWN_FTSE))

rows = conn.execute(
    "SELECT ticker, director, date, shares, price, value, url "
    "FROM transactions "
    "WHERE type='BUY' AND price > 0 AND url LIKE '%investegate%' "
    f"  AND ticker IN ({placeholders}) "
    "ORDER BY RANDOM() LIMIT 5",
    KNOWN_FTSE,
).fetchall()

print(f"Sampled {len(rows)} BUY transactions.\n")
print(f"{'ticker':6} | {'date':>10} | {'shares':>9} | "
      f"{'price':>10} | {'value':>12} | {'sh*px':>12} | "
      f"{'ratio':>6}  url")
print("-" * 120)
for r in rows:
    sh = r["shares"] or 0
    px = r["price"] or 0
    val = r["value"] or 0
    calc = sh * px
    ratio = (val / calc) if calc else 0
    print(f"{r['ticker']:6} | {r['date']:>10} | {sh:>9} | "
          f"{px:>10.4f} | {val:>12.2f} | {calc:>12.2f} | "
          f"{ratio:>6.4f}  {r['url']}")

print()
print("HOW TO INTERPRET:")
print("  ratio close to 1.0000   -> shares, price, value are unit-consistent (price in £)")
print("  ratio close to 0.0100   -> price is pence; value already in £; need /100 on display")
print("  ratio mixed across rows -> mixed units; A.2 deferred to Sprint 12")

conn.close()
