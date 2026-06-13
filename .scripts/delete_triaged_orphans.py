"""B-020 follow-up: targeted deletion of triaged orphan rows.

Reads the triage CSV produced by ``triage_orphans.py``, filters to the
classifications you specify (default: ``delete_safe_garbage``), and
deletes ONLY those fingerprints. The preview is shown EVERY run; the
DELETE only happens with ``--confirm``.

WHY THIS EXISTS
---------------
``reparse_corpus.py --confirm --delete-orphans`` deletes EVERY orphan
the reconcile() loop produces -- it can't be told "just these 207
specific ones". The triage CSV split orphans into 5 buckets and only
``delete_safe_garbage`` and ``delete_safe_zero_new`` are safe to remove.
This script bridges that gap: it deletes a HAND-PICKED set, defined by
the classifications you pass.

SAFETY MODEL
------------
1. Preview is the default. Running with no flags PRINTS what would be
   deleted but never touches the DB.
2. Preview shows the director string as ``repr(...)`` so embedded
   newlines and garbage (which Excel hides) are visible.
3. ``--confirm`` is required for the actual DELETE.
4. Every DELETE is wrapped in a single SQL transaction so a crash
   rolls back cleanly.
5. An audit log is written BEFORE the DELETE, so if anything goes
   wrong you can re-fetch the rows from cached HTML.
6. FK-safe order: signals -> paper_trades -> transactions.
7. ``db_health.seal()`` runs on success so the backup tracks the change.

USAGE (PowerShell, on Windows -- DB write is Zone B per CLAUDE.md)
-----------------------------------------------------------------

    # 1. Preview (default; nothing deleted)
    python .scripts/delete_triaged_orphans.py

    # 2. Preview with a custom CSV path (e.g., yesterday's triage)
    python .scripts/delete_triaged_orphans.py \\
        --triage-csv .data/_orphan_triage_2026-05-19.csv

    # 3. Preview AND deletion. Required after eyeballing the preview.
    python .scripts/delete_triaged_orphans.py --confirm

    # 4. Include the zero-new bucket too (use only if you've reviewed
    #    both classifications in the CSV and are sure).
    python .scripts/delete_triaged_orphans.py \\
        --classifications delete_safe_garbage,delete_safe_zero_new \\
        --confirm

AFTER DELETION
--------------
The cleanup invalidates downstream signal firings + paper trades. Run::

    python .scripts/eval_signals.py
    python .scripts/backtest.py
    python .scripts/build_dashboard.py

The script prints these as a reminder at the end of a successful
confirmed run.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / ".data"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

DEFAULT_CSV_NAME = f"_orphan_triage_{_date.today().isoformat()}.csv"
SAFE_CLASSIFICATIONS = {"delete_safe_garbage", "delete_safe_zero_new"}


def _load_triage(csv_path: Path,
                 classifications: set[str]) -> list[dict]:
    """Load only rows whose classification is in `classifications`."""
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("classification") in classifications:
                rows.append(row)
    return rows


def _print_preview(rows: list[dict], show_n: int = 30) -> None:
    """Print a preview of what would be deleted. Director shown as
    repr() so embedded newlines / garbage are visible (Excel hides
    them, which is why the post-Sprint-3 'clean-looking' orphans
    fooled the first spot-check)."""
    print()
    print("=== Preview of rows to be deleted ===")
    print("director shown as repr() so embedded \\n / labels are visible.")
    print("Excel hides those; the cell displays just the name prefix.")
    print()
    print(f"{'rns_id':12s}  {'ticker':6s}  {'date':10s}  director (repr)")
    print(f"{'-'*12:12s}  {'-'*6:6s}  {'-'*10:10s}  {'-'*40}")
    for r in rows[:show_n]:
        print(
            f"{r['rns_id']:12s}  "
            f"{(r.get('existing_ticker') or ''):6s}  "
            f"{(r.get('existing_date') or ''):10s}  "
            f"{r.get('existing_director', '')!r}"
        )
    if len(rows) > show_n:
        print(f"... and {len(rows) - show_n} more (full list in audit CSV "
              "once you --confirm)")


def _write_audit_log(rows: list[dict]) -> Path:
    """Snapshot every row about to be deleted -- written BEFORE the
    DELETE so a crash mid-delete still leaves a recovery trail."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    path = DATA_DIR / f"_deleted_orphans_{ts}.csv"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "rns_id", "fingerprint", "classification", "reason",
        "existing_date", "existing_ticker", "existing_director",
        "existing_company", "existing_type", "existing_shares",
        "n_new_rows",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})
    import os
    os.replace(tmp, path)
    return path


