"""Weekly Conviction Score — Phase 1 pure-compute scoring engine (B-171).

Spec: docs/specs/conviction-score-spec.md (§3 the six factors, §4 the
composite, §11 the locked decisions). This module is the "aggregation
layer" of build-phase 1 in §9: given a director BUY's fields it returns a
continuous 0-100 conviction score plus the six 0.0-1.0 sub-scores.

Core principle (§2): strength, NOT threshold. Every factor maps its raw
value to a 0.0-1.0 strength via a simple monotonic curve. Two buys that
both "fired" a binary badge can score very differently here.

Design constraints (per the build brief / CLAUDE.md Zone-A rules):
  * PURE COMPUTE. No DB connection, no I/O, no network. Every public
    function takes plain dicts / numbers and returns plain dicts / numbers,
    so each curve is independently unit-testable.
  * Field names are grounded against role_normalize.py and the snapshot
    CSV headers (transactions.csv, tickers_meta.csv) so the eventual
    Phase-2 wiring is realistic — but nothing here reads those files.
  * No claim of "expected return" (§5, §10): the honest label of this
    score in v1 is "signal strength / research priority".

Phase boundaries (explicitly NOT done here — later, Rupert-gated):
  * No wiring into eval_signals.py / the exporter / any renderer (Phase 2).
  * No measure-forward pick log or backtest join (Phase 3).
  * No weight re-fitting (Phase 4 calibration, deferred ~3 months). The
    weights below are judgment priors, NOT fitted to in-sample returns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# role_normalize lives one directory up's sibling — same dir as this file.
# Import is best-effort: the scoring functions themselves never need it
# (callers pass a tier string / role-strength directly), but the
# convenience role_strength_from_role() helper uses it when available.
try:  # pragma: no cover - import shim
    from role_normalize import normalize_role  # type: ignore
except Exception:  # pragma: no cover
    normalize_role = None  # type: ignore


# ---------------------------------------------------------------------------
# §4 — composite weights (provisional judgment priors, NOT fitted)
# ---------------------------------------------------------------------------
# Keys are the five ADDITIVE factors, all combined inside the same weighted
# sum / clamp01 (revised 2026-07-01 — see the sector-momentum note at F5
# below). These sum to exactly 1.0. Revised ONLY on out-of-sample forward
# data (§5/§7), or on an explicit Rupert-approved design change like this one.
#
# 2026-07-01 revision (conviction-sector-weighting-review, Part 3, APPROVED):
# sector momentum folds into the main weighted sum as a genuine 5th factor
# (sector_momentum, 0.20) instead of a post-hoc multiplier. The prior four
# weights are scaled by 0.80 so the whole dict still sums to 1.0.
WEIGHTS: dict[str, float] = {
    "who": 0.24,               # F1  (0.30 x 0.80)
    "buy_size": 0.24,          # F2  (0.30 x 0.80)
    "company_size": 0.176,     # F3  (0.22 x 0.80)
    "earnings_timing": 0.144,  # F4  (0.18 x 0.80)
    "sector_momentum": 0.20,   # F5  (was the F6 multiplier; see f5_sector_momentum)
    # F5 (past_performance, the old reversal-bias factor) removed 2026-07-01 —
    # direction unresolved pending forward data; will be re-added with correct
    # sign once proven. Not to be confused with the NEW "sector_momentum" key
    # above, which reuses the "F5" slot name in some docs/exports as the fifth
    # additive factor but is a distinct signal (sector net-buy momentum).
}

# §4 strength bands (lower-inclusive, upper-exclusive except the top band).
BANDS: tuple[tuple[float, float, str], ...] = (
    (0.0, 40.0, "Low"),
    (40.0, 60.0, "Moderate"),
    (60.0, 80.0, "High"),
    (80.0, 100.0001, "Exceptional"),
)


def band_for(score: float) -> str:
    """Map a 0-100 conviction score to its §4 strength band label."""
    for lo, hi, label in BANDS:
        if lo <= score < hi:
            return label
    # score clamps to [0,100], so this is only reachable at exactly 100.
    return "Exceptional"


def _clamp01(x: float) -> float:
    """Clamp to the [0.0, 1.0] strength range every factor must live in."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


