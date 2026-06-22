"""backfill_lse_diary.py — forward earnings dates from the LSE Financial Diary (B-111).

ZONE B — writes to `.data/directors.db` and `.scripts/_lse_diary_cache/`.
**Rupert runs this**; Claude never runs it from bash.

What it does
------------
Scrapes the London South East ("lse.co.uk") Financial Diary — a free, forward-
looking, date-addressable results calendar — and writes upcoming RESULTS dates
for tickers we already hold into the `reporting_dates` table so the 60-day
pre-results badge lights up.

Source page (month view — one fetch covers a whole month):
    https://www.lse.co.uk/share-prices/financial-diary.html?selected-date=DD-Mon-YYYY&mode=month

Page structure (verified against `.scripts/fixtures/lse_diary_sample.html`):
    div.financial-diary__section
      h3.financial-diary__title                -> event type, e.g. "Final Results"
      table.financial-diary__table
        tr
          td.financial-diary__table-date       -> "01-Jun-2026"
          td > a[href*="shareprice=BLOE"]       -> company name
          td > a[href*="shareprice=BLOE"]       -> TIDM

Decisions (locked 2026-06-05):
  * Cadence: once a day (a scheduled daily run; see --months for the horizon).
  * report_type map (only RESULTS-type sections are kept; everything else —
    AGM/EGM/GM, dividends, ex-dividends, annual reports, economic announcements —
    is ignored):
        Final Results            -> PRELIM
        Interim Results          -> INTERIM
        Q1/Q2/Q3/Q4 Results      -> QUARTERLY
        Trading Announcement      -> TRADING_STMT
        Interim Management Statement -> TRADING_STMT
  * confidence = 'confirmed' for every row this scraper writes. The synthetic
    "(est)" gap-filler lives in the separate backfill_expected_reporting_dates.py
    and writes confidence='est'.
  * Replace-on-rerun: each run deletes all prior source='lse_diary' rows and
    re-inserts, so dropped/changed diary events don't linger. yahoo/est rows are
    left untouched.

Politeness: identified User-Agent, rate-limited, retry/backoff, short cache TTL
(~24h) under `.scripts/_lse_diary_cache/`.

Run:
    python .scripts\\backfill_lse_diary.py            # current + next 6 months (default)
    python .scripts\\backfill_lse_diary.py --months 9 # extend the horizon further
    python .scripts\\backfill_lse_diary.py --dry-run  # parse + report, write nothing
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

# --- Constants --------------------------------------------------------------

BASE_URL = "https://www.lse.co.uk/share-prices/financial-diary.html"
SOURCE = "lse_diary"
CONFIDENCE = "confirmed"

# When a confirmed LSE date lands within this many days of a source='est' date
# for the same ticker, the estimate is stale and the confirmed date wins.
# Deleted immediately so the dashboard never shows both at once.
NEARBY_EST_DAYS = 21
CACHE_DIR = HERE / "_lse_diary_cache"
CACHE_TTL_SECONDS = 24 * 3600
USER_AGENT = (
    "DirectorsDealingsBot/1.0 (personal research project; "
    "contact rspiegelberg@gmail.com)"
)
REQUEST_DELAY_SECONDS = 2.0   # polite gap between live fetches
MAX_RETRIES = 3

# Month abbreviations used by the diary date cells ("01-Jun-2026").
_DATE_RE = re.compile(r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s*$")
_SHAREPRICE_RE = re.compile(r"[?&]shareprice=([^&]+)", re.IGNORECASE)
_Q_RE = re.compile(r"^q[1-4]\s+results$")


# --- Pure helpers (no network / no DB — unit-tested) ------------------------

def report_type_for_title(title: str) -> str | None:
    """Map an LSE Diary section title to our `report_type`, or None to ignore.

    Only results-type sections are kept. Dividends, AGMs, economic
    announcements, annual reports, etc. return None.
    """
    t = (title or "").strip().lower()
    if t == "final results":
        return "PRELIM"
    if t == "interim results":
        return "INTERIM"
    if _Q_RE.match(t):
        return "QUARTERLY"
    if t in ("trading announcement", "trading announcements",
             "trading statement", "trading update",
             "interim management statement"):
        return "TRADING_STMT"
    return None


def to_iso_date(diary_date: str) -> str | None:
    """'01-Jun-2026' -> '2026-06-01'. Returns None on an unparseable string."""
    m = _DATE_RE.match(diary_date or "")
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
                               "%d-%b-%Y")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d")


def tidm_from_href(href: str) -> str | None:
    """Extract the TIDM from a SharePrice link's `shareprice=` query param."""
    if not href:
        return None
    m = _SHAREPRICE_RE.search(href)
    return m.group(1).strip().upper() if m else None


