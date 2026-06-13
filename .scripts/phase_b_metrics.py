"""Sprint 9 Phase B — pre/post-state metrics dumper.

Read-only diagnostic. Prints transaction count + value-band counts so
Rupert can eyeball the impact of the reparse before/after running it.

Usage from PowerShell:
    python .scripts/phase_b_metrics.py
    python .scripts/phase_b_metrics.py --label PRE
    python .scripts/phase_b_metrics.py --label POST

Read-only on the DB (`SELECT COUNT` only); safe to run any time.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE.parent / ".data" / "directors.db"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--label", default="STATE",
        help="Heading label (e.g. PRE or POST).",
    )
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 2

    # Read-only via URI mode — defence-in-depth.
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        lt_1k = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE value < 1000"
        ).fetchone()[0]
        mid = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE value >= 1000 AND value <= 1000000"
        ).fetchone()[0]
        gt_1m = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE value > 1000000"
        ).fetchone()[0]
        zero_price = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE value = 0"
        ).fetchone()[0]
    finally:
        conn.close()

    print(f"--- {args.label} ---")
    print(f"  total rows:       {total}")
    print(f"  value < GBP 1k:   {lt_1k}  ({lt_1k * 100 / max(total, 1):.1f}%)")
    print(f"  value 1k..1m:     {mid}  ({mid * 100 / max(total, 1):.1f}%)")
    print(f"  value > GBP 1m:   {gt_1m}  ({gt_1m * 100 / max(total, 1):.1f}%)")
    print(f"  value = 0 rows:   {zero_price}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
