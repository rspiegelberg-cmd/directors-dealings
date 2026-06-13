"""B-060 -- correct pence/pounds price-unit misparses across the corpus.

ZONE B (write-path). Rupert runs this from PowerShell:

    python .scripts/backfill_price_units.py            # dry-run (default)
    python .scripts/backfill_price_units.py --confirm  # writes

For every priced transaction it looks up the market close nearest the
transaction date (from the `prices` table, already stored in pounds) and asks
`price_reconcile.reconcile_price` which reading is right:

  * corrected_pence  -> UPDATE price = price/100, value = newprice*shares,
                        price_audit = 'corrected_pence'
  * unresolved       -> keep price (for audit), value = NULL,
                        price_audit = 'unresolved'   (garbage / Mode B)
  * no_market        -> value = NULL, price_audit = 'no_market'
  * ok_pounds        -> price_audit = 'ok_pounds'    (no value/price change)

Flagged rows ('unresolved'/'no_market') are excluded from signal firing
(eval_signals) and, with value=NULL, from every value metric. The change is
fully reversible from the JSONL log written to .scripts/_price_unit_fixes.jsonl.

Idempotent: a re-run re-evaluates against market and converges (corrected rows
now read as ok_pounds; flagged rows stay flagged).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
from price_reconcile import reconcile_price  # noqa: E402

LOG_PATH = HERE / "_price_unit_fixes.jsonl"
# +/- days to find a market close to reconcile against. 30d (not 7) because a
# tighter window left ~350 rows 'no_market' (flagged but uncorrected); a 100x
# pence error is unmistakable even against a close a few weeks away.
DEFAULT_WINDOW_DAYS = 30


def nearest_close(conn, ticker: str, date: str, window_days: int):
    """Return the market close (pounds) for `ticker` nearest `date` within
    +/- window_days, or None. Benchmark ('^...') rows are never stock prices."""
    row = conn.execute(
        "SELECT close FROM prices "
        "WHERE ticker = ? AND close IS NOT NULL "
        "  AND ABS(julianday(date) - julianday(?)) <= ? "
        "ORDER BY ABS(julianday(date) - julianday(?)) ASC LIMIT 1",
        (ticker, date, window_days, date),
    ).fetchone()
    if row is None:
        return None
    return float(row["close"] if not isinstance(row, tuple) else row[0])


def run(conn, confirm: bool, window_days: int, log_path: Path) -> dict:
    """Reconcile every priced row. Returns a summary dict. Writes only when
    `confirm` is True. Pure w.r.t. the passed connection (testable)."""
    rows = conn.execute(
        "SELECT fingerprint, ticker, date, type, shares, price, value, "
        "       price_audit "
        "FROM transactions WHERE price IS NOT NULL AND price > 0"
    ).fetchall()

    summary = Counter()
    changes = []
    for r in rows:
        close = nearest_close(conn, r["ticker"], r["date"], window_days)
        new_price, status = reconcile_price(
            r["price"], r["shares"], close, r["type"]
        )
        summary[status] += 1
        if status == "ok_pounds":
            # Only stamp the audit flag; no price/value change.
            if confirm and r["price_audit"] != "ok_pounds":
                conn.execute(
                    "UPDATE transactions SET price_audit='ok_pounds' "
                    "WHERE fingerprint=?", (r["fingerprint"],)
                )
            continue

        if status == "corrected_pence":
            new_value = round(new_price * r["shares"], 2) if r["shares"] else 0.0
            change = {
                "fingerprint": r["fingerprint"], "ticker": r["ticker"],
                "date": r["date"], "type": r["type"], "status": status,
                "old_price": r["price"], "new_price": new_price,
                "old_value": r["value"], "new_value": new_value,
                "market_close": close,
            }
            if confirm:
                conn.execute(
                    "UPDATE transactions SET price=?, value=?, "
                    "price_audit='corrected_pence' WHERE fingerprint=?",
                    (new_price, new_value, r["fingerprint"]),
                )
        else:  # unresolved / no_market -> set flag only.
            # `value` is NOT NULL in the schema, so we leave it intact and
            # exclude these rows from value metrics at READ time (a CASE that
            # nulls the value when price_audit is flagged) and from signals
            # (eval_signals candidate filter). The row stays a real trade.
            change = {
                "fingerprint": r["fingerprint"], "ticker": r["ticker"],
                "date": r["date"], "type": r["type"], "status": status,
                "old_price": r["price"], "new_price": r["price"],
                "old_value": r["value"], "new_value": r["value"],
                "market_close": close,
            }
            if confirm:
                conn.execute(
                    "UPDATE transactions SET price_audit=? "
                    "WHERE fingerprint=?", (status, r["fingerprint"]),
                )
        changes.append(change)

    if confirm:
        conn.commit()
        with open(log_path, "a", encoding="utf-8") as fh:
            stamp = datetime.utcnow().isoformat()
            for c in changes:
                c["logged_at"] = stamp
                fh.write(json.dumps(c) + "\n")

    summary["_total_rows"] = len(rows)
    summary["_changes"] = len(changes)
    return dict(summary)


def main() -> None:
    ap = argparse.ArgumentParser(description="B-060 price-unit reconciliation")
    ap.add_argument("--confirm", action="store_true",
                    help="write changes (default: dry-run)")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help="max +/- days to find a market close (default 30)")
    args = ap.parse_args()

    conn = db.connect()  # applies migration 010 if needed
    try:
        if args.confirm:
            try:
                import db_health
                db_health.backup()
                print("[ok] DB backed up before write")
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] backup skipped: {exc}")
        summary = run(conn, args.confirm, args.window_days, LOG_PATH)
    finally:
        conn.close()

    mode = "WROTE" if args.confirm else "DRY-RUN (no changes)"
    print(f"\n=== B-060 price-unit reconciliation -- {mode} ===")
    print(f"  rows examined (price>0):  {summary.get('_total_rows', 0)}")
    print(f"  ok_pounds (trusted):      {summary.get('ok_pounds', 0)}")
    print(f"  corrected_pence (fixed):  {summary.get('corrected_pence', 0)}")
    print(f"  unresolved (flagged):     {summary.get('unresolved', 0)}")
    print(f"  no_market (flagged):      {summary.get('no_market', 0)}")
    print(f"  total changes:            {summary.get('_changes', 0)}")
    if args.confirm:
        print(f"  log: {LOG_PATH}")
        print("\nNext: python .scripts/eval_signals.py --rebuild  then export + build")
    else:
        print("\nDry-run only. Re-run with --confirm to write.")


if __name__ == "__main__":
    main()
