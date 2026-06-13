"""repair_dates.py -- One-time DB repair for wrong transaction dates.

WHAT THIS FIXES
---------------
Two bugs in parse_pdmr.py (now fixed) caused wrong data to be written:

Bug 1 -- Date regex missing optional "the":
  _TX_DATE_LABEL_RE only matched "Date of the transaction". Some issuers
  (e.g. Mondi) write "Date of transaction" (no "the"). The label-based
  extraction failed, triggering the latest-wins fallback, which always
  picked the announcement date (the highest date in the document) rather
  than the actual transaction date.

Bug 2 -- South African Rand not flagged as foreign currency:
  Filings traded on the JSE use "R203.30" format. "R" was not in the
  foreign-currency marker list, so ZAR prices were stored as GBP.

HOW TO USE
----------
Run AFTER applying the parse_pdmr.py fix:

    python .scripts/repair_dates.py [--dry-run] [--verbose]

--dry-run   Print what would change without touching the DB.
--verbose   Print every filing checked (not just changes).

The script:
  1. Iterates every cached HTML file.
  2. Re-parses with the fixed parser.
  3. For clean parses (no warnings), compares the new correct fingerprint
     against what is in the DB under the same URL.
  4. If they differ:
       a) Deletes the old wrong record.
       b) Inserts the new correct record.
  5. For filings now producing foreign_currency warnings (was wrongly
     stored before), the old record is deleted and moved to pending.

SAFETY NOTES
------------
* Skips any RNS ID where the cache file is absent.
* Uses a transaction per row-pair (delete + insert) so a crash mid-run
  leaves the DB consistent.
* Idempotent: re-running after a partial run will skip already-correct rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
import db_health
import parse_pdmr
import scrape_investegate as scraper

PENDING_PATH = HERE / "_pending_review.json"
PENDING_TMP_PATH = PENDING_PATH.with_suffix(".json.tmp")
CACHE_DIR = HERE / "_scrape_cache"


class _AbortAfterN(Exception):
    """Raised by the --abort-after-n test hook to simulate a mid-loop crash."""


def _load_pending() -> dict:
    if PENDING_PATH.exists():
        try:
            return json.loads(PENDING_PATH.read_text(encoding="utf-8")).get("items") or {}
        except Exception:
            return {}
    return {}


def _save_pending(items: dict) -> None:
    """Atomically write the pending file via tempfile + os.replace.

    B-006: callers may invoke this once per Case B row (so a mid-loop
    crash never strands a deleted row outside the pending file). The
    rename via `os.replace` is atomic on both POSIX and Windows. The
    tempfile lives in the same directory so the rename stays atomic.

    B-038 (2026-05-21): consolidated to use db.atomic_write_json.
    PENDING_TMP_PATH is retained at module scope because the
    orphan-cleanup logic below also references it.
    """
    payload = {"generated_at": db.iso_now(), "count": len(items), "items": items}
    db.atomic_write_json(PENDING_PATH, payload)


def _cleanup_orphan_tmp() -> None:
    """B-006: remove any leftover `.json.tmp` from a previously crashed run.

    A leftover tmp file is harmless on its own (the atomic rename never
    happened so the live file is unchanged), but cleaning it up at
    startup prevents disk clutter and keeps `--abort-after-n` tests
    deterministic.
    """
    try:
        if PENDING_TMP_PATH.exists():
            PENDING_TMP_PATH.unlink()
    except OSError:
        # Best-effort; failure to delete is non-fatal.
        pass


def _insert_transaction(conn, row: dict, parser_source: str) -> None:
    """Insert a repaired-date row via the canonical upsert.

    B-037 (2026-05-22): previously this used a raw INSERT OR IGNORE that
    bypassed `db.upsert_transaction`, which meant repaired rows lacked
    the `role_normalized` column populated by the canonical path. Routing
    through `db.upsert_transaction` keeps role_normalized in sync with
    the rest of the ingest surface.

    Returns None for caller compatibility. The caller is responsible for
    committing — `repair_dates.run()` uses `with conn:` blocks that
    commit on context exit (see B-028 callers-commit convention).
    """
    db.upsert_transaction(conn, row, parser_source)


def run(dry_run: bool = False, verbose: bool = False,
        abort_after_n: int | None = None) -> dict:
    """Walk every cached HTML filing and reconcile against the DB.

    B-006: `abort_after_n` is a test hook -- when set, raise `_AbortAfterN`
    immediately AFTER the Nth Case B deletion commits and its pending
    entry is written. Production callers leave it `None`. The hook lets
    `.scripts/test_repair_dates_atomicity.py` simulate a mid-loop crash
    and assert the pending file + DB end up in a recoverable state.
    """
    # B-006: clean any leftover `.json.tmp` from a previously crashed run
    # so the atomic-rename invariant holds.
    _cleanup_orphan_tmp()

    # Code-review fix C-2 (2026-05-20): take a fresh .bak BEFORE opening
    # for write. This script does cascading deletes across `transactions`,
    # `signals`, and `paper_trades`. A FUSE blip mid-loop would leave the
    # DB partially repaired with no fresh backup to fall back to.
    # Skip the snapshot in dry-run mode (read-only).
    if not dry_run:
        if not db_health.check(db.DB_PATH):
            print("[repair_dates] FATAL: pre-run integrity_check failed. "
                  "Run start.bat to restore from .bak before retrying.")
            raise RuntimeError("DB integrity check failed before repair")
        if not db_health.backup():
            print("[repair_dates] FATAL: failed to take pre-repair .bak. "
                  "Refusing to proceed (cascading writes).")
            raise RuntimeError("Pre-repair backup failed")

    conn = db.connect() if not dry_run else None
    # The actual work is wrapped in try/finally below so the SQLite
    # connection is ALWAYS closed even when _AbortAfterN raises mid-loop.
    # Without that, Windows holds the .db file lock and any subsequent
    # cleanup (e.g. the atomicity test's TemporaryDirectory.cleanup())
    # fails with WinError 32. This was an actual production hygiene gap
    # surfaced by test_repair_dates_atomicity.py.
    try:
        pending = _load_pending()

        stats = {
            "checked": 0,
            "already_correct": 0,
            "date_fixed": 0,
            "moved_to_pending": 0,
            "resumed_from_pending": 0,
            "no_db_record": 0,
            "no_cache": 0,
            "parse_failed": 0,
        }

        # Build a lookup: url -> (fingerprint, announced_at) from DB.
        if not dry_run:
            url_to_row = {
                r["url"]: dict(r)
                for r in conn.execute(
                    "SELECT fingerprint, url, date, announced_at FROM transactions "
                    "WHERE url IS NOT NULL AND url != ''"
                ).fetchall()
            }
        else:
            # Dry-run: read the DB read-only just for the lookup.
            dry_conn = db.connect()
            try:
                url_to_row = {
                    r["url"]: dict(r)
                    for r in dry_conn.execute(
                        "SELECT fingerprint, url, date, announced_at FROM transactions "
                        "WHERE url IS NOT NULL AND url != ''"
                    ).fetchall()
                }
            finally:
                dry_conn.close()

        # Walk every cached HTML file.
        cache_files = sorted(CACHE_DIR.glob("*.html"))
        total = len(cache_files)
        print(f"Scanning {total} cached filings...")

        for i, html_path in enumerate(cache_files):
            rns_id = html_path.stem
            stats["checked"] += 1

            if verbose and i % 500 == 0:
                print(f"  [{i}/{total}] ...", flush=True)

            try:
                html = html_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                if verbose:
                    print(f"  SKIP {rns_id}: read error {e}")
                stats["no_cache"] += 1
                continue

            # Derive URL from the DB record (we need it to re-parse correctly).
            # Find any DB row whose URL contains this rns_id.
            url_candidates = [u for u in url_to_row if rns_id in u]
            url = url_candidates[0] if url_candidates else ""
            db_row = url_to_row.get(url)

            if db_row is None:
                # No DB record for this RNS ID at all — skip.
                stats["no_db_record"] += 1
                continue

            announced_at = db_row.get("announced_at") or ""
            old_fp = db_row["fingerprint"]

            extracted, warnings, source = parse_pdmr.parse_announcement(
                html, url=url, rns_id=rns_id, announced_at=announced_at,
            )

            # Case A: parser now produces a clean result.
            if extracted and not warnings:
                new_fp = extracted[0]["fingerprint"]
                if new_fp == old_fp:
                    stats["already_correct"] += 1
                    if verbose:
                        print(f"  OK {rns_id}: fingerprint unchanged ({new_fp})")
                    continue

                # Fingerprint changed → date or shares was wrong. Fix it.
                ex = extracted[0]
                print(
                    f"  FIX {rns_id}: "
                    f"date {db_row['date']} -> {ex['date']}  "
                    f"fp {old_fp} -> {new_fp}  "
                    f"{'(dry-run)' if dry_run else ''}"
                )
                if not dry_run:
                    with conn:
                        # FK order: delete dependent rows BEFORE the parent
                        # transactions row, else FOREIGN KEY constraint fails.
                        conn.execute(
                            "DELETE FROM signals WHERE fingerprint = ?", (old_fp,)
                        )
                        conn.execute(
                            "DELETE FROM paper_trades WHERE fingerprint = ?", (old_fp,)
                        )
                        conn.execute(
                            "DELETE FROM transactions WHERE fingerprint = ?", (old_fp,)
                        )
                        _insert_transaction(conn, ex, source)
                stats["date_fixed"] += 1

            # Case B: parser now warns (e.g. foreign_currency) — old record was wrong.
            elif warnings and db_row:
                # B-006: crash-recovery guard. If a previous run crashed
                # AFTER writing pending but BEFORE committing the DB delete,
                # the row will be both in pending and in the DB. Detect and
                # skip -- the DB delete is what's left to do, but the row
                # has already been processed by intent. Logging it as
                # `resumed_from_pending` makes the inconsistency visible.
                if rns_id in pending:
                    stats["resumed_from_pending"] += 1
                    if verbose:
                        print(f"  SKIP {rns_id}: already in pending from earlier run")
                    continue

                print(
                    f"  PENDING {rns_id}: now flagged {warnings}  "
                    f"old fp={old_fp}  {'(dry-run)' if dry_run else ''}"
                )
                if not dry_run:
                    # B-006 ordering, defended by the scope: write the pending
                    # entry to disk BEFORE the DB DELETEs commit, so a crash
                    # between the two leaves the DB unchanged and the row
                    # safely captured in pending.
                    #
                    # `_save_pending` uses tempfile + `os.replace`, which is
                    # atomic on Windows (CLAUDE.md target platform) as well
                    # as POSIX, so there is no half-written-file failure mode.
                    pending[rns_id] = {
                        "url": url,
                        "warnings": warnings,
                        "extracted": extracted,
                        "parser_source": source,
                        "repair_note": "moved from transactions by repair_dates.py",
                    }
                    _save_pending(pending)

                    with conn:
                        # FK order: delete dependent rows BEFORE the parent
                        # transactions row, else FOREIGN KEY constraint fails.
                        conn.execute(
                            "DELETE FROM signals WHERE fingerprint = ?", (old_fp,)
                        )
                        conn.execute(
                            "DELETE FROM paper_trades WHERE fingerprint = ?", (old_fp,)
                        )
                        conn.execute(
                            "DELETE FROM transactions WHERE fingerprint = ?", (old_fp,)
                        )
                stats["moved_to_pending"] += 1

                # B-006: test hook. After the Nth successful Case B
                # delete-and-pending commit, raise to simulate a crash.
                if (abort_after_n is not None
                        and stats["moved_to_pending"] >= abort_after_n):
                    raise _AbortAfterN(
                        f"--abort-after-n {abort_after_n} hit after "
                        f"processing rns_id={rns_id}"
                    )

            else:
                stats["parse_failed"] += 1
                if verbose:
                    print(f"  WARN {rns_id}: parse failed entirely warnings={warnings}")

        if not dry_run:
            # Final defensive save -- a no-op if every Case B already saved
            # in-loop, but cheap insurance in case future code paths bypass
            # the per-row save.
            _save_pending(pending)

        return stats
    finally:
        # ALWAYS close the connection, including when _AbortAfterN raises.
        # Windows holds the .db file lock for the lifetime of the open
        # connection -- a leaked handle would block any subsequent
        # cleanup (test tearDown, manual file move, etc.) with WinError 32.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Repair wrong transaction dates in the DB.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without modifying the DB.")
    ap.add_argument("--verbose", action="store_true",
                    help="Log every filing checked.")
    ap.add_argument("--abort-after-n", type=int, default=None,
                    help=("TEST HOOK (B-006). Raise after the Nth Case B "
                          "delete-and-pending commit to simulate a "
                          "mid-loop crash. Production runs omit this."))
    args = ap.parse_args(argv)

    try:
        stats = run(dry_run=args.dry_run, verbose=args.verbose,
                    abort_after_n=args.abort_after_n)
    except _AbortAfterN as e:
        print(f"\nABORT: {e}")
        return 2
    print()
    print("=== repair_dates summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("(dry-run: no changes made)")
    else:
        # B-024: refresh the auto-backup. repair_dates can move many rows
        # to pending; without this the .bak would silently lag behind.
        # Code-review fix C-2 (2026-05-20): pre-snapshot is taken inside
        # run() — this is the post-run seal that pairs with it.
        # Post-run integrity check first; if it fails, the pre-run .bak
        # taken in run() is the rollback target.
        try:
            if not db_health.check(db.DB_PATH):
                print("[repair_dates] WARNING: post-run integrity_check "
                      "failed. The pre-run .bak is valid — restore via "
                      "start.bat. Skipping seal to preserve good backup.")
                return 4
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-script seal failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":  # noqa: E402  (B-037 touch 2026-05-22)
    sys.exit(main())
