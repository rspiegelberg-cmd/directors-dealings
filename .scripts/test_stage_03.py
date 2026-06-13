"""Stage 3 smoke + unit + integration tests.

19 cases. Self-cleaning: monkey-patches `db.DB_PATH` and `db.DB_DIR`
to a tempdir, and rmtree's it in finally. Never touches the real
`.data/directors.db`.

Mocks `urllib.request.urlopen` for ALL Yahoo calls -- zero network
during tests.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import fetch_prices  # noqa: E402
import fetch_sectors  # noqa: E402
import backfill_prices  # noqa: E402
import backfill_benchmarks  # noqa: E402


RESULTS: list = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if not ok else ""))


def run_case(name, fn) -> None:
    try:
        fn()
        record(name, True)
    except AssertionError as exc:
        record(name, False, f"assertion: {exc}")
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")


# ---------------------------------------------------------------------------
# Canned Yahoo response helpers
# ---------------------------------------------------------------------------

def _canned_chart_json(symbol, currency="GBP", timestamps=None, closes=None, volumes=None):
    """Build a Yahoo chart-style JSON payload matching the empirical shape."""
    timestamps = timestamps if timestamps is not None else [
        1735862400, 1735948800, 1736035200,
    ]
    closes = closes if closes is not None else [100.0, 101.5, 99.75]
    volumes = volumes if volumes is not None else [1000, 1100, 1200]
    payload = {
        "chart": {
            "result": [{
                "meta": {"currency": currency, "symbol": symbol},
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open": list(closes),
                        "high": list(closes),
                        "low": list(closes),
                        "close": list(closes),
                        "volume": list(volumes),
                    }],
                    "adjclose": [{"adjclose": list(closes)}],
                },
            }],
            "error": None,
        }
    }
    return json.dumps(payload).encode("utf-8")


class _FakeResp:
    """Minimal stand-in for urllib's response object."""
    def __init__(self, body, status=200, content_encoding=""):
        self._body = body
        self.status = status
        self.headers = {"Content-Encoding": content_encoding}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_status(code):
    def _opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, f"HTTP {code}", {}, None)
    return _opener


def _fake_urlopen_sequence(seq):
    """Pop one fake from the front of the list each call."""
    box = list(seq)

    def _opener(req, timeout=None):
        item = box.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)
    return _opener


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def case_01_fetch_basic_ok():
    body = _canned_chart_json("BARC.L", currency="GBP",
                              timestamps=[1735862400], closes=[214.20], volumes=[5000])
    with mock.patch("fetch_prices.urllib.request.urlopen", _fake_urlopen_sequence([body])):
        with mock.patch("fetch_prices.time.sleep"):
            r = fetch_prices.fetch("BARC", "2025-01-01", "2025-01-31",
                                   rate_limit=0, use_cache=False)
    assert r.status == "ok", r
    assert r.yahoo_symbol == "BARC.L", r.yahoo_symbol
    assert len(r.rows) == 1, r.rows
    assert r.rows[0]["close"] == 214.20
    assert r.cache_hit is False
    assert r.network_calls == 1


def case_02_cache_hit_recent():
    body = _canned_chart_json("CACHE1.L", currency="GBP",
                              timestamps=[1735862400, 1735948800],
                              closes=[214.20, 215.30], volumes=[5000, 6000])
    seq = _fake_urlopen_sequence([body])
    with mock.patch("fetch_prices.urllib.request.urlopen", seq), \
         mock.patch("fetch_prices.time.sleep"):
        r1 = fetch_prices.fetch("CACHE1", "2025-01-01", "2025-01-05",
                                rate_limit=0, use_cache=True,
                                now_epoch=time.time())
        r2 = fetch_prices.fetch("CACHE1", "2025-01-01", "2025-01-05",
                                rate_limit=0, use_cache=True,
                                now_epoch=time.time())
    assert r1.status == "ok", f"r1 status={r1.status}"
    assert r1.cache_hit is False, f"r1 cache_hit={r1.cache_hit}"
    assert r1.network_calls == 1, f"r1 network_calls={r1.network_calls}"
    assert r2.cache_hit is True, f"r2 cache_hit={r2.cache_hit}"
    assert r2.network_calls == 0, f"r2 network_calls={r2.network_calls}"


