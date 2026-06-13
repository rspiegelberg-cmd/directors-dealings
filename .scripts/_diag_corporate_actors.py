"""_diag_corporate_actors.py — B-136 review aid (READ-ONLY, Claude-safe).

Lists the distinct actor names that the B-136 corporate heuristic
(`role_normalize.is_corporate_actor`) would EXCLUDE from scoring, with their
transaction counts — so the flagged set can be eyeballed for FALSE POSITIVES
(a real individual with a corporate-sounding surname) BEFORE an eval rebuild
shifts any firing counts. Diff-first discipline for a signal-engine change.

Reads the TEXT snapshot `.data/_snapshots/transactions.csv` (produced by
`snapshot_db.py`, strictly read-only) — never opens the binary DB, so it's
safe under FUSE. Run:
    python .scripts\\snapshot_db.py            # (Rupert, Windows) refresh snapshot
    python .scripts\\_diag_corporate_actors.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from role_normalize import is_corporate_actor, is_related_party  # noqa: E402

SNAP = HERE.parent / ".data" / "_snapshots" / "transactions.csv"


def main() -> int:
    if not SNAP.exists():
        print(f"[diag] snapshot not found: {SNAP}")
        print("[diag] run `python .scripts/snapshot_db.py` first (read-only).")
        return 1

    with SNAP.open(encoding="utf-8", errors="replace", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("director") or "").strip()]
    total = len(rows)

    # Actor-level resolution: an actor tagged a related party (PCA / family
    # trust) on ANY filing is KEPT across all filings (source role text is
    # inconsistent across an actor's rows).
    related = {(r.get("director") or "").strip()
               for r in rows
               if is_related_party(r.get("role_normalized"), r.get("role"),
                                   (r.get("director") or "").strip())}
    counts: Counter = Counter()
    for r in rows:
        name = (r.get("director") or "").strip()
        if is_corporate_actor(name) and name not in related:
            counts[name] += 1

    flagged_tx = sum(counts.values())
    print(f"[diag] transactions scanned: {total}")
    print(f"[diag] distinct corporate-flagged actors: {len(counts)} "
          f"({flagged_tx} transactions) — these are EXCLUDED from scoring (B-136)")
    print("-" * 64)
    for name, n in counts.most_common():
        print(f"  {n:>4}  {name}")
    print("-" * 64)
    print("[diag] REVIEW for false positives: any name above that is actually a")
    print("[diag] PERSON should not be here. If so, tell Claude to tighten")
    print("[diag] role_normalize._CORP_ACTOR_RE BEFORE running eval_signals --rebuild.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