def normalise_tidm(tidm: str) -> str:
    """Canonical form for matching against held tickers: upper-cased, trimmed.

    The dot-stripped variant is produced separately by `ticker_match_keys` so
    awkward cases (e.g. '.' suffixes) can be compared both ways.
    """
    return (tidm or "").strip().upper()


def ticker_match_keys(tidm: str) -> list[str]:
    """Candidate keys to match a diary TIDM against our held-ticker set.

    Returns the normalised TIDM plus a dot-stripped variant (e.g. 'BT.A' ->
    also 'BTA') so we tolerate '.'-suffix differences between the diary and our
    DB. Dual-line / preference-code aliases that don't reduce to one of these
    are intentionally left to the unmatched-log coverage audit rather than
    guessed at.
    """
    n = normalise_tidm(tidm)
    keys = [n]
    nodot = n.replace(".", "")
    if nodot and nodot != n:
        keys.append(nodot)
    return keys


def parse_diary_html(html: str) -> list[dict]:
    """Parse one LSE Diary month page into results events.

    Returns a list of {report_date, tidm, report_type, company}. Only
    results-type sections are included (see report_type_for_title); AGM,
    dividend, ex-dividend, economic and other rows are dropped. Rows with an
    unparseable date or no TIDM are skipped.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    events: list[dict] = []
    for section in soup.select(".financial-diary__section"):
        title_el = section.select_one(".financial-diary__title")
        if not title_el:
            continue
        rtype = report_type_for_title(title_el.get_text(strip=True))
        if rtype is None:
            continue
        table = section.select_one("table.financial-diary__table")
        if not table:
            continue
        for tr in table.select("tr"):
            date_td = tr.select_one("td.financial-diary__table-date")
            if not date_td:
                continue
            iso = to_iso_date(date_td.get_text(strip=True))
            if not iso:
                continue
            # Each row has two links (company, then TIDM), both carrying the
            # same `shareprice=` param. Take the TIDM from the param and the
            # company from the first link's text (positional — robust even when
            # the company's display name equals its TIDM, e.g. "Lpa"/LPA).
            links = tr.select("a[href]")
            tidm = None
            for a in links:
                t = tidm_from_href(a.get("href", ""))
                if t:
                    tidm = t
                    break
            if not tidm:
                continue
            company = links[0].get_text(strip=True) if links else ""
            events.append({
                "report_date": iso,
                "tidm": tidm,
                "report_type": rtype,
                "company": company or "",
            })
    return events


# --- Network ----------------------------------------------------------------

def month_anchor_dates(today: date, months_ahead: int) -> list[date]:
    """First-of-month anchor dates: current month + the next `months_ahead`."""
    anchors = []
    y, m = today.year, today.month
    for _ in range(months_ahead + 1):
        anchors.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return anchors


def month_url(anchor: date) -> str:
    return (f"{BASE_URL}?selected-date={anchor.strftime('%d-%b-%Y')}"
            f"&mode=month")


def _cache_path(anchor: date) -> Path:
    return CACHE_DIR / f"{anchor.strftime('%Y-%m')}.html"


def fetch_month_html(anchor: date, *, use_cache: bool = True) -> str:
    """Fetch one month page politely, with a short-TTL on-disk cache.

    requests is imported lazily so the pure parser can be unit-tested without
    the dependency installed.
    """
    cache = _cache_path(anchor)
    if use_cache and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            return cache.read_text(encoding="utf-8", errors="replace")

    import requests  # lazy

    url = month_url(anchor)
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            html = resp.text
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(html, encoding="utf-8")
            time.sleep(REQUEST_DELAY_SECONDS)
            return html
        except Exception as exc:  # noqa: BLE001 — retry/backoff
            last_exc = exc
            time.sleep(REQUEST_DELAY_SECONDS * attempt)
    raise RuntimeError(f"LSE Diary fetch failed for {url}: {last_exc}")


# --- DB write ---------------------------------------------------------------

def _delete_nearby_estimates(conn, rows: list[tuple],
                              nearby_days: int = NEARBY_EST_DAYS) -> int:
    """Delete source='est' rows within nearby_days of any newly confirmed date.

    Called immediately after LSE diary rows are written so the dashboard never
    shows a stale estimate alongside its confirmed counterpart.

    rows is the list of (ticker, report_date, report_type, source, fetched_at,
    confidence) tuples that were just inserted.  Returns count of rows deleted.
    """
    deleted = 0
    for row in rows:
        ticker, report_date = row[0], row[1]
        try:
            rd = date.fromisoformat(report_date)
        except (TypeError, ValueError):
            continue
        low  = (rd - timedelta(days=nearby_days)).isoformat()
        high = (rd + timedelta(days=nearby_days)).isoformat()
        cur = conn.execute(
            "DELETE FROM reporting_dates "
            "WHERE source='est' AND ticker=? AND report_date BETWEEN ? AND ?",
            (ticker, low, high),
        )
        deleted += cur.rowcount
    return deleted


def load_held_tickers(conn) -> set[str]:
    """Distinct tickers we hold (from transactions)."""
    rows = conn.execute("SELECT DISTINCT ticker FROM transactions").fetchall()
    # B-179: index by column name (r["ticker"]) not position (r[0]) so this
    # works on both sqlite3.Row and Postgres dict_row.
    return {normalise_tidm(r["ticker"]) for r in rows if r and r["ticker"]}


def write_reporting_dates(conn, events: list[dict], held: set[str],
                          *, dry_run: bool = False) -> dict:
    """Store ALL diary events (replace-on-rerun); use held only for coverage reporting.

    We no longer filter to held tickers. Every event from the LSE diary is
    stored so that a ticker appearing in transactions for the first time already
    has its earnings date ready — no 24-hour gap.

    'held' is used purely to report how many of our current transactions tickers
    have a future earnings date in the diary, and to canonicalise dot-variant
    tickers (e.g. BT.A <-> BTA) where we have a known canonical form.

    Returns a stats dict. Does not commit when dry_run.
    """
    held_nodot = {t.replace(".", ""): t for t in held}
    fetched_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    all_rows: list[tuple] = []
    held_covered: set[str] = set()   # diary tickers that overlap with held

    for ev in events:
        # Canonicalise against held if possible; store regardless of match.
        canonical = ev["tidm"]
        for key in ticker_match_keys(ev["tidm"]):
            if key in held:
                canonical = key
                held_covered.add(key)
                break
            if key in held_nodot:
                canonical = held_nodot[key]
                held_covered.add(canonical)
                break

        all_rows.append((
            canonical, ev["report_date"], ev["report_type"],
            SOURCE, fetched_at, CONFIDENCE,
        ))

    current_tickers = {r[0] for r in all_rows}
    not_yet_held = current_tickers - held   # in diary but not in transactions yet

    if not dry_run:
        # Capture prior coverage before replacing so we can report net-new.
        prior_rows = conn.execute(
            "SELECT DISTINCT ticker FROM reporting_dates WHERE source = ?",
            (SOURCE,),
        ).fetchall()
        # B-179: index by name not position (Postgres dict_row has no r[0]).
        prior_tickers = {r["ticker"] for r in prior_rows}

        # Replace-on-rerun: drop our own prior rows only, then insert fresh.
        conn.execute("DELETE FROM reporting_dates WHERE source = ?", (SOURCE,))
        # B-179: INSERT OR REPLACE (SQLite) <-> ON CONFLICT DO UPDATE (Postgres)
        # on PK (ticker, report_date, report_type). The prior DELETE clears our
        # own source rows first, so any in-batch conflict is a true duplicate and
        # the DO UPDATE refreshes source/fetched_at/confidence — behaviour parity.
        if db.backend() == "postgres":
            _rd_sql = (
                "INSERT INTO reporting_dates "
                "(ticker, report_date, report_type, source, fetched_at, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (ticker, report_date, report_type) DO UPDATE SET "
                "source = excluded.source, fetched_at = excluded.fetched_at, "
                "confidence = excluded.confidence"
            )
        else:
            _rd_sql = (
                "INSERT OR REPLACE INTO reporting_dates "
                "(ticker, report_date, report_type, source, fetched_at, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
        conn.executemany(_rd_sql, all_rows)
        # Immediately remove any stale source='est' rows that are now shadowed
        # by a confirmed LSE date within NEARBY_EST_DAYS days for the same
        # ticker. This closes the staleness window between diary and estimate
        # script runs (Rupert 2026-06-08).
        _delete_nearby_estimates(conn, all_rows)
        conn.commit()

        net_new = sorted(current_tickers - prior_tickers)
        dropped  = sorted(prior_tickers - current_tickers)
    else:
        prior_tickers = set()
        net_new = []
        dropped = []

    return {
        "parsed": len(events),
        "stored": len(all_rows),
        "written": 0 if dry_run else len(all_rows),
        "held_tickers": len(held),
        "held_with_future_date": len(held_covered),
        "not_yet_held": len(not_yet_held),
        "net_new": len(net_new),
        "net_new_tickers": net_new,
        "dropped": len(dropped),
        "dropped_tickers": dropped,
    }


# --- CLI --------------------------------------------------------------------

def run(months_ahead: int = 6, *, dry_run: bool = False,
        use_cache: bool = True, conn=None) -> dict:
    """Scrape `months_ahead` months and write results dates. Returns stats."""
    today = date.today()
    own_conn = conn is None
    if own_conn:
        conn = db.connect()
    try:
        held = load_held_tickers(conn)
        all_events: list[dict] = []
        for anchor in month_anchor_dates(today, months_ahead):
            html = fetch_month_html(anchor, use_cache=use_cache)
            evs = parse_diary_html(html)
            # keep only today-or-future events (the badge only reads >= today)
            evs = [e for e in evs if e["report_date"] >= today.isoformat()]
            all_events.extend(evs)
            print(f"[lse_diary] {anchor:%Y-%m}: {len(evs)} future results events")
        stats = write_reporting_dates(conn, all_events, held, dry_run=dry_run)
        print(f"[lse_diary] parsed={stats['parsed']} stored={stats['stored']} "
              f"written={stats['written']}")
        print(f"[lse_diary] held_coverage={stats['held_with_future_date']}/"
              f"{stats['held_tickers']} "
              f"not_yet_held={stats['not_yet_held']} "
              f"net_new={stats['net_new']} dropped={stats['dropped']}")
        if stats["net_new_tickers"]:
            print(f"[lse_diary] newly in diary: {', '.join(stats['net_new_tickers'])}")
        if stats["dropped_tickers"]:
            print(f"[lse_diary] dropped from diary: {', '.join(stats['dropped_tickers'])}")
        return stats
    finally:
        if own_conn:
            conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill forward earnings dates "
                                             "from the LSE Financial Diary (B-111).")
    ap.add_argument("--months", type=int, default=6,
                    help="how many months ahead of the current month to scrape "
                         "(default 6 -> current + next 6, ~7 month-fetches). The "
                         "60-day badge needs only ~2 months, but a longer horizon "
                         "gives runway for forward signals and is cheap.")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report coverage but write nothing.")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the on-disk cache and re-fetch every month.")
    args = ap.parse_args(argv)
    run(months_ahead=args.months, dry_run=args.dry_run,
        use_cache=not args.no_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