def case_03_cache_window_extended():
    body1 = _canned_chart_json("CACHE2.L", timestamps=[1735862400],
                               closes=[100.0], volumes=[1])
    body2 = _canned_chart_json("CACHE2.L", timestamps=[1735862400, 1736035200],
                               closes=[100.0, 101.0], volumes=[1, 2])
    seq = _fake_urlopen_sequence([body1, body2])
    with mock.patch("fetch_prices.urllib.request.urlopen", seq), \
         mock.patch("fetch_prices.time.sleep"):
        fetch_prices.fetch("CACHE2", "2025-01-03", "2025-01-03",
                           rate_limit=0, use_cache=True, now_epoch=time.time())
        r2 = fetch_prices.fetch("CACHE2", "2025-01-01", "2025-01-31",
                                rate_limit=0, use_cache=True, now_epoch=time.time())
    assert r2.cache_hit is False
    assert r2.network_calls == 1


def case_04_cache_ttl_expired():
    body = _canned_chart_json("CACHE3.L", timestamps=[1735862400],
                              closes=[1.0], volumes=[1])
    now = time.time()
    seq = _fake_urlopen_sequence([body, body])
    with mock.patch("fetch_prices.urllib.request.urlopen", seq), \
         mock.patch("fetch_prices.time.sleep"):
        r1 = fetch_prices.fetch("CACHE3", "2025-01-03", "2025-01-03",
                                rate_limit=0, use_cache=True, now_epoch=now)
        r2 = fetch_prices.fetch("CACHE3", "2025-01-03", "2025-01-03",
                                rate_limit=0, use_cache=True,
                                now_epoch=now + 21 * 3600)
    assert r1.cache_hit is False
    assert r2.cache_hit is False


def case_05_404_delisted():
    with mock.patch("fetch_prices.urllib.request.urlopen", _fake_urlopen_status(404)), \
         mock.patch("fetch_prices.time.sleep"):
        r = fetch_prices.fetch("ZZDEAD", "2025-01-01", "2025-01-31",
                               rate_limit=0, use_cache=False)
    assert r.status == "delisted", r
    assert r.rows == []


def case_06_429_then_ok():
    body = _canned_chart_json("RTRY.L", timestamps=[1735862400],
                              closes=[100.0], volumes=[1])
    err1 = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    err2 = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    seq = _fake_urlopen_sequence([err1, err2, body])
    sleep_calls = []
    with mock.patch("fetch_prices.urllib.request.urlopen", seq), \
         mock.patch("fetch_prices.time.sleep", side_effect=sleep_calls.append):
        r = fetch_prices.fetch("RTRY", "2025-01-01", "2025-01-31",
                               rate_limit=0, use_cache=False)
    assert r.status == "ok", r
    assert 30 in sleep_calls, sleep_calls
    assert 60 in sleep_calls, sleep_calls


def case_07_gbp_pence_normalised():
    body = _canned_chart_json("PENCE.L", currency="GBp",
                              timestamps=[1735862400], closes=[21420.0], volumes=[1])
    with mock.patch("fetch_prices.urllib.request.urlopen",
                    _fake_urlopen_sequence([body])), \
         mock.patch("fetch_prices.time.sleep"):
        r = fetch_prices.fetch("PENCE", "2025-01-01", "2025-01-31",
                               rate_limit=0, use_cache=False)
    assert r.status == "ok"
    assert abs(r.rows[0]["close"] - 214.20) < 1e-9, r.rows[0]["close"]


def case_08_gbp_pounds_passthrough():
    body = _canned_chart_json("POUND.L", currency="GBP",
                              timestamps=[1735862400], closes=[214.20], volumes=[1])
    with mock.patch("fetch_prices.urllib.request.urlopen",
                    _fake_urlopen_sequence([body])), \
         mock.patch("fetch_prices.time.sleep"):
        r = fetch_prices.fetch("POUND", "2025-01-01", "2025-01-31",
                               rate_limit=0, use_cache=False)
    assert abs(r.rows[0]["close"] - 214.20) < 1e-9


def case_09_resolve_barc_financials():
    sector_map = {"BARC": {"sector": "Financials",
                            "benchmark_symbol": "^FTAS", "is_aim": 0}}
    bench = {"_default": "^FTAS", "Financials": "^FTAS"}
    meta = fetch_sectors.resolve("BARC", sector_map, bench)
    assert meta.sector == "Financials"
    assert meta.benchmark_symbol == "^FTAS"
    assert meta.is_aim == 0


def case_10_resolve_unknown_fallback():
    meta = fetch_sectors.resolve("UNKNOWN", {}, {"_default": "^FTAS"})
    assert meta.sector is None
    assert meta.benchmark_symbol == "^FTAS"
    assert meta.is_aim == 0


