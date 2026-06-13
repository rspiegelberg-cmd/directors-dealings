#!/usr/bin/env python3
"""
classify_small_cap.py -- set tickers_meta.small_cap based on market_cap_gbp threshold.

Zone B: Rupert runs from PowerShell. Reads and writes directors.db.

Logic:
  small_cap = 1   if market_cap_gbp IS NOT NULL AND market_cap_gbp < threshold
  small_cap = 0   if market_cap_gbp IS NOT NULL AND market_cap_gbp >= threshold
  small_cap = 0   (default, left unchanged) if market_cap_gbp IS NULL

Default threshold: GBP 500,000,000 (500m).

Usage:
    python .scripts\\classify_small_cap.py [--threshold 500000000] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db           # noqa: E402
import db_health    # noqa: E402

DEFAULT_THRESHOLD_GBP = 500_000_000   # 500m


def classify(conn, *, threshold: float, dry_run: bool) -> dict:
    """Apply small_cap classification. Returns summary counts."""
    rows = conn.execute(
        "SELECT ticker, market_cap_gbp FROM tickers_meta"
    ).fetchall()

    small_cap_tickers:  list[str] = []
    large_cap_tickers:  list[str] = []
    no_cap_tickers:     list[str] = []

    for row in rows:
        mc = row["market_cap_gbp"]
        if mc is None:
            no_cap_tickers.append(row["ticker"])
            continue
        if mc < threshold:
            small_cap_tickers.append(row["ticker"])
        else:
            large_cap_tickers.append(row["ticker"])

    if not dry_run:
        if small_cap_tickers:
            conn.executemany(
                "UPDATE tickers_meta SET small_cap = 1 WHERE ticker = ?",
                [(t,) for t in small_cap_tickers],
            )
        if large_cap_tickers:
            conn.executemany(
                "UPDATE tickers_meta SET small_cap = 0 WHERE ticker = ?",
                [(t,) for t in large_cap_tickers],
            )
        conn.commit()

    return {
        "threshold_gbp":    threshold,
        "small_cap_count":  len(small_cap_tickers),
        "large_cap_count":  len(large_cap_tickers),
        "no_cap_count":     len(no_cap_tickers),
        "total":            len(rows),
        "dry_run":          dry_run,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Classify tickers_meta.small_cap by market_cap_gbp threshold (B-138)."
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_GBP,
        help=f"Market-cap threshold in GBP (default: {DEFAULT_THRESHOLD_GBP:,.0f})",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print classification counts without writing to DB.",
    )
    args = ap.parse_args(argv)

    if not args.dry_run:
        if not db_health.check(db.DB_PATH):
            print("[classify_small_cap] FATAL: pre-run integrity_check failed.")
            return 2
        if not db_health.backup():
            print("[classify_small_cap] FATAL: backup failed. Refusing to proceed.")
            return 3

    conn = db.connect()
    try:
        summary = classify(conn, threshold=args.threshold, dry_run=args.dry_run)
    finally:
        conn.close()

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(
        f"{prefix}classify_small_cap: threshold=GBP {args.threshold:,.0f}\n"
        f"  {summary['small_cap_count']:>4} tickers classified small_cap=1  "
        f"(market_cap < GBP {args.threshold:,.0f})\n"
        f"  {summary['large_cap_count']:>4} tickers classified small_cap=0  "
        f"(market_cap >= GBP {args.threshold:,.0f})\n"
        f"  {summary['no_cap_count']:>4} tickers unclassifiable "
        f"(market_cap_gbp IS NULL)\n"
        f"  {summary['total']:>4} total tickers in tickers_meta"
    )
    if not args.dry_run:
        if not db_health.check(db.DB_PATH):
            print("[classify_small_cap] WARNING: post-run integrity check failed.")
            return 4
        try:
            db_health.seal()
        except Exception as exc:
            print("[db_health] seal failed (non-fatal):", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
