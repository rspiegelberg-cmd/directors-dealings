"""Tests for signals.b1_lone_conviction_buy_v1.evaluate (Sprint 13).

Uses an in-memory SQLite DB with the full project schema so the lone-buyer
query and the price momentum query hit real SQL rather than mocks.

Run:
    python -m unittest test_signals_b1 -v
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db as db_mod
from signals.b1_lone_conviction_buy_v1 import evaluate, SIGNAL_ID


# ---------------------------------------------------------------------------
# Helpers

def _make_conn():
    """In-memory SQLite with the full schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_mod.migrate(conn)
    return conn


def _insert_tx(conn, **kwargs):
    """Insert a minimal transaction row. Caller supplies overrides."""
    defaults = {
        "fingerprint":    "fp-test",
        "first_seen":     "2024-01-01T00:00:00Z",
        "last_seen":      "2024-01-01T00:00:00Z",
        "seen_count":     1,
        "date":           "2024-06-01",
        "ticker":         "ABC",
        "company":        "Acme Corp",
        "director":       "Alice Smith",
        "role":           "CEO",
        "role_normalized":"CEO",
        "type":           "BUY",
        "shares":         10000,
        "price":          20.0,
        "value":          200000.0,
        "context":        None,
        "url":            "https://example.com/1",
        "announced_at":   "2024-06-01",
        "cluster_id":     None,
        "first_time_buy": 0,
        "parser_source":  "test",
        "buy_strictness": "STRICT_BUY",
    }
    defaults.update(kwargs)
    conn.execute(
        "INSERT INTO transactions ("
        "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        "company, director, role, role_normalized, type, shares, price, "
        "value, context, url, announced_at, cluster_id, first_time_buy, "
        "parser_source, buy_strictness"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(defaults[k] for k in [
            "fingerprint", "first_seen", "last_seen", "seen_count", "date",
            "ticker", "company", "director", "role", "role_normalized",
            "type", "shares", "price", "value", "context", "url",
            "announced_at", "cluster_id", "first_time_buy",
            "parser_source", "buy_strictness",
        ]),
    )
    conn.commit()
    return dict(defaults)


def _insert_price(conn, ticker, date, close):
    conn.execute(
        "INSERT INTO prices (ticker, date, close, source, fetched_at) "
        "VALUES (?, ?, ?, 'test', '2024-01-01T00:00:00Z')",
        (ticker, date, close),
    )
    conn.commit()


def _tx_row(conn, fingerprint="fp-test"):
    return conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Tests

class TestB1FiresOnValidTransaction(unittest.TestCase):
    """B1 fires when all five criteria are satisfied."""

    def test_basic_fire(self):
        conn = _make_conn()
        _insert_tx(conn)
        tx = _tx_row(conn)
        result = evaluate(tx, conn, "2024-06-01")
        self.assertIsNotNone(result)
        self.assertEqual(result["signal_id"], SIGNAL_ID)
        self.assertEqual(result["confidence"], "high")

    def test_metadata_fields_present(self):
        conn = _make_conn()
        _insert_tx(conn)
        tx = _tx_row(conn)
        result = evaluate(tx, conn, "2024-06-01")
        meta = json.loads(result["metadata"])
        self.assertIn("value_gbp", meta)
        self.assertIn("n_other_buyers_30d", meta)
        self.assertIn("momentum_60d", meta)
        self.assertEqual(meta["n_other_buyers_30d"], 0)

    def test_fires_with_no_price_data(self):
        """No price data -> momentum is None -> criterion passes."""
        conn = _make_conn()
        _insert_tx(conn)
        tx = _tx_row(conn)
        result = evaluate(tx, conn, "2024-06-01")
        self.assertIsNotNone(result)
        meta = json.loads(result["metadata"])
        self.assertIsNone(meta["momentum_60d"])


class TestB1GateBuyStrictness(unittest.TestCase):
    """Gate 1 + 2: type and buy_strictness filters."""

    def test_sell_type_blocked(self):
        conn = _make_conn()
        _insert_tx(conn, type="SELL", buy_strictness=None)
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_non_buy_only_blocked(self):
        conn = _make_conn()
        _insert_tx(conn, buy_strictness="NON_BUY_ONLY")
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_mixed_blocked(self):
        conn = _make_conn()
        _insert_tx(conn, buy_strictness="MIXED")
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_unknown_blocked(self):
        conn = _make_conn()
        _insert_tx(conn, buy_strictness="UNKNOWN")
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_null_buy_strictness_blocked(self):
        """NULL buy_strictness on the TX itself fails Gate 1 in the module
        (defense-in-depth — universe filter should have already excluded it,
        but the module checks explicitly)."""
        conn = _make_conn()
        _insert_tx(conn, buy_strictness=None)
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))


