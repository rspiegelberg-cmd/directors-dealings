"""restore_sectors_from_cache.py — one-shot sector restore from local FMP cache.

Reads every .json file in .scripts/_fmp_cache/ (produced by a prior
backfill_sectors.py run) and writes the sector data directly to Supabase
Postgres.  No FMP API calls — uses cached data only.

USAGE (PowerShell):
    $env:DD_DATABASE_URL = "<your Supabase connection string>"
    python .scripts\\restore_sectors_from_cache.py --dry-run   # preview
    python .scripts\\restore_sectors_from_cache.py              # write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE_DIR = HERE / "_fmp_cache"

# Same sector normalisation as backfill_sectors.py
SECTOR_NORMALISE: dict[str, str] = {
    "Financial Services":    "Financials",
    "Consumer Cyclical":     "Consumer Discretionary",
    "Consumer Defensive":    "Consumer Staples",
    "Basic Materials":       "Materials",
    "Healthcare":            "Health Care",
}

# Sector -> benchmark (mirrors benchmark_symbols.json defaults)
BENCHMARK_BY_SECTOR: dict[str, str] = {
    "Technology":               "^FTAS",
    "Financials":               "^FTAS",
    "Health Care":              "^FTAS",
    "Consumer Discretionary":   "^FTAS",
    "Consumer Staples":         "^FTAS",
    "Energy":                   "^FTAS",
    "Materials":                "^FTAS",
    "Industrials":              "^FTAS",
    "Utilities":                "^FTAS",
    "Real Estate":              "^FTAS",
    "Communication Services":   "^FTAS",
}
DEFAULT_BENCHMARK = "^FTAS"


def load_cache() -> dict[str, dict]:
    """Load all cached profiles. Returns {ticker: {sector, industry, website}}."""
    out: dict[str, dict] = {}
    if not CACHE_DIR.exists():
        return out
    for path in CACHE_DIR.glob("*.json"):
        ticker = path.stem.upper()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data and isinstance(data, dict) and data.get("sector"):
                sector = SECTOR_NORMALISE.get(data["sector"], data["sector"])
                out[ticker] = {
                    "sector": sector,
                    "industry": data.get("industry"),
                    "website": data.get("website"),
                }
        except (ValueError, OSError):
            continue
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Restore tickers_meta.sector from local FMP cache -> Postgres"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be written, make no DB changes")
    args = ap.parse_args(argv)

    cache = load_cache()
    if not cache:
        print("[warn] No cache files found in", CACHE_DIR)
        return 1

    print(f"Found {len(cache)} cached profiles in {CACHE_DIR}")

    dsn = os.environ.get("DD_DATABASE_URL")
    if not dsn:
        sys.exit("[FAIL] DD_DATABASE_URL not set — set the Supabase connection string first.")

    import psycopg  # noqa: PLC0415

    written = 0
    skipped = 0
    errors = 0

    with psycopg.connect(dsn, connect_timeout=30) as pg:
        for ticker, info in sorted(cache.items()):
            sector = info["sector"]
            website = info.get("website")
            benchmark = BENCHMARK_BY_SECTOR.get(sector, DEFAULT_BENCHMARK)

            if args.dry_run:
                print(f"  {ticker:<8} -> sector={sector!r:30s}  benchmark={benchmark}")
                written += 1
                continue

            try:
                with pg.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tickers_meta (ticker, sector, benchmark_symbol, website_url, updated_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (ticker) DO UPDATE SET
                            sector           = EXCLUDED.sector,
                            benchmark_symbol = CASE
                                WHEN tickers_meta.is_aim = 1 THEN '^FTSC'
                                ELSE EXCLUDED.benchmark_symbol
                                END,
                            website_url      = COALESCE(
                                NULLIF(tickers_meta.website_url, ''),
                                EXCLUDED.website_url
                            ),
                            updated_at       = EXCLUDED.updated_at
                        WHERE tickers_meta.sector IS NULL OR tickers_meta.sector = ''
                        """,
                        (ticker, sector, benchmark, website),
                    )
                written += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [error] {ticker}: {exc}")
                errors += 1

        if not args.dry_run:
            pg.commit()

    action = "Would write" if args.dry_run else "Wrote"
    print(f"\n{action} {written} sectors, {skipped} skipped, {errors} errors.")
    if not args.dry_run:
        print("Sectors are now live in Supabase — refresh the dashboard to see them.")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
