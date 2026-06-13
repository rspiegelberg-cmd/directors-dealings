import sqlite3

conn = sqlite3.connect('.data/directors.db')
rows = conn.execute(
    "SELECT ticker, sector FROM tickers_meta "
    "WHERE benchmark_symbol='^AIM' ORDER BY ticker"
).fetchall()
conn.close()

print(f"{len(rows)} tickers now benchmarked against ^AIM:")
for ticker, sector in rows:
    print(f"  {ticker:<8} {sector or '(no sector)'}")

if not rows:
    print("  None detected -- backfill_ticker_meta found no AIM exchange stocks.")
    print("  Check that Yahoo returned exchangeName data for your tickers.")
