"""Stage 3 benchmark backfill orchestrator.

Selects every distinct `^…` symbol referenced by tickers_meta.benchmark_symbol
(plus a hard-coded `^FTAS` so the universal fallback is always available)
and fetches close+volume into `prices` with the `^…` symbol stored as-is.

Idempotent (INSERT OR IGNORE). Polite rate limit. Reuses fetch_prices.fetch.

CLI:
    python backfill_benchmarks.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                  [--symbol ^FTAS] [--dry-run]
                                  [--rate-limit FLOAT] [--verbose]

Sprint 10 Phase 5 (2026-05-25):
    * 5.A: per-symbol MAX(date)==today skip (Gate 1 addition).
    * 5.B: abort cleanly after CONSECUTIVE_429_ABORT_THRESHOLD consecutive
      429 failures from Yahoo; skipped symbols logged to
      .data/_benchmark_skipped_due_to_rate_limit.json. Pipeline continues
      past this step into signals/backtest/build.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import fetch_prices  # noqa: E402
from backfill_prices import (  # noqa: E402
    CONSECUTIVE_429_ABORT_THRESHOLD,
    _is_rate_limit_error,
    insert_rows,
)

# Sprint 10 Phase 5.B: benchmark-specific skip log path. Distinct from
# the prices log so the two backfill scripts don't overwrite each
# other's audit trail on the same Yahoo-bad day.
RATE_LIMIT_SKIP_PATH = HERE.parent / ".data" / "_benchmark_skipped_due_to_rate_limit.json"


def _write_rate_limit_skip_log(skipped_symbols, threshold: int) -> None:
    """Persist the list of benchmark symbols skipped due to systemic 429s.

    Mirror of backfill_prices._write_rate_limit_skip_log but writes to
    the benchmark-specific path. Atomic; safe to call once at step end.
    """
    if not skipped_symbols:
        return
    payload = {
        "aborted_at":          db.iso_now(),
        "threshold":           threshold,
        "skipped_symbols":     sorted(skipped_symbols),
        "n_skipped":           len(skipped_symbols),
    }
    RATE_LIMIT_SKIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RATE_LIMIT_SKIP_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, RATE_LIMIT_SKIP_PATH)


def _today() -> str:
    return date.today().isoformat()


def _default_from() -> str:
    return (date.today() - timedelta(days=395)).isoformat()


def distinct_benchmark_symbols(conn) -> list[str]:
    """All `^…` symbols referenced by tickers_meta, plus `^FTAS` always.

    Returns a sorted, deduplicated list.
    """
    rows = conn.execute(
        "SELECT DISTINCT benchmark_symbol FROM tickers_meta "
        "WHERE benchmark_symbol IS NOT NULL AND benchmark_symbol LIKE '^%'"
    ).fetchall()
    symbols = {r["benchmark_symbol"] for r in rows}
    symbols.add("^FTAS")
    return sorted(symbols)


def run(*, date_from: str, date_to: str,
        only_symbol: str | None = None,
        dry_run: bool = False,
        rate_limit: float = 0.5,
        verbose: bool = False) -> dict:
    summary = {
        "symbols": 0, "ok": 0, "delisted": 0,
        "unsupported": 0, "errors": 0, "empty": 0,
        "rows_inserted": 0,
        # Sprint 10 Phase 5 additions:
        "skipped_today":       0,      # 5.A
        "rate_limit_429s":     0,      # 5.B
        "aborted_after_429":   False,  # 5.B
        "abort_skipped_count": 0,      # 5.B
    }
    # Sprint 10 Phase 5.B: consecutive 429 counter across symbols.
    consecutive_429s = 0
    rate_limit_skipped: list[str] = []

    conn = db.connect()
    try:
        symbols = [only_symbol] if only_symbol else distinct_benchmark_symbols(conn)
        today_iso = date.today().isoformat()
        for idx, sym in enumerate(symbols):
            if not sym:
                continue
            summary["symbols"] += 1

            # Sprint 10 Phase 5.A (Gate 1): per-symbol MAX(date)==today
            # skip. Mirrors the rule in backfill_prices.run(). Benchmark
            # symbols (^FTAS, ^FTSE etc.) live in the same `prices`
            # table, so the same query works.
            last_date_row = conn.execute(
                "SELECT MAX(date) AS d FROM prices WHERE ticker = ?", (sym,)
            ).fetchone()
            last_date = last_date_row["d"] if last_date_row and last_date_row["d"] else None
            if last_date and last_date >= today_iso:
                summary["skipped_today"] += 1
                consecutive_429s = 0
                if verbose:
                    print(f"  {sym}: already current for today, skipped (5.A)")
                continue

            # fetch_prices.yahoo_symbol_for already handles the `^` prefix.
            result = fetch_prices.fetch(sym, date_from, date_to, rate_limit=rate_limit)
            if verbose:
                print(f"  {sym}: status={result.status} rows={len(result.rows)} "
                      f"cache_hit={result.cache_hit} net={result.network_calls}")
            if result.status == "ok":
                summary["ok"] += 1
                consecutive_429s = 0
                if not dry_run:
                    summary["rows_inserted"] += insert_rows(conn, sym, result.rows)
            elif result.status == "delisted":
                summary["delisted"] += 1
                consecutive_429s = 0
            elif result.status == "unsupported_currency":
                summary["unsupported"] += 1
                consecutive_429s = 0
            elif result.status == "empty":
                summary["empty"] += 1
                consecutive_429s = 0
            else:
                # Sprint 10 Phase 5.B: distinguish 429 errors from
                # other errors. 429s increment the counter; non-429
                # errors leave it unchanged.
                summary["errors"] += 1
                if _is_rate_limit_error(result):
                    consecutive_429s += 1
                    summary["rate_limit_429s"] += 1
                    rate_limit_skipped.append(sym)
                    if verbose:
                        print(f"  {sym}: 429 #{consecutive_429s} of "
                              f"{CONSECUTIVE_429_ABORT_THRESHOLD}")

            # Sprint 10 Phase 5.B: abort cleanly on systemic 429s.
            # Skip log is written ONLY on abort; scattered 429s in a
            # no-abort run leave no log (visible via summary counts).
            if consecutive_429s >= CONSECUTIVE_429_ABORT_THRESHOLD:
                remaining = [s for s in symbols[idx + 1:] if s]
                rate_limit_skipped.extend(remaining)
                summary["aborted_after_429"] = True
                summary["abort_skipped_count"] = len(remaining)
                _write_rate_limit_skip_log(
                    rate_limit_skipped, CONSECUTIVE_429_ABORT_THRESHOLD
                )
                print(
                    f"[backfill_benchmarks] aborting: "
                    f"{consecutive_429s} consecutive 429s "
                    f"(threshold {CONSECUTIVE_429_ABORT_THRESHOLD}). "
                    f"{len(remaining)} symbol(s) skipped. "
                    f"Logged to {RATE_LIMIT_SKIP_PATH.name}."
                )
                break
    finally:
        conn.close()

    if verbose:
        print(f"backfill_benchmarks: {summary}")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 3 benchmark backfill.")
    ap.add_argument("--from", dest="date_from", default=_default_from())
    ap.add_argument("--to", dest="date_to", default=_today())
    ap.add_argument("--symbol", default=None, help="One specific ^symbol.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rate-limit", type=float, default=0.5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    summary = run(date_from=args.date_from, date_to=args.date_to,
                  only_symbol=args.symbol, dry_run=args.dry_run,
                  rate_limit=args.rate_limit, verbose=args.verbose)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
