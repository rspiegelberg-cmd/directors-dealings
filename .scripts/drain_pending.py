"""Drain recoverable filings out of _pending_review.json into the DB.

WRITE-PATH SCRIPT — MUST BE RUN FROM WINDOWS POWERSHELL.
Never run this from Claude's Linux bash sandbox: it opens directors.db
for writing and rewrites _pending_review.json, both Zone-B files. The
FUSE mount truncates non-sequential SQLite writes and has corrupted this
project's DB four times. Run it as:

    cd C:\\Dev\\DirectorsDealings
    python .scripts\\drain_pending.py --dry-run     # report only, no writes
    python .scripts\\drain_pending.py               # for-real ingest + prune

What it does (Phase 2 of the 2026-06-02 ingest-gate incident fix):

  * Reads .scripts/_pending_review.json (the backlog ledger).
  * Re-validates every stored row through the SAME gate helpers used by
    run_scrape.py (`_row_is_ingestable`) — single source of truth, no
    duplicated classification logic.
  * Inserts the now-ingestable rows via db.upsert_transaction (the
    canonical insert + fingerprint path), so it is idempotent: a row
    already in the DB just bumps seen_count, it does not double-insert.
  * Prunes drained filings from _pending_review.json — a filing is
    removed only once ALL its rows have either been ingested or are no
    longer blocked. Filings that still have blocked rows keep only those
    rows.

Idempotency: safe to run repeatedly. Fingerprints dedupe at the DB level;
already-drained filings are gone from pending so they are not revisited.

CLI:
    --dry-run   -- report what WOULD ingest/prune; no DB or JSON writes.
    --verbose   -- per-row logging.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
import db_health
import run_scrape  # reuse the gate helpers + pending IO — single source of truth


def _classify_filing(entry: dict) -> tuple:
    """Split one pending entry's rows into (ingestable, still_blocked).

    Uses run_scrape._row_is_ingestable so the drain and the live scrape
    apply byte-identical gate logic. `entry["warnings"]` is the
    filing-level warning list the scraper stored when it parked the
    filing; we pass it to the gate exactly as the live path would.
    """
    warnings = entry.get("warnings") or []
    rows = entry.get("extracted") or []
    ingestable = []
    blocked = []
    for r in rows:
        if run_scrape._row_is_ingestable(r, warnings):
            ingestable.append(r)
        else:
            blocked.append(r)
    return ingestable, blocked


def run(args) -> int:
    verbose = args.verbose
    dry = args.dry_run

    pending = run_scrape._load_pending()
    if not pending:
        print("[drain] _pending_review.json is empty — nothing to do.")
        return 0

    # Pre-write integrity check + backup (mirrors run_scrape's db_health
    # pattern). Skipped on --dry-run since no writes happen.
    conn = None
    if not dry:
        if not db_health.check(db.DB_PATH):
            print("[drain] FATAL: pre-run integrity_check failed. "
                  "Restore from .bak via start.bat before retrying.")
            return 2
        if not db_health.backup():
            print("[drain] FATAL: failed to take pre-drain .bak. "
                  "Refusing to proceed (destructive INSERTs ahead).")
            return 3
        conn = db.connect()

    filings_seen = len(pending)
    rows_ingested = 0          # new inserts
    rows_bumped = 0            # already-present (idempotent re-run)
    filings_pruned = 0
    filings_partial = 0
    excluded_tickers = set()

    try:
        if conn is not None:
            excluded_tickers = run_scrape._load_excluded_tickers(conn)
        else:
            _tmp = db.connect()
            try:
                excluded_tickers = run_scrape._load_excluded_tickers(_tmp)
            finally:
                _tmp.close()

        # Iterate over a snapshot of keys so we can mutate `pending` safely.
        for rns_id in list(pending.keys()):
            entry = pending[rns_id]
            source = entry.get("parser_source") or "llm"
            ingestable, blocked = _classify_filing(entry)

            if not ingestable:
                # Nothing recoverable in this filing — leave it untouched.
                continue

            kept = []
            for r in ingestable:
                if r.get("ticker") in excluded_tickers:
                    if verbose or dry:
                        print(f"  EXCLUDE {rns_id}: {r.get('ticker')} (IT/CEF — skip)")
                    continue
                kept.append(r)

            for r in kept:
                if dry:
                    print(f"  DRY {rns_id}: would ingest "
                          f"{r.get('fingerprint')} {r.get('ticker')} "
                          f"{r.get('type')} {r.get('shares')} "
                          f"value={r.get('value')}")
                else:
                    if db.upsert_transaction(conn, r, source, verbose=verbose):
                        rows_ingested += 1
                    else:
                        rows_bumped += 1
            if kept and not dry:
                conn.commit()

            # Prune logic: a filing leaves pending only when no blocked rows
            # remain. If some rows are still blocked, keep ONLY those.
            if blocked:
                filings_partial += 1
                if not dry:
                    entry["extracted"] = blocked
                    pending[rns_id] = entry
            else:
                filings_pruned += 1
                if not dry:
                    del pending[rns_id]

    finally:
        if not dry:
            # Single write of the mutated dict — no RMW in any loop.
            run_scrape._write_pending(pending)
        if conn is not None:
            conn.close()

    print(
        f"\n[drain] filings_seen={filings_seen}, "
        f"rows_ingested={rows_ingested}, rows_bumped={rows_bumped}, "
        f"filings_pruned={filings_pruned}, filings_partial={filings_partial}"
        + ("  (DRY-RUN — no writes)" if dry else "")
    )

    if not dry:
        try:
            if not db_health.check(db.DB_PATH):
                print("[drain] WARNING: post-run integrity_check failed. "
                      "The pre-run .bak is valid — restore via start.bat. "
                      "Skipping seal to preserve good backup.")
                return 4
            db_health.seal()
        except Exception as e:
            print(f"[drain] post-drain seal failed (non-fatal): {e}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Drain recoverable filings from _pending_review.json"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
