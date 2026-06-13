"""Tests for backfill_pending_extracted.py (B-130).

Covers the lenient-row -> `extracted` schema mapping and the
{generated_at, count, items} wrapper round-trip (atomic write preserves shape).

Run:
    python -m unittest test_b130_pending_extracted -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_pending_extracted as b130  # noqa: E402

_SCHEMA_KEYS = {
    "fingerprint", "date", "ticker", "company", "director", "role", "type",
    "shares", "price", "value", "context", "url", "announced_at", "buy_strictness",
}


class TestExtractedSchema(unittest.TestCase):
    def test_full_row_maps_with_fingerprint(self):
        r = {"date": "2026-05-01", "director": "Jane Smith", "role": "CEO",
             "type": "BUY", "shares": 100, "price": 10.0,
             "nature": "Purchase of shares", "warnings": []}
        rec = {"url": "https://x/123", "announced_at": "2026-05-01T08:00:00Z"}
        obj = b130._to_extracted_obj(r, "AAA", "Acme plc", rec)
        self.assertEqual(set(obj), _SCHEMA_KEYS)
        self.assertEqual(obj["ticker"], "AAA")
        self.assertEqual(obj["company"], "Acme plc")
        self.assertEqual(obj["director"], "Jane Smith")
        self.assertEqual(obj["shares"], 100)
        self.assertEqual(obj["price"], 10.0)
        self.assertEqual(obj["value"], 1000.0)
        self.assertTrue(obj["fingerprint"])          # all key fields present
        self.assertEqual(obj["url"], "https://x/123")

    def test_partial_row_blank_fingerprint_and_zero_value(self):
        # director only — no date/ticker/type -> no fingerprint, value 0.
        obj = b130._to_extracted_obj(
            {"director": "Jane", "shares": 0, "price": 0}, "", "", {})
        self.assertEqual(set(obj), _SCHEMA_KEYS)
        self.assertEqual(obj["fingerprint"], "")
        self.assertEqual(obj["value"], 0.0)
        self.assertEqual(obj["ticker"], "")


class TestWrapperRoundTrip(unittest.TestCase):
    def test_load_write_preserves_wrapper(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "_pending_review.json"
            p.write_text(json.dumps({
                "generated_at": "t", "count": 1,
                "items": {"123": {"warnings": [], "extracted": []}},
            }), encoding="utf-8")
            payload = b130.load_pending(p)
            self.assertIn("items", payload)
            b130.write_pending(p, payload["items"])
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(set(raw), {"generated_at", "count", "items"})
            self.assertEqual(set(raw["items"]), {"123"})
            self.assertEqual(raw["count"], 1)

    def test_rejects_non_wrapper(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "_pending_review.json"
            p.write_text(json.dumps({"123": {"warnings": []}}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                b130.load_pending(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
