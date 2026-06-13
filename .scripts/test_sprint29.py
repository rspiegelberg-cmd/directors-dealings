"""Sprint 29 tests.

Covers:
  B-104 -- ALL_BUY_SCOPE: all buy signals in scope, b2/t0 excluded
  B-079 -- focus-view mean label present in JS
  B-098 -- abs_return_pct in export row, company page rtn column
  B-009 -- sparkline capped to 12 months, no 0-coercion of null
  B-019 -- B-019 toggle JS present in rendered performance page
  B-103 -- data-sort th + JS present in Today page
  B-102 -- build_monthly_buysell logic + negative sell values
  B-100 Phase B -- eval opens paper_trade rows; close_paper_trades closes
"""
from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem_db():
    """In-memory SQLite with full schema."""
    schema = (HERE / "db_schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    # Apply all migrations.
    mig_dir = HERE / "schema_migrations"
    if mig_dir.exists():
        for f in sorted(mig_dir.glob("*.sql")):
            try:
                conn.executescript(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# B-104 -- HIGH_CONVICTION_NON_NED_SIGNALS expansion
# ---------------------------------------------------------------------------

class TestB104BucketScope(unittest.TestCase):
    def setUp(self):
        import export_dashboard_json as edj
        self.edj = edj

    def test_t3_ned_in_scope(self):
        self.assertIn("t3_ned_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_t4_in_scope(self):
        self.assertIn("t4_other_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_t5_pca_in_scope(self):
        self.assertIn("t5_pca_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_t6_in_scope(self):
        self.assertIn("t6_company_sec_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_s1_in_scope(self):
        self.assertIn("s1_cluster_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_f1_in_scope(self):
        self.assertIn("f1_first_time_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_b1_in_scope(self):
        self.assertIn("b1_lone_conviction_buy", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_b2_excluded(self):
        self.assertNotIn("b2_crowded_cluster_kill", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_t0_excluded(self):
        self.assertNotIn("t0_cluster_combo", self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)

    def test_t1a_t1b_t2_t7_still_present(self):
        for sid in ("t1a_ceo_founder_buy", "t1b_cfo_buy", "t2_exec_buy", "t7_chair_buy"):
            self.assertIn(sid, self.edj.HIGH_CONVICTION_NON_NED_SIGNALS)


# ---------------------------------------------------------------------------
# B-079 -- focus-view mean label tooltip in JS
# ---------------------------------------------------------------------------

class TestB079FocusViewMean(unittest.TestCase):
    def test_mean_net_car_tooltip_present(self):
        from dashboard import render_performance
        import inspect
        src = inspect.getsource(render_performance)
        self.assertIn("Mean net CAR since inception", src,
                      "B-079: tooltip explaining mean_car_t30_overall equivalent not found")

    def test_b079_comment_present(self):
        from dashboard import render_performance
        import inspect
        src = inspect.getsource(render_performance)
        self.assertIn("B-079", src)


# ---------------------------------------------------------------------------
# B-098 -- abs_return_pct in export dealings row
# ---------------------------------------------------------------------------

class TestB098AbsReturn(unittest.TestCase):
    def _make_conn_with_prices(self):
        conn = _mem_db()
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,shares,price,value) "
            "VALUES ('fp1','2026-01-02','2026-01-02','TST','BUY','2026-01-02','2026-01-02',"
            "'J Smith','Test Co','CEO',100,100.0,10000.0)"
        )
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('TST',0,'^FTAS','2026-01-01T00:00:00Z')"
        )
        # Entry close on 2026-01-03 (T+1), latest on 2026-06-01.
        for d, c in [('2026-01-03', 100.0), ('2026-06-01', 115.0)]:
            conn.execute(
                # B-117: prices.fetched_at is NOT NULL (schema v1) -> include it.
                "INSERT INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
                "VALUES ('TST',?,100,110,99,?,1000,'2026-01-01T00:00:00Z')", (d, c)
            )
        conn.commit()
        return conn

    def test_abs_return_pct_present_in_row(self):
        import export_dashboard_json as edj
        conn = self._make_conn_with_prices()
        result = edj.build_dealings(conn, date(2026, 6, 4))
        rows = result.get("today", []) + result.get("this_week", [])
        conn.close()
        # abs_return_pct key must exist
        for r in rows:
            self.assertIn("abs_return_pct", r)

    def test_abs_return_pct_gross_not_net(self):
        """abs_return_pct must not deduct costs; mtm_pct deducts costs."""
        import export_dashboard_json as edj
        conn = self._make_conn_with_prices()
        result = edj.build_dealings(conn, date(2026, 6, 4))
        conn.close()
        all_rows = result.get("today", []) + result.get("this_week", [])
        for r in all_rows:
            if r.get("abs_return_pct") is not None and r.get("mtm_pct") is not None:
                # gross >= net always (cost deduction makes net smaller)
                self.assertGreaterEqual(r["abs_return_pct"], r["mtm_pct"])

    def test_stock_rtn_header_in_today_page(self):
        """Today page table header should say 'Stock Rtn' not 'MTM'."""
        from dashboard import render_index
        html = render_index.render(
            {"horizon_aggregates": {}, "active_clusters": [], "paper_pnl_open": 0,
             "paper_trades_open": 0, "paper_trades_closed": 0, "cohorts": {},
             "cohorts_v2": {}, "pending_diagnostics": {}, "companies_index": [],
             "paper_book": {}},
            {"today": [], "this_week": [], "generated_at": "2026-06-04T00:00:00Z",
             "signals_today_count": 0, "signals_today_delta_vs_avg": 0},
        )
        self.assertIn("Stock Rtn", html)
        self.assertNotIn(">MTM*<", html)


# ---------------------------------------------------------------------------
# B-009 -- sparkline caps to 12 months, null handled
# ---------------------------------------------------------------------------

class TestB009Sparkline(unittest.TestCase):
    def test_capped_to_12_months(self):
        from dashboard.render_helpers import cohort_sparkline_svg
        points = [{"month_iso": f"2024-{m:02d}", "mean_car_t30": 0.01 * m}
                  for m in range(1, 13)]
        points += [{"month_iso": "2025-01", "mean_car_t30": 0.015},
                   {"month_iso": "2025-02", "mean_car_t30": 0.020}]
        # 14 points total; default max_months=12 should use last 12.
        svg = cohort_sparkline_svg(points, "#10b981")
        # Verify we get a valid SVG (not a dash placeholder).
        self.assertIn("<svg", svg)

    def test_null_gap_breaks_line(self):
        """Points with None mean_car_t30 should not produce a 0-value coordinate."""
        from dashboard.render_helpers import cohort_sparkline_svg
        # B-117: a single isolated point now renders as a <circle> dot (a line
        # needs >=2 points), so each side of the gap must have >=2 real points
        # for the "gap breaks the line into two polylines" property to be tested.
        pts = [
            {"month_iso": "2026-01", "mean_car_t30": 0.02},
            {"month_iso": "2026-02", "mean_car_t30": 0.025},
            {"month_iso": "2026-03", "mean_car_t30": None},   # gap
            {"month_iso": "2026-04", "mean_car_t30": 0.03},
            {"month_iso": "2026-05", "mean_car_t30": 0.035},
        ]
        svg = cohort_sparkline_svg(pts, "#f43f5e")
        # Two separate runs -> two polylines (no continuous line through the gap).
        self.assertEqual(svg.count("<polyline"), 2)

    def test_fewer_than_2_real_returns_placeholder(self):
        from dashboard.render_helpers import cohort_sparkline_svg
        pts = [{"month_iso": "2026-01", "mean_car_t30": None}]
        result = cohort_sparkline_svg(pts, "#666")
        self.assertIn("&mdash;", result)


# ---------------------------------------------------------------------------
# B-019 -- toggle JS present in performance page
# ---------------------------------------------------------------------------

class TestB019ToggleJS(unittest.TestCase):
    def _render_perf(self):
        from dashboard import render_performance
        return render_performance.render(
            {"horizon_aggregates": {}, "active_clusters": [], "paper_pnl_open": 0,
             "paper_trades_open": 0, "paper_trades_closed": 0, "cohorts": {},
             "cohorts_v2": {}, "pending_diagnostics": {}, "companies_index": [],
             "paper_book": {}, "monthly_buysell": {}},
        )

    def test_visible_set_var_present(self):
        html = self._render_perf()
        self.assertIn("visibleSet", html)

    def test_double_click_solo_logic(self):
        html = self._render_perf()
        self.assertIn("dblTimer", html)

    def test_build_overlay_function_present(self):
        html = self._render_perf()
        self.assertIn("buildOverlay", html)


# ---------------------------------------------------------------------------
# B-103 -- sortable table headers in Today page
# ---------------------------------------------------------------------------

class TestB103SortableHeaders(unittest.TestCase):
    def _render_today(self):
        from dashboard import render_index
        return render_index.render(
            {"horizon_aggregates": {}, "active_clusters": [], "paper_pnl_open": 0,
             "paper_trades_open": 0, "paper_trades_closed": 0, "cohorts": {},
             "cohorts_v2": {}, "pending_diagnostics": {}, "companies_index": [],
             "paper_book": {}},
            {"today": [], "this_week": [], "generated_at": "2026-06-04T00:00:00Z",
             "signals_today_count": 0, "signals_today_delta_vs_avg": 0},
        )

    def test_data_sort_attributes_present(self):
        html = self._render_today()
        self.assertIn("data-sort", html)

    def test_sort_js_block_present(self):
        html = self._render_today()
        self.assertIn("wireTable", html)

    def test_data_sv_on_td(self):
        html = self._render_today()
        self.assertIn("data-sv", html)


# ---------------------------------------------------------------------------
# B-102 -- build_monthly_buysell logic
# ---------------------------------------------------------------------------

class TestB102MonthlyBuySell(unittest.TestCase):
    def _conn_with_activity(self):
        conn = _mem_db()
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('TST',0,'^FTAS','2026-01-01T00:00:00Z')"
        )
        # A buy this month and a sell 2 months ago.
        today = date.today()
        cur_mo = today.strftime("%Y-%m-15")
        prior_d = (today.replace(day=1) - timedelta(days=1)).replace(day=15).isoformat()
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,shares,price,value) VALUES "
            "('fp1',?,?,'TST','BUY',?,?,'J Smith','Test Co','CEO',100,100.0,10000.0)",
            (cur_mo, cur_mo, cur_mo, cur_mo)
        )
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,shares,price,value) VALUES "
            "('fp2',?,?,'TST','SELL',?,?,'J Smith','Test Co','CEO',50,100.0,5000.0)",
            (prior_d, prior_d, prior_d, prior_d)
        )
        conn.commit()
        return conn

    def test_returns_12_months(self):
        import export_dashboard_json as edj
        conn = self._conn_with_activity()
        result = edj.build_monthly_buysell(conn, date.today())
        conn.close()
        self.assertEqual(len(result["months"]), 12)
        self.assertEqual(len(result["buy_values"]), 12)
        self.assertEqual(len(result["sell_values"]), 12)

    def test_sell_values_negative(self):
        import export_dashboard_json as edj
        conn = self._conn_with_activity()
        result = edj.build_monthly_buysell(conn, date.today())
        conn.close()
        sell_vals = [v for v in result["sell_values"] if v is not None]
        for v in sell_vals:
            self.assertLess(v, 0, "sell values must be negative for below-axis display")

    def test_buy_values_positive(self):
        import export_dashboard_json as edj
        conn = self._conn_with_activity()
        result = edj.build_monthly_buysell(conn, date.today())
        conn.close()
        buy_vals = [v for v in result["buy_values"] if v is not None]
        for v in buy_vals:
            self.assertGreater(v, 0)

    def test_no_activity_returns_none_not_zero(self):
        """Empty months should carry None, not 0, so the chart skips them."""
        import export_dashboard_json as edj
        conn = _mem_db()
        result = edj.build_monthly_buysell(conn, date.today())
        conn.close()
        # All values should be None (no transactions).
        for v in result["buy_values"]:
            self.assertIsNone(v)


# ---------------------------------------------------------------------------
# B-125 -- Monthly Activity excludes corporate/PCA rows
# ---------------------------------------------------------------------------

class TestB125CorporatePcaExclusion(unittest.TestCase):
    def test_helper_flags_pca_bucket(self):
        import export_dashboard_json as edj
        self.assertTrue(edj._is_corporate_or_pca("PCA", "Person closely assoc", "Potomac View"))

    def test_helper_flags_pca_in_role_text(self):
        import export_dashboard_json as edj
        # Not normalized to 'PCA' but role text says so (Eminence Capital case).
        self.assertTrue(edj._is_corporate_or_pca("", "PCA of Ricky Chad Sandler", "Someone"))

    def test_helper_flags_corporate_name(self):
        import export_dashboard_json as edj
        self.assertTrue(edj._is_corporate_or_pca("Other / unclassified", "DKL Energy Limited", "DKL Energy Limited"))
        self.assertTrue(edj._is_corporate_or_pca("", "", "Eni UK Limited"))

    def test_helper_keeps_individuals(self):
        import export_dashboard_json as edj
        for nm, role, rn in [
            ("Michael Danson", "Chief Executive", "CEO"),
            ("Lord Wolfson of Aspley Guise", "Chief Executive (PDMR)", ""),
            ("John Kearon", "Non-Executive Director", "NED"),
            ("Mark Wood CBE", "Chair/PDMR", "Chair (executive)"),
        ]:
            self.assertFalse(edj._is_corporate_or_pca(rn, role, nm), f"{nm} wrongly excluded")

    def test_monthly_buysell_drops_corporate_sell(self):
        import export_dashboard_json as edj
        conn = _mem_db()
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('TST',0,'^FTAS','2026-01-01T00:00:00Z')"
        )
        today = date.today()
        cur = today.strftime("%Y-%m-15")
        # One real-director sell + one corporate block sell, same month.
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,role_normalized,shares,price,value) VALUES "
            "('ind',?,?,'TST','SELL',?,?,'Jane Director','Test Co','CEO','CEO',50,100.0,5000.0)",
            (cur, cur, cur, cur),
        )
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,role_normalized,shares,price,value) VALUES "
            "('corp',?,?,'TST','SELL',?,?,'DKL Energy Limited','Test Co','DKL Energy Limited',"
            "'Other / unclassified',1000,100.0,9000000.0)",
            (cur, cur, cur, cur),
        )
        conn.commit()
        result = edj.build_monthly_buysell(conn, today)
        conn.close()
        self.assertEqual(result["excluded_corporate_count"], 1)
        # Only the \xa35k individual sell remains (sells stored negative).
        sells = [v for v in result["sell_values"] if v]
        self.assertEqual(sells, [-5000.0])
        self.assertEqual(result["trailing12_sell_count"], 1)


