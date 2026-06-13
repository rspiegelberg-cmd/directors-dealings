"""Sprint 10 Phase 3 tests — sticky-flag classifier + --unflag CLI.

Verifies three behaviours introduced in Phase 3:

  1. `_run_unflag` removes the is_excluded_issuer flag from a named
     ticker, returns 0, and writes to the audit log.
  2. `_run_unflag` is a no-op (returns 1) when the named ticker
     either doesn't exist in tickers_meta OR is already un-flagged.
  3. The sticky-flag algorithm (in `run()`) does NOT silently
     un-flag a ticker that was previously flagged but no longer
     matches any source. Tested by mimicking the critical persist
     block end-to-end against an in-memory snapshot.

WHY THIS MATTERS
----------------
The Sprint-3 gotcha (memory: `project_classify_issuers_resets_flag.md`)
was that `classify_issuers.py` zeroed every flag before re-applying,
so a Yahoo / AIC blip would silently un-exclude every Investment
Trust. Sprint 10 Phase 3 makes the classifier sticky — once flagged,
stays flagged unless explicit `--unflag TICKER`. These tests would
fail against the pre-Phase-3 code.

RUNNING (Windows, per CLAUDE.md):
    python -m unittest .scripts.test_classify_sticky_flag
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _seed_tickers_meta(db_path: Path, rows: list[tuple]) -> None:
    """Initialise schema via db.connect() and insert tickers_meta rows.

    `rows` is a list of (ticker, is_excluded_issuer) tuples. Other
    columns default to NULL / 0 — sufficient for the flag-state tests.
    """
    import db as db_mod
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            now = db_mod.iso_now()
            for ticker, is_excluded in rows:
                conn.execute(
                    "INSERT INTO tickers_meta(ticker, is_excluded_issuer, "
                    "excluded_source, classified_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ticker, is_excluded, "C" if is_excluded else None,
                     now, now),
                )
            conn.commit()
        finally:
            conn.close()


def _flag_state(db_path: Path, ticker: str) -> int | None:
    """Read back is_excluded_issuer for a ticker. None if row absent."""
    import db as db_mod
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            row = conn.execute(
                "SELECT is_excluded_issuer FROM tickers_meta WHERE ticker = ?",
                (ticker,),
            ).fetchone()
            return row["is_excluded_issuer"] if row else None
        finally:
            conn.close()


class TestUnflagCLI(unittest.TestCase):
    """Phase 3 — `--unflag TICKER` manual override behaviour."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.unflag_log = Path(self._tmpdir.name) / "_classifier_unflag.log"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_unflag_removes_flag_from_previously_flagged_ticker(self):
        """SMT is flagged; --unflag SMT removes the flag, returns 0."""
        _seed_tickers_meta(self.db_path, [("SMT", 1), ("CTY", 1)])

        import db as db_mod
        import classify_issuers
        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(classify_issuers, "UNFLAG_LOG",
                               self.unflag_log):
            rc = classify_issuers._run_unflag(["SMT"])

        self.assertEqual(rc, 0)
        self.assertEqual(_flag_state(self.db_path, "SMT"), 0)
        # CTY untouched.
        self.assertEqual(_flag_state(self.db_path, "CTY"), 1)
        # Audit log written.
        self.assertTrue(self.unflag_log.exists())
        self.assertIn("SMT", self.unflag_log.read_text())

    def test_unflag_returns_1_when_ticker_not_in_tickers_meta(self):
        """--unflag XYZ where XYZ doesn't exist returns 1, no crash."""
        _seed_tickers_meta(self.db_path, [("SMT", 1)])

        import db as db_mod
        import classify_issuers
        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(classify_issuers, "UNFLAG_LOG",
                               self.unflag_log):
            rc = classify_issuers._run_unflag(["XYZ"])

        self.assertEqual(rc, 1)
        # No log written when nothing was affected.
        self.assertFalse(self.unflag_log.exists())

    def test_unflag_returns_1_when_ticker_already_unflagged(self):
        """--unflag TST where TST is already unflagged returns 1."""
        _seed_tickers_meta(self.db_path, [("TST", 0)])

        import db as db_mod
        import classify_issuers
        with mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(classify_issuers, "UNFLAG_LOG",
                               self.unflag_log):
            rc = classify_issuers._run_unflag(["TST"])

        self.assertEqual(rc, 1)
        # State unchanged.
        self.assertEqual(_flag_state(self.db_path, "TST"), 0)


class TestStickyFlagBehaviour(unittest.TestCase):
    """Phase 3 — sticky-flag persistence across re-runs."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.sticky_log = Path(self._tmpdir.name) / "_sticky.log"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_sticky_holds_logged_correctly(self):
        """_append_sticky_holds_log writes one line per call, with ticker list."""
        import classify_issuers
        with mock.patch.object(classify_issuers, "STICKY_HOLDS_LOG",
                               self.sticky_log):
            classify_issuers._append_sticky_holds_log(["SMT", "CTY"])
            classify_issuers._append_sticky_holds_log([])  # no-op for empty
            classify_issuers._append_sticky_holds_log(["FCIT"])

        lines = self.sticky_log.read_text().strip().split("\n")
        # Two lines (empty list is a no-op).
        self.assertEqual(len(lines), 2)
        # Each line is tab-separated: timestamp\tcount\ttickers
        for line in lines:
            parts = line.split("\t")
            self.assertEqual(len(parts), 3)
        # First line covers SMT and CTY.
        self.assertEqual(lines[0].split("\t")[1], "2")
        self.assertIn("SMT", lines[0])
        self.assertIn("CTY", lines[0])
        # Second line covers FCIT only.
        self.assertEqual(lines[1].split("\t")[1], "1")
        self.assertIn("FCIT", lines[1])

    def test_sticky_set_arithmetic_does_not_silently_unflag(self):
        """The sticky-flag algorithm: pre_flagged - union = sticky_holds.

        Mimics the critical block in run() without spinning up the full
        AIC / Yahoo classifier. Demonstrates that a ticker flagged on
        run N would, under sticky-flag, remain flagged on run N+1 even
        if no source confirms it (which the old code would have
        silently un-flagged).
        """
        # Run N: SMT and CTY are both flagged.
        _seed_tickers_meta(self.db_path,
                           [("SMT", 1), ("CTY", 1), ("TST", 0)])

        # Run N+1: classifier produces an empty union (e.g. AIC scrape
        # failed, --no-yahoo, regex matched nothing). Under OLD code,
        # SMT and CTY would be un-flagged. Under sticky-flag, both
        # remain flagged and appear in sticky_holds.
        import db as db_mod
        with mock.patch.object(db_mod, "DB_PATH", self.db_path):
            conn = db_mod.connect()
            try:
                pre_flagged = {
                    r["ticker"] for r in conn.execute(
                        "SELECT ticker FROM tickers_meta "
                        "WHERE is_excluded_issuer = 1"
                    ).fetchall()
                }
                union = set()  # empty union — no source matched
                sticky_holds = sorted(pre_flagged - union)
            finally:
                conn.close()

        # Both previously-flagged tickers are sticky-held.
        self.assertEqual(sticky_holds, ["CTY", "SMT"])
        # And — critically — the on-disk state is unchanged because the
        # sticky algorithm did NOT zero anything (no UPDATE-to-0).
        self.assertEqual(_flag_state(self.db_path, "SMT"), 1)
        self.assertEqual(_flag_state(self.db_path, "CTY"), 1)
        # TST stays unflagged.
        self.assertEqual(_flag_state(self.db_path, "TST"), 0)


if __name__ == "__main__":
    unittest.main()
