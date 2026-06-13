"""T4 — Catch-all discretionary buy (spec 05).

Fires when:
  * tx.type == "BUY"
  * (tx.value or 0) >= 1_000  GBP
  * classify_role(tx.role) == "T4"  (everything not bucketed into T1/T2/T3)

The orchestrator's tier dedup pass independently strips T4 when any of
T1/T2/T3 also fired for the same fingerprint, but T4 is ALSO gated here
on the role classifier returning "T4". This is intentional belt-and-
braces: T4 should never fire on a clearly classified senior role even
if the orchestrator's dedup hypothetically misbehaved.

Walk-forward: no DB SELECT issued; pure tx-local logic.

Confidence: "low" per Stage 4 prompt decision #7.
"""
from __future__ import annotations

import json

from . import roles


SIGNAL_ID = "t4_other_buy"
SIGNAL_VERSION = "1.0.0"

THRESHOLD_GBP = 1_000.0


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    role_class = roles.classify_role(tx["role"])
    if role_class != "T4":
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
        "confidence": "low",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
