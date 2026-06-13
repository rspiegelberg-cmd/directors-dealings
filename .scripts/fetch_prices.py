"""Single-ticker Yahoo close+volume fetcher for Directors Dealings Stage 3.

Stdlib only. Anonymous Yahoo chart endpoint
    https://query1.finance.yahoo.com/v8/finance/chart/{symbol}

Public surface:
    UnsupportedCurrency           -- raised by normalise_currency for non-GBP*
    FetchResult (NamedTuple)      -- {status, rows, currency, yahoo_symbol,
                                       cache_hit, network_calls}
    yahoo_symbol_for(ticker)      -- adds '.L' unless ticker starts with '^'
    db_ticker_for(yahoo_symbol)   -- strips '.L' unless ^-prefixed
    fetch_chart(symbol, p1, p2, ua, timeout=30)
                                  -- raw network call -> chart.result[0]
    chart_to_rows(chart_block)    -- (rows, currency); uses adjclose for close
    normalise_currency(rows, currency)
                                  -- GBp -> GBP (/100); raises for USD/EUR
    fetch(ticker, date_from, date_to, *, rate_limit, use_cache, ...)
                                  -- top-level public entry point

Cache shape (.scripts/_price_cache/{ticker}.json):
    {ticker, yahoo_symbol, currency, fetched_at, window:{date_from, date_to},
     rows:[{date, close, volume}, ...]}

Decisions enforced here:
    * D-YAHOO-ENDPOINT (anonymous chart endpoint, no crumb)
    * D-CACHE          (20h TTL)
    * D-FAIL-LOUD      (404 -> status='delisted')
    * D-FX             (GBp/100; USD/EUR -> unsupported_currency)
    * D-WINDOW         (caller passes period)
    * D-RATE-LIMIT     (default 0.5s, sleep AFTER network call)
    * close := adjclose[i]  (split/div-adjusted)

Empirical Yahoo response shape (probe of ^FTAS on 2026-05-13):
    {"chart": {"result": [{
        "meta": {"currency":"GBP","symbol":"^FTAS", ...},
        "timestamp": [1746082800, ...],
        "indicators": {
            "quote": [{"close":[...], "volume":[...], "open":[...],
                        "high":[...], "low":[...]}],
            "adjclose": [{"adjclose":[...]}]
        }
    }], "error": null}}
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "_price_cache"
CACHE_TTL_SECONDS = 20 * 3600  # 20 hours
USER_AGENT = "DirectorsDealings-Research/0.3 (+contact: rspiegelberg@gmail.com)"
BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"

# Backoff schedule for HTTP 429 / 503. Index = retry attempt (0..2).
BACKOFF_SECONDS = (30, 60, 120)
MAX_RETRIES = 3


class UnsupportedCurrency(Exception):
    """Raised when Yahoo returns a currency we deliberately don't normalise."""


class FetchResult(NamedTuple):
    status: str
    rows: list
    currency: object
    yahoo_symbol: str
    cache_hit: bool
    network_calls: int
    detail: str = ""


# ---------------------------------------------------------------------------
# Symbol translation
# ---------------------------------------------------------------------------

def yahoo_symbol_for(ticker):
    """Add .L for LSE stocks; leave caret-prefixed benchmarks alone."""
    t = ticker.strip()
    if t.startswith("^"):
        return t
    return t + ".L"


def db_ticker_for(yahoo_symbol):
    """Strip trailing .L for stocks; leave caret-prefixed benchmarks alone."""
    s = yahoo_symbol.strip()
    if s.startswith("^"):
        return s
    if s.endswith(".L"):
        return s[:-2]
    return s


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _to_epoch(d):
    """Convert YYYY-MM-DD (str or date) to UTC midnight epoch seconds.

    M-F (2026-05-22): wrap the strptime call in try/except so a malformed
    date passed by an external caller (e.g. a backfill script with a
    typo'd CLI arg) raises a clear ValueError naming `_to_epoch` and the
    bad input, instead of an opaque ValueError from deep inside strptime.
    """
    if isinstance(d, date):
        s = d.isoformat()
    else:
        s = str(d)
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"fetch_prices._to_epoch: expected ISO date YYYY-MM-DD, got {s!r} "
            f"({exc})"
        ) from exc
    return int(dt.timestamp())


