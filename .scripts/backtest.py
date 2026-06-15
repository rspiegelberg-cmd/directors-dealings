"""Stage 4 -- event-study backtest harness.

For each row in `signals`, computes the forward-window CARs at
T+1 / T+30 / T+90 / T+180 / T+365 (calendar-day labels; trading-day offsets: 1, 21, 63, 126, 252) vs the sector-matched
benchmark (`tickers_meta.benchmark_symbol`, fallback ^FTAS).
Applies UK trading costs:

  * Spread (B-157): dynamic per-stock estimate via Corwin-Schultz (2012),
    computed from the 20 trading days of high/low data before the signal
    fires.  Median of adjacent-day pair estimates; clipped to [5, 400] bps.
    Falls back to CS_FALLBACK_BPS (50 bps) when fewer than CS_MIN_PAIRS (5)
    valid OHLCV pairs are available.
  * Stamp duty: 50 bps on non-AIM BUY at entry only (locked decision
    D-COSTS-MODEL, #4 + #12). Stays flat -- it is a legal tax, not a
    market-friction term.

  cost_bps = cs_spread_bps(ticker, announced_at) + (0 if is_aim else 50)

  net_car_t* = car_t* - cost_bps / 10000

Writes one CSV row per firing to `.data/_backtest_results.csv`
(decision D-RESULTS-STORE, #1). Skipped firings (T+1 unavailable,
insufficient history) are recorded in `.data/_backtest_skips.json`
(decision #9 + D-INSUFFICIENT-HISTORY).

Records the run in `backtest_runs`. CSV path is atomically replaced
on success.

CLI:
    python backtest.py [--signal SIGNAL_ID] [--signal-version VER]
                       [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                       [--out PATH] [--run-id ID] [--verbose]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import db_health  # noqa: E402

# B-164: canonical as-of short-interest aggregate lives in the ingest
# script. Import defensively so backtest never breaks if the script is
# missing; the column simply stays empty.
try:
    from backfill_short_interest import aggregate_short_pct  # noqa: E402
except ImportError:  # pragma: no cover -- defensive
    aggregate_short_pct = None

# B-155: routine vs opportunistic trader classifier (Phase A feature
# columns). Import defensively, same pattern as B-164; if the module is
# missing the two columns simply stay empty.
try:
    from routine_flag import build_buy_history_index, classify_routine  # noqa: E402
except ImportError:  # pragma: no cover -- defensive
    build_buy_history_index = None
    classify_routine = None

# B-159: net-seller-reversal classifier (Phase A feature columns).
# Same defensive-import pattern; missing module -> columns stay empty.
try:
    from reversal_flag import build_trade_history_index, classify_reversal  # noqa: E402
except ImportError:  # pragma: no cover -- defensive
    build_trade_history_index = None
    classify_reversal = None

# B-161: first-window-after-results classifier (Phase A feature
# columns). Same defensive-import pattern.
try:
    from results_window_flag import (  # noqa: E402
        build_results_date_index, classify_post_results,
    )
except ImportError:  # pragma: no cover -- defensive
    build_results_date_index = None
    classify_post_results = None

# B-168: salary-multiple conviction feature. Import defensively; missing
# module -> the salary-multiple columns stay empty.
try:
    import director_pay as _dp  # noqa: E402
except ImportError:  # pragma: no cover -- defensive
    _dp = None


DEFAULT_OUT = db.DB_DIR / "_backtest_results.csv"
SKIPS_PATH  = db.DB_DIR / "_backtest_skips.json"

MIN_HISTORY_DAYS = 30
OFFSETS = (1, 21, 63, 126, 252)
OFFSET_TO_HORIZON = {1: "t1", 21: "t30", 63: "t90", 126: "t180", 252: "t365"}

# B-095 (Sprint 22): split/consolidation guard. A window/entry price ratio
# beyond this band is almost always an unadjusted corporate action or a
# price-data artifact, not a real return (e.g. TIN/Cornish Metals showed a
# +1451% T+21 from an unadjusted ~15:1 consolidation). CARs at such horizons
# are nulled so one broken record can't dominate a cohort; each is logged to
# _split_guard_flagged.csv for review (diff-first).
SPLIT_GUARD_MAX_RATIO = 4.0
SPLIT_GUARD_MIN_RATIO = 0.25

# B-157: Corwin-Schultz dynamic spread estimator.
STAMP_DUTY_NON_AIM_BPS = 50   # legal tax on non-AIM buys — stays flat
CS_FALLBACK_BPS        = 50   # used when OHLCV history is too thin
CS_WINDOW              = 20   # trading days of H/L history before signal
CS_MIN_PAIRS           = 5    # minimum adjacent-day pairs for a reliable estimate
CS_FLOOR_BPS           = 5    # clip negative / near-zero estimates
CS_CAP_BPS             = 400  # clip extreme estimates (e.g. suspended stocks)

# Kept for metadata logging and legacy test compatibility.
AIM_COST_BPS     = CS_FALLBACK_BPS
NON_AIM_COST_BPS = CS_FALLBACK_BPS + STAMP_DUTY_NON_AIM_BPS

# Tickers permanently excluded from backtest results due to confirmed data-quality
# issues (is_aim misclassification + micro-cap corporate-event outliers that are
# not informative about signal performance).  See .data/_data_quality_report.md.
EXCLUDED_TICKERS: list[str] = ["HDD", "DCTA"]

ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


HEADER = [
    "run_id", "signal_id", "signal_version", "fingerprint", "fired_at",
    # B-025 Phase A: role_normalized added alongside role and role_class.
    # All three retained for audit (raw role + Phase A bucket + legacy class).
    "ticker", "role", "role_normalized", "role_class", "value_gbp", "is_aim",
    "market_cap_gbp", "small_cap",
    "benchmark_symbol", "entry_date", "entry_close",
    "t1_close", "t30_close", "t90_close", "t180_close", "t365_close",
    "benchmark_entry", "benchmark_t1", "benchmark_t30",
    "benchmark_t90", "benchmark_t180", "benchmark_t365",
    "raw_return_t1", "raw_return_t30", "raw_return_t90", "raw_return_t180", "raw_return_t365",
    "benchmark_return_t1", "benchmark_return_t30",
    "benchmark_return_t90", "benchmark_return_t180", "benchmark_return_t365",
    "car_t1", "car_t30", "car_t90", "car_t180", "car_t365",
    "cost_bps", "net_car_t1", "net_car_t30", "net_car_t90", "net_car_t180", "net_car_t365",
    # B-160: 52-week range position and price momentum at entry.
    "low_52wk", "high_52wk", "dist_52wk_low",
    "mom_1m", "mom_3m", "mom_6m",
    # B-156: stated post-transaction holding + derived % stake increase.
    "resulting_shares", "holding_pct_increase",
    # B-164: aggregate FCA-disclosed net short interest as of announcement.
    # Empty = no disclosure data for the ticker; 0.0 = all holders exited.
    "short_pct_at_announcement",
    # B-155: routine vs opportunistic trader flag (walk-forward, strictly
    # prior buys only). Values: routine | opportunistic |
    # insufficient_history; empty for non-BUY rows.
    "routine_flag", "routine_prior_buy_years",
    # B-159: net-seller-reversal. seller_reversal_flag = 1 when the
    # director was a net seller (shares) of this ticker over the prior
    # 12 months and this is a BUY; net_shares_prior_12m is the signed
    # magnitude. Empty for non-BUY rows.
    "seller_reversal_flag", "net_shares_prior_12m",
    # B-161: UK MAR first-window-after-results. post_results_flag = 1
    # when the BUY lands within 14 calendar days (~10 trading days)
    # after the ticker's most recent confirmed report_date;
    # days_since_results is the gap. Empty = no reporting-dates
    # coverage (missing data, not zero) or non-BUY row.
    "post_results_flag", "days_since_results",
    "windows_available",
    # B-168: salary-multiple conviction feature. buy value / director pay,
    # GBP, with an AR-publication-date lookahead guard (the figure must have
    # been public at announcement). _total = vs single-figure total comp,
    # _base = vs base salary. Empty when no lookahead-safe figure exists or
    # the multiple is meaningless (zero/nominal pay) or the row is non-BUY.
    # pay_status surfaces the no-figure reason (new_appointee_no_disclosure /
    # out_of_scope / extraction_fail) for diagnostics. Appended AFTER
    # windows_available so the idx_wa-anchored HEADER pin tests (B-155..B-164)
    # stay valid -- consumers read columns by name, not position.
    "salary_multiple_total", "salary_multiple_base",
    "pay_total_gbp", "pay_base_gbp", "pay_fy_end", "pay_confidence", "pay_status",
]


def _holding_pct_increase(tx_type, shares, resulting_shares):
    """B-156 derived metric: shares / (resulting_shares - shares).

    Returns None (-> empty CSV cell) when:
      * resulting_shares is NULL (filing didn't state the figure), or
      * the transaction is not a BUY, or
      * prior holding (resulting - shares) is <= 0 (first-time holding, or
        an inconsistent figure that slipped past the parse guard).
    No winsorisation at write time (plan decision -- consumers clip).
    """
    if resulting_shares is None or tx_type != "BUY" or not shares:
        return None
    try:
        prior = int(resulting_shares) - int(shares)
    except (TypeError, ValueError):
        return None
    if prior <= 0:
        return None
    return int(shares) / prior


def _compact_iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _make_run_id() -> str:
    return f"bt_{_compact_iso_now()}"


def _ticker_dates(cache: dict, conn, ticker: str) -> list[str]:
    """Return sorted ascending list of date strings for `ticker` in `prices`."""
    if ticker in cache:
        return cache[ticker]
    rows = conn.execute(
        "SELECT date FROM prices WHERE ticker = ? AND close IS NOT NULL "
        "ORDER BY date ASC",
        (ticker,),
    ).fetchall()
    out = [r["date"] for r in rows]
    cache[ticker] = out
    return out


def _close_on(conn, ticker: str, the_date: str | None) -> float | None:
    if the_date is None:
        return None
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? AND date = ?",
        (ticker, the_date),
    ).fetchone()
    return row["close"] if row else None


def _ticker_ohlc(cache: dict, conn, ticker: str) -> dict:
    """Return {date: (high, low)} for all rows with valid H/L. Cached per run."""
    if ticker in cache:
        return cache[ticker]
    rows = conn.execute(
        "SELECT date, high, low FROM prices "
        "WHERE ticker = ? AND high IS NOT NULL AND low IS NOT NULL "
        "  AND high > 0 AND low > 0",
        (ticker,),
    ).fetchall()
    out = {r["date"]: (r["high"], r["low"]) for r in rows}
    cache[ticker] = out
    return out


def _cs_spread_bps(
    ohlc_cache: dict,
    conn,
    ticker: str,
    ticker_dates: list,
    entry_idx: int,
) -> float:
    """Estimate per-stock bid-ask spread in bps via Corwin-Schultz (2012).

    Uses the CS_WINDOW trading days *before* entry (pre-announcement period,
    so the signal itself does not contaminate the spread estimate).  Takes the
    median of all valid adjacent-day pair estimates, clips to [CS_FLOOR_BPS,
    CS_CAP_BPS], and returns CS_FALLBACK_BPS when fewer than CS_MIN_PAIRS
    valid pairs are available.

    Formula (Corwin & Schultz, JF 2012, eq. 14):
        K       = 3 - 2*sqrt(2)           # ~0.17157
        beta_i  = ln(H0/L0)^2 + ln(H1/L1)^2
        gamma_i = ln(max(H0,H1) / min(L0,L1))^2
        alpha_i = (sqrt(2*beta_i) - sqrt(beta_i)) / K - sqrt(gamma_i / K)
        S_i     = 2*(exp(alpha_i)-1) / (1+exp(alpha_i))   (fraction of mid)
    """
    K = 3.0 - 2.0 * math.sqrt(2.0)   # ~0.17157

    ohlc = _ticker_ohlc(ohlc_cache, conn, ticker)
    if not ohlc:
        return CS_FALLBACK_BPS

    # Window: up to CS_WINDOW+1 trading days ending the day before entry.
    end   = entry_idx          # exclusive upper bound (entry day itself excluded)
    start = max(0, end - (CS_WINDOW + 1))
    window_dates = ticker_dates[start:end]   # at most CS_WINDOW+1 dates

    estimates: list[float] = []
    for i in range(len(window_dates) - 1):
        d0, d1 = window_dates[i], window_dates[i + 1]
        pair0 = ohlc.get(d0)
        pair1 = ohlc.get(d1)
        if pair0 is None or pair1 is None:
            continue
        h0, l0 = pair0
        h1, l1 = pair1
        if l0 <= 0 or l1 <= 0 or h0 <= 0 or h1 <= 0:
            continue
        try:
            beta  = math.log(h0 / l0) ** 2 + math.log(h1 / l1) ** 2
            gamma = math.log(max(h0, h1) / min(l0, l1)) ** 2
            alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / K \
                    - math.sqrt(gamma / K)
            if alpha <= 0.0:
                s = 0.0
            else:
                s = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
            estimates.append(max(0.0, s))
        except (ValueError, ZeroDivisionError, OverflowError):
            continue

    if len(estimates) < CS_MIN_PAIRS:
        return CS_FALLBACK_BPS

    estimates.sort()
    mid   = len(estimates) // 2
    # Median (take lower of the two middle values for even-length lists)
    median_s = estimates[mid]
    spread_bps = median_s * 10_000.0
    return max(CS_FLOOR_BPS, min(CS_CAP_BPS, spread_bps))


def _rolling_hl(ohlc_cache: dict, conn, ticker: str,
                ticker_dates: list, entry_idx: int,
                n_days: int = 252) -> tuple:
    """Return (low_min, high_max) over the `n_days` trading days strictly
    before entry (pre-announcement only, to avoid contamination).

    Requires prices.high / prices.low (populated by B-157 OHLCV backfill).
    Returns (None, None) when no valid H/L pairs are available in the window.

    B-160: used for 52-week low/high range at entry.
    """
    ohlc = _ticker_ohlc(ohlc_cache, conn, ticker)
    if not ohlc:
        return None, None
    start = max(0, entry_idx - n_days)
    window_dates = ticker_dates[start:entry_idx]
    lows: list[float] = []
    highs: list[float] = []
    for d in window_dates:
        pair = ohlc.get(d)
        if pair is None:
            continue
        hi, lo = pair
        if hi and lo and hi > 0 and lo > 0:
            highs.append(hi)
            lows.append(lo)
    if not lows:
        return None, None
    return min(lows), max(highs)


def _prior_close(ticker_dates: list, conn, ticker: str,
                 entry_idx: int, n_days: int) -> float | None:
    """Close `n_days` trading days before `entry_idx`, or None if unavailable.

    B-160: used for trailing price momentum (1m/3m/6m) at entry.
    """
    back_idx = entry_idx - n_days
    if back_idx < 0:
        return None
    return _close_on(conn, ticker, ticker_dates[back_idx])


def _first_trading_date_after(dates: list[str], announced_at: str):
    """Return (idx, date) for first `prices.date > announced_at`, else (None, None).

    announced_at can be a YYYY-MM-DD or ISO datetime; we compare on the
    date prefix so a "2026-04-20T19:30:00Z" matches dates "> 2026-04-20".

    B-008: defensive assert -- bisect over date strings only equals
    chronological order if every entry is YYYY-MM-DD. A non-ISO date
    sneaking in would silently corrupt CAR calculations. Fail loudly
    instead. No-op on the empty-list case (new ticker, no history).
    """
    for d in dates:
        assert ISO_DATE_RE.match(d), f"Non-ISO date in backtest: {d!r}"
    key = announced_at[:10]
    idx = bisect_right(dates, key)
    if idx >= len(dates):
        return None, None
    return idx, dates[idx]


def _date_at_offset(dates: list[str], entry_idx: int, n: int):
    """Return dates[entry_idx + n] -- N trading days AFTER entry, else None.

    `entry_idx` is the index of the entry date (first trading day strictly
    after announced_at). T+1 = entry_idx + 1; T+21 = entry_idx + 21; etc.
    Returns None if the offset runs past the end of the price series for
    the ticker (then the window is recorded as unavailable in the CSV).
    """
    pos = entry_idx + n
    if pos < 0 or pos >= len(dates):
        return None
    return dates[pos]


def _safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a / b) - 1.0


def _count_history_days(conn, ticker: str, announced_at: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM prices "
        "WHERE ticker = ? AND date < ? AND close IS NOT NULL",
        (ticker, announced_at[:10]),
    ).fetchone()
    return row["n"] if row else 0


def _select_firings(conn, signal_id, signal_version, date_from, date_to):
    """Return rows joined across signals + transactions + tickers_meta.

    `effective_announced_at` is the announcement datetime when available,
    falling back to the transaction date when `announced_at` is missing or
    empty (a known scraper gap — RNS time regex may not match every filing).
    Using the deal date as a fallback introduces a small lookahead window
    (0–3 days) but is far preferable to silently dropping all signals.

    Tickers in EXCLUDED_TICKERS are filtered out at query time so they never
    appear in backtest results, even after a DB rebuild.
    """
    # ── Sector fallback (spec §benchmark-fallback) ────────────────────────────
    # Tickers that have no row in tickers_meta, OR whose row has a NULL
    # benchmark_symbol, fall back to ^FTAS (FTSE All-Share). Previously these
    # rows were silently dropped by an INNER JOIN + `IS NOT NULL` filter, which
    # meant any newly-ingested ticker without a sector mapping produced zero
    # CAR rows in the backtest with no warning. The LEFT JOIN + COALESCE
    # restores the documented fallback behaviour.
    #
    # is_aim is COALESCE'd to 0 (non-AIM) so an unknown ticker is conservatively
    # charged the higher non-AIM cost (50bps spread + 0.5% stamp duty) rather
    # than the AIM-only 50bps. Better to under-state CAR than over-state it.
    # B-011 / Sprint 10 Phase 1: COALESCE(tm.is_excluded_issuer, 0) != 1
    # filters IT/CEF/VCT/REIT tickers. This is independent of the
    # existing EXCLUDED_TICKERS list (which is data-quality outliers,
    # not issuer-type exclusions). Both apply.
    excl_placeholders = ",".join("?" * len(EXCLUDED_TICKERS))
    return conn.execute(
        "SELECT s.signal_id, s.signal_version, s.fingerprint, s.fired_at, "
        "       s.metadata AS sig_metadata, "
        # B-155: director feeds the routine/opportunistic classifier.
        "       t.ticker, t.role, t.role_normalized, t.director, "
        # B-156: type/shares/resulting_shares feed holding_pct_increase.
        "       t.type AS tx_type, t.shares, t.resulting_shares, "
        "       t.value AS value_gbp, t.announced_at, t.date, "
        "       COALESCE(NULLIF(t.announced_at, ''), t.date) AS effective_announced_at, "
        "       COALESCE(tm.benchmark_symbol, '^FTAS') AS benchmark_symbol, "
        "       COALESCE(tm.is_aim, 0) AS is_aim, "
        "       tm.market_cap_gbp, "
        "       COALESCE(tm.small_cap, 0) AS small_cap "
        "FROM signals s "
        "JOIN transactions t ON t.fingerprint = s.fingerprint "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        f"WHERE t.ticker NOT IN ({excl_placeholders}) "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "  AND (? IS NULL OR s.signal_id = ?) "
        "  AND (? IS NULL OR s.signal_version = ?) "
        "  AND (? IS NULL OR COALESCE(NULLIF(t.announced_at, ''), t.date) >= ?) "
        "  AND (? IS NULL OR COALESCE(NULLIF(t.announced_at, ''), t.date) <= ?) "
        "ORDER BY COALESCE(NULLIF(t.announced_at, ''), t.date), s.signal_id, s.fingerprint",
        (*EXCLUDED_TICKERS,
         signal_id, signal_id, signal_version, signal_version,
         date_from, date_from, date_to, date_to),
    ).fetchall()


def run_backtest(conn, *, signal_id=None, signal_version=None,
                 date_from=None, date_to=None,
                 out_path: Path = DEFAULT_OUT,
                 run_id: str | None = None,
                 verbose: bool = False) -> dict:
    """Execute the backtest. Returns summary dict."""
    run_id = run_id or _make_run_id()
    started_at = db.iso_now()
    conn.execute(
        "INSERT OR REPLACE INTO backtest_runs "
        "(run_id, started_at, signal_id, signal_version, metadata, universe, "
        " period_start, period_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, started_at, signal_id, signal_version,
         json.dumps({"offsets": list(OFFSETS),
                     "spread_model": "corwin_schultz_2012",
                     "cs_window": CS_WINDOW,
                     "cs_fallback_bps": CS_FALLBACK_BPS,
                     "stamp_duty_non_aim_bps": STAMP_DUTY_NON_AIM_BPS}),
         "transactions LEFT JOIN tickers_meta (benchmark_symbol fallback ^FTAS)",
         date_from, date_to),
    )
    conn.commit()

    firings = _select_firings(conn, signal_id, signal_version,
                              date_from, date_to)
    if verbose:
        print(f"backtest run_id={run_id} firings={len(firings)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    skips: list[dict] = []
    split_flags: list[dict] = []   # B-095 split/consolidation guard
    date_cache: dict = {}
    ohlc_cache: dict = {}          # B-157 Corwin-Schultz H/L lookup
    n_written = 0

    # B-164: one-time guard, not per-row try/except. On a pre-migration DB
    # (no short_positions table) or with the ingest script missing, the
    # short_pct_at_announcement column is emitted empty for every row.
    has_short_data = (
        aggregate_short_pct is not None
        and conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='short_positions'"
        ).fetchone() is not None
    )

    # B-155: routine vs opportunistic classifier index, built once per
    # run (one SELECT over all BUYs). None -> both columns emit empty.
    routine_index = (
        build_buy_history_index(conn)
        if build_buy_history_index is not None else None
    )

    # B-159: net-seller-reversal index (BUY + SELL rows), built once
    # per run. None -> both columns emit empty.
    trade_index = (
        build_trade_history_index(conn)
        if build_trade_history_index is not None else None
    )

    # B-161: confirmed reporting-dates index, built once per run.
    # Guarded by a sqlite_master check (B-164 has_short_data pattern)
    # so old test fixtures without the reporting_dates table still run;
    # None -> both columns emit empty.
    results_index = None
    if build_results_date_index is not None and conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='reporting_dates'"
    ).fetchone() is not None:
        results_index = build_results_date_index(conn)

    # B-168: salary-multiple feature available only when the director_pay
    # table exists (migration 015) and the module imported. Same one-time
    # sqlite_master guard as B-164/B-161 -> old fixtures emit empty cells.
    has_director_pay = (
        _dp is not None
        and conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='director_pay'"
        ).fetchone() is not None
    )

    with tmp_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)

        for r in firings:
            ticker     = r["ticker"]
            announced  = r["effective_announced_at"]  # date fallback applied in SQL
            benchmark  = r["benchmark_symbol"]
            is_aim     = r["is_aim"] or 0

            ticker_dates = _ticker_dates(date_cache, conn, ticker)
            bench_dates  = _ticker_dates(date_cache, conn, benchmark)

            # Insufficient history -> skip + log (D-INSUFFICIENT-HISTORY).
            n_prior = _count_history_days(conn, ticker, announced)
            if n_prior < MIN_HISTORY_DAYS:
                skips.append({
                    "run_id": run_id,
                    "fingerprint": r["fingerprint"],
                    "signal_id": r["signal_id"],
                    "ticker": ticker,
                    "announced_at": announced,
                    "reason": f"insufficient history ({n_prior} < {MIN_HISTORY_DAYS})",
                })
                continue

            # T+1: first trading day strictly after announced_at.
            entry_idx, entry_date = _first_trading_date_after(ticker_dates, announced)
            if entry_idx is None:
                skips.append({
                    "run_id": run_id,
                    "fingerprint": r["fingerprint"],
                    "signal_id": r["signal_id"],
                    "ticker": ticker,
                    "announced_at": announced,
                    "reason": "no trading day after announced_at",
                })
                continue

            # Compute close at each window in the ticker series.
            entry_close = _close_on(conn, ticker, entry_date)
            window_dates = {n: _date_at_offset(ticker_dates, entry_idx, n)
                            for n in OFFSETS}
            window_closes = {n: _close_on(conn, ticker, window_dates[n])
                             for n in OFFSETS}

            # Benchmark: find first benchmark trading day on/after entry_date.
            b_idx = bisect_left(bench_dates, entry_date)
            if b_idx >= len(bench_dates):
                bench_entry_date = None
            else:
                bench_entry_date = bench_dates[b_idx]
            bench_entry_close = _close_on(conn, benchmark, bench_entry_date)

            bench_window_dates = {
                n: (bench_dates[b_idx + n]
                    if (bench_entry_date is not None
                        and 0 <= b_idx + n < len(bench_dates))
                    else None)
                for n in OFFSETS
            }
            bench_window_closes = {
                n: _close_on(conn, benchmark, bench_window_dates[n])
                for n in OFFSETS
            }

            # Returns vs entry.
            raw_returns = {n: _safe_div(window_closes[n], entry_close)
                           for n in OFFSETS}
            # B-095 split/consolidation guard: a window/entry price ratio
            # beyond the plausible band is overwhelmingly an unadjusted
            # corporate action or price-data artifact (e.g. TIN's +1451%).
            # Null that horizon's return so cars/net_cars become None and it
            # is excluded from cohort aggregates; log it for review.
            if entry_close and entry_close > 0:
                for _n in OFFSETS:
                    _wc = window_closes[_n]
                    if _wc is None or _wc <= 0:
                        continue
                    _ratio = _wc / entry_close
                    if _ratio > SPLIT_GUARD_MAX_RATIO or _ratio < SPLIT_GUARD_MIN_RATIO:
                        raw_returns[_n] = None
                        split_flags.append({
                            "run_id": run_id,
                            "fingerprint": r["fingerprint"],
                            "signal_id": r["signal_id"],
                            "ticker": ticker,
                            "entry_date": entry_date,
                            "horizon": OFFSET_TO_HORIZON[_n],
                            "entry_close": entry_close,
                            "window_close": _wc,
                            "ratio": round(_ratio, 3),
                        })
            bench_returns = {n: _safe_div(bench_window_closes[n], bench_entry_close)
                             for n in OFFSETS}
            cars = {
                n: (raw_returns[n] - bench_returns[n])
                   if (raw_returns[n] is not None and bench_returns[n] is not None)
                   else None
                for n in OFFSETS
            }

            # B-157: dynamic spread via Corwin-Schultz, plus flat stamp duty.
            cs_spread    = _cs_spread_bps(ohlc_cache, conn, ticker,
                                          ticker_dates, entry_idx)
            stamp_duty   = 0 if is_aim else STAMP_DUTY_NON_AIM_BPS
            cost_bps     = cs_spread + stamp_duty
            net_cars = {
                n: (cars[n] - cost_bps / 10_000.0)
                   if cars[n] is not None else None
                for n in OFFSETS
            }

            # B-160: 52-week low/high range at entry.
            low_52wk, high_52wk = _rolling_hl(
                ohlc_cache, conn, ticker, ticker_dates, entry_idx, n_days=252
            )
            if (low_52wk is not None and high_52wk is not None
                    and high_52wk > low_52wk and entry_close is not None):
                dist_52wk_low = (entry_close - low_52wk) / (high_52wk - low_52wk)
                # Clip to [0, 1] — intraday vs end-of-day can push close
                # fractionally outside the H/L range.
                dist_52wk_low = max(0.0, min(1.0, dist_52wk_low))
            else:
                dist_52wk_low = None

            # B-160: trailing price momentum (simple return) ending at entry.
            p_1m = _prior_close(ticker_dates, conn, ticker, entry_idx, 21)
            p_3m = _prior_close(ticker_dates, conn, ticker, entry_idx, 63)
            p_6m = _prior_close(ticker_dates, conn, ticker, entry_idx, 126)
            mom_1m = _safe_div(entry_close, p_1m) if entry_close is not None else None
            mom_3m = _safe_div(entry_close, p_3m) if entry_close is not None else None
            mom_6m = _safe_div(entry_close, p_6m) if entry_close is not None else None

            windows_available = ",".join(
                OFFSET_TO_HORIZON[n] for n in OFFSETS if raw_returns[n] is not None
            )

            # role_class from metadata if available, else compute.
            role_class = ""
            sig_md = r["sig_metadata"]
            if sig_md:
                try:
                    md = json.loads(sig_md)
                    role_class = md.get("role_class", "") or ""
                except Exception:  # noqa: BLE001
                    role_class = ""

            # role_normalized may be NULL on un-backfilled rows.
            role_normalized = (
                r["role_normalized"]
                if "role_normalized" in r.keys() else None
            ) or ""

            # B-156: stated post-transaction holding + derived stake metric.
            # Defensive .keys() checks keep old test fixtures (pre-migration
            # SELECTs) working; in production the columns always exist.
            resulting_shares = (
                r["resulting_shares"]
                if "resulting_shares" in r.keys() else None
            )
            tx_shares = r["shares"] if "shares" in r.keys() else None
            tx_type = r["tx_type"] if "tx_type" in r.keys() else None
            holding_pct = _holding_pct_increase(
                tx_type, tx_shares, resulting_shares
            )

            # B-164: aggregate disclosed net short interest STRICTLY
            # BEFORE the announcement date (inclusive=False). The FCA
            # publishes a position the next business day, so a position
            # dated on the announcement date was not public knowledge
            # at announcement time -- including it would be lookahead
            # bias (P3-6). (None -> empty cell when no data.)
            short_pct = (
                aggregate_short_pct(conn, ticker, announced,
                                    inclusive=False)
                if has_short_data else None
            )

            # B-155: routine vs opportunistic flag, walk-forward.
            # History is strictly prior to the announcement (same P3-6
            # lookahead discipline as B-164). BUY rows only; non-BUY
            # firings emit empty cells. Defensive .keys() check keeps
            # old test fixtures (SELECTs without t.director) working.
            tx_director = r["director"] if "director" in r.keys() else None
            if (routine_index is not None and classify_routine is not None
                    and tx_type == "BUY" and tx_director):
                routine_flag_val, routine_years = classify_routine(
                    routine_index, tx_director, ticker, announced
                )
            else:
                routine_flag_val, routine_years = None, None

            # B-159: net-seller-reversal, walk-forward (strictly-prior
            # window, same P3-6 lookahead discipline). BUY rows only.
            if (trade_index is not None and classify_reversal is not None
                    and tx_type == "BUY" and tx_director):
                reversal_flag_val, net_shares_prior = classify_reversal(
                    trade_index, tx_director, ticker, announced
                )
            else:
                reversal_flag_val, net_shares_prior = None, None

            # B-161: first-window-after-results. Ticker-level lookup —
            # no director needed. BUY rows only; empty = no coverage.
            if (results_index is not None
                    and classify_post_results is not None
                    and tx_type == "BUY"):
                post_results_val, days_since_results = (
                    classify_post_results(results_index, ticker, announced)
                )
            else:
                post_results_val, days_since_results = None, None

            # B-168: salary-multiple feature, lookahead-guarded on AR
            # publication date (latest_pay_before requires ar_published_at <=
            # the announcement). BUY rows only. Two denominators (total + base).
            sm_total = sm_base = None
            pay_total_gbp = pay_base_gbp = None
            pay_fy_end = pay_confidence = pay_status_out = None
            if has_director_pay and tx_type == "BUY" and tx_director:
                dkey = _dp.director_key(tx_director)
                val = r["value_gbp"]
                total_row = _dp.latest_pay_before(
                    conn, ticker, dkey, announced, "single_figure_total")
                base_row = _dp.latest_pay_before(
                    conn, ticker, dkey, announced, "base_salary")
                if total_row is not None:
                    pay_total_gbp = total_row["pay_gbp"]
                    sm_total = _dp.salary_multiple(
                        val, pay_gbp=pay_total_gbp,
                        pay_status=total_row["pay_status"],
                        pay_type=total_row["pay_type"])
                    pay_fy_end = total_row["fy_end"]
                    pay_confidence = total_row["confidence"]
                if base_row is not None:
                    pay_base_gbp = base_row["pay_gbp"]
                    sm_base = _dp.salary_multiple(
                        val, pay_gbp=pay_base_gbp,
                        pay_status=base_row["pay_status"],
                        pay_type=base_row["pay_type"])
                    if pay_fy_end is None:
                        pay_fy_end = base_row["fy_end"]
                    if pay_confidence is None:
                        pay_confidence = base_row["confidence"]
                if total_row is not None or base_row is not None:
                    pay_status_out = "ok"
                else:
                    nr = conn.execute(
                        "SELECT pay_status FROM director_pay "
                        "WHERE ticker = ? AND director_key = ? "
                        "  AND pay_type = 'none' LIMIT 1",
                        (ticker, dkey)).fetchone()
                    pay_status_out = nr["pay_status"] if nr else None
            writer.writerow([
                run_id, r["signal_id"], r["signal_version"],
                r["fingerprint"], r["fired_at"],
                ticker, r["role"] or "", role_normalized, role_class,
                r["value_gbp"], is_aim,
                r["market_cap_gbp"], r["small_cap"] or 0,
                benchmark, entry_date, entry_close,
                window_closes[1], window_closes[21],
                window_closes[63], window_closes[126], window_closes[252],
                bench_entry_close, bench_window_closes[1],
                bench_window_closes[21], bench_window_closes[63],
                bench_window_closes[126], bench_window_closes[252],
                raw_returns[1], raw_returns[21],
                raw_returns[63], raw_returns[126], raw_returns[252],
                bench_returns[1], bench_returns[21],
                bench_returns[63], bench_returns[126], bench_returns[252],
                cars[1], cars[21], cars[63], cars[126], cars[252],
                cost_bps,
                net_cars[1], net_cars[21], net_cars[63], net_cars[126], net_cars[252],
                low_52wk, high_52wk, dist_52wk_low,
                mom_1m, mom_3m, mom_6m,
                resulting_shares, holding_pct,
                short_pct,
                routine_flag_val, routine_years,
                reversal_flag_val, net_shares_prior,
                post_results_val, days_since_results,
                windows_available,
                sm_total, sm_base,
                pay_total_gbp, pay_base_gbp, pay_fy_end, pay_confidence,
                pay_status_out,
            ])
            n_written += 1

    os.replace(tmp_path, out_path)

    if skips:
        existing = []
        if SKIPS_PATH.exists():
            try:
                existing = json.loads(SKIPS_PATH.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                existing = []
        merged = (existing or []) + skips
        # B-031 + B-038 (2026-05-21): atomic write via canonical
        # db.atomic_write_json helper.
        db.atomic_write_json(SKIPS_PATH, merged)

    if split_flags:
        flag_path = out_path.parent / "_split_guard_flagged.csv"
        try:
            with flag_path.open("w", encoding="utf-8", newline="") as ff:
                w = csv.DictWriter(ff, fieldnames=list(split_flags[0].keys()))
                w.writeheader()
                w.writerows(split_flags)
        except OSError:
            pass
        print(f"[backtest] B-095 split-guard nulled {len(split_flags)} "
              f"implausible CAR horizon(s) (ratio >{SPLIT_GUARD_MAX_RATIO}x or "
              f"<{SPLIT_GUARD_MIN_RATIO}x); see {flag_path.name}")

    conn.execute(
        "UPDATE backtest_runs SET finished_at = ? WHERE run_id = ?",
        (db.iso_now(), run_id),
    )
    conn.commit()

    summary = {
        "run_id": run_id,
        "rows_written": n_written,
        "rows_skipped": len(skips),
        "split_guard_flagged": len(split_flags),
        "output_path": str(out_path),
    }
    if verbose:
        print(f"backtest summary: {summary}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Stage 4 backtest.")
    parser.add_argument("--signal", dest="signal_id", default=None)
    parser.add_argument("--signal-version", dest="signal_version", default=None)
    parser.add_argument("--from", dest="date_from", default=None)
    parser.add_argument("--to", dest="date_to", default=None)
    parser.add_argument("--out", dest="out_path", default=None)
    parser.add_argument("--run-id", dest="run_id", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    out_path = Path(args.out_path) if args.out_path else DEFAULT_OUT

    # B-024: db_health pattern — pre-run integrity check + backup before
    # backtest_runs INSERTs and CSV overwrite. Canonical reference:
    # classify_issuers.py:run().
    if not db_health.check(db.DB_PATH):
        print("[backtest] FATAL: pre-run integrity_check failed. "
              "Run start.bat to restore from .bak before retrying.")
        return 2
    if not db_health.backup():
        print("[backtest] FATAL: failed to take pre-backtest .bak. "
              "Refusing to proceed.")
        return 3

    # B-033: open the connection INSIDE the try so a connect() failure
    # (corrupt schema, disk full) doesn't leak a half-initialised handle.
    conn = None
    try:
        conn = db.connect()
        summary = run_backtest(
            conn,
            signal_id=args.signal_id,
            signal_version=args.signal_version,
            date_from=args.date_from,
            date_to=args.date_to,
            out_path=out_path,
            run_id=args.run_id,
            verbose=args.verbose,
        )
        print(f"run_id={summary['run_id']}  "
              f"rows_written={summary['rows_written']}  "
              f"rows_skipped={summary['rows_skipped']}  "
              f"out={summary['output_path']}")
    finally:
        if conn is not None:
            conn.close()

    # B-024: db_health post-run pattern. Skip seal if post-run integrity
    # fails so the pre-run .bak stays the rollback target.
    try:
        if not db_health.check(db.DB_PATH):
            print("[backtest] WARNING: post-run integrity_check failed. "
                  "The pre-run .bak is valid — restore via start.bat. "
                  "Skipping seal to preserve good backup.")
            return 4
        db_health.seal()
    except Exception as e:
        print(f"[db_health] post-backtest seal failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
