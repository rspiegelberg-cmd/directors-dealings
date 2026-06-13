"""Signal registry for the Stage 4 engine.

Single source of truth for which signals exist, their version, their
evaluator function, and the order they're run by `eval_signals.py`.

B-025 Phase B (2026-05-20): the original t1_ceo_cfo_buy is replaced by
8 per-bucket tier signals. Each canonical bucket from role_normalize
now has its own signal module:

  t1a_ceo_founder_buy  — CEO + Founder       (replaces half of old t1)
  t1b_cfo_buy          — CFO                 (replaces other half of old t1)
  t2_exec_buy          — Other senior exec   (cleaner cohort, chairs moved out)
  t3_ned_buy           — NED                 (cleaner cohort, non-exec chairs moved out)
  t4_other_buy         — Catch-all           (PDMR-only / Other / Parser fragment)
  t5_pca_buy           — PCA                 (NEW — was misfiring as T1/T2/T3)
  t6_company_sec_buy   — Company Sec / GC    (NEW)
  t7_chair_buy         — Chair (exec + NE)   (NEW — preserves chair firings
                                              that used to fire as T2/T3)

The other signals (s1_cluster_buy, f1_first_time_buy, t0_cluster_combo)
are unchanged.

T0 runs LAST because it inspects the `signals` table for prior firings
on the same fingerprint — those must already be persisted. The
orchestrator commits between the pre-T0 pass and the T0 pass.

Tier dedup (T1a > T1b > T7 > T2 > T3 > T5 > T6 > T4) is enforced by the
orchestrator, NOT inside individual evaluator modules. Each evaluator
is a pure function of (tx, conn, as_of). The orchestrator decides
which to keep.
"""
from __future__ import annotations

from . import (
    b1_lone_conviction_buy_v1,
    b2_crowded_cluster_kill_v1,
    f1_first_time_buy_v1,
    roles,
    s1_cluster_buy_v1,
    t0_combo_v1,
    t1a_ceo_founder_buy_v1,
    t1b_cfo_buy_v1,
    t2_exec_buy_v1,
    t3_ned_buy_v1,
    t4_other_buy_v1,
    t5_pca_buy_v1,
    t6_company_sec_buy_v1,
    t7_chair_buy_v1,
)

# (signal_id, signal_version, evaluator_module) in EVAL_ORDER.
# Per-bucket tier signals (B-025 Phase B) come first, then cluster and
# first-time, then t0 (which depends on the others already firing).
SIGNALS = [
    ("t1a_ceo_founder_buy",    "1.0.0", t1a_ceo_founder_buy_v1),
    ("t1b_cfo_buy",            "1.0.0", t1b_cfo_buy_v1),
    ("t7_chair_buy",           "1.0.0", t7_chair_buy_v1),
    ("t2_exec_buy",            "1.0.0", t2_exec_buy_v1),
    ("t3_ned_buy",             "1.0.0", t3_ned_buy_v1),
    ("t5_pca_buy",             "1.0.0", t5_pca_buy_v1),
    ("t6_company_sec_buy",     "1.0.0", t6_company_sec_buy_v1),
    ("t4_other_buy",           "1.0.0", t4_other_buy_v1),
    ("s1_cluster_buy",         "1.0.0", s1_cluster_buy_v1),
    ("f1_first_time_buy",      "1.0.0", f1_first_time_buy_v1),
    ("b1_lone_conviction_buy",      "1.0.0", b1_lone_conviction_buy_v1),
    ("b2_crowded_cluster_kill",    "1.0.0", b2_crowded_cluster_kill_v1),
    ("t0_cluster_combo",           "1.0.0", t0_combo_v1),
]

# Convenience map signal_id -> module.
REGISTRY = {sid: mod for sid, _v, mod in SIGNALS}

# All signal_ids in evaluation order. T0 last.
EVAL_ORDER = [sid for sid, _v, _m in SIGNALS]

# Signals whose evaluation depends on the `signals` table having
# already been populated by the same run. T0 is the only one.
DEPENDENT_SIGNALS = ("t0_cluster_combo",)

# Tier rank for orchestrator dedup. Lower number = higher priority.
# When multiple tier signals fire on the same fingerprint, the
# orchestrator keeps the one with the lowest rank. The ordering
# reflects information-content conviction: CEO/Founder buys are the
# strongest direct signal; PCA / Company Sec are weaker.
TIER_RANK = {
    "t1a_ceo_founder_buy":  1,  # highest conviction
    "t1b_cfo_buy":          2,
    "t7_chair_buy":         3,
    "t2_exec_buy":          4,
    "t3_ned_buy":           5,
    "t5_pca_buy":           6,
    "t6_company_sec_buy":   7,
    "t4_other_buy":         8,  # catch-all
}


def iter_signals():
    """Yield (signal_id, signal_version, module) in EVAL_ORDER."""
    for sid, ver, mod in SIGNALS:
        yield sid, ver, mod


def get_signal(signal_id: str):
    """Return the evaluator module for `signal_id`, or raise KeyError."""
    return REGISTRY[signal_id]


__all__ = [
    "SIGNALS", "REGISTRY", "EVAL_ORDER", "DEPENDENT_SIGNALS",
    "TIER_RANK", "iter_signals", "get_signal", "roles",
]
