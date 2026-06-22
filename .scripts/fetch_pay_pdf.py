#!/usr/bin/env python3
"""fetch_pay_pdf.py - B-168 helper: download a UK annual-report / remuneration PDF
and surface the audited "single total figure of remuneration" row(s) for a named
executive director, so the figure can be read and recorded by hand.

WHY THIS EXISTS
---------------
web_fetch (Claude's tool) caps PDF text extraction at ~40-55 pages, but the audited
single-figure table in a UK annual report usually sits on page ~78-160. This script
runs on Windows (Rupert), downloads the *whole* PDF, extracts every page, finds the
remuneration table page(s) for the named director, and prints the candidate figures.

IT DOES NOT GUESS / DOES NOT AUTO-WRITE
---------------------------------------
It only *surfaces* the audited table text + candidate numbers. A human (or Claude
reading the .txt output) confirms the figure and records it in .data/_pay_manual.csv
manually - preserving the source policy ("read from the audited table, never guess").

ZONE
----
ZONE B (Rupert runs from PowerShell): downloads BINARY PDFs and writes a TEXT cache
under .scripts/_pay_pdf_cache/. Claude must NOT run the download path from the Linux
sandbox (FUSE truncates binary writes). Claude reads only the produced .txt files
(text crosses the FUSE bridge cleanly). The script never opens directors.db and never
writes under .data/.

DEPENDENCIES
------------
    pip install pdfplumber requests
(pypdf is used as a text fallback if present; pdfplumber is strongly preferred for
table extraction.)

USAGE
-----
    # one director from a known report URL
    python .scripts/fetch_pay_pdf.py --url <PDF_URL> --director "Greg Fitzgerald" --ticker VTY

    # force a page range if auto-locate misses (1-based, inclusive)
    python .scripts/fetch_pay_pdf.py --url <PDF_URL> --director "Greg Fitzgerald" --pages 126-142

    # sweep every extraction_fail row in the ledger that has a source_url
    python .scripts/fetch_pay_pdf.py --from-ledger

OUTPUTS
-------
    .scripts/_pay_pdf_cache/<sha1>.pdf            cached download (binary)
    .scripts/_pay_pdf_cache/<ticker>_<slug>.txt   matched remuneration page text
    stdout                                        candidate total / base / FY per director
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "_pay_pdf_cache")
LEDGER = os.path.join(HERE, os.pardir, ".data", "_pay_manual.csv")

# Phrases that anchor the audited single-total-figure table in a UK DRR.
ANCHORS = (
    "single total figure",
    "single figure of remuneration",
    "single figure table",
    "total single figure",
    "total fixed pay",
    "total variable pay",
)

# A token that looks like a money amount: 1,086 / 1086 / 1,086.5 / 5,810
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
YEAR_RE = re.compile(r"\b(20\d{2})\b")
# en-dash, em-dash, hyphen or "nil" used for a zero cell
DASH_RE = re.compile(r"^[–—\-nil]+$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Core (pure, unit-testable on raw text)
# --------------------------------------------------------------------------- #
def _to_number(tok: str):
    """'1,086' -> 1086 (int) ; '234,640.5' -> 234640.5 (float) ; else None."""
    tok = tok.strip().strip("£$€")  # strip GBP/USD/EUR symbols
    if not tok or not re.fullmatch(r"\d[\d,]*(?:\.\d+)?", tok):
        return None
    tok = tok.replace(",", "")
    return float(tok) if "." in tok else int(tok)


def surname_of(director: str) -> str:
    """'Mr Joshua Schulman' -> 'schulman'  ('the last alphabetic token')."""
    parts = [p for p in re.split(r"[^A-Za-z'-]+", director) if p]
    return parts[-1].lower() if parts else director.lower()


def parse_rows_from_text(text: str, director: str):
    """Find candidate single-figure rows for `director` in a block of table text.

    Returns a list of dicts: {raw, year, numbers, candidate_base, candidate_total}.
    A row qualifies if its line mentions the director's surname AND contains a
    20xx year AND >= 3 numeric tokens (the shape of a single-figure table row).
    Heuristic: in UK single-figure tables the rightmost money column is the
    single TOTAL figure and the leftmost is salary/base - but ALL numbers are
    returned so a human can confirm which column is which.
    """
    sname = surname_of(director)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or sname not in line.lower():
            continue
        ym = YEAR_RE.search(line)
        if not ym:
            continue
        # numbers AFTER the year (the table cells), skipping the year itself
        tail = line[ym.end():]
        nums = [n for n in (_to_number(t) for t in NUM_RE.findall(tail)) if n is not None]
        if len(nums) < 3:
            continue
        out.append({
            "raw": line,
            "year": int(ym.group(1)),
            "numbers": nums,
            "candidate_base": nums[0],
            "candidate_total": nums[-1],
        })
    return out


def detect_currency(text: str) -> str:
    if "£" in text or "GBP" in text or "£000" in text:
        return "GBP"
    if "$" in text or "USD" in text:
        return "USD"
    if "€" in text or "EUR" in text:
        return "EUR"
    return "?"


def detect_fy_end(text: str):
    """Best-effort 'year ended <date>' -> e.g. '31 May 2024'."""
    m = re.search(
        r"(?:financial )?year (?:ended|ending)\s+"
        r"(\d{1,2}\s+[A-Za-z]+\s+20\d{2})",
        text, re.IGNORECASE,
    )
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# PDF I/O (Windows / Zone B)
# --------------------------------------------------------------------------- #
def download_pdf(url: str, dest: str) -> str:
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return dest
    import requests
    headers = {
        # Many corporate CDNs reject the default python-requests UA.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
    }
    r = requests.get(url, headers=headers, timeout=60, stream=True)
    r.raise_for_status()
    tmp = dest + ".part"
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
    os.replace(tmp, dest)
    return dest


def extract_pages(pdf_path: str):
    """Return list[str] of per-page text. pdfplumber preferred; pypdf fallback."""
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for pg in pdf.pages:
                pages.append(pg.extract_text() or "")
        return pages
    except Exception as e:               # noqa: BLE001 - fall back, report
        sys.stderr.write(f"[warn] pdfplumber failed ({e}); trying pypdf\n")
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        return [(p.extract_text() or "") for p in reader.pages]


def locate_pages(pages, director: str):
    """1-based page numbers that look like the single-figure table for director."""
    sname = surname_of(director)
    hits = []
    for i, txt in enumerate(pages, start=1):
        low = txt.lower()
        if sname in low and any(a in low for a in ANCHORS):
            hits.append(i)
    if hits:
        return hits
    # fallback: any page with an anchor (table may put the name on a wrapped line)
    return [i for i, txt in enumerate(pages, start=1)
            if any(a in txt.lower() for a in ANCHORS)]


def extract_tables_text(pdf_path: str, page_nums):
    """Pull pdfplumber tables on the given 1-based pages as readable rows."""
    try:
        import pdfplumber
    except Exception:
        return ""
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in page_nums:
            try:
                page = pdf.pages[p - 1]
            except IndexError:
                continue
            for ti, tbl in enumerate(page.extract_tables() or []):
                rows = [" | ".join((c or "").replace("\n", " ").strip()
                                   for c in row) for row in tbl]
                chunks.append(f"--- page {p} table {ti + 1} ---\n" + "\n".join(rows))
    return "\n\n".join(chunks)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def process(url: str, director: str, ticker: str = "", force_pages=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    sha = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    pdf_path = os.path.join(CACHE_DIR, sha + ".pdf")

    print(f"\n=== {ticker or '?'} | {director} ===")
    print(f"url: {url}")
    try:
        download_pdf(url, pdf_path)
    except Exception as e:               # noqa: BLE001
        print(f"  DOWNLOAD FAILED: {e}")
        return
    pages = extract_pages(pdf_path)
    print(f"  pages extracted: {len(pages)}")

    if force_pages:
        page_nums = force_pages
    else:
        page_nums = locate_pages(pages, director)
    if not page_nums:
        print("  no remuneration/anchor pages located - try --pages N-M from the report contents")
        return
    print(f"  candidate pages: {page_nums}")

    matched_text = "\n".join(f"[page {p}]\n{pages[p - 1]}" for p in page_nums
                             if 1 <= p <= len(pages))
    tables_text = extract_tables_text(pdf_path, page_nums)

    # write the text snapshot for Claude / human review
    out_txt = os.path.join(CACHE_DIR, f"{slug(ticker) or 'x'}_{slug(director)}.txt")
    with open(out_txt, "w", encoding="utf-8") as fh:
        fh.write(matched_text)
        if tables_text:
            fh.write("\n\n===== EXTRACTED TABLES =====\n\n")
            fh.write(tables_text)
    print(f"  wrote: {out_txt}")

    # surface candidate rows (from text AND tables)
    rows = parse_rows_from_text(matched_text + "\n" + tables_text.replace("|", " "),
                                director)
    cur = detect_currency(matched_text)
    fy = detect_fy_end(matched_text)
    if not rows:
        print("  no single-figure row auto-parsed - read the .txt directly (layout edge case)")
    else:
        print(f"  currency~{cur}  fy_end~{fy or '?'}   CONFIRM against the .txt before recording:")
        for r in sorted(rows, key=lambda x: x["year"], reverse=True):
            print(f"    FY{r['year']}: numbers={r['numbers']}  "
                  f"-> candidate base={r['candidate_base']}  total={r['candidate_total']}")


def iter_ledger_fails():
    with open(LEDGER, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") == "extraction_fail" and (row.get("source_url") or "").strip():
                yield row["ticker"], row["director"], row["source_url"].strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Surface audited director pay from a report PDF.")
    ap.add_argument("--url", help="PDF URL to download and parse")
    ap.add_argument("--director", help="director display name, e.g. 'Greg Fitzgerald'")
    ap.add_argument("--ticker", default="", help="ticker (for the output filename)")
    ap.add_argument("--pages", help="force 1-based page range, e.g. 126-142")
    ap.add_argument("--from-ledger", action="store_true",
                    help="process every extraction_fail row in _pay_manual.csv with a source_url")
    args = ap.parse_args(argv)

    force_pages = None
    if args.pages:
        a, _, b = args.pages.partition("-")
        force_pages = list(range(int(a), int(b or a) + 1))

    if args.from_ledger:
        seen = set()
        for ticker, director, url in iter_ledger_fails():
            key = (ticker.lower(), director.lower())
            if key in seen:
                continue
            seen.add(key)
            process(url, director, ticker)
        return 0

    if not args.url or not args.director:
        ap.error("provide --url and --director, or use --from-ledger")
    process(args.url, args.director, args.ticker, force_pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
