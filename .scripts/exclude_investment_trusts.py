"""Sprint 2 / B-011 — preview and execute the IT / CEF / VCT / REIT purge.

This is the deletion runner. It depends on `classify_issuers.py` having
already populated `tickers_meta.is_excluded_issuer`. The script never
classifies; it just reads the existing flags and acts on them.

Two modes:

    --preview   (default — safe)
        Writes .data/_excluded_it_cef.preview.csv with one row per
        ticker to be deleted plus a SUMMARY row at the top. The
        SUMMARY row has two blank columns: `signed_off_by` and
        `signed_off_at`. Rupert reviews the preview and fills those
        in to authorise the delete.

    --confirm   (destructive)
        Verifies the SUMMARY row has both `signed_off_by` and
        `signed_off_at` populated. Writes the audit log
        .data/_excluded_it_cef.csv (one row per deleted fingerprint
        with full context). Then DELETEs from signals,
        paper_trades, transactions in FK-safe order, inside a single
        SQL transaction.

CLI:
    python .scripts/exclude_investment_trusts.py --preview
    python .scripts/exclude_investment_trusts.py --confirm
    python .scripts/exclude_investment_trusts.py --confirm --dry-run
        # --dry-run prints what would be deleted without touching DB

FUSE rule (CLAUDE.md): this script writes to .data/ and modifies
directors.db. Always run from Windows PowerShell, never from a Linux
sandbox. The pre-flight backup at
.data/directors.db.pre-it-purge.bak must exist before --confirm
(safety net for rollback).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

ROOT = HERE.parent
DATA_DIR = ROOT / ".data"
PREVIEW_CSV = DATA_DIR / "_excluded_it_cef.preview.csv"
AUDIT_CSV = DATA_DIR / "_excluded_it_cef.csv"
BACKUP_PATH = DATA_DIR / "directors.db.pre-it-purge.bak"

PREVIEW_FIELDS = ["ticker", "company", "source", "tx_count",
                  "signal_count", "signed_off_by", "signed_off_at"]
AUDIT_FIELDS = ["fingerprint", "ticker", "company", "director",
                "date", "type", "shares", "price", "source",
                "deleted_at"]

SUMMARY_TICKER = "SUMMARY"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Preview

def _load_excluded_rows(conn) -> list[dict]:
    """Load per-ticker rows for everything currently flagged excluded."""
    rows = conn.execute(
        "SELECT m.ticker, m.excluded_source AS source, "
        "       COALESCE(t.company, '') AS company, "
        "       COALESCE(t.tx_count, 0) AS tx_count, "
        "       COALESCE(s.signal_count, 0) AS signal_count "
        "FROM tickers_meta m "
        "LEFT JOIN ("
        "  SELECT ticker, COUNT(*) AS tx_count, "
        "         MAX(company) AS company "
        "  FROM transactions GROUP BY ticker"
        ") t ON t.ticker = m.ticker "
        "LEFT JOIN ("
        "  SELECT tr.ticker, COUNT(*) AS signal_count "
        "  FROM signals sg "
        "  JOIN transactions tr ON tr.fingerprint = sg.fingerprint "
        "  GROUP BY tr.ticker"
        ") s ON s.ticker = m.ticker "
        "WHERE m.is_excluded_issuer = 1 "
        "ORDER BY t.tx_count DESC, m.ticker ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def _write_preview(rows: list[dict]) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    total_tx = sum(r["tx_count"] for r in rows)
    total_signals = sum(r["signal_count"] for r in rows)
    summary_company = (
        f"{len(rows)} tickers, {total_tx} transactions, "
        f"{total_signals} signals — FILL IN signed_off_by AND "
        f"signed_off_at TO AUTHORISE DELETE"
    )

    tmp = PREVIEW_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PREVIEW_FIELDS)
        writer.writeheader()
        writer.writerow({
            "ticker": SUMMARY_TICKER,
            "company": summary_company,
            "source": "",
            "tx_count": total_tx,
            "signal_count": total_signals,
            "signed_off_by": "",
            "signed_off_at": "",
        })
        for r in rows:
            writer.writerow({
                "ticker": r["ticker"],
                "company": r["company"],
                "source": r["source"] or "",
                "tx_count": r["tx_count"],
                "signal_count": r["signal_count"],
                "signed_off_by": "",
                "signed_off_at": "",
            })
    tmp.replace(PREVIEW_CSV)
    return {
        "ticker_count": len(rows),
        "tx_count": total_tx,
        "signal_count": total_signals,
        "path": PREVIEW_CSV,
    }


def _do_preview(args) -> int:
    conn = db.connect()
    try:
        rows = _load_excluded_rows(conn)
    finally:
        conn.close()

    if not rows:
        print(
            "No tickers are flagged for exclusion. Run "
            "`python .scripts/classify_issuers.py` first."
        )
        return 1

    summary = _write_preview(rows)
    print("Preview written.")
    print(f"  path:         {summary['path']}")
    print(f"  tickers:      {summary['ticker_count']}")
    print(f"  transactions: {summary['tx_count']}")
    print(f"  signals:      {summary['signal_count']}")
    print()
    print("Next steps:")
    print("  1. Open the preview CSV.")
    print("  2. Eyeball the ticker list. Look for false positives "
          "(operating companies caught by the name regex) and false "
          "negatives (well-known ITs that aren't on the list).")
    print("  3. In the SUMMARY row at the top, fill in `signed_off_by` "
          "(your name) and `signed_off_at` (today's ISO date, e.g. "
          f"`{_iso_now()[:10]}`).")
    print("  4. Save the file.")
    print("  5. Re-run with --confirm to execute the deletion.")
    return 0


# ---------------------------------------------------------------------------
# Confirm (destructive)

def _read_signoff_from_preview() -> tuple[str, str]:
    """Return (signed_off_by, signed_off_at) from the SUMMARY row."""
    if not PREVIEW_CSV.exists():
        raise SystemExit(
            f"Preview CSV not found at {PREVIEW_CSV}. Run "
            f"`python .scripts/exclude_investment_trusts.py --preview` first."
        )
    with PREVIEW_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("ticker") == SUMMARY_TICKER:
                return (
                    (row.get("signed_off_by") or "").strip(),
                    (row.get("signed_off_at") or "").strip(),
                )
    raise SystemExit(
        f"Preview CSV {PREVIEW_CSV} has no SUMMARY row. Re-generate "
        f"with --preview."
    )


def _load_deletion_fingerprints(conn) -> list[dict]:
    """Return per-fingerprint rows for everything that will be deleted."""
    rows = conn.execute(
        "SELECT t.fingerprint, t.ticker, t.company, t.director, "
        "       t.date, t.type, t.shares, t.price, "
        "       m.excluded_source AS source "
        "FROM transactions t "
        "JOIN tickers_meta m ON m.ticker = t.ticker "
        "WHERE m.is_excluded_issuer = 1 "
        "ORDER BY t.ticker, t.date"
    ).fetchall()
    return [dict(r) for r in rows]


def _append_audit(rows: list[dict]) -> None:
    """Append deleted-fingerprint detail to .data/_excluded_it_cef.csv.

    Append mode so multiple purges (e.g. a re-classify and re-delete in
    a later session) accumulate a full history. The deleted_at column
    makes runs distinguishable.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not AUDIT_CSV.exists()
    now = _iso_now()
    with AUDIT_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        if new_file:
            writer.writeheader()
        for r in rows:
            writer.writerow({
                "fingerprint": r["fingerprint"],
                "ticker": r["ticker"],
                "company": r["company"],
                "director": r["director"],
                "date": r["date"],
                "type": r["type"],
                "shares": r["shares"],
                "price": r["price"],
                "source": r["source"] or "",
                "deleted_at": now,
            })


