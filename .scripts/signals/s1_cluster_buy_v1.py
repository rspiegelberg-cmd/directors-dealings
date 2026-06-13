"""S1 -- Cluster buy (spec 05 + spec 01).

Fires when:
  * tx.type == "BUY"
  * tx.cluster_id IS NOT NULL  (populated by detect_clusters.detect)
  * the cluster visible as-of `as_of` has >= 2 distinct directors

The orchestrator runs detect_clusters.detect(conn, as_of) BEFORE
evaluating S1, which is why we can trust tx["cluster_id"] as the
authoritative cluster membership.

Walk-forward: the distinct-director count query is gated by
`announced_at <= as_of` to make sure a future-arriving director
doesn't artificially inflate the count when re-evaluating a
historical tx.

Q-CLUSTER-90D-ACTIVE locked: S1 fires on EVERY cluster row regardless
of cluster age. The 90-day "active" filter is a Stage 5 dashboard
concern only.

Confidence: "med" per Stage 4 prompt decision #7.
"""
from __future__ import annotations

import json


SIGNAL_ID = "s1_cluster_buy"
SIGNAL_VERSION = "1.0.0"


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    cluster_id = tx["cluster_id"]
    if not cluster_id:
        return None
    if tx["announced_at"] is None:
        return None

    # Count distinct directors in the cluster, walk-forward gated.
    row = conn.execute(
        "SELECT COUNT(DISTINCT director) AS n "
        "FROM transactions "
        "WHERE cluster_id = ? "
        "  AND type = 'BUY' "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at <= ?",
        (cluster_id, as_of),
    ).fetchone()
    n_directors = row["n"] if row else 0
    if (n_directors or 0) < 2:
        return None

    # Collect cluster member fingerprints (for metadata transparency).
    members = conn.execute(
        "SELECT fingerprint, director, announced_at "
        "FROM transactions "
        "WHERE cluster_id = ? "
        "  AND type = 'BUY' "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at <= ? "
        "ORDER BY announced_at",
        (cluster_id, as_of),
    ).fetchall()
    member_fingerprints = [m["fingerprint"] for m in members]

    metadata = {
        "value_gbp": tx["value"] or 0,
        "role": tx["role"],
        "ticker": tx["ticker"],
        "cluster_id": cluster_id,
        "cluster_director_count": n_directors,
        "cluster_member_fingerprints": member_fingerprints,
    }
    return {
        "signal_id": SIGNAL_ID,
        "signal_version": SIGNAL_VERSION,
        "fingerprint": tx["fingerprint"],
        "fired_at": None,
        "confidence": "med",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
