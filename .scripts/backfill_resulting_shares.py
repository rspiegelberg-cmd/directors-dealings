"""Sprint 61 / B-156 -- resulting_shares backfill.

Populates transactions.resulting_shares (the stated post-transaction total
beneficial holding) on existing rows where it IS NULL, by re-parsing the
cached HTML filing and matching rows by fingerprint. Sweeps ALL transaction
types (the figure is informative on sells too); the end-of-run report focuses
on BUY population rate, which is the B-156 acceptance metric
(>=10% of BUY rows, stretch 15% -- most MAR-template filings simply do not
state the figure, so sparse coverage is expected and correct).

New rows inserted by run_scrape.py / reparse_corpus.py after B-156 ships
carry resulting_shares from parse time. This script is the one-off sweep
for the pre-B-156 corpus (the main population vehicle).

Every applied UPDATE is appended as one JSON line to the audit log
`.data/_resulting_shares_backfill.log` (JSONL append -- never RMW).

FUSE rule (CLAUDE.md): this script writes .data/directors.db.
Run from Windows PowerShell, never from the Linux sandbox.

CLI:
    python .scripts/backfill_resulting_shares.py            # preview
    python .scripts/backfill_resulting_shares.py --confirm  # apply
    python .scripts/backfill_resulting_shares.py --confirm --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
from parse_pdmr import parse_announcement

CACHE_DIR = HERE / "_scrape_cache"
AUDIT_LOG = db.DB_DIR / "_resulting_shares_backfill.log"


def _append_audit(entries: list[dict]) -> None:
    """JSONL append (no read-modify-write -- project rule)."""
    if not entries:
        return
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _buy_population_report(conn, pending: dict) -> None:
    """Print BUY population rate, current DB state + would-be updates.

    `pending` maps fingerprint -> resulting_shares for rows that WOULD be
    updated in preview mode (empty after a --confirm run, where the DB
    already reflects the updates).
    """
    rows = conn.execute(
        "SELECT fingerprint, buy_strictness, "
        "       resulting_shares IS NOT NULL AS pop "
        "FROM transactions WHERE type = 'BUY'"
    ).fetchall()
    n_buy = len(rows)
    n_pop = sum(1 for r in rows
                if r["pop"] or r["fingerprint"] in pending)
    strict = [r for r in rows if r["buy_strictness"] == "STRICT_BUY"]
    n_strict = len(strict)
    n_strict_pop = sum(1 for r in strict
                       if r["pop"] or r["fingerprint"] in pending)

    def _pct(a, b):
        return (100.0 * a / b) if b else 0.0

    print()
    print("BUY population rate (B-156 acceptance metric, target >=10%):")
    print(f"  all BUY rows:        {n_pop}/{n_buy}  "
          f"({_pct(n_pop, n_buy):.1f}%)")
    print(f"  STRICT_BUY rows:     {n_strict_pop}/{n_strict}  "
          f"({_pct(n_strict_pop, n_strict):.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm", action="store_true",
        help="Apply UPDATEs to the DB. Default: preview only (no writes).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    conn = db.connect()   # applies migration 013 if not yet applied

    null_rows = conn.execute(
        "SELECT fingerprint, url, announced_at, type, shares "
        "FROM transactions "
        "WHERE resulting_shares IS NULL "
        "  AND url IS NOT NULL AND url <> ''"
    ).fetchall()

    if not null_rows:
        print("No rows with resulting_shares IS NULL found. Nothing to do.")
        _buy_population_report(conn, {})
        return

    print(f"Rows with resulting_shares IS NULL:  {len(null_rows)}")

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
             "type": row["type"]}
        )

    print(f"Unique filings to scan:              {len(by_rns)}")

    n_updated = 0
    n_not_stated = 0   # filing parsed, but no resulting figure for this fp
    n_no_cache = 0
    n_parse_err = 0
    pending: dict[str, int] = {}   # fp -> value (preview-mode report input)
    audit_entries: list[dict] = []

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

        url = fp_entries[0]["url"]
        announced_at = fp_entries[0]["announced_at"]
        try:
            new_rows, _warnings, _src = parse_announcement(
                html, url, rns_id, announced_at,
            )
        except Exception as exc:
            n_parse_err += len(fp_entries)
            print(f"  WARN: parse error {rns_id}: {exc}", file=sys.stderr)
            continue

        # fingerprint -> resulting_shares from the re-parsed rows.
        fp_to_rs: dict[str, int] = {}
        for r in new_rows:
            fp = r.get("fingerprint")
            rs = r.get("resulting_shares")
            if fp and rs is not None:
                fp_to_rs[fp] = int(rs)

        for entry in fp_entries:
            fp = entry["fingerprint"]
            rs = fp_to_rs.get(fp)
            if rs is None:
                # Figure not stated for this row (normal -- MAR template),
                # or the fingerprint didn't match a re-parsed row.
                n_not_stated += 1
                if args.verbose:
                    print(f"  none: {rns_id} fp={fp[:14]}...")
                continue

            if args.confirm:
                conn.execute(
                    "UPDATE transactions SET resulting_shares = ? "
                    "WHERE fingerprint = ? AND resulting_shares IS NULL",
                    (rs, fp),
                )
                audit_entries.append({
                    "ts": db.iso_now(),
                    "rns_id": rns_id,
                    "fingerprint": fp,
                    "type": entry["type"],
                    "resulting_shares": rs,
                })
            else:
                pending[fp] = rs
            n_updated += 1
            if args.verbose:
                verb = "UPDATE" if args.confirm else "WOULD UPDATE"
                print(f"  {verb}: fp={fp[:14]}... -> {rs}")

        if args.confirm:
            conn.commit()

    if args.confirm:
        _append_audit(audit_entries)

    print()
    if args.confirm:
        print("DONE.")
        print(f"  audit log: {AUDIT_LOG}")
    else:
        print("PREVIEW (no DB writes). Run with --confirm to apply.")
    print(f"  rows updated:        {n_updated}")
    print(f"  not stated (skip):   {n_not_stated}")
    print(f"  no_cache (skip):     {n_no_cache}")
    print(f"  parse error (skip):  {n_parse_err}")

    _buy_population_report(conn, pending)


if __name__ == "__main__":
    main()
