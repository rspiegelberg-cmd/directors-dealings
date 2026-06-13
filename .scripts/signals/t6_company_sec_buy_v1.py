"""T6 — Company Secretary / General Counsel buy (B-025 Phase B, new cohort).

Fires when:
  * tx.type == "BUY"
  * classify_role(tx.role) == "T6"  (Company Secretary, General Counsel,
                                     Group Legal Director, Group General
                                     Counsel, etc.)
  * (tx.value or 0) >= 10_000  GBP

Why a Company Sec / GC-specific signal:
  Before B-025 Phase B, these roles were rolled into T4 (catch-all) or
  silently misfiring as T2 (because "Director" or "Officer" matched).
  Company Secretaries and General Counsels carry institutional knowledge
  but a different information set from operational executives — they see
  board materials, legal matters, M&A early. Worth tracking as a distinct
  cohort, though signal strength is expected to be modest (30 total
  transactions in the corpus as of 2026-05-20, 7 BUYs).

Walk-forward: no DB SELECT issued; pure tx-local logic.

Confidence: "low" — institutional role, limited operational insight.
"""
from __future__ import annotations

import json

from . import roles


SIGNAL_ID = "t6_company_sec_buy"
SIGNAL_VERSION = "1.0.0"

THRESHOLD_GBP = 10_000.0


def evaluate(tx, conn, as_of: str):
    if tx["type"] != "BUY":
        return None
    role_class = roles.classify_role(tx["role"])
    if role_class != "T6":
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
