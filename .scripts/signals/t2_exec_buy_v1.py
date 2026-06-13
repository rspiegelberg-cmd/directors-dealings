"""T2 — Other senior executive opportunistic buy (spec 05).

Fires when:
  * tx.type == "BUY"
  * classify_role(tx.role) == "T2"  (Chair, Director, Executive, COO/CTO/CCO,
    "Chief X Officer" other than CRO)
  * (tx.value or 0) >= 25_000  GBP

The "not already T1" rule is enforced by the orchestrator's tier dedup
pass, not inside this evaluator (locked decision #11).

Walk-forward: no DB SELECT issued; pure tx-local logic.

Confidence: "med" per Stage 4 prompt decision #7.
"""
from __future__ import annotations

import json

from . import roles


SIGNAL_ID = "t2_exec_buy"
SIGNAL_VERSION = "1.0.0"

THRESHOLD_GBP = 25_000.0


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    role_class = roles.classify_role(tx["role"])
    if role_class != "T2":
        return None
    value = tx["value"] or 0
    if value < THRESHOLD_GBP:
        return None
    metadata = {
        "value_gbp": value,
        "role": tx["role"],
        "role_class": role_class,
        "ticker": tx["ticker"],
    }
    return {
        "signal_id": SIGNAL_ID,
        "signal_version": SIGNAL_VERSION,
        "fingerprint": tx["fingerprint"],
        "fired_at": None,
        "confidence": "med",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
