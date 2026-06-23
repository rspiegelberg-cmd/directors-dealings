"""Sprint 5 dual-emit migration tests for export_dashboard_json.py.

Pinned to the Sprint 5 acceptance criteria:
  * signals.json contains BOTH legacy `cohorts` AND new `cohorts_v2`
  * Three new files (`performance_bucket.json`, `performance_role.json`,
    `performance_sector.json`) are emitted
  * Both shapes coexist in signals.json without one overwriting the other
  * `_atomic_write_json` is used for every output
  * Existing `render_performance.py` does not crash on the new shape
    (it reads legacy `cohorts.by_value_bucket` — the legacy shape must
    stay byte-identical to pre-Sprint-5)

Tests use a temp DB + temp CSV seeded with synthetic data so they're
fully Zone A (no touching the real `.data/` directory).

Run under:
    python .scripts/test_export_dashboard_json.py
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import export_dashboard_json as edj  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic-fixture builders
# ---------------------------------------------------------------------------

_CSV_HEADER = [
    "run_id", "signal_id", "signal_version", "fingerprint", "fired_at",
    "ticker", "role", "role_class", "value_gbp", "is_aim",
    "benchmark_symbol", "entry_date", "entry_close",
    "t1_close", "t30_close", "t90_close", "t180_close", "t365_close",
    "benchmark_entry", "benchmark_t1", "benchmark_t30", "benchmark_t90",
    "benchmark_t180", "benchmark_t365",
    "raw_return_t1", "raw_return_t30", "raw_return_t90", "raw_return_t180", "raw_return_t365",
    "benchmark_return_t1", "benchmark_return_t30",
    "benchmark_return_t90", "benchmark_return_t180", "benchmark_return_t365",
    "car_t1", "car_t30", "car_t90", "car_t180", "car_t365",
    "cost_bps", "net_car_t1", "net_car_t30", "net_car_t90", "net_car_t180", "net_car_t365",
    "windows_available",
]


def _write_synthetic_csv(path, rows):
    """Write a synthetic _backtest_results.csv. Each row dict needs to
    carry at least signal_id, fingerprint, fired_at, ticker, role,
    role_class, value_gbp, car_t30. Missing fields default to empty."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADER)
        writer.writeheader()
        for r in rows:
            full = {h: "" for h in _CSV_HEADER}
            full.update(r)
            writer.writerow(full)


def _seed_db(conn, transactions, tickers_meta, signals):
    """Seed a fresh in-memory DB with the rows needed for the test."""
    for tx in transactions:
        conn.execute(
            "INSERT INTO transactions ("
            "  fingerprint, first_seen, last_seen, seen_count, "
            "  date, ticker, company, director, role, type, "
            "  shares, price, value, context, url, announced_at"
            ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 'BUY', "
            "          100, 10.0, ?, NULL, NULL, NULL)",
            (
                tx["fingerprint"], tx["date"], tx["date"],
                tx["date"], tx["ticker"], tx["company"],
                tx["director"], tx.get("role", ""),
                tx.get("value", 1000.0),
            ),
        )
    for tm in tickers_meta:
        conn.execute(
            "INSERT INTO tickers_meta ("
            "  ticker, sector, benchmark_symbol, is_aim, market_cap_gbp,"
            "  updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (tm["ticker"], tm["sector"], tm["benchmark_symbol"],
             tm.get("is_aim", 0), tm.get("market_cap_gbp", 0.0),
             "2026-05-01"),
        )
    for sig in signals:
        conn.execute(
            "INSERT INTO signals ("
            "  signal_id, signal_version, fingerprint, fired_at, "
            "  confidence, metadata"
            ") VALUES (?, '1', ?, ?, NULL, NULL)",
            (sig["signal_id"], sig["fingerprint"], sig["fired_at"]),
        )
    conn.commit()


