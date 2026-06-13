"""Stage 4 main test suite.

>=20 cases covering:
  * roles.classify_role precedence (incl. Q-SID locked T3)
  * Each of the 7 signal evaluators -- fires/doesn't-fire positive cases
  * Schema version pin updated to '12' (Sprint 58) -- B-154
  * Tier dedup in the orchestrator (T1 > T2 > T3 > T4)
  * detect_clusters connected-component / distinct-director / 30-day gap
  * eval_signals end-to-end orchestrator + idempotency
  * backtest CAR computation, cost application, T+1 weekend fallback,
    insufficient-history skip, partial-window flag

Self-cleaning. Monkey-patches db.DB_PATH / db.DB_DIR + signals'
backfill auto-invoke to a no-op.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import detect_clusters  # noqa: E402
import eval_signals  # noqa: E402
import backtest  # noqa: E402
import signals as signals_pkg  # noqa: E402
from signals.roles import classify_role  # noqa: E402


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

def _insert_meta(conn, ticker="XYZ", is_aim=0, benchmark="^FTAS"):
    conn.execute(
        "INSERT OR REPLACE INTO tickers_meta "
        "(ticker, sector, benchmark_symbol, is_aim, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticker, "Industrials", benchmark, is_aim, db.iso_now()),
    )


def _insert_tx(conn, *, fp, ticker="XYZ", director="Alice CEO",
               role="Chief Executive Officer", typ="BUY",
               date_="2026-01-31", announced_at=None,
               value=200_000.0, shares=1000, price=200.0):
    now = db.iso_now()
    announced_at = announced_at or date_
    conn.execute(
        "INSERT OR REPLACE INTO transactions "
        "(fingerprint, first_seen, last_seen, seen_count, "
        " date, ticker, company, director, role, type, shares, price, value, "
        " announced_at) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fp, now, now, date_, ticker, f"{ticker} plc",
         director, role, typ, shares, price, value, announced_at),
    )


def _insert_price(conn, ticker, day, close):
    conn.execute(
        "INSERT OR REPLACE INTO prices "
        "(ticker, date, close, source, fetched_at) "
        "VALUES (?, ?, ?, 'yahoo', ?)",
        (ticker, day, close, db.iso_now()),
    )


def _seed_30day_history(conn, ticker, anchor="2026-01-01"):
    """Seed 30 daily closes from anchor for ticker and the benchmark."""
    from datetime import date, timedelta
    base = date.fromisoformat(anchor)
    for i in range(35):
        d = (base + timedelta(days=i)).isoformat()
        _insert_price(conn, ticker, d, 100.0 + i)
        _insert_price(conn, "^FTAS", d, 1000.0 + i * 0.5)
    conn.commit()


def _fetch_tx(conn, fp):
    return conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?", (fp,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Role classifier cases (1-9)
# ---------------------------------------------------------------------------

def case_01_role_ceo():
    # B-025 Phase B: CEO/Founder bucket is now T1a (was T1).
    assert classify_role("Group Chief Executive") == "T1a"

def case_02_role_cfo():
    # B-025 Phase B: CFO bucket is now T1b (was T1).
    assert classify_role("Chief Financial Officer") == "T1b"

def case_03_role_ceo_acronym():
    assert classify_role("CFO") == "T1b"

def case_04_role_ned():
    assert classify_role("Non-Executive Director") == "T3"

def case_05_role_ned_space_variant():
    assert classify_role("Non Executive Director") == "T3"

def case_06_role_sid_is_t3():
    """Q-SID locked: Senior Independent Director -> T3."""
    assert classify_role("Senior Independent Director") == "T3"

def case_07_role_chairman_t7():
    # B-025 Phase B: Chairman bucket is now T7 (was T2).
    assert classify_role("Chairman") == "T7"

def case_08_role_coo_t2():
    assert classify_role("Chief Operating Officer") == "T2"

def case_09_role_none_t4():
    assert classify_role(None) == "T4"
    assert classify_role("") == "T4"
    # B-025 Phase B: Company Secretary/GC now classifies as T6, not T4.
    # Use a genuinely uncategorised role to test the T4 catch-all.
    assert classify_role("Group Company Secretary") == "T6"
    assert classify_role("Unknown Board Member") == "T4"


# ---------------------------------------------------------------------------
# Per-signal positive and negative cases (10-18)
# ---------------------------------------------------------------------------

def case_10_t1_fires(conn):
    # B-025 Phase B: t1_ceo_cfo_buy split into t1a_ceo_founder_buy + t1b_cfo_buy.
    # CEO role ("Chief Executive Officer" default) fires t1a.
    _insert_meta(conn)
    _insert_tx(conn, fp="c10", value=150_000.0)
    tx = _fetch_tx(conn, "c10")
    mod = signals_pkg.REGISTRY["t1a_ceo_founder_buy"]
    result = mod.evaluate(tx, conn, as_of=tx["announced_at"])
    assert result is not None
    assert result["confidence"] == "high"
    md = json.loads(result["metadata"])
    assert md["role_class"] == "T1a"


def case_11_t1_below_threshold(conn):
    _insert_meta(conn)
    _insert_tx(conn, fp="c11", value=80_000.0)
    tx = _fetch_tx(conn, "c11")
    mod = signals_pkg.REGISTRY["t1a_ceo_founder_buy"]
    assert mod.evaluate(tx, conn, as_of=tx["announced_at"]) is None


def case_12_t1_sell_no_fire(conn):
    _insert_meta(conn)
    _insert_tx(conn, fp="c12", typ="SELL", value=200_000.0)
    tx = _fetch_tx(conn, "c12")
    assert signals_pkg.REGISTRY["t1a_ceo_founder_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is None


def case_13_t2_fires(conn):
    # B-025 Phase B: Chairman is now T7 (its own bucket). Use COO for T2.
    _insert_meta(conn)
    _insert_tx(conn, fp="c13", role="Chief Operating Officer", value=30_000.0)
    tx = _fetch_tx(conn, "c13")
    result = signals_pkg.REGISTRY["t2_exec_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    )
    assert result is not None
    md = json.loads(result["metadata"])
    assert md["role_class"] == "T2"


def case_14_t3_fires_ned(conn):
    _insert_meta(conn)
    _insert_tx(conn, fp="c14", role="Non-Executive Director", value=15_000.0)
    tx = _fetch_tx(conn, "c14")
    assert signals_pkg.REGISTRY["t3_ned_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is not None


def case_15_t3_fires_sid(conn):
    """Q-SID locked: SID classifies as T3 -- T3 evaluator must fire."""
    _insert_meta(conn)
    _insert_tx(conn, fp="c15", role="Senior Independent Director", value=20_000.0)
    tx = _fetch_tx(conn, "c15")
    result = signals_pkg.REGISTRY["t3_ned_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    )
    assert result is not None, "SID buy >= 10k should fire T3"
    md = json.loads(result["metadata"])
    assert md["role_class"] == "T3"


def case_16_t4_fires_unknown_role(conn):
    # B-025 Phase B: "Group Company Secretary" now classifies as T6.
    # Use a genuinely uncategorised role to test T4 catch-all.
    _insert_meta(conn)
    _insert_tx(conn, fp="c16", role="Unknown Board Member", value=5_000.0)
    tx = _fetch_tx(conn, "c16")
    assert signals_pkg.REGISTRY["t4_other_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is not None


def case_17_t4_below_threshold(conn):
    _insert_meta(conn)
    _insert_tx(conn, fp="c17", role="Company Secretary", value=500.0)
    tx = _fetch_tx(conn, "c17")
    assert signals_pkg.REGISTRY["t4_other_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is None


def case_18_f1_fires_first_time(conn):
    _insert_meta(conn)
    _insert_tx(conn, fp="c18a", director="Alice CEO",
               announced_at="2026-01-31", value=150_000.0)
    tx = _fetch_tx(conn, "c18a")
    assert signals_pkg.REGISTRY["f1_first_time_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is not None


def case_19_f1_does_not_fire_when_prior_buy(conn):
    _insert_meta(conn)
    _insert_tx(conn, fp="c19a", director="Bob CFO",
               announced_at="2025-12-01", value=50_000.0)
    _insert_tx(conn, fp="c19b", director="Bob CFO",
               announced_at="2026-01-31", value=60_000.0)
    tx = _fetch_tx(conn, "c19b")
    assert signals_pkg.REGISTRY["f1_first_time_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is None


def case_20_f1_walk_forward(conn):
    """F1 must not be tripped by a future BUY at director+ticker."""
    _insert_meta(conn)
    _insert_tx(conn, fp="c20a", director="Carol CEO",
               announced_at="2026-01-31", value=200_000.0)
    # Future "phantom" buy (must be ignored by walk-forward gate).
    _insert_tx(conn, fp="c20b", director="Carol CEO",
               announced_at="2026-02-05", value=50_000.0)
    tx = _fetch_tx(conn, "c20a")
    assert signals_pkg.REGISTRY["f1_first_time_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is not None, "F1 should fire at D=2026-01-31 -- future BUY invisible"


# ---------------------------------------------------------------------------
# detect_clusters cases (21-23)
# ---------------------------------------------------------------------------

def case_21_cluster_transitive(conn):
    _insert_meta(conn, ticker="ABC")
    _insert_tx(conn, fp="c21a", ticker="ABC", director="A", role="CEO",
               value=120_000.0, date_="2026-01-01", announced_at="2026-01-01")
    _insert_tx(conn, fp="c21b", ticker="ABC", director="B", role="CFO",
               value=120_000.0, date_="2026-01-25", announced_at="2026-01-25")
    _insert_tx(conn, fp="c21c", ticker="ABC", director="C", role="Chair",
               value=120_000.0, date_="2026-01-31", announced_at="2026-01-31")
    summary = detect_clusters.detect(conn, "2026-02-15")
    assert summary["n_clusters"] == 1
    assert summary["n_clustered_tx"] == 3
    a = _fetch_tx(conn, "c21a")["cluster_id"]
    c = _fetch_tx(conn, "c21c")["cluster_id"]
    assert a is not None and a == c


def case_22_cluster_same_director_rejected(conn):
    _insert_meta(conn, ticker="DEF")
    _insert_tx(conn, fp="c22a", ticker="DEF", director="Dave",
               role="CEO", value=120_000.0,
               date_="2026-01-01", announced_at="2026-01-01")
    _insert_tx(conn, fp="c22b", ticker="DEF", director="Dave",
               role="CEO", value=120_000.0,
               date_="2026-01-20", announced_at="2026-01-20")
    summary = detect_clusters.detect(conn, "2026-02-15")
    assert summary["n_clusters"] == 0


def case_23_cluster_more_than_30d_apart(conn):
    _insert_meta(conn, ticker="GHI")
    _insert_tx(conn, fp="c23a", ticker="GHI", director="Ed", role="CEO",
               value=120_000.0, date_="2026-01-01", announced_at="2026-01-01")
    _insert_tx(conn, fp="c23b", ticker="GHI", director="Fran", role="CFO",
               value=120_000.0, date_="2026-02-15", announced_at="2026-02-15")
    summary = detect_clusters.detect(conn, "2026-03-15")
    assert summary["n_clusters"] == 0


# ---------------------------------------------------------------------------
# S1 + T0 cases (24-26)
# ---------------------------------------------------------------------------

def case_24_s1_fires_on_cluster(conn):
    _insert_meta(conn, ticker="JKL")
    _insert_tx(conn, fp="c24a", ticker="JKL", director="G", role="CEO",
               value=120_000.0, date_="2026-01-01", announced_at="2026-01-01")
    _insert_tx(conn, fp="c24b", ticker="JKL", director="H", role="CFO",
               value=120_000.0, date_="2026-01-20", announced_at="2026-01-20")
    detect_clusters.detect(conn, "2026-02-01")
    tx = _fetch_tx(conn, "c24b")
    result = signals_pkg.REGISTRY["s1_cluster_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    )
    assert result is not None, "S1 should fire when cluster has >=2 directors"
    md = json.loads(result["metadata"])
    assert md["cluster_director_count"] >= 2


def case_25_s1_no_cluster(conn):
    _insert_meta(conn, ticker="MNO")
    _insert_tx(conn, fp="c25a", ticker="MNO", director="I", role="CEO",
               value=120_000.0, date_="2026-01-01", announced_at="2026-01-01")
    detect_clusters.detect(conn, "2026-02-01")
    tx = _fetch_tx(conn, "c25a")
    assert signals_pkg.REGISTRY["s1_cluster_buy"].evaluate(
        tx, conn, as_of=tx["announced_at"]
    ) is None


def case_26_t0_needs_s1_and_t1(conn):
    """T0 fires only when BOTH S1 and (T1 or T2) have been recorded."""
    _insert_meta(conn, ticker="PQR")
    _insert_tx(conn, fp="c26a", ticker="PQR", director="J", role="CEO",
               value=200_000.0, date_="2026-01-01", announced_at="2026-01-01")
    _insert_tx(conn, fp="c26b", ticker="PQR", director="K", role="CFO",
               value=200_000.0, date_="2026-01-15", announced_at="2026-01-15")

    detect_clusters.detect(conn, "2026-02-01")
    tx_b = _fetch_tx(conn, "c26b")
    as_of = tx_b["announced_at"]

    # Manually persist S1 + T1a firings for tx_b.
    # B-025 Phase B: t1_ceo_cfo_buy split; T0 checks for any T1a/T1b signal.
    fired_at = "2026-01-15"
    conn.execute(
        "INSERT OR REPLACE INTO signals (signal_id, signal_version, "
        "fingerprint, fired_at, confidence) VALUES (?, ?, ?, ?, ?)",
        ("s1_cluster_buy", "1.0.0", "c26b", fired_at, "med"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO signals (signal_id, signal_version, "
        "fingerprint, fired_at, confidence) VALUES (?, ?, ?, ?, ?)",
        ("t1a_ceo_founder_buy", "1.0.0", "c26b", fired_at, "high"),
    )
    conn.commit()
    result = signals_pkg.REGISTRY["t0_cluster_combo"].evaluate(
        tx_b, conn, as_of=as_of
    )
    assert result is not None, "T0 should fire when both S1 and T1a present"

    # And not fire when only S1 is present.
    conn.execute("DELETE FROM signals WHERE fingerprint = ? "
                 "AND signal_id = 't1a_ceo_founder_buy'", ("c26b",))
    conn.commit()
    assert signals_pkg.REGISTRY["t0_cluster_combo"].evaluate(
        tx_b, conn, as_of=as_of
    ) is None


# ---------------------------------------------------------------------------
# Orchestrator + tier dedup (27-29)
# ---------------------------------------------------------------------------

def case_27_orchestrator_tier_dedup(conn):
    """CEO buy of GBP150k -> only T1a fires, NOT T2/T3/T4."""
    # B-025 Phase B: t1_ceo_cfo_buy split; CEO fires t1a_ceo_founder_buy.
    _insert_meta(conn, ticker="STU")
    _insert_tx(conn, fp="c27", ticker="STU", director="L", role="Chief Executive",
               value=150_000.0, date_="2026-01-31", announced_at="2026-01-31")
    summary = eval_signals.evaluate_all(conn)
    rows = conn.execute(
        "SELECT signal_id FROM signals WHERE fingerprint = ?", ("c27",)
    ).fetchall()
    fired = {r["signal_id"] for r in rows}
    assert "t1a_ceo_founder_buy" in fired, f"expected T1a in {fired}"
    assert "t2_exec_buy" not in fired, f"T2 should be deduped: {fired}"
    assert "t3_ned_buy" not in fired
    assert "t4_other_buy" not in fired
    # F1 fires too (no prior buy).
    assert "f1_first_time_buy" in fired


def case_28_orchestrator_idempotent(conn):
    """Re-running evaluate_all produces zero net new rows."""
    _insert_meta(conn, ticker="VWX")
    _insert_tx(conn, fp="c28", ticker="VWX", director="M", role="CEO",
               value=200_000.0, date_="2026-01-31", announced_at="2026-01-31")
    eval_signals.evaluate_all(conn)
    n1 = conn.execute("SELECT COUNT(*) AS n FROM signals WHERE fingerprint = ?",
                      ("c28",)).fetchone()["n"]
    eval_signals.evaluate_all(conn)
    n2 = conn.execute("SELECT COUNT(*) AS n FROM signals WHERE fingerprint = ?",
                      ("c28",)).fetchone()["n"]
    assert n1 == n2 and n1 > 0, f"n1={n1} n2={n2}"


def case_29_orchestrator_t0_chains(conn):
    """End-to-end: a cluster + CFO buy fires T1b + S1 + F1 + T0 simultaneously."""
    # B-025 Phase B: t1_ceo_cfo_buy split; CFO role fires t1b_cfo_buy.
    _insert_meta(conn, ticker="YZ1")
    _insert_tx(conn, fp="c29a", ticker="YZ1", director="N", role="CEO",
               value=200_000.0, date_="2026-01-01", announced_at="2026-01-01")
    _insert_tx(conn, fp="c29b", ticker="YZ1", director="O", role="CFO",
               value=200_000.0, date_="2026-01-15", announced_at="2026-01-15")
    eval_signals.evaluate_all(conn)
    rows = conn.execute(
        "SELECT signal_id FROM signals WHERE fingerprint = ?", ("c29b",)
    ).fetchall()
    fired = {r["signal_id"] for r in rows}
    assert "t1b_cfo_buy" in fired, fired
    assert "s1_cluster_buy" in fired, fired
    assert "f1_first_time_buy" in fired, fired
    assert "t0_cluster_combo" in fired, fired


# ---------------------------------------------------------------------------
# Backtest cases (30-34)
# ---------------------------------------------------------------------------

def case_30_backtest_car_known_inputs(conn, tmp_dir):
    """Hand-computed CAR for known prices.

    raw_return_t1 = 110/100 - 1 = 0.10
    benchmark_t1  = 1040/1000 - 1 = 0.04
    CAR_t1        = 0.06
    """
    _insert_meta(conn, ticker="BT1", is_aim=0)
    from datetime import date, timedelta
    base = date.fromisoformat("2026-01-01")
    for i in range(31):
        _insert_price(conn, "BT1", (base + timedelta(days=i)).isoformat(), 100.0)
        _insert_price(conn, "^FTAS", (base + timedelta(days=i)).isoformat(), 1000.0)
    _insert_price(conn, "BT1", "2026-02-01", 100.0)
    _insert_price(conn, "^FTAS", "2026-02-01", 1000.0)
    for i in range(32, 75):
        day = (base + timedelta(days=i)).isoformat()
        _insert_price(conn, "BT1", day, 110.0)
        _insert_price(conn, "^FTAS", day, 1040.0)
    _insert_tx(conn, fp="bt30", ticker="BT1", director="P", role="CEO",
               value=200_000.0, date_="2026-01-31", announced_at="2026-01-31")
    eval_signals.evaluate_all(conn)
    out = tmp_dir / "_bt30.csv"
    summary = backtest.run_backtest(conn, out_path=out)
    assert summary["rows_written"] >= 1
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows, "expected at least one CSV row"
    target = [r for r in rows if r["fingerprint"] == "bt30"
              and r["signal_id"] == "t1a_ceo_founder_buy"]
    assert target, "expected a t1a row for bt30"
    r = target[0]
    raw = float(r["raw_return_t1"])
    bench = float(r["benchmark_return_t1"])
    car = float(r["car_t1"])
    assert abs(raw - 0.10) < 1e-9, f"raw_return_t1 = {raw}"
    assert abs(bench - 0.04) < 1e-9, f"benchmark_return_t1 = {bench}"
    assert abs(car - 0.06) < 1e-9, f"car_t1 = {car}"
    cost_bps = int(r["cost_bps"])
    assert cost_bps == 100, f"non-AIM cost_bps should be 100, got {cost_bps}"
    net_t1 = float(r["net_car_t1"])
    assert abs(net_t1 - (0.06 - 0.01)) < 1e-9


def case_31_backtest_aim_cost(conn, tmp_dir):
    """AIM ticker -> cost_bps = 50 (spread only, no stamp)."""
    _insert_meta(conn, ticker="BT2", is_aim=1)
    from datetime import date, timedelta
    base = date.fromisoformat("2026-01-01")
    for i in range(31):
        _insert_price(conn, "BT2", (base + timedelta(days=i)).isoformat(), 100.0)
        _insert_price(conn, "^FTAS", (base + timedelta(days=i)).isoformat(), 1000.0)
    for i in range(31, 60):
        day = (base + timedelta(days=i)).isoformat()
        _insert_price(conn, "BT2", day, 105.0)
        _insert_price(conn, "^FTAS", day, 1020.0)
    _insert_tx(conn, fp="bt31", ticker="BT2", director="Q", role="CEO",
               value=200_000.0, date_="2026-01-31", announced_at="2026-01-31")
    eval_signals.evaluate_all(conn)
    out = tmp_dir / "_bt31.csv"
    backtest.run_backtest(conn, out_path=out)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    target = [r for r in rows if r["fingerprint"] == "bt31"
              and r["signal_id"] == "t1a_ceo_founder_buy"]
    assert target, "expected t1a row for bt31"
    r = target[0]
    assert int(r["cost_bps"]) == 50, f"AIM cost_bps should be 50, got {r['cost_bps']}"


def case_32_backtest_insufficient_history(conn, tmp_dir):
    """Ticker with <30 prior trading days -> skipped to _backtest_skips.json."""
    _insert_meta(conn, ticker="BT3", is_aim=0)
    from datetime import date, timedelta
    base = date.fromisoformat("2026-01-20")
    for i in range(10):
        _insert_price(conn, "BT3", (base + timedelta(days=i)).isoformat(), 100.0)
        _insert_price(conn, "^FTAS", (base + timedelta(days=i)).isoformat(), 1000.0)
    for i in range(10, 40):
        day = (base + timedelta(days=i)).isoformat()
        _insert_price(conn, "BT3", day, 110.0)
        _insert_price(conn, "^FTAS", day, 1010.0)
    _insert_tx(conn, fp="bt32", ticker="BT3", director="R", role="CEO",
               value=200_000.0, date_="2026-01-30", announced_at="2026-01-30")
    eval_signals.evaluate_all(conn)
    out = tmp_dir / "_bt32.csv"
    backtest.run_backtest(conn, out_path=out)
    skips_after = list(json.loads(backtest.SKIPS_PATH.read_text(encoding="utf-8")))
    new_skips = [s for s in skips_after if s.get("fingerprint") == "bt32"]
    assert new_skips, f"expected bt32 to be skipped; {skips_after=}"
    assert "insufficient history" in new_skips[0]["reason"]
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert not [r for r in rows if r["fingerprint"] == "bt32"]


def case_33_backtest_t1_fallback_skip(conn, tmp_dir):
    """If NO trading day exists after announced_at -> skip per Stage 4 dec #9."""
    _insert_meta(conn, ticker="BT4", is_aim=0)
    from datetime import date, timedelta
    base = date.fromisoformat("2026-01-01")
    for i in range(40):
        _insert_price(conn, "BT4", (base + timedelta(days=i)).isoformat(), 100.0)
        _insert_price(conn, "^FTAS", (base + timedelta(days=i)).isoformat(), 1000.0)
    _insert_tx(conn, fp="bt33", ticker="BT4", director="S", role="CEO",
               value=200_000.0, date_="2026-02-10", announced_at="2026-02-10")
    eval_signals.evaluate_all(conn)
    out = tmp_dir / "_bt33.csv"
    backtest.run_backtest(conn, out_path=out)
    skips = json.loads(backtest.SKIPS_PATH.read_text(encoding="utf-8"))
    new_skips = [s for s in skips if s.get("fingerprint") == "bt33"]
    assert new_skips, f"expected bt33 skip; got {skips}"
    assert "no trading day" in new_skips[0]["reason"]


