"""Stage 5 dashboard smoke tests.

Mirrors the Stage 4 / 4.6 self-cleaning pattern: every test creates
its own temp dir + minimal fixture JSON, calls the renderer, asserts on
the output. No live API calls. No live DB writes. Mocks Flask via
flask.testing.

>= 22 cases. Run via:

    python -u .scripts/test_stage_05.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import (  # noqa: E402
    render_helpers as rh,
    templates,
    render_index,
    render_performance,
    render_company,
)
import build_dashboard  # noqa: E402


# ---------- fixtures ----------

def _minimal_signals_json() -> dict:
    """A minimal but realistic signals.json fixture.

    B-027 (2026-05-21): expanded to the B-025 Phase B 11-signal set
    (t0/t1a/t1b/t7/t2/t3/t5/t6/t4/s1/f1). The renderer iterates
    SIGNAL_DISPLAY_ORDER and asserts presence of each `data-signal-id`."""
    sigs_t90 = {
        "t0":  {"trades": 2, "hit_pct": 0.0, "median_car": -24.5, "mean_car": -24.5,
                "edge": -32.8, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t1a": {"trades": 2, "hit_pct": 0.0, "median_car": -24.5, "mean_car": -24.5,
                "edge": -32.8, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t1b": {"trades": 1, "hit_pct": 100.0, "median_car": 4.2, "mean_car": 4.2,
                "edge": 1.8, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t7":  {"trades": 3, "hit_pct": 33.3, "median_car": -1.0, "mean_car": -0.5,
                "edge": -3.2, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t2":  {"trades": 2, "hit_pct": 100.0, "median_car": 10.7, "mean_car": 10.7,
                "edge": 2.4, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t3":  {"trades": 22, "hit_pct": 22.7, "median_car": -11.5, "mean_car": -6.9,
                "edge": -18.8, "sparkline": [0.0]*9, "status": "kill?", "outlier_flag": False},
        "t5":  {"trades": 4, "hit_pct": 50.0, "median_car": 0.0, "mean_car": 1.2,
                "edge": -2.0, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t6":  {"trades": 1, "hit_pct": 0.0, "median_car": -3.0, "mean_car": -3.0,
                "edge": -5.5, "sparkline": [0.0]*9, "status": "review", "outlier_flag": False},
        "t4":  {"trades": 5, "hit_pct": 60.0, "median_car": 0.5, "mean_car": 57.9,
                "edge": -6.8, "sparkline": [0.0]*9, "status": "review", "outlier_flag": True},
        "s1":  {"trades": 24, "hit_pct": 20.8, "median_car": -13.6, "mean_car": -15.4,
                "edge": -20.9, "sparkline": [0.0]*9, "status": "kill?", "outlier_flag": False},
        "f1":  {"trades": 35, "hit_pct": 31.4, "median_car": -10.2, "mean_car": 2.5,
                "edge": -17.5, "sparkline": [0.0]*9, "status": "gated", "outlier_flag": True},
    }
    return {
        "generated_at": "2026-05-14T16:36:13Z",
        "schema_version": "1.0",
        "horizon_aggregates": {
            "t1":   {"base_rate": 54.8, "signals": sigs_t90},
            "t30":  {"base_rate": 73.6, "signals": sigs_t90},
            "t90":  {"base_rate": 100.0, "signals": sigs_t90},
            "t365": {"base_rate": 50.0, "signals": {k: {**v, "trades": 0,
                "hit_pct": None, "median_car": None, "mean_car": None, "edge": None}
                for k, v in sigs_t90.items()}},
        },
        "active_clusters": [
            {"ticker": "DNLM", "company": "Dunelm Group plc",
             "director_count": 5, "aggregate_value_gbp": 4_919_934.0,
             "first_buy_date": "2026-05-12", "last_buy_date": "2026-05-13",
             "s1_active": True},
            {"ticker": "BREW", "company": "Brewing Corp plc",
             "director_count": 2, "aggregate_value_gbp": 50_000.0,
             "first_buy_date": "2026-03-15", "last_buy_date": "2026-03-20",
             "s1_active": False},
            {"ticker": "STAL", "company": "Stale Co plc",
             "director_count": 2, "aggregate_value_gbp": 10_000.0,
             "first_buy_date": "2025-08-01", "last_buy_date": "2025-08-05",
             "s1_active": False},
        ],
        "paper_pnl_open": 0.0,
        "paper_trades_open": 0,
        "paper_trades_closed": 0,
        "cohorts": {
            "by_value_bucket": {
                "1k-25k": None, "25k-100k": 2.5,
                "100k-500k": 2.34, "500k+": None,
            },
            "by_sector": [
                {"sector": "Industrials", "hit_pct": 63.6, "base_rate": 73.6, "n": 11},
                {"sector": "Financials", "hit_pct": 43.1, "base_rate": 73.6, "n": 51},
            ],
        },
    }


def _minimal_dealings_json(today_iso: str | None = None) -> dict:
    today_iso = today_iso or "2026-05-14"
    return {
        "generated_at": "2026-05-14T16:36:13Z",
        "schema_version": "1.0",
        "as_of_date": today_iso,
        "signals_today_count": 0,
        "signals_today_delta_vs_avg": -2,
        "today": [],
        "this_week": [
            {"time_utc": "2026-05-13", "ticker": "RWA",
             "company": "Robert Walters plc", "director": "Andrew Rashbass",
             "role": "Non-executive Director", "txn_type": "BUY",
             "value_gbp": 19999.88, "signals_fired": ["f1", "s1", "t3"],
             "mtm_pct": None},
            {"time_utc": "2026-05-11", "ticker": "REE",
             "company": "Altona Rare Earths Plc", "director": "Harvey Sinclair",
             "role": "Non-Executive Chairman", "txn_type": "BUY",
             "value_gbp": 4959.84, "signals_fired": ["f1"], "mtm_pct": -3.22},
        ],
    }


# ---------- tests ----------

class TestRenderHelpers(unittest.TestCase):
    def test_01_badge_palette_locked(self):
        """B-025 Phase B palette (2026-05-20). Per-bucket tiers replace the
        legacy combined T1:
            T0=red-600, T1a=red-500, T1b=rose-500, T7=violet-500,
            T2=amber-500, T3=emerald-500, T5=orange-400, T6=slate-300,
            T4=slate-400, S1=blue-500, F1=purple-500."""
        expect = {
            "t0":  "bg-red-600",
            "t1a": "bg-red-500",
            "t1b": "bg-rose-500",
            "t7":  "bg-violet-500",
            "t2":  "bg-amber-500",
            "t3":  "bg-emerald-500",
            "t5":  "bg-orange-400",
            "t6":  "bg-slate-300",
            "t4":  "bg-slate-400",
            "s1":  "bg-blue-500",
            "f1":  "bg-purple-500",
        }
        for sid, cls in expect.items():
            html = rh.render_badge(sid)
            self.assertIn(cls, html, f"{sid} should have class {cls}")
            self.assertIn(sid.upper(), html)
            self.assertIn("title=", html, f"{sid} should have tooltip")

    def test_02_car_color_rules(self):
        """positive=emerald, negative=rose, null/zero=slate."""
        self.assertEqual(rh.car_color_class(1.0), "text-emerald-600")
        self.assertEqual(rh.car_color_class(-1.0), "text-rose-600")
        self.assertEqual(rh.car_color_class(0.0), "text-slate-500")
        self.assertEqual(rh.car_color_class(None), "text-slate-400")
        self.assertIn("text-emerald-600", rh.car_cell(2.34))
        self.assertIn("text-rose-600", rh.car_cell(-2.34))
        # NULL CAR renders the dash, not a coloured cell.
        self.assertIn("text-slate-300", rh.car_cell(None))

    def test_04_generated_at_footer(self):
        """Footer renders with timestamp + build sha."""
        html = rh.generated_at_footer("2026-05-14T12:49:35Z", "abcd123")
        self.assertIn("Generated", html)
        self.assertIn("build", html)
        self.assertIn("abcd123", html)
        self.assertIn("2026-05-14", html)


class TestRenderIndex(unittest.TestCase):
    def test_05_index_renders_against_real_shape(self):
        """index.html renders without error against the actual JSON shape."""
        signals = _minimal_signals_json()
        dealings = _minimal_dealings_json()
        html = render_index.render(signals, dealings, build_sha="test")
        self.assertIn("<!doctype html>", html)
        self.assertIn("Directors Dealings - This Week", html)
        self.assertIn("Signals today", html)
        self.assertIn("Active clusters", html)
        self.assertIn("Brewing", html)
        self.assertIn("DNLM", html)        # Active cluster ticker shown.
        self.assertIn("RWA", html)         # This-week row.
        # Tailwind CDN reference.
        self.assertIn("cdn.tailwindcss.com", html)

    def test_06_index_empty_state(self):
        """Index renders empty-state copy when no today/week rows."""
        signals = _minimal_signals_json()
        dealings = {"generated_at": "...", "as_of_date": "2026-05-14",
                    "signals_today_count": 0, "signals_today_delta_vs_avg": 0,
                    "today": [], "this_week": []}
        signals = dict(signals); signals["active_clusters"] = []
        html = render_index.render(signals, dealings, build_sha="test")
        self.assertIn("No signals fired this week", html)
        self.assertIn("No active clusters", html)

    def test_07_index_cluster_classification(self):
        """Active vs Brewing vs Stale -- only first two shown."""
        signals = _minimal_signals_json()
        dealings = _minimal_dealings_json()
        html = render_index.render(signals, dealings, build_sha="test")
        # DNLM is active S1.
        self.assertIn("DNLM", html)
        # BREW (last_buy 2026-03-20, today 2026-05-14 ~55d) is brewing.
        self.assertIn("BREW", html)
        # STAL (last_buy 2025-08-05) is >90d stale -- HIDDEN.
        self.assertNotIn("STAL", html)


class TestRenderPerformance(unittest.TestCase):
    def test_08_performance_renders(self):
        """performance.html renders + scoreboard contains all 11 Phase B
        signals (t0/t1a/t1b/t7/t2/t3/t5/t6/t4/s1/f1)."""
        signals = _minimal_signals_json()
        html = render_performance.render(signals, build_sha="test")
        for sid in ("t0", "t1a", "t1b", "t7", "t2", "t3",
                    "t5", "t6", "t4", "s1", "f1"):
            self.assertIn(f'data-signal-id="{sid}"', html)
        self.assertIn("Per-signal scoreboard", html)
        self.assertIn("Model assessment", html)
        # Chart.js CDN.
        self.assertIn("chart.js@4.4.6", html)

    def test_09_deprecate_button_visible_per_row(self):
        """A deprecate button is rendered on each (non-gated) scoreboard row."""
        signals = _minimal_signals_json()
        html = render_performance.render(signals, build_sha="test")
        # 7 deprecate buttons total (one per signal).
        n_btns = html.count("Deprecate")
        self.assertGreaterEqual(n_btns, 7)
        # Gated row has disabled attribute on its button.
        self.assertIn("disabled", html)

    def test_10_kill_candidates_in_model_assessment(self):
        """t3 and s1 (N>=20, mean<0, hit<base) appear under 'Kill candidates'."""
        signals = _minimal_signals_json()
        html = render_performance.render(signals, build_sha="test")
        # Take the model-assessment block region.
        idx = html.index("Model assessment")
        block = html[idx:idx + 1500]
        self.assertIn("Kill candidates", block)
        self.assertTrue("T3" in block or "S1" in block,
                        "expected at least one of T3 / S1 in kill block")

    def test_11_horizon_dropdown_options(self):
        """All four horizon options present + t30 selected."""
        signals = _minimal_signals_json()
        html = render_performance.render(signals, build_sha="test")
        for key, label in [("t1", "T+1"), ("t30", "T+30"),
                           ("t90", "T+90"), ("t365", "T+365")]:
            self.assertIn(f'value="{key}"', html)
            self.assertIn(label, html)
        # t30 default selected.
        self.assertIn('value="t30" selected', html)

    def test_12_horizon_change_event_wired(self):
        """The horizonChange custom event is dispatched + chart subscribes."""
        signals = _minimal_signals_json()
        html = render_performance.render(signals, build_sha="test")
        self.assertIn("horizonChange", html)
        self.assertIn("rebuildScoreboard", html)
        self.assertIn("rebuildDiag", html)


class TestRenderCompany(unittest.TestCase):
    def _record(self, **overrides):
        rec = {
            "ticker": "DNLM", "company": "Dunelm Group plc",
            "sector": "Consumer Discretionary", "is_aim": False,
            "latest_close": 1234.0, "prev_close": 1224.0,
            "generated_at": "2026-05-14T12:00:00Z",
            "active_cluster": {"ticker": "DNLM", "director_count": 5,
                "aggregate_value_gbp": 4_000_000.0,
                "first_buy_date": "2026-05-12",
                "last_buy_date": "2026-05-13", "s1_active": True},
            "recent_firing": None,
            "prices": [
                {"date": "2026-05-10", "close": 1200.0, "volume": 100000},
                {"date": "2026-05-11", "close": 1210.0, "volume": 120000},
                {"date": "2026-05-12", "close": 1220.0, "volume": 130000},
                {"date": "2026-05-13", "close": 1234.0, "volume": 150000},
            ],
            "transactions": [
                {"fingerprint": "abc123", "date": "2026-05-13",
                 "announced_at": "2026-05-13T07:00:00Z",
                 "director": "Jane Doe", "role": "CEO", "txn_type": "BUY",
                 "shares": 1000, "price": 1234.0, "value": 1_234_000.0,
                 "signals": ["t1", "s1"], "url": "https://example/rns/123"},
            ],
            "firings": [
                {"fired_at": "2026-05-13T07:30:00Z",
                 "entry_date": "2026-05-13", "signal_id": "t1",
                 "director": "Jane Doe", "car_t1": 0.5, "car_t30": None,
                 "car_t90": None, "car_t365": None},
            ],
            "clusters": [
                {"cluster_id": "C1", "first_buy_date": "2026-05-12",
                 "last_buy_date": "2026-05-13", "director_count": 5,
                 "aggregate_value": 4_000_000.0, "active": True},
            ],
        }
        rec.update(overrides)
        return rec

    def test_13_company_page_renders(self):
        rec = self._record()
        html = render_company.render(rec, build_sha="test")
        self.assertIn("DNLM", html)
        self.assertIn("Dunelm Group plc", html)
        self.assertIn("Transactions", html)
        self.assertIn("Signal-firing history", html)
        self.assertIn("Cluster history", html)
        # RNS source link mandatory.
        self.assertIn("https://example/rns/123", html)

    def test_14_company_aim_badge(self):
        """is_aim=True renders AIM badge; False renders Main."""
        rec = self._record(is_aim=True)
        html = render_company.render(rec, build_sha="test")
        self.assertIn(">AIM<", html)
        rec2 = self._record(is_aim=False)
        html2 = render_company.render(rec2, build_sha="test")
        self.assertIn(">Main<", html2)

    def test_15_company_no_signals_just_transactions(self):
        """Page renders with empty firings -> empty-state copy in firings section."""
        rec = self._record(firings=[])
        html = render_company.render(rec, build_sha="test")
        self.assertIn("No signals have fired", html)
        # Transactions still render.
        self.assertIn("Jane Doe", html)

    def test_16_company_pending_car_cells(self):
        """Pending T+21/T+90/T+252 cells render '-' rather than zeros."""
        rec = self._record()
        html = render_company.render(rec, build_sha="test")
        # Pending shows a hyphen + a title attr "Matures ...".
        self.assertIn("Matures", html)

    def test_17_company_no_prices_empty_state(self):
        """No prices -> 'No price history available' empty state."""
        rec = self._record(prices=[])
        html = render_company.render(rec, build_sha="test")
        self.assertIn("No price history available", html)


class TestBuildOrchestrator(unittest.TestCase):
    """Tests that hit the full orchestrator against a temp on-disk fixture set."""

    def _make_temp_inputs(self, tmp: Path):
        signals_path = tmp / "signals.json"
        dealings_path = tmp / "dealings.json"
        signals_path.write_text(json.dumps(_minimal_signals_json()), encoding="utf-8")
        dealings_path.write_text(json.dumps(_minimal_dealings_json()), encoding="utf-8")
        # Tiny SQLite DB (no real backfill) -- transactions table only.
        db_path = tmp / "directors.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE transactions (
                fingerprint TEXT PRIMARY KEY,
                date TEXT, ticker TEXT, company TEXT,
                director TEXT, role TEXT, type TEXT,
                shares INTEGER, price REAL, value REAL,
                url TEXT, announced_at TEXT, cluster_id TEXT,
                first_seen TEXT, last_seen TEXT, seen_count INT,
                first_time_buy INT
            );
            CREATE TABLE prices (
                ticker TEXT, date TEXT, open REAL, high REAL,
                low REAL, close REAL, volume INTEGER, source TEXT,
                fetched_at TEXT, PRIMARY KEY (ticker, date)
            );
            CREATE TABLE tickers_meta (
                ticker TEXT PRIMARY KEY, sector TEXT,
                benchmark_symbol TEXT, is_aim INT DEFAULT 0,
                market_cap_gbp REAL, updated_at TEXT
            );
            CREATE TABLE signals (
                signal_id TEXT, signal_version TEXT, fingerprint TEXT,
                fired_at TEXT, confidence TEXT, metadata TEXT,
                PRIMARY KEY (signal_id, signal_version, fingerprint)
            );
        """)
        conn.execute(
            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("fp1", "2026-05-13", "DNLM", "Dunelm Group plc",
             "Jane Doe", "CEO", "BUY", 1000, 100.0, 100000.0,
             "https://example/dnlm", "2026-05-13T07:00:00Z", None,
             "2026-05-13", "2026-05-13", 1, 0))
        conn.execute(
            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("fp2", "2026-05-13", "ACSO", "accesso Technology Group Plc",
             "Bob Smith", "Director", "BUY", 500, 50.0, 25000.0,
             "https://example/acso", "2026-05-13T07:30:00Z", None,
             "2026-05-13", "2026-05-13", 1, 1))
        conn.execute(
            "INSERT INTO tickers_meta VALUES (?,?,?,?,?,?)",
            ("DNLM", "Consumer Discretionary", "^FTAS", 0, None, "2026-05-13"))
        conn.execute(
            "INSERT INTO tickers_meta VALUES (?,?,?,?,?,?)",
            ("ACSO", "Technology", "^FTAS", 1, None, "2026-05-13"))
        conn.execute(
            "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)",
            ("DNLM", "2026-05-12", 95.0, 102.0, 94.0, 100.0, 1000,
             "yahoo", "2026-05-13"))
        conn.execute(
            "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)",
            ("DNLM", "2026-05-13", 100.0, 105.0, 98.0, 103.0, 2000,
             "yahoo", "2026-05-13"))
        conn.commit()
        conn.close()
        return signals_path, dealings_path, db_path

    def test_18_full_build_writes_all_pages(self):
        """End-to-end: orchestrator writes index + performance + data copies.

        B-184: per-ticker company pages are no longer built (the dynamic
        company.html?ticker= template replaces them), so the build writes NO
        outputs/companies/*.html files and reports company_pages == 0."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            signals_path, dealings_path, db_path = self._make_temp_inputs(tmp)
            out_dir = tmp / "outputs"
            # Patch db.DB_PATH so build_dashboard's DB connection sees the temp DB.
            import db as db_mod
            orig_path = db_mod.DB_PATH
            db_mod.DB_PATH = db_path
            try:
                summary = build_dashboard.build(
                    out_dir=out_dir,
                    signals_path=signals_path,
                    dealings_path=dealings_path,
                    status_path=None,
                    csv_path=tmp / "missing.csv",
                    clusters_path=tmp / "missing.json",
                    build_sha="testbuild",
                    verbose=False,
                )
            finally:
                db_mod.DB_PATH = orig_path
            # M6 (live front page): build_dashboard no longer writes index.html.
            # The front page is a hand-maintained live page that reads Supabase
            # directly in the browser; the daily build was clobbering it, so the
            # index render step is now a deliberate no-op. (Front-page render
            # abandoned 2026-06-23 — see project_live_frontpage_decision.)
            self.assertFalse((out_dir / "index.html").exists())
            self.assertTrue((out_dir / "performance.html").exists())
            # B-184: static company pages are no longer generated.
            self.assertFalse((out_dir / "companies" / "DNLM.html").exists())
            self.assertFalse((out_dir / "companies" / "ACSO.html").exists())
            # data/ copied for fetch().
            self.assertTrue((out_dir / "data" / "signals.json").exists())
            self.assertTrue((out_dir / "data" / "dealings.json").exists())
            self.assertEqual(summary["company_pages"], 0)


class TestIdempotency(unittest.TestCase):
    def test_19_rebuild_byte_identical_except_generated_at(self):
        """Two rebuilds with the same inputs produce byte-identical HTML
        (no random ordering / time-dependent state except generated_at footer
        from JSON, which is the same JSON across runs)."""
        signals = _minimal_signals_json()
        dealings = _minimal_dealings_json()
        a = render_index.render(signals, dealings, build_sha="abc1234")
        b = render_index.render(signals, dealings, build_sha="abc1234")
        # All but the generated-at line should match.
        self.assertEqual(a, b, "index render should be deterministic")


class TestCDNUrlsHardcoded(unittest.TestCase):
    def test_20_cdn_urls_pinned(self):
        """Tailwind 3.4.x + Chart.js 4.4.6 hardcoded (no typos, pinned versions)."""
        signals = _minimal_signals_json()
        dealings = _minimal_dealings_json()
        html_index = render_index.render(signals, dealings, build_sha="t")
        html_perf = render_performance.render(signals, build_sha="t")
        # Tailwind CDN.
        self.assertIn("cdn.tailwindcss.com/3.4.", html_index)
        # Chart.js pinned.
        self.assertIn("chart.js@4.4.6", html_perf)


class TestServerEndpoints(unittest.TestCase):
    def _client_with_patched_path(self):
        """Patch _SIGNAL_STATUS_PATH to a temp file before creating the client."""
        import importlib
        # Reload to pick up env each test (defensive).
        import server as srv  # noqa: F401
        return srv

    def test_21_deprecate_endpoint_atomic_write(self):
        """POST /api/deprecate writes signal_status.json atomically + returns 200."""
        srv = self._client_with_patched_path()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".data" / "signal_status.json"
            orig_path = srv._SIGNAL_STATUS_PATH
            srv._SIGNAL_STATUS_PATH = target
            try:
                client = srv.app.test_client()
                resp = client.post("/api/deprecate",
                                   json={"signal_id": "t3", "action": "deprecate"})
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data["ok"])
                self.assertIn("t3", data["deprecated"])
                self.assertTrue(target.exists())
                payload = json.loads(target.read_text(encoding="utf-8"))
                self.assertIn("t3", payload["deprecated"])
                # Reactivate.
                resp2 = client.post("/api/deprecate",
                                    json={"signal_id": "t3", "action": "reactivate"})
                self.assertEqual(resp2.status_code, 200)
                payload2 = json.loads(target.read_text(encoding="utf-8"))
                self.assertNotIn("t3", payload2["deprecated"])
            finally:
                srv._SIGNAL_STATUS_PATH = orig_path

    def test_22_deprecate_endpoint_rejects_bad_input(self):
        """Invalid signal_id or action returns 400 + ok=False."""
        srv = self._client_with_patched_path()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ss.json"
            orig = srv._SIGNAL_STATUS_PATH
            srv._SIGNAL_STATUS_PATH = target
            try:
                client = srv.app.test_client()
                resp = client.post("/api/deprecate",
                                   json={"signal_id": "xx", "action": "deprecate"})
                self.assertEqual(resp.status_code, 400)
                self.assertFalse(resp.get_json()["ok"])
                resp2 = client.post("/api/deprecate",
                                    json={"signal_id": "t3", "action": "explode"})
                self.assertEqual(resp2.status_code, 400)
            finally:
                srv._SIGNAL_STATUS_PATH = orig


class TestEvalSkipsDeprecated(unittest.TestCase):
    def test_23_eval_signals_skips_deprecated(self):
        """eval_signals.py: a signal listed in signal_status.json is removed
        from the eval order. Stage 5 contract."""
        import eval_signals
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            status_path = tmp / "signal_status.json"
            status_path.write_text(json.dumps(
                {"deprecated": ["t3"], "updated_at": "2026-05-14T00:00:00Z"}
            ), encoding="utf-8")
            orig = eval_signals.SIGNAL_STATUS_PATH
            eval_signals.SIGNAL_STATUS_PATH = status_path
            try:
                dep = eval_signals._load_deprecated_signal_ids()
                self.assertIn("t3", dep)
                self.assertIn("t3_ned_buy", dep,  # long form also added
                              "short id should be expanded to long form")
            finally:
                eval_signals.SIGNAL_STATUS_PATH = orig


class TestStaticAssets(unittest.TestCase):
    def test_24_outputs_data_copied(self):
        """build_dashboard copies signals.json + dealings.json to outputs/data/."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            signals_path = tmp / "signals.json"
            dealings_path = tmp / "dealings.json"
            signals_path.write_text(json.dumps(_minimal_signals_json()))
            dealings_path.write_text(json.dumps(_minimal_dealings_json()))
            out_dir = tmp / "outputs"
            info = build_dashboard._copy_data_dir(signals_path, dealings_path,
                                                  out_dir, None)
            self.assertIn("signals.json", info)
            self.assertIn("dealings.json", info)
            self.assertTrue((out_dir / "data" / "signals.json").exists())
            # B-184: private review-queue JSONs must NOT land in the public
            # outputs/data/ bundle.
            self.assertNotIn("pending_review.json", info)
            self.assertNotIn("tx_index.json", info)
            self.assertFalse((out_dir / "data" / "pending_review.json").exists())
            self.assertFalse((out_dir / "data" / "tx_index.json").exists())