# ===========================================================================
# F1 — Who is buying (role strength)            spec §3 F1
# ===========================================================================
# Continuous by seniority. Spec anchors: Chair / CEO / Founder ~= 1.0;
# CFO ~= 0.9; other exec ~= 0.7; Co Sec / GC ~= 0.5; NED ~= 0.3.
# Keyed on the 8-tier strings from signals/roles.py (T1a..T7), which is the
# project's load-bearing role taxonomy. T4 (catch-all) and T5 (bare PCA, no
# inheritance available) sit low. Note the spec's caution: NEDs lag in our
# scans, so T3 is deliberately weak.
_TIER_STRENGTH: dict[str, float] = {
    "T1a": 1.0,   # CEO / Founder
    "T7": 1.0,    # Chair (exec or non-exec) — spec groups Chair with CEO
    "T1b": 0.9,   # CFO
    "T2": 0.7,    # Other senior exec (Other Chief, Exec Dir, Divisional, Pres/VP)
    "T6": 0.5,    # Company Secretary / General Counsel
    "T3": 0.3,    # NED  (kept low — our scans show NEDs lag)
    "T4": 0.2,    # Catch-all (PDMR-only / Other / Parser fragment)
    "T5": 1.0,    # PCA — independently strong traders (decision 2026-07-01)
}
_DEFAULT_TIER_STRENGTH = 0.2


def role_strength_from_tier(tier: Optional[str]) -> float:
    """Map an 8-tier role string (T1a..T7) to a 0.0-1.0 role strength.

    Unknown / None tiers fall to the conservative catch-all (0.2).
    """
    if not tier:
        return _DEFAULT_TIER_STRENGTH
    return _TIER_STRENGTH.get(tier.strip(), _DEFAULT_TIER_STRENGTH)


def role_strength_from_role(role: Optional[str]) -> float:
    """Convenience: raw free-text role -> 0.0-1.0 strength.

    Uses role_normalize + the signals.roles tier mapping when importable;
    otherwise returns the catch-all. Kept thin so the curve stays testable
    without the role module present.
    """
    if normalize_role is None:
        return _DEFAULT_TIER_STRENGTH
    try:  # pragma: no cover - exercised in integration, not unit tests
        from signals.roles import classify_role  # type: ignore
        return role_strength_from_tier(classify_role(role))
    except Exception:  # pragma: no cover
        return _DEFAULT_TIER_STRENGTH


def f1_who(tier: Optional[str],
           is_pca: bool = False,
           company_top_tier: Optional[str] = None) -> float:
    """F1 role-strength sub-score (0.0-1.0).

    PCA inheritance (spec §3 F1, decision 2026-06-18): ANY PCA at the company
    counts and INHERITS the strength of the most-senior director at that same
    company. So a PCA buying where the Chair sits scores like a Chair.

    Args:
      tier:               this actor's own 8-tier string (T1a..T7).
      is_pca:             True if this row is a PCA (tier == "T5", or flagged
                          by the caller). When True we use the inherited
                          company-top tier instead of the bare-PCA strength.
      company_top_tier:   the most-senior director tier seen at this company
                          (computed upstream in Phase 2 from the company's
                          roster). If a PCA and this is provided, the PCA
                          inherits it. If None, the PCA falls back to its own
                          (low) T5 strength.

    The caller decides who is a PCA — we accept either an explicit is_pca
    flag or tier == "T5". PCAs score at max (1.0) independently: they are
    consistently among the strongest traders in our data and are no longer
    treated as proxies for their linked director (decision 2026-07-01).
    The company_top_tier arg is retained for signature compatibility but is
    no longer used.
    """
    pca = is_pca or (tier or "").strip() == "T5"
    if pca:
        return 1.0  # PCA always max — independent strong-trader evidence
    return role_strength_from_tier(tier)


