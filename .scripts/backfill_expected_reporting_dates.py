"""backfill_expected_reporting_dates.py — synthetic "(est)" forward earnings (B-118).

ZONE B — writes to `.data/directors.db`. **Rupert runs this**; Claude never
runs it from bash.

What it does
------------
The confirmed forward-earnings feed (backfill_lse_diary.py, B-111) only covers
tickers that appear on the LSE Financial Diary's near-term month pages. Many held
tickers have NO confirmed upcoming results date, so the 60-day pre-results badge
(B-111) / pre-earnings conviction chip (B-114) can never light up for them.

This gap-filler projects each held ticker's NEXT expected results date from its
own historical reporting cadence and writes it with **confidence='est'** and
**source='est'**, so the dashboard can show it clearly marked as an estimate (the
exporter appends "(est)" and the chip carries a `near_reporting_est` flag).

Design (locked 2026-06-05/06)
-----------------------------
  * Cadence: a once-a-day scheduled run (same as the LSE diary scrape).
  * Synthetic estimates are ALLOWED but must be MARKED — confidence='est'.
  * Gap-filler only: a ticker that already has a CONFIRMED future results date
    is skipped (we never shadow a real date with a guess).
  * Projection: from a ticker's confirmed historical results dates, take the
    median gap between consecutive announcements (UK issuers typically report
    twice a year -> ~182 days; annual-only -> ~365). Clamp the gap to a sane
    [80, 400] day band, then roll forward from the most recent date until the
    projected date is in the future. Only store it if it falls within the
    horizon (default 400 days = the cadence ceiling, so no valid one-cadence
    projection is ever dropped; downstream panels/badges still read only the
    near term). Estimates are always badged "(est)".
  * report_type = 'EARNINGS' (generic — we're estimating "next results", not a
    specific interim/final).
  * Replace-on-rerun: each run deletes all prior source='est' rows and
    re-inserts. confirmed (lse_diary / yahoo) rows are never touched.

Run:
    python .scripts\\backfill_expected_reporting_dates.py            # write estimates
    python .scripts\\backfill_expected_reporting_dates.py --dry-run  # report only
    python .scripts\\backfill_expected_reporting_dates.py --horizon 120
    python .scripts\\snapshot_db.py                                  # then snapshot
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

# --- Constants --------------------------------------------------------------

SOURCE = "est"
CONFIDENCE = "est"
REPORT_TYPE = "EARNINGS"

DEFAULT_CADENCE_DAYS = 365     # fallback when a ticker has only one prior date
MIN_GAP_DAYS = 80             # clamp band: quarterly-ish floor
MAX_GAP_DAYS = 400            # clamp band: annual-ish ceiling
DEFAULT_HORIZON_DAYS = 400    # store estimates landing within this window.
# Set to match MAX_GAP_DAYS (the cadence ceiling): the roll-forward always lands
# within one cadence of today, and cadence is clamped to <= 400, so a 400-day
# horizon never discards a valid projection. The old 180 silently dropped every
# annual reporter whose next results were >6 months out (2026-06-18: this raised
# active-holding coverage from ~51% toward the ~77% that have usable history).
# Downstream surfaces (30-day panel, 60-day pre-results badge) read only the
# near term, so longer-dated estimates raise coverage without cluttering the UI.

# If any confirmed date (any type, any source) for a ticker lands within this
# many days of the projected estimate, the estimate is suppressed.  Covers the
# case where LSE diary has e.g. 2026-07-15 and our cadence roll produces
# 2026-07-12 — clearly the same event; the confirmed date wins.
NEARBY_CONFIRMED_DAYS = 21

# Only these confirmed report types count as "results" for cadence/skip logic.
_RESULTS_TYPES = ("INTERIM", "FINAL", "PRELIM", "QUARTERLY", "EARNINGS")


# --- Pure helpers (no DB — unit-tested) -------------------------------------

def _parse_iso(s: str) -> date | None:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def median_cadence_days(iso_dates: list[str],
                        *, default: int = DEFAULT_CADENCE_DAYS,
                        min_gap: int = MIN_GAP_DAYS,
                        max_gap: int = MAX_GAP_DAYS) -> int:
    """Median gap (days) between consecutive results dates, clamped to a band.

    With <2 parseable dates, returns `default`. The clamp keeps a single odd
    interval (a delayed/early result, a one-off special) from skewing the
    projection out of a sensible annual/half-year range.
    """
    ds = sorted({d for d in (_parse_iso(x) for x in iso_dates) if d})
    if len(ds) < 2:
        return default
    gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
    if not gaps:
        return default
    g = int(round(statistics.median(gaps)))
    return max(min_gap, min(max_gap, g))


def estimate_next_results_date(iso_dates: list[str], today: date,
                               *, horizon_days: int = DEFAULT_HORIZON_DAYS
                               ) -> str | None:
    """Project the next expected results date from historical dates.

    Rolls forward from the most recent prior date by the median cadence until
    the projection is strictly after `today`. Returns the ISO date string if it
    lands within `horizon_days`, else None (too far out to be reliable/useful).
    Returns None when there is no usable history.
    """
    ds = sorted({d for d in (_parse_iso(x) for x in iso_dates) if d})
    if not ds:
        return None
    cadence = median_cadence_days(iso_dates)
    nxt = ds[-1]
    # roll forward to the first strictly-future occurrence
    guard = 0
    while nxt <= today and guard < 50:
        nxt = nxt + timedelta(days=cadence)
        guard += 1
    if nxt <= today:
        return None
    if (nxt - today).days > horizon_days:
        return None
    return nxt.isoformat()


# --- DB read/write ----------------------------------------------------------

def load_held_tickers(conn) -> set[str]:
    rows = conn.execute("SELECT DISTINCT ticker FROM transactions").fetchall()
    return {r[0].strip().upper() for r in rows if r and r[0]}


def tickers_with_confirmed_future(conn, today: date) -> set[str]:
    """Tickers that already have a CONFIRMED results date today or later."""
    placeholders = ",".join("?" * len(_RESULTS_TYPES))
    rows = conn.execute(
        f"SELECT DISTINCT ticker FROM reporting_dates "
        f"WHERE confidence='confirmed' AND report_date >= ? "
        f"AND report_type IN ({placeholders})",
        (today.isoformat(), *_RESULTS_TYPES),
    ).fetchall()
    return {r[0].strip().upper() for r in rows if r and r[0]}


def _has_nearby_confirmed(conn, ticker: str, est_date: date,
                          nearby_days: int = NEARBY_CONFIRMED_DAYS) -> bool:
    """True if any confirmed date for ticker falls within nearby_days of est_date.

    Catches the staleness window where LSE diary added a confirmed date after
    the last estimate run: ensures we never write (or retain) an estimate that
    is really just a noisier version of an already-confirmed date.
    """
    low  = (est_date - timedelta(days=nearby_days)).isoformat()
    high = (est_date + timedelta(days=nearby_days)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM reporting_dates "
        "WHERE ticker=? AND confidence='confirmed' AND report_date BETWEEN ? AND ?",
        (ticker, low, high),
    ).fetchone()
    return row is not None


def confirmed_history_by_ticker(conn) -> dict[str, list[str]]:
    """Map ticker -> list of confirmed historical results dates (ISO)."""
    placeholders = ",".join("?" * len(_RESULTS_TYPES))
    rows = conn.execute(
        f"SELECT ticker, report_date FROM reporting_dates "
        f"WHERE confidence='confirmed' AND report_type IN ({placeholders})",
        _RESULTS_TYPES,
    ).fetchall()
    hist: dict[str, list[str]] = {}
    for tk, rd in rows:
        if tk and rd:
            hist.setdefault(tk.strip().upper(), []).append(rd)
    return hist


def build_estimates(conn, today: date,
                    *, horizon_days: int = DEFAULT_HORIZON_DAYS) -> list[dict]:
    """Build the per-ticker estimated next-results rows (gap-filler only)."""
    held = load_held_tickers(conn)
    have_confirmed = tickers_with_confirmed_future(conn, today)
    history = confirmed_history_by_ticker(conn)

    estimates: list[dict] = []
    for tk in sorted(held):
        if tk in have_confirmed:
            continue  # never shadow a real future date (fast path: same ticker)

        est = estimate_next_results_date(history.get(tk, []), today,
                                         horizon_days=horizon_days)
        if not est:
            continue

        # Secondary guard: if ANY confirmed date (any type, any source) lands
        # within NEARBY_CONFIRMED_DAYS of our projection, the confirmed date
        # wins.  Handles the race where LSE diary ran after the last estimate
        # run and added a date close to our projection.
        est_date = date.fromisoformat(est)
        if _has_nearby_confirmed(conn, tk, est_date):
            continue

        estimates.append({"ticker": tk, "report_date": est})
    return estimates


def write_estimates(conn, estimates: list[dict], *, dry_run: bool = False) -> dict:
    """Replace-on-rerun: drop prior source='est' rows, insert the new ones."""
    fetched_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [(e["ticker"], e["report_date"], REPORT_TYPE, SOURCE,
             fetched_at, CONFIDENCE) for e in estimates]
    if not dry_run:
        conn.execute("DELETE FROM reporting_dates WHERE source = ?", (SOURCE,))
        conn.executemany(
            "INSERT OR REPLACE INTO reporting_dates "
            "(ticker, report_date, report_type, source, fetched_at, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return {"estimated": len(rows), "written": 0 if dry_run else len(rows)}


# --- CLI --------------------------------------------------------------------

def run(*, dry_run: bool = False, horizon_days: int = DEFAULT_HORIZON_DAYS,
        conn=None) -> dict:
    today = date.today()
    own_conn = conn is None
    if own_conn:
        conn = db.connect()
    try:
        estimates = build_estimates(conn, today, horizon_days=horizon_days)
        stats = write_estimates(conn, estimates, dry_run=dry_run)
        held = len(load_held_tickers(conn))
        print(f"[est-dates] held={held} estimated={stats['estimated']} "
              f"written={stats['written']} horizon={horizon_days}d "
              f"{'(dry-run)' if dry_run else ''}")
        if estimates:
            preview = ", ".join(f"{e['ticker']}~{e['report_date']}"
                                for e in estimates[:25])
            print(f"[est-dates] e.g. {preview}")
        return stats
    finally:
        if own_conn:
            conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Backfill synthetic '(est)' forward earnings dates (B-118).")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report estimates but write nothing.")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS,
                    help=f"only store estimates within N days ahead "
                         f"(default {DEFAULT_HORIZON_DAYS}).")
    args = ap.parse_args(argv)
    run(dry_run=args.dry_run, horizon_days=args.horizon)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
