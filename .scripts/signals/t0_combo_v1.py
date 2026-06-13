"""T0 -- Cluster + senior-exec combo signal (spec 05).

Fires when, for the same fingerprint, all of:
  * S1 has fired (the tx is part of a multi-director cluster), AND
  * Either T1 has fired (CEO/CFO buy) OR T2 has fired (other senior
    exec buy) -- in EITHER case the buyer is senior, and the cluster
    is corroborated by other directors.

T0 runs AFTER the rest of the registry in the orchestrator, so by the
time `evaluate` is called the `signals` table already carries the
T1/T2/S1 firings (orchestrator commits between the two passes).

Q-T0-VERSION locked: T0's signal_version does NOT chain when
T1/T2/S1 bump. We record the observed sub-signal versions in
`metadata` instead.

Walk-forward gate. `signals.fired_at` is the SYSTEM clock at the moment
the signal evaluator ran (set via db.iso_now()), NOT a market-time
field. Using fired_at as the walk-forward bound would be wrong in any
backtest-replay scenario because the orchestrator's evaluation run
happens at "now", not at `tx.announced_at`. The correct walk-forward
bound is `transactions.announced_at`. We therefore JOIN signals to
transactions and gate on `t.announced_at <= as_of`.

Because T0 evaluates for a specific tx and queries the SAME fingerprint,
the joined `t.announced_at` is exactly `tx.announced_at`, which by
construction satisfies the gate. The gate becomes meaningful if a
caller passes a different as_of (e.g. mid-test injection of a
future-dated stub firing) -- in that case the JOINed announced_at is
checked.

Confidence: "high" per Stage 4 prompt decision #7 (T0 inherits T1's
high-conviction band by design).
"""
from __future__ import annotations

import json


SIGNAL_ID = "t0_cluster_combo"
SIGNAL_VERSION = "1.0.0"

# Sub-signals that must (at least partially) be present for T0 to fire.
# B-025 Phase B update: t1_ceo_cfo_buy was split into t1a_ceo_founder_buy
# and t1b_cfo_buy. T0 accepts any of the high-conviction tier signals.
_REQUIRED_S1 = "s1_cluster_buy"
_REQUIRED_T1_OR_T2 = (
    "t1a_ceo_founder_buy",  # CEO/Founder (was half of t1_ceo_cfo_buy)
    "t1b_cfo_buy",          # CFO (was other half of t1_ceo_cfo_buy)
    "t7_chair_buy",         # Chair (new bucket, also high-conviction)
    "t2_exec_buy",          # Other senior exec
)


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None

    rows = conn.execute(
        "SELECT s.signal_id, s.signal_version "
        "FROM signals s "
        "JOIN transactions t ON t.fingerprint = s.fingerprint "
        "WHERE s.fingerprint = ? "
        "  AND t.announced_at IS NOT NULL "
        "  AND t.announced_at <= ?",
        (tx["fingerprint"], as_of),
    ).fetchall()
    if not rows:
        return None
    seen = {r["signal_id"]: r["signal_version"] for r in rows}

    if _REQUIRED_S1 not in seen:
        return None
    if not any(s in seen for s in _REQUIRED_T1_OR_T2):
        return None

    sub_versions = {sid: seen[sid] for sid in seen
                    if sid in (_REQUIRED_S1,) + _REQUIRED_T1_OR_T2}

    metadata = {
        "value_gbp": tx["value"] or 0,
        "role": tx["role"],
        "ticker": tx["ticker"],
        "sub_signal_versions": sub_versions,
    }
    return {
        "signal_id": SIGNAL_ID,
        "signal_version": SIGNAL_VERSION,
        "fingerprint": tx["fingerprint"],
        "fired_at": None,
        "confidence": "high",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