# ===========================================================================
# F2 — Size of buy                              spec §3 F2
# ===========================================================================
# Two inputs blended: absolute £, and £ relative to the stock's normal daily
# turnover (the relative measure is what makes a small company's buy
# comparable to a big one's). Both log-scaled and bigger = stronger.
#
# Absolute curve: log-anchored so £5k -> ~0.0 and £5m -> ~1.0, smoothly. We
# use a log10 ramp between the two anchors rather than a fitted percentile
# (we have no in-sample distribution to fit to in Phase 1 — §5).
_ABS_FLOOR_GBP = 5_000.0       # below this, absolute size adds little
_ABS_CEIL_GBP = 5_000_000.0    # at/above this, absolute size is maxed
# Relative curve: buy value as a fraction of average daily £ turnover.
# 0.1x daily volume -> ~0.0, 5x daily volume -> ~1.0 (a buy worth multiple
# days of trading is a very large relative commitment).
_REL_FLOOR = 0.1
_REL_CEIL = 5.0
# Blend weight: how much the relative measure counts vs absolute. The spec
# calls the relative measure the one that makes cross-company comparison
# fair, so it gets the majority weight — but only when we have volume data.
_REL_BLEND = 0.6


def _log_ramp(value: float, floor: float, ceil: float) -> float:
    """0.0 at/below `floor`, 1.0 at/above `ceil`, log10-linear between."""
    if value <= floor:
        return 0.0
    if value >= ceil:
        return 1.0
    lo, hi = math.log10(floor), math.log10(ceil)
    return _clamp01((math.log10(value) - lo) / (hi - lo))


def f2_buy_size(value_gbp: Optional[float],
                avg_daily_turnover_gbp: Optional[float] = None) -> float:
    """F2 buy-size sub-score (0.0-1.0).

    Args:
      value_gbp:                the BUY's £ value (transactions.value).
      avg_daily_turnover_gbp:   the stock's average daily £ turnover (price ×
                                volume), for the relative measure. Optional:
                                if absent (no volume join yet — §8), we fall
                                back to the absolute curve alone, so the score
                                still works on the data we have today.

    Bigger = stronger on both axes.
    """
    v = value_gbp or 0.0
    abs_strength = _log_ramp(v, _ABS_FLOOR_GBP, _ABS_CEIL_GBP)
    if not avg_daily_turnover_gbp or avg_daily_turnover_gbp <= 0:
        # No volume data -> absolute only (graceful Phase-1 degrade).
        return _clamp01(abs_strength)
    rel = v / avg_daily_turnover_gbp
    rel_strength = _log_ramp(rel, _REL_FLOOR, _REL_CEIL)
    blended = (_REL_BLEND * rel_strength) + ((1.0 - _REL_BLEND) * abs_strength)
    return _clamp01(blended)


# ===========================================================================
# F3 — Company size (market cap)                spec §3 F3
# ===========================================================================
# Smaller = stronger. Inverse-log of market cap, normalised. Spec anchors:
# micro-cap ~= 1.0, large-cap ~= 0.1. We ramp DOWN over the log-cap range
# from a micro-cap floor to a large-cap ceiling.
_CAP_MICRO_GBP = 50_000_000.0       # £50m and below -> ~1.0 (micro-cap)
_CAP_LARGE_GBP = 10_000_000_000.0   # £10bn and above -> ~0.1 (large-cap)
_CAP_LARGE_FLOOR = 0.1              # spec: large-cap ~= 0.1, not 0.0


