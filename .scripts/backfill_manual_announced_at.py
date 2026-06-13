"""backfill_manual_announced_at.py

Zone-B script (Rupert runs from PowerShell — writes to directors.db).

For every manually-added transaction where announced_at IS NULL, sets
announced_at = date || "T00:00:00Z".  This is the minimum required for
eval_signals to include these rows in its universe (it filters
WHERE announced_at IS NOT NULL).

Also updates the role_normalized column for any row where the stored
role_normalized differs from the current normalize_role() output, so
that the DB display column stays consistent with the signal engine.

Usage:
    python .scripts/backfill_manual_announced_at.py
    python .scripts/backfill_manual_announced_at.py --dry-run

Safe to run multiple times (idempotent).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".scripts"))

import db  # noqa: E402
from role_normalize import normalize_role  # noqa: E402


def run(dry_run: bool = False) -> dict:
    conn = db.connect()

    # ── 1. Fix NULL announced_at for manual-source rows ─────────────────────
    rows_needing_at = conn.execute(
        "SELECT fingerprint, date FROM transactions "
        "WHERE announced_at IS NULL AND parser_source = 'manual' "
        "ORDER BY date ASC"
    ).fetchall()

    n_at_fixed = 0
    for row in rows_needing_at:
        new_at = row["date"] + "T00:00:00Z"
        if dry_run:
            print(f"[DRY-RUN] {row['fingerprint']} -> announced_at = {new_at}")
        else:
            conn.execute(
                "UPDATE transactions SET announced_at = ? WHERE fingerprint = ?",
                (new_at, row["fingerprint"]),
            )
        n_at_fixed += 1

    # ── 2. Re-normalise role_normalized for rows where it's stale ────────────
    # Covers ALL rows (not just manual), so abbreviations entered historically
    # in the role column get corrected in the display column too.
    all_rows = conn.execute(
        "SELECT fingerprint, role, role_normalized FROM transactions "
        "WHERE role IS NOT NULL AND role != ''"
    ).fetchall()

    n_role_fixed = 0
    for row in all_rows:
        correct = normalize_role(row["role"])
        if correct != row["role_normalized"]:
            if dry_run:
                print(f"[DRY-RUN] {row['fingerprint']} role_normalized "
                      f"{row['role_normalized']!r} -> {correct!r}  (role={row['role']!r})")
            else:
                conn.execute(
                    "UPDATE transactions SET role_normalized = ? WHERE fingerprint = ?",
                    (correct, row["fingerprint"]),
                )
            n_role_fixed += 1

    if not dry_run:
        conn.commit()
        print(f"[backfill_manual_announced_at] announced_at fixed: {n_at_fixed}")
        print(f"[backfill_manual_announced_at] role_normalized refreshed: {n_role_fixed}")
    else:
        print(f"[DRY-RUN] would fix announced_at for {n_at_fixed} row(s)")
        print(f"[DRY-RUN] would refresh role_normalized for {n_role_fixed} row(s)")

    conn.close()
    return {"n_at_fixed": n_at_fixed, "n_role_fixed": n_role_fixed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without touching the DB")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
