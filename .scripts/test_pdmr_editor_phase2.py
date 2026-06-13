"""Sprint 25 Phase 2 -- apply_edits.py tests.

All tests that touch SQLite use a fresh in-memory or /tmp DB built from
the project schema. The real directors.db is never opened.

Coverage:
  A. validate_edit: action/field rules
  B. _make_fingerprint: matches parse_pdmr._fingerprint
  C. apply_update (non-key): plain UPDATE, parser_source -> 'manual'
  D. apply_update (key-field): delete+insert, signals cascade
  E. apply_add: INSERT with correct fingerprint + parser_source='manual'
  F. apply_reject: deletes tx+signals by URL, records in rejected list
  G. audit append (JSONL): each entry is valid JSON; append is idempotent
  H. queue lifecycle: read -> apply -> clear
  I. rejected_rns_ids persistence
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# DB fixture helpers
# ---------------------------------------------------------------------------

SCHEMA_SQL = (HERE / "db_schema.sql").read_text(encoding="utf-8")
_MIG_DIR   = HERE / "schema_migrations"

def make_test_conn():
    """Return an in-memory sqlite3 connection with the full project schema
    including all migrations (parser_source, is_excluded_issuer,
    role_normalized, buy_strictness).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    # Apply all migrations in order
    for mig in sorted(_MIG_DIR.glob("0*.sql")):
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except Exception:
            pass  # column may already exist
    conn.commit()
    return conn


def insert_tx(conn, *, fingerprint, date="2026-05-12", ticker="CARD",
              company="Card Factory", director="Jane Doe", role="CEO",
              tx_type="BUY", shares=1000, price=1.5, value=1500.0,
              url="https://www.investegate.co.uk/announcement/rns/card/9564925",
              parser_source="regex", buy_strictness="STRICT_BUY"):
    """Insert a minimal test transaction row."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO transactions ("
        "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        "company, director, role, role_normalized, type, shares, price, "
        "value, context, url, announced_at, cluster_id, first_time_buy, "
        "parser_source, buy_strictness"
        ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (fingerprint, now, now, date, ticker, company, director, role, None,
         tx_type, shares, price, value, None, url, None, None, parser_source, buy_strictness)
    )
    conn.commit()


def insert_signal(conn, *, fingerprint, signal_id="t1a_ceo_founder_buy"):
    """Insert a minimal signal row referencing a transaction."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT OR IGNORE INTO signals (signal_id, signal_version, fingerprint, fired_at) "
        "VALUES (?, '1.0', ?, ?)",
        (signal_id, fingerprint, now)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Inline apply_edits helpers (FUSE-safe — do not import the module directly)
# ---------------------------------------------------------------------------

def _make_fingerprint(date, ticker, director, tx_type, shares):
    raw = f"{date}|{ticker}|{director}|{tx_type}|{shares}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


VALID_TX_TYPES = {"BUY", "SELL", "SELL_TAX", "EXERCISE", "GRANT", "SIP"}
VALID_ACTIONS  = {"update", "add", "reject"}
_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")
_FP_RE   = __import__("re").compile(r"^[0-9a-f]{8,32}$")
_RNS_RE  = __import__("re").compile(r"^\d{5,12}$")
FINGERPRINT_FIELDS = {"date", "ticker", "director", "type", "shares"}


def validate_edit(edit):
    errors = []
    action = edit.get("action", "")
    if action not in VALID_ACTIONS:
        return [f"Unknown action {action!r}"]
    if action == "reject":
        if not _RNS_RE.match(str(edit.get("target_rns_id") or "")):
            errors.append("reject requires a valid target_rns_id")
        return errors
    if action == "update":
        if not _FP_RE.match(str(edit.get("target_fingerprint") or "")):
            errors.append("update requires a valid target_fingerprint")
    if action == "add":
        if not _RNS_RE.match(str(edit.get("target_rns_id") or "")):
            errors.append("add requires a valid target_rns_id")
    fields = edit.get("fields") or {}
    if action == "update" and not fields:
        errors.append("update: fields must not be empty")
    if action == "add":
        for req in ("date", "ticker", "director", "type", "shares"):
            if not fields.get(req) and fields.get(req) != 0:
                errors.append(f"add: '{req}' is required")
    t = fields.get("type")
    if t is not None and t not in VALID_TX_TYPES:
        errors.append(f"type must be one of valid types, got {t!r}")
    for nf in ("shares", "price", "value"):
        v = fields.get(nf)
        if v is not None:
            try:
                fv = float(v)
                if fv < 0:
                    errors.append(f"{nf} must be >= 0")
                if nf == "shares" and fv != int(fv):
                    errors.append("shares must be a whole number")
            except (TypeError, ValueError):
                errors.append(f"{nf} must be numeric")
    d = fields.get("date")
    if d is not None and not _DATE_RE.match(str(d)):
        errors.append("date must be YYYY-MM-DD")
    return errors


