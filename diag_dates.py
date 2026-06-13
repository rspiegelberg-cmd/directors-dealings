import sqlite3, json, os
conn = sqlite3.connect('.data/directors.db')
conn.row_factory = sqlite3.Row

r = conn.execute('SELECT MIN(date) AS lo, MAX(date) AS hi, COUNT(*) AS n FROM transactions').fetchone()
print(f"Date range: {r['lo']} to {r['hi']}, total {r['n']}")
print()

print("Transactions per month:")
for row in conn.execute("SELECT substr(date,1,7) AS m, COUNT(*) AS n FROM transactions GROUP BY m ORDER BY m"):
    bar = "#" * (row["n"] // 3)
    print(f"  {row['m']}: {bar} {row['n']}")
print()

print("Type distribution:")
for row in conn.execute("SELECT type, COUNT(*) AS n FROM transactions GROUP BY type ORDER BY n DESC"):
    print(f"  {row['type']}: {row['n']}")
print()

print("Cache files:", len(os.listdir(".scripts/_scrape_cache")))
try:
    with open(".scripts/_backfill_progress.json") as f:
        p = json.load(f)
    print(f"Backfill state: seen={p.get('filings_seen')}, written={p.get('transactions_written')}, completed_dates_len={len(p.get('completed_dates', []))}")
    print(f"Current window: {p.get('current_window')}")
except Exception as e:
    print(f"progress: {e}")
