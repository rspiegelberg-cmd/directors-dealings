"""B-094 -- One-off normalisation of non-ISO announced_at values in the DB.

Finds rows where announced_at is stored in 'DD Mon YYYY' headline format
(e.g. '02 Jun 2026') and converts them to ISO 'YYYY-MM-DD'.

Safe contract:
  - Only touches rows where announced_at does NOT already match YYYY-MM-DD
    (or is NULL / empty).
  - Uses a transaction; rolls back on any error.
  - Prints a before/after summary so you can verify the count.

Run from PowerShell:
    cd C:/Dev/DirectorsDealings
    python .scripts/normalize_announced_at.py

Add --dry-run to preview without writing:
    python .scripts/normalize_announced_at.py --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db

_HUMAN_DATE_FMTS = ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")

# Matches values that are already ISO (YYYY-MM-DD or YYYY-MM-DDTHH:MM...)
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _to_iso(s: str) -> str | None:
    """Convert 'DD Mon YYYY' to 'YYYY-MM-DD', or return None if unparseable."""
    s = s.strip()
    if _ISO_RE.match(s):
        return s[:10]  # already ISO
    for fmt in _HUMAN_DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description="B-094: normalise announced_at to ISO")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    args = parser.parse_args()

    conn = db.connect()

    # Find all non-ISO / non-empty announced_at values
    rows = conn.execute(
        "SELECT rowid, announced_at, ticker, date "
        "FROM transactions "
        "WHERE announced_at IS NOT NULL "
        "  AND announced_at != '' "
        "  AND announced_at NOT GLOB '????-??-??*'"
    ).fetchall()

    if not rows:
        print("No non-ISO announced_at rows found -- nothing to do.")
        conn.close()
        return

    print(f"Found {len(rows)} non-ISO announced_at row(s):")
    updates = []
    for r in rows:
        iso = _to_iso(r["announced_at"])
        status = f"-> {iso}" if iso else "UNPARSEABLE (will skip)"
        print(f"  rowid={r['rowid']:6d}  {r['announced_at']!r:20s}  {r['ticker']:8s}  {r['date']}  {status}")
        if iso:
            updates.append((iso, r["rowid"]))

    if args.dry_run:
        print(f"\nDry run: would update {len(updates)} row(s). Re-run without --dry-run to apply.")
        conn.close()
        return

    if not updates:
        print("No parseable rows to update.")
        conn.close()
        return

    print(f"\nApplying {len(updates)} update(s)...")
    try:
        with conn:
            for iso, rowid in updates:
                conn.execute(
                    "UPDATE transactions SET announced_at = ? WHERE rowid = ?",
                    (iso, rowid),
                )
        print(f"Done. {len(updates)} row(s) normalised to ISO format.")
    except Exception as exc:
        print(f"ERROR: {exc}. Transaction rolled back -- DB unchanged.")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
