"""Read-only ticker inspector — diagnose dashboard data problems.

Dumps everything about one ticker so we can see why its performance /
company page / MTM looks wrong: transactions, meta (exclusion flag),
price coverage, backtest CAR rows (entry/t30/t90 closes — reveals
split/unit errors), signals fired, and whether the company page exists.

Read-only. No writes. Safe to run anytime.

    python .scripts/inspect_ticker.py TIN
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db

CSV_PATH = HERE.parent / ".data" / "_backtest_results.csv"
COMPANIES_DIR = HERE.parent / "outputs" / "companies"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python .scripts/inspect_ticker.py <TICKER>")
        return
    ticker = sys.argv[1].strip().upper()
    conn = db.connect()

    print(f"================  {ticker}  ================\n")

    print("--- tickers_meta ---")
    m = conn.execute(
        "SELECT ticker, sector, benchmark_symbol, is_aim, is_excluded_issuer "
        "FROM tickers_meta WHERE ticker = ?", (ticker,)
    ).fetchone()
    print("  ", dict(m) if m else "NO tickers_meta row")

    print("\n--- transactions ---")
    txs = conn.execute(
        "SELECT date, announced_at, director, role_normalized, type, shares, "
        "price, value, buy_strictness, url FROM transactions "
        "WHERE ticker = ? ORDER BY date", (ticker,)
    ).fetchall()
    for t in txs:
        # flag a non-ISO announced_at (the MTM-breaker)
        aa = t["announced_at"] or ""
        iso = len(aa) >= 10 and aa[4] == "-" and aa[7] == "-"
        flag = "" if iso or not aa else "  <-- NON-ISO announced_at (breaks MTM)"
        print(f"  {t['date']} {t['type']:8} sh={t['shares']} "
              f"price={t['price']} value={t['value']} "
              f"strict={t['buy_strictness']} aa='{aa}'{flag}")
        print(f"      {t['director']} ({t['role_normalized']})  {t['url']}")
    if not txs:
        print("  NONE — ticker has no transactions")

    print("\n--- price coverage ---")
    p = conn.execute(
        "SELECT COUNT(*) n, MIN(date) lo, MAX(date) hi FROM prices WHERE ticker = ?",
        (ticker,)
    ).fetchone()
    print(f"  rows={p['n']}  range={p['lo']} .. {p['hi']}")
    # show a few closes to eyeball for a split/unit jump
    sample = conn.execute(
        "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date", (ticker,)
    ).fetchall()
    if sample:
        closes = [s["close"] for s in sample]
        print(f"  close min={min(closes)} max={max(closes)} "
              f"(max/min ratio={max(closes)/min(closes):.1f}x — a big ratio = "
              f"likely unadjusted split)")

    print("\n--- backtest CAR rows (entry/t30/t90 closes) ---")
    if CSV_PATH.exists():
        for r in csv.DictReader(open(CSV_PATH, encoding="utf-8")):
            if (r.get("ticker") or "").upper() != ticker:
                continue
            print(f"  {r.get('signal_id')} fire={r.get('entry_date')} "
                  f"entry_close={r.get('entry_close')} "
                  f"t30_close={r.get('t30_close')} t90_close={r.get('t90_close')} "
                  f"| car_t30={r.get('car_t30')} car_t90={r.get('car_t90')}")
    else:
        print("  (_backtest_results.csv not found)")

    print("\n--- signals fired ---")
    for s in conn.execute(
        "SELECT s.signal_id, s.fired_at FROM signals s JOIN transactions t "
        "ON s.fingerprint = t.fingerprint WHERE t.ticker = ?", (ticker,)
    ).fetchall():
        print("  ", s["signal_id"], s["fired_at"])

    print("\n--- company page ---")
    # mirror build_dashboard._sanitize_ticker (uppercase, keep dots, unsafe->_)
    safe = "".join(ch if (ch.isalnum() or ch in ".-") else "_" for ch in ticker)
    page = COMPANIES_DIR / f"{safe}.html"
    print(f"  expected: {page}")
    print(f"  exists:   {page.exists()}")

    conn.close()


if __name__ == "__main__":
    main()
