"""DB-aware adapter for the Weekly Conviction Score (B-171, Phase 2).

This module is the bridge between the pure-compute scoring engine
(`conviction.py` — DO NOT MODIFY) and the live SQLite DB. It builds the six
factor inputs from `transactions`, `prices`, `tickers_meta`, and
`reporting_dates`, then calls `conviction.conviction_score(...)` per BUY.

Design constraints (CLAUDE.md Zone-A rules + spec §2/§6/§7):
  * READ-ONLY against the DB. Nothing here writes the DB. The exporter
    (Phase 3 shadow log) does the one upsert; this adapter only reads.
  * LOOKAHEAD DISCIPLINE (project rule P3-6, spec §7): every price-,
    return-, turnover-, and sector-derived input uses ONLY rows strictly
    BEFORE the buy's effective announcement date. A buy must never "see"
    its own announcement-day or later price action. Encoded here and
    tested explicitly in test_conviction_pipeline.py.
  * Graceful degradation: missing volume/cap/earnings/sector inputs return
    None so the engine's own neutral / drop-and-renormalise rules apply.

Factor → input mapping (spec §8, grounded against the live schema):
  F1 who                 <- signals.roles.classify_role(tx.role), with the
                            PCA-inheritance roster lift (build_company_top_tier)
  F2 buy size            <- tx.value + avg_daily_turnover (price×volume join)
  F3 company size        <- tickers_meta.market_cap_gbp
  F4 earnings timing     <- reporting_dates distances around the buy
  F5 past performance    <- trailing price return (NOT reversal_flag net-shares)
  F6 sector guardrail    <- recent benchmark run via tickers_meta.benchmark_symbol
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Make .scripts/ importable so `conviction`, `signals.roles`, and the
# export helper resolve whether we're run as a module or a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import conviction  # noqa: E402  (pure-compute engine — never modified here)
from conviction import ConvictionResult  # noqa: E402
from signals.roles import ALL_TIERS, classify_role  # noqa: E402

# ---------------------------------------------------------------------------
# Trading-day windows (calendar-day approximations are NOT used — we count
# real rows in `prices`, so a ~20-trading-day mean is exactly 20 stored bars).
# ---------------------------------------------------------------------------
TURNOVER_WINDOW = 20          # ~1 month of trading days for avg daily turnover
TRAILING_RETURN_WINDOW = 63   # ~3 months of trading days for F5 trailing return
SECTOR_WINDOW = 63            # ~3 months of benchmark bars for F6 hotness


# ---------------------------------------------------------------------------
# Date helpers — keep lookahead exclusion in one place.
# ---------------------------------------------------------------------------

def _iso_day(value: Optional[str]) -> Optional[str]:
    """Normalise a date / announced_at value to 'YYYY-MM-DD', else None.

    Mirrors export_dashboard_json._to_iso_day so the adapter and the exporter
    agree on what 'the buy's effective day' is (ISO prefix or 'DD Mon YYYY').
    """
    if not value:
        return None
    s = str(value).strip()
    head = s[:10]
    try:
        datetime.strptime(head, "%Y-%m-%d")
        return head
    except ValueError:
        pass
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def effective_announced_day(tx_row) -> Optional[str]:
    """The buy's effective announcement day (ISO) — the lookahead cutoff.

    Prefer announced_at (when the deal became public); fall back to the
    transaction date for backfilled rows with no announced_at. This is the
    EXCLUSIVE upper bound: all price/return/turnover/sector reads use rows
    strictly BEFORE this day.
    """
    val = _row_get(tx_row, "announced_at")
    day = _iso_day(val)
    if day:
        return day
    return _iso_day(_row_get(tx_row, "date"))


def _row_get(row, key, default=None):
    """Read a key from a sqlite3.Row or a plain dict uniformly."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


# ---------------------------------------------------------------------------
# F1 — PCA-inheritance roster lookup.
# ---------------------------------------------------------------------------

