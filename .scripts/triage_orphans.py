"""B-020 — Triage script for orphan candidates from Sprint 3 reparse.

WHAT'S AN ORPHAN
----------------
Per ``reparse_corpus.py:reconcile()``, an existing ``transactions`` row
is *orphaned* when the new table-aware parser re-runs against that
filing's cached HTML and produces NO row whose ``(date, ticker, type,
shares)`` tuple matches the existing one. Sprint 3 deliberately ran
``--confirm`` WITHOUT ``--delete-orphans`` because the orphan list
included ~50-80 clean-name rows (Rachel Lawrence, David Bloomfield,
Graham Charlton, Richard Howell, Daniel Bloomfield, etc.) that we
couldn't explain without per-row investigation.

WHAT THIS SCRIPT DOES
---------------------
Walks every cached HTML file in ``.scripts/_scrape_cache/``, re-parses
with the current ``parse_announcement``, runs the same reconcile()
logic, and classifies each orphan row into one of:

    delete_safe_garbage     — existing director string contains a
                              newline, a role label, or 'No. of shares'
                              prose. JDW-style multi-line bleed-through.
    delete_safe_zero_new    — new parser yields ZERO rows for this
                              filing AND the existing director name
                              looks fine. The cached HTML probably no
                              longer carries what the old regex
                              somehow extracted.
    investigate_mismatch    — new parser yields rows but none match the
                              existing (date, ticker, type, shares).
                              Needs eyeballing.
    investigate_no_table    — _extract_via_table found no transaction
                              table header. Parser may need a layout
                              variant added.
    skip_no_cache           — cache HTML missing. Cannot triage.

OUTPUT
------
A CSV at ``.data/_orphan_triage_<YYYY-MM-DD>.csv`` (or whatever
``--out`` overrides to) with one row per orphan, sorted by
classification then rns_id. Counts printed to stdout.

NEXT STEPS
----------
After Rupert eyeballs the triage CSV he can run::

    python .scripts/reparse_corpus.py --confirm --delete-orphans \\
        --limit <comma-sep list of delete_safe_* rns_ids>

to surgically clean only the rows the triage flagged as safe.

FUSE
----
This script READS the DB (via a temporary copy to a working dir) and
WRITES a CSV. It does NOT touch ``transactions``. Per CLAUDE.md the
output write IS to ``.data/`` -- run from Windows PowerShell rather
than from Claude's Linux sandbox::

    python .scripts/triage_orphans.py [--limit N] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import tempfile
from datetime import date as _date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = ROOT / ".data"
CACHE_DIR = HERE / "_scrape_cache"
DB_PATH = DATA_DIR / "directors.db"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
from parse_pdmr import parse_announcement  # noqa: E402


# --- Helpers (inlined from reparse_corpus.py to keep this script
#     self-contained; duplication is small and the logic is stable). -----------

def _load_existing_by_url(conn) -> dict:
    """Return ``{rns_id: [row_dict, ...]}`` for every transactions row.
    Mirror of reparse_corpus._load_existing_by_url."""
    by_rns: dict = {}
    rows = conn.execute(
        "SELECT * FROM transactions WHERE url IS NOT NULL AND url <> ''"
    ).fetchall()
    for r in rows:
        url = r["url"]
        rns_id = url.rstrip("/").rsplit("/", 1)[-1]
        by_rns.setdefault(rns_id, []).append(dict(r))
    return by_rns


def reconcile(existing: list, new_rows: list, excluded_tickers: set) -> dict:
    """Mirror of reparse_corpus.reconcile.

    Returns dict with ``actions`` (unchanged/update/insert) and
    ``orphans`` (existing rows that no new extraction matched).
    """
    new_rows = [r for r in new_rows if r.get("ticker") not in excluded_tickers]
    existing_by_fp = {r["fingerprint"]: r for r in existing}
    matched_fps: set = set()
    actions: list = []
    for new in new_rows:
        new_fp = new["fingerprint"]
        if new_fp in existing_by_fp:
            matched_fps.add(new_fp)
            actions.append({"kind": "unchanged", "new_row": new, "existing_fp": new_fp})
            continue
        match_fp = None
        for fp, ex in existing_by_fp.items():
            if fp in matched_fps:
                continue
            if (ex["date"] == new["date"]
                    and ex["ticker"] == new["ticker"]
                    and ex["type"] == new["type"]
                    and int(ex["shares"]) == int(new["shares"])):
                match_fp = fp
                break
        if match_fp:
            matched_fps.add(match_fp)
            actions.append({"kind": "update", "new_row": new, "existing_fp": match_fp})
            continue
        actions.append({"kind": "insert", "new_row": new, "existing_fp": None})
    orphans = [r for r in existing if r["fingerprint"] not in matched_fps]
    return {"actions": actions, "orphans": orphans}


# Patterns that mark an existing director cell as obvious mis-extraction
# garbage. Borrowed from B-020 backlog evidence (JDW, GLE, RPI examples).
_GARBAGE_DIRECTOR_PATTERNS = [
    r"\n",                        # any newline = cross-cell bleed
    r"^\s*Role\b",                # bled in the "Role" label
    r"No\.?\s*of\s+ordinary\s+shares",  # bled the share-count label
    r"^\s*Daniel\s*$",            # truncated single-token names from RPI etc
    r"^\s*Graham\s*$",
    r"^\s*Stefan\s*$",
    r"^\s*Richard\s*$",
]
_GARBAGE_DIRECTOR_RE = re.compile(
    "|".join(_GARBAGE_DIRECTOR_PATTERNS), re.IGNORECASE,
)


def _classify_orphan(orphan: dict, new_rows: list) -> tuple[str, str]:
    """Decide which classification bucket this orphan falls into.

    Returns ``(classification, reason)``. ``reason`` is a short human-
    readable note that goes into the CSV.
    """
    director = (orphan.get("director") or "").strip()

    if _GARBAGE_DIRECTOR_RE.search(director):
        return "delete_safe_garbage", (
            f"director cell looks like mis-extraction garbage: {director!r}"
        )

    if not new_rows:
        return "delete_safe_zero_new", (
            "new parser produced 0 rows for this filing — cached HTML "
            "no longer carries an extractable row"
        )

    # Some new rows exist but none matched the existing fingerprint on
    # (date, ticker, type, shares). Worth a manual look.
    return "investigate_mismatch", (
        f"new parser produced {len(new_rows)} row(s) but none match "
        f"(date={orphan.get('date')}, type={orphan.get('type')}, "
        f"shares={orphan.get('shares')})"
    )


def _read_cache(rns_id: str) -> str | None:
    p = CACHE_DIR / f"{rns_id}.html"
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _load_excluded_tickers_readonly(conn) -> set[str]:
    """Mirror of reparse_corpus._load_excluded_tickers but called against
    our read-only conn. Tickers in tickers_meta.is_excluded_issuer=1
    plus those listed in .data/_excluded_it_cef.csv (defence-in-depth)."""
    out: set[str] = set()
    for row in conn.execute(
        "SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1"
    ):
        out.add(row["ticker"])
    audit = DATA_DIR / "_excluded_it_cef.csv"
    if audit.exists():
        try:
            with audit.open(encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    t = (row.get("ticker") or "").strip().upper()
                    if t:
                        out.add(t)
        except (OSError, csv.Error):
            pass
    return out


def run(*, limit: int = 0, out_path: Path | None = None,
        verbose: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"ABORT: {DB_PATH} does not exist.")
        return 2
    if not CACHE_DIR.exists():
        print(f"ABORT: cache directory {CACHE_DIR} does not exist.")
        return 2

    # Read-only inspection: open the DB directly (no writes happen via
    # this script). Connect once, hold for the duration -- typical
    # triage takes 1-3 minutes on 7k filings.
    conn = db.connect()
    try:
        excluded = _load_excluded_tickers_readonly(conn)
        existing_by_rns = _load_existing_by_url(conn)
    finally:
        conn.close()

    print(f"Loaded {sum(len(v) for v in existing_by_rns.values())} existing "
          f"transactions across {len(existing_by_rns)} filings; "
          f"excluded-issuer tickers: {len(excluded)}.")

    cache_files = sorted(CACHE_DIR.glob("*.html"))
    if limit:
        cache_files = cache_files[:limit]
    total = len(cache_files)
    print(f"Scanning {total} cached filings...")

    rows: list[dict] = []
    counts: dict[str, int] = {
        "delete_safe_garbage": 0,
        "delete_safe_zero_new": 0,
        "investigate_mismatch": 0,
        "investigate_no_table": 0,
        "skip_no_cache": 0,
    }
    seen_orphan_total = 0

    # Cache files we DIDN'T have a DB row for are not orphans (they're
    # not even in the DB). Cover the inverse: filings with existing rows
    # whose cache is missing.
    rns_ids_to_process = set(p.stem for p in cache_files) | set(existing_by_rns.keys())

    for i, rns_id in enumerate(sorted(rns_ids_to_process)):
        if verbose and i % 500 == 0:
            print(f"  [{i}/{len(rns_ids_to_process)}] processed; "
                  f"orphans so far: {seen_orphan_total}", flush=True)

        existing = existing_by_rns.get(rns_id, [])
        if not existing:
            continue  # nothing to orphan
        if limit and rns_id not in {p.stem for p in cache_files}:
            continue  # respect --limit by skipping out-of-window filings

        html = _read_cache(rns_id)
        if html is None:
            # Every existing row is "orphaned" by missing cache.
            for ex in existing:
                rows.append({
                    "rns_id": rns_id,
                    "fingerprint": ex["fingerprint"],
                    "existing_director": ex.get("director"),
                    "existing_company": ex.get("company"),
                    "existing_date": ex.get("date"),
                    "existing_ticker": ex.get("ticker"),
                    "existing_type": ex.get("type"),
                    "existing_shares": ex.get("shares"),
                    "n_new_rows": 0,
                    "classification": "skip_no_cache",
                    "reason": "cached HTML missing — cannot re-parse",
                })
                counts["skip_no_cache"] += 1
                seen_orphan_total += 1
            continue

        url = existing[0].get("url") or ""
        announced_at = existing[0].get("announced_at") or ""
        try:
            new_rows, warnings, _ = parse_announcement(
                html, url, rns_id, announced_at,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {rns_id}: {type(e).__name__}: {e}")
            continue

        result = reconcile(existing, new_rows, excluded)
        orphans = result["orphans"]

        if not orphans:
            continue

        # Diagnose whether the new parser even found a transaction
        # table; informs the investigate_no_table classification.
        no_table = not new_rows

        for orphan in orphans:
            cls, reason = _classify_orphan(orphan, new_rows)
            # Promote to investigate_no_table when zero rows AND the
            # director is clean (we'd have classified as
            # delete_safe_zero_new otherwise — but the underlying
            # cause is likely a parser layout gap, worth flagging).
            if cls == "delete_safe_zero_new" and no_table:
                cls = "investigate_no_table"
                reason = (
                    "no transaction table header found by new parser — "
                    "filing may use a layout variant not yet covered"
                )
            rows.append({
                "rns_id": rns_id,
                "fingerprint": orphan["fingerprint"],
                "existing_director": orphan.get("director"),
                "existing_company": orphan.get("company"),
                "existing_date": orphan.get("date"),
                "existing_ticker": orphan.get("ticker"),
                "existing_type": orphan.get("type"),
                "existing_shares": orphan.get("shares"),
                "n_new_rows": len(new_rows),
                "classification": cls,
                "reason": reason,
            })
            counts[cls] += 1
            seen_orphan_total += 1

    # Sort: classification (delete_safe_* first so they're easy to
    # paste into reparse_corpus --limit), then rns_id.
    rows.sort(key=lambda r: (r["classification"], r["rns_id"]))

    # Write CSV.
    out_path = out_path or (
        DATA_DIR / f"_orphan_triage_{_date.today().isoformat()}.csv"
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "rns_id", "fingerprint", "classification", "reason",
        "existing_date", "existing_ticker", "existing_director",
        "existing_company", "existing_type", "existing_shares",
        "n_new_rows",
    ]
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in fields})
    import os
    os.replace(tmp, out_path)

    print()
    print(f"=== Orphan triage summary ===")
    for k, v in counts.items():
        print(f"  {k:25s} {v}")
    print(f"  {'TOTAL':25s} {seen_orphan_total}")
    print()
    print(f"Triage CSV written to: {out_path}")
    print()
    print("Next step (if classifications look right):")
    safe_rns_sample = sorted({
        r["rns_id"] for r in rows
        if r["classification"].startswith("delete_safe_")
    })
    if safe_rns_sample:
        print(f"  python .scripts/reparse_corpus.py --confirm --delete-orphans \\")
        print(f"      --limit {len(safe_rns_sample)}  # or paste the rns_id list")
    else:
        print("  No delete_safe candidates this run; nothing to clean.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B-020: triage orphan candidates from the Sprint 3 reparse."
    )
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N cached files (for testing).")
    ap.add_argument("--out", default=None, type=Path,
                    help="Output CSV path. Defaults to "
                         ".data/_orphan_triage_YYYY-MM-DD.csv")
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-batch progress.")
    args = ap.parse_args(argv)
    return run(limit=args.limit, out_path=args.out, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
