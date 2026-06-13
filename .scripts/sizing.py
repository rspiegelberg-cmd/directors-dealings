"""B-115 / spec 07 — conviction position sizing.

Pure functions mapping a director's £ transaction value to a paper-trade
notional. No DB access. Shared by eval_signals.py (write path) and
export_dashboard_json.py (read-only Phase-A estimate) so both books agree.

Spec 07 decisions (Rupert sign-off 2026-06-05):
  D1 sizing function : log-scaled (default); tier / linear / flat alternatives
  D2 bounds          : £500 floor, £5,000 cap
  D3 portfolio cap   : none (per-trade bounds only)
"""
from __future__ import annotations

import math

# --- Spec 07 D2: bounds ---
FLOOR_GBP = 500.0
CAP_GBP = 5_000.0          # was 50_000 pre-B-115

# --- Spec 07 D1: sizing-function parameters ---
DEFAULT_SIZING = "log"
LOG_BASE = 1_000.0         # £ added per 10x of director value
LOG_REF = 50_000.0         # reference value in log10(value / LOG_REF)
LINEAR_FACTOR = 0.005      # Option C: 0.5% of transaction value

# Option B tiers: (lower-bound-inclusive £, notional £), highest bound first.
TIERS = (
    (1_000_000.0, 5_000.0),
    (250_000.0, 2_000.0),
    (50_000.0, 1_000.0),
    (10_000.0, 500.0),
)

SCHEMES = ("flat", "log", "tier", "linear")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def position_size(value_gbp, sizing: str = DEFAULT_SIZING,
                  floor: float = FLOOR_GBP, cap: float = CAP_GBP) -> float:
    """Map a director's £ transaction value to a paper-trade notional (£).

    Always clamped to [floor, cap]. A missing / non-positive value sizes at
    the floor — unknown conviction is treated as low, never high.

    Schemes (spec 07 D1):
      flat   — fixed reference notional (LOG_BASE), conviction-blind
      log    — LOG_BASE * log10(value / LOG_REF); smooth over ~4 orders of mag
      tier   — step function over TIERS
      linear — value * LINEAR_FACTOR (0.5%)
    """
    if sizing not in SCHEMES:
        raise ValueError(
            f"unknown sizing scheme: {sizing!r} (expected one of {SCHEMES})"
        )

    if value_gbp is None or value_gbp <= 0:
        return floor

    if sizing == "flat":
        raw = LOG_BASE
    elif sizing == "log":
        raw = LOG_BASE * math.log10(value_gbp / LOG_REF)
    elif sizing == "linear":
        raw = value_gbp * LINEAR_FACTOR
    else:  # tier
        raw = floor
        for lower, notional in TIERS:
            if value_gbp >= lower:
                raw = notional
                break

    return _clamp(raw, floor, cap)
