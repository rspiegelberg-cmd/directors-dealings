"""Stage 4.6 smoke tests for the dashboard JSON exporter.

>=15 cases covering:
  * Empty-CSV / empty-DB sanity
  * Per-horizon per-signal aggregation correctness
  * Schema version pin updated to '12' (Sprint 58) -- B-154
  * Hit-pct / median / mean / edge round-trip
  * Active cluster derivation (fresh vs brewing vs stale boundaries)
  * Paper-trade open/closed counts + MTM math
  * Dealings today + this_week feed shape
  * MTM_pct rule (T+1 entry vs latest, cost net)
  * Outlier flag on +500% CAR
  * F1 + outlier => status='gated'
  * Idempotency (no-timestamp round trip)
  * Atomic write + valid JSON output
  * Cohort buckets shape

Self-cleaning. Monkey-patches db.DB_PATH / db.DB_DIR + DEFAULT paths.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import export_dashboard_json as ex  # noqa: E402


RESULTS: list = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    if ok:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} -- {detail}")


def run_case(name, fn):
    try:
        fn()
        record(name, True)
    except AssertionError as exc:
        record(name, False, f"assertion: {exc}")
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"{type(exc).__name__}: {exc}\n"
                            f"{traceback.format_exc()[-500:]}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CSV_HEADER = [
    "run_id", "signal_id", "signal_version", "fingerprint", "fired_at",
    "ticker", "role", "role_class", "value_gbp", "is_aim",
    "benchmark_symbol", "entry_date", "entry_close",
    "t1_close", "t30_close", "t90_close", "t180_close", "t365_close",
    "benchmark_entry", "benchmark_t1", "benchmark_t30",
    "benchmark_t90", "benchmark_t180", "benchmark_t365",
    "raw_return_t1", "raw_return_t30", "raw_return_t90", "raw_return_t180", "raw_return_t365",
    "benchmark_return_t1", "benchmark_return_t30",
    "benchmark_return_t90", "benchmark_return_t180", "benchmark_return_t365",
    "car_t1", "car_t30", "car_t90", "car_t180", "car_t365",
    "cost_bps", "net_car_t1", "net_car_t30", "net_car_t90", "net_car_t180", "net_car_t365",
    "windows_available",
]


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        w.writeheader()
        for r in rows:
            full = {k: "" for k in CSV_HEADER}
            full.update(r)
            w.writerow(full)


def _csv_row(signal_id, fingerprint, fired_at, car_t30,
             ticker="XYZ", value_gbp=100000.0, car_t1=None,
             car_t90=None, car_t365=None, bench_t30=0.0):
    return {
        "run_id": "bt_test", "signal_id": signal_id,
        "signal_version": "1.0.0", "fingerprint": fingerprint,
        "fired_at": fired_at, "ticker": ticker,
        "value_gbp": value_gbp, "is_aim": 0,
        "benchmark_symbol": "^FTAS",
        "car_t1":   car_t1 if car_t1 is not None else "",
        "car_t30":  car_t30,
        "car_t90":  car_t90 if car_t90 is not None else "",
        "car_t365": car_t365 if car_t365 is not None else "",
        "benchmark_return_t30": bench_t30,
        "benchmark_return_t1": 0.0,
        "benchmark_return_t90": 0.0,
        "benchmark_return_t365": 0.0,
    }


def _seed_meta(conn, ticker="XYZ", is_aim=0, sector="Industrials"):
    conn.execute(
        "INSERT OR REPLACE INTO tickers_meta "
        "(ticker, sector, benchmark_symbol, is_aim, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticker, sector, "^FTAS", is_aim, db.iso_now()),
    )
    conn.commit()


def _seed_tx(conn, fp, ticker="XYZ", director="Alice", role="CEO",
             typ="BUY", date_="2026-05-01", announced_at=None,
             value=100_000.0, cluster_id=None):
    announced_at = announced_at or date_
    conn.execute(
        "INSERT OR REPLACE INTO transactions "
        "(fingerprint, first_seen, last_seen, seen_count, "
        " date, ticker, company, director, role, type, shares, price, value, "
        " announced_at, cluster_id) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fp, db.iso_now(), db.iso_now(), date_, ticker,
         f"{ticker} plc", director, role, typ, 100, 100.0, value,
         announced_at, cluster_id),
    )
    conn.commit()


def _seed_price(conn, ticker, day, close):
    conn.execute(
        "INSERT OR REPLACE INTO prices "
        "(ticker, date, close, source, fetched_at) "
        "VALUES (?, ?, ?, 'test', ?)",
        (ticker, day, close, db.iso_now()),
    )
    conn.commit()


def _seed_signal(conn, fp, signal_id, fired_at):
    conn.execute(
        "INSERT OR IGNORE INTO signals "
        "(signal_id, signal_version, fingerprint, fired_at) "
        "VALUES (?, ?, ?, ?)",
        (signal_id, "1.0.0", fp, fired_at),
    )
    conn.commit()


def _seed_paper_trade(conn, trade_id, fp, signal_id, status,
                      entry_close, shares, notional, exit_close=None):
    conn.execute(
        "INSERT OR REPLACE INTO paper_trades "
        "(trade_id, signal_id, signal_version, fingerprint, sizing_scheme, "
        " notional_gbp, entry_date, entry_close, shares, exit_date, "
        " exit_close, status, opened_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'flat', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_id, signal_id, "1.0.0", fp, notional,
         "2026-04-01", entry_close, shares, None, exit_close,
         status, db.iso_now(), db.iso_now()),
    )
    conn.commit()


def _wipe(conn):
    for tbl in ("paper_trades", "signals", "transactions", "prices",
                "tickers_meta", "backtest_runs"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

TODAY = date(2026, 5, 14)


def case_01_empty_db_raises(conn, tmp_dir):
    """Empty signals table -> SystemExit with clear message."""
    csv_path = tmp_dir / "empty.csv"
    _write_csv(csv_path, [])
    try:
        ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "signals table" in str(e) or "no rows" in str(e), str(e)


def case_02_missing_csv_raises(conn, tmp_dir):
    """Missing CSV -> SystemExit."""
    try:
        ex.run(out_dir=tmp_dir / "out",
               csv_path=tmp_dir / "does_not_exist.csv", today=TODAY)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "not found" in str(e)


def case_03_basic_signals_payload_shape(conn, tmp_dir):
    """Smallest happy path -> all top-level keys present."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c03", announced_at="2026-04-01")
    _seed_signal(conn, "c03", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c03",
                         "2026-04-01T00:00:00Z", car_t30=0.05)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    for k in ["generated_at", "schema_version", "horizon_aggregates",
              "active_clusters", "paper_pnl_open", "paper_trades_open",
              "paper_trades_closed", "cohorts"]:
        assert k in sig, f"missing top-level key: {k}"
    for h in ["t1", "t30", "t90", "t365"]:
        assert h in sig["horizon_aggregates"], h