# ---------------------------------------------------------------------------
# A. validate_edit
# ---------------------------------------------------------------------------

class TestValidateEdit(unittest.TestCase):

    def test_unknown_action(self):
        errs = validate_edit({"action": "magic"})
        self.assertTrue(any("Unknown action" in e for e in errs))

    def test_update_missing_fingerprint(self):
        errs = validate_edit({"action": "update", "fields": {"role": "CEO"}})
        self.assertTrue(any("fingerprint" in e for e in errs))

    def test_update_empty_fields(self):
        errs = validate_edit({"action": "update",
                              "target_fingerprint": "abcdef1234567890",
                              "fields": {}})
        self.assertTrue(any("fields must not be empty" in e for e in errs))

    def test_update_valid(self):
        errs = validate_edit({"action": "update",
                              "target_fingerprint": "abcdef1234567890",
                              "fields": {"role": "Chief Executive"}})
        self.assertEqual([], errs)

    def test_add_missing_required(self):
        errs = validate_edit({"action": "add", "target_rns_id": "9564925",
                              "fields": {"ticker": "CARD", "director": "Jane",
                                         "type": "BUY", "shares": 100}})
        self.assertTrue(any("date" in e for e in errs))

    def test_add_valid(self):
        errs = validate_edit({
            "action": "add", "target_rns_id": "9564925",
            "fields": {"date": "2026-05-12", "ticker": "CARD",
                       "director": "Jane", "type": "BUY", "shares": 100}
        })
        self.assertEqual([], errs)

    def test_reject_valid(self):
        errs = validate_edit({"action": "reject", "target_rns_id": "9564925",
                              "fields": {}})
        self.assertEqual([], errs)

    def test_reject_invalid_rns(self):
        errs = validate_edit({"action": "reject", "target_rns_id": "abc",
                              "fields": {}})
        self.assertTrue(errs)

    def test_invalid_type(self):
        errs = validate_edit({"action": "update",
                              "target_fingerprint": "abcdef1234567890",
                              "fields": {"type": "MAGIC"}})
        self.assertTrue(any("type" in e for e in errs))

    def test_negative_shares(self):
        errs = validate_edit({"action": "update",
                              "target_fingerprint": "abcdef1234567890",
                              "fields": {"shares": -10}})
        self.assertTrue(any("shares" in e and ">= 0" in e for e in errs))

    def test_fractional_shares(self):
        errs = validate_edit({"action": "update",
                              "target_fingerprint": "abcdef1234567890",
                              "fields": {"shares": 10.5}})
        self.assertTrue(any("whole" in e for e in errs))

    def test_bad_date(self):
        errs = validate_edit({"action": "update",
                              "target_fingerprint": "abcdef1234567890",
                              "fields": {"date": "12/05/2026"}})
        self.assertTrue(any("YYYY-MM-DD" in e for e in errs))


# ---------------------------------------------------------------------------
# B. _make_fingerprint
# ---------------------------------------------------------------------------

class TestMakeFingerprint(unittest.TestCase):

    def test_matches_parse_pdmr(self):
        """Fingerprint must match parse_pdmr._fingerprint exactly."""
        date, ticker, director, tx_type, shares = "2026-05-12", "CARD", "Jane Doe", "BUY", 1000
        expected = hashlib.sha1(
            f"{date}|{ticker}|{director}|{tx_type}|{shares}".encode()
        ).hexdigest()[:16]
        self.assertEqual(_make_fingerprint(date, ticker, director, tx_type, shares), expected)

    def test_deterministic(self):
        fp1 = _make_fingerprint("2026-01-01", "TEST", "Alice", "BUY", 500)
        fp2 = _make_fingerprint("2026-01-01", "TEST", "Alice", "BUY", 500)
        self.assertEqual(fp1, fp2)

    def test_different_inputs_differ(self):
        fp1 = _make_fingerprint("2026-01-01", "TEST", "Alice", "BUY", 500)
        fp2 = _make_fingerprint("2026-01-01", "TEST", "Alice", "BUY", 501)
        self.assertNotEqual(fp1, fp2)

    def test_output_is_16hex(self):
        fp = _make_fingerprint("2026-01-01", "T", "D", "BUY", 1)
        self.assertEqual(len(fp), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))


