"""Stage 5 dashboard Refresh-All orchestrator.

Runs the full pipeline end-to-end in a single subprocess sequence and
writes step-by-step status to ``.data/_refresh_status.json`` so the
dashboard server can poll progress.

Steps (in order)::

    1. run_scrape           -- pull new Investegate RNS filings (LLM cost)
    2. backfill_prices      -- incremental Yahoo OHLCV
    3. backfill_benchmarks  -- sector benchmark refresh
    4. eval_signals         -- recompute signal firings
    5. backfill_lse_diary   -- forward earnings calendar (SOFT: non-blocking)
    6. backfill_expected_reporting_dates -- "(est)" dates for uncovered
                               holdings (SOFT: non-blocking)
    7. export_dashboard_json -- rebuild signals.json + dealings.json
    8. build_dashboard      -- regen all HTML pages

Each step is a ``python -u .scripts/<step>.py`` subprocess with a
per-step timeout. Status JSON is updated atomically after each step.

The pipeline can be killed cleanly: any in-flight subprocess will be
terminated and the status file marked ``status=cancelled``.

CLI (also used as the server-side worker)::

    python -u .scripts/refresh_all.py [--scrape-days N] [--no-llm]
                                       [--skip-scrape] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATUS_PATH = ROOT / ".data" / "_refresh_status.json"

_SCRAPE_STATS_RE = re.compile(r"^SCRAPE_STATS\s+(\{.*\})\s*$", re.MULTILINE)


def _check_scrape_volume_anomaly(stdout: str) -> None:
    """Warn (non-blocking) if today's scrape landed far fewer new
    transactions than the recent daily average.

    B-198 (2026-07-06): added after a real, confirmed PDMR under-collection
    (~2/day landing vs ~22/day of real filings) went unnoticed for several
    days because the daily-refresh workflow's own success/failure status
    doesn't reflect discovery volume -- a scrape that finds almost nothing
    still exits 0 as long as it doesn't crash, and its diagnostics used to
    be captured into a status-JSON tail that nothing in CI ever reads.
    This is a WARNING ONLY: a genuinely quiet day (bank holiday, thin news
    day) must still complete normally, so this never touches
    `base_state["status"]` or any step's rc -- it only prints a
    `::warning::` GitHub Actions annotation (surfaces in the run summary)
    so a real collapse is visible going forward without anyone having to
    notice the dashboard looks thin.
    """
    try:
        m = _SCRAPE_STATS_RE.search(stdout)
        if not m:
            return  # older run_scrape.py, --dry-run, or failed before stats printed
        stats = json.loads(m.group(1))
        inserts = int(stats.get("inserts") or 0)

        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import db  # noqa: PLC0415
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT date(first_seen) AS d, COUNT(*) AS n "
                "FROM transactions "
                "WHERE datetime(first_seen) >= datetime('now', '-8 days') "
                "  AND date(first_seen) < date('now') "
                "GROUP BY d"
            ).fetchall()
        finally:
            conn.close()
        daily_counts = [r["n"] for r in rows]
        if not daily_counts:
            return  # no recent history yet to compare against
        baseline = sum(daily_counts) / len(daily_counts)

        # Thresholds are deliberately loose (baseline floor of 5/day, alarm
        # only below 25% of baseline) to avoid false alarms on naturally
        # quiet days while still catching a real collapse like 2/day vs ~22.
        if baseline >= 5 and inserts < 0.25 * baseline:
            index_err = stats.get("index_error")
            archive_err = stats.get("archive_error")
            hint = ""
            if index_err or archive_err:
                hint = f" (index_error={index_err!r}, archive_error={archive_err!r})"
            print(
                "::warning::PDMR volume looks low: "
                f"{inserts} new transactions today vs a {baseline:.1f}/day "
                f"trailing-week average (index_count={stats.get('index_count')}, "
                f"archive_count={stats.get('archive_count')}, "
                f"pending={stats.get('pending_count')}){hint}. "
                "This does not fail the run -- check the scrape step log above."
            )
    except Exception as e:  # noqa: BLE001 - advisory only, never break the pipeline
        print(f"[refresh_all] (non-fatal) volume-anomaly check failed: {e!r}")


def _warn_if_thin_history() -> None:
    """Print a clear warning if the transactions table has no year of history.

    This is a non-blocking advisory: if the oldest transaction is within the
    last 60 days (or the table is empty) the user needs to run
    `python .scripts/backfill_filings.py --resume` first to load the full
    historic archive. The pipeline still proceeds — it just won't have enough
    data for the signal engine to be meaningful.
    """
    # B-013: the original pattern was `conn = db.connect(); conn.execute(...);
    # conn.close()` all inside one broad `except Exception: pass`. If the
    # SELECT raised between connect and close, Windows held the SQLite file
    # lock until process exit. Inner try/finally guarantees conn.close()
    # even on a fetchone() error.
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import db  # noqa: PLC0415
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT MIN(date) AS oldest, COUNT(*) AS n FROM transactions"
            ).fetchone()
        finally:
            conn.close()
        if not row or not row["oldest"]:
            print(
                "\n[refresh_all] ⚠  WARNING: transactions table is empty.\n"
                "   Run the one-time historic backfill first:\n"
                "       python .scripts/backfill_filings.py --resume\n"
            )
            return
        from datetime import date  # noqa: PLC0415
        oldest = date.fromisoformat(row["oldest"])
        age_days = (date.today() - oldest).days
        if age_days < 60:
            print(
                f"\n[refresh_all] ⚠  WARNING: oldest transaction is only {age_days} days old "
                f"({row['n']} rows total). A full year of history is needed for "
                "the signal engine to be meaningful.\n"
                "   Run the one-time historic backfill first:\n"
                "       python .scripts/backfill_filings.py --resume\n"
            )
    except Exception:  # noqa: BLE001
        pass  # advisory only — never block the pipeline


def _compute_scrape_days() -> int:
    """Return how many days back to scrape based on the newest transaction in the DB.

    Logic:
      * Read MAX(date) from the transactions table.
      * scrape_days = (today - last_date).days + 2   (2-day buffer for late filings)
      * Clamp between 3 (minimum) and 60 (safety cap).
      * Falls back to 7 if the DB is missing, corrupted, or has no rows.
    """
    # B-013: same pattern as _warn_if_thin_history above. Inner try/finally
    # so a SELECT failure doesn't leave the SQLite connection open.
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import db  # noqa: PLC0415
        conn = db.connect()
        try:
            row = conn.execute("SELECT MAX(date) AS d FROM transactions").fetchone()
        finally:
            conn.close()
        if row and row["d"]:
            last_date = date.fromisoformat(row["d"])
            delta = (date.today() - last_date).days
            return max(3, min(delta + 2, 60))
    except Exception:  # noqa: BLE001
        pass
    return 7  # safe fallback: one week covers any brief gap


# Step config: (key, label, script_relpath, default_args, timeout_sec).
# NOTE: scrape step args are overridden at runtime by run_pipeline().
#
# The "audit" step runs WITHOUT --gate inside the loop -- it always exits 0
# so the pipeline can finish rendering the dashboard with the red panel
# visible. The gate is enforced AFTER the loop in run_pipeline() by reading
# _date_audit_report.json["overall"] and overriding final status to "error".
STEPS = [
    ("scrape",     "Fetching new RNS filings",
     "run_scrape.py",            ["--days", "7"],    60 * 30),
    ("prices",     "Backfilling share prices",
     "backfill_prices.py",       ["--rate-limit", "0.5"], 60 * 20),
    ("benchmarks", "Updating sector benchmarks",
     "backfill_benchmarks.py",   ["--rate-limit", "0.5"], 60 * 10),
    ("sectors",    "Resolving ticker sectors + AIM flags",
     "fetch_sectors.py",         [],                 60 * 5),
    # FMP sector backfill: fills sector for tickers not in sector_map.csv.
    # Only fetches tickers with NULL sector, so fast on typical days (~5s).
    # SOFT: rate-limit hits or API errors don't abort the pipeline.
    ("sector_fmp", "Backfilling missing sectors via FMP",
     "backfill_sectors.py",      [],                 60 * 10, True),
    # B-011 / Sprint 10 Phase 2: classify IT/CEF/VCT/REIT issuers BEFORE
    # signals so the universe filter in eval_signals._universe_rows
    # (Phase 1) sees up-to-date is_excluded_issuer flags. Default
    # --no-yahoo per Gate 1 (AIC + name regex only; Yahoo sweep is
    # opt-in for manual classifier runs to avoid burning Yahoo
    # quota during every routine refresh).
    ("classify",   "Classifying IT/CEF/VCT/REIT issuers",
     "classify_issuers.py",      ["--no-yahoo"],     60 * 5),
    # B-151: classify new-company arrivals BEFORE signals fire.
    # --missing-only ensures only stubs with no market_cap_gbp are fetched,
    # so this step adds ~5s for a typical daily run with no new companies.
    # Gate 1 in eval_signals (benchmark_symbol IS NOT NULL) holds any ticker
    # that backfill_market_cap doesn't resolve (LSE 404 / MISS) out of signals
    # permanently until the next successful fetch.
    ("mktcap",     "Enriching new companies with market cap data",
     "backfill_market_cap.py",   ["--missing-only"], 60 * 10),
    # Apply £500m small/large threshold immediately after so performance pages
    # can segment the newly classified companies in the same pipeline run.
    ("smallcap",   "Applying small/large cap classification (GBP 500m)",
     "classify_small_cap.py",    ["--threshold", "500000000"], 60 * 2),
    # B-200 (2026-08-21): FCA short-interest ingest was never wired into the
    # daily job, so `short_positions` froze at 2026-06-09 while the front
    # page's short-interest column kept rendering it as current. SOFT: the
    # FCA workbook URL is a third-party download and must never be able to
    # block the price/signal core. --no-figi keeps the daily run off the
    # OpenFIGI API (quota); run it by hand without the flag to resolve any
    # newly-unmapped ISINs.
    ("shorts",     "Ingesting FCA short interest",
     "backfill_short_interest.py", ["--no-figi"],  60 * 15, True),
    # Timeouts widened for the cloud (Postgres-over-network): the signal
    # engine + backtest fire many small queries, each with network latency,
    # so they run far slower than against local SQLite. 15min was too tight
    # and tripped run #2. Generous caps; actual runtime is ~10-20min.
    # B-209 (2026-08-28): price-unit reconciliation. This is NOT new code --
    # backfill_price_units.py + the pure price_reconcile module have existed
    # since B-060, are unit-tested, idempotent and reversible, and eval_signals
    # already refuses to fire on rows they flag (see its price_audit filter).
    # The script was simply never wired into the pipeline: it was marked "Zone B
    # -- Rupert runs this from PowerShell", which stopped being a workable plan
    # the day the pipeline moved into GitHub Actions.
    #
    # The cost of it not running, measured on the 2026-08-28 catch-up: a GBP
    # 11k director buy stored as GBP 999,900 and a GBP 4.3k buy stored as GBP
    # 432,000, both AIM small caps quoted in pence. The conviction engine ranks
    # on buy SIZE, so those land at the TOP of the signal list. Wrong data that
    # looks confident is worse than no data.
    #
    # Must run AFTER prices (it disambiguates against the market close) and
    # BEFORE signals (which is what it protects).
    #
    # HARD step, deliberately. It touches no network and no third party, so it
    # has no legitimate transient failure mode -- if this breaks, something real
    # is wrong and publishing unvalidated prices is the worse outcome.
    ("price_units", "Reconciling price units (pence vs pounds)",
     "backfill_price_units.py",  ["--confirm"],      60 * 15),
    ("signals",    "Recomputing signal firings",
     "eval_signals.py",          [],                 60 * 45),
    ("backtest",   "Computing signal backtests + CAR",
     "backtest.py",              [],                 60 * 45),
    # B-201 (2026-08-21): close_paper_trades.py was never wired into the daily
    # job, so the paper book stopped marking out on 2026-06-04: 338 'linear'
    # trades sat `open` past their horizon and the entire 'log' book had zero
    # closed trades. It upgrades planned->open once an entry price exists and
    # closes any open trade whose 21-trading-day horizon has passed, so it must
    # run AFTER eval_signals/backtest (which create the trades) and BEFORE
    # export (which publishes the book). SOFT: a price gap must not block the
    # dashboard build.
    ("close_paper", "Marking out matured paper trades",
     "close_paper_trades.py",    ["--horizon", "21"], 60 * 10, True),
    # Conviction scoring — score every BUY in the trailing 28-day window and
    # upsert to conviction_scores. Runs AFTER backtest (all deals processed)
    # and BEFORE export. SOFT: a transient data gap logs and continues.
    ("conviction", "Scoring director conviction buys",
     "conviction_pipeline.py",  [],                 60 * 5, True),
    # Forward earnings calendar — refresh BEFORE export so the upcoming-events
    # panel and the 60-day pre-results badges reflect today's dates. Both steps
    # carry a trailing True = SOFT: a transient LSE outage logs and the pipeline
    # continues; the calendar must never block the price/signal core. The diary
    # writes confirmed results dates; the est gap-filler then projects a marked
    # "(est)" next-results date for every held ticker the diary doesn't cover.
    ("diary",      "Refreshing forward earnings calendar (LSE Diary)",
     "backfill_lse_diary.py",    [],                 60 * 10, True),
    ("est_dates",  "Estimating earnings dates for uncovered holdings",
     "backfill_expected_reporting_dates.py", [],     60 * 5,  True),
    ("export",     "Rebuilding signals + dealings JSON",
     "export_dashboard_json.py", [],                 60 * 20),
    ("audit",      "Auditing date integrity",
     "audit_dates.py",           ["--verbose"],      60 * 5),
    ("build",      "Regenerating HTML pages",
     "build_dashboard.py",       [],                 60 * 10),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(state: dict) -> None:
    """Atomic write of the status JSON. Safe for concurrent polling."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, STATUS_PATH)


