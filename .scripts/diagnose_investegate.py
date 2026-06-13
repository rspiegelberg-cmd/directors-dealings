"""Diagnostic: show what backfill_reporting_dates.py actually sees on Investegate.

Zone A — read-only diagnostic, no DB writes, safe to run from PowerShell.

Run:
    cd C:\Dev\DirectorsDealings
    python .scripts\diagnose_investegate.py

Paste the output back to Claude so the parser can be fixed.
"""
from __future__ import annotations

import gzip
import re
import sys
import urllib.request

TICKER = "LLOY"   # large liquid stock — always has results on Investegate
TYPE_SLUG = "Half+Year+Results"
URL = (
    "https://www.investegate.co.uk/Index.aspx"
    f"?searchtype=RNSType&searchterm={TYPE_SLUG}&searchrns={TICKER}"
)
UA = "DirectorsDealings-Research/0.3 (+contact: rspiegelberg@gmail.com)"

# ── Regexes from backfill_reporting_dates.py (unchanged) ─────────────────────
_ROW_RE   = re.compile(r"<tr[^>]*>(.*?)</tr>",   re.IGNORECASE | re.DOTALL)
_LINK_RE  = re.compile(r'href="[^"]*announcement[^"]*"', re.IGNORECASE)
_DATE_RE  = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_ANY_HREF = re.compile(r'href="([^"]+)"', re.IGNORECASE)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent":      UA,
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding", "") or "").lower()
        if enc == "gzip":
            raw = gzip.decompress(raw)
        ct = resp.headers.get("Content-Type", "") or ""
        m = re.search(r"charset=([\w\-]+)", ct, re.IGNORECASE)
        charset = m.group(1) if m else "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")


def main() -> int:
    print(f"Fetching: {URL}\n")
    try:
        html = fetch(URL)
    except Exception as exc:
        print(f"FETCH FAILED: {exc}")
        return 1

    print(f"HTML length: {len(html)} chars\n")

    # ── 1. First 800 chars (shows page title / structure) ─────────────────────
    print("=" * 70)
    print("SECTION 1 — first 800 chars of HTML")
    print("=" * 70)
    print(html[:800])
    print()

    # ── 2. All <tr> blocks matching current _LINK_RE (the "announcement" filter)
    print("=" * 70)
    print("SECTION 2 — rows that match _LINK_RE (href containing 'announcement')")
    print("=" * 70)
    matched_rows = 0
    for i, m in enumerate(_ROW_RE.finditer(html)):
        row = m.group(1)
        if _LINK_RE.search(row):
            matched_rows += 1
            dm = _DATE_RE.search(row)
            print(f"  Row #{i+1}: date_found={dm.group(0) if dm else 'NONE'}")
            print(f"  First 300 chars: {row[:300]!r}")
            print()
    if matched_rows == 0:
        print("  (no rows matched _LINK_RE — this is why no dates are found!)")
    print()

    # ── 3. First 15 <tr> blocks containing ANY date (DD Mon YYYY) ─────────────
    print("=" * 70)
    print("SECTION 3 — first 15 rows containing a DD Mon YYYY date")
    print("=" * 70)
    shown = 0
    for i, m in enumerate(_ROW_RE.finditer(html)):
        if shown >= 15:
            break
        row = m.group(1)
        dm = _DATE_RE.search(row)
        if dm:
            shown += 1
            hrefs = _ANY_HREF.findall(row)
            print(f"  Row #{i+1}: date={dm.group(0)}")
            print(f"  hrefs: {hrefs[:3]}")   # first 3 links in this row
            print(f"  first 200 chars: {row[:200]!r}")
            print()
    if shown == 0:
        print("  (no rows with DD Mon YYYY dates found at all)")
    print()

    # ── 4. All unique hrefs on the page containing 'article' or 'rns' ─────────
    print("=" * 70)
    print("SECTION 4 — sample hrefs containing 'article' or 'rns' or 'result'")
    print("=" * 70)
    seen_hrefs: set[str] = set()
    for hm in re.finditer(r'href="([^"]+)"', html, re.IGNORECASE):
        h = hm.group(1).lower()
        if any(k in h for k in ("article", "/rns", "result", "announce")):
            seen_hrefs.add(hm.group(1))
    for h in sorted(seen_hrefs)[:20]:
        print(f"  {h}")
    print()

    # ── 5. Structure around the first <table> (shows column headers) ──────────
    print("=" * 70)
    print("SECTION 5 — first <table> block (first 1500 chars)")
    print("=" * 70)
    tm = re.search(r"<table[^>]*>(.*?)</table>", html, re.IGNORECASE | re.DOTALL)
    if tm:
        print(tm.group(0)[:1500])
    else:
        print("  (no <table> found — page may be JS-rendered)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