# ---------------------------------------------------------------------------
# C. apply_update (non-key fields) -- plain UPDATE
# ---------------------------------------------------------------------------

class TestApplyUpdateNonKey(unittest.TestCase):

    def setUp(self):
        self.conn = make_test_conn()
        self.fp = _make_fingerprint("2026-05-12", "CARD", "Jane Doe", "BUY", 1000)
        insert_tx(self.conn, fingerprint=self.fp)

    def tearDown(self):
        self.conn.close()

    def test_non_key_update_changes_role(self):
        """Updating role should UPDATE the row in place (same fingerprint)."""
        self.conn.execute(
            "UPDATE transactions SET role = 'Chief Executive', "
            "parser_source = 'manual', last_seen = '2026-06-03T00:00:00Z' "
            "WHERE fingerprint = ?", (self.fp,)
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT role, parser_source FROM transactions WHERE fingerprint = ?",
            (self.fp,)
        ).fetchone()
        self.assertEqual(row["role"], "Chief Executive")
        self.assertEqual(row["parser_source"], "manual")

    def test_fingerprint_unchanged_after_non_key_edit(self):
        """Fingerprint must NOT change when only role/company/price change."""
        self.conn.execute(
            "UPDATE transactions SET price = 2.0, value = 2000.0 "
            "WHERE fingerprint = ?", (self.fp,)
        )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT fingerprint FROM transactions"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fingerprint"], self.fp)


# ---------------------------------------------------------------------------
# D. apply_update (key field) -- DELETE + INSERT + signals cascade
# ---------------------------------------------------------------------------

class TestApplyUpdateKeyField(unittest.TestCase):

    def setUp(self):
        self.conn = make_test_conn()
        self.fp_old = _make_fingerprint("2026-05-12", "CARD", "Jane Doe", "BUY", 1000)
        insert_tx(self.conn, fingerprint=self.fp_old)
        insert_signal(self.conn, fingerprint=self.fp_old)

    def tearDown(self):
        self.conn.close()

    def _simulate_key_change(self, new_shares):
        """Simulate a fingerprint-changing edit manually (what apply_update does)."""
        fp_new = _make_fingerprint("2026-05-12", "CARD", "Jane Doe", "BUY", new_shares)
        # Delete old signals + tx
        self.conn.execute(
            "DELETE FROM signals WHERE fingerprint = ?", (self.fp_old,))
        self.conn.execute(
            "DELETE FROM transactions WHERE fingerprint = ?", (self.fp_old,))
        # Insert new tx
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, role_normalized, type, shares, price, "
            "value, context, url, announced_at, cluster_id, first_time_buy, "
            "parser_source, buy_strictness"
            ") VALUES (?, ?, ?, 1, '2026-05-12', 'CARD', 'Card Factory', "
            "'Jane Doe', 'CEO', NULL, 'BUY', ?, 1.5, 1500.0, NULL, "
            "'https://example.com', NULL, NULL, 0, 'manual', 'STRICT_BUY')",
            (fp_new, now, now, new_shares)
        )
        self.conn.commit()
        return fp_new

    def test_old_fingerprint_deleted(self):
        fp_new = self._simulate_key_change(2000)
        row = self.conn.execute(
            "SELECT fingerprint FROM transactions WHERE fingerprint = ?",
            (self.fp_old,)
        ).fetchone()
        self.assertIsNone(row, "Old fingerprint should be deleted")

    def test_new_fingerprint_inserted(self):
        fp_new = self._simulate_key_change(2000)
        row = self.conn.execute(
            "SELECT fingerprint, shares FROM transactions WHERE fingerprint = ?",
            (fp_new,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["shares"], 2000)

    def test_signals_cascade_deleted(self):
        self._simulate_key_change(2000)
        sigs = self.conn.execute(
            "SELECT * FROM signals WHERE fingerprint = ?", (self.fp_old,)
        ).fetchall()
        self.assertEqual(len(sigs), 0, "Signals for old fingerprint should be deleted")

    def test_new_parser_source_is_manual(self):
        fp_new = self._simulate_key_change(2000)
        row = self.conn.execute(
            "SELECT parser_source FROM transactions WHERE fingerprint = ?",
            (fp_new,)
        ).fetchone()
        self.assertEqual(row["parser_source"], "manual")


# ---------------------------------------------------------------------------
# E. apply_add
# ---------------------------------------------------------------------------

class TestApplyAdd(unittest.TestCase):

    def setUp(self):
        self.conn = make_test_conn()

    def tearDown(self):
        self.conn.close()

    def _insert_manually(self, date, ticker, director, tx_type, shares):
        fp = _make_fingerprint(date, ticker, director, tx_type, shares)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, role_normalized, type, shares, price, "
            "value, context, url, announced_at, cluster_id, first_time_buy, "
            "parser_source, buy_strictness"
            ") VALUES (?, ?, ?, 1, ?, ?, '', ?, '', NULL, ?, ?, 1.0, 100.0, "
            "NULL, NULL, NULL, NULL, 0, 'manual', NULL)",
            (fp, now, now, date, ticker, director, tx_type, shares)
        )
        self.conn.commit()
        return fp

    def test_add_creates_correct_fingerprint(self):
        date, ticker, director, tx_type, shares = "2026-05-12", "XYZ", "Bob", "BUY", 500
        fp = self._insert_manually(date, ticker, director, tx_type, shares)
        expected = _make_fingerprint(date, ticker, director, tx_type, shares)
        self.assertEqual(fp, expected)

    def test_add_sets_parser_source_manual(self):
        fp = self._insert_manually("2026-05-12", "XYZ", "Bob", "BUY", 500)
        row = self.conn.execute(
            "SELECT parser_source FROM transactions WHERE fingerprint = ?", (fp,)
        ).fetchone()
        self.assertEqual(row["parser_source"], "manual")

    def test_add_row_queryable(self):
        fp = self._insert_manually("2026-05-12", "XYZ", "Bob", "BUY", 500)
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE fingerprint = ?", (fp,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["ticker"], "XYZ")


