"""Read-only diagnostic: find transactions whose share count looks like a year.

Flags the 'year-as-shares' parser bug (e.g. shares == 2026, the filing year
leaking into the volume field). READ-ONLY — does not write to the DB.

Run from project root:  python .scripts/audit_year_shares.py
"""
import sqlite3

DB = ".data/directors.db"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute(
    """
    SELECT ticker, director, date, shares, price, value
    FROM transactions
    WHERE shares BETWEEN 2018 AND 2027
    ORDER BY shares, date
    """
).fetchall()

print(f"{len(rows)} suspect rows (shares in 2018..2027)\n")
print(f"{'TICKER':<8} {'DATE':<12} {'SHARES':>7}  {'PRICE':>7}  {'VALUE':>12}  DIRECTOR")
print("-" * 80)
for r in rows:
    print(
        f"{(r['ticker'] or ''):<8} {(r['date'] or ''):<12} "
        f"{(r['shares'] if r['shares'] is not None else ''):>7}  "
        f"{(r['price'] if r['price'] is not None else ''):>7}  "
        f"{(r['value'] if r['value'] is not None else ''):>12}  "
        f"{r['director'] or ''}"
    )

# Cluster summary: how many rows share each exact year-value?
print("\nCount by exact share value:")
clusters = con.execute(
    """
    SELECT shares, COUNT(*) AS n
    FROM transactions
    WHERE shares BETWEEN 2018 AND 2027
    GROUP BY shares
    ORDER BY n DESC, shares
    """
).fetchall()
for c in clusters:
    print(f"  shares={c['shares']:<6} -> {c['n']} row(s)")

con.close()
