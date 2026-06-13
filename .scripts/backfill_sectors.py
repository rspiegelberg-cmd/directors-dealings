"""backfill_sectors.py — populate `tickers_meta.sector` from Financial Modeling
Prep (B-128).

ZONE B — writes to `.data/directors.db` and `.scripts/_fmp_cache/`.
**Rupert runs this**; Claude never runs it from bash.

Why
---
~86% of tickers have no sector (only the hand-maintained `sector_map.csv` does).
Yahoo's `assetProfile`/`quoteSummary` endpoint is blocked, so this uses FMP's
company-profile endpoint, which is keyed by ticker (LSE tickers use a `.L`
suffix, e.g. `LSEG.L`) and returns GICS `sector` / `industry` / `website` — the
same sector taxonomy already in `sector_map.csv`, so no remapping is needed.

For each ticker with a NULL sector, this fetches the profile, writes the sector
(and website if missing), and re-resolves `benchmark_symbol` from the new sector
via `benchmark_symbols.json` — so a sector-matched benchmark actually takes
effect. The curated `sector_map.csv` always wins (only NULL sectors are filled).

Setup (one-time, Rupert)
------------------------
1. Get a FREE FMP API key: https://site.financialmodelingprep.com/ (no payment).
2. Set it as an env var (PowerShell, user scope):
       setx DD_FMP_API_KEY "<your key>"
   (open a new shell afterwards so it's picked up)
3. The free tier is rate-limited (~250 requests/day). This script is RESUMABLE:
   successful fetches are cached under `.scripts/_fmp_cache/`, so re-running on
   later days skips what's already fetched. On an HTTP 429 it stops cleanly and
   reports how far it got.

Run
---
    python .scripts\\backfill_sectors.py --dry-run     # fetch + report, write nothing
    python .scripts\\backfill_sectors.py               # write sectors
    python .scripts\\backfill_sectors.py --limit 200   # cap requests this run
    python .scripts\\backfill_sectors.py --normalise   # fix already-written FMP names (B-163)
    python .scripts\\snapshot_db.py                    # then snapshot
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import fetch_sectors  # noqa: E402 — reuse benchmark resolution

# --- Constants --------------------------------------------------------------

# FMP "stable" company-profile endpoint (keyed by symbol). Returns a JSON list
# with one object carrying `sector`, `industry`, `website`, `exchange`, etc.
FMP_PROFILE_URL = "https://financialmodelingprep.com/stable/profile"
API_KEY_ENV = "DD_FMP_API_KEY"
CACHE_DIR = HERE / "_fmp_cache"
REQUEST_DELAY_SECONDS = 0.4   # polite gap between live fetches
MAX_RETRIES = 3
USER_AGENT = ("DirectorsDealingsBot/1.0 (personal research project; "
              "contact rspiegelberg@gmail.com)")

# B-163: FMP uses slightly different GICS sector spellings from the hand-curated
# sector_map.csv (which follows Yahoo Finance / original GICS conventions).
# Map FMP names -> project-canonical names so all tickers land in one bucket.
SECTOR_NORMALISE: dict[str, str] = {
    "Financial Services":    "Financials",
    "Consumer Cyclical":     "Consumer Discretionary",
    "Consumer Defensive":    "Consumer Staples",
    "Basic Materials":       "Materials",
    "Healthcare":            "Health Care",
}


# --- Pure helpers (unit-tested; no network / no DB) -------------------------

def candidate_symbols(ticker: str) -> list[str]:
    """FMP symbols to try for a bare LSE ticker: '<T>.L' first, then bare '<T>'.

    LSE tickers carry a '.L' suffix on FMP; a few dual-listed names resolve on
    the bare symbol. Dotted tickers (e.g. 'BT.A') also try the dot-stripped form.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return []
    out = [f"{t}.L", t]
    if "." in t:
        out.append(t.replace(".", "") + ".L")
    # de-dupe, preserve order
    seen: set = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def sector_from_payload(payload) -> dict | None:
    """Extract {sector, industry, website} from an FMP profile JSON payload.

    FMP returns a list with a single object (or an empty list / error dict).
    Returns None when no usable sector is present.
    """
    obj = None
    if isinstance(payload, list) and payload:
        obj = payload[0]
    elif isinstance(payload, dict) and payload.get("symbol"):
        obj = payload
    if not isinstance(obj, dict):
        return None
    sector = (obj.get("sector") or "").strip()
    if not sector:
        return None
    return {
        "sector": sector,
        "industry": (obj.get("industry") or "").strip() or None,
        "website": (obj.get("website") or "").strip() or None,
    }


# --- Network (lazy requests import so pure helpers test without the dep) -----

def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.json"


def _read_cache(ticker: str) -> dict | None:
    """Return the cached parsed profile (no network), or None on miss."""
    cache = _cache_path(ticker)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8")) or None
        except (ValueError, OSError):
            return None
    return None


def fetch_profile(ticker: str, api_key: str, *, use_cache: bool = True) -> dict | None:
    """Fetch + parse one ticker's profile. Returns {sector,industry,website} or
    None. Caches the parsed result so re-runs are free. Raises RuntimeError with
    'RATE_LIMIT' in the message on HTTP 429 so the caller can stop cleanly.
    """
    cache = _cache_path(ticker)
    if use_cache and cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            return cached or None
        except (ValueError, OSError):
            pass

    import requests  # lazy

    last_exc: Exception | None = None
    for sym in candidate_symbols(ticker):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.get(
                    FMP_PROFILE_URL,
                    params={"symbol": sym, "apikey": api_key},
                    headers={"User-Agent": USER_AGENT}, timeout=30)
                if resp.status_code == 429:
                    raise RuntimeError("RATE_LIMIT (HTTP 429 from FMP)")
                resp.raise_for_status()
                parsed = sector_from_payload(resp.json())
                time.sleep(REQUEST_DELAY_SECONDS)
                if parsed:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(parsed), encoding="utf-8")
                    return parsed
                break  # symbol resolved but no sector — try next candidate
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001 — retry/backoff
                last_exc = exc
                time.sleep(REQUEST_DELAY_SECONDS * attempt)
    if last_exc:
        print(f"[sectors] {ticker}: fetch failed ({last_exc})")
    return None