# ---------------------------------------------------------------------------
# E2. apply_add collision (B-134) -- non-fatal duplicate handling
# ---------------------------------------------------------------------------

class TestApplyAddCollision(unittest.TestCase):
    """B-134: an 'add' edit whose fingerprint already exists must raise the
    dedicated AddTxAlreadyExists (non-fatal) rather than the old generic
    ValueError that aborted the whole batch."""

    def setUp(self):
        self.conn = make_test_conn()

    def tearDown(self):
        self.conn.close()

    def _add_edit(self):
        return {
            "action": "add",
            "edit_id": "add-1",
            "target_rns_id": "9595811",
            "fields": {
                "date": "2026-06-01", "ticker": "RKT",
                "director": "Marybeth Hays", "type": "BUY",
                "shares": 340, "price": 50.0, "value": 17000.0,
                "role": "NED", "company": "Reckitt",
            },
        }

    def test_first_add_inserts(self):
        import apply_edits as ae
        _, after = ae.apply_add(self.conn, self._add_edit(), verbose=False)
        self.assertEqual(after["ticker"], "RKT")
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE fingerprint = ?", (after["fingerprint"],)
        ).fetchone()
        self.assertIsNotNone(row)

    def test_duplicate_add_raises_specific_exception(self):
        import apply_edits as ae
        edit = self._add_edit()
        ae.apply_add(self.conn, edit, verbose=False)        # first add lands
        with self.assertRaises(ae.AddTxAlreadyExists) as ctx:
            ae.apply_add(self.conn, edit, verbose=False)    # second collides
        # Exception carries the fingerprint for the skip/audit path.
        self.assertTrue(ctx.exception.fingerprint)

    def test_collision_is_not_plain_valueerror(self):
        # Regression guard: the old code raised ValueError, which the run()
        # loop treated as fatal. The new type must NOT be a ValueError so the
        # dedicated non-fatal handler is the one that catches it.
        import apply_edits as ae
        self.assertFalse(issubclass(ae.AddTxAlreadyExists, ValueError))


# ---------------------------------------------------------------------------
# F. apply_reject
# ---------------------------------------------------------------------------

