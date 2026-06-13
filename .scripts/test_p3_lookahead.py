"""Stage 4 -- THE non-negotiable walk-forward / lookahead-bias gate.

Spec 05 P3-6: a signal evaluator must NEVER return data that depends on
a row in `transactions` or `prices` dated strictly later than the
walk-forward upper bound `as_of`.

This test wraps the sqlite3.Connection so that:

  * Every executed SQL string is captured.
  * Every result row produced by an evaluator's query is scanned for
    columns named 'date' or 'announced_at' or 'fired_at'. If any such
    value > as_of, the test fails hard with a stack trace.

Method:
  1. Build a synthetic SQLite store in tmpdir with schema applied.
  2. Seed `tickers_meta`, `prices`, `transactions`. The seed contains
     a TRAP row: a transaction (or price) at as_of + 5 days that
     MUST NOT appear in any evaluator's intermediate results.
  3. For each of the 7 signal evaluators, call `evaluate(tx, conn,
     as_of=as_of)` where `tx.announced_at == as_of`. Assert no
     peek occurred.
  4. Also call `detect_clusters.detect(conn, as_of=as_of)` with the
     trap row hidden and visible; assert correct behaviour in both.

Exit code 0 means every signal respected `as_of`. Exit code 1 means
at least one peeked at the future.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import detect_clusters  # noqa: E402
import signals as signals_pkg  # noqa: E402


RESULTS: list = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    if ok:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} -- {detail}")


# ---------------------------------------------------------------------------
# Lookahead-detecting connection wrapper
# ---------------------------------------------------------------------------

class LookaheadGuard:
    """Wraps a sqlite3.Connection and scans every result for future dates.

    The guard knows the `as_of` upper bound and the set of column names
    that carry visibility-relevant dates. After every fetchall/fetchone
    invocation (via a wrapped Cursor) we inspect the rows.
    """

    DATE_COLUMNS = {"date", "announced_at", "fired_at", "first_seen", "last_seen"}

    def __init__(self, conn: sqlite3.Connection, as_of: str):
        self._conn = conn
        self.as_of = as_of[:10]
        self.peeks: list[dict] = []

    def execute(self, sql: str, params=()):
        cur = self._conn.execute(sql, params)
        return self._wrap_cursor(cur, sql)

    def cursor(self):
        return self._wrap_cursor(self._conn.cursor(), None)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    def _wrap_cursor(self, cur, sql_for_trace):
        return GuardedCursor(cur, self, sql_for_trace)

    # Allow row_factory passthrough for evaluators that need it.
    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


class GuardedCursor:
    def __init__(self, cur, guard: LookaheadGuard, sql_for_trace):
        self._cur = cur
        self._guard = guard
        self._sql_for_trace = sql_for_trace

    def execute(self, sql, params=()):
        self._sql_for_trace = sql
        self._cur.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is not None:
            self._inspect([row])
        return row

    def fetchall(self):
        rows = self._cur.fetchall()
        self._inspect(rows)
        return rows

    def __iter__(self):
        return iter(self._cur)

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _inspect(self, rows):
        if not rows:
            return
        try:
            keys = rows[0].keys()
        except Exception:  # noqa: BLE001
            return
        date_keys = [k for k in keys if k in LookaheadGuard.DATE_COLUMNS]
        if not date_keys:
            return
        for r in rows:
            for k in date_keys:
                v = r[k]
                if isinstance(v, str) and v[:10] > self._guard.as_of:
                    self._guard.peeks.append({
                        "as_of": self._guard.as_of,
                        "column": k,
                        "value": v,
                        "sql": (self._sql_for_trace or "")[:200],
                    })


# ---------------------------------------------------------------------------
# Synthetic store
# ---------------------------------------------------------------------------

def _seed(conn):
    """Seed a synthetic DB. Returns the `as_of` upper bound (2026-01-31)."""
    conn.execute("INSERT INTO tickers_meta (ticker, sector, benchmark_symbol, "
                 "is_aim, updated_at) VALUES (?, ?, ?, ?, ?)",
                 ("XYZ", "Industrials", "^FTAS", 0, db.iso_now()))

    # Prices for XYZ: 2026-01-01 .. 2026-01-31 + a TRAP at 2026-02-05.
    fetched = db.iso_now()
    for i in range(31):
        day = f"2026-01-{i+1:02d}"
        conn.execute("INSERT INTO prices (ticker, date, close, source, fetched_at) "
                     "VALUES (?, ?, ?, 'yahoo', ?)",
                     ("XYZ", day, 100.0 + i, fetched))
        conn.execute("INSERT INTO prices (ticker, date, close, source, fetched_at) "
                     "VALUES (?, ?, ?, 'yahoo', ?)",
                     ("^FTAS", day, 1000.0 + i, fetched))
    # TRAP rows after as_of:
    conn.execute("INSERT INTO prices (ticker, date, close, source, fetched_at) "
                 "VALUES (?, ?, ?, 'yahoo', ?)",
                 ("XYZ", "2026-02-05", 999.0, fetched))
    conn.execute("INSERT INTO prices (ticker, date, close, source, fetched_at) "
                 "VALUES (?, ?, ?, 'yahoo', ?)",
                 ("^FTAS", "2026-02-05", 1500.0, fetched))

    # First buy at announced_at=2026-01-31 by CEO Alice for £200k.
    conn.execute("INSERT INTO transactions "
                 "(fingerprint, first_seen, last_seen, seen_count, "
                 " date, ticker, company, director, role, type, shares, "
                 " price, value, announced_at) "
                 "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 'BUY', ?, ?, ?, ?)",
                 ("fp_alice_d1", fetched, fetched,
                  "2026-01-31", "XYZ", "XYZ plc", "Alice CEO",
                  "Chief Executive Officer", 1000, 200.0, 200_000.0,
                  "2026-01-31"))
    # Second BUY at the TRAP date (must NOT appear when as_of=2026-01-31).
    conn.execute("INSERT INTO transactions "
                 "(fingerprint, first_seen, last_seen, seen_count, "
                 " date, ticker, company, director, role, type, shares, "
                 " price, value, announced_at) "
                 "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 'BUY', ?, ?, ?, ?)",
                 ("fp_alice_trap", fetched, fetched,
                  "2026-02-05", "XYZ", "XYZ plc", "Alice CEO",
                  "Chief Executive Officer", 500, 200.0, 100_000.0,
                  "2026-02-05"))
    # Third BUY by Bob CFO at 2026-01-15 -- for S1 cluster.
    conn.execute("INSERT INTO transactions "
                 "(fingerprint, first_seen, last_seen, seen_count, "
                 " date, ticker, company, director, role, type, shares, "
                 " price, value, announced_at) "
                 "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 'BUY', ?, ?, ?, ?)",
                 ("fp_bob_d1", fetched, fetched,
                  "2026-01-15", "XYZ", "XYZ plc", "Bob CFO",
                  "Chief Financial Officer", 1000, 150.0, 150_000.0,
                  "2026-01-15"))
    conn.commit()
    return "2026-01-31"


# ---------------------------------------------------------------------------
# The actual tests
# ---------------------------------------------------------------------------

def _per_signal_peek_test(conn, as_of: str) -> dict:
    """For each signal, wrap the conn and call evaluate(). Return peeks per signal."""
    rows = conn.execute(
        "SELECT * FROM transactions WHERE announced_at = ?",
        (as_of,),
    ).fetchall()
    assert rows, "expected at least one tx at as_of for the test"
    tx = rows[0]  # Alice's tx is the firing candidate

    # detect_clusters must run first to populate cluster_id for S1.
    detect_clusters.detect(conn, as_of)

    # Re-fetch tx after detect_clusters (cluster_id may have been set).
    tx = conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?",
        (tx["fingerprint"],),
    ).fetchone()

    results = {}
    for sid, ver, mod in signals_pkg.iter_signals():
        guard = LookaheadGuard(conn, as_of=as_of)
        try:
            mod.evaluate(tx, guard, as_of=as_of)
        except Exception as exc:  # noqa: BLE001
            results[sid] = {"peeks": [], "exception": str(exc)}
            continue
        results[sid] = {"peeks": list(guard.peeks), "exception": None}
    return results


def case_no_peek_per_signal(conn, as_of):
    results = _per_signal_peek_test(conn, as_of)
    for sid, info in results.items():
        if info["exception"]:
            raise AssertionError(f"{sid} raised: {info['exception']}")
        if info["peeks"]:
            sample = info["peeks"][:3]
            raise AssertionError(
                f"{sid} peeked past as_of={as_of}: {sample}"
            )


def case_detect_clusters_walk_forward(conn, as_of):
    """detect_clusters with as_of=2026-01-31 must NOT include the trap tx."""
    detect_clusters.detect(conn, as_of)
    row = conn.execute(
        "SELECT cluster_id FROM transactions WHERE fingerprint = ?",
        ("fp_alice_trap",),
    ).fetchone()
    assert row["cluster_id"] is None, \
        f"trap tx got cluster_id {row['cluster_id']!r}; expected None"

    # Sanity: Alice and Bob form a cluster as-of 2026-01-31.
    alice = conn.execute(
        "SELECT cluster_id FROM transactions WHERE fingerprint = ?",
        ("fp_alice_d1",),
    ).fetchone()
    bob = conn.execute(
        "SELECT cluster_id FROM transactions WHERE fingerprint = ?",
        ("fp_bob_d1",),
    ).fetchone()
    assert alice["cluster_id"] is not None, "Alice should be in a cluster"
    assert alice["cluster_id"] == bob["cluster_id"], \
        "Alice & Bob should share cluster_id"


def case_t0_walk_forward(conn, as_of):
    """T0's walk-forward gate.

    T0 joins `signals` to `transactions` and gates on
    `transactions.announced_at <= as_of`. This means: if we evaluate T0
    for a tx whose announced_at is AFTER as_of, T0 must not fire even
    if signals rows exist for that fingerprint.

    Here we use the trap fingerprint (announced_at = 2026-02-05, well
    past as_of = 2026-01-31). We seed S1 + T1 signal rows for the trap
    fingerprint, then evaluate T0 with as_of = 2026-01-31. T0 must NOT
    fire because the underlying tx is invisible at as_of.

    Then we re-evaluate T0 for fp_alice_d1 (announced_at = 2026-01-31)
    after seeding its sub-signals. T0 MUST fire.
    """
    trap_fp = "fp_alice_trap"
    fired_at = "2026-01-31"   # consistent date-only format with as_of
    conn.execute(
        "INSERT OR REPLACE INTO signals "
        "(signal_id, signal_version, fingerprint, fired_at, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1_cluster_buy", "1.0.0", trap_fp, fired_at, "med"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO signals "
        "(signal_id, signal_version, fingerprint, fired_at, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("t1b_cfo_buy", "1.0.0", trap_fp, fired_at, "high"),
    )
    conn.commit()

    # Build a synthetic tx Row for the trap. Note we pass tx INTO
    # evaluate() but evaluate() looks up the fingerprint in the JOIN
    # against `transactions` so we don't need a Row object that lies.
    tx_trap = conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?",
        (trap_fp,),
    ).fetchone()
    guard = LookaheadGuard(conn, as_of=as_of)
    t0_mod = signals_pkg.REGISTRY["t0_cluster_combo"]
    result_trap = t0_mod.evaluate(tx_trap, guard, as_of=as_of)
    assert not guard.peeks, f"T0 peeked: {guard.peeks[:3]}"
    assert result_trap is None, (
        "T0 must NOT fire for a tx whose announced_at > as_of, "
        f"even though signals rows exist (got {result_trap})"
    )

    # Now seed sub-signals for the in-window fingerprint and verify T0
    # fires correctly.
    in_window_fp = "fp_alice_d1"
    conn.execute(
        "INSERT OR REPLACE INTO signals "
        "(signal_id, signal_version, fingerprint, fired_at, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1_cluster_buy", "1.0.0", in_window_fp, fired_at, "med"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO signals "
        "(signal_id, signal_version, fingerprint, fired_at, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("t1b_cfo_buy", "1.0.0", in_window_fp, fired_at, "high"),
    )
    conn.commit()
    tx_in = conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?",
        (in_window_fp,),
    ).fetchone()
    guard2 = LookaheadGuard(conn, as_of=as_of)
    result_in = t0_mod.evaluate(tx_in, guard2, as_of=as_of)
    assert not guard2.peeks, f"T0 peeked on second pass: {guard2.peeks[:3]}"
    assert result_in is not None, (
        "T0 must fire when sub-signals exist AND announced_at <= as_of"
    )

    # Cleanup so other cases aren't polluted.
    conn.execute("DELETE FROM signals WHERE fingerprint IN (?, ?)",
                 (trap_fp, in_window_fp))
    conn.commit()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="dd_stage4_lookahead_"))
    real_db_path = db.DB_PATH
    real_db_dir = db.DB_DIR
    try:
        db.DB_DIR = tmp_dir
        db.DB_PATH = tmp_dir / "directors.db"
        conn = db.connect()
        try:
            as_of = _seed(conn)
            try:
                case_no_peek_per_signal(conn, as_of)
                record("per-signal no-peek", True)
            except AssertionError as exc:
                record("per-signal no-peek", False, str(exc))

            try:
                case_detect_clusters_walk_forward(conn, as_of)
                record("detect_clusters walk-forward", True)
            except AssertionError as exc:
                record("detect_clusters walk-forward", False, str(exc))

            try:
                case_t0_walk_forward(conn, as_of)
                record("t0 walk-forward gate", True)
            except AssertionError as exc:
                record("t0 walk-forward gate", False, str(exc))
            except Exception as exc:  # noqa: BLE001
                record("t0 walk-forward gate", False,
                       f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}")
        finally:
            conn.close()
    finally:
        db.DB_DIR = real_db_dir
        db.DB_PATH = real_db_path
        shutil.rmtree(tmp_dir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
