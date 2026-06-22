"""Sprint 2 / B-011 — classify IT / CEF / VCT / REIT issuers.

Identifies tickers in `transactions` that belong to investment trusts,
closed-end funds, VCTs, or REITs so they can be excluded from signal
scoring (see `exclude_investment_trusts.py` for the deletion runner).

Three sources, combined with AIC primary + name regex as the catch-all
and Yahoo as the optional sweep:

    A  Association of Investment Companies (AIC) member list
       — scraped from theaic.co.uk; cached locally; must yield >= 300
       members or the run aborts (defensive: AIC page-structure
       changes shouldn't silently delete on weak data).
    B  Yahoo Finance `quoteType` per ticker — flags ETF, MUTUALFUND,
       CLOSEDENDFUND. Cached in `.scripts/_classifier_cache.json`.
       Optional; disable with `--no-yahoo`.
    C  Name regex (conservative) — matches explicit IT/CEF/VCT/REIT
       terms in the company name. Conservative per Rupert decision
       2026-05-18: low false-positive risk, accepts some false
       negatives.

Output: writes three columns on `tickers_meta`:
    is_excluded_issuer  INTEGER  0/1
    excluded_source     TEXT     'A' / 'B' / 'C' / 'A,B' / etc.
    classified_at       TEXT     ISO timestamp of last classifier run

Idempotent — safe to re-run. Re-running refreshes the classification
against the current set of tickers and the latest sources.

CLI:
    python .scripts/classify_issuers.py             # full run
    python .scripts/classify_issuers.py --no-yahoo  # skip Yahoo sweep
    python .scripts/classify_issuers.py --aic-csv PATH
                                        # use a manual AIC ticker CSV
    python .scripts/classify_issuers.py --refresh-aic
                                        # ignore cache, re-scrape AIC
    python .scripts/classify_issuers.py --verbose
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # project root; used by B-021 Source E to find .data/
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import db_health  # noqa: E402

# ---------------------------------------------------------------------------
# Constants

CACHE_PATH = HERE / "_classifier_cache.json"

# AIC member directory landing page. The AIC publishes ~400 UK investment
# companies. The page is server-rendered HTML so plain HTTP + BeautifulSoup
# is enough; no JS engine required.
AIC_URL = "https://www.theaic.co.uk/aic/find-investment-company"

# Minimum AIC entries before we trust the scrape. If the page structure
# changes and we get fewer than this, abort rather than silently
# under-classify.
AIC_MIN_COUNT = 300

# Conservative name pattern per Rupert decision 2026-05-18.
# Word boundaries protect against false positives like "Trustpilot".
#
# REITs intentionally NOT matched (decision 2026-05-18, second pass):
# major UK REITs (British Land, Landsec, SEGRO, etc.) are operating
# real-estate businesses with normal director-dealing dynamics, not
# closed-end fund signalling noise. Real-estate-focused investment
# trusts (e.g. Schroder European Real Estate Investment Trust) still
# match via the "investment trust" alternation.
_NAME_REGEX = re.compile(
    r"(?i)\b(investment\s+trust|vct|capital\s+trust)\b"
)

# Yahoo quote API. The v7 endpoint accepts batches of symbols.
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_BATCH_SIZE = 100
YAHOO_TIMEOUT = 15
YAHOO_REQUEST_DELAY_S = 0.5  # gentle throttle between batches

# Quote types Yahoo uses to flag funds.
_EXCLUDE_QUOTE_TYPES = {"ETF", "MUTUALFUND", "CLOSEDENDFUND"}

# HTTP headers used for both the AIC scrape and the Yahoo calls. Yahoo
# clamps down on header-less requests; a desktop-Firefox UA is enough to
# get a 200 most of the time.
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Cache

def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(
                f"WARN: cache at {CACHE_PATH} is malformed — starting fresh",
                file=sys.stderr,
            )
    return {"aic": None, "aic_fetched_at": None, "yahoo": {}}


def _save_cache(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CACHE_PATH)


# ---------------------------------------------------------------------------
# Ticker normalisation

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,8}$")


def _normalise(ticker: str) -> str | None:
    """Strip exchange suffix and uppercase. Return None if not ticker-like."""
    if not ticker:
        return None
    t = ticker.strip().upper()
    if t.endswith(".L"):
        t = t[:-2]
    elif t.endswith(".LON"):
        t = t[:-4]
    if not _TICKER_RE.match(t):
        return None
    return t


# ---------------------------------------------------------------------------
# Source A — AIC

def _fetch_aic_html() -> str:
    """Fetch the AIC member directory page. Raises on HTTP/network error."""
    req = urllib.request.Request(AIC_URL, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    # AIC serves utf-8; fall back to latin-1 if anything odd slips through.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _extract_aic_tickers_from_html(html: str) -> list[str]:
    """Parse AIC directory HTML and pull out EPIC tickers.

    The AIC directory page contains per-company entries that include the
    LSE EPIC ticker. We try multiple patterns since the page layout has
    historically shifted; whichever pattern produces the most matches
    wins.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover -- handled by caller
        raise RuntimeError(
            "BeautifulSoup is required for the AIC scrape. "
            "Install with: pip install beautifulsoup4"
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()

    # Pattern 1: explicit "EPIC:" / "Ticker:" labels in the per-company markup.
    text = soup.get_text(separator="\n")
    for pat in (
        r"(?i)EPIC\s*[:\-]\s*([A-Z0-9.]{2,8})",
        r"(?i)Ticker\s*[:\-]\s*([A-Z0-9.]{2,8})",
        r"(?i)TIDM\s*[:\-]\s*([A-Z0-9.]{2,8})",
    ):
        for m in re.finditer(pat, text):
            norm = _normalise(m.group(1))
            if norm:
                found.add(norm)

    # Pattern 2: anchors linking to per-company pages, last URL segment.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/companies/([a-z0-9\-]+)/?", href, re.I)
        if not m:
            continue
        # Slugs are usually company-name-based, not tickers — but some
        # AIC pages append the EPIC. Take the last token if it looks
        # ticker-like.
        last = m.group(1).split("-")[-1].upper()
        norm = _normalise(last)
        if norm:
            found.add(norm)

    return sorted(found)


def _load_aic_csv(path: Path) -> list[str]:
    """Load AIC tickers from a manual CSV (single column or `ticker` column)."""
    tickers: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return []
    if has_header:
        header = [h.strip().lower() for h in rows[0]]
        idx = header.index("ticker") if "ticker" in header else 0
        data_rows = rows[1:]
    else:
        idx = 0
        data_rows = rows
    for r in data_rows:
        if idx < len(r):
            norm = _normalise(r[idx])
            if norm:
                tickers.add(norm)
    return sorted(tickers)


def _source_a_aic(args, cache: dict) -> set[str]:
    """Return the set of AIC member tickers (normalised).

    Source A is optional. The AIC website is JS-rendered (Drupal frontend
    + Morningstar API), so the direct urllib scrape returns an HTML shell
    with no member data. Options for getting a real AIC list:
      --aic-csv PATH    feed a manual CSV (any column named `ticker`)
      --no-aic          skip Source A entirely; rely on B + C + manual
    Source D (manual-include CSV) is the recommended workflow for
    patching named-trust false negatives spotted in the preview.
    """
    if args.no_aic:
        if args.verbose:
            print("[A] Skipped (--no-aic).")
        return set()

    # Manual override beats cache and scrape.
    if args.aic_csv:
        csv_path = Path(args.aic_csv)
        if not csv_path.exists():
            raise SystemExit(f"--aic-csv path does not exist: {csv_path}")
        tickers = _load_aic_csv(csv_path)
        if args.verbose:
            print(f"[A] Loaded {len(tickers)} AIC tickers from {csv_path}")
    elif cache.get("aic") and not args.refresh_aic:
        tickers = cache["aic"]
        if args.verbose:
            print(
                f"[A] Loaded {len(tickers)} AIC tickers from cache "
                f"(fetched {cache.get('aic_fetched_at')})"
            )
    else:
        print(f"[A] Scraping {AIC_URL} ...")
        try:
            html = _fetch_aic_html()
            tickers = _extract_aic_tickers_from_html(html)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(
                f"[A] AIC scrape failed: {e}\n"
                f"    Continuing without Source A. Use --aic-csv PATH to "
                f"supply a manual ticker list, or rely on B + C + "
                f"manual-include override.",
                file=sys.stderr,
            )
            return set()
        cache["aic"] = tickers
        cache["aic_fetched_at"] = _iso_now()
        _save_cache(cache)
        if args.verbose:
            print(f"[A] Scraped {len(tickers)} AIC tickers")

    if len(tickers) < AIC_MIN_COUNT and not args.allow_low_aic_count:
        print(
            f"[A] AIC source produced {len(tickers)} tickers, below safety "
            f"threshold {AIC_MIN_COUNT}. Continuing without Source A. "
            f"(Pass --allow-low-aic-count to use the partial list.)",
            file=sys.stderr,
        )
        return set()
    return set(tickers)


# ---------------------------------------------------------------------------
# Source D — manual include list (always-flag tickers)

def _source_d_manual(path_arg: str | None, *, verbose: bool) -> set[str]:
    """Load manual-include tickers from a CSV.

    Format: same as --aic-csv. One ticker per row, or a `ticker` column.
    Used to patch in named-trust false negatives that Source B and C miss
    (e.g. SMT, PSH, CLDN — well-known investment trusts whose names
    don't contain "Trust", "VCT", or "REIT").
    """
    if not path_arg:
        return set()
    p = Path(path_arg)
    if not p.exists():
        raise SystemExit(f"--manual-include path does not exist: {p}")
    tickers = set(_load_aic_csv(p))  # same CSV format
    if verbose:
        print(f"[D] Loaded {len(tickers)} manual-include tickers from {p}")
    return tickers


# ---------------------------------------------------------------------------
# Source E — audit log of previously-excluded tickers (B-021)
#
# When the classifier runs against a DB whose excluded rows have already been
# deleted (Sprint 2 IT/CEF purge, or after a subsequent reparse), Sources A-D
# can lose ground: Source C is regex-on-company-names, but `_load_companies`
# only sees tickers whose transactions are still present. If Sprint 2 deleted
# every Investment Trust row from `transactions`, those tickers no longer
# appear in Source C, and unless they happen to be in the manual-include CSV
# they silently lose their `is_excluded_issuer` flag on the next classifier
# run (B-021).
#
# Source E reads `.data/_excluded_it_cef.csv` -- the append-only audit log
# that Sprint 2's `exclude_investment_trusts.py` writes every fingerprint to.
# Any ticker that was ever excluded stays excluded. The audit log is the
# single source of historical truth; this Source treats it as authoritative.

def _source_e_audit_log(audit_csv_path: Path | None,
                         *, verbose: bool) -> set[str]:
    """Return the set of tickers ever recorded in the IT/CEF exclusion
    audit log. Returns an empty set if the file is missing or empty.
    Never raises -- a missing audit log is the normal pre-Sprint-2 state."""
    if audit_csv_path is None or not audit_csv_path.exists():
        if verbose:
            print(f"[E] Audit log not found at {audit_csv_path}; skipping.")
        return set()
    tickers: set[str] = set()
    try:
        with audit_csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = (row.get("ticker") or "").strip().upper()
                if t:
                    tickers.add(t)
    except (OSError, csv.Error) as e:
        # Malformed audit log shouldn't block a re-classification.
        if verbose:
            print(f"[E] Audit log read error ({e}); skipping.")
        return set()
    if verbose:
        print(f"[E] Loaded {len(tickers)} unique tickers from audit log.")
    return tickers


# ---------------------------------------------------------------------------
# Source B — Yahoo quoteType

def _fetch_yahoo_quote_batch(symbols_with_suffix: list[str]) -> dict:
    """Call Yahoo /v7/finance/quote for one batch. Return dict by symbol."""
    qs = ",".join(symbols_with_suffix)
    url = f"{YAHOO_QUOTE_URL}?symbols={qs}"
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=YAHOO_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = {}
    for entry in data.get("quoteResponse", {}).get("result", []):
        sym = entry.get("symbol")
        if sym:
            out[sym] = entry.get("quoteType")
    return out


def _source_b_yahoo(tickers: list[str], cache: dict, *, verbose: bool) -> set[str]:
    """Return tickers whose Yahoo quoteType marks them as a fund.

    Yahoo is queried with the `.L` suffix. Result keyed by **normalised**
    ticker (no suffix) for ease of join later.
    """
    yahoo_cache: dict = cache.setdefault("yahoo", {})
    to_query: list[str] = []
    excluded: set[str] = set()

    for t in tickers:
        cached = yahoo_cache.get(t)
        if cached is None:
            to_query.append(t)
            continue
        if cached in _EXCLUDE_QUOTE_TYPES:
            excluded.add(t)

    if not to_query:
        if verbose:
            print(f"[B] Yahoo: all {len(tickers)} tickers cached")
        return excluded

    if verbose:
        print(f"[B] Yahoo: querying {len(to_query)} new tickers "
              f"(cached: {len(tickers) - len(to_query)})")

    for i in range(0, len(to_query), YAHOO_BATCH_SIZE):
        batch = to_query[i:i + YAHOO_BATCH_SIZE]
        sym_batch = [f"{t}.L" for t in batch]
        try:
            response = _fetch_yahoo_quote_batch(sym_batch)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, json.JSONDecodeError) as e:
            print(f"WARN: Yahoo batch {i // YAHOO_BATCH_SIZE} failed: {e}",
                  file=sys.stderr)
            # Cache as 'UNKNOWN' so we don't loop forever — but we DON'T
            # mark them excluded. Single retry per run by deleting cache.
            for t in batch:
                yahoo_cache[t] = "UNKNOWN"
            time.sleep(YAHOO_REQUEST_DELAY_S * 4)
            continue

        for t in batch:
            qtype = response.get(f"{t}.L")
            yahoo_cache[t] = qtype or "NOT_FOUND"
            if qtype in _EXCLUDE_QUOTE_TYPES:
                excluded.add(t)

        if verbose:
            sys.stderr.write(
                f"  batch {i // YAHOO_BATCH_SIZE + 1}: "
                f"{len(batch)} symbols, "
                f"{sum(1 for t in batch if yahoo_cache[t] in _EXCLUDE_QUOTE_TYPES)} "
                f"flagged\n"
            )
        time.sleep(YAHOO_REQUEST_DELAY_S)

    _save_cache(cache)
    return excluded


