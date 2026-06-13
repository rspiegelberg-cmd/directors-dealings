"""Tests for B2 -- Crowded Cluster Kill signal (Sprint 13).

Covers all six gates of the evaluate() function:
  G1  type == BUY
  G2  buy_strictness == STRICT_BUY
  G3  announced_at present
  G4  COUNT(DISTINCT director) >= 4 in the 30-day walk-forward window
  Boundary: director exactly 30d ago counted; 31d ago not counted
  Walk-forward: directors with announced_at > as_of not counted
  Ticker isolation: cluster from a different ticker not counted
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db as db_mod
import signals.b2_crowded_cluster_kill_v1 as b2_mod

BASE_DATE = "2024-06-15"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn():
    """In-memory SQLite connection with full schema (no FUSE, no disk write)."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db_mod.migrate(conn)
    return conn


def _insert_tx(conn, *, fingerprint, ticker, director,
               type_="BUY", buy_strictness="STRICT_BUY",
               announced_at=BASE_DATE, date_=BASE_DATE,
               shares=10_000, price=10.0, value=100_000.0,
               role="CEO", first_seen=BASE_DATE, last_seen=BASE_DATE,
               company="ACME PLC"):
    conn.execute(
        "INSERT INTO transactions "
        "(fingerprint, ticker, director, type, buy_strictness, "
        " announced_at, date, shares, price, value, role, first_seen, last_seen, company) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fingerprint, ticker, director, type_, buy_strictness,
         announced_at, date_, shares, price, value, role, first_seen, last_seen, company),
    )
    conn.commit()


def _tx_row(conn, fingerprint):
    """Fetch a single transaction as a sqlite3.Row (mirrors orchestrator path)."""
    return conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()


