"""Stage 4 — cluster detector.

Reads BUY transactions visible as-of `as_of` and groups them into
connected components per spec 01:

  * Same ticker.
  * Each pair (i, j) is connected if |date_i - date_j| <= 30 CALENDAR
    days AND director_i != director_j.
  * Connected components are computed by union-find -- transitivity
    is respected. A at D, B at D+25, C at D+50 -> {A, B, C} even though
    A and C are 50 days apart, because A--B and B--C bridge them.
  * A "cluster" is a component containing >= 2 DISTINCT directors.
  * cluster_id = "{ticker}-{first_buy_date}" where first_buy_date is
    the earliest tx.date within the cluster.

The detector is idempotent. Re-running over the same as_of produces
the same cluster_id state. Re-running with a later as_of may grow
existing clusters or form new ones.

Walk-forward: only transactions with announced_at IS NOT NULL AND
announced_at <= as_of are considered. Future-of-as_of buys are
invisible. This is what makes the detector safe to call from
eval_signals when walking each tx's own announced_at as as_of.

The cluster_id column is reset to NULL for any non-clustered tx,
including any tx that USED to belong to a cluster but doesn't now.
This keeps the column consistent and avoids stale cluster_ids.

CLI:
    python detect_clusters.py [--as-of YYYY-MM-DD] [--verbose]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402


WINDOW_DAYS = 30


def _to_date(s: str) -> date:
    # L-C: reject obviously-short strings before slicing — `s[:10]` on a 6-char
    # input silently truncates to a malformed value that date.fromisoformat
    # then refuses with a less helpful message.
    if not s or len(s) < 10:
        raise ValueError(f"detect_clusters._to_date: expected ISO date YYYY-MM-DD, got {s!r}")
    return date.fromisoformat(s[:10])


def _union(parent: dict, a, b) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def _find(parent: dict, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def detect(conn, as_of: str, verbose: bool = False) -> dict:
    """Run the cluster detector. Updates transactions.cluster_id in place.

    Returns a summary dict: {n_clusters, n_clustered_tx, n_reset_tx}.

    B-044 (P-A): `as_of` MUST NOT be in the future. The walk-forward gate
    on the SELECT below (`announced_at <= ?`) keeps the detector itself
    correct, but a caller that passes a stray future date silently relaxes
    the spec-P3-6 "no lookahead bias" invariant for any downstream code
    that pairs detect() output with as_of-keyed price data. Fail loud here.
    """
    if _to_date(as_of) > date.today():
        raise ValueError(
            f"detect_clusters.detect: as_of={as_of!r} is in the future; "
            "P3-6 (no lookahead bias) requires as_of <= today. Pass an "
            "announced_at value when walking forward."
        )

    rows = conn.execute(
        "SELECT fingerprint, ticker, director, date, announced_at "
        "FROM transactions "
        "WHERE type = 'BUY' "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at <= ? "
        "  AND ticker IS NOT NULL "
        "ORDER BY ticker, date, fingerprint",
        (as_of,),
    ).fetchall()

    # Group by ticker for per-ticker union-find.
    by_ticker: dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    cluster_assignments: dict[str, str] = {}  # fingerprint -> cluster_id

    for ticker, txs in by_ticker.items():
        if len(txs) < 2:
            continue
        # Union-find over indices into `txs`.
        parent = {i: i for i in range(len(txs))}
        for i, ri in enumerate(txs):
            di = _to_date(ri["date"])
            for j in range(i + 1, len(txs)):
                rj = txs[j]
                dj = _to_date(rj["date"])
                if (dj - di).days > WINDOW_DAYS:
                    # txs are sorted by date; once gap > 30, break.
                    break
                if ri["director"] != rj["director"]:
                    _union(parent, i, j)

        # Collect components.
        comps: dict = {}
        for i in range(len(txs)):
            root = _find(parent, i)
            comps.setdefault(root, []).append(i)

        for root, members in comps.items():
            distinct_dirs = {txs[m]["director"] for m in members}
            if len(distinct_dirs) < 2:
                continue
            first_date = min(txs[m]["date"] for m in members)
            cluster_id = f"{ticker}-{first_date}"
            for m in members:
                cluster_assignments[txs[m]["fingerprint"]] = cluster_id

    # Apply: set cluster_id for every clustered tx; reset cluster_id to
    # NULL for every tx that is currently clustered but shouldn't be.
    cur = conn.cursor()
    # Reset: any tx with a non-null cluster_id that isn't in the new
    # assignments gets cleared. Walk-forward gated: only tx visible
    # as-of as_of are touched.
    cur.execute(
        "SELECT fingerprint FROM transactions "
        "WHERE cluster_id IS NOT NULL "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at <= ?",
        (as_of,),
    )
    currently = {r["fingerprint"] for r in cur.fetchall()}
    to_reset = currently - set(cluster_assignments.keys())
    for fp in to_reset:
        cur.execute(
            "UPDATE transactions SET cluster_id = NULL WHERE fingerprint = ?",
            (fp,),
        )
    for fp, cid in cluster_assignments.items():
        cur.execute(
            "UPDATE transactions SET cluster_id = ? WHERE fingerprint = ?",
            (cid, fp),
        )
    conn.commit()

    summary = {
        "n_clusters": len({cid for cid in cluster_assignments.values()}),
        "n_clustered_tx": len(cluster_assignments),
        "n_reset_tx": len(to_reset),
    }
    if verbose:
        print(f"detect_clusters as_of={as_of}: {summary}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Detect director-dealing clusters.")
    parser.add_argument("--as-of", default=None,
                        help="ISO date YYYY-MM-DD. Default: today.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    as_of = args.as_of or date.today().isoformat()

    conn = None
    try:
        conn = db.connect()
        summary = detect(conn, as_of, verbose=args.verbose)
        print(f"clusters={summary['n_clusters']}  "
              f"clustered_tx={summary['n_clustered_tx']}  "
              f"reset_tx={summary['n_reset_tx']}  as_of={as_of}")
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