def build_company_top_tier(conn) -> dict:
    """Map each ticker -> the most-senior director tier seen at that company.

    Used for PCA inheritance (spec §3 F1): a PCA buy inherits the strength of
    the most-senior director at the same company. We compute the roster from
    ALL of that company's transactions (the full known board roster), classify
    each role via signals.roles.classify_role, and reduce to the most-senior
    tier using ALL_TIERS ordering (T1a is most senior, then T1b, T2, ...).

    Note: T5 (PCA) and T4 (catch-all) sit late in ALL_TIERS, so a company whose
    only actors are PCAs returns "T5" — which the engine treats as the bare-PCA
    fallback (no senior to inherit). T7 (Chair) sits LAST in ALL_TIERS but is a
    SENIOR role; we therefore rank by an explicit seniority order, not raw
    ALL_TIERS index, so a Chair correctly outranks a NED.
    """
    # Seniority rank: lower = more senior. Mirrors conviction._TIER_STRENGTH
    # intent (Chair/CEO strongest) rather than the ALL_TIERS declaration order.
    seniority = {
        "T1a": 0,  # CEO / Founder
        "T7": 0,   # Chair (grouped with CEO by the spec)
        "T1b": 1,  # CFO
        "T2": 2,   # other senior exec
        "T6": 3,   # Co Sec / GC
        "T3": 4,   # NED
        "T5": 5,   # PCA (only if nothing more senior at the company)
        "T4": 6,   # catch-all (weakest)
    }
    rows = conn.execute(
        "SELECT ticker, role FROM transactions WHERE ticker IS NOT NULL"
    ).fetchall()
    best: dict[str, str] = {}
    best_rank: dict[str, int] = {}
    for r in rows:
        ticker = _row_get(r, "ticker")
        if not ticker:
            continue
        tier = classify_role(_row_get(r, "role"))
        rank = seniority.get(tier, 99)
        if ticker not in best_rank or rank < best_rank[ticker]:
            best_rank[ticker] = rank
            best[ticker] = tier
    return best


# ---------------------------------------------------------------------------
# F2 (relative leg) — average daily £ turnover.
# ---------------------------------------------------------------------------

def avg_daily_turnover(conn, ticker: str, as_of: Optional[str]) -> Optional[float]:
    """Trailing ~20-trading-day mean of close*volume, strictly BEFORE as_of.

    Returns None when there is no usable volume history (the F2 relative leg
    then degrades to the absolute-£ curve inside the engine). `prices.volume`
    exists and is populated for most equities — no migration needed.

    Lookahead: rows are filtered `date < as_of` (strict), so the buy never
    sees its own announcement-day bar or later.
    """
    if not ticker or not as_of:
        return None
    rows = conn.execute(
        "SELECT close, volume FROM prices "
        "WHERE ticker = ? AND date < ? "
        "  AND close IS NOT NULL AND close > 0 "
        "  AND volume IS NOT NULL AND volume > 0 "
        "ORDER BY date DESC LIMIT ?",
        (ticker, as_of, TURNOVER_WINDOW),
    ).fetchall()
    if not rows:
        return None
    turnovers = [float(r["close"]) * float(r["volume"]) for r in rows]
    if not turnovers:
        return None
    return sum(turnovers) / len(turnovers)


# ---------------------------------------------------------------------------
# F5 — trailing PRICE return (reuses backtest._close_on pattern).
# ---------------------------------------------------------------------------

def _close_on_or_before(conn, ticker: str, the_day: str):
    """Latest close strictly BEFORE `the_day` (lookahead-safe entry price)."""
    row = conn.execute(
        "SELECT close FROM prices "
        "WHERE ticker = ? AND date < ? AND close IS NOT NULL AND close > 0 "
        "ORDER BY date DESC LIMIT 1",
        (ticker, the_day),
    ).fetchone()
    return float(row["close"]) if row else None


def trailing_return(conn, ticker: str, as_of: Optional[str]) -> Optional[float]:
    """Trailing ~3-month PRICE return ending strictly BEFORE as_of.

    close(latest < as_of) / close(~63 trading days earlier) - 1.

    This is a PRICE return (spec F5), deliberately NOT reversal_flag.py, which
    works on net SHARES and is the wrong quantity here. Returns None when
    either endpoint is missing (engine then treats F5 as neutral 0.5).

    Lookahead: both endpoints come from rows with date < as_of.
    """
    if not ticker or not as_of:
        return None
    # All closes strictly before the buy, newest last.
    rows = conn.execute(
        "SELECT date, close FROM prices "
        "WHERE ticker = ? AND date < ? AND close IS NOT NULL AND close > 0 "
        "ORDER BY date ASC",
        (ticker, as_of),
    ).fetchall()
    if len(rows) < 2:
        return None
    end_close = float(rows[-1]["close"])
    back_idx = len(rows) - 1 - TRAILING_RETURN_WINDOW
    if back_idx < 0:
        # Not enough history for a full 3-month lookback; use the oldest bar
        # we have (still strictly pre-buy) rather than inventing a number.
        back_idx = 0
    start_close = float(rows[back_idx]["close"])
    if start_close <= 0:
        return None
    return (end_close / start_close) - 1.0


# ---------------------------------------------------------------------------
# F4 — earnings distances from reporting_dates.
# ---------------------------------------------------------------------------

