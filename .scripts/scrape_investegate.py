"""Stage 2 Investegate scraper — polite, stdlib-only.

Walks the Director-Deals index page and (for the multi-year backfill)
the `/advanced-search/draw` endpoint with the Director's-Dealings
category filter. Caches each filing's HTML at
`.scripts/_scrape_cache/{rns_id}.html`. Tracks progress at
`.scripts/_scrape_progress.json`.

Public surface:
    check_robots()                -- one-shot robots.txt gate.
    iter_index(start, end)        -- yields lightweight filing rows
                                     from the live index, newest-first.
    iter_archive(start, end)      -- walks /advanced-search/draw in 30-day
                                     descending chunks (newest-first) so
                                     an aborted backfill leaves the most
                                     recent filings already processed.
    fetch_filing(rns_id, url)     -- returns Path to cached HTML.
    load_cached(rns_id)           -> HTML text or None.
    update_progress(...) / load_progress()

Errors:
    FetchError, RateLimitError, RobotsBlockedError.

All sleeps are 0.6-1.0s (mean 0.8s) jittered, per D-POLITE.
"""
from __future__ import annotations

import gzip
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# --- Constants --------------------------------------------------------------

USER_AGENT = (
    "DirectorsDealings-Research/0.3 "
    "(+contact: rspiegelberg@gmail.com; personal research tool)"
)
BASE_URL = "https://www.investegate.co.uk"
INDEX_URL = f"{BASE_URL}/category/directors-dealings"
ARCHIVE_URL = f"{BASE_URL}/announcement-archive"
ADVANCED_SEARCH_URL = f"{BASE_URL}/advanced-search/draw"
ROBOTS_URL = f"{BASE_URL}/robots.txt"

# Investegate Advanced-Search category code for "Director's Dealings"
# (confirmed 2026-05-13 from the Advanced Search <select name="categories">).
CATEGORY_DIRECTORS_DEALINGS = "16"

POLITE_SLEEP_RANGE = (0.6, 1.0)  # seconds
BACKOFF_SCHEDULE = (30, 60, 120)  # 3 retries

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".scripts" / "_scrape_cache"
PROGRESS_PATH = ROOT / ".scripts" / "_scrape_progress.json"


# --- Errors -----------------------------------------------------------------

class FetchError(Exception):
    pass


class RateLimitError(Exception):
    pass


class RobotsBlockedError(Exception):
    pass


class ArchiveCalibrationError(Exception):
    """Raised when the archive page structure is unclear and we'd
    rather fail loud than scrape silently-wrong data.
    """


# --- Polite fetch -----------------------------------------------------------

def _polite_sleep() -> None:
    time.sleep(random.uniform(*POLITE_SLEEP_RANGE))


