"""Sprint 28 B-096b -- Reporting dates backfill from Investegate.

Rewrites Sprint 26's Yahoo calendarEvents approach (which 401s as of 2026).
Scrapes the Investegate per-company page for each ticker to find
"Preliminary Results", "Half Year Results", and "Trading Statement" dates.

Zone B -- Rupert runs from PowerShell. Never run from Claude bash (writes DB).

Why this matters (B-096):
  Directors are legally in a closed period 30 calendar days before an
  interim or year-end announcement (MAR Article 19). We use a wider
  60-day window (Rupert decision 2026-06-03) as a conservative flag.
  The dashboard shows a yellow badge on any transaction within 60 days
  BEFORE a reporting date for the same ticker.

Investegate URL used (one fetch per company):
    https://www.investegate.co.uk/company/{TICKER}

  The company page lists all recent announcements for that ticker. We
  filter in Python for headlines/URL slugs matching results types:
        PRELIM       -- "Preliminary Results", "Full Year Results" etc.
        INTERIM      -- "Half Year Results", "Interim Results" etc.
        TRADING_STMT -- "Trading Statement", "Trading Update" etc.

  NOTE (2026-06-08): The old search URL
    Index.aspx?searchtype=RNSType&searchterm=...&searchrns={TICKER}
  was returning the GENERAL announcement feed (all companies, today's dates)
  instead of filtered results. Fixed by switching to the per-company URL.

Limitations:
  - Investegate company pages show ~50 most recent announcements.
    For very active companies this may only cover recent quarters.
  - Only tickers present in the transactions table are fetched.
  - INSERT OR IGNORE prevents duplicates.

Cache:
  _reporting_cache/{ticker}_all.json with 30-day TTL.
  (Old per-type cache files {ticker}_preliminary_results.json etc. are
   ignored by the new code but can be deleted from _reporting_cache/ if
   desired to free disk space.)

Pipeline position:
    1. fetch_sectors.py            -- sector + benchmark base assignment
    2. backfill_ticker_meta.py     -- AIM detection + enrichment
    3. backfill_benchmarks.py      -- ^AIM + ^FTAS price history
    4. backfill_reporting_dates.py -- results + trading statement dates
    5. export_dashboard_json.py
    6. build_dashboard.py

Progress file: .scripts/_reporting_progress.json
CLI:
    python backfill_reporting_dates.py [--ticker TICKER] [--dry-run]
                                       [--rate-limit FLOAT] [--resume]
                                       [--no-cache] [--verbose]
                                       [--lookback-years INT]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db           # noqa: E402
import db_health    # noqa: E402

PROGRESS_PATH = HERE / "_reporting_progress.json"
CACHE_DIR = HERE / "_reporting_cache"
CACHE_TTL_SECONDS = 30 * 24 * 3600   # 30-day TTL; results dates are stable

BASE_URL = "https://www.investegate.co.uk"
USER_AGENT = "DirectorsDealings-Research/0.3 (+contact: rspiegelberg@gmail.com)"
BACKOFF_SECONDS = (30, 60, 120)
MAX_RETRIES = 3
DEFAULT_LOOKBACK_YEARS = 3

# Investegate date pattern in announcement rows: "DD Mon YYYY [HH:MM AM/PM]"
_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Announcement-type detection: checked against headline text + URL slug
_PRELIM_RE = re.compile(
    r"preliminary[\s\-]results|prelim[\s\-]results|"
    r"full[\s\-]year[\s\-]results|annual[\s\-]results|"
    r"final[\s\-]results|year[\s\-]end[\s\-]results",
    re.IGNORECASE,
)
_INTERIM_RE = re.compile(
    r"half[\s\-]year[\s\-]results|interim[\s\-]results|"
    r"half[\s\-]year[\s\-]financial|six[\s\-]month[\s\-]results|"
    r"6[\s\-]month[\s\-]results",
    re.IGNORECASE,
)
_TRADING_RE = re.compile(
    r"trading[\s\-]statement|trading[\s\-]update|trading[\s\-]announcement|"
    r"interim[\s\-]management[\s\-]statement",
    re.IGNORECASE,
)


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


def _cache_path(ticker: str, type_slug: str) -> Path:
    safe = ticker.replace("/", "_").replace("\\", "_")
    # type_slug already URL-safe (letters + plus) — strip the + for filenames
    type_key = type_slug.replace("+", "_").lower()
    return CACHE_DIR / f"{safe}_{type_key}.json"


def _read_cache(ticker: str, type_slug: str) -> dict | None:
    p = _cache_path(ticker, type_slug)
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
    age = time.time() - dt.timestamp()
    return data if age < CACHE_TTL_SECONDS else None


def _write_cache(ticker: str, type_slug: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(ticker, type_slug)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _fetch_html(url: str, *, rate_limit: float = 0.8) -> str | None:
    """Fetch URL with polite sleep. Returns decoded HTML or None on 404.
    Raises RuntimeError on other persistent failures.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding", "") or "").lower()
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                # Charset detection: header > meta > utf-8 fallback
                ct = resp.headers.get("Content-Type", "") or ""
                m = re.search(r"charset=([\w\-]+)", ct, re.IGNORECASE)
                charset = m.group(1) if m else None
                if not charset:
                    head = raw[:2048].decode("ascii", errors="ignore")
                    m2 = re.search(r'<meta[^>]+charset=["\']?([\w\-]+)', head,
                                   re.IGNORECASE)
                    charset = m2.group(1) if m2 else "utf-8"
                try:
                    html = raw.decode(charset, errors="replace")
                except LookupError:
                    html = raw.decode("utf-8", errors="replace")
            time.sleep(rate_limit)
            return html
        except urllib.error.HTTPError as e:
            time.sleep(rate_limit)
            if e.code == 404:
                return None
            if e.code in (429, 503) and attempt < MAX_RETRIES - 1:
                last_err = e
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            last_err = e
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            time.sleep(rate_limit)
            last_err = e
            break
    raise RuntimeError(f"fetch_html({url}): {last_err}") from last_err


