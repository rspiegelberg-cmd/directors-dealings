"""Sprint 13 Phase 1 — buy_strictness backfill.

Updates buy_strictness on existing BUY transactions whose buy_strictness
IS NULL by re-parsing the cached HTML filing and matching by fingerprint.

New rows inserted by run_scrape.py / reparse_corpus.py after Sprint 13
Phase 1 ships will have buy_strictness set at parse time. This script
is a one-off sweep for the pre-Sprint-13 corpus.

FUSE rule (CLAUDE.md): this script writes .data/directors.db.
Run from Windows PowerShell, never from the Linux sandbox.

CLI:
    python .scripts/backfill_buy_strictness.py            # preview
    python .scripts/backfill_buy_strictness.py --confirm  # apply
    python .scripts/backfill_buy_strictness.py --confirm --verbose
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
from parse_pdmr import parse_announcement

CACHE_DIR = HERE / "_scrape_cache"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm", action="store_true",
        help="Apply UPDATEs to the DB. Default: preview only (no writes).",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--include-unknown", action="store_true",
        help="Also re-classify rows already labelled UNKNOWN or MIXED "
             "(B-093 / Sprint 20: the strict-buy rule was widened to "
             "recognise bare 'Purchase' nature cells). Default: NULL only.",
    )
    parser.add_argument(
        "--reclassify-all", action="store_true",
        help="Re-apply the CURRENT classifier to EVERY BUY row, including "
             "those already labelled STRICT_BUY. Use after a classifier "
             "change (e.g. the comp-event Fix 1, 2026-06-03) so existing "
             "STRICT_BUY comp events are demoted to MIXED/NON_BUY_ONLY. "
             "Always preview first (no --confirm) to inspect the flips.",
    )
    args = parser.parse_args()

    conn = db.connect()

    # Load BUY rows we can re-evaluate from the cache. By default only the
    # never-classified (NULL) rows; with --include-unknown also the rows the
    # old rule parked as UNKNOWN/MIXED, so the widened B-093 rule can recover
    # them. The UPDATE guard below matches this selection so the sweep is
    # idempotent either way.
    if args.reclassify_all:
        sel = "1=1"
        label = "ALL (reclassify)"
    elif args.include_unknown:
        sel = ("(buy_strictness IS NULL OR buy_strictness IN "
               "('UNKNOWN', 'MIXED'))")
        label = "NULL/UNKNOWN/MIXED"
    else:
        sel = "buy_strictness IS NULL"
        label = "NULL"
    null_rows = conn.execute(
        "SELECT fingerprint, url, announced_at, "
        "  COALESCE(buy_strictness, '(null)') AS old_bs "
        "FROM transactions "
        f"WHERE type = 'BUY' AND {sel} "
        "  AND url IS NOT NULL AND url <> ''"
    ).fetchall()

    if not null_rows:
        print(f"No BUY rows with buy_strictness = {label} found. Nothing to do.")
        return

    print(f"BUY rows to (re)classify [{label}]: {len(null_rows)}")

    # Group by rns_id so we parse each cached HTML file exactly once.
    # rns_id is the last path segment of the URL (same as reparse_corpus.py).
    by_rns: dict[str, list[dict]] = {}
    for row in null_rows:
        url = row["url"]
        rns_id = url.rstrip("/").rsplit("/", 1)[-1]
        by_rns.setdefault(rns_id, []).append(
            {"fingerprint": row["fingerprint"],
             "url": url,
             "announced_at": row["announced_at"] or "",
             "old_bs": row["old_bs"]}
        )

    print(f"Unique filings to scan:             {len(by_rns)}")

    # Counters
    n_updated = 0
    n_no_cache = 0
    n_no_match = 0
    transitions: Counter = Counter()  # (old_bs -> new_bs) when they differ

    for rns_id, fp_entries in by_rns.items():
        cache_path = CACHE_DIR / f"{rns_id}.html"
        if not cache_path.exists():
            n_no_cache += len(fp_entries)
            if args.verbose:
                print(f"  SKIP (no cache): {rns_id}")
            continue

        try:
            html = cache_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            n_no_cache += len(fp_entries)
            print(f"  WARN: cannot read {rns_id}: {exc}", file=sys.stderr)
            continue

        # Re-parse to get rows with buy_strictness populated.
        url = fp_entries[0]["url"]
        announced_at = fp_entries[0]["announced_at"]
        try:
            new_rows, _warnings, _src = parse_announcement(
                html, url, rns_id, announced_at,
            )
        except Exception as exc:
            n_no_match += len(fp_entries)
            print(f"  WARN: parse error {rns_id}: {exc}", file=sys.stderr)
            continue

        # Build fingerprint -> buy_strictness lookup from re-parsed rows.
        fp_to_bs: dict[str, str] = {}
        for r in new_rows:
            fp = r.get("fingerprint")
            bs = r.get("buy_strictness")
            if fp and bs is not None:
                fp_to_bs[fp] = bs

        for entry in fp_entries:
            fp = entry["fingerprint"]
            bs = fp_to_bs.get(fp)
            if bs is None:
                # Fingerprint didn't match any re-parsed row (rare —
                # happens when a filing's parser output changed between
                # the original scrape and now).
                n_no_match += 1
                if args.verbose:
                    print(f"  SKIP (no fp match): {rns_id} fp={fp[:14]}...")
                continue

            old_bs = entry["old_bs"]
            if bs != old_bs:
                transitions[(old_bs, bs)] += 1
            if args.confirm:
                if args.reclassify_all:
                    guard = "1=1"
                elif args.include_unknown:
                    guard = ("buy_strictness IS NULL OR buy_strictness IN "
                             "('UNKNOWN', 'MIXED')")
                else:
                    guard = "buy_strictness IS NULL"
                conn.execute(
                    "UPDATE transactions SET buy_strictness = ? "
                    f"WHERE fingerprint = ? AND ({guard})",
                    (bs, fp),
                )
            n_updated += 1
            if args.verbose:
                verb = "UPDATE" if args.confirm else "WOULD UPDATE"
                print(f"  {verb}: fp={fp[:14]}... -> {bs}")

        if args.confirm:
            conn.commit()

    print()
    if args.confirm:
        print("DONE.")
    else:
        print("PREVIEW (no DB writes). Run with --confirm to apply.")
    print(f"  rows (re)evaluated: {n_updated}")
    print(f"  no_cache (skip):    {n_no_cache}")
    print(f"  no_match (skip):    {n_no_match}")

    if transitions:
        print()
        print("  label changes (old -> new):")
        for (old_bs, new_bs), n in sorted(
            transitions.items(), key=lambda kv: -kv[1]
        ):
            print(f"    {old_bs:14s} -> {new_bs:14s} {n:5d}")
        demoted = sum(
            n for (old_bs, new_bs), n in transitions.items()
            if old_bs == "STRICT_BUY" and new_bs != "STRICT_BUY"
        )
        print(f"  STRICT_BUY demoted (existing-corpus contamination): {demoted}")
    else:
        print("  label changes: none")


if __name__ == "__main__":
    main()