def _decode_response(resp) -> str:
    raw = resp.read()
    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
        raw = gzip.decompress(raw)
    # Charset sniff: header > meta > utf-8 fallback > latin-1 last resort
    ct = resp.headers.get("Content-Type", "") or ""
    m = re.search(r"charset=([\w\-]+)", ct, re.IGNORECASE)
    enc = m.group(1) if m else None
    if not enc:
        head = raw[:2048].decode("ascii", errors="ignore")
        m = re.search(r'<meta[^>]+charset=["\']?([\w\-]+)', head, re.IGNORECASE)
        enc = m.group(1) if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _fetch(url: str, retries: int = 3, extra_headers: dict | None = None) -> str:
    """Polite GET. Returns decoded text. Raises on persistent failure.

    `extra_headers` lets callers add things like X-Requested-With for the
    advanced-search AJAX endpoint without touching the default header set.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    attempts = 0
    last_err = None
    while attempts <= retries:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                _polite_sleep()
                return _decode_response(resp)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503) and attempts < len(BACKOFF_SCHEDULE):
                time.sleep(BACKOFF_SCHEDULE[attempts])
                attempts += 1
                continue
            raise FetchError(f"HTTP {e.code} for {url}: {e.reason}") from e
        except urllib.error.URLError as e:
            last_err = e
            raise FetchError(f"network error for {url}: {e.reason}") from e
        except Exception as e:
            last_err = e
            raise FetchError(f"unexpected error for {url}: {e}") from e
        finally:
            attempts += 1
    raise RateLimitError(
        f"persistent rate-limit/timeout for {url}: {last_err}"
    )


# --- Robots gate ------------------------------------------------------------

_ROBOTS_RETRY_BACKOFF = (5, 15, 30)  # 3 retries, ~50s total max wait
_ROBOTS_RETRY_HTTP_CODES = {408, 429, 500, 502, 503, 504}


def check_robots(target_path: str = "/category/directors-dealings") -> None:
    """Fetch /robots.txt and refuse to crawl if disallowed.

    Retries on transient network errors (DNS hiccup, 5xx, timeout) with
    short backoff before giving up. Does NOT retry on hard refusals
    (4xx other than 408/429) or on RobotsBlockedError — those are
    deterministic. Total wait before final give-up is ~50s, which is
    short enough not to interfere with the daily refresh budget but
    long enough to ride out the typical transient blip.
    """
    rp = urllib.robotparser.RobotFileParser()
    last_err: Exception | None = None
    for attempt in range(len(_ROBOTS_RETRY_BACKOFF) + 1):
        try:
            req = urllib.request.Request(ROBOTS_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                txt = resp.read().decode("utf-8", errors="replace")
            rp.parse(txt.splitlines())
            last_err = None
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in _ROBOTS_RETRY_HTTP_CODES and attempt < len(_ROBOTS_RETRY_BACKOFF):
                time.sleep(_ROBOTS_RETRY_BACKOFF[attempt])
                continue
            raise FetchError(f"robots.txt unreachable: {e}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < len(_ROBOTS_RETRY_BACKOFF):
                time.sleep(_ROBOTS_RETRY_BACKOFF[attempt])
                continue
            raise FetchError(f"robots.txt unreachable: {e}") from e
        except Exception as e:
            raise FetchError(f"robots.txt unreachable: {e}") from e
    if last_err is not None:
        # Defensive: loop exited without success and without raising.
        raise FetchError(f"robots.txt unreachable: {last_err}") from last_err
    if not rp.can_fetch(USER_AGENT, BASE_URL + target_path):
        raise RobotsBlockedError(
            f"robots.txt disallows {target_path} for {USER_AGENT!r}"
        )


# --- Cache + progress -------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def fetch_filing(rns_id: str, url: str) -> Path:
    """Return Path to cached HTML. Fetches if not cached."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{rns_id}.html"
    if cache_path.exists():
        return cache_path
    html = _fetch(url)
    _atomic_write(cache_path, html)
    return cache_path


def load_cached(rns_id: str) -> str | None:
    p = CACHE_DIR / f"{rns_id}.html"
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return None


def update_progress(rns_id: str, window_start: str, window_end: str) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_rns_id": rns_id,
        "last_run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": window_start,
        "window_end": window_end,
    }
    _atomic_write(PROGRESS_PATH, json.dumps(payload, indent=2))


def load_progress() -> dict | None:
    if PROGRESS_PATH.exists():
        try:
            return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


# --- Index parsing ----------------------------------------------------------

# Each filing row in the index table contains a link of the form:
#   https://www.investegate.co.uk/announcement/{rns|prn}/{slug}/{headline-slug}/{numeric-id}
# The href on Investegate is typically absolute. The captured group 1 is
# the full URL, group 2 the numeric rns_id, group 3 the headline anchor text.
# Match a filing link from ANY Primary Information Provider, not just RNS/PRN.
# Investegate routes PDMR filings through several PIPs — rns, prn, bzw (Business
# Wire), eqs (EQS Group), gnw (GlobeNewswire), mss, dgap, etc. The old
# `(?:rns|prn)` allow-list silently dropped BZW/EQS/GNW filings (e.g.
# TotalEnergies, Next 15, Magnum Ice Cream, M&G Credit) before fetch. We now
# accept any short alpha source segment; the trailing `/(\d+)` (numeric RNS id)
# keeps this anchored to real filing URLs only.
_FILING_LINK_RE = re.compile(
    r'href="((?:https?://www\.investegate\.co\.uk)?/announcement/[a-z]{2,8}/[^"]+/(\d+))"[^>]*>([^<]+)</a>',
    re.IGNORECASE,
)
_INDEX_ROW_RE = re.compile(
    r"<tr[^>]*>(.*?)</tr>",
    re.IGNORECASE | re.DOTALL,
)
# Capture the DATE token from an index-row timestamp, wherever it sits and
# however it's punctuated. Investegate renders it date-first space-separated
# ("02 Jun 2026 06:15 PM"); other sources/fixtures use dash-separated
# ("02-Jun-2026") and may lead with the time. We only need the "DD<sep>Mon<sep>
# YYYY" token — `.search` finds it regardless of a leading time, and the
# separator class accepts dash / slash / whitespace. (An earlier pattern was
# space-and-date-first only and missed dash-separated rows, so the window
# filter silently ran fail-open.)
_INDEX_TIME_RE = re.compile(
    r"(\d{1,2}[-/\s]+[A-Za-z]{3,9}[-/\s]+\d{4})",
    re.IGNORECASE,
)
# Fallback only: a parenthesised code in the display-name cell. Allows digits
# (e.g. MAB1) and prefers the LAST match because names can carry a "(DI)" /
# "(CDI)" suffix before the real code, e.g. "UIL Limited (DI) (UTL)".
_TICKER_CELL_RE = re.compile(r"\(([A-Z0-9]{2,5}\.?[A-Z]?)\)")

