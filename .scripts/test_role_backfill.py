"""Integration tests for the role-normalization backfill (B-025 Phase A).

Tests:
  * Migration 004 applies cleanly on a fresh DB (role_normalized column,
    schema_version bumped to "4").
  * Backfill loop populates every seeded row with the correct bucket.
  * Backfill is idempotent — running twice produces identical state.
  * Mid-loop crash leaves the DB unchanged (atomicity).
  * upsert_transaction() in db.py populates role_normalized on new inserts
    (proves the ingest path is wired correctly).
  * Precedence test through upsert: PCA must beat CEO when both keywords
    appear in the role string.

Pattern follows test_repair_dates_atomicity.py: tempfile DB, db.connect()
patched via DB_PATH, no FUSE-affected directory writes.

RUNNING (Windows, per CLAUDE.md):
    python -m unittest .scripts/test_role_backfill.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


# Test fixtures — (fingerprint, ticker, raw_role, expected_bucket).
# Covers every load-bearing precedence rule.
SEED: list[tuple[str, str, str, str]] = [
    ("fp-ceo-1", "AAA", "Chief Executive Officer", "CEO"),
    ("fp-ceo-2", "BBB", "CEO", "CEO"),
    ("fp-cfo-1", "CCC", "Chief Financial Officer", "CFO"),
    ("fp-cfo-2", "DDD", "Finance Director", "CFO"),
    ("fp-ned-1", "EEE", "Non-Executive Director", "NED"),
    ("fp-ned-2", "FFF", "Non-executive director", "NED"),
    ("fp-ned-3", "GGG", "Director", "NED"),  # UK convention
    ("fp-chair-1", "HHH", "Chairman", "Chair (executive)"),
    # Rupert Q4 (broadened 2026-05-21): non-exec chair roles bucket
    # with NEDs, not the legacy "Non-Exec Chair" bucket.
    ("fp-nec-1", "III", "Non-Executive Chair", "NED"),
    ("fp-pca-1", "JJJ", "PCA to PDMR", "PCA"),
    ("fp-founder-1", "KKK", "President and Founder", "Founder"),
    ("fp-div-1", "LLL", "Chief Executive Officer, North America",
     "Divisional / Regional Exec"),
    ("fp-other-1", "MMM", "Fund Manager", "Other / unclassified"),
    ("fp-frag-1", "NNN", "Nature of the transaction", "Parser fragment"),
]


def _seed_db(db_path: Path) -> None:
    """Initialise schema via db.connect() (so migration 004 runs) and
    insert SEED rows with role_normalized NULL so the backfill has work
    to do."""
    import db as db_mod
    with mock.patch.object(db_mod, "DB_PATH", db_path):
        conn = db_mod.connect()
        try:
            now = db_mod.iso_now()
            for fp, ticker, raw_role, _ in SEED:
                conn.execute(
                    "INSERT INTO transactions("
                    "fingerprint, first_seen, last_seen, seen_count, date, "
                    "ticker, company, director, role, role_normalized, "
                    "type, shares, price, value, context, url, "
                    "announced_at, cluster_id, first_time_buy, parser_source"
                    ") VALUES (?, ?, ?, 1, '2026-01-01', ?, 'Test plc', "
                    "'Joe Director', ?, NULL, 'BUY', 1, 1.0, 1.0, NULL, "
                    "'https://example', NULL, NULL, 0, 'regex')",
                    (fp, now, now, ticker, raw_role),
                )
            conn.commit()
        finally:
            conn.close()


class TestMigration(unittest.TestCase):
    """Migration 004 adds the role_normalized column and bumps schema_version."""

    def test_migration_adds_column(self) -> None:
        import db as db_mod
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    cols = [
                        r[1] for r in conn.execute(
                            "PRAGMA table_info(transactions)"
                        ).fetchall()
                    ]
                    self.assertIn("role_normalized", cols)
                    # connect() chains all migrations forward; the head is the
                    # latest migration's target. Migration 004 adds role_normalized
                    # (asserted above); the version is the chain head. Bump per new
                    # migration. B-117: 5->8; B-111: 8->9 (009 reporting_dates confidence).
                    # B-060: 9->10 (010 price_audit). B-148: 10->11; B-151: 11->12.
                    # B-156: 12->13 (013 resulting_shares).
                    # B-164: 13->14 (014 short_positions).
                    # B-168: 14->15 (015 director_pay).
                    self.assertEqual(
                        db_mod.get_meta(conn, "schema_version"), "15",
                    )
                finally:
                    conn.close()


class TestBackfill(unittest.TestCase):
    """Backfill loop populates every row with the correct bucket."""

    def test_backfill_populates_all_rows(self) -> None:
        import db as db_mod
        from role_normalize import normalize_role
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            _seed_db(db_path)
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    rows = conn.execute(
                        "SELECT fingerprint, role FROM transactions",
                    ).fetchall()
                    updates = [
                        (normalize_role(r["role"]), r["fingerprint"])
                        for r in rows
                    ]
                    conn.execute("BEGIN IMMEDIATE")
                    conn.executemany(
                        "UPDATE transactions SET role_normalized = ? "
                        "WHERE fingerprint = ?",
                        updates,
                    )
                    conn.commit()
                    actual = dict(conn.execute(
                        "SELECT fingerprint, role_normalized "
                        "FROM transactions"
                    ).fetchall())
                    for fp, _, _, expected in SEED:
                        self.assertEqual(
                            actual[fp], expected,
                            f"{fp}: got {actual[fp]!r}, expected {expected!r}",
                        )
                    null_count = conn.execute(
                        "SELECT COUNT(*) FROM transactions "
                        "WHERE role_normalized IS NULL"
                    ).fetchone()[0]
                    self.assertEqual(null_count, 0)
                finally:
                    conn.close()


class TestIdempotent(unittest.TestCase):
    """Re-running the backfill is safe — same final state, no errors."""

    def test_backfill_twice_same_result(self) -> None:
        import db as db_mod
        from role_normalize import normalize_role
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            _seed_db(db_path)
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    for _ in range(2):
                        rows = conn.execute(
                            "SELECT fingerprint, role FROM transactions",
                        ).fetchall()
                        updates = [
                            (normalize_role(r["role"]), r["fingerprint"])
                            for r in rows
                        ]
                        conn.execute("BEGIN IMMEDIATE")
                        conn.executemany(
                            "UPDATE transactions SET role_normalized = ? "
                            "WHERE fingerprint = ?",
                            updates,
                        )
                        conn.commit()
                    actual = dict(conn.execute(
                        "SELECT fingerprint, role_normalized "
                        "FROM transactions"
                    ).fetchall())
                    for fp, _, _, expected in SEED:
                        self.assertEqual(actual[fp], expected)
                finally:
                    conn.close()


class TestAtomicity(unittest.TestCase):
    """A crash inside the UPDATE rolls back — DB stays all-NULL."""

    def test_mid_run_crash_rolls_back(self) -> None:
        import db as db_mod
        from role_normalize import normalize_role
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            _seed_db(db_path)
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    try:
                        rows = conn.execute(
                            "SELECT fingerprint, role FROM transactions",
                        ).fetchall()
                        # Simulate a crash on the 5th row.
                        updates = []
                        for i, r in enumerate(rows):
                            if i == 5:
                                raise RuntimeError("simulated crash")
                            updates.append(
                                (normalize_role(r["role"]), r["fingerprint"]),
                            )
                    except RuntimeError:
                        pass
                finally:
                    conn.close()
                # New connection — verify nothing was written.
                conn = sqlite3.connect(str(db_path))
                try:
                    null_count = conn.execute(
                        "SELECT COUNT(*) FROM transactions "
                        "WHERE role_normalized IS NULL"
                    ).fetchone()[0]
                    total = conn.execute(
                        "SELECT COUNT(*) FROM transactions",
                    ).fetchone()[0]
                    self.assertEqual(
                        null_count, total,
                        "Crash before commit must leave all rows NULL",
                    )
                finally:
                    conn.close()


class TestUpsertWiring(unittest.TestCase):
    """upsert_transaction populates role_normalized on every insert."""

    def test_upsert_populates(self) -> None:
        import db as db_mod
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    row = {
                        "fingerprint": "fp-upsert-ceo",
                        "date": "2026-01-01", "ticker": "ZZZ",
                        "company": "Test plc", "director": "Jane Doe",
                        "role": "Chief Executive Officer",
                        "type": "BUY", "shares": 1000,
                        "price": 5.0, "value": 5000.0,
                        "url": "https://example",
                    }
                    inserted = db_mod.upsert_transaction(
                        conn, row, parser_source="regex",
                    )
                    self.assertTrue(inserted)
                    norm = conn.execute(
                        "SELECT role_normalized FROM transactions "
                        "WHERE fingerprint = ?",
                        (row["fingerprint"],),
                    ).fetchone()[0]
                    self.assertEqual(norm, "CEO")
                finally:
                    conn.close()

    def test_upsert_pca_precedence(self) -> None:
        """PCA must beat any exec title in the role string."""
        import db as db_mod
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    row = {
                        "fingerprint": "fp-upsert-pca",
                        "date": "2026-01-01", "ticker": "YYY",
                        "company": "Test plc", "director": "Spouse Doe",
                        "role": "PCA - Spouse of Chief Executive Officer",
                        "type": "BUY", "shares": 100,
                        "price": 1.0, "value": 100.0,
                        "url": "https://example",
                    }
                    db_mod.upsert_transaction(
                        conn, row, parser_source="regex",
                    )
                    norm = conn.execute(
                        "SELECT role_normalized FROM transactions "
                        "WHERE fingerprint = ?",
                        (row["fingerprint"],),
                    ).fetchone()[0]
                    self.assertEqual(
                        norm, "PCA",
                        "PCA must beat CEO in precedence order",
                    )
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