# ---------------------------------------------------------------------------
# Source C — name regex

def _source_c_regex(rows: list[tuple[str, str]]) -> set[str]:
    """Return tickers whose company name matches the conservative regex.

    rows: list of (ticker, company_name).
    """
    excluded: set[str] = set()
    for ticker, company in rows:
        if not company:
            continue
        if _NAME_REGEX.search(company):
            excluded.add(ticker)
    return excluded


# ---------------------------------------------------------------------------
# DB orchestration

def _populate_tickers_meta(conn) -> int:
    """INSERT OR IGNORE every distinct ticker from transactions.

    Returns the number of rows actually inserted (new tickers we hadn't
    classified before).
    """
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions WHERE ticker IS NOT NULL "
        "AND ticker <> ''"
    ).fetchall()
    now = _iso_now()
    inserted = 0
    # B-179: INSERT OR IGNORE (SQLite) <-> ON CONFLICT DO NOTHING (Postgres) on
    # PK ticker. rowcount parity holds (1 on insert, 0 on conflict).
    if db.backend() == "postgres":
        _tm_sql = (
            "INSERT INTO tickers_meta (ticker, updated_at) VALUES (?, ?) "
            "ON CONFLICT (ticker) DO NOTHING"
        )
    else:
        _tm_sql = (
            "INSERT OR IGNORE INTO tickers_meta (ticker, updated_at) VALUES (?, ?)"
        )
    for r in rows:
        cur = conn.execute(_tm_sql, (r["ticker"], now))
        inserted += cur.rowcount or 0
    conn.commit()
    return inserted