def earnings_distances(conn, ticker: str, buy_day: Optional[str]):
    """(days_to_next_results, days_since_last_results) around buy_day.

    Reads `reporting_dates` (the ~23% forward-coverage table). days_to_next is
    the nearest reporting date strictly AFTER the buy; days_since_last is the
    nearest reporting date strictly BEFORE the buy. Either or both can be None
    when coverage is missing — the engine drops F4 and re-normalises when BOTH
    are None (spec decision 2026-06-18).

    Returns (None, None) when the ticker has no reporting_dates rows.
    """
    if not ticker or not buy_day:
        return (None, None)
    try:
        buy = datetime.strptime(buy_day, "%Y-%m-%d").date()
    except ValueError:
        return (None, None)
    rows = conn.execute(
        "SELECT report_date FROM reporting_dates "
        "WHERE ticker = ? AND report_date IS NOT NULL",
        (ticker,),
    ).fetchall()
    days_to_next: Optional[float] = None
    days_since_last: Optional[float] = None
    for r in rows:
        d_iso = _iso_day(_row_get(r, "report_date"))
        if not d_iso:
            continue
        try:
            d = datetime.strptime(d_iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        delta = (d - buy).days
        if delta > 0:
            if days_to_next is None or delta < days_to_next:
                days_to_next = float(delta)
        elif delta < 0:
            since = float(-delta)
            if days_since_last is None or since < days_since_last:
                days_since_last = since
        # delta == 0 (results announced on the buy day): ignore — ambiguous.
    return (days_to_next, days_since_last)


# ---------------------------------------------------------------------------
# F6 — sector hotness (recent benchmark run, 0-1).
# ---------------------------------------------------------------------------

def sector_hotness(conn, benchmark_symbol: Optional[str],
                   as_of: Optional[str]) -> Optional[float]:
    """Normalised recent benchmark run (0.0 calm .. 1.0 hot), or None.

    Measures how hard the sector benchmark has run over the trailing
    ~3 months ending strictly BEFORE as_of, and maps that run to 0-1 so the
    engine's F6 guardrail can DISCOUNT (never lift) a hot-sector buy.

    Mapping (judgment, spec §6 — guardrail not booster):
      benchmark return <= 0%   -> 0.0  (calm / falling: no discount)
      benchmark return >= +20% -> 1.0  (running hard: max discount)
      linear between.

    Returns None when the benchmark series is missing (engine then applies no
    discount, multiplier 1.0).
    """
    if not benchmark_symbol or not as_of:
        return None
    rows = conn.execute(
        "SELECT date, close FROM prices "
        "WHERE ticker = ? AND date < ? AND close IS NOT NULL AND close > 0 "
        "ORDER BY date ASC",
        (benchmark_symbol, as_of),
    ).fetchall()
    if len(rows) < 2:
        return None
    end_close = float(rows[-1]["close"])
    back_idx = len(rows) - 1 - SECTOR_WINDOW
    if back_idx < 0:
        back_idx = 0
    start_close = float(rows[back_idx]["close"])
    if start_close <= 0:
        return None
    run = (end_close / start_close) - 1.0
    # Map [0%, +20%] -> [0.0, 1.0]; clamp outside.
    if run <= 0.0:
        return 0.0
    if run >= 0.20:
        return 1.0
    return run / 0.20


# ---------------------------------------------------------------------------
# Per-buy scoring.
# ---------------------------------------------------------------------------

class _Caches:
    """Per-run lookups built once, reused across every buy in the week."""

    def __init__(self, conn):
        self.company_top_tier = build_company_top_tier(conn)
        self.ticker_meta: dict = {}
        for r in conn.execute(
            "SELECT ticker, benchmark_symbol, market_cap_gbp "
            "FROM tickers_meta"
        ).fetchall():
            self.ticker_meta[_row_get(r, "ticker")] = {
                "benchmark_symbol": _row_get(r, "benchmark_symbol"),
                "market_cap_gbp": _row_get(r, "market_cap_gbp"),
            }


def build_caches(conn) -> _Caches:
    """Build the per-run cache bundle (roster + ticker meta)."""
    return _Caches(conn)


def _factor_inputs(conn, tx_row, caches: _Caches) -> dict:
    """Assemble the raw engine inputs for one BUY (all lookahead-safe).

    Returns a dict of conviction.conviction_score kwargs plus an
    `inputs_missing` list naming the factors with no underlying data (so the
    UI can show 'unknown' rather than a misleading 0).
    """
    ticker = _row_get(tx_row, "ticker")
    as_of = effective_announced_day(tx_row)
    buy_day = as_of  # earnings distances measured from the same effective day

    tier = classify_role(_row_get(tx_row, "role"))
    is_pca = (tier == "T5")
    company_top = caches.company_top_tier.get(ticker)

    meta = caches.ticker_meta.get(ticker, {})
    market_cap = meta.get("market_cap_gbp")
    benchmark = meta.get("benchmark_symbol")

    value_gbp = _row_get(tx_row, "value")
    turnover = avg_daily_turnover(conn, ticker, as_of)
    days_to_next, days_since_last = earnings_distances(conn, ticker, buy_day)
    tret = trailing_return(conn, ticker, as_of)
    hotness = sector_hotness(conn, benchmark, as_of)

    inputs_missing: list[str] = []
    if turnover is None:
        inputs_missing.append("buy_size_relative")  # absolute leg still used
    if not market_cap or float(market_cap or 0) <= 0:
        inputs_missing.append("company_size")
    if days_to_next is None and days_since_last is None:
        inputs_missing.append("earnings_timing")
    if tret is None:
        inputs_missing.append("past_performance")
    if hotness is None:
        inputs_missing.append("sector_mult")

    return {
        "kwargs": {
            "tier": tier,
            "is_pca": is_pca,
            "company_top_tier": company_top,
            "value_gbp": float(value_gbp) if value_gbp is not None else None,
            "avg_daily_turnover_gbp": turnover,
            "market_cap_gbp": float(market_cap) if market_cap else None,
            "days_to_next_results": days_to_next,
            "days_since_last_results": days_since_last,
            "trailing_return": tret,
            "sector_beta_hotness": hotness,
        },
        "inputs_missing": inputs_missing,
        "tier": tier,
        "is_pca": is_pca,
        "as_of": as_of,
    }


def score_buy(conn, tx_row, caches: _Caches):
    """Score one BUY row -> (ConvictionResult, meta dict).

    `meta` carries inputs_missing + the resolved tier/PCA flag/effective day,
    for the exporter and the panel. The ConvictionResult is the engine output.
    """
    fi = _factor_inputs(conn, tx_row, caches)
    result: ConvictionResult = conviction.conviction_score(**fi["kwargs"])
    meta = {
        "inputs_missing": fi["inputs_missing"],
        "tier": fi["tier"],
        "is_pca": fi["is_pca"],
        "as_of": fi["as_of"],
    }
    return result, meta


# ---------------------------------------------------------------------------
# Rolling trailing-window selection (B-171 revised surfacing).
# ---------------------------------------------------------------------------

def _window_bounds(as_of, days: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the trailing `days`-day window.

    The window is INCLUSIVE on both ends: a buy is in-window if its effective
    announcement day falls in [as_of - days, as_of]. `as_of` may be a date or
    an ISO 'YYYY-MM-DD' string.
    """
    if isinstance(as_of, str):
        end = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    else:
        end = as_of
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def score_window(conn, as_of, days: int = 28) -> list[dict]:
    """Score EVERY BUY in the trailing `days`-day window; return the full ranking.

    A buy belongs to the window if its effective announcement day falls in
    the INCLUSIVE range [as_of - days, as_of]. This replaces the old
    Monday-anchored week with a rolling window (B-171 revised): the panel
    shows the strongest buys over a rolling trailing 4 weeks, ageing a buy out
    once it is more than `days` days old.

    Returns a list of dicts (one per buy) sorted by score DESC, each carrying
    the ConvictionResult.as_dict(), the transaction identity fields, and
    inputs_missing — the complete distribution the Phase-3 shadow log needs.
    Each dict also carries `rank_in_window` (1..N over the WHOLE window).
    """
    start_iso, end_iso = _window_bounds(as_of, days)
    caches = build_caches(conn)

    # Pull all BUYs, then filter by EFFECTIVE day in Python (announced_at may be
    # blank / non-ISO, so we can't filter purely in SQL without losing rows).
    rows = conn.execute(
        "SELECT t.fingerprint, t.date, t.ticker, t.company, t.director, "
        "       t.role, t.value, t.announced_at "
        "FROM transactions t "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.type = 'BUY' "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1"
    ).fetchall()

    scored: list[dict] = []
    for r in rows:
        eff = effective_announced_day(r)
        if not eff or eff < start_iso or eff > end_iso:
            continue
        result, meta = score_buy(conn, r, caches)
        scored.append({
            "fingerprint": _row_get(r, "fingerprint"),
            "date": eff,
            "ticker": _row_get(r, "ticker"),
            "company": _row_get(r, "company"),
            "director": _row_get(r, "director"),
            "role": _row_get(r, "role"),
            "tier": meta["tier"],
            "is_pca": meta["is_pca"],
            "value_gbp": _row_get(r, "value"),
            "inputs_missing": meta["inputs_missing"],
            "result": result.as_dict(),
            "score": result.score,
            "band": result.band,
        })

    scored.sort(key=lambda d: d["score"], reverse=True)
    for i, d in enumerate(scored, start=1):
        d["rank_in_window"] = i
    return scored
