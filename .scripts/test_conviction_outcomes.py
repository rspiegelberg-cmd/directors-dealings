"""Tests for conviction_outcomes.py — the score -> forward-CAR join (B-171 P3).

Runs against an in-memory SQLite conviction_scores table plus a synthetic
backtest results CSV written to a tempfile (never the real .data/ files), per
the CLAUDE.md Zone-A rules.

Regression-guards the EXACT horizon column names: T+21 == car_t30, T+90 ==
car_t90 (there is NO car_t21 column).
"""
from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import conviction_outcomes as co  # noqa: E402


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE conviction_scores (
            fingerprint TEXT, window_end TEXT, score REAL, band TEXT,
            f1_who REAL, f2_buy_size REAL, f3_company_size REAL,
            f4_earnings_timing REAL, f5_past_performance REAL, f6_sector_mult REAL,
            rank_in_window INTEGER, surfaced INTEGER, earnings_dropped INTEGER,
            inputs_missing TEXT,
            PRIMARY KEY (fingerprint, window_end)
        );
        """
    )
    return conn


def _add_score(conn, fp, score, *, band="High", rank=1, surfaced=1):
    conn.execute(
        "INSERT INTO conviction_scores (fingerprint, window_end, score, band, "
        "f1_who, f2_buy_size, f3_company_size, f4_earnings_timing, "
        "f5_past_performance, f6_sector_mult, rank_in_window, surfaced, "
        "earnings_dropped, inputs_missing) VALUES "
        "(?, '2026-06-15', ?, ?, 1.0, 0.8, 1.0, 0.5, 0.5, 1.0, ?, ?, 0, '[]')",
        (fp, score, band, rank, surfaced),
    )


def _write_csv(tmpdir, header, rows):
    p = Path(tmpdir) / "_backtest_results.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return p


# A realistic header subset including the load-bearing columns.
_HEADER = [
    "fingerprint", "ticker", "car_t1", "car_t30", "car_t90",
    "net_car_t1", "net_car_t30", "net_car_t90",
]


class TestHorizonColumnMapping(unittest.TestCase):
    def test_t21_maps_to_car_t30(self):
        self.assertEqual(co.CAR_T21_COL, "car_t30")
        self.assertEqual(co.NET_CAR_T21_COL, "net_car_t30")

    def test_t90_maps_to_car_t90(self):
        self.assertEqual(co.CAR_T90_COL, "car_t90")
        self.assertEqual(co.NET_CAR_T90_COL, "net_car_t90")

    def test_missing_car_t30_raises(self):
        with tempfile.TemporaryDirectory() as td:
            # Header deliberately omits car_t30 -> must raise (no silent blanks).
            bad_header = ["fingerprint", "car_t1", "car_t90"]
            p = _write_csv(td, bad_header, [["fp1", "0.01", "0.05"]])
            with self.assertRaises(KeyError):
                co.load_backtest_cars(p)


class TestJoin(unittest.TestCase):
    def test_tracked_buy_gets_car(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_csv(td, _HEADER, [
                ["fp1", "ABC", "0.01", "0.06", "0.12",
                 "0.005", "0.05", "0.11"],
            ])
            conn = _mk_conn()
            _add_score(conn, "fp1", 80.0)
            conn.commit()
            out = co.build_outcomes(conn, csv_path=p)
            self.assertEqual(len(out), 1)
            o = out[0]
            self.assertTrue(o["tracked"])
            self.assertAlmostEqual(o["car_t21"], 0.06)   # from car_t30 column
            self.assertAlmostEqual(o["car_t90"], 0.12)
            self.assertAlmostEqual(o["net_car_t21"], 0.05)
            self.assertAlmostEqual(o["net_car_t90"], 0.11)

    def test_untracked_buy_kept_with_null_car(self):
        with tempfile.TemporaryDirectory() as td:
            # CSV has fp1 only; fp2 (scored but no signal fired) is absent.
            p = _write_csv(td, _HEADER, [
                ["fp1", "ABC", "0.01", "0.06", "0.12",
                 "0.005", "0.05", "0.11"],
            ])
            conn = _mk_conn()
            _add_score(conn, "fp1", 80.0, rank=1, surfaced=1)
            _add_score(conn, "fp2", 30.0, band="Low", rank=2, surfaced=1)
            conn.commit()
            out = co.build_outcomes(conn, csv_path=p)
            by_fp = {o["fingerprint"]: o for o in out}
            self.assertEqual(len(out), 2)
            # fp2 untracked but NOT dropped.
            self.assertFalse(by_fp["fp2"]["tracked"])
            self.assertIsNone(by_fp["fp2"]["car_t21"])
            self.assertIsNone(by_fp["fp2"]["car_t90"])
            self.assertEqual(by_fp["fp2"]["band"], "Low")

    def test_blank_car_cell_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_csv(td, _HEADER, [
                ["fp1", "ABC", "0.01", "", "0.12", "", "", ""],
            ])
            conn = _mk_conn()
            _add_score(conn, "fp1", 80.0)
            conn.commit()
            out = co.build_outcomes(conn, csv_path=p)
            o = out[0]
            self.assertTrue(o["tracked"])     # row present, just blank t30
            self.assertIsNone(o["car_t21"])   # blank cell -> None
            self.assertAlmostEqual(o["car_t90"], 0.12)

    def test_subscores_carried_through(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write_csv(td, _HEADER, [])  # empty -> all untracked
            conn = _mk_conn()
            _add_score(conn, "fp1", 80.0)
            conn.commit()
            out = co.build_outcomes(conn, csv_path=p)
            sub = out[0]["subscores"]
            self.assertAlmostEqual(sub["f1_who"], 1.0)
            self.assertAlmostEqual(sub["f6_sector_mult"], 1.0)

    def test_missing_csv_all_untracked(self):
        conn = _mk_conn()
        _add_score(conn, "fp1", 80.0)
        conn.commit()
        out = co.build_outcomes(conn, csv_path=Path("/no/such/file.csv"))
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["tracked"])


if __name__ == "__main__":
    unittest.main()
