"""Targeted data-fix: re-flip five mis-classified SELL rows back to BUY.

WRITE-PATH SCRIPT — RUN FROM WINDOWS POWERSHELL ONLY.

  ⚠️ FUSE rule (CLAUDE.md Two-zone rule): this script opens
  `.data/directors.db` for WRITING. Claude's Linux sandbox accesses the
  project via a FUSE mount that truncates non-sequential binary writes →
  SQLite corruption. NEVER run this from Claude's bash. Rupert runs it
  from PowerShell; Claude only authors + dry-runs the read-only audit.

------------------------------------------------------------------------
WHAT THIS FIXES
------------------------------------------------------------------------
Five director BUYS were ingested as SELL by the (now-fixed) whole-page
type-classification bug. The parser fix (scoped `_classify_type` on the
'Nature of the transaction' cell / bounded tx block) is already live, so
re-parsing each cached page now yields the correct BUY. But these rows
were ingested BEFORE the fix and remain wrong in the DB.

THE FINGERPRINT TRAP
--------------------
The row fingerprint is `sha1(date|ticker|director|type|shares)` — it
INCLUDES `type`. So a corrected BUY has a DIFFERENT fingerprint from the
stored SELL. Simply re-parsing and re-inserting (db.upsert_transaction
keys on fingerprint) would create a NEW BUY row and leave the old wrong
SELL in place — a duplicate, not a fix. This script therefore REPLACES:
it DELETEs the existing row(s) for the announcement (scoped by `url`
only) and INSERTs the freshly-reparsed BUY row(s) in one transaction.

------------------------------------------------------------------------
SAFETY MODEL
------------------------------------------------------------------------
* Pre-flight: db_health.backup() (refuses if primary DB unhealthy) +
  PRAGMA integrity_check; abort on either failure.
* Per filing: re-parse the cached HTML, ASSERT it now yields BUY rows
  with sane values, BEFORE touching the DB.
* DELETE is scoped to `WHERE url = ?` for the single target URL only —
  never broader. Referencing `signals` / `paper_trades` rows for the
  deleted fingerprints are cleaned too (eval_signals re-fires them).
* INSERT uses the project's canonical db.upsert_transaction so the new
  rows carry role_normalized + buy_strictness and signals pick them up
  on the next eval/build.
* Idempotent: if a target's DB row is already BUY and matches the
  reparse, it is left untouched (no-op). Safe to re-run.
* --dry-run: reports current DB type -> reparsed type + values and what
  WOULD be deleted/inserted, writing NOTHING.

------------------------------------------------------------------------
USAGE (PowerShell)
------------------------------------------------------------------------
    python .scripts\fix_incident_buys.py --dry-run     # inspect, no writes
    python .scripts\fix_incident_buys.py --apply       # perform the fix

Then rebuild so signals + dashboard pick up the corrected BUYS:
    python .scripts\eval_signals.py
    python .scripts\backtest.py
    python .scripts\export_dashboard_json.py
    python .scripts\build_dashboard.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import db_health  # noqa: E402
from parse_pdmr import parse_announcement  # noqa: E402

CACHE_DIR = HERE / "_scrape_cache"

# ---------------------------------------------------------------------------
# Confirmed target list (audited against DB + cache, 2026-06-02).
#
# Each tuple: (rns_id, expected_ticker, note). The rns_id is BOTH the
# cached-HTML filename stem (.scripts/_scrape_cache/<rns_id>.html) AND the
# last path segment of the stored `url`. The fix is scoped by the full
# url (looked up from the DB by this rns_id) so a delete can never touch a
# sibling filing. All five were stored type=SELL, parser_source=regex.
# ---------------------------------------------------------------------------
TARGETS = [
    # rns_id,    ticker, note
    ("9598140", "JMAT", "Liam Condon (CEO) — Acquisition ~£95,701"),
    ("9597759", "GEN",  "Shatish Dasani (NED) — Purchase ~£31,480"),
    ("9598244", "UTL",  "Peter Durhager (NED, UIL Ltd DI) — Purchase ~£69,922"),
    ("9595626", "CAD",  "Michel Meeus (NED) — Purchase ~£660"),
    ("9592456", "PSN",  "Dean Finch (Group CEO) — Purchase ~£671,442"),
]


def _cache_path(rns_id: str) -> Path:
    return CACHE_DIR / f"{rns_id}.html"


def _load_db_rows_for_url(conn, url: str) -> list:
    """Return all transactions rows whose `url` exactly equals `url`.

    The delete + replace is scoped by this exact-equality match ONLY, so
    sibling filings are never touched. Returns a list of dict rows.
    """
    rows = conn.execute(
        "SELECT * FROM transactions WHERE url = ?", (url,)
    ).fetchall()
    return [dict(r) for r in rows]


def _resolve_url(conn, rns_id: str) -> str | None:
    """Find the stored `url` for a target by its rns_id (url suffix).

    Matches on the url ending in the rns_id so we recover the exact
    stored URL string, which we then use for exact-equality scoping. If
    more than one distinct url matches (should never happen for a single
    announcement id), we refuse to guess and return None.
    """
    rows = conn.execute(
        "SELECT DISTINCT url FROM transactions "
        "WHERE url LIKE ? AND url IS NOT NULL AND url <> ''",
        (f"%/{rns_id}",),
    ).fetchall()
    urls = [r["url"] for r in rows]
    if len(urls) == 1:
        return urls[0]
    if not urls:
        # Fall back to a looser suffix match (some urls may have a
        # trailing slug variant). Still require a unique result.
        rows = conn.execute(
            "SELECT DISTINCT url FROM transactions "
            "WHERE url LIKE ? AND url IS NOT NULL AND url <> ''",
            (f"%{rns_id}%",),
        ).fetchall()
        urls = [r["url"] for r in rows]
        if len(urls) == 1:
            return urls[0]
    return None


def _reparse(rns_id: str, url: str, announced_at: str) -> tuple:
    """Re-parse the cached HTML for `rns_id`. Returns (rows, warnings).

    Raises FileNotFoundError if the cache file is missing.
    """
    path = _cache_path(rns_id)
    if not path.exists():
        raise FileNotFoundError(f"cache file missing: {path}")
    html = path.read_text(encoding="utf-8", errors="replace")
    rows, warnings, _src = parse_announcement(html, url, rns_id, announced_at or "")
    return rows, warnings


def _assert_buy_rows(rns_id: str, expected_ticker: str, rows: list) -> None:
    """Validate the reparse before any DB write.

    Guards (any failure aborts the whole run, before touching the DB):
      * at least one row produced
      * every produced row is type == 'BUY'
      * ticker matches the expected ticker
      * shares > 0 and value > 0 (sane, non-misread)
    """
    if not rows:
        raise AssertionError(
            f"{rns_id}: reparse produced ZERO rows — refusing to delete "
            "the existing row with no replacement. Inspect the cached HTML."
        )
    for r in rows:
        if r.get("type") != "BUY":
            raise AssertionError(
                f"{rns_id}: reparse produced a non-BUY row "
                f"(type={r.get('type')!r}). Expected BUY. Aborting — the "
                "live parser fix may not be in effect on this machine."
            )
        if (r.get("ticker") or "") != expected_ticker:
            raise AssertionError(
                f"{rns_id}: reparsed ticker {r.get('ticker')!r} != expected "
                f"{expected_ticker!r}. Aborting to avoid a mis-scoped fix."
            )
        if int(r.get("shares") or 0) <= 0 or float(r.get("value") or 0.0) <= 0.0:
            raise AssertionError(
                f"{rns_id}: reparsed row has non-positive shares/value "
                f"(shares={r.get('shares')}, value={r.get('value')}). "
                "Refusing to insert a misread row."
            )


def _plan_filing(conn, rns_id: str, expected_ticker: str, note: str) -> dict:
    """Build the per-filing action plan (read-only). No DB writes here.

    Returns a dict with the resolved url, existing DB rows, reparsed rows,
    and a `status` of one of:
      * 'fix'      — existing wrong row(s) present; reparse yields BUY(s)
      * 'noop'     — DB already matches the reparsed BUY (idempotent re-run)
      * 'error:..' — a problem that aborts the run
    """
    plan = {
        "rns_id": rns_id, "expected_ticker": expected_ticker, "note": note,
        "url": None, "existing": [], "reparsed": [], "status": None,
        "detail": "",
    }
    url = _resolve_url(conn, rns_id)
    if not url:
        plan["status"] = "error"
        plan["detail"] = (
            f"could not resolve a unique stored url for rns_id {rns_id}"
        )
        return plan
    plan["url"] = url

    existing = _load_db_rows_for_url(conn, url)
    plan["existing"] = existing
    announced_at = existing[0]["announced_at"] if existing else ""

    rows, warnings = _reparse(rns_id, url, announced_at)
    plan["reparsed"] = rows
    plan["warnings"] = warnings

    # Hard validation — aborts run on failure (raises).
    _assert_buy_rows(rns_id, expected_ticker, rows)

    # Idempotency check: if the DB already holds EXACTLY the reparsed
    # BUY fingerprint(s) and nothing else under this url, it's a no-op.
    existing_fps = {r["fingerprint"] for r in existing}
    reparsed_fps = {r["fingerprint"] for r in rows}
    existing_types = {r["type"] for r in existing}
    if existing_fps == reparsed_fps and existing_types == {"BUY"}:
        plan["status"] = "noop"
        plan["detail"] = "DB already holds the corrected BUY row(s)"
    else:
        plan["status"] = "fix"
        plan["detail"] = (
            f"replace {len(existing)} existing row(s) "
            f"(types={sorted(existing_types)}) with {len(rows)} BUY row(s)"
        )
    return plan


def _print_plan(plan: dict) -> None:
    print(f"\n--- {plan['rns_id']} [{plan['expected_ticker']}] {plan['note']}")
    print(f"    url:    {plan['url']}")
    print(f"    status: {plan['status']} — {plan['detail']}")
    for r in plan["existing"]:
        print(
            f"    DB  fp={r['fingerprint']} {r['date']} {r['ticker']} "
            f"{r['director']!r} type={r['type']} shares={r['shares']} "
            f"value={r['value']}"
        )
    for r in plan["reparsed"]:
        print(
            f"    NEW fp={r['fingerprint']} {r['date']} {r['ticker']} "
            f"{r['director']!r} type={r['type']} shares={r['shares']} "
            f"value={r['value']} buy_strictness={r.get('buy_strictness')}"
        )
    if plan.get("warnings"):
        print(f"    warnings: {plan['warnings']}")


def _apply_filing(conn, plan: dict) -> None:
    """Replace the existing row(s) for one filing with the reparsed BUY(s).

    Scoped DELETE: `WHERE url = ?` for the single resolved url ONLY.
    Referencing signals / paper_trades for the deleted fingerprints are
    removed too (eval_signals re-fires the correct ones on the new
    BUY fingerprints). INSERT goes through db.upsert_transaction so the
    new rows carry role_normalized + buy_strictness.
    """
    url = plan["url"]
    # 1) Clean referencing rows for the soon-to-be-deleted fingerprints.
    for r in plan["existing"]:
        fp = r["fingerprint"]
        conn.execute("DELETE FROM signals WHERE fingerprint = ?", (fp,))
        conn.execute("DELETE FROM paper_trades WHERE fingerprint = ?", (fp,))
    # 2) Scoped delete of the transactions row(s) for THIS url only.
    conn.execute("DELETE FROM transactions WHERE url = ?", (url,))
    # 3) Insert the freshly-reparsed BUY row(s) via the canonical upsert.
    for r in plan["reparsed"]:
        db.upsert_transaction(conn, r, parser_source="regex")


def run(apply: bool) -> int:
    if apply:
        # Pre-flight backup (mirrors drain_pending.py). Refuses if the
        # primary DB is unhealthy, so we never seal a corrupt state.
        print("[fix] taking pre-flight backup via db_health.backup() ...")
        if not db_health.backup():
            print("[fix] ABORT: pre-flight backup failed (DB unhealthy?).")
            return 2

    conn = db.connect()
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            print(f"[fix] ABORT: integrity_check returned {ok!r}, not 'ok'.")
            return 2
        print(f"[fix] integrity_check: {ok}")

        # Build all plans first (read-only). Any validation failure raises
        # here, before a single write.
        plans = []
        for rns_id, ticker, note in TARGETS:
            plan = _plan_filing(conn, rns_id, ticker, note)
            plans.append(plan)
            _print_plan(plan)

        errors = [p for p in plans if p["status"] == "error"]
        if errors:
            print(
                f"\n[fix] ABORT: {len(errors)} target(s) could not be "
                "resolved. No DB writes performed."
            )
            return 2

        to_fix = [p for p in plans if p["status"] == "fix"]
        noops = [p for p in plans if p["status"] == "noop"]
        print(
            f"\n[fix] summary: {len(to_fix)} to fix, {len(noops)} already "
            f"correct (no-op), of {len(plans)} targets."
        )

        if not apply:
            print(
                "\n[fix] DRY-RUN — no DB writes performed. "
                "Re-run with --apply to perform the fix."
            )
            return 0

        if not to_fix:
            print("\n[fix] nothing to fix — all targets already BUY. Done.")
            return 0

        # --- Apply, single transaction ---
        print(f"\n[fix] applying {len(to_fix)} replacement(s) ...")
        conn.execute("BEGIN")
        try:
            for plan in to_fix:
                _apply_filing(conn, plan)
            conn.commit()
        except Exception:
            conn.rollback()
            print("[fix] ERROR during apply — rolled back, DB unchanged.")
            raise

        # Re-verify integrity post-commit.
        ok2 = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"[fix] post-commit integrity_check: {ok2}")

        print("\n[fix] DONE. Corrected the following to BUY:")
        for plan in to_fix:
            for r in plan["reparsed"]:
                print(
                    f"    {r['ticker']} {r['date']} {r['director']!r} "
                    f"shares={r['shares']} value={r['value']}"
                )
        print(
            "\n[fix] Next (run from PowerShell):\n"
            "    python .scripts\\eval_signals.py\n"
            "    python .scripts\\backtest.py\n"
            "    python .scripts\\export_dashboard_json.py\n"
            "    python .scripts\\build_dashboard.py"
        )
        # Refresh .bak after a successful write (B-024 pattern).
        try:
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-script seal failed (non-fatal): {e}")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Re-flip five incident SELL rows back to BUY "
                    "(scoped delete-by-url + reparse-insert)."
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true",
                   help="Report current vs reparsed type/values; write "
                        "NOTHING (default).")
    g.add_argument("--apply", action="store_true",
                   help="Perform the delete-and-replace fix in one "
                        "transaction.")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
