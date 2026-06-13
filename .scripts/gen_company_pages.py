"""Standalone company-page generator — Stage 5 Sprint 4.

Generates ``outputs/companies/{TICKER}.html`` for every ticker that has at
least one transaction in the DB.  Reads the same inputs as build_dashboard.py
but only writes company pages — does not touch index.html or performance.html.

This script is safe to run independently between full dashboard rebuilds if
you just want to refresh company pages (e.g. after a price backfill).

Usage::

    python -u .scripts/gen_company_pages.py [--out-dir PATH]
                                            [--signals-json PATH]
                                            [--dealings-json PATH]
                                            [--status-json PATH]
                                            [--csv PATH]
                                            [--clusters PATH]
                                            [--pending-json PATH]
                                            [--workers N]
                                            [--rebuild]
                                            [--verbose]

Zone discipline (CLAUDE.md):
  * This script reads Zone-B data (DB, CSV, JSON) — run from Windows Python
    only, never from Claude's bash sandbox.
  * Writes HTML pages to the outputs directory.  HTML is Zone-A (text only)
    so the write is safe through the FUSE mount.

Parallelism: the per-ticker render is CPU-bound and embarrassingly parallel.
``--workers N`` (default: auto = min(cpu_count, 8)) controls the Pool size.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
from build_dashboard import (  # noqa: E402
    DEFAULT_SIGNALS_JSON,
    DEFAULT_DEALINGS_JSON,
    DEFAULT_STATUS_JSON,
    DEFAULT_CSV_PATH,
    DEFAULT_CLUSTERS_PATH,
    DEFAULT_PENDING_PATH,
    _detect_build_sha,
    _sha256,
    _load_backtest_rows,
    _build_active_clusters_lookup,
    _build_company_record,
    _load_pending_per_ticker,
    _tickers_with_transactions,
    _sanitize_ticker,
)
from dashboard import render_company  # noqa: E402
from dashboard import render_helpers as rh  # noqa: E402


def _write_one_ticker(args_tuple) -> tuple[str, int]:
    """Worker function for multiprocessing.Pool.

    Returns (ticker, bytes_written).
    Needs to be a top-level function (picklable) for Pool.
    """
    (ticker, record, out_dir, build_sha) = args_tuple
    fname = _sanitize_ticker(ticker) + ".html"
    written = render_company.render_to_file(
        record,
        out_path=Path(out_dir) / fname,
        build_sha=build_sha,
    )
    return (ticker, written)


def build_company_pages(
    out_dir: Path,
    signals_path: Path,
    clusters_path: Path,
    csv_path: Path,
    build_sha: str,
    pending_path: Path | None = None,
    workers: int | None = None,
    verbose: bool = False,
) -> dict:
    """Generate one HTML file per ticker in the transactions table.

    Returns a summary dict with keys: ``tickers``, ``company_pages``, ``bytes``,
    ``elapsed_s``.
    """
    t0 = time.monotonic()
    companies_dir = out_dir / "companies"
    companies_dir.mkdir(parents=True, exist_ok=True)

    # Load shared inputs.
    signals_data: dict = {}
    if signals_path.exists():
        try:
            signals_data = json.loads(signals_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[warn] could not read {signals_path}: {exc}", file=sys.stderr)

    active_clusters_short = _build_active_clusters_lookup(signals_data)
    generated_at = signals_data.get("generated_at") or rh.now_utc_str()

    backtest_rows = _load_backtest_rows(csv_path)

    clusters_json: list = []
    if clusters_path and clusters_path.exists():
        try:
            clusters_json = json.loads(
                clusters_path.read_text(encoding="utf-8")) or []
        except Exception:
            clusters_json = []

    pending_per_ticker = _load_pending_per_ticker(pending_path) if pending_path else {}

    # Build per-ticker records from DB.
    conn = db.connect()
    try:
        today = datetime.now(timezone.utc).date()
        tickers = _tickers_with_transactions(conn)
        if verbose:
            print(f"[gen_company_pages] {len(tickers)} tickers found in DB")

        records: list[tuple] = []
        for ticker in tickers:
            record = _build_company_record(
                conn, ticker, today, backtest_rows, clusters_json,
                active_clusters_short, generated_at,
                pending_per_ticker=pending_per_ticker,
            )
            records.append((ticker, record, str(companies_dir), build_sha))
    finally:
        conn.close()

    # Render — use Pool if there are many tickers.
    n_workers = workers
    if n_workers is None:
        n_workers = min(multiprocessing.cpu_count(), 8)
    use_pool = len(records) > 10 and n_workers > 1

    total_bytes = 0
    n_written = 0

    if use_pool:
        if verbose:
            print(f"[gen_company_pages] rendering with Pool(workers={n_workers})")
        with multiprocessing.Pool(processes=n_workers) as pool:
            for ticker, written in pool.imap_unordered(_write_one_ticker, records):
                total_bytes += written
                n_written += 1
                if verbose and n_written <= 5:
                    print(f"  [{n_written}] {ticker} ({written} bytes)")
                elif verbose and n_written % 50 == 0:
                    print(f"  [{n_written}/{len(records)}] running...")
    else:
        for args in records:
            ticker, written = _write_one_ticker(args)
            total_bytes += written
            n_written += 1
            if verbose and n_written <= 5:
                print(f"  [{n_written}] {ticker} ({written} bytes)")

    elapsed = time.monotonic() - t0
    return {
        "tickers": len(tickers),
        "company_pages": n_written,
        "bytes": total_bytes,
        "elapsed_s": round(elapsed, 1),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate per-ticker company HTML pages.")
    parser.add_argument("--out-dir",      type=Path, default=ROOT / "outputs")
    parser.add_argument("--signals-json", type=Path, default=DEFAULT_SIGNALS_JSON)
    parser.add_argument("--csv",          type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--clusters",     type=Path, default=DEFAULT_CLUSTERS_PATH)
    parser.add_argument("--pending-json", type=Path, default=DEFAULT_PENDING_PATH)
    parser.add_argument("--workers",      type=int,  default=None,
                        help="Pool worker count (default: min(cpu_count, 8))")
    parser.add_argument("--rebuild",      action="store_true",
                        help="Wipe outputs/companies/ before rebuilding.")
    parser.add_argument("--verbose",      action="store_true")
    parser.add_argument("--build-sha",    default=None)
    args = parser.parse_args(argv)

    if args.rebuild:
        comp_dir = args.out_dir / "companies"
        if comp_dir.exists():
            shutil.rmtree(comp_dir)
            if args.verbose:
                print(f"[gen_company_pages] wiped {comp_dir}")

    build_sha = args.build_sha or _detect_build_sha()
    pending = args.pending_json if args.pending_json.exists() else None

    summary = build_company_pages(
        out_dir=args.out_dir,
        signals_path=args.signals_json,
        clusters_path=args.clusters,
        csv_path=args.csv,
        build_sha=build_sha,
        pending_path=pending,
        workers=args.workers,
        verbose=args.verbose,
    )

    print(
        f"gen_company_pages: {summary['company_pages']} pages "
        f"({summary['bytes']:,} bytes) in {summary['elapsed_s']}s  "
        f"[build_sha={build_sha}]"
    )
    if summary["elapsed_s"] > 30:
        print(
            f"[warn] generation took {summary['elapsed_s']}s > 30s target. "
            f"Consider using --workers to increase parallelism."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