# ── Headline denylist (Phase 4, 2026-06-02 discovery-gap fix) ───────────────
# The /category/directors-dealings index is ALREADY a PDMR-only feed: every
# row on it is, by Investegate's own categorisation, a director-dealing
# announcement. The previous design re-filtered that already-filtered list
# with a closed allow-list of ~11 headline substrings, which silently dropped
# any PDMR filing worded differently ("Director Dealing", "Exercise of Share
# Options", "Conditional LTIP Awards", "Share Incentive Plan Purchase", bare
# "PDMR transaction notification", etc.) — ~1 in 6 of the day's dealings.
#
# Fix: trust the source's classification. KEEP every row by default and only
# DROP rows whose headline clearly matches a non-PDMR announcement type. The
# denylist is intentionally conservative and additive: a pattern is only added
# after it is seen to pull in genuine non-dealing noise. Over-capture is
# bounded — anything that slips through lands in the downstream ingest gate
# and routes to _pending_review.json, never the dashboard.
#
# IMPORTANT: keep these patterns tight. A loose pattern re-introduces the very
# class of silent loss we are removing. Each entry targets a clearly-non-PDMR
# announcement type. EBT share-purchase patterns (the original two) are kept.
# A "dealing hint" marks a headline that clearly concerns a director/PDMR
# dealing. When present, the conditional denylist below MUST NOT drop the row.
# NOTE: deliberately excludes the bare word "transaction" — several denylist
# phrases ("transaction in own shares") contain it, so including it here would
# make those guards self-cancel. The hint set keys on PDMR/director identity
# and dealing-instrument words only.
_PDMR_HINT_RE = re.compile(
    r"pdmr|director|pca|person\s+closely\s+associated|dealing|"
    r"shareholding|option|award|ltip|sip|vesting|grant|exercise",
    re.IGNORECASE,
)

# TIER 1 — UNCONDITIONAL drops. Announcement types that never describe a PDMR
# dealing even in a compound headline. Safe to drop outright.
_OFFSCOPE_ALWAYS = (
    # --- Original EBT patterns (employee benefit trust share purchases) ---
    re.compile(r"\bEBT\s+share\s+purchase\b", re.IGNORECASE),
    re.compile(r"market\s+purchase\s+of\s+shares\s+for\s+ebt", re.IGNORECASE),

    # --- NAV / net asset value (investment-trust housekeeping) -------------
    re.compile(r"\bnet\s+asset\s+value\b", re.IGNORECASE),
    re.compile(r"\bNAV\b"),  # case-sensitive: avoid matching "navision" etc.

    # --- Regulatory disclosure forms that ride adjacent feeds --------------
    re.compile(r"\bform\s+8\.?[35]\b", re.IGNORECASE),   # Form 8.3 / 8.5
    re.compile(r"\brule\s+8\.?[35]\b", re.IGNORECASE),   # Rule 8.3 / 8.5
    re.compile(r"\bEMM(?:\s|$)", re.IGNORECASE),         # exempt market maker
    re.compile(r"\bEPT(?:\s|/|$)", re.IGNORECASE),       # exempt principal trader

    # --- Holdings-in-company major-shareholder TR-1 notices ----------------
    # NOT a PDMR dealing — anchor on the "holding(s) in company" phrasing.
    re.compile(r"\bholding(?:\(s\)|s)?\s+in\s+company\b", re.IGNORECASE),

    # --- Block-listing-only admin (no transaction) -------------------------
    # Tight: only a bare block-listing announcement. Compound headlines like
    # "Director Shareholding & Block Listing Application" (FDM) are KEPT.
    re.compile(r"^\s*block\s+listing", re.IGNORECASE),
    re.compile(r"block\s+listing\s+(?:six\s+monthly\s+)?return", re.IGNORECASE),
)

