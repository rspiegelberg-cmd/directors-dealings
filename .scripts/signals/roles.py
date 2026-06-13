"""Role classification ladder for the Directors Dealings signal engine.

B-025 Phase B (2026-05-20): switched from regex-on-raw-role to dict
lookup on the canonical bucket produced by `role_normalize.normalize_role()`.
The 14-bucket taxonomy is mapped to 8 tier strings:

  T1a -- CEO + Founder    (top-of-pyramid, highest conviction)
  T1b -- CFO              (financial-knowledge specific)
  T2  -- Other senior exec (Chief X Officer, Exec Director, MD,
                            Divisional/Regional Exec, President/VP)
  T3  -- NED              (Non-Executive Director, incl. SID, Supervisory Board)
  T4  -- Catch-all        (PDMR-only, Other, Parser fragment)
  T5  -- PCA              (Persons Closely Associated — spouses, family
                            trusts. Currently NO signal fires on T5 — they
                            are an identified cohort for future signals.)
  T6  -- Company Secretary / General Counsel
                          (Also no signal fires on T6 — institutional role
                            with limited insider information.)
  T7  -- Chair            (Both executive and non-executive chair, combined.
                            Board-level signal with its own t7_chair_buy.)

The mapping table below is the contract — DO NOT REORDER without
updating the signal modules and the orchestrator's TIER_RANK table.

Why this change:
  * Old regex-on-raw-role missed 4 case variants of "Non-Executive Director"
    and misfired on "PCA of CEO" (the "CEO" substring matched T1).
  * The canonical-bucket approach makes every role-conditional decision
    a deterministic dict lookup.
  * PCA, Company Secretary, and Chair are now distinct cohorts — they
    no longer pollute the load-bearing T1/T2/T3 signals.

Pure stdlib. No DB dependency. No third-party packages.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make .scripts/role_normalize.py importable. The signals package lives
# at .scripts/signals/; the mapper is at .scripts/role_normalize.py.
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from role_normalize import normalize_role  # noqa: E402


# Canonical bucket -> tier string. The only source of truth.
_BUCKET_TO_TIER: dict[str, str] = {
    "CEO":                                  "T1a",
    "Founder":                              "T1a",
    "CFO":                                  "T1b",
    "Other Chief":                          "T2",
    "Executive Director":                   "T2",
    "Divisional / Regional Exec":           "T2",
    "President / VP":                       "T2",
    "NED":                                  "T3",
    "PDMR-only":                            "T4",
    "Other / unclassified":                 "T4",
    "Parser fragment":                      "T4",
    "PCA":                                  "T5",
    "Company Secretary / General Counsel":  "T6",
    "Chair (executive)":                    "T7",
    "Non-Exec Chair":                       "T7",
}


def classify_role(role: str | None) -> str:
    """Return the tier string for `role`.

    One of: "T1a" | "T1b" | "T2" | "T3" | "T4" | "T5" | "T6" | "T7".

    Returns "T4" for None/empty/unclassifiable roles. Case-insensitive.
    Never raises.

    The mapping is deterministic via `role_normalize.normalize_role()`.
    The 14 canonical buckets cleanly cover every observed role variant
    in the live corpus (2026-05-20 audit).
    """
    bucket = normalize_role(role)
    return _BUCKET_TO_TIER.get(bucket, "T4")


# All canonical tier strings — useful for tests / orchestrator.
ALL_TIERS: tuple[str, ...] = (
    "T1a", "T1b", "T2", "T3", "T4", "T5", "T6", "T7",
)
