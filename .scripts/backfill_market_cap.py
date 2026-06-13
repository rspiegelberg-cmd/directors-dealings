"""backfill_market_cap.py -- Enrich tickers_meta with shares_outstanding and
market_cap_gbp by scraping lse.co.uk SharePrice pages (B-137).

ZONE B -- writes to `.data/directors.db` and `.scripts/_mktcap_cache/`.
**Rupert runs this**; Claude never runs it from bash.

What it does
------------
For each ticker held in tickers_meta (or a supplied list), fetches the LSE
SharePrice page and parses the HTML data table which contains rows like:

    <th title="Shares in Issue for ...">Shares in Issue</th>
    <td>1.55<strong>b</strong></td>

    <th title="Market Capitalisation for ...">Market Cap</th>
    <td>£214.92<strong>b</strong></td>

Both values are written back to tickers_meta.shares_outstanding (INTEGER,
estimated from the abbreviated figure) and tickers_meta.market_cap_gbp
(REAL, pounds).

Why lse.co.uk
-------------
Yahoo Finance v8 returns no marketCap/sharesOutstanding for .L symbols.
lse.co.uk SharePrice pages carry both values in the HTML meta-description tag
and are freely accessible without authentication.

Cache
-----
Responses are cached under `.scripts/_mktcap_cache/<TICKER>.html` with a
7-day TTL. Market cap doesn't change daily, so a weekly refresh is sufficient.

Run:
    python .scripts\\backfill_market_cap.py
    python .scripts\\backfill_market_cap.py --tickers AZN HSBA LLOY
    python .scripts\\backfill_market_cap.py --force          # ignore cache TTL
    python .scripts\\backfill_market_cap.py --dry-run        # no DB writes
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.lse.co.uk/SharePrice.html"
CACHE_DIR = HERE / "_mktcap_cache"
CACHE_TTL_SECONDS = 7 * 24 * 3600   # 7 days
USER_AGENT = (
    "DirectorsDealingsBot/1.0 (personal research project; "
    "contact rspiegelberg@gmail.com)"
)
REQUEST_DELAY_SECONDS = 1.5   # polite gap between live fetches
MAX_RETRIES = 3

# Regex for the numeric+suffix pattern found in <td> cells on the LSE page.
# Examples matched: "1.55b", "214.92b", "57.78b", "500m", "1,234.5"
# The suffix is captured separately and may be absent (raw number).
_NUM_SUFFIX_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s*([BMbm]?)$")


# ---------------------------------------------------------------------------
# Pure helpers (no network, no DB -- unit-tested)
# ---------------------------------------------------------------------------

def parse_market_cap_gbp(raw_value: str, suffix: str) -> float | None:
    """Convert scraped numeric + suffix to a float GBP value.

    Args:
        raw_value: the numeric part as a string, e.g. '1.23' or '456.7'
        suffix:    'B', 'b', 'M', 'm', or '' (empty = raw pounds)

    Returns float GBP or None on parse failure.

    Examples:
        parse_market_cap_gbp('1.23', 'B')   -> 1_230_000_000.0
        parse_market_cap_gbp('456.7', 'm')  -> 456_700_000.0
        parse_market_cap_gbp('5000000', '') -> 5_000_000.0
    """
    if not raw_value:
        return None
    try:
        n = float(raw_value.replace(",", ""))
    except ValueError:
        return None
    s = (suffix or "").strip().upper()
    if s == "B":
        return n * 1_000_000_000
    if s == "M":
        return n * 1_000_000
    return n


def _parse_th_td_value(soup, th_title_fragment: str) -> str | None:
    """Find a <th> whose title contains th_title_fragment and return its <td> text.

    The LSE page uses a pattern like:
        <th title="Shares in Issue for Company">Shares in Issue</th>
        <td>1.55<strong>b</strong></td>

    We locate the <th> by a substring of its title attribute, then walk to the
    sibling <td> in the same <tr> and concatenate all text nodes (including
    those inside <strong>). Returns None if not found.
    """
    th = soup.find("th", title=re.compile(th_title_fragment, re.IGNORECASE))
    if not th:
        return None
    tr = th.find_parent("tr")
    if not tr:
        return None
    td = tr.find("td")
    if not td:
        return None
    # get_text() collapses "1.55<strong>b</strong>" -> "1.55b"
    return td.get_text(strip=True) or None


def _detect_is_aim(soup) -> bool:
    """Return True if the LSE SharePrice page indicates the stock is AIM-listed.

    Detection strategy: look for a <p class="sp-share-details__text"> paragraph
    that contains a link to the FTSE AIM All-Share index page.  This is the
    canonical indicator used by lse.co.uk, e.g.:

        <p class="sp-share-details__text">
          Metals One is listed in the
          <a href="https://www.lse.co.uk/share-prices/indices/ftse-aim-all-share/">
            FTSE AIM All-Share
          </a> index.
        </p>

    For Main Market stocks the paragraph links to ftse-all-share / ftse-100
    etc. -- never ftse-aim-all-share.

    Returns False (not True) when the page has no such paragraph (e.g. the
    page was not found, or the stock is delisted).
    """
    for p in soup.find_all("p", class_="sp-share-details__text"):
        for a in p.find_all("a", href=True):
            if "ftse-aim-all-share" in a["href"].lower():
                return True
    return False


def parse_lse_page(html: str) -> tuple[int | None, float | None, bool]:
    """Extract (shares_outstanding, market_cap_gbp, is_aim) from an LSE page.

    Parses the HTML data table which has <th title="Shares in Issue for ...">
    and <th title="Market Capitalisation for ..."> rows. Values in the <td>
    are abbreviated (e.g. "1.55b", "214.92b") with the suffix inside a
    <strong> tag.

    Also detects whether the stock is AIM-listed via the index-membership
    paragraph (see _detect_is_aim).

    Returns (None, None, False) if neither financial value is found. Does not
    raise.

    shares_outstanding is stored as an integer estimate:
        "1.55b" -> 1_550_000_000
        "500m"  -> 500_000_000

    Args:
        html: raw HTML of the LSE SharePrice page.

    Returns:
        (shares_outstanding as int or None, market_cap_gbp as float or None,
         is_aim as bool)
    """
    # Lazy import so pure helpers can be tested without BeautifulSoup installed.
    from bs4 import BeautifulSoup  # noqa: PLC0415

    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001
        return None, None, False

    shares: int | None = None
    market_cap: float | None = None

    # --- Shares in Issue ---
    raw_shares = _parse_th_td_value(soup, r"Shares in Issue")
    if raw_shares:
        m = _NUM_SUFFIX_RE.match(raw_shares)
        if m:
            val = parse_market_cap_gbp(m.group(1), m.group(2))
            if val is not None:
                shares = int(round(val))

    # --- Market Cap (strip leading £ / currency symbol) ---
    raw_cap = _parse_th_td_value(soup, r"Market Cap")
    if raw_cap:
        raw_cap = raw_cap.lstrip("£$€GBPgbp ")
        m = _NUM_SUFFIX_RE.match(raw_cap.strip())
        if m:
            market_cap = parse_market_cap_gbp(m.group(1), m.group(2))

    # --- AIM detection ---
    is_aim = _detect_is_aim(soup)

    return shares, market_cap, is_aim


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _slug_variant(ticker: str) -> str:
    """Return the alternate lse.co.uk slug for a ticker (B-169).

    lse.co.uk is inconsistent about trailing dots in EPIC codes: some pages
    resolve only with the dot ('TW.'), others only without ('TW'). The
    canonical DB ticker may be either form, so the variant is:

        'TW.'  -> 'TW'   (strip trailing dot)
        'NYCE' -> 'NYCE.' (append dot)
    """
    return ticker[:-1] if ticker.endswith(".") else ticker + "."


def _cache_path(ticker: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ticker)
    return CACHE_DIR / f"{safe}.html"


def fetch_lse_page(ticker: str, *, force: bool = False) -> str:
    """Fetch the LSE SharePrice page for `ticker`, with disk cache.

    Caches HTML under `.scripts/_mktcap_cache/<ticker>.html` for CACHE_TTL_SECONDS.
    Set force=True to ignore the cache and always fetch live.

    Retries up to MAX_RETRIES times with back-off. Raises RuntimeError on
    permanent failure.
    """
    import requests  # lazy import -- pure helpers don't need it

    cache = _cache_path(ticker)
    if not force and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            return cache.read_text(encoding="utf-8", errors="replace")

    url = f"{BASE_URL}?shareprice={ticker}"
    headers = {"User-Agent": USER_AGENT}
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            html = resp.text
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(html, encoding="utf-8")
            time.sleep(REQUEST_DELAY_SECONDS)
            return html
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # B-169: a 404 is permanent for this slug -- fail fast so the
            # caller can try the dot-variant slug instead of burning the
            # remaining back-off retries on the same dead URL.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                break
            time.sleep(REQUEST_DELAY_SECONDS * attempt)
    raise RuntimeError(f"LSE fetch failed for {ticker}: {last_exc}")


def fetch_lse_page_with_variants(ticker: str, *, force: bool = False) -> tuple[str, str]:
    """Fetch the LSE page for `ticker`, retrying once with the dot-variant slug.

    B-169: tries the canonical slug first; if that fetch fails (404 or
    exhausted retries) tries `_slug_variant(ticker)` once. Max 2 slug
    attempts per ticker. Politeness delays are handled inside
    fetch_lse_page().

    Returns (html, slug_used). Raises RuntimeError if both slugs fail.
    """
    try:
        return fetch_lse_page(ticker, force=force), ticker
    except RuntimeError:
        variant = _slug_variant(ticker)
        html = fetch_lse_page(variant, force=force)   # may raise RuntimeError
        print(f"[retry] {ticker} -> {variant}")
        return html, variant


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def load_tickers(
    conn,
    tickers_override: list[str] | None = None,
    *,
    missing_only: bool = False,
) -> list[str]:
    """Return the list of tickers to process.

    If tickers_override is supplied, use that list (filtered to ones present
    in tickers_meta to guard against typos). Otherwise return all tickers in
    tickers_meta that are not excluded issuers.

    missing_only: if True, restrict to tickers where market_cap_gbp IS NULL
        (used by --missing-only CLI flag in refresh_all pipeline so daily runs
        only hit LSE for genuinely new/unclassified companies).
    """
    if tickers_override:
        rows = conn.execute(
            "SELECT ticker FROM tickers_meta WHERE ticker IN ({})".format(
                ",".join("?" * len(tickers_override))
            ),
            tickers_override,
        ).fetchall()
        return [r["ticker"] for r in rows]

    extra = "AND market_cap_gbp IS NULL " if missing_only else ""
    rows = conn.execute(
        "SELECT ticker FROM tickers_meta "
        "WHERE COALESCE(is_excluded_issuer, 0) != 1 "
        + extra +
        "ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


_SMALL_CAP_THRESHOLD_GBP = 300_000_000  # matches small_cap flag (Sprint 54 migration 011)


def update_ticker(conn, ticker: str, shares: int | None,
                  market_cap: float | None,
                  is_aim: bool = False) -> None:
    """Write shares_outstanding, market_cap_gbp, is_aim, and benchmark_symbol.

    lse.co.uk is the authoritative source for listing venue classification.
    Benchmark assignment:
      - is_aim=True                           -> ^FTSC  (AIM proxy)
      - is_aim=False, market_cap < £300M      -> ^FTSC  (Main Market small-cap)
      - is_aim=False, market_cap >= £300M     -> ^FTAS  (large/mid-cap All-Share)
      - is_aim=False, market_cap unknown      -> ^FTAS  (safe fallback)

    ^FTSC is the right benchmark for ANY small-cap (whether AIM or Main Market)
    because the size effect would show as systematic alpha vs the All-Share.
    Main Market small-caps sit in the FTSE Small Cap index by definition.

    This overrides Yahoo Finance false positives (e.g. a FTSE-250 stock
    misclassified as AIM) and clears any stale '^AIM' entries left from
    before that ticker was delisted on Yahoo (B-147 / DIR-74).

    Only called when the lse.co.uk page was successfully fetched and parsed
    (see run() -- fetch errors skip this call entirely), so a temporary
    page-unavailability cannot incorrectly demote a real AIM ticker.
    """
    is_small = (market_cap is not None and market_cap < _SMALL_CAP_THRESHOLD_GBP)
    benchmark = "^FTSC" if (is_aim or is_small) else "^FTAS"
    aim_flag = 1 if is_aim else 0
    conn.execute(
        "UPDATE tickers_meta "
        "SET shares_outstanding = ?, market_cap_gbp = ?, "
        "    is_aim = ?, benchmark_symbol = ? "
        "WHERE ticker = ?",
        (shares, market_cap, aim_flag, benchmark, ticker),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core run logic
# ---------------------------------------------------------------------------

def run(
    tickers_override: list[str] | None = None,
    *,
    force: bool = False,
    dry_run: bool = False,
    missing_only: bool = False,
    conn=None,
) -> dict:
    """Scrape LSE and enrich tickers_meta. Returns a stats dict.

    Args:
        tickers_override: if given, only process these tickers.
        force:       ignore cache TTL, re-fetch every page.
        dry_run:     parse and print results but write nothing to the DB.
        missing_only: only process tickers where market_cap_gbp IS NULL
            (used by the refresh_all pipeline to classify new arrivals cheaply).
        conn:        optional open DB connection (used by tests).
    """
    own_conn = conn is None
    if own_conn:
        conn = db.connect()
    try:
        tickers = load_tickers(conn, tickers_override, missing_only=missing_only)
        total = len(tickers)
        print(f"[mktcap] Processing {total} ticker(s)"
              + (" (dry-run)" if dry_run else "")
              + (" (force)" if force else "")
              + (" (missing-only)" if missing_only else ""))

        n_scraped = 0
        n_shares = 0
        n_cap = 0
        n_missing = 0
        n_aim = 0

        for ticker in tickers:
            try:
                html, slug_used = fetch_lse_page_with_variants(ticker, force=force)
                n_scraped += 1
            except RuntimeError as exc:
                print(f"[ERR] {ticker}: {exc}")
                n_missing += 1
                continue

            shares, market_cap, is_aim = parse_lse_page(html)

            # B-169: canonical slug returned a page with no data table --
            # try the dot-variant slug once before declaring a miss.
            if shares is None and market_cap is None and slug_used == ticker:
                variant = _slug_variant(ticker)
                try:
                    v_html = fetch_lse_page(variant, force=force)
                    v_shares, v_cap, v_aim = parse_lse_page(v_html)
                    if v_shares is not None or v_cap is not None:
                        print(f"[retry] {ticker} -> {variant}")
                        shares, market_cap, is_aim = v_shares, v_cap, v_aim
                except RuntimeError:
                    pass

            if shares is None and market_cap is None:
                print(f"[MISS] {ticker} (data not found in page)")
                n_missing += 1
            else:
                cap_str = "n/a"
                if market_cap is not None:
                    if market_cap >= 1_000_000_000:
                        cap_str = f"GBP{market_cap / 1_000_000_000:.2f}B"
                    else:
                        cap_str = f"GBP{market_cap / 1_000_000:.1f}M"
                shares_str = f"{shares:,}" if shares is not None else "n/a"
                aim_str = " [AIM]" if is_aim else ""
                print(f"[OK] {ticker} shares={shares_str} cap={cap_str}{aim_str}")
                if shares is not None:
                    n_shares += 1
                if market_cap is not None:
                    n_cap += 1
                if is_aim:
                    n_aim += 1
                if not dry_run:
                    update_ticker(conn, ticker, shares, market_cap, is_aim=is_aim)

        summary = {
            "total": total,
            "scraped": n_scraped,
            "shares_populated": n_shares,
            "cap_populated": n_cap,
            "aim_detected": n_aim,
            "missing": n_missing,
        }
        print(
            f"[mktcap] Done: {n_scraped}/{total} scraped, "
            f"{n_shares} shares_outstanding populated, "
            f"{n_cap} market_cap_gbp populated, "
            f"{n_aim} AIM detected, "
            f"{n_missing} missing/error"
        )
        return summary
    finally:
        if own_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Backfill tickers_meta.shares_outstanding and market_cap_gbp "
            "from lse.co.uk SharePrice pages (B-137)."
        )
    )
    ap.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="process only these tickers (space-separated); "
             "default is all tickers in tickers_meta",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="ignore cache TTL and re-scrape every ticker",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="fetch and parse but do not write to the DB",
    )
    ap.add_argument(
        "--missing-only", action="store_true",
        help="only process tickers where market_cap_gbp IS NULL "
             "(used by refresh_all to classify new companies cheaply)",
    )
    args = ap.parse_args(argv)
    run(
        tickers_override=args.tickers,
        force=args.force,
        dry_run=args.dry_run,
        missing_only=args.missing_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
