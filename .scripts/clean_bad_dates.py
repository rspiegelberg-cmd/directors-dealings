"""clean_bad_dates.py -- Remove transactions where date is implausibly after announced_at.

THE PROBLEM
-----------
When announced_at is correctly populated (via backfill_announced_at.py), some
transactions have a `date` (transaction date) that is many months or years
AFTER their `announced_at` (filing date).  That is impossible: a director
cannot file a transaction before it happens.  These records have a bad `date`
value caused by the parser's "latest-wins" fallback picking up a future date
mentioned in the filing (e.g. an option expiry, maturity date, or meeting
date) instead of the actual transaction date.

RULE
----
If  date > announced_at + TOLERANCE_DAYS  the record is bad and is deleted.

TOLERANCE_DAYS = 30.  In practice, a legitimate filing has announced_at
within 3 business days of date (UK MAR rules), so 30 days is very generous.

HOW TO RUN
----------
On Windows, from C:\\Dev\\DirectorsDealings:

    python .scripts\\clean_bad_dates.py [--dry-run] [--verbose]

--dry-run   Print what would be deleted without touching the DB.
--verbose   Print every transaction checked, not just the bad ones.

Idempotent: re-running after a partial run skips already-deleted rows.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

TOLERANCE_DAYS = 30


def run(dry_run: bool = False, verbose: bool = False) -> dict:
    stats = {
        "checked":    0,
        "ok":         0,
        "deleted":    0,
        "skipped_no_announced_at": 0,
    }

    conn = db.connect()
    try:
        # Only check rows where announced_at is populated -- we can't judge
        # without a filing date anchor.
        rows = conn.execute(
            "SELECT fingerprint, date, announced_at, ticker, director, type "
            "FROM transactions "
            "WHERE announced_at IS NOT NULL AND announced_at != '' "
            "ORDER BY date DESC"
        ).fetchall()

        to_delete: list[str] = []

        for r in rows:
            stats["checked"] += 1
            raw_date     = (r["date"] or "")[:10]
            raw_announced = (r["announced_at"] or "")[:10]

            if not raw_date or not raw_announced:
                stats["skipped_no_announced_at"] += 1
                continue

            try:
                tx_date  = date.fromisoformat(raw_date)
                ann_date = date.fromisoformat(raw_announced)
            except ValueError:
                stats["skipped_no_announced_at"] += 1
                continue

            gap = (tx_date - ann_date).days  # positive = tx date is after filing

            if gap > TOLERANCE_DAYS:
                print(
                    f"  BAD  {r['fingerprint']}  ticker={r['ticker']}"
                    f"  date={raw_date}  announced={raw_announced}"
                    f"  gap=+{gap}d  director={r['director']}"
                    f"  {'(dry-run)' if dry_run else ''}"
                )
                to_delete.append(r["fingerprint"])
                stats["deleted"] += 1
            else:
                stats["ok"] += 1
                if verbose:
                    print(
                        f"  OK   {r['fingerprint']}  {r['ticker']}"
                        f"  date={raw_date}  announced={raw_announced}"
                        f"  gap={gap:+d}d"
                    )

        if not dry_run and to_delete:
            for fp in to_delete:
                conn.execute(
                    "DELETE FROM signals WHERE fingerprint = ?", (fp,)
                )
                conn.execute(
                    "DELETE FROM transactions WHERE fingerprint = ?", (fp,)
                )
            conn.commit()
            print(f"\nDeleted {len(to_delete)} bad transactions "
                  f"(+ their signals) from the DB.")
    finally:
        conn.close()

    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Remove transactions whose date is implausibly after announced_at."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be deleted without modifying the DB.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every transaction checked.")
    args = ap.parse_args(argv)

    stats = run(dry_run=args.dry_run, verbose=args.verbose)

    print()
    print("=== clean_bad_dates summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("(dry-run: no changes made)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
