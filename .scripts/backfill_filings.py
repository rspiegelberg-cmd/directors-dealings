"""Stage 2 multi-year backfill orchestrator.

Walks the Investegate /advanced-search/draw endpoint in 30-day descending
chunks for a wide historic window (default 2024-01-01 -> today). Resumable
across crashes via `_backfill_progress.json`. Honours an LLM cost ceiling
per run so a stuck loop can't drain the API balance.

Run this ONCE on initial setup to populate a year of historic filings before
using refresh_all.py for daily incremental updates.

CLI:
    --from YYYY-MM-DD --to YYYY-MM-DD  (default 2024-01-01 -> today)
    --resume                            -- continue from saved progress
    --no-llm                            -- skip LLM fallback
    --llm-budget-usd F                  -- ceiling per run (default 50.0)
    --verbose
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
import db_health
import parse_pdmr
import scrape_investegate as scraper

try:
    import llm_cost
except ImportError:
    llm_cost = None


PROGRESS_PATH = HERE / "_backfill_progress.json"
PENDING_PATH = HERE / "_pending_review.json"
EXCLUDED_CSV = HERE.parent / ".data" / "_excluded_it_cef.csv"
EXCLUDED_INGEST_LOG = HERE.parent / ".data" / "_excluded_at_ingest.log"


def _load_excluded_tickers(conn) -> set:
    """Tickers we must never (re-)ingest — B-094 (Sprint 22).

    backfill_filings.py historically had NO exclusion filter (unlike
    run_scrape.py / reparse_corpus.py), so a historic re-scrape re-imported
    the IT/CEF issuers Sprint 2 purged. Mirrors reparse_corpus._load_excluded:
      1. tickers_meta.is_excluded_issuer = 1 (live classifier flag), AND
      2. .data/_excluded_it_cef.csv (append-mode audit log) — defensive,
         because classify_issuers resets the flag each run and can't re-flag
         a ticker whose transactions were deleted.
    """
    excluded: set = set()
    try:
        for r in conn.execute(
            "SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1"
        ).fetchall():
            excluded.add(r["ticker"])
    except Exception:
        pass
    if EXCLUDED_CSV.exists():
        try:
            with EXCLUDED_CSV.open("r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    t = (row.get("ticker") or "").strip()
                    if t:
                        excluded.add(t)
        except Exception:
            pass
    return excluded


def _log_excluded_ingest(ticker, rns_id, url, headline) -> None:
    """Append one TSV line recording an ingest-time exclusion (auditable)."""
    try:
        EXCLUDED_INGEST_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(EXCLUDED_INGEST_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{db.iso_now()}\t{ticker}\t{rns_id}\t{url}\t"
                     f"{headline or ''}\n")
    except OSError:
        pass


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_progress() -> dict:
    if PROGRESS_PATH.exists():
        try:
            return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_progress(state: dict) -> None:
    tmp = PROGRESS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def _load_pending() -> dict:
    if PENDING_PATH.exists():
        try:
            return json.loads(PENDING_PATH.read_text(encoding="utf-8")).get("items") or {}
        except json.JSONDecodeError:
            return {}
    return {}


def _save_pending(items: dict) -> None:
    payload = {"generated_at": db.iso_now(), "count": len(items), "items": items}
    tmp = PENDING_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(PENDING_PATH)


def run(args) -> int:
    start = args.from_ or "2024-01-01"
    end = args.to or _today_iso()
    verbose = args.verbose

    try:
        scraper.check_robots()
    except (scraper.RobotsBlockedError, scraper.FetchError) as e:
        print(f"ABORT: {e}")
        return 3

    # B-024: db_health pattern — pre-run integrity check + backup before
    # any destructive writes. Canonical reference: classify_issuers.py:run().
    if not db_health.check(db.DB_PATH):
        print("[backfill_filings] FATAL: pre-run integrity_check failed. "
              "Run start.bat to restore from .bak before retrying.")
        return 2
    if not db_health.backup():
        print("[backfill_filings] FATAL: failed to take pre-backfill .bak. "
              "Refusing to proceed (destructive INSERTs ahead).")
        return 3

    state = _load_progress() if args.resume else {}
    state.setdefault("started_at", db.iso_now())
    state.setdefault("completed_dates", [])
    state.setdefault("filings_seen", 0)
    state.setdefault("transactions_written", 0)
    state.setdefault("pending_count", 0)
    state["current_window"] = {"from": start, "to": end}

    pending = _load_pending()

    # B-026: connection-leak fix. Previously `conn = db.connect()` and
    # `llm_cost.start_run()` ran OUTSIDE the try: block. If
    # `start_run()` raised (auth failure, API hiccup, etc.) the SQLite
    # handle leaked and Windows held the DB file lock until process
    # exit -- next start.bat would then fail to open the DB. Same
    # B-013 pattern already applied to run_scrape.py: pre-initialise
    # conn = None and run_id = None, open both inside the try, let the
    # existing finally close everything.
    conn = None
    run_id = None
    try:
        conn = db.connect()
        if not args.no_llm and llm_cost is not None:
            run_id = llm_cost.start_run()
        # B-094: load the IT/CEF exclusion set so a historic re-scrape does
        # not re-import the issuers Sprint 2 purged (run_scrape.py and
        # reparse_corpus.py already do this; backfill_filings.py did not).
        excluded_tickers = _load_excluded_tickers(conn)
        state.setdefault("excluded_at_ingest", 0)
        print(f"  [ingest-filter] {len(excluded_tickers)} excluded tickers loaded")

        # The archive walker is intentionally not calibrated yet.
        # iter_archive() raises ArchiveCalibrationError. We surface
        # that loudly rather than silently doing nothing.
        for row in scraper.iter_archive(start, end):
            state["filings_seen"] = state.get("filings_seen", 0) + 1
            rns_id = row["rns_id"]
            url = row["url"]

            try:
                cache_path = scraper.fetch_filing(rns_id, url)
                html = cache_path.read_text(encoding="utf-8", errors="replace")
            except scraper.FetchError as e:
                if verbose:
                    print(f"  ! fetch failed {rns_id}: {e}")
                pending[rns_id] = {
                    "url": url,
                    "headline": row.get("headline"),
                    "warnings": [f"fetch_error:{e}"],
                    "extracted": [],
                }
                state["pending_count"] = state.get("pending_count", 0) + 1
                _save_progress(state)
                continue

            extracted, warnings, source = parse_pdmr.parse_announcement(
                html, url=url, rns_id=rns_id,
                announced_at=row.get("announced_at") or "",
                headline=row.get("headline"),
                ticker_hint=row.get("ticker_hint"),
            )

            used_llm = False
            if warnings and not args.no_llm:
                try:
                    import llm_parser
                    if llm_cost is not None and run_id is not None:
                        llm_cost.check_budget(run_id, args.llm_budget_usd)
                    llm_extracted, llm_warnings = llm_parser.parse_with_llm(
                        html, url, rns_id, row.get("announced_at") or "",
                        run_id=run_id,
                    )
                    if llm_extracted:
                        extracted = llm_extracted
                        warnings = llm_warnings
                        source = "llm"
                        used_llm = True
                except Exception as e:
                    if llm_cost is not None:
                        from llm_cost import BudgetExceededError
                        if isinstance(e, BudgetExceededError):
                            print(f"ABORT: LLM budget exceeded -- {e}")
                            _save_pending(pending)
                            _save_progress(state)
                            return 4
                    if verbose:
                        print(f"  ! LLM failed {rns_id}: {e}")
                    warnings = warnings + [f"llm_error:{type(e).__name__}"]

            if extracted and not warnings:
                # B-094: drop excluded-issuer rows at ingest (IT/CEF), logging
                # each for audit, before they reach the DB.
                kept = []
                for ex in extracted:
                    if ex.get("ticker") in excluded_tickers:
                        state["excluded_at_ingest"] = (
                            state.get("excluded_at_ingest", 0) + 1)
                        _log_excluded_ingest(
                            ex.get("ticker"), rns_id, url, row.get("headline"))
                        if verbose:
                            print(f"  - exclude {rns_id} {ex.get('ticker')} "
                                  "(is_excluded_issuer)")
                        continue
                    kept.append(ex)
                for ex in kept:
                    _upsert_transaction(conn, ex, source, verbose=verbose)
                # B-028 (2026-05-21): commit per filing instead of per
                # row. `db.upsert_transaction` no longer commits — the
                # caller owns the commit boundary. Per-filing commits
                # cut the FUSE write surface by ~100× during multi-
                # thousand-row backfills.
                if kept:
                    conn.commit()
                state["transactions_written"] = state.get("transactions_written", 0) + len(kept)
            else:
                pending[rns_id] = {
                    "url": url,
                    "headline": row.get("headline"),
                    "warnings": warnings,
                    "extracted": extracted,
                    "parser_source": source,
                    "used_llm": used_llm,
                }
                state["pending_count"] = state.get("pending_count", 0) + 1

            _save_progress(state)
            _save_pending(pending)

    except scraper.ArchiveCalibrationError as e:
        print(f"ABORT: {e}")
        _save_progress(state)
        return 5
    finally:
        if run_id is not None and llm_cost is not None:
            llm_cost.end_run(run_id)
        if conn is not None:
            conn.close()

    print(
        f"Backfill done: seen={state['filings_seen']}, "
        f"written={state['transactions_written']}, "
        f"pending={state['pending_count']}, "
        f"excluded_at_ingest={state.get('excluded_at_ingest', 0)}"
    )

    # B-024: db_health post-run pattern. Post-run integrity check before
    # seal — if the DB is now sick, preserve the pre-run .bak as the
    # rollback target. Reference: classify_issuers.py:run() end.
    try:
        if not db_health.check(db.DB_PATH):
            print("[backfill_filings] WARNING: post-run integrity_check "
                  "failed. The pre-run .bak is valid — restore via "
                  "start.bat. Skipping seal to preserve good backup.")
            return 4
        db_health.seal()
    except Exception as e:
        print(f"[db_health] post-backfill seal failed (non-fatal): {e}")
    return 0


def _upsert_transaction(conn, row: dict, parser_source: str, *, verbose: bool = False) -> bool:
    """Thin wrapper — delegates to db.upsert_transaction (single canonical impl)."""
    return db.upsert_transaction(conn, row, parser_source, verbose=verbose)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Stage 2 multi-year backfill")
    ap.add_argument("--from", dest="from_", default="2024-01-01")
    ap.add_argument("--to", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-budget-usd", type=float, default=50.0)
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
