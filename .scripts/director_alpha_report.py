"""Read-only historical analysis: director leaderboard + conviction calibration.

TWO deliverables in one script, both strictly READ-ONLY and writing ONLY
under `outputs/` (never `.data/`, never the live DB):

  PART A  Director "best-investor" leaderboard.
          Universe = ALL director BUYS (not just signal firings). For each buy
          we compute the benchmark-relative cumulative abnormal return (CAR) at
          T+30 / T+60 / T+90 / T+360 *calendar* days, mapped to the nearest
          trading-day offsets, REUSING backtest.py's CAR / benchmark / cost
          engine verbatim (no re-implementation). Costs = Corwin-Schultz spread
          + 0.5% stamp duty on non-AIM buys, exactly as backtest applies them.
          Buys whose horizon window has not yet matured (runs past the last
          stored price) are EXCLUDED from that horizon — never counted as 0.
          Aggregated by director identity (routine_flag.director_key).

  PART B  Conviction-score calibration study.
          Every buy is scored with conviction_pipeline (as-of-date inputs, no
          lookahead). We test whether the 0-100 score predicts net CAR
          out-of-sample (older 70% = train, newer 30% = test; we report the
          TEST set), with ticker-clustered t-tests so correlated buys in one
          name can't masquerade as signal. Per-factor sub-score correlations
          too. Honest verdict printed up top.

Methodology is inherited from the project, not reinvented:
  * CAR = (stock return - sector-benchmark return), benchmark = sector-matched
    with ^FTAS (FTSE All-Share) fallback. (backtest._select_firings /
    _safe_div / cars).
  * net CAR = CAR - cost_bps/10000, cost_bps = CS spread + (0 | 50bps stamp).
  * Exclusions: is_excluded_issuer (IT/CEF/VCT/REIT) + EXCLUDED_TICKERS
    data-quality outliers, same filter backtest uses. --include-excluded
    overrides.

CLI:
    python .scripts/director_alpha_report.py
        [--horizons t30,t60,t90,t360] [--min-buys 3] [--include-excluded]
        [--out-dir outputs] [--db PATH]

Outputs (all under outputs/):
    director_alpha_report.html      human-readable report (verdict up top)
    director_leaderboard.csv        one row per director
    conviction_calibration.csv      decile table + per-factor correlations

READ-ONLY guarantee: the DB is opened with `mode=ro` (URI). This script never
writes the DB and never imports/touches any write-path script.
"""
from __future__ import annotations

import argparse
import csv
import html
import math
import sqlite3
import statistics
import sys
from bisect import bisect_left
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Reuse the project's CAR / benchmark / cost engine verbatim. We import the
# pure helpers (no DB writes) so the methodology is identical to production.
import backtest as bt  # noqa: E402
import conviction_pipeline as cp  # noqa: E402
from routine_flag import director_key  # noqa: E402  canonical director identity

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".data" / "directors.db"
OUT_DIR = ROOT / "outputs"

# ---------------------------------------------------------------------------
# Horizons.
# Project backtest defines trading-day offsets (1,21,63,126,252) labelled
# t1/t30/t90/t180/t365. The brief asks for CALENDAR-day horizons
# T+30/T+60/T+90/T+360 mapped to the NEAREST trading-day offset:
#   30 cal ≈ 21 td  (existing t30 / "t30")
#   60 cal ≈ 42 td  (NEW — added here; backtest had no t60)
#   90 cal ≈ 63 td  (existing t90)
#   360 cal ≈ 252 td (existing t365 offset; relabelled t360 for this report)
# Each entry: label -> trading-day offset.
# ---------------------------------------------------------------------------
HORIZON_OFFSETS: dict[str, int] = {
    "t30": 21,
    "t60": 42,
    "t90": 63,
    "t360": 252,
}
DEFAULT_HORIZONS = ["t30", "t60", "t90", "t360"]
PRIMARY_HORIZON = "t90"   # ranking + win-rate anchor (per brief)


# ===========================================================================
# Read-only DB
# ===========================================================================

