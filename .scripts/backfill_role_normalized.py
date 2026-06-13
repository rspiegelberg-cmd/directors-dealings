"""One-shot backfill: populate transactions.role_normalized for all rows.

ZONE B SCRIPT — must be run from Windows PowerShell, NOT from Claude's
Linux bash sandbox. Writes to .data/directors.db which is FUSE-corruption-
unsafe over the Linux mount.

What it does, in order:
    1. PRAGMA integrity_check on the DB. Aborts if not "ok".
    2. Takes a fresh snapshot to .data/directors.db.bak-pre-role-normalize-YYYYMMDD
       (the auto-backup is broken — do not rely on .bak).
    3. Applies migration 004 if not already applied (db.connect handles this).
    4. Single transaction UPDATE: sets role_normalized for every row.
    5. PRAGMA integrity_check again.
    6. Prints bucket distribution.
    7. Verifies acceptance floors: CEO ≥ 350, NED ≥ 450, CFO ≥ 200,
       Other ≤ 5%, Parser fragment ≤ 5%. Exits non-zero if any fail.

Usage:
    python .scripts\\backfill_role_normalized.py            # apply
    python .scripts\\backfill_role_normalized.py --dry-run  # report only

Idempotent: re-running this script unconditionally rewrites role_normalized
for every row, so a future mapper change can be re-applied cleanly.

Spec: docs/specs/role-normalization-pass.md (B-025 Phase A).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# Ensure .scripts/ is on the import path when run as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from role_normalize import normalize_role, BUCKETS  # noqa: E402


# Acceptance floors from the spec.
ACCEPTANCE_FLOORS = {
    "CEO": 350,
    "NED": 450,
    "CFO": 200,
}
ACCEPTANCE_CEILINGS_PCT = {
    "Other / unclassified": 5.0,
    "Parser fragment": 5.0,
}


def _check_integrity(conn) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "unknown"


def _take_snapshot() -> Path:
    """Copy .data/directors.db to a pre-run snapshot. Returns the new path."""
    today = date.today().strftime("%Y%m%d")
    target = db.DB_DIR / f"directors.db.bak-pre-role-normalize-{today}"
    if target.exists():
        # Don't clobber a same-day snapshot — append a counter.
        counter = 1
        while True:
            candidate = db.DB_DIR / (
                f"directors.db.bak-pre-role-normalize-{today}-{counter}"
            )
            if not candidate.exists():
                target = candidate
                break
            counter += 1
    shutil.copy2(db.DB_PATH, target)
    return target


def _bucket_counts(conn) -> Counter:
    counts = Counter()
    cur = conn.execute(
        "SELECT role_normalized, COUNT(*) FROM transactions "
        "GROUP BY role_normalized",
    )
    for bucket, n in cur.fetchall():
        counts[bucket] = n
    return counts


def _project_counts(conn) -> Counter:
    """Compute what the distribution WOULD be — without writing."""
    counts = Counter()
    cur = conn.execute("SELECT role, COUNT(*) FROM transactions GROUP BY role")
    for raw, n in cur.fetchall():
        counts[normalize_role(raw)] += n
    return counts


def _print_distribution(counts: Counter, total: int) -> None:
    print()
    print(f"{'Bucket':<45} {'Count':>6}  {'%':>5}")
    print("-" * 65)
    for b in BUCKETS:
        c = counts.get(b, 0)
        pct = 100 * c / total if total else 0
        print(f"{b:<45} {c:>6}  {pct:>4.1f}%")
    print("-" * 65)
    print(f"{'TOTAL':<45} {total:>6}")


def _check_acceptance(counts: Counter, total: int) -> list[str]:
    """Return a list of failure strings. Empty list = all pass."""
    failures: list[str] = []
    for bucket, floor in ACCEPTANCE_FLOORS.items():
        if counts.get(bucket, 0) < floor:
            failures.append(
                f"  FAIL {bucket} = {counts.get(bucket, 0)} (floor {floor})",
            )
    for bucket, ceiling_pct in ACCEPTANCE_CEILINGS_PCT.items():
        pct = 100 * counts.get(bucket, 0) / total if total else 0
        if pct > ceiling_pct:
            failures.append(
                f"  FAIL {bucket} = {pct:.1f}% (ceiling {ceiling_pct:.1f}%)",
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what the distribution would be without writing.",
    )
    parser.add_argument(
        "--no-snapshot", action="store_true",
        help="Skip the pre-run .bak snapshot. Use only for development.",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Open the DB (this applies migration 004 if not already applied).
    # ------------------------------------------------------------------
    print(f"Opening {db.DB_PATH} ...")
    conn = db.connect()
    try:
        # 1. Pre-run integrity check
        result = _check_integrity(conn)
        if result != "ok":
            print(f"FATAL: pre-run PRAGMA integrity_check = {result!r}")
            return 2
        print("Pre-run integrity_check: ok")

        # 2. Snapshot before any write
        if args.dry_run:
            print("DRY RUN — no snapshot taken, no rows will be written.")
            total = conn.execute(
                "SELECT COUNT(*) FROM transactions",
            ).fetchone()[0]
            projected = _project_counts(conn)
            _print_distribution(projected, total)
            failures = _check_acceptance(projected, total)
            print()
            if failures:
                print("Acceptance floor checks: FAIL")
                for f in failures:
                    print(f)
                return 3
            print("Acceptance floor checks: PASS (all 5 conditions)")
            return 0

        if not args.no_snapshot:
            snapshot_path = _take_snapshot()
            print(f"Snapshot taken: {snapshot_path}")

        # 3. Single-transaction UPDATE
        # Pull every (fingerprint, role) pair, compute role_normalized in
        # Python, then UPDATE in one batch inside one transaction. This
        # is the QA-recommended atomic pattern.
        rows = conn.execute(
            "SELECT fingerprint, role FROM transactions",
        ).fetchall()
        total = len(rows)
        print(f"Backfilling {total} rows ...")

        updates: list[tuple[str, str]] = []
        for r in rows:
            fingerprint = r["fingerprint"]
            normalized = normalize_role(r["role"])
            updates.append((normalized, fingerprint))

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "UPDATE transactions SET role_normalized = ? "
                "WHERE fingerprint = ?",
                updates,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            print("FATAL: backfill UPDATE failed. Rolled back. No rows changed.")
            raise

        # 4. Post-run integrity check
        result = _check_integrity(conn)
        if result != "ok":
            print(f"FATAL: post-run PRAGMA integrity_check = {result!r}")
            print(
                "Restore from snapshot: copy "
                f"{snapshot_path} over .data/directors.db",
            )
            return 4
        print("Post-run integrity_check: ok")

        # 5. Verify every row got populated
        null_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE role_normalized IS NULL",
        ).fetchone()[0]
        if null_count:
            print(f"FATAL: {null_count} rows still have NULL role_normalized")
            return 5

        # 6. Bucket distribution + acceptance gates
        counts = _bucket_counts(conn)
        _print_distribution(counts, total)
        failures = _check_acceptance(counts, total)
        print()
        if failures:
            print("Acceptance floor checks: FAIL")
            for f in failures:
                print(f)
            print()
            print(
                "The data is written but the distribution is suspicious. "
                "Inspect the buckets before relying on the column. To roll "
                f"back: copy {snapshot_path} over .data/directors.db.",
            )
            return 6
        print("Acceptance floor checks: PASS (all 5 conditions)")

        print()
        print("Backfill complete. role_normalized is now populated for all "
              f"{total} rows.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
