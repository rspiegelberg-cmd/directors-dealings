"""reversal_flag.py — B-159 net-seller-reversal flag (Phase A).

Literature basis: Cohen-Malloy-Pomorski (2012) conviction mechanism — a
director who was a NET SELLER of this stock over the trailing 12 months
and now BUYS is reversing a revealed negative stance: a high-conviction
event. Extends the F1 first-buy concept (F1 covers no-history buys; this
flag covers the reversal case specifically).

Definition (walk-forward, per (director-key, ticker), as-of a BUY's
effective announcement time A):

  * Window W = same-key transactions with eff_date >= A_date - 365 days
    (inclusive lower bound, flat 365 days) AND eff STRICTLY < A (the
    P3-6 lookahead guard — same tuple-bisect discipline as B-155).
  * net_shares_prior_12m = sum(BUY shares in W) - sum(SELL shares in W).
  * seller_reversal_flag = 1 if net_shares_prior_12m < 0 else 0.
    No history at all -> (0, 0): the first-buy case is F1's job.

Type taxonomy: sells = SELL only — SELL_TAX is non-discretionary
tax-withholding on vesting and reveals no chosen stance. Buys = BUY
only. EXERCISE / GRANT / SIP excluded from both sides (non-discretionary
inflows / option mechanics). The SELECT filters type IN ('BUY','SELL')
so the others never enter the index.

Metric is SHARES, not GBP value: shares are populated on ~100% of
BUY/SELL rows while value is missing on ~6% of SELLs; and share count is
the position — value confounds position change with price moves. Known
caveat: a share split inside the window would distort the sum (rare on a
12-month horizon; accepted).

Storage: none. Compute-at-row-time backtest CSV columns only
(seller_reversal_flag, net_shares_prior_12m) — the value is
as-of-relative and changes as the window slides, so a static DB column
would be permanently stale. See docs/specs/sprint-63-plan.md (B-159).
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta

from routine_flag import director_key

WINDOW_DAYS = 365


def build_trade_history_index(conn) -> dict:
    """One-pass index of BUY + SELL transactions for walk-forward lookups.

    Returns dict[(director_key, ticker)] -> list of (eff, type, shares),
    sorted by eff (effective announced-at: COALESCE(NULLIF(announced_at,
    ''), date), the project-wide visibility convention). Shares are
    coerced to float at build time; unparseable values stored as None
    and skipped from sums in classify_reversal.
    """
    index: dict = {}
    rows = conn.execute(
        "SELECT director, ticker, type, shares, "
        "       COALESCE(NULLIF(announced_at, ''), date) AS eff "
        "FROM transactions WHERE type IN ('BUY', 'SELL')"
    ).fetchall()
    for r in rows:
        # sqlite3.Row has no .get(); index by key (project rule).
        try:
            shares = float(r["shares"]) if r["shares"] is not None else None
        except (TypeError, ValueError):
            shares = None
        key = (director_key(r["director"]), r["ticker"])
        index.setdefault(key, []).append(
            (r["eff"] or "", r["type"] or "", shares)
        )
    for entries in index.values():
        entries.sort(key=lambda e: e[0])
    return index


def classify_reversal(index, director, ticker, effective_announced_at):
    """Classify a BUY as-of its announcement time.

    Returns (seller_reversal_flag, net_shares_prior_12m):
      * (1, negative float)  -- net seller over the prior 12m: reversal.
      * (0, float)           -- net flat/buyer, or no window history.
      * (None, None)         -- unusable inputs (missing director /
                                ticker / timestamp). The caller gates on
                                tx_type == "BUY"; non-BUY rows should
                                not reach here.

    Lookahead guard: only entries with eff STRICTLY below
    `effective_announced_at` are considered (bisect on the sorted eff
    list — identical timestamps, i.e. same-RNS siblings and the
    transaction itself, are excluded). Window lower bound is inclusive:
    eff_date >= A_date - 365 days, compared on the first 10 characters
    (date component), tolerant of both "YYYY-MM-DD" and
    "YYYY-MM-DDTHH:MM:SSZ" eff formats.
    """
    if not director or not ticker or not effective_announced_at:
        return None, None
    entries = index.get((director_key(director), ticker), [])
    a_str = str(effective_announced_at)
    effs = [e[0] for e in entries]
    cut = bisect_left(effs, a_str)

    try:
        lo = (date.fromisoformat(a_str[:10])
              - timedelta(days=WINDOW_DAYS)).isoformat()
    except ValueError:
        return None, None

    net = 0.0
    for eff, tx_type, shares in entries[:cut]:
        if eff[:10] < lo:
            continue
        if shares is None:
            continue
        if tx_type == "BUY":
            net += shares
        elif tx_type == "SELL":
            net -= shares
    return (1 if net < 0 else 0), net