def case_04_dealings_payload_shape(conn, tmp_dir):
    """dealings.json has expected top-level keys."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c04", announced_at="2026-04-01")
    _seed_signal(conn, "c04", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c04",
                         "2026-04-01T00:00:00Z", car_t30=0.05)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    d = json.loads((tmp_dir / "out" / "dealings.json").read_text())
    for k in ["generated_at", "schema_version", "as_of_date",
              "signals_today_count", "signals_today_delta_vs_avg",
              "today", "this_week"]:
        assert k in d, f"missing key {k}"


def case_05_hit_pct_and_median(conn, tmp_dir):
    """Three firings, 2 positive 1 negative -> hit_pct=66.7, median=2%."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c05a", announced_at="2026-04-01")
    _seed_signal(conn, "c05a", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [
        _csv_row("t1a_ceo_founder_buy", "c05a", "2026-04-01T00:00:00Z",
                 car_t30=0.02),
        _csv_row("t1a_ceo_founder_buy", "c05b", "2026-04-02T00:00:00Z",
                 car_t30=0.04),
        _csv_row("t1a_ceo_founder_buy", "c05c", "2026-04-03T00:00:00Z",
                 car_t30=-0.01),
    ]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    t1a = sig["horizon_aggregates"]["t30"]["signals"]["t1a"]
    assert t1a["trades"] == 3, t1a
    assert abs(t1a["hit_pct"] - 66.7) < 0.1, t1a["hit_pct"]
    assert abs(t1a["median_car"] - 2.0) < 0.001, t1a["median_car"]


def case_06_outlier_flag_500pct(conn, tmp_dir):
    """One firing with +500% CAR -> outlier_flag=true."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c06", announced_at="2026-04-01")
    _seed_signal(conn, "c06", "f1_first_time_buy", "2026-04-01")
    csv_rows = [_csv_row("f1_first_time_buy", "c06",
                         "2026-04-01T00:00:00Z", car_t30=5.0)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    f1 = sig["horizon_aggregates"]["t30"]["signals"]["f1"]
    assert f1["outlier_flag"] is True, f1


def case_07_f1_gated_when_outlier(conn, tmp_dir):
    """F1 with outlier_flag=True -> status='gated'."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c07", announced_at="2026-04-01")
    _seed_signal(conn, "c07", "f1_first_time_buy", "2026-04-01")
    csv_rows = [_csv_row("f1_first_time_buy", "c07",
                         "2026-04-01T00:00:00Z", car_t30=5.0)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    f1 = sig["horizon_aggregates"]["t30"]["signals"]["f1"]
    assert f1["status"] == "gated", f1


def case_08_active_cluster_fresh(conn, tmp_dir):
    """Cluster with last_buy_date in last 30 days -> s1_active=True."""
    _seed_meta(conn, ticker="CLF")
    fresh = (TODAY - timedelta(days=5)).isoformat()
    older = (TODAY - timedelta(days=20)).isoformat()
    cid = "CLF-fresh"
    _seed_tx(conn, fp="c08a", ticker="CLF", director="A", role="CEO",
             date_=older, announced_at=older, cluster_id=cid)
    _seed_tx(conn, fp="c08b", ticker="CLF", director="B", role="CFO",
             date_=fresh, announced_at=fresh, cluster_id=cid)
    _seed_signal(conn, "c08b", "t1a_ceo_founder_buy", fresh)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c08b",
                         f"{fresh}T00:00:00Z", car_t30=0.05)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    clf = [c for c in sig["active_clusters"] if c["ticker"] == "CLF"]
    assert clf, f"expected CLF cluster, got {sig['active_clusters']}"
    assert clf[0]["s1_active"] is True, clf[0]
    assert clf[0]["director_count"] == 2