class DualEmitTestBase(unittest.TestCase):
    """Common setup: temp dir, fresh DB, synthetic CSV + DB rows."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sprint5_")
        self.out_dir = Path(self.tmp) / "dashboard" / "data"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Patch db.DB_PATH so db.connect() opens our temp DB.
        self._orig_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp) / "directors.db"
        # Apply the project schema to the temp DB.
        conn = sqlite3.connect(str(db.DB_PATH))
        conn.executescript((HERE / "db_schema.sql").read_text())
        conn.commit()
        conn.close()
        # Seed the CSV path.
        self.csv_path = Path(self.tmp) / "_backtest_results.csv"
        # Seed DB with three transactions + signals.
        conn = db.connect()
        try:
            _seed_db(
                conn,
                transactions=[
                    {"fingerprint": "fp-AAL-001", "date": "2026-05-01",
                     "ticker": "AAL", "company": "Anglo American Plc",
                     "director": "Duncan Wanblad", "role": "Chief Executive Officer",
                     "value": 150_000.0},
                    {"fingerprint": "fp-AZN-001", "date": "2026-05-01",
                     "ticker": "AZN", "company": "AstraZeneca Plc",
                     "director": "Pascal Soriot", "role": "Chief Executive Officer",
                     "value": 250_000.0},
                    {"fingerprint": "fp-BP-001", "date": "2026-05-01",
                     "ticker": "BP",  "company": "BP Plc",
                     "director": "Murray Auchincloss",
                     "role": "Non-Executive Director",
                     "value": 50_000.0},
                ],
                tickers_meta=[
                    {"ticker": "AAL", "sector": "Materials",
                     "benchmark_symbol": "^FTNMX1770"},
                    {"ticker": "AZN", "sector": "Health Care",
                     "benchmark_symbol": "^FTNMX2010"},
                    {"ticker": "BP",  "sector": "Energy",
                     "benchmark_symbol": "^FTNMX1010"},
                ],
                signals=[
                    # B-027: switched legacy t1_ceo_cfo_buy to Phase B's
                    # per-bucket t1a_ceo_founder_buy (the CEO/Founder tile).
                    {"signal_id": "t1a_ceo_founder_buy",
                     "fingerprint": "fp-AAL-001",
                     "fired_at": "2026-05-01T09:00:00Z"},
                    {"signal_id": "t1a_ceo_founder_buy",
                     "fingerprint": "fp-AZN-001",
                     "fired_at": "2026-05-01T09:00:00Z"},
                    {"signal_id": "t3_ned_buy",
                     "fingerprint": "fp-BP-001",
                     "fired_at": "2026-05-01T09:00:00Z"},
                ],
            )
        finally:
            conn.close()
        # Seed the CSV with three matching rows.
        _write_synthetic_csv(self.csv_path, [
            {"run_id": "r1", "signal_id": "t1a_ceo_founder_buy", "signal_version": "1",
             "fingerprint": "fp-AAL-001", "fired_at": "2026-05-01T09:00:00Z",
             "ticker": "AAL", "role": "Chief Executive Officer",
             "role_class": "T1a", "value_gbp": "150000",
             "car_t30": "0.05", "car_t1": "0.01", "car_t90": "0.06",
             "car_t365": "0.10",
             "benchmark_return_t30": "0.02"},
            {"run_id": "r1", "signal_id": "t1a_ceo_founder_buy", "signal_version": "1",
             "fingerprint": "fp-AZN-001", "fired_at": "2026-05-01T09:00:00Z",
             "ticker": "AZN", "role": "Chief Executive Officer",
             "role_class": "T1a", "value_gbp": "250000",
             "car_t30": "-0.02", "car_t1": "0.00", "car_t90": "0.03",
             "car_t365": "0.05",
             "benchmark_return_t30": "0.02"},
            {"run_id": "r1", "signal_id": "t3_ned_buy", "signal_version": "1",
             "fingerprint": "fp-BP-001", "fired_at": "2026-05-01T09:00:00Z",
             "ticker": "BP", "role": "Non-Executive Director",
             "role_class": "T3", "value_gbp": "50000",
             "car_t30": "0.04", "car_t1": "0.01", "car_t90": "0.02",
             "car_t365": "0.07",
             "benchmark_return_t30": "0.02"},
        ])

    def tearDown(self):
        db.DB_PATH = self._orig_db_path
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_exporter(self):
        """Run the exporter end-to-end and return the summary dict."""
        return edj.run(
            out_dir=self.out_dir,
            csv_path=self.csv_path,
            today=date(2026, 5, 18),
            emit_timestamp=False,
            verbose=False,
        )


class TestDualEmitSignalsJson(DualEmitTestBase):
    """signals.json must contain BOTH legacy `cohorts` AND new `cohorts_v2`."""

    def test_01_legacy_cohorts_shape_preserved(self):
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        # Legacy keys present.
        self.assertIn("cohorts", signals)
        self.assertIn("by_value_bucket", signals["cohorts"])
        self.assertIn("by_sector", signals["cohorts"])
        # Legacy by_value_bucket is the old scalar-per-bucket shape.
        for key, val in signals["cohorts"]["by_value_bucket"].items():
            self.assertTrue(val is None or isinstance(val, (int, float)),
                            f"legacy {key} should be scalar, got {type(val)}")
        # Legacy by_sector is a list of dicts (the old top-5 shape).
        self.assertIsInstance(signals["cohorts"]["by_sector"], list)

    def test_02_cohorts_v2_block_present_with_three_tiles(self):
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        self.assertIn("cohorts_v2", signals)
        self.assertEqual(
            set(signals["cohorts_v2"].keys()),
            {"by_value_bucket", "by_role", "by_sector"},
        )

    def test_03_cohorts_v2_uses_new_horizon_lookback_shape(self):
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        v2_bucket = signals["cohorts_v2"]["by_value_bucket"]
        # Top level keys = horizons.
        self.assertEqual(
            set(v2_bucket.keys()),
            {"t1", "t30", "t90", "t180", "t365"},
        )
        # Each horizon has 4 lookbacks.
        self.assertEqual(
            set(v2_bucket["t30"].keys()),
            {"30d", "90d", "6m", "1y", "all"},
        )
        # Each lookback cell has the new shape.
        cell = v2_bucket["t30"]["all"]
        self.assertIn("rows",    cell)
        self.assertIn("total_n", cell)

    def test_04_both_shapes_coexist_without_overwriting(self):
        """Defensive: confirms one block writing didn't clobber the other."""
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        self.assertIn("cohorts", signals)
        self.assertIn("cohorts_v2", signals)
        # Different shapes — types should differ at the same sub-key.
        legacy_bucket = signals["cohorts"]["by_value_bucket"]
        v2_bucket = signals["cohorts_v2"]["by_value_bucket"]
        self.assertIsInstance(legacy_bucket, dict)
        self.assertIsInstance(v2_bucket, dict)
        # Legacy keys are bucket names; v2 keys are horizon names.
        self.assertEqual(set(v2_bucket.keys()), {"t1", "t30", "t90", "t180", "t365"})
        # Legacy keys are some subset of bucket names.
        for k in legacy_bucket.keys():
            self.assertIn(k, {"1k-25k", "25k-100k", "100k-500k", "500k+"})