def f3_company_size(market_cap_gbp: Optional[float]) -> float:
    """F3 company-size sub-score (0.0-1.0). Smaller cap = stronger.

    Inverse-log ramp: <= £50m -> 1.0, >= £10bn -> 0.1, log10-linear between.
    Missing cap (the ~21% with no coverage, §8) returns a neutral 0.5 rather
    than penalising or maxing — we don't know, so we don't bias.
    """
    if not market_cap_gbp or market_cap_gbp <= 0:
        return 0.5  # unknown cap -> neutral, neither rewarded nor penalised
    if market_cap_gbp <= _CAP_MICRO_GBP:
        return 1.0
    if market_cap_gbp >= _CAP_LARGE_GBP:
        return _CAP_LARGE_FLOOR
    lo, hi = math.log10(_CAP_MICRO_GBP), math.log10(_CAP_LARGE_GBP)
    frac = (math.log10(market_cap_gbp) - lo) / (hi - lo)  # 0 at micro, 1 at large
    # Ramp from 1.0 (micro) down to _CAP_LARGE_FLOOR (large).
    return _clamp01(1.0 - frac * (1.0 - _CAP_LARGE_FLOOR))


# ===========================================================================
# F4 — Earnings proximity                       spec §3 F4
# ===========================================================================
# A CURVE over days-to-next-results, not a flag:
#   * inside the ~30-day close period -> INVALID (directors can't legally
#     deal; treat as a data error to investigate, score 0 and let the caller
#     decide whether to surface a warning).
#   * just before lockout (~31-45 days out) -> ELEVATED (bought right before
#     going dark) — this is the spec's worked example.
#   * just after results (recently reported) -> HIGH (saw the numbers, still
#     bought).
#   * mid-cycle -> LOW.
#
# Inputs come from B-114 (forward earnings date) and B-161 (days since last
# results). The forward earnings date is the known coverage ceiling (~23%,
# §8); when it is missing the COMPOSITE drops this factor and re-normalises
# (handled in conviction_score, decision 2026-06-18), so this function is
# only called when at least one of the two timing signals is known.
_CLOSE_PERIOD_DAYS = 30        # legal close period before results
_LOCKOUT_WINDOW_END = 45       # "just before lockout" upper bound
_POST_RESULTS_WINDOW = 21      # "just after results" window (days since)


def f4_earnings_timing(days_to_next_results: Optional[float] = None,
                       days_since_last_results: Optional[float] = None) -> float:
    """F4 earnings-proximity sub-score (0.0-1.0).

    Provide at least one of the two timing inputs. If both are None the
    composite should DROP this factor (see conviction_score) — this function
    returns 0.0 in that case as a safe default, but the re-normalisation is
    the intended path, not this return value.

    Curve shape (spec §3 F4):
      days_to_next_results in [0, 30)   -> 0.0  (inside close period: invalid)
      days_to_next_results in [31, 45]  -> ~1.0 (just before lockout: elevated)
      days_to_next_results > 45         -> decays toward LOW (mid-cycle)
      days_since_last_results in [0,21] -> high, decaying (just after results)

    When both are available we take the stronger of the two reads (a buy that
    is both just-after-old-results and just-before-new-results is maximally
    interesting).
    """
    reads: list[float] = []

    if days_to_next_results is not None:
        d = days_to_next_results
        if d < 0:
            pass  # nonsensical; ignore this read
        elif d < _CLOSE_PERIOD_DAYS:
            # Inside the legal close period — directors cannot deal. Invalid.
            reads.append(0.0)
        elif d <= _LOCKOUT_WINDOW_END:
            # Just before lockout: peak of the curve.
            reads.append(1.0)
        else:
            # Mid-cycle decay: linear from 1.0 at day 45 down to ~0.1 by ~120
            # days out, floored at 0.1 (still some signal, never zero).
            span = 120.0 - _LOCKOUT_WINDOW_END
            decayed = 1.0 - ((d - _LOCKOUT_WINDOW_END) / span) * 0.9
            reads.append(_clamp01(max(0.1, decayed)))

    if days_since_last_results is not None:
        s = days_since_last_results
        if s < 0:
            pass  # results are in the future for this input; ignore
        elif s <= _POST_RESULTS_WINDOW:
            # Just after results: high, decaying from 0.9 at day 0 to ~0.3 by
            # day 21. (Slightly below the pre-lockout peak per spec ordering:
            # "elevated" for pre-lockout is the worked example; post-results
            # is "high" but flagged coverage-confounded -> don't over-reward.)
            reads.append(_clamp01(0.9 - (s / _POST_RESULTS_WINDOW) * 0.6))
        else:
            reads.append(0.1)  # mid-cycle floor

    if not reads:
        return 0.0  # no usable timing input; composite should drop F4 instead
    return max(reads)


