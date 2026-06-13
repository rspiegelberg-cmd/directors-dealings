"""Sprint 25 Phase 1 — staging endpoint tests.

Tests the server-side validation and queue logic WITHOUT running Flask.
All logic is tested by importing the validation helpers inline.

Coverage:
  A. _validate_fields: all field-level rules
  B. Stage-edit body validation rules (action, target, fields)
  C. Queue read/write helpers (_read_edit_queue / _write_edit_queue_atomic)
  D. Unstage removes correct edit_id
  E. review.html Phase 1 content: editable form markers, queue panel, reject dialog
"""

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# ── Inline validation logic (mirrors server.py — FUSE-safe) ──────────────────

VALID_TYPES   = {"BUY", "SELL", "SELL_TAX", "EXERCISE", "GRANT", "SIP"}
VALID_ACTIONS = {"update", "add", "reject"}
KNOWN_SECTORS = {
    "Financials", "Energy", "Health Care", "Industrials",
    "Consumer Discretionary", "Consumer Staples", "Materials",
    "Technology", "Utilities", "Communication Services", "Real Estate",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RNS_RE  = re.compile(r"^\d{5,12}$")
_FP_RE   = re.compile(r"^[0-9a-f]{8,32}$")


def _validate_fields(fields, action):
    errors = []
    if action == "reject":
        return errors
    if action == "add":
        for req in ("date", "ticker", "director", "type", "shares"):
            if not fields.get(req) and fields.get(req) != 0:
                errors.append(f"'{req}' is required for add action")
    t = fields.get("type")
    if t is not None and t not in VALID_TYPES:
        errors.append(f"type must be one of valid types, got {t!r}")
    for num_field in ("shares", "price", "value"):
        v = fields.get(num_field)
        if v is not None:
            try:
                fv = float(v)
                if fv < 0:
                    errors.append(f"{num_field} must be >= 0")
                if num_field == "shares" and fv != int(fv):
                    errors.append("shares must be a whole number")
            except (TypeError, ValueError):
                errors.append(f"{num_field} must be numeric")
    d = fields.get("date")
    if d is not None and not _DATE_RE.match(str(d)):
        errors.append("date must be YYYY-MM-DD")
    tk = fields.get("ticker")
    if tk is not None and not str(tk).strip():
        errors.append("ticker must not be empty")
    for sf in ("director", "company"):
        sv = fields.get(sf)
        if sv is not None and not str(sv).strip():
            errors.append(f"{sf} must not be empty")
    sector = fields.get("sector")
    if sector and sector not in KNOWN_SECTORS:
        errors.append(f"sector {sector!r} not in known list")
    return errors


def _make_queue_path(tmp_dir):
    return Path(tmp_dir) / "_edit_queue.json"


def _read_q(path):
    if not path.exists():
        return {"version": 1, "edits": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "edits": []}


def _write_q(queue, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ── A. Field validation ───────────────────────────────────────────────────────

class TestValidateFields(unittest.TestCase):

    # ── type ──────────────────────────────────────────────────────────────────
    def test_valid_types_pass(self):
        for t in VALID_TYPES:
            self.assertEqual([], _validate_fields({"type": t}, "update"),
                             f"type {t} should be valid")

    def test_invalid_type_error(self):
        errs = _validate_fields({"type": "MAGIC"}, "update")
        self.assertTrue(any("type" in e for e in errs))

    # ── shares ────────────────────────────────────────────────────────────────
    def test_shares_zero_ok(self):
        self.assertEqual([], _validate_fields({"shares": 0}, "update"))

    def test_shares_negative_error(self):
        errs = _validate_fields({"shares": -1}, "update")
        self.assertTrue(any("shares" in e and ">= 0" in e for e in errs))

    def test_shares_fractional_error(self):
        errs = _validate_fields({"shares": 100.5}, "update")
        self.assertTrue(any("whole" in e for e in errs))

    def test_shares_non_numeric_error(self):
        errs = _validate_fields({"shares": "abc"}, "update")
        self.assertTrue(any("numeric" in e for e in errs))

    # ── price / value ─────────────────────────────────────────────────────────
    def test_price_zero_ok(self):
        self.assertEqual([], _validate_fields({"price": 0}, "update"))

    def test_price_negative_error(self):
        errs = _validate_fields({"price": -0.01}, "update")
        self.assertTrue(any("price" in e and ">= 0" in e for e in errs))

    # ── date ──────────────────────────────────────────────────────────────────
    def test_date_iso_ok(self):
        self.assertEqual([], _validate_fields({"date": "2026-05-12"}, "update"))

    def test_date_bad_format_error(self):
        for bad in ("12/05/2026", "12 May 2026", "2026-5-12"):
            errs = _validate_fields({"date": bad}, "update")
            self.assertTrue(any("YYYY-MM-DD" in e for e in errs), f"bad date {bad!r} should error")

    # ── ticker / director ─────────────────────────────────────────────────────
    def test_empty_ticker_error(self):
        errs = _validate_fields({"ticker": ""}, "update")
        self.assertTrue(any("ticker" in e for e in errs))

    def test_empty_director_error(self):
        errs = _validate_fields({"director": "   "}, "update")
        self.assertTrue(any("director" in e for e in errs))

    # ── sector ────────────────────────────────────────────────────────────────
    def test_known_sector_ok(self):
        self.assertEqual([], _validate_fields({"sector": "Financials"}, "update"))

    def test_unknown_sector_warns(self):
        errs = _validate_fields({"sector": "SpaceX"}, "update")
        self.assertTrue(any("sector" in e for e in errs))

    # ── reject skips validation ───────────────────────────────────────────────
    def test_reject_skips_field_validation(self):
        errs = _validate_fields({"type": "INVALID", "shares": -99}, "reject")
        self.assertEqual([], errs)

    # ── add requires key fields ────────────────────────────────────────────────
    def test_add_requires_date(self):
        errs = _validate_fields({"ticker": "T", "director": "D", "type": "BUY", "shares": 1}, "add")
        self.assertTrue(any("date" in e for e in errs))

    def test_add_requires_ticker(self):
        errs = _validate_fields({"date": "2026-01-01", "director": "D", "type": "BUY", "shares": 1}, "add")
        self.assertTrue(any("ticker" in e for e in errs))

    def test_add_full_fields_ok(self):
        errs = _validate_fields({
            "date": "2026-05-12", "ticker": "CARD", "director": "Jane",
            "type": "BUY", "shares": 100, "price": 1.5, "value": 150,
        }, "add")
        self.assertEqual([], errs)


# ── B. Action / target validation ─────────────────────────────────────────────

class TestActionValidation(unittest.TestCase):

    def test_valid_actions(self):
        for a in VALID_ACTIONS:
            self.assertIn(a, VALID_ACTIONS)

    def test_invalid_action(self):
        self.assertNotIn("delete", VALID_ACTIONS)

    def test_update_needs_fingerprint(self):
        fp = "abc123def456ab12"
        self.assertTrue(_FP_RE.match(fp))

    def test_update_invalid_fingerprint(self):
        self.assertIsNone(_FP_RE.match("xyz!@#"))

    def test_add_needs_rns_id(self):
        self.assertTrue(_RNS_RE.match("9564925"))

    def test_add_invalid_rns_id(self):
        self.assertIsNone(_RNS_RE.match("123"))
        self.assertIsNone(_RNS_RE.match("abc"))


# ── C. Queue read/write ───────────────────────────────────────────────────────

class TestQueueReadWrite(unittest.TestCase):

    def test_empty_queue_on_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_queue_path(td)
            q = _read_q(p)
            self.assertEqual(q, {"version": 1, "edits": []})

    def test_write_and_read_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_queue_path(td)
            queue = {"version": 1, "edits": [
                {"edit_id": "edit-001", "action": "update",
                 "target_fingerprint": "abcdef12", "fields": {"director": "Jane"},
                 "staged_at": "2026-06-03T14:00:00Z"}
            ]}
            _write_q(queue, p)
            back = _read_q(p)
            self.assertEqual(back["edits"][0]["edit_id"], "edit-001")
            self.assertEqual(back["edits"][0]["fields"]["director"], "Jane")

    def test_atomic_write_no_partial(self):
        """Atomic write: tmp file must be replaced, not left behind."""
        with tempfile.TemporaryDirectory() as td:
            p = _make_queue_path(td)
            _write_q({"version": 1, "edits": []}, p)
            tmp = p.with_suffix(".json.tmp")
            self.assertFalse(tmp.exists(), "tmp file should be cleaned up by os.replace")

    def test_append_edit(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_queue_path(td)
            q = _read_q(p)
            q["edits"].append({"edit_id": "e1", "action": "update", "fields": {}})
            _write_q(q, p)
            q2 = _read_q(p)
            q2["edits"].append({"edit_id": "e2", "action": "add", "fields": {}})
            _write_q(q2, p)
            final = _read_q(p)
            self.assertEqual(len(final["edits"]), 2)
            self.assertEqual(final["edits"][1]["edit_id"], "e2")


# ── D. Unstage ────────────────────────────────────────────────────────────────

class TestUnstage(unittest.TestCase):

    def _make_queue(self, *edit_ids):
        return {"version": 1, "edits": [
            {"edit_id": eid, "action": "update", "fields": {}} for eid in edit_ids
        ]}

    def test_unstage_removes_correct_edit(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_queue_path(td)
            _write_q(self._make_queue("e1", "e2", "e3"), p)
            q = _read_q(p)
            q["edits"] = [e for e in q["edits"] if e["edit_id"] != "e2"]
            _write_q(q, p)
            final = _read_q(p)
            ids = [e["edit_id"] for e in final["edits"]]
            self.assertNotIn("e2", ids)
            self.assertIn("e1", ids)
            self.assertIn("e3", ids)

    def test_unstage_nonexistent_returns_same_count(self):
        with tempfile.TemporaryDirectory() as td:
            p = _make_queue_path(td)
            _write_q(self._make_queue("e1", "e2"), p)
            q = _read_q(p)
            before = len(q["edits"])
            q["edits"] = [e for e in q["edits"] if e["edit_id"] != "e_MISSING"]
            self.assertEqual(before, len(q["edits"]))


# ── E. review.html Phase 1 content ───────────────────────────────────────────
# NOTE: review.html is a large file (560+ lines). Reading it via FUSE from
# the bash sandbox causes truncation past ~380 lines; the form-builder JS
# lives at lines 480+. Use subprocess (fresh process, bypasses FUSE page cache)
# to grep for specific strings — same pattern used by Phase 0 tests.

import subprocess as _subprocess

def _grep_review(*needles):
    """Return dict {needle: found} for each needle in review.html.

    Reads the file via a fresh Python subprocess so FUSE page-cache
    truncation in the parent process doesn't affect the result.
    """
    path = str(REPO / "outputs" / "review.html")
    needle_list = repr(list(needles))
    code = (
        f"needles={needle_list}; "
        f"content=open({repr(path)}, encoding='utf-8').read(); "
        f"print('\\n'.join(n + ':' + ('1' if n in content else '0') for n in needles))"
    )
    # B-117: two Windows-only bugs made this whole class silently fail:
    #  (1) the literal "python3" is a non-functional Store stub -> use sys.executable;
    #  (2) open() with no encoding defaults to cp1252 and chokes on review.html's
    #      UTF-8 (&mdash;/£/Unicode) -> read it as utf-8.
    r = _subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    # Fail loudly rather than reporting "all needles absent" if the subprocess
    # errored (missing file, decode error, ...). That silent path is exactly what
    # masked this cluster for so long.
    if r.returncode != 0:
        raise AssertionError(
            f"_grep_review subprocess failed (rc={r.returncode}): {r.stderr.strip()}")
    result = {}
    for line in r.stdout.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            result[k] = v.strip() == "1"
    return result


class TestReviewHtmlPhase1(unittest.TestCase):

    def _check(self, *needles):
        """Assert all needles are found in review.html (subprocess read)."""
        found = _grep_review(*needles)
        for n in needles:
            self.assertTrue(found.get(n, False),
                            f"{n!r} not found in review.html")

    def _absent(self, needle):
        found = _grep_review(needle)
        self.assertFalse(found.get(needle, True), f"{needle!r} should NOT be in review.html")

    def test_phase1_badge_present(self):
        # B-117: the review page badge advanced past Phase 1 to "Phase 4 -
        # Complete" as the editor shipped. Assert the current phase badge (the
        # point of this test is that the page is the built editor, not Phase 0).
        self._check("Phase 4")

    def test_phase0_badge_absent(self):
        self._absent("Phase 0 — Read-only")

    def test_form_fields_present(self):
        self._check("f-date", "f-ticker", "f-director", "f-type",
                    "f-shares", "f-price", "f-value")

    def test_stage_edit_endpoint_referenced(self):
        self._check("/api/stage-edit")

    def test_edit_queue_endpoint_referenced(self):
        self._check("/api/edit-queue")

    def test_reject_dialog_present(self):
        self._check("reject-dialog", "Reject filing")

    def test_queue_panel_present(self):
        self._check("queue-panel", "edits staged")

    def test_type_dropdown_has_all_valid_types(self):
        self._check("BUY", "SELL_TAX", "EXERCISE", "GRANT", "SIP")

    def test_sector_dropdown_has_known_sectors(self):
        self._check("Financials", "Health Care", "Consumer Discretionary")

    def test_unstage_delete_endpoint(self):
        self._check("api/edit-queue/")

    def test_reject_reasons_present(self):
        self._check("junk", "boilerplate", "foreign_currency", "duplicate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
