"""Stage 2 daily-incremental scrape orchestrator.

Walks the Investegate Director-Deals index for a configurable window
(default 60 days), fetches each PDMR-style filing into a local cache,
parses with the stdlib regex parser, falls back to the Anthropic LLM
parser on warnings (unless `--no-llm`), and writes clean rows into the
`transactions` table.

CLI:
    --days N              -- scrape last N days (default 60).
    --from YYYY-MM-DD --to YYYY-MM-DD -- explicit window, overrides --days.
    --rns-id ID           -- re-parse one cached filing and print.
    --dry-run             -- no DB writes, no API calls; just parse + print.
    --no-llm              -- skip LLM fallback path entirely.
    --llm-budget-usd F    -- ceiling per run (default 50.0).
    --verbose             -- verbose logging.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
import db_health
import parse_pdmr
import scrape_investegate as scraper

# JSON-LD dateCreated extractor -- same logic as backfill_announced_at.py
import re as _re
_DATE_CREATED_RE = _re.compile(
    r'"dateCreated"\s*:\s*"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"'
)


def _extract_announced_at(html: str) -> str:
    """Extract the Investegate filing timestamp from the JSON-LD block.

    Returns an ISO string ("YYYY-MM-DDTHH:MM:SSZ") or empty string.
    This is more reliable than the index-row regex approach because the
    JSON-LD block is always present in the filing page HTML.
    """
    from datetime import datetime as _dt
    m = _DATE_CREATED_RE.search(html[:3072])  # always in <head>
    if not m:
        return ""
    try:
        return _dt.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except ValueError:
        return ""

# Lazy import — only when LLM is actually invoked.
try:
    import llm_cost
except ImportError:
    llm_cost = None


PENDING_PATH = HERE / "_pending_review.json"
EXCLUDED_INGEST_LOG = HERE.parent / ".data" / "_excluded_at_ingest.log"


# ── Ingest gate (Phase 1, 2026-06-02 incident fix) ──────────────────────────
# Historical bug: the ingest decision was per-FILING and all-or-nothing
# (`if extracted and not warnings:`). Any warning — even a harmless advisory
# like "couldn't read the company name" — diverted the WHOLE filing to
# `_pending_review.json`. ~74% of discovered filings were suppressed this way.
#
# Fix: classify warnings into BLOCKING (route the row to pending) vs ADVISORY
# (ingest the row anyway). Decision is now per-ROW. Anything not explicitly
# blocking and not matching a blocking prefix is treated as advisory.
#
# Taxonomy derived from source (parse_pdmr.py + llm_parser.py), not guessed.

# Exact blocking warning codes emitted by parse_pdmr.py / llm_parser.py.
BLOCKING_WARNING_CODES: frozenset = frozenset({
    # parse_pdmr.py structured codes
    "required_fields_missing",
    "could_not_parse_tx_date",
    "could_not_extract_ticker",
    "could_not_extract_PDMR_name",
    "could_not_classify_type",
    "could_not_separate_price_volume",
    "zero_shares_non_grant",
    "zero_price_non_grant",
    "foreign_currency",
    "multiple_distinct_prices",
    "duplicate_number_pull",
    "no_numeric_values",
    # llm_parser.py structured codes
    "llm_invalid_shares",
})

# Blocking warning prefixes (warnings that carry a ':payload' suffix).
BLOCKING_WARNING_PREFIXES: tuple = (
    "plausibility_rejected:",   # parse_pdmr R1-R4 hard reject
    "llm_missing_fields:",      # llm_parser required field absent
    "llm_invalid_type:",        # llm_parser bad transaction type
    "fetch_error:",             # filing could not be fetched
    "llm_error:",               # LLM call raised
    "llm_unparseable_response:",
)

# Advisory warnings that must NOT block ingest (purely informational).
#   - could_not_extract_company: ticker resolved fine; company name is cosmetic.
#   - plausibility_flagged:R5...: log-only, the parser deliberately emitted it.
#   - free-text LLM prose notes (e.g. "Filing also includes EXERCISE ...") —
#     these have no ':' code prefix and describe context, not a defect.
ADVISORY_WARNING_CODES: frozenset = frozenset({
    "could_not_extract_company",
})

# Transaction types where price==0 / value==0 is legitimate (nil-cost).
# Mirrors parse_pdmr.NIL_COST_CARVEOUT_TYPES; kept local to avoid a hard
# import dependency on a module constant that could drift.
_NIL_COST_TYPES: frozenset = frozenset({"GRANT", "EXERCISE"})


def _warning_is_blocking(w: str) -> bool:
    """Return True if warning `w` should keep a row out of the DB.

    A warning is blocking if it is an exact known blocking code, or starts
    with a known blocking prefix. Everything else (advisory codes + free-text
    LLM prose notes) is non-blocking.
    """
    if not w:
        return False
    if w in ADVISORY_WARNING_CODES:
        return False
    if w in BLOCKING_WARNING_CODES:
        return True
    return any(w.startswith(p) for p in BLOCKING_WARNING_PREFIXES)


def _row_is_ingestable(row: dict, warnings: list) -> bool:
    """Decide whether a single extracted row may be ingested.

    Two independent gates, both must pass:
      1. No blocking warning is attached to the filing.
      2. HARD per-row guard: a NON-grant / NON-exercise row must have a
         non-zero price AND non-zero value. A zero-value real trade is a
         parser misread (e.g. an unconverted FX trade) — invisible to every
         value-gated signal and pure table pollution. Grants/exercises are
         legitimately nil-cost, so they are exempt.
    """
    for w in (warnings or []):
        if _warning_is_blocking(w):
            return False
    tx_type = row.get("type")
    if tx_type not in _NIL_COST_TYPES:
        try:
            price = float(row.get("price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            value = float(row.get("value") or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if price == 0.0 or value == 0.0:
            return False
    return True


def _load_excluded_tickers(conn) -> set:
    """Load the set of tickers flagged is_excluded_issuer = 1.

    Returns an empty set if the column does not exist (e.g. running against
    a pre-migration DB). The ingest filter degrades safely — defensive
    second layer; the primary defence is the one-shot purge.
    """
    try:
        rows = conn.execute(
            "SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1"
        ).fetchall()
        return {r["ticker"] for r in rows}
    except Exception:
        return set()


def _log_excluded_ingest(ticker: str, rns_id: str, url: str,
                          headline: str | None) -> None:
    """Append one line to .data/_excluded_at_ingest.log."""
    EXCLUDED_INGEST_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"{db.iso_now()}\t{ticker}\t{rns_id}\t"
        f"{(headline or '').replace(chr(9), ' ')}\t{url}\n"
    )
    with EXCLUDED_INGEST_LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _resolve_window(args) -> tuple:
    if args.from_ and args.to:
        return args.from_, args.to
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    return _iso(start), _iso(end)


def _upsert_transaction(conn, row: dict, parser_source: str, *, verbose: bool = False) -> bool:
    """Thin wrapper — delegates to db.upsert_transaction (single canonical impl)."""
    return db.upsert_transaction(conn, row, parser_source, verbose=verbose)


def _write_pending(items: dict) -> None:
    payload = {
        "generated_at": db.iso_now(),
        "count": len(items),
        "items": items,
    }
    tmp = PENDING_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(PENDING_PATH)


def _load_pending() -> dict:
    if PENDING_PATH.exists():
        try:
            data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
            return data.get("items") or {}
        except json.JSONDecodeError:
            return {}
    return {}


def run(args) -> int:
    window_start, window_end = _resolve_window(args)
    verbose = args.verbose

    # Single-filing re-parse mode
    if args.rns_id:
        html = scraper.load_cached(args.rns_id)
        if html is None:
            print(f"no cached HTML for rns_id={args.rns_id}")
            return 2
        extracted, warnings, source = parse_pdmr.parse_announcement(
            html, url="", rns_id=args.rns_id, announced_at=""
        )
        print(json.dumps({
            "extracted": extracted, "warnings": warnings, "parser_source": source,
        }, indent=2))
        return 0

    # robots.txt gate
    try:
        scraper.check_robots()
    except scraper.RobotsBlockedError as e:
        print(f"ABORT: {e}")
        return 3
    except scraper.FetchError as e:
        # In sandbox: networking may be blocked. Surface clearly.
        print(f"ABORT: robots.txt fetch failed -- {e}")
        return 3

    # B-024: db_health pattern — pre-run integrity check + backup before
    # any destructive write. Skipped in --dry-run (no DB writes happen).
    # Canonical reference: classify_issuers.py:run().
    # B-179: SQLite/FUSE corruption defence only — skip on Postgres.
    if not args.dry_run and db.backend() == "sqlite":
        if not db_health.check(db.DB_PATH):
            print("[run_scrape] FATAL: pre-run integrity_check failed. "
                  "Run start.bat to restore from .bak before retrying.")
            return 2
        if not db_health.backup():
            print("[run_scrape] FATAL: failed to take pre-scrape .bak. "
                  "Refusing to proceed (destructive INSERTs ahead).")
            return 3

    conn = db.connect() if not args.dry_run else None
    # B-013: the work between conn = db.connect() and the main `try:` below
    # used to live OUTSIDE the protected block. If `_load_excluded_tickers`
    # or `llm_cost.start_run` raised, the SQLite connection leaked and
    # Windows held the file lock until process exit. Folded that setup
    # into the same try/finally so the conn always closes cleanly.
    #
    # Variables referenced by the finally block are pre-initialised here so
    # an exception during setup can't trigger a secondary NameError on the
    # way out of the function.
    pending: dict = {}
    run_id = None
    filings_seen = 0
    clean_writes = 0
    pending_count = 0
    inserts = 0
    excluded_at_ingest = 0
    try:
        # Defensive double-layer: even on dry runs, surface which filings
        # would be excluded so Rupert can spot misclassification early.
        if conn is not None:
            excluded_tickers = _load_excluded_tickers(conn)
        else:
            _tmp = db.connect()
            try:
                excluded_tickers = _load_excluded_tickers(_tmp)
            finally:
                _tmp.close()
        if verbose:
            print(f"  [ingest-filter] {len(excluded_tickers)} excluded tickers loaded")

        pending = _load_pending()

        if not args.no_llm and llm_cost is not None and not args.dry_run:
            run_id = llm_cost.start_run()

        for row in scraper.iter_index(window_start, window_end):
            filings_seen += 1
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
                pending_count += 1
                continue

            # Prefer the JSON-LD dateCreated embedded in every filing page
            # (always UTC; format "YYYY-MM-DDTHH:MM:SSZ").  Fall back to the
            # index-row timestamp if dateCreated is somehow absent.
            announced_at = _extract_announced_at(html) or row.get("announced_at") or ""

            extracted, warnings, source = parse_pdmr.parse_announcement(
                html, url=url, rns_id=rns_id,
                announced_at=announced_at,
                headline=row.get("headline"),
                ticker_hint=row.get("ticker_hint"),
            )

            # LLM fallback if regex flagged
            used_llm = False
            if warnings and not args.no_llm and not args.dry_run:
                try:
                    import llm_parser
                    if llm_cost is not None and run_id is not None:
                        llm_cost.check_budget(run_id, args.llm_budget_usd)
                    llm_extracted, llm_warnings = llm_parser.parse_with_llm(
                        html, url, rns_id,
                        row.get("announced_at") or "",
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
                            _write_pending(pending)
                            return 4
                    if verbose:
                        print(f"  ! LLM failed {rns_id}: {e}")
                    warnings = warnings + [f"llm_error:{type(e).__name__}"]

            # Phase 1 (2026-06-02): per-ROW ingest gate. Ingest any row whose
            # own required fields are complete and whose attached warnings are
            # purely advisory; only genuinely-incomplete rows go to pending.
            if extracted:
                kept = []           # ingestable, not excluded → insert
                pending_rows = []   # blocked rows → route filing to pending
                for ex in extracted:
                    if not _row_is_ingestable(ex, warnings):
                        pending_rows.append(ex)
                        continue
                    # B-011 ingest-time filter: drop excluded-issuer rows
                    # before they reach upsert. Logged to
                    # .data/_excluded_at_ingest.log for auditability.
                    if ex.get("ticker") in excluded_tickers:
                        excluded_at_ingest += 1
                        _log_excluded_ingest(
                            ex["ticker"], rns_id, url, row.get("headline"),
                        )
                        if verbose or args.dry_run:
                            print(f"  EXCLUDE {rns_id}: {ex['ticker']} "
                                  f"(IT/CEF — would skip insert)")
                        continue
                    kept.append(ex)

                for ex in kept:
                    if args.dry_run:
                        print(f"  DRY {rns_id}: would insert {ex['fingerprint']} {ex['ticker']} {ex['type']} {ex['shares']}")
                    else:
                        if _upsert_transaction(conn, ex, source, verbose=verbose):
                            inserts += 1
                # B-028 (2026-05-21): commit per filing instead of per row.
                # `db.upsert_transaction` no longer commits — the caller
                # owns the commit boundary. Per-filing commits cut the
                # FUSE write surface by ~100× during multi-thousand-row
                # backfills.
                if kept and not args.dry_run:
                    conn.commit()
                    clean_writes += 1
                elif kept:
                    clean_writes += 1

                if pending_rows:
                    # Some rows in this filing were blocked — keep the filing
                    # in pending, but only with the blocked rows.
                    pending[rns_id] = {
                        "url": url,
                        "headline": row.get("headline"),
                        "warnings": warnings,
                        "extracted": pending_rows,
                        "parser_source": source,
                        "used_llm": used_llm,
                    }
                    pending_count += 1
                elif rns_id in pending:
                    # Phase 2 prune-on-success: this filing was previously
                    # trapped in pending and now ingests cleanly. Drop it from
                    # the in-memory dict; persisted once at the end (no RMW in
                    # the hot path — matches _write_pending's single write).
                    del pending[rns_id]
            else:
                # Nothing extracted at all (e.g. bundled multi-PDMR refusal,
                # required_fields_missing on every row) → route to pending.
                pending[rns_id] = {
                    "url": url,
                    "headline": row.get("headline"),
                    "warnings": warnings,
                    "extracted": extracted,
                    "parser_source": source,
                    "used_llm": used_llm,
                }
                pending_count += 1

            if not args.dry_run:
                scraper.update_progress(rns_id, window_start, window_end)

    finally:
        if not args.dry_run:
            _write_pending(pending)
        if run_id is not None and llm_cost is not None:
            llm_cost.end_run(run_id)
        if conn is not None:
            conn.close()

    total = max(filings_seen, 1)
    pct_pending = (pending_count / total) * 100.0
    print(
        f"\nSummary: filings_seen={filings_seen}, clean_writes={clean_writes}, "
        f"inserts={inserts}, pending={pending_count} ({pct_pending:.1f}%), "
        f"excluded_at_ingest={excluded_at_ingest}"
    )
    if pct_pending >= 30.0:
        print("WARN: pending rate >= 30%")

    # ── Schema-change canary ─────────────────────────────────────────────────
    # If we discovered filings but extracted absolutely nothing — no clean
    # rows, no pending, no exclusions — that almost certainly means
    # Investegate changed its HTML/PDF structure and the parser silently
    # dropped everything. Return rc=2 so refresh_all halts the chain rather
    # than rendering a stale dashboard for the user.
    if (filings_seen > 0
            and clean_writes == 0
            and pending_count == 0
            and excluded_at_ingest == 0):
        print(
            f"\n[CRITICAL] Scrape canary tripped: {filings_seen} filings "
            "discovered but ZERO extracted rows, ZERO pending, ZERO "
            "exclusions. This usually means Investegate's HTML/PDF "
            "structure has changed and the parser is silently dropping all "
            "filings. Halting the pipeline so you don't render a stale "
            "dashboard. Re-run with --verbose to investigate."
        )
        return 2

    if filings_seen == 0:
        # Quiet day (e.g., UK bank holiday) or the archive index is empty
        # for the chosen window. Not fatal, but worth flagging in the log.
        print(
            "[INFO] 0 filings discovered in the chosen window. "
            "This is normal on bank holidays; if it happens two days "
            "running, check the Investegate index URL by hand."
        )

    # B-024: db_health post-run pattern. If post-run integrity check
    # fails, skip seal() so the pre-run .bak is preserved as the rollback
    # target. Skipped in --dry-run.
    # B-179: local-SQLite-only; skip on Postgres.
    if not args.dry_run and db.backend() == "sqlite":
        try:
            if not db_health.check(db.DB_PATH):
                print("[run_scrape] WARNING: post-run integrity_check "
                      "failed. The pre-run .bak is valid — restore via "
                      "start.bat. Skipping seal to preserve good backup.")
                return 4
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-scrape seal failed (non-fatal): {e}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Stage 2 daily-incremental scrape")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--from", dest="from_", default=None)
    ap.add_argument("--to", default=None)
    ap.add_argument("--rns-id", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-budget-usd", type=float, default=50.0)
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
