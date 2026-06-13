"""B-020 — re-triage the 126 standing orphan candidates with TODAY's parser.

READ-ONLY. Writes nothing to the DB. The orphan list in
`.data/_orphan_triage_2026-05-22.csv` was classified by the MAY-22 parser.
Since then the parser gained Layouts A/B (B-090), the section extractor
(B-023), company fallbacks (B-022) etc., so many "investigate_no_table" rows
should now parse. This re-runs `parse_announcement` over each orphan's cached
HTML and re-classifies, so Rupert can decide keep-vs-delete from current truth.

Run from PowerShell (it reads cached HTML + the CSV; touches no DB):

    python .scripts/_diag_orphan_retriage.py

Output: per-row verdict + a summary. Verdicts:
  NOW_REPRODUCED   today's parser emits a row matching the existing DB row
                   -> the orphan is resolved; KEEP (a future reparse re-inserts it)
  PARSER_GAP       parser emits nothing BUT the filing HTML contains a real
                   transaction table (Price/Volume/Nature) -> KEEP. This is a
                   parser COVERAGE gap (e.g. PCA/corporate-holder or SIP/EBT
                   layout), NOT garbage. Deleting would lose real insider data.
  EMPTY_CANDIDATE  parser emits nothing AND no transaction markers in the HTML
                   -> the only genuine delete candidate (verify before deleting).
  MISMATCH         parser emits rows but none match -> eyeball the filing
  NO_CACHE         cached HTML missing -> can't decide here

IMPORTANT: do NOT bulk-delete PARSER_GAP rows. They are real transactions in
layouts the parser can't yet read; the fix is to extend the parser, not to
delete the data.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parse_pdmr  # noqa: E402

ROOT = HERE.parent
CSV_PATH = ROOT / ".data" / "_orphan_triage_2026-05-22.csv"
CACHE_DIR = HERE / "_scrape_cache"


def _has_txn_table(html: str) -> bool:
    """True if the filing HTML contains real PDMR transaction-table markers.
    Distinguishes 'real filing the parser can't read' from 'genuinely empty'."""
    low = html.lower()
    has_price = "price" in low
    has_vol = "volume" in low
    has_nature = ("nature of the transaction" in low) or ("aggregated" in low)
    return has_price and has_vol and has_nature


def _row_matches(parsed: dict, ex_date: str, ex_type: str, ex_shares: str) -> bool:
    try:
        ps = int(float(parsed.get("shares") or 0))
    except (TypeError, ValueError):
        ps = -1
    try:
        xs = int(float(ex_shares or 0))
    except (TypeError, ValueError):
        xs = -2
    return (str(parsed.get("date") or "") == (ex_date or "")
            and str(parsed.get("type") or "") == (ex_type or "")
            and ps == xs)


def main() -> None:
    if not CSV_PATH.exists():
        print(f"orphan CSV not found: {CSV_PATH}")
        return
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    print(f"Re-triaging {len(rows)} orphans with the current parser...\n")

    verdicts = Counter()
    deletes: list = []
    for r in rows:
        rns = r["rns_id"]
        cache = CACHE_DIR / f"{rns}.html"
        if not cache.exists():
            verdicts["NO_CACHE"] += 1
            print(f"  NO_CACHE        {rns} {r['existing_ticker']:6s} "
                  f"{r['existing_director'][:24]}")
            continue
        html = cache.read_text(encoding="utf-8", errors="replace")
        try:
            extracted, _w, _src = parse_pdmr.parse_announcement(
                html, url="", rns_id=rns,
                announced_at=r["existing_date"],
                ticker_hint=r["existing_ticker"],
            )
        except Exception as exc:  # noqa: BLE001
            verdicts["PARSE_ERROR"] += 1
            print(f"  PARSE_ERROR     {rns} {r['existing_ticker']:6s} {exc}")
            continue

        if not extracted:
            if _has_txn_table(html):
                verdicts["PARSER_GAP"] += 1
                v = "PARSER_GAP"
            else:
                verdicts["EMPTY_CANDIDATE"] += 1
                deletes.append(rns)
                v = "EMPTY_CANDIDATE"
        elif any(_row_matches(p, r["existing_date"], r["existing_type"],
                              r["existing_shares"]) for p in extracted):
            verdicts["NOW_REPRODUCED"] += 1
            v = "NOW_REPRODUCED"
        else:
            verdicts["MISMATCH"] += 1
            v = "MISMATCH"
        print(f"  {v:15s} {rns} {r['existing_ticker']:6s} "
              f"{r['existing_director'][:24]:24s} "
              f"({r['existing_type']} {r['existing_shares']} sh, "
              f"{len(extracted)} parsed)")

    print("\n=== SUMMARY ===")
    for k, n in verdicts.most_common():
        print(f"  {k:15s} {n}")
    print("\n  PARSER_GAP rows are REAL filings the parser can't read yet -> KEEP.")
    print("  These are the parser-coverage backlog (PCA/corporate-holder, SIP, EBT).")
    if deletes:
        print(f"\nGenuine delete candidates (EMPTY_CANDIDATE, no txn markers): "
              f"{len(deletes)}")
        print("  " + ",".join(deletes))
        print("\n  Only after eyeballing each, delete with (Zone B):")
        print("  python .scripts/reparse_corpus.py --confirm --delete-orphans "
              "--limit <comma-separated-rns-ids>")
    else:
        print("\n  No genuine delete candidates -- every unparsed orphan is a "
              "real filing (parser gap). Nothing to delete.")
    print("\nREAD-ONLY -- nothing written.")


if __name__ == "__main__":
    main()