def _delete_in_transaction(conn, fingerprints: list[str]) -> dict:
    """FK-safe deletion. Returns counts per table."""
    # SQLite has a hard limit of 999 host parameters per statement.
    # Chunk the fingerprint list so we never blow past that.
    CHUNK = 500
    counts = {"signals": 0, "paper_trades": 0, "transactions": 0}
    conn.execute("BEGIN")
    try:
        for i in range(0, len(fingerprints), CHUNK):
            chunk = fingerprints[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"DELETE FROM signals WHERE fingerprint IN ({placeholders})",
                chunk,
            )
            counts["signals"] += cur.rowcount or 0
            cur = conn.execute(
                f"DELETE FROM paper_trades WHERE fingerprint IN ({placeholders})",
                chunk,
            )
            counts["paper_trades"] += cur.rowcount or 0
            cur = conn.execute(
                f"DELETE FROM transactions WHERE fingerprint IN ({placeholders})",
                chunk,
            )
            counts["transactions"] += cur.rowcount or 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def _do_confirm(args) -> int:
    signed_off_by, signed_off_at = _read_signoff_from_preview()
    if not signed_off_by or not signed_off_at:
        raise SystemExit(
            f"Refusing to delete: SUMMARY row in {PREVIEW_CSV} is not "
            f"signed off. Open the preview CSV, fill in `signed_off_by` "
            f"and `signed_off_at` in the SUMMARY row, save, and re-run."
        )

    # Pre-flight backup guard (advisory). The backup is created by Rupert
    # from PowerShell before this script runs; we just check it exists.
    if not BACKUP_PATH.exists() and not args.skip_backup_check:
        raise SystemExit(
            f"Refusing to delete: pre-flight backup not found at "
            f"{BACKUP_PATH}.\n"
            f"Take the backup from PowerShell first (see B-011 scope), "
            f"or pass --skip-backup-check to override (NOT recommended)."
        )

    conn = db.connect()
    try:
        rows = _load_deletion_fingerprints(conn)
        if not rows:
            print("Nothing to delete — no transactions reference an "
                  "excluded issuer. Did `classify_issuers.py` run?")
            return 1

        # By-ticker summary first so Rupert sees what's about to happen.
        by_ticker: dict[str, int] = {}
        for r in rows:
            by_ticker[r["ticker"]] = by_ticker.get(r["ticker"], 0) + 1

        print(f"Authorised by: {signed_off_by}  on  {signed_off_at}")
        print(f"Will delete: {len(rows)} transactions across "
              f"{len(by_ticker)} tickers.")
        if args.verbose:
            for tk, n in sorted(by_ticker.items(), key=lambda x: -x[1]):
                print(f"  {tk:<8} {n}")

        if args.dry_run:
            print("\n--dry-run: NO changes made.")
            return 0

        # Audit trail before delete — if the delete fails, we still have
        # a record of what was on the chopping block.
        _append_audit(rows)
        print(f"Audit log appended: {AUDIT_CSV}")

        fingerprints = [r["fingerprint"] for r in rows]
        counts = _delete_in_transaction(conn, fingerprints)
        print(
            f"\nDELETE complete.\n"
            f"  signals deleted:        {counts['signals']}\n"
            f"  paper_trades deleted:   {counts['paper_trades']}\n"
            f"  transactions deleted:   {counts['transactions']}\n"
        )

        # Sanity check.
        remaining = conn.execute(
            "SELECT COUNT(*) FROM transactions t "
            "JOIN tickers_meta m ON m.ticker = t.ticker "
            "WHERE m.is_excluded_issuer = 1"
        ).fetchone()[0]
        if remaining:
            print(f"WARN: {remaining} excluded-issuer transactions STILL "
                  f"present after delete. Investigate.")
            return 2

        print(
            "Next steps (run from PowerShell):\n"
            "    python .scripts/eval_signals.py\n"
            "    python .scripts/backtest.py\n"
            "    python .scripts/build_dashboard.py\n"
            "    python .scripts/audit_dates.py\n"
        )
        # B-024: refresh the auto-backup so the bak tracks this Sprint
        # write. Best-effort -- the script's main work has succeeded by
        # the time we get here, so a backup failure must not flip the
        # exit code. seal() handles its own logging.
        try:
            import db_health
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-script seal failed (non-fatal): {e}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Preview and execute the IT/CEF/VCT/REIT purge."
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true",
                      help="Write the preview CSV (default).")
    mode.add_argument("--confirm", action="store_true",
                      help="DELETE rows for excluded issuers. Requires "
                           "the SUMMARY row in the preview CSV to be "
                           "signed off.")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --confirm: print what would be deleted "
                         "but make no DB changes.")
    ap.add_argument("--skip-backup-check", action="store_true",
                    help="Skip the .data/directors.db.pre-it-purge.bak "
                         "existence check. NOT recommended.")
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.confirm:
        return _do_confirm(args)
    # default: preview
    return _do_preview(args)


if __name__ == "__main__":
    sys.exit(main())
