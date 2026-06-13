"""One-shot cleanup: delete the 594 corrupted reporting_dates rows from the
June 4th bad scraper run.

Root cause: on 2026-06-04 the scraper used Index.aspx?searchtype=RNSType
which returns the general all-companies feed (showing that day's date) rather
than the per-company page. This wrote "2026-06-04 INTERIM" for ~594 tickers.
Fixed on 2026-06-08 by switching to the per-company URL. This script deletes
the poisoned rows.

Safe: only deletes rows where fetched_at is on 2026-06-04 itself (the bad
run), preserving the 6 tickers that legitimately had an announcement on that
date (those were written by the corrected June 8th scraper and have a
different fetched_at).

Zone B -- Rupert runs from PowerShell. Never run from Claude bash.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / ".data" / "directors.db"

conn = sqlite3.connect(str(DB_PATH))
try:
    c = conn.execute(
        "DELETE FROM reporting_dates "
        "WHERE report_date = '2026-06-04' "
        "  AND source = 'investegate' "
        "  AND fetched_at >= '2026-06-04T00:00:00Z' "
        "  AND fetched_at  < '2026-06-05T00:00:00Z'"
    )
    deleted = c.rowcount
    conn.commit()
    print(f"Deleted {deleted} corrupted rows.")
    # Verify the 6 legitimate June 4 rows survived.
    survivors = conn.execute(
        "SELECT ticker, report_date, report_type, fetched_at "
        "FROM reporting_dates WHERE report_date = '2026-06-04'"
    ).fetchall()
    print(f"Remaining rows with date 2026-06-04: {len(survivors)}")
    for row in survivors:
        print(f"  {row[0]}  {row[1]}  {row[2]}  {row[3]}")
finally:
    conn.close()
print("Done.")
