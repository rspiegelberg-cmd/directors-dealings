"""Stage 4.6/5 -- Dashboard JSON exporter.

Reads SQLite + ``.data/_backtest_results.csv`` and writes two JSON files:
  * ``dashboard/data/signals.json``  -- per-signal aggregates by horizon
  * ``dashboard/data/dealings.json`` -- today + this-week transaction feed

Stdlib-only. Idempotent: same inputs produce byte-identical output (except
``generated_at`` -- use ``--no-timestamp`` for round-trip diff testing).

Locked decisions implemented:

  * Active-cluster definition (Rupert 2026-05-14): 2+ distinct directors at
    same ticker, all BUYs, dates within 30 days. ``s1_active=True`` if the
    most recent buy is within the last 30 days; ``s1_active=False``
    ("brewing") if it's 30-90 days back; stale (>90d) is dropped.
  * MTM: open paper-trade positions use latest ``prices.close``. Per-row
    MTM in dealings.json uses T+1 close after ``announced_at`` as entry,
    net of 50bps spread (+0.5% stamp on non-AIM BUYs).
  * Status auto-computation: per stage-05 design rule (live/review/kill?/
    gated) using the current 4-week vs prior 4-week median CAR_t30 window.
    F1 stays ``gated`` while outlier_flag is true (Stage 4.5 dependency).
    Signals with <5 matured firings in the current window default to
    ``review``.
  * Cohort cuts: by_value_bucket (4 GBP buckets), by_sector (top 5).
  * Pending diagnostics (Stage 5 add-on): aggregate _pending_review.json
    into 7 recoverability buckets for the performance.html dashboard panel.

CLI::

    python export_dashboard_json.py [--dry-run] [--no-timestamp]
                                    [--out-dir PATH] [--pending-path PATH]
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import sqlite3  # B-179: sqlite3.IntegrityError used in the conviction write path
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import sizing  # noqa: E402  (B-115 spec 07 conviction position sizing)
import conviction_pipeline  # noqa: E402  (B-171 weekly conviction score adapter)
from classify_role import classify_role  # noqa: E402 — Sprint 4
from role_normalize import is_corporate_actor, is_related_party  # noqa: E402 — B-136

ROOT = db.DB_DIR.parent
DEFAULT_OUT_DIR = ROOT / "dashboard" / "data"
DEFAULT_CSV_PATH = db.DB_DIR / "_backtest_results.csv"
DEFAULT_CLUSTERS_PATH = db.DB_DIR.parent / ".scripts" / "clusters.json"
DEFAULT_PENDING_PATH = HERE / "_pending_review.json"

SCHEMA_VERSION = "1.1"  # B-025 Phase B: per-bucket tier signals
SIGNAL_ORDER = [
    "t0_cluster_combo",
    "t1a_ceo_founder_buy",
    "t1b_cfo_buy",
    "t7_chair_buy",
    "t2_exec_buy",
    "t3_ned_buy",
    "t5_pca_buy",
    "t6_company_sec_buy",
    "t4_other_buy",
    "s1_cluster_buy",
    "f1_first_time_buy",
    "b1_lone_conviction_buy",
    "b2_crowded_cluster_kill",
]
SIGNAL_SHORT = {
    "t0_cluster_combo":        "t0",
    "t1a_ceo_founder_buy":     "t1a",
    "t1b_cfo_buy":             "t1b",
    "t7_chair_buy":            "t7",
    "t2_exec_buy":             "t2",
    "t3_ned_buy":              "t3",
    "t5_pca_buy":              "t5",
    "t6_company_sec_buy":      "t6",
    "t4_other_buy":            "t4",
    "s1_cluster_buy":          "s1",
    "f1_first_time_buy":       "f1",
    "b1_lone_conviction_buy":      "b1",
    "b2_crowded_cluster_kill":     "b2",
}

# B-104: "By transaction size" tile scope — all buy signals.
# Previously only T1a/T1b/T7/T2; expanded to include T3/T4/T5/T6/S1/F1/B1 so
# that large NED/PCA/other-exec buys appear in the bucket analysis.
# Excludes: b2_crowded_cluster_kill (kill signal, not a buy) and
#           t0_cluster_combo (composite — not a standalone buy).
HIGH_CONVICTION_NON_NED_SIGNALS = (
    "t1a_ceo_founder_buy",
    "t1b_cfo_buy",
    "t2_exec_buy",
    "t3_ned_buy",
    "t4_other_buy",
    "t5_pca_buy",
    "t6_company_sec_buy",
    "t7_chair_buy",
    "s1_cluster_buy",
    "f1_first_time_buy",
    "b1_lone_conviction_buy",
)
HORIZONS = ["t1", "t30", "t90", "t180", "t365"]

# Cohort buckets (lo, hi) in GBP. None == no upper bound.
VALUE_BUCKETS = [
    ("1k-25k",   1_000.0,   25_000.0),
    ("25k-100k", 25_000.0,  100_000.0),
    ("100k-500k", 100_000.0, 500_000.0),
    ("500k+",    500_000.0, None),
]

# Outlier threshold: |CAR| > 200% means data quality issue (F1).
OUTLIER_ABS_CAR = 2.00


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _safe_float(s):
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _round(x, n=1):
    if x is None:
        return None
    return round(x, n)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2, sort_keys=False,
                      ensure_ascii=False) + "\n"
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CSV ingestion
# ---------------------------------------------------------------------------

def load_backtest_csv(path: Path) -> list[dict]:
    """Load _backtest_results.csv into a list of dicts.

    Returns [] if the file is empty or missing (caller decides whether
    to error out).
    """
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["_car_t1"]    = _safe_float(row.get("car_t1"))
            row["_car_t30"]   = _safe_float(row.get("car_t30"))
            row["_car_t90"]   = _safe_float(row.get("car_t90"))
            row["_car_t180"]  = _safe_float(row.get("car_t180"))
            row["_car_t365"]  = _safe_float(row.get("car_t365"))
            # B-066 (Sprint 14): net-of-cost CAR columns for the cohort
            # performance blob. Already net of spread + stamp upstream.
            row["_net_car_t1"]    = _safe_float(row.get("net_car_t1"))
            row["_net_car_t30"]   = _safe_float(row.get("net_car_t30"))
            row["_net_car_t90"]   = _safe_float(row.get("net_car_t90"))
            row["_net_car_t180"]  = _safe_float(row.get("net_car_t180"))
            row["_net_car_t365"]  = _safe_float(row.get("net_car_t365"))
            row["_bench_t1"]      = _safe_float(row.get("benchmark_return_t1"))
            row["_bench_t30"]     = _safe_float(row.get("benchmark_return_t30"))
            row["_bench_t90"]     = _safe_float(row.get("benchmark_return_t90"))
            row["_bench_t180"]    = _safe_float(row.get("benchmark_return_t180"))
            row["_bench_t365"]    = _safe_float(row.get("benchmark_return_t365"))
            row["_value_gbp"] = _safe_float(row.get("value_gbp"))
            row["_fired_at"] = row.get("fired_at") or ""
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# Per-signal aggregation
# ---------------------------------------------------------------------------

def _matured(rows: list[dict], horizon: str) -> list[float]:
    """Return CAR values for matured firings (the horizon was reachable)."""
    key = f"_car_{horizon}"
    return [r[key] for r in rows if r.get(key) is not None]


def _matured_abs(rows: list[dict], horizon: str) -> list[float]:
    """B-107: gross stock returns (car + bench) for matured firings.

    abs_return = car + benchmark_return (same formula as _top_bottom_firings).
    Rows where either field is None are excluded.
    """
    k_car = f"_car_{horizon}"
    k_bench = f"_bench_{horizon}"
    return [
        r[k_car] + r[k_bench]
        for r in rows
        if r.get(k_car) is not None and r.get(k_bench) is not None
    ]


def _hit_pct(values: list[float]) -> float | None:
    if not values:
        return None
    return round(100.0 * sum(1 for v in values if v > 0) / len(values), 1)


def _safe_median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values) * 100.0, 1)  # report as %


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.fmean(values) * 100.0, 1)


def _sparkline(rows: list[dict], horizon: str, today: date,
               period_months: int = 12) -> list[float | None]:
    """B-009: 13 monthly buckets covering the trailing `period_months` months.

    Bucket M-N spans calendar month N months back from `today`; bucket
    "Now" is the current partial month. The chart in render_performance
    labels these "M-12 ... M-1, Now". For each bucket we compute the
    **median** raw CAR_h (Rupert decision -- preserve median, do not
    switch to mean) across signals that:
      (a) fired in that month-window, AND
      (b) have a non-null CAR at the requested horizon (i.e. matured).

    Empty buckets return None so Chart.js renders them as a true gap
    (spanGaps:true is set in render_performance.py). The old code
    forward-filled empty buckets from a `last_val = 0.0`, which painted
    a misleading flat 0% line over the unmatured months.

    Returned values are percentages (e.g. 1.5 means +1.5%). The order
    runs oldest-to-newest: [M-12, M-11, ..., M-1, Now] -- matches the
    chart's hard-coded label order.
    """
    key = f"_car_{horizon}"
    n_buckets = period_months + 1

    # Walk back month-by-month from the current month's first day.
    cur_first = today.replace(day=1)
    starts: list[date] = []
    y, m = cur_first.year, cur_first.month
    for _ in range(n_buckets):
        starts.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    # Reverse so the list runs oldest-first (M-12 ... Now).
    starts.reverse()

    out: list[float | None] = []
    for i, start in enumerate(starts):
        if i + 1 < len(starts):
            end = starts[i + 1]  # exclusive: first day of the next month
        else:
            # "Now" bucket -- include today by extending one day past it.
            end = today + timedelta(days=1)
        bucket: list[float] = []
        for r in rows:
            fired = r["_fired_at"][:10]
            if not fired:
                continue
            try:
                fd = date.fromisoformat(fired)
            except ValueError:
                continue
            if start <= fd < end and r.get(key) is not None:
                bucket.append(r[key])
        if bucket:
            out.append(round(statistics.median(bucket) * 100.0, 2))
        else:
            out.append(None)
    return out


def _compute_status(rows: list[dict], today: date,
                    signal_id: str, outlier_flag: bool) -> str:
    """Status per stage-05-design-notes auto-computation rule.

    Uses CAR_t30 to assess. Compare current 4-week window vs prior 4-week
    window. If <5 matured firings in current window -> 'review'.
    F1 stays 'gated' while outlier_flag is True.
    """
    if signal_id == "f1_first_time_buy" and outlier_flag:
        return "gated"
    cur_start = today - timedelta(weeks=4)
    prior_start = today - timedelta(weeks=8)
    cur, prior = [], []
    for r in rows:
        if r.get("_car_t30") is None:
            continue
        fired = r["_fired_at"][:10]
        try:
            fd = date.fromisoformat(fired)
        except ValueError:
            continue
        if cur_start <= fd <= today:
            cur.append(r["_car_t30"])
        elif prior_start <= fd < cur_start:
            prior.append(r["_car_t30"])
    if len(cur) < 5:
        return "review"
    cur_med = statistics.median(cur)
    prior_med = statistics.median(prior) if prior else 0.0
    if cur_med >= 0 and prior_med >= 0:
        return "live"
    if cur_med < 0 and prior_med < 0:
        return "kill?"
    return "review"


def aggregate_signals(rows: list[dict], today: date,
                      lookback_days: int | None = 365) -> dict:
    """Build the horizon_aggregates payload + per-signal stats.

    B-058 (2026-05-22): `lookback_days` parameterises the firing-date
    cutoff. Default 365 preserves the original "trailing 12 months"
    behaviour. Pass `None` for no cutoff (include all firings — surfaces
    the recovered older bundled-filing data from B-023). The signals
    payload now emits both `horizon_aggregates` (365d) and
    `horizon_aggregates_all` (no cutoff) so the scoreboard JS can
    toggle between them client-side.
    """
    out: dict = {}
    by_sig: dict[str, list[dict]] = {s: [] for s in SIGNAL_ORDER}
    cutoff = (today - timedelta(days=lookback_days)
              if lookback_days is not None else None)
    for r in rows:
        sid = r.get("signal_id")
        if sid not in by_sig:
            continue
        fired = r["_fired_at"][:10]
        try:
            fd = date.fromisoformat(fired)
        except ValueError:
            continue
        if cutoff is not None and fd < cutoff:
            continue
        by_sig[sid].append(r)

    # Benchmark median per horizon (FTSE All-Share rolling h-day returns).
    # In the absence of a precomputed base-rate file, derive from the same
    # CSV's benchmark_return_t* columns over the trailing 12 months.
    bench: dict[str, list[float]] = {h: [] for h in HORIZONS}
    for r in rows:
        for h in HORIZONS:
            v = _safe_float(r.get(f"benchmark_return_{h}"))
            if v is not None:
                bench[h].append(v)

    for h in HORIZONS:
        base_rate = (round(100.0 * sum(1 for v in bench[h] if v > 0)
                           / len(bench[h]), 1)
                     if bench[h] else 50.0)
        bench_median_pct = (round(statistics.median(bench[h]) * 100.0, 2)
                            if bench[h] else 0.0)
        per_signal: dict = {}
        for sid in SIGNAL_ORDER:
            sig_rows = by_sig[sid]
            matured = _matured(sig_rows, h)
            n = len(matured)
            outlier = any(abs(v) > OUTLIER_ABS_CAR for v in matured)
            med = _safe_median(matured)
            mean = _safe_mean(matured)
            edge = (round(med - bench_median_pct, 1)
                    if med is not None else None)
            status = _compute_status(sig_rows, today, sid, outlier)
            # B-107: mean gross stock return = car + benchmark_return.
            mean_abs = _safe_mean(_matured_abs(sig_rows, h))
            per_signal[SIGNAL_SHORT[sid]] = {
                "trades":           n,
                "hit_pct":          _hit_pct(matured),
                "median_car":       med,
                "mean_car":         mean,
                "mean_abs_return":  mean_abs,
                "edge":             edge,
                "sparkline":        _sparkline(sig_rows, h, today),
                "status":           status,
                "outlier_flag":     outlier,
            }
        out[h] = {
            "base_rate": base_rate,
            "signals":   per_signal,
        }
    return out


# ---------------------------------------------------------------------------
# Cohort cuts
# ---------------------------------------------------------------------------

# Default lookback ladder for the performance-page-redesign-v1 cohort tiles.
# Each entry is (label, max_age_in_days). None = no lower bound (use all rows).
# Sprint 7 fix #4 — LOOKBACKS now imported from render_helpers as the
# single source of truth. Adding a 6th lookback option is now a one-line
# edit in render_helpers.LOOKBACK_KEYS / LOOKBACK_DAYS / LOOKBACK_DISPLAY.
from dashboard import render_helpers as _h_const  # noqa: E402
LOOKBACKS = _h_const.LOOKBACKS
# B-066 (Sprint 14): reuse the single-source-of-truth tier colour palette
# for the cohort_performance.json `color_hex` field — do not invent colours.
TIER_PALETTE = _h_const.TIER_PALETTE


def _within_lookback(fired_at: str, today: date, days: int | None) -> bool:
    """Return True if `fired_at` (ISO-prefix string) falls within the trailing
    `days` window from `today`. `days=None` means 'no lower bound — always True'.

    Pure helper used by `build_cohort_table`. Robust to malformed / empty
    `fired_at` values: those are excluded by returning False.
    """
    if days is None:
        return True
    if not fired_at:
        return False
    try:
        fd = date.fromisoformat(fired_at[:10])
    except ValueError:
        return False
    return fd >= today - timedelta(days=days)


def build_cohort_table(
    rows: list[dict],
    group_fn,
    label_fn,
    horizons: list[str],
    lookbacks: list[tuple],
    today: date,
    scope_filter_fn=None,
    outlier_threshold: float = OUTLIER_ABS_CAR,
) -> dict:
    """Shared horizon × lookback cohort-table builder for the v1 redesign.

    Produces the inner dict that drives the three cohort tiles on the
    redesigned Performance page (`by_value_bucket`, `by_role`, `by_sector`).
    The function is **pure** — no DB access, no file I/O, no time lookups
    other than the explicit `today` argument. Same inputs always produce
    the same output. See backend plan §4.1 for the algorithm.

    Args:
        rows: Pre-loaded CSV rows (output of `load_backtest_csv`). Each row
            must carry `_fired_at` (ISO date prefix), `_car_<h>` per horizon,
            and whatever fields `group_fn` / `scope_filter_fn` inspect.
        group_fn: Callable `row -> key | None`. Rows that group to `None`
            are dropped from the cohort (this is how the role classifier's
            catch-all bucket is excluded — Rupert decision §5.4).
        label_fn: Callable `key -> display label`. Used for the row's
            `"label"` field. Should be deterministic.
        horizons: List of horizon names (e.g. `["t1", "t30", "t90", "t180", "t365"]`).
            Each becomes a top-level key in the returned dict.
        lookbacks: List of `(label, days_or_None)` tuples. Pass
            `LOOKBACKS` for the default ladder.
        today: Reference date for the lookback window — caller supplies so
            the function stays pure / testable.
        scope_filter_fn: Optional row predicate run BEFORE grouping. Used
            by the bucket tile to keep only T1+T2 firings. Pass `None`
            to accept all rows. Defaults to `None`.
        outlier_threshold: A bucket row gets `"outlier_flag": True` when any
            firing in the bucket has `abs(car) > outlier_threshold`. Rupert
            Q2 (2026-05-18) locks this to 2.0 (i.e. |CAR| > 200%); fractions,
            not percent. Defaults to `OUTLIER_ABS_CAR`.

    Returns:
        ::

            {
              "t1":   { "90d": {"rows": [...], "total_n": N}, "6m": {...},
                        "1y": {...}, "all": {...} },
              "t30":  {...}, "t90": {...}, "t180": {...}, "t365": {...},
            }

        Each `rows` entry: `{"key", "label", "n", "hit_pct", "median_car"}`
        plus `"outlier_flag": True` when the bucket trips the threshold.
        Rows are sorted by key alphabetically for byte-stable idempotency.
    """
    if scope_filter_fn is not None:
        in_scope = [r for r in rows if scope_filter_fn(r)]
    else:
        in_scope = list(rows)

    out: dict = {}
    for h in horizons:
        car_key = f"_car_{h}"
        per_lookback: dict = {}
        for lb_label, lb_days in lookbacks:
            # Filter to rows that (a) matured at this horizon and (b) fall
            # within the lookback window. Store (car, value_gbp) pairs for VW.
            windowed: dict = {}   # key -> list of (car, value_gbp)
            for r in in_scope:
                car = r.get(car_key)
                if car is None:
                    continue
                if not _within_lookback(r.get("_fired_at", ""),
                                        today, lb_days):
                    continue
                key = group_fn(r)
                if key is None:
                    continue
                vgbp = r.get("_value_gbp")   # may be None
                windowed.setdefault(key, []).append((car, vgbp))
            # Build the bucket rows.
            rows_out: list = []
            for key in sorted(windowed.keys()):
                pairs = windowed[key]
                vals = [p[0] for p in pairs]
                n = len(vals)
                hit_pct = round(100.0 * sum(1 for v in vals if v > 0) / n, 1)
                median_car = round(statistics.median(vals) * 100.0, 1)
                # B-078: value-weighted mean (toggle, does not replace equal-wt)
                # Weight = transaction value; fall back to equal weights when
                # values are all None or zero.
                weights = [p[1] for p in pairs if p[1] and p[1] > 0]
                if len(weights) == len(pairs) and sum(weights) > 0:
                    total_w = sum(weights)
                    vw_mean_car = round(
                        sum(c * w for (c, _), w in zip(pairs, weights)) / total_w * 100.0,
                        1,
                    )
                else:
                    vw_mean_car = None   # insufficient value data -> omit
                row: dict = {
                    "key":         key,
                    "label":       label_fn(key),
                    "n":           n,
                    "hit_pct":     hit_pct,
                    "median_car":  median_car,
                    "vw_mean_car": vw_mean_car,   # B-078 additive field
                }
                if any(abs(v) > outlier_threshold for v in vals):
                    row["outlier_flag"] = True
                rows_out.append(row)
            per_lookback[lb_label] = {
                "rows":    rows_out,
                "total_n": sum(len(v) for v in windowed.values()),
            }
        out[h] = per_lookback
    return out


# ---------------------------------------------------------------------------
# Drill-down payloads (Performance page redesign v1, Sprint 3+)
#
# Shared helpers + `build_drill_payload` orchestrator + `build_bucket_payload`
# thin wrapper. Pure functions — no DB / file I/O. Sprint 4 will plug in
# `build_role_payload` and `build_sector_payload` using the same helpers.
# Spec: docs/specs/performance-page-redesign-v1.md §5.2, §5.3
# ---------------------------------------------------------------------------

SCHEMA_VERSION_PERF_V1 = "1.0"

# Display labels for the four value buckets, per spec §3.1.
VALUE_BUCKET_LABELS = {
    "1k-25k":    "£1–25k",
    "25k-100k":  "£25–100k",
    "100k-500k": "£100–500k",
    "500k+":     "£500k+",
}

# Role tile labels + per-role scope_note.
# B-025 Phase B refinement (2026-05-20): 6 per-tier rows instead of 3
# combined buckets. T4 (catch-all) and T6 (Company Sec) excluded — too
# noisy / too small for tile display.
ROLE_LABELS = {
    "t1a": "CEO + Founder",
    "t1b": "CFO",
    "t7":  "Chair",
    "t2":  "Other exec",
    "t3":  "NED",
    "t5":  "PCA",
    # Legacy keys retained so any stale JSON still renders sensibly
    # during the cut-over window.
    "ceo_cfo":    "CEO / CFO",
    "other_exec": "Other exec",
    "ned":        "NED",
}
ROLE_SCOPE_NOTES = {
    "t1a": "Chief Executive Officers and Founders (incl. President + Founder)",
    "t1b": "Chief Financial Officers (incl. Finance Director)",
    "t7":  "Chair — both executive and non-executive",
    "t2":  "Other senior exec: Chief X Officer, Exec Director, MD, Divisional Exec, President/VP",
    "t3":  "Non-Executive Directors (incl. SID + Supervisory Board)",
    "t5":  "Persons Closely Associated (spouse / family trust / connected party)",
    "ceo_cfo":    "Chief Executive Officers and Chief Financial Officers",
    "other_exec": "Chair, group executive, COO, CTO, divisional director",
    "ned":        "Non-executive directors",
}

# FTSE All-Share fallback benchmark — spec §3.3 sector-specific benchmark
# falls back to this when the sector has no `benchmark_symbol` mapping.
FTSE_ALL_SHARE = "^FTAS"


def _bucket_for_value(value_gbp):
    """Return bucket key (`"1k-25k"`, `"25k-100k"`, `"100k-500k"`, `"500k+"`)
    for a GBP transaction amount, or None if below the £1k floor or invalid.
    """
    if value_gbp is None or value_gbp < 1_000.0:
        return None
    for label, lo, hi in VALUE_BUCKETS:
        if value_gbp >= lo and (hi is None or value_gbp < hi):
            return label
    return None


def _signal_tier_lookup(rows):
    """Build `{fingerprint -> highest-precedence short signal tier}` from
    the full row set. Rupert Q5 (locked 2026-05-18): SIGNAL_ORDER precedence
    — t0 wins over t1 over t2 ... over f1. A firing may appear under
    multiple signals; this lookup picks the highest-conviction tier for
    the firing-row badge per §5.3.
    """
    idx = {sid: i for i, sid in enumerate(SIGNAL_ORDER)}
    by_fp: dict = {}
    for r in rows:
        fp = r.get("fingerprint")
        sig_id = r.get("signal_id")
        if not fp or not sig_id:
            continue
        cur = by_fp.get(fp)
        if cur is None or idx.get(sig_id, 999) < idx.get(cur, 999):
            by_fp[fp] = sig_id
    return {fp: SIGNAL_SHORT.get(sid, sid) for fp, sid in by_fp.items()}


def _firing_row(row, horizon, signal_tier_lookup=None,
                tx_lookup=None, sector_map=None):
    """Build a §5.3 firing-row dict from one CSV row at one horizon.

    Per spec §5.3 v1.2, `bench_car` and per-firing `outlier_flag` are
    DROPPED. The cohort-level `outlier_flag` lives on the drill_block,
    not on individual firings.

    `tx_lookup: dict[fingerprint -> {director, company, ...}]` is built
    once per exporter run (Sprint 4/5 work) — when absent, director and
    company fall back to empty strings. `sector_map` is consulted only
    when `tx_lookup` doesn't carry a company.
    """
    fp = row.get("fingerprint", "") or ""
    ticker = row.get("ticker", "") or ""
    tx = (tx_lookup or {}).get(fp, {}) if tx_lookup else {}
    sm = (sector_map or {}).get(ticker, {}) if sector_map else {}
    tier = (signal_tier_lookup or {}).get(fp) or SIGNAL_SHORT.get(
        row.get("signal_id", ""), ""
    )
    car = row.get(f"_car_{horizon}")
    car_pct = round(car * 100.0, 1) if car is not None else None
    bench = row.get(f"_bench_{horizon}")
    # abs_return = gross stock return at this horizon (car + benchmark_return).
    # Formula: car = stock_return - benchmark_return => stock_return = car + bench.
    abs_ret_pct = (
        round((car + bench) * 100.0, 1)
        if (car is not None and bench is not None) else None
    )
    value_gbp = row.get("_value_gbp")
    value_int = int(round(value_gbp)) if value_gbp is not None else None
    bench_ret_pct = (
        round(bench * 100.0, 1) if bench is not None else None
    )
    return {
        "date":        (row.get("_fired_at") or "")[:10],
        "ticker":      ticker,
        "company":     (tx.get("company") or sm.get("company") or ""),
        "director":    tx.get("director") or "",
        "role":        row.get("role", "") or tx.get("role", "") or "",
        # B-025 Phase A: include canonical bucket alongside raw role.
        "role_normalized": (
            row.get("role_normalized") or tx.get("role_normalized") or None
        ),
        "role_class":  row.get("role_class", "") or "",
        "signal_tier": tier,
        "value_gbp":   value_int,
        "car":         car_pct,
        "abs_return":  abs_ret_pct,
        "bench_return": bench_ret_pct,  # B113: benchmark at same horizon
    }


def _top_bottom(firings, k=10):
    """Return `(top_k_desc, bottom_k_asc)` from a list of firing-row dicts.

    Top panel sorts by `car` desc, bottom panel by `car` asc. Firings with
    `car=None` are excluded. When fewer than k firings have a valid CAR,
    both lists shrink — front-end shows the "<10 losers" / "<10 winners"
    note per spec §2.3.
    """
    valid = [f for f in firings if f.get("car") is not None]
    sorted_desc = sorted(valid, key=lambda f: f["car"], reverse=True)
    top = sorted_desc[:k]
    bottom_slice = sorted_desc[-k:] if len(sorted_desc) > k else sorted_desc
    bottom = sorted(bottom_slice, key=lambda f: f["car"])
    return top, bottom


def _ticker_rollup(firings):
    """Per-ticker aggregation for the §5.2 `rollup` field.

    Sorted: tickers with N≥3 first (by `hit_pct` desc, ticker asc tiebreak),
    then tickers with N<3 (same sort within group). Front-end inserts the
    "n<3" divider when `n` drops below 3.
    """
    by_ticker: dict = {}
    for f in firings:
        t = f.get("ticker") or ""
        if not t:
            continue
        by_ticker.setdefault(t, []).append(f)
    rows: list = []
    for t, fs in by_ticker.items():
        cars = [f["car"] for f in fs if f.get("car") is not None]
        if not cars:
            continue
        n = len(cars)
        hit_pct = round(100.0 * sum(1 for c in cars if c > 0) / n, 1)
        mean_car = round(statistics.fmean(cars), 1)
        latest = max((f.get("date") or "") for f in fs)
        rows.append({
            "ticker":      t,
            "company":     fs[0].get("company") or "",
            "n":           n,
            "hit_pct":     hit_pct,
            "mean_car":    mean_car,
            "latest_fire": latest,
        })
    n3 = [r for r in rows if r["n"] >= 3]
    n_below = [r for r in rows if r["n"] < 3]
    n3.sort(key=lambda r: (-r["hit_pct"], r["ticker"]))
    n_below.sort(key=lambda r: (-r["hit_pct"], r["ticker"]))
    return n3 + n_below


def _drill_block(cohort_rows, horizon, today, lookback_days,
                 signal_tier_lookup=None, tx_lookup=None,
                 sector_map=None, outlier_threshold=OUTLIER_ABS_CAR):
    """Build one §5.2 drill_block for a (cohort, horizon, lookback) cell.

    Filters `cohort_rows` to those matured at this horizon and within the
    lookback window, then computes the 9 (or 10 with outlier_flag) fields.
    """
    car_key = f"_car_{horizon}"
    bench_key = f"benchmark_return_{horizon}"
    in_scope: list = []
    bench_vals: list = []
    for r in cohort_rows:
        car = r.get(car_key)
        if car is None:
            continue
        if not _within_lookback(r.get("_fired_at", ""), today, lookback_days):
            continue
        in_scope.append(r)
        bv = r.get(bench_key)
        if bv not in (None, "", "None"):
            try:
                bench_vals.append(float(bv))
            except (TypeError, ValueError):
                pass

    if not in_scope:
        return {
            "benchmark_car_pct": None,
            "total_firings":     0,
            "distinct_tickers":  0,
            "tickers_with_n3":   0,
            "hit_pct":           None,
            "median_car":        None,
            "top_firings":       [],
            "bottom_firings":    [],
            "rollup":            [],
        }

    firings = [
        _firing_row(r, horizon, signal_tier_lookup, tx_lookup, sector_map)
        for r in in_scope
    ]
    top, bottom = _top_bottom(firings)
    rollup = _ticker_rollup(firings)

    cars = [r[car_key] for r in in_scope]
    n = len(cars)
    hit_pct = round(100.0 * sum(1 for c in cars if c > 0) / n, 1)
    median_car = round(statistics.median(cars) * 100.0, 1)
    bench_car_pct = (
        round(statistics.median(bench_vals) * 100.0, 2)
        if bench_vals else None
    )
    distinct_tickers = len({r.get("ticker") for r in in_scope})
    tickers_with_n3 = sum(1 for r in rollup if r["n"] >= 3)

    block = {
        "benchmark_car_pct": bench_car_pct,
        "total_firings":     n,
        "distinct_tickers":  distinct_tickers,
        "tickers_with_n3":   tickers_with_n3,
        "hit_pct":           hit_pct,
        "median_car":        median_car,
        "top_firings":       top,
        "bottom_firings":    bottom,
        "rollup":            rollup,
    }
    # Rupert Q2 (2026-05-18): cohort-level outlier_flag = any firing in
    # this drill block has |car| > outlier_threshold (default 2.0 = 200%).
    if any(abs(c) > outlier_threshold for c in cars):
        block["outlier_flag"] = True
    return block


def build_drill_payload(
    rows,
    key_fn,
    label_fn,
    today,
    horizons,
    lookbacks,
    scope_filter_fn=None,
    scope_note=None,
    sector_map=None,
    sector_benchmark_lookup=None,
    tx_lookup=None,
    signal_tier_lookup=None,
    outlier_threshold=OUTLIER_ABS_CAR,
):
    """Shared drill-down payload builder for bucket / role / sector pages.

    Returns the inner cohort-keyed dict — caller wraps with the outer
    `{generated_at, schema_version, <bucket-or-role-or-sector>: ...}` shell
    via a thin wrapper such as `build_bucket_payload` / `build_role_payload`
    / `build_sector_payload`.

    Pure function — no DB / file I/O. All lookups passed in pre-built:
      * `sector_map`: TICKER-keyed meta dict (`{ticker: {sector, company, ...}}`),
        consulted by `_firing_row` for company name fallback.
      * `sector_benchmark_lookup`: SECTOR-keyed map (`{sector_name: benchmark_symbol}`)
        attached to each sector cohort entry; allows the front-end to disclose
        when the FTSE A-S fallback is in use. Bucket / role callers leave it None.
      * `tx_lookup`: FINGERPRINT-keyed dict (`{fp: {director, company, role}}`),
        primary source of per-firing director + company (Rupert Q6).
      * `signal_tier_lookup`: FINGERPRINT-keyed dict (`{fp: short tier}`),
        computed from the full row pool if not supplied (Rupert Q5 precedence).
    """
    if signal_tier_lookup is None:
        signal_tier_lookup = _signal_tier_lookup(rows)

    if scope_filter_fn is not None:
        scoped_rows = [r for r in rows if scope_filter_fn(r)]
    else:
        scoped_rows = list(rows)

    by_key: dict = {}
    for r in scoped_rows:
        k = key_fn(r)
        if k is None:
            continue
        by_key.setdefault(k, []).append(r)

    out: dict = {}
    for key in sorted(by_key.keys()):
        cohort_rows = by_key[key]
        cohort_entry: dict = {"label": label_fn(key)}
        if scope_note:
            cohort_entry["scope_note"] = scope_note
        # Sector payloads pass a sector_benchmark_lookup keyed by sector NAME
        # for the per-cohort benchmark_symbol. Bucket / role callers leave
        # it None and no benchmark_symbol field is emitted.
        if sector_benchmark_lookup is not None and key in sector_benchmark_lookup:
            cohort_entry["benchmark_symbol"] = (
                sector_benchmark_lookup[key] or "^FTAS"
            )
        for h in horizons:
            per_lookback: dict = {}
            for lb_label, lb_days in lookbacks:
                per_lookback[lb_label] = _drill_block(
                    cohort_rows, h, today, lb_days,
                    signal_tier_lookup=signal_tier_lookup,
                    tx_lookup=tx_lookup,
                    sector_map=sector_map,
                    outlier_threshold=outlier_threshold,
                )
            cohort_entry[h] = per_lookback
        out[key] = cohort_entry
    return out


def build_bucket_payload(rows, today, tx_lookup=None,
                         emit_timestamp=True,
                         outlier_threshold=OUTLIER_ABS_CAR):
    """§5.2 bucket payload — wraps `build_drill_payload` with bucket-specific
    scope filter (T1+T2 buys only) and label mapping.

    Pure function — `tx_lookup` is the only optional dependency on
    `transactions` data (built externally; None gives empty director/company).
    """
    def _bucket_scope_filter(r):
        # B-025 Phase B: high-conviction non-NED cohort (was t1+t2 combined)
        return r.get("signal_id") in HIGH_CONVICTION_NON_NED_SIGNALS

    def _bucket_key_fn(r):
        return _bucket_for_value(r.get("_value_gbp"))

    def _bucket_label_fn(k):
        return VALUE_BUCKET_LABELS.get(k, k)

    buckets = build_drill_payload(
        rows=rows,
        key_fn=_bucket_key_fn,
        label_fn=_bucket_label_fn,
        today=today,
        horizons=HORIZONS,
        lookbacks=LOOKBACKS,
        scope_filter_fn=_bucket_scope_filter,
        scope_note="T1 + T2 buys only",
        tx_lookup=tx_lookup,
        outlier_threshold=outlier_threshold,
    )
    payload: dict = {
        "schema_version": SCHEMA_VERSION_PERF_V1,
        "buckets":        buckets,
    }
    if emit_timestamp:
        payload = {"generated_at": _now_utc_iso(), **payload}
    return payload


# ---------------------------------------------------------------------------
# Sprint 4 — Role + Sector payload builders + sector benchmark resolver
# ---------------------------------------------------------------------------

def resolve_sector_benchmark(sector, tickers_meta):
    """Return the sector's `benchmark_symbol` or `^FTAS` fallback.

    `tickers_meta` is the canonical ticker-keyed dict
    (`{ticker: {sector, benchmark_symbol, ...}}`) from the `tickers_meta`
    table. When multiple tickers in the same sector report different
    benchmarks (a data-quality oddity that shouldn't happen in practice),
    the first non-empty benchmark encountered wins.

    Per spec §3.3 and backend plan risk R3: emitting the resolved symbol
    on the cohort entry lets the front-end disclose when the FTSE A-S
    fallback is in use instead of silently swapping benchmarks.
    """
    if not sector:
        return FTSE_ALL_SHARE
    for entry in tickers_meta.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("sector") == sector:
            bs = entry.get("benchmark_symbol")
            if bs:
                return bs
    return FTSE_ALL_SHARE


def _build_sector_benchmark_lookup(tickers_meta):
    """Build `{sector_name -> benchmark_symbol}` from the ticker-keyed
    tickers_meta dict in a single pass. Used inside `build_sector_payload`
    so we don't call `resolve_sector_benchmark` N×M times.
    """
    out: dict = {}
    for entry in tickers_meta.values():
        if not isinstance(entry, dict):
            continue
        sec = entry.get("sector")
        if not sec or sec in out:
            continue
        out[sec] = entry.get("benchmark_symbol") or FTSE_ALL_SHARE
    return out


def build_role_payload(rows, today, tx_lookup=None, sector_map=None,
                       emit_timestamp=True,
                       outlier_threshold=OUTLIER_ABS_CAR):
    """§5.2 role payload — wraps `build_drill_payload` with the role classifier
    as the cohort key function.

    B-025 Phase B (2026-05-20): keys are now 6 per-tier strings (t1a,
    t1b, t7, t2, t3, t5) instead of the legacy 3 (ceo_cfo, other_exec,
    ned). T4 catch-all and T6 Company Sec are deliberately excluded by
    `_BUCKET_TO_PERF_TILE` returning None. Per-role scope_note is
    attached after building (since `build_drill_payload` takes one
    common scope_note and the role tile needs distinct ones).

    Pure function — Sprint 5 supplies `tx_lookup` / `sector_map` from
    a single DB pass.
    """
    def _role_scope_filter(r):
        return classify_role(r.get("role_class"), r.get("role")) is not None

    def _role_key_fn(r):
        return classify_role(r.get("role_class"), r.get("role"))

    def _role_label_fn(k):
        return ROLE_LABELS.get(k, k)

    roles = build_drill_payload(
        rows=rows,
        key_fn=_role_key_fn,
        label_fn=_role_label_fn,
        today=today,
        horizons=HORIZONS,
        lookbacks=LOOKBACKS,
        scope_filter_fn=_role_scope_filter,
        scope_note=None,                  # set per-role below
        sector_map=sector_map,
        tx_lookup=tx_lookup,
        outlier_threshold=outlier_threshold,
    )
    # Attach the per-role scope_note (spec §3.2 sub-line wording).
    for key, note in ROLE_SCOPE_NOTES.items():
        if key in roles:
            roles[key]["scope_note"] = note

    payload: dict = {
        "schema_version": SCHEMA_VERSION_PERF_V1,
        "roles":          roles,
    }
    if emit_timestamp:
        payload = {"generated_at": _now_utc_iso(), **payload}
    return payload


def build_sector_payload(rows, today, tickers_meta, tx_lookup=None,
                          emit_timestamp=True,
                          outlier_threshold=OUTLIER_ABS_CAR):
    """§5.2 sector payload — groups firings by sector via the ticker→sector
    map derived from `tickers_meta`. Attaches `benchmark_symbol` per cohort
    (sector-specific where available, FTSE A-S fallback otherwise — spec §3.3).

    Per backend plan §10.7 and spec §1.3: emits **ALL** sectors with at least
    one in-scope firing. The front-end is responsible for slicing to "top 3
    + bottom 2" on the cohort tile. Keeping the JSON agnostic of presentation
    lets a future "all sectors" view consume the same payload.
    """
    # Build the lookup maps once.
    ticker_to_sector = {
        ticker: meta.get("sector")
        for ticker, meta in tickers_meta.items()
        if isinstance(meta, dict) and meta.get("sector")
    }
    sector_benchmark_lookup = _build_sector_benchmark_lookup(tickers_meta)

    def _sector_scope_filter(r):
        return ticker_to_sector.get(r.get("ticker")) is not None

    def _sector_key_fn(r):
        return ticker_to_sector.get(r.get("ticker"))

    def _sector_label_fn(k):
        # Sector names are their own display labels (no separate mapping).
        return k

    sectors = build_drill_payload(
        rows=rows,
        key_fn=_sector_key_fn,
        label_fn=_sector_label_fn,
        today=today,
        horizons=HORIZONS,
        lookbacks=LOOKBACKS,
        scope_filter_fn=_sector_scope_filter,
        scope_note=None,                   # sector page has no sub-line
        sector_map=tickers_meta,           # ticker-keyed; for firing-row company fallback
        sector_benchmark_lookup=sector_benchmark_lookup,
        tx_lookup=tx_lookup,
        outlier_threshold=outlier_threshold,
    )

    payload: dict = {
        "schema_version": SCHEMA_VERSION_PERF_V1,
        "sectors":        sectors,
    }
    if emit_timestamp:
        payload = {"generated_at": _now_utc_iso(), **payload}
    return payload


def cohort_value_buckets(rows: list[dict]) -> dict:
    """Median CAR_t30 across all high-conviction non-NED firings per value bucket.

    B-025 Phase B: cohort expanded from {T1, T2} to
    {T1a, T1b, T7, T2} to reflect the new per-bucket signal split.
    """
    bucket_vals: dict[str, list[float]] = {b[0]: [] for b in VALUE_BUCKETS}
    for r in rows:
        if r.get("signal_id") not in HIGH_CONVICTION_NON_NED_SIGNALS:
            continue
        v = r.get("_value_gbp")
        car = r.get("_car_t30")
        if v is None or car is None:
            continue
        for label, lo, hi in VALUE_BUCKETS:
            if v >= lo and (hi is None or v < hi):
                bucket_vals[label].append(car)
                break
    out: dict = {}
    for label, _lo, _hi in VALUE_BUCKETS:
        vs = bucket_vals[label]
        out[label] = round(statistics.median(vs) * 100.0, 2) if vs else None
    return out


def cohort_by_sector(rows: list[dict], conn, today: date,
                     base_rate: float) -> list[dict]:
    """Hit % at T+30 per sector with >=10 firings in last 90 days. Top 5."""
    cutoff = today - timedelta(days=90)
    sector_map = {r["ticker"]: r["sector"]
                  for r in conn.execute(
                      "SELECT ticker, sector FROM tickers_meta "
                      "WHERE sector IS NOT NULL"
                  ).fetchall()}
    by_sector: dict[str, list[float]] = {}
    for r in rows:
        if r.get("_car_t30") is None:
            continue
        fired = r["_fired_at"][:10]
        try:
            fd = date.fromisoformat(fired)
        except ValueError:
            continue
        if fd < cutoff:
            continue
        sec = sector_map.get(r.get("ticker"))
        if not sec:
            continue
        by_sector.setdefault(sec, []).append(r["_car_t30"])
    out = []
    for sec, vs in by_sector.items():
        if len(vs) < 10:
            continue
        hit = round(100.0 * sum(1 for v in vs if v > 0) / len(vs), 1)
        out.append({"sector": sec, "hit_pct": hit,
                    "base_rate": base_rate, "n": len(vs)})
    out.sort(key=lambda d: d["hit_pct"], reverse=True)
    return out[:5]


# ---------------------------------------------------------------------------
# Active clusters
# ---------------------------------------------------------------------------

def compute_active_clusters(conn, today: date) -> list[dict]:
    """Derive active clusters directly from `transactions`.

    Definition (Rupert 2026-05-14): 2+ distinct directors at same ticker,
    all BUYs, dates within 30 days, most recent buy within 90 days of today.
    s1_active=True if last_buy is within 30 days; False ("brewing") if
    30-90 days back.
    """
    cutoff_90 = today - timedelta(days=90)
    cutoff_30 = today - timedelta(days=30)
    # B-011 / Sprint 10 Phase 1: LEFT JOIN tickers_meta + COALESCE so
    # excluded IT/CEF/VCT/REIT tickers don't appear in the Active
    # Clusters panel. Direct transactions read with no signals join,
    # so this is the only filter point.
    rows = conn.execute(
        "SELECT t.ticker, t.company, t.director, t.date, "
        "  CASE WHEN COALESCE(t.price_audit,'ok') IN ('unresolved','no_market') "
        "       THEN NULL ELSE t.value END AS value, "
        "  t.cluster_id, t.role_normalized, t.role "
        "FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.type = 'BUY' "
        "  AND t.cluster_id IS NOT NULL "
        "  AND t.date >= ? "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "ORDER BY t.cluster_id, t.date",
        ((today - timedelta(days=180)).isoformat(),),
    ).fetchall()
    # B-136: exclude arms-length corporate holders from cluster scoring; KEEP
    # corporate PCAs / family trusts. Actor-level: related on ANY row -> keep all.
    _related = {r["director"] for r in rows
                if is_related_party(r["role_normalized"], r["role"], r["director"])}
    rows = [r for r in rows
            if not (is_corporate_actor(r["director"])
                    and r["director"] not in _related)]
    by_cluster: dict[str, list] = {}
    for r in rows:
        by_cluster.setdefault(r["cluster_id"], []).append(r)
    out: list[dict] = []
    for cid, txs in by_cluster.items():
        directors = {t["director"] for t in txs}
        if len(directors) < 2:
            continue
        first = min(t["date"] for t in txs)
        last  = max(t["date"] for t in txs)
        try:
            last_d  = date.fromisoformat(last[:10])
        except ValueError:
            continue
        if last_d < cutoff_90:
            continue  # stale
        agg_value = sum((t["value"] or 0.0) for t in txs)
        # Conviction score: director_count*3 + value_score*2 + compression*2
        value_score = (3 if agg_value >= 500_000 else
                       2 if agg_value >= 100_000 else
                       1 if agg_value >= 10_000 else 0)
        try:
            first_d2 = date.fromisoformat(first[:10])
            days_spread = (last_d - first_d2).days
        except ValueError:
            days_spread = 99
        compression = (2 if days_spread <= 7 else
                       1 if days_spread <= 14 else 0)
        conviction = len(directors) * 3 + value_score * 2 + compression * 2
        out.append({
            "ticker":               txs[0]["ticker"],
            "company":              txs[0]["company"],
            "director_count":       len(directors),
            "aggregate_value_gbp":  round(agg_value, 2),
            "first_buy_date":       first[:10],
            "last_buy_date":        last[:10],
            "s1_active":            last_d >= cutoff_30,
            "conviction":           conviction,
        })
    out.sort(key=lambda c: (c["conviction"], c["last_buy_date"]), reverse=True)
    return out


def _brewing_count_as_of(conn, as_of: date) -> int:
    """Number of 'brewing' clusters (2+ distinct directors, most recent buy
    30-90 days before `as_of`) as-of a given date.

    Walk-forward correct: only buys with date <= as_of are considered, so the
    historical sparkline can't see future buys (compute_active_clusters has no
    upper date bound and must NOT be reused here). Brewing window matches
    compute_active_clusters: cutoff_90 <= last_buy < cutoff_30, >=2 directors,
    excluded issuers filtered out.
    """
    cutoff_180 = (as_of - timedelta(days=180)).isoformat()
    rows = conn.execute(
        "SELECT t.cluster_id, t.director, t.date, t.role_normalized, t.role "
        "FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.type = 'BUY' AND t.cluster_id IS NOT NULL "
        "  AND t.date >= ? AND t.date <= ? "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1",
        (cutoff_180, as_of.isoformat()),
    ).fetchall()
    # B-136: exclude arms-length corporate holders; keep corporate PCAs/family
    # trusts. Actor-level: related on ANY row -> keep all of that actor's rows.
    _related = {r["director"] for r in rows
                if is_related_party(r["role_normalized"], r["role"], r["director"])}
    rows = [r for r in rows
            if not (is_corporate_actor(r["director"])
                    and r["director"] not in _related)]
    by_cluster: dict[str, list] = {}
    for r in rows:
        by_cluster.setdefault(r["cluster_id"], []).append(r)
    cutoff_90 = as_of - timedelta(days=90)
    cutoff_30 = as_of - timedelta(days=30)
    n = 0
    for _cid, txs in by_cluster.items():
        if len({t["director"] for t in txs}) < 2:
            continue
        try:
            last_d = max(date.fromisoformat(t["date"][:10]) for t in txs)
        except (ValueError, TypeError):
            continue
        if cutoff_90 <= last_d < cutoff_30:
            n += 1
    return n


def build_cluster_brewing_trend(conn, today: date) -> dict:
    """B-132: brewing-cluster trend for the Active-Cluster panel.

    Emits the current brewing count, an 8-point weekly sparkline (oldest ->
    newest, 7 weeks back through today), and the trailing-~30-day average (mean
    of the last 5 weekly snapshots) so the panel can show a ▲/▼-vs-average
    trend. Each weekly point is computed by _brewing_count_as_of (buys
    date <= snapshot), so the series is walk-forward correct (no lookahead).
    """
    weekly_dates = [today - timedelta(days=7 * k) for k in range(7, -1, -1)]
    weekly = [_brewing_count_as_of(conn, d) for d in weekly_dates]
    current = weekly[-1]
    last30 = weekly[-5:]  # ~28 days -> trailing-30d proxy
    avg_30d = round(sum(last30) / len(last30), 1) if last30 else 0.0
    return {"current": current, "avg_30d": avg_30d, "weekly": weekly}


# ---------------------------------------------------------------------------
# Paper trades
# ---------------------------------------------------------------------------

def paper_trade_stats(conn) -> dict:
    """Open / closed counts + open MTM P&L using latest prices."""
    rows = conn.execute(
        "SELECT trade_id, signal_id, fingerprint, notional_gbp, "
        "       entry_close, shares, status, exit_close, "
        "       (SELECT ticker FROM transactions WHERE fingerprint = "
        "        paper_trades.fingerprint) AS ticker "
        "FROM paper_trades"
    ).fetchall()
    open_n = 0
    closed_n = 0
    open_mtm = 0.0
    latest_cache: dict[str, float] = {}
    for r in rows:
        if r["status"] == "closed":
            closed_n += 1
            continue
        if r["status"] != "open":
            continue
        open_n += 1
        tkr = r["ticker"]
        if tkr is None:
            continue
        if tkr not in latest_cache:
            row = conn.execute(
                "SELECT close FROM prices WHERE ticker = ? "
                "ORDER BY date DESC LIMIT 1", (tkr,)
            ).fetchone()
            latest_cache[tkr] = row["close"] if row else None
        latest = latest_cache[tkr]
        entry = r["entry_close"]
        shares = r["shares"]
        if latest is None or entry is None or shares is None:
            continue
        open_mtm += (latest - entry) * shares
    return {
        "paper_pnl_open":      round(open_mtm, 2),
        "paper_trades_open":   open_n,
        "paper_trades_closed": closed_n,
    }


# ---------------------------------------------------------------------------
# B-100 Phase A: Live paper book (read-only, no paper_trades table writes)
# ---------------------------------------------------------------------------

# Signal IDs that represent directional buy signals (exclude suppression).
_PAPER_BOOK_SIGNAL_IDS = {
    "t1a_ceo_founder_buy", "t1b_cfo_buy", "t7_chair_buy",
    "t2_exec_buy", "t3_ned_buy", "t5_pca_buy", "t6_company_sec_buy",
    "t4_other_buy", "s1_cluster_buy", "f1_first_time_buy",
    "b1_lone_conviction_buy",
}

# Position cap per signal (Rupert decision 2026-06-03: CLI arg --max-notional;
# read-only display uses the same cap for consistency).
# B-115 / spec 07: cap dropped 50k -> 5k and sizing is now log-scaled.
_PAPER_BOOK_MAX_NOTIONAL = sizing.CAP_GBP

# Exit horizon: 30 calendar days after fired_at (not trading days).
_PAPER_BOOK_HOLD_DAYS = 30


def build_paper_book_summary(conn, today: date,
                              max_notional: float = _PAPER_BOOK_MAX_NOTIONAL) -> dict:
    """B-100 Phase A -- compute a live paper-book snapshot from signals + prices.

    NO writes. Treats each signal firing as a notional long entry:
      entry_close = first close in `prices` on or after fired_at date for ticker
      current_close = latest close in `prices` for that ticker
      notional_gbp = position_size(transaction.value)  # spec 07 log scale, £5k cap
      mtm_pct = (current_close - entry_close) / entry_close * 100
      hold_days = calendar days from fired_at to today
      status = OPEN (hold_days <= 21) | CLOSED (hold_days > 21)

    Only one row per (signal_id, fingerprint) pair — de-duplicated by taking
    the highest-conviction signal per fingerprint (SIGNAL_ORDER precedence).

    Returns:
        {
          "positions": [...],    # list of position dicts sorted by fired_at desc
          "summary": {
            "open_count": int,
            "closed_count": int,
            "open_notional_gbp": float,
            "open_mtm_pct_mean": float | None,
            "open_winners": int,
            "open_losers": int,
          }
        }
    """
    # Load all buy signals joined to their transactions.
    rows = conn.execute(
        """
        SELECT s.signal_id, s.fingerprint, s.fired_at,
               t.ticker, t.value, t.company, t.director,
               tm.market_cap_gbp
        FROM signals s
        JOIN transactions t ON s.fingerprint = t.fingerprint
        LEFT JOIN tickers_meta tm ON t.ticker = tm.ticker
        WHERE t.type = 'BUY'
          AND s.signal_id IN ({})
        ORDER BY s.fired_at DESC
        """.format(",".join("?" * len(_PAPER_BOOK_SIGNAL_IDS))),
        tuple(_PAPER_BOOK_SIGNAL_IDS),
    ).fetchall()

    # De-duplicate: one entry per fingerprint, highest-conviction signal wins.
    sig_rank = {sid: i for i, sid in enumerate(SIGNAL_ORDER)}
    best: dict[str, dict] = {}   # fingerprint -> row dict
    for r in rows:
        fp = r["fingerprint"]
        rank = sig_rank.get(r["signal_id"], 999)
        if fp not in best or rank < sig_rank.get(best[fp]["signal_id"], 999):
            best[fp] = {
                "signal_id":      r["signal_id"],
                "fingerprint":    fp,
                "fired_at":       r["fired_at"],
                "ticker":         r["ticker"],
                "value":          r["value"],
                "company":        r["company"] or "",
                "director":       r["director"] or "",
                "market_cap_gbp": r["market_cap_gbp"],
            }

    # Price caches to avoid N+1 queries.
    _prices_by_ticker: dict[str, list[tuple]] = {}   # ticker -> [(date, close), ...]

    def _get_price_series(ticker: str) -> list[tuple]:
        if ticker not in _prices_by_ticker:
            pr = conn.execute(
                "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date ASC",
                (ticker,),
            ).fetchall()
            _prices_by_ticker[ticker] = [(r["date"], r["close"]) for r in pr]
        return _prices_by_ticker[ticker]

    def _first_close_on_or_after(ticker: str, iso_day: str):
        """First close on or after iso_day. Returns (date_str, close) or None."""
        series = _get_price_series(ticker)
        if not series:
            return None
        dates = [s[0] for s in series]
        i = bisect.bisect_left(dates, iso_day)
        return series[i] if i < len(series) else None

    def _nth_close_after(ticker: str, iso_day: str, n: int):
        """Close price n trading days after the first close on or after iso_day.
        Returns float or None if not enough price history."""
        series = _get_price_series(ticker)
        if not series:
            return None
        dates = [s[0] for s in series]
        i = bisect.bisect_left(dates, iso_day)
        idx = i + n
        return series[idx][1] if idx < len(series) else None

    def _latest_close(ticker: str):
        """Latest close in prices. Returns (date_str, close) or None."""
        series = _get_price_series(ticker)
        return series[-1] if series else None

    def _parse_fired_at(fired_at: str) -> date | None:
        """Normalise fired_at to a date — handles ISO timestamps and 'DD Mon YYYY'."""
        if not fired_at:
            return None
        s = fired_at.strip()
        # ISO timestamp or date
        head = s[:10]
        try:
            datetime.strptime(head, "%Y-%m-%d")
            return date.fromisoformat(head)
        except ValueError:
            pass
        # 'DD Mon YYYY' format
        _FMTS = ("%d %b %Y", "%d %B %Y")
        for fmt in _FMTS:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    positions: list[dict] = []
    open_count = closed_count = 0
    open_notional = 0.0
    open_mtm_vals: list[float] = []
    open_winners = open_losers = 0

    for fp, rec in best.items():
        ticker = rec["ticker"]
        fired_dt = _parse_fired_at(rec["fired_at"])
        if fired_dt is None or not ticker:
            continue

        fired_iso = fired_dt.isoformat()
        hold_days = (today - fired_dt).days
        status = "OPEN" if hold_days <= _PAPER_BOOK_HOLD_DAYS else "CLOSED"

        # Entry: first trading day on or after fired_at.
        entry = _first_close_on_or_after(ticker, fired_iso)
        if entry is None:
            entry_date = None
            entry_close = None
        else:
            entry_date, entry_close = entry

        # Current price.
        latest = _latest_close(ticker)
        current_close = latest[1] if latest else None
        current_date = latest[0] if latest else None

        # MTM.
        if entry_close and current_close and entry_close > 0:
            mtm_pct = round((current_close - entry_close) / entry_close * 100, 2)
        else:
            mtm_pct = None

        # Notional — B-115 / spec 07 conviction sizing (log scale, £5k cap).
        raw_value = rec["value"] or 0.0
        notional = (sizing.position_size(float(raw_value), cap=max_notional)
                    if raw_value > 0 else None)

        # Accumulators.
        if status == "OPEN":
            open_count += 1
            if notional:
                open_notional += notional
            if mtm_pct is not None:
                open_mtm_vals.append(mtm_pct)
                if mtm_pct > 0:
                    open_winners += 1
                else:
                    open_losers += 1
        else:
            closed_count += 1

        positions.append({
            "signal_id":      SIGNAL_SHORT.get(rec["signal_id"], rec["signal_id"]),
            "fingerprint":    fp,
            "ticker":         ticker,
            "company":        rec["company"],
            "director":       rec["director"],
            "fired_at":       fired_iso,
            "entry_date":     entry_date,
            "entry_close":    _round(entry_close, 4),
            "current_date":   current_date,
            "current_close":  _round(current_close, 4),
            # B-153: horizon prices for the tile toggle (T+1 / T+21 / T+90 td).
            "close_t1":       _round(_nth_close_after(ticker, fired_iso, 1), 4),
            "close_t21":      _round(_nth_close_after(ticker, fired_iso, 21), 4),
            "close_t90":      _round(_nth_close_after(ticker, fired_iso, 90), 4),
            "notional_gbp":   _round(notional, 0),
            "hold_days":      hold_days,
            "mtm_pct":        mtm_pct,
            "status":         status,
            "market_cap_gbp": rec.get("market_cap_gbp"),  # B-146
        })

    # Sort open first, then closed; within each group newest fired_at first.
    positions.sort(key=lambda p: (0 if p["status"] == "OPEN" else 1,
                                  p["fired_at"]), reverse=False)
    positions.sort(key=lambda p: p["fired_at"], reverse=True)
    positions.sort(key=lambda p: p["status"])  # CLOSED last

    summary_stats = {
        "open_count":          open_count,
        "closed_count":        closed_count,
        "open_notional_gbp":   _round(open_notional, 0),
        "open_mtm_pct_mean":   (_round(sum(open_mtm_vals) / len(open_mtm_vals), 2)
                                if open_mtm_vals else None),
        "open_winners":        open_winners,
        "open_losers":         open_losers,
    }

    return {
        "positions": positions,
        "summary":   summary_stats,
    }


def build_capital_deployed(conn, today: date) -> dict:
    """B-152: 13-week weekly snapshots of paper capital deployed.

    Retrocomputes how much notional capital was 'deployed' (in OPEN positions)
    at each of 13 weekly snapshot dates — today going back 12 weeks.

    A position is OPEN on snapshot_date when:
      fired_dt <= snapshot_date  AND
      (snapshot_date - fired_dt).days <= _PAPER_BOOK_HOLD_DAYS (30)

    Splits by tickers_meta.small_cap:
      small_cap = 1  → "small"  (<£500m)
      small_cap = 0  → "large"  (≥£500m)
      small_cap NULL → included in "all" only

    Returns:
        {
            "weeks":       list[str],   # 13 ISO dates oldest→newest
            "all":         list[float], # total deployed £ at each snapshot
            "small":       list[float], # small-cap portion
            "large":       list[float], # large-cap portion
            "ma3m_all":    float,       # 13-week mean of "all"
            "ma3m_small":  float,
            "ma3m_large":  float,
        }
    """
    snapshot_dates = [today - timedelta(days=7 * k) for k in range(12, -1, -1)]

    # Fetch all buy signal firings, de-duplicated by fingerprint
    # (highest-conviction signal wins — mirrors build_paper_book_summary).
    rows = conn.execute(
        """
        SELECT s.signal_id, s.fingerprint, s.fired_at, t.value,
               COALESCE(tm.small_cap, -1) AS small_cap_flag
        FROM signals s
        JOIN transactions t ON s.fingerprint = t.fingerprint
        LEFT JOIN tickers_meta tm ON t.ticker = tm.ticker
        WHERE t.type = 'BUY'
          AND s.signal_id IN ({})
        ORDER BY s.fired_at DESC
        """.format(",".join("?" * len(_PAPER_BOOK_SIGNAL_IDS))),
        tuple(_PAPER_BOOK_SIGNAL_IDS),
    ).fetchall()

    sig_rank = {sid: i for i, sid in enumerate(SIGNAL_ORDER)}
    best: dict[str, dict] = {}
    for r in rows:
        fp = r["fingerprint"]
        rank = sig_rank.get(r["signal_id"], 999)
        if fp not in best or rank < best[fp]["_rank"]:
            best[fp] = {
                "fired_at":  r["fired_at"],
                "value":     r["value"],
                "small_cap": int(r["small_cap_flag"]),  # 1, 0, or -1 (NULL)
                "_rank":     rank,
            }

    # Pre-parse fired dates and compute notionals once.
    entries = []
    for rec in best.values():
        fa = (rec["fired_at"] or "").strip()
        if not fa:
            continue
        try:
            fd = date.fromisoformat(fa[:10])
        except ValueError:
            try:
                import datetime as _dt
                fd = _dt.datetime.strptime(fa[:11], "%d %b %Y").date()
            except Exception:
                continue
        raw_val = rec["value"] or 0.0
        notional = sizing.position_size(float(raw_val)) if raw_val > 0 else 0.0
        entries.append({"fired_dt": fd, "notional": notional, "small_cap": rec["small_cap"]})

    # Compute deployed capital and position counts at each snapshot.
    all_series: list[float] = []
    small_series: list[float] = []
    large_series: list[float] = []
    all_count_series: list[int] = []
    small_count_series: list[int] = []
    large_count_series: list[int] = []
    for snap in snapshot_dates:
        total_all = total_small = total_large = 0.0
        count_all = count_small = count_large = 0
        for e in entries:
            hold = (snap - e["fired_dt"]).days
            if 0 <= hold <= _PAPER_BOOK_HOLD_DAYS:
                total_all   += e["notional"]
                count_all   += 1
                if e["small_cap"] == 1:
                    total_small += e["notional"]
                    count_small += 1
                elif e["small_cap"] == 0:
                    total_large += e["notional"]
                    count_large += 1
        all_series.append(round(total_all,   0))
        small_series.append(round(total_small, 0))
        large_series.append(round(total_large, 0))
        all_count_series.append(count_all)
        small_count_series.append(count_small)
        large_count_series.append(count_large)

    def _mean(lst: list[float]) -> float:
        return round(sum(lst) / len(lst), 0) if lst else 0.0

    def _mean_count(lst: list[int]) -> float:
        return round(sum(lst) / len(lst), 1) if lst else 0.0

    return {
        "weeks":             [d.isoformat() for d in snapshot_dates],
        "all":               all_series,
        "small":             small_series,
        "large":             large_series,
        "ma3m_all":          _mean(all_series),
        "ma3m_small":        _mean(small_series),
        "ma3m_large":        _mean(large_series),
        "all_count":         all_count_series,
        "small_count":       small_count_series,
        "large_count":       large_count_series,
        "ma3m_all_count":    _mean_count(all_count_series),
        "ma3m_small_count":  _mean_count(small_count_series),
        "ma3m_large_count":  _mean_count(large_count_series),
    }


# ---------------------------------------------------------------------------
# Dealings feed
# ---------------------------------------------------------------------------

def _ticker_is_aim(conn, cache: dict, ticker: str) -> bool:
    if ticker not in cache:
        r = conn.execute(
            "SELECT is_aim FROM tickers_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
        cache[ticker] = bool(r["is_aim"]) if r else False
    return cache[ticker]


def _ticker_dates(conn, cache: dict, ticker: str) -> list[str]:
    if ticker not in cache:
        rows = conn.execute(
            "SELECT date FROM prices WHERE ticker = ? ORDER BY date ASC",
            (ticker,),
        ).fetchall()
        cache[ticker] = [r["date"] for r in rows]
    return cache[ticker]


def _ticker_close_on(conn, cache: dict, ticker: str, day: str):
    key = (ticker, day)
    if key not in cache:
        r = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date = ?",
            (ticker, day),
        ).fetchone()
        cache[key] = r["close"] if r else None
    return cache[key]


def _latest_close(conn, cache: dict, ticker: str):
    if ticker not in cache:
        r = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? "
            "ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        cache[ticker] = r["close"] if r else None
    return cache[ticker]


def _next_trading_day(dates: list[str], after_day: str):
    """First date strictly after `after_day` in `dates` (sorted asc)."""
    from bisect import bisect_right
    i = bisect_right(dates, after_day)
    return dates[i] if i < len(dates) else None


# Human date formats the scraper sometimes stores in announced_at when it keeps
# the RNS headline date verbatim (e.g. "02 Jun 2026") instead of an ISO
# timestamp.
_ANNOUNCED_FMTS = ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")


def _to_iso_day(s: str | None) -> str | None:
    """Normalise an announced_at / date value to 'YYYY-MM-DD', else None.

    Accepts ISO dates and timestamps ('2026-06-02', '2026-06-02T16:15:07Z') by
    validating the 10-char prefix, plus the human 'DD Mon YYYY' headline format
    the scraper sometimes stores. Anything unparseable returns None so the
    caller falls back to another field.

    Why this exists: the old code did a blind ``announced_at[:10]`` slice. A
    value like "02 Jun 2026" became the garbage string "02 Jun 20", which sorts
    *before* every ISO date in the trading-day bisect -> entry price defaulted
    to the earliest close on record (~1yr old) -> wildly wrong MTM (UTG showed
    -41% vs a true ~0%). Validating to real ISO closes that hole.
    """
    if not s:
        return None
    s = s.strip()
    head = s[:10]
    try:
        datetime.strptime(head, "%Y-%m-%d")
        return head
    except ValueError:
        pass
    for fmt in _ANNOUNCED_FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _mtm_pct(conn, ticker: str, announced_at: str, is_aim: bool,
             dates_cache: dict, close_cache: dict,
             latest_cache: dict, date_field: str = "") -> float | None:
    """MTM per design notes: T+1 close after announced_at -> latest close,
    net of 50bps spread + 0.5% stamp (non-AIM).

    Falls back to transaction date when announced_at is absent (which is the
    case for all backfilled records until backfill_announced_at.py is run).
    """
    if not ticker:
        return None
    effective = _to_iso_day(announced_at) or _to_iso_day(date_field)
    if not effective:
        return None
    day = effective
    dates = _ticker_dates(conn, dates_cache, ticker)
    if not dates:
        return None
    t1 = _next_trading_day(dates, day)
    if t1 is None:
        return None
    entry = _ticker_close_on(conn, close_cache, ticker, t1)
    latest = _latest_close(conn, latest_cache, ticker)
    if entry is None or latest is None or entry == 0:
        return None
    cost_pct = 0.5 + (0.0 if is_aim else 0.5)
    return round((latest / entry - 1.0) * 100.0 - cost_pct, 2)


def _abs_return_pct(conn, ticker: str, announced_at: str,
                    dates_cache: dict, close_cache: dict,
                    latest_cache: dict, date_field: str = "") -> float | None:
    """B-098: Gross absolute stock return since T+1 close after announced_at.

    Same entry point as _mtm_pct but with NO cost deduction, so callers can
    display the raw stock move independent of spread/stamp assumptions.
    Caches are shared with _mtm_pct so DB hits are not repeated.
    """
    if not ticker:
        return None
    effective = _to_iso_day(announced_at) or _to_iso_day(date_field)
    if not effective:
        return None
    dates = _ticker_dates(conn, dates_cache, ticker)
    if not dates:
        return None
    t1 = _next_trading_day(dates, effective)
    if t1 is None:
        return None
    entry = _ticker_close_on(conn, close_cache, ticker, t1)
    latest = _latest_close(conn, latest_cache, ticker)
    if entry is None or latest is None or entry == 0:
        return None
    return round((latest / entry - 1.0) * 100.0, 2)


def _abs_return_from_announcement_pct(conn, ticker: str, announced_at: str,
                                      dates_cache: dict, close_cache: dict,
                                      latest_cache: dict,
                                      date_field: str = "") -> float | None:
    """B-127: raw stock return measured from the ANNOUNCEMENT-date close.

    Entry = first close ON OR AFTER announced_at (deliberately NOT the T+1
    close that _abs_return_pct uses — Rupert's decision is to measure from the
    announcement date itself). Return = (latest_close / entry - 1) * 100, raw:
    no benchmark removed, no trading costs. Shares the price caches with the
    other helpers so no extra DB round-trips.
    """
    if not ticker:
        return None
    effective = _to_iso_day(announced_at) or _to_iso_day(date_field)
    if not effective:
        return None
    dates = _ticker_dates(conn, dates_cache, ticker)
    if not dates:
        return None
    from bisect import bisect_left
    i = bisect_left(dates, effective)
    entry_day = dates[i] if i < len(dates) else None
    if entry_day is None:
        return None
    entry = _ticker_close_on(conn, close_cache, ticker, entry_day)
    latest = _latest_close(conn, latest_cache, ticker)
    if entry is None or latest is None or entry == 0:
        return None
    return round((latest / entry - 1.0) * 100.0, 2)


def _bench_return_pct(conn, ticker: str, announced_at: str,
                      dates_cache: dict, close_cache: dict,
                      latest_cache: dict, tickers_meta: dict,
                      date_field: str = "") -> float | None:
    """B113: Sector benchmark return over same period as _abs_return_pct.

    Finds the sector benchmark ticker for `ticker` (falling back to ^FTAS),
    then returns (bench_latest / bench_t1 - 1) * 100 where t1 is the same
    T+1 trading day used for the stock return.  Shares the existing price
    caches so no extra DB round-trips for benchmarks already fetched.
    """
    if not ticker:
        return None
    effective = _to_iso_day(announced_at) or _to_iso_day(date_field)
    if not effective:
        return None
    dates = _ticker_dates(conn, dates_cache, ticker)
    if not dates:
        return None
    t1 = _next_trading_day(dates, effective)
    if t1 is None:
        return None
    # Resolve sector benchmark ticker.
    meta = tickers_meta.get(ticker) if tickers_meta else None
    bench_sym = (
        (meta.get("benchmark_symbol") if isinstance(meta, dict) else None)
        or FTSE_ALL_SHARE
    )
    bench_entry = _ticker_close_on(conn, close_cache, bench_sym, t1)
    bench_latest = _latest_close(conn, latest_cache, bench_sym)
    if bench_entry is None or bench_latest is None or bench_entry == 0:
        return None
    return round((bench_latest / bench_entry - 1.0) * 100.0, 2)


def _signals_for(conn, fingerprint: str) -> list[str]:
    rows = conn.execute(
        "SELECT signal_id FROM signals WHERE fingerprint = ?", (fingerprint,)
    ).fetchall()
    return sorted({SIGNAL_SHORT.get(r["signal_id"], r["signal_id"])
                   for r in rows})


_EVENT_LABELS = {
    "INTERIM":       "Interim Results",
    "FINAL":         "Full Year Results",
    "PRELIM":        "Full Year Results",
    "TRADING_UPDATE": "Trading Update",
    "TRADING_STMT":  "Trading Update",
    "EARNINGS":      "Earnings",
    "QUARTERLY":     "Quarterly Results",
    # B-145: spec asked for the LSE Diary event_type values too; these are
    # the actual report_type values from migration 009. Map lowercase aliases
    # in case any older row was stored in lowercase.
    "interim":       "Interim Results",
    "full_year":     "Full Year Results",
    "agm":           "AGM",
}


def _build_upcoming_events(conn, today: date) -> list[dict]:
    """B-145: Upcoming events (reporting_dates) for the next 30 days.

    Queries reporting_dates joined to transactions (for company name) for events
    from today through today+30. Degrades gracefully when the table is absent
    (pre-migration DB) -- returns [].

    Window widened 14->30 days (2026-06-18) so the panel stays populated through
    quieter stretches of the UK results calendar.

    Column names in reporting_dates: ticker, report_date, report_type, source,
    confidence (migration 009). Company name is sourced from the most-recent
    transaction per ticker (same pattern as _load_tickers_meta).
    """
    cutoff = (today + timedelta(days=30)).isoformat()
    today_iso = today.isoformat()
    try:
        rows = conn.execute(
            """
            SELECT rd.report_date, rd.ticker,
                   COALESCE(
                       (SELECT t.company FROM transactions t
                        WHERE t.ticker = rd.ticker
                        ORDER BY t.date DESC LIMIT 1),
                       rd.ticker
                   ) AS company,
                   rd.report_type,
                   tm.market_cap_gbp
              FROM reporting_dates rd
              LEFT JOIN tickers_meta tm ON rd.ticker = tm.ticker
             WHERE rd.report_date BETWEEN ? AND ?
             ORDER BY rd.report_date ASC
            """,
            (today_iso, cutoff),
        ).fetchall()
    except Exception:
        return []  # table absent on a pre-migration DB -- degrade gracefully
    out: list[dict] = []
    for r in rows:
        r_dict = dict(r)
        raw_type = r_dict.get("report_type") or ""
        label = _EVENT_LABELS.get(raw_type,
                                  _EVENT_LABELS.get(raw_type.lower(),
                                                    raw_type.replace("_", " ").title()))
        out.append({
            "date":           r_dict["report_date"],
            "ticker":         r_dict["ticker"],
            "company":        r_dict["company"],
            "event_type":     label,
            "market_cap_gbp": r_dict.get("market_cap_gbp"),
        })
    return out


def _holding_pct_increase_tx(tx) -> float | None:
    """B-156 derived metric for a transactions row (sqlite3.Row or dict).

    shares / (resulting_shares - shares); None when resulting_shares is
    NULL, the row is not a BUY, or the prior holding is <= 0 (first-time
    holding). Mirrors backtest._holding_pct_increase.
    """
    rs = tx["resulting_shares"] if "resulting_shares" in tx.keys() else None
    if rs is None or tx["type"] != "BUY":
        return None
    try:
        shares = int(tx["shares"] or 0)
        prior = int(rs) - shares
    except (TypeError, ValueError):
        return None
    if not shares or prior <= 0:
        return None
    return shares / prior


def build_dealings(conn, today: date, tickers_meta: dict | None = None) -> dict:
    """Build the dealings.json payload."""
    today_iso = today.isoformat()
    week_start = (today - timedelta(days=7)).isoformat()

    dates_cache: dict = {}
    close_cache: dict = {}
    latest_cache: dict = {}
    aim_cache: dict = {}

    # B-111: per-ticker upcoming reporting dates (+confidence) for the 60-day
    # pre-results badge on the today/this-week rows. Mirrors the company-page
    # badge (build_dashboard) but carries confidence so a synthetic date (the
    # est-filler, B-118) renders with an "(est)" suffix. Degrades gracefully on
    # a pre-migration DB (no reporting_dates table / confidence column).
    reporting_by_ticker: dict = {}
    try:
        for r in conn.execute(
            "SELECT ticker, report_date, confidence FROM reporting_dates "
            "WHERE report_date >= ? ORDER BY report_date ASC",
            (today_iso,),
        ).fetchall():
            reporting_by_ticker.setdefault(r["ticker"], []).append(
                (r["report_date"], (r["confidence"] or "confirmed")))
    except Exception:
        pass  # table/column absent on a pre-migration DB — degrade gracefully.

    def _near_reporting(ticker, txn_date):
        """(report_date, is_est) for the nearest results date 0-60 days after
        the transaction, or (None, False)."""
        tdate = (txn_date or "")[:10]
        rds = reporting_by_ticker.get(ticker)
        if not (tdate and rds):
            return None, False
        try:
            td = date.fromisoformat(tdate)
        except (ValueError, TypeError):
            return None, False
        for rd_str, conf in rds:
            try:
                days = (date.fromisoformat(rd_str) - td).days
            except (ValueError, TypeError):
                continue
            if 0 <= days <= 60:
                return rd_str, (conf == "est")
        return None, False

    def _row(tx):
        ticker = tx["ticker"]
        is_aim = _ticker_is_aim(conn, aim_cache, ticker)
        # Use announced_at when present; fall back to transaction date so MTM
        # and time_utc are populated even when announced_at is blank.
        date_field = tx["date"] or ""
        mtm = _mtm_pct(conn, ticker, tx["announced_at"], is_aim,
                       dates_cache, close_cache, latest_cache,
                       date_field=date_field)
        # B-098: gross absolute stock return (no cost deduction). Shares the
        # same price cache as _mtm_pct so no extra DB round-trips.
        abs_ret = _abs_return_pct(conn, ticker, tx["announced_at"],
                                  dates_cache, close_cache, latest_cache,
                                  date_field=date_field)
        # B113: sector benchmark return over the same T+1→today period.
        bench_ret = _bench_return_pct(conn, ticker, tx["announced_at"],
                                      dates_cache, close_cache, latest_cache,
                                      tickers_meta or {},
                                      date_field=date_field)
        sigs = _signals_for(conn, tx["fingerprint"])
        _nr = _near_reporting(ticker, tx["date"])  # B-111
        return {
            "time_utc":        tx["announced_at"] or tx["date"] or "",
            "ticker":          ticker,
            "company":         tx["company"],
            "director":        tx["director"],
            "role":            tx["role"] or "",
            # B-025 Phase A: canonical bucket for role-conditional UI/signals.
            "role_normalized": (
                tx["role_normalized"] if "role_normalized" in tx.keys()
                else None
            ),
            "txn_type":        tx["type"],
            "value_gbp":       round(tx["value"] or 0.0, 2),
            "signals_fired":   sigs,
            "mtm_pct":         mtm,
            "abs_return_pct":  abs_ret,    # B-098: gross, no costs
            "bench_return_pct": bench_ret, # B113: benchmark over same period
            "near_reporting_date": _nr[0], # B-111: nearest results date 0-60d out
            "near_reporting_est":  _nr[1], # B-111: True when confidence='est'
            # B-120: flag rows whose price couldn't be verified against market
            # data (B-060 audit -> 'unresolved'/'no_market') so the value can be
            # marked "unverified" in the dealings table. Degrades on a
            # pre-migration DB (no price_audit column).
            "unverified": (
                (tx["price_audit"] if "price_audit" in tx.keys() else None)
                in ("unresolved", "no_market")
            ),
            # B-146: market cap for display in dealings table (sourced from
            # tickers_meta loaded before the row loop — NULL when not enriched).
            "market_cap_gbp": (tickers_meta or {}).get(ticker, {}).get("market_cap_gbp"),
            # B-156: stated post-transaction holding (NULL when the filing
            # doesn't state it — most MAR-template filings) + derived % stake
            # increase. SELECT t.* flows the column through; the .keys()
            # check degrades gracefully on a pre-migration DB.
            "resulting_shares": (
                tx["resulting_shares"]
                if "resulting_shares" in tx.keys() else None
            ),
            "holding_pct_increase": _holding_pct_increase_tx(tx),
        }

    # Filter by transaction date (announced_at is often blank; using date gives
    # correct "today / this week" counts. Once backfill_announced_at.py is run
    # the two fields will converge to within 1-3 days of each other anyway).
    # B-010: sort by (date DESC, announced_at DESC NULLS LAST, ticker ASC).
    # Old code COALESCEd announced_at into date, which mixed timestamps
    # with dates and made the secondary tie-break unreliable. Splitting
    # them yields stable, freshest-first ordering with a deterministic
    # tertiary tie on ticker.
    # B-011 / Sprint 10 Phase 1: today_txs reads raw transactions
    # without a signals join, so excluded IT/CEF/VCT/REIT tickers
    # would surface in the dashboard's "today" table if not filtered.
    # LEFT JOIN tickers_meta + COALESCE keeps tickers absent from
    # tickers_meta (defensive). The SELECT t.* is unchanged.
    today_txs = conn.execute(
        "SELECT t.* FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.date = ? "
        "  AND t.type = 'BUY' "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "ORDER BY t.date DESC, "
        "         (t.announced_at IS NULL OR t.announced_at = '') ASC, "
        "         t.announced_at DESC, t.ticker ASC",
        (today_iso,),
    ).fetchall()

    week_txs = conn.execute(
        "SELECT t.* FROM transactions t "
        "WHERE t.date > ? "
        "  AND t.date < ? "
        "  AND t.type = 'BUY' "
        "  AND EXISTS (SELECT 1 FROM signals s "
        "              WHERE s.fingerprint = t.fingerprint) "
        "ORDER BY t.date DESC, "
        "         (t.announced_at IS NULL OR t.announced_at = '') ASC, "
        "         t.announced_at DESC, t.ticker ASC",
        (week_start, today_iso),
    ).fetchall()

    today_out = [_row(t) for t in today_txs]
    today_with_sigs = [r for r in today_out if r["signals_fired"]]

    # B-122: the "Signals Today" KPI must count by *fire-date* — a signal fires
    # when the RNS is announced (announced_at), which lags the dealing date
    # (t.date) by 1-4 business days. Keying the KPI on t.date undercounts today's
    # fired signals. Bucket on COALESCE(NULLIF(announced_at,''), date) so rows
    # with a blank announced_at fall back to the dealing date. KPI-only change:
    # the Today / This-Week *tables* still key on t.date (deferred, diff-first).
    today_count_row = conn.execute(
        "SELECT COUNT(DISTINCT t.fingerprint) AS n "
        "FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE date(COALESCE(NULLIF(t.announced_at,''), t.date)) = ? "
        "  AND t.type = 'BUY' "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "  AND EXISTS (SELECT 1 FROM signals s WHERE s.fingerprint = t.fingerprint)",
        (today_iso,),
    ).fetchone()
    signals_today_count = today_count_row["n"] if today_count_row else 0

    # Daily counts last 7 days for delta (also keyed on fire-date for consistency).
    counts = conn.execute(
        "SELECT date(COALESCE(NULLIF(t.announced_at,''), t.date)) AS d, "
        "       COUNT(DISTINCT t.fingerprint) AS n "
        "FROM transactions t "
        "JOIN signals s ON s.fingerprint = t.fingerprint "
        "WHERE date(COALESCE(NULLIF(t.announced_at,''), t.date)) >= ? "
        "  AND date(COALESCE(NULLIF(t.announced_at,''), t.date)) < ? "
        "GROUP BY d",
        ((today - timedelta(days=7)).isoformat(), today_iso),
    ).fetchall()
    daily = [r["n"] for r in counts]
    if daily:
        avg = statistics.fmean(daily)
    else:
        avg = 0.0
    delta = int(round(signals_today_count - avg))

    week_out = [_row(t) for t in week_txs]

    # B-145: upcoming events panel (next 30 days from reporting_dates).
    upcoming_events = _build_upcoming_events(conn, today)

    return {
        "as_of_date":               today_iso,
        "signals_today_count":      signals_today_count,
        "signals_today_delta_vs_avg": delta,
        "today":                    today_out,
        "this_week":                today_with_sigs + week_out,
        "upcoming_events":          upcoming_events,
    }


# ---------------------------------------------------------------------------
# Pending-review diagnostics
# ---------------------------------------------------------------------------

# Ordered by recoverability (most-recoverable last). Each bucket has:
#   id           -- stable key
#   name         -- display label
#   recoverable  -- one of "no" | "v2-fx" | "v2-fanout" | "manual" | "unknown"
#   description  -- short paragraph for the dashboard tooltip / row sub-text
#   match        -- list of case-insensitive substrings; warning matches if
#                   ANY substring appears in the warning text.
PENDING_BUCKET_SPEC: list[dict] = [
    {
        "id":          "bundled_multi_pdmr",
        "name":        "Bundled multi-PDMR",
        "recoverable": "no",
        "description": ("Filing names multiple directors in one announcement "
                        "-- D-STRICT refuses to split"),
        "match": [
            "bundled_multi_pdmr",
            "bundled multi-pdmr",
            "names not extractable from boilerplate",
            "could_not_extract_pdmr_name",
        ],
    },
    {
        "id":          "foreign_currency",
        "name":        "Foreign currency",
        "recoverable": "v2-fx",
        "description": "Non-GBP filing -- needs FX rate fetcher",
        "match": [
            "foreign_currency",
            "denominated in eur",
            "denominated in usd",
            "denominated in chf",
            "denominated in jpy",
            "priced in eur",
            "priced in usd",
            "priced in chf",
            "priced in jpy",
            " eur ",
            " usd ",
            " chf ",
            " jpy ",
            "€",
            "us$",
        ],
    },
    {
        "id":          "multi_tranche",
        "name":        "Multi-tranche / multi-transaction",
        "recoverable": "v2-fanout",
        "description": ("Filing reports two or more distinct prices or "
                        "transactions for the same PDMR -- needs row fan-out"),
        "match": [
            "multiple_distinct_prices",
            "could_not_separate_price_volume",
            "tranches",
            "two transactions",
            "multiple transactions",
            "two separate",
            "aggregated",
            "weighted average price",
            "three separate",
        ],
    },
    {
        "id":          "corporate_actions",
        "name":        "Corporate actions",
        "recoverable": "manual",
        "description": ("Vesting / option exercise / SIPP / DRIP / "
                        "scheme-of-reconstruction -- case-by-case review"),
        "match": [
            "isa transfer",
            "bed-and-isa",
            "bed and isa",
            "sipp",
            "dividend reinvest",
            "drip",
            "vesting",
            "exercise",
            "scheme of reconstruction",
            "transfer of shares",
            "transfer of beneficial",
            "scrip dividend",
            "ltip",
            "dbp",
            "matching share",
            "deferred share",
            "conditional share",
            "performance share",
            "persons closely associated",
            "pca/family",
        ],
    },
    {
        "id":          "could_not_classify",
        "name":        "Could not classify / extract",
        "recoverable": "manual",
        "description": ("Parser could not extract ticker / company / type / "
                        "required fields -- case-by-case review"),
        "match": [
            "could_not_classify_type",
            "could_not_extract_company",
            "could_not_extract_ticker",
            "required_fields_missing",
            "could_not_parse_tx_date",
        ],
    },
    {
        "id":          "data_quirks",
        "name":        "Zero-share / data quirks",
        "recoverable": "manual",
        "description": ("Zero-share non-grant rows, nil consideration, "
                        "no numeric values -- case-by-case review"),
        "match": [
            "zero_shares_non_grant",
            "no_numeric_values",
            "price was 0.012 pence",
            "nil consideration",
            "no price available",
            "fractional shares reported",
        ],
    },
]


def _classify_pending_warnings(warnings: list[str]) -> str:
    """Return the bucket id for a list of warning strings.

    First bucket whose ANY match-substring appears in ANY warning (case-
    insensitive) wins. Falls through to "other".
    """
    if not warnings:
        return "other"
    blob = " ".join(str(w) for w in warnings).lower()
    for spec in PENDING_BUCKET_SPEC:
        for needle in spec["match"]:
            if needle.lower() in blob:
                return spec["id"]
    return "other"


def _load_pending_items(pending_path: Path) -> dict:
    """Load _pending_review.json defensively.

    Returns the dict-of-items. If the file is missing or invalid JSON,
    attempt a regex-based recovery to extract warnings per rns_id so a
    truncated state file still yields usable diagnostics. Returns {} on
    total failure.
    """
    if not pending_path.exists():
        return {}
    try:
        raw = pending_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Recovery path -- the file has been truncated mid-write more than
        # once. Walk the raw text positionally: each rns_id key is followed
        # by exactly one "warnings" array before the next rns_id key, so
        # we use positional zip rather than a nested-brace regex (which
        # fails on inner objects inside the "extracted" list).
        import re
        items: dict = {}
        id_re = re.compile(r'"(\d{5,})"\s*:\s*\{')
        warns_re = re.compile(r'"warnings"\s*:\s*\[(.*?)\]', re.DOTALL)
        str_re = re.compile(r'"((?:[^"\\]|\\.)*)"')
        ids = [(m.start(), m.group(1)) for m in id_re.finditer(raw)]
        for i, (pos, rns) in enumerate(ids):
            end = ids[i + 1][0] if i + 1 < len(ids) else len(raw)
            chunk = raw[pos:end]
            wm = warns_re.search(chunk)
            warns: list[str] = []
            if wm:
                for s in str_re.findall(wm.group(1)):
                    try:
                        warns.append(json.loads('"' + s + '"'))
                    except Exception:
                        warns.append(s)
            items[rns] = {"warnings": warns}
        return items
    if isinstance(parsed, dict):
        items = parsed.get("items") or {}
        if isinstance(items, dict):
            return items
    return {}


def _compute_pending_diagnostics(pending_path: Path | None = None,
                                 generated_at: str | None = None) -> dict:
    """Categorise the _pending_review.json items into buckets.

    Returns ``{"total": N, "generated_at": "<iso>", "categories": [...]}``
    where ``categories`` is the 6 named buckets sorted by count desc,
    followed by a single "Other" row (always last).
    """
    pending_path = pending_path or DEFAULT_PENDING_PATH
    items = _load_pending_items(pending_path)
    total = len(items)

    # Bucketise.
    counts: dict[str, int] = {spec["id"]: 0 for spec in PENDING_BUCKET_SPEC}
    counts["other"] = 0
    for rns_id, rec in items.items():
        warns = rec.get("warnings") if isinstance(rec, dict) else None
        bucket = _classify_pending_warnings(warns or [])
        counts[bucket] = counts.get(bucket, 0) + 1

    # Build named bucket rows.
    named: list[dict] = []
    for spec in PENDING_BUCKET_SPEC:
        n = counts[spec["id"]]
        pct = round(100.0 * n / total, 1) if total > 0 else 0.0
        named.append({
            "id":           spec["id"],
            "name":         spec["name"],
            "count":        n,
            "pct":          pct,
            "recoverable":  spec["recoverable"],
            "description":  spec["description"],
        })
    named.sort(key=lambda d: d["count"], reverse=True)

    other_n = counts["other"]
    other_pct = round(100.0 * other_n / total, 1) if total > 0 else 0.0
    other_row = {
        "id":           "other",
        "name":         "Other",
        "count":        other_n,
        "pct":          other_pct,
        "recoverable":  "unknown",
        "description":  "Uncategorised -- review warnings text",
    }

    return {
        "total":        total,
        "generated_at": generated_at or _now_utc_iso(),
        "categories":   named + [other_row],
    }


# ---------------------------------------------------------------------------
# Sprint 14 (B-066) — cohort_performance.json blob
#
# A brand-new, ADDITIVE export. Per-(signal_group, calendar-month) aggregates
# of the NET-of-cost CAR (`net_car_*` columns, already net of 50bps spread +
# 0.5% stamp upstream), plus per-group sparkline / trend / header rollups and a
# trade-level drill-down with share-of-cohort-movement weights and a pre-computed
# verdict string.
#
# Design spec: docs/specs/cohort-performance-chart-design-spec.md
#              §"Data needed from JSON".
# Brief:       docs/specs/cohort-performance-chart-redesign-brief.md
#              §"Data fields required from upstream".
#
# Locked decisions honoured here:
#   * Expanding window since inception (no trailing cutoff).
#   * One point = one calendar-month cohort.
#   * sparkline_points emits explicit `null` for empty calendar months
#     (Rupert's locked decision).
#   * All cohort numbers are NET of costs (net_car_*), consistent with the
#     rest of the dashboard.
#
# Share-of-cohort-movement metric (metric correction ruling, 2026-05-29):
#   The per-trade weight is the bounded absolute-magnitude share
#   |r_i| / sum(|r_j|) over the month's matured net_car_t30 rows. It is always
#   in [0, 1], sums to ~1.0, and is sign-stable (it never inverts on
#   negative-mean cohorts the way the old signed r_i/(N*mean) did). It answers
#   "which ticker is pulling the line" regardless of sign; direction is read
#   from the adjacent net_car_t30 column. `single_ticker_weight`, the drill-down
#   `cohort_weight` field, and the verdict all use this single basis.
#   Guard: if sum(|r_j|) == 0 (every trade exactly flat), fall back to equal
#   weight 1/N. See docs/specs/cohort-performance-chart-design-spec.md
#   §"Metric correction ruling (2026-05-29)".
# ---------------------------------------------------------------------------

COHORT_SIGNAL_GROUPS = [
    "t1a", "t1b", "t2", "t3", "t4", "t5", "t6", "t7", "t0", "s1", "f1",
    # B-099 (Sprint 24): B1 lone-conviction buy added to cohort chart.
    # Expect sparse data for months — labelled "N=X (building)" client-side.
    # B2 (crowded cluster kill) is a suppression signal, not a buy signal;
    # it produces no positively-fired CAR series. B2 is excluded here and
    # handled separately as a suppression count annotation in the scoreboard.
    "b1",
]

# short-code -> long signal_id used in the backtest CSV / signals table.
_COHORT_SHORT_TO_LONG = {
    "t0":  "t0_cluster_combo",
    "t1a": "t1a_ceo_founder_buy",
    "t1b": "t1b_cfo_buy",
    "t7":  "t7_chair_buy",
    "t2":  "t2_exec_buy",
    "t3":  "t3_ned_buy",
    "t5":  "t5_pca_buy",
    "t6":  "t6_company_sec_buy",
    "t4":  "t4_other_buy",
    "s1":  "s1_cluster_buy",
    "f1":  "f1_first_time_buy",
    "b1":  "b1_lone_conviction_buy",   # B-099
}

# Human-readable labels for the chart header / pill row.
COHORT_GROUP_LABELS = {
    "t0":  "T0 cluster combo",
    "t1a": "T1A CEO/Founder buy",
    "t1b": "T1B CFO buy",
    "t7":  "T7 Chair buy",
    "t2":  "T2 exec buy",
    "t3":  "T3 NED buy",
    "t5":  "T5 PCA buy",
    "t6":  "T6 Co-Sec buy",
    "t4":  "T4 other buy",
    "s1":  "S1 cluster buy",
    "f1":  "F1 first-time buy",
    "b1":  "B1 lone conviction buy",   # B-099
}

# Role short labels for the drill-down rows.
_COHORT_ROLE_SHORT = {
    "t0":  "Cluster", "t1a": "CEO", "t1b": "CFO", "t7": "Chair",
    "t2":  "Exec", "t3": "NED", "t5": "PCA", "t6": "Co-Sec",
    "t4":  "Other", "s1": "Cluster", "f1": "First-buy",
    "b1":  "Lone conv.",   # B-099
}

def _month_iso(fired_at: str) -> str | None:
    """Return the 'YYYY-MM' calendar-month key for an ISO timestamp prefix."""
    s = (fired_at or "")[:10]
    if len(s) < 7:
        return None
    try:
        date.fromisoformat(s)
    except ValueError:
        # Accept a bare 'YYYY-MM' too.
        try:
            date.fromisoformat(s[:7] + "-01")
        except ValueError:
            return None
    return s[:7]


def _month_minus(month_iso: str, n: int) -> str:
    """Return the 'YYYY-MM' that is `n` months before `month_iso` (n>=0)."""
    y, m = int(month_iso[:4]), int(month_iso[5:7])
    total = y * 12 + (m - 1) - n
    ny, nm = divmod(total, 12)
    return f"{ny:04d}-{nm + 1:02d}"


def _month_range_inclusive(lo: str, hi: str) -> list[str]:
    """All 'YYYY-MM' from lo..hi inclusive, ascending. Used to fill gaps."""
    out = [lo]
    cur = lo
    # Guard against pathological inputs (lo > hi) — return just [lo].
    while cur < hi:
        y, m = int(cur[:4]), int(cur[5:7])
        total = y * 12 + (m - 1) + 1
        ny, nm = divmod(total, 12)
        cur = f"{ny:04d}-{nm + 1:02d}"
        out.append(cur)
    return out


def _completed_month(today: date) -> str:
    """Latest COMPLETE calendar month (the month before `today`'s month)."""
    return _month_minus(today.replace(day=1).isoformat()[:7], 1)


def _cohort_mean_pct(values: list[float]) -> float | None:
    """Mean of net CAR fractions -> fraction (NOT percent). Rounded to 4dp."""
    if not values:
        return None
    return round(statistics.fmean(values), 4)


def cohort_verdict(signals: list[dict]) -> str:
    """One-line verdict for a cohort's drill-down footer.

    Metric correction ruling (2026-05-29): uses the bounded absolute-magnitude
    share per ticker (sign-stable, in [0, 1]) to flag single-ticker dominance,
    and states DIRECTION (drag vs contributor) from the sign of the dominant
    ticker's OWN summed net_car_t30. Operates on the drill-down `signals[]`
    (each carrying `ticker` and `net_car_t30`).
    """
    if not signals:
        return ""
    # abs-share per ticker (sign-stable, bounded)
    total_abs = sum(abs(s["net_car_t30"]) for s in signals
                    if s.get("net_car_t30") is not None)
    if total_abs == 0:
        return "Cohort outcomes were broadly consistent."
    by_ticker: dict = {}
    for s in signals:
        r = s.get("net_car_t30")
        if r is None:
            continue
        by_ticker.setdefault(s["ticker"], 0.0)
        by_ticker[s["ticker"]] += r          # signed sum -> direction
    abs_by_ticker = {t: 0.0 for t in by_ticker}
    for s in signals:
        r = s.get("net_car_t30")
        if r is None:
            continue
        abs_by_ticker[s["ticker"]] += abs(r)
    top_tkr = max(abs_by_ticker, key=abs_by_ticker.get)
    share = abs_by_ticker[top_tkr] / total_abs
    if share > 0.5:
        net = by_ticker[top_tkr]
        share_pct = round(share * 100)
        net_pct = abs(net) * 100
        if net < 0:
            return (f"1 ticker ({top_tkr}) was the largest single drag on "
                    f"the cohort ({share_pct}% of total movement, "
                    f"-{net_pct:.1f}% net CAR).")
        return (f"1 ticker ({top_tkr}) was the largest single contributor to "
                f"the cohort ({share_pct}% of total movement, "
                f"+{net_pct:.1f}% net CAR).")
    nets = [s["net_car_t30"] for s in signals
            if s.get("net_car_t30") is not None]
    if nets and (max(nets) - min(nets)) < 0.05:
        return "Cohort outcomes were broadly consistent."
    return ""


def _rolling_hit_rate(month_rows: dict, months_sorted: list[str],
                      window_months: int = 6) -> dict:
    """Trailing-window hit rate of net_car_t30 > 0 (beat sector benchmark).

    `month_rows` maps month_iso -> list of net_car_t30 fractions (matured
    only). Returns month_iso -> float|None. The window is the inclusive
    trailing `window_months` calendar months ending at each month, so it is
    robust to gap months (an empty intermediate month contributes nothing
    but does not break the lookback span).
    """
    out: dict = {}
    for m in months_sorted:
        lo = _month_minus(m, window_months - 1)
        window: list[float] = []
        for src_month, vals in month_rows.items():
            if lo <= src_month <= m:
                window.extend(vals)
        if not window:
            out[m] = None
        else:
            out[m] = round(sum(1 for v in window if v > 0) / len(window), 4)
    return out


def _ma3(means_by_month: dict, months_sorted: list[str]) -> dict:
    """3-month trailing moving average of mean_car_t30.

    Per spec: null for the first 2 months of the group's history. A trailing
    window that contains a gap month (mean=None) still averages over whatever
    real means fall in the trailing-3 calendar span; if all three are null the
    result is null.
    """
    out: dict = {}
    for i, m in enumerate(months_sorted):
        if i < 2:
            out[m] = None
            continue
        window_months = months_sorted[i - 2:i + 1]
        vals = [means_by_month[w] for w in window_months
                if means_by_month.get(w) is not None]
        out[m] = round(statistics.fmean(vals), 4) if vals else None
    return out


def _cohort_contributions(net_cars: list[float]) -> list[float]:
    """Share of cohort movement per trade: |r_i| / sum(|r_j|).

    Bounded absolute-magnitude share (metric correction ruling, 2026-05-29).
    Always in [0, 1], sums to ~1.0, sign-stable (never inverts on negative-mean
    cohorts). Guard: if sum(|r_j|) == 0 (every trade exactly flat), fall back to
    equal weight 1/N.
    """
    n = len(net_cars)
    if n == 0:
        return []
    total_abs = sum(abs(r) for r in net_cars)
    if total_abs == 0:
        # Degenerate: every trade is exactly 0 net CAR. Equal weight.
        return [1.0 / n] * n
    return [abs(r) / total_abs for r in net_cars]


def _per_horizon_stats(h: str, month_rows_by_month: dict[str, list],
                       axis: list[str]) -> dict[str, dict]:
    """Compute per-month {mean, min, max, hit_rate, single_ticker_weight,
    rolling_6m_hit_rate, ma3} for the given horizon key (t1, t30, t90, t180, t365).

    `month_rows_by_month` maps month_iso -> list of raw row dicts.
    Net-CAR key is ``_net_car_{h}``.  Returns month_iso -> stat dict.
    All values are rounded; None when no matured data.
    """
    key = f"_net_car_{h}"

    # Build per-month lists of matured values + per-ticker maps.
    vals_by_month: dict[str, list[float]] = {}
    rows_by_month: dict[str, list] = {}
    for mo in axis:
        rs = month_rows_by_month.get(mo, [])
        vals = [r[key] for r in rs if r.get(key) is not None]
        vals_by_month[mo] = vals
        rows_by_month[mo] = [r for r in rs if r.get(key) is not None]

    # Rolling 6m hit rate (same window logic as _rolling_hit_rate).
    rolling = _rolling_hit_rate(vals_by_month, axis)

    # Means map for ma3.
    means: dict[str, float | None] = {
        mo: _cohort_mean_pct(vals_by_month[mo]) for mo in axis
    }
    ma3 = _ma3(means, axis)

    out: dict[str, dict] = {}
    for mo in axis:
        vals = vals_by_month[mo]
        rs_h = rows_by_month[mo]
        mean_v = _cohort_mean_pct(vals)
        hit_v = (round(sum(1 for v in vals if v > 0) / len(vals), 4)
                 if vals else None)

        stw = None
        if vals:
            contribs = _cohort_contributions(vals)
            by_ticker: dict[str, float] = {}
            for rr, w in zip(rs_h, contribs):
                tkr = rr.get("ticker") or ""
                by_ticker[tkr] = by_ticker.get(tkr, 0.0) + w
            if by_ticker:
                stw = round(max(by_ticker.values()), 4)

        out[mo] = {
            "mean":    mean_v,
            "min":     round(min(vals), 4) if vals else None,
            "max":     round(max(vals), 4) if vals else None,
            "hit":     hit_v,
            "stw":     stw,
            "rolling": rolling.get(mo),
            "ma3":     ma3.get(mo),
        }
    return out


def build_cohort_performance(rows: list[dict], today: date,
                             tx_lookup: dict | None = None,
                             emit_timestamp: bool = True,
                             abs_return_ann_by_fp: dict | None = None) -> dict:
    """Build the additive cohort_performance.json payload (B-066).

    Pure aside from `today`. `rows` are the loaded backtest CSV rows; each
    must carry `signal_id`, `_fired_at`, `ticker`, `fingerprint`, and the
    `_net_car_t1/t30/t90/t180/t365` + `_bench_t30` fields added by `load_backtest_csv`.
    `tx_lookup` (fingerprint -> {director, role, ...}) supplies drill-down
    director names; absent -> empty strings.
    `abs_return_ann_by_fp` (B-127) maps fingerprint -> announcement-date
    absolute stock return % (raw); computed by the driver where the DB/price
    caches are available. Absent/missing keys -> null in the drill-down.
    """
    tx_lookup = tx_lookup or {}
    abs_map = abs_return_ann_by_fp or {}
    latest_complete = _completed_month(today)

    # Bucket rows by (group, month). Only months <= latest complete month.
    by_group: dict[str, dict[str, list[dict]]] = {
        g: {} for g in COHORT_SIGNAL_GROUPS
    }
    long_to_short = {v: k for k, v in _COHORT_SHORT_TO_LONG.items()}
    for r in rows:
        grp = long_to_short.get(r.get("signal_id"))
        if grp is None:
            continue
        mo = _month_iso(r.get("_fired_at", ""))
        if mo is None or mo > latest_complete:
            continue
        by_group[grp].setdefault(mo, []).append(r)

    groups_out: dict = {}
    drilldown_out: dict = {}

    for grp in COHORT_SIGNAL_GROUPS:
        months_map = by_group[grp]
        # Determine the contiguous month axis: inception (earliest month with
        # ANY firing) to the latest complete month, filling gap months.
        firing_months = sorted(months_map.keys())
        if firing_months:
            axis = _month_range_inclusive(firing_months[0], latest_complete)
        else:
            axis = []

        # Per-horizon stats for t1, t30, t90, t180, t365 via the helper.
        # B-151: renamed from t21->t30, t252->t365; added t180.
        h_stats: dict[str, dict[str, dict]] = {}
        for hkey in ("t1", "t30", "t90", "t180", "t365"):
            h_stats[hkey] = _per_horizon_stats(hkey, months_map, axis)

        # Retain the per-month t30 matured list for the trend calc (reuse).
        net_t30_by_month: dict[str, list[float]] = {
            mo: [r["_net_car_t30"] for r in months_map.get(mo, [])
                 if r.get("_net_car_t30") is not None]
            for mo in axis
        }

        months_list: list[dict] = []
        all_net_t30: list[float] = []   # for header mean/hit-rate overall
        all_net_by_h: dict[str, list[float]] = {h: [] for h in ("t1", "t90", "t180", "t365")}
        sparkline_points: list[dict] = []

        for mo in axis:
            rs = months_map.get(mo, [])
            n_signals = len(rs)
            s30  = h_stats["t30"][mo]
            s1   = h_stats["t1"][mo]
            s90  = h_stats["t90"][mo]
            s180 = h_stats["t180"][mo]
            s365 = h_stats["t365"][mo]

            mean30 = s30["mean"]
            all_net_t30.extend(net_t30_by_month[mo])
            for hkey in ("t1", "t90", "t180", "t365"):
                all_net_by_h[hkey].extend(
                    r[f"_net_car_{hkey}"] for r in rs
                    if r.get(f"_net_car_{hkey}") is not None
                )

            signal_ids = sorted(
                rr.get("fingerprint") for rr in rs if rr.get("fingerprint")
            )

            # B-072 (Sprint 14): pending = signals fired this month but the
            # T+30 window has not yet matured (no matured net_car_t30). The
            # explicit flag spares the front-end re-deriving
            # (n_signals>0 && mean_car_t30==null). Gap months (n_signals==0)
            # are NOT pending — they are genuinely empty.
            is_pending = n_signals > 0 and mean30 is None

            # Sprint 24: pending_horizons = which horizons haven't matured yet.
            pending_horizons = []
            if n_signals > 0:
                for hkey, hs in h_stats.items():
                    if hs[mo]["mean"] is None:
                        pending_horizons.append(hkey)

            months_list.append({
                # ---- T+30 keys (B-151 rename from t21) ----
                "month_iso":                mo,
                "n_signals":                n_signals,
                "mean_car_t1":              s1["mean"],
                "mean_car_t30":             mean30,
                "mean_car_t90":             s90["mean"],
                "min_car_t30":              s30["min"],
                "max_car_t30":              s30["max"],
                "hit_rate_t30":             s30["hit"],
                "hit_rate_t30_rolling_6m":  s30["rolling"],
                "single_ticker_weight":     s30["stw"],
                "ma3_mean_car_t30":         s30["ma3"],
                "signal_ids":               signal_ids,
                "pending":                  is_pending,
                # ---- per-horizon extensions ----
                "pending_horizons":         pending_horizons,
                # t1
                "min_car_t1":               s1["min"],
                "max_car_t1":               s1["max"],
                "hit_rate_t1":              s1["hit"],
                "hit_rate_t1_rolling_6m":   s1["rolling"],
                "single_ticker_weight_t1":  s1["stw"],
                "ma3_mean_car_t1":          s1["ma3"],
                # t30 extended (align naming pattern for cohortPick)
                "min_car_t30":              s30["min"],
                "max_car_t30":              s30["max"],
                "single_ticker_weight_t30": s30["stw"],
                # t90
                "mean_car_t90":             s90["mean"],
                "min_car_t90":              s90["min"],
                "max_car_t90":              s90["max"],
                "hit_rate_t90":             s90["hit"],
                "hit_rate_t90_rolling_6m":  s90["rolling"],
                "single_ticker_weight_t90": s90["stw"],
                "ma3_mean_car_t90":         s90["ma3"],
                # t180
                "mean_car_t180":            s180["mean"],
                "min_car_t180":             s180["min"],
                "max_car_t180":             s180["max"],
                "hit_rate_t180":            s180["hit"],
                "hit_rate_t180_rolling_6m": s180["rolling"],
                "single_ticker_weight_t180":s180["stw"],
                "ma3_mean_car_t180":        s180["ma3"],
                # t365
                "mean_car_t365":            s365["mean"],
                "min_car_t365":             s365["min"],
                "max_car_t365":             s365["max"],
                "hit_rate_t365":            s365["hit"],
                "hit_rate_t365_rolling_6m": s365["rolling"],
                "single_ticker_weight_t365":s365["stw"],
                "ma3_mean_car_t365":        s365["ma3"],
            })

            # sparkline_points: explicit null for empty calendar months
            # (Rupert's locked decision).
            sparkline_points.append({
                "month_iso":    mo,
                "mean_car_t30": mean30,   # None for gap months
            })

        def _overall_hit(vals: list[float]) -> float | None:
            if not vals:
                return None
            return round(sum(1 for v in vals if v > 0) / len(vals), 4)

        # Header rollups (inception-to-date, net of costs).
        header = {
            "n_total_signals":        sum(m["n_signals"] for m in months_list),
            "mean_car_t30_overall":   _cohort_mean_pct(all_net_t30),
            "hit_rate_t30_overall":   _overall_hit(all_net_t30),
            # Additional horizon rollups
            "mean_car_t1_overall":    _cohort_mean_pct(all_net_by_h["t1"]),
            "hit_rate_t1_overall":    _overall_hit(all_net_by_h["t1"]),
            "mean_car_t90_overall":   _cohort_mean_pct(all_net_by_h["t90"]),
            "hit_rate_t90_overall":   _overall_hit(all_net_by_h["t90"]),
            "mean_car_t180_overall":  _cohort_mean_pct(all_net_by_h["t180"]),
            "hit_rate_t180_overall":  _overall_hit(all_net_by_h["t180"]),
            "mean_car_t365_overall":  _cohort_mean_pct(all_net_by_h["t365"]),
            "hit_rate_t365_overall":  _overall_hit(all_net_by_h["t365"]),
        }

        # trend_3m_vs_prior3m_t30: last 3 calendar months' mean net CAR @ T+30
        # minus the prior 3 months'. Uses the contiguous axis (gap months
        # contribute no trades). Null if either window has no matured trades.
        trend = None
        if len(axis) >= 1:
            last3 = axis[-3:]
            prior3 = axis[-6:-3]
            last_vals = [v for mo in last3 for v in net_t30_by_month.get(mo, [])]
            prior_vals = [v for mo in prior3 for v in net_t30_by_month.get(mo, [])]
            if last_vals and prior_vals:
                trend = round(statistics.fmean(last_vals)
                              - statistics.fmean(prior_vals), 4)

        pal = TIER_PALETTE.get(grp, {})
        groups_out[grp] = {
            "label":     COHORT_GROUP_LABELS.get(grp, grp.upper()),
            "color_hex": pal.get("hex", "#94a3b8"),
            "header":    header,
            "months":    months_list,
            "sparkline_points":           sparkline_points,
            "trend_3m_vs_prior3m_t30":    trend,
        }

        # ----- drill-down: one entry per (group, month) that has trades -----
        grp_drill: dict = {}
        for mo in firing_months:
            rs = months_map.get(mo, [])
            rs_t30 = [rr for rr in rs if rr.get("_net_car_t30") is not None]
            if not rs_t30:
                # B-072 (Sprint 14): a firing month with no matured T+30
                # trades is PENDING (signals fired, return window not yet
                # elapsed). Cohort-weight / verdict maths is impossible
                # without matured returns, but Rupert wants the fired trades
                # reachable in the drill-down. Emit the same row shape with
                # null for everything that needs maturity, plus pending:true.
                if not rs:
                    continue
                pend_sigs: list[dict] = []
                for rr in rs:
                    fp = rr.get("fingerprint", "") or ""
                    tx = tx_lookup.get(fp, {})
                    pend_sigs.append({
                        "fingerprint":   fp,
                        "ticker":        rr.get("ticker") or "",
                        "director":      tx.get("director") or "",
                        "role_short":    _COHORT_ROLE_SHORT.get(grp, grp.upper()),
                        "fire_date":     (rr.get("_fired_at") or "")[:10],
                        # car_t1 may be populated; the rest are typically
                        # null/absent until maturity -> emit null.
                        "car_t1":        rr.get("_car_t1"),
                        "car_t30":       rr.get("_car_t30"),
                        "car_t90":       rr.get("_car_t90"),
                        "benchmark_t30": rr.get("_bench_t30"),
                        "net_car_t30":   rr.get("_net_car_t30"),
                        # B-127: raw stock return since the announcement date.
                        "abs_return_ann": abs_map.get(fp),
                        # No matured returns -> share of cohort movement is
                        # undefined. Do NOT fall back to abs-share on partial
                        # data; emit null.
                        "cohort_weight": None,
                    })
                # Sort most-recent first (share unavailable as a sort key).
                pend_sigs.sort(key=lambda s: s["fire_date"], reverse=True)
                grp_drill[mo] = {
                    "verdict":  "",       # no verdict for an unmeasured cohort
                    "signals":  pend_sigs,
                    "pending":  True,
                }
                continue
            net30 = [rr["_net_car_t30"] for rr in rs_t30]
            contribs = _cohort_contributions(net30)
            sigs: list[dict] = []
            for rr, w in zip(rs_t30, contribs):
                fp = rr.get("fingerprint", "") or ""
                tx = tx_lookup.get(fp, {})
                sigs.append({
                    "fingerprint":   fp,
                    "ticker":        rr.get("ticker") or "",
                    "director":      tx.get("director") or "",
                    "role_short":    _COHORT_ROLE_SHORT.get(grp, grp.upper()),
                    "fire_date":     (rr.get("_fired_at") or "")[:10],
                    "car_t1":        rr.get("_car_t1"),
                    "car_t30":       rr.get("_car_t30"),
                    "car_t90":       rr.get("_car_t90"),
                    "benchmark_t30": rr.get("_bench_t30"),
                    "net_car_t30":   rr.get("_net_car_t30"),
                    # B-127: raw stock return since the announcement date.
                    "abs_return_ann": abs_map.get(fp),
                    "cohort_weight": round(w, 4),
                })
            # Default sort: share of cohort movement descending (biggest
            # mover first, win or lose).
            sigs.sort(key=lambda s: s["cohort_weight"], reverse=True)
            grp_drill[mo] = {
                "verdict":            cohort_verdict(sigs),
                "signals":            sigs,
            }
        if grp_drill:
            drilldown_out[grp] = grp_drill

    payload: dict = {
        "horizon":          "t30",
        "signal_groups":    COHORT_SIGNAL_GROUPS,
        "groups":           groups_out,
        "cohort_drilldown": drilldown_out,
    }
    if emit_timestamp:
        payload = {"generated_at": _now_utc_iso(), **payload}
    return payload


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sprint 5 — DB-derived lookup helpers (tickers_meta + tx_lookup)
# ---------------------------------------------------------------------------

def _load_tickers_meta(conn) -> dict:
    """Load ``{ticker -> {sector, benchmark_symbol, is_aim, company}}`` from DB.

    The `tickers_meta` table itself has no `company` column, so company is
    sourced from the most-recent `transactions.company` per ticker (Rupert
    Q6 — locked 2026-05-18). The two queries together populate a single
    map that downstream builders (sector payload, firing rows) read.

    Defensive: returns ``{}`` if the tables are missing or queries fail
    (lets the exporter still produce signals.json with empty sector cuts
    rather than crashing on a Sprint-5-only feature).
    """
    try:
        tm_rows = conn.execute(
            "SELECT ticker, sector, benchmark_symbol, is_aim, market_cap_gbp "
            "FROM tickers_meta"
        ).fetchall()
    except Exception:
        return {}
    out: dict = {}
    for r in tm_rows:
        out[r["ticker"]] = {
            "sector":           r["sector"],
            "benchmark_symbol": r["benchmark_symbol"],
            "is_aim":           bool(r["is_aim"]) if r["is_aim"] is not None else False,
            "market_cap_gbp":   r["market_cap_gbp"],
            "company":          "",
        }
    # Enrich with most-recent company per ticker (Rupert Q6).
    try:
        company_rows = conn.execute(
            "SELECT t.ticker, t.company "
            "FROM transactions t "
            "INNER JOIN ( "
            "  SELECT ticker, MAX(date) AS max_date "
            "  FROM transactions "
            "  GROUP BY ticker "
            ") m ON t.ticker = m.ticker AND t.date = m.max_date"
        ).fetchall()
    except Exception:
        return out
    for r in company_rows:
        if r["ticker"] in out:
            out[r["ticker"]]["company"] = r["company"] or ""
        else:
            # Ticker has transactions but no tickers_meta row — include
            # with empty sector so firing rows still get a company name.
            out[r["ticker"]] = {
                "sector":           None,
                "benchmark_symbol": None,
                "is_aim":           False,
                "company":          r["company"] or "",
            }
    return out


def _load_tx_lookup(conn) -> dict:
    """Load ``{fingerprint -> {director, role, company, ticker}}`` from the
    transactions table in a single query. Used by drill-payload firing-row
    construction to avoid N+1 lookups.

    Defensive: returns ``{}`` on any SQL error so the dual-emit path
    degrades gracefully (firing rows will show empty director / company).
    """
    try:
        rows = conn.execute(
            "SELECT fingerprint, director, role, role_normalized, "
            "company, ticker, announced_at "
            "FROM transactions"
        ).fetchall()
    except Exception:
        return {}
    return {
        r["fingerprint"]: {
            "director": r["director"] or "",
            "role":     r["role"] or "",
            # B-025 Phase A: canonical bucket carried through to firings.
            "role_normalized": r["role_normalized"] if "role_normalized" in r.keys() else None,
            "company":  r["company"] or "",
            "ticker":   r["ticker"] or "",
            # B-127: announcement timestamp for announcement-date abs return.
            "announced_at": (r["announced_at"] if "announced_at" in r.keys() else "") or "",
        }
        for r in rows
    }


# B-125: corporate-entity name tokens. Used to spot non-personal "directors"
# (associated corporate holders / funds / trusts) that file as PCAs or land in
# the 'Other / unclassified' bucket. Matched as whole words, case-insensitive.
_CORP_NAME_RE = re.compile(
    r"\b("
    r"LIMITED|LTD|PLC|LLP|L\.?P|GMBH|INC|CORP|"
    r"HOLDINGS?|CAPITAL|PARTNERS?|INVESTMENTS?|VENTURES?|MANAGEMENT|"
    r"NOMINEES?|SECURITIES|TRUST|FUND|GROUP|ASSET|ENERGY|RESOURCES|"
    r"FOUNDATION|PENSION|ADVISOR|ADVISER|EQUITY|BANK|COMPANY|CO\."
    r")\b",
    re.IGNORECASE,
)


def _is_corporate_or_pca(role_normalized: str, role: str, director: str) -> bool:
    """B-125: True if a transaction row represents an associated corporate
    holder or a Person-Closely-Associated (PCA) rather than an individual
    director. Used to keep the Monthly Activity chart to genuine director
    buy/sell activity (large institutional block disposals — Potomac View,
    Eminence Capital, DKL Energy, Eni UK, etc. — otherwise dwarf the chart).

    Three independent tests (calibrated on the live corpus, 86/1324 sells, no
    individual false positives):
      1. role_normalized == 'PCA' (the clean bucket).
      2. role text says PCA / "closely associated" (catches rows that never got
         normalized, e.g. Eminence Capital LP tagged role_normalized='').
      3. director name carries a corporate-entity token (catches DKL Energy
         Limited / Eni UK Limited sitting in 'Other / unclassified').
    """
    rn = (role_normalized or "").strip()
    if rn == "PCA":
        return True
    role_l = (role or "").lower()
    if re.search(r"\bpca\b", role_l) or "closely assoc" in role_l:
        return True
    if _CORP_NAME_RE.search(director or ""):
        return True
    return False


def build_monthly_buysell(conn, today: date, *, small_cap: int | None = None) -> dict:
    """B-102 + sprint-34: Trailing 12-month buy/sell summary.

    Returns:
        {
          "months":               ["2025-07", ..., "2026-06"],
          "buy_values":           [1234567.0, ..., None],   # None if no buys
          "sell_values":          [-456789.0, ..., None],   # negative; None if no sells
          "buy_counts":           [12, ..., 0],
          "sell_counts":          [3, ..., 0],
          # Sprint 34 additions:
          "trailing12_buy_total":  float,   # sum of all buy values
          "trailing12_sell_total": float,   # sum of abs(sell) values
          "trailing12_buy_count":  int,
          "trailing12_sell_count": int,
          "trend_buy_pct":         float|None,  # % chg last-3mo vs prior-3mo
          "trend_sell_pct":        float|None,
          "monthly_txns":          {month_iso: [{ticker, company, director,
                                                  type, value}, ...]},
                                   # top 10 by value per month, all types
        }

    Excludes is_excluded_issuer tickers. Uses announced_at when available,
    falls back to `date`.
    Sprint 56: small_cap=1 restricts to mkt cap < £500m; small_cap=0 to >= £500m.
    """
    # Build the 12-month axis (trailing, inclusive of current month).
    months = []
    for offset in range(11, -1, -1):
        y = today.year
        m = today.month - offset
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")

    sc_clause = " AND COALESCE(tm.small_cap, -1) = ?" if small_cap is not None else ""
    params: list = [months[0]]
    if small_cap is not None:
        params.append(small_cap)

    rows = conn.execute(
        "SELECT t.type, "
        "  CASE WHEN COALESCE(t.price_audit,'ok') IN ('unresolved','no_market') "
        "       THEN NULL ELSE t.value END AS value, "
        "  t.ticker, t.company, t.director, t.role, t.role_normalized, "
        # B-179: substr(...,1,7) extracts 'YYYY-MM' from an ISO date/timestamp
        # string identically on SQLite and Postgres (both 1-indexed), replacing
        # the SQLite-only strftime('%Y-%m', ...).
        "  COALESCE(substr(t.announced_at, 1, 7), substr(t.date, 1, 7)) AS mo "
        "FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "  AND t.type IN ('BUY', 'SELL') "
        "  AND t.value IS NOT NULL "
        "  AND COALESCE(substr(t.announced_at, 1, 7), substr(t.date, 1, 7)) "
        f"      >= ?"
        f"{sc_clause}",
        tuple(params),
    ).fetchall()

    buy_val: dict[str, float] = {}
    sell_val: dict[str, float] = {}
    buy_cnt: dict[str, int] = {}
    sell_cnt: dict[str, int] = {}
    monthly_txns_raw: dict[str, list] = {}

    excluded_corp_count = 0
    excluded_corp_value = 0.0
    for r in rows:
        mo = r["mo"]
        if mo not in months:
            continue
        # B-125: drop associated corporate holders / PCAs so the chart reflects
        # individual-director activity, not institutional block disposals.
        if _is_corporate_or_pca(
            r["role_normalized"] if "role_normalized" in r.keys() else "",
            r["role"] if "role" in r.keys() else "",
            r["director"],
        ):
            excluded_corp_count += 1
            excluded_corp_value += float(r["value"] or 0)
            continue
        val = float(r["value"] or 0)
        if r["type"] == "BUY":
            buy_val[mo] = buy_val.get(mo, 0) + val
            buy_cnt[mo] = buy_cnt.get(mo, 0) + 1
        else:
            sell_val[mo] = sell_val.get(mo, 0) + val
            sell_cnt[mo] = sell_cnt.get(mo, 0) + 1
        monthly_txns_raw.setdefault(mo, []).append({
            "ticker":   r["ticker"] or "",
            "company":  (r["company"] or "")[:40],
            "director": (r["director"] or "")[:30],
            "type":     r["type"],
            "value":    round(val, 0),
        })

    # Top 10 by value per month (largest first).
    monthly_txns = {
        mo: sorted(txs, key=lambda t: t["value"], reverse=True)[:10]
        for mo, txs in monthly_txns_raw.items()
    }

    # Trailing-12 totals.
    t12_buy_total  = round(sum(buy_val.values()), 0)
    t12_sell_total = round(sum(sell_val.values()), 0)
    t12_buy_count  = sum(buy_cnt.values())
    t12_sell_count = sum(sell_cnt.values())

    # Trend: last 3 months vs previous 3 months (indices -3..-1 vs -6..-4).
    def _3mo_sum(month_list, val_dict, idx_start, idx_end):
        return sum(val_dict.get(month_list[i], 0) for i in range(idx_start, idx_end))

    buy_last3  = _3mo_sum(months, buy_val,  9, 12)
    buy_prev3  = _3mo_sum(months, buy_val,  6, 9)
    sell_last3 = _3mo_sum(months, sell_val, 9, 12)
    sell_prev3 = _3mo_sum(months, sell_val, 6, 9)

    trend_buy_pct = (
        round((buy_last3 - buy_prev3) / buy_prev3 * 100.0, 1)
        if buy_prev3 > 0 else None
    )
    trend_sell_pct = (
        round((sell_last3 - sell_prev3) / sell_prev3 * 100.0, 1)
        if sell_prev3 > 0 else None
    )

    return {
        "months":      months,
        "buy_values":  [round(buy_val[mo], 0) if mo in buy_val else None
                        for mo in months],
        "sell_values": [round(-sell_val[mo], 0) if mo in sell_val else None
                        for mo in months],
        "buy_counts":  [buy_cnt.get(mo, 0) for mo in months],
        "sell_counts": [sell_cnt.get(mo, 0) for mo in months],
        "trailing12_buy_total":  t12_buy_total,
        "trailing12_sell_total": t12_sell_total,
        "trailing12_buy_count":  t12_buy_count,
        "trailing12_sell_count": t12_sell_count,
        "trend_buy_pct":         trend_buy_pct,
        "trend_sell_pct":        trend_sell_pct,
        "monthly_txns":          monthly_txns,
        # B-125: how many corporate/PCA rows were excluded from this view.
        "excluded_corporate_count": excluded_corp_count,
        "excluded_corporate_value": round(excluded_corp_value, 0),
    }


# ---------------------------------------------------------------------------
# B-123: £10k-per-signal strategy tracker vs ^FTAS shadow
# ---------------------------------------------------------------------------

_STRAT_STAKE_GBP   = 10_000.0   # flat stake per buy-signal transaction
_STRAT_SPREAD      = 0.005      # 50bps spread at entry, both legs
_STRAT_STAMP       = 0.005      # 0.5% stamp on non-AIM buys, equity leg only
_STRAT_EXIT_OFFSET = 21         # sell at T+30 calendar days (21 trading days)
_FTAS_SYMBOL       = "^FTAS"    # FTSE All-Share shadow index


def _ff_on_axis(axis: list, dates: list, closes: list) -> list:
    """Forward-fill ``closes`` (paired with ascending ``dates``) onto ``axis``.

    Returns a list aligned to ``axis`` where each entry is the last close whose
    date is <= the axis date, or None before the first available date.
    """
    out = [None] * len(axis)
    j = 0
    last = None
    n = len(dates)
    for i, d in enumerate(axis):
        while j < n and dates[j] <= d:
            last = closes[j]
            j += 1
        out[i] = last
    return out


def _close_le(dates: list, closes: list, target) -> float | None:
    """Last close whose date is <= ``target`` (binary search), else None."""
    if not target:
        return None
    k = bisect.bisect_right(dates, target)
    return closes[k - 1] if k > 0 else None


# B-171 sub-score display metadata — single source of truth for labels/order
# used to build the per-factor display fields the panel renders.
_CONVICTION_FACTOR_LABELS = [
    ("who", "f1_who", "Who"),
    ("buy_size", "f2_buy_size", "Buy size"),
    ("company_size", "f3_company_size", "Company size"),
    ("earnings_timing", "f4_earnings_timing", "Earnings timing"),
    ("past_performance", "f5_past_performance", "Past performance"),
]


# B-171 revised surfacing: rolling trailing-window length (days) and the
# fixed number of strongest buys to surface in the permanent table.
CONVICTION_WINDOW_DAYS = 28
CONVICTION_TOP_N = 10


def build_conviction_picks(conn, today: date) -> dict:
    """B-171 (revised surfacing): rolling-window Conviction Score top-10 + shadow log.

    Returns a dict shaped for the dashboard panel:
        {
          "window_days":  28,
          "window_start": "<ISO>",   # today - 28 days (inclusive)
          "window_end":   "<ISO>",   # today (inclusive)
          "top10":        [ <pick>, ... ],   # up to 10, no min-score gate
        }

    Selection (revised 2026-06-18): score EVERY BUY whose effective
    announcement day falls in the trailing 28-day window [today-28, today], rank
    them all, and surface the strongest 10 — a permanent table refreshed every
    pipeline run. A buy ages out once it is more than 28 days old. There is no
    minimum-score bar: the 10 strongest are always shown regardless of strength.

    Each <pick> is the engine ConvictionResult.as_dict() PLUS the buy identity
    (date / ticker / company / director / role / value_gbp), the per-factor
    display fields, the inputs_missing list, and rank / band.

    SIDE EFFECT (Phase-3 shadow log, spec §7): upserts ONE conviction_scores
    row per buy in the window — the WHOLE distribution, not just the surfaced 10
    — with rank_in_window (= rank within the window) across all buys and
    surfaced=1 for the surfaced top 10 only. The `window_end` column stores
    the window-END (= run) date. This is what the measure-forward loop later
    regresses forward CAR against. We commit here because the caller does not.

    Honest-surfacing rule (spec §6): the top 10 are shown regardless of bar —
    the SCORE/band does the honest work. Factors with no underlying data are
    flagged in inputs_missing so the renderer shows "unknown", never a
    misleading 0.

    Returns {..., "top10": []} when no buys fall in the window.
    """
    window_end = today.isoformat()
    window_start = (today - timedelta(days=CONVICTION_WINDOW_DAYS)).isoformat()
    empty = {
        "window_days":  CONVICTION_WINDOW_DAYS,
        "window_start": window_start,
        "window_end":   window_end,
        "top10":        [],
    }

    try:
        ranked = conviction_pipeline.score_window(
            conn, today, days=CONVICTION_WINDOW_DAYS)
    except Exception as exc:
        # R-1 (cloud migration): on Postgres a failure here is almost always a
        # dialect/porting bug in the conviction READ queries — fail LOUD so it
        # can never ship an empty conviction panel with a green exit (the exact
        # failure mode this migration set out to kill). On SQLite keep the
        # historical lenient behaviour so a genuine scoring hiccup doesn't break
        # the whole dashboard export.
        import sys as _sys
        print(f"[conviction] score_window failed: {type(exc).__name__}: {exc}",
              file=_sys.stderr)
        if db.backend() == "postgres":
            raise
        return empty

    scored_at = _now_utc_iso()

    # ---- Phase-3 shadow log: upsert EVERY buy in the window. ----
    # The `window_end` column stores the window-END (run) date; the schema 016
    # PK (fingerprint, window_end) keeps a row per buy per run.
    #
    # B-179: INSERT OR REPLACE (SQLite) <-> ON CONFLICT DO UPDATE (Postgres) on
    # the PK (fingerprint, window_end). Full-row insert -> the DO UPDATE refreshes
    # every non-PK column so a re-run within the same window overwrites in place,
    # matching the SQLite delete+reinsert exactly.
    if db.backend() == "postgres":
        _cs_sql = (
            "INSERT INTO conviction_scores ("
            "fingerprint, window_end, scored_at, score, band, "
            "f1_who, f2_buy_size, f3_company_size, f4_earnings_timing, "
            "f5_past_performance, f6_sector_mult, weights_used, "
            "earnings_dropped, rank_in_window, surfaced, inputs_missing"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (fingerprint, window_end) DO UPDATE SET "
            "scored_at = excluded.scored_at, score = excluded.score, "
            "band = excluded.band, f1_who = excluded.f1_who, "
            "f2_buy_size = excluded.f2_buy_size, "
            "f3_company_size = excluded.f3_company_size, "
            "f4_earnings_timing = excluded.f4_earnings_timing, "
            "f5_past_performance = excluded.f5_past_performance, "
            "f6_sector_mult = excluded.f6_sector_mult, "
            "weights_used = excluded.weights_used, "
            "earnings_dropped = excluded.earnings_dropped, "
            "rank_in_window = excluded.rank_in_window, "
            "surfaced = excluded.surfaced, inputs_missing = excluded.inputs_missing"
        )
    else:
        _cs_sql = (
            "INSERT OR REPLACE INTO conviction_scores ("
            "fingerprint, window_end, scored_at, score, band, "
            "f1_who, f2_buy_size, f3_company_size, f4_earnings_timing, "
            "f5_past_performance, f6_sector_mult, weights_used, "
            "earnings_dropped, rank_in_window, surfaced, inputs_missing"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )

    # R-1 (B-179): do NOT swallow write failures silently — that is exactly how
    # the conviction panel shipped EMPTY in production (a column mismatch raised
    # on every row, was caught by a blanket except, and the run exited 0 with an
    # empty table). We still tolerate the ONE legitimately-skippable per-row
    # condition (a missing FK parent transaction => IntegrityError) but count it,
    # and re-raise anything else. After the loop we assert that, when there were
    # rows to write, at least one actually landed — so a systematic porting bug
    # fails loudly instead of producing an empty panel with exit 0.
    _integrity_errors = (sqlite3.IntegrityError,)
    try:  # widen the catch to include psycopg's IntegrityError on the PG path.
        import psycopg
        _integrity_errors = (sqlite3.IntegrityError, psycopg.errors.IntegrityError)
    except Exception:  # noqa: BLE001 - psycopg absent on the SQLite path
        pass

    n_attempted = len(ranked)
    n_written = 0
    n_skipped_fk = 0
    for entry in ranked:
        sub = entry["result"].get("subscores", {})
        surfaced = 1 if entry["rank_in_window"] <= CONVICTION_TOP_N else 0
        try:
            conn.execute(
                _cs_sql,
                (
                    entry["fingerprint"], window_end, scored_at,
                    entry["score"], entry["band"],
                    sub.get("who"), sub.get("buy_size"),
                    sub.get("company_size"), sub.get("earnings_timing"),
                    sub.get("past_performance"),
                    entry["result"].get("sector_multiplier"),
                    json.dumps(entry["result"].get("weights_used", {})),
                    1 if entry["result"].get("earnings_dropped") else 0,
                    entry["rank_in_window"], surfaced,
                    json.dumps(entry.get("inputs_missing", [])),
                ),
            )
            n_written += 1
        except _integrity_errors:
            # A single bad row (missing FK parent) must not abort the whole
            # shadow log; skip it and continue. Postgres aborts the txn on a
            # failed statement, so roll back to keep the connection usable.
            n_skipped_fk += 1
            if db.backend() == "postgres":
                conn.rollback()
            continue
        # Any OTHER exception (e.g. a column-name / dialect porting bug) is NOT
        # swallowed — it propagates so the pipeline step fails with rc!=0.

    conn.commit()

    # R-1 assertion: if we had buys to log and EVERY one failed (n_written == 0
    # while n_attempted > 0 and the failures were not all genuine FK skips),
    # that is the silent-empty-panel failure mode — surface it loudly.
    if n_attempted > 0 and n_written == 0 and n_skipped_fk < n_attempted:
        raise RuntimeError(
            f"conviction_scores shadow log wrote 0 of {n_attempted} rows "
            f"(fk_skips={n_skipped_fk}). Refusing to silently ship an empty "
            "conviction panel — investigate the conviction_scores write path."
        )

    # ---- Panel payload: the top 10 with full factor breakdown. ----
    top10 = []
    for entry in ranked[:CONVICTION_TOP_N]:
        rd = dict(entry["result"])
        sub = rd.get("subscores", {})
        missing = set(entry.get("inputs_missing", []))
        factors = []
        for key, fid, label in _CONVICTION_FACTOR_LABELS:
            # company_size / earnings_timing / past_performance can be "unknown".
            is_unknown = (
                (key == "company_size" and "company_size" in missing)
                or (key == "earnings_timing" and "earnings_timing" in missing)
                or (key == "past_performance" and "past_performance" in missing)
            )
            factors.append({
                "id": fid,
                "label": label,
                "value": (None if is_unknown else sub.get(key)),
                "unknown": is_unknown,
            })
        pick = {
            **rd,
            "fingerprint": entry["fingerprint"],
            "rank": entry["rank_in_window"],
            "date": entry["date"],
            "ticker": entry["ticker"],
            "company": entry["company"],
            "director": entry["director"],
            "role": entry["role"],
            "value_gbp": entry["value_gbp"],
            "factors": factors,
            "inputs_missing": entry.get("inputs_missing", []),
            "sector_caution": (entry["result"].get("sector_multiplier", 1.0) < 1.0),
        }
        top10.append(pick)

    return {
        "window_days":  CONVICTION_WINDOW_DAYS,
        "window_start": window_start,
        "window_end":   window_end,
        "top10":        top10,
    }


def build_strategy_tracker(conn, today: date, *, small_cap: int | None = None) -> dict:
    """B-123: flat £10,000-per-buy-signal strategy vs an identical £10k/signal
    FTSE All-Share (^FTAS) shadow, as a daily mark-to-market time series.

    Locked design (Rupert 2026-06-06):
      * Stake: flat £10k per *transaction* that fired any of the 11 directional
        buy signals (deduped by fingerprint — a stock isn't bought twice just
        because two signals fired on the same purchase).
      * Entry: first trading day after announcement; Exit: sell at T+30 (21 trading days).
      * Costs CHARGED at entry: 50bps spread both legs + 0.5% stamp on non-AIM
        buys (equity leg only — no stamp on an index).
      * Pre-entry capital is held as cash (each position contributes its £10k
        stake until its entry date), so both legs start at N×£10k and the
        comparison is like-for-like.

    Returns ``{}`` (renderer omits the panel) when ^FTAS prices or buy-signal
    firings are unavailable.
    """
    try:
        from eval_signals import _PAPER_BUY_SIGNALS as BUY_SIGNALS  # noqa: PLC0415
    except Exception:
        return {}
    if not BUY_SIGNALS:
        return {}

    # Audit fix 2026-06-06: mirror backtest.py's data-quality guards so a single
    # unadjusted corporate action (e.g. TIN 7.8p->£1.16, ~15x) or a known-bad
    # ticker can't manufacture fake "excess vs FTSE". Without these, the tracker
    # reused the entry/exit/cost spine but none of the protective filters.
    try:
        from backtest import (  # noqa: PLC0415
            EXCLUDED_TICKERS as _STRAT_EXCL_TICKERS,
            SPLIT_GUARD_MAX_RATIO as _STRAT_SG_MAX,
            SPLIT_GUARD_MIN_RATIO as _STRAT_SG_MIN,
        )
    except Exception:
        _STRAT_EXCL_TICKERS = ["HDD", "DCTA"]
        _STRAT_SG_MAX, _STRAT_SG_MIN = 4.0, 0.25

    ftas_rows = conn.execute(
        "SELECT date, close FROM prices WHERE ticker = ? AND close IS NOT NULL "
        "ORDER BY date ASC",
        (_FTAS_SYMBOL,),
    ).fetchall()
    if not ftas_rows:
        return {}
    axis = [r["date"] for r in ftas_rows]
    ftas_close = [float(r["close"]) for r in ftas_rows]
    # Cap the axis at `today` so the series never runs into future-dated rows.
    cut = bisect.bisect_right(axis, today.isoformat())
    axis, ftas_close = axis[:cut], ftas_close[:cut]
    if not axis:
        return {}

    n = len(axis)
    ticker_cache: dict = {}
    ff_cache: dict = {}

    def _price_series(ticker):
        if ticker not in ticker_cache:
            trows = conn.execute(
                "SELECT date, close FROM prices WHERE ticker = ? AND close IS NOT NULL "
                "ORDER BY date ASC",
                (ticker,),
            ).fetchall()
            ticker_cache[ticker] = (
                [x["date"] for x in trows],
                [float(x["close"]) for x in trows],
            )
        return ticker_cache[ticker]

    def _fetch_positions(signal_ids, min_value, exit_offset=_STRAT_EXIT_OFFSET):
        """Staked-position set for a signal subset (+ optional minimum
        transaction value). Applies the same excluded-ticker / excluded-issuer
        filters and split/consolidation guard as the all-buys book, so every
        line on the chart is computed on identical, artifact-free rules.
        Returns ``(positions, excluded_split_count)``.
        Phase B (Sprint 56): ``small_cap`` captured from the outer
        build_strategy_tracker scope; when set, restricts to that size band.
        """
        sig_q = ",".join("?" * len(signal_ids))
        ex_q = ",".join("?" * len(_STRAT_EXCL_TICKERS)) or "''"
        val_clause = " AND t.value > ?" if min_value is not None else ""
        # Sprint 56 Phase B: size-band filter.
        sc_clause = " AND COALESCE(tm.small_cap, -1) = ?" if small_cap is not None else ""
        params = [*signal_ids, *_STRAT_EXCL_TICKERS]
        if min_value is not None:
            params.append(min_value)
        if small_cap is not None:
            params.append(small_cap)
        prows = conn.execute(
            f"SELECT DISTINCT t.fingerprint, t.ticker, t.announced_at, t.date, "
            f"COALESCE(tm.is_aim, 0) AS is_aim "
            f"FROM signals s "
            f"JOIN transactions t ON t.fingerprint = s.fingerprint "
            f"LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
            f"WHERE s.signal_id IN ({sig_q}) "
            f"  AND t.ticker NOT IN ({ex_q}) "
            f"  AND COALESCE(tm.is_excluded_issuer, 0) != 1"
            f"{val_clause}"
            f"{sc_clause}",
            tuple(params),
        ).fetchall()
        out: list = []
        dropped = 0
        for r in prows:
            ticker = r["ticker"]
            ann = (r["announced_at"] or r["date"] or "")
            if not ticker or not ann:
                continue
            tdates, tcloses = _price_series(ticker)
            if not tdates:
                continue
            ei = bisect.bisect_right(tdates, ann[:10])
            if ei >= len(tdates):
                continue                          # no trading day after announcement
            entry_close = tcloses[ei]
            if not entry_close or entry_close <= 0:
                continue
            entry_date = tdates[ei]
            xi = ei + exit_offset
            if xi < len(tdates):
                exit_date, exit_close = tdates[xi], tcloses[xi]
            else:
                exit_date, exit_close = None, None  # younger than T+30 -> still open
            # Split/consolidation guard (mirror backtest B-095): drop positions
            # whose mark/entry price ratio implies an unadjusted corporate action.
            # Mark = realised exit if closed, else latest close <= today (also
            # caps a still-open consolidation / stale delisted mark).
            _mark = exit_close if exit_close is not None else _close_le(
                tdates, tcloses, today.isoformat())
            if _mark and entry_close > 0:
                _ratio = _mark / entry_close
                if _ratio > _STRAT_SG_MAX or _ratio < _STRAT_SG_MIN:
                    dropped += 1
                    continue
            is_aim = bool(r["is_aim"])
            eq_cost = _STRAT_SPREAD + (0.0 if is_aim else _STRAT_STAMP)
            shares = _STRAT_STAKE_GBP * (1.0 - eq_cost) / entry_close
            f_entry = _close_le(axis, ftas_close, entry_date)
            if not f_entry or f_entry <= 0:
                continue
            f_units = _STRAT_STAKE_GBP * (1.0 - _STRAT_SPREAD) / f_entry
            f_exit = _close_le(axis, ftas_close, exit_date) if exit_date else None
            out.append({
                "ticker": ticker, "entry_date": entry_date, "exit_date": exit_date,
                "shares": shares, "exit_close": exit_close,
                "f_units": f_units, "f_exit": f_exit,
            })
        return out, dropped

    def _daily_series(positions):
        """Daily £ mark (realised + open MTM; un-deployed stake held as cash)
        and the matched FTSE shadow, summed across ``positions``, on ``axis``."""
        strat_ = [0.0] * n
        ftse_ = [0.0] * n
        for p in positions:
            if p["ticker"] not in ff_cache:
                td, tc = ticker_cache[p["ticker"]]
                ff_cache[p["ticker"]] = _ff_on_axis(axis, td, tc)
            tff = ff_cache[p["ticker"]]
            ed, xd = p["entry_date"], p["exit_date"]
            sh, xc = p["shares"], p["exit_close"]
            fu, fx = p["f_units"], p["f_exit"]
            for i in range(n):
                d = axis[i]
                if d < ed:
                    strat_[i] += _STRAT_STAKE_GBP                 # cash, not yet deployed
                elif xd is not None and d >= xd:
                    strat_[i] += sh * xc                          # realised at T+30
                else:
                    c = tff[i]
                    strat_[i] += (sh * c) if c else _STRAT_STAKE_GBP  # open MTM
                if d < ed:
                    ftse_[i] += _STRAT_STAKE_GBP
                elif xd is not None and d >= xd and fx:
                    ftse_[i] += fu * fx
                else:
                    ftse_[i] += fu * ftas_close[i]
        return strat_, ftse_

    # All-buys book (back-compat: drives the existing £ series + summary tiles).
    positions, excluded_split = _fetch_positions(BUY_SIGNALS, None)
    if not positions:
        return {}
    strat, ftse = _daily_series(positions)

    # High-conviction tier lines, each with its own minimum buy value. Chosen by
    # economic prior, not by screening; small-N -> hypothesis-only, so each line
    # carries its own N for the reader. Same guard rules as the all-buys book.
    # T5 PCA uses a £5k floor (2026-06-07: PCA performance does not improve with
    # size — at £5k this is effectively the whole PCA cohort); CFO/Chair £100k.
    tier_defs = [
        ("t5",  "T5 PCA &gt;£5k",     ["t5_pca_buy"],     5_000.0),
        ("t1b", "T1B CFO &gt;£100k",  ["t1b_cfo_buy"],  100_000.0),
        ("t7",  "T7 Chair &gt;£100k", ["t7_chair_buy"], 100_000.0),
    ]
    tier_books: dict = {}
    tier_meta: dict = {}
    for _key, _label, _sids, _minv in tier_defs:
        _tpos, _ = _fetch_positions(_sids, _minv)
        tier_meta[_key] = {"label": _label, "n": len(_tpos)}
        if _tpos:
            _ts, _tf = _daily_series(_tpos)
            tier_books[_key] = (_ts, _tf)   # (strategy £, matched FTSE shadow £)
    tier_meta["all"] = {"label": "All buy signals", "n": len(positions)}

    def _trend30(arr: list):
        if len(arr) < 2:
            return None
        target = (datetime.fromisoformat(axis[-1]) - timedelta(days=30)).date().isoformat()
        k = bisect.bisect_right(axis, target) - 1
        if k < 0:
            k = 0
        base, cur = arr[k], arr[-1]
        if not base:
            return None
        return round((cur / base - 1.0) * 100.0, 1)

    series = [
        {"date": axis[i],
         "strategy_value_gbp": round(strat[i], 0),
         "ftse_value_gbp": round(ftse[i], 0)}
        for i in range(n)
    ]
    sv, fv = strat[-1], ftse[-1]
    summary = {
        "as_of":                  axis[-1],
        "n_positions":            len(positions),
        "capital_deployed_gbp":   round(len(positions) * _STRAT_STAKE_GBP, 0),
        "strategy_value_gbp":     round(sv, 0),
        "ftse_value_gbp":         round(fv, 0),
        "excess_gbp":             round(sv - fv, 0),
        "excess_pct":             round((sv / fv - 1.0) * 100.0, 2) if fv else None,
        "strategy_trend_30d_pct": _trend30(strat),
        "ftse_trend_30d_pct":     _trend30(ftse),
        # Audit fix 2026-06-06: positions dropped by the split/consolidation
        # guard (excluded tickers/issuers are filtered in SQL, before this).
        "excluded_split":         excluded_split,
    }

    # Cumulative-EXCESS-vs-FTSE lines for the multi-tier chart. Each book is
    # differenced against its OWN matched FTSE shadow (identical cash timing), so
    # the cash-drag of a partly-deployed book cancels out and the line is pure
    # alpha. Excess% at i = (strat[i] - shadow[i]) / base * 100, base = N×£10k
    # (both legs start there). The FTSE itself is therefore the flat 0% baseline:
    # above 0 = beating the market, below 0 = trailing it.
    def _excess_pct(s_arr, f_arr):
        base = s_arr[0] if s_arr and s_arr[0] else None
        if not base:
            return [None] * len(s_arr)
        return [round((s_arr[i] - f_arr[i]) / base * 100.0, 2)
                for i in range(len(s_arr))]

    all_x = _excess_pct(strat, ftse)
    tier_x = {k: _excess_pct(s, f) for k, (s, f) in tier_books.items()}
    pct_series = [
        {"date": axis[i], "all": all_x[i], "ftse": 0.0,
         "t5":  tier_x["t5"][i]  if "t5"  in tier_x else None,
         "t1b": tier_x["t1b"][i] if "t1b" in tier_x else None,
         "t7":  tier_x["t7"][i]  if "t7"  in tier_x else None}
        for i in range(n)
    ]
    # -----------------------------------------------------------------------
    # Multi-horizon + per-signal series for the interactive strategy chart
    # Loop over T+1/T+30/T+90/T+180/T+365; compute tier excess% and a line per
    # individual buy signal. Stored in pct_by_horizon so the renderer can
    # switch horizons and toggle signals fully client-side.
    # -----------------------------------------------------------------------
    _HORIZ_MAP = [("t1", 1), ("t30", 21), ("t90", 63), ("t180", 126), ("t365", 252)]
    _SIG_DEFS = [
        ("t1a_ceo_founder_buy",    "T1A CEO/Founder", "#1e40af"),
        ("t1b_cfo_buy",            "T1B CFO",         "#6d28d9"),
        ("t2_exec_buy",            "T2 Exec",         "#0e7490"),
        ("t3_ned_buy",             "T3 NED",          "#047857"),
        ("t4_other_buy",           "T4 Other",        "#4d7c0f"),
        ("t5_pca_buy",             "T5 PCA",          "#b45309"),
        ("t6_company_sec_buy",     "T6 Co Sec",       "#9a3412"),
        ("t7_chair_buy",           "T7 Chair",        "#f59e0b"),
        ("s1_cluster_buy",         "S1 Cluster",      "#dc2626"),
        ("f1_first_time_buy",      "F1 First Buy",    "#9333ea"),
        ("b1_lone_conviction_buy", "B1 Conviction",   "#0284c7"),
    ]
    pct_by_horizon: dict = {"dates": axis}
    signal_meta_out: dict = {}
    for _hk, _ho in _HORIZ_MAP:
        _ap, _ = _fetch_positions(BUY_SIGNALS, None, _ho)
        if not _ap:
            continue
        _as, _af = _daily_series(_ap)
        _te: dict = {"all": _excess_pct(_as, _af)}
        for _tk, _, _ts, _tv in tier_defs:
            _tp, _ = _fetch_positions(_ts, _tv, _ho)
            _te[_tk] = _excess_pct(*_daily_series(_tp)) if _tp else [None] * n
        _se: dict = {}
        for _sid, _slbl, _scol in _SIG_DEFS:
            _sp, _ = _fetch_positions([_sid], None, _ho)
            _se[_sid] = _excess_pct(*_daily_series(_sp)) if _sp else [None] * n
            if _hk == "t30":
                signal_meta_out[_sid] = {"label": _slbl, "color": _scol, "n": len(_sp)}
        pct_by_horizon[_hk] = {"tiers": _te, "signals": _se}
    for _sid, _slbl, _scol in _SIG_DEFS:
        signal_meta_out.setdefault(_sid, {"label": _slbl, "color": _scol, "n": 0})

    return {"series": series, "summary": summary,
            "pct_series": pct_series, "tier_meta": tier_meta,
            "pct_by_horizon": pct_by_horizon, "signal_meta": signal_meta_out}


def _build_band_signals_payload(
    conn,
    rows: list[dict],
    today: date,
    emit_timestamp: bool = True,
    small_cap: int | None = None,
) -> dict:
    """Sprint 56 Phase A — Build a signals.json-shaped payload for one size band.

    ``rows`` must already be pre-filtered to the target band (small_cap=1 or 0).
    ``small_cap`` is forwarded to ``build_strategy_tracker`` so the strategy
    tracker also shows only that band's positions.

    The returned dict has the same top-level structure as the ``signals``
    key of ``build_payload``'s return value.  It is written as
    ``signals_small.json`` / ``signals_large.json`` by ``run()``.
    """
    tickers_meta = _load_tickers_meta(conn)

    agg = aggregate_signals(rows, today)
    agg_all = aggregate_signals(rows, today, lookback_days=None)
    clusters = compute_active_clusters(conn, today)
    paper = paper_trade_stats(conn)
    paper_book = build_paper_book_summary(conn, today)
    capital_deployed = build_capital_deployed(conn, today)  # B-152
    base_rate_t30 = agg.get("t30", {}).get("base_rate", 50.0)
    cohorts_legacy = {
        "by_value_bucket": cohort_value_buckets(rows),
        "by_sector":       cohort_by_sector(rows, conn, today, base_rate_t30),
    }
    ticker_to_sector = {
        t: meta.get("sector")
        for t, meta in tickers_meta.items()
        if isinstance(meta, dict) and meta.get("sector")
    }
    cohorts_v2 = {
        "by_value_bucket": build_cohort_table(
            rows,
            group_fn=lambda r: _bucket_for_value(r.get("_value_gbp")),
            label_fn=lambda k: VALUE_BUCKET_LABELS.get(k, k),
            horizons=HORIZONS,
            lookbacks=LOOKBACKS,
            today=today,
            scope_filter_fn=lambda r: (
                r.get("signal_id") in HIGH_CONVICTION_NON_NED_SIGNALS
            ),
        ),
        "by_role": build_cohort_table(
            rows,
            group_fn=lambda r: classify_role(
                r.get("role_class"), r.get("role")
            ),
            label_fn=lambda k: ROLE_LABELS.get(k, k),
            horizons=HORIZONS,
            lookbacks=LOOKBACKS,
            today=today,
        ),
        "by_sector": build_cohort_table(
            rows,
            group_fn=lambda r: ticker_to_sector.get(r.get("ticker")),
            label_fn=lambda k: k,
            horizons=HORIZONS,
            lookbacks=LOOKBACKS,
            today=today,
        ),
    }
    monthly_buysell = build_monthly_buysell(conn, today, small_cap=small_cap)
    strategy_tracker = build_strategy_tracker(conn, today, small_cap=small_cap)

    payload: dict = {
        "schema_version":         SCHEMA_VERSION,
        "horizon_aggregates":     agg,
        "horizon_aggregates_all": agg_all,
        "active_clusters":        clusters,
        "cluster_brewing_trend":  build_cluster_brewing_trend(conn, today),
        "paper_pnl_open":         paper["paper_pnl_open"],
        "paper_trades_open":      paper["paper_trades_open"],
        "paper_trades_closed":    paper["paper_trades_closed"],
        "cohorts":                cohorts_legacy,
        "cohorts_v2":             cohorts_v2,
        "pending_diagnostics":    {},    # band pages don't show a pending panel
        "companies_index":        [],    # not needed on band pages
        "paper_book":             paper_book,
        "monthly_buysell":        monthly_buysell,
        "strategy_tracker":       strategy_tracker,
        "total_companies":        0,
        "pending_classification": 0,
        "capital_deployed":       capital_deployed,  # B-152
    }
    if emit_timestamp:
        payload = {"generated_at": _now_utc_iso(), **payload}
    return payload


def build_payload(conn, csv_path: Path, today: date | None = None,
                  emit_timestamp: bool = True,
                  pending_path: Path | None = None) -> dict:
    """Build all dashboard JSON payloads.

    Returns a dict keyed by output filename (stem):

        {
          "signals":             signals_payload (legacy cohorts + cohorts_v2),
          "dealings":            dealings_payload,
          "performance_bucket":  bucket drill-down payload,
          "performance_role":    role drill-down payload,
          "performance_sector":  sector drill-down payload,
        }

    Sprint 5 dual-emit migration: ``signals.cohorts`` (legacy two-key shape)
    stays byte-identical so the existing ``render_performance.py`` keeps
    rendering. ``signals.cohorts_v2`` is the new horizon × lookback × rows
    shape per spec §5.1. The three new ``performance_*.json`` files carry
    the §5.2 drill-down payloads for the new pages.
    """
    today = today or _today_utc()
    rows = load_backtest_csv(csv_path)

    # Load DB-derived lookups ONCE — reused across all payloads.
    tickers_meta = _load_tickers_meta(conn)
    tx_lookup = _load_tx_lookup(conn)

    # signals.json — horizon aggregates, clusters, paper trades, both
    # cohorts shapes side-by-side.
    # B-058 (2026-05-22): emit both the default 365d aggregates AND an
    # all-time variant so the scoreboard JS can toggle between them.
    # The recovered bundled-filing rows from Sprint 7 are mostly older
    # than 365d; without `horizon_aggregates_all` they're invisible on
    # the scoreboard.
    agg = aggregate_signals(rows, today)
    agg_all = aggregate_signals(rows, today, lookback_days=None)
    clusters = compute_active_clusters(conn, today)
    paper = paper_trade_stats(conn)
    # B-100 Phase A: read-only live paper book from signals + prices.
    paper_book = build_paper_book_summary(conn, today)
    # B-152: 13-week capital deployed trend (all / small / large).
    capital_deployed = build_capital_deployed(conn, today)
    base_rate_t30 = agg.get("t30", {}).get("base_rate", 50.0)
    cohorts_legacy = {
        "by_value_bucket": cohort_value_buckets(rows),
        "by_sector":       cohort_by_sector(rows, conn, today, base_rate_t30),
    }
    # cohorts_v2 — new spec §5.1 shape; horizon × lookback × rows per tile.
    ticker_to_sector = {
        t: meta.get("sector")
        for t, meta in tickers_meta.items()
        if isinstance(meta, dict) and meta.get("sector")
    }
    cohorts_v2 = {
        "by_value_bucket": build_cohort_table(
            rows,
            group_fn=lambda r: _bucket_for_value(r.get("_value_gbp")),
            label_fn=lambda k: VALUE_BUCKET_LABELS.get(k, k),
            horizons=HORIZONS,
            lookbacks=LOOKBACKS,
            today=today,
            scope_filter_fn=lambda r: (
                r.get("signal_id") in HIGH_CONVICTION_NON_NED_SIGNALS
            ),
        ),
        "by_role": build_cohort_table(
            rows,
            group_fn=lambda r: classify_role(
                r.get("role_class"), r.get("role")
            ),
            label_fn=lambda k: ROLE_LABELS.get(k, k),
            horizons=HORIZONS,
            lookbacks=LOOKBACKS,
            today=today,
        ),
        "by_sector": build_cohort_table(
            rows,
            group_fn=lambda r: ticker_to_sector.get(r.get("ticker")),
            label_fn=lambda k: k,
            horizons=HORIZONS,
            lookbacks=LOOKBACKS,
            today=today,
        ),
    }
    pending_diag = _compute_pending_diagnostics(
        pending_path=pending_path or DEFAULT_PENDING_PATH
    )
    # B-059 — companies index for the index-page search box.
    # B-184: url now points at the dynamic company template
    # (company.html?ticker=…, ticker URL-encoded) — static companies/{T}.html
    # pages are no longer generated. company_url() handles dotted tickers.
    _company_rows = conn.execute(
        "SELECT DISTINCT ticker, company FROM transactions "
        "WHERE ticker IS NOT NULL ORDER BY ticker"
    ).fetchall()
    companies_index = [
        {
            "ticker": r["ticker"],
            "company": r["company"] or "",
            "url": _h_const.company_url(r["ticker"]),
        }
        for r in _company_rows
    ]

    # B-151: holding-basket stats — total companies tracked + pending classification.
    # "total_companies" = distinct non-excluded tickers that appear in transactions.
    # "pending_classification" = those where small_cap IS NULL AND not excluded
    #   (i.e. the holding basket: either stub-only with no benchmark, or has signals
    #    fired but hasn't been size-classified yet).
    _basket_row = conn.execute("""
        SELECT
            COUNT(DISTINCT CASE WHEN COALESCE(tm.is_excluded_issuer, 0) != 1
                                 THEN t.ticker END) AS total_companies,
            COUNT(DISTINCT CASE WHEN COALESCE(tm.is_excluded_issuer, 0) != 1
                                      AND tm.small_cap IS NULL
                                 THEN t.ticker END) AS pending_classification
        FROM transactions t
        JOIN tickers_meta tm ON tm.ticker = t.ticker
    """).fetchone()
    total_companies = int(_basket_row["total_companies"] or 0) if _basket_row else 0
    pending_classification = int(_basket_row["pending_classification"] or 0) if _basket_row else 0

    # B-102: trailing 12-month buy/sell chart data.
    monthly_buysell = build_monthly_buysell(conn, today)

    # B-123: £10k-per-signal strategy tracker vs ^FTAS shadow.
    strategy_tracker = build_strategy_tracker(conn, today)

    # B-171 (revised): rolling-28-day Conviction Score top-10 panel + shadow log.
    # NOTE: this also upserts one conviction_scores row per buy in the trailing
    # 28-day window (the whole distribution) and commits.
    conviction_picks = build_conviction_picks(conn, today)

    signals_payload: dict = {
        "schema_version":     SCHEMA_VERSION,
        "horizon_aggregates": agg,
        # B-058 (2026-05-22): all-time variant for the scoreboard toggle.
        "horizon_aggregates_all": agg_all,
        "active_clusters":    clusters,
        "cluster_brewing_trend": build_cluster_brewing_trend(conn, today),  # B-132
        "paper_pnl_open":     paper["paper_pnl_open"],
        "paper_trades_open":  paper["paper_trades_open"],
        "paper_trades_closed": paper["paper_trades_closed"],
        "cohorts":            cohorts_legacy,    # legacy — DO NOT REMOVE
        "cohorts_v2":         cohorts_v2,        # Sprint 5 dual-emit
        "pending_diagnostics": pending_diag,
        "companies_index":    companies_index,   # B-059
        # B-100 Phase A: live paper book (read-only snapshot).
        "paper_book":         paper_book,
        # B-102: monthly activity chart data.
        "monthly_buysell":    monthly_buysell,
        # B-123: £10k/signal strategy tracker vs ^FTAS shadow.
        "strategy_tracker":   strategy_tracker,
        # B-151: holding-basket counter (unclassified companies).
        "total_companies":         total_companies,
        "pending_classification":  pending_classification,
        # B-152: 13-week capital deployed trend.
        "capital_deployed":        capital_deployed,
        # B-171 (revised): rolling-28-day Conviction Score (top-10, scored
        # regardless of bar). Derived view next to active_clusters —
        # deliberately NOT a signal_id, so it is kept OUT of SIGNAL_ORDER /
        # SIGNAL_SHORT / the JS SIDS array.
        "conviction_top10":         conviction_picks["top10"],
        "conviction_window_days":   conviction_picks["window_days"],
        "conviction_window_start":  conviction_picks["window_start"],
        "conviction_window_end":    conviction_picks["window_end"],
    }

    dealings_payload = build_dealings(conn, today, tickers_meta=tickers_meta)
    dealings_payload = {"schema_version": SCHEMA_VERSION, **dealings_payload}

    # Three drill-down payloads — Sprints 3 + 4 builders wired through here.
    bucket_payload = build_bucket_payload(
        rows, today, tx_lookup=tx_lookup, emit_timestamp=emit_timestamp,
    )
    role_payload = build_role_payload(
        rows, today, tx_lookup=tx_lookup, sector_map=tickers_meta,
        emit_timestamp=emit_timestamp,
    )
    sector_payload = build_sector_payload(
        rows, today, tickers_meta, tx_lookup=tx_lookup,
        emit_timestamp=emit_timestamp,
    )

    # B-127: announcement-date absolute stock return per fingerprint for the
    # cohort drill-down "Stock return" column. Computed here because the cohort
    # builder is otherwise price-free; shares one set of price caches.
    _abs_ann_dates: dict = {}
    _abs_ann_close: dict = {}
    _abs_ann_latest: dict = {}
    abs_return_ann_by_fp: dict = {}
    for _r in rows:
        _fp = _r.get("fingerprint")
        if not _fp:
            continue
        _txi = tx_lookup.get(_fp, {})
        _tkr = _r.get("ticker") or _txi.get("ticker") or ""
        _ann = _txi.get("announced_at") or ""
        abs_return_ann_by_fp[_fp] = _abs_return_from_announcement_pct(
            conn, _tkr, _ann, _abs_ann_dates, _abs_ann_close, _abs_ann_latest,
            date_field=(_r.get("_fired_at") or ""),
        )

    # B-066 (Sprint 14): additive cohort_performance.json blob.
    cohort_perf_payload = build_cohort_performance(
        rows, today, tx_lookup=tx_lookup, emit_timestamp=emit_timestamp,
        abs_return_ann_by_fp=abs_return_ann_by_fp,
    )

    if emit_timestamp:
        ts = _now_utc_iso()
        signals_payload  = {"generated_at": ts, **signals_payload}
        dealings_payload = {"generated_at": ts, **dealings_payload}

    return {
        "signals":             signals_payload,
        "dealings":            dealings_payload,
        "performance_bucket":  bucket_payload,
        "performance_role":    role_payload,
        "performance_sector":  sector_payload,
        "cohort_performance":  cohort_perf_payload,
    }


def _rns_id_from_url(url: str) -> str:
    """Extract the RNS numeric ID from an Investegate URL.

    ``https://www.investegate.co.uk/announcement/rns/.../9564925``
    → ``"9564925"``

    Returns ``""`` if the URL is empty, the tail is not purely digits,
    or the tail is fewer than 5 digits (real RNS IDs are always 5–12 digits;
    this prevents year numbers or other short numerics from being mistaken
    for RNS IDs — consistent with the server-side ``^\\d{5,12}$`` regex).
    """
    if not url:
        return ""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail if (tail.isdigit() and 5 <= len(tail) <= 12) else ""


def _load_resolved_rns_ids(data_dir: Path | None = None) -> set[str]:
    """Return the union of rejected and manually-added RNS IDs.

    Sprint 25 Phase 3/4 safety filter: items that apply_edits.py has already
    processed (either manually added or rejected) must not appear in the Tab A
    pending queue even if they haven't been purged from _pending_review.json yet
    (e.g. if apply_edits.py was run with --no-pipeline and the pipeline hasn't
    caught up). Belt-and-suspenders on top of remove_from_pending_queue().
    """
    data_dir = data_dir or (DEFAULT_PENDING_PATH.parent.parent / ".data")
    ids: set[str] = set()
    for fname, key in [
        ("_rejected_rns_ids.json", "rejected_rns_ids"),
        ("_manual_rns_ids.json",   "manual_rns_ids"),
    ]:
        p = data_dir / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ids.update(data.get(key) or [])
        except Exception:
            pass   # malformed manifest: fail open
    return ids


def build_pending_review_export(
    pending_path: Path | None = None,
    scrape_cache_dir: Path | None = None,
    generated_at: str | None = None,
) -> dict:
    """Build the ``pending_review.json`` export for the PDMR review surface.

    Returns a dict with:
    - ``total``       — total pending items (after filtering resolved)
    - ``generated_at``
    - ``buckets``     — list of {id, name, recoverable, count}
    - ``items``       — list of all items (rns_id, url, headline, bucket,
                        warnings, extracted, extracted_count, has_cache)

    ``extracted`` carries the first 3 parsed-transaction objects so the
    review UI can pre-fill the edit form without an extra fetch.

    Items already handled by apply_edits.py (rejected or manually added)
    are excluded as a safety filter — belt-and-suspenders on top of the
    in-place _pending_review.json cleanup that apply_edits.py performs.

    All remaining items are sorted: manually-recoverable buckets first,
    then v2 candidates, then hopeless, each group sorted by rns_id desc
    (newest first).
    """
    pending_path = pending_path or DEFAULT_PENDING_PATH
    scrape_cache_dir = scrape_cache_dir or (DEFAULT_PENDING_PATH.parent / "_scrape_cache")

    items_raw = _load_pending_items(pending_path)

    # Sprint 25 Phase 3/4: exclude items already resolved via apply_edits.py.
    resolved_ids = _load_resolved_rns_ids()
    if resolved_ids:
        before = len(items_raw)
        items_raw = {k: v for k, v in items_raw.items() if k not in resolved_ids}
        filtered = before - len(items_raw)
        if filtered:
            import sys as _sys
            print(f"[export] pending_review: filtered {filtered} already-resolved "
                  f"item(s) (rejected or manually added).", file=_sys.stderr)

    total = len(items_raw)
    generated_at = generated_at or _now_utc_iso()

    # Bucket priority for sort: manual first, then v2, then no/other.
    _BUCKET_PRIORITY = {
        "could_not_classify": 0,
        "corporate_actions":  1,
        "data_quirks":        2,
        "multi_tranche":      3,
        "other":              4,
        "foreign_currency":   5,
        "bundled_multi_pdmr": 6,
    }

    # Compute bucket counts and build item list.
    counts: dict[str, int] = {spec["id"]: 0 for spec in PENDING_BUCKET_SPEC}
    counts["other"] = 0
    item_list: list[dict] = []

    for rns_id, rec in items_raw.items():
        if not isinstance(rec, dict):
            continue
        warns = rec.get("warnings") or []
        bucket = _classify_pending_warnings(warns)
        counts[bucket] = counts.get(bucket, 0) + 1
        extracted = rec.get("extracted") or []
        url = rec.get("url") or ""
        has_cache = (scrape_cache_dir / (rns_id + ".html")).exists()
        extracted_export = extracted[:3]
        item_list.append({
            "rns_id":          rns_id,
            "url":             url,
            "headline":        rec.get("headline") or "",
            "bucket":          bucket,
            "warnings":        warns[:5],
            "extracted":       extracted_export,
            "extracted_count": len(extracted),
            "has_cache":       has_cache,
            "parser_source":   rec.get("parser_source") or "",
            "used_llm":        bool(rec.get("used_llm")),
        })

    # Sort: priority bucket, then rns_id desc (newest first).
    item_list.sort(key=lambda x: (
        _BUCKET_PRIORITY.get(x["bucket"], 99),
        -int(x["rns_id"]) if x["rns_id"].isdigit() else 0,
    ))

    # Bucket summary (ordered same as PENDING_BUCKET_SPEC + other).
    bucket_summary: list[dict] = []
    for spec in PENDING_BUCKET_SPEC:
        bucket_summary.append({
            "id":          spec["id"],
            "name":        spec["name"],
            "recoverable": spec["recoverable"],
            "count":       counts.get(spec["id"], 0),
        })
    bucket_summary.append({
        "id":          "other",
        "name":        "Other",
        "recoverable": "unknown",
        "count":       counts.get("other", 0),
    })

    return {
        "generated_at": generated_at,
        "total":        total,
        "buckets":      bucket_summary,
        "items":        item_list,
    }


def build_tx_index(conn, generated_at=None):
    """Build a thin transactions index for the PDMR review Tab B search."""
    generated_at = generated_at or _now_utc_iso()
    rows = conn.execute(
        """
        SELECT fingerprint, date, ticker, company, director, role,
               role_normalized, type, shares, price, value, url,
               announced_at, parser_source, buy_strictness
        FROM transactions
        ORDER BY date DESC, fingerprint
        """
    ).fetchall()

    tx_list: list[dict] = []
    for r in rows:
        url = r["url"] or ""
        tx_list.append({
            "fingerprint":    r["fingerprint"],
            "date":           r["date"],
            "ticker":         r["ticker"],
            "company":        r["company"] or "",
            "director":       r["director"] or "",
            "role":           r["role"] or "",
            "role_normalized": (r["role_normalized"]
                                if "role_normalized" in r.keys() else None),
            "type":           r["type"],
            "shares":         r["shares"],
            "price":          r["price"],
            "value":          r["value"],
            "url":            url,
            "rns_id":         _rns_id_from_url(url),
            "announced_at":   r["announced_at"] or "",
            "parser_source":  r["parser_source"] or "",
            "buy_strictness": r["buy_strictness"] or "",
        })

    return {
        "generated_at": generated_at,
        "count":        len(tx_list),
        "transactions": tx_list,
    }


def run(out_dir=DEFAULT_OUT_DIR,
        csv_path=DEFAULT_CSV_PATH,
        dry_run=False,
        emit_timestamp=True,
        today=None,
        verbose=False,
        pending_path=None):
    """Top-level entry point. Returns a summary dict."""
    if not csv_path.exists():
        raise SystemExit(
            "error: backtest CSV not found at " + str(csv_path) + ". "
            "Run backtest.py first."
        )
    conn = db.connect()
    try:
        n_signals = conn.execute(
            "SELECT COUNT(*) AS n FROM signals"
        ).fetchone()["n"]
        if n_signals == 0:
            raise SystemExit(
                "error: no rows in signals table. Run eval_signals.py first."
            )
        payloads = build_payload(
            conn, csv_path, today=today, emit_timestamp=emit_timestamp,
            pending_path=pending_path,
        )
    finally:
        conn.close()

    signals_payload     = payloads["signals"]
    dealings_payload    = payloads["dealings"]
    bucket_payload      = payloads["performance_bucket"]
    role_payload        = payloads["performance_role"]
    sector_payload      = payloads["performance_sector"]
    cohort_perf_payload = payloads["cohort_performance"]

    with csv_path.open(encoding="utf-8") as _csv_fh:
        n_csv_rows = sum(1 for _ in _csv_fh) - 1
    summary = {
        "n_signal_rows":     n_signals,
        "n_csv_rows":        n_csv_rows,
        "n_active_clusters": len(signals_payload["active_clusters"]),
        "n_today":           len(dealings_payload["today"]),
        "n_this_week":       len(dealings_payload["this_week"]),
        "n_buckets":         len(bucket_payload.get("buckets", {})),
        "n_roles":           len(role_payload.get("roles", {})),
        "n_sectors":         len(sector_payload.get("sectors", {})),
        "n_cohort_groups":   len(cohort_perf_payload.get("groups", {})),
    }
    if verbose:
        print(json.dumps(summary, indent=2))

    if dry_run:
        return summary

    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(out_dir / "signals.json",            signals_payload)
    _atomic_write_json(out_dir / "dealings.json",           dealings_payload)
    _atomic_write_json(out_dir / "performance_bucket.json", bucket_payload)
    _atomic_write_json(out_dir / "performance_role.json",   role_payload)
    _atomic_write_json(out_dir / "performance_sector.json", sector_payload)
    _atomic_write_json(out_dir / "cohort_performance.json", cohort_perf_payload)

    _review_ts = _now_utc_iso() if emit_timestamp else None
    _pending_export = build_pending_review_export(
        pending_path=pending_path or DEFAULT_PENDING_PATH,
        generated_at=_review_ts,
    )
    _atomic_write_json(out_dir / "pending_review.json", _pending_export)

    _conn2 = db.connect()
    try:
        _tx_index = build_tx_index(_conn2, generated_at=_review_ts)
    finally:
        _conn2.close()
    _atomic_write_json(out_dir / "tx_index.json", _tx_index)

    summary["n_pending_items"] = _pending_export["total"]
    summary["n_tx_index"]      = _tx_index["count"]

    # Sprint 56 Phase A: write per-band signals JSON (small_cap=1 / small_cap=0).
    # A third connection is used so the band builds are fully isolated from the
    # main build_payload connection (already closed above).
    _today_band = today or _today_utc()
    _all_rows = load_backtest_csv(csv_path)
    _small_rows = [r for r in _all_rows if r.get("small_cap") == "1"]
    _large_rows = [r for r in _all_rows if r.get("small_cap") == "0"]
    _conn3 = db.connect()
    try:
        _sig_small = _build_band_signals_payload(
            _conn3, _small_rows, _today_band,
            emit_timestamp=emit_timestamp, small_cap=1,
        )
        _sig_large = _build_band_signals_payload(
            _conn3, _large_rows, _today_band,
            emit_timestamp=emit_timestamp, small_cap=0,
        )
    finally:
        _conn3.close()
    _atomic_write_json(out_dir / "signals_small.json", _sig_small)
    _atomic_write_json(out_dir / "signals_large.json", _sig_large)
    # Sprint 56 Phase A fix: build band-specific cohort_performance blobs so
    # the signal-overview mini-charts and Level-2 cohort charts on the band
    # pages show only that band's data (not combined).
    # tx_lookup / abs_return_ann_by_fp are optional enrichments used for
    # drill-down director names and abs-return annotation; they are not
    # required for the mini sparklines and are skipped here for simplicity.
    _cohort_perf_small = build_cohort_performance(
        _small_rows, _today_band, emit_timestamp=emit_timestamp,
    )
    _cohort_perf_large = build_cohort_performance(
        _large_rows, _today_band, emit_timestamp=emit_timestamp,
    )
    _atomic_write_json(out_dir / "cohort_performance_small.json", _cohort_perf_small)
    _atomic_write_json(out_dir / "cohort_performance_large.json", _cohort_perf_large)
    summary["n_small_rows"] = len(_small_rows)
    summary["n_large_rows"] = len(_large_rows)

    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="Export dashboard JSON.")
    p.add_argument("--dry-run",      action="store_true")
    p.add_argument("--no-timestamp", action="store_true",
                   help="Omit generated_at -- for round-trip diff tests.")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), type=str)
    p.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH), type=str)
    p.add_argument("--pending-path", default=str(DEFAULT_PENDING_PATH),
                   type=str,
                   help="Path to _pending_review.json for diagnostics panel.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    summary = run(
        out_dir=Path(args.out_dir),
        csv_path=Path(args.csv_path),
        dry_run=args.dry_run,
        emit_timestamp=not args.no_timestamp,
        verbose=args.verbose,
        pending_path=Path(args.pending_path),
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