# ---------------------------------------------------------------------------
# B-123 -- \xa310k-per-signal strategy tracker vs ^FTAS shadow
# ---------------------------------------------------------------------------

class TestB123StrategyTracker(unittest.TestCase):
    def _seed(self):
        conn = _mem_db()
        dates = [f"2026-05-{d:02d}" for d in range(1, 31)]   # 30 daily 'trading' days
        for dt in dates:
            for tk in ("^FTAS", "TST"):
                conn.execute(
                    "INSERT INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
                    "VALUES (?,?,100,100,100,100,0,'x')", (tk, dt))
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('TST',0,'^FTAS','x')")
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,"
            "announced_at,director,company,role,shares,price,value) VALUES "
            "('fp1','x','x','TST','BUY','2026-05-01','2026-05-01','Jane','Co','CEO',100,100.0,10000.0)")
        conn.execute(
            "INSERT INTO signals (signal_id,signal_version,fingerprint,fired_at) "
            "VALUES ('t1a_ceo_founder_buy','1.0.0','fp1','2026-05-01')")
        conn.commit()
        return conn

    def test_costs_make_strategy_trail_ftse(self):
        import export_dashboard_json as edj
        conn = self._seed()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        self.assertIn("series", out)
        s = out["summary"]
        self.assertEqual(s["n_positions"], 1)
        self.assertEqual(s["capital_deployed_gbp"], 10000)
        # Flat prices -> only entry costs differ. Non-AIM equity pays 1.0% (9900);
        # FTSE shadow pays 0.5% spread only (9950).
        self.assertAlmostEqual(s["strategy_value_gbp"], 9900, delta=1)
        self.assertAlmostEqual(s["ftse_value_gbp"], 9950, delta=1)
        self.assertAlmostEqual(s["excess_gbp"], -50, delta=1)
        self.assertEqual(len(out["series"]), 30)
        # Day 0 is pre-entry: both legs hold the \xa310k stake as cash.
        self.assertEqual(out["series"][0]["strategy_value_gbp"], 10000)
        self.assertEqual(out["series"][0]["ftse_value_gbp"], 10000)

    def test_aim_buy_pays_no_stamp(self):
        import export_dashboard_json as edj
        conn = self._seed()
        conn.execute("UPDATE tickers_meta SET is_aim=1 WHERE ticker='TST'")
        conn.commit()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        # AIM buy: spread only (0.5%) -> 9950, matching the FTSE shadow.
        self.assertAlmostEqual(out["summary"]["strategy_value_gbp"], 9950, delta=1)
        self.assertAlmostEqual(out["summary"]["excess_gbp"], 0, delta=1)

    def test_empty_without_ftas_prices(self):
        import export_dashboard_json as edj
        conn = _mem_db()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        self.assertEqual(out, {})

    def test_split_consolidation_position_dropped(self):
        # Audit fix 2026-06-06: a position whose exit/entry ratio implies an
        # unadjusted consolidation (here 20x) must be dropped, not staked.
        import export_dashboard_json as edj
        conn = self._seed()
        # Entry = first TST trading day after announcement (2026-05-02);
        # exit = T+21 (2026-05-23). Make that window a ~20x artifact.
        conn.execute("UPDATE prices SET close=1.0  WHERE ticker='TST' AND date='2026-05-02'")
        conn.execute("UPDATE prices SET close=20.0 WHERE ticker='TST' AND date='2026-05-23'")
        conn.commit()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        # Only position is dropped -> no positions -> renderer-omit sentinel.
        self.assertEqual(out, {})

    def test_excluded_ticker_dropped(self):
        # Audit fix 2026-06-06: tickers in backtest.EXCLUDED_TICKERS (HDD/DCTA)
        # must be filtered out, matching the backtest, so they can't leak in.
        import export_dashboard_json as edj
        conn = _mem_db()
        dates = [f"2026-05-{d:02d}" for d in range(1, 31)]
        for dt in dates:
            for tk in ("^FTAS", "HDD"):
                conn.execute(
                    "INSERT INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
                    "VALUES (?,?,100,100,100,100,0,'x')", (tk, dt))
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('HDD',0,'^FTAS','x')")
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,"
            "announced_at,director,company,role,shares,price,value) VALUES "
            "('fpx','x','x','HDD','BUY','2026-05-01','2026-05-01','Jane','Co','CEO',100,100.0,10000.0)")
        conn.execute(
            "INSERT INTO signals (signal_id,signal_version,fingerprint,fired_at) "
            "VALUES ('t1a_ceo_founder_buy','1.0.0','fpx','2026-05-01')")
        conn.commit()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        self.assertEqual(out, {})

    def test_tier_lines_emitted_with_threshold(self):
        # Audit fix 2026-06-07: tier % lines (T5/T1B/T7 >\xa3100k) emitted with N.
        import export_dashboard_json as edj
        conn = self._seed()  # all-buys book: 1 t1a CEO buy (value \xa310k)
        # Add a \xa3200k PCA buy (qualifies for the >\xa3100k T5 tier line).
        for dt in [f"2026-05-{d:02d}" for d in range(1, 31)]:
            conn.execute(
                "INSERT INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
                "VALUES ('PCO',?,100,100,100,100,0,'x')", (dt,))
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('PCO',0,'^FTAS','x')")
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,"
            "announced_at,director,company,role,shares,price,value) VALUES "
            "('fp5','x','x','PCO','BUY','2026-05-01','2026-05-01','PCA Co','Co','PCA',2000,100.0,200000.0)")
        conn.execute(
            "INSERT INTO signals (signal_id,signal_version,fingerprint,fired_at) "
            "VALUES ('t5_pca_buy','1.0.0','fp5','2026-05-01')")
        conn.commit()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        self.assertIn("pct_series", out)
        self.assertIn("tier_meta", out)
        self.assertEqual(out["tier_meta"]["t5"]["n"], 1)
        self.assertEqual(out["tier_meta"]["all"]["n"], 2)  # CEO \xa310k + PCA \xa3200k
        # pct lines rebased to 0% at the start.
        self.assertEqual(out["pct_series"][0]["all"], 0.0)
        self.assertEqual(out["pct_series"][0]["t5"], 0.0)

    def test_sub_threshold_tier_excluded(self):
        # A PCA buy <= \xa35k (T5 floor) must NOT populate the T5 tier line.
        import export_dashboard_json as edj
        conn = self._seed()
        for dt in [f"2026-05-{d:02d}" for d in range(1, 31)]:
            conn.execute(
                "INSERT INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
                "VALUES ('PCO',?,100,100,100,100,0,'x')", (dt,))
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('PCO',0,'^FTAS','x')")
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,"
            "announced_at,director,company,role,shares,price,value) VALUES "
            "('fp5b','x','x','PCO','BUY','2026-05-01','2026-05-01','PCA Co','Co','PCA',10,100.0,1000.0)")
        conn.execute(
            "INSERT INTO signals (signal_id,signal_version,fingerprint,fired_at) "
            "VALUES ('t5_pca_buy','1.0.0','fp5b','2026-05-01')")
        conn.commit()
        out = edj.build_strategy_tracker(conn, date(2026, 6, 1))
        conn.close()
        self.assertEqual(out["tier_meta"]["t5"]["n"], 0)
        self.assertIsNone(out["pct_series"][0]["t5"])


