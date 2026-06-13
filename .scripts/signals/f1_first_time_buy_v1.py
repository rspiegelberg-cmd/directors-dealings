"""F1 — First-time buy for a (director, ticker) pair (spec 05).

Fires when:
  * tx.type == "BUY"
  * No prior BUY row exists in `transactions` with the SAME
    (director, ticker) and `announced_at < tx.announced_at`.

Walk-forward: the lookup query takes BOTH bounds:
  * `announced_at < tx.announced_at`   (strictly earlier than firing tx)
  * `announced_at <= as_of`            (no future-of-as_of rows)

Confidence: "med" per Stage 4 prompt decision #7.
"""
from __future__ import annotations

import json


SIGNAL_ID = "f1_first_time_buy"
SIGNAL_VERSION = "1.0.0"


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    if tx["announced_at"] is None:
        return None

    # Walk-forward gated lookup. announced_at is the canonical visibility
    # field; the strict-less-than self-excludes the firing transaction
    # itself if it happens to share its own fingerprint by accident.
    prior = conn.execute(
        "SELECT 1 FROM transactions "
        "WHERE director = ? AND ticker = ? AND type = 'BUY' "
        "  AND announced_at IS NOT NULL "
        "  AND announced_at < ? "
        "  AND announced_at <= ? "
        "LIMIT 1",
        (tx["director"], tx["ticker"], tx["announced_at"], as_of),
    ).fetchone()
    if prior is not None:
        return None

    metadata = {
        "value_gbp": tx["value"] or 0,
        "role": tx["role"],
        "ticker": tx["ticker"],
        "director": tx["director"],
    }
    return {
        "signal_id": SIGNAL_ID,
        "signal_version": SIGNAL_VERSION,
        "fingerprint": tx["fingerprint"],
        "fired_at": None,
        "confidence": "med",
        "metadata": json.dumps(metadata, separators=(",", ":")),
    }
