"""B-060 — price unit reconciliation (pence vs pounds vs garbage).

`parse_pdmr.py` stores `price` in pounds, but a BARE numeric price cell with
no currency marker is ambiguous: UK RNS tables usually quote pence, so "171"
means 171p = GBP 1.71, not GBP 171. A naive "bare => pence" rule was tried and
reverted because it corrupted genuine pound-quoted prices. The robust fix is to
disambiguate against the market close we already store in the `prices` table:
an on-market PDMR deal executes near the day's close, so the correct reading is
the one within tolerance of it.

This module is PURE (no DB, no IO) so it is fully unit-testable. The caller
(a Zone-B backfill / the ingest path) supplies the market close it looked up.

Statuses returned by `reconcile_price`:
  * ok_pounds            -- the stored pounds reading is right; no change
  * corrected_pence      -- it was pence-as-pounds; divide by 100
  * unresolved           -- neither reading matches market (garbage / Mode B);
                            do NOT trust the value (caller flags + nulls value)
  * no_market            -- no market close available; cannot decide

See docs/specs/b-060-pence-value-plan.md.
"""
from __future__ import annotations

# Tolerance band around the market close. An on-market deal is typically within
# a few percent of the same-day close; we allow 35% to absorb VWAP / multi-
# tranche prints and a close taken from a nearby trading day. A 100x pence error
# sits ~9900% away, so this band separates the two readings cleanly.
TOL = 0.35

# Hard ceiling backstop: a single PDMR transaction line worth more than this is
# almost certainly a garbage price (total grabbed as per-share). Used only to
# catch Mode-B rows whose bad reading happens to land near market by accident.
VALUE_CEILING_GBP = 100_000_000.0


def _within(ratio: float) -> bool:
    return (1.0 - TOL) <= ratio <= (1.0 + TOL)


def reconcile_price(
    price_raw_gbp,
    shares,
    market_close_gbp,
    tx_type: str | None = None,
):
    """Reconcile a stored (pounds-assumed) price against the market close.

    Returns (price_gbp, status). `price_gbp` is the resolved per-share price in
    pounds (unchanged for ok_pounds / the un-trusted readings; raw/100 for
    corrected_pence). The caller decides what to do per status.
    """
    # Nil-cost / zero price (grants, nil-cost options): nothing to reconcile.
    if price_raw_gbp is None or price_raw_gbp <= 0:
        return price_raw_gbp, "ok_pounds"

    if market_close_gbp is None or market_close_gbp <= 0:
        return price_raw_gbp, "no_market"

    r_pounds = price_raw_gbp / market_close_gbp
    r_pence = (price_raw_gbp / 100.0) / market_close_gbp
    pounds_ok = _within(r_pounds)
    pence_ok = _within(r_pence)

    if pounds_ok and not pence_ok:
        return price_raw_gbp, "ok_pounds"
    if pence_ok and not pounds_ok:
        # Backstop: even the pence reading must not exceed the value ceiling.
        if shares and (price_raw_gbp / 100.0) * shares > VALUE_CEILING_GBP:
            return price_raw_gbp, "unresolved"
        return round(price_raw_gbp / 100.0, 6), "corrected_pence"
    if pounds_ok and pence_ok:
        # Both fit only when close is tiny and raw ~= close (sub-penny stock);
        # the pounds reading is the literal value, so leave it unchanged.
        return price_raw_gbp, "ok_pounds"

    # Neither reading is near the market close -> garbage (Mode B) or a price
    # genuinely far from market. Do not trust it.
    return price_raw_gbp, "unresolved"