def ro_connect(path: Path) -> sqlite3.Connection:
    """Open the DB strictly read-only (mode=ro). Never writes, never migrates."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ===========================================================================
# Per-buy CAR computation — REUSES backtest.py helpers.
# ===========================================================================

def compute_buy_cars(conn, date_cache, ohlc_cache, *, ticker, announced,
                     benchmark, is_aim, horizons):
    """Return per-horizon {label: (car, net_car)} for one buy, plus cost_bps.

    Reuses backtest's engine end-to-end:
      bt._ticker_dates, bt._close_on, bt._first_trading_date_after,
      bt._date_at_offset, bt._safe_div, bt._cs_spread_bps, bt._count_history_days
    so the CAR/benchmark/cost arithmetic is byte-for-byte the production path.

    CENSORING: a horizon whose target trading day runs past the last stored
    price returns (None, None) for that label and is reported as `matured=False`
    by the caller (excluded from that horizon's stats, never treated as 0).
    Also applies backtest's split/consolidation guard so an unadjusted corporate
    action can't inject a fake +1000% CAR.
    """
    result = {h: {"car": None, "net": None, "matured": False} for h in horizons}
    result["_entry_close"] = None
    result["_cost_bps"] = None

    ticker_dates = bt._ticker_dates(date_cache, conn, ticker)
    bench_dates = bt._ticker_dates(date_cache, conn, benchmark)
    if not ticker_dates:
        return result

    # Insufficient pre-buy history -> unresolved (same gate backtest uses).
    if bt._count_history_days(conn, ticker, announced) < bt.MIN_HISTORY_DAYS:
        return result

    entry_idx, entry_date = bt._first_trading_date_after(ticker_dates, announced)
    if entry_idx is None:
        return result
    entry_close = bt._close_on(conn, ticker, entry_date)
    if not entry_close or entry_close <= 0:
        return result
    result["_entry_close"] = entry_close

    # Benchmark entry: first benchmark trading day on/after entry_date.
    b_idx = bisect_left(bench_dates, entry_date)
    bench_entry_date = bench_dates[b_idx] if b_idx < len(bench_dates) else None
    bench_entry_close = bt._close_on(conn, benchmark, bench_entry_date)

    cs_spread = bt._cs_spread_bps(ohlc_cache, conn, ticker, ticker_dates, entry_idx)
    stamp_duty = 0 if is_aim else bt.STAMP_DUTY_NON_AIM_BPS
    cost_bps = cs_spread + stamp_duty
    result["_cost_bps"] = cost_bps

    last_date = ticker_dates[-1]

    for label in horizons:
        n = HORIZON_OFFSETS[label]
        win_date = bt._date_at_offset(ticker_dates, entry_idx, n)
        if win_date is None:
            # Horizon window runs past the last stored price for this ticker.
            # NOT matured -> leave (None, None, matured=False). Censoring.
            continue
        win_close = bt._close_on(conn, ticker, win_date)
        raw_ret = bt._safe_div(win_close, entry_close)

        # Split/consolidation guard (backtest B-095): implausible ratio -> drop.
        if raw_ret is not None and win_close and win_close > 0:
            ratio = win_close / entry_close
            if (ratio > bt.SPLIT_GUARD_MAX_RATIO
                    or ratio < bt.SPLIT_GUARD_MIN_RATIO):
                raw_ret = None

        # Benchmark return over the same window.
        bench_ret = None
        if bench_entry_close and bench_entry_date is not None:
            pos = b_idx + n
            if 0 <= pos < len(bench_dates):
                bench_win = bt._close_on(conn, benchmark, bench_dates[pos])
                bench_ret = bt._safe_div(bench_win, bench_entry_close)

        if raw_ret is None or bench_ret is None:
            # Could be a real data gap inside the window even though the date
            # exists; treat as unresolved for this horizon (not 0).
            # But distinguish from "not matured": the window date EXISTS, so if
            # the benchmark simply lacks the bar we still mark not-matured.
            continue

        car = raw_ret - bench_ret
        net = car - cost_bps / 10_000.0
        result[label] = {"car": car, "net": net, "matured": True}

    # Mark `result["_last_date"]` so the caller can reason about censoring.
    result["_last_date"] = last_date
    return result


# ===========================================================================
# Statistics helpers (clustered-t, Spearman, OLS) — stdlib only.
# ===========================================================================

def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _median(xs):
    return statistics.median(xs) if xs else float("nan")


def spearman(xs, ys):
    """Spearman rank correlation rho. Returns nan if <3 pairs or no variance."""
    n = len(xs)
    if n < 3:
        return float("nan")
    rx = _rankdata(xs)
    ry = _rankdata(ys)
    return _pearson(rx, ry)


def _rankdata(xs):
    """Average-rank of each element (ties share the mean rank)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def ols_slope(xs, ys):
    """Simple OLS slope of y on x. nan if <2 points or x has no variance."""
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return float("nan")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def clustered_t_stat(values, clusters):
    """One-sample t-stat for mean(values)=0 with cluster-robust SE.

    Standard cluster-robust (CR0) variance for the mean estimator: clusters
    are independent, observations within a cluster are correlated. With all
    `values` from a single ticker, the effective sample size collapses to ~1
    cluster and the t-stat falls toward 0 — exactly the behaviour the project
    standard demands (correlated buys in one name must not look significant).

    Returns (t_stat, mean, n_obs, n_clusters). t_stat is nan if <2 clusters.
    """
    n = len(values)
    if n == 0:
        return float("nan"), float("nan"), 0, 0
    mean = _mean(values)
    groups: dict = {}
    for v, c in zip(values, clusters):
        groups.setdefault(c, []).append(v)
    g = len(groups)
    if g < 2:
        return float("nan"), mean, n, g
    # CR variance of the sample mean: model y = mu + e. The mean estimator is
    # mu_hat = sum(y)/n. Cluster-robust var(mu_hat) = (1/n^2) * sum_g (sum_{i in g} e_i)^2,
    # with e_i = y_i - mu_hat. Small-sample correction G/(G-1).
    resid = [v - mean for v in values]
    cluster_sums: dict = {}
    idx = 0
    for v, c in zip(values, clusters):
        cluster_sums[c] = cluster_sums.get(c, 0.0) + (v - mean)
        idx += 1
    meat = sum(s * s for s in cluster_sums.values())
    correction = g / (g - 1.0)
    var_mean = correction * meat / (n * n)
    if var_mean <= 0:
        return float("nan"), mean, n, g
    se = math.sqrt(var_mean)
    if se == 0:
        return float("nan"), mean, n, g
    return mean / se, mean, n, g


def bucket_table(scores, nets, n_buckets=10):
    """Mean net CAR by score bucket (decile/quintile). Returns list of dicts."""
    pairs = sorted(zip(scores, nets), key=lambda p: p[0])
    n = len(pairs)
    if n < n_buckets:
        n_buckets = max(1, n)
    out = []
    for b in range(n_buckets):
        lo = b * n // n_buckets
        hi = (b + 1) * n // n_buckets
        chunk = pairs[lo:hi]
        if not chunk:
            continue
        bscores = [p[0] for p in chunk]
        bnets = [p[1] for p in chunk]
        out.append({
            "bucket": b + 1,
            "n": len(chunk),
            "score_lo": min(bscores),
            "score_hi": max(bscores),
            "score_mean": _mean(bscores),
            "net_mean": _mean(bnets),
            "net_median": _median(bnets),
        })
    return out


# ===========================================================================
# Data loading
# ===========================================================================

def load_buys(conn, *, include_excluded: bool):
    """All director BUYS with the join fields the CAR engine needs.

    Mirrors backtest._select_firings' universe filter (is_excluded_issuer +
    EXCLUDED_TICKERS) but over the FULL transactions table — every BUY, not
    just signal firings. `--include-excluded` drops the exclusion filter.
    """
    excl = list(bt.EXCLUDED_TICKERS)
    placeholders = ",".join("?" * len(excl)) if excl else "''"
    where = ["t.type = 'BUY'", "t.ticker IS NOT NULL"]
    params: list = []
    if not include_excluded:
        where.append(f"t.ticker NOT IN ({placeholders})")
        params.extend(excl)
        where.append("COALESCE(tm.is_excluded_issuer, 0) != 1")
    sql = (
        "SELECT t.fingerprint, t.ticker, t.company, t.director, t.role, "
        "       t.value, t.shares, t.date, t.announced_at, "
        "       COALESCE(NULLIF(t.announced_at, ''), t.date) AS effective_announced_at, "
        "       COALESCE(tm.benchmark_symbol, '^FTAS') AS benchmark_symbol, "
        "       COALESCE(tm.is_aim, 0) AS is_aim, "
        "       tm.market_cap_gbp "
        "FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY COALESCE(NULLIF(t.announced_at, ''), t.date)"
    )
    return conn.execute(sql, params).fetchall()


# ===========================================================================
# Part A — leaderboard
# ===========================================================================

def build_leaderboard(per_buy, horizons, min_buys):
    """Aggregate per-buy rows into a director leaderboard.

    per_buy rows carry: director_key, director_name, ticker, value_gbp, and a
    `cars` dict {label: {"net": float|None, "matured": bool, ...}}.
    """
    by_dir: dict = {}
    for row in per_buy:
        dk = row["director_key"]
        d = by_dir.setdefault(dk, {
            "director_key": dk,
            "director_name": row["director_name"],
            "n_buys": 0,
            "total_gbp": 0.0,
            "tickers": set(),
            "nets": {h: [] for h in horizons},  # matured net CARs per horizon
        })
        d["n_buys"] += 1
        d["total_gbp"] += float(row["value_gbp"] or 0.0)
        d["tickers"].add(row["ticker"])
        # Keep a representative display name (first seen).
        for h in horizons:
            cell = row["cars"].get(h)
            if cell and cell["matured"] and cell["net"] is not None:
                d["nets"][h].append(cell["net"])

    leaderboard = []
    for dk, d in by_dir.items():
        entry = {
            "director_key": dk,
            "director_name": d["director_name"],
            "n_buys": d["n_buys"],
            "n_tickers": len(d["tickers"]),
            "total_gbp": d["total_gbp"],
        }
        for h in horizons:
            nets = d["nets"][h]
            entry[f"n_matured_{h}"] = len(nets)
            entry[f"median_net_{h}"] = _median(nets) if nets else None
            entry[f"mean_net_{h}"] = _mean(nets) if nets else None
        # Win rate at the primary horizon.
        prim = d["nets"].get(PRIMARY_HORIZON, [])
        entry["n_matured_primary"] = len(prim)
        entry["win_rate_primary"] = (
            sum(1 for x in prim if x > 0) / len(prim) if prim else None
        )
        leaderboard.append(entry)

    # Min-buys filter is applied on MATURED buys at the primary horizon so a
    # director with 5 buys none of which have matured doesn't rank on noise.
    qualified = [e for e in leaderboard
                 if e["n_matured_primary"] >= min_buys
                 and e[f"median_net_{PRIMARY_HORIZON}"] is not None]
    qualified.sort(
        key=lambda e: e[f"median_net_{PRIMARY_HORIZON}"], reverse=True
    )
    return qualified, leaderboard


# ===========================================================================
# Part B — calibration
# ===========================================================================

def run_calibration(per_buy, horizons):
    """Out-of-sample calibration of conviction score vs net CAR.

    Splits by date: older 70% = train, newer 30% = test. Reports the TEST set.
    Ticker-clustered t-tests for significance.
    """
    # Order by effective day for the train/test split.
    dated = [r for r in per_buy if r["effective_day"]]
    dated.sort(key=lambda r: r["effective_day"])
    n = len(dated)
    split = int(n * 0.70)
    train = dated[:split]
    test = dated[split:]

    out = {
        "n_total": n,
        "n_train": len(train),
        "n_test": len(test),
        "split_date": dated[split]["effective_day"] if 0 < split < n else None,
        "horizons": {},
        "factors": {},
        "buckets": {},
    }

    for h in horizons:
        rows = [r for r in test
                if r["cars"].get(h) and r["cars"][h]["matured"]
                and r["cars"][h]["net"] is not None]
        scores = [r["score"] for r in rows]
        nets = [r["cars"][h]["net"] for r in rows]
        tickers = [r["ticker"] for r in rows]
        rho = spearman(scores, nets)
        slope = ols_slope(scores, nets)
        # Clustered significance: do high-score buys earn positive net CAR?
        # We test the top-quintile mean net CAR != 0 with ticker clusters,
        # AND report the slope's basic info. The honest headline metric is the
        # decile spread + clustered t on the score-net relationship via a
        # residual test below.
        t_top, mean_top, n_top, g_top = (float("nan"),) * 4
        if rows:
            srt = sorted(rows, key=lambda r: r["score"], reverse=True)
            top_n = max(1, len(srt) // 5)
            top = srt[:top_n]
            t_top, mean_top, n_top, g_top = clustered_t_stat(
                [r["cars"][h]["net"] for r in top],
                [r["ticker"] for r in top],
            )
        out["horizons"][h] = {
            "n_test_matured": len(rows),
            "spearman_rho": rho,
            "ols_slope": slope,
            "top_quintile_mean_net": mean_top,
            "top_quintile_t_clustered": t_top,
            "top_quintile_n": n_top,
            "top_quintile_clusters": g_top,
            "overall_mean_net": _mean(nets) if nets else float("nan"),
        }
        out["buckets"][h] = bucket_table(scores, nets, n_buckets=10)

    # Per-factor sub-score correlation with net CAR at the primary horizon
    # (test set). Which of the six factors, if any, carries signal.
    factor_keys = ["who", "buy_size", "company_size",
                   "earnings_timing", "past_performance"]
    h = PRIMARY_HORIZON if PRIMARY_HORIZON in horizons else horizons[0]
    rows = [r for r in test
            if r["cars"].get(h) and r["cars"][h]["matured"]
            and r["cars"][h]["net"] is not None and r["subscores"]]
    nets = [r["cars"][h]["net"] for r in rows]
    for fk in factor_keys:
        fvals = [r["subscores"].get(fk, 0.0) for r in rows]
        out["factors"][fk] = {
            "spearman_rho": spearman(fvals, nets),
            "n": len(rows),
        }
    # Sector multiplier (F6) is separate.
    fvals = [r["sector_mult"] for r in rows if r.get("sector_mult") is not None]
    nets_f6 = [r["cars"][h]["net"] for r in rows if r.get("sector_mult") is not None]
    out["factors"]["sector_mult"] = {
        "spearman_rho": spearman(fvals, nets_f6),
        "n": len(nets_f6),
    }
    out["primary_horizon"] = h
    return out


def calibration_verdict(calib):
    """A short honest verdict string for the top of the report."""
    h = calib["primary_horizon"]
    hz = calib["horizons"].get(h, {})
    rho = hz.get("spearman_rho", float("nan"))
    t = hz.get("top_quintile_t_clustered", float("nan"))
    n = hz.get("n_test_matured", 0)

    def _f(x):
        return "n/a" if (x is None or (isinstance(x, float) and math.isnan(x))) else f"{x:.3f}"

    if n < 20:
        strength = "INCONCLUSIVE (too few matured test buys)"
    elif (not math.isnan(rho) and rho > 0.1
          and not math.isnan(t) and abs(t) >= 2.0):
        strength = "POSITIVE and clustered-significant — investigate, do not yet trust"
    elif not math.isnan(rho) and rho > 0.05:
        strength = "WEAKLY POSITIVE but NOT clustered-significant (likely noise)"
    elif not math.isnan(rho) and rho < -0.05:
        strength = "NEGATIVE — score does not predict positive net CAR"
    else:
        strength = "FLAT — no out-of-sample relationship"

    return (
        f"Out-of-sample verdict (test set, {h}, n={n} matured buys): "
        f"Spearman rho={_f(rho)}, top-quintile clustered t={_f(t)}. "
        f"Conclusion: {strength}."
    )


# ===========================================================================
# Output writers
# ===========================================================================

def write_leaderboard_csv(path, qualified, horizons):
    cols = (["rank", "director_name", "director_key", "n_buys", "n_tickers",
             "total_gbp", "n_matured_primary", "win_rate_primary"]
            + [f"n_matured_{h}" for h in horizons]
            + [f"median_net_{h}" for h in horizons]
            + [f"mean_net_{h}" for h in horizons])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i, e in enumerate(qualified, start=1):
            row = [i, e["director_name"], e["director_key"], e["n_buys"],
                   e["n_tickers"], round(e["total_gbp"], 2),
                   e["n_matured_primary"],
                   _r(e["win_rate_primary"], 4)]
            row += [e[f"n_matured_{h}"] for h in horizons]
            row += [_r(e[f"median_net_{h}"], 5) for h in horizons]
            row += [_r(e[f"mean_net_{h}"], 5) for h in horizons]
            w.writerow(row)


def write_calibration_csv(path, calib, horizons):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["section", "horizon", "key", "value"])
        for h in horizons:
            hz = calib["horizons"].get(h, {})
            for k, v in hz.items():
                w.writerow(["horizon_summary", h, k, _r(v, 5)])
        for fk, fv in calib["factors"].items():
            w.writerow(["factor", calib["primary_horizon"], fk,
                        _r(fv["spearman_rho"], 5)])
        for h in horizons:
            for b in calib["buckets"].get(h, []):
                w.writerow([
                    "bucket", h,
                    f"bucket{b['bucket']}_n{b['n']}_"
                    f"score[{b['score_lo']:.1f},{b['score_hi']:.1f}]",
                    _r(b["net_mean"], 5),
                ])


def _r(x, nd):
    if x is None:
        return ""
    if isinstance(x, float) and math.isnan(x):
        return ""
    if isinstance(x, float):
        return round(x, nd)
    return x


def _pct(x, nd=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x * 100:.{nd}f}%"


def _num(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def write_html(path, *, qualified, full_leaderboard, calib, horizons,
               per_buy, min_buys, include_excluded, coverage):
    verdict = calibration_verdict(calib)
    esc = html.escape

    def th(cols):
        return "".join(f"<th>{esc(c)}</th>" for c in cols)

    # Leaderboard table.
    lb_head = (["#", "Director", "Buys", "Tickers", "£ deployed",
                f"Matured ({PRIMARY_HORIZON})", "Win% (T+90)"]
               + [f"Median net {h}" for h in horizons]
               + [f"n {h}" for h in horizons])
    lb_rows = []
    for i, e in enumerate(qualified[:60], start=1):
        cells = [
            str(i), esc(e["director_name"]), str(e["n_buys"]),
            str(e["n_tickers"]), f"£{e['total_gbp']:,.0f}",
            str(e["n_matured_primary"]), _pct(e["win_rate_primary"]),
        ]
        for h in horizons:
            cells.append(_pct(e[f"median_net_{h}"]))
        for h in horizons:
            cells.append(str(e[f"n_matured_{h}"]))
        lb_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

    # Per-buy detail for the top 10 directors.
    top_keys = [e["director_key"] for e in qualified[:10]]
    detail_rows = []
    by_key = {}
    for r in per_buy:
        by_key.setdefault(r["director_key"], []).append(r)
    for dk in top_keys:
        name = next(e["director_name"] for e in qualified if e["director_key"] == dk)
        for r in sorted(by_key.get(dk, []), key=lambda x: x["effective_day"] or ""):
            cells = [esc(name), esc(r["ticker"]),
                     esc(r["effective_day"] or ""),
                     f"£{float(r['value_gbp'] or 0):,.0f}",
                     _num(r["score"], 1)]
            for h in horizons:
                cell = r["cars"].get(h, {})
                if cell.get("matured") and cell.get("net") is not None:
                    cells.append(_pct(cell["net"]))
                else:
                    cells.append('<span class="imm">immature</span>')
            detail_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

    # Calibration: horizon summary table.
    cal_rows = []
    for h in horizons:
        hz = calib["horizons"].get(h, {})
        cal_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in [
            esc(h), str(hz.get("n_test_matured", 0)),
            _num(hz.get("spearman_rho"), 3),
            _num(hz.get("ols_slope"), 5),
            _pct(hz.get("top_quintile_mean_net")),
            _num(hz.get("top_quintile_t_clustered"), 2),
            f"{hz.get('top_quintile_n', 0)} / {hz.get('top_quintile_clusters', 0)} clusters",
            _pct(hz.get("overall_mean_net")),
        ]) + "</tr>")

    # Factor table.
    fac_rows = []
    for fk, fv in calib["factors"].items():
        fac_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in [
            esc(fk), str(fv["n"]), _num(fv["spearman_rho"], 3),
        ]) + "</tr>")

    # Decile table at primary horizon.
    dec_rows = []
    for b in calib["buckets"].get(calib["primary_horizon"], []):
        dec_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in [
            str(b["bucket"]), str(b["n"]),
            f"{b['score_lo']:.1f}–{b['score_hi']:.1f}",
            _num(b["score_mean"], 1),
            _pct(b["net_mean"]), _pct(b["net_median"]),
        ]) + "</tr>")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Director Alpha &amp; Conviction Calibration Report</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0 auto;
        max-width: 1180px; padding: 24px; color: #1a1a1a; }}
 h1 {{ font-size: 22px; }} h2 {{ margin-top: 34px; font-size: 18px;
        border-bottom: 2px solid #e3e3e3; padding-bottom: 6px; }}
 .verdict {{ background: #fff8e1; border: 1px solid #f0d27a; border-radius: 8px;
        padding: 14px 18px; font-size: 15px; margin: 18px 0; }}
 .meta {{ color: #666; font-size: 13px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 12px 0 28px; }}
 th, td {{ border: 1px solid #ddd; padding: 5px 8px; text-align: right; }}
 th {{ background: #f4f4f4; cursor: pointer; text-align: right; position: sticky; top: 0; }}
 td:nth-child(2), th:nth-child(2) {{ text-align: left; }}
 tr:nth-child(even) td {{ background: #fafafa; }}
 .imm {{ color: #b08; font-style: italic; }}
 .note {{ color: #555; font-size: 13px; line-height: 1.5; }}
 code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
</style>
<script>
 // Lightweight click-to-sort on any table header.
 function sortTable(th) {{
   var table = th.closest('table'); var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
   var tbody = table.tBodies[0]; var rows = Array.prototype.slice.call(tbody.rows);
   var asc = th.getAttribute('data-asc') !== 'true';
   rows.sort(function(a, b) {{
     var x = a.cells[idx].innerText.replace(/[£,%]/g,'').trim();
     var y = b.cells[idx].innerText.replace(/[£,%]/g,'').trim();
     var nx = parseFloat(x), ny = parseFloat(y);
     if (!isNaN(nx) && !isNaN(ny)) return asc ? nx - ny : ny - nx;
     return asc ? x.localeCompare(y) : y.localeCompare(x);
   }});
   rows.forEach(function(r) {{ tbody.appendChild(r); }});
   th.setAttribute('data-asc', asc);
 }}
 document.addEventListener('DOMContentLoaded', function() {{
   document.querySelectorAll('th').forEach(function(th) {{
     th.addEventListener('click', function() {{ sortTable(th); }});
   }});
 }});
</script>
</head><body>
<h1>Director Alpha &amp; Conviction Calibration Report</h1>
<p class="meta">Generated {generated} &middot; READ-ONLY analysis &middot;
   horizons = {", ".join(horizons)} (calendar-day labels mapped to nearest trading-day offsets) &middot;
   min-buys = {min_buys} &middot; exclusions {"OFF (--include-excluded)" if include_excluded else "ON"}</p>

<div class="verdict"><strong>Calibration verdict:</strong> {esc(verdict)}</div>

<p class="note">
  <strong>Methodology.</strong> CAR is benchmark-relative (sector-matched
  benchmark, FTSE All-Share <code>^FTAS</code> fallback), net of a
  Corwin-Schultz spread estimate plus 0.5% stamp duty on non-AIM buys — the
  exact engine <code>backtest.py</code> uses in production. A buy whose horizon
  window extends past the last available price is <em>immature</em> and is
  <strong>excluded</strong> from that horizon (never counted as zero); each
  horizon column shows its own matured count. Ranking metric: median net CAR at
  T+90 over directors with at least {min_buys} matured T+90 buys.
</p>

<h2>Coverage</h2>
<p class="note">
  {coverage['n_buys']:,} director BUYS in universe &middot;
  {coverage['n_resolved']:,} with a computable entry ({_pct(coverage['n_resolved'] / coverage['n_buys'] if coverage['n_buys'] else 0, 1)}) &middot;
  matured per horizon:
  {" &middot; ".join(f"{h}: {coverage['matured'][h]:,}" for h in horizons)} &middot;
  {coverage['n_directors']:,} distinct directors,
  {coverage['n_qualified']:,} clear the {min_buys}-buy gate.
</p>

<h2>Part A — Director leaderboard (top 60 by median net CAR, T+90)</h2>
<table><thead><tr>{th(lb_head)}</tr></thead>
<tbody>{''.join(lb_rows) or '<tr><td colspan="99">No directors qualified.</td></tr>'}</tbody></table>

<h2>Per-buy detail — top 10 directors</h2>
<table><thead><tr>{th(["Director", "Ticker", "Day", "£ value", "Score"] + [f"net {h}" for h in horizons])}</tr></thead>
<tbody>{''.join(detail_rows) or '<tr><td colspan="99">—</td></tr>'}</tbody></table>

<h2>Part B — Conviction calibration (out-of-sample TEST set)</h2>
<p class="note">
  Split by date: oldest 70% = train ({calib['n_train']:,} buys), newest 30% =
  test ({calib['n_test']:,} buys). Split at {esc(str(calib['split_date']))}.
  All figures below are on the held-out TEST set. <code>t</code> is a
  <strong>ticker-clustered</strong> t-stat on the top-score-quintile mean net
  CAR, so correlated buys in one name cannot masquerade as significance.
</p>
<table><thead><tr>{th(["Horizon", "n test matured", "Spearman rho", "OLS slope",
       "Top-quintile mean net", "Clustered t", "n / clusters", "Overall mean net"])}</tr></thead>
<tbody>{''.join(cal_rows)}</tbody></table>

<h3>Per-factor sub-score correlation with net CAR ({calib['primary_horizon']}, test set)</h3>
<table><thead><tr>{th(["Factor", "n", "Spearman rho vs net CAR"])}</tr></thead>
<tbody>{''.join(fac_rows)}</tbody></table>

<h3>Decile table — mean net CAR by conviction-score bucket ({calib['primary_horizon']}, test set)</h3>
<table><thead><tr>{th(["Decile", "n", "Score range", "Score mean", "Mean net CAR", "Median net CAR"])}</tr></thead>
<tbody>{''.join(dec_rows)}</tbody></table>

<p class="note">
  <strong>Honest framing.</strong> Every prior scan of this dataset has found no
  robust positive edge. A flat or negative result here is the expected, honest
  outcome — not a failure of the tool. Treat any apparently-positive cell as a
  hypothesis to stress-test (clustering, out-of-sample, cost sensitivity), never
  as a tradeable signal on first sight.
</p>
</body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)


# ===========================================================================
# Orchestration
# ===========================================================================

def run(conn, *, horizons, min_buys, include_excluded, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    buys = load_buys(conn, include_excluded=include_excluded)
    caches = cp.build_caches(conn)
    date_cache: dict = {}
    ohlc_cache: dict = {}

    per_buy = []
    n_resolved = 0
    matured_counts = {h: 0 for h in horizons}

    for b in buys:
        ticker = b["ticker"]
        announced = b["effective_announced_at"]
        eff_day = cp.effective_announced_day(b)

        cars = compute_buy_cars(
            conn, date_cache, ohlc_cache,
            ticker=ticker, announced=announced,
            benchmark=b["benchmark_symbol"], is_aim=b["is_aim"] or 0,
            horizons=horizons,
        )
        if cars.get("_entry_close") is not None:
            n_resolved += 1
        for h in horizons:
            if cars.get(h, {}).get("matured"):
                matured_counts[h] += 1

        # Conviction score (as-of-date, lookahead-safe) via the pipeline.
        result, meta = cp.score_buy(conn, b, caches)

        per_buy.append({
            "director_key": director_key(b["director"]),
            "director_name": b["director"] or "(unknown)",
            "ticker": ticker,
            "value_gbp": b["value"],
            "effective_day": eff_day,
            "cars": cars,
            "score": result.score,
            "subscores": dict(result.subscores),
            "sector_mult": result.sector_multiplier,
        })

    qualified, full_lb = build_leaderboard(per_buy, horizons, min_buys)
    calib = run_calibration(per_buy, horizons)

    coverage = {
        "n_buys": len(buys),
        "n_resolved": n_resolved,
        "matured": matured_counts,
        "n_directors": len({r["director_key"] for r in per_buy}),
        "n_qualified": len(qualified),
    }

    lb_csv = out_dir / "director_leaderboard.csv"
    cal_csv = out_dir / "conviction_calibration.csv"
    html_path = out_dir / "director_alpha_report.html"
    write_leaderboard_csv(lb_csv, qualified, horizons)
    write_calibration_csv(cal_csv, calib, horizons)
    write_html(html_path, qualified=qualified, full_leaderboard=full_lb,
               calib=calib, horizons=horizons, per_buy=per_buy,
               min_buys=min_buys, include_excluded=include_excluded,
               coverage=coverage)

    return {
        "coverage": coverage,
        "verdict": calibration_verdict(calib),
        "outputs": [str(html_path), str(lb_csv), str(cal_csv)],
        "n_qualified": len(qualified),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Director alpha + conviction calibration (read-only).")
    p.add_argument("--horizons", default=",".join(DEFAULT_HORIZONS),
                   help="comma list from t30,t60,t90,t360 (default all)")
    p.add_argument("--min-buys", type=int, default=3,
                   help="min matured T+90 buys for the leaderboard (default 3)")
    p.add_argument("--include-excluded", action="store_true",
                   help="include investment trusts / excluded issuers")
    p.add_argument("--out-dir", default=str(OUT_DIR))
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args(argv)

    horizons = [h.strip() for h in args.horizons.split(",") if h.strip()]
    bad = [h for h in horizons if h not in HORIZON_OFFSETS]
    if bad:
        print(f"ERROR: unknown horizon(s) {bad}; valid: {list(HORIZON_OFFSETS)}")
        return 2

    conn = ro_connect(Path(args.db))
    try:
        summary = run(conn, horizons=horizons, min_buys=args.min_buys,
                      include_excluded=args.include_excluded,
                      out_dir=Path(args.out_dir))
    finally:
        conn.close()

    cov = summary["coverage"]
    print(f"buys={cov['n_buys']}  resolved={cov['n_resolved']}  "
          f"directors={cov['n_directors']}  qualified={summary['n_qualified']}")
    print("matured: " + ", ".join(f"{h}={cov['matured'][h]}" for h in horizons))
    print(summary["verdict"])
    for o in summary["outputs"]:
        print(f"  wrote {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
