"""Role classifier for the Performance page redesign v1.

B-025 Phase B (2026-05-20): switched from regex-on-raw-role to dict
lookup on the canonical bucket produced by `role_normalize.normalize_role()`.

Phase B refinement (same date): the 3-bucket schema (ceo_cfo / other_exec
/ ned) was further split into 6 per-tier rows on the Performance page so
each cohort can be measured independently. The function now returns one
of the per-tier strings below.

Returns one of:
  * 't1a'  — CEO / Founder bucket
  * 't1b'  — CFO bucket (incl. UK Finance / Financial Director synonyms)
  * 't2'   — other senior exec (Other Chief, Executive Director, Divisional
             Exec, President / VP)
  * 't3'   — Non-Executive Directors (incl. SID, Non-executive Chairman,
             Supervisory Board)
  * 't5'   — PCA (Person Closely Associated — spouse / family trust /
             connected party)
  * 't7'   — Chair (executive or non-executive Chair / Chairman bare)
  * None   — catch-all (T4 catch-all / T6 Company Sec / PDMR-only / parser
             fragments); deliberately excluded from the role tile per the
             locked per-tier scope.

The function never raises on unmappable inputs; it returns None.

Locked precedence per spec `docs/specs/performance-page-redesign-v1.md` §5.4
and backend plan §3.1. After B-025 Phase B the precedence is enforced by
`normalize_role()` rather than regex ordering. CEO beats Chair; NED beats
Chair (so a "Non-executive Chairman" maps to t3, not t7).

The `role_class` argument is RETAINED for backward compatibility with
legacy backtest CSV rows that have "T1"/"T2"/"T3"/"T4" in the role_class
column. Post Phase B, role_class values are the new 8-tier strings
("T1a", "T1b", "T2", "T3", "T4", "T5", "T6", "T7"). Legacy "T1" falls
back to t1a (the larger CEO/Founder half) — see _LEGACY_ROLE_CLASS_TO_TILE
below.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make .scripts/role_normalize.py importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from role_normalize import normalize_role  # noqa: E402


# Canonical bucket -> Performance-page tile key.
# B-025 Phase B refinement (2026-05-20): the 3-bucket scheme
# (ceo_cfo / other_exec / ned) is replaced by 6 per-tier rows so Rupert
# can see CEO+Founder vs CFO vs Chair vs other exec vs NED vs PCA
# separately on the role tile.
#
# T4 (catch-all) and T6 (Company Sec, only ~2 firings) deliberately
# return None — too small and too noisy to merit a tile row.
_BUCKET_TO_PERF_TILE: dict[str, str | None] = {
    "CEO":                                  "t1a",
    "Founder":                              "t1a",
    "CFO":                                  "t1b",
    "Other Chief":                          "t2",
    "Executive Director":                   "t2",
    "Divisional / Regional Exec":           "t2",
    "President / VP":                       "t2",
    "Chair (executive)":                    "t7",
    "Non-Exec Chair":                       "t7",
    "NED":                                  "t3",
    "PCA":                                  "t5",
    "PDMR-only":                            None,
    "Company Secretary / General Counsel":  None,
    "Other / unclassified":                 None,
    "Parser fragment":                      None,
}

# Legacy role_class strings -> Performance-page tile. Keeps backward
# compatibility with backtest CSV rows from before Phase B that have
# "T1"/"T2"/"T3"/"T4" in the role_class column. After re-running the
# backtest, role_class values are the new 8-tier strings (T1a, T1b,
# T2, T3, T4, T5, T6, T7) — see signals/roles.py.
_LEGACY_ROLE_CLASS_TO_TILE: dict[str, str | None] = {
    "T1":  "t1a",   # legacy combined falls back to T1a (the larger half)
    "T1A": "t1a",
    "T1B": "t1b",
    "T2":  "t2",
    "T3":  "t3",
    "T4":  None,
    "T5":  "t5",
    "T6":  None,    # Company Sec — too small for the tile
    "T7":  "t7",
}


def classify_role(role_class, role_str):
    """Classify a director firing into one of the six Performance-page tiles.

    Args:
        role_class: The `role_class` column from `_backtest_results.csv`
            (often 'T1', 'T2', 'T3', 'T4', or — post-Phase-B — 'T1a',
            'T1b', 'T5', 'T6', 'T7'). May be None or empty.
        role_str: The free-text role string (often the literal title
            from the RNS announcement). May be None.

    Returns:
        't1a' | 't1b' | 't2' | 't3' | 't5' | 't7' | None.

        Never raises on unmappable inputs — returns None.

    Order of resolution (first match wins):
        1. If role_class is set and recognised, use it. This preserves
           the legacy behaviour where the backtest CSV's role_class
           column drove the tile. Legacy 'T1' maps to 't1a'.
        2. Otherwise, call normalize_role() on the raw role string and
           look up the canonical bucket in _BUCKET_TO_PERF_TILE.
    """
    # 1. Prefer the explicit role_class when provided and recognised.
    rc = (role_class or "").strip().upper()
    if rc in _LEGACY_ROLE_CLASS_TO_TILE:
        return _LEGACY_ROLE_CLASS_TO_TILE[rc]

    # 2. Fall back to bucket lookup on raw role.
    bucket = normalize_role(role_str)
    return _BUCKET_TO_PERF_TILE.get(bucket)


# B-039 (2026-05-21): removed the dead CEO_CFO_RE / NED_RE / OTHER_EXEC_RE
# regex constants and the trailing `import re` they needed. They were
# kept after B-025 Phase B for backward-import compatibility but
# nothing imports them (grep across the repo confirms only docstring
# mentions in test_classify_role.py remain). classify_role() itself
# uses the deterministic normalize_role() pipeline, not these regexes.

__all__ = ["classify_role"]