class TestNewPerformanceFiles(DualEmitTestBase):
    """The three new drill-down files must be written + valid JSON."""

    def test_05_three_new_files_written(self):
        self._run_exporter()
        self.assertTrue((self.out_dir / "performance_bucket.json").exists())
        self.assertTrue((self.out_dir / "performance_role.json").exists())
        self.assertTrue((self.out_dir / "performance_sector.json").exists())

    def test_06_files_parse_as_valid_json(self):
        self._run_exporter()
        for name in ("performance_bucket.json", "performance_role.json",
                     "performance_sector.json"):
            content = (self.out_dir / name).read_text()
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                self.fail(f"{name} is not valid JSON: {e}")

    def test_07_bucket_payload_top_level_shape(self):
        self._run_exporter()
        p = json.loads(
            (self.out_dir / "performance_bucket.json").read_text()
        )
        self.assertEqual(p["schema_version"], "1.0")
        self.assertIn("buckets", p)
        # Synthetic CSV: AAL 150k + AZN 250k → 100k-500k bucket only.
        self.assertIn("100k-500k", p["buckets"])
        self.assertEqual(
            p["buckets"]["100k-500k"]["scope_note"],
            "T1 + T2 buys only",
        )

    def test_08_role_payload_per_tier_keys_excludes_none_catchall(self):
        """B-025 Phase B (2026-05-20): the role payload now keys on the
        per-tier strings (t1a/t1b/t2/t3/t5/t7) instead of the legacy
        combined buckets (ceo_cfo/other_exec/ned)."""
        self._run_exporter()
        p = json.loads(
            (self.out_dir / "performance_role.json").read_text()
        )
        self.assertIn("roles", p)
        # Synthetic: 2 CEOs (→ t1a) + 1 NED (→ t3) → 2 keys in role payload.
        self.assertEqual(
            set(p["roles"].keys()) & {"t1a", "t3"},
            {"t1a", "t3"},
        )
        # The classifier's None catch-all is excluded.
        self.assertNotIn(None,    p["roles"])
        self.assertNotIn("None",  p["roles"])
        # Legacy combined keys must not appear.
        self.assertNotIn("ceo_cfo",    p["roles"])
        self.assertNotIn("other_exec", p["roles"])
        self.assertNotIn("ned",        p["roles"])

    def test_09_sector_payload_emits_all_sectors_with_benchmark(self):
        self._run_exporter()
        p = json.loads(
            (self.out_dir / "performance_sector.json").read_text()
        )
        self.assertIn("sectors", p)
        # Synthetic: 3 distinct sectors seeded.
        self.assertEqual(
            set(p["sectors"].keys()),
            {"Materials", "Health Care", "Energy"},
        )
        # Each sector cohort carries its benchmark_symbol.
        self.assertEqual(
            p["sectors"]["Materials"]["benchmark_symbol"],
            "^FTNMX1770",
        )

    def test_10_summary_includes_new_counts(self):
        """run() summary dict gains n_buckets / n_roles / n_sectors keys
        per Sprint 5 acceptance."""
        summary = self._run_exporter()
        self.assertIn("n_buckets", summary)
        self.assertIn("n_roles",   summary)
        self.assertIn("n_sectors", summary)


