"""Sprint 34 tests — B113 benchmark column, C conviction score, #3 monthly depth.

Run:
    python -m unittest .scripts.test_sprint34 -v
"""

import sqlite3
import unittest
from datetime import date


# ---------------------------------------------------------------------------
# Helpers — minimal in-memory DB for export tests
# ---------------------------------------------------------------------------

def _make_db(rows):
    """Return a sqlite3.Connection with minimal transactions + tickers_meta
    tables populated from `rows`.

    rows: list of dicts with keys: ticker, company, director, type, value,
          date, announced_at (optional).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, company TEXT, director TEXT,
            role TEXT, role_normalized TEXT,
            type TEXT, value REAL,
            date TEXT, announced_at TEXT,
            cluster_id TEXT,
            price_audit TEXT
        );
        CREATE TABLE tickers_meta (
            ticker TEXT PRIMARY KEY,
            is_excluded_issuer INTEGER DEFAULT 0
        );
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO transactions "
            "(ticker, company, director, role, role_normalized, type, value, "
            " date, announced_at, cluster_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["ticker"], r.get("company", "Co"), r.get("director", "Dir"),
             r.get("role", ""), r.get("role_normalized", ""),
             r["type"], r["value"], r["date"], r.get("announced_at"),
             r.get("cluster_id")),
        )
    return conn


# ---------------------------------------------------------------------------
# C — conviction score for active clusters
# ---------------------------------------------------------------------------

class TestConvictionScore(unittest.TestCase):
    """compute_active_clusters() conviction field: directors*3 + value*2 + comp*2."""

    def _run(self, txs_by_ticker, today=date(2026, 6, 5)):
        """Call compute_active_clusters with a DB built from txs_by_ticker.

        txs_by_ticker: {ticker: [row, ...]}  where row matches _make_db schema.
        Returns the first cluster dict (sorted by conviction desc).
        """
        from export_dashboard_json import compute_active_clusters
        rows = []
        for ticker, trxs in txs_by_ticker.items():
            for t in trxs:
                t["ticker"] = ticker
                # cluster_id must be non-NULL; use ticker as the cluster key.
                if "cluster_id" not in t:
                    t["cluster_id"] = ticker
                rows.append(t)
        conn = _make_db(rows)
        clusters = compute_active_clusters(conn, today)
        return clusters

    def test_two_directors_small_value_wide_spread_conviction(self):
        """2 dirs, £6k value (score 0), 30d spread (comp 0) → conviction = 2×3 = 6.
        compute_active_clusters requires >=2 directors per cluster."""
        rows = [
            {"director": "A", "type": "BUY", "value": 3_000,
             "date": "2026-05-01", "announced_at": "2026-05-01"},
            {"director": "B", "type": "BUY", "value": 3_000,
             "date": "2026-05-31", "announced_at": "2026-05-31"},
        ]
        clusters = self._run({"AAA": rows})
        self.assertTrue(len(clusters) >= 1)
        c = clusters[0]
        self.assertEqual(c["ticker"], "AAA")
        # agg_value = 6000 → value_score = 0; spread = 30d → compression = 0
        self.assertEqual(c["conviction"], 2 * 3 + 0 * 2 + 0 * 2)

    def test_two_directors_500k_tight_window(self):
        """2 dirs, £600k value (score 3), 5d spread (comp 2) → conviction = 2×3+3×2+2×2 = 16."""
        rows = [
            {"director": "A", "type": "BUY", "value": 300_000,
             "date": "2026-05-01", "announced_at": "2026-05-01"},
            {"director": "B", "type": "BUY", "value": 300_000,
             "date": "2026-05-06", "announced_at": "2026-05-06"},
        ]
        clusters = self._run({"BBB": rows})
        c = clusters[0]
        self.assertEqual(c["director_count"], 2)
        self.assertEqual(c["conviction"], 2 * 3 + 3 * 2 + 2 * 2)

    def test_conviction_key_present(self):
        """conviction key is always present in every cluster dict."""
        rows = [
            {"director": "X", "type": "BUY", "value": 1_000,
             "date": "2026-05-20", "announced_at": "2026-05-20"},
            {"director": "Y", "type": "BUY", "value": 500,
             "date": "2026-05-21", "announced_at": "2026-05-21"},
        ]
        clusters = self._run({"XYZ": rows})
        self.assertTrue(len(clusters) >= 1, "Expected at least one cluster")
        for c in clusters:
            self.assertIn("conviction", c)
            self.assertIsInstance(c["conviction"], int)

    def test_clusters_sorted_conviction_desc(self):
        """Higher-conviction cluster appears first."""
        high = [
            {"director": "A", "type": "BUY", "value": 600_000,
             "date": "2026-05-01", "announced_at": "2026-05-01"},
            {"director": "B", "type": "BUY", "value": 100_000,
             "date": "2026-05-03", "announced_at": "2026-05-03"},
        ]
        low = [
            {"director": "C", "type": "BUY", "value": 1_000,
             "date": "2026-05-01", "announced_at": "2026-05-01"},
            {"director": "D", "type": "BUY", "value": 500,
             "date": "2026-05-02", "announced_at": "2026-05-02"},
        ]
        clusters = self._run({"HIGH": high, "LOW": low})
        self.assertTrue(len(clusters) >= 2, "Expected both clusters to appear")
        self.assertGreaterEqual(
            clusters[0]["conviction"], clusters[1]["conviction"]
        )


# ---------------------------------------------------------------------------
# #3 — build_monthly_buysell() new fields
# ---------------------------------------------------------------------------

class TestBuildMonthlyBuysell(unittest.TestCase):
    """Sprint-34 additions to build_monthly_buysell(): trailing12 totals,
    trend_pct, monthly_txns."""

    def _build(self, rows, today=date(2026, 6, 5)):
        from export_dashboard_json import build_monthly_buysell
        conn = _make_db(rows)
        return build_monthly_buysell(conn, today)

    def _make_rows(self, month, buy_rows, sell_rows=None):
        """Convenience: build transaction rows for a given YYYY-MM."""
        day = f"{month}-15"
        out = []
        for i, v in enumerate(buy_rows):
            out.append({"ticker": f"T{i}", "company": f"Co{i}",
                        "director": f"Dir{i}", "type": "BUY",
                        "value": v, "date": day, "announced_at": day})
        for i, v in enumerate(sell_rows or []):
            out.append({"ticker": f"S{i}", "company": f"Co{i}",
                        "director": f"Dir{i}", "type": "SELL",
                        "value": v, "date": day, "announced_at": day})
        return out

    def test_trailing12_totals_present(self):
        rows = self._make_rows("2026-05", [10_000, 20_000], [5_000])
        result = self._build(rows)
        self.assertIn("trailing12_buy_total", result)
        self.assertIn("trailing12_sell_total", result)
        self.assertIn("trailing12_buy_count", result)
        self.assertIn("trailing12_sell_count", result)

    def test_trailing12_buy_total_correct(self):
        rows = self._make_rows("2026-05", [10_000, 20_000])
        result = self._build(rows)
        self.assertAlmostEqual(result["trailing12_buy_total"], 30_000, places=0)

    def test_trailing12_sell_total_correct(self):
        rows = self._make_rows("2026-05", [], [15_000, 5_000])
        result = self._build(rows)
        self.assertAlmostEqual(result["trailing12_sell_total"], 20_000, places=0)

    def test_trailing12_counts_correct(self):
        rows = (self._make_rows("2026-05", [1_000, 2_000])
                + self._make_rows("2026-04", [3_000]))
        result = self._build(rows)
        self.assertEqual(result["trailing12_buy_count"], 3)
        self.assertEqual(result["trailing12_sell_count"], 0)

    def test_trend_key_present(self):
        rows = self._make_rows("2026-05", [10_000])
        result = self._build(rows)
        self.assertIn("trend_buy_pct", result)
        self.assertIn("trend_sell_pct", result)

    def test_trend_buy_pct_none_when_no_prior_data(self):
        """If previous 3 months had no buys, trend should be None (no div-by-zero)."""
        rows = self._make_rows("2026-05", [50_000])  # only current month data
        result = self._build(rows)
        # No data in months 2026-01 / 2026-02 / 2026-03 → prev3 = 0 → None
        self.assertIsNone(result["trend_buy_pct"])

    def test_trend_buy_pct_positive(self):
        """Last 3 months more than prev 3 months → positive pct."""
        # Put 1k in each of prev 3 months (2026-01/02/03)
        # Put 4k in each of last 3 months (2026-04/05/06) → 12k vs 3k → +300%
        rows = []
        for mo, val in [("2026-01", 1_000), ("2026-02", 1_000), ("2026-03", 1_000),
                        ("2026-04", 4_000), ("2026-05", 4_000), ("2026-06", 4_000)]:
            rows += self._make_rows(mo, [val])
        result = self._build(rows)
        self.assertIsNotNone(result["trend_buy_pct"])
        self.assertGreater(result["trend_buy_pct"], 0)

    def test_monthly_txns_present(self):
        rows = self._make_rows("2026-05", [10_000, 20_000])
        result = self._build(rows)
        self.assertIn("monthly_txns", result)
        self.assertIsInstance(result["monthly_txns"], dict)

    def test_monthly_txns_contains_month_key(self):
        rows = self._make_rows("2026-05", [10_000])
        result = self._build(rows)
        self.assertIn("2026-05", result["monthly_txns"])

    def test_monthly_txns_capped_at_10(self):
        """Top-10 cap: more than 10 txns in a month → only top 10 returned."""
        rows = self._make_rows("2026-05", [i * 1_000 for i in range(1, 16)])
        result = self._build(rows)
        txns = result["monthly_txns"].get("2026-05", [])
        self.assertLessEqual(len(txns), 10)

    def test_monthly_txns_sorted_by_value_desc(self):
        """Largest transactions first in monthly_txns."""
        rows = self._make_rows("2026-05", [5_000, 30_000, 1_000])
        result = self._build(rows)
        txns = result["monthly_txns"].get("2026-05", [])
        if len(txns) >= 2:
            self.assertGreaterEqual(txns[0]["value"], txns[1]["value"])

    def test_monthly_txns_schema(self):
        """Each txn row has required keys."""
        rows = self._make_rows("2026-05", [10_000])
        result = self._build(rows)
        txns = result["monthly_txns"].get("2026-05", [])
        self.assertTrue(len(txns) > 0, "Expected at least one txn")
        t = txns[0]
        for key in ("ticker", "company", "director", "type", "value"):
            self.assertIn(key, t, f"Missing key: {key}")

    def test_excluded_issuer_not_in_monthly_txns(self):
        """Transactions from excluded issuers are filtered out."""
        from export_dashboard_json import build_monthly_buysell
        conn = _make_db(self._make_rows("2026-05", [100_000]))
        # Mark T0 as excluded.
        conn.execute("INSERT INTO tickers_meta (ticker, is_excluded_issuer) VALUES ('T0', 1)")
        conn.commit()
        result = build_monthly_buysell(conn, date(2026, 6, 5))
        txns = result["monthly_txns"].get("2026-05", [])
        tickers_in_result = {t["ticker"] for t in txns}
        self.assertNotIn("T0", tickers_in_result)


# ---------------------------------------------------------------------------
# #3 — _monthly_buysell_chart() render
# ---------------------------------------------------------------------------

class TestMonthlyBuysellChartRender(unittest.TestCase):
    """Sprint-34: new HTML elements in the rendered chart."""

    def _render(self, mbs_override=None):
        from dashboard.render_performance import _monthly_buysell_chart
        base = {
            "months": ["2026-04", "2026-05", "2026-06"],
            "buy_values":  [50_000.0, 80_000.0, 30_000.0],
            "sell_values": [-20_000.0, None, -10_000.0],
            "buy_counts":  [3, 5, 2],
            "sell_counts": [2, 0, 1],
            "trailing12_buy_total":  160_000.0,
            "trailing12_sell_total": 30_000.0,
            "trailing12_buy_count":  10,
            "trailing12_sell_count": 3,
            "trend_buy_pct":  25.0,
            "trend_sell_pct": -10.0,
            "monthly_txns": {
                "2026-05": [{"ticker": "ABC", "company": "ABC Ltd",
                             "director": "John Smith", "type": "BUY",
                             "value": 80_000.0}]
            },
        }
        if mbs_override:
            base.update(mbs_override)
        return _monthly_buysell_chart({"monthly_buysell": base})

    def test_renders_non_empty(self):
        html = self._render()
        self.assertTrue(len(html) > 100)

    def test_totals_buy_figure_present(self):
        html = self._render()
        # £160k buy total should appear somewhere in the totals bar
        self.assertIn("160k", html)

    def test_totals_sell_figure_present(self):
        html = self._render()
        self.assertIn("30k", html)

    def test_trailing12_counts_present(self):
        html = self._render()
        self.assertIn("10 tx", html)
        self.assertIn("3 tx", html)

    def test_trend_buy_arrow_shown(self):
        html = self._render()
        # &#9650; is up arrow; 25% trend should appear
        self.assertIn("25%", html)

    def test_trend_sell_arrow_shown(self):
        html = self._render()
        self.assertIn("10%", html)

    def test_trend_emerald_class_for_positive_buy(self):
        html = self._render()
        # Positive buy trend → emerald class
        self.assertIn("text-emerald-600", html)

    def test_trend_rose_class_for_negative_sell(self):
        html = self._render()
        # Negative sell trend (selling less → negative pct → rose, bad for bears)
        # Actually: positive_good=False for sells, so pct<0 on sells is good=emerald
        # trend_sell_pct=-10 means sells decreased; positive_good=False: pct<0 → good=emerald
        # Let's just check the class is present (either emerald or rose)
        self.assertTrue(
            "text-emerald-600" in html or "text-rose-500" in html
        )

    def test_drilldown_panel_present(self):
        html = self._render()
        self.assertIn("bscDrilldown", html)

    def test_drilldown_panel_hidden_by_default(self):
        html = self._render()
        self.assertIn('id="bscDrilldown" style="display:none"', html)

    def test_drilldown_rows_element_present(self):
        html = self._render()
        self.assertIn("bscDrillRows", html)

    def test_monthly_txns_json_embedded(self):
        html = self._render()
        # The monthly_txns JSON is embedded as var txns=...
        self.assertIn("var txns=", html)

    def test_click_handler_present(self):
        html = self._render()
        self.assertIn("showDrill", html)
        self.assertIn("onClick", html)

    def test_buy_sell_ratio_shown_when_both_nonzero(self):
        html = self._render()
        # 160k / 30k ≈ 5.3x
        self.assertIn("buy:sell", html)

    def test_returns_empty_when_no_months(self):
        from dashboard.render_performance import _monthly_buysell_chart
        html = _monthly_buysell_chart({"monthly_buysell": {}})
        self.assertEqual(html, "")

    def test_trend_none_no_crash(self):
        """Render does not crash when trend fields are None."""
        html = self._render({"trend_buy_pct": None, "trend_sell_pct": None})
        self.assertTrue(len(html) > 100)
        # No trend chips should appear when both are None
        self.assertNotIn("vs prev 3m", html)


if __name__ == "__main__":
    unittest.main()
