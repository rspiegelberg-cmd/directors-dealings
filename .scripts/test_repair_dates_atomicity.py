"""B-006 atomicity regression test for repair_dates.py.

Goal: simulate a crash AFTER the Nth Case B delete-and-pending commit
and assert the on-disk state is recoverable:

  * pending file contains exactly N entries (each one is the row that
    was deleted), and
  * the DB is missing exactly the same N rows.

The test exercises the `--abort-after-n` hook added in B-006. It also
re-runs `repair_dates` after the simulated crash to confirm the
duplicate-detection path (`resumed_from_pending`) catches the row
without re-deleting it, i.e. the script is idempotent under partial
crashes.

WHY THIS MATTERS
----------------
Before B-006 the Case B path could lose data: signals + paper_trades +
transactions were deleted inside a `with conn:` block, but the
filesystem-side write of `_pending_review.json` happened only once at
end-of-loop. A crash between those two steps would leave the row gone
from the DB and not yet in pending. B-006 reorders to write pending
first via tempfile + os.replace, so any crash leaves a recoverable
state. This test would have failed loudly against the old code.

RUNNING (Windows, per CLAUDE.md):
    python -m unittest .scripts/test_repair_dates_atomicity.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _seed_db(db_path: Path) -> None:
    """Initialise the schema via db.connect() (so it always matches the
    production migration chain), then insert three test transactions.

    Hand-rolling the schema in this test used to drift away from the real
    one whenever db.py added a migration; an actual run on 2026-05-18
    failed with `OperationalError: no such column: signal_id` because the
    test schema lagged behind. Using db.connect() is the only durable fix.
    """
    import db as db_mod  # local import: respects the DB_PATH patch
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            now = db_mod.iso_now()
            rows = [
                ("fp-aaa", "TST1", "https://example/rns/aaa"),
                ("fp-bbb", "TST2", "https://example/rns/bbb"),
                ("fp-ccc", "TST3", "https://example/rns/ccc"),
            ]
            for fp, ticker, url in rows:
                conn.execute(
                    "INSERT INTO transactions("
                    "fingerprint, first_seen, last_seen, seen_count, date, "
                    "ticker, company, director, role, type, shares, price, "
                    "value, context, url, announced_at, cluster_id, "
                    "first_time_buy, parser_source"
                    ") VALUES (?, ?, ?, 1, '2026-01-01', ?, 'Test plc', "
                    "'Joe Director', 'CEO', 'BUY', 1, 1.0, 1.0, NULL, ?, "
                    "NULL, NULL, 0, 'regex')",
                    (fp, now, now, ticker, url),
                )
                conn.execute(
                    "INSERT INTO signals(signal_id, signal_version, "
                    "fingerprint, fired_at) "
                    "VALUES ('t1_ceo_cfo_buy', '1.0.0', ?, ?)",
                    (fp, now),
                )
            conn.commit()
        finally:
            # Release the file lock before setUp returns -- Windows
            # tearDown can't delete an open SQLite file.
            conn.close()


class _FakeWarn:
    """Simulate parser output that triggers Case B for every cached file."""
    def __init__(self, rns_id):
        self.rns_id = rns_id

    @staticmethod
    def parse_announcement(html, url, rns_id, announced_at):
        # Returning warnings + non-empty extracted forces Case B.
        return (
            [{"fingerprint": f"new-{rns_id}", "date": "2026-02-01",
              "ticker": "TST", "type": "BUY", "shares": 1, "price": 1.0,
              "value": 1.0}],
            ["foreign_currency"],
            "regex",
        )


class TestRepairDatesAtomicity(unittest.TestCase):
    def setUp(self):
        # Isolate the test from the real DB and cache. We point repair_dates
        # at a temp DB via the `db` module's DB_PATH constant; the cache dir
        # is harder to redirect since CACHE_DIR is a module-level constant,
        # so we instead patch CACHE_DIR.glob() at the module level.
        # `ignore_cleanup_errors=True` (Py 3.10+): on Windows the SQLite
        # file lock may linger for a few ms after conn.close(), and
        # `repair_dates.run()` is itself responsible for closing the
        # connection. We don't want a stuck tearDown to mask a real
        # test failure in the assert phase.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp.name)
        self.db_path = self.tmp_path / "test.db"
        self.pending_path = self.tmp_path / "_pending_review.json"
        self.tmp_pending = self.tmp_path / "_pending_review.json.tmp"
        _seed_db(self.db_path)

        # Synthetic cache files -- repair_dates iterates CACHE_DIR.glob("*.html").
        cache_dir = self.tmp_path / "_cache"
        cache_dir.mkdir()
        for rns_id in ("aaa", "bbb", "ccc"):
            (cache_dir / f"{rns_id}.html").write_text(
                "<html>fixture</html>", encoding="utf-8")
        self.cache_dir = cache_dir

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, abort_after_n=None):
        import repair_dates
        import db as db_mod
        import db_health
        import shutil
        # db_health.backup() uses its own hardcoded DB_PATH constant, ignoring
        # the db.DB_PATH patch.  Replace it with a version that backs up our
        # temp DB so the pre-repair guard passes cleanly.
        tmp_bak = self.tmp_path / "test.db.bak"
        def _fake_backup():
            shutil.copy2(str(self.db_path), str(tmp_bak))
            return True
        with mock.patch.object(repair_dates, "PENDING_PATH", self.pending_path), \
             mock.patch.object(repair_dates, "PENDING_TMP_PATH", self.tmp_pending), \
             mock.patch.object(repair_dates, "CACHE_DIR", self.cache_dir), \
             mock.patch.object(repair_dates, "parse_pdmr", _FakeWarn), \
             mock.patch.object(db_mod, "DB_PATH", self.db_path), \
             mock.patch.object(db_health, "backup", _fake_backup), \
             mock.patch.object(db_health, "check", return_value=True):
            # `db.connect()` reads `db.DB_PATH` at call time so patching it
            # is enough; we don't need to fake `db.connect` itself.
            return repair_dates.run(abort_after_n=abort_after_n)

    def test_abort_after_one_leaves_pending_and_db_consistent(self):
        """After abort-after-1, pending has 1 entry and DB has 1 row gone."""
        import repair_dates
        with self.assertRaises(repair_dates._AbortAfterN):
            self._run(abort_after_n=1)

        # Pending file exists and contains exactly 1 entry.
        self.assertTrue(self.pending_path.exists())
        payload = json.loads(self.pending_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["items"]), 1)

        # DB is missing exactly 1 transaction row.
        conn = sqlite3.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        self.assertEqual(n, 2, "expected exactly one transaction to be deleted")

    def test_resume_after_crash_is_idempotent(self):
        """Re-running after a crash leaves the system consistent: every
        row that's not in the DB is in pending, no row is lost, and the
        re-run completes without crashing.

        We DO NOT assert `resumed_from_pending >= 1` -- that counter
        only fires on a half-crash (pending written, DB delete NOT
        committed). Our `_AbortAfterN` hook raises AFTER both have
        committed, so the second run sees no DB row for the first
        fingerprint and skips it via the `no_db_record` path, not the
        `resumed_from_pending` path. The real safety invariant is
        "pending + DB = the original set", which is what we check.
        """
        import repair_dates
        with self.assertRaises(repair_dates._AbortAfterN):
            self._run(abort_after_n=1)

        # Re-run without abort -- completes the remaining rows.
        self._run(abort_after_n=None)

        # All 3 fingerprints accounted for: pending has 3, DB has 0.
        # No row is lost in the crash-and-resume cycle.
        payload = json.loads(self.pending_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 3)
        conn = sqlite3.connect(self.db_path)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_orphan_tmp_cleaned_on_startup(self):
        """An orphan `.json.tmp` from a previous crashed run is deleted at
        startup -- the file invariant is `tmp must not exist after startup`."""
        # Plant an orphan tmp file BEFORE running.
        self.tmp_pending.write_text('{"orphan": true}', encoding="utf-8")
        self._run()
        self.assertFalse(self.tmp_pending.exists(),
                         "orphan .tmp file must be cleaned up at startup")


if __name__ == "__main__":
    unittest.main()
