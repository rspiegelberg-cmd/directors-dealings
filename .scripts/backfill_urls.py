"""Sprint 62 / B-167 -- url + announced_at backfill for no-url rows.

WHY
---
4,501 of 6,383 transactions (859 of 1,872 BUYs) carry an empty url, which
makes them unreachable by every cache-based enrichment (resulting_shares
backfill, announced_at backfill, price-units audit, source verification).

Root cause: reparse_corpus.process_filing() derived each filing's URL only
from existing DB rows ("url = existing[0]['url'] if existing else ''").
Filings with no DB row yet -- e.g. the ~74% of filings quarantined by the
2026-06-02 ingest-gate incident and later recovered via full-corpus
reparses -- were parsed with url="" and announced_at="", so every row
_apply_insert wrote carried empty values. The reparse-side bug is fixed
(B-167, same date); this script repairs the rows it left behind.

HOW
---
The cached Investegate HTML embeds both missing values:

  * an og:url <meta> tag  -> the canonical announcement URL
  * a JSON-LD "dateCreated" -> the publish timestamp (same source
    run_scrape._extract_announced_at uses)

The script walks every cached file in .scripts/_scrape_cache/, re-parses it
with the SAME arguments the buggy writer used (url="", announced_at="") so
the emitted fingerprints reproduce the no-url rows exactly, then:

    UPDATE transactions
    SET url = ?, announced_at = <fill-only-if-blank>
    WHERE fingerprint = ? AND (url IS NULL OR url = '')

A fingerprint that appears in several cached filings (re-announcements)
resolves to the lowest rns_id -- the earliest filing -- matching the
first-seen semantics of db.upsert_transaction. Never overwrites a
populated url or announced_at.

Every applied UPDATE is appended as one JSON line to the audit log
`.data/_url_backfill.log` (JSONL append -- never RMW).

ORDERING NOTE: run this BEFORE any future reparse_corpus run. The fixed
reparse parses with the recovered URL, which can shift ticker extraction
(and therefore fingerprints) on a handful of filings; this script must
match the fingerprints the OLD parse produced.

FUSE rule (CLAUDE.md): this script writes .data/directors.db.
Run from Windows PowerShell, never from the Linux sandbox.

CLI:
    python .scripts/backfill_urls.py            # preview (no writes)
    python .scripts/backfill_urls.py --confirm  # apply
    python .scripts/backfill_urls.py --confirm --verbose
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
from parse_pdmr import parse_announcement

CACHE_DIR = HERE / "_scrape_cache"
AUDIT_LOG = db.DB_DIR / "_url_backfill.log"
COMMIT_EVERY = 200   # filings per commit batch (Windows-side, cheap)

_OG_URL_RE = re.compile(
    r"property=['\"]og:url['\"]\s+content=['\"]([^'\"]+)['\"]"
)
_DATE_CREATED_RE = re.compile(
    r'"dateCreated"\s*:\s*"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"'
)


def _url_from_html(html: str) -> str:
    """Canonical announcement URL from the og:url meta tag ('' if absent)."""
    m = _OG_URL_RE.search(html)
    if not m:
        return ""
    url = m.group(1).strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def _announced_at_from_html(html: str) -> str:
    """Investegate publish timestamp from the JSON-LD block ('' if absent)."""
    m = _DATE_CREATED_RE.search(html)
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except ValueError:
        return ""


def _append_audit(entries: list[dict]) -> None:
    """JSONL append (no read-modify-write -- project rule)."""
    if not entries:
        return
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _load_targets(conn) -> dict[str, dict]:
    """fingerprint -> {type, announced_at} for every blank-url row."""
    rows = conn.execute(
        "SELECT fingerprint, type, announced_at FROM transactions "
        "WHERE url IS NULL OR url = ''"
    ).fetchall()
    return {
        r["fingerprint"]: {
            "type": r["type"],
            "announced_at": r["announced_at"] or "",
        }
        for r in rows
    }


def _scan_cache(targets: dict, verbose: bool = False) -> tuple[dict, dict]:
    """Walk the cache; return (candidates, stats).

    candidates: fingerprint -> list of {rns_id, url, announced_at} for
    every target fingerprint reproduced by re-parsing a cached filing
    with the buggy writer's arguments (url="", announced_at="").
    """
    stats = {"files_scanned": 0, "parse_errors": 0, "no_og_url": 0}
    candidates: dict[str, list] = {}
    files = sorted(CACHE_DIR.glob("*.html"))
    for path in files:
        rns_id = path.stem
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            stats["parse_errors"] += 1
            print(f"  WARN: cannot read {rns_id}: {exc}", file=sys.stderr)
            continue
        stats["files_scanned"] += 1
        url = _url_from_html(html)
        if not url:
            stats["no_og_url"] += 1
            continue
        announced_at = _announced_at_from_html(html)
        try:
            # Reproduce the buggy writer's parse EXACTLY (url="",
            # announced_at="") so fingerprints line up with the DB rows.
            extracted, _warnings, _src = parse_announcement(
                html, "", rns_id, "",
            )
        except Exception as exc:
            stats["parse_errors"] += 1
            if verbose:
                print(f"  WARN: parse error {rns_id}: {exc}",
                      file=sys.stderr)
            continue
        for row in extracted or []:
            fp = row.get("fingerprint")
            if fp in targets:
                candidates.setdefault(fp, []).append({
                    "rns_id": rns_id,
                    "url": url,
                    "announced_at": announced_at,
                })
    return candidates, stats


def _resolve(cands: list[dict]) -> dict:
    """Pick the earliest filing (lowest numeric rns_id) when ambiguous."""
    def _key(c):
        try:
            return (0, int(c["rns_id"]))
        except ValueError:
            return (1, 0)
    return sorted(cands, key=_key)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm", action="store_true",
        help="Apply UPDATEs to the DB. Default: preview only (no writes).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    conn = db.connect()

    targets = _load_targets(conn)
    n_buy_targets = sum(1 for t in targets.values() if t["type"] == "BUY")
    print(f"Rows with blank url:                 {len(targets)}")
    print(f"  of which BUY:                      {n_buy_targets}")
    if not targets:
        print("Nothing to do.")
        conn.close()
        return

    print(f"Scanning cache dir:                  {CACHE_DIR}")
    candidates, stats = _scan_cache(targets, verbose=args.verbose)
    print(f"  files scanned:                     {stats['files_scanned']}")
    print(f"  parse errors (skipped):            {stats['parse_errors']}")
    print(f"  no og:url in HTML (skipped):       {stats['no_og_url']}")

    n_updated = 0
    n_buy_updated = 0
    n_ann_filled = 0
    n_ambiguous = 0
    audit_entries: list[dict] = []
    since_commit = 0

    for fp, cands in sorted(candidates.items()):
        chosen = _resolve(cands)
        distinct_urls = {c["url"] for c in cands}
        ambiguous = len(distinct_urls) > 1
        if ambiguous:
            n_ambiguous += 1
        fills_ann = bool(chosen["announced_at"]) and \
            not targets[fp]["announced_at"]

        if args.confirm:
            conn.execute(
                "UPDATE transactions SET "
                "  url = ?, "
                "  announced_at = CASE WHEN announced_at IS NULL "
                "    OR announced_at = '' THEN ? ELSE announced_at END "
                "WHERE fingerprint = ? AND (url IS NULL OR url = '')",
                (chosen["url"], chosen["announced_at"] or "", fp),
            )
            audit_entries.append({
                "ts": db.iso_now(),
                "fingerprint": fp,
                "rns_id": chosen["rns_id"],
                "url": chosen["url"],
                "announced_at_set": fills_ann,
                "announced_at": chosen["announced_at"],
                "type": targets[fp]["type"],
                "n_candidate_filings": len(cands),
                "ambiguous": ambiguous,
            })
            since_commit += 1
            if since_commit >= COMMIT_EVERY:
                conn.commit()
                since_commit = 0

        n_updated += 1
        if targets[fp]["type"] == "BUY":
            n_buy_updated += 1
        if fills_ann:
            n_ann_filled += 1
        if args.verbose:
            verb = "UPDATE" if args.confirm else "WOULD UPDATE"
            print(f"  {verb}: fp={fp[:14]}... rns_id={chosen['rns_id']}"
                  + ("  [ambiguous]" if ambiguous else ""))

    if args.confirm:
        conn.commit()
        _append_audit(audit_entries)

    remaining = len(targets) - n_updated
    remaining_buy = n_buy_targets - n_buy_updated

    print()
    if args.confirm:
        print("DONE.")
        print(f"  audit log: {AUDIT_LOG}")
    else:
        print("PREVIEW (no DB writes). Run with --confirm to apply.")
    print(f"  urls restored:                     {n_updated}/{len(targets)}")
    print(f"  of which BUY:                      "
          f"{n_buy_updated}/{n_buy_targets}")
    print(f"  announced_at filled alongside:     {n_ann_filled}")
    print(f"  ambiguous (multi-filing, earliest "
          f"chosen):                           {n_ambiguous}")
    print(f"  still unreachable (no cache match):"
          f" {remaining}  (BUY: {remaining_buy})")
    if remaining:
        print("  -> unreachable rows have no cached filing that reproduces")
        print("     their fingerprint; they stay url-less and are excluded")
        print("     from cache-based enrichment metrics.")
    # Windows: an open handle blocks tempdir cleanup in tests (B-167 fix).
    conn.close()


if __name__ == "__main__":
    main()
