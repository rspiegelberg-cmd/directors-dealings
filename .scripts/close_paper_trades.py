"""B-100 Phase B: close matured paper trades.

Finds open paper_trades where entry_date was >= N trading days ago
(default 21), looks up the exit close in `prices`, and marks the trade
CLOSED.  Also upgrades PLANNED trades to OPEN when an entry price is
now available.

Zone B -- Rupert runs this; it writes to directors.db.

CLI::

    python .scripts\\close_paper_trades.py [--horizon 21|90]
                                            [--dry-run] [--verbose]

    --horizon  N   Trading-day exit window (default 21). Also supports 90.
    --dry-run      Print what would be closed; make no changes.
    --verbose      Detailed per-trade logging.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402


def _nth_trading_day(conn, ticker: str, start_date: str, n: int) -> str | None:
    """Return the date N trading days after start_date (inclusive of start+1).

    Looks up the `prices` table for the ticker, finds the entry at
    position start+n in chronological order.  Returns None if not enough
    price history exists.
    """
    rows = conn.execute(
        "SELECT date FROM prices WHERE ticker = ? AND date > ? "
        "ORDER BY date ASC LIMIT ?",
        (ticker, start_date, n),
    ).fetchall()
    if len(rows) < n:
        return None
    return rows[n - 1]["date"]


def _close_price_on(conn, ticker: str, target_date: str) -> float | None:
    """Return the close price on or after target_date for ticker."""
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? AND date >= ? "
        "ORDER BY date ASC LIMIT 1",
        (ticker, target_date),
    ).fetchone()
    return float(row["close"]) if row else None


def run(horizon: int = 21, dry_run: bool = False, verbose: bool = False) -> dict:
    """Main logic. Returns a summary dict."""
    conn = db.connect()
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Step 1: upgrade PLANNED -> OPEN when entry price now available ---
    planned = conn.execute(
        "SELECT pt.*, t.ticker "
        "FROM paper_trades pt "
        "LEFT JOIN transactions t ON t.fingerprint = pt.fingerprint "
        "WHERE pt.status = 'planned'"
    ).fetchall()

    planned_upgraded = 0
    for row in planned:
        ticker = row["ticker"] or ""
        fired_at = (row["entry_date"] or "")  # planned rows have no entry_date
        # For planned rows, use opened_at as the signal-fire proxy.
        fire_date = (row["opened_at"] or "")[:10]
        if not fire_date or not ticker:
            continue
        entry_row = conn.execute(
            "SELECT date, close FROM prices WHERE ticker = ? AND date >= ? "
            "ORDER BY date ASC LIMIT 1",
            (ticker, fire_date),
        ).fetchone()
        if not entry_row:
            continue
        entry_close = float(entry_row["close"])
        entry_date = entry_row["date"]
        notional = float(row["notional_gbp"] or 0)
        shares = notional / entry_close if entry_close > 0 else None
        if verbose:
            print(f"  planned->open: {row['trade_id']} @ {entry_close} ({ticker})")
        if not dry_run:
            conn.execute(
                "UPDATE paper_trades SET status='open', entry_date=?, entry_close=?, "
                "shares=?, updated_at=? WHERE trade_id=?",
                (entry_date, entry_close, shares, now_iso, row["trade_id"]),
            )
            planned_upgraded += 1

    if not dry_run and planned_upgraded:
        conn.commit()

    # --- Step 2: close OPEN trades that have reached horizon ---
    open_rows = conn.execute(
        "SELECT pt.*, t.ticker "
        "FROM paper_trades pt "
        "LEFT JOIN transactions t ON t.fingerprint = pt.fingerprint "
        "WHERE pt.status = 'open' AND pt.entry_date IS NOT NULL"
    ).fetchall()

    closed = 0
    skipped_no_price = 0
    skipped_too_soon = 0

    for row in open_rows:
        ticker = row["ticker"] or ""
        entry_date = row["entry_date"] or ""
        if not ticker or not entry_date:
            continue

        exit_date = _nth_trading_day(conn, ticker, entry_date, horizon)
        if exit_date is None:
            # Not enough price history yet.
            skipped_too_soon += 1
            if verbose:
                print(f"  too-soon: {row['trade_id']} ({ticker}, entry {entry_date}, "
                      f"horizon {horizon}d, not enough prices yet)")
            continue

        # Check today's date: don't close a trade before the exit date arrives.
        today_iso = datetime.utcnow().date().isoformat()
        if exit_date > today_iso:
            skipped_too_soon += 1
            if verbose:
                print(f"  pending: {row['trade_id']} ({ticker}, exit {exit_date})")
            continue

        exit_close = _close_price_on(conn, ticker, exit_date)
        if exit_close is None:
            skipped_no_price += 1
            if verbose:
                print(f"  no-price: {row['trade_id']} ({ticker}, exit {exit_date})")
            continue

        if verbose:
            entry_close = float(row["entry_close"] or 0)
            ret_pct = (exit_close / entry_close - 1) * 100 if entry_close > 0 else 0
            print(f"  close: {row['trade_id']} ({ticker}) "
                  f"entry={entry_close:.2f} exit={exit_close:.2f} ret={ret_pct:+.1f}%")

        if not dry_run:
            conn.execute(
                "UPDATE paper_trades SET status='closed', exit_date=?, exit_close=?, "
                "updated_at=?, notes=? WHERE trade_id=?",
                (exit_date, exit_close, now_iso,
                 f"horizon={horizon}d", row["trade_id"]),
            )
            closed += 1

    if not dry_run and closed:
        conn.commit()
    conn.close()

    summary = {
        "planned_upgraded": planned_upgraded,
        "closed":           closed,
        "skipped_no_price": skipped_no_price,
        "skipped_too_soon": skipped_too_soon,
        "dry_run":          dry_run,
        "horizon":          horizon,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Close matured paper trades.")
    parser.add_argument("--horizon", type=int, default=21,
                        help="Trading-day exit window (default 21; also 90)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be closed without writing")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    result = run(horizon=args.horizon, dry_run=args.dry_run, verbose=args.verbose)
    label = "[DRY-RUN] " if result["dry_run"] else ""
    print(f"{label}B-100 close_paper_trades (horizon={result['horizon']}d): "
          f"planned->open={result['planned_upgraded']}, "
          f"closed={result['closed']}, "
          f"skipped(no-price)={result['skipped_no_price']}, "
          f"skipped(too-soon)={result['skipped_too_soon']}")


if __name__ == "__main__":
    main()
