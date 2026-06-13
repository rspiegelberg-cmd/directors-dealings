"""DB health utilities for Directors Dealings.

Provides three public entry points called by start.bat (via Python one-liner)
and by refresh_all.py at pipeline start/end:

    check()      -> True if DB is healthy, False if corrupted / missing
    backup()     -> Copy DB to .data/directors.db.bak atomically
    restore()    -> Replace DB with .bak if primary is corrupted
    guard()      -> Called at pipeline START: check + restore if needed.
                   Exits with code 2 if DB is unrecoverable.
    seal()       -> Called at pipeline END (success only): take a fresh backup.

Usage from start.bat:
    python .scripts/db_health.py check
    python .scripts/db_health.py restore

Usage from refresh_all.py:
    import db_health
    db_health.guard()   # start of pipeline
    db_health.seal()    # end of pipeline (success)
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
DB_PATH   = ROOT / ".data" / "directors.db"
BAK_PATH  = ROOT / ".data" / "directors.db.bak"
CSV_PATH  = ROOT / ".data" / "_backtest_results.csv"
CSV_BAK   = ROOT / ".data" / "_backtest_results.csv.bak"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def check(path: Path = DB_PATH) -> bool:
    """Return True if the SQLite file at `path` passes integrity_check."""
    if not path.exists():
        return False
    try:
        con = sqlite3.connect(str(path), timeout=5)
        result = con.execute("PRAGMA integrity_check").fetchone()
        con.close()
        return result is not None and result[0] == "ok"
    except Exception:
        return False


def backup() -> bool:
    """Copy DB → .bak atomically (only if primary is healthy).
    Returns True on success."""
    if not check(DB_PATH):
        print("[db_health] backup skipped: primary DB is not healthy")
        return False
    tmp = BAK_PATH.with_suffix(".tmp")
    try:
        shutil.copy2(str(DB_PATH), str(tmp))
        # B-035: fsync the tmp file before rename so a hard reset can't strand
        # an orphan tmp with no .bak. Cheap on local NVMe; matters most when
        # the OS is the one to crash (FUSE remount, BSOD, power loss).
        # NOTE: open in "rb+" (read+write), not "rb" — Windows fsync requires
        # a writable handle and raises [Errno 9] Bad file descriptor on
        # read-only handles (surfaced by test_repair_dates_atomicity on
        # Rupert's Windows side 2026-05-22).
        with open(tmp, "rb+") as fh:
            os.fsync(fh.fileno())
        tmp.replace(BAK_PATH)
        size_kb = BAK_PATH.stat().st_size // 1024
        print(f"[db_health] backup written -> {BAK_PATH.name} ({size_kb} KB)")
        return True
    except Exception as e:
        print(f"[db_health] backup failed: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False


def restore() -> bool:
    """If primary DB is corrupted, replace it from .bak.
    Returns True if restoration succeeded or DB was already healthy.
    Returns False if both primary and backup are bad."""
    if check(DB_PATH):
        print("[db_health] primary DB is healthy — no restore needed")
        return True
    if not BAK_PATH.exists():
        print("[db_health] no backup found — cannot restore")
        return False
    if not check(BAK_PATH):
        print("[db_health] backup is also corrupted — cannot restore")
        return False
    # Delete corrupt primary, copy backup in
    tmp = DB_PATH.with_suffix(".db.restoring")
    try:
        shutil.copy2(str(BAK_PATH), str(tmp))
        if DB_PATH.exists():
            DB_PATH.unlink()
        tmp.replace(DB_PATH)
        print(f"[db_health] restored DB from backup ({BAK_PATH.stat().st_size // 1024} KB)")
        return True
    except Exception as e:
        print(f"[db_health] restore failed: {e}")
        return False


def guard() -> None:
    """Called at pipeline START.

    * If DB is missing entirely → fine, the scrape step will create it.
    * If DB exists but is corrupted → attempt restore from backup.
    * If both primary and backup are corrupt → exit 2 (pipeline cannot proceed).
    """
    if not DB_PATH.exists():
        return  # fresh install — pipeline will create a new DB
    if check(DB_PATH):
        return  # healthy
    print("[db_health] [warn] primary DB corrupted - attempting restore from backup...")
    # Also try to restore the CSV in the same pass
    _restore_csv()
    if restore():
        print("[db_health] [ok] restored successfully - pipeline continuing")
    else:
        print("[db_health] [fail] DB unrecoverable. Delete .data/directors.db and rerun.")
        sys.exit(2)


def _backup_csv() -> None:
    """Backup _backtest_results.csv if it looks healthy (>1KB, no null bytes in first row)."""
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size < 1024:
        return
    try:
        first = CSV_PATH.read_bytes()[:256]
        if b"\x00" in first:
            print("[db_health] CSV backup skipped: null bytes detected in header")
            return
        tmp = CSV_BAK.with_suffix(".tmp")
        shutil.copy2(str(CSV_PATH), str(tmp))
        tmp.replace(CSV_BAK)
        print(f"[db_health] CSV backup written -> {CSV_BAK.name} ({CSV_PATH.stat().st_size // 1024} KB)")
    except Exception as e:
        print(f"[db_health] CSV backup failed: {e}")


def _restore_csv() -> None:
    """Restore _backtest_results.csv from backup if primary is corrupted."""
    if CSV_PATH.exists() and CSV_PATH.stat().st_size > 1024:
        first = CSV_PATH.read_bytes()[:256]
        if b"\x00" not in first:
            return  # primary looks fine
    if not CSV_BAK.exists():
        return
    try:
        tmp = CSV_PATH.with_suffix(".restoring")
        shutil.copy2(str(CSV_BAK), str(tmp))
        if CSV_PATH.exists():
            CSV_PATH.unlink()
        tmp.replace(CSV_PATH)
        print(f"[db_health] CSV restored from backup")
    except Exception as e:
        print(f"[db_health] CSV restore failed: {e}")


def seal() -> None:
    """Called at pipeline END after a successful run — take a fresh backup of DB + CSV."""
    backup()
    _backup_csv()


# ---------------------------------------------------------------------------
# B-024: stale-backup defences
# ---------------------------------------------------------------------------
#
# Before today, only `refresh_all.py` called `seal()`. Ad-hoc scripts
# (`exclude_investment_trusts.py`, `reparse_corpus.py`, `run_pending_sweep.py`,
# etc.) write to `directors.db` but historically never refreshed `.bak`. Net
# result: a user could accumulate days of Sprint work between refreshes, and
# if the live DB went sick the only available backup was pre-Sprint.
#
# Two new tools below close that gap:
#   * `warn_if_stale(hours)`   — soft visibility from `start.bat`
#   * `auto_seal_if_stale(hours)` — opportunistic re-seal at startup if the
#                                    live DB is healthy
# The ad-hoc scripts also call `seal()` in their success path now (see
# B-024 fix in the relevant `_main` functions).


def _backup_age_hours() -> float | None:
    """Return age of `.bak` in hours, or None if backup is missing /
    unreadable. Uses mtime; same wall-clock comparison the auto-seal
    decision relies on."""
    if not BAK_PATH.exists():
        return None
    try:
        mtime = datetime.fromtimestamp(BAK_PATH.stat().st_mtime)
        return (datetime.now() - mtime).total_seconds() / 3600.0
    except OSError:
        return None


def warn_if_stale(hours: int = 24) -> int:
    """Print a stale-backup warning. Non-blocking. Returns 0 if fresh /
    missing, 1 if stale. Designed for `start.bat` to surface gradual
    backup drift to Rupert without changing exit code."""
    age = _backup_age_hours()
    if age is None:
        # Missing backup is its own problem; let auto_seal handle it.
        return 0
    if age <= hours:
        return 0
    # Use plain ASCII so cp1252 PowerShell consoles don't render artifacts.
    print(
        f"[db_health] WARNING: backup is {age:.1f}h old (threshold {hours}h). "
        "Open the dashboard and click Refresh, or any ad-hoc DB script will "
        "re-seal at the end of its run."
    )
    return 1


def fail_if_stale(hours: int = 48) -> int:
    """Hard-fail when `.bak` is older than `hours`.

    Designed for `start.bat` to escalate beyond the soft `warn_if_stale`
    when the situation is bad enough to refuse to start the dashboard.
    Prints a loud banner + the exact recovery command, then returns
    non-zero so the caller can `exit /b 3`.

    Returns 0 if fresh or missing-but-unrecoverable, 1 if stale beyond
    threshold (caller should treat as fatal).
    """
    age = _backup_age_hours()
    if age is None:
        # No backup at all. auto_seal_if_stale should have created one
        # earlier in start.bat; if we're here it means the live DB was
        # missing or unhealthy. Don't block — let the dashboard load
        # empty so the user can investigate.
        return 0
    if age <= hours:
        return 0
    banner = "!" * 72
    print()
    print(banner)
    print(f"[db_health] FATAL: backup is {age:.1f}h old "
          f"(fail-stale threshold {hours}h).")
    print()
    print("This means no fresh .bak has been written in over "
          f"{hours} hours. Continuing would mean every script run from "
          "here is one FUSE corruption event away from un-recoverable "
          "data loss.")
    print()
    print("To unblock: confirm the live DB is healthy, then take a fresh")
    print("backup manually with:")
    print()
    print("    python .scripts\\db_health.py auto-seal 0")
    print()
    print("That will force-refresh the .bak. Then re-run start.bat.")
    print(banner)
    print()
    return 1


def auto_seal_if_stale(hours: int = 24) -> int:
    """If `.bak` is older than `hours` and the live DB is healthy, take a
    fresh backup. Designed for `start.bat` so the bak refreshes against
    whatever Sprint scripts have written since the last refresh_all run.

    Returns 0 on success or no-op, 1 on failure. Never raises — start.bat
    must remain runnable even if the backup write fails.
    """
    age = _backup_age_hours()
    if age is None:
        # No backup at all — try to take an initial one, but only if the
        # live DB is healthy. (A missing DB is a fresh install; no-op.)
        if not DB_PATH.exists():
            return 0
        if check(DB_PATH):
            print("[db_health] no backup found — taking initial seal")
            return 0 if backup() else 1
        return 0
    if age <= hours:
        return 0  # fresh enough — no action
    # Backup is stale. Only re-seal if the live DB is healthy; otherwise
    # the existing (possibly old but clean) backup is the safer artifact.
    if not check(DB_PATH):
        print(
            f"[db_health] backup is {age:.1f}h old AND live DB is unhealthy. "
            "Leaving backup alone so `restore` can use it."
        )
        return 1
    print(f"[db_health] backup is {age:.1f}h old (>{hours}h) — refreshing")
    return 0 if backup() else 1


# ---------------------------------------------------------------------------
# CLI interface (called from start.bat)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd  = args[0] if args else "check"

    if cmd == "check":
        ok = check()
        print("ok" if ok else "corrupted")
        return 0 if ok else 1

    if cmd == "restore":
        return 0 if restore() else 1

    if cmd == "backup":
        return 0 if backup() else 1

    if cmd == "guard":
        guard()
        return 0

    if cmd == "seal":
        seal()
        return 0

    if cmd == "warn-stale":
        # Optional second arg: threshold in hours (default 24).
        hours = int(args[1]) if len(args) > 1 else 24
        return warn_if_stale(hours)

    if cmd == "auto-seal":
        hours = int(args[1]) if len(args) > 1 else 24
        return auto_seal_if_stale(hours)

    if cmd == "fail-stale":
        # Hard-fail variant of warn-stale. Optional second arg: threshold
        # in hours (default 48). Used by start.bat to refuse to launch
        # the dashboard when .bak is dangerously stale.
        hours = int(args[1]) if len(args) > 1 else 48
        return fail_if_stale(hours)

    print(f"Unknown command: {cmd}. Use check | restore | backup | guard | "
          "seal | warn-stale [hours] | auto-seal [hours] | "
          "fail-stale [hours]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