# TIER 2 — CONDITIONAL drops. Corporate-action / event types that are usually
# issuer admin but CAN appear in a compound PDMR headline (e.g. "Director buys
# shares after trading update", "PDMR dealing — Transaction in Own Shares").
# Drop ONLY when NO dealing hint is present, so a genuine dealing is never lost.
_OFFSCOPE_UNLESS_DEALING = (
    # --- Buyback / own-share transactions (company, not a PDMR) ------------
    re.compile(r"transaction\s+in\s+own\s+shares", re.IGNORECASE),
    re.compile(r"\bshare\s+buy-?back\b", re.IGNORECASE),
    re.compile(r"\brepurchase\s+of\s+(?:ordinary\s+)?shares\b", re.IGNORECASE),

    # --- Corporate results / statements / dividends (no PDMR dealing) ------
    re.compile(r"\b(?:interim|final|half[- ]year|full[- ]year|annual)\s+results\b", re.IGNORECASE),
    re.compile(r"\btrading\s+(?:statement|update)\b", re.IGNORECASE),
    re.compile(r"\bdividend\s+(?:declaration|announcement|record\s+date)\b", re.IGNORECASE),
)

# Total-Voting-Rights housekeeping. TVR notices on their OWN are non-PDMR admin,
# but some PDMR notifications carry a TVR sub-line. Drop only a standalone TVR
# notice (no dealing hint present).
_TVR_STANDALONE_RE = re.compile(
    r"\b(?:total\s+voting\s+rights|tvr)\b", re.IGNORECASE
)

# Backwards-compat alias (older tests/callers referenced _DEALING_HINT_RE).
_DEALING_HINT_RE = _PDMR_HINT_RE


def _row_is_pdmr(headline: str) -> bool:
    """Decide whether an index-page row is a PDMR dealing worth fetching.

    Phase 4 inversion: the directors-dealings category page is already
    PDMR-only, so the default is KEEP. We only DROP a row when its headline
    clearly matches a non-PDMR announcement type in the denylist.

    Fail-open: an empty/odd headline is KEPT (better to fetch-and-let-the-
    gate-decide than to silently drop a real dealing). The downstream ingest
    gate quarantines genuine noise in _pending_review.json.
    """
    if not headline:
        # Fail-open: no headline text to judge by → keep and let the parser
        # + ingest gate decide. Never silently drop on a blank headline.
        return True
    # A genuine dealing hint overrides every conditional drop below.
    has_dealing_hint = bool(_PDMR_HINT_RE.search(headline))
    # Standalone Total-Voting-Rights housekeeping (no dealing word present).
    if _TVR_STANDALONE_RE.search(headline) and not has_dealing_hint:
        return False
    # TIER 1 — unconditional non-PDMR announcement types.
    if any(rx.search(headline) for rx in _OFFSCOPE_ALWAYS):
        return False
    # TIER 2 — corporate-action types: drop only if no dealing hint present, so
    # a compound PDMR headline mentioning results/own-shares/etc. is KEPT.
    if not has_dealing_hint and any(rx.search(headline) for rx in _OFFSCOPE_UNLESS_DEALING):
        return False
    # Default: trust the category page — keep the row.
    return True


def _filter_lse_aim(row: dict) -> bool:
    """D-SCOPE: keep LSE main + AIM only.

    Without confirmed venue metadata in the index HTML, the safe default
    is to keep everything our headline filter caught. The orchestrator
    can add stricter post-filtering if needed.
    """
    return True


