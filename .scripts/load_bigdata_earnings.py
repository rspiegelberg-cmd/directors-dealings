"""Load forward earnings dates from a Bigdata.com export into reporting_dates.

WHAT THIS DOES (plain English)
------------------------------
Bigdata.com (https://bigdata.com) gives us a forward-looking UK earnings
calendar (sourced from Quartr). Claude fetched it and saved it to a plain
CSV: .scripts/imports/bigdata_earnings_calendar.csv

This script reads that CSV and writes the dates into the `reporting_dates`
table, but ONLY for tickers we actually hold and that are NOT excluded
investment trusts / closed-end funds. It is fully idempotent: every run
wipes the previous `source='bigdata'` rows and re-inserts, so re-running
never duplicates.

ZONE-B / FUSE NOTE
------------------
This is a WRITE-PATH script (it writes directors.db). Per the project's
two-zone rule it must be run by Rupert from Windows PowerShell, never by
Claude from the Linux sandbox.

USAGE
-----
    # 1) See what WOULD happen, no DB write:
    python .scripts/load_bigdata_earnings.py --dry-run

    # 2) Actually write:
    python .scripts/load_bigdata_earnings.py

    # 3) Snapshot for inspection (read-only):
    python .scripts/snapshot_db.py

Optional: --csv <path> to point at a different export file.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

SOURCE = "bigdata"
CONFIDENCE = "confirmed"  # Quartr-scheduled calendar events
DEFAULT_CSV = HERE / "imports" / "bigdata_earnings_calendar.csv"

VALID_TYPES = {"INTERIM", "FINAL", "TRADING_UPDATE", "EARNINGS",
               "PRELIM", "QUARTERLY", "TRADING_STMT"}


# ---------------------------------------------------------------------------
# Helpers (pure functions — unit-testable)
# ---------------------------------------------------------------------------
def read_csv(path: Path) -> list[dict]:
    """Read the Bigdata export. Expected columns:
    ticker, report_date, report_type, company_name, source_url
    """
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        tk = (r.get("ticker") or "").strip().upper()
        rd = (r.get("report_date") or "").strip()
        rt = (r.get("report_type") or "").strip().upper()
        if not tk or not rd or rt not in VALID_TYPES:
            continue
        out.append({
            "ticker": tk,
            "report_date": rd,
            "report_type": rt,
            "company_name": (r.get("company_name") or "").strip(),
            "source_url": (r.get("source_url") or "").strip() or None,
        })
    return out


def held_universe(conn) -> set[str]:
    """Tickers we hold that are NOT excluded issuers (IT/CEF) and not
    benchmark symbols. LEFT JOIN so a missing tickers_meta row is treated
    as 'not excluded' rather than dropping the ticker."""
    cur = conn.execute(
        "SELECT DISTINCT t.ticker "
        "FROM transactions t "
        "LEFT JOIN tickers_meta m ON m.ticker = t.ticker "
        "WHERE t.ticker NOT LIKE '^%' "
        "AND COALESCE(m.is_excluded_issuer, 0) = 0"
    )
    return {row[0] for row in cur.fetchall()}


def canonicalise(feed_ticker: str, held: set[str], held_nodot: dict) -> str | None:
    """Map a feed ticker to our canonical held ticker.
    Exact match first, then dot-insensitive (e.g. BT.A <-> BTA)."""
    if feed_ticker in held:
        return feed_ticker
    return held_nodot.get(feed_ticker.replace(".", ""))


def match_rows(records: list[dict], held: set[str]) -> tuple[list[tuple], dict]:
    """Return (rows_to_write, report). rows_to_write are deduped tuples
    (ticker, report_date, report_type, source, fetched_at, confidence, source_url)."""
    held_nodot = {t.replace(".", ""): t for t in held}
    fetched_at = db.iso_now()
    seen: set[tuple] = set()
    rows: list[tuple] = []
    matched_tickers: set[str] = set()
    unmatched_tickers: set[str] = set()

    for rec in records:
        canon = canonicalise(rec["ticker"], held, held_nodot)
        if canon is None:
            unmatched_tickers.add(rec["ticker"])
            continue
        matched_tickers.add(canon)
        key = (canon, rec["report_date"], rec["report_type"])
        if key in seen:
            continue
        seen.add(key)
        rows.append((canon, rec["report_date"], rec["report_type"],
                     SOURCE, fetched_at, CONFIDENCE, rec["source_url"]))

    report = {
        "feed_records": len(records),
        "rows_to_write": len(rows),
        "matched_tickers": len(matched_tickers),
        "unmatched_feed_tickers": len(unmatched_tickers),
        "held_total": len(held),
        "held_with_dates": len(matched_tickers),
        "sample_unmatched": sorted(unmatched_tickers)[:20],
    }
    return rows, report


def write_rows(conn, rows: list[tuple]) -> None:
    """Idempotent replace of all source='bigdata' rows."""
    conn.execute("DELETE FROM reporting_dates WHERE source = ?", (SOURCE,))
    conn.executemany(
        "INSERT OR REPLACE INTO reporting_dates "
        "(ticker, report_date, report_type, source, fetched_at, confidence, source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load Bigdata.com forward earnings into reporting_dates")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="Path to the Bigdata earnings CSV")
    ap.add_argument("--dry-run", action="store_true", help="Report only; do not write the DB")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[load_bigdata_earnings] FATAL: CSV not found: {csv_path}")
        return 2

    records = read_csv(csv_path)
    print(f"[load_bigdata_earnings] read {len(records)} valid rows from {csv_path.name}")

    conn = db.connect()
    try:
        held = held_universe(conn)
        rows, rep = match_rows(records, held)

        print("-" * 60)
        print(f"  feed records (valid)      : {rep['feed_records']}")
        print(f"  held universe (non-excl)  : {rep['held_total']}")
        print(f"  >> rows to write          : {rep['rows_to_write']}")
        print(f"  >> held tickers covered   : {rep['matched_tickers']} "
              f"({100*rep['matched_tickers']/max(rep['held_total'],1):.1f}% of held)")
        print(f"  feed tickers not held     : {rep['unmatched_feed_tickers']} "
              f"(ignored — not in our universe)")
        if rep["sample_unmatched"]:
            print(f"     e.g. {', '.join(rep['sample_unmatched'])}")
        print("-" * 60)

        if args.dry_run:
            print("[load_bigdata_earnings] DRY RUN — no changes written.")
            return 0

        if rep["rows_to_write"] == 0:
            print("[load_bigdata_earnings] ABORT: 0 rows matched; not wiping existing data.")
            return 1

        # Best-effort backup before write (reporting_dates is fully rebuildable,
        # so a backup hiccup is not fatal, but we try).
        try:
            import db_health  # noqa: E402
            if not db_health.backup():
                print("[load_bigdata_earnings] WARN: db_health.backup() returned False; continuing.")
        except Exception as exc:  # pragma: no cover
            print(f"[load_bigdata_earnings] WARN: backup skipped ({exc}); continuing.")

        write_rows(conn, rows)
        conn.commit()
        print(f"[load_bigdata_earnings] OK: wrote {rep['rows_to_write']} rows "
              f"(source='{SOURCE}').")
        print("[load_bigdata_earnings] Next: python .scripts/snapshot_db.py, "
              "then export_dashboard_json + build_dashboard to surface on the dashboard.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
