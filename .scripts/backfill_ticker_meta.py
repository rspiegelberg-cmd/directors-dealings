"""Sprint 26/28 -- Ticker metadata enrichment.

B-097: market_cap_gbp via Yahoo Finance v8 chart endpoint (same endpoint
as fetch_prices.py). Sprint 28 spike confirmed Yahoo v8 does NOT return
marketCap for UK .L symbols (always None). Fallback: static CSV at
.data/_company_market_caps.csv (ticker,market_cap_gbp,notes). CSV rows
fill the gap for the top ~50 tickers; the rest stay NULL.

B-101: website_url via static CSV at .data/_company_websites.csv
(ticker,website_url,notes). Yahoo assetProfile (the only reliable source)
requires auth as of 2026. render_company.py already falls back to a Google
IR search link when website_url is NULL.

B-105: AIM detection from v8 chart exchangeName / fullExchangeName still
works (returns exchange metadata even when marketCap is absent).

Zone B -- Rupert runs from PowerShell. Never run from Claude bash (writes DB).

Pipeline position:
    1. fetch_sectors.py
    2. backfill_ticker_meta.py    <-- this script
    3. backfill_benchmarks.py
    4. backfill_reporting_dates.py
    5. export_dashboard_json.py
    6. build_dashboard.py

CLI:
    python backfill_ticker_meta.py [--ticker TICKER] [--dry-run]
                                   [--rate-limit FLOAT] [--resume] [--verbose]

Progress file: .scripts/_meta_progress.json
Cache:         .scripts/_meta_cache/{ticker}_meta.json  (7-day TTL)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db           # noqa: E402
import db_health    # noqa: E402
import fetch_prices # noqa: E402  -- reuse fetch_chart() and BACKOFF_SECONDS

PROGRESS_PATH = HERE / "_meta_progress.json"
CACHE_DIR = HERE / "_meta_cache"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7-day TTL; meta changes slowly

# Static CSV fallbacks (Sprint 28 B-097b / B-101b)
_CSV_MARKET_CAPS  = HERE.parent / ".data" / "_company_market_caps.csv"
_CSV_WEBSITES     = HERE.parent / ".data" / "_company_websites.csv"


def _load_csv_overrides() -> dict:
    """Load market_cap_gbp and website_url from static CSV fallback files.

    Returns {ticker -> {"market_cap_gbp": float|None, "website_url": str|None}}.
    Missing or malformed CSVs are silently skipped (CSV is best-effort).
    """
    import csv as _csv
    out: dict = {}

    def _read(path: Path, key_col: str, val_col: str, transform=None):
        if not path.exists():
            return
        try:
            with path.open(encoding="utf-8") as fh:
                reader = _csv.DictReader(fh)
                for row in reader:
                    ticker = (row.get("ticker") or "").strip()
                    val = (row.get(val_col) or "").strip()
                    if not ticker or not val:
                        continue
                    if transform:
                        try:
                            val = transform(val)
                        except (TypeError, ValueError):
                            continue
                    if ticker not in out:
                        out[ticker] = {"market_cap_gbp": None, "website_url": None}
                    out[ticker][key_col] = val
        except (OSError, Exception):
            pass

    _read(_CSV_MARKET_CAPS, "market_cap_gbp", "market_cap_gbp",
          transform=lambda v: float(v))
    _read(_CSV_WEBSITES, "website_url", "website_url")
    return out

# Minimal date window -- we only need the meta block, not actual price rows.
# One day's range is enough to get a valid chart response with meta populated.
_WINDOW_DAYS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_progress() -> dict:
    if not PROGRESS_PATH.exists():
        return {"completed_tickers": [], "last_run": None}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"completed_tickers": [], "last_run": None}


def _write_progress_atomic(state: dict) -> None:
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, PROGRESS_PATH)


def _cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / (safe + "_meta.json")


def _read_cache(ticker: str) -> dict | None:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = data.get("fetched_at")
    if not fetched_at:
        return None
    try:
        dt = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return data if (time.time() - dt.timestamp()) < CACHE_TTL_SECONDS else None


def _write_cache(ticker: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(ticker)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _fetch_meta_block(yahoo_symbol: str, *, rate_limit: float) -> dict | None:
    """Fetch chart.result[0].meta for `yahoo_symbol` using a minimal date window.

    Reuses fetch_prices.fetch_chart() which already handles gzip, retries,
    backoff, and the User-Agent that Yahoo accepts. Returns the meta dict or
    None on 404 (delisted). Raises RuntimeError on other network failures.
    """
    from datetime import timedelta
    today = date.today()
    p2_date = today
    p1_date = today - timedelta(days=_WINDOW_DAYS)
    p1 = fetch_prices._to_epoch(p1_date)
    p2 = fetch_prices._to_epoch(p2_date) + 24 * 3600

    last_err = None
    for attempt in range(fetch_prices.MAX_RETRIES):
        try:
            chart_block = fetch_prices.fetch_chart(yahoo_symbol, p1, p2)
            time.sleep(rate_limit)
            return chart_block.get("meta") or {}
        except Exception as e:  # noqa: BLE001
            import urllib.error
            time.sleep(rate_limit)
            if isinstance(e, urllib.error.HTTPError):
                if e.code == 404:
                    return None   # delisted / unknown symbol
                if e.code in (429, 503) and attempt < fetch_prices.MAX_RETRIES - 1:
                    last_err = e
                    time.sleep(fetch_prices.BACKOFF_SECONDS[attempt])
                    continue
            last_err = e
            break

    raise RuntimeError(
        f"fetch_meta_block({yahoo_symbol}): {last_err}"
    ) from last_err


def _extract_enrichment(meta: dict) -> dict:
    """Parse a chart meta block into a flat enrichment dict.

    Keys: market_cap_gbp, is_aim, benchmark_symbol.
    website_url is not available from the v8 chart endpoint -- stays None.
    """
    out: dict = {
        "market_cap_gbp":   None,
        "shares_outstanding": None,   # not in chart meta; reserved for future
        "website_url":      None,     # requires quoteSummary (blocked)
        "is_aim":           None,
        "benchmark_symbol": None,
    }

    # AIM detection from exchangeName / fullExchangeName.
    exchange      = (meta.get("exchangeName")     or "").upper()
    full_exchange = (meta.get("fullExchangeName") or "").upper()
    is_aim = 1 if ("AIM" in exchange or "AIM" in full_exchange) else 0
    out["is_aim"] = is_aim
    out["benchmark_symbol"] = "^FTSC" if is_aim else None  # None -> keep existing; ^AIM dead on Yahoo

    # Market cap (present for individual stocks, not for indices).
    mc_raw = meta.get("marketCap")
    if mc_raw is not None:
        try:
            mc = float(mc_raw)
            currency = (meta.get("currency") or "").strip()
            if currency == "GBp":
                mc = mc / 100.0   # pence -> pounds
            elif currency not in ("GBP", ""):
                mc = None         # reject non-GBP
            out["market_cap_gbp"] = mc
        except (TypeError, ValueError):
            pass

    return out


def _upsert_meta(conn, ticker: str, enrichment: dict, *,
                 now: str, csv_override: dict | None = None) -> str:
    """UPSERT enrichment into tickers_meta. Returns a change summary string.

    csv_override (Sprint 28 B-097b/B-101b): dict with optional keys
    'market_cap_gbp' and 'website_url'. CSV values fill in when Yahoo
    returns None -- they do NOT overwrite a non-None Yahoo value.
    """
    changes = []
    csv = csv_override or {}

    # Resolve market_cap: Yahoo value takes precedence; CSV is fallback.
    mc = enrichment.get("market_cap_gbp")
    if mc is None:
        mc = csv.get("market_cap_gbp")

    # Resolve website_url: CSV only (Yahoo blocked).
    website = csv.get("website_url")

    row = conn.execute(
        "SELECT market_cap_gbp, is_aim, benchmark_symbol, website_url "
        "FROM tickers_meta WHERE ticker = ?",
        (ticker,),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO tickers_meta "
            "(ticker, market_cap_gbp, is_aim, benchmark_symbol, "
            " website_url, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                ticker,
                mc,
                enrichment["is_aim"] or 0,
                enrichment["benchmark_symbol"] or "^FTAS",
                website,
                now,
            ),
        )
        return "inserted"

    # Build UPDATE. Only promote is_aim/benchmark to AIM -- never demote back.
    set_parts = ["market_cap_gbp = ?", "updated_at = ?"]
    params: list = [mc, now]

    if enrichment["is_aim"] == 1:
        set_parts += ["is_aim = 1", "benchmark_symbol = '^FTSC'"]
        if not row["is_aim"]:
            changes.append("is_aim->1")
        if row["benchmark_symbol"] != "^FTSC":
            changes.append("benchmark->^FTSC")

    # website_url: only set when currently NULL and CSV has a value.
    if website and not row["website_url"]:
        set_parts.append("website_url = ?")
        params.append(website)
        changes.append("website=set")

    conn.execute(
        "UPDATE tickers_meta SET " + ", ".join(set_parts) + " WHERE ticker = ?",
        params + [ticker],
    )
    if mc is not None:
        changes.append("mktcap=" + str(round(mc / 1e6)) + "m")
    return ", ".join(changes) if changes else "ok"


def distinct_stock_tickers(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions "
        "WHERE ticker NOT LIKE '^%' AND ticker IS NOT NULL "
        "ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


# ---------------------------------------------------------------------------
# Main run()
# ---------------------------------------------------------------------------

def run(
    *,
    only_ticker: str | None = None,
    dry_run: bool = False,
    rate_limit: float = 0.5,
    resume: bool = False,
    verbose: bool = False,
) -> dict:
    summary = {
        "tickers":            0,
        "ok":                 0,
        "not_found":          0,
        "errors":             0,
        "skipped_resume":     0,
        "aim_detected":       0,
        "mktcap_populated":   0,
        "website_populated":  0,
        "csv_mktcap_used":    0,
        "csv_website_used":   0,
    }
    # Sprint 28: load CSV fallbacks once per run
    csv_overrides = _load_csv_overrides()
    if verbose:
        print(f"  CSV overrides loaded: {len(csv_overrides)} tickers")

    progress = (_read_progress() if resume
                else {"completed_tickers": [], "last_run": None})
    completed = set(progress.get("completed_tickers") or [])

    conn = db.connect()
    try:
        tickers = [only_ticker] if only_ticker else distinct_stock_tickers(conn)
        for t in tickers:
            if not t:
                continue
            summary["tickers"] += 1

            if resume and t in completed:
                summary["skipped_resume"] += 1
                if verbose:
                    print(f"  [skip] {t}")
                continue

            # 7-day cache check.
            cached = _read_cache(t)
            if cached:
                enrichment = cached.get("extracted") or {}
                if verbose:
                    print(f"  {t}: cache hit")
            else:
                yahoo_symbol = fetch_prices.yahoo_symbol_for(t)
                try:
                    meta = _fetch_meta_block(yahoo_symbol, rate_limit=rate_limit)
                except RuntimeError as e:
                    summary["errors"] += 1
                    if verbose:
                        print(f"  {t}: ERROR {e}")
                    completed.add(t)
                    _persist_progress(progress, completed)
                    continue

                if meta is None:
                    summary["not_found"] += 1
                    if verbose:
                        print(f"  {t}: not found / delisted")
                    enrichment = {}
                else:
                    enrichment = _extract_enrichment(meta)
                    try:
                        _write_cache(t, {
                            "ticker": t,
                            "fetched_at": db.iso_now(),
                            "extracted": enrichment,
                        })
                    except OSError:
                        pass

            csv_ov = csv_overrides.get(t)
            csv_ov = csv_overrides.get(t)
            if enrichment:
                summary["ok"] += 1
                if enrichment.get("is_aim") == 1:
                    summary["aim_detected"] += 1
                # market cap: Yahoo value or CSV fallback
                mc_yahoo = enrichment.get("market_cap_gbp")
                mc_csv = (csv_ov or {}).get("market_cap_gbp")
                if mc_yahoo is not None or mc_csv is not None:
                    summary["mktcap_populated"] += 1
                    if mc_yahoo is None and mc_csv is not None:
                        summary["csv_mktcap_used"] += 1
                # website: CSV only
                if (csv_ov or {}).get("website_url"):
                    summary["website_populated"] += 1
                    summary["csv_website_used"] += 1
                if not dry_run:
                    change = _upsert_meta(conn, t, enrichment, now=db.iso_now(),
                                          csv_override=csv_ov)
                    conn.commit()
                    if verbose:
                        print("  " + t + ": " + change)
            else:
                summary["ok"] += 1
                # Even with no Yahoo data, CSV override may have website/mktcap
                if csv_ov:
                    if csv_ov.get("market_cap_gbp") is not None:
                        summary["mktcap_populated"] += 1
                        summary["csv_mktcap_used"] += 1
                    if csv_ov.get("website_url"):
                        summary["website_populated"] += 1
                        summary["csv_website_used"] += 1
                    if not dry_run:
                        _empty = {"market_cap_gbp": None, "is_aim": None,
                                  "benchmark_symbol": None, "website_url": None,
                                  "shares_outstanding": None}
                        change = _upsert_meta(conn, t, _empty, now=db.iso_now(),
                                              csv_override=csv_ov)
                        conn.commit()
                        if verbose:
                            print("  " + t + ": csv-only " + change)
                elif verbose:
                    print("  " + t + ": no data")

            completed.add(t)
            _persist_progress(progress, completed)

    finally:
        conn.close()

    if verbose:
        import json as _j
        print("backfill_ticker_meta:", _j.dumps(summary, indent=2))
    return summary


def _persist_progress(progress: dict, completed: set) -> None:
    progress["completed_tickers"] = sorted(completed)
    progress["last_run"] = db.iso_now()
    try:
        _write_progress_atomic(progress)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Enrich tickers_meta via Yahoo v8 chart + CSV fallbacks (B-097/B-101/B-105)."
    )
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rate-limit", type=float, default=0.5)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not args.dry_run:
        if not db_health.check(db.DB_PATH):
            print("[backfill_ticker_meta] FATAL: pre-run integrity_check failed.")
            return 2
        if not db_health.backup():
            print("[backfill_ticker_meta] FATAL: backup failed. Refusing to proceed.")
            return 3

    summary = run(
        only_ticker=args.ticker,
        dry_run=args.dry_run,
        rate_limit=args.rate_limit,
        resume=args.resume,
        verbose=args.verbose,
    )
    import json as _json
    print(_json.dumps(summary, indent=2, sort_keys=True))

    if not args.dry_run:
        try:
            if not db_health.check(db.DB_PATH):
                print("[backfill_ticker_meta] WARNING: post-run integrity check failed.")
                return 4
            db_health.seal()
        except Exception as exc:
            print("[db_health] seal failed (non-fatal):", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