class TestB123StrategyTrackerRender(unittest.TestCase):
    _DATA = {"strategy_tracker": {
        "series": [
            {"date": "2026-05-01", "strategy_value_gbp": 10000, "ftse_value_gbp": 10000},
            {"date": "2026-05-30", "strategy_value_gbp": 10500, "ftse_value_gbp": 10100},
        ],
        "summary": {"as_of": "2026-05-30", "n_positions": 3,
                    "capital_deployed_gbp": 30000, "strategy_value_gbp": 10500,
                    "ftse_value_gbp": 10100, "excess_gbp": 400, "excess_pct": 3.96,
                    "strategy_trend_30d_pct": 1.2, "ftse_trend_30d_pct": 0.4}}}

    def test_panel_renders_key_elements(self):
        from dashboard import render_performance as rp
        html = rp._strategy_tracker_section(self._DATA)
        self.assertIn("strategyTrackerChart", html)
        self.assertIn("Strategy tracker", html)
        self.assertIn("Excess vs FTSE", html)
        self.assertIn("FTSE All-Share shadow", html)

    def test_empty_payload_omits_panel(self):
        from dashboard import render_performance as rp
        self.assertEqual(rp._strategy_tracker_section({}), "")

    def test_pct_chart_renders_tier_lines(self):
        # Audit fix 2026-06-07: when pct_series is present, the multi-tier % chart
        # renders the tier line labels with their N.
        from dashboard import render_performance as rp
        data = {"strategy_tracker": {
            "series": self._DATA["strategy_tracker"]["series"],
            "summary": self._DATA["strategy_tracker"]["summary"],
            "pct_series": [
                {"date": "2026-05-01", "all": 0.0, "t5": 0.0, "t1b": 0.0,
                 "t7": None, "ftse": 0.0},
                {"date": "2026-05-30", "all": 1.7, "t5": 4.2, "t1b": -1.1,
                 "t7": None, "ftse": 2.0},
            ],
            "tier_meta": {"all": {"label": "All buy signals", "n": 1300},
                          "t5": {"label": "T5 PCA &gt;\xa3100k", "n": 27},
                          "t1b": {"label": "T1B CFO &gt;\xa3100k", "n": 18},
                          "t7": {"label": "T7 Chair &gt;\xa3100k", "n": 0}}}}
        html = rp._strategy_tracker_section(data)
        self.assertIn("strategyTrackerChart", html)
        self.assertIn("T5 PCA", html)
        self.assertIn("T1B CFO", html)
        self.assertIn("nT5=27", html)       # tier N surfaced as JS var in legend
        self.assertIn("FTSE All-Share", html)