def _days_offset(base: str, delta: int) -> str:
    """Return an ISO date `delta` days from `base`."""
    return (datetime.strptime(base, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestB2FiresOnValidCluster(unittest.TestCase):
    """Basic firing: four or more distinct directors triggers B2."""

    def test_four_directors_fires(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="AAA",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        self.assertIsNotNone(result)

    def test_five_directors_fires(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave", "Eve"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="BBB",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        self.assertIsNotNone(result)

    def test_signal_id_is_correct(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="CCC",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        self.assertEqual(result["signal_id"], "b2_crowded_cluster_kill")

    def test_confidence_is_kill(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="DDD",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        self.assertEqual(result["confidence"], "kill")

    def test_metadata_fields(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="EEE",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        meta = json.loads(result["metadata"])
        self.assertEqual(meta["ticker"], "EEE")
        self.assertEqual(meta["n_distinct_directors_30d"], 4)
        self.assertEqual(meta["suppression_days"], 60)
        self.assertIn("window_start", meta)

    def test_fingerprint_passthrough(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="FFF",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp2")   # fp2 = "Carol"
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        self.assertEqual(result["fingerprint"], "fp2")


class TestB2GateMinDirectors(unittest.TestCase):
    """Threshold: exactly 3 directors does not fire; 4 does."""

    def test_three_directors_no_fire(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="GGG",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_two_directors_no_fire(self):
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="HHH",
                       director=name, announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_lone_buyer_no_fire(self):
        conn = _make_conn()
        _insert_tx(conn, fingerprint="fp0", ticker="III",
                   director="Alice", announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_same_director_twice_counts_once(self):
        """Two buys by same director = 1 distinct; 3 distinct total → no fire."""
        conn = _make_conn()
        # Alice buys twice
        _insert_tx(conn, fingerprint="fp0", ticker="JJJ",
                   director="Alice", announced_at=BASE_DATE)
        _insert_tx(conn, fingerprint="fp1", ticker="JJJ",
                   director="Alice", announced_at=_days_offset(BASE_DATE, -5))
        # Bob and Carol each buy once → 3 distinct total (Alice, Bob, Carol)
        _insert_tx(conn, fingerprint="fp2", ticker="JJJ",
                   director="Bob", announced_at=BASE_DATE)
        _insert_tx(conn, fingerprint="fp3", ticker="JJJ",
                   director="Carol", announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp0")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))


class TestB2GateBuyType(unittest.TestCase):
    """Gate G1: only BUY rows can trigger B2."""

    def _setup_cluster(self, conn, ticker, n=4):
        names = ["Alice", "Bob", "Carol", "Dave", "Eve"]
        for i in range(n):
            _insert_tx(conn, fingerprint=f"fp_{ticker}_{i}", ticker=ticker,
                       director=names[i], announced_at=BASE_DATE)

    def test_sell_blocked(self):
        conn = _make_conn()
        self._setup_cluster(conn, "KKK")
        _insert_tx(conn, fingerprint="fp_sell", ticker="KKK",
                   director="Eve", type_="SELL", announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp_sell")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_exercise_blocked(self):
        conn = _make_conn()
        self._setup_cluster(conn, "LLL")
        _insert_tx(conn, fingerprint="fp_ex", ticker="LLL",
                   director="Eve", type_="EXERCISE", announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp_ex")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))


class TestB2GateBuyStrictness(unittest.TestCase):
    """Gate G2: only STRICT_BUY rows can trigger B2 (the evaluated tx)."""

    def _setup_cluster(self, conn, ticker, n=4):
        names = ["Alice", "Bob", "Carol", "Dave"]
        for i in range(n):
            _insert_tx(conn, fingerprint=f"fp_{ticker}_{i}", ticker=ticker,
                       director=names[i], announced_at=BASE_DATE)

    def test_non_buy_only_blocked(self):
        conn = _make_conn()
        self._setup_cluster(conn, "MMM")
        _insert_tx(conn, fingerprint="fp_nbo", ticker="MMM", director="Eve",
                   type_="BUY", buy_strictness="NON_BUY_ONLY",
                   announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp_nbo")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_mixed_blocked(self):
        conn = _make_conn()
        self._setup_cluster(conn, "NNN")
        _insert_tx(conn, fingerprint="fp_mix", ticker="NNN", director="Eve",
                   type_="BUY", buy_strictness="MIXED",
                   announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp_mix")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_unknown_blocked(self):
        conn = _make_conn()
        self._setup_cluster(conn, "OOO")
        _insert_tx(conn, fingerprint="fp_unk", ticker="OOO", director="Eve",
                   type_="BUY", buy_strictness="UNKNOWN",
                   announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp_unk")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_null_buy_strictness_blocked(self):
        """Evaluated tx with NULL buy_strictness is blocked at Gate G2."""
        conn = _make_conn()
        self._setup_cluster(conn, "PPP")
        conn.execute(
            "INSERT INTO transactions "
            "(fingerprint, ticker, director, type, buy_strictness, "
            " announced_at, date, shares, price, value, first_seen, last_seen, company) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("fp_null", "PPP", "Eve", "BUY", None,
             BASE_DATE, BASE_DATE, 1_000, 10.0, 10_000.0, BASE_DATE, BASE_DATE, "ACME PLC"),
        )
        conn.commit()
        tx = _tx_row(conn, "fp_null")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))


class TestB2WindowBoundary(unittest.TestCase):
    """30-day window edge cases: exactly-30d counted, exactly-31d not counted."""

    def test_director_exactly_30d_ago_counted(self):
        """Buy exactly 30 days before BASE_DATE sits on window_start → included."""
        conn = _make_conn()
        # Three directors on BASE_DATE
        for i, name in enumerate(["Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="QQQ",
                       director=name, announced_at=BASE_DATE)
        # Fourth director exactly 30 days ago
        thirty_ago = _days_offset(BASE_DATE, -30)
        _insert_tx(conn, fingerprint="fp_edge", ticker="QQQ",
                   director="Alice", announced_at=thirty_ago)
        tx = _tx_row(conn, "fp0")
        # 4 distinct directors within [thirty_ago, BASE_DATE] → fires
        result = b2_mod.evaluate(tx, conn, as_of=BASE_DATE)
        self.assertIsNotNone(result)

    def test_director_31d_ago_not_counted(self):
        """Buy 31 days ago falls outside the window → not counted."""
        conn = _make_conn()
        for i, name in enumerate(["Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="RRR",
                       director=name, announced_at=BASE_DATE)
        thirty_one_ago = _days_offset(BASE_DATE, -31)
        _insert_tx(conn, fingerprint="fp_out", ticker="RRR",
                   director="Alice", announced_at=thirty_one_ago)
        tx = _tx_row(conn, "fp0")
        # Only 3 distinct directors within window → does not fire
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))


class TestB2WalkForward(unittest.TestCase):
    """Walk-forward gate: future transactions are invisible."""

    def test_future_director_not_counted(self):
        """Director who buys tomorrow is not counted when as_of = BASE_DATE."""
        conn = _make_conn()
        for i, name in enumerate(["Alice", "Bob", "Carol"]):
            _insert_tx(conn, fingerprint=f"fp{i}", ticker="SSS",
                       director=name, announced_at=BASE_DATE)
        _insert_tx(conn, fingerprint="fp_future", ticker="SSS",
                   director="Dave", announced_at=_days_offset(BASE_DATE, 1))
        tx = _tx_row(conn, "fp0")
        # Only 3 directors visible at as_of=BASE_DATE → no fire
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))

    def test_null_announced_at_blocked(self):
        """Gate G3: tx with NULL announced_at returns None immediately."""
        conn = _make_conn()
        conn.execute(
            "INSERT INTO transactions "
            "(fingerprint, ticker, director, type, buy_strictness, "
            " announced_at, date, shares, price, value, first_seen, last_seen, company) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("fp_noann", "TTT", "Alice", "BUY", "STRICT_BUY",
             None, BASE_DATE, 1_000, 10.0, 200_001.0, BASE_DATE, BASE_DATE, "ACME PLC"),
        )
        conn.commit()
        tx = _tx_row(conn, "fp_noann")
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))


class TestB2TickerIsolation(unittest.TestCase):
    """Directors from a different ticker do not contribute to the cluster count."""

    def test_different_ticker_not_counted(self):
        conn = _make_conn()
        # 4 directors all buying ticker "UUU"
        for i, name in enumerate(["Alice", "Bob", "Carol", "Dave"]):
            _insert_tx(conn, fingerprint=f"fp_uuu{i}", ticker="UUU",
                       director=name, announced_at=BASE_DATE)
        # 1 director buying ticker "VVV" (the evaluated tx)
        _insert_tx(conn, fingerprint="fp_vvv0", ticker="VVV",
                   director="Eve", announced_at=BASE_DATE)
        tx = _tx_row(conn, "fp_vvv0")
        # Only 1 director for "VVV" → no fire
        self.assertIsNone(b2_mod.evaluate(tx, conn, as_of=BASE_DATE))


if __name__ == "__main__":
    unittest.main()
