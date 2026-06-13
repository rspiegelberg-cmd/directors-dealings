"""
Phase 11.4 integrity gate -- read-only checks on directors.db.
Run from PowerShell: python .scripts/phase11_integrity_check.py
"""
import sqlite3, pathlib, sys

DB = pathlib.Path(__file__).parent.parent / ".data" / "directors.db"

conn = sqlite3.connect(str(DB))
c = conn.cursor()

c.execute("SELECT COUNT(*) FROM transactions")
total = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM signals")
sig_total = c.fetchone()[0]

# 1. BUY + price=0  (D.3 gate should have removed all of these)
c.execute("SELECT COUNT(*) FROM transactions WHERE type='BUY' AND (price IS NULL OR price=0)")
buy_zero = c.fetchone()[0]

# 2. Year-as-shares: shares value in calendar-year range (suspicious contamination)
c.execute("""
    SELECT ticker, director, type, shares, price, date
    FROM transactions
    WHERE shares BETWEEN 2000 AND 2030
      AND type IN ('BUY','SELL')
    ORDER BY date DESC LIMIT 15
""")
year_samples = c.fetchall()

# 3. Price >£200 outside the known allowlist
ALLOWLIST = ("LTI", "NXT", "AZN", "GAW")
ph = ",".join("?" * len(ALLOWLIST))
c.execute(f"""
    SELECT COUNT(*) FROM transactions
    WHERE price > 200 AND type IN ('BUY','SELL')
    AND ticker NOT IN ({ph})
""", ALLOWLIST)
price_outliers = c.fetchone()[0]
c.execute(f"""
    SELECT ticker, director, type, shares, price, date
    FROM transactions
    WHERE price > 200 AND type IN ('BUY','SELL')
    AND ticker NOT IN ({ph})
    ORDER BY price DESC LIMIT 10
""", ALLOWLIST)
outlier_samples = c.fetchall()

# 4. Tullow year-as-shares fingerprint — should be gone
c.execute("SELECT shares,price,value FROM transactions WHERE fingerprint=?",
          ("21fea6a08312de60",))
tullow_old = c.fetchall()

# 5. Allowlist tickers sanity (should still have rows at realistic prices)
c.execute(f"""
    SELECT ticker, COUNT(*), MIN(price), MAX(price), AVG(price)
    FROM transactions
    WHERE ticker IN ({ph}) AND type IN ('BUY','SELL')
    GROUP BY ticker
""", ALLOWLIST)
allowlist_rows = c.fetchall()

# 6. Signal counts by id
c.execute("""
    SELECT signal_id, COUNT(*) FROM signals
    GROUP BY signal_id ORDER BY COUNT(*) DESC
""")
sig_counts = c.fetchall()

conn.close()

# --- Report ---
print("=" * 55)
print("Phase 11.4 Integrity Gate")
print("=" * 55)
print(f"  Transactions : {total}")
print(f"  Signals      : {sig_total}")
print()

r1 = "PASS" if buy_zero == 0 else f"FAIL ({buy_zero} rows)"
print(f"[1] BUY + price=0       : {r1}")

r2 = "PASS" if len(year_samples) == 0 else f"REVIEW ({len(year_samples)} rows)"
print(f"[2] Year-as-shares      : {r2}")
for row in year_samples:
    print(f"      {row[0]:6s} {row[2]:4s} shares={row[3]:6} price={row[4]:8.2f} {row[5]}  {row[1][:30]}")

r3 = "PASS" if price_outliers == 0 else f"REVIEW ({price_outliers} rows)"
print(f"[3] Price >£200 (non-AL): {r3}")
for row in outlier_samples:
    print(f"      {row[0]:6s} {row[2]:4s} shares={row[3]:6} price={row[4]:8.2f} {row[5]}")

r4 = "PASS" if not tullow_old else f"FAIL — row still present: {tullow_old}"
print(f"[4] Tullow old fp gone  : {r4}")

print()
print("[5] Allowlist tickers:")
for row in allowlist_rows:
    print(f"      {row[0]:6s} {row[1]:3d} rows  price min={row[2]:.2f} max={row[3]:.2f} avg={row[4]:.2f}")

print()
print("[6] Signal counts:")
for s, n in sig_counts:
    print(f"      {s:<25s} {n:5d}")

all_pass = (buy_zero == 0 and len(year_samples) == 0 and
            price_outliers == 0 and not tullow_old)
print()
print("OVERALL:", "PASS — ready for Phase 11.5" if all_pass else "REVIEW items above")
