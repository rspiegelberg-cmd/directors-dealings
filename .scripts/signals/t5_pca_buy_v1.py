"""T5 — Person Closely Associated (PCA) buy (B-025 Phase B, new cohort).

Fires when:
  * tx.type == "BUY"
  * classify_role(tx.role) == "T5"  (PCA / spouse / family trust /
                                     connected person — anyone closely
                                     associated with a PDMR)
  * (tx.value or 0) >= 10_000  GBP

Why a PCA-specific signal:
  Before B-025 Phase B, PCA transactions were silently misfiring as
  T1/T2/T3 because the regex matched the role of the PERSON THEY WERE
  ASSOCIATED WITH (e.g. "PCA of CEO" was firing as T1). Phase A
  identified 143 PCA transactions, of which 114 were polluting real-
  insider tiers. After Phase B these route correctly to T5.

  PCA buys carry indirect insider information — different from direct
  director buys but still meaningful. Tracking them separately allows
  performance measurement of the PCA cohort vs direct-insider cohorts.

Walk-forward: no DB SELECT issued; pure tx-local logic.

Confidence: "low" — weaker signal than direct director purchases.
"""
from __future__ import annotations

import json

from . import roles


SIGNAL_ID = "t5_pca_buy"
SIGNAL_VERSION = "1.0.0"

THRESHOLD_GBP = 10_000.0


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    role_class = roles.classify_role(tx["role"])
    if role_class != "T5":
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