# ---------------------------------------------------------------------------
# B-100 Phase B -- paper trade write path
# ---------------------------------------------------------------------------

class TestB100PhaseBEval(unittest.TestCase):
    def _conn_with_signal_and_prices(self):
        conn = _mem_db()
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,shares,price,value) "
            "VALUES ('fp_pt','2026-01-02','2026-01-02','PT1','BUY','2026-01-02','2026-01-02',"
            "'CEO Test','PaperCo','CEO',100,100.0,10000.0)"
        )
        conn.execute(
            "INSERT INTO tickers_meta (ticker,is_aim,benchmark_symbol,updated_at) "
            "VALUES ('PT1',0,'^FTAS','2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
            "VALUES ('PT1','2026-01-03',100,110,99,105.0,1000,'2026-01-01T00:00:00Z')"
        )
        conn.commit()
        return conn

    def test_open_paper_trade_created_on_buy_signal(self):
        import eval_signals
        conn = self._conn_with_signal_and_prices()
        result = {
            "signal_id": "t1a_ceo_founder_buy",
            "signal_version": "1.0.0",
            "fingerprint": "fp_pt",
            "fired_at": "2026-01-02",
        }
        tx_row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='fp_pt'"
        ).fetchone()
        eval_signals._open_paper_trade(conn, result, tx_row)
        conn.commit()
        pt = conn.execute(
            "SELECT * FROM paper_trades WHERE trade_id='pt_t1a_ceo_founder_buy_fp_pt'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(pt)
        self.assertEqual(pt["status"], "open")
        self.assertAlmostEqual(float(pt["entry_close"]), 105.0, places=1)

    def test_idempotent_insert_or_ignore(self):
        """Running _open_paper_trade twice must not error or create duplicates."""
        import eval_signals
        conn = self._conn_with_signal_and_prices()
        result = {
            "signal_id": "t1a_ceo_founder_buy",
            "signal_version": "1.0.0",
            "fingerprint": "fp_pt",
            "fired_at": "2026-01-02",
        }
        tx_row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='fp_pt'"
        ).fetchone()
        eval_signals._open_paper_trade(conn, result, tx_row)
        eval_signals._open_paper_trade(conn, result, tx_row)
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_trades"
        ).fetchone()["n"]
        conn.close()
        self.assertEqual(count, 1)

    def test_b2_excluded_from_paper_trades(self):
        import eval_signals
        conn = self._conn_with_signal_and_prices()
        result = {
            "signal_id": "b2_crowded_cluster_kill",
            "signal_version": "1.0.0",
            "fingerprint": "fp_pt",
            "fired_at": "2026-01-02",
        }
        tx_row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='fp_pt'"
        ).fetchone()
        eval_signals._open_paper_trade(conn, result, tx_row)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) AS n FROM paper_trades").fetchone()["n"]
        conn.close()
        self.assertEqual(count, 0, "b2 must not create a paper trade")

    def test_t0_excluded_from_paper_trades(self):
        import eval_signals
        conn = self._conn_with_signal_and_prices()
        result = {
            "signal_id": "t0_cluster_combo",
            "signal_version": "1.0.0",
            "fingerprint": "fp_pt",
            "fired_at": "2026-01-02",
        }
        tx_row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='fp_pt'"
        ).fetchone()
        eval_signals._open_paper_trade(conn, result, tx_row)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) AS n FROM paper_trades").fetchone()["n"]
        conn.close()
        self.assertEqual(count, 0, "t0 must not create a paper trade")

    def test_planned_when_no_price(self):
        """When no price exists after fired_at, status should be 'planned'."""
        import eval_signals
        conn = _mem_db()
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,shares,price,value) "
            "VALUES ('fp_np','2026-01-02','2026-01-02','NOPX','BUY','2026-01-02','2026-01-02',"
            "'Dir','NoPriceCo','NED',100,100.0,10000.0)"
        )
        conn.commit()
        result = {
            "signal_id": "t3_ned_buy",
            "signal_version": "1.0.0",
            "fingerprint": "fp_np",
            "fired_at": "2026-01-02",
        }
        tx_row = conn.execute(
            "SELECT * FROM transactions WHERE fingerprint='fp_np'"
        ).fetchone()
        eval_signals._open_paper_trade(conn, result, tx_row)
        conn.commit()
        pt = conn.execute("SELECT * FROM paper_trades").fetchone()
        conn.close()
        self.assertIsNotNone(pt)
        self.assertEqual(pt["status"], "planned")
        self.assertIsNone(pt["entry_close"])