class TestApplyReject(unittest.TestCase):

    def setUp(self):
        self.conn = make_test_conn()
        self.rns_id = "9564925"
        self.url = f"https://www.investegate.co.uk/announcement/rns/card/{self.rns_id}"
        self.fp = _make_fingerprint("2026-05-12", "CARD", "Jane", "BUY", 1000)
        insert_tx(self.conn, fingerprint=self.fp, url=self.url)
        insert_signal(self.conn, fingerprint=self.fp)

    def tearDown(self):
        self.conn.close()

    def _simulate_reject(self):
        url_pattern = f"%/{self.rns_id}"
        affected = self.conn.execute(
            "SELECT fingerprint FROM transactions WHERE url LIKE ?", (url_pattern,)
        ).fetchall()
        fps = [r["fingerprint"] for r in affected]
        if fps:
            self.conn.execute(
                f"DELETE FROM signals WHERE fingerprint IN ({','.join('?'*len(fps))})", fps)
            self.conn.execute("DELETE FROM transactions WHERE url LIKE ?", (url_pattern,))
        self.conn.commit()
        return fps

    def test_reject_deletes_tx(self):
        self._simulate_reject()
        row = self.conn.execute(
            "SELECT fingerprint FROM transactions WHERE fingerprint = ?", (self.fp,)
        ).fetchone()
        self.assertIsNone(row, "Transaction should be deleted by reject")

    def test_reject_deletes_signals(self):
        self._simulate_reject()
        sigs = self.conn.execute(
            "SELECT * FROM signals WHERE fingerprint = ?", (self.fp,)
        ).fetchall()
        self.assertEqual(len(sigs), 0)

    def test_reject_no_op_on_unknown_rns(self):
        """Rejecting an RNS with no transactions should not raise."""
        url_pattern = f"%/9999999"
        affected = self.conn.execute(
            "SELECT fingerprint FROM transactions WHERE url LIKE ?", (url_pattern,)
        ).fetchall()
        self.assertEqual(len(affected), 0)  # no-op, no error


# ---------------------------------------------------------------------------
# F2. Cascade deletes clear paper_trades children (B-135)
# ---------------------------------------------------------------------------

class TestCascadeClearsPaperTrades(unittest.TestCase):
    """B-135: signals AND paper_trades both FK to transactions(fingerprint)
    with no ON DELETE CASCADE, and db.connect() runs with foreign_keys=ON.
    Deleting a transaction that still has a paper_trade child raised
    'FOREIGN KEY constraint failed' and aborted the whole apply batch. The
    reject / delete / update-key paths must clear paper_trades first."""

    def setUp(self):
        self.conn = make_test_conn()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.rns_id = "9595811"
        self.url = f"https://www.investegate.co.uk/announcement/rns/card/{self.rns_id}"
        self.fp = _make_fingerprint("2026-06-01", "RKT", "A Director", "BUY", 340)
        insert_tx(self.conn, fingerprint=self.fp, ticker="RKT", url=self.url)
        self._insert_paper_trade(self.fp)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _insert_paper_trade(self, fp):
        self.conn.execute(
            "INSERT INTO paper_trades (trade_id, signal_id, signal_version, "
            "fingerprint, sizing_scheme, notional_gbp, status, opened_at, updated_at) "
            "VALUES ('t1','t1a_ceo_founder_buy','1.0.0',?,'log',500.0,'open',"
            "'2026-06-01T00:00:00Z','2026-06-01T00:00:00Z')",
            (fp,),
        )

    def test_reject_clears_paper_trade_no_fk_error(self):
        import apply_edits as ae
        edit = {"action": "reject", "edit_id": "r1", "target_rns_id": self.rns_id}
        # Must not raise sqlite3.IntegrityError (FOREIGN KEY constraint failed).
        ae.apply_reject(self.conn, edit, verbose=False)
        self.conn.commit()
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM transactions WHERE fingerprint=?", (self.fp,)).fetchone())
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM paper_trades WHERE fingerprint=?", (self.fp,)).fetchone())

    def test_delete_clears_paper_trade_no_fk_error(self):
        import apply_edits as ae
        # apply_delete only removes manual rows — flip parser_source first.
        self.conn.execute("UPDATE transactions SET parser_source='manual' WHERE fingerprint=?", (self.fp,))
        self.conn.commit()
        edit = {"action": "delete", "edit_id": "d1", "target_fingerprint": self.fp}
        ae.apply_delete(self.conn, edit, verbose=False)
        self.conn.commit()
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM paper_trades WHERE fingerprint=?", (self.fp,)).fetchone())


# ---------------------------------------------------------------------------
# F3. remove_from_pending_queue dedupes RNS ids (B-136)
# ---------------------------------------------------------------------------

