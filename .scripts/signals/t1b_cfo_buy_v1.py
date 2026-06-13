"""T1b — CFO opportunistic buy (B-025 Phase B, replaces t1_ceo_cfo_buy).

Fires when:
  * tx.type == "BUY"
  * classify_role(tx.role) == "T1b"  (CFO, Chief Financial Officer,
                                      Finance Director, Group CFO, etc.)
  * (tx.value or 0) >= 100_000  GBP

Split from the original t1_ceo_cfo_buy as part of B-025 Phase B so CFO
firings can be tracked separately from CEO/Founder firings. CFO buys
have a distinct information edge (often precede earnings beats) that's
worth measuring in isolation.

Walk-forward: no DB SELECT issued; pure tx-local logic.

Confidence: "high".
"""
from __future__ import annotations

import json

from . import roles


SIGNAL_ID = "t1b_cfo_buy"
SIGNAL_VERSION = "1.0.0"

THRESHOLD_GBP = 100_000.0


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    role_class = roles.classify_role(tx["role"])
    if role_class != "T1b":
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
        "confidence": "high",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
