"""B-094 tests -- announced_at non-ISO date normalisation.

Covers:
  1. Scraper: _index_row_date correctly parses 'DD Mon YYYY' -> date object,
     which the scraper now uses to emit ISO announced_at.
  2. b1 signal: _normalise_announced_at handles all expected formats.
  3. b2 signal: same helper, same contract.
  4. Both signals: evaluate() no longer silently returns None on a 'DD Mon YYYY'
     announced_at (integration with a mock tx dict).
"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# -- path setup so we can import from .scripts/ and .scripts/signals/ --------
SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "signals"))


class TestScraperISO(unittest.TestCase):
    """Scraper emits ISO announced_at for 'DD Mon YYYY' index rows."""

    def setUp(self):
        import scrape_investegate as s
        self.mod = s

    def test_index_row_date_parses_human(self):
        d = self.mod._index_row_date("02 Jun 2026")
        self.assertEqual(d, date(2026, 6, 2))

    def test_index_row_date_parses_padded(self):
        d = self.mod._index_row_date("2 Jun 2026")
        self.assertEqual(d, date(2026, 6, 2))

    def test_index_row_date_returns_none_for_iso(self):
        # ISO format doesn't match _INDEX_DATE_RE -> None (scraper keeps raw,
        # which is already ISO -- correct fallback behaviour).
        d = self.mod._index_row_date("2026-06-02")
        self.assertIsNone(d)

    def test_index_row_date_none_input(self):
        self.assertIsNone(self.mod._index_row_date(None))

    def test_index_row_date_isoformat(self):
        """row_date.isoformat() gives the ISO string the scraper now stores."""
        d = self.mod._index_row_date("02 Jun 2026")
        self.assertEqual(d.isoformat(), "2026-06-02")

    def test_index_row_date_december(self):
        d = self.mod._index_row_date("31 Dec 2025")
        self.assertEqual(d, date(2025, 12, 31))

    def test_index_row_date_january(self):
        d = self.mod._index_row_date("1 Jan 2026")
        self.assertEqual(d, date(2026, 1, 1))


class TestB1Normaliser(unittest.TestCase):
    """b1 _normalise_announced_at handles all expected formats."""

    def setUp(self):
        import b1_lone_conviction_buy_v1 as b1
        self.norm = b1._normalise_announced_at

    def test_iso_date_passthrough(self):
        self.assertEqual(self.norm("2026-06-02"), "2026-06-02")

    def test_iso_timestamp_truncated(self):
        self.assertEqual(self.norm("2026-06-02T16:15:07Z"), "2026-06-02")

    def test_dd_mon_yyyy(self):
        self.assertEqual(self.norm("02 Jun 2026"), "2026-06-02")

    def test_d_mon_yyyy(self):
        self.assertEqual(self.norm("2 Jun 2026"), "2026-06-02")

    def test_dd_Month_yyyy(self):
        self.assertEqual(self.norm("02 June 2026"), "2026-06-02")

    def test_none_input(self):
        self.assertIsNone(self.norm(None))

    def test_empty_string(self):
        self.assertIsNone(self.norm(""))

    def test_garbage(self):
        self.assertIsNone(self.norm("not a date"))

    def test_whitespace_stripped(self):
        self.assertEqual(self.norm("  02 Jun 2026  "), "2026-06-02")

    def test_december(self):
        self.assertEqual(self.norm("31 Dec 2025"), "2025-12-31")


class TestB2Normaliser(unittest.TestCase):
    """b2 _normalise_announced_at -- same contract as b1."""

    def setUp(self):
        import b2_crowded_cluster_kill_v1 as b2
        self.norm = b2._normalise_announced_at

    def test_iso_date_passthrough(self):
        self.assertEqual(self.norm("2026-06-02"), "2026-06-02")

    def test_dd_mon_yyyy(self):
        self.assertEqual(self.norm("03 Jun 2026"), "2026-06-03")

    def test_none_input(self):
        self.assertIsNone(self.norm(None))

    def test_empty_string(self):
        self.assertIsNone(self.norm(""))

    def test_garbage(self):
        self.assertIsNone(self.norm("bad data"))


class TestB1EvaluateNonISO(unittest.TestCase):
    """b1 evaluate() does NOT silently return None on 'DD Mon YYYY' announced_at.

    Before B-094, the [:10] slice turned "02 Jun 2026" -> "02 Jun 20", which
    strptime rejected -> silent None. Now evaluate() normalises first and only
    returns None when the date is genuinely unparseable.
    """

    def setUp(self):
        import b1_lone_conviction_buy_v1 as b1
        self.b1 = b1

    def _make_tx(self, announced_at):
        tx = MagicMock()
        tx.__getitem__ = lambda s, k: {
            "type": "BUY",
            "buy_strictness": "STRICT_BUY",
            "value": 300_000,
            "announced_at": announced_at,
            "ticker": "JMAT",
            "role": "CEO",
            "director": "Test Director",
            "date": "2026-06-02",
            "fingerprint": "test-fp-b094",
        }[k]
        return tx

    def _make_conn(self, n_others=0):
        """Return a conn mock that handles both the lone-buyer query (returns n)
        and the _trailing_return price queries (returns close=None -> price
        unavailable -> momentum gate passes by conservative fallback)."""
        conn = MagicMock()
        def _fetchone_side_effect():
            # Return a generic row whose __getitem__ handles any key.
            # n=0 satisfies lone-buyer gate; close=None makes _trailing_return
            # return None (price unavailable -> pass through momentum gate).
            row = MagicMock()
            row.__getitem__ = lambda s, k: {"n": n_others, "close": None, "date": "2026-06-02"}.get(k)
            return row
        conn.execute.return_value.fetchone.side_effect = _fetchone_side_effect
        return conn

    def test_human_date_does_not_return_none_at_gate4(self):
        """With a 'DD Mon YYYY' announced_at, evaluate() should proceed past
        gate 4 (i.e. not return None at the strptime step)."""
        tx = self._make_tx("02 Jun 2026")
        conn = self._make_conn(n_others=0)
        result = self.b1.evaluate(tx, conn, "2026-06-02")
        # conn.execute must have been called — proves we got past gate 4.
        # Result may be None from later gates, but NOT from the date parse.
        self.assertTrue(
            conn.execute.called,
            "evaluate() returned None at gate 4 without reaching the DB query "
            "-- normalisation fix may not be working",
        )

    def test_iso_date_still_works(self):
        """ISO announced_at still functions correctly after the change."""
        tx = self._make_tx("2026-06-02")
        conn = self._make_conn(n_others=0)
        self.b1.evaluate(tx, conn, "2026-06-02")
        self.assertTrue(conn.execute.called)

    def test_garbage_date_still_returns_none(self):
        """A genuinely unparseable announced_at should still return None."""
        tx = self._make_tx("not-a-date")
        conn = MagicMock()
        result = self.b1.evaluate(tx, conn, "2026-06-02")
        self.assertIsNone(result)


class TestB2EvaluateNonISO(unittest.TestCase):
    """b2 evaluate() does NOT silently return None on 'DD Mon YYYY' announced_at."""

    def setUp(self):
        import b2_crowded_cluster_kill_v1 as b2
        self.b2 = b2

    def _make_tx(self, announced_at):
        tx = MagicMock()
        tx.__getitem__ = lambda s, k: {
            "type": "BUY",
            "buy_strictness": "STRICT_BUY",
            "announced_at": announced_at,
            "ticker": "JMAT",
        }[k]
        return tx

    def test_human_date_reaches_db_query(self):
        tx = self._make_tx("03 Jun 2026")
        conn = MagicMock()
        not_crowded = MagicMock()
        not_crowded.__getitem__ = lambda s, k: {"n": 1}[k]
        conn.execute.return_value.fetchone.return_value = not_crowded

        self.b2.evaluate(tx, conn, "2026-06-03")
        self.assertTrue(
            conn.execute.called,
            "b2.evaluate() returned None at date-parse gate with 'DD Mon YYYY' input",
        )

    def test_garbage_date_returns_none(self):
        tx = self._make_tx("bad-date")
        conn = MagicMock()
        result = self.b2.evaluate(tx, conn, "2026-06-03")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
