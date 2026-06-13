"""routine_flag.py — B-155 routine vs opportunistic trader classifier (Phase A).

Literature basis: Cohen-Malloy-Pomorski (2012) / Ali-Hirshleifer (2017) —
opportunistic (non-habitual) insider buys carry information; routine
calendar-pattern buys (same month every year) carry ~none.

Three-valued, walk-forward classification per (director-key, ticker),
evaluated as-of a transaction's announcement time:

  * "insufficient_history"  — fewer than ROUTINE_MIN_YEARS distinct calendar
                              years of strictly-prior buys.
  * "routine"               — some calendar month contains a buy in a strict
                              majority of prior years (never fewer than 2).
  * "opportunistic"         — everything else.

Lookahead discipline (P3-6): the history set H for a transaction with
effective announcement time A contains only buys whose own effective
announcement time is STRICTLY < A. The transaction itself (eff == A) and
any same-RNS siblings sharing the timestamp are excluded. Visibility gates
on announced-at; the calendar pattern itself uses the trade `date` column
(the habit is about when the director deals, not when the RNS prints).

Storage: none. This is a pure compute-at-row-time feature consumed by
backtest.py (CSV columns `routine_flag`, `routine_prior_buy_years`) and,
later, Phase B signal modules. The flag is as-of-relative and non-monotone,
so a static DB column would be permanently stale (see sprint-63-plan.md).
"""
from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left

# Minimum distinct calendar years of prior-buy history before the
# routine/opportunistic distinction is attempted. The canonical CMP rule
# is 3 consecutive years; with ~1 year of feed history (2026-06) that
# classifies nobody, so we start at 2 ("both prior years hit the same
# month"). REVISIT: raise to 3 (full CMP) once ~3 years of feed history
# exist (~mid-2027). The majority rule below needs no other change as
# history deepens.
ROUTINE_MIN_YEARS = 2

INSUFFICIENT = "insufficient_history"
ROUTINE = "routine"
OPPORTUNISTIC = "opportunistic"

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})")


def director_key(name) -> str:
    """Canonical join key for a director name.

    NFKC-normalises (folds non-breaking spaces and other compatibility
    characters to their plain forms), collapses whitespace runs to a
    single ASCII space, strips, and casefolds. Handles the observed live
    collisions: case variants ("MURRAY MCGOWAN" vs "Murray McGowan") and
    NBSP variants ("Serpil\xa0Timuray" vs "Serpil Timuray").

    Deliberately NOT parse_pdmr._normalise_director_name: that produces a
    display form for storage; this produces a match key only. Joint-PCA
    strings ("A AND B") stay distinct keys — they are distinct reporting
    entities.
    """
    if not name:
        return ""
    folded = unicodedata.normalize("NFKC", str(name))
    return _WS_RE.sub(" ", folded).strip().casefold()


def _year_month(trade_date):
    """Extract ("YYYY", "MM") from an ISO-ish date string, else None."""
    if not trade_date:
        return None
    m = _DATE_RE.match(str(trade_date))
    if not m:
        return None
    return m.group(1), m.group(2)


def build_buy_history_index(conn) -> dict:
    """One-pass index of all BUY transactions for walk-forward lookups.

    Returns dict[(director_key, ticker)] -> list of
    (effective_announced_at, trade_date), sorted by effective_announced_at.
    effective_announced_at follows the backtest convention:
    COALESCE(NULLIF(announced_at, ''), date).

    Built once per backtest run (one SELECT over ~6k rows) instead of a
    per-firing SQL query.
    """
    index: dict = {}
    rows = conn.execute(
        "SELECT director, ticker, date, "
        "       COALESCE(NULLIF(announced_at, ''), date) AS eff "
        "FROM transactions WHERE type = 'BUY'"
    ).fetchall()
    for r in rows:
        # sqlite3.Row has no .get(); index by key (project rule).
        key = (director_key(r["director"]), r["ticker"])
        index.setdefault(key, []).append((r["eff"] or "", r["date"] or ""))
    for entries in index.values():
        entries.sort()
    return index


def classify_routine(index, director, ticker, effective_announced_at):
    """Classify a BUY as-of its announcement time.

    Returns (flag, n_prior_years) where flag is one of the module
    constants, or (None, None) when inputs are unusable (missing
    director/ticker/timestamp). The caller gates on tx_type == "BUY";
    non-BUY rows should not reach here.

    Lookahead guard: only history entries with eff STRICTLY below
    `effective_announced_at` are considered. Tuple bisect with ("",)
    as the second element places the cutoff before any entry whose eff
    equals the transaction's — identical timestamps (same-RNS siblings,
    and the transaction itself) are excluded.
    """
    if not director or not ticker or not effective_announced_at:
        return None, None
    entries = index.get((director_key(director), ticker), [])
    cut = bisect_left(entries, (str(effective_announced_at), ""))
    prior = entries[:cut]

    years_by_month: dict = {}   # "MM" -> set of "YYYY"
    all_years: set = set()
    for _eff, trade_date in prior:
        ym = _year_month(trade_date)
        if ym is None:
            continue
        year, month = ym
        all_years.add(year)
        years_by_month.setdefault(month, set()).add(year)

    n_years = len(all_years)
    if n_years < ROUTINE_MIN_YEARS:
        return INSUFFICIENT, n_years

    # Strict majority of prior years, never fewer than 2.
    need = max(2, n_years // 2 + 1)
    for years in years_by_month.values():
        if len(years) >= need:
            return ROUTINE, n_years
    return OPPORTUNISTIC, n_years