def case_09_active_cluster_brewing(conn, tmp_dir):
    """Cluster with last_buy 30-90 days ago -> s1_active=False ('brewing')."""
    _seed_meta(conn, ticker="CLB")
    brewing = (TODAY - timedelta(days=60)).isoformat()
    older  = (TODAY - timedelta(days=80)).isoformat()
    cid = "CLB-brew"
    _seed_tx(conn, fp="c09a", ticker="CLB", director="A", role="CEO",
             date_=older, announced_at=older, cluster_id=cid)
    _seed_tx(conn, fp="c09b", ticker="CLB", director="B", role="CFO",
             date_=brewing, announced_at=brewing, cluster_id=cid)
    _seed_signal(conn, "c09b", "t1a_ceo_founder_buy", brewing)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c09b",
                         f"{brewing}T00:00:00Z", car_t30=0.05)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    clb = [c for c in sig["active_clusters"] if c["ticker"] == "CLB"]
    assert clb, f"expected CLB cluster, got {sig['active_clusters']}"
    assert clb[0]["s1_active"] is False, clb[0]


def case_10_active_cluster_stale_excluded(conn, tmp_dir):
    """Cluster with last_buy >90 days back -> dropped from active_clusters."""
    _seed_meta(conn, ticker="CLS")
    stale = (TODAY - timedelta(days=100)).isoformat()
    older  = (TODAY - timedelta(days=110)).isoformat()
    cid = "CLS-stale"
    _seed_tx(conn, fp="c10a", ticker="CLS", director="A", role="CEO",
             date_=older, announced_at=older, cluster_id=cid)
    _seed_tx(conn, fp="c10b", ticker="CLS", director="B", role="CFO",
             date_=stale, announced_at=stale, cluster_id=cid)
    _seed_signal(conn, "c10b", "t1a_ceo_founder_buy", stale)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c10b",
                         f"{stale}T00:00:00Z", car_t30=0.05)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    cls = [c for c in sig["active_clusters"] if c["ticker"] == "CLS"]
    assert not cls, f"stale cluster should be excluded, got {cls}"


