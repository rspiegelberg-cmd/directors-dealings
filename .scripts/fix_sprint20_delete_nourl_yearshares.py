"""Sprint 20 — surgical delete of no-URL year-as-shares rows (B-060 family).

Deletes ONLY the 15 fingerprints listed below: rows the data-integrity audit
(2026-06-03) flagged as year-as-shares corruption that have NO source filing,
so a reparse cannot reach them, and whose `shares` value equals the
transaction's own calendar year (the unambiguous year-bleed signature).

Rows where `shares` is in 1990-2099 but does NOT equal the transaction year
(genuine ~2,000-share holdings — CCEP, BARC, SVT, ...) are deliberately NOT
in this list. See docs/audits/sprint20_nourl_yearshares_review.md.

Safety: before deleting, each row is re-read and re-verified — the ticker must
still match AND shares must still equal int(date[:4]). Any row that no longer
matches (e.g. already corrected/reparsed) is SKIPPED, never force-deleted.
Dependent signals / paper_trades rows are removed first (FK order), then the
transaction. eval_signals regenerates signals on the next rebuild.

FUSE rule (CLAUDE.md): this script writes .data/directors.db. Run from Windows
PowerShell, never from the Linux sandbox.

CLI:
    python .scripts/fix_sprint20_delete_nourl_yearshares.py            # preview
    python .scripts/fix_sprint20_delete_nourl_yearshares.py --confirm  # apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

# (fingerprint, expected_ticker, expected_shares) — expected_shares == tx year.
DELETE_LIST = [
    ("553d2d022d461461", "SCLP", 2025),
    ("fa80558bcec8605b", "SFOR", 2025),
    ("07178a5ab3323df7", "SFOR", 2025),
    ("5af985261b6c1bd6", "RHR",  2025),
    ("53265bd1b41914fd", "TKO",  2026),
    ("9d3b44a030758477", "TKO",  2026),
    ("e96986c7fc5035fc", "TKO",  2026),
    ("07dac526c1c5f4db", "KEN",  2026),
    ("8f8c3564c7bf3644", "TST",  2026),
    ("e2cad68bfc299d37", "TST",  2026),
    ("55215415d9ee02b7", "ESNT", 2026),
    ("2ba6448548a53d52", "ESNT", 2026),
    ("74d120aec530bcf1", "LSL",  2025),
    ("e01a20d9d93932e7", "LSL",  2025),
    ("01dc494a0a6dea6d", "LSL",  2025),
]


def _table_has_fingerprint(conn, table: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return "fingerprint" in cols


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm", action="store_true",
                    help="Apply the deletes. Default: preview only (no writes).")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    try:
        verified, skipped = [], []
        for fp, exp_ticker, exp_shares in DELETE_LIST:
            row = conn.execute(
                "SELECT date, ticker, shares, type, value "
                "FROM transactions WHERE fingerprint = ?", (fp,)
            ).fetchone()
            if row is None:
                skipped.append((fp, exp_ticker, "already absent"))
                continue
            date, ticker, shares, tx_type, value = (
                row["date"], row["ticker"], row["shares"],
                row["type"], row["value"],
            )
            # Re-verify the year-bleed signature before trusting the delete.
            try:
                tx_year = int(str(date)[:4])
            except (TypeError, ValueError):
                tx_year = None
            if ticker != exp_ticker or shares != exp_shares or shares != tx_year:
                skipped.append(
                    (fp, ticker, f"signature changed (shares={shares}, "
                                 f"date={date}) — NOT deleting"))
                continue
            verified.append((fp, ticker, date, shares, tx_type, value))

        print(f"Verified for delete: {len(verified)}    Skipped: {len(skipped)}")
        print("\n-- Rows to delete (year-as-shares, no source, shares==tx year) --")
        for fp, ticker, date, shares, tx_type, value in verified:
            print(f"  {fp}  {ticker:5} {date}  shares={shares} "
                  f"type={tx_type} value={value}")
        if skipped:
            print("\n-- Skipped --")
            for fp, ticker, why in skipped:
                print(f"  {fp}  {ticker:5} {why}")

        if not args.confirm:
            print("\nPREVIEW only — no DB writes. Re-run with --confirm to apply.")
            return

        if not verified:
            print("\nNothing verified to delete. No writes made.")
            return

        # Best-effort backup before any write (B-024 safety net).
        try:
            import db_health
            db_health.backup()
            print("\n[backup] directors.db.bak written.")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[backup] WARNING: auto-backup failed ({exc}). "
                  "Ensure you have a manual .bak before relying on this.")

        sig_has_fp = _table_has_fingerprint(conn, "signals")
        pt_has_fp = _table_has_fingerprint(conn, "paper_trades")
        n_sig = n_pt = n_tx = 0
        for fp, *_ in verified:
            if sig_has_fp:
                n_sig += conn.execute(
                    "DELETE FROM signals WHERE fingerprint = ?", (fp,)).rowcount
            if pt_has_fp:
                n_pt += conn.execute(
                    "DELETE FROM paper_trades WHERE fingerprint = ?", (fp,)).rowcount
            n_tx += conn.execute(
                "DELETE FROM transactions WHERE fingerprint = ?", (fp,)).rowcount
        conn.commit()

        # Re-seal so .bak tracks the post-delete state (project convention:
        # backup() pre-flight + seal() on success).
        try:
            import db_health
            db_health.seal()
        except Exception as exc:  # noqa: BLE001
            print(f"[seal] WARNING: post-run seal failed ({exc}).")

        print(f"\nDONE. Deleted transactions: {n_tx}, signals: {n_sig}, "
              f"paper_trades: {n_pt}.")
        print("Next: rebuild so the dashboard reflects the removals:")
        print("  python .scripts\\eval_signals.py")
        print("  python .scripts\\backtest.py")
        print("  python .scripts\\export_dashboard_json.py")
        print("  python .scripts\\build_dashboard.py")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
