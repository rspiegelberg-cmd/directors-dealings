import sqlite3, os

con = sqlite3.connect('.data/directors.db')
cur = con.cursor()

cur.execute('''SELECT fingerprint, url, date, ticker, CAST(value AS REAL) as v
FROM transactions
WHERE type = "BUY" AND buy_strictness = "UNKNOWN"
AND fingerprint NOT IN (SELECT DISTINCT fingerprint FROM signals)
ORDER BY v DESC''')
rows = cur.fetchall()

CACHE_DIR = '.scripts/_scrape_cache'
cache_files = set(os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else set()
print(f'Total files in scrape cache: {len(cache_files):,}')

# rns_id = last non-empty path segment of URL
def rns_id_from_url(url):
    if not url:
        return None
    parts = [p for p in url.rstrip('/').split('/') if p]
    return parts[-1] if parts else None

# Show a few URL examples and their derived rns_ids
print('\nURL -> rns_id derivation check (first 3 rows):')
for fp, url, date, ticker, v in rows[:3]:
    rns = rns_id_from_url(url)
    fname = f'{rns}.html'
    in_cache = fname in cache_files
    print(f'  URL: {url}')
    print(f'  rns_id: {rns}  |  cache file: {fname}  |  in cache: {in_cache}')
    print()

print('=== CACHE COVERAGE ===')
cached, not_cached, no_url = [], [], []
for fp, url, date, ticker, v in rows:
    if not url:
        no_url.append((date, ticker, v, url))
        continue
    rns = rns_id_from_url(url)
    fname = f'{rns}.html'
    if fname in cache_files:
        cached.append((date, ticker, v, rns))
    else:
        not_cached.append((date, ticker, v, rns, url))

print(f'In scrape cache:  {len(cached):,}  (reparse_corpus.py can fix these immediately)')
print(f'NOT in cache:     {len(not_cached):,}  (need backfill_filings.py first)')
print(f'No URL:           {len(no_url):,}')

if cached:
    print(f'\n=== CACHED — ready for reparse (top 15 by value) ===')
    for date, ticker, v, rns in sorted(cached, key=lambda x: -x[2])[:15]:
        print(f'  {date} {ticker:<6} £{v:>9,.0f}  rns_id={rns}')

if not_cached:
    print(f'\n=== NOT CACHED — need scrape first (top 15 by value) ===')
    for date, ticker, v, rns, url in sorted(not_cached, key=lambda x: -x[2])[:15]:
        print(f'  {date} {ticker:<6} £{v:>9,.0f}  rns_id={rns}')