def _load_companies(conn) -> dict[str, str]:
    """Map ticker -> most recent company name string from transactions."""
    out: dict[str, str] = {}
    for r in conn.execute(
        "SELECT ticker, company FROM transactions "
        "WHERE ticker IS NOT NULL AND company IS NOT NULL "
        "ORDER BY last_seen DESC"
    ):
        out.setdefault(r["ticker"], r["company"])
    return out


def _write_classification(
    conn,
    ticker: str,
    is_excluded: int,
    source: str | None,
) -> None:
    now = _iso_now()
    conn.execute(
        "UPDATE tickers_meta "
        "SET is_excluded_issuer = ?, excluded_source = ?, "
        "    classified_at = ?, updated_at = ? "
        "WHERE ticker = ?",
        (is_excluded, source, now, now, ticker),
    )


# ---------------------------------------------------------------------------
# Sprint 10 Phase 3: sticky-flag semantics + manual --unflag CLI.
#
# Once a ticker is flagged is_excluded_issuer = 1, the classifier never
# silently un-flags it. The only path to un-flag is via the --unflag CLI.
# Audit logs capture both directions:
#   .data/_classifier_sticky_holds.log  — tickers held sticky in a run
#   .data/_classifier_unflag.log        — tickers manually un-flagged

STICKY_HOLDS_LOG = HERE.parent / ".data" / "_classifier_sticky_holds.log"
UNFLAG_LOG       = HERE.parent / ".data" / "_classifier_unflag.log"


