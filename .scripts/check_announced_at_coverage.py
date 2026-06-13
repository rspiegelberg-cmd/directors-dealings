"""Read-only diagnostic: announced_at coverage report.

Run this any time after `python .scripts/backfill_announced_at.py` (or
just any time you want a sanity check on how many transactions have the
precise Investegate publication timestamp vs how many are still falling
back to the transaction date.

Read-only by design — opens directors.db with mode=ro and never writes,
so it is safe to run while the dashboard is up or even mid-pipeline.

Usage::

    python .scripts/check_announced_at_coverage.py
    python .scripts/check_announced_at_coverage.py --db .data/directors.db.pre-b001.bak
    python .scripts/check_announced_at_coverage.py --sample 20

The exit code is 0 when coverage is at or above the --warn-threshold
(default 99.0%) and 2 below, so you can wire this into a future
pre-flight check if you want — but most of the time you'll just read
the printed report.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_DB = ROOT / ".data" / "directors.db"


def _bar(pct: float, width: int = 40) -> str:
    """A tiny ASCII progress bar so the coverage % is easy to scan."""
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100.0 * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def run(db_path: Path, sample_n: int, warn_threshold: float) -> int:
    if not db_path.exists():
        print(f"ERROR: no such DB at {db_path}", file=sys.stderr)
        return 2

    # Read-only open. immutable=1 tells SQLite to skip lock/journal
    # checks, which is what we want for an inspection-only tool.
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    # ── Integrity quick-check ────────────────────────────────────────────────
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        print(f"WARNING: integrity_check returned {ok!r} — results below "
              "may be unreliable. Restore from a known-good backup before "
              "trusting these numbers.\n")

    # ── Top-line coverage ────────────────────────────────────────────────────
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN announced_at IS NULL OR announced_at = '' "
        "                THEN 1 ELSE 0 END) AS blank, "
        "       SUM(CASE WHEN announced_at IS NOT NULL AND announced_at <> '' "
        "                THEN 1 ELSE 0 END) AS populated "
        "FROM transactions"
    ).fetchone()
    total = row["total"] or 0
    blank = row["blank"] or 0
    populated = row["populated"] or 0
    pct = (100.0 * populated / total) if total else 0.0

    print(f"\nDB:           {db_path}")
    print(f"Transactions: {total:,}")
    print(f"Populated:    {populated:,}")
    print(f"Blank:        {blank:,}")
    print(f"Coverage:     {pct:5.1f}%  {_bar(pct)}")

    if total == 0:
        print("\n(Empty transactions table — nothing else to report.)")
        return 0

    # ── Date range of populated vs blank ─────────────────────────────────────
    pr = conn.execute(
        "SELECT MIN(date) AS oldest, MAX(date) AS newest "
        "FROM transactions "
        "WHERE announced_at IS NOT NULL AND announced_at <> ''"
    ).fetchone()
    br = conn.execute(
        "SELECT MIN(date) AS oldest, MAX(date) AS newest "
        "FROM transactions "
        "WHERE announced_at IS NULL OR announced_at = ''"
    ).fetchone()

    print("\nDate range:")
    if pr["oldest"]:
        print(f"  populated: {pr['oldest']}  ->  {pr['newest']}")
    else:
        print("  populated: (none)")
    if br["oldest"]:
        print(f"  blank:     {br['oldest']}  ->  {br['newest']}")
    else:
        print("  blank:     (none)")

    # ── Precision check on populated rows ────────────────────────────────────
    print("\nPrecision of populated rows:")
    rows = conn.execute(
        "SELECT CASE WHEN announced_at LIKE '%T%' "
        "       THEN 'with HH:MM:SS' ELSE 'date-only' END AS shape, "
        "       COUNT(*) AS n "
        "FROM transactions "
        "WHERE announced_at IS NOT NULL AND announced_at <> '' "
        "GROUP BY shape "
        "ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print(f"  {r['shape']:18s} {r['n']:>6,}")

    # ── Blank-row sample for spot-check ──────────────────────────────────────
    if blank > 0 and sample_n > 0:
        print(f"\nSample of blank rows (most recent {min(sample_n, blank)}):")
        print(f"  {'date':<12} {'ticker':<10} {'director':<28} url")
        print(f"  {'-'*12} {'-'*10} {'-'*28} {'-'*40}")
        sample = conn.execute(
            "SELECT date, ticker, director, url FROM transactions "
            "WHERE announced_at IS NULL OR announced_at = '' "
            "ORDER BY date DESC, ticker ASC LIMIT ?",
            (sample_n,),
        ).fetchall()
        for r in sample:
            director = (r["director"] or "")[:27]
            url = (r["url"] or "")[:38]
            print(f"  {r['date']:<12} {r['ticker'] or '-':<10} "
                  f"{director:<28} {url}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print()
    if pct >= warn_threshold:
        print(f"OK  — coverage at or above {warn_threshold}% threshold.")
        rc = 0
    else:
        print(f"BELOW THRESHOLD — coverage {pct:.1f}% is under "
              f"{warn_threshold:.1f}%.")
        print("Suggested action: run "
              "`python .scripts/backfill_announced_at.py --verbose`.")
        rc = 2

    conn.close()
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only report on announced_at coverage in directors.db."
    )
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="Path to directors.db (default: .data/directors.db).")
    ap.add_argument("--sample", type=int, default=10,
                    help="Number of blank-row examples to show (default: 10, "
                         "use 0 to skip).")
    ap.add_argument("--warn-threshold", type=float, default=99.0,
                    help="Coverage %% below which the script exits 2 "
                         "(default: 99.0).")
    args = ap.parse_args(argv)
    return run(Path(args.db), args.sample, args.warn_threshold)


if __name__ == "__main__":  # B-043 touch 2026-05-22
    sys.exit(main())