# --- DB ---------------------------------------------------------------------

def tickers_missing_sector(conn) -> list[str]:
    """Distinct transaction tickers whose tickers_meta.sector is NULL/empty."""
    rows = conn.execute(
        "SELECT DISTINCT t.ticker FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.ticker NOT LIKE '^%' AND t.ticker IS NOT NULL "
        "  AND (tm.sector IS NULL OR tm.sector = '') "
        "ORDER BY t.ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


def write_sector(conn, ticker: str, info: dict, benchmark_symbol: str | None) -> None:
    """Fill sector (+ website if missing) + benchmark, only where sector is NULL
    so the curated sector_map.csv is never overwritten.

    B-158 AIM guard: if is_aim=1 the benchmark_symbol is preserved as '^FTSC'.
    All GICS sectors currently map to '^FTAS' (sector indices dead on Yahoo), so
    without this guard backfill_sectors would silently overwrite the AIM benchmark
    fix from B-147 for the 38+ AIM tickers that still lack a sector.
    """
    conn.execute(
        "INSERT INTO tickers_meta (ticker, sector, benchmark_symbol, "
        "  website_url, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET "
        "  sector = excluded.sector, "
        "  benchmark_symbol = CASE WHEN tickers_meta.is_aim = 1 THEN '^FTSC' "
        "                         ELSE excluded.benchmark_symbol END, "
        "  website_url = COALESCE(NULLIF(tickers_meta.website_url, ''), "
        "                         excluded.website_url), "
        "  updated_at = excluded.updated_at "
        "WHERE tickers_meta.sector IS NULL OR tickers_meta.sector = ''",
        (ticker, info["sector"], benchmark_symbol, info.get("website"),
         db.iso_now()),
    )


# --- B-163: normalise existing rows -----------------------------------------

def normalise_existing(conn) -> int:
    """UPDATE rows already in the DB that have non-canonical sector names.

    Safe to run repeatedly — a second run finds zero rows to update.
    Returns total rows updated.
    """
    total = 0
    for fmp_name, canonical in SECTOR_NORMALISE.items():
        cur = conn.execute(
            "UPDATE tickers_meta SET sector = ?, updated_at = ? "
            "WHERE sector = ?",
            (canonical, db.iso_now(), fmp_name),
        )
        n = cur.rowcount
        if n > 0:
            print(f"  [normalise] {fmp_name!r} -> {canonical!r}: {n} rows")
        total += n
    return total


# --- CLI --------------------------------------------------------------------

def run(*, dry_run: bool = False, limit: int = 0, verbose: bool = False) -> dict:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"[sectors] {API_KEY_ENV} not set. Get a free key at "
              f"https://site.financialmodelingprep.com/ and `setx {API_KEY_ENV} "
              f'"<key>"`. See this script\'s docstring.')
        return {"processed": 0, "written": 0, "no_sector": 0, "skipped_no_key": True}

    benchmark_symbols = fetch_sectors._load_benchmark_symbols()
    summary = {"processed": 0, "written": 0, "no_sector": 0, "rate_limited": False}
    conn = db.connect()
    try:
        todo = tickers_missing_sector(conn)
        if limit > 0:
            todo = todo[:limit]
        print(f"[sectors] {len(todo)} tickers missing a sector"
              f"{' (capped)' if limit else ''}")
        rate_limited = False
        for tk in todo:
            summary["processed"] += 1
            # Cached tickers cost NO quota — always write them. Only fetch
            # (network) on a cache miss, and once the daily 429 hits, stop
            # fetching but KEEP writing the remaining already-cached ones.
            info = _read_cache(tk)
            if info is None and not rate_limited:
                try:
                    info = fetch_profile(tk, api_key)
                except RuntimeError:
                    rate_limited = True
                    summary["rate_limited"] = True
                    print("[sectors] FMP daily rate limit hit — writing only the "
                          "already-cached results for the rest of this run; re-run "
                          "tomorrow for the remainder (progress is cached).")
                    info = None
            if not info:
                summary["no_sector"] += 1
                continue
            sector = SECTOR_NORMALISE.get(info["sector"], info["sector"])
            info = dict(info, sector=sector)  # propagate canonical name
            bench = (benchmark_symbols.get(sector)
                     or benchmark_symbols.get("_default", "^FTAS"))
            if verbose:
                print(f"  {tk} -> {sector} (bench {bench})")
            if not dry_run:
                write_sector(conn, tk, info, bench)
                summary["written"] += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    print(f"[sectors] processed={summary['processed']} written={summary['written']} "
          f"no_sector={summary['no_sector']} {'(dry-run)' if dry_run else ''}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill tickers_meta.sector from FMP (B-128).")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + report but write nothing.")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of tickers processed this run "
                         "(for the free-tier daily quota).")
    ap.add_argument("--normalise", action="store_true",
                    help="(B-163) Fix already-written FMP sector names to "
                         "project-canonical names and exit. No FMP fetch.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.normalise:
        conn = db.connect()
        try:
            total = normalise_existing(conn)
            conn.commit()
        finally:
            conn.close()
        print(f"[normalise] {total} rows updated.")
        return 0
    run(dry_run=args.dry_run, limit=args.limit, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
