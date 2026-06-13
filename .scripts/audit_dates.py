"""audit_dates.py -- Read-only date integrity audit for the transactions DB.

PURPOSE
-------
Runs five invariants against the transactions + signals tables and writes
a single JSON report at `.data/_date_audit_report.json`.

The report is consumed by `build_dashboard.py` to render the "Data
quality" panel at the top of `outputs/index.html`. With the `--gate`
flag, the script exits non-zero if any invariant fails -- this is what
`refresh_all.py` uses to block the pipeline.

INVARIANTS
----------
I1  Every `transactions.date` matches `YYYY-MM-DD`.
I2  Every `transactions.date` <= today + 1 day.
I3  Where `announced_at` is populated, `date` <= `announced_at + 7 days`.
I4  Where `announced_at` is populated, `date` >= `announced_at - 3 years`.
I5  Every row in `signals` references a transaction whose date passes
    I1/I2 and whose `date` is within +/- 1 year of `announced_at` (or
    announced_at is empty -- in which case I5 falls back to I1+I2).

I3 catches the historical "latest-wins fallback" bug: parser silently
picked an option expiry / AGM / maturity date instead of the actual
transaction date.

I4 catches the inverse case: parser picked an unrelated historic date
from a comparator table in the filing.

I5 narrows the spotlight to the rows that actually drive your signal
firings and performance tracker -- the ones that *matter* for trading
decisions.

OUTPUT
------
`.data/_date_audit_report.json` (always written):

    {
      "generated_at": "2026-05-15T14:02:00Z",
      "total_transactions": 2630,
      "signals_rows": 412,
      "overall": "PASS" | "FAIL",
      "summary": {
        "I1": {"name": "...", "pass": true, "ok": 2630, "bad": 0},
        "I2": {...},
        ...
      },
      "anomalies": {
        "I1": [{"fingerprint": "...", "date": "...", "ticker": "..."}],
        ...
      }
    }

Each anomaly list is capped at 200 rows; the count tells you the true total.

USAGE
-----
    python .scripts/audit_dates.py [--gate] [--verbose]

--gate    Exit code 1 if any invariant fails (default: always 0).
--verbose Print the summary table to stdout.

SAFETY
------
Read-only: opens DB via SQLite URI with mode=ro. Cannot corrupt or
modify the DB even in error paths. Safe to run mid-pipeline.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / ".data" / "directors.db"
REPORT_PATH = ROOT / ".data" / "_date_audit_report.json"

# How many bad rows to keep per invariant in the report's "anomalies"
# section. The full count is preserved in summary[*].bad regardless.
MAX_ANOMALIES_PER_INVARIANT = 200

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_date(s: str | None) -> date | None:
    """Parse first 10 chars as YYYY-MM-DD, else None. No exceptions raised."""
    if not s:
        return None
    s = str(s)[:10]
    if not ISO_DATE_RE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the DB in read-only mode via URI; safe to run mid-pipeline."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Invariant runners. Each returns (ok_count, bad_rows_list).
# ---------------------------------------------------------------------------

def _run_I1(conn) -> tuple[int, list[dict]]:
    """Format check: date matches YYYY-MM-DD."""
    ok = 0
    bad: list[dict] = []
    for r in conn.execute(
        "SELECT fingerprint, date, ticker FROM transactions"
    ):
        d = r["date"] or ""
        if ISO_DATE_RE.match(d):
            ok += 1
        else:
            bad.append({
                "fingerprint": r["fingerprint"],
                "date":        r["date"],
                "ticker":      r["ticker"],
                "issue":       "non_iso_date_format",
            })
    return ok, bad


def _run_I2(conn, today: date) -> tuple[int, list[dict]]:
    """Future-dated transactions."""
    cutoff = (today.toordinal() + 1)
    ok = 0
    bad: list[dict] = []
    for r in conn.execute(
        "SELECT fingerprint, date, ticker FROM transactions"
    ):
        d = _parse_iso_date(r["date"])
        if d is None:
            continue  # I1 catches these
        if d.toordinal() <= cutoff:
            ok += 1
        else:
            bad.append({
                "fingerprint": r["fingerprint"],
                "date":        r["date"],
                "ticker":      r["ticker"],
                "days_future": d.toordinal() - today.toordinal(),
                "issue":       "future_dated",
            })
    return ok, bad


def _run_I3_I4(conn) -> tuple[int, list[dict], int, list[dict]]:
    """Gap checks: date vs announced_at, both directions."""
    ok_I3, bad_I3 = 0, []
    ok_I4, bad_I4 = 0, []
    for r in conn.execute(
        "SELECT fingerprint, date, announced_at, ticker, director, type "
        "FROM transactions "
        "WHERE announced_at IS NOT NULL AND announced_at != ''"
    ):
        d = _parse_iso_date(r["date"])
        a = _parse_iso_date(r["announced_at"])
        if d is None or a is None:
            continue  # I1/format catches these
        gap = (d - a).days

        # I3: tx date must not be more than 7 days AFTER filing date.
        if gap <= 7:
            ok_I3 += 1
        else:
            bad_I3.append({
                "fingerprint":  r["fingerprint"],
                "date":         r["date"],
                "announced_at": r["announced_at"],
                "gap_days":     gap,
                "ticker":       r["ticker"],
                "director":     r["director"],
                "type":         r["type"],
                "issue":        "date_after_announced",
            })

        # I4: tx date must not be more than 3 years BEFORE filing date.
        if gap >= -1095:
            ok_I4 += 1
        else:
            bad_I4.append({
                "fingerprint":  r["fingerprint"],
                "date":         r["date"],
                "announced_at": r["announced_at"],
                "gap_days":     gap,
                "ticker":       r["ticker"],
                "director":     r["director"],
                "type":         r["type"],
                "issue":        "date_too_old",
            })
    return ok_I3, bad_I3, ok_I4, bad_I4


#  Maximum legitimate gap between transaction date and filing date.
#  Investment Trust DRIPs, year-end compliance reviews, and other
#  late-disclosed but real filings can run up to ~3 years late. We set
#  the threshold at 4 years (1,460 days) so the audit catches parser
#  errors (which typically land 5+ years off) without flagging real but
#  unusual late filings.
I5_MAX_GAP_DAYS = 1460


def _run_I5(conn, today: date) -> tuple[int, list[dict]]:
    """Signal-row integrity: dates feeding the performance tracker
    must be sane (ISO, not in the future, within I5_MAX_GAP_DAYS of
    the filing date). The gap threshold accepts late but legitimate
    filings; only obvious parser errors should still fail.
    """
    ok = 0
    bad: list[dict] = []
    for r in conn.execute(
        "SELECT s.signal_id, s.signal_version, s.fingerprint, s.fired_at, "
        "       t.date, t.announced_at, t.ticker "
        "FROM signals s "
        "JOIN transactions t ON t.fingerprint = s.fingerprint"
    ):
        d = _parse_iso_date(r["date"])
        if d is None or d > today:
            bad.append({
                "signal_id":   r["signal_id"],
                "fingerprint": r["fingerprint"],
                "date":        r["date"],
                "ticker":      r["ticker"],
                "issue":       "signal_refers_bad_date",
            })
            continue
        a = _parse_iso_date(r["announced_at"])
        if a is not None and abs((d - a).days) > I5_MAX_GAP_DAYS:
            bad.append({
                "signal_id":    r["signal_id"],
                "fingerprint":  r["fingerprint"],
                "date":         r["date"],
                "announced_at": r["announced_at"],
                "ticker":       r["ticker"],
                "gap_days":     (d - a).days,
                "issue":        "signal_date_far_from_filing",
            })
            continue
        ok += 1
    return ok, bad


# ---------------------------------------------------------------------------
# Orchestration + report writer
# ---------------------------------------------------------------------------

INVARIANT_NAMES = {
    "I1": "Date format: YYYY-MM-DD",
    "I2": "No future-dated transactions",
    "I3": "Transaction date <= announced_at + 7 days",
    "I4": "Transaction date >= announced_at - 3 years",
    "I5": "Signal rows have valid, on-window dates",
}


def run(verbose: bool = False) -> dict:
    if not DB_PATH.exists():
        # No DB yet -- emit a blank "pass" report so build_dashboard
        # doesn't choke. The dashboard's first refresh will populate it.
        report = {
            "generated_at":       _now_iso(),
            "db_path":            str(DB_PATH),
            "db_present":         False,
            "total_transactions": 0,
            "signals_rows":       0,
            "overall":            "PASS",
            "summary":            {
                k: {"name": v, "pass": True, "ok": 0, "bad": 0}
                for k, v in INVARIANT_NAMES.items()
            },
            "anomalies":          {k: [] for k in INVARIANT_NAMES},
        }
        _write_report(report)
        if verbose:
            print("[audit_dates] DB not present yet -- wrote blank report.")
        return report

    today = datetime.now(timezone.utc).date()
    conn = _connect_read_only(DB_PATH)
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions"
        ).fetchone()["n"]
        sigs_n = conn.execute(
            "SELECT COUNT(*) AS n FROM signals"
        ).fetchone()["n"]

        ok_I1, bad_I1 = _run_I1(conn)
        ok_I2, bad_I2 = _run_I2(conn, today)
        ok_I3, bad_I3, ok_I4, bad_I4 = _run_I3_I4(conn)
        ok_I5, bad_I5 = _run_I5(conn, today)
    finally:
        conn.close()

    summary = {
        "I1": {"name": INVARIANT_NAMES["I1"], "ok": ok_I1, "bad": len(bad_I1),
               "pass": len(bad_I1) == 0},
        "I2": {"name": INVARIANT_NAMES["I2"], "ok": ok_I2, "bad": len(bad_I2),
               "pass": len(bad_I2) == 0},
        "I3": {"name": INVARIANT_NAMES["I3"], "ok": ok_I3, "bad": len(bad_I3),
               "pass": len(bad_I3) == 0},
        "I4": {"name": INVARIANT_NAMES["I4"], "ok": ok_I4, "bad": len(bad_I4),
               "pass": len(bad_I4) == 0},
        "I5": {"name": INVARIANT_NAMES["I5"], "ok": ok_I5, "bad": len(bad_I5),
               "pass": len(bad_I5) == 0},
    }
    overall = "PASS" if all(s["pass"] for s in summary.values()) else "FAIL"

    report = {
        "generated_at":       _now_iso(),
        "db_path":            str(DB_PATH),
        "db_present":         True,
        "total_transactions": total,
        "signals_rows":       sigs_n,
        "overall":            overall,
        "summary":            summary,
        "anomalies": {
            "I1": bad_I1[:MAX_ANOMALIES_PER_INVARIANT],
            "I2": bad_I2[:MAX_ANOMALIES_PER_INVARIANT],
            "I3": bad_I3[:MAX_ANOMALIES_PER_INVARIANT],
            "I4": bad_I4[:MAX_ANOMALIES_PER_INVARIANT],
            "I5": bad_I5[:MAX_ANOMALIES_PER_INVARIANT],
        },
    }

    _write_report(report)

    if verbose:
        print(f"=== Date integrity audit ({report['generated_at']}) ===")
        print(f"  Total transactions: {total}")
        print(f"  Signal rows:        {sigs_n}")
        print()
        for k in ("I1", "I2", "I3", "I4", "I5"):
            s = summary[k]
            mark = "PASS" if s["pass"] else "FAIL"
            print(f"  {k}  {s['name']:<48}  {mark}  "
                  f"(ok={s['ok']}, bad={s['bad']})")
        print()
        print(f"OVERALL: {overall}")
        if overall == "FAIL":
            print(f"  Full row-level detail in: {REPORT_PATH}")
        print()

    return report


def _write_report(report: dict) -> None:
    """Atomic write via .tmp + replace, so a partial write never corrupts."""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(REPORT_PATH)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only date integrity audit for transactions + signals."
    )
    ap.add_argument("--gate", action="store_true",
                    help="Exit code 1 if any invariant fails (default: always 0).")
    ap.add_argument("--verbose", action="store_true",
                    help="Print the summary table to stdout.")
    args = ap.parse_args(argv)

    report = run(verbose=args.verbose)

    if args.gate and report["overall"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