def case_11_resolve_aim_flagged():
    sector_map = {"FEVR": {"sector": "Consumer Staples",
                            "benchmark_symbol": "^FTAS", "is_aim": 1}}
    meta = fetch_sectors.resolve("FEVR", sector_map, {"_default": "^FTAS"})
    assert meta.is_aim == 1


def case_12_benchmark_backfill_end_to_end(conn):
    body = _canned_chart_json("^FTAS", timestamps=[1735862400, 1735948800],
                              closes=[4600.0, 4610.0], volumes=[1, 2])
    seq = _fake_urlopen_sequence([body])
    with mock.patch("fetch_prices.urllib.request.urlopen", seq), \
         mock.patch("fetch_prices.time.sleep"):
        summary = backfill_benchmarks.run(
            date_from="2025-01-01", date_to="2025-01-31",
            only_symbol="^FTAS", rate_limit=0, verbose=False,
        )
    assert summary["ok"] == 1, summary
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM prices WHERE ticker = '^FTAS'"
    ).fetchone()["n"]
    assert n >= 2, f"expected at least 2 rows for ^FTAS, got {n}"


def case_13_backfill_prices_idempotent(conn):
    body = _canned_chart_json("TST.L", timestamps=[1735862400, 1735948800],
                              closes=[10.0, 11.0], volumes=[1, 2])
    conn.execute(
        "INSERT OR IGNORE INTO transactions "
        "(fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        " company, director, role, type, shares, price, value, context, "
        " url, announced_at, cluster_id, first_time_buy) "
        "VALUES ('fp-tst', ?, ?, 1, '2025-01-03', 'TST', 'Test', 'D', 'CEO', "
        "'BUY', 100, 1.0, 100.0, NULL, NULL, NULL, NULL, 0)",
        (db.iso_now(), db.iso_now()),
    )
    conn.commit()
    with mock.patch("fetch_prices.urllib.request.urlopen",
                    _fake_urlopen_sequence([body])), \
         mock.patch("fetch_prices.time.sleep"):
        backfill_prices.run(date_from="2025-01-01", date_to="2025-01-31",
                            only_ticker="TST", rate_limit=0)
    n1 = conn.execute(
        "SELECT COUNT(*) AS n FROM prices WHERE ticker='TST'"
    ).fetchone()["n"]
    with mock.patch("fetch_prices.urllib.request.urlopen",
                    _fake_urlopen_sequence([body])), \
         mock.patch("fetch_prices.time.sleep"):
        backfill_prices.run(date_from="2025-01-01", date_to="2025-01-31",
                            only_ticker="TST", rate_limit=0)
    n2 = conn.execute(
        "SELECT COUNT(*) AS n FROM prices WHERE ticker='TST'"
    ).fetchone()["n"]
    assert n1 == n2 == 2, f"n1={n1} n2={n2}"


def case_14_three_day_window_exact_count(conn):
    body = _canned_chart_json("THREE.L",
                              timestamps=[1735862400, 1735948800, 1736035200],
                              closes=[100.0, 101.0, 102.0], volumes=[1, 2, 3])
    with mock.patch("fetch_prices.urllib.request.urlopen",
                    _fake_urlopen_sequence([body])), \
         mock.patch("fetch_prices.time.sleep"):
        r = fetch_prices.fetch("THREE", "2025-01-03", "2025-01-05",
                               rate_limit=0, use_cache=False)
    assert len(r.rows) == 3, r.rows


def case_15_resume_skips_completed(conn):
    conn.execute(
        "INSERT OR IGNORE INTO transactions "
        "(fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        " company, director, role, type, shares, price, value, context, "
        " url, announced_at, cluster_id, first_time_buy) "
        "VALUES ('fp-done', ?, ?, 1, '2025-01-03', 'DONE', 'X', 'D', 'CEO', "
        "'BUY', 100, 1.0, 100.0, NULL, NULL, NULL, NULL, 0)",
        (db.iso_now(), db.iso_now()),
    )
    conn.commit()
    progress = {"completed_tickers": ["DONE"], "last_run": db.iso_now()}
    backfill_prices.PROGRESS_PATH.write_text(json.dumps(progress), encoding="utf-8")
    def _raise(req, timeout=None):
        raise AssertionError("network should not be called for resumed ticker")
    with mock.patch("fetch_prices.urllib.request.urlopen", _raise), \
         mock.patch("fetch_prices.time.sleep"):
        summary = backfill_prices.run(
            date_from="2025-01-01", date_to="2025-01-31",
            only_ticker="DONE", rate_limit=0, resume=True,
        )
    assert summary["skipped_resume"] == 1, summary