# Index-row timestamp -> ISO date. Best-effort; returns None if unparseable.
# The index time cell looks like "08:00 AM 02-Jun-2026" (or 24h "08:00").
_INDEX_DATE_RE = re.compile(
    r"(\d{1,2})[-/ ]([A-Za-z]{3,9})[-/ ](\d{4})"
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _index_row_date(ann_at: str | None):
    """Parse an index-row timestamp string into a `datetime.date`, or None.

    Fail-open contract: callers MUST treat None as 'keep the row' (we could
    not judge the date, so we never drop). Only used to apply the window when
    the date is confidently parseable.
    """
    if not ann_at:
        return None
    m = _INDEX_DATE_RE.search(ann_at)
    if not m:
        return None
    from datetime import date as _date
    day = int(m.group(1))
    mon = _MONTHS.get(m.group(2)[:3].lower())
    year = int(m.group(3))
    if mon is None:
        return None
    try:
        return _date(year, mon, day)
    except ValueError:
        return None


def _ticker_from_url(url: str) -> str | None:
    """Extract the ticker from an Investegate announcement URL slug.

    Path shape: /announcement/{src}/{company-slug}/{headline-slug}/{rns_id}
    The company slug ends with the ticker after a run of 2+ dashes, e.g.
    'johnson-matthey--jmat' -> JMAT, 'uil-limited-di---utl' -> UTL,
    'greencore-group-cdi---gnc' -> GNC, '...holdings---mab1' -> MAB1. This is
    the canonical ticker source — far more reliable than the display-name cell,
    which can carry a '(DI)'/'(CDI)' suffix before the real code.
    """
    try:
        path = url.split("//", 1)[-1].split("/", 1)[1]  # strip scheme+domain
    except IndexError:
        return None
    parts = [p for p in path.split("/") if p]
    # Locate the rns_id (last all-digit segment); company slug sits two before.
    company = None
    for i, p in enumerate(parts):
        if p.isdigit() and i >= 2:
            company = parts[i - 2]
            break
    if company is None:
        return None
    m = re.search(r"-{2,}([a-z0-9.]+)$", company)
    return m.group(1).upper() if m else None


def iter_index(
    start_date: str,
    end_date: str,
    max_pages: int = 5,
    *,
    discover_only: bool = False,
) -> Iterator[dict]:
    """Walk the Director-Deals index and yield filing rows in window.

    Each yield: `{rns_id, url, headline, ticker_hint, announced_at}`.
    `announced_at` is best-effort; the row may be missing it.

    Phase 4 (2026-06-02) changes:
      * Trust the source: every row on /category/directors-dealings is kept
        unless its headline matches the non-PDMR denylist (`_row_is_pdmr`).
      * Real date window: rows whose parseable index-date falls OUTSIDE
        [start_date, end_date] are dropped. Rows with NO parseable date are
        KEPT (fail-open) — never silently dropped.
      * Real pagination: walks ?show=300&page=1,2,... up to `max_pages`,
        stopping early once a page has rows but ALL of them are older than
        start_date (the index is newest-first), or a page yields no filing
        links at all. Reuses the polite `_fetch` (jittered sleep) — no
        aggressive hammering.

    `discover_only` is a no-op here (the function never fetches individual
    filings — only the index pages). It exists so a dry-discovery harness can
    document intent at the call site; index-only walking is always read-only.
    """
    from datetime import date as _date

    try:
        win_start = _date.fromisoformat(start_date) if start_date else None
    except (TypeError, ValueError):
        win_start = None
    try:
        win_end = _date.fromisoformat(end_date) if end_date else None
    except (TypeError, ValueError):
        win_end = None

    seen_rns: set[str] = set()

    for page_num in range(1, max(1, max_pages) + 1):
        # Page 1 keeps the historical bare ?show=300 URL for cache/behaviour
        # parity; later pages add &page=N.
        if page_num == 1:
            page_url = f"{INDEX_URL}?show=300"
        else:
            page_url = f"{INDEX_URL}?show=300&page={page_num}"

        html = _fetch(page_url)

        page_had_link = False
        page_all_before_window = True  # until we see an in/after-window row
        page_yielded_any = False

        for tr in _INDEX_ROW_RE.findall(html):
            m = _FILING_LINK_RE.search(tr)
            if not m:
                continue
            page_had_link = True
            url, rns_id, headline = m.group(1), m.group(2), m.group(3).strip()
            if url.startswith("/"):
                url = BASE_URL + url

            # Cross-page dedup (defensive against an endpoint that repeats
            # rows when ?page=N is unsupported and silently returns page 1).
            if rns_id in seen_rns:
                continue

            if not _row_is_pdmr(headline):
                # A denied row still counts as 'seen on the page', but does
                # not affect the before-window early-stop decision (we cannot
                # date-judge a row we are dropping for type reasons).
                continue

            ann_at = None
            tt = _INDEX_TIME_RE.search(tr)
            if tt:
                ann_at = tt.group(1).strip()

            row_date = _index_row_date(ann_at)
            if row_date is not None:
                # Apply the window only when we can confidently parse a date.
                if win_end is not None and row_date > win_end:
                    # Newer than the window — skip this row but keep walking
                    # (newest-first index; older in-window rows follow).
                    page_all_before_window = False
                    continue
                if win_start is not None and row_date < win_start:
                    # Older than the window — drop. Do NOT flip the
                    # all-before-window flag (this one IS before window).
                    continue
                # In window.
                page_all_before_window = False
            else:
                # Fail-open: unparseable date → keep, and treat as 'not
                # before window' so a page of dateless rows never triggers
                # the early stop.
                page_all_before_window = False

            # Canonical ticker from the URL slug; fall back to the LAST
            # parenthesised code in the row (handles "Name (DI) (TCKR)").
            ticker = _ticker_from_url(url)
            if not ticker:
                cand = _TICKER_CELL_RE.findall(tr)
                if cand:
                    ticker = cand[-1].rstrip(".")
            row = {
                "rns_id": rns_id,
                "url": url,
                "headline": headline,
                "ticker_hint": ticker,
                # B-094: store ISO 'YYYY-MM-DD' when parseable; fall back to
                # the raw string (or None) so callers can still filter it out.
                # _index_row_date already parsed ann_at -> row_date for the
                # window check, so we reuse that result here at zero cost.
                "announced_at": row_date.isoformat() if row_date else ann_at,
            }
            if not _filter_lse_aim(row):
                continue
            seen_rns.add(rns_id)
            page_yielded_any = True
            yield row

        # Stop conditions for pagination:
        #   * the page had no filing links at all (end of index), OR
        #   * the page had links but every dated row was older than the
        #     window start (we have walked past the window — newest-first).
        if not page_had_link:
            break
        if win_start is not None and page_had_link and page_all_before_window \
                and not page_yielded_any:
            break


# --- Archive (backfill) parsing --------------------------------------------

# PDMR-style filings — match on the URL slug (most reliable) and headline.
_PDMR_URL_HINT_RE = re.compile(
    r"/director-pdmr-shareholding/|/pdmr-dealing/|/notification-of-transactions-of-directors",
    re.IGNORECASE,
)
_PDMR_HEADLINE_HINTS = (
    "director/pdmr",
    "pdmr shareholding",
    "pdmr dealing",
    "notification of transactions of directors",
    "person closely associated with pdmr",
)

# Chunk size for the advanced-search backfill. Investegate DOES paginate
# within a date window (~98 filings per page; verified live 2026-05-13).
# Within each 30-day chunk we walk page=1, page=2, ... until the response
# returns 0 PDMR filings or an HTTP 422. The 30-day chunk size keeps each
# request bounded; the page walk captures everything beyond the first 98.
_ARCHIVE_CHUNK_DAYS = 30

# Hard upper bound on pages walked per chunk. At observed peak filing
# density (~98/page, ~510 filings/month), six pages is plenty. Twenty is
# the unlikely-to-hit safety stop that protects against a misbehaving
# endpoint that loops back to page 1 instead of erroring on overshoot.
_ARCHIVE_MAX_PAGES_PER_CHUNK = 20


def iter_archive(start_date: str, end_date: str) -> Iterator[dict]:
    """Walk the advanced-search endpoint and yield PDMR-style filing rows.

    Investegate's `/advanced-search/draw` endpoint accepts:
        date_from=YYYY-MM-DD
        date_to=YYYY-MM-DD
        categories[]=16        (numeric code for Director's Dealings)
        exclude_navs=1         (drops NAV announcements we don't care about)
        page=N                 (1-based; ~98 filings per page)

    Each 30-day chunk is paginated server-side (~98 filings per page;
    calibrated live 2026-05-13: page=1 returned 98, page=2 returned 65
    different rns_ids, page=3 returned 0 with a "No results found"
    table). We walk page=1, page=2, ... per chunk until any of:
      - response contains 0 PDMR filing links, OR
      - every filing on the page has already been yielded in this chunk
        (defensive against endpoints that loop back to page 1), OR
      - HTTP 422 from _fetch (typical "no more pages" signal).
    A hard cap of `_ARCHIVE_MAX_PAGES_PER_CHUNK` pages protects against
    a misbehaving endpoint.

    Chunks walk in 30-day DESCENDING order (newest-first) so an aborted
    backfill leaves the most-recent filings already in the DB.

    Calibrated 2026-05-13:
      - /today-announcements/<date> returned only ~3 PDMR rows/day
        (curated daily-highlights view, ~17% capture).
      - /advanced-search/draw with categories[]=16 + page walk returned
        ~32/day, matching the underlying LSE PDMR-filing rate.

    Args:
        start_date: ISO YYYY-MM-DD inclusive (earliest date to fetch).
        end_date:   ISO YYYY-MM-DD inclusive (most-recent date to fetch).

    Yields:
        dict {rns_id, url, headline, ticker_hint, announced_at} - same
        shape as iter_index() so the orchestrator code path is identical.
    """
    from datetime import date as _date, timedelta as _td

    start = _date.fromisoformat(start_date)
    end = _date.fromisoformat(end_date)
    if start > end:
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")

    seen_rns: set[str] = set()

    chunk_end = end
    while chunk_end >= start:
        chunk_start = max(start, chunk_end - _td(days=_ARCHIVE_CHUNK_DAYS - 1))

        # Page walk within this chunk.
        chunk_yielded: set[str] = set()
        for page_num in range(1, _ARCHIVE_MAX_PAGES_PER_CHUNK + 1):
            params = [
                ("date_from", chunk_start.isoformat()),
                ("date_to", chunk_end.isoformat()),
                ("categories[]", CATEGORY_DIRECTORS_DEALINGS),
                ("exclude_navs", "1"),
                ("page", str(page_num)),
            ]
            url = ADVANCED_SEARCH_URL + "?" + urllib.parse.urlencode(params)
            try:
                html = _fetch(url, extra_headers={"X-Requested-With": "XMLHttpRequest"})
            except FetchError as e:
                msg = str(e)
                # 422 = past the last page. 404 = malformed chunk. Either
                # way, stop paginating this chunk and move on.
                if "404" in msg or "422" in msg:
                    break
                raise

            # Collect this page's PDMR rns_ids first so we can do
            # loop-detection (server returning page=1 content when we
            # asked for page=N+1).
            page_rns_ids: list[str] = []
            page_rows: list[dict] = []
            for m in _FILING_LINK_RE.finditer(html):
                filing_url, rns_id, headline = m.group(1), m.group(2), m.group(3).strip()
                if filing_url.startswith("/"):
                    filing_url = BASE_URL + filing_url
                # B-196: the advanced-search query is already scoped to the
                # directors-dealings category, so the restrictive URL/headline
                # hint gate below was redundant double-filtering that silently
                # dropped real director buys with non-standard slugs/headlines
                # (e.g. GANA "Director Share Purchase" -> /director-share-purchase-/,
                # which matched neither _PDMR_URL_HINT_RE nor _PDMR_HEADLINE_HINTS).
                # Keep ONLY the same fail-open headline check the index path uses.
                if not _row_is_pdmr(headline):
                    continue
                page_rns_ids.append(rns_id)
                page_rows.append({
                    "rns_id": rns_id,
                    "url": filing_url,
                    "headline": headline,
                    "ticker_hint": None,
                    "announced_at": None,
                })

            # End-of-pages signal: zero PDMR links on this page.
            if not page_rns_ids:
                break

            # Loop-back detection: every rns_id on this page was already
            # yielded in this chunk. Likely the server returned a
            # repeated page (Investegate's pagination has been observed
            # to silently loop in some corner cases). Stop paginating
            # this chunk.
            if page_rns_ids and all(r in chunk_yielded for r in page_rns_ids):
                break

            # Yield each new filing; chunk_yielded tracks per-chunk
            # dedup, seen_rns tracks across the entire backfill.
            for row in page_rows:
                rns_id = row["rns_id"]
                if rns_id in chunk_yielded:
                    continue
                chunk_yielded.add(rns_id)
                if rns_id in seen_rns:
                    continue
                seen_rns.add(rns_id)
                if not _filter_lse_aim(row):
                    continue
                yield row

        # Move to the next-older chunk.
        chunk_end = chunk_start - _td(days=1)
