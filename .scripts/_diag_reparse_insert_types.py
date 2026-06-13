"""READ-ONLY diagnostic: break down the reparse inserts/orphans by type,
and dump the full BUY-insert list to an audit CSV for verification.

Reuses reparse_corpus's own reconciliation logic so the numbers match the
preview exactly, but writes NOTHING to the DB or the scrape/price caches.
It DOES write one human-readable audit CSV under docs/audits/ (Zone A, code
side) listing every projected BUY insert so the Data Integrity Auditor can
sample-verify them against source filings before any --confirm.

Run from PowerShell:
    python .scripts/_diag_reparse_insert_types.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import reparse_corpus as rc  # noqa: E402

CACHE_DIR = HERE / "_scrape_cache"
AUDIT_OUT = HERE.parent / "docs" / "audits" / "reparse_buy_insert_sample_2026-06-03.csv"


def main() -> int:
    cache_files = sorted(CACHE_DIR.glob("*.html"))
    if not cache_files:
        print(f"No cached HTML at {CACHE_DIR}")
        return 1

    conn = db.connect()
    try:
        excluded = rc._load_excluded_tickers(conn)
        existing_by_rns = rc._load_existing_by_url(conn)

        insert_types: Counter = Counter()
        orphan_types: Counter = Counter()
        buy_band = {"lt_1k": 0, "1k_25k": 0, "25k_100k": 0, "gt_100k": 0}
        buy_rows: list = []
        total_inserts = 0
        total_orphans = 0

        def band_of(v: float) -> str:
            if v < 1000:
                return "lt_1k"
            if v < 25000:
                return "1k_25k"
            if v < 100000:
                return "25k_100k"
            return "gt_100k"

        for html_path in cache_files:
            rns_id = html_path.stem
            try:
                html = html_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            result = rc.process_filing(
                conn, rns_id, html, existing_by_rns, excluded,
            )
            for a in result["actions"]:
                if a["kind"] != "insert":
                    continue
                nr = a["new_row"]
                t = nr.get("type", "?")
                insert_types[t] += 1
                total_inserts += 1
                if t == "BUY":
                    v = float(nr.get("value") or 0.0)
                    buy_band[band_of(v)] += 1
                    buy_rows.append({
                        "rns_id": rns_id,
                        "url": nr.get("url") or "",
                        "date": nr.get("date"),
                        "ticker": nr.get("ticker"),
                        "director": nr.get("director"),
                        "shares": nr.get("shares"),
                        "price": nr.get("price"),
                        "value": round(v, 2),
                        "value_band": band_of(v),
                        "fingerprint": nr.get("fingerprint"),
                    })
            for o in result["orphans"]:
                orphan_types[o.get("type", "?")] += 1
                total_orphans += 1

        # Write the full BUY-insert list for the auditor (Zone A path).
        AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
        fields = ["rns_id", "url", "date", "ticker", "director",
                  "shares", "price", "value", "value_band", "fingerprint"]
        with AUDIT_OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in sorted(buy_rows, key=lambda x: (-float(x["value"]))):
                w.writerow(r)

        print("=== REPARSE INSERTS BY TYPE (read-only, no DB writes) ===")
        for t, n in insert_types.most_common():
            print(f"  {t:10s} {n:5d}")
        print(f"  {'TOTAL':10s} {total_inserts:5d}")
        print()
        print("=== Of the BUY inserts, value bands ===")
        for band, n in buy_band.items():
            print(f"  {band:10s} {n:5d}")
        print()
        print("=== ORPHAN CANDIDATES BY TYPE ===")
        for t, n in orphan_types.most_common():
            print(f"  {t:10s} {n:5d}")
        print(f"  {'TOTAL':10s} {total_orphans:5d}")
        print()
        print(f"BUY-insert audit CSV written: {AUDIT_OUT}  ({len(buy_rows)} rows)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
