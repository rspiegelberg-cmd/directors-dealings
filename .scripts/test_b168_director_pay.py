"""Tests for B-168 Phase 1 -- director_pay schema + helpers.

Runs against an in-memory SQLite DB (db.migrate on a :memory: connection) so it
never touches .data/directors.db. Sandbox-safe.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import director_pay as dp  # noqa: E402


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db.migrate(conn)
    return conn


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.conn = _fresh_conn()

    def tearDown(self):
        self.conn.close()

    def test_migration_reaches_15(self):
        self.assertEqual(db.get_meta(self.conn, "schema_version"), "15")

    def test_table_and_columns_exist(self):
        cols = {r["name"] for r in self.conn.execute(
            "PRAGMA table_info(director_pay)")}
        expected = {
            "id", "ticker", "director_key", "director_name_raw", "fy_end",
            "ar_published_at", "pay_native", "currency", "fx_rate", "fx_date",
            "pay_gbp", "pay_type", "role_class", "pay_status", "source_rung",
            "source_url", "confidence", "machine_readable", "fetched_at",
        }
        self.assertEqual(expected - cols, set(), f"missing cols: {expected - cols}")

    def test_pay_type_check_constraint(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO director_pay "
                "(ticker, director_key, pay_type, fetched_at) "
                "VALUES ('X', 'y', 'bogus_type', 'now')")

    def test_unique_key_is_four_part(self):
        # same director+FY, different pay_type -> both allowed (dual denominator)
        for pt in ("single_figure_total", "base_salary"):
            dp.upsert_director_pay(self.conn, {
                "ticker": "PSN", "director_key": "dean finch",
                "fy_end": "2025-12-31", "pay_type": pt, "pay_gbp": 1000.0,
            })
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM director_pay").fetchone()["c"]
        self.assertEqual(n, 2)


class TestFX(unittest.TestCase):
    def test_gbp_passthrough(self):
        out = dp.convert_to_gbp(1000.0, "GBP", "2025-12-31")
        self.assertEqual(out["pay_gbp"], 1000.0)
        self.assertEqual(out["fx_rate"], 1.0)

    def test_usd_converts(self):
        out = dp.convert_to_gbp(2160000, "USD", "2025-12-31")
        self.assertAlmostEqual(out["fx_rate"], 0.79)
        self.assertAlmostEqual(out["pay_gbp"], round(2160000 * 0.79, 2))

    def test_eur_converts(self):
        out = dp.convert_to_gbp(258125, "EUR", "2025-09-26")
        self.assertAlmostEqual(out["fx_rate"], 0.845)

    def test_unsupported_currency_returns_none(self):
        self.assertIsNone(dp.convert_to_gbp(100, "JPY", "2025-12-31"))

    def test_none_native_returns_none(self):
        self.assertIsNone(dp.convert_to_gbp(None, "GBP", "2025-12-31"))

    def test_unknown_year_uses_default(self):
        out = dp.convert_to_gbp(100, "USD", "1999-12-31")
        self.assertAlmostEqual(out["fx_rate"], 0.79)  # the USD default


class TestBuckets(unittest.TestCase):
    def test_classify_nominal(self):
        self.assertEqual(dp.classify_nominal(0), "fee_waiver_zero")
        self.assertEqual(dp.classify_nominal(-5), "fee_waiver_zero")
        self.assertEqual(dp.classify_nominal(5000), "nominal")
        self.assertEqual(dp.classify_nominal(50000), "ok")


class TestSalaryMultiple(unittest.TestCase):
    def test_normal(self):
        # GBP 300k buy vs GBP 3.0m pay -> 0.1x
        self.assertAlmostEqual(
            dp.salary_multiple(300000, pay_gbp=3000000), 0.1)

    def test_none_for_nonok_status(self):
        self.assertIsNone(dp.salary_multiple(
            300000, pay_gbp=3000000, pay_status="new_appointee_no_disclosure"))

    def test_none_for_no_multiple_pay_types(self):
        for pt in ("fee_waiver_zero", "nominal", "none"):
            self.assertIsNone(dp.salary_multiple(
                300000, pay_gbp=5000, pay_type=pt))

    def test_none_for_zero_pay(self):
        self.assertIsNone(dp.salary_multiple(300000, pay_gbp=0))

    def test_none_for_missing_buy(self):
        self.assertIsNone(dp.salary_multiple(None, pay_gbp=3000000))


class TestDirectorKeyReuse(unittest.TestCase):
    def test_case_and_nbsp_fold(self):
        self.assertEqual(dp.director_key("MURRAY MCGOWAN"),
                         dp.director_key("Murray McGowan"))
        self.assertEqual(dp.director_key("Serpil\xa0Timuray"),
                         dp.director_key("Serpil Timuray"))


class TestUpsertAndLookahead(unittest.TestCase):
    def setUp(self):
        self.conn = _fresh_conn()

    def tearDown(self):
        self.conn.close()

    def test_upsert_idempotent(self):
        rec = {"ticker": "PSN", "director_key": "dean finch",
               "fy_end": "2025-12-31", "pay_type": "single_figure_total",
               "pay_gbp": 3108751.0, "pay_status": "ok",
               "ar_published_at": "2026-03-01"}
        dp.upsert_director_pay(self.conn, rec)
        dp.upsert_director_pay(self.conn, dict(rec, pay_gbp=3200000.0))
        rows = self.conn.execute("SELECT pay_gbp FROM director_pay").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pay_gbp"], 3200000.0)  # overwritten

    def test_lookahead_excludes_future_publication(self):
        # FY2024 AR published 2025-03 (knowable); FY2025 AR published 2026-03 (not
        # yet, for a buy on 2026-01-15). The guard must pick FY2024.
        dp.upsert_director_pay(self.conn, {
            "ticker": "PSN", "director_key": "dean finch", "fy_end": "2024-12-31",
            "pay_type": "single_figure_total", "pay_gbp": 2529463.0,
            "pay_status": "ok", "ar_published_at": "2025-03-01"})
        dp.upsert_director_pay(self.conn, {
            "ticker": "PSN", "director_key": "dean finch", "fy_end": "2025-12-31",
            "pay_type": "single_figure_total", "pay_gbp": 3108751.0,
            "pay_status": "ok", "ar_published_at": "2026-03-01"})
        row = dp.latest_pay_before(
            self.conn, "PSN", "dean finch", "2026-01-15")
        self.assertIsNotNone(row)
        self.assertEqual(row["fy_end"], "2024-12-31")

    def test_lookahead_excludes_null_publication(self):
        dp.upsert_director_pay(self.conn, {
            "ticker": "PSN", "director_key": "dean finch", "fy_end": "2024-12-31",
            "pay_type": "single_figure_total", "pay_gbp": 2529463.0,
            "pay_status": "ok", "ar_published_at": None})
        row = dp.latest_pay_before(
            self.conn, "PSN", "dean finch", "2026-01-15")
        self.assertIsNone(row)

    def test_lookahead_picks_latest_when_both_published(self):
        for fy, pub, pay in (("2023-12-31", "2024-03-01", 100.0),
                             ("2024-12-31", "2025-03-01", 200.0)):
            dp.upsert_director_pay(self.conn, {
                "ticker": "PSN", "director_key": "dean finch", "fy_end": fy,
                "pay_type": "single_figure_total", "pay_gbp": pay,
                "pay_status": "ok", "ar_published_at": pub})
        row = dp.latest_pay_before(
            self.conn, "PSN", "dean finch", "2026-01-15")
        self.assertEqual(row["fy_end"], "2024-12-31")


if __name__ == "__main__":
    unittest.main(verbosity=2)