class TestHTMLValidity(unittest.TestCase):
    def test_25_every_page_is_text_html(self):
        """All generated pages start with <!doctype html> + have <head> +
        a generated-at footer (per spec)."""
        signals = _minimal_signals_json()
        dealings = _minimal_dealings_json()
        html_idx = render_index.render(signals, dealings, build_sha="t")
        html_perf = render_performance.render(signals, build_sha="t")
        for html in (html_idx, html_perf):
            self.assertTrue(html.startswith("<!doctype html>"))
            self.assertIn("<head>", html)
            self.assertIn("</html>", html)
            # Generated-at footer present.
            self.assertRegex(html, r"Generated [\d\-: UTC]+&middot; build")


class TestSecuritySanitization(unittest.TestCase):
    def test_26_html_escaping(self):
        """User-controlled fields with <script> tags are escaped."""
        signals = _minimal_signals_json()
        dealings = _minimal_dealings_json()
        dealings["this_week"].append({
            "time_utc": "2026-05-11", "ticker": "EVIL",
            "company": "<script>alert(1)</script>",
            "director": "<img src=x>", "role": "Chief",
            "txn_type": "BUY", "value_gbp": 0,
            "signals_fired": ["t3"], "mtm_pct": None,
        })
        html = render_index.render(signals, dealings, build_sha="t")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestPendingDiagnosticsPanel(unittest.TestCase):
    """Stage 5 - Pending diagnostics panel on performance.html."""

    def _pending_block(self) -> dict:
        return {
            "total": 3491,
            "generated_at": "2026-05-14T17:36:00Z",
            "categories": [
                {"id": "bundled_multi_pdmr",
                 "name": "Bundled multi-PDMR", "count": 2003, "pct": 57.4,
                 "recoverable": "no",
                 "description": "Filing names multiple directors"},
                {"id": "foreign_currency",
                 "name": "Foreign currency", "count": 370, "pct": 10.6,
                 "recoverable": "v2-fx",
                 "description": "Non-GBP filing"},
                {"id": "multi_tranche",
                 "name": "Multi-tranche / multi-transaction",
                 "count": 278, "pct": 8.0, "recoverable": "v2-fanout",
                 "description": "Two or more distinct prices"},
                {"id": "could_not_classify",
                 "name": "Could not classify / extract",
                 "count": 210, "pct": 6.0, "recoverable": "manual",
                 "description": "Parser failed"},
                {"id": "corporate_actions",
                 "name": "Corporate actions", "count": 150, "pct": 4.3,
                 "recoverable": "manual",
                 "description": "Vesting / option exercise"},
                {"id": "data_quirks",
                 "name": "Zero-share / data quirks", "count": 31, "pct": 0.9,
                 "recoverable": "manual",
                 "description": "Zero-share rows"},
                {"id": "other",
                 "name": "Other", "count": 449, "pct": 12.9,
                 "recoverable": "unknown",
                 "description": "Uncategorised"},
            ],
        }

    def test_27_pending_panel_renders(self):
        """performance.html renders the Pending review panel when
        pending_diagnostics is present."""
        signals = _minimal_signals_json()
        signals["pending_diagnostics"] = self._pending_block()
        html = render_performance.render(signals, build_sha="t")
        # Heading.
        self.assertIn("Pending review", html)
        # Total filings count appears with thousands sep.
        self.assertIn("3,491 filings", html)
        # Table has 7 data rows.
        m = re.search(r'<table[^>]*id="pendingDiag">.*?</table>', html, re.S)
        self.assertIsNotNone(m, "pendingDiag table not found")
        rows = re.findall(r'<tr[^>]*>', m.group(0))
        # 1 thead row + 7 tbody rows == 8.
        self.assertEqual(len(rows), 8, f"row count = {len(rows)}")
        # Recoverability badges all rendered with the locked Tailwind classes.
        for cls in ("bg-slate-200 text-slate-700",       # No
                    "bg-blue-100 text-blue-800",          # v2-fx + v2-fanout
                    "bg-amber-100 text-amber-800",        # Manual
                    "bg-slate-100 text-slate-600"):       # Unknown
            self.assertIn(cls, html, f"missing badge class: {cls}")
        # Display labels.
        self.assertIn("v2 (FX)", html)
        self.assertIn("v2 (fan-out)", html)
        self.assertIn(">Manual<", html)
        self.assertIn(">No<", html)
        self.assertIn(">Unknown<", html)
        # Panel sits below Model Assessment. Layout change in Sprint 55-57:
        # pending now renders AFTER "By director" / cohort cuts.
        i_model = html.index("Model assessment")
        i_pending = html.index("Pending review")
        i_cohort = html.index("By director")
        self.assertLess(i_model, i_pending,
                        "Pending panel must sit BELOW Model Assessment")
        self.assertGreater(i_pending, i_cohort,
                           "Pending panel now sits BELOW cohort cuts (layout changed post Sprint 55-57)")

    def test_28_pending_panel_empty_state(self):
        """When signals.json has no pending_diagnostics, the panel renders an
        empty-state pointing at the exporter."""
        signals = _minimal_signals_json()
        # Ensure key truly absent.
        signals.pop("pending_diagnostics", None)
        html = render_performance.render(signals, build_sha="t")
        self.assertIn("Pending review", html)
        self.assertIn("Pending diagnostics unavailable", html)
        self.assertIn("export_dashboard_json.py", html)


