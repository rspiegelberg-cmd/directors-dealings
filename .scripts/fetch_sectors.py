"""Ticker -> sector / benchmark / AIM resolver. Stage 3.

Reads `sector_map.csv` + `benchmark_symbols.json`. Upserts one row per
distinct ticker in `transactions` into `tickers_meta`.

Resolution order for `benchmark_symbol`:
  1. CSV row for `ticker` provides an explicit `benchmark_symbol`.
  2. CSV row's `sector` looked up in `benchmark_symbols.json`.
  3. JSON `_default` (currently '^FTAS').

Resolution order for `sector` / `is_aim`:
  * CSV row direct match -> use as-is.
  * No CSV row -> sector=NULL, is_aim=0, benchmark_symbol=JSON _default.

CLI:
    python fetch_sectors.py [--ticker TICKER] [--dry-run] [--verbose]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

SECTOR_MAP_PATH = HERE / "sector_map.csv"
BENCHMARK_JSON_PATH = HERE / "benchmark_symbols.json"


class TickerMeta(NamedTuple):
    ticker: str
    sector: str | None
    benchmark_symbol: str | None
    is_aim: int


def _load_sector_map(path: Path = SECTOR_MAP_PATH) -> dict[str, dict]:
    """Return {ticker: {sector, benchmark_symbol, is_aim}}.

    Lines starting with '#' (after optional whitespace) are comments and
    skipped before csv.DictReader sees them.
    """
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    cleaned = [ln for ln in lines if not ln.lstrip().startswith("#") and ln.strip()]
    if not cleaned:
        return {}
    reader = csv.DictReader(cleaned)
    out: dict[str, dict] = {}
    for row in reader:
        ticker = (row.get("ticker") or "").strip()
        if not ticker:
            continue
        try:
            is_aim = int((row.get("is_aim") or "0").strip())
        except ValueError:
            is_aim = 0
        out[ticker] = {
            "sector": (row.get("sector") or "").strip() or None,
            "benchmark_symbol": (row.get("benchmark_symbol") or "").strip() or None,
            "is_aim": 1 if is_aim else 0,
        }
    return out


def _load_benchmark_symbols(path: Path = BENCHMARK_JSON_PATH) -> dict[str, str]:
    """Return the JSON dict (sector -> ^symbol; also _default)."""
    if not path.exists():
        return {"_default": "^FTAS"}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"_default": "^FTAS"}
    if "_default" not in data:
        data["_default"] = "^FTAS"
    return data


def resolve(ticker: str,
            sector_map: dict[str, dict] | None = None,
            benchmark_symbols: dict[str, str] | None = None) -> TickerMeta:
    """Resolve a single bare-LSE ticker to its sector/benchmark/AIM tuple.

    Pure function — no DB access. Designed to be easy to unit test.
    """
    sector_map = sector_map if sector_map is not None else _load_sector_map()
    benchmark_symbols = (benchmark_symbols
                         if benchmark_symbols is not None
                         else _load_benchmark_symbols())
    default_symbol = benchmark_symbols.get("_default", "^FTAS")

    row = sector_map.get(ticker)
    if row is None:
        return TickerMeta(ticker=ticker, sector=None,
                          benchmark_symbol=default_symbol, is_aim=0)

    sector = row.get("sector")
    is_aim_flag = int(row.get("is_aim") or 0)
    csv_symbol = row.get("benchmark_symbol")
    if csv_symbol:
        benchmark_symbol = csv_symbol
    elif sector and sector in benchmark_symbols:
        benchmark_symbol = benchmark_symbols[sector]
    else:
        benchmark_symbol = default_symbol

    # B-105 Sprint 26: AIM override guard.
    # If sector_map flags this ticker as AIM (is_aim=1), force the benchmark
    # to '^FTSC' (FTSE Small Cap) regardless of what the sector lookup resolved.
    # This prevents AIM stocks from being benchmarked against FTSE All-Share.
    # Note: '^AIM' is no longer available on Yahoo Finance (confirmed delisted
    # 2026-06-07). Using '^FTSC' as a proxy for AIM/small-cap performance.
    # backfill_ticker_meta.py also sets is_aim=1 / benchmark_symbol='^FTSC'
    # dynamically from Yahoo quoteType.exchange.
    if is_aim_flag == 1:
        benchmark_symbol = "^FTSC"

    return TickerMeta(
        ticker=ticker,
        sector=sector,
        benchmark_symbol=benchmark_symbol,
        is_aim=is_aim_flag,
    )


def upsert_meta(conn, meta: TickerMeta) -> None:
    """Write one tickers_meta row, preserving Sprint 26 enrichment columns.

    Uses INSERT ... ON CONFLICT DO UPDATE so that fetch_sectors.py only
    manages sector / benchmark_symbol / is_aim / updated_at.
    The Sprint 26 enrichment columns (market_cap_gbp, shares_outstanding,
    website_url) are preserved from any prior backfill_ticker_meta.py run.

    B-105 Sprint 26: INSERT OR REPLACE was changed to this pattern because
    INSERT OR REPLACE deletes + re-inserts the row, resetting the new
    Sprint 26 columns to NULL and undoing backfill_ticker_meta enrichment.

    COALESCE on sector: fetch_sectors.py only knows sectors from sector_map.csv
    (~110 tickers). For all others it resolves sector=NULL. Using COALESCE
    preserves any sector written by backfill_sectors.py (FMP API) rather than
    overwriting it with NULL on every daily refresh.
    """
    conn.execute(
        "INSERT INTO tickers_meta "
        "(ticker, sector, benchmark_symbol, is_aim, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET "
        "  sector           = COALESCE(excluded.sector, tickers_meta.sector), "
        "  benchmark_symbol = excluded.benchmark_symbol, "
        "  is_aim           = excluded.is_aim, "
        "  updated_at       = excluded.updated_at",
        (meta.ticker, meta.sector, meta.benchmark_symbol, meta.is_aim, db.iso_now()),
    )


def distinct_tickers(conn) -> list[str]:
    """All distinct stock tickers in transactions (excluding ^-prefixed)."""
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions "
        "WHERE ticker NOT LIKE '^%' AND ticker IS NOT NULL "
        "ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


def run(*, only_ticker: str | None = None,
        dry_run: bool = False, verbose: bool = False) -> dict:
    """Orchestrator. Returns a summary dict {processed, written, skipped}."""
    sector_map = _load_sector_map()
    benchmark_symbols = _load_benchmark_symbols()

    summary = {"processed": 0, "written": 0, "skipped": 0}
    conn = None
    try:
        conn = db.connect()
        tickers = [only_ticker] if only_ticker else distinct_tickers(conn)
        for t in tickers:
            if not t:
                continue
            summary["processed"] += 1
            meta = resolve(t, sector_map, benchmark_symbols)
            if verbose:
                print(f"  {t} -> sector={meta.sector} "
                      f"benchmark={meta.benchmark_symbol} aim={meta.is_aim}")
            if dry_run:
                summary["skipped"] += 1
                continue
            upsert_meta(conn, meta)
            summary["written"] += 1
        if not dry_run:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    if verbose:
        print(f"fetch_sectors: {summary}")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve tickers -> tickers_meta.")
    ap.add_argument("--ticker", default=None, help="Resolve a single ticker only.")
    ap.add_argument("--dry-run", action="store_true", help="Resolve but don't write.")
    ap.add_argument("--verbose", action="store_true", help="Print each resolution.")
    args = ap.parse_args(argv)
    summary = run(only_ticker=args.ticker, dry_run=args.dry_run, verbose=args.verbose)
    print(f"{summary['processed']} processed, {summary['written']} written, "
          f"{summary['skipped']} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
