"""Sprint 9 Phase A — run the plausibility gate over every cached
filing, fast.

Why this exists: the full pipeline (``start.bat`` / ``refresh_all.py``)
takes hours because it does scraping, price backfill, signal eval,
and dashboard rebuild. To collect Phase A audit data we only need
the parser to run over the existing cached HTML corpus — that's a
few-minutes job, not a few-hours one. This script does exactly that
and nothing else.

What it does:
    1. Optionally archive any existing _suspect_filings.jsonl / .json
       so the audit only reflects this run (use --fresh).
    2. Walks ``.scripts/_scrape_cache/*.html`` and calls
       ``parse_announcement`` on each. The plausibility gate inside
       parse_pdmr.py auto-logs flagged rows to
       ``.data/_suspect_filings.jsonl``.
    3. Prints progress every 100 filings and a final summary.

What it does NOT do:
    - Open the DB for write. Reads URL/announced_at from DB only if
      ``--use-db`` is passed (off by default for max speed).
    - Touch any other ``.data/`` file beyond the suspect-filings log.
    - Modify any transactions row.

Usage:
    python .scripts/run_phase_a_audit.py --fresh
    python .scripts/run_phase_a_audit.py --fresh --use-db
    python .scripts/run_phase_a_audit.py --limit 500

After the run finishes:
    python .scripts/audit_suspect_filings.py --summary
    python .scripts/audit_suspect_filings.py --sample 100
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parse_pdmr  # noqa: E402

ROOT = HERE.parent
DATA_DIR = ROOT / ".data"
CACHE_DIR = HERE / "_scrape_cache"
SUSPECT_JSONL = DATA_DIR / "_suspect_filings.jsonl"
SUSPECT_JSON_LEGACY = DATA_DIR / "_suspect_filings.json"


def _archive_existing() -> list:
    """Move any existing suspect files aside with a timestamp so the run
    we're about to do starts from a clean slate. Returns the list of
    archived paths so the caller can mention them."""
    archived: list = []
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    for src in (SUSPECT_JSONL, SUSPECT_JSON_LEGACY):
        if src.exists():
            dst = src.with_suffix(src.suffix + f".pre-audit-{ts}")
            shutil.move(str(src), str(dst))
            archived.append(dst)
    return archived


def _load_url_lookup() -> dict:
    """Build {rns_id: (url, announced_at)} from the DB so we can pass
    real URLs to parse_announcement (only used with --use-db). Opens
    the DB read-only via the standard URI mode.
    """
    import sqlite3

    db_path = DATA_DIR / "directors.db"
    if not db_path.exists():
        return {}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    lookup: dict = {}
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT url, announced_at FROM transactions "
            "WHERE url IS NOT NULL AND url <> ''"
        ).fetchall()
        for r in rows:
            url = r["url"]
            rns_id = url.rstrip("/").rsplit("/", 1)[-1]
            # First wins; multiple txs per filing share the same URL anyway.
            lookup.setdefault(rns_id, (url, r["announced_at"] or ""))
        conn.close()
    except Exception as e:
        print(f"WARN: --use-db lookup failed: {e}", file=sys.stderr)
    return lookup


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fresh", action="store_true",
                    help="Archive existing _suspect_filings.* before run")
    ap.add_argument("--use-db", action="store_true",
                    help="Look up real URL/announced_at from DB (read-only)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after N cached filings (smoke test)")
    args = ap.parse_args(argv)

    if not CACHE_DIR.exists():
        print(f"ERROR: cache directory not found: {CACHE_DIR}", file=sys.stderr)
        return 2

    if args.fresh:
        archived = _archive_existing()
        for a in archived:
            print(f"Archived: {a}")

    url_lookup = _load_url_lookup() if args.use_db else {}

    cache_files = sorted(CACHE_DIR.glob("*.html"))
    if args.limit:
        cache_files = cache_files[: args.limit]
    total = len(cache_files)
    if total == 0:
        print(f"No HTML files under {CACHE_DIR}", file=sys.stderr)
        return 2

    print(f"Phase A audit: parsing {total} cached filings...")
    if args.use_db:
        print(f"   URL lookup: {len(url_lookup)} entries from DB")
    print()

    t0 = time.time()
    crashes = 0
    parsed_with_rows = 0
    parsed_empty = 0

    for i, html_path in enumerate(cache_files, start=1):
        rns_id = html_path.stem
        url, announced_at = url_lookup.get(rns_id, ("", ""))
        if not url:
            url = f"https://www.investegate.co.uk/announcement/{rns_id}"

        try:
            html = html_path.read_text(encoding="utf-8", errors="replace")
            extracted, warnings, _src = parse_pdmr.parse_announcement(
                html, url=url, rns_id=rns_id, announced_at=announced_at,
            )
            if extracted:
                parsed_with_rows += 1
            else:
                parsed_empty += 1
        except Exception as e:
            crashes += 1
            print(f"  CRASH on {rns_id}: {type(e).__name__}: {e}",
                  file=sys.stderr)

        if i % 100 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta_s = (total - i) / rate if rate > 0 else 0
            print(
                f"  [{i:5d}/{total}]  "
                f"rows={parsed_with_rows}  empty={parsed_empty}  "
                f"crashes={crashes}  "
                f"rate={rate:5.1f}/s  eta={eta_s:5.0f}s"
            )

    elapsed = time.time() - t0
    print()
    print(f"Done in {elapsed:.1f}s.")
    print(f"  Filings parsed:        {total}")
    print(f"  With extracted rows:   {parsed_with_rows}")
    print(f"  Empty (no rows):       {parsed_empty}")
    print(f"  Crashes:               {crashes}")

    if SUSPECT_JSONL.exists():
        try:
            n_logged = sum(
                1 for _ in SUSPECT_JSONL.open("r", encoding="utf-8")
            )
            print(f"  Suspect rows logged:   {n_logged}")
        except Exception:
            pass
    print()
    print("Next:")
    print("  python .scripts/audit_suspect_filings.py --summary")
    print("  python .scripts/audit_suspect_filings.py --sample 100")
    return 0


if __name__ == "__main__":
    sys.exit(main())