def case_34_backtest_partial_window(conn, tmp_dir):
    """T+252 unavailable -> CSV row written with windows_available reflecting it."""
    _insert_meta(conn, ticker="BT5", is_aim=0)
    from datetime import date, timedelta
    base = date.fromisoformat("2026-01-01")
    for i in range(31):
        _insert_price(conn, "BT5", (base + timedelta(days=i)).isoformat(), 100.0)
        _insert_price(conn, "^FTAS", (base + timedelta(days=i)).isoformat(), 1000.0)
    for i in range(31, 82):
        day = (base + timedelta(days=i)).isoformat()
        _insert_price(conn, "BT5", day, 105.0)
        _insert_price(conn, "^FTAS", day, 1020.0)
    _insert_tx(conn, fp="bt34", ticker="BT5", director="T", role="CEO",
               value=200_000.0, date_="2026-01-31", announced_at="2026-01-31")
    eval_signals.evaluate_all(conn)
    out = tmp_dir / "_bt34.csv"
    backtest.run_backtest(conn, out_path=out)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    target = [r for r in rows if r["fingerprint"] == "bt34"
              and r["signal_id"] == "t1a_ceo_founder_buy"]
    assert target, "expected t1a row"
    r = target[0]
    wa = r["windows_available"].split(",")
    assert "t1" in wa and "t30" in wa
    assert "t365" not in wa


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _wipe(conn):
    """Wipe state between cases (keep schema).
    Order matters: paper_trades FK -> signals; signals FK -> transactions.
    """
    for tbl in ("paper_trades", "signals", "transactions", "prices",
                "tickers_meta", "backtest_runs"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="dd_stage4_"))
    real_db_dir = db.DB_DIR
    real_db_path = db.DB_PATH
    real_skips = backtest.SKIPS_PATH
    try:
        db.DB_DIR = tmp_dir
        db.DB_PATH = tmp_dir / "directors.db"
        backtest.SKIPS_PATH = tmp_dir / "_backtest_skips.json"
        backtest.DEFAULT_OUT = tmp_dir / "_backtest_results.csv"

        # Pure-function role cases (no DB needed).
        run_case("01. role: Group Chief Executive -> T1a", case_01_role_ceo)
        run_case("02. role: Chief Financial Officer -> T1b", case_02_role_cfo)
        run_case("03. role: CFO acronym -> T1b", case_03_role_ceo_acronym)
        run_case("04. role: Non-Executive Director -> T3", case_04_role_ned)
        run_case("05. role: Non Executive Director (space) -> T3", case_05_role_ned_space_variant)
        run_case("06. role: Senior Independent Director -> T3 (Q-SID)", case_06_role_sid_is_t3)
        run_case("07. role: Chairman -> T7", case_07_role_chairman_t7)
        run_case("08. role: Chief Operating Officer -> T2", case_08_role_coo_t2)
        run_case("09. role: None/empty/unknown -> T4", case_09_role_none_t4)

        conn = db.connect()
        try:
            cases_with_conn = [
                ("10. T1a fires CEO buy >= 100k", case_10_t1_fires),
                ("11. T1a below threshold", case_11_t1_below_threshold),
                ("12. T1a SELL no-fire", case_12_t1_sell_no_fire),
                ("13. T2 fires COO >= 25k", case_13_t2_fires),
                ("14. T3 fires NED >= 10k", case_14_t3_fires_ned),
                ("15. T3 fires SID >= 10k (Q-SID)", case_15_t3_fires_sid),
                ("16. T4 fires unknown role >= 1k", case_16_t4_fires_unknown_role),
                ("17. T4 below threshold", case_17_t4_below_threshold),
                ("18. F1 fires first-time buy", case_18_f1_fires_first_time),
                ("19. F1 no-fire when prior buy exists", case_19_f1_does_not_fire_when_prior_buy),
                ("20. F1 walk-forward (ignore future BUY)", case_20_f1_walk_forward),
                ("21. cluster connected-component (transitive)", case_21_cluster_transitive),
                ("22. cluster same-director rejected", case_22_cluster_same_director_rejected),
                ("23. cluster >30 days apart no-cluster", case_23_cluster_more_than_30d_apart),
                ("24. S1 fires on cluster", case_24_s1_fires_on_cluster),
                ("25. S1 no-cluster no-fire", case_25_s1_no_cluster),
                ("26. T0 needs S1 + T1 to fire", case_26_t0_needs_s1_and_t1),
                ("27. orchestrator tier dedup T1a>T2>T3>T4", case_27_orchestrator_tier_dedup),
                ("28. orchestrator idempotent", case_28_orchestrator_idempotent),
                ("29. orchestrator T0 chains", case_29_orchestrator_t0_chains),
            ]
            for name, fn in cases_with_conn:
                _wipe(conn)
                run_case(name, (lambda f=fn: f(conn)))

            cases_with_conn_tmp = [
                ("30. backtest CAR hand-computed", case_30_backtest_car_known_inputs),
                ("31. backtest AIM cost = 50bps", case_31_backtest_aim_cost),
                ("32. backtest insufficient-history skip", case_32_backtest_insufficient_history),
                ("33. backtest no-T+1 -> skip", case_33_backtest_t1_fallback_skip),
                ("34. backtest partial-window flag", case_34_backtest_partial_window),
            ]
            for name, fn in cases_with_conn_tmp:
                _wipe(conn)
                if backtest.SKIPS_PATH.exists():
                    backtest.SKIPS_PATH.unlink()
                run_case(name, (lambda f=fn: f(conn, tmp_dir)))
        finally:
            conn.close()
    finally:
        db.DB_DIR = real_db_dir
        db.DB_PATH = real_db_path
        backtest.SKIPS_PATH = real_skips
        shutil.rmtree(tmp_dir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
