"""One-shot repair tool for `.scripts/_pending_review.json`.

Background (2026-05-19): the pending-review file was found corrupt
mid-write — the last entry (rns_id 9575414) was only partially written
before the writer process died. JSON parse fails at line 48144 col 6.

This script recovers the salvageable entries (~4,174 of the 4,189 the
header claimed), rewrites the file cleanly, and backs up the corrupt
original to `.scripts/_pending_review.json.corrupt-YYYY-MM-DD` so it can
be inspected later if needed.

Safety:
  * `--preview` (default) — analyses + prints what would change, NO writes.
  * `--confirm` — actually performs the repair.
  * Atomic write via `tmp + os.replace` — FUSE-safe per CLAUDE.md.
  * Backup is taken BEFORE the write attempt; abort if backup fails.

Usage (PowerShell):

    cd C:\\Dev\\DirectorsDealings
    python .scripts\\repair_pending_review.py            # preview
    python .scripts\\repair_pending_review.py --confirm  # apply

After repair, `python .scripts\\run_pending_sweep.py --help` should
load the file cleanly.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "_pending_review.json"


def _recover_entries(path: Path) -> tuple[dict, dict, int]:
    """Parse the corrupt file by trimming back to the last complete entry.

    Returns `(items, top_level, total_lines_dropped)`. `items` is the
    recovered entry dict. `top_level` is the wrapping JSON dict
    (typically `{"generated_at": ..., "count": ..., "items": items}`).
    """
    with path.open(encoding="utf-8") as f:
        lines = f.readlines()
    total_lines = len(lines)

    # Find the last line that's exactly a clean entry-close ("    }," or "},").
    last_clean = None
    for i in range(total_lines - 1, -1, -1):
        if lines[i].rstrip() in ("},", "    },"):
            last_clean = i
            break
    if last_clean is None:
        raise SystemExit("ERROR: cannot find any clean entry close — file is unrecoverable.")

    # Keep up to (and INCLUDING) the last_clean line, but drop the trailing comma.
    kept = lines[:last_clean] + ["    }\n", "  }\n", "}\n"]
    text = "".join(kept)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"ERROR: recovered text still not valid JSON ({e.lineno}:{e.colno})."
        )

    items = data["items"] if isinstance(data, dict) and "items" in data else data
    return items, data, total_lines - last_clean


def _write_atomic(path: Path, content: str) -> None:
    """Tmp + os.replace pattern — FUSE-safe."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _backup_corrupt(path: Path) -> Path:
    """Copy the corrupt file to `<path>.corrupt-YYYY-MM-DD` BEFORE the
    repair write. Aborts if the backup destination already exists (don't
    silently clobber an earlier corruption snapshot)."""
    today = date.today().isoformat()
    backup = path.with_suffix(path.suffix + f".corrupt-{today}")
    if backup.exists():
        # Append a counter so re-runs don't clobber the first snapshot.
        for n in range(1, 100):
            alt = path.with_suffix(path.suffix + f".corrupt-{today}.{n}")
            if not alt.exists():
                backup = alt
                break
        else:
            raise SystemExit(
                f"ERROR: too many existing backups under "
                f"{path.with_suffix(path.suffix + f'.corrupt-{today}')}.*"
            )
    shutil.copy2(path, backup)
    return backup


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Actually perform the repair. Without this flag, runs in "
             "preview mode (no writes).",
    )
    args = p.parse_args(argv)

    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found.")
        return 1

    print(f"Target: {TARGET}")
    print(f"Size:   {TARGET.stat().st_size:,} bytes")

    # Try to load it cleanly first — if it's NOT corrupt, no repair needed.
    try:
        with TARGET.open(encoding="utf-8") as f:
            json.load(f)
        print("\nFile parses cleanly already — no repair needed. Exiting.")
        return 0
    except json.JSONDecodeError as e:
        print(f"\nFile IS corrupt — JSON parse fails at line {e.lineno}, "
              f"col {e.colno}.")

    # Recover entries via line-trimming.
    items, top_level, lines_dropped = _recover_entries(TARGET)
    declared_count = top_level.get("count", "<not in file>")
    recovered_count = len(items)
    lost_count = (declared_count - recovered_count
                  if isinstance(declared_count, int) else "<?>")

    print(f"\n=== Recovery report ===")
    print(f"  Declared count (file header):  {declared_count}")
    print(f"  Recovered entries:             {recovered_count}")
    print(f"  Lines dropped:                 {lines_dropped}")
    print(f"  Estimated lost entries:        {lost_count}")
    if isinstance(lost_count, int) and lost_count > 50:
        print(f"  [!] WARNING: losing >{lost_count} entries — investigate manually")

    # Rebuild clean JSON. Preserve top-level fields (generated_at, etc.) but
    # update `count` to match what's actually present.
    if isinstance(top_level, dict) and "items" in top_level:
        rebuilt = dict(top_level)
        rebuilt["items"] = items
        rebuilt["count"] = recovered_count
    else:
        rebuilt = items

    rebuilt_text = json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n"
    print(f"  Repaired size (estimate):      {len(rebuilt_text):,} bytes")

    if not args.confirm:
        print("\n=== PREVIEW MODE — no files written ===")
        print("To apply the repair, re-run with --confirm.")
        return 0

    # Backup first.
    print("\n=== APPLYING REPAIR ===")
    backup_path = _backup_corrupt(TARGET)
    print(f"  Backup written:  {backup_path}")

    # Atomic write of the repaired content.
    _write_atomic(TARGET, rebuilt_text)
    print(f"  Repaired file written: {TARGET}")

    # Verify the result parses.
    try:
        with TARGET.open(encoding="utf-8") as f:
            verify = json.load(f)
        verify_count = len(verify.get("items", verify))
        print(f"  Verification: parsed OK, {verify_count} entries.")
    except json.JSONDecodeError as e:
        print(f"  [!] Verification FAILED ({e.lineno}:{e.colno}) — "
              f"restore from {backup_path}!")
        return 2

    print("\n[ok] Repair complete.")
    if isinstance(lost_count, int) and lost_count > 0:
        print(
            f"\n  Note: {lost_count} entries were in the truncated portion "
            f"of the original file. They are NOT in the repaired file, but "
            f"their cached HTML still exists in .scripts/_scrape_cache/ and "
            f"will be re-detected on the next scrape run."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