# ===========================================================================
# F5 — Past stock performance (reversal bias)   spec §3 F5
# ===========================================================================
# Trailing 1-3 month return, INVERTED: buying AFTER a fall (dip-buy /
# contrarian conviction) scores higher than buying INTO a rally. Capped so a
# crash doesn't automatically max it out. Reuses the B-159 reversal idea.
#
# Mapping: trailing return r (as a decimal, e.g. -0.20 = down 20%).
#   r >= +0.20 (rallied >=20%)  -> ~0.0  (buying into strength: weak signal)
#   r ==  0.00 (flat)           -> ~0.5  (neutral)
#   r <= -0.30 (fell >=30%)     -> ~1.0  (deep dip-buy), CAPPED here so a
#                                         -80% crash doesn't read differently
#                                         from a -30% dip.
_PERF_RALLY_CAP = 0.20    # +20% rally -> strength 0.0
_PERF_DIP_CAP = -0.30     # -30% fall  -> strength 1.0 (capped)


def f5_past_performance(trailing_return: Optional[float]) -> float:
    """F5 reversal/dip-buy sub-score (0.0-1.0). Inverted trailing return.

    Args:
      trailing_return: trailing ~1-3 month total return as a DECIMAL
                       (-0.20 == down 20%, +0.15 == up 15%).

    Missing data -> neutral 0.5 (we don't know the prior move, so don't bias).
    A buy after a fall scores high; a buy into a rally scores low; capped at
    both ends so an extreme crash reads the same as a -30% dip.

    NOTE: this factor is currently NOT part of WEIGHTS / composite() — it was
    removed 2026-07-01 pending forward data (direction unresolved). The
    function is kept for any external callers and for a possible future
    re-add with a validated sign. It is unrelated to the NEW
    f5_sector_momentum() factor below, which is the current 5th weighted
    factor despite the shared "F5" label collision in older docs.
    """
    if trailing_return is None:
        return 0.5
    r = trailing_return
    if r <= _PERF_DIP_CAP:
        return 1.0
    if r >= _PERF_RALLY_CAP:
        return 0.0
    # Linear interpolation: map [_PERF_DIP_CAP .. _PERF_RALLY_CAP] -> [1.0 .. 0.0]
    frac = (r - _PERF_DIP_CAP) / (_PERF_RALLY_CAP - _PERF_DIP_CAP)  # 0 at dip, 1 at rally
    return _clamp01(1.0 - frac)


# ===========================================================================
# F5 — Sector momentum (additive factor, superseded the F6 multiplier 2026-07-01)
# ===========================================================================
# See docs/agents/quant-researcher.md-produced review
# outputs/conviction-sector-weighting-review-2026-07-02.md Part 3 (Rupert
# APPROVED): sector net-buy momentum is now a genuine 5th weighted factor
# inside the same additive composite as who/buy_size/company_size/
# earnings_timing, NOT a post-hoc multiplier applied after clamp01. This
# replaces the old f6_sector_multiplier() mechanism below (kept, deprecated).

