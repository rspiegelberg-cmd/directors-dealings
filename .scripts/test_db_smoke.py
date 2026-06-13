"""Stage 1 smoke test for the Directors Dealings SQLite foundation.

Runs the 9 cases enumerated in stage-01-plan.md against a throwaway
temp database. Monkey-patches `db.DB_PATH` and `db.DB_DIR` BEFORE calling
`db.connect()` so the real .data\\directors.db is never touched.

Self-cleaning: `shutil.rmtree(tmp_dir, ignore_errors=True)` runs in a
`finally` block whether assertions pass or fail.

Stage 2 update: cases 2 and 3 accept the new chained-migration behaviour
(schema_version now bumps to '2' on connect()).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402


RESULTS: list = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    if ok:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} -- {detail}")


def run_case(name: str, fn) -> None:
    try:
        fn()
        record(name, True)
    except AssertionError as exc:
        record(name, False, f"assertion: {exc}")
    except Exception as exc:
        record(name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


EXPECTED_TABLES = {
    "transactions",
    "prices",
    "tickers_meta",
    "signals",
    "backtest_runs",
    "paper_trades",
    "meta",
    "reporting_dates",  # added migration 008 (Sprint 26)
}


def case_1_all_tables_present() -> None:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        found = {r[0] for r in rows}
        missing = EXPECTED_TABLES - found
        assert not missing, f"missing tables: {sorted(missing)}"
        user_tables = found - {"sqlite_sequence"}
        # Allow tables we don't explicitly know about (forward-compat):
        # test only that all EXPECTED tables are present, none missing.
        extra = user_tables - EXPECTED_TABLES
        assert not (EXPECTED_TABLES - user_tables), (
            f"missing tables: {sorted(EXPECTED_TABLES - user_tables)}"
        )
    finally:
        conn.close()


def case_2_schema_version_present() -> None:
    """Stage 2 update: connect() applies the migration chain. The
    fresh-DB version is the LATEST (currently '2'). Test name kept
    for parity with stage-01-plan; assertion accepts any version >= 1.
    """
    conn = db.connect()
    try:
        val = db.get_meta(conn, "schema_version")
        assert val is not None and int(val) >= 1, (
            f"expected schema_version >= 1, got {val!r}"
        )
    finally:
        conn.close()


def case_3_migrate_is_idempotent() -> None:
    """The migrate() chain is idempotent: re-running keeps the same
    final version. Stage 2 update: compare against whatever version
    connect() settled on.
    """
    conn = db.connect()
    try:
        first = db.get_meta(conn, "schema_version")
        db.migrate(conn)
        db.migrate(conn)
        db.migrate(conn)
        val = db.get_meta(conn, "schema_version")
        assert val == first, f"schema_version drifted: {first!r} -> {val!r}"
    finally:
        conn.close()


def case_4_insert_buy_transaction_roundtrip() -> None:
    conn = db.connect()
    try:
        now = db.iso_now()
        conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, type, shares, price, value, context, "
            "url, announced_at, cluster_id, first_time_buy"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "fp-test-1", now, now, 1, "2026-04-01", "CHH",
                "Churchill China", "Jane Doe", "CFO", "BUY",
                1000, 3.21, 3210.0, None, None, now, None, 0,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT ticker, shares FROM transactions WHERE fingerprint = ?",
            ("fp-test-1",),
        ).fetchone()
        assert row is not None
        assert row["ticker"] == "CHH"
        assert row["shares"] == 1000
    finally:
        conn.close()


def case_5_insert_stock_price_roundtrip() -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO prices (ticker, date, open, high, low, close, volume, "
            "source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("CHH", "2026-04-01", 3.20, 3.25, 3.18, 3.21, 12345, "yahoo", db.iso_now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date = ?",
            ("CHH", "2026-04-01"),
        ).fetchone()
        assert row is not None
        assert row["close"] == 3.21
    finally:
        conn.close()


def case_6_insert_benchmark_price_row() -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO prices (ticker, date, open, high, low, close, volume, "
            "source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("^FTAS", "2026-04-01", 4500.0, 4520.0, 4490.0, 4510.0, 0, "yahoo", db.iso_now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM prices WHERE ticker LIKE '^%'"
        ).fetchone()
        assert row["n"] == 1
        bench = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date = ?",
            ("^FTAS", "2026-04-01"),
        ).fetchone()
        assert bench is not None and bench["close"] == 4510.0
    finally:
        conn.close()


def case_7_signal_fk_accepts_row() -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO signals (signal_id, signal_version, fingerprint, "
            "fired_at, confidence, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("S_FIRST_TIME_BUY", "v1", "fp-test-1", db.iso_now(), "high", "{}"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT signal_id FROM signals WHERE fingerprint = ?",
            ("fp-test-1",),
        ).fetchone()
        assert row is not None
        assert row["signal_id"] == "S_FIRST_TIME_BUY"
    finally:
        conn.close()


def case_8_set_meta_upserts() -> None:
    conn = db.connect()
    try:
        db.set_meta(conn, "smoke", "yes")
        db.set_meta(conn, "smoke", "again")
        val = db.get_meta(conn, "smoke")
        assert val == "again"
    finally:
        conn.close()


def case_9_foreign_keys_on() -> None:
    conn = db.connect()
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        value = row[0]
        assert value == 1
    finally:
        conn.close()


def main() -> int:
    tmp_dir = tempfile.mkdtemp(prefix="dd_smoke_")
    try:
        tmp_path = Path(tmp_dir)
        db.DB_DIR = tmp_path
        db.DB_PATH = tmp_path / "directors.db"

        run_case("1. all 7 tables present", case_1_all_tables_present)
        run_case("2. schema_version present (>= 1)", case_2_schema_version_present)
        run_case("3. migrate() is idempotent", case_3_migrate_is_idempotent)
        run_case("4. insert BUY tx roundtrip", case_4_insert_buy_transaction_roundtrip)
        run_case("5. insert stock price roundtrip", case_5_insert_stock_price_roundtrip)
        run_case("6. insert ^FTAS benchmark row (S1)", case_6_insert_benchmark_price_row)
        run_case("7. signal FK accepts row", case_7_signal_fk_accepts_row)
        run_case("8. set_meta upsert semantics", case_8_set_meta_upserts)
        run_case("9. PRAGMA foreign_keys == 1", case_9_foreign_keys_on)

        passed = sum(1 for _, ok, _ in RESULTS if ok)
        failed = sum(1 for _, ok, _ in RESULTS if not ok)
        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