class TestB1GateValue(unittest.TestCase):
    """Gate 3: minimum value >= £200k."""

    def test_exactly_200k_fires(self):
        conn = _make_conn()
        _insert_tx(conn, value=200_000.0)
        tx = _tx_row(conn)
        self.assertIsNotNone(evaluate(tx, conn, "2024-06-01"))

    def test_below_200k_blocked(self):
        conn = _make_conn()
        _insert_tx(conn, value=199_999.99)
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_no_announced_at_blocked(self):
        conn = _make_conn()
        _insert_tx(conn, announced_at=None)
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))


class TestB1GateLoneBuyer(unittest.TestCase):
    """Gate 4: lone-buyer check."""

    def test_other_director_same_ticker_in_window_blocks(self):
        conn = _make_conn()
        _insert_tx(conn, fingerprint="fp-alice", director="Alice Smith",
                   announced_at="2024-06-01")
        # Bob buys same ticker 15 days later (within ±30d window)
        _insert_tx(conn, fingerprint="fp-bob", director="Bob Jones",
                   announced_at="2024-06-16", buy_strictness="STRICT_BUY")
        tx = _tx_row(conn, "fp-alice")
        # as_of must be >= bob's announced_at to see him
        self.assertIsNone(evaluate(tx, conn, "2024-07-01"))

    def test_other_director_outside_window_does_not_block(self):
        conn = _make_conn()
        _insert_tx(conn, fingerprint="fp-alice", director="Alice Smith",
                   announced_at="2024-06-01")
        # Bob buys same ticker 45 days later (outside ±30d window)
        _insert_tx(conn, fingerprint="fp-bob", director="Bob Jones",
                   announced_at="2024-07-16", buy_strictness="STRICT_BUY")
        tx = _tx_row(conn, "fp-alice")
        self.assertIsNotNone(evaluate(tx, conn, "2024-07-16"))

    def test_other_director_non_buy_only_does_not_block(self):
        """NON_BUY_ONLY other director (LTIP etc.) must not trigger lone-buyer fail."""
        conn = _make_conn()
        _insert_tx(conn, fingerprint="fp-alice", director="Alice Smith",
                   announced_at="2024-06-01")
        _insert_tx(conn, fingerprint="fp-bob", director="Bob Jones",
                   announced_at="2024-06-10", buy_strictness="NON_BUY_ONLY")
        tx = _tx_row(conn, "fp-alice")
        self.assertIsNotNone(evaluate(tx, conn, "2024-07-01"))

    def test_walk_forward_gate_hides_future_buyer(self):
        """Other director announced AFTER as_of must not be visible."""
        conn = _make_conn()
        _insert_tx(conn, fingerprint="fp-alice", director="Alice Smith",
                   announced_at="2024-06-01")
        _insert_tx(conn, fingerprint="fp-bob", director="Bob Jones",
                   announced_at="2024-06-15", buy_strictness="STRICT_BUY")
        tx = _tx_row(conn, "fp-alice")
        # Evaluate as of the same day as alice — bob hasn't announced yet
        self.assertIsNotNone(evaluate(tx, conn, "2024-06-01"))


class TestB1GateMomentum(unittest.TestCase):
    """Gate 5: momentum exclusion zone [-10%, -2%)."""

    def _conn_with_prices(self, start_close, end_close):
        conn = _make_conn()
        _insert_tx(conn, date="2024-06-01", announced_at="2024-06-01")
        _insert_price(conn, "ABC", "2024-04-02", start_close)   # ~60d ago
        _insert_price(conn, "ABC", "2024-06-01", end_close)
        return conn

    def test_momentum_outside_zone_fires(self):
        """Stock up 5% — well outside exclusion zone."""
        conn = self._conn_with_prices(100.0, 105.0)
        tx = _tx_row(conn)
        self.assertIsNotNone(evaluate(tx, conn, "2024-06-01"))

    def test_momentum_in_exclusion_zone_blocks(self):
        """-6% is inside [-10%, -2%) — should block."""
        conn = self._conn_with_prices(100.0, 94.0)
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_momentum_at_lower_bound_blocks(self):
        """-10% exactly is the inclusive lower bound — should block."""
        conn = self._conn_with_prices(100.0, 90.0)
        tx = _tx_row(conn)
        self.assertIsNone(evaluate(tx, conn, "2024-06-01"))

    def test_momentum_at_upper_bound_fires(self):
        """-2% exactly is the exclusive upper bound — should NOT block."""
        conn = self._conn_with_prices(100.0, 98.0)
        tx = _tx_row(conn)
        self.assertIsNotNone(evaluate(tx, conn, "2024-06-01"))

    def test_momentum_sharply_down_fires(self):
        """-15% is below exclusion zone lower bound — not blocked."""
        conn = self._conn_with_prices(100.0, 85.0)
        tx = _tx_row(conn)
        self.assertIsNotNone(evaluate(tx, conn, "2024-06-01"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