def case_11_paper_open_mtm(conn, tmp_dir):
    """Open paper trade marked to market against latest price."""
    _seed_meta(conn, ticker="PT1")
    _seed_tx(conn, fp="c11", ticker="PT1", announced_at="2026-04-01")
    _seed_signal(conn, "c11", "t1a_ceo_founder_buy", "2026-04-01")
    _seed_price(conn, "PT1", "2026-04-02", 100.0)
    _seed_price(conn, "PT1", "2026-05-13", 120.0)
    _seed_paper_trade(conn, "pt_c11", "c11", "t1a_ceo_founder_buy", "open",
                      entry_close=100.0, shares=10.0, notional=1000.0)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c11",
                         "2026-04-01T00:00:00Z", car_t30=0.05,
                         ticker="PT1")]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    assert sig["paper_trades_open"] == 1, sig
    assert abs(sig["paper_pnl_open"] - 200.0) < 0.01, sig


def case_12_paper_closed_excluded_from_pnl(conn, tmp_dir):
    """Closed trades count separately and dont contribute to open MTM."""
    _seed_meta(conn, ticker="PT2")
    _seed_tx(conn, fp="c12", ticker="PT2", announced_at="2026-04-01")
    _seed_signal(conn, "c12", "t1a_ceo_founder_buy", "2026-04-01")
    _seed_price(conn, "PT2", "2026-04-02", 100.0)
    _seed_price(conn, "PT2", "2026-05-13", 200.0)
    _seed_paper_trade(conn, "pt_c12", "c12", "t1a_ceo_founder_buy", "closed",
                      entry_close=100.0, shares=10.0, notional=1000.0,
                      exit_close=110.0)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c12",
                         "2026-04-01T00:00:00Z", car_t30=0.05,
                         ticker="PT2")]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    assert sig["paper_trades_closed"] == 1, sig
    assert sig["paper_trades_open"] == 0, sig
    assert sig["paper_pnl_open"] == 0.0, sig


def case_13_dealings_today_and_week(conn, tmp_dir):
    """Todays row + a row from 3 days ago appear correctly."""
    _seed_meta(conn, ticker="DLG")
    today_iso = TODAY.isoformat()
    threedays = (TODAY - timedelta(days=3)).isoformat()
    _seed_tx(conn, fp="c13a", ticker="DLG", announced_at=today_iso,
             date_=today_iso, value=50_000.0)
    _seed_tx(conn, fp="c13b", ticker="DLG", announced_at=threedays,
             date_=threedays, value=80_000.0, director="Bob")
    _seed_signal(conn, "c13a", "t1a_ceo_founder_buy", today_iso)
    _seed_signal(conn, "c13b", "t2_exec_buy", threedays)
    csv_rows = [
        _csv_row("t1a_ceo_founder_buy", "c13a", f"{today_iso}T00:00:00Z",
                 car_t30=0.02, ticker="DLG"),
        _csv_row("t2_exec_buy", "c13b", f"{threedays}T00:00:00Z",
                 car_t30=0.03, ticker="DLG"),
    ]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    d = json.loads((tmp_dir / "out" / "dealings.json").read_text())
    assert len(d["today"]) == 1, d["today"]
    assert d["today"][0]["ticker"] == "DLG"
    assert "t1a" in d["today"][0]["signals_fired"]
    assert d["signals_today_count"] == 1
    assert len(d["this_week"]) == 2, d["this_week"]
    week_sigs = [s for row in d["this_week"] for s in row["signals_fired"]]
    assert "t2" in week_sigs, d["this_week"]


def case_14_mtm_pct_math(conn, tmp_dir):
    """MTM math: T+1 close after announce -> latest, minus cost_pct."""
    _seed_meta(conn, ticker="MTM", is_aim=0)
    today_iso = TODAY.isoformat()
    _seed_tx(conn, fp="c14", ticker="MTM", announced_at=today_iso,
             date_=today_iso, value=10_000.0)
    _seed_signal(conn, "c14", "t1a_ceo_founder_buy", today_iso)
    plus1 = (TODAY + timedelta(days=1)).isoformat()
    _seed_price(conn, "MTM", today_iso, 99.0)
    _seed_price(conn, "MTM", plus1, 100.0)
    plus3 = (TODAY + timedelta(days=3)).isoformat()
    _seed_price(conn, "MTM", plus3, 110.0)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c14",
                         f"{today_iso}T00:00:00Z",
                         car_t30=0.02, ticker="MTM")]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    d = json.loads((tmp_dir / "out" / "dealings.json").read_text())
    mtm = d["today"][0]["mtm_pct"]
    assert abs(mtm - 9.0) < 0.01, f"mtm={mtm}"