def run(triage_csv: Path, classifications: set[str], *,
        confirm: bool) -> int:
    if not triage_csv.exists():
        print(f"ABORT: triage CSV not found at {triage_csv}")
        print("       (run `python .scripts/triage_orphans.py` first)")
        return 2

    rows = _load_triage(triage_csv, classifications)
    print(f"Triage CSV:        {triage_csv}")
    print(f"Classifications:   {sorted(classifications)}")
    print(f"Matching rows:     {len(rows)}")

    # Refuse a non-safe classification unless the user passes
    # --force-non-safe. Defence against typos like 'investigate_no_table'
    # which would delete real director-dealing rows.
    unsafe = classifications - SAFE_CLASSIFICATIONS
    if unsafe:
        print(f"\nABORT: classifications {sorted(unsafe)} are not in the "
              f"safe list {sorted(SAFE_CLASSIFICATIONS)}.")
        print("       Pass --force-non-safe ONLY if you've manually reviewed "
              "the triage CSV and are sure.")
        return 3

    if not rows:
        print("\nNothing to delete.")
        return 0

    _print_preview(rows)

    if not confirm:
        print()
        print("=== Preview only ===")
        print("Re-run with --confirm to perform the deletion.")
        return 0

    # --- confirmed delete path ---
    audit_path = _write_audit_log(rows)
    print(f"\nAudit log written: {audit_path}")
    print("(this is your reversal trail -- keep it)")

    fingerprints = [r["fingerprint"] for r in rows]
    conn = db.connect()
    counts = {"signals": 0, "paper_trades": 0, "transactions": 0}
    try:
        conn.execute("BEGIN")
        try:
            # Chunk to avoid SQLite's per-statement parameter limit (999).
            CHUNK = 500
            for i in range(0, len(fingerprints), CHUNK):
                chunk = fingerprints[i:i + CHUNK]
                placeholders = ",".join("?" * len(chunk))
                # FK-safe order: dependents first.
                c1 = conn.execute(
                    f"DELETE FROM signals WHERE fingerprint IN ({placeholders})",
                    chunk,
                ).rowcount
                c2 = conn.execute(
                    f"DELETE FROM paper_trades WHERE fingerprint IN ({placeholders})",
                    chunk,
                ).rowcount
                c3 = conn.execute(
                    f"DELETE FROM transactions WHERE fingerprint IN ({placeholders})",
                    chunk,
                ).rowcount
                counts["signals"] += c1
                counts["paper_trades"] += c2
                counts["transactions"] += c3
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print()
        print("=== Deleted ===")
        for k, v in counts.items():
            print(f"  {k:20s} {v}")
        print()
        print("Next steps (run from PowerShell):")
        print("    python .scripts/eval_signals.py")
        print("    python .scripts/backtest.py")
        print("    python .scripts/build_dashboard.py")
        print("    python .scripts/audit_dates.py")
        # B-024: refresh the auto-backup.
        try:
            import db_health
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-script seal failed (non-fatal): {e}")
    finally:
        conn.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=("B-020 targeted deletion of triaged orphan rows. "
                     "Preview by default; --confirm to execute."),
    )
    ap.add_argument("--triage-csv", type=Path,
                    default=DATA_DIR / DEFAULT_CSV_NAME,
                    help=f"Path to the triage CSV (default: today's at "
                         f"{DATA_DIR / DEFAULT_CSV_NAME}).")
    ap.add_argument("--classifications", default="delete_safe_garbage",
                    help=("Comma-separated list of classification values to "
                          "include. Default: delete_safe_garbage. Add "
                          "delete_safe_zero_new only after reviewing both "
                          "buckets in the triage CSV."))
    ap.add_argument("--confirm", action="store_true",
                    help="Execute the DELETE. Default is preview-only.")
    ap.add_argument("--force-non-safe", action="store_true",
                    help=("Allow classifications outside the safe list. "
                          "Only use this if you've manually reviewed every "
                          "row in the CSV. Dangerous."))
    args = ap.parse_args(argv)

    classifications = {c.strip() for c in args.classifications.split(",") if c.strip()}
    # Override the safety guard only if explicitly requested.
    if args.force_non_safe:
        # Skip the safe-list check by adding all requested classifications
        # to SAFE_CLASSIFICATIONS for this run.
        SAFE_CLASSIFICATIONS.update(classifications)

    return run(args.triage_csv, classifications, confirm=args.confirm)


if __name__ == "__main__":
    sys.exit(main())
