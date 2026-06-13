"""B-060 integration test for backfill_price_units.run against a temp DB.

Uses a throwaway sqlite file (no .data write) so it is safe in Claude's Linux
sandbox per CLAUDE.md. Verifies the four reconciliation outcomes end-to-end and
the signal-exclusion semantics of the eval_signals candidate filter.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_price_units as bp  # noqa: E402

# The exact exclusion clause added to eval_signals' candidate query.
EVAL_FILTER = "COALESCE(price_audit, 'ok') NOT IN ('unresolved', 'no_market')"


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE transactions (
            fingerprint TEXT PRIMARY KEY,
            date TEXT, ticker TEXT, type TEXT,
            shares INTEGER, price REAL, value REAL,
            price_audit TEXT
        );
        CREATE TABLE prices (ticker TEXT, date TEXT, close REAL);
        """
    )
    # transactions: (fp, date, ticker, type, shares, price, value)
    txs = [
        ("igp",  "2025-06-24", "IGP",  "BUY",      60000, 171.0,     10260000.0),
        ("pnd",  "2025-09-01", "GEN",  "BUY",        100,  27.0,         2700.0),
        ("idox", "2026-04-22", "IDOX", "EXERCISE",     2, 1837455.0,  3674910.0),
        ("nomk", "2025-12-01", "ZZZ",  "BUY",         10, 171.0,         1710.0),
    ]
    conn.executemany(
        "INSERT INTO transactions "
        "(fingerprint,date,ticker,type,shares,price,value,price_audit) "
        "VALUES (?,?,?,?,?,?,?,NULL)", txs
    )
    # prices (pounds). ZZZ deliberately absent -> no_market.
    prices = [
        ("IGP",  "2025-06-24", 1.70),
        ("GEN",  "2025-09-01", 27.10),
        ("IDOX", "2026-04-22", 0.60),
    ]
    conn.executemany("INSERT INTO prices VALUES (?,?,?)", prices)
    conn.commit()
    return conn


class TestBackfill(unittest.TestCase):
    def setUp(self):
        fd, self.dbpath = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        fd2, self.logpath = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd2)
        self.conn = _make_db(self.dbpath)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.dbpath)
        os.unlink(self.logpath)

    def test_reconciliation_outcomes(self):
        summary = bp.run(self.conn, confirm=True, window_days=7,
                         log_path=Path(self.logpath))
        self.assertEqual(summary["_total_rows"], 4)
        self.assertEqual(summary.get("corrected_pence"), 1)
        self.assertEqual(summary.get("ok_pounds"), 1)
        self.assertEqual(summary.get("unresolved"), 1)
        self.assertEqual(summary.get("no_market"), 1)

        rows = {r["fingerprint"]: r for r in
                self.conn.execute("SELECT * FROM transactions")}
        # IGP pence corrected: 171 -> 1.71, value 60000*1.71
        self.assertEqual(rows["igp"]["price_audit"], "corrected_pence")
        self.assertAlmostEqual(rows["igp"]["price"], 1.71, places=2)
        self.assertAlmostEqual(rows["igp"]["value"], 102600, delta=1)
        # Genuine pounds untouched
        self.assertEqual(rows["pnd"]["price_audit"], "ok_pounds")
        self.assertEqual(rows["pnd"]["price"], 27.0)
        self.assertEqual(rows["pnd"]["value"], 2700.0)
        # IDOX garbage flagged; value + price LEFT INTACT (NOT NULL column),
        # excluded from metrics at read time, not by mutating the row.
        self.assertEqual(rows["idox"]["price_audit"], "unresolved")
        self.assertEqual(rows["idox"]["value"], 3674910.0)
        self.assertEqual(rows["idox"]["price"], 1837455.0)
        # No market price -> flagged; value left intact.
        self.assertEqual(rows["nomk"]["price_audit"], "no_market")
        self.assertEqual(rows["nomk"]["value"], 1710.0)

    def test_read_time_value_exclusion(self):
        # The CASE used by export_dashboard_json: flagged rows read value=NULL
        # (excluded from GBP metrics) while the row itself is still counted.
        bp.run(self.conn, confirm=True, window_days=7,
               log_path=Path(self.logpath))
        case = ("SELECT fingerprint, "
                "CASE WHEN COALESCE(price_audit,'ok') "
                "  IN ('unresolved','no_market') THEN NULL ELSE value END AS v "
                "FROM transactions")
        vals = {r["fingerprint"]: r["v"] for r in self.conn.execute(case)}
        self.assertIsNone(vals["idox"])           # excluded from GBP totals
        self.assertIsNone(vals["nomk"])
        self.assertIsNotNone(vals["igp"])         # corrected row still valued
        self.assertIsNotNone(vals["pnd"])
        # All four rows still present (counted in volume).
        self.assertEqual(len(vals), 4)

    def test_dry_run_writes_nothing(self):
        bp.run(self.conn, confirm=False, window_days=7,
               log_path=Path(self.logpath))
        rows = {r["fingerprint"]: r for r in
                self.conn.execute("SELECT * FROM transactions")}
        self.assertIsNone(rows["igp"]["price_audit"])
        self.assertEqual(rows["igp"]["price"], 171.0)  # unchanged
        self.assertEqual(os.path.getsize(self.logpath), 0)

    def test_eval_filter_excludes_flagged(self):
        bp.run(self.conn, confirm=True, window_days=7,
               log_path=Path(self.logpath))
        kept = {r["fingerprint"] for r in self.conn.execute(
            f"SELECT fingerprint FROM transactions WHERE {EVAL_FILTER}")}
        # corrected + ok survive; unresolved + no_market excluded
        self.assertEqual(kept, {"igp", "pnd"})

    def test_idempotent_rerun(self):
        bp.run(self.conn, confirm=True, window_days=7,
               log_path=Path(self.logpath))
        s2 = bp.run(self.conn, confirm=True, window_days=7,
                    log_path=Path(self.logpath))
        # Second pass: corrected row now reads ok_pounds; flags stay.
        self.assertEqual(s2.get("ok_pounds"), 2)      # igp(now ok) + pnd
        self.assertEqual(s2.get("unresolved"), 1)
        self.assertEqual(s2.get("no_market"), 1)
        self.assertEqual(s2.get("corrected_pence", 0), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
