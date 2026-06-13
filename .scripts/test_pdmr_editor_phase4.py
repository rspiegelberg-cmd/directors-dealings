"""Sprint 25 Phase 4 -- undo, delete action, and audit-log tests.

Coverage:
  A. validate_edit: "delete" action accepted with valid fingerprint
  B. validate_edit: "delete" rejected without fingerprint
  C. apply_delete: safeguard rejects non-manual rows
  D. apply_delete: removes manual transaction + signals
  E. apply_delete: leaves other rows intact
  F. audit-log reader: empty list when file missing
  G. audit-log reader: returns last N entries most-recent first
  H. audit-log reader: handles malformed JSONL lines
  I. undo contract: update → stage inverse update (correct body shape)
  J. undo contract: add → stage delete (correct body shape)
  K. undo contract: delete → stage add (correct body shape)
  L. undo contract: reject → not auto-undoable (action check)
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# DB / schema helpers
# ---------------------------------------------------------------------------

SCHEMA_SQL = (HERE / "db_schema.sql").read_text(encoding="utf-8")
_MIG_DIR   = HERE / "schema_migrations"


def make_test_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    for mig in sorted(_MIG_DIR.glob("0*.sql")):
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except Exception:
            pass
    conn.commit()
    return conn


def _fp(date, ticker, director, tx_type, shares):
    raw = f"{date}|{ticker}|{director}|{tx_type}|{shares}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def insert_tx(conn, *, fingerprint, date="2026-05-12", ticker="CARD",
              company="Card Factory", director="Jane Doe", role="CEO",
              tx_type="BUY", shares=1000, price=1.5, value=1500.0,
              url="https://www.investegate.co.uk/announcement/rns/card/9564925",
              parser_source="regex"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO transactions ("
        "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        "company, director, role, role_normalized, type, shares, price, "
        "value, context, url, announced_at, cluster_id, first_time_buy, "
        "parser_source, buy_strictness"
        ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'STRICT_BUY')",
        (fingerprint, now, now, date, ticker, company, director, role, None,
         tx_type, shares, price, value, None, url, None, None, parser_source)
    )
    conn.commit()


def insert_signal(conn, *, fingerprint, signal_id="t1a_ceo_founder_buy"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT OR IGNORE INTO signals (signal_id, signal_version, fingerprint, fired_at) "
        "VALUES (?, '1.0', ?, ?)",
        (signal_id, fingerprint, now)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Inlined validate_edit (mirrors apply_edits.py — FUSE-safe)
# ---------------------------------------------------------------------------

import re as _re

VALID_TX_TYPES = {"BUY", "SELL", "SELL_TAX", "EXERCISE", "GRANT", "SIP"}
VALID_ACTIONS  = {"update", "add", "reject", "delete"}
_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FP_RE   = _re.compile(r"^[0-9a-f]{8,32}$")
_RNS_RE  = _re.compile(r"^\d{5,12}$")


def validate_edit(edit):
    errors = []
    action = edit.get("action", "")
    if action not in VALID_ACTIONS:
        return [f"Unknown action {action!r}"]
    if action == "reject":
        if not _RNS_RE.match(str(edit.get("target_rns_id") or "")):
            errors.append("reject requires a valid target_rns_id")
        return errors
    if action in ("update", "delete"):
        if not _FP_RE.match(str(edit.get("target_fingerprint") or "")):
            errors.append(f"{action} requires a valid target_fingerprint (8-32 hex chars)")
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
    return errors


# ---------------------------------------------------------------------------
# A. validate_edit — delete action
# ---------------------------------------------------------------------------

class TestValidateEditDelete(unittest.TestCase):

    def test_delete_with_valid_fingerprint(self):
        errs = validate_edit({
            "action": "delete",
            "target_fingerprint": "abcdef1234567890",
            "fields": {},
        })
        self.assertEqual(errs, [])

    def test_delete_without_fingerprint(self):
        errs = validate_edit({"action": "delete", "fields": {}})
        self.assertTrue(any("fingerprint" in e for e in errs))

    def test_delete_with_short_fingerprint(self):
        errs = validate_edit({
            "action": "delete",
            "target_fingerprint": "abc",   # too short
            "fields": {},
        })
        self.assertTrue(any("fingerprint" in e for e in errs))

    def test_delete_with_non_hex_fingerprint(self):
        errs = validate_edit({
            "action": "delete",
            "target_fingerprint": "xyz12345678901234",
            "fields": {},
        })
        self.assertTrue(any("fingerprint" in e for e in errs))

    def test_delete_is_in_valid_actions(self):
        self.assertIn("delete", VALID_ACTIONS)


# ---------------------------------------------------------------------------
# B+C. apply_delete safeguard
# ---------------------------------------------------------------------------

class TestApplyDeleteSafeguardPhase4(unittest.TestCase):

    def setUp(self):
        self.conn = make_test_conn()
        self.fp_regex  = _fp("2026-05-12", "CARD", "Jane", "BUY", 1000)
        self.fp_manual = _fp("2026-05-12", "XYZ",  "Bob",  "BUY",  500)
        insert_tx(self.conn, fingerprint=self.fp_regex,  parser_source="regex")
        insert_tx(self.conn, fingerprint=self.fp_manual, parser_source="manual",
                  ticker="XYZ", director="Bob", shares=500)

    def tearDown(self):
        self.conn.close()

    def _simulate_delete(self, fp):
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE fingerprint = ?", (fp,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Fingerprint {fp!r} not found")
        if dict(row)["parser_source"] != "manual":
            raise ValueError(
                f"parser_source={dict(row)['parser_source']!r}. "
                "Only manual rows can be deleted."
            )
        self.conn.execute("DELETE FROM signals WHERE fingerprint = ?", (fp,))
        self.conn.execute("DELETE FROM transactions WHERE fingerprint = ?", (fp,))
        self.conn.commit()

    def test_delete_regex_row_raises(self):
        with self.assertRaises(ValueError, msg="Should raise for regex-sourced row"):
            self._simulate_delete(self.fp_regex)

    def test_regex_row_survives_failed_delete(self):
        try:
            self._simulate_delete(self.fp_regex)
        except ValueError:
            pass
        row = self.conn.execute(
            "SELECT fingerprint FROM transactions WHERE fingerprint = ?",
            (self.fp_regex,)
        ).fetchone()
        self.assertIsNotNone(row, "Regex row must still exist after failed delete")

    def test_delete_manual_row_succeeds(self):
        self._simulate_delete(self.fp_manual)
        row = self.conn.execute(
            "SELECT fingerprint FROM transactions WHERE fingerprint = ?",
            (self.fp_manual,)
        ).fetchone()
        self.assertIsNone(row, "Manual row should be deleted")


# ---------------------------------------------------------------------------
# D+E. apply_delete: removes tx + signals, leaves others
# ---------------------------------------------------------------------------

class TestApplyDeleteEffect(unittest.TestCase):

    def setUp(self):
        self.conn = make_test_conn()
        self.fp = _fp("2026-05-13", "ABC", "Alice", "SELL", 200)
        self.other_fp = _fp("2026-05-14", "DEF", "Carol", "BUY", 100)
        insert_tx(self.conn, fingerprint=self.fp, parser_source="manual",
                  ticker="ABC", director="Alice", tx_type="SELL", shares=200)
        insert_signal(self.conn, fingerprint=self.fp)
        insert_tx(self.conn, fingerprint=self.other_fp, parser_source="manual",
                  ticker="DEF", director="Carol", shares=100)

    def tearDown(self):
        self.conn.close()

    def _delete(self, fp):
        self.conn.execute("DELETE FROM signals WHERE fingerprint = ?", (fp,))
        self.conn.execute("DELETE FROM transactions WHERE fingerprint = ?", (fp,))
        self.conn.commit()

    def test_transaction_deleted(self):
        self._delete(self.fp)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM transactions WHERE fingerprint = ?", (self.fp,)
        ).fetchone())

    def test_signals_cascaded(self):
        self._delete(self.fp)
        self.assertEqual(len(self.conn.execute(
            "SELECT 1 FROM signals WHERE fingerprint = ?", (self.fp,)
        ).fetchall()), 0)

    def test_other_row_untouched(self):
        self._delete(self.fp)
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM transactions WHERE fingerprint = ?", (self.other_fp,)
        ).fetchone())


# ---------------------------------------------------------------------------
# F–H. Audit-log reader
# ---------------------------------------------------------------------------

class TestAuditLogReader(unittest.TestCase):
    """Inline simulation of the /api/audit-log logic."""

    def _read_audit_log(self, path, n=50):
        """Inline version of the Flask endpoint logic."""
        if not path.exists():
            return {"ok": True, "entries": [], "total_lines": 0}
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        total = len(lines)
        recent = lines[-n:][::-1]
        entries = []
        for line in recent:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append({"_raw": line, "_parse_error": True})
        return {"ok": True, "entries": entries, "total_lines": total}

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_edit_audit.jsonl"
            result = self._read_audit_log(path)
            self.assertTrue(result["ok"])
            self.assertEqual(result["entries"], [])
            self.assertEqual(result["total_lines"], 0)

    def test_returns_entries_most_recent_first(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_edit_audit.jsonl"
            entries = [{"ts": f"2026-06-04T00:0{i}:00Z", "action": "update"} for i in range(5)]
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
            result = self._read_audit_log(path, n=5)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["entries"]), 5)
            # Most recent (last written) should be first
            self.assertEqual(result["entries"][0]["ts"], "2026-06-04T00:04:00Z")
            self.assertEqual(result["entries"][-1]["ts"], "2026-06-04T00:00:00Z")

    def test_n_limits_returned_entries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_edit_audit.jsonl"
            path.write_text("\n".join(
                json.dumps({"ts": f"2026-06-04T00:0{i}:00Z"}) for i in range(8)
            ) + "\n")
            result = self._read_audit_log(path, n=3)
            self.assertEqual(len(result["entries"]), 3)
            self.assertEqual(result["total_lines"], 8)

    def test_handles_malformed_jsonl_line(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_edit_audit.jsonl"
            path.write_text(
                '{"ts":"2026-06-04T00:00:00Z","action":"add"}\n'
                'NOT VALID JSON {{{\n'
                '{"ts":"2026-06-04T00:01:00Z","action":"update"}\n'
            )
            result = self._read_audit_log(path, n=10)
            self.assertTrue(result["ok"])
            self.assertEqual(result["total_lines"], 3)
            # Malformed line becomes a _parse_error entry
            parse_errors = [e for e in result["entries"] if e.get("_parse_error")]
            self.assertEqual(len(parse_errors), 1)

    def test_empty_file_returns_no_entries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_edit_audit.jsonl"
            path.write_text("")
            result = self._read_audit_log(path)
            self.assertEqual(result["entries"], [])
            self.assertEqual(result["total_lines"], 0)


# ---------------------------------------------------------------------------
# I–L. Undo body construction logic
# ---------------------------------------------------------------------------

class TestUndoBodyConstruction(unittest.TestCase):
    """Verify the undo request bodies match the expected API contract.
    Mirrors the JS undoEdit() function logic in Python for testability.
    """

    EDITABLE = ["date","ticker","director","type","shares","role","company",
                "price","value","sector","announced_at"]

    def _undo_body(self, entry):
        """Python equivalent of the JS undoEdit() body construction."""
        action = entry.get("action")

        if action == "update":
            current_fp = (entry.get("after") or {}).get("fingerprint") or entry.get("target_fingerprint")
            before = entry.get("before") or {}
            fields = {k: before[k] for k in self.EDITABLE if k in before}
            return {"action": "update", "target_fingerprint": current_fp, "fields": fields}

        elif action == "add":
            fp = (entry.get("after") or {}).get("fingerprint")
            return {"action": "delete", "target_fingerprint": fp, "fields": {}}

        elif action == "delete":
            before = entry.get("before") or {}
            url    = before.get("url") or ""
            rns_id = entry.get("target_rns_id") or url.split("/")[-1]
            fields = {k: before.get(k) for k in
                      ["date","ticker","director","type","shares","price","value","role","company"]
                      if before.get(k) is not None}
            return {"action": "add", "target_rns_id": rns_id, "fields": fields}

        else:
            return None  # reject: not auto-undoable

    def test_undo_update_stages_inverse_update(self):
        entry = {
            "action": "update",
            "target_fingerprint": "oldfingerprint1",
            "before": {"role": "CEO", "price": 1.5},
            "after":  {"fingerprint": "oldfingerprint1", "role": "CFO", "price": 1.5},
        }
        body = self._undo_body(entry)
        self.assertEqual(body["action"], "update")
        self.assertEqual(body["target_fingerprint"], "oldfingerprint1")
        self.assertEqual(body["fields"]["role"], "CEO")

    def test_undo_update_uses_after_fingerprint_if_key_changed(self):
        """If fingerprint changed (key edit), undo targets the NEW fingerprint."""
        entry = {
            "action": "update",
            "target_fingerprint": "old_fp_12345678",
            "before": {"shares": 1000, "role": "CEO"},
            "after":  {"fingerprint": "new_fp_12345678", "shares": 2000},
        }
        body = self._undo_body(entry)
        self.assertEqual(body["target_fingerprint"], "new_fp_12345678")
        self.assertEqual(body["fields"]["shares"], 1000)

    def test_undo_add_stages_delete(self):
        entry = {
            "action": "add",
            "target_rns_id": "9564925",
            "before": {},
            "after": {"fingerprint": "manualfp12345678", "ticker": "XYZ"},
        }
        body = self._undo_body(entry)
        self.assertEqual(body["action"], "delete")
        self.assertEqual(body["target_fingerprint"], "manualfp12345678")

    def test_undo_delete_stages_add(self):
        entry = {
            "action": "delete",
            "target_rns_id": "9564925",
            "before": {
                "ticker": "XYZ", "director": "Bob", "type": "BUY",
                "shares": 500, "date": "2026-05-12", "role": "CEO",
                "company": "XYZ Co", "price": 1.5, "value": 750.0,
                "url": "https://investegate.co.uk/rns/9564925",
            },
            "after": {"deleted": True},
        }
        body = self._undo_body(entry)
        self.assertEqual(body["action"], "add")
        self.assertEqual(body["target_rns_id"], "9564925")
        self.assertEqual(body["fields"]["ticker"], "XYZ")
        self.assertEqual(body["fields"]["shares"], 500)

    def test_undo_reject_returns_none(self):
        """Reject cannot be auto-undone — undo_body returns None."""
        entry = {
            "action": "reject",
            "target_rns_id": "9564925",
            "before": {"deleted_fingerprints": []},
            "after":  {"rejected": True},
        }
        body = self._undo_body(entry)
        self.assertIsNone(body, "Reject undo should return None (not supported)")

    def test_undo_update_includes_only_editable_fields(self):
        """Non-editable fields like fingerprint/parser_source should not leak into undo body."""
        entry = {
            "action": "update",
            "target_fingerprint": "fp_12345678",
            "before": {
                "role": "CEO", "fingerprint": "fp_12345678",
                "parser_source": "regex", "last_seen": "2026-01-01T00:00:00Z"
            },
            "after": {"fingerprint": "fp_12345678", "role": "CFO"},
        }
        body = self._undo_body(entry)
        self.assertNotIn("fingerprint", body["fields"])
        self.assertNotIn("parser_source", body["fields"])
        self.assertNotIn("last_seen", body["fields"])
        self.assertIn("role", body["fields"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
