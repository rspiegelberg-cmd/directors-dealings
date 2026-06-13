"""Diagnostic: what does Yahoo v8 chart meta return for known AIM tickers?"""
import sys
sys.path.insert(0, '.scripts')
import fetch_prices, time
from datetime import date, timedelta

AIM_TICKERS = ['ARC', 'FEVR', 'JET2', 'OXB', 'SYS1']

today = date.today()
p2 = fetch_prices._to_epoch(today) + 86400
p1 = fetch_prices._to_epoch(today - timedelta(days=5))

for ticker in AIM_TICKERS:
    sym = ticker + '.L'
    try:
        block = fetch_prices.fetch_chart(sym, p1, p2)
        meta = block.get('meta', {})
        print(f"{ticker}:")
        print(f"  exchangeName:     {meta.get('exchangeName')!r}")
        print(f"  fullExchangeName: {meta.get('fullExchangeName')!r}")
        print(f"  market:           {meta.get('market')!r}")
        print(f"  marketCap:        {meta.get('marketCap')!r}")
        time.sleep(0.5)
    except Exception as e:
        print(f"{ticker}: ERROR {e}")
