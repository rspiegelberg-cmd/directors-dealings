"""B1 -- Lone Conviction Buy (Sprint 13).

Fires when a single director makes a high-value discretionary on-market
purchase in isolation -- no other director buying the same ticker within
a ±30-day window, and the stock's 60-day trailing return is not in the
moderate-weakness exclusion zone [-10%, -2%).

Criteria (all must pass):
  1. tx.type == 'BUY'
  2. tx.buy_strictness == 'STRICT_BUY'  (on-market purchase confirmed;
     defense-in-depth -- _universe_rows already pre-filters)
  3. tx.value >= £200,000
  4. Lone buyer: 0 other distinct directors buying same ticker with
     COALESCE(buy_strictness,'STRICT_BUY') = 'STRICT_BUY' in the window
     [announced_at - 30d, announced_at + 30d], walk-forward gated by
     announced_at <= as_of.
  5. Momentum not in exclusion zone: 60-day trailing close-to-close return
     of the ticker is NOT in [-10%, -2%). If price data is unavailable
     the criterion passes (conservative fallback -- don't silently drop).

Confidence: "high" -- lone discretionary purchase at significant scale.

Expected firing rate: ~20 signals, 55% hit rate T+21 (Sprint 13 target).
Thresholds confirmed by Rupert 2026-05-28.

Spec: docs/specs/08-phase-4-behavioural-signals.md §B1
Sprint 13 -- 2026-05-28.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta


SIGNAL_ID = "b1_lone_conviction_buy"
SIGNAL_VERSION = "1.0.0"

_MIN_VALUE_GBP = 200_000
_LONE_WINDOW_DAYS = 30       # ±30d around announced_at
_MOMENTUM_DAYS = 60          # trailing-return lookback window
_EXCL_LOW = -0.10            # momentum exclusion zone: lower bound (inclusive)
_EXCL_HIGH = -0.02           # momentum exclusion zone: upper bound (exclusive)

# B-094: human date formats the scraper may have stored before the ISO fix.
_HUMAN_DATE_FMTS = ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")


def _normalise_announced_at(s: str | None) -> str | None:
    """Return 'YYYY-MM-DD' from s, or None if unparseable.

    Handles ISO dates/timestamps ('2026-06-02', '2026-06-02T16:15:07Z') and
    the 'DD Mon YYYY' headline format the scraper stored before B-094 was
    fixed.  Previously b1 did a blind [:10] slice and silently returned None
    on non-ISO values, causing it to skip evaluation on the newest filings.
    """
    if not s:
        return None
    s = s.strip()
    head = s[:10]
    try:
        datetime.strptime(head, "%Y-%m-%d")
        return head
    except ValueError:
        pass
    for fmt in _HUMAN_DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _trailing_return(conn, ticker: str, as_of_date: str):
    """Return 60-day trailing close-to-close return, or None if unavailable.

    Uses the most-recent price on or before `as_of_date` as the end price,
    and the most-recent price on or before (as_of_date - 60 days) as the
    start price.  Both must exist to produce a valid return.

    Walk-forward safe: `as_of_date` is the transaction date, so we only
    ever read prices that were observable at trade time.
    """
    try:
        dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    except ValueError:
        return None

    start_date = (dt - timedelta(days=_MOMENTUM_DAYS)).strftime("%Y-%m-%d")

    end_row = conn.execute(
        "SELECT close FROM prices "
        "WHERE ticker = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (ticker, as_of_date),
    ).fetchone()
    if not end_row or end_row["close"] is None:
        return None

    start_row = conn.execute(
        "SELECT close FROM prices "
        "WHERE ticker = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (ticker, start_date),
    ).fetchone()
    if not start_row or start_row["close"] is None:
        return None

    if start_row["close"] == 0:
        return None

    return (end_row["close"] - start_row["close"]) / start_row["close"]


def evaluate(tx, conn, as_of: str):
    # Gate 1: type + buy_strictness
    if tx["type"] != "BUY":
        return None
    if tx["buy_strictness"] != "STRICT_BUY":
        return None

    # Gate 2: minimum value
    value = tx["value"] or 0
    if value < _MIN_VALUE_GBP:
        return None

    # Gate 3: announced_at required for walk-forward lone-buyer check
    announced_at = tx["announced_at"]
    if not announced_at:
        return None

    # Gate 4: lone buyer -- 0 other distinct directors buying same ticker
    # with a confirmed on-market buy (STRICT_BUY or NULL) in ±30d window,
    # walk-forward gated.
    # B-094: normalise before parsing so 'DD Mon YYYY' rows are not silently
    # skipped (old [:10] slice turned "02 Jun 2026" into "02 Jun 20" ->
    # strptime rejected it -> silent None return on newest filings).
    iso_ann = _normalise_announced_at(announced_at)
    if iso_ann is None:
        return None
    try:
        ann_dt = datetime.strptime(iso_ann, "%Y-%m-%d")
    except ValueError:
        return None
    window_start = (ann_dt - timedelta(days=_LONE_WINDOW_DAYS)).strftime(
        "%Y-%m-%d"
    )
    window_end = (ann_dt + timedelta(days=_LONE_WINDOW_DAYS)).strftime(
        "%Y-%m-%d"
    )

    other_row = conn.execute(
        "SELECT COUNT(DISTINCT director) AS n "
        "FROM transactions "
        "WHERE ticker = ? "
        "  AND type = 'BUY' "
        "  AND COALESCE(buy_strictness, 'STRICT_BUY') = 'STRICT_BUY' "
        "  AND director != ? "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at >= ? "
        "  AND announced_at <= ? "
        "  AND announced_at <= ?",
        (tx["ticker"], tx["director"],
         window_start, window_end, as_of),
    ).fetchone()
    n_others = (other_row["n"] if other_row else 0) or 0
    if n_others > 0:
        return None

    # Gate 5: momentum exclusion -- skip if 60-day trailing return is in
    # [-10%, -2%).  Price unavailable -> pass (conservative fallback).
    tx_date = (tx["date"] or announced_at)[:10]
    momentum = _trailing_return(conn, tx["ticker"], tx_date)
    if momentum is not None and _EXCL_LOW <= momentum < _EXCL_HIGH:
        return None

    metadata = {
        "value_gbp": value,
        "role": tx["role"],
        "ticker": tx["ticker"],
        "director": tx["director"],
        "n_other_buyers_30d": int(n_others),
        "momentum_60d": round(momentum, 4) if momentum is not None else None,
    }
    return {
        "signal_id": SIGNAL_ID,
        "signal_version": SIGNAL_VERSION,
        "fingerprint": tx["fingerprint"],
        "fired_at": None,
        "confidence": "high",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
