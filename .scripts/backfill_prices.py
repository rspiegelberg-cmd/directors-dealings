"""Stage 3 price backfill orchestrator.

Reads distinct stock tickers from `transactions` (excluding ^-prefixed
benchmarks), fetches each via `fetch_prices.fetch`, and writes close+volume
rows to `prices` via INSERT OR IGNORE. Resumable across crashes via
`.scripts/_price_progress.json`.

PER-TICKER SMART RANGE (v1.3, Rupert 2026-05-13; B-162 2026-06-10):
    For each ticker, the effective fetch start date is computed as:
      * NEW ticker (no rows in prices) -> use --from (full 5-year window)
      * EXISTING ticker (rows exist)    -> max(MAX(date)+1, --from)
    If the effective start > --to, the ticker is skipped as already current.
    This means re-running daily costs almost nothing (only fetches the new
    day), while a newly-onboarded ticker from Stage 2 automatically gets
    its full 5 years of history so charts have deep context.

    --extend bypasses the smart range entirely, forcing the full --from window
    for every ticker (including ones that already have data). Use once for the
    B-162 one-time historic backward fill; daily runs do not need it.

CLI:
    python backfill_prices.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                              [--ticker TICKER] [--dry-run]
                              [--rate-limit FLOAT] [--resume] [--extend]
                              [--verbose]

Defaults:
    --from = today - 1827 days (~5 years, B-162 extended window)
    --to   = today
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
import db_health  # noqa: E402
import fetch_prices  # noqa: E402

PROGRESS_PATH = HERE / "_price_progress.json"


def _today() -> str:
    return date.today().isoformat()


def _default_from() -> str:
    # B-162: 5-year window (365*5 + 2 leap-day buffer = 1827 days).
    return (date.today() - timedelta(days=1827)).isoformat()


def _read_progress() -> dict:
    if not PROGRESS_PATH.exists():
        return {"completed_tickers": [], "last_run": None}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"completed_tickers": [], "last_run": None}


def _write_progress_atomic(state: dict) -> None:
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, PROGRESS_PATH)


def distinct_stock_tickers(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions "
        "WHERE ticker NOT LIKE '^%' AND ticker IS NOT NULL "
        "ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


def ticker_effective_from(conn, ticker: str, base_from: str) -> str:
    """Compute the per-ticker effective start date.

    Policy (locked v1.3, per Rupert 2026-05-13):
      * NEW ticker (no rows in `prices`) -> full `base_from` window. This
        guarantees a freshly-onboarded ticker gets 13 months of history
        so its Stage 5 chart has context.
      * EXISTING ticker -> incremental: fetch from MAX(date)+1, but never
        earlier than `base_from`.

    Returns the ISO date string to use as `period1` for this ticker.
    Caller skips the ticker if the returned date is > date_to.
    """
    row = conn.execute(
        "SELECT MAX(date) AS d FROM prices WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    max_date = row["d"] if row and row["d"] else None
    if max_date is None:
        return base_from
    next_date = (date.fromisoformat(max_date) + timedelta(days=1)).isoformat()
    return max(next_date, base_from)


def insert_rows(conn, ticker: str, rows: list[dict]) -> int:
    """INSERT OR IGNORE rows, now including high and low (B-157 OHLCV).

    Returns the number of rows inserted (or that would be inserted in
    dry-run mode -- we count `rows` for parity).
    """
    fetched_at = db.iso_now()
    cursor = conn.cursor()
    inserted = 0
    # B-179: INSERT OR IGNORE (SQLite) <-> ON CONFLICT DO NOTHING (Postgres) on
    # PK (ticker, date). rowcount semantics match (1 on insert, 0 on conflict).
    if db.backend() == "postgres":
        _px_sql = (
            "INSERT INTO prices "
            "(ticker, date, close, high, low, volume, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'yahoo', ?) "
            "ON CONFLICT (ticker, date) DO NOTHING"
        )
    else:
        _px_sql = (
            "INSERT OR IGNORE INTO prices "
            "(ticker, date, close, high, low, volume, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'yahoo', ?)"
        )
    for r in rows:
        try:
            cursor.execute(
                _px_sql,
                (ticker, r["date"], r["close"],
                 r.get("high"), r.get("low"), r.get("volume"), fetched_at),
            )
            inserted += cursor.rowcount
        except Exception:  # noqa: BLE001
            continue
    conn.commit()
    return inserted


def update_hl_nulls(conn, ticker: str, rows: list[dict]) -> int:
    """B-157: UPDATE existing rows where high/low are NULL with fetched values.

    Called during --backfill-hl pass so that rows inserted before the
    B-157 fix (which omitted high/low) get backfilled.  Only updates
    rows that have both valid high and low in the fetched data.

    Returns the number of DB rows updated.
    """
    cursor = conn.cursor()
    updated = 0
    for r in rows:
        hi = r.get("high")
        lo = r.get("low")
        if hi is None or lo is None:
            continue
        try:
            cursor.execute(
                "UPDATE prices SET high = ?, low = ? "
                "WHERE ticker = ? AND date = ? "
                "  AND (high IS NULL OR low IS NULL)",
                (hi, lo, ticker, r["date"]),
            )
            updated += cursor.rowcount
        except Exception:  # noqa: BLE001
            continue
    conn.commit()
    return updated


RATE_LIMIT_SKIP_PATH = HERE.parent / ".data" / "_price_skipped_due_to_rate_limit.json"
CONSECUTIVE_429_ABORT_THRESHOLD = 5  # Sprint 10 Phase 5.B / Gate 1


def _is_rate_limit_error(result) -> bool:
    """True if a fetch_prices.fetch() result represents a 429-driven failure.

    fetch_prices packs the HTTPError type+message into `detail`, e.g.
    "HTTPError: HTTP Error 429: Too Many Requests". Sprint 10 Phase 5.B
    string-checks this rather than re-implementing retry tracking inside
    fetch_prices itself (smaller change surface).
    """
    if not result or result.status != "error":
        return False
    return "429" in (result.detail or "")


def _write_rate_limit_skip_log(skipped_tickers, threshold: int) -> None:
    """Persist the list of tickers skipped due to systemic 429s.

    Atomic write; safe to call once at end of step. The format is a
    single JSON object — small, end-of-step write only, so no
    read-modify-write concern (memory: feedback_no_rmw_json_in_hotpath).
    """
    if not skipped_tickers:
        return
    payload = {
        "aborted_at":          db.iso_now(),
        "threshold":           threshold,
        "skipped_tickers":     sorted(skipped_tickers),
        "n_skipped":           len(skipped_tickers),
    }
    RATE_LIMIT_SKIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RATE_LIMIT_SKIP_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, RATE_LIMIT_SKIP_PATH)


def run(*, date_from: str, date_to: str,
        only_ticker: str | None = None,
        dry_run: bool = False,
        rate_limit: float = 0.5,
        resume: bool = False,
        verbose: bool = False,
        extend: bool = False) -> dict:
    summary = {
        "tickers": 0, "ok": 0, "delisted": 0,
        "unsupported": 0, "errors": 0, "empty": 0,
        "rows_inserted": 0, "skipped_resume": 0,
        "already_current": 0, "new_tickers_full_history": 0,
        # Sprint 10 Phase 5 additions:
        "skipped_today":       0,      # 5.A: MAX(date)==today skip
        "rate_limit_429s":     0,      # 5.B: count of 429-driven failures
        "aborted_after_429":   False,  # 5.B: set True if threshold tripped
        "abort_skipped_count": 0,      # 5.B: # of tickers we stopped before
    }
    progress = _read_progress() if resume else {"completed_tickers": [], "last_run": None}
    completed = set(progress.get("completed_tickers") or [])

    # Sprint 10 Phase 5.B: track consecutive 429 failures across tickers.
    # Counter resets on ANY success path (ok / cache_hit / 5.A skip).
    consecutive_429s = 0
    rate_limit_skipped: list[str] = []

    conn = db.connect()
    try:
        tickers = [only_ticker] if only_ticker else distinct_stock_tickers(conn)
        today_iso = date.today().isoformat()
        for idx, t in enumerate(tickers):
            if not t:
                continue
            summary["tickers"] += 1
            if resume and t in completed:
                summary["skipped_resume"] += 1
                if verbose:
                    print(f"  [skip] {t} already completed")
                continue

            if extend:
                # B-162: --extend forces the full date_from window for every
                # ticker, bypassing the incremental smart-range and the Phase
                # 5.A "already current" skip. INSERT OR IGNORE prevents
                # duplicates for rows that already exist in the DB.  Use once
                # for the one-time historic backward fill; daily runs do not
                # need this flag.
                effective_from = date_from
                is_new = True
                if verbose:
                    print(f"  {t}: extend -- full window {effective_from} -> {date_to}")
            else:
                # Sprint 10 Phase 5.A (Gate 1): per-ticker MAX(date)==today
                # skip. If the prices table already has a row for this
                # ticker dated today (or later — shouldn't happen but
                # defensive), skip the fetch entirely. Cheaper than the
                # 20-hour cache check downstream because we don't even
                # touch the cache file. Resets the consecutive-429
                # counter (a skip is a successful no-op).
                last_date_row = conn.execute(
                    "SELECT MAX(date) AS d FROM prices WHERE ticker = ?", (t,)
                ).fetchone()
                last_date = last_date_row["d"] if last_date_row and last_date_row["d"] else None
                if last_date and last_date >= today_iso:
                    summary["skipped_today"] += 1
                    consecutive_429s = 0
                    if verbose:
                        print(f"  {t}: already current for today, skipped (5.A)")
                    completed.add(t)
                    progress["completed_tickers"] = sorted(completed)
                    progress["last_run"] = db.iso_now()
                    try:
                        _write_progress_atomic(progress)
                    except OSError:
                        pass
                    continue

                # Per-ticker smart range: NEW -> full window, EXISTING -> incremental
                effective_from = ticker_effective_from(conn, t, date_from)
                if effective_from > date_to:
                    summary["already_current"] += 1
                    consecutive_429s = 0
                    if verbose:
                        print(f"  {t}: already up to date (latest >= {date_to})")
                    completed.add(t)
                    progress["completed_tickers"] = sorted(completed)
                    progress["last_run"] = db.iso_now()
                    try:
                        _write_progress_atomic(progress)
                    except OSError:
                        pass
                    continue

                is_new = (effective_from == date_from)
                if is_new:
                    summary["new_tickers_full_history"] += 1
                    if verbose:
                        print(f"  {t}: NEW ticker -- full window {effective_from} -> {date_to}")
                elif verbose:
                    print(f"  {t}: incremental {effective_from} -> {date_to}")

            result = fetch_prices.fetch(t, effective_from, date_to, rate_limit=rate_limit)
            if verbose:
                print(f"  {t}: status={result.status} rows={len(result.rows)} "
                      f"cache_hit={result.cache_hit} net={result.network_calls}")
            if result.status == "ok":
                summary["ok"] += 1
                consecutive_429s = 0
                if not dry_run:
                    summary["rows_inserted"] += insert_rows(conn, t, result.rows)
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
                # other errors. 429s increment the consecutive
                # counter; non-429 errors leave it unchanged.
                summary["errors"] += 1
                if _is_rate_limit_error(result):
                    consecutive_429s += 1
                    summary["rate_limit_429s"] += 1
                    rate_limit_skipped.append(t)
                    if verbose:
                        print(f"  {t}: 429 #{consecutive_429s} of "
                              f"{CONSECUTIVE_429_ABORT_THRESHOLD}")

            completed.add(t)
            progress["completed_tickers"] = sorted(completed)
            progress["last_run"] = db.iso_now()
            try:
                _write_progress_atomic(progress)
            except OSError:
                pass

            # Sprint 10 Phase 5.B: abort the step cleanly when N
            # consecutive 429s indicate Yahoo is systemically
            # refusing. Tickers we haven't reached yet are added to
            # the skip log so the next refresh can prioritise them.
            # The log is written ONLY on abort — a no-abort run with
            # some scattered 429s leaves no skip log (those tickers
            # are already counted in summary["errors"] /
            # summary["rate_limit_429s"] for visibility).
            # Exit with status "partial_success" semantics — we
            # return summary, the pipeline continues into signals.
            if consecutive_429s >= CONSECUTIVE_429_ABORT_THRESHOLD:
                remaining = [u for u in tickers[idx + 1:] if u]
                rate_limit_skipped.extend(remaining)
                summary["aborted_after_429"] = True
                summary["abort_skipped_count"] = len(remaining)
                _write_rate_limit_skip_log(
                    rate_limit_skipped, CONSECUTIVE_429_ABORT_THRESHOLD
                )
                print(
                    f"[backfill_prices] aborting: "
                    f"{consecutive_429s} consecutive 429s "
                    f"(threshold {CONSECUTIVE_429_ABORT_THRESHOLD}). "
                    f"{len(remaining)} ticker(s) skipped. "
                    f"Logged to {RATE_LIMIT_SKIP_PATH.name}."
                )
                break
    finally:
        conn.close()

    if verbose:
        print(f"backfill_prices: {summary}")
    return summary


def run_hl_backfill(conn, *, date_from: str, date_to: str,
                    only_ticker: str | None = None,
                    rate_limit: float = 0.5,
                    dry_run: bool = False,
                    verbose: bool = False) -> dict:
    """B-157: one-time pass to backfill high/low for existing NULL rows.

    Finds all tickers with NULL high/low in the prices table, re-fetches
    their price history from Yahoo (cache is bypassed for old format entries
    via the H/L-present check in fetch_prices.fetch()), and UPDATEs
    existing rows.  Does not INSERT new rows -- the normal run() path
    handles those.

    Returns a summary dict with the number of tickers processed and rows
    updated.
    """
    if only_ticker:
        tickers = [only_ticker]
    else:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM prices WHERE high IS NULL"
        ).fetchall()
        tickers = [r["ticker"] for r in rows]

    print(f"[backfill_hl] {len(tickers)} tickers with NULL high/low to backfill")

    total_updated = 0
    errors = 0
    for t in tickers:
        result = fetch_prices.fetch(t, date_from, date_to, rate_limit=rate_limit)
        if result.status != "ok" or not result.rows:
            if verbose:
                print(f"  {t}: skip (status={result.status})")
            if result.status not in ("delisted", "unsupported_currency", "empty"):
                errors += 1
            continue
        if not dry_run:
            updated = update_hl_nulls(conn, t, result.rows)
            total_updated += updated
            if verbose and updated:
                print(f"  {t}: updated {updated} rows")

    summary = {
        "tickers_processed": len(tickers),
        "rows_updated": total_updated,
        "errors": errors,
    }
    print(f"[backfill_hl] done: {total_updated} rows updated across "
          f"{len(tickers)} tickers ({errors} errors)")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 3 OHLCV backfill.")
    ap.add_argument("--from", dest="date_from", default=_default_from(),
                    help="ISO date inclusive (default: today - 1827d, ~5 years).")
    ap.add_argument("--to", dest="date_to", default=_today(),
                    help="ISO date inclusive (default: today).")
    ap.add_argument("--ticker", default=None, help="Run for a single ticker only.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + report; do not write to DB.")
    ap.add_argument("--rate-limit", type=float, default=0.5,
                    help="Seconds to sleep after each network call.")
    ap.add_argument("--resume", action="store_true",
                    help="Skip tickers already in _price_progress.json.")
    ap.add_argument("--extend", action="store_true",
                    help="B-162: fetch the full --from..--to window for every "
                         "ticker, bypassing the incremental smart range. Use "
                         "once for the one-time historic backward fill.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--backfill-hl", action="store_true",
                    help="B-157: UPDATE high/low for existing NULL rows instead "
                         "of running the normal INSERT pass. Run once after the "
                         "B-157 fix to populate H/L on all existing price rows.")
    args = ap.parse_args(argv)

    # B-024: db_health pattern — pre-run integrity check + backup before
    # any INSERT OR IGNORE writes. Skipped in --dry-run (no DB writes).
    # Canonical reference: classify_issuers.py:run().
    # B-179: SQLite/FUSE corruption defence only — skip on Postgres.
    if not args.dry_run and db.backend() == "sqlite":
        if not db_health.check(db.DB_PATH):
            print("[backfill_prices] FATAL: pre-run integrity_check failed. "
                  "Run start.bat to restore from .bak before retrying.")
            return 2
        if not db_health.backup():
            print("[backfill_prices] FATAL: failed to take pre-backfill .bak. "
                  "Refusing to proceed.")
            return 3

    if args.backfill_hl:
        conn = db.connect()
        try:
            summary = run_hl_backfill(
                conn,
                date_from=args.date_from, date_to=args.date_to,
                only_ticker=args.ticker, rate_limit=args.rate_limit,
                dry_run=args.dry_run, verbose=args.verbose,
            )
        finally:
            conn.close()
    else:
        summary = run(date_from=args.date_from, date_to=args.date_to,
                      only_ticker=args.ticker, dry_run=args.dry_run,
                      rate_limit=args.rate_limit, resume=args.resume,
                      verbose=args.verbose, extend=args.extend)

    print(json.dumps(summary, indent=2, sort_keys=True))

    # B-024: db_health post-run pattern. Skip seal if post-run integrity
    # fails so the pre-run .bak is preserved as the rollback target.
    # B-179: local-SQLite-only; skip on Postgres.
    if not args.dry_run and db.backend() == "sqlite":
        try:
            if not db_health.check(db.DB_PATH):
                print("[backfill_prices] WARNING: post-run integrity_check "
                      "failed. The pre-run .bak is valid — restore via "
                      "start.bat. Skipping seal to preserve good backup.")
                return 4
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-backfill seal failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