def _epoch_to_iso_date(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def fetch_chart(yahoo_symbol, period1, period2, user_agent=USER_AGENT, timeout=30.0):
    """One network call. Returns the chart.result[0] dict."""
    qs = urllib.parse.urlencode({
        "period1": int(period1),
        "period2": int(period2),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    })
    url = BASE_URL + urllib.parse.quote(yahoo_symbol, safe="") + "?" + qs
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        enc = resp.headers.get("Content-Encoding", "") if hasattr(resp, "headers") else ""
        if enc == "gzip":
            body = gzip.decompress(body)
    parsed = json.loads(body)
    chart = parsed.get("chart") or {}
    err = chart.get("error")
    if err:
        raise RuntimeError("Yahoo chart error: " + str(err))
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("Yahoo chart returned empty result list")
    return results[0]


def chart_to_rows(chart_block):
    """Convert a chart.result[0] dict to (rows, currency).

    B-157: now extracts high and low alongside close/volume so that
    backfill_prices can persist OHLCV and backtest._cs_spread_bps has
    the raw H/L data it needs for the Corwin-Schultz spread estimator.
    """
    meta = chart_block.get("meta") or {}
    currency = meta.get("currency")
    timestamps = chart_block.get("timestamp") or []
    indicators = chart_block.get("indicators") or {}
    quote_block = (indicators.get("quote") or [{}])[0]
    adjclose_block = (indicators.get("adjclose") or [{}])[0]
    closes  = adjclose_block.get("adjclose") or quote_block.get("close") or []
    volumes = quote_block.get("volume") or []
    highs   = quote_block.get("high")   or []   # B-157
    lows    = quote_block.get("low")    or []   # B-157

    rows = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        vol = volumes[i] if i < len(volumes) else None
        hi  = highs[i]   if i < len(highs)   else None   # B-157
        lo  = lows[i]    if i < len(lows)    else None   # B-157
        rows.append({
            "date":   _epoch_to_iso_date(int(ts)),
            "close":  float(close),
            "high":   float(hi) if hi is not None else None,   # B-157
            "low":    float(lo) if lo is not None else None,   # B-157
            "volume": int(vol)  if vol is not None else None,
        })
    return rows, currency


def normalise_currency(rows, currency):
    """Divide close/high/low by 100 if currency=='GBp'; passthrough for 'GBP';
    raise UnsupportedCurrency for anything else (USD, EUR, etc.).

    B-157: high and low are now also scaled so C-S spread estimates are in
    consistent price units (pounds, not pence).
    """
    if currency is None:
        return rows
    # Pence first -- 'GBp'.upper() == 'GBP', so the case-insensitive
    # passthrough check must come AFTER this exact-case pence detector.
    if currency == "GBp":
        for r in rows:
            r["close"] = r["close"] / 100.0
            if r.get("high") is not None:   # B-157
                r["high"] = r["high"] / 100.0
            if r.get("low") is not None:    # B-157
                r["low"]  = r["low"]  / 100.0
        return rows
    if currency == "GBP":
        return rows
    raise UnsupportedCurrency("currency=" + repr(currency) + " not supported")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(ticker):
    safe = ticker.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / (safe + ".json")


def _read_cache(ticker):
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache_atomic(ticker, payload):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(ticker)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _cache_fresh(cache, now_epoch):
    fetched_at = cache.get("fetched_at")
    if not fetched_at:
        return False
    try:
        dt = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = now_epoch - dt.timestamp()
    return age < CACHE_TTL_SECONDS


def _cache_covers_window(cache, date_from, date_to):
    w = cache.get("window") or {}
    cf, ct = w.get("date_from"), w.get("date_to")
    if not cf or not ct:
        return False
    return cf <= date_from and ct >= date_to


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch(ticker, date_from, date_to, *,
          rate_limit=0.5, use_cache=True,
          user_agent=USER_AGENT, timeout=30.0, now_epoch=None):
    """Top-level: cache-aware Yahoo fetch for a single DB ticker."""
    if now_epoch is None:
        now_epoch = time.time()
    yahoo_symbol = yahoo_symbol_for(ticker)
    network_calls = 0

    if use_cache:
        cache = _read_cache(ticker)
        if cache and _cache_fresh(cache, now_epoch) and _cache_covers_window(cache, date_from, date_to):
            cached_rows = cache.get("rows", [])
            # B-157: old cache entries lack 'high'/'low' keys (pre-OHLCV format).
            # If any rows are present but have no 'high' key, treat as stale so
            # we re-fetch and repopulate the cache with full OHLCV data.
            hl_present = (not cached_rows) or ("high" in cached_rows[0])
            if hl_present:
                rows = [r for r in cached_rows
                        if date_from <= r["date"] <= date_to]
                return FetchResult(
                    status="ok",
                    rows=rows,
                    currency=cache.get("currency"),
                    yahoo_symbol=cache.get("yahoo_symbol", yahoo_symbol),
                    cache_hit=True,
                    network_calls=0,
                )
            # else: fall through to network fetch to refresh the cache

    period1 = _to_epoch(date_from)
    # Yahoo period2 is exclusive; bump one day so date_to is included.
    period2 = _to_epoch(date_to) + 24 * 3600

    chart_block = None
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            chart_block = fetch_chart(yahoo_symbol, period1, period2,
                                      user_agent=user_agent, timeout=timeout)
            network_calls += 1
            time.sleep(rate_limit)
            break
        except urllib.error.HTTPError as e:
            network_calls += 1
            time.sleep(rate_limit)
            if e.code == 404:
                return FetchResult(
                    status="delisted",
                    rows=[],
                    currency=None,
                    yahoo_symbol=yahoo_symbol,
                    cache_hit=False,
                    network_calls=network_calls,
                    detail="HTTP 404 for " + yahoo_symbol,
                )
            if e.code in (429, 503):
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_SECONDS[attempt])
                continue
            last_err = e
            break
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            network_calls += 1
            time.sleep(rate_limit)
            last_err = e
            break

    if chart_block is None:
        return FetchResult(
            status="error",
            rows=[],
            currency=None,
            yahoo_symbol=yahoo_symbol,
            cache_hit=False,
            network_calls=network_calls,
            detail=type(last_err).__name__ + ": " + str(last_err) if last_err else "unknown",
        )

    rows, currency = chart_to_rows(chart_block)
    if not rows:
        return FetchResult(
            status="empty",
            rows=[],
            currency=currency,
            yahoo_symbol=yahoo_symbol,
            cache_hit=False,
            network_calls=network_calls,
            detail="no rows in response",
        )

    try:
        normalise_currency(rows, currency)
    except UnsupportedCurrency as e:
        return FetchResult(
            status="unsupported_currency",
            rows=[],
            currency=currency,
            yahoo_symbol=yahoo_symbol,
            cache_hit=False,
            network_calls=network_calls,
            detail=str(e),
        )

    payload = {
        "ticker": ticker,
        "yahoo_symbol": yahoo_symbol,
        "currency": currency,
        "fetched_at": datetime.fromtimestamp(now_epoch, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "window": {"date_from": date_from, "date_to": date_to},
        "rows": rows,
    }
    try:
        _write_cache_atomic(ticker, payload)
    except OSError:
        pass

    out_rows = [r for r in rows if date_from <= r["date"] <= date_to]
    return FetchResult(
        status="ok",
        rows=out_rows,
        currency=currency,
        yahoo_symbol=yahoo_symbol,
        cache_hit=False,
        network_calls=network_calls,
    )


if __name__ == "__main__":
    print("fetch_prices module API:")
    print("yahoo_symbol_for BARC:", yahoo_symbol_for("BARC"))
    print("yahoo_symbol_for ^FTAS:", yahoo_symbol_for("^FTAS"))
    print("db_ticker_for BARC.L:", db_ticker_for("BARC.L"))
    print("db_ticker_for ^FTAS:", db_ticker_for("^FTAS"))
    sys.exit(0)
