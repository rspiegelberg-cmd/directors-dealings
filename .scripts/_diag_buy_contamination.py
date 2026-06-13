"""READ-ONLY diagnostic: size comp-event contamination that actually reaches
the signal layer.

Context: signals only fire on buy_strictness='STRICT_BUY'. So a comp event
(Bonus Deferral / Deferred Bonus Plan / Share Purchase Plan / scrip dividend /
SAYE / ESPP / SIP matching-partnership-free shares) is only dangerous if the
parser tagged it STRICT_BUY by mistake. This script counts:

  Part A  EXISTING corpus:
    - buy_strictness distribution across all type=BUY rows
    - how many STRICT_BUY rows carry comp-event language in `context`
      that the classifier should have demoted (the misses)
    - of those misses, how many actually have a signal fired (blast radius
      on Brief 01/02)
  Part B  The 685 reparse INSERT BUYs:
    - buy_strictness distribution the parser assigns them
    - how many comp-event-suspect inserts are STRICT_BUY (would reach signals
      if committed) vs already gated as NON_BUY_ONLY/UNKNOWN

Writes NOTHING to the DB or caches. Run from PowerShell:
    python .scripts/_diag_buy_contamination.py
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import reparse_corpus as rc  # noqa: E402

CACHE_DIR = HERE / "_scrape_cache"

# Comp-event language the auditor found slipping through as type=BUY.
# These are forms a genuine on-market purchase would NOT contain.
SUSPECT_RE = re.compile(
    r"deferred\s+bonus|bonus\s+deferral|\bDBP\b|deferred\s+share|"
    r"share\s+purchase\s+plan|\bSPP\b|\bSAYE\b|\bESPP\b|"
    r"scrip|dividend\s+reinvest|dividend\s+shares?|in\s+lieu\s+of\s+dividend|"
    r"matching\s+shares?|partnership\s+shares?|free\s+shares?|"
    r"\bLTIP\b|\bPSP\b|\bRSU\b|vesting|award",
    re.IGNORECASE,
)


def part_a(conn) -> None:
    print("=== PART A — EXISTING corpus type=BUY ===")
    dist = Counter()
    for r in conn.execute(
        "SELECT COALESCE(buy_strictness,'(null)') AS bs, COUNT(*) AS n "
        "FROM transactions WHERE type='BUY' GROUP BY bs"
    ):
        dist[r["bs"]] = r["n"]
    total_buys = sum(dist.values())
    print(f"  total BUY rows: {total_buys}")
    for bs, n in dist.most_common():
        print(f"    {bs:14s} {n:5d}")

    # STRICT_BUY rows carrying comp-event language = classifier misses.
    strict = conn.execute(
        "SELECT fingerprint, COALESCE(context,'') AS context "
        "FROM transactions WHERE type='BUY' AND buy_strictness='STRICT_BUY'"
    ).fetchall()
    suspect_fps = [r["fingerprint"] for r in strict
                   if SUSPECT_RE.search(r["context"])]
    print(f"  STRICT_BUY rows: {len(strict)}")
    print(f"  STRICT_BUY rows w/ comp-event language (MISSES): "
          f"{len(suspect_fps)}")

    # Blast radius: how many suspect STRICT_BUY rows have a signal fired.
    if suspect_fps:
        qmarks = ",".join("?" * len(suspect_fps))
        fired = conn.execute(
            f"SELECT COUNT(DISTINCT fingerprint) FROM signals "
            f"WHERE fingerprint IN ({qmarks})", suspect_fps
        ).fetchone()[0]
        print(f"  of those, with >=1 signal fired (Brief 01/02 blast "
              f"radius): {fired}")
        # show a few examples
        ex = conn.execute(
            f"SELECT ticker, director, value, "
            f"  substr(context,1,70) AS ctx "
            f"FROM transactions WHERE fingerprint IN ({qmarks}) "
            f"ORDER BY value DESC LIMIT 8", suspect_fps
        ).fetchall()
        print("  examples (highest value):")
        for e in ex:
            print(f"    {e['ticker']:6s} {str(e['director'])[:22]:22s} "
                  f"£{e['value']:>12,.0f}  {e['ctx']!r}")
    print()


def part_b(conn) -> None:
    print("=== PART B — 685 reparse INSERT BUYs ===")
    cache_files = sorted(CACHE_DIR.glob("*.html"))
    excluded = rc._load_excluded_tickers(conn)
    existing_by_rns = rc._load_existing_by_url(conn)

    strict_dist = Counter()
    suspect_total = 0
    suspect_strict = 0
    for html_path in cache_files:
        rns_id = html_path.stem
        try:
            html = html_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        result = rc.process_filing(conn, rns_id, html, existing_by_rns, excluded)
        for a in result["actions"]:
            if a["kind"] != "insert":
                continue
            nr = a["new_row"]
            if nr.get("type") != "BUY":
                continue
            bs = nr.get("buy_strictness") or "(null)"
            strict_dist[bs] += 1
            ctx = (nr.get("context") or "")
            if SUSPECT_RE.search(ctx):
                suspect_total += 1
                if bs == "STRICT_BUY":
                    suspect_strict += 1

    print("  buy_strictness the parser assigns the insert BUYs:")
    for bs, n in strict_dist.most_common():
        print(f"    {bs:14s} {n:5d}")
    print(f"  comp-event-suspect insert BUYs (any strictness): {suspect_total}")
    print(f"  ...of which tagged STRICT_BUY (WOULD reach signals): "
          f"{suspect_strict}")
    print()


def main() -> int:
    conn = db.connect()
    try:
        part_a(conn)
        part_b(conn)
        print("NOTE: 'misses' / 'STRICT suspect' are the only rows that can "
              "pollute signals. NON_BUY_ONLY / UNKNOWN are already gated out.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
