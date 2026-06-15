"""director_pay.py -- helpers for the salary-multiple conviction feature (B-168).

Pure helpers + DB read/write for the `director_pay` table (migration 015). No
network here -- collection (curl/pdftotext/search) lives in backfill_director_pay.py
(Zone B). These functions are unit-tested in the sandbox.

Spec: docs/specs/b168-salary-multiple-plan.md.

Key ideas
---------
* DUAL denominator: store BOTH single-figure total comp and base salary (where
  separable) as separate rows; compute two multiples downstream.
* The salary multiple is deliberately NULL for zero/nominal pay and for any
  no-figure status -- never a divide-by-zero or a fake number.
* LOOKAHEAD guard: a pay figure is only knowable once its annual report is
  published, so it may attach to a buy only when ar_published_at <= buy date.
* director_key joins to transactions via routine_flag.director_key() -- reused,
  not duplicated, so the feature attaches to the same director identity the
  routine/reversal flags use.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
from routine_flag import director_key  # noqa: E402  -- reuse the canonical key

__all__ = [
    "director_key", "PAY_TYPES", "PAY_STATUSES", "NO_MULTIPLE_PAY_TYPES",
    "NOMINAL_PAY_FLOOR_GBP", "convert_to_gbp", "classify_nominal",
    "salary_multiple", "upsert_director_pay", "latest_pay_before",
]

# --- Constants --------------------------------------------------------------

PAY_TYPES = (
    "single_figure_total", "base_salary", "ned_fees",
    "fee_waiver_zero", "nominal", "none",
)
PAY_STATUSES = (
    "ok", "new_appointee_no_disclosure", "out_of_scope", "extraction_fail",
)
# pay_types for which a salary multiple is meaningless (NULL by design)
NO_MULTIPLE_PAY_TYPES = frozenset({"fee_waiver_zero", "nominal", "none"})

# Below this GBP figure a salary multiple is noise (KZG's GBP 5k interim, etc.),
# so it is bucketed as 'nominal' rather than dividing a buy by a near-zero pay.
NOMINAL_PAY_FLOOR_GBP = 10_000.0

# Curated FY-end FX rates (native -> GBP). Seeded from the B-163 spike (USD ~0.79,
# EUR ~0.845). Keyed by (currency, year-of-fy-end); falls back to the currency
# default when a specific year is absent. Extend as new FYs are collected; a
# production refinement is to pin true FY-end rates per report. GBP is always 1.0.
_FX_RATES: dict[str, dict] = {
    "GBP": {"default": 1.0},
    "USD": {"default": 0.79, 2023: 0.785, 2024: 0.79, 2025: 0.79, 2026: 0.79},
    "EUR": {"default": 0.845, 2023: 0.865, 2024: 0.845, 2025: 0.845, 2026: 0.845},
}


# --- Pure helpers (no DB, no network) ---------------------------------------

def _fy_year(fy_end: str | None) -> int | None:
    """Year integer from an ISO-ish fy_end string, else None."""
    if not fy_end:
        return None
    try:
        return int(str(fy_end)[:4])
    except (ValueError, TypeError):
        return None


def convert_to_gbp(pay_native, currency: str | None, fy_end: str | None) -> dict | None:
    """Convert a native pay figure to GBP using the curated FY-end rate table.

    Returns {pay_gbp, fx_rate, fx_date, currency} or None when the figure is
    missing or the currency is unsupported. fx_date echoes fy_end (the date the
    rate is meant to represent). GBP passes through at rate 1.0.
    """
    if pay_native is None:
        return None
    try:
        native = float(pay_native)
    except (ValueError, TypeError):
        return None
    cur = (currency or "GBP").strip().upper()
    table = _FX_RATES.get(cur)
    if table is None:
        return None  # unsupported currency -- caller flags for manual handling
    rate = table.get(_fy_year(fy_end), table["default"])
    return {
        "pay_gbp": round(native * rate, 2),
        "fx_rate": rate,
        "fx_date": fy_end or None,
        "currency": cur,
    }


def classify_nominal(pay_gbp) -> str:
    """Map a GBP pay figure to a pay_type bucket for the multiple's purposes.

    'fee_waiver_zero' for <= 0, 'nominal' below the floor, else 'ok' (meaning a
    real figure -- the caller keeps single_figure_total / base_salary / ned_fees).
    """
    try:
        v = float(pay_gbp)
    except (ValueError, TypeError):
        return "ok"
    if v <= 0:
        return "fee_waiver_zero"
    if v < NOMINAL_PAY_FLOOR_GBP:
        return "nominal"
    return "ok"


def salary_multiple(buy_value_gbp, *, pay_gbp, pay_status="ok",
                    pay_type="single_figure_total"):
    """buy_value_gbp / pay_gbp, or None when the multiple is not meaningful.

    Returns None for any non-'ok' status, any no-multiple pay_type
    (fee_waiver_zero/nominal/none), a non-positive pay, or a missing buy value.
    """
    if pay_status != "ok":
        return None
    if pay_type in NO_MULTIPLE_PAY_TYPES:
        return None
    if buy_value_gbp is None or pay_gbp is None:
        return None
    try:
        bv = float(buy_value_gbp)
        pg = float(pay_gbp)
    except (ValueError, TypeError):
        return None
    if pg <= 0:
        return None
    return bv / pg


# --- DB read/write ----------------------------------------------------------

_UPSERT_COLS = (
    "ticker", "director_key", "director_name_raw", "fy_end", "ar_published_at",
    "pay_native", "currency", "fx_rate", "fx_date", "pay_gbp", "pay_type",
    "role_class", "pay_status", "source_rung", "source_url", "confidence",
    "machine_readable", "fetched_at",
)


def upsert_director_pay(conn, rec: dict) -> None:
    """Idempotent upsert on (ticker, director_key, fy_end, pay_type).

    `rec` may omit fetched_at (defaults to db.iso_now()) and fy_end (defaults
    to '' for no-figure rows). ticker/director_key/pay_type are required.
    Re-running with a fresher figure overwrites the provenance fields.
    """
    row = dict(rec)
    row.setdefault("fetched_at", db.iso_now())
    row.setdefault("fy_end", "")
    row.setdefault("pay_status", "ok")
    row.setdefault("machine_readable", 0)
    for c in _UPSERT_COLS:
        row.setdefault(c, None)
    placeholders = ", ".join("?" for _ in _UPSERT_COLS)
    updates = ", ".join(
        f"{c} = excluded.{c}" for c in _UPSERT_COLS
        if c not in ("ticker", "director_key", "fy_end", "pay_type")
    )
    conn.execute(
        f"INSERT INTO director_pay ({', '.join(_UPSERT_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(ticker, director_key, fy_end, pay_type) DO UPDATE SET {updates}",
        tuple(row[c] for c in _UPSERT_COLS),
    )


def latest_pay_before(conn, ticker: str, dkey: str, as_of_date: str,
                      pay_type: str = "single_figure_total"):
    """Latest lookahead-safe pay row for a director as of `as_of_date`.

    Returns the most recent (by fy_end) 'ok' row of the given pay_type whose
    annual report was published on or before as_of_date -- i.e. the figure was
    public when the buy happened. Rows with NULL ar_published_at are excluded by
    the guard. Returns a sqlite3.Row (or None).
    """
    return conn.execute(
        "SELECT * FROM director_pay "
        "WHERE ticker = ? AND director_key = ? AND pay_type = ? "
        "  AND pay_status = 'ok' AND pay_gbp IS NOT NULL "
        "  AND ar_published_at IS NOT NULL AND ar_published_at <= ? "
        "ORDER BY fy_end DESC LIMIT 1",
        (ticker, dkey, pay_type, as_of_date),
    ).fetchone()