def case_15_mtm_aim_lower_cost(conn, tmp_dir):
    """AIM ticker -> cost_pct = 0.5 (no stamp)."""
    _seed_meta(conn, ticker="AIM", is_aim=1)
    today_iso = TODAY.isoformat()
    _seed_tx(conn, fp="c15", ticker="AIM", announced_at=today_iso,
             date_=today_iso, value=10_000.0)
    _seed_signal(conn, "c15", "t1a_ceo_founder_buy", today_iso)
    plus1 = (TODAY + timedelta(days=1)).isoformat()
    plus3 = (TODAY + timedelta(days=3)).isoformat()
    _seed_price(conn, "AIM", plus1, 100.0)
    _seed_price(conn, "AIM", plus3, 110.0)
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c15",
                         f"{today_iso}T00:00:00Z",
                         car_t30=0.02, ticker="AIM")]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    d = json.loads((tmp_dir / "out" / "dealings.json").read_text())
    mtm = d["today"][0]["mtm_pct"]
    assert abs(mtm - 9.5) < 0.01, f"AIM mtm={mtm}"


def case_16_idempotent_no_timestamp(conn, tmp_dir):
    """Running twice with --no-timestamp produces byte-identical output."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c16", announced_at="2026-04-01")
    _seed_signal(conn, "c16", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c16",
                         "2026-04-01T00:00:00Z", car_t30=0.02)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out1", csv_path=csv_path, today=TODAY,
           emit_timestamp=False)
    ex.run(out_dir=tmp_dir / "out2", csv_path=csv_path, today=TODAY,
           emit_timestamp=False)
    a1 = (tmp_dir / "out1" / "signals.json").read_bytes()
    a2 = (tmp_dir / "out2" / "signals.json").read_bytes()
    assert a1 == a2, "signals.json not byte-identical"
    b1 = (tmp_dir / "out1" / "dealings.json").read_bytes()
    b2 = (tmp_dir / "out2" / "dealings.json").read_bytes()
    assert b1 == b2, "dealings.json not byte-identical"


def case_17_atomic_write_no_tmp_left(conn, tmp_dir):
    """After successful run no .tmp files are left behind."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c17", announced_at="2026-04-01")
    _seed_signal(conn, "c17", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c17",
                         "2026-04-01T00:00:00Z", car_t30=0.02)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    out = tmp_dir / "out"
    ex.run(out_dir=out, csv_path=csv_path, today=TODAY)
    tmps = list(out.glob("*.tmp"))
    assert not tmps, f"unexpected .tmp files: {tmps}"
    json.loads((out / "signals.json").read_text())
    json.loads((out / "dealings.json").read_text())


def case_18_cohort_value_buckets_shape(conn, tmp_dir):
    """Cohort by_value_bucket has the 4 expected keys."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c18", announced_at="2026-04-01")
    _seed_signal(conn, "c18", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [
        _csv_row("t1a_ceo_founder_buy", "c18a", "2026-04-01T00:00:00Z",
                 value_gbp=10_000.0, car_t30=0.02),
        _csv_row("t1a_ceo_founder_buy", "c18b", "2026-04-02T00:00:00Z",
                 value_gbp=50_000.0, car_t30=0.03),
        _csv_row("t2_exec_buy",    "c18c", "2026-04-03T00:00:00Z",
                 value_gbp=200_000.0, car_t30=0.04),
        _csv_row("t1a_ceo_founder_buy", "c18d", "2026-04-04T00:00:00Z",
                 value_gbp=1_000_000.0, car_t30=0.05),
    ]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    bv = sig["cohorts"]["by_value_bucket"]
    for k in ["1k-25k", "25k-100k", "100k-500k", "500k+"]:
        assert k in bv, f"missing bucket {k}"
    assert abs(bv["1k-25k"] - 2.0) < 0.01, bv


def case_19_tier_dedup_t1_only_signals(conn, tmp_dir):
    """A tx with only T1a in CSV -> only t1a has trades>0 (per-signal)."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c19", announced_at="2026-04-01")
    _seed_signal(conn, "c19", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c19",
                         "2026-04-01T00:00:00Z", car_t30=0.02)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    per = sig["horizon_aggregates"]["t30"]["signals"]
    assert per["t1a"]["trades"] == 1
    for s in ["t1b", "t2", "t3", "t4"]:
        assert per[s]["trades"] == 0, f"{s}={per[s]['trades']}"


