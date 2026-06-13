"""Export baskets.json -- Basket Report data for the small-cap conviction page.

Reads ``.data/_backtest_results.csv`` and ``.data/baskets_config.json``.
For each basket: filters rows matching ``signal_ids`` + ``small_cap == 1``,
computes median net CARs (outlier-resistant), % positive, and the latest
10 firings.

Writes ``.data/baskets.json``.

CLI:
    python .scripts/export_baskets_json.py

Output (one line per basket):
    [basket_id] n=N  median_car21=X%  median_car90=Y%
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

CSV_PATH    = ROOT / ".data" / "_backtest_results.csv"
CONFIG_PATH = ROOT / ".data" / "baskets_config.json"
OUT_PATH    = ROOT / ".data" / "baskets.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(s) -> float | None:
    if s is None or s == "" or s == "None":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CSV ingestion
# ---------------------------------------------------------------------------

def load_backtest_csv(path: Path) -> list[dict]:
    """Load _backtest_results.csv into a list of dicts with typed floats."""
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse the columns we need; leave rest as raw strings.
            row["_net_car_t21"]  = _safe_float(row.get("net_car_t21"))
            row["_net_car_t90"]  = _safe_float(row.get("net_car_t90"))
            row["_value_gbp"]    = _safe_float(row.get("value_gbp"))
            row["_market_cap"]   = _safe_float(row.get("market_cap_gbp"))
            row["_small_cap"]    = row.get("small_cap", "0").strip()
            row["_fired_at"]     = (row.get("fired_at") or "")[:10]
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# Per-basket computation
# ---------------------------------------------------------------------------

def _compute_basket(basket_cfg: dict, all_rows: list[dict], proven_threshold: int) -> dict:
    """Compute stats for one basket.

    Filters rows to those whose signal_id matches basket_cfg["signal_ids"]
    AND small_cap == 1.

    Returns a dict with:
      id, label, description, n, proven, median_net_car_21, median_net_car_90,
      pct_positive_21, pct_positive_90, early_data, latest_firings
    """
    basket_id   = basket_cfg["id"]
    signal_ids  = set(basket_cfg["signal_ids"])
    label       = basket_cfg["label"]
    description = basket_cfg.get("description", "")
    early_data  = basket_cfg.get("early_data", False)

    # Filter rows: signal match + small_cap=1
    matching: list[dict] = []
    for r in all_rows:
        if r.get("signal_id") not in signal_ids:
            continue
        # small_cap column is stored as "0" / "1" / "" / None in CSV
        sc = r["_small_cap"]
        if sc not in ("1", 1):
            continue
        matching.append(r)

    n = len(matching)
    proven = n >= proven_threshold

    # Median net CAR -- skip None values
    car21_vals = [r["_net_car_t21"] for r in matching if r["_net_car_t21"] is not None]
    car90_vals = [r["_net_car_t90"] for r in matching if r["_net_car_t90"] is not None]

    def _median_pct(vals: list[float]) -> float | None:
        if not vals:
            return None
        return round(statistics.median(vals) * 100.0, 2)

    def _pct_positive(vals: list[float]) -> float | None:
        if not vals:
            return None
        return round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1)

    median_net_car_21 = _median_pct(car21_vals)
    median_net_car_90 = _median_pct(car90_vals)
    pct_positive_21   = _pct_positive(car21_vals)
    pct_positive_90   = _pct_positive(car90_vals)

    # Latest 10 firings (sorted by fired_at descending)
    # Use only rows that have a fired_at date so sort is stable.
    dated = sorted(
        [r for r in matching if r["_fired_at"]],
        key=lambda r: r["_fired_at"],
        reverse=True,
    )
    latest_10 = dated[:10]

    def _fmt_value(v):
        if v is None:
            return None
        return int(round(v))

    def _fmt_mktcap(v):
        if v is None:
            return None
        return int(round(v))

    def _fmt_car(v):
        if v is None:
            return None
        return round(v * 100.0, 2)

    latest_firings = [
        {
            "ticker":        r.get("ticker") or "",
            "fired_at":      r["_fired_at"],
            "value_gbp":     _fmt_value(r["_value_gbp"]),
            "net_car_21":    _fmt_car(r["_net_car_t21"]),
            "net_car_90":    _fmt_car(r["_net_car_t90"]),
            "market_cap_gbp": _fmt_mktcap(r["_market_cap"]),
        }
        for r in latest_10
    ]

    return {
        "id":                basket_id,
        "label":             label,
        "description":       description,
        "n":                 n,
        "proven":            proven,
        "early_data":        early_data,
        "median_net_car_21": median_net_car_21,
        "median_net_car_90": median_net_car_90,
        "pct_positive_21":   pct_positive_21,
        "pct_positive_90":   pct_positive_90,
        "latest_firings":    latest_firings,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def export_baskets(csv_path: Path = CSV_PATH,
                   config_path: Path = CONFIG_PATH,
                   out_path: Path = OUT_PATH) -> list[dict]:
    """Compute basket stats and write baskets.json. Returns the basket list."""
    if not csv_path.exists():
        print(f"[export_baskets] ERROR: backtest CSV not found: {csv_path}")
        print("  Run: python backtest.py  (or python refresh_all.py) first.")
        sys.exit(1)

    if not config_path.exists():
        print(f"[export_baskets] ERROR: baskets_config.json not found: {config_path}")
        sys.exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    proven_threshold = config.get("n_proven_threshold", 30)
    baskets_cfg: list[dict] = config["baskets"]

    print(f"[export_baskets] Loading {csv_path.name}...")
    all_rows = load_backtest_csv(csv_path)
    print(f"[export_baskets] {len(all_rows)} rows loaded.")

    baskets: list[dict] = []
    for cfg in baskets_cfg:
        b = _compute_basket(cfg, all_rows, proven_threshold)
        baskets.append(b)
        car21_str = f"{b['median_net_car_21']:+.2f}%" if b["median_net_car_21"] is not None else "n/a"
        car90_str = f"{b['median_net_car_90']:+.2f}%" if b["median_net_car_90"] is not None else "n/a"
        print(f"  [{b['id']}] n={b['n']}  median_car21={car21_str}  median_car90={car90_str}"
              f"{'  (early data)' if b['early_data'] else ''}")

    # Sort by median_net_car_90 descending (None last) for the page ranking.
    baskets_sorted = sorted(
        baskets,
        key=lambda b: (b["median_net_car_90"] is None, -(b["median_net_car_90"] or 0)),
    )

    payload = {
        "generated_at":     _now_utc_iso(),
        "proven_threshold": proven_threshold,
        "baskets":          baskets_sorted,
    }

    _atomic_write_json(out_path, payload)
    print(f"[export_baskets] Written -> {out_path}")
    return baskets_sorted


def main(argv=None) -> int:
    export_baskets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
