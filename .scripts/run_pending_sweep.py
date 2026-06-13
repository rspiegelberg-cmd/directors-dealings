"""B-002: LLM sweep over `.scripts/_pending_review.json`.

PURPOSE
-------
Seven (give or take) RNS filings are stranded in pending review because
the regex-based parser cannot locate their transaction date (JSE/SIP/
foreign-issuer template variants). This script asks Claude Sonnet to
extract the missing fields from the cached HTML, sanity-checks the
result, and writes any clean rows back to `transactions` with
`parser_source='llm'`. Processed entries move out of pending into
`_resolved_pending.json` (audit trail).

WHY A SEPARATE SCRIPT
---------------------
* `backfill_filings.py` runs the LLM as a fallback at *initial ingest*.
  Once a row is in pending, it stays there until something explicitly
  re-tries -- this is that "something."
* Keeps blast radius small: we only touch the rows we mean to touch,
  and there's a hard cost ceiling so a runaway loop can't burn money.

CLAUDE.md / FUSE NOTES (Rupert: please read before running)
-----------------------------------------------------------
This script writes to `.data/directors.db` and triggers further writes
(eval_signals.py, build_dashboard.py). That makes it a **Zone B**
operation per CLAUDE.md -- Claude must NOT run it from its Linux
sandbox; **only run it from a Windows shell**:

    python .scripts/run_pending_sweep.py [--target stuck-on-date|all]
                                         [--budget-usd 1.50]
                                         [--max-rows N]
                                         [--no-pipeline]

Flags:
    --target      Which pending entries to sweep (default: `stuck-on-date`).
                  - `stuck-on-date` selects ONLY entries whose warnings
                    contain `could_not_parse_tx_date`, whose `extracted`
                    list is empty, and which have NOT already failed an
                    LLM attempt. This is the B-002 target population --
                    roughly 7 filings as of 2026-05.
                  - `all` sweeps every entry. Use sparingly: the
                    pending file accumulates ALL failed parses (fetch
                    errors, foreign-currency rejections, bundled-PDMR
                    filings, ...) so an `all` sweep will call the LLM
                    thousands of times. Almost never the right choice.
    --budget-usd  Hard ceiling on LLM spend for the run (default 1.50 USD,
                  roughly £1.20 at current rates). Aborts mid-loop if the
                  ledger crosses this.
    --max-rows    Process at most N entries (handy for a single-row dry-run
                  before doing the full sweep).
    --no-pipeline Skip the eval_signals.py + build_dashboard.py re-run at
                  the end. Default is to run them, because without that
                  step the recovered rows are invisible in the dashboard.

PERMANENT-API-ERROR ABORT
-------------------------
If the first LLM call returns an HTTP 4xx (auth, credit, malformed
request), the script aborts the loop immediately rather than hammering
the API for every remaining entry. A credit-too-low message will look
like: `Anthropic API HTTP 400 Bad Request: ...credit balance is too low`.
Top up the Anthropic account, then re-run; the per-row save means any
rows already recovered won't be re-charged.

ACCEPTANCE CRITERIA (from B-002 scope)
--------------------------------------
* Zero rows remain in `_pending_review.json` after the run (for the
  entries we successfully parsed; entries with missing cache HTML or
  out-of-window dates stay in pending with an added flag).
* All recovered fingerprints land in `transactions` with
  `parser_source='llm'`.
* `eval_signals.py` is re-run so any signals these rows would fire
  are now firing.
* `build_dashboard.py` is re-run so the 7 rows surface in the Today /
  company pages.
* Sanity guard: an LLM-returned date more than ±60 days from
  `announced_at` is rejected.
* Cost ceiling: hard `--budget-usd` cap.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

# Optional ledger -- import is best-effort so the sweep works even on a
# machine without llm_cost ready (e.g. fresh checkout).
try:
    import llm_cost  # type: ignore
except ImportError:
    llm_cost = None

# Defer importing llm_parser until we actually need to call it -- importing
# it eagerly would surface a `MissingApiKeyError` for callers running
# `--help`.

PENDING_PATH      = HERE / "_pending_review.json"
RESOLVED_PATH     = HERE / "_resolved_pending.json"
CACHE_DIR         = HERE / "_scrape_cache"

# Sprint 25 Phase 3: sticky-protection manifests written by apply_edits.py.
# These live in .data/ (Zone B), but are *read* here (read-only, safe).
ROOT              = HERE.parent
_REJECTED_IDS_PATH = ROOT / ".data" / "_rejected_rns_ids.json"
_MANUAL_IDS_PATH   = ROOT / ".data" / "_manual_rns_ids.json"
DEFAULT_BUDGET_USD = 1.50  # ~£1.20; B-002 scope estimated ~£0.30 actual.
SANITY_WINDOW_DAYS = 60    # AC: reject dates >±60 days from announced_at

# The exact warning string parse_pdmr emits when it cannot locate a
# transaction date. This is the B-002 target population.
STUCK_ON_DATE_WARNING = "could_not_parse_tx_date"
# Warnings that prove an LLM pass already failed -- skip those entries
# so a re-run doesn't bill twice for the same failure.
PRIOR_LLM_FAIL_PREFIX = "llm_error:"

# B-015: regex to recover `announced_at` from cached HTML when the
# pending entry itself lacks it. Investegate filings embed a JSON-LD
# `dateCreated` field in <head> -- same pattern used by
# `run_scrape.py:_DATE_CREATED_RE` and `backfill_announced_at.py`.
# Future cleanup: consolidate the three copies into a shared module
# (small DRY task; not on the immediate critical path).
_DATE_CREATED_RE = re.compile(
    r'"dateCreated"\s*:\s*"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"'
)


def _extract_announced_at_from_html(html: str) -> str:
    """Return the Investegate JSON-LD `dateCreated` as an ISO datetime
    string ("YYYY-MM-DDTHH:MM:SSZ"), or '' if the pattern is absent /
    malformed. The value is always in <head>; we scan the first 3 KB
    to keep the cost trivial."""
    m = _DATE_CREATED_RE.search(html[:3072])
    if not m:
        return ""
    try:
        dt = datetime.strptime(m.group(1).strip(), "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""


# --- pending file helpers ---------------------------------------------------

def _load_pending() -> dict:
    """Return the `items` dict from `_pending_review.json`, or {} if absent
    / malformed. The same shape backfill_filings.py / repair_dates.py use."""
    if not PENDING_PATH.exists():
        return {}
    try:
        return (
            json.loads(PENDING_PATH.read_text(encoding="utf-8")).get("items")
            or {}
        )
    except json.JSONDecodeError:
        return {}


def _atomic_write(path: Path, payload: dict) -> None:
    """Delegate to db.atomic_write_json (B-038, 2026-05-21).

    The wrapper is preserved so the existing callers (_save_pending,
    _save_resolved) keep their concise signature without each one
    importing `db` directly.
    """
    db.atomic_write_json(path, payload)


def _save_pending(items: dict) -> None:
    _atomic_write(
        PENDING_PATH,
        {"generated_at": db.iso_now(), "count": len(items), "items": items},
    )


def _load_resolved() -> dict:
    """Audit log of every fingerprint this sweep has resolved historically.
    Each run appends to the existing dict so we don't lose context if the
    sweep is re-run later."""
    if not RESOLVED_PATH.exists():
        return {}
    try:
        return (
            json.loads(RESOLVED_PATH.read_text(encoding="utf-8")).get("items")
            or {}
        )
    except json.JSONDecodeError:
        return {}


def _save_resolved(items: dict) -> None:
    _atomic_write(
        RESOLVED_PATH,
        {"generated_at": db.iso_now(), "count": len(items), "items": items},
    )


# --- candidate selection ----------------------------------------------------

def _is_stuck_on_date_candidate(entry: dict) -> bool:
    """Return True if this entry matches the B-002 target population.

    Rule (matches the 2026-05-18 investigation): the entry's warnings
    contain `could_not_parse_tx_date`, the extracted list is empty
    (nothing already on file), and no prior LLM attempt has failed.
    Anything else stays in pending untouched.
    """
    warnings = entry.get("warnings") or []
    if not any(STUCK_ON_DATE_WARNING in w for w in warnings):
        return False
    if any(w.startswith(PRIOR_LLM_FAIL_PREFIX) for w in warnings):
        return False
    if entry.get("extracted"):
        return False
    return True


def _load_already_resolved_ids() -> set[str]:
    """Return the union of manually-added and rejected RNS IDs from apply_edits.py.

    Sprint 25 Phase 3 sticky protection: if Rupert manually added or rejected
    a filing via the /review UI, the pending sweep must not re-process it.
    Both manifests are written atomically by apply_edits.py (Zone B) and read
    here as plain JSON -- read-only, safe from any caller.
    """
    ids: set[str] = set()
    for path in (_REJECTED_IDS_PATH, _MANUAL_IDS_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Both files use different keys but same shape: {key: [rns_id, ...]}
            for key in ("rejected_rns_ids", "manual_rns_ids"):
                ids.update(data.get(key) or [])
        except Exception:
            pass  # missing or malformed manifest -- fail open (skip nothing)
    return ids


def _select_candidates(pending: dict, target: str) -> list[str]:
    """Return the list of rns_ids to sweep, in iteration order.

    Always excludes IDs that have been manually resolved or rejected via
    apply_edits.py (Phase 3 sticky protection) so that a Rupert-curated
    manual add is never overwritten by a subsequent LLM sweep.
    """
    already_resolved = _load_already_resolved_ids()
    if already_resolved:
        print(f"[sweep] Sticky skip: {len(already_resolved)} RNS ID(s) already "
              f"resolved via apply_edits (rejected or manually added).")

    if target == "all":
        candidates = [rid for rid in pending.keys()
                      if rid not in already_resolved]
    elif target == "stuck-on-date":
        candidates = [rid for rid, e in pending.items()
                      if _is_stuck_on_date_candidate(e)
                      and rid not in already_resolved]
    else:
        raise ValueError(f"unknown --target {target!r}")

    skipped = len(pending) - len(candidates)
    if already_resolved:
        print(f"[sweep] After sticky skip: {len(candidates)} candidate(s) "
              f"({skipped} skipped total).")
    return candidates


# --- permanent-error detection ---------------------------------------------

def _looks_like_permanent_api_failure(exc: Exception) -> bool:
    """An HTTP 4xx from the Anthropic API will NOT clear on retry --
    it's an auth/credit/malformed-request issue. Hammering the API
    4,000 more times wastes the user's time and floods the log.

    The LLM parser wraps HTTPError as
    `LLMParserError("Anthropic API HTTP <code> ...")`, so we sniff for
    that exact prefix. We also catch obvious bodies-only signals
    (credit balance, invalid api key) defensively in case the wrapper
    format changes.
    """
    msg = str(exc).lower()
    if "anthropic api http 40" in msg or "anthropic api http 41" in msg \
            or "anthropic api http 42" in msg or "anthropic api http 43" in msg:
        return True
    if "credit balance" in msg or "invalid api key" in msg \
            or "authentication" in msg or "permission_error" in msg:
        return True
    return False


# --- sanity-check helpers ---------------------------------------------------

def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _date_within_window(extracted_date: str, announced_at: str,
                         window_days: int = SANITY_WINDOW_DAYS) -> bool:
    """AC: reject any LLM date more than `window_days` away from
    announced_at. Defends against an LLM hallucination that returns
    e.g. a year-old date because the filing happens to mention it
    somewhere in the prose. If `announced_at` is unparseable we accept
    the extracted date (no anchor to compare against)."""
    a = _parse_iso_date(announced_at)
    if a is None:
        return True
    e = _parse_iso_date(extracted_date)
    if e is None:
        return False
    return abs((e - a).days) <= window_days


# --- pipeline re-run --------------------------------------------------------

def _run_pipeline(no_pipeline: bool, verbose: bool) -> int:
    """Trigger eval_signals.py → export_dashboard_json.py → build_dashboard.py
    as subprocesses so each step runs in isolation -- same convention
    refresh_all.py uses.  export_dashboard_json.py is required between eval
    and build so the dashboard reads current JSON (CLAUDE.md memory).
    Returns 0 on success, non-zero on the first step that fails."""
    if no_pipeline:
        print("Skipping pipeline re-run (--no-pipeline).")
        return 0
    py = sys.executable or "python"
    for step in ("eval_signals.py", "export_dashboard_json.py", "build_dashboard.py"):
        script = HERE / step
        print(f"\n>> Running {step} ...")
        result = subprocess.run([py, str(script)], cwd=str(HERE))
        if result.returncode != 0:
            print(f"!! {step} exited {result.returncode}; aborting.")
            return result.returncode
        if verbose:
            print(f"   {step} OK")
    return 0


# --- main loop --------------------------------------------------------------

def run(args) -> int:
    # Import here so --help works without ANTHROPIC_API_KEY set.
    try:
        import llm_parser  # type: ignore
    except ImportError as e:
        print(f"ABORT: llm_parser unavailable -- {e}")
        return 5

    pending = _load_pending()
    if not pending:
        print("No entries in _pending_review.json. Nothing to do.")
        return 0

    print(f"Loaded {len(pending)} pending entries.")

    try:
        candidates = _select_candidates(pending, args.target)
    except ValueError as e:
        print(f"ABORT: {e}")
        return 3

    print(f"Selected {len(candidates)} candidate(s) under --target "
          f"{args.target} ({len(pending) - len(candidates)} skipped).")

    if not candidates:
        print("No matching candidates. Nothing to sweep.")
        return 0

    if args.max_rows:
        rns_ids = candidates[: args.max_rows]
        print(f"Limiting to first {len(rns_ids)} (--max-rows).")
    else:
        rns_ids = candidates

    run_id = None
    if llm_cost is not None:
        run_id = llm_cost.start_run()

    resolved = _load_resolved()
    conn = db.connect()

    stats = {
        "considered":           0,
        "recovered":            0,
        "no_cache":             0,
        "no_llm_extract":       0,
        "date_out_of_window":   0,
        "missing_announced_at": 0,
        "error":                0,
        "aborted_permanent":    0,
    }

    try:
        for rns_id in rns_ids:
            stats["considered"] += 1
            entry = pending[rns_id]
            url = entry.get("url") or ""
            announced_at = entry.get("announced_at") or ""

            # Budget check BEFORE the next LLM call so we abort cleanly
            # rather than overshoot.
            if llm_cost is not None and run_id is not None:
                try:
                    llm_cost.check_budget(run_id, args.budget_usd)
                except Exception as e:
                    print(f"!! Budget exceeded ({args.budget_usd} USD): {e}")
                    print("   Stopping sweep; partial progress is saved.")
                    break

            cache_path = CACHE_DIR / f"{rns_id}.html"
            if not cache_path.exists():
                print(f"  SKIP {rns_id}: cache HTML missing at {cache_path}")
                # Leave entry in pending; do NOT re-fetch (scope: out of scope).
                stats["no_cache"] += 1
                continue

            # Read the cached HTML once -- both the announced_at recovery
            # step (B-015) and the subsequent LLM call need it.
            try:
                html = cache_path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"  SKIP {rns_id}: read error {e}")
                stats["error"] += 1
                continue

            # B-015: if the pending entry lacks `announced_at` (typical for
            # rows that were moved to pending by repair_dates.py -- those
            # entries only carry url/warnings/extracted/parser_source/
            # repair_note), recover it from the cached HTML's JSON-LD
            # dateCreated block before calling the LLM. Without an anchor
            # the ±60-day sanity guard at lines below cannot fire and the
            # LLM's output would be accepted blind. The 2026-05-18 QA
            # review caught this; the original B-002 sweep blind-accepted
            # CPIC's LLM-extracted date.
            if not announced_at:
                derived = _extract_announced_at_from_html(html)
                if derived:
                    announced_at = derived
                    # Persist the derived value to the live pending dict so
                    # downstream code (resolved-audit write below) carries it
                    # through. If this run fails the next one re-derives
                    # cheaply (one regex on 3KB).
                    entry["announced_at"] = announced_at
                    print(f"  ANCHOR {rns_id}: derived announced_at from "
                          f"cached HTML dateCreated -> {announced_at}")
                else:
                    print(f"  SKIP {rns_id}: pending entry has no "
                          "announced_at AND cached HTML has no JSON-LD "
                          "dateCreated. Cannot validate LLM output. "
                          "Leaving in pending.")
                    stats["missing_announced_at"] += 1
                    continue

            try:
                extracted, warnings = llm_parser.parse_with_llm(
                    html, url, rns_id, announced_at,
                    run_id=run_id,
                )
            except llm_parser.MissingApiKeyError:
                print("ABORT: ANTHROPIC_API_KEY not set. Add to .env first.")
                return 4
            except Exception as e:
                print(f"  ERROR {rns_id}: {type(e).__name__}: {e}")
                stats["error"] += 1
                # Defence against the 2026-05-18 incident: a credit-low
                # HTTP 400 from the Anthropic API will repeat for every
                # remaining entry. Abort the loop after the first such
                # failure so we don't flood the console with thousands
                # of identical errors. Re-raise / break out cleanly.
                if _looks_like_permanent_api_failure(e):
                    print("\nABORT: this looks like a PERMANENT API failure "
                          "(auth / credit / bad request).")
                    print("       Halting sweep -- top up the Anthropic "
                          "account or fix the key, then re-run.")
                    print("       Per-row save protects any rows already "
                          "recovered; they will not be re-charged.")
                    stats["aborted_permanent"] = 1
                    break
                continue

            if not extracted:
                print(f"  EMPTY {rns_id}: LLM returned no rows  "
                      f"warnings={warnings}")
                stats["no_llm_extract"] += 1
                continue

            # Sanity-check every extracted date against announced_at.
            # The `announced_at` is guaranteed present here -- the pre-LLM
            # gate above rejects any entry without it. So the "blind keep"
            # path that used to live here has been removed.
            kept = []
            for ex in extracted:
                if _date_within_window(ex.get("date") or "", announced_at):
                    kept.append(ex)
                else:
                    print(f"  REJECT {rns_id}: extracted date "
                          f"{ex.get('date')!r} is >{SANITY_WINDOW_DAYS}d "
                          f"away from announced_at {announced_at}")
                    stats["date_out_of_window"] += 1

            if not kept:
                continue  # leave pending entry in place

            # Upsert each kept row with parser_source='llm'.
            for ex in kept:
                db.upsert_transaction(conn, ex, parser_source="llm",
                                      verbose=args.verbose)
            # B-028 (2026-05-21): commit per resolved pending entry now
            # that `db.upsert_transaction` no longer commits per row.
            # Per-entry commit matches the per-entry _save_pending /
            # _save_resolved writes below, so a crash between the DB
            # commit and the JSON writes leaves the recovered row
            # safely in `transactions` and still listed in pending
            # (next sweep re-resolves it harmlessly).
            conn.commit()

            # Resolve: move pending -> resolved.
            resolved[rns_id] = {
                **entry,
                "resolved_at": db.iso_now(),
                "llm_extracted": kept,
                "llm_warnings": warnings,
            }
            del pending[rns_id]
            # Persist after every successful resolution so a crash mid-loop
            # never re-bills us for already-processed rows.
            _save_pending(pending)
            _save_resolved(resolved)
            stats["recovered"] += 1
            print(f"  OK    {rns_id}: recovered {len(kept)} row(s)")

    finally:
        conn.close()
        if run_id is not None and llm_cost is not None:
            try:
                llm_cost.end_run(run_id)
            except Exception:
                pass  # ledger close is best-effort

    # Summary
    print("\n=== run_pending_sweep summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if llm_cost is not None and run_id is not None:
        # Best-effort cost printout -- the ledger module owns formatting.
        try:
            print(f"  run_id: {run_id}")
        except Exception:
            pass

    # Trigger the pipeline only if we actually wrote rows -- otherwise
    # there's nothing new to surface and the rebuild would just churn.
    # Also skip on a permanent-API abort: pipeline can't fix a credit
    # problem, so don't tie its outcome to the abort signal.
    if stats["recovered"] == 0:
        print("\nSkipping pipeline re-run: no rows recovered.")
        if stats["aborted_permanent"]:
            return 6  # distinct exit code so a re-run script can react
        return 0
    rc = _run_pipeline(args.no_pipeline, args.verbose)
    # B-024 + B-034: ALWAYS attempt the post-run seal — both on pipeline
    # success and failure. Rationale: db.upsert_transaction has already
    # committed the recovered LLM rows by the time we get here. If the
    # pipeline (eval_signals → build_dashboard) then fails, the DB still
    # holds new data the existing .bak doesn't have. Without a seal here,
    # a subsequent FUSE event would lose those rows.
    #
    # The C-3 post-run integrity_check still guards us: if post-run state
    # is corrupt, skip seal so the existing .bak stays the rollback
    # target (a stale-but-clean .bak is safer than a fresh-but-corrupt
    # one). Previous behaviour gated seal on `rc == 0` which created a
    # silent drift window.
    try:
        import db_health  # type: ignore
        if db_health.check(db.DB_PATH):
            db_health.seal()
        else:
            print("[run_pending_sweep] WARNING: post-run integrity_check "
                  "failed. Skipping seal to preserve the existing .bak "
                  "as the rollback target. Restore via start.bat if "
                  "needed.")
    except Exception as e:
        print(f"[db_health] post-sweep seal failed (non-fatal): {e}")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "B-002 LLM sweep: re-try pending PDMR filings with Claude "
            "Sonnet, write recovered rows back to transactions, and "
            "re-run the dashboard pipeline."
        )
    )
    ap.add_argument("--target", choices=("stuck-on-date", "all"),
                    default="stuck-on-date",
                    help=("Which pending entries to sweep. Default "
                          "`stuck-on-date` selects only the ~7 entries "
                          "where the regex parser couldn't find a "
                          "transaction date AND no prior LLM attempt has "
                          "failed -- the original B-002 scope. `all` "
                          "sweeps every entry; almost never the right "
                          "choice (the pending file accumulates ALL "
                          "failed parses, ~4k entries as of 2026-05)."))
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                    help=f"Hard LLM spend ceiling in USD "
                         f"(default {DEFAULT_BUDGET_USD}). Aborts mid-loop "
                         "if the ledger crosses this.")
    ap.add_argument("--max-rows", type=int, default=None,
                    help="Process at most N pending entries (for dry-run).")
    ap.add_argument("--no-pipeline", action="store_true",
                    help="Skip the eval_signals + build_dashboard re-run.")
    ap.add_argument("--verbose", action="store_true",
                    help="Log every upsert and pipeline step.")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
