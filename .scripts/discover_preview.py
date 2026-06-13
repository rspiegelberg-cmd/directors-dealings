"""Phase 4 dry-discovery preview — READ-ONLY diff-first deliverable.

Lists the candidate director-dealing announcements that the live scraper's
discovery step (`scrape_investegate.iter_index`) WOULD select for a given
date window, and WRITES NOTHING.

What it does:
  * Fetches only the directors-dealings INDEX page(s) (the same
    `?show=300[&page=N]` URLs `iter_index` walks).
  * Applies the Phase-4 keep+deny filter + fail-open date window.
  * Prints one line per candidate: date | ticker | rns_id | headline | url.

What it explicitly does NOT do (safe in any sandbox / for diff review):
  * No DB connection, no writes to .data/ or any cache dir.
  * Does NOT call `fetch_filing` — individual filings are never downloaded
    or cached. Only the index page(s) are read.
  * No parser, no LLM, no progress file.

CLI:
    python .scripts/discover_preview.py --from 2026-06-01 --to 2026-06-02
    python .scripts/discover_preview.py --days 2
    python .scripts/discover_preview.py --from 2026-06-01 --to 2026-06-02 --json

Exit codes:
    0  success (candidates listed)
    3  index fetch failed (e.g. network blocked in sandbox; robots block)

NOTE (sandbox honesty): Investegate's category page may be JS-rendered and
return stale/partial data via a plain GET from some environments. This tool
deliberately uses the SAME index-fetch path the real scraper uses
(`scrape_investegate._fetch` -> `iter_index`), so whatever it shows here is
exactly what the live daily scrape would discover. If run from a sandbox
where the live GET is unreliable, validate on the Windows machine.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scrape_investegate as scraper


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _resolve_window(args) -> tuple[str, str]:
    if args.from_ and args.to:
        return args.from_, args.to
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    return _iso(start), _iso(end)


def discover(window_start: str, window_end: str, max_pages: int) -> list[dict]:
    """Return the list of candidate rows iter_index would select.

    Read-only: walks index pages only; never fetches individual filings.
    """
    rows: list[dict] = []
    # discover_only=True documents intent; iter_index never fetches filings.
    for row in scraper.iter_index(
        window_start, window_end, max_pages=max_pages, discover_only=True
    ):
        rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Phase 4 dry-discovery preview (READ-ONLY; writes nothing)"
    )
    ap.add_argument("--from", dest="from_", default=None,
                    help="window start YYYY-MM-DD")
    ap.add_argument("--to", default=None, help="window end YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=2,
                    help="trailing window if --from/--to omitted (default 2)")
    ap.add_argument("--max-pages", type=int, default=5,
                    help="max index pages to walk (default 5)")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    window_start, window_end = _resolve_window(args)

    # robots gate — same courtesy as the real scraper. Read-only.
    try:
        scraper.check_robots()
    except scraper.RobotsBlockedError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 3
    except scraper.FetchError as e:
        print(f"ABORT: robots.txt fetch failed -- {e}", file=sys.stderr)
        return 3

    try:
        rows = discover(window_start, window_end, args.max_pages)
    except scraper.FetchError as e:
        print(f"ABORT: index fetch failed -- {e}", file=sys.stderr)
        print(
            "(If running in Claude's sandbox, the live GET may be blocked or "
            "stale — validate on the Windows machine.)",
            file=sys.stderr,
        )
        return 3

    if args.json:
        print(json.dumps({
            "window_start": window_start,
            "window_end": window_end,
            "count": len(rows),
            "candidates": rows,
        }, indent=2))
        return 0

    print(f"Dry-discovery preview — window {window_start} .. {window_end}")
    print(f"Candidates iter_index WOULD fetch: {len(rows)}")
    print("(READ-ONLY — no DB writes, no filing downloads)\n")
    print(f"{'DATE':<22} {'TICKER':<8} {'RNS_ID':<10} HEADLINE")
    print("-" * 100)
    for r in rows:
        date = (r.get("announced_at") or "")[:21]
        ticker = (r.get("ticker_hint") or "")[:7]
        rns_id = (r.get("rns_id") or "")[:9]
        headline = r.get("headline") or ""
        print(f"{date:<22} {ticker:<8} {rns_id:<10} {headline}")
        print(f"{'':<42} {r.get('url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