def _append_sticky_holds_log(tickers) -> None:
    """Append one tab-separated line per run summarising the sticky holds.

    Format: `{iso_now}\\t{count}\\t{comma_separated_tickers}\\n`

    Sticky-held tickers are those previously flagged is_excluded_issuer = 1
    that did NOT match any source in the current run. Under the old
    zero-then-reapply behaviour they would have silently lost their flag.
    Under sticky behaviour they keep it — and we record which ones here
    so Rupert can spot-check whether the classifier is over-holding.
    """
    if not tickers:
        return
    STICKY_HOLDS_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_iso_now()}\t{len(tickers)}\t{','.join(tickers)}\n"
    with STICKY_HOLDS_LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def _run_unflag(tickers, verbose: bool = False) -> int:
    """Set is_excluded_issuer = 0 for the given tickers and audit-log.

    Returns 0 on success (at least one ticker un-flagged) or 1 if no
    rows were updated (all tickers either missing or already
    un-flagged). Always commits before returning.

    Manual override path. Run as
        python .scripts/classify_issuers.py --unflag TICKER [--unflag TICKER]
    """
    conn = db.connect()
    try:
        affected = []
        for t in tickers:
            row = conn.execute(
                "SELECT is_excluded_issuer FROM tickers_meta WHERE ticker = ?",
                (t,),
            ).fetchone()
            if row is None:
                print(f"[unflag] {t}: not in tickers_meta, skipped")
                continue
            if row["is_excluded_issuer"] != 1:
                print(f"[unflag] {t}: already un-flagged, no-op")
                continue
            now = _iso_now()
            conn.execute(
                "UPDATE tickers_meta "
                "SET is_excluded_issuer = 0, excluded_source = NULL, "
                "    classified_at = ?, updated_at = ? "
                "WHERE ticker = ?",
                (now, now, t),
            )
            affected.append(t)
            print(f"[unflag] {t}: flag removed")
        conn.commit()
        if affected:
            UNFLAG_LOG.parent.mkdir(parents=True, exist_ok=True)
            line = f"{_iso_now()}\t{len(affected)}\t{','.join(affected)}\n"
            with UNFLAG_LOG.open("a", encoding="utf-8") as f:
                f.write(line)
            print(f"\n[unflag] {len(affected)} ticker(s) un-flagged. "
                  f"Audit: {UNFLAG_LOG}")
            return 0
        print("[unflag] no tickers un-flagged (none matched).")
        return 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main

