"""
Phase 11.4 cleanup -- removes two categories of contaminated rows.
Run from PowerShell: python .scripts/phase11_cleanup.py

Category 1: BUY + price=0  (149 rows surviving reparse — source filings not
            in scrape cache so the orphan mechanism never fired on them)
Category 2: price > 200 outside the verified allowlist  (38 rows where the
            parser captured the total transaction VALUE as the unit price)

Zone B write — Windows Python only.
"""
import sqlite3, pathlib

DB = pathlib.Path(__file__).parent.parent / ".data" / "directors.db"
ALLOWLIST = ("LTI", "NXT", "AZN", "GAW")
ph = ",".join("?" * len(ALLOWLIST))

conn = sqlite3.connect(str(DB))
c = conn.cursor()

# ── preview ─────────────────────────────────────────────────────────────────
c.execute(
    "SELECT fingerprint, ticker, director, date, shares, price "
    "FROM transactions WHERE type='BUY' AND (price IS NULL OR price=0) "
    "ORDER BY date DESC"
)
cat1_rows = c.fetchall()
cat1_fps  = [r[0] for r in cat1_rows]

c.execute(
    f"SELECT fingerprint, ticker, director, date, shares, price "
    f"FROM transactions "
    f"WHERE price > 200 AND type IN ('BUY','SELL') AND ticker NOT IN ({ph}) "
    f"ORDER BY price DESC",
    ALLOWLIST,
)
cat2_rows = c.fetchall()
cat2_fps  = [r[0] for r in cat2_rows]

all_fps = list(set(cat1_fps + cat2_fps))

print("=" * 60)
print("Phase 11.4 Cleanup Preview")
print("=" * 60)
print(f"\nCategory 1 — BUY + price=0: {len(cat1_rows)} rows")
for r in cat1_rows[:10]:
    print(f"  {r[1]:6s} {r[3]}  shares={r[4]:6}  price={r[5]}  {str(r[2])[:30]}")
if len(cat1_rows) > 10:
    print(f"  ... and {len(cat1_rows)-10} more")

print(f"\nCategory 2 — price >£200 (non-allowlist): {len(cat2_rows)} rows")
for r in cat2_rows[:15]:
    print(f"  {r[1]:6s} {r[3]}  shares={r[4]:6}  price={r[5]:.2f}  {str(r[2])[:30]}")
if len(cat2_rows) > 15:
    print(f"  ... and {len(cat2_rows)-15} more")

print(f"\nTotal unique fingerprints to delete: {len(all_fps)}")

# ── confirm ──────────────────────────────────────────────────────────────────
resp = input("\nProceed with deletion? [y/N] ").strip().lower()
if resp != "y":
    print("Aborted — no changes made.")
    conn.close()
    exit()

# ── delete (cascade: signals → paper_trades → transactions) ─────────────────
chunk = 200
def chunked_delete(table, col, fps):
    deleted = 0
    for i in range(0, len(fps), chunk):
        batch = fps[i:i+chunk]
        bph   = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM {table} WHERE {col} IN ({bph})", batch)
        deleted += len(batch)
    return deleted

chunked_delete("signals",      "fingerprint", all_fps)
chunked_delete("paper_trades", "fingerprint", all_fps)
chunked_delete("transactions", "fingerprint", all_fps)
conn.commit()

# ── verify ───────────────────────────────────────────────────────────────────
c.execute("SELECT COUNT(*) FROM transactions WHERE type='BUY' AND (price IS NULL OR price=0)")
rem_zero = c.fetchone()[0]
c.execute(f"SELECT COUNT(*) FROM transactions WHERE price>200 AND type IN ('BUY','SELL') AND ticker NOT IN ({ph})", ALLOWLIST)
rem_outliers = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM transactions")
total = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM signals")
sig_total = c.fetchone()[0]

print("\nDone.")
print(f"  Transactions remaining : {total}")
print(f"  Signals remaining      : {sig_total}")
print(f"  BUY+price=0 remaining  : {rem_zero}  {'PASS' if rem_zero==0 else 'FAIL'}")
print(f"  price>200 remaining    : {rem_outliers}  {'PASS' if rem_outliers==0 else 'FAIL'}")
print()
print("Next steps (run from PowerShell):")
print("  python .scripts/eval_signals.py")
print("  python .scripts/backtest.py")
print("  python .scripts/export_dashboard_json.py")
print("  python .scripts/build_dashboard.py")