def f5_sector_momentum(net_buy_count_30d: Optional[float], k: float = 12.0) -> float:
    """Sector-momentum sub-score (0.0-1.0): sigmoid of trailing 30-day net director buy/sell count.

    net_buy_count_30d: (# BUY) - (# SELL + # SELL_TAX) transactions in that sector over the
    trailing 30 calendar days (flat window, no decay — Rupert's explicit spec, per Part 3 of
    the sector-weighting review). None/missing -> neutral 0.5 (unknown sector, don't bias).

    subscore = 1 / (1 + exp(-net_buy_count_30d / k)), so net=0 -> 0.5 neutral,
    strongly positive net buying -> approaches 1.0, strongly negative -> approaches 0.0.
    k=12 was calibrated against the observed live distribution (see the review doc) so the
    hottest sector seen in ~3 months of data (net~+24) reads ~0.88, not pinned near 1.0.
    """
    if net_buy_count_30d is None:
        return 0.5
    x = float(net_buy_count_30d) / k
    subscore = 1.0 / (1.0 + math.exp(-x))
    return _clamp01(subscore)


# ===========================================================================
# F6 — Dynamic sector multiplier (net-buy volume, DEPRECATED 2026-07-02)
# ===========================================================================
# DEPRECATED — superseded 2026-07-02 by f5_sector_momentum(), see
# docs/agents/quant-researcher.md-produced review
# outputs/conviction-sector-weighting-review-2026-07-02.md Part 3. Kept in
# place (not deleted) in case any other script still imports these; they are
# no longer called by conviction_score()/composite().
#
# F6 was computed live from net director activity in the trailing 30
# calendar days: net_buys = BUY count − (SELL + SELL_TAX) count per sector.
#
# Net-buy → F6 score mapping (symmetric dead-band ±4):
#   net ≥ 21          →  +1.00   (sector_mult = 2.00)
#   15 ≤ net ≤ 20     →  +0.75   (sector_mult = 1.75)
#   10 ≤ net ≤ 14     →  +0.50   (sector_mult = 1.50)
#    5 ≤ net ≤  9     →  +0.25   (sector_mult = 1.25)
#   −4 ≤ net ≤  4     →   0.00   (sector_mult = 1.00  — neutral dead-band)
#   −9 ≤ net ≤ −5     →  −0.25   (sector_mult = 0.75)
#  −14 ≤ net ≤ −10    →  −0.50   (sector_mult = 0.50)
#  −20 ≤ net ≤ −15    →  −0.75   (sector_mult = 0.25)
#        net ≤ −21    →  −1.00   (sector_mult = 0.00 — score zeroed)
#
# sector_mult = max(0.0, 1.0 + f6_score)


def net_buys_to_f6(net: int) -> float:
    """DEPRECATED — superseded 2026-07-02 by f5_sector_momentum(), see
    docs/agents/quant-researcher.md-produced review
    outputs/conviction-sector-weighting-review-2026-07-02.md Part 3.

    Map trailing-30d net buy count to an F6 score (-1.0 … +1.0).

    Symmetric dead-band of ±4; returns one of:
    {-1.0, -0.75, -0.5, -0.25, 0.0, +0.25, +0.5, +0.75, +1.0}.
    """
    if net >= 21:
        return 1.0
    if net >= 15:
        return 0.75
    if net >= 10:
        return 0.5
    if net >= 5:
        return 0.25
    if net >= -4:
        return 0.0
    if net >= -9:
        return -0.25
    if net >= -14:
        return -0.5
    if net >= -20:
        return -0.75
    return -1.0


def f6_sector_multiplier(sector: Optional[str] = None,
                         sector_f6_map: Optional[dict] = None) -> float:
    """DEPRECATED — superseded 2026-07-02 by f5_sector_momentum(), see
    docs/agents/quant-researcher.md-produced review
    outputs/conviction-sector-weighting-review-2026-07-02.md Part 3.

    F6 sector multiplier from the live 30-day net-buy map.

    Args:
      sector:        sector tag from tickers_meta (e.g. "Financials").
      sector_f6_map: pre-computed {sector: f6_score} dict produced by
                     conviction_pipeline.compute_sector_f6_map(conn).
                     Pass None (or omit) for unit tests → neutral 1.0.

    Returns sector_mult = max(0.0, 1.0 + f6_score).
      f6 = +1.0  → mult 2.00 (strong net buying)
      f6 =  0.0  → mult 1.00 (neutral / dead-band)
      f6 = -1.0  → mult 0.00 (strong net selling; score zeroed)
    Final score is still capped at 100 by the composite() function.
    """
    if not sector or not sector_f6_map:
        return 1.0  # unknown sector / no map → neutral
    f6 = sector_f6_map.get(sector.strip(), 0.0)
    return max(0.0, 1.0 + f6)


