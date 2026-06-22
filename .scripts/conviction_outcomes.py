"""B-171 Phase 3 — Conviction Score measure-forward join.

Read-only pairing of the `conviction_scores` shadow log (every scored buy in
each rolling 28-day window) to the backtest output, ON fingerprint, so the
score / band / sub-scores can later be regressed against realised forward CAR
(spec §7). This is the harness the adaptive-calibration loop (Phase 4) consumes;
it does NOT fit weights and does NOT write anything.

CRITICAL HORIZON MAPPING (regression-guarded in the test):
    T+21 == backtest column ``car_t30``   (NOT ``car_t21`` — that column does
                                            not exist; see backtest.OFFSET_TO_HORIZON)
    T+90 == backtest column ``car_t90``
Net-of-cost counterparts are ``net_car_t30`` / ``net_car_t90``.

UNTRACKED BUYS: the backtest CSV only contains buys on which a signal FIRED.
Most scored buys never fired a signal, so they have no CAR row. Those are kept
with CAR = None and ``tracked = False`` ("untracked") rather than dropped — the
shadow log's whole point (§7) is to test the score across the FULL distribution,
including buys no binary signal flagged.

Stdlib-only. Reads the DB ``mode=ro`` and the backtest CSV read-only.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import db  # noqa: E402

DEFAULT_CSV_PATH = db.DB_DIR / "_backtest_results.csv"

# The EXACT backtest column names for the spec's two horizons. Hard-coded and
# regression-guarded — a rename upstream must fail the test, not silently emit
# blank CAR. T+21 -> car_t30, T+90 -> car_t90 (backtest.OFFSET_TO_HORIZON).
CAR_T21_COL = "car_t30"
CAR_T90_COL = "car_t90"
NET_CAR_T21_COL = "net_car_t30"
NET_CAR_T90_COL = "net_car_t90"


def _to_float(value) -> Optional[float]:
    """Parse a CSV cell to float, or None for blank / unparseable."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() in ("none", "null", "nan"):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load_backtest_cars(csv_path: Path | None = None) -> dict:
    """Map fingerprint -> {car_t21, car_t90, net_car_t21, net_car_t90}.

    Reads the backtest results CSV. When a fingerprint fired more than one
    signal it appears on multiple rows with identical CARs (CAR is per-buy, not
    per-signal), so the last row wins harmlessly. Missing CSV -> empty map.

    The four output keys use the SPEC horizon names (t21 / t90), mapped from the
    backtest's t30 / t90 columns per CAR_T21_COL / CAR_T90_COL above.
    """
    path = csv_path or DEFAULT_CSV_PATH
    if not Path(path).exists():
        return {}
    out: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        # Regression guard: the exact columns must be present in the header.
        for col in (CAR_T21_COL, CAR_T90_COL):
            if col not in (reader.fieldnames or []):
                raise KeyError(
                    f"backtest CSV missing expected column {col!r}. "
                    f"T+21 must map to {CAR_T21_COL!r} and T+90 to "
                    f"{CAR_T90_COL!r} (backtest.OFFSET_TO_HORIZON). "
                    f"There is no car_t21 column."
                )
        for row in reader:
            fp = (row.get("fingerprint") or "").strip()
            if not fp:
                continue
            out[fp] = {
                "car_t21": _to_float(row.get(CAR_T21_COL)),
                "car_t90": _to_float(row.get(CAR_T90_COL)),
                "net_car_t21": _to_float(row.get(NET_CAR_T21_COL)),
                "net_car_t90": _to_float(row.get(NET_CAR_T90_COL)),
            }
    return out


def _connect_ro() -> sqlite3.Connection:
    """Open the real DB strictly read-only (mode=ro, never writes)."""
    uri = f"file:{db.DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build_outcomes(conn, csv_path: Path | None = None) -> list[dict]:
    """Pair every conviction_scores row with its realised forward CAR.

    Returns a list of dicts (one per scored buy), each carrying the score /
    band / sub-scores from the shadow log plus realised + net-of-cost CAR at
    T+21 and T+90. Buys with no backtest row (no signal fired) get null CAR and
    ``tracked = False`` — they are kept, not dropped (spec §7 full-distribution
    test).

    `conn` is any sqlite3 connection with `conviction_scores`. Read-only: this
    function issues only SELECTs.
    """
    cars = load_backtest_cars(csv_path)
    rows = conn.execute(
        "SELECT fingerprint, window_end, score, band, "
        "       f1_who, f2_buy_size, f3_company_size, f4_earnings_timing, "
        "       f5_past_performance, f6_sector_mult, "
        "       rank_in_window, surfaced, earnings_dropped, inputs_missing "
        "FROM conviction_scores"
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        fp = r["fingerprint"]
        car = cars.get(fp)
        tracked = car is not None
        out.append({
            "fingerprint": fp,
            "window_end": r["window_end"],
            "score": r["score"],
            "band": r["band"],
            "subscores": {
                "f1_who": r["f1_who"],
                "f2_buy_size": r["f2_buy_size"],
                "f3_company_size": r["f3_company_size"],
                "f4_earnings_timing": r["f4_earnings_timing"],
                "f5_past_performance": r["f5_past_performance"],
                "f6_sector_mult": r["f6_sector_mult"],
            },
            "rank_in_window": r["rank_in_window"],
            "surfaced": bool(r["surfaced"]),
            "earnings_dropped": bool(r["earnings_dropped"]),
            "inputs_missing": r["inputs_missing"],
            "tracked": tracked,
            "car_t21": (car or {}).get("car_t21"),
            "car_t90": (car or {}).get("car_t90"),
            "net_car_t21": (car or {}).get("net_car_t21"),
            "net_car_t90": (car or {}).get("net_car_t90"),
        })
    return out


def main(argv=None) -> int:
    """CLI: print a compact summary of the score/CAR pairing (read-only)."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH,
                        help="backtest results CSV (default: .data/_backtest_results.csv)")
    parser.add_argument("--json", action="store_true",
                        help="emit the full pairing as JSON")
    args = parser.parse_args(argv)

    conn = _connect_ro()
    try:
        outcomes = build_outcomes(conn, csv_path=args.csv)
    finally:
        conn.close()

    tracked = [o for o in outcomes if o["tracked"]]
    if args.json:
        print(json.dumps(outcomes, indent=2))
    else:
        print(f"conviction_scores rows: {len(outcomes)}")
        print(f"  tracked (have backtest CAR): {len(tracked)}")
        print(f"  untracked (no signal fired): {len(outcomes) - len(tracked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
