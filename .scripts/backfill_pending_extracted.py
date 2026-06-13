"""backfill_pending_extracted.py — best-guess prefill for the review queue (B-130).

ZONE B — writes `.scripts/_pending_review.json` (large, FUSE-sensitive).
**Rupert runs this**; Claude never runs it from bash.

Why
---
The PDMR review form prefills its edit fields from each pending record's
`extracted` list — but only ~360 of ~4,600 records carry one, so most open
blank. This pass fills `extracted` with a deterministic parser best-guess so the
reviewer starts from real director/date/shares/price values instead of nothing.

How (deterministic — NO network, NO LLM)
----------------------------------------
For each targeted record lacking an `extracted` list:
  1. Re-run the FULL parser (`parse_announcement`). Since B-090C (SIP aggregate)
     and B-121 (Mode-3 gate) landed, some records now parse cleanly — use that
     fully-validated result as-is.
  2. If the parser still returns nothing (these records are pending precisely
     because a gate dropped them), fall back to the LENIENT intermediate
     extractors (`_extract_via_sections` / `_extract_via_table` /
     `_extract_via_aggregate_table`), which emit PARTIAL rows (director found,
     price/shares may be 0). Map them to the 14-key `extracted` schema and tag
     `parser_source="regex-bestguess"` so the UI/audit can tell a best-guess
     prefill from a clean parse.
`warnings` are NOT touched (the bucket classifier keys off them).

Target buckets (default): the big recoverable gaps — bundled_multi_pdmr,
could_not_classify, multi_tranche. foreign_currency is opt-in (`--include-fx`):
its non-price fields prefill usefully but price is GBP-only so it stays blank.

Safety
------
- **Dry-run by DEFAULT** — pass `--apply` to write.
- Backs up `_pending_review.json` -> `.bak.before_b130_<ts>` before writing.
- Mutates an in-memory dict and writes the whole file ONCE at the end via
  temp-file + atomic rename (the only FUSE-safe pattern for this file — never a
  per-record read-modify-write).

Run:
    python .scripts\\backfill_pending_extracted.py                 # dry-run report
    python .scripts\\backfill_pending_extracted.py --apply         # write
    python .scripts\\backfill_pending_extracted.py --apply --include-fx
    python .scripts\\export_dashboard_json.py                      # then refresh export
    python .scripts\\snapshot_db.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import parse_pdmr  # noqa: E402
import export_dashboard_json as ex  # noqa: E402 — reuse the bucket classifier

PENDING_PATH = HERE / "_pending_review.json"
CACHE_DIR = HERE / "_scrape_cache"
DEFAULT_BUCKETS = {"bundled_multi_pdmr", "could_not_classify", "multi_tranche"}


# --- Load / write (preserve the {generated_at, count, items} wrapper) -------

def load_pending(path: Path) -> dict:
    """Return the FULL payload dict (with its 'items' map). Empty skeleton if
    the file is missing."""
    if not path.exists():
        return {"generated_at": db.iso_now(), "count": 0, "items": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "items" not in payload:
        raise SystemExit(
            f"[b130] {path.name} is not the expected {{items: ...}} wrapper — "
            f"refusing to touch it.")
    return payload


def write_pending(path: Path, items: dict) -> None:
    """Atomic whole-file write (temp + rename) — FUSE-safe; never in-place."""
    payload = {"generated_at": db.iso_now(), "count": len(items), "items": items}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_cached_html(rns_id: str) -> str | None:
    p = CACHE_DIR / f"{rns_id}.html"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


# --- Best-guess extraction --------------------------------------------------

def _to_extracted_obj(r: dict, ticker: str, company: str, rec: dict) -> dict:
    """Map a lenient intermediate row dict -> the 14-key `extracted` schema."""
    shares = int(r.get("shares") or 0)
    price = float(r.get("price") or 0.0)
    date = r.get("date")
    director = r.get("director") or ""
    ttype = r.get("type")
    has_key = bool(date and ticker and director and ttype)
    return {
        "fingerprint": (parse_pdmr._fingerprint(date, ticker, director, ttype,
                                                shares) if has_key else ""),
        "date": date,
        "ticker": ticker or "",
        "company": company or "",
        "director": director,
        "role": r.get("role"),
        "type": ttype,
        "shares": shares,
        "price": price,
        "value": round(price * shares, 2) if (price and shares) else 0.0,
        "context": None,
        "url": rec.get("url") or "",
        "announced_at": rec.get("announced_at") or "",
        "buy_strictness": parse_pdmr._classify_buy_strictness(r.get("nature") or ""),
    }


def best_guess_rows(html: str, rec: dict, rns_id: str) -> tuple[list, str]:
    """(rows, source). Full parser first; then lenient intermediates."""
    url = rec.get("url") or ""
    headline = rec.get("headline")
    announced_at = rec.get("announced_at") or ""

    # 1. Full parser — may now fully recover (B-090C / B-121).
    try:
        rows, _w, source = parse_pdmr.parse_announcement(
            html, url=url, rns_id=rns_id, announced_at=announced_at,
            headline=headline)
        if rows:
            return rows, (source or "reparse")
    except Exception as exc:  # noqa: BLE001 — never let one filing crash the batch
        print(f"[b130] {rns_id}: parse_announcement raised {exc!r}")

    # 2. Lenient partials. Resolve ticker the way the parser does.
    try:
        text = parse_pdmr.html_to_text(html)
        ticker = parse_pdmr._extract_ticker(
            text, headline=headline, url=url, html=html) or ""
    except Exception:  # noqa: BLE001
        ticker = ""

    for extractor in (parse_pdmr._extract_via_sections,
                      parse_pdmr._extract_via_table,
                      parse_pdmr._extract_via_aggregate_table):
        try:
            rows, company = extractor(html)
        except Exception:  # noqa: BLE001
            continue
        if rows:
            objs = [_to_extracted_obj(r, ticker, company or "", rec)
                    for r in rows if (r.get("director") or "").strip()]
            if objs:
                return objs, "regex-bestguess"
    return [], ""


# --- Batch ------------------------------------------------------------------

def backfill(*, apply: bool, buckets: set[str], limit: int = 0,
             pending_path: Path = PENDING_PATH) -> dict:
    payload = load_pending(pending_path)
    items = payload.get("items") or {}
    stats: Counter = Counter()

    if apply:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        bak = pending_path.with_suffix(f".json.bak.before_b130_{ts}")
        shutil.copyfile(pending_path, bak)
        print(f"[b130] backed up -> {bak.name}")

    processed = 0
    for rns_id, rec in items.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("extracted"):
            stats["skip_has_extracted"] += 1
            continue
        if ex._classify_pending_warnings(rec.get("warnings") or []) not in buckets:
            stats["skip_bucket"] += 1
            continue
        html = load_cached_html(rns_id)
        if html is None:
            stats["skip_no_cache"] += 1
            continue
        rows, source = best_guess_rows(html, rec, rns_id)
        if not rows:
            stats["no_guess"] += 1
            continue
        stats["filled"] += 1
        stats["fully_recovered" if source != "regex-bestguess"
              else "bestguess"] += 1
        if apply:
            rec["extracted"] = rows
            if source:
                rec["parser_source"] = source
        processed += 1
        if limit and processed >= limit:
            print(f"[b130] hit --limit {limit}")
            break

    if apply:
        write_pending(pending_path, items)
        print(f"[b130] wrote {pending_path.name} ({len(items)} items)")
    print(f"[b130] {'APPLIED' if apply else 'DRY-RUN'} | "
          f"filled={stats['filled']} (recovered={stats['fully_recovered']}, "
          f"bestguess={stats['bestguess']}) no_guess={stats['no_guess']} "
          f"skip[has={stats['skip_has_extracted']} bucket={stats['skip_bucket']} "
          f"no_cache={stats['skip_no_cache']}]")
    if not apply:
        print("[b130] DRY-RUN — nothing written. Re-run with --apply to write, "
              "then run export_dashboard_json.py + snapshot_db.py.")
    return dict(stats)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Best-guess prefill for the PDMR review queue (B-130).")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is dry-run: report only).")
    ap.add_argument("--include-fx", action="store_true",
                    help="also fill foreign_currency records (price stays blank).")
    ap.add_argument("--buckets", default=None,
                    help="comma-separated bucket ids to target "
                         f"(default: {','.join(sorted(DEFAULT_BUCKETS))}).")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of records filled this run.")
    args = ap.parse_args(argv)

    if args.buckets:
        buckets = {b.strip() for b in args.buckets.split(",") if b.strip()}
    else:
        buckets = set(DEFAULT_BUCKETS)
        if args.include_fx:
            buckets.add("foreign_currency")
    backfill(apply=args.apply, buckets=buckets, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