# Deprecated — kept for any callers that haven't been updated yet.
def f6_sector_guardrail(sector_beta_hotness: Optional[float] = None) -> float:
    """DEPRECATED — superseded 2026-07-02 by f5_sector_momentum(), see
    docs/agents/quant-researcher.md-produced review
    outputs/conviction-sector-weighting-review-2026-07-02.md Part 3.

    Use f5_sector_momentum(net_buy_count_30d) instead.
    """
    if sector_beta_hotness is None:
        return 1.0
    h = _clamp01(sector_beta_hotness)
    return 1.0 - h * (1.0 - 0.7)


# ===========================================================================
# §4 — composite score
# ===========================================================================

@dataclass
class ConvictionResult:
    """The full conviction read for one BUY: 0-100 score, band, sub-scores."""
    score: float                       # 0-100
    band: str                          # Low / Moderate / High / Exceptional
    subscores: dict[str, float] = field(default_factory=dict)  # each 0.0-1.0
    weights_used: dict[str, float] = field(default_factory=dict)  # post-renorm
    sector_momentum: float = 0.5       # the 0.0-1.0 sector_momentum sub-score actually used
    earnings_dropped: bool = False     # True if F4 was dropped & re-normalised

    def as_dict(self) -> dict:
        """Plain-dict view for the eventual JSON export (Phase 2)."""
        return {
            "score": self.score,
            "band": self.band,
            "subscores": dict(self.subscores),
            "weights_used": dict(self.weights_used),
            "sector_momentum": self.sector_momentum,
            "earnings_dropped": self.earnings_dropped,
        }


def _renormalise(weights: dict[str, float], drop: set[str]) -> dict[str, float]:
    """Drop `drop` keys and rescale the rest so they sum to 1.0 again.

    Used for the missing-earnings-date case (decision 2026-06-18): drop F4
    and re-normalise the remaining weights so a missing date neither
    blocks nor penalises the buy. Generic over whatever keys are present in
    `weights`, so it works unchanged whether there are four or five factors.
    """
    kept = {k: v for k, v in weights.items() if k not in drop}
    total = sum(kept.values())
    if total <= 0:
        return kept
    return {k: v / total for k, v in kept.items()}


def composite(subscores: dict[str, float],
              drop_earnings: bool = False,
              weights: Optional[dict[str, float]] = None) -> ConvictionResult:
    """Combine 0.0-1.0 sub-scores into a 0-100 ConvictionResult (spec §4).

    formula:  100 × clamp01( Σ wi·Fi )

    Args:
      subscores:     dict with keys "who", "buy_size", "company_size",
                     "earnings_timing", "sector_momentum", each 0.0-1.0.
                     sector_momentum is a genuine 5th weighted factor inside
                     this same sum (revised 2026-07-01 — see f5_sector_momentum);
                     there is no longer a separate post-clamp multiplier.
      drop_earnings: when True (earnings date unknown — decision
                     2026-06-18), drop "earnings_timing" and re-normalise
                     the remaining weights so they sum to 1.0.
      weights:       override the default WEIGHTS (Phase-4 calibration).

    The additive sum is clamped to [0,1], and the final score (100x that) is
    therefore naturally bounded to [0,100] — no separate clamp needed.
    """
    w = dict(weights or WEIGHTS)
    drop: set[str] = {"earnings_timing"} if drop_earnings else set()
    w_eff = _renormalise(w, drop) if drop else w

    weighted_sum = 0.0
    for key, weight in w_eff.items():
        weighted_sum += weight * _clamp01(subscores.get(key, 0.0))
    weighted_sum = _clamp01(weighted_sum)

    score = 100.0 * weighted_sum
    if score < 0.0:
        score = 0.0
    elif score > 100.0:
        score = 100.0

    return ConvictionResult(
        score=score,
        band=band_for(score),
        subscores={k: _clamp01(subscores.get(k, 0.0)) for k in WEIGHTS},
        weights_used=w_eff,
        sector_momentum=_clamp01(subscores.get("sector_momentum", 0.5)),
        earnings_dropped=drop_earnings,
    )


