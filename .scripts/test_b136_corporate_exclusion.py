"""Tests for B-136 — scope scoring to individuals + related parties.

Policy (Rupert 2026-06-06): EXCLUDE arms-length corporate holders (funds, asset
managers, investment companies) from scoring, but KEEP corporate PCAs and family
trusts — they are reportable related-party dealings. The TYPE part of B-136
(SIP/GRANT/EXERCISE never fire) is already enforced by every signal module's
`if tx["type"] != "BUY": return None`.

Run:
    python -m unittest test_b136_corporate_exclusion -v
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from role_normalize import (  # noqa: E402
    is_corporate_actor, is_related_party, exclude_corporate_from_scoring)

TODAY = date(2026, 6, 6)


class TestIsCorporateActor(unittest.TestCase):
    """Bare name heuristic (corporate-looking name, custody carve-out)."""

    def test_individuals_not_flagged(self):
        for name in ["Toby Courtauld", "Jane Smith", "Gavin Rochussen",
                     "Nonkululeko Nyembezi", "Dan Nicholson", "Bob Jones",
                     "Anne-Marie Trustham", "Grant Thornton-Smith"]:
            self.assertFalse(is_corporate_actor(name), name)

    def test_corporates_flagged(self):
        for name in ["Yew Tree Overseas Limited", "Eminence Capital, LP",
                     "Sequoia Investment Management Company Limited",
                     "Acme Holdings Ltd", "BlackRock Fund", "Eni UK Limited"]:
            self.assertTrue(is_corporate_actor(name), name)

    def test_custody_individuals_not_flagged(self):
        for name in [
            "Mark Robson Shares held in accounts held with HL (Nominees) Limited",
            "John Morgan Held In Chase Nominees Limited A",
            "Kelly Gangotra Held With Pershing Nominees Limited"]:
            self.assertFalse(is_corporate_actor(name), name)


class TestRelatedPartyKept(unittest.TestCase):
    """Corporate PCAs + family trusts are KEPT; arms-length corporates excluded."""

    def test_corporate_pca_kept(self):
        # role_normalized == 'PCA'
        self.assertFalse(exclude_corporate_from_scoring(
            "Eminence Capital, LP", "PCA", "PCA of Ricky Chad Sandler"))
        # PCA in raw role only (role_normalized missing)
        self.assertFalse(exclude_corporate_from_scoring(
            "Adobelero Holdings Co. Limited", None, "PCA of Panos Benos"))
        # 'closely associated' wording
        self.assertFalse(exclude_corporate_from_scoring(
            "Long Path Partners", None, "Person closely associated with X"))

    def test_family_trust_kept(self):
        self.assertFalse(exclude_corporate_from_scoring(
            "The Michael Bell Grandchildren's Trust", "PCA", "PCA of Michael Bell"))
        # family-trust name backstop even with no role tag
        self.assertFalse(exclude_corporate_from_scoring(
            "Smith Family Trust", None, None))
        # trustee role keeps it
        self.assertFalse(exclude_corporate_from_scoring(
            "Butterfield Trust", "PCA", "In the capacity as the trustee"))

    def test_arms_length_corporate_excluded(self):
        self.assertTrue(exclude_corporate_from_scoring(
            "Sequoia Investment Management Company Limited",
            "Other / unclassified", "Investment Adviser"))
        self.assertTrue(exclude_corporate_from_scoring(
            "Eni UK Limited", "Other / unclassified", "Eni UK Limited"))
        self.assertTrue(exclude_corporate_from_scoring(
            "Discover Investment Company", None, None))

    def test_individual_never_excluded(self):
        self.assertFalse(exclude_corporate_from_scoring("Jane Smith", "CEO", "CEO"))

    def test_is_related_party_helper(self):
        self.assertTrue(is_related_party("PCA", None, None))
        self.assertTrue(is_related_party(None, "PCA of X", None))
        self.assertTrue(is_related_party(None, None, "Jones Family Trust"))
        self.assertFalse(is_related_party("CEO", "Chief Executive", "Acme Ltd"))


class TestUniverseExcludesArmsLengthCorporates(unittest.TestCase):
    def _setup(self):
        import eval_signals
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE transactions (fingerprint TEXT, ticker TEXT, "
                  "director TEXT, role TEXT, role_normalized TEXT, type TEXT, "
                  "announced_at TEXT, buy_strictness TEXT, price_audit TEXT)")
        c.execute("CREATE TABLE tickers_meta (ticker TEXT, benchmark_symbol "
                  "TEXT, is_excluded_issuer INTEGER)")
        c.execute("INSERT INTO tickers_meta VALUES ('AAA','^FTAS',0)")
        c.executemany(
            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)",
            [("fp1", "AAA", "Jane Smith", "CEO", "CEO", "BUY",
              "2026-05-01", "STRICT_BUY", None),
             ("fp2", "AAA", "Eminence Capital, LP", "PCA of Ricky Sandler",
              "PCA", "BUY", "2026-05-02", "STRICT_BUY", None),
             ("fp3", "AAA", "Eni UK Limited", "Eni UK Limited",
              "Other / unclassified", "BUY", "2026-05-03", "STRICT_BUY", None)])
        c.commit()
        return c, eval_signals

    def test_arms_length_excluded_pca_and_individual_kept(self):
        c, eval_signals = self._setup()
        names = {r["director"] for r in eval_signals._universe_rows(c, None, None)}
        self.assertIn("Jane Smith", names)               # individual
        self.assertIn("Eminence Capital, LP", names)     # corporate PCA -> kept
        self.assertNotIn("Eni UK Limited", names)        # arms-length -> excluded


class TestClustersExcludeArmsLength(unittest.TestCase):
    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE transactions (ticker TEXT, company TEXT, "
                  "director TEXT, date TEXT, type TEXT, value REAL, "
                  "cluster_id TEXT, price_audit TEXT, role_normalized TEXT, "
                  "role TEXT)")
        c.execute("CREATE TABLE tickers_meta (ticker TEXT, is_excluded_issuer INTEGER)")
        return c

    def _insert(self, c, actors):
        d = (TODAY - timedelta(days=10)).isoformat()
        c.executemany(
            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
            [("AAA", "A plc", name, d, "BUY", 60000, "AAA-1", None, rn, role)
             for (name, rn, role) in actors])
        c.commit()

    def test_arms_length_corporate_not_counted(self):
        import export_dashboard_json as ex
        c = self._conn()
        # individual + arms-length corporate -> corporate dropped -> 1 distinct
        # director -> below the 2-director cluster threshold.
        self._insert(c, [("Jane Smith", "CEO", "CEO"),
                         ("Eni UK Limited", "Other / unclassified", "Eni UK Limited")])
        self.assertEqual(ex.compute_active_clusters(c, TODAY), [])

    def test_corporate_pca_counts_as_director(self):
        import export_dashboard_json as ex
        c = self._conn()
        # individual + corporate PCA -> both kept -> 2 directors -> cluster forms.
        self._insert(c, [("Jane Smith", "CEO", "CEO"),
                         ("Eminence Capital, LP", "PCA", "PCA of Ricky Sandler")])
        self.assertEqual(len(ex.compute_active_clusters(c, TODAY)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
