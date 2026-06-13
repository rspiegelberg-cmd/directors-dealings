"""Dump all tickers + company names for manual review.

Helper for the Sprint 2 / B-011 classification when Sources A (AIC) and
B (Yahoo) are unavailable. Writes a single CSV the user can scan for
named-trust false negatives that the conservative regex (Source C)
won't catch — Scottish Mortgage (SMT), Pershing Square (PSH),
Caledonia (CLDN), and similar.

Output: .data/_review_candidates.csv
Columns:
    ticker, company, tx_count, is_excluded_issuer, excluded_source
Sorted by:
    is_excluded_issuer ASC (unflagged first), tx_count DESC
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

OUT_CSV = HERE.parent / ".data" / "_review_candidates.csv"


def main() -> int:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT m.ticker, "
            "       COALESCE(t.company, '') AS company, "
            "       COALESCE(t.tx_count, 0) AS tx_count, "
            "       m.is_excluded_issuer, "
            "       COALESCE(m.excluded_source, '') AS excluded_source "
            "FROM tickers_meta m "
            "LEFT JOIN ("
            "  SELECT ticker, "
            "         COUNT(*) AS tx_count, "
            "         MAX(company) AS company "
            "  FROM transactions GROUP BY ticker"
            ") t ON t.ticker = m.ticker "
            "ORDER BY m.is_excluded_issuer ASC, t.tx_count DESC, m.ticker ASC"
        ).fetchall()
    finally:
        conn.close()

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "company", "tx_count",
                    "is_excluded_issuer", "excluded_source"])
        for r in rows:
            w.writerow([r["ticker"], r["company"], r["tx_count"],
                        r["is_excluded_issuer"], r["excluded_source"]])
    tmp.replace(OUT_CSV)

    excluded = sum(1 for r in rows if r["is_excluded_issuer"])
    print(f"Wrote {OUT_CSV} — {len(rows)} tickers "
          f"({excluded} already flagged for exclusion, "
          f"{len(rows) - excluded} unflagged).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