def _parse_date(day_str: str, mon_str: str, year_str: str) -> str | None:
    """Convert day/mon/year strings to 'YYYY-MM-DD', or None."""
    mon = _MONTHS.get(mon_str[:3].lower())
    if mon is None:
        return None
    try:
        d = date(int(year_str), mon, int(day_str))
        return d.isoformat()
    except ValueError:
        return None


def _extract_dates_from_html(html: str, ticker: str,
                              cutoff_date: date) -> list[dict]:
    """Parse Investegate company-page HTML for results announcements.

    Uses the .table-investegate tbody structure confirmed by diagnostic on
    2026-06-08. Each row: <td>DD Mon YYYY HH:MM PM</td> | source | company |
    <a class="announcement-link" href="...">Headline</a>.

    Type is inferred from the announcement headline text + URL slug using
    _PRELIM_RE, _INTERIM_RE, _TRADING_RE. Rows that don't match any type
    (e.g. PDMR notifications, director dealings, AGM notices) are skipped.

    Returns a list of {ticker, report_date, report_type, source} dicts.
    Only dates >= cutoff_date are returned; duplicate (date, type) pairs
    are de-duplicated.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[tuple] = set()

    for row in soup.select("table.table-investegate tbody tr"):
        tds = row.find_all("td")
        if not tds:
            continue

        # Date is always in the first <td>: "08 Jun 2026 07:09 PM"
        raw_date = tds[0].get_text(strip=True)
        dm = _DATE_RE.search(raw_date)
        if not dm:
            continue

        report_date = _parse_date(dm.group(1), dm.group(2), dm.group(3))
        if not report_date:
            continue
        try:
            rd = date.fromisoformat(report_date)
        except ValueError:
            continue
        if rd < cutoff_date:
            continue

        # Find the announcement link (class="announcement-link")
        ann_link = row.find("a", class_="announcement-link")
        if not ann_link:
            continue

        headline = ann_link.get_text(strip=True)
        href = ann_link.get("href", "")
        combined = headline + " " + href

        # Classify type; skip non-results announcements
        if _PRELIM_RE.search(combined):
            report_type = "PRELIM"
        elif _INTERIM_RE.search(combined):
            report_type = "INTERIM"
        elif _TRADING_RE.search(combined):
            report_type = "TRADING_STMT"
        else:
            continue

        key = (report_date, report_type)
        if key in seen:
            continue
        seen.add(key)

        # Build absolute URL for the filing deep-link (B-119).
        source_url = (BASE_URL + href) if href and href.startswith("/") else href or None

        out.append({
            "ticker":      ticker,
            "report_date": report_date,
            "report_type": report_type,
            "source":      "investegate",
            "source_url":  source_url,
        })

    return out


def _fetch_company_types(ticker: str, cutoff_date: date, *,
                         rate_limit: float, no_cache: bool = False,
                         verbose: bool = False) -> list[dict]:
    """Fetch the Investegate company page and extract all results-type dates.

    One fetch per company returns PRELIM + INTERIM + TRADING_STMT in one
    pass (instead of three separate search-URL fetches, which no longer
    filter by company correctly as of 2026-06-08).

    Returns list of {ticker, report_date, report_type, source} dicts, or []
    on 404 / no results. Raises RuntimeError on network failure.

    Cache key: {ticker}_all.json (30-day TTL).
    Old per-type cache files ({ticker}_preliminary_results.json etc.) are
    ignored by this function and can be deleted manually if desired.
    """
    type_slug = "all"

    if not no_cache:
        cached = _read_cache(ticker, type_slug)
        if cached is not None:
            dates = cached.get("extracted") or []
            if verbose:
                print(f"    {ticker}: cache hit ({len(dates)} dates)")
            return dates

    url = f"{BASE_URL}/company/{urllib.parse.quote(ticker)}"
    if verbose:
        print(f"    {ticker}: fetching {url}")

    html = _fetch_html(url, rate_limit=rate_limit)
    if html is None:
        if verbose:
            print(f"    {ticker}: 404")
        dates = []
    else:
        dates = _extract_dates_from_html(html, ticker, cutoff_date)
        if verbose:
            print(f"    {ticker}: {len(dates)} date(s) found")

    # Write to cache (even empty — avoids hammering 404 tickers)
    try:
        _write_cache(ticker, type_slug, {
            "ticker":     ticker,
            "type_slug":  type_slug,
            "fetched_at": db.iso_now(),
            "extracted":  dates,
        })
    except OSError:
        pass

    return dates


def _upsert_reporting_dates(conn, dates: list[dict], *, now: str) -> int:
    """INSERT OR IGNORE dates into reporting_dates. Returns count of new rows.
    Also back-fills source_url on existing rows when the new record has one
    (handles re-runs after migration 012 adds the column).
    """
    inserted = 0
    for d in dates:
        source_url = d.get("source_url")
        cur = conn.execute(
            "INSERT OR IGNORE INTO reporting_dates "
            "(ticker, report_date, report_type, source, fetched_at, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (d["ticker"], d["report_date"], d["report_type"], d["source"], now,
             source_url),
        )
        if cur.rowcount == 0 and source_url:
            # Row already exists but predates migration 012 — back-fill the URL.
            conn.execute(
                "UPDATE reporting_dates SET source_url=? "
                "WHERE ticker=? AND report_date=? AND report_type=? "
                "AND source_url IS NULL",
                (source_url, d["ticker"], d["report_date"], d["report_type"]),
            )
        else:
            inserted += cur.rowcount
    return inserted


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
    rate_limit: float = 0.8,
    resume: bool = False,
    no_cache: bool = False,
    verbose: bool = False,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
) -> dict:
    summary = {
        "tickers":        0,
        "ok":             0,
        "errors":         0,
        "skipped_resume": 0,
        "dates_found":    0,
        "rows_inserted":  0,
    }
    # Cutoff: don't import reporting dates older than lookback_years
    cutoff_date = date(date.today().year - lookback_years, 1, 1)

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

            if verbose:
                print(f"  {t}:")

            had_error = False
            try:
                all_dates = _fetch_company_types(
                    t, cutoff_date,
                    rate_limit=rate_limit,
                    no_cache=no_cache,
                    verbose=verbose,
                )
            except RuntimeError as e:
                summary["errors"] += 1
                had_error = True
                all_dates = []
                if verbose:
                    print(f"    {t}: ERROR {e}")

            if not had_error:
                summary["ok"] += 1
            summary["dates_found"] += len(all_dates)

            if verbose and all_dates:
                for d in all_dates:
                    print(f"    -> {d['report_date']} ({d['report_type']})")

            if not dry_run and all_dates:
                now = db.iso_now()
                inserted = _upsert_reporting_dates(conn, all_dates, now=now)
                summary["rows_inserted"] += inserted
                conn.commit()

            completed.add(t)
            _persist_progress(progress, completed)

    finally:
        conn.close()

    if verbose:
        print("backfill_reporting_dates summary:", summary)
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
    # type: (list[str] | None) -> int
    ap = argparse.ArgumentParser(
        description="Backfill results dates from Investegate search (B-096b)."
    )
    ap.add_argument("--ticker", default=None,
                    help="Run for a single ticker only.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch and report; do not write to DB.")
    ap.add_argument("--rate-limit", type=float, default=0.8,
                    help="Seconds to sleep after each call (default 0.8).")
    ap.add_argument("--resume", action="store_true",
                    help="Skip tickers already in _reporting_progress.json.")
    ap.add_argument("--no-cache", action="store_true",
                    help="Ignore the on-disk cache and re-fetch every company page. "
                         "Use after fixing a parser bug to regenerate all data.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--lookback-years", type=int, default=DEFAULT_LOOKBACK_YEARS,
                    help="Years back to capture reporting dates.")
    args = ap.parse_args(argv)

    if not args.dry_run:
        if not db_health.check(db.DB_PATH):
            print("[backfill_reporting_dates] FATAL: integrity_check failed.")
            return 2
        if not db_health.backup():
            print("[backfill_reporting_dates] FATAL: backup failed.")
            return 3

    summary = run(
        only_ticker=args.ticker,
        dry_run=args.dry_run,
        rate_limit=args.rate_limit,
        resume=args.resume,
        no_cache=args.no_cache,
        verbose=args.verbose,
        lookback_years=args.lookback_years,
    )
    import json as _json
    print(_json.dumps(summary, indent=2, sort_keys=True))

    if not args.dry_run:
        try:
            if not db_health.check(db.DB_PATH):
                print("[backfill_reporting_dates] WARNING: post-run check failed.")
                return 4
            db_health.seal()
        except Exception as exc:
            print("[db_health] seal failed (non-fatal):", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