class TestFileSizesWithinBound(DualEmitTestBase):
    """Sprint 5 acceptance: each performance_*.json should be < 200 KB on
    realistic data. Synthetic data is tiny but the assertion guards against
    runaway payloads (e.g. accidentally embedding the full CSV)."""

    def test_11_all_outputs_under_size_cap(self):
        self._run_exporter()
        for name in ("signals.json", "dealings.json",
                     "performance_bucket.json", "performance_role.json",
                     "performance_sector.json"):
            size = (self.out_dir / name).stat().st_size
            self.assertLess(
                size, 200 * 1024,
                f"{name} is {size} bytes — over 200KB cap on synthetic data",
            )


class TestAtomicWriteUsed(DualEmitTestBase):
    """All five outputs must go through _atomic_write_json (tmp + replace).
    We check the behavioural symptom: after run() returns, no .tmp files
    are left behind."""

    def test_12_no_leftover_tmp_files(self):
        self._run_exporter()
        leftover = list(self.out_dir.glob("*.tmp"))
        self.assertEqual(
            leftover, [],
            f"Unexpected leftover .tmp files: {leftover}",
        )


class TestCompaniesIndex(DualEmitTestBase):
    """B-059: signals.json must contain a companies_index list for the
    search box, with ticker / company / url keys per entry."""

    def test_13_companies_index_present_in_signals(self):
        """companies_index key must exist at top level of signals.json."""
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        self.assertIn("companies_index", signals,
                      "companies_index missing from signals.json (B-059)")

    def test_14_companies_index_has_expected_shape(self):
        """Each entry must carry ticker, company, and url strings."""
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        index = signals["companies_index"]
        self.assertIsInstance(index, list)
        self.assertGreater(len(index), 0,
                           "companies_index is empty — synthetic DB has 3 tickers")
        for entry in index:
            for field in ("ticker", "company", "url"):
                self.assertIn(field, entry,
                              f"companies_index entry missing '{field}': {entry}")
            # B-184: url now points at the dynamic company template
            # (company.html?ticker=…) instead of a static companies/*.html page.
            self.assertRegex(entry["url"],
                             r"^company\.html\?ticker=[A-Za-z0-9._%-]+$",
                             f"url format unexpected: {entry['url']}")

    def test_15_companies_index_sorted_by_ticker(self):
        """Entries must be ordered alphabetically by ticker (per query ORDER BY)."""
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        index = signals["companies_index"]
        tickers = [e["ticker"] for e in index]
        self.assertEqual(tickers, sorted(tickers),
                         f"companies_index not sorted by ticker: {tickers}")

    def test_16_companies_index_contains_seeded_tickers(self):
        """The three seeded tickers (AAL, AZN, BP) must appear in the index."""
        self._run_exporter()
        signals = json.loads((self.out_dir / "signals.json").read_text())
        ticker_set = {e["ticker"] for e in signals["companies_index"]}
        for expected in ("AAL", "AZN", "BP"):
            self.assertIn(expected, ticker_set,
                          f"Seeded ticker {expected} missing from companies_index")


if __name__ == "__main__":
    unittest.main(verbosity=2)