def case_16_unsupported_currency_no_rows(conn):
    body = _canned_chart_json("ADRX.L", currency="USD",
                              timestamps=[1735862400], closes=[42.0], volumes=[1])
    with mock.patch("fetch_prices.urllib.request.urlopen",
                    _fake_urlopen_sequence([body])), \
         mock.patch("fetch_prices.time.sleep"):
        r = fetch_prices.fetch("ADRX", "2025-01-01", "2025-01-31",
                               rate_limit=0, use_cache=False)
    assert r.status == "unsupported_currency", r
    assert r.rows == []


def case_17_yahoo_symbol_for_benchmark():
    assert fetch_prices.yahoo_symbol_for("^FTAS") == "^FTAS"
    assert fetch_prices.yahoo_symbol_for("BARC") == "BARC.L"


def case_18_db_ticker_for_round_trip():
    assert fetch_prices.db_ticker_for("BARC.L") == "BARC"
    assert fetch_prices.db_ticker_for("^FTAS") == "^FTAS"


def case_19_sector_csv_loads_real_file():
    m = fetch_sectors._load_sector_map()
    assert isinstance(m, dict)
    assert len(m) >= 30, f"expected >=30 rows, got {len(m)}"
    assert "BARC" in m
    assert m["BARC"]["sector"] == "Financials"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    tmp_dir_str = tempfile.mkdtemp(prefix="dd_stage3_")
    tmp_dir = Path(tmp_dir_str)
    real_db = db.DB_PATH
    real_mtime = real_db.stat().st_mtime if real_db.exists() else None

    saved_cache_dir = fetch_prices.CACHE_DIR
    saved_progress_path = backfill_prices.PROGRESS_PATH

    try:
        db.DB_DIR = tmp_dir
        db.DB_PATH = tmp_dir / "directors.db"
        fetch_prices.CACHE_DIR = tmp_dir / "_price_cache"
        backfill_prices.PROGRESS_PATH = tmp_dir / "_price_progress.json"

        run_case("01. fetch basic ok",              case_01_fetch_basic_ok)
        run_case("02. cache hit recent",            case_02_cache_hit_recent)
        run_case("03. cache window extended",       case_03_cache_window_extended)
        run_case("04. cache TTL expired",           case_04_cache_ttl_expired)
        run_case("05. 404 -> delisted",             case_05_404_delisted)
        run_case("06. 429 backoff then ok",         case_06_429_then_ok)
        run_case("07. GBp pence normalised",        case_07_gbp_pence_normalised)
        run_case("08. GBP passthrough",             case_08_gbp_pounds_passthrough)
        run_case("09. resolve BARC Financials",     case_09_resolve_barc_financials)
        run_case("10. resolve UNKNOWN fallback",    case_10_resolve_unknown_fallback)
        run_case("11. resolve AIM flag",            case_11_resolve_aim_flagged)
        run_case("17. yahoo_symbol_for ^FTAS",      case_17_yahoo_symbol_for_benchmark)
        run_case("18. db_ticker_for round-trip",    case_18_db_ticker_for_round_trip)
        run_case("19. sector_map.csv loads",        case_19_sector_csv_loads_real_file)

        conn = db.connect()
        try:
            run_case("12. benchmark backfill ^FTAS",
                     lambda: case_12_benchmark_backfill_end_to_end(conn))
            run_case("13. backfill prices idempotent",
                     lambda: case_13_backfill_prices_idempotent(conn))
            run_case("14. three-day window exact",
                     lambda: case_14_three_day_window_exact_count(conn))
            run_case("15. resume skips completed",
                     lambda: case_15_resume_skips_completed(conn))
            run_case("16. unsupported currency",
                     lambda: case_16_unsupported_currency_no_rows(conn))
        finally:
            conn.close()

        if real_mtime is not None and real_db.exists():
            assert real_db.stat().st_mtime == real_mtime, "real DB mtime changed"

        passed = sum(1 for _, ok, _ in RESULTS if ok)
        failed = sum(1 for _, ok, _ in RESULTS if not ok)
        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        fetch_prices.CACHE_DIR = saved_cache_dir
        backfill_prices.PROGRESS_PATH = saved_progress_path
        shutil.rmtree(tmp_dir_str, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