def read_status() -> dict:
    if not STATUS_PATH.exists():
        return {"status": "idle"}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "idle"}


def _is_running() -> bool:
    st = read_status()
    return st.get("status") == "running"


def run_pipeline(*, scrape_days: int | None = None, no_llm: bool = False,
                 skip_scrape: bool = False,
                 verbose: bool = False) -> dict:
    """Run the full pipeline. Updates STATUS_PATH after each step."""
    # ── DB health gate ────────────────────────────────────────────────────────
    # Guard before touching the DB: restore from backup if corrupted.
    # This runs on the user's Windows machine where FUSE is not involved,
    # so shutil.copy2 is safe.
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import db  # noqa: PLC0415
        import db_health  # noqa: PLC0415
        # B-179: the .bak restore/guard is a SQLite/FUSE corruption defence.
        # On Postgres there is no local .db file, so skip the guard entirely.
        if db.backend() == "sqlite":
            db_health.guard()
    except SystemExit as e:
        # guard() exits with code 2 when DB is unrecoverable.
        state = {
            "status": "error",
            "error": "DB corrupted and backup unavailable. Delete .data/directors.db and retry.",
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
            "completed": [], "log": [], "step": None, "step_label": None,
        }
        write_status(state)
        return state

    # Advisory: warn if the DB looks like a fresh install with no history.
    _warn_if_thin_history()

    started_at = _now_iso()
    base_state = {
        "status":     "running",
        "started_at": started_at,
        "step":       None,
        "step_label": None,
        "completed":  [],
        "log":        [],
        "error":      None,
    }
    write_status(base_state)

    def _set(step_key, step_label):
        base_state["step"] = step_key
        base_state["step_label"] = step_label
        base_state["updated_at"] = _now_iso()
        write_status(base_state)

    # Resolve scrape window once (auto-detect delta if not explicitly set).
    effective_scrape_days = scrape_days if scrape_days is not None else _compute_scrape_days()
    if verbose:
        src = "explicit" if scrape_days is not None else "auto-detected"
        print(f"[refresh_all] scrape_days={effective_scrape_days} ({src})")

    for step in STEPS:
        key, label, script, default_args, timeout = step[:5]
        # Optional 6th element marks a SOFT step: failure/timeout logs and the
        # pipeline continues instead of aborting (used for the calendar steps).
        soft = step[5] if len(step) > 5 else False
        if skip_scrape and key == "scrape":
            base_state["completed"].append({
                "step": key, "skipped": True,
                "finished_at": _now_iso(),
            })
            continue

        args = list(default_args)
        if key == "scrape":
            args = ["--days", str(effective_scrape_days)]
            if no_llm:
                args.append("--no-llm")
            # B-207: the 30-minute scrape budget fits the ~3-day daily window.
            # A catch-up run (--scrape-days 30 after an outage) re-fetches and
            # re-parses weeks of filings and will otherwise be killed mid-way.
            # Roughly 6 minutes of headroom per day of history, capped at 3h.
            if effective_scrape_days > 10:
                timeout = max(timeout, 60 * min(180, effective_scrape_days * 6))

        _set(key, label)

        cmd = [sys.executable, "-u", str(HERE / script)] + args
        if verbose:
            print(f"[refresh_all] step={key} cmd={cmd}")
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(ROOT), capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            if soft:
                base_state["completed"].append({
                    "step": key, "label": label, "soft_failed": True,
                    "error": f"timed out after {timeout}s",
                    "finished_at": _now_iso(),
                })
                base_state["log"].append(
                    f"[{_now_iso()}] {key}: SOFT timeout after {timeout}s — skipped")
                write_status(base_state)
                continue
            base_state["status"] = "error"
            base_state["error"] = (
                f"Step '{key}' timed out after {timeout}s"
            )
            base_state["finished_at"] = _now_iso()
            write_status(base_state)
            return base_state
        except Exception as e:  # noqa: BLE001
            if soft:
                base_state["completed"].append({
                    "step": key, "label": label, "soft_failed": True,
                    "error": repr(e), "finished_at": _now_iso(),
                })
                base_state["log"].append(
                    f"[{_now_iso()}] {key}: SOFT error {e!r} — skipped")
                write_status(base_state)
                continue
            base_state["status"] = "error"
            base_state["error"] = f"Step '{key}' raised: {e!r}"
            base_state["finished_at"] = _now_iso()
            write_status(base_state)
            return base_state

        duration = round(time.time() - t0, 1)
        completed_row = {
            "step":     key,
            "label":    label,
            "rc":       proc.returncode,
            "duration": duration,
            "finished_at": _now_iso(),
        }
        # Persist tail of stdout/stderr for debugging.
        stdout_tail = (proc.stdout or "")[-2000:]
        stderr_tail = (proc.stderr or "")[-2000:]
        completed_row["stdout_tail"] = stdout_tail
        completed_row["stderr_tail"] = stderr_tail
        base_state["completed"].append(completed_row)
        base_state["log"].append(
            f"[{_now_iso()}] {key}: rc={proc.returncode} dur={duration}s"
        )

        # B-198 (2026-07-06): the scrape step's own diagnostics (per-source
        # discovery counts, parse/ingest outcomes, the SCRAPE_STATS line)
        # used to be captured here and only ever written into the 2000-char
        # tail above -- nothing printed them to the actual CI console, so a
        # silent volume collapse was invisible in the GitHub Actions log
        # (confirmed investigating the 2026-07-06 PDMR under-collection,
        # which required manually pulling raw run logs to diagnose). Print
        # this step's full stdout/stderr unconditionally -- regardless of
        # rc -- so it's diagnosable from the Actions log alone from now on.
        if key == "scrape":
            print(f"[refresh_all] ---- scrape step output (rc={proc.returncode}) ----")
            if proc.stdout:
                print(proc.stdout)
            if proc.stderr:
                print("[refresh_all] scrape stderr:")
                print(proc.stderr)
            print("[refresh_all] ---- end scrape step output ----")
            _check_scrape_volume_anomaly(proc.stdout or "")

        if proc.returncode != 0:
            if soft:
                base_state["log"].append(
                    f"[{_now_iso()}] {key}: SOFT failure rc={proc.returncode} "
                    f"— continuing. stderr: {stderr_tail[-300:]}")
                write_status(base_state)
                continue
            base_state["status"] = "error"
            base_state["error"] = (
                f"Step '{key}' failed (rc={proc.returncode}). "
                f"stderr: {stderr_tail[-500:]}"
            )
            base_state["finished_at"] = _now_iso()
            write_status(base_state)
            return base_state

        write_status(base_state)

    # ── Gate on date-integrity audit ──────────────────────────────────────────
    # The audit step earlier in the loop always wrote a fresh report at
    # .data/_date_audit_report.json. The dashboard build picked it up and
    # rendered the panel. NOW we fail the overall pipeline if any
    # invariant failed -- so the user can see the red panel AND knows the
    # run wasn't clean.
    audit_report_path = ROOT / ".data" / "_date_audit_report.json"
    try:
        audit_report = json.loads(audit_report_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        audit_report = None

    if audit_report and audit_report.get("overall") == "FAIL":
        summary = audit_report.get("summary") or {}
        failing = [
            f"{k} ({v.get('bad', 0)} bad)"
            for k, v in summary.items() if not v.get("pass", False)
        ]
        base_state["status"] = "error"
        base_state["error"] = (
            "Date integrity audit failed: " + ", ".join(failing) +
            ". See .data/_date_audit_report.json and the dashboard "
            "Data Quality panel."
        )
        base_state["finished_at"] = _now_iso()
        write_status(base_state)
        return base_state

    base_state["status"] = "done"
    base_state["step"] = None
    base_state["step_label"] = None
    base_state["finished_at"] = _now_iso()
    write_status(base_state)

    # ── Seal: take a fresh backup now the pipeline succeeded ─────────────────
    # B-179: local-SQLite-only backup; no-op on Postgres.
    try:
        import db  # noqa: PLC0415
        import db_health  # noqa: PLC0415
        if db.backend() == "sqlite":
            db_health.seal()
    except Exception:  # noqa: BLE001
        pass  # backup failure is non-fatal

    return base_state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refresh-All pipeline driver.")
    ap.add_argument("--scrape-days", type=int, default=None,
                    help="Days back to scrape RNS (default: auto-detect from DB).")
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip LLM fallback in the parser.")
    ap.add_argument("--skip-scrape", action="store_true",
                    help="Skip the scrape step (HTML-only rebuild path).")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    state = run_pipeline(
        scrape_days=args.scrape_days,
        no_llm=args.no_llm,
        skip_scrape=args.skip_scrape,
        verbose=args.verbose,
    )
    print(json.dumps({"status": state.get("status"),
                      "error": state.get("error")}, indent=2))
    return 0 if state.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