class TestCompanyPendingReviewPanel(unittest.TestCase):
    """Stage 5 - Per-ticker Pending review panel on company pages."""

    def _record(self, **overrides):
        rec = {
            "ticker": "DNLM", "company": "Dunelm Group plc",
            "sector": "Consumer Discretionary", "is_aim": False,
            "latest_close": 1234.0, "prev_close": 1224.0,
            "generated_at": "2026-05-14T12:00:00Z",
            "active_cluster": None, "recent_firing": None,
            "prices": [{"date": "2026-05-13", "close": 1234.0, "volume": 100}],
            "transactions": [],
            "firings": [],
            "clusters": [],
        }
        rec.update(overrides)
        return rec

    def test_29_company_pending_panel_renders(self):
        """Per-ticker panel renders when pending_review is present in record."""
        rec = self._record(pending_review={
            "total": 3,
            "categories": [
                {"id": "bundled_multi_pdmr", "name": "Bundled multi-PDMR",
                 "count": 2, "pct": 66.7, "recoverable": "no",
                 "description": "Multiple directors named"},
                {"id": "foreign_currency", "name": "Foreign currency",
                 "count": 1, "pct": 33.3, "recoverable": "v2-fx",
                 "description": "Non-GBP"},
            ],
        })
        html = render_company.render(rec, build_sha="t")
        # Heading + count + ticker.
        self.assertIn("Pending review", html)
        self.assertIn("3 DNLM filings excluded", html)
        # Table id locked.
        self.assertIn('id="pendingDiagTicker"', html)
        # Both rows shown with badges.
        self.assertIn("Bundled multi-PDMR", html)
        self.assertIn("Foreign currency", html)
        self.assertIn(">No<", html)
        self.assertIn("v2 (FX)", html)

    def test_30_company_pending_panel_omits_when_empty(self):
        """No pending_review key OR total=0 -> panel omitted entirely."""
        # Key absent.
        html_a = render_company.render(self._record(), build_sha="t")
        self.assertNotIn('id="pendingDiagTicker"', html_a)
        self.assertNotIn("filings excluded from signals", html_a)
        # Key present but total=0.
        html_b = render_company.render(self._record(
            pending_review={"total": 0, "categories": []}), build_sha="t")
        self.assertNotIn('id="pendingDiagTicker"', html_b)

    def test_31_company_pending_singular_plural(self):
        """1 filing -> singular; 2+ -> plural."""
        rec1 = self._record(pending_review={
            "total": 1,
            "categories": [{"id": "data_quirks", "name": "Zero-share",
                            "count": 1, "pct": 100.0,
                            "recoverable": "manual",
                            "description": "x"}]})
        html1 = render_company.render(rec1, build_sha="t")
        self.assertIn("1 DNLM filing excluded", html1)
        rec2 = self._record(pending_review={
            "total": 2,
            "categories": [{"id": "data_quirks", "name": "Zero-share",
                            "count": 2, "pct": 100.0,
                            "recoverable": "manual",
                            "description": "x"}]})
        html2 = render_company.render(rec2, build_sha="t")
        self.assertIn("2 DNLM filings excluded", html2)

    def test_32_ticker_from_pending_item_extracted_first(self):
        """extracted[0].ticker wins over URL fallback."""
        from build_dashboard import _ticker_from_pending_item
        item = {
            "url": "https://www.investegate.co.uk/announcement/rns/foo--bar/x/123",
            "extracted": [{"ticker": "DNLM"}],
        }
        self.assertEqual(_ticker_from_pending_item(item), "DNLM")

    def test_33_ticker_from_pending_item_url_fallback(self):
        """No extracted -> parse ticker from URL slug --{ticker}/."""
        from build_dashboard import _ticker_from_pending_item
        item = {
            "url": "https://www.investegate.co.uk/announcement/rns/card-factory--card/director-pdmr-shareholding/9564925",
            "extracted": [],
        }
        self.assertEqual(_ticker_from_pending_item(item), "CARD")

    def test_34_ticker_from_pending_item_none(self):
        """No URL and no extracted -> None."""
        from build_dashboard import _ticker_from_pending_item
        self.assertIsNone(_ticker_from_pending_item({}))
        self.assertIsNone(_ticker_from_pending_item({"url": "", "extracted": []}))

    def test_35_load_pending_per_ticker_buckets(self):
        """Round-trip: write a minimal _pending_review.json, load, assert
        per-ticker bucketization is correct + matches recoverability."""
        import build_dashboard as bd
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_pending_review.json"
            p.write_text(json.dumps({
                "generated_at": "2026-05-14T17:36:00Z",
                "count": 3,
                "items": {
                    "1": {"url": "https://x.com/rns/dunelm--dnlm/x/1",
                          "warnings": ["bundled multi-PDMR filing"],
                          "extracted": []},
                    "2": {"url": "https://x.com/rns/dunelm--dnlm/x/2",
                          "warnings": ["Transaction priced in EUR 2.15"],
                          "extracted": [{"ticker": "DNLM"}]},
                    "3": {"url": "https://x.com/rns/acso--acso/x/3",
                          "warnings": ["bundled multi-PDMR filing"],
                          "extracted": []},
                },
            }), encoding="utf-8")
            per_ticker = bd._load_pending_per_ticker(p)
            self.assertIn("DNLM", per_ticker)
            self.assertIn("ACSO", per_ticker)
            self.assertEqual(per_ticker["DNLM"]["total"], 2)
            self.assertEqual(per_ticker["ACSO"]["total"], 1)
            # DNLM should have two buckets (bundled_multi_pdmr, foreign_currency).
            dnlm_ids = {c["id"] for c in per_ticker["DNLM"]["categories"]}
            self.assertIn("bundled_multi_pdmr", dnlm_ids)
            self.assertIn("foreign_currency", dnlm_ids)