def run(args) -> int:
    cache = _load_cache()

    # Code-review fix C-3 (2026-05-20): take a fresh .bak BEFORE opening
    # the DB for write. This script does
    #     UPDATE tickers_meta SET is_excluded_issuer = 0 ...
    # inside the same transaction as the re-apply loop. A crash between
    # the reset and the re-apply silently un-excludes every investment
    # trust / CEF — and the existing seal() at the end of run() would
    # be too late. Pre-snapshot defends against this.
    # Memory ref: project_classify_issuers_resets_flag.md
    # B-179: SQLite/FUSE corruption defence only — skip on Postgres.
    if db.backend() == "sqlite" and not db_health.check(db.DB_PATH):
        print("[classify_issuers] FATAL: pre-run integrity_check failed. "
              "Run start.bat to restore from .bak before retrying.")
        return 2
    if db.backend() == "sqlite" and not db_health.backup():
        print("[classify_issuers] FATAL: failed to take pre-classify .bak. "
              "Refusing to proceed (destructive UPDATE).")
        return 3

    # Sprint 10 Phase 3: --unflag is a manual override path. Skip
    # the full classifier and just remove the flag from the named
    # tickers. Integrity check + backup above already ran, so
    # _run_unflag operates against a snapshotted DB.
    if getattr(args, "unflag", None):
        return _run_unflag(args.unflag, verbose=args.verbose)

    conn = db.connect()
    try:
        # Step 0: make sure every transactions.ticker has a tickers_meta row.
        inserted = _populate_tickers_meta(conn)
        if args.verbose or inserted:
            print(f"[init] tickers_meta inserts (new tickers): {inserted}")

        companies = _load_companies(conn)
        all_tickers = sorted(companies.keys())
        if args.verbose:
            print(f"[init] candidate ticker count: {len(all_tickers)}")

        if not all_tickers:
            print("No tickers in `transactions`. Nothing to classify.")
            return 0

        # Source A — AIC member list. Optional; degrades gracefully.
        aic_set = _source_a_aic(args, cache)
        print(f"[A] AIC matches in our universe: "
              f"{len(aic_set & set(all_tickers))} / {len(aic_set)} total")

        # Source B — Yahoo quoteType (optional).
        if args.no_yahoo:
            yahoo_set: set[str] = set()
            print("[B] Yahoo sweep: SKIPPED (--no-yahoo)")
        else:
            yahoo_set = _source_b_yahoo(all_tickers, cache, verbose=args.verbose)
            print(f"[B] Yahoo matches: {len(yahoo_set)}")

        # Source C — name regex.
        regex_set = _source_c_regex(list(companies.items()))
        print(f"[C] Name-regex matches: {len(regex_set)}")

        # Source D — manual include list (catches named-trust false
        # negatives Source B/C miss, e.g. Scottish Mortgage, Pershing
        # Square, Caledonia — well-known ITs without "Trust" in name).
        manual_set = _source_d_manual(args.manual_include,
                                      verbose=args.verbose)
        print(f"[D] Manual-include matches: "
              f"{len(manual_set & set(all_tickers))} / {len(manual_set)} total")

        # Source E (B-021) — historical exclusions from .data/_excluded_it_cef.csv.
        # Catches tickers whose Sprint 2 deletion removed them from
        # transactions: Source C can't see them (no company in `companies`),
        # and unless they're in the manual CSV they'd silently lose the
        # is_excluded_issuer flag on this run. The audit log is the
        # single source of historical truth.
        audit_path: Path | None = None
        if not args.no_audit_log:
            audit_path = ROOT / ".data" / "_excluded_it_cef.csv"
        audit_set = _source_e_audit_log(audit_path, verbose=args.verbose)
        print(f"[E] Audit-log matches: {len(audit_set)} historical tickers")

        # Combine. Build per-ticker source string.
        union = (
            (aic_set & set(all_tickers))
            | yahoo_set
            | regex_set
            | (manual_set & set(all_tickers))
            | audit_set                       # B-021: stays excluded forever
        )
        per_ticker_sources: dict[str, str] = {}
        for t in union:
            srcs = []
            if t in aic_set:
                srcs.append("A")
            if t in yahoo_set:
                srcs.append("B")
            if t in regex_set:
                srcs.append("C")
            if t in manual_set:
                srcs.append("D")
            if t in audit_set:
                srcs.append("E")
            per_ticker_sources[t] = ",".join(srcs)

        # Sprint 10 Phase 3: sticky-flag semantics. Capture the set
        # of tickers currently flagged BEFORE writing, so we can
        # log "sticky holds" — previously-flagged tickers that no
        # source confirmed this run. The old behaviour zeroed every
        # flag then re-applied; that silently un-flagged tickers
        # when a source (Yahoo, AIC) had a transient outage.
        # Memory ref: project_classify_issuers_resets_flag.md
        pre_flagged = {
            r["ticker"] for r in conn.execute(
                "SELECT ticker FROM tickers_meta "
                "WHERE is_excluded_issuer = 1"
            ).fetchall()
        }
        sticky_holds = sorted(pre_flagged - union)

        # Persist.
        conn.execute("BEGIN")
        try:
            # Additive UPSERT — no longer zeros flags first. Tickers
            # in `union` (matched a source this run) are written or
            # refreshed. Tickers in `pre_flagged - union` are sticky
            # holds — left untouched, keep their flag, logged below.
            # To un-flag, use the `--unflag TICKER` CLI explicitly.
            for t in sorted(union):
                _write_classification(conn, t, 1, per_ticker_sources[t])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        # Audit log for sticky holds (no-op if list is empty).
        _append_sticky_holds_log(sticky_holds)
        if sticky_holds:
            print(
                f"\n[sticky-flag] {len(sticky_holds)} ticker(s) held flagged "
                f"despite no source matching this run "
                f"(audit: {STICKY_HOLDS_LOG.name}). "
                f"To un-flag, run: "
                f"python .scripts/classify_issuers.py --unflag TICKER"
            )

        print(
            f"\nClassification complete. "
            f"{len(union)} tickers flagged for exclusion "
            f"(A={len(aic_set & set(all_tickers))}, "
            f"B={len(yahoo_set)}, "
            f"C={len(regex_set)}, "
            f"D={len(manual_set & set(all_tickers))}, "
            f"E={len(audit_set)}). "
            f"Multi-source: "
            f"{sum(1 for s in per_ticker_sources.values() if ',' in s)}"
        )
        print(
            "\nNext step: run\n"
            "    python .scripts/exclude_investment_trusts.py --preview\n"
            "to produce the sign-off CSV."
        )
        # B-024 + Code-review fix C-3 (2026-05-20): post-run integrity
        # check before sealing. If the post-run state is corrupt, the
        # pre-run .bak taken at the top of run() is the rollback target.
        # B-179: local-SQLite-only; skip on Postgres.
        if db.backend() == "sqlite":
            try:
                if not db_health.check(db.DB_PATH):
                    print("[classify_issuers] WARNING: post-run integrity_check "
                          "failed. The pre-run .bak is valid — restore via "
                          "start.bat. Skipping seal to preserve good backup.")
                    return 4
                db_health.seal()
            except Exception as e:
                print(f"[db_health] post-script seal failed (non-fatal): {e}")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Classify IT/CEF/VCT/REIT issuers in tickers_meta."
    )
    ap.add_argument("--no-yahoo", action="store_true",
                    help="Skip the Yahoo quoteType sweep (Source B).")
    ap.add_argument("--no-aic", action="store_true",
                    help="Skip the AIC scrape entirely (Source A). "
                         "Useful when the AIC page is JS-rendered or "
                         "unavailable; rely on B + C + manual instead.")
    ap.add_argument("--aic-csv", default=None,
                    help="Path to a manual AIC ticker CSV "
                         "(overrides the live scrape).")
    ap.add_argument("--manual-include", default=None,
                    help="Path to a CSV of ticker symbols to always "
                         "flag for exclusion (Source D). Used to patch "
                         "in named-trust false negatives.")
    ap.add_argument("--no-audit-log", action="store_true",
                    help="Skip Source E (B-021): the .data/_excluded_it_cef.csv "
                         "audit-log recovery. Use only if you genuinely want to "
                         "re-classify from scratch and DELIBERATELY drop "
                         "historical exclusions.")
    ap.add_argument("--refresh-aic", action="store_true",
                    help="Bypass the AIC cache and re-scrape.")
    ap.add_argument("--allow-low-aic-count", action="store_true",
                    help="Proceed even if AIC returns < 300 tickers. "
                         "Only use if you've manually verified the "
                         "scrape and accept the lower coverage.")
    ap.add_argument("--unflag", action="append", default=[], metavar="TICKER",
                    help="Sprint 10 Phase 3 manual override: remove the "
                         "is_excluded_issuer flag from one ticker. Can be "
                         "repeated (e.g. --unflag SMT --unflag CTY). "
                         "When set, the classifier is SKIPPED and only "
                         "the un-flag operation runs. Audited to "
                         ".data/_classifier_unflag.log.")
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