def conviction_score(*,
                     tier: Optional[str] = None,
                     is_pca: bool = False,
                     company_top_tier: Optional[str] = None,
                     value_gbp: Optional[float] = None,
                     avg_daily_turnover_gbp: Optional[float] = None,
                     market_cap_gbp: Optional[float] = None,
                     days_to_next_results: Optional[float] = None,
                     days_since_last_results: Optional[float] = None,
                     sector: Optional[str] = None,
                     sector_net_buy_map: Optional[dict] = None,      # raw {sector: net_buy_count_30d}
                     # Deprecated params kept for call-site compat during migration:
                     trailing_return: Optional[float] = None,       # ignored
                     sector_beta_hotness: Optional[float] = None,   # ignored
                     weights: Optional[dict[str, float]] = None
                     ) -> ConvictionResult:
    """End-to-end convenience: raw inputs -> 0-100 ConvictionResult.

    This is the single entry point Phase 2 will call per BUY. It computes the
    five factor curves, applies the missing-earnings-date rule, and combines.
    All inputs are plain numbers / strings / None, so it stays DB-free and
    unit-testable.

    Missing-earnings rule (decision 2026-06-18): if BOTH timing inputs are
    None we DROP F4 and re-normalise the remaining weights — a missing
    forward earnings date (the known ~23% coverage ceiling, §8) must not
    block or penalise the buy.

    Sector momentum (revised 2026-07-01): `sector_net_buy_map` is the RAW
    {sector: net_buy_count_30d} dict (NOT pre-mapped to a -1..+1 score, unlike
    the deprecated sector_f6_map). We look up this buy's sector and pass the
    raw count through f5_sector_momentum() to get the 0.0-1.0 sub-score, then
    fold it into `subscores["sector_momentum"]` alongside who/buy_size/
    company_size/earnings_timing — it is weighted and summed by composite()
    exactly like the other four factors, not applied as a post-hoc multiplier.

    Field-name mapping for Phase-2 wiring (grounded against snapshots):
      tier                    <- signals.roles.classify_role(tx["role"])
      is_pca                  <- tier == "T5" (or an explicit PCA flag)
      company_top_tier        <- most-senior director tier at tx["ticker"]
      value_gbp               <- tx["value"]
      avg_daily_turnover_gbp  <- price × volume join (NOT in DB yet — §8)
      market_cap_gbp          <- tickers_meta.market_cap_gbp
      days_to_next_results    <- B-114 forward earnings date - tx["date"]
      days_since_last_results <- tx["date"] - B-161 last results date
      sector_net_buy_map      <- conviction_pipeline.compute_sector_net_buy_count_map(conn)
    """
    drop_earnings = (days_to_next_results is None
                     and days_since_last_results is None)

    net_buy_count = None
    if sector and sector_net_buy_map:
        net_buy_count = sector_net_buy_map.get(sector.strip())

    subscores = {
        "who": f1_who(tier, is_pca=is_pca, company_top_tier=company_top_tier),
        "buy_size": f2_buy_size(value_gbp, avg_daily_turnover_gbp),
        "company_size": f3_company_size(market_cap_gbp),
        "earnings_timing": (0.0 if drop_earnings else f4_earnings_timing(
            days_to_next_results, days_since_last_results)),
        "sector_momentum": f5_sector_momentum(net_buy_count),
    }

    return composite(
        subscores,
        drop_earnings=drop_earnings,
        weights=weights,
    )
