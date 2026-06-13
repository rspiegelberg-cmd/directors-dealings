"""results_window_flag.py — B-161 "first window after results" flag (Phase A).

Literature basis: alpha-research 2026-06-10 item #7 — UK MAR Art. 19(11)
bans PDMR dealing in the 30 days before results, so the US "buy before
earnings" signal barely exists here. The high-information moment is a buy
in the FIRST dealing window just after results: the director has just
signed off the numbers and is acting on them at the earliest legal moment.

Definition (per BUY firing, as-of its effective announcement time A,
compared on the date component A[:10]):

  * D = sorted distinct report_date values for the ticker where
    confidence = 'confirmed' (any report_type, any source — including
    TRADING_STMT: UK dealing codes routinely open the window after any
    scheduled results-type announcement, and for many small caps the
    trading statement IS the de facto results event. REVISIT if the
    factor scan wants a results-only split: re-derive by joining
    reporting_dates on ticker + days_since_results).
  * last_results = max d in D with d <= A[:10] (same-day INCLUSIVE: UK
    results go out at 07:00 London and the MAR window opens at that
    announcement — a same-day PDMR buy is the canonical first-window
    trade; report_date is date-only so intraday order is unobservable).
  * days_since_results = calendar days from last_results to A[:10].
  * post_results_flag = 1 if days_since_results <= WINDOW_CALENDAR_DAYS.
  * No coverage / no prior confirmed date -> (None, None): BOTH columns
    empty or both populated. Empty = missing data (B-164 semantics),
    not an informative zero — mean(flag) over non-empty rows is then
    the honest in-window rate among covered tickers.

Calendar days, not trading days: the issue's N=10 trading days is
implemented as 14 calendar days (~10 trading days). The ticker's own
price calendar is unreliable for thin names, and the companion
days_since_results column lets the factor scan re-bucket at any cutoff.

Lookahead discipline (P3-6): a results announcement is public the moment
it happens, so conditioning on a PAST report_date relative to a later
buy is not lookahead even if our row was ingested later. The real risks
are guarded structurally: (1) 'est' rows (synthetic forecast dates) are
excluded at SELECT time; (2) future-dated confirmed rows (scheduled
LSE-diary dates) are excluded by construction (d <= A[:10]).

Storage: none. Compute-at-row-time backtest CSV columns only
(post_results_flag, days_since_results) — new reporting dates arrive
continuously, so a static DB column would staleness-rot. See
docs/specs/sprint-63-plan.md (B-161).
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date

WINDOW_CALENDAR_DAYS = 14


def build_results_date_index(conn) -> dict:
    """One-pass index of confirmed reporting dates per ticker.

    Returns dict[ticker] -> sorted list of "YYYY-MM-DD" strings.
    DISTINCT collapses the same date appearing under multiple
    report_types; 'est' (synthetic estimate) rows never enter the index.

    QA hardening: values are truncated to the date component ([:10]) and
    must parse as ISO dates, or they are dropped at build time. Current
    migrations only produce date-only values; this guards against a
    timestamped or malformed row silently breaking same-day inclusion
    or masking an earlier valid date.
    """
    index: dict = {}
    rows = conn.execute(
        "SELECT DISTINCT ticker, report_date FROM reporting_dates "
        "WHERE confidence = 'confirmed'"
    ).fetchall()
    for r in rows:
        # sqlite3.Row has no .get(); index by key (project rule).
        if not r["ticker"] or not r["report_date"]:
            continue
        day = str(r["report_date"])[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        index.setdefault(r["ticker"], set()).add(day)
    return {t: sorted(days) for t, days in index.items()}


def classify_post_results(index, ticker, effective_announced_at):
    """Classify a BUY as-of its announcement time.

    Returns (post_results_flag, days_since_results):
      * (1, d)        -- most recent prior confirmed results date is
                         d <= WINDOW_CALENDAR_DAYS days back (d >= 0;
                         d == 0 is a same-day, in-window buy).
      * (0, d)        -- covered, but the most recent prior date is
                         outside the window (d > WINDOW_CALENDAR_DAYS).
      * (None, None)  -- no coverage: ticker unknown, no confirmed date
                         at or before A, missing/malformed inputs.

    Both values are always both-None or both-populated, and
    flag == (days <= WINDOW_CALENDAR_DAYS) whenever populated.
    """
    if not ticker or not effective_announced_at:
        return None, None
    dates = index.get(ticker)
    if not dates:
        return None, None
    a_day = str(effective_announced_at)[:10]
    try:
        a_date = date.fromisoformat(a_day)
    except ValueError:
        return None, None
    # bisect_right -> rightmost date <= a_day (same-day inclusive).
    pos = bisect_right(dates, a_day)
    if pos == 0:
        return None, None  # every known date is in the future
    try:
        last_results = date.fromisoformat(dates[pos - 1])
    except ValueError:
        return None, None
    days = (a_date - last_results).days
    return (1 if days <= WINDOW_CALENDAR_DAYS else 0), days
