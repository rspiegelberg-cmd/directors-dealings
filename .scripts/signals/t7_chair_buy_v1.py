"""T7 — Chair (executive OR non-executive) buy (B-025 Phase B, new cohort).

Fires when:
  * tx.type == "BUY"
  * classify_role(tx.role) == "T7"  (Chair, Chairman, Executive Chair,
                                     Non-Executive Chair, Independent Chair,
                                     Chair of the Board, etc.)
  * (tx.value or 0) >= 25_000  GBP

Why a Chair-specific signal:
  Chair buys are the LARGEST tier by aggregate £ value in the corpus
  (£65.92m of historical BUY value — 32.8% of total, more than CEO/Founder
  + CFO combined). Before B-025 Phase B these were split across T2 (exec
  chair) and T3 (non-exec chair). Phase B groups them as one cohort
  because both kinds of chair sit at the top of the governance structure
  and have similar information access (board materials, all executive
  conversations).

  Note: this combines exec and non-exec chairs. Their information sets
  differ slightly — exec chairs are often founder-chairs or hands-on
  operators (closer to T1a conviction); non-exec chairs are governance
  and oversight (closer to T3 NED). If signal performance diverges,
  consider splitting into T7a (exec) and T7b (non-exec) in a future
  spec.

Walk-forward: no DB SELECT issued; pure tx-local logic.

Confidence: "med".
"""
from __future__ import annotations

import json

from . import roles


SIGNAL_ID = "t7_chair_buy"
SIGNAL_VERSION = "1.0.0"

THRESHOLD_GBP = 25_000.0


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    role_class = roles.classify_role(tx["role"])
    if role_class != "T7":
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
