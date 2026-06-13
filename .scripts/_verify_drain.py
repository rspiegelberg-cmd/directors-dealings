r"""Read-only post-drain verification. Safe to run anytime; writes nothing.

    python .scripts\_verify_drain.py
"""
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, ".data", "directors.db")
PENDING = os.path.join(ROOT, ".scripts", "_pending_review.json")


def main():
    p = json.load(open(PENDING, encoding="utf-8"))
    pending_now = p.get("count", len(p)) if isinstance(p, dict) else len(p)
    print("pending now:", pending_now, "(was 4,352 before the drain)")

    c = sqlite3.connect(DB)
    print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
    print("transactions total:", c.execute("SELECT COUNT(1) FROM transactions").fetchone()[0])
    print("signals total:", c.execute("SELECT COUNT(1) FROM signals").fetchone()[0])

    print("buys since 2026-05-30:",
          c.execute("SELECT COUNT(1) FROM transactions "
                    "WHERE type='BUY' AND date>='2026-05-30'").fetchone()[0])

    print("\nrecent fired signals (>=2026-05-30):")
    rows = c.execute(
        "SELECT t.date, s.signal_id, t.ticker, t.type, t.value "
        "FROM signals s JOIN transactions t ON s.fingerprint=t.fingerprint "
        "WHERE t.date>='2026-05-30' ORDER BY t.date, t.ticker"
    ).fetchall()
    for r in rows:
        print("  ", r)
    print("  (count:", len(rows), ")")

    print("\nincident filings (expect still SELL until the targeted reparse):")
    for tk in ("JMAT", "GEN", "UTL", "UIL", "CAD", "PSN"):
        for r in c.execute(
            "SELECT date, ticker, director, type, value FROM transactions "
            "WHERE ticker=? AND date>='2026-05-28'", (tk,)
        ).fetchall():
            print("  ", r)


if __name__ == "__main__":
    main()
