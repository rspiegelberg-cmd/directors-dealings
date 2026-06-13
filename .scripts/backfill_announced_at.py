"""backfill_announced_at.py -- Populate announced_at for every transaction.

WHY
---
The announced_at column (the date/time the RNS filing appeared on Investegate)
has always been stored as an empty string for every transaction.  The scraper's
index-row timestamp regex never matched, and the archive backfill always set it
to None.

This script recovers the correct value from each filing's cached HTML.
Investegate embeds a JSON-LD block in every filing page:

    <script type="application/ld+json">
    {
        ...
        "dateCreated": "2025-05-01 07:01:36",
        ...
    }
    </script>

"dateCreated" is the exact UTC timestamp when Investegate published the filing.
It is present in every cached HTML file (confirmed across a sample of 2,630
filings).

HOW
---
Run ONCE on Windows (Zone B write -- DB must not be written through FUSE):

    python .scripts\backfill_announced_at.py [--dry-run] [--verbose]

--dry-run   Print what would change without touching the DB.
--verbose   Log every filing processed (not just changed ones).

The script:
  1. Builds a URL -> (fingerprint, current_announced_at) lookup from the DB.
  2. Walks every cached HTML file in .scripts/_scrape_cache/.
  3. Extracts "dateCreated" from the JSON-LD block.
  4. Matches the cache file's rns_id to any DB record whose URL contains
     that rns_id.
  5. If the record's announced_at is blank (or differs), updates it.

Idempotent: re-running after a partial run skips already-correct rows.

SAFETY
------
* Read-only scan of cached HTML -- no network calls.
* Writes only to the announced_at column of existing rows; no inserts or deletes.
* Each UPDATE is committed individually so a crash mid-run leaves the DB consistent.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import db_health  # noqa: E402

CACHE_DIR = HERE / "_scrape_cache"

# JSON-LD dateCreated -- present in Investegate filing page <head>.
# Format: "YYYY-MM-DD HH:MM:SS"
_DATE_CREATED_RE = re.compile(
    r'"dateCreated"\s*:\s*"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"'
)


def _extract_date_created(html: str) -> str | None:
    """Return ISO datetime string from JSON-LD dateCreated, or None."""
    m = _DATE_CREATED_RE.search(html)
    if not m:
        return None
    raw = m.group(1).strip()
    # Normalise to the same format we use elsewhere: "YYYY-MM-DDTHH:MM:SSZ"
    # dateCreated is already UTC on Investegate.
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def run(dry_run: bool = False, verbose: bool = False) -> dict:
    stats = {
        "html_files_scanned": 0,
        "no_date_created":    0,
        "no_db_record":       0,
        "already_correct":    0,
        "updated":            0,
        "errors":             0,
    }

    # B-036: single connection lifecycle. Previous code opened conn twice
    # in dry-run mode (once for the initial SELECT, closed, then never
    # reopened because writes are gated by `if not dry_run`). Wasted I/O
    # and a connection-leak hazard if the initial SELECT raised. Now:
    # open once, keep through both the SELECT and the main loop, close
    # in one finally regardless of dry_run.
    conn = None
    try:
        conn = db.connect()

        # Build rns_id -> list of (fingerprint, current_announced_at) from DB.
        rows = conn.execute(
            "SELECT fingerprint, url, announced_at FROM transactions "
            "WHERE url IS NOT NULL AND url != ''"
        ).fetchall()

        # Index by rns_id (numeric suffix in the Investegate URL).
        rns_to_rows: dict[str, list[dict]] = {}
        for r in rows:
            url = r["url"] or ""
            # URL ends with the numeric rns_id:
            # .../announcement/rns/company--ticker/headline/8855732
            m = re.search(r"/(\d+)(?:/|$)", url)
            if m:
                rns_id = m.group(1)
                rns_to_rows.setdefault(rns_id, []).append(dict(r))

        cache_files = sorted(CACHE_DIR.glob("*.html"))
        total = len(cache_files)
        print(f"Scanning {total} cached HTML files...")

        # B-029: previously committed once per UPDATE — 3,000+ separate
        # non-sequential SQLite writes per run = chronic FUSE risk surface.
        # Now wrap the whole loop in a single BEGIN IMMEDIATE / COMMIT.
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")

        for i, html_path in enumerate(cache_files):
            stats["html_files_scanned"] += 1
            rns_id = html_path.stem

            if verbose and i % 200 == 0:
                print(f"  [{i}/{total}] processed={stats['updated']} ...", flush=True)

            # Read just the first 3 KB -- dateCreated is always in <head>.
            try:
                with html_path.open(encoding="utf-8", errors="replace") as f:
                    head = f.read(3072)
            except OSError as e:
                if verbose:
                    print(f"  SKIP {rns_id}: read error {e}")
                stats["errors"] += 1
                continue

            announced_at = _extract_date_created(head)
            if not announced_at:
                stats["no_date_created"] += 1
                if verbose:
                    print(f"  SKIP {rns_id}: no dateCreated in HTML head")
                continue

            db_rows = rns_to_rows.get(rns_id)
            if not db_rows:
                stats["no_db_record"] += 1
                if verbose:
                    print(f"  SKIP {rns_id}: not in transactions table")
                continue

            for db_row in db_rows:
                current = db_row.get("announced_at") or ""
                if current == announced_at:
                    stats["already_correct"] += 1
                    if verbose:
                        print(f"  OK   {rns_id}: {announced_at} (unchanged)")
                    continue

                print(
                    f"  SET  {rns_id}: {repr(current)!s:<35} -> {announced_at}"
                    f"  {'(dry-run)' if dry_run else ''}"
                )
                if not dry_run:
                    conn.execute(
                        "UPDATE transactions SET announced_at = ? "
                        "WHERE fingerprint = ?",
                        (announced_at, db_row["fingerprint"]),
                    )
                    # B-029: no per-row commit — single commit at end-of-loop.
                stats["updated"] += 1

        # B-029: single commit after the full loop. If we got here without
        # an exception, every UPDATE since BEGIN IMMEDIATE persists atomically.
        if not dry_run:
            conn.commit()
    except Exception:
        if not dry_run and conn is not None:
            conn.rollback()
        raise
    finally:
        # B-036: close in BOTH dry-run and live paths — previously the
        # dry-run path closed conn early in an inner finally, so the
        # outer finally was gated on `not dry_run`. Now there's a single
        # path so the close must always run.
        if conn is not None:
            conn.close()

    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill announced_at from cached Investegate filing HTML."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without modifying the DB.")
    ap.add_argument("--verbose", action="store_true",
                    help="Log every filing processed.")
    args = ap.parse_args(argv)

    # B-024/B-029: db_health pattern — pre-run integrity check + backup
    # before destructive UPDATEs. Skipped in --dry-run (no DB writes).
    # Canonical reference: classify_issuers.py:run().
    if not args.dry_run:
        if not db_health.check(db.DB_PATH):
            print("[backfill_announced_at] FATAL: pre-run integrity_check "
                  "failed. Run start.bat to restore from .bak before retrying.")
            return 2
        if not db_health.backup():
            print("[backfill_announced_at] FATAL: failed to take "
                  "pre-backfill .bak. Refusing to proceed.")
            return 3

    stats = run(dry_run=args.dry_run, verbose=args.verbose)

    print()
    print("=== backfill_announced_at summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("(dry-run: no changes made)")
        return 0

    # B-024/B-029: post-run integrity check + seal. Skip seal on bad
    # post-run state so the pre-run .bak stays the rollback target.
    try:
        if not db_health.check(db.DB_PATH):
            print("[backfill_announced_at] WARNING: post-run "
                  "integrity_check failed. The pre-run .bak is valid — "
                  "restore via start.bat. Skipping seal to preserve good "
                  "backup.")
            return 4
        db_health.seal()
    except Exception as e:
        print(f"[db_health] post-backfill seal failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