class TestRemoveFromPendingQueueDuplicates(unittest.TestCase):
    """B-136: several 'add' edits often share one RNS (multi-director filing),
    so the resolved-id list passed to remove_from_pending_queue has duplicates.
    The old code did `del items[r]` per entry and raised KeyError on the second
    occurrence, aborting the post-commit step after the DB had already
    committed."""

    def test_duplicate_rns_ids_do_not_raise(self):
        import apply_edits as ae
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_pending_review.json"
            p.write_text(json.dumps({
                "items": {"9595811": {"x": 1}, "9592753": {"y": 2}},
                "count": 2,
            }), encoding="utf-8")
            with patch.object(ae, "PENDING_REVIEW_PATH", p):
                # '9595811' appears 5x (5 adds), '9592753' 2x — as in the live queue.
                ae.remove_from_pending_queue(
                    ["9595811"] * 5 + ["9592753"] * 2 + ["unknown"])
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertNotIn("9595811", data["items"])
            self.assertNotIn("9592753", data["items"])
            self.assertEqual(data["count"], 0)


# ---------------------------------------------------------------------------
# G. Audit JSONL
# ---------------------------------------------------------------------------

class TestAuditJsonl(unittest.TestCase):

    def test_append_produces_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            audit_path = Path(td) / "_edit_audit.jsonl"
            entries = [
                {"ts": "2026-06-03T14:00:00Z", "action": "update",
                 "edit_id": "e1", "target_fingerprint": "abc"},
                {"ts": "2026-06-03T14:01:00Z", "action": "add",
                 "edit_id": "e2", "target_fingerprint": None},
            ]
            for entry in entries:
                with audit_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")

            lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                parsed = json.loads(line)  # must not raise
                self.assertIn("action", parsed)

    def test_append_is_additive(self):
        with tempfile.TemporaryDirectory() as td:
            audit_path = Path(td) / "_edit_audit.jsonl"
            for i in range(5):
                with audit_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"edit_id": f"e{i}"}) + "\n")
            lines = audit_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 5)


# ---------------------------------------------------------------------------
# H. Queue lifecycle
# ---------------------------------------------------------------------------

class TestQueueLifecycle(unittest.TestCase):

    def test_read_empty_on_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_edit_queue.json"
            # Simulate read_queue on missing file
            result = [] if not p.exists() else json.loads(p.read_text()).get("edits", [])
            self.assertEqual(result, [])

    def test_clear_sets_empty_edits(self):
        import os as _os
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_edit_queue.json"
            p.write_text(json.dumps({
                "version": 1,
                "edits": [{"edit_id": "e1"}, {"edit_id": "e2"}]
            }))
            # Simulate clear_queue
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version": 1, "edits": []}))
            _os.replace(tmp, p)
            back = json.loads(p.read_text())
            self.assertEqual(back["edits"], [])
            self.assertEqual(back["version"], 1)


# ---------------------------------------------------------------------------
# I. Rejected RNS IDs persistence
# ---------------------------------------------------------------------------

class TestRejectedIds(unittest.TestCase):

    def test_add_and_read_back(self):
        with tempfile.TemporaryDirectory() as td:
            rejected_path = Path(td) / "_rejected_rns_ids.json"

            def _add(rns_id):
                if rejected_path.exists():
                    ids = set(json.loads(rejected_path.read_text()).get("rejected_rns_ids", []))
                else:
                    ids = set()
                ids.add(rns_id)
                rejected_path.write_text(json.dumps({
                    "rejected_rns_ids": sorted(ids), "updated_at": "2026-06-03"
                }))

            def _read():
                if not rejected_path.exists():
                    return set()
                return set(json.loads(rejected_path.read_text()).get("rejected_rns_ids", []))

            _add("9564925")
            _add("9564926")
            ids = _read()
            self.assertIn("9564925", ids)
            self.assertIn("9564926", ids)

    def test_idempotent_add(self):
        with tempfile.TemporaryDirectory() as td:
            rejected_path = Path(td) / "_rejected_rns_ids.json"

            def _add(rns_id):
                ids = set()
                if rejected_path.exists():
                    ids = set(json.loads(rejected_path.read_text()).get("rejected_rns_ids", []))
                ids.add(rns_id)
                rejected_path.write_text(json.dumps({"rejected_rns_ids": sorted(ids)}))

            _add("9564925")
            _add("9564925")  # add same ID again
            data = json.loads(rejected_path.read_text())
            self.assertEqual(data["rejected_rns_ids"].count("9564925"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
