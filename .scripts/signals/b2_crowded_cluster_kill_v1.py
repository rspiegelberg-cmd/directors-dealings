"""B2 -- Crowded Cluster Kill (Sprint 13).

Fires when ≥4 distinct directors buy the same ticker within a 30-day
walk-forward window.  Returns confidence="kill" which the orchestrator
uses to suppress all other signals on that ticker for 60 days.

This is NOT a buy signal — it is a suppression trigger.  The dashboard
renders it with a red badge to indicate the cluster is too crowded to
be a clean conviction trade.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

SIGNAL_ID = "b2_crowded_cluster_kill"
SIGNAL_VERSION = "1.0.0"

_CROWDED_WINDOW_DAYS = 30   # rolling look-back for the cluster count
_MIN_DIRECTORS = 4          # ≥ this many distinct directors → crowded
_SUPPRESSION_DAYS = 60      # orchestrator suppresses other signals for this long

# B-094: human date formats the scraper may have stored before the ISO fix.
_HUMAN_DATE_FMTS = ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")


def _normalise_announced_at(s: str | None) -> str | None:
    """Return 'YYYY-MM-DD' from s, or None if unparseable.

    Handles ISO dates/timestamps and the 'DD Mon YYYY' headline format the
    scraper stored before B-094.  Previously b2 did a blind [:10] slice and
    silently returned None on non-ISO values.
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


def evaluate(tx, conn, as_of: str):
    """Return a kill signal dict if this tx belongs to a crowded cluster.

    Parameters
    ----------
    tx   : sqlite3.Row for the transaction being evaluated
    conn : read-only DB connection (walk-forward safe — as_of guards query)
    as_of: ISO-8601 date string (== tx["announced_at"])

    Returns None if the crowded-cluster threshold is not met.
    """
    # Gate 1: only BUY transactions
    if tx["type"] != "BUY":
        return None

    # Gate 2: only strict buys
    if tx["buy_strictness"] != "STRICT_BUY":
        return None

    # Gate 3: announced_at must be present (needed for walk-forward window)
    announced_at = tx["announced_at"]
    if not announced_at:
        return None

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

    window_start = (ann_dt - timedelta(days=_CROWDED_WINDOW_DAYS)).strftime("%Y-%m-%d")

    # Count distinct directors who bought this ticker in the 30-day window
    # ending on as_of (walk-forward safe — no future data leaks through).
    row = conn.execute(
        "SELECT COUNT(DISTINCT director) AS n "
        "FROM transactions "
        "WHERE ticker = ? "
        "  AND type = 'BUY' "
        "  AND COALESCE(buy_strictness, 'STRICT_BUY') = 'STRICT_BUY' "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at >= ? "
        "  AND announced_at <= ?",
        (tx["ticker"], window_start, as_of),
    ).fetchone()

    n_directors = int((row["n"] if row else 0) or 0)

    if n_directors < _MIN_DIRECTORS:
        return None

    metadata = {
        "ticker": tx["ticker"],
        "n_distinct_directors_30d": n_directors,
        "window_start": window_start,
        "suppression_days": _SUPPRESSION_DAYS,
    }

    return {
        "signal_id": SIGNAL_ID,
        "signal_version": SIGNAL_VERSION,
        "fingerprint": tx["fingerprint"],
        "fired_at": None,          # orchestrator sets this
        "confidence": "kill",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
