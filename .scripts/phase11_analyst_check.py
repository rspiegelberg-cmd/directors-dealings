"""
Phase 11.4 Analyst sign-off — mean CAR per signal (post-cleanup).
Flags any signal where the current T+90 mean net-CAR looks inconsistent
with what a legitimate insider-buying alpha source should produce.

Run: python .scripts/phase11_analyst_check.py
"""
import csv, pathlib, math, statistics

CSV_PATH = pathlib.Path(__file__).parent.parent / ".data" / "_backtest_results.csv"
BAK_PATH = pathlib.Path(__file__).parent.parent / ".data" / "_backtest_results.csv.bak"

def load_cars(path):
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["signal_id"]
            try:
                t1   = float(row["net_car_t1"])  if row["net_car_t1"]  else None
                t30  = float(row["net_car_t30"]) if row["net_car_t30"] else None
                t90  = float(row["net_car_t90"]) if row["net_car_t90"] else None
            except ValueError:
                continue
            if sid not in rows:
                rows[sid] = {"t1": [], "t30": [], "t90": []}
            if t1  is not None: rows[sid]["t1"].append(t1)
            if t30 is not None: rows[sid]["t30"].append(t30)
            if t90 is not None: rows[sid]["t90"].append(t90)
    return rows

def summarise(rows):
    out = {}
    for sid, d in rows.items():
        out[sid] = {
            "n_t90": len(d["t90"]),
            "mean_t1":  statistics.mean(d["t1"])  * 100 if d["t1"]  else None,
            "mean_t30": statistics.mean(d["t30"]) * 100 if d["t30"] else None,
            "mean_t90": statistics.mean(d["t90"]) * 100 if d["t90"] else None,
        }
    return out

print("=" * 70)
print("Phase 11.4 Analyst Sign-off — mean net-CAR by signal (post-cleanup)")
print("=" * 70)

current = summarise(load_cars(CSV_PATH))
has_bak = BAK_PATH.exists()
if has_bak:
    prev    = summarise(load_cars(BAK_PATH))

# Print table
THRESHOLD_PP = 3.0   # Gate 1 answer: flag if shift > 3pp on N>=20

SIG_ORDER = [
    "t1a_ceo_founder_buy", "t1b_cfo_buy", "t7_chair_buy",
    "t2_exec_buy",         "t3_ned_buy",  "t5_pca_buy",
    "t6_company_sec_buy",  "t4_other_buy",
    "s1_cluster_buy",      "f1_first_time_buy", "t0_cluster_combo",
]

hdr = f"{'Signal':<22} {'N':>5} {'T+1%':>7} {'T+30%':>7} {'T+90%':>7}"
if has_bak:
    hdr += f"  {'ΔT+90pp':>8}  {'Flag':>5}"
print(hdr)
print("-" * (len(hdr) + 5))

flags = []
for sid in SIG_ORDER:
    c = current.get(sid)
    if not c:
        continue
    n   = c["n_t90"]
    t1  = f"{c['mean_t1']:+.2f}" if c["mean_t1"]  is not None else "  n/a"
    t30 = f"{c['mean_t30']:+.2f}" if c["mean_t30"] is not None else "  n/a"
    t90 = f"{c['mean_t90']:+.2f}" if c["mean_t90"] is not None else "  n/a"

    line = f"{sid:<22} {n:>5} {t1:>7} {t30:>7} {t90:>7}"
    if has_bak and sid in prev and n >= 20:
        p90   = prev[sid].get("mean_t90")
        c90   = c["mean_t90"]
        if p90 is not None and c90 is not None:
            delta = c90 - p90
            flag  = "FLAG" if abs(delta) > THRESHOLD_PP else "ok"
            line += f"  {delta:+8.2f}  {flag:>5}"
            if flag == "FLAG":
                flags.append((sid, n, p90, c90, delta))
    print(line)

print()
if not has_bak:
    print("NOTE: no .bak CSV found — showing post-cleanup baseline only.")
    print("      Pre/post delta comparison not available (bak was overwritten")
    print("      when the second pipeline rebuild ran after cleanup).")
else:
    if flags:
        print("FLAGGED signals (T+90 shift > 3pp, N>=20):")
        for sid, n, p90, c90, delta in flags:
            print(f"  {sid}: prev={p90:+.2f}%  now={c90:+.2f}%  delta={delta:+.2f}pp  N={n}")
        print()
        print("Analyst rationale required for each flagged signal before republish.")
    else:
        print("No signals flagged (all T+90 shifts within ±3pp for N>=20).")
        print("Gate 1 criterion met — dashboard cleared for Phase 11.5.")