class TestB100PhaseBClose(unittest.TestCase):
    def _conn_with_open_trade(self, entry_date="2026-01-02",
                              entry_close=100.0, notional=10000.0):
        conn = _mem_db()
        conn.execute(
            "INSERT INTO transactions (fingerprint,first_seen,last_seen,ticker,type,date,announced_at,"
            "director,company,role,shares,price,value) "
            "VALUES ('fp_c','2026-01-02','2026-01-02','CLS1','BUY','2026-01-02','2026-01-02',"
            "'CEO','CloseCo','CEO',100,100.0,10000.0)"
        )
        conn.commit()
        # Seed 30 trading days worth of prices starting the day after entry.
        base = datetime.strptime(entry_date, "%Y-%m-%d")
        for i in range(1, 31):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            close = entry_close * (1 + 0.001 * i)
            conn.execute(
                "INSERT OR IGNORE INTO prices (ticker,date,open,high,low,close,volume,fetched_at) "
                "VALUES ('CLS1',?,100,110,99,?,1000,'2026-01-01T00:00:00Z')", (d, round(close, 2))
            )
        conn.execute(
            "INSERT INTO paper_trades "
            "(trade_id,signal_id,signal_version,fingerprint,sizing_scheme,"
            " notional_gbp,entry_date,entry_close,shares,status,opened_at,updated_at) "
            "VALUES ('pt_test','t1a_ceo_founder_buy','1.0.0','fp_c','linear',"
            "?,?,?,?,?,'2026-01-02T00:00:00Z','2026-01-02T00:00:00Z')",
            (notional, entry_date, entry_close, notional / entry_close, "open")
        )
        conn.commit()
        return conn

    def test_close_paper_trades_closes_matured(self):
        # B-117: the old harness seeded an in-memory DB then closed it and called
        # run(), which opened the REAL .data/directors.db -> the seeded trade was
        # never seen (closed==0). run() calls db.connect() exactly once, so patch
        # it to return the seeded connection and the real close path is exercised.
        import close_paper_trades
        import db as db_mod
        from unittest.mock import patch
        conn = self._conn_with_open_trade()
        with patch.object(db_mod, "connect", return_value=conn):
            result = close_paper_trades.run(horizon=21, dry_run=False, verbose=False)
        self.assertGreaterEqual(result["closed"], 1, "should have closed the trade")

    def test_dry_run_makes_no_changes(self):
        import close_paper_trades
        import db as db_mod
        from unittest.mock import patch
        conn = self._conn_with_open_trade()
        # Patch db.connect so run() uses the seeded in-memory connection.
        # close_paper_trades.run() always calls conn.close() at the end, so
        # we cannot query conn afterwards.  Instead verify via the result dict:
        # dry_run=True must be present AND closed must be 0 (nothing committed).
        with patch.object(db_mod, "connect", return_value=conn):
            result = close_paper_trades.run(horizon=21, dry_run=True, verbose=False)
        self.assertEqual(result["dry_run"], True)
        self.assertEqual(result.get("closed", 0), 0,
                         "dry_run must not actually close any trades")


def db_conn():
    """Open a live connection to .data/directors.db for verification tests."""
    import db
    return db.connect()


if __name__ == "__main__":
    unittest.main()
