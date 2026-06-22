"""B-173 — Cloud-IP scraper spike (THE GATE for the cloud migration).

Purpose: prove (or disprove) that the Investegate RNS scraper and the Yahoo
price fetch still work when run from a GitHub Actions datacenter IP, which
many sites rate-limit or block more harshly than a home broadband IP.

This is a READ-ONLY probe:
  * NO database writes.
  * NO cache writes (uses the low-level index/chart fetchers, not the
    caching `fetch()` wrappers).

It is safe to run from Claude's sandbox, from Rupert's PC, or from a GitHub
Actions runner. The runner result is what decides the migration gate.

Exit code 0 = PASS (both feeds reachable). Exit code 1 = FAIL (one or both
blocked) -> fall back to the "pipeline stays local, writes to Supabase" target.

Run locally:   python .scripts/spike_cloud_scrape.py
Run on CI:     via .github/workflows/spike-cloud-scrape.yml (workflow_dispatch)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make sibling modules importable whether run from repo root or .scripts/.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import scrape_investegate as scraper  # noqa: E402
import fetch_prices as fp  # noqa: E402

# Five liquid FTSE names — if Yahoo serves these, the price feed is alive.
SAMPLE_TICKERS = ["BARC", "HSBA", "BP", "VOD", "LLOY"]
SCRAPE_DAYS = 7
PRICE_DAYS = 30


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def probe_scraper() -> tuple[bool, str]:
    """Walk the Investegate directors-dealings index for the last week."""
    end = _today_utc().date()
    start = end - timedelta(days=SCRAPE_DAYS)
    s, e = start.isoformat(), end.isoformat()
    try:
        scraper.check_robots()
    except Exception as exc:  # robots blocked / unreachable
        return False, f"robots/preflight failed: {type(exc).__name__}: {exc}"
    try:
        rows = list(scraper.iter_index(s, e, max_pages=2))
    except Exception as exc:
        return False, f"iter_index failed: {type(exc).__name__}: {exc}"
    n = len(rows)
    sample = "; ".join((r.get("headline") or "?")[:60] for r in rows[:3])
    ok = n >= 1
    msg = f"{n} index rows over {s}..{e}" + (f" | e.g. {sample}" if sample else "")
    return ok, msg


def probe_prices() -> tuple[bool, str]:
    """Fetch ~30 days of daily closes for the sample tickers (no cache)."""
    end = _today_utc()
    p2 = int(end.timestamp())
    p1 = int((end - timedelta(days=PRICE_DAYS)).timestamp())
    got, details = 0, []
    for tk in SAMPLE_TICKERS:
        sym = fp.yahoo_symbol_for(tk)
        try:
            block = fp.fetch_chart(sym, p1, p2)
            rows = fp.chart_to_rows(block)
            details.append(f"{tk}:{len(rows)}")
            if rows:
                got += 1
        except Exception as exc:
            details.append(f"{tk}:ERR({type(exc).__name__})")
        time.sleep(0.5)  # be polite
    ok = got >= 3  # majority of the sample must return data
    return ok, f"{got}/{len(SAMPLE_TICKERS)} tickers returned data | " + ", ".join(details)


def main() -> int:
    print("=" * 64)
    print("B-173 CLOUD-IP SCRAPER SPIKE — read-only, no DB/cache writes")
    print(f"run at {_today_utc().isoformat()}  python {sys.version.split()[0]}")
    print("=" * 64)

    scrape_ok, scrape_msg = probe_scraper()
    print(f"[{'PASS' if scrape_ok else 'FAIL'}] Investegate RNS index : {scrape_msg}")

    price_ok, price_msg = probe_prices()
    print(f"[{'PASS' if price_ok else 'FAIL'}] Yahoo price feed       : {price_msg}")

    overall = scrape_ok and price_ok
    print("-" * 64)
    if overall:
        print("GATE RESULT: PASS — cloud-IP scraping works. Proceed with full cloud pipeline (M1+).")
    else:
        print("GATE RESULT: FAIL — at least one feed is blocked from this IP.")
        print("  -> Adopt fallback target: pipeline stays LOCAL, writes to Supabase.")
        print("  -> Still kills FUSE corruption + gives anywhere-access; just not 100% PC-free.")
    print("=" * 64)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