class TestRefreshButton(unittest.TestCase):
    """Stage 5 - Refresh Data button + /api/refresh-* endpoints."""

    def test_36_refresh_button_in_header(self):
        """Every base_page renders a Refresh button + modal+polling script."""
        html = templates.base_page(
            title="Test", body="<p>x</p>", generated_at_iso="2026-05-14T12:00:00Z",
            build_sha="t",
        )
        self.assertIn('id="refreshBtn"', html)
        self.assertIn('id="refreshBtnLabel"', html)
        self.assertIn("/api/refresh-all", html)
        self.assertIn("/api/refresh-status", html)
        # Cost warning copy is present (user-safety requirement).
        self.assertIn("LLM credits", html)
        self.assertIn("Run pipeline", html)

    def test_37_refresh_status_endpoint_idle(self):
        """GET /api/refresh-status returns idle when no run in flight."""
        import importlib
        import server as srv
        importlib.reload(srv)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "_refresh_status.json"
            orig = srv._REFRESH_STATUS_PATH
            srv._REFRESH_STATUS_PATH = target
            try:
                client = srv.app.test_client()
                resp = client.get("/api/refresh-status")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.get_json()["status"], "idle")
            finally:
                srv._REFRESH_STATUS_PATH = orig

    def test_38_refresh_all_rejects_when_running(self):
        """POST /api/refresh-all returns 409 if status is already running."""
        import importlib
        import server as srv
        importlib.reload(srv)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "_refresh_status.json"
            target.write_text(json.dumps({"status": "running",
                                          "step": "scrape",
                                          "step_label": "Fetching"}),
                              encoding="utf-8")
            orig = srv._REFRESH_STATUS_PATH
            srv._REFRESH_STATUS_PATH = target
            try:
                client = srv.app.test_client()
                resp = client.post("/api/refresh-all", json={})
                self.assertEqual(resp.status_code, 409)
                body = resp.get_json()
                self.assertFalse(body["ok"])
                self.assertEqual(body["reason"], "already_running")
            finally:
                srv._REFRESH_STATUS_PATH = orig

    def test_39_refresh_reset_blocked_when_running(self):
        """POST /api/refresh-reset returns 409 if currently running."""
        import importlib
        import server as srv
        importlib.reload(srv)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "_refresh_status.json"
            target.write_text(json.dumps({"status": "running"}),
                              encoding="utf-8")
            orig = srv._REFRESH_STATUS_PATH
            srv._REFRESH_STATUS_PATH = target
            try:
                client = srv.app.test_client()
                resp = client.post("/api/refresh-reset")
                self.assertEqual(resp.status_code, 409)
                # Reset only works on terminal states.
                target.write_text(json.dumps({"status": "error",
                                              "error": "boom"}),
                                  encoding="utf-8")
                resp2 = client.post("/api/refresh-reset")
                self.assertEqual(resp2.status_code, 200)
                self.assertTrue(resp2.get_json()["ok"])
                after = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(after["status"], "idle")
            finally:
                srv._REFRESH_STATUS_PATH = orig

    def test_40_orchestrator_status_atomic_write(self):
        """refresh_all.write_status writes via .tmp + os.replace (atomic)."""
        import refresh_all
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "_refresh_status.json"
            orig = refresh_all.STATUS_PATH
            refresh_all.STATUS_PATH = target
            try:
                refresh_all.write_status({"status": "running", "step": "x"})
                self.assertTrue(target.exists())
                data = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(data["status"], "running")
                # No leftover .tmp file.
                self.assertFalse(target.with_suffix(".json.tmp").exists())
            finally:
                refresh_all.STATUS_PATH = orig



if __name__ == "__main__":
    unittest.main(verbosity=2)
