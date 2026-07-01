#!/usr/bin/env python3
"""
fetch_director_pay.py  (B-168 helper)

Downloads a company annual-report / remuneration PDF, extracts the FULL text
(reaching the back-of-report Directors' Remuneration Report that web_fetch's
~120KB cap cannot), and surfaces the single-total-figure / emoluments table for
the named director(s) so the audited figure can be read and recorded by hand.

READ-ONLY. It never writes to directors.db or _pay_manual.csv. It only prints
the located table region (and a clearly-flagged heuristic "candidate" number)
for review. The human/operator records the real figure into _pay_manual.csv.

Requirements (already present in CI / a normal Python env):
  - poppler's `pdftotext` on PATH
  - python `requests`

Usage:
  python .scripts/fetch_director_pay.py <PDF_URL> --names "Raja,Gagne" [--context 8] [--save out.txt]

Notes:
  - Give it the DIRECT PDF link (the company IR "annual report" or
    "remuneration report" .pdf), not an HTML landing page.
  - Some sites (bot-protected) refuse automated downloads; the tool reports the
    HTTP status clearly so you can grab the file by hand or use a mirror
    (annualreports.com, the FCA National Storage Mechanism).

Why this exists: annual-report pay tables sit near the BACK of the document;
web_fetch only extracts the front ~40-50 pages. This tool pulls the whole PDF
and greps the remuneration section locally. See CLAUDE.md / B-168.
"""
import argparse, re, subprocess, tempfile, os, unicodedata

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ANCHORS = [
    r"single\s+total\s+figure",
    r"single\s+figure\s+of\s+remuneration",
    r"total\s+remuneration",
    r"directors?[''’]?\s+remuneration",
    r"directors?[''’]?\s+emoluments",
    r"aggregate\s+remuneration",
    r"salary\s+and\s+fees",
]


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def download(url, dest):
    import requests
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    referer = f"{parts.scheme}://{parts.netloc}/"
    headers = {
        "User-Agent": UA,
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": referer,
    }
    sess = requests.Session()
    r = sess.get(url, headers=headers, timeout=60, stream=True, allow_redirects=True)
    if r.status_code != 200:
        raise SystemExit(f"ERROR: HTTP {r.status_code} fetching the PDF. "
                         f"The site may block automated downloads or the URL may be stale. "
                         f"Try the report's direct PDF link from the company IR page, "
                         f"or a mirror (annualreports.com / FCA NSM).")
    with open(dest, "wb") as fh:
        for chunk in r.iter_content(8192):
            fh.write(chunk)
    head = open(dest, "rb").read(5)
    if head[:4] != b"%PDF":
        raise SystemExit(f"ERROR: downloaded content is not a PDF (starts {head!r}); "
                         f"the URL is probably an HTML landing page, not the file itself.")
    return dest


def extract_text(pdf_path):
    # pdftotext -layout preserves table columns (the director row keeps its
    # numbers on one line). stderr is silenced (noisy 'Invalid Font Weight').
    txt_path = pdf_path + ".txt"
    subprocess.run(["pdftotext", "-layout", pdf_path, txt_path],
                   check=True, stderr=subprocess.DEVNULL)
    with open(txt_path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


NUM = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d{2,}\b")


def find_name_rows(lines, surname):
    sn = strip_accents(surname).lower()
    hits = []
    for i, ln in enumerate(lines):
        if sn in strip_accents(ln).lower():
            nums = NUM.findall(ln)
            hits.append((i, len(nums), ln))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--names", required=True,
                    help="comma-separated surnames or full names to locate")
    ap.add_argument("--context", type=int, default=6,
                    help="lines of context to print around each match")
    ap.add_argument("--save", help="also write the full extracted text here")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        pdf = download(args.url, os.path.join(td, "report.pdf"))
        size = os.path.getsize(pdf)
        text = extract_text(pdf)
    lines = text.splitlines()
    print(f"# Source: {args.url}")
    print(f"# PDF size: {size/1024:.0f} KB | extracted chars: {len(text):,} | lines: {len(lines):,}")
    if args.save:
        open(args.save, "w", encoding="utf-8").write(text)
        print(f"# Full text saved to: {args.save}")

    # 1) anchor locations (where the remuneration table likely lives)
    anchor_lines = []
    for i, ln in enumerate(lines):
        low = strip_accents(ln).lower()
        if any(re.search(a, low) for a in ANCHORS):
            anchor_lines.append(i)
    print(f"\n# Remuneration anchors found on {len(anchor_lines)} line(s): "
          f"{anchor_lines[:20]}{' ...' if len(anchor_lines) > 20 else ''}")

    names = [n.strip() for n in args.names.split(",") if n.strip()]
    for name in names:
        surname = name.split()[-1]
        print("\n" + "=" * 78)
        print(f"DIRECTOR: {name}  (matching on '{surname}')")
        print("=" * 78)
        hits = find_name_rows(lines, surname)
        if not hits:
            print("  !! surname not found in extracted text.")
            continue
        # rank: rows with the most numbers are most likely the pay table row
        hits_sorted = sorted(hits, key=lambda h: -h[1])
        rich = [h for h in hits_sorted if h[1] >= 2]
        show = rich[:3] if rich else hits_sorted[:3]
        for (i, ncount, ln) in show:
            lo, hi = max(0, i - args.context), min(len(lines), i + args.context + 1)
            tag = "  <-- candidate pay row" if ncount >= 2 else ""
            print(f"\n--- context around line {i} ({ncount} numbers on the line){tag} ---")
            for j in range(lo, hi):
                marker = ">>" if j == i else "  "
                print(f"{marker} {lines[j].rstrip()}")
            if ncount >= 2:
                nums = NUM.findall(ln)
                # drop standalone years and bare "000" column tags for the guess
                clean = [n for n in nums
                         if not re.fullmatch(r"(?:19|20)\d\d", n) and n != "000"]
                print(f"   numbers on row: {nums}")
                if clean:
                    print(f"   CANDIDATE (heuristic only - may be wrong when two tables sit "
                          f"side by side; trust the headers above): "
                          f"first~base {clean[0]} | last~total {clean[-1]}")
    print("\n# Reminder: read the actual column headers above before recording any "
          "figure. Record rung 'b', confidence 'high' only if read from the audited table.")


if __name__ == "__main__":
    main()