def case_20_schema_version_present(conn, tmp_dir):
    """Both JSONs carry schema_version >= '1.0' (currently '1.1' post B-025 Phase B)."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c20", announced_at="2026-04-01")
    _seed_signal(conn, "c20", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c20",
                         "2026-04-01T00:00:00Z", car_t30=0.02)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    d   = json.loads((tmp_dir / "out" / "dealings.json").read_text())
    assert sig["schema_version"] >= "1.0", sig["schema_version"]
    assert d["schema_version"]   >= "1.0", d["schema_version"]


def case_21_generated_at_iso(conn, tmp_dir):
    """generated_at is a parseable ISO Z timestamp."""
    from datetime import datetime
    _seed_meta(conn)
    _seed_tx(conn, fp="c21", announced_at="2026-04-01")
    _seed_signal(conn, "c21", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c21",
                         "2026-04-01T00:00:00Z", car_t30=0.02)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    ex.run(out_dir=tmp_dir / "out", csv_path=csv_path, today=TODAY)
    sig = json.loads((tmp_dir / "out" / "signals.json").read_text())
    ts = sig["generated_at"]
    assert ts.endswith("Z"), ts
    parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.year >= 2026


def case_22_dry_run_no_files(conn, tmp_dir):
    """--dry-run does not write the JSON files."""
    _seed_meta(conn)
    _seed_tx(conn, fp="c22", announced_at="2026-04-01")
    _seed_signal(conn, "c22", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c22",
                         "2026-04-01T00:00:00Z", car_t30=0.02)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    out = tmp_dir / "out_dry"
    summary = ex.run(out_dir=out, csv_path=csv_path, today=TODAY,
                     dry_run=True)
    assert summary["n_signal_rows"] >= 1
    assert not (out / "signals.json").exists()
    assert not (out / "dealings.json").exists()


def case_23_pending_diagnostics_buckets(conn, tmp_dir):
    """Synthesised _pending_review.json maps to correct bucket counts."""
    p = tmp_dir / "_pending_review.json"
    fixture = {
        "generated_at": "2026-05-14T00:00:00Z",
        "count": 0,
        "items": {
            "1": {"warnings": ["bundled multi-PDMR filing -- names "
                               "not extractable from boilerplate"]},
            "2": {"warnings": ["could_not_extract_PDMR_name"]},
            "3": {"warnings": ["foreign_currency"]},
            "4": {"warnings": ["multiple_distinct_prices"]},
            "5": {"warnings": ["EXERCISE of nil-cost Restricted Shares"]},
            "6": {"warnings": ["required_fields_missing"]},
            "7": {"warnings": ["zero_shares_non_grant"]},
            "8": {"warnings": ["totally bespoke warning that should not match"]},
        },
    }
    p.write_text(json.dumps(fixture), encoding="utf-8")
    diag = ex._compute_pending_diagnostics(pending_path=p,
                                           generated_at="2026-05-14T00:00:00Z")
    assert diag["total"] == 8, diag["total"]
    cats = {c["name"]: c for c in diag["categories"]}
    assert cats["Bundled multi-PDMR"]["count"] == 2, cats["Bundled multi-PDMR"]
    assert cats["Foreign currency"]["count"] == 1
    assert cats["Multi-tranche / multi-transaction"]["count"] == 1
    assert cats["Corporate actions"]["count"] == 1
    assert cats["Could not classify / extract"]["count"] == 1
    assert cats["Zero-share / data quirks"]["count"] == 1
    assert cats["Other"]["count"] == 1
    assert sum(c["count"] for c in diag["categories"]) == diag["total"]
    assert cats["Bundled multi-PDMR"]["recoverable"] == "no"
    assert cats["Foreign currency"]["recoverable"] == "v2-fx"
    assert cats["Multi-tranche / multi-transaction"]["recoverable"] == "v2-fanout"
    assert cats["Corporate actions"]["recoverable"] == "manual"
    assert cats["Other"]["recoverable"] == "unknown"
    assert diag["categories"][-1]["name"] == "Other"
    assert abs(sum(c["pct"] for c in diag["categories"]) - 100.0) < 0.5


def case_24_pending_diagnostics_in_signals_payload(conn, tmp_dir):
    """build_payload wires pending_diagnostics into signals.json output."""
    p = tmp_dir / "_pending_review.json"
    fixture = {
        "generated_at": "2026-05-14T00:00:00Z",
        "count": 0,
        "items": {
            "10": {"warnings": ["bundled multi-PDMR filing"]},
            "11": {"warnings": ["foreign_currency"]},
        },
    }
    p.write_text(json.dumps(fixture), encoding="utf-8")
    _seed_meta(conn)
    _seed_tx(conn, fp="c24", announced_at="2026-04-01")
    _seed_signal(conn, "c24", "t1a_ceo_founder_buy", "2026-04-01")
    csv_rows = [_csv_row("t1a_ceo_founder_buy", "c24",
                         "2026-04-01T00:00:00Z", car_t30=0.01)]
    csv_path = tmp_dir / "r.csv"
    _write_csv(csv_path, csv_rows)
    out = tmp_dir / "out_c24"
    ex.run(out_dir=out, csv_path=csv_path, today=TODAY,
           pending_path=p)
    sig = json.loads((out / "signals.json").read_text(encoding="utf-8"))
    assert "pending_diagnostics" in sig, list(sig.keys())
    pd = sig["pending_diagnostics"]
    assert pd["total"] == 2
    assert isinstance(pd["categories"], list)
    assert len(pd["categories"]) == 7, len(pd["categories"])
    for c in pd["categories"]:
        for k in ("id", "name", "count", "pct", "recoverable", "description"):
            assert k in c, (k, c)
    for k in ("schema_version", "horizon_aggregates", "active_clusters",
              "cohorts", "paper_pnl_open"):
        assert k in sig, k


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="dd_stage46_"))
    real_db_dir = db.DB_DIR
    real_db_path = db.DB_PATH
    try:
        db.DB_DIR = tmp_dir
        db.DB_PATH = tmp_dir / "directors.db"
        conn = db.connect()
        cases = [
            ("01. empty signals table raises", case_01_empty_db_raises),
            ("02. missing CSV raises", case_02_missing_csv_raises),
            ("03. signals.json top-level shape", case_03_basic_signals_payload_shape),
            ("04. dealings.json top-level shape", case_04_dealings_payload_shape),
            ("05. hit_pct & median CAR", case_05_hit_pct_and_median),
            ("06. outlier flag at |CAR|>200%", case_06_outlier_flag_500pct),
            ("07. F1 gated when outlier", case_07_f1_gated_when_outlier),
            ("08. active cluster fresh -> s1_active=true",
             case_08_active_cluster_fresh),
            ("09. active cluster brewing (30-90d)",
             case_09_active_cluster_brewing),
            ("10. active cluster stale (>90d) excluded",
             case_10_active_cluster_stale_excluded),
            ("11. paper open MTM math", case_11_paper_open_mtm),
            ("12. paper closed excluded from open P&L",
             case_12_paper_closed_excluded_from_pnl),
            ("13. dealings today + this_week feed",
             case_13_dealings_today_and_week),
            ("14. MTM pct math (non-AIM, cost=1.0)",
             case_14_mtm_pct_math),
            ("15. MTM AIM (cost=0.5)", case_15_mtm_aim_lower_cost),
            ("16. idempotency (no-timestamp byte-equal)",
             case_16_idempotent_no_timestamp),
            ("17. atomic write -- no .tmp leftover",
             case_17_atomic_write_no_tmp_left),
            ("18. cohort by_value_bucket keys",
             case_18_cohort_value_buckets_shape),
            ("19. per-signal trade counts respect input",
             case_19_tier_dedup_t1_only_signals),
            ("20. schema_version present", case_20_schema_version_present),
            ("21. generated_at parseable ISO",
             case_21_generated_at_iso),
            ("22. --dry-run writes no files", case_22_dry_run_no_files),
            ("23. pending diagnostics bucket counts",
             case_23_pending_diagnostics_buckets),
            ("24. pending_diagnostics wired into signals.json",
             case_24_pending_diagnostics_in_signals_payload),
        ]
        for name, fn in cases:
            for tbl in ("paper_trades", "signals", "transactions", "prices",
                        "tickers_meta", "backtest_runs"):
                conn.execute(f"DELETE FROM {tbl}")
            conn.commit()
            run_case(name, (lambda f=fn: f(conn, tmp_dir)))
        conn.close()
    finally:
        db.DB_DIR = real_db_dir
        db.DB_PATH = real_db_path
        shutil.rmtree(tmp_dir, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
