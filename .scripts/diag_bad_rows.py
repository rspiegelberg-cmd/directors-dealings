"""diag_bad_rows.py -- One-shot diagnostic for the 15 I3-failing rows.

Reads .data/_date_audit_report.json, joins each anomaly fingerprint to
the transactions table, and prints fingerprint + ticker + date + url +
parser_source. Tells us in one glance whether the bad dates came from
the regex parser or the LLM fallback.

USAGE
-----
    python .scripts/diag_bad_rows.py

Read-only. Safe to run any time.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / ".data" / "directors.db"
REPORT_PATH = ROOT / ".data" / "_date_audit_report.json"


def main() -> int:
    if not REPORT_PATH.exists():
        print(f"ERROR: {REPORT_PATH} not found. Run audit_dates.py first.")
        return 1
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found.")
        return 1

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    anomalies = report.get("anomalies") or {}
    fps_I3 = [a["fingerprint"] for a in anomalies.get("I3", [])]
    fps_I5 = [a["fingerprint"] for a in anomalies.get("I5", [])]
    all_fps = list(dict.fromkeys(fps_I3 + fps_I5))  # de-dup, preserve order

    if not all_fps:
        print("No anomalies in report. Nothing to inspect.")
        return 0

    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    print(f"Inspecting {len(all_fps)} flagged transactions")
    print("=" * 110)
    print(f"{'fingerprint':<18} {'ticker':<8} {'date':<12} {'announced_at':<24} "
          f"{'src':<6} {'director':<25} url")
    print("-" * 110)

    src_counts: dict[str, int] = {}

    for fp in all_fps:
        row = conn.execute(
            "SELECT fingerprint, ticker, date, announced_at, director, "
            "       parser_source, url "
            "FROM transactions WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if not row:
            print(f"{fp:<18} (not in DB!)")
            continue
        src = row["parser_source"] or "?"
        src_counts[src] = src_counts.get(src, 0) + 1
        director = (row["director"] or "")[:24].replace("\n", "\\n")
        ann = (row["announced_at"] or "")[:22]
        url_short = (row["url"] or "").split("/")[-1] if row["url"] else "-"
        print(f"{fp:<18} {row['ticker'] or '-':<8} {row['date'] or '-':<12} "
              f"{ann:<24} {src:<6} {director:<25} ...{url_short}")

    conn.close()

    print("-" * 110)
    print("Parser source distribution:")
    for k, v in sorted(src_counts.items()):
        print(f"  {k}: {v}")
    print()
    print("Interpretation:")
    print("  All 'regex' -> the regex parser's latest-wins fallback is firing")
    print("                 on a template variant it doesn't handle.")
    print("  All 'llm'   -> the LLM fallback is hallucinating dates.")
    print("  Mixed       -> both. Fix both.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
