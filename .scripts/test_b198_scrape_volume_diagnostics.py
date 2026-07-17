"""B-198 (2026-07-06) — scrape-volume diagnostics unit tests.

Covers the two additions made after a real, confirmed PDMR under-collection
was diagnosed (~2/day landing vs ~22/day of real filings, discovered only by
manually pulling raw GitHub Actions logs because nothing in the daily-refresh
CI console showed discovery/parse counts):

  1. run_scrape.py: `_discover_rows()` now tracks per-source (index/archive)
     raw yield counts and captured errors, and `run()` prints a single
     machine-parseable `SCRAPE_STATS <json>` line unconditionally.
  2. refresh_all.py: `_check_scrape_volume_anomaly()` parses that line and
     compares today's insert count to a trailing-week baseline computed from
     the local (freshly-downloaded-from-Postgres) SQLite mirror, printing a
     non-blocking `::warning::` GitHub Actions annotation when volume
     collapses -- but never on a genuinely quiet day with no baseline, and
     never in a way that changes the pipeline's pass/fail status.

Self-cleaning: monkey-patches `db.DB_DIR`/`db.DB_PATH` to a tempdir for every
test and restores them in tearDown. Never touches the real
`.data/directors.db`. No live network (scraper.check_robots / iter_index /
iter_archive are all mocked).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
import run_scrape as rs
import refresh_all as ra


SCRAPE_STATS_RE = re.compile(r"^SCRAPE_STATS\s+(\{.*\})\s*$", re.MULTILINE)


def _fake_row(rns_id: str, ticker: str = "AAA") -> dict:
    return {
        "rns_id": rns_id,
        "url": f"https://www.investegate.co.uk/announcement/rns/{ticker.lower()}/x/{rns_id}",
        "headline": f"{ticker} Director Dealing",
        "ticker_hint": ticker,
        "announced_at": None,
    }


class _TempDbTestCase(unittest.TestCase):
    """Shared tempdir-DB scaffolding, mirroring test_stage_02.py's pattern."""

    def setUp(self):
        self._tmp_dir_str = tempfile.mkdtemp(prefix="dd_b198_")
        self._tmp_dir = Path(self._tmp_dir_str)
        self._real_db_dir = db.DB_DIR
        self._real_db_path = db.DB_PATH
        db.DB_DIR = self._tmp_dir
        db.DB_PATH = self._tmp_dir / "directors.db"
        # Force schema creation (mirrors test_stage_02 case_14 pattern).
        conn = db.connect()
        conn.close()

    def tearDown(self):
        db.DB_DIR = self._real_db_dir
        db.DB_PATH = self._real_db_path
        shutil.rmtree(self._tmp_dir_str, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. run_scrape.py: SCRAPE_STATS emission + per-source counters
# ---------------------------------------------------------------------------

class TestScrapeStatsEmission(_TempDbTestCase):

    def _run_with_sources(self, index_rows, archive_rows, archive_raises=None):
        """Run run_scrape.run(--dry-run --no-llm) with discovery mocked.

        Returns (rc, stdout_text, stats_dict_or_None).
        """
        def fake_iter_index(start, end, max_pages=20):
            for r in index_rows:
                yield r

        def fake_iter_archive(start, end):
            if archive_raises is not None:
                raise archive_raises
            for r in archive_rows:
                yield r

        # Every discovered row is treated as an already-parsed clean BUY so
        # the dry-run "would insert" path is exercised without needing real
        # filing HTML. required_fields for _row_is_ingestable: type/price/value.
        fake_extracted = [{
            "fingerprint": "fp-x", "ticker": "AAA", "type": "BUY",
            "shares": 100, "price": 1.0, "value": 100.0,
        }]

        args = rs.build_parser().parse_args(
            ["--dry-run", "--no-llm", "--from", "2026-06-01", "--to", "2026-06-02"]
        )

        # Write a REAL cached HTML file per filing rather than mocking
        # pathlib.Path.read_text globally -- a blanket patch on read_text
        # also intercepts db.py's own SCHEMA_PATH.read_text() during the
        # nested db.connect() inside run(), corrupting the schema SQL.
        fake_html_dir = self._tmp_dir / "fake_cache"
        fake_html_dir.mkdir(exist_ok=True)

        def fake_fetch_filing(rns_id, url):
            p = fake_html_dir / f"{rns_id}.html"
            if not p.exists():
                p.write_text("<html><body>fake filing</body></html>", encoding="utf-8")
            return p

        buf = io.StringIO()
        with mock.patch.object(rs.scraper, "check_robots", return_value=None), \
             mock.patch.object(rs.scraper, "iter_index", side_effect=fake_iter_index), \
             mock.patch.object(rs.scraper, "iter_archive", side_effect=fake_iter_archive), \
             mock.patch.object(rs.scraper, "fetch_filing", side_effect=fake_fetch_filing), \
             mock.patch.object(rs.parse_pdmr, "parse_announcement",
                               return_value=(fake_extracted, [], "regex")), \
             redirect_stdout(buf):
            rc = rs.run(args)

        text = buf.getvalue()
        m = SCRAPE_STATS_RE.search(text)
        stats = json.loads(m.group(1)) if m else None
        return rc, text, stats

    def test_per_source_counts_and_dedup(self):
        # index yields 1,2,3; archive yields 2,3,4 (2 overlap with index).
        index_rows = [_fake_row("1"), _fake_row("2"), _fake_row("3")]
        archive_rows = [_fake_row("2"), _fake_row("3"), _fake_row("4")]
        rc, _text, stats = self._run_with_sources(index_rows, archive_rows)

        self.assertIsNotNone(stats, "SCRAPE_STATS line missing from output")
        self.assertEqual(stats["index_count"], 3)
        self.assertEqual(stats["archive_count"], 3)
        self.assertIsNone(stats["index_error"])
        self.assertIsNone(stats["archive_error"])
        # 4 distinct rns_ids across both sources after dedup.
        self.assertEqual(stats["filings_seen"], 4)

    def test_archive_error_is_captured_and_index_still_counted(self):
        index_rows = [_fake_row("10"), _fake_row("11")]
        rc, text, stats = self._run_with_sources(
            index_rows, [], archive_raises=RuntimeError("advanced-search 500")
        )

        self.assertIsNotNone(stats)
        self.assertEqual(stats["index_count"], 2)
        self.assertEqual(stats["archive_count"], 0)
        self.assertIn("advanced-search 500", stats["archive_error"] or "")
        # The soft-backstop failure must not crash the run.
        self.assertEqual(rc, 0)
        self.assertIn("discovery source 'archive' failed", text)

    def test_stats_line_is_well_formed_json_with_expected_keys(self):
        rc, _text, stats = self._run_with_sources(
            [_fake_row("20")], [_fake_row("21")]
        )
        expected_keys = {
            "window_start", "window_end", "filings_seen", "index_count",
            "index_error", "archive_count", "archive_error", "clean_writes",
            "inserts", "pending_count", "excluded_at_ingest",
            "llm_missing_key_count",
        }
        self.assertEqual(expected_keys, set(stats.keys()))


# ---------------------------------------------------------------------------
# 1b. B-199: missing ANTHROPIC_API_KEY canary + llm_missing_key_count
# ---------------------------------------------------------------------------

class TestMissingLlmApiKeyCanary(_TempDbTestCase):
    """Root cause of the confirmed PDMR under-collection (2026-07-06 report,
    re-confirmed 2026-07-17 with a live 6-day sample showing only 19/117 real
    filings reaching Postgres): daily-refresh.yml never set ANTHROPIC_API_KEY
    on the pipeline step, so every LLM-fallback attempt raised
    MissingApiKeyError, silently routing the filing to pending. These tests
    cover the loud startup canary and the per-run counter that make this
    class of failure impossible to miss in the CI log going forward.
    """

    def _run_no_key_scenario(self, env_has_key: bool):
        import llm_parser

        index_rows = [_fake_row("100")]

        def fake_iter_index(start, end, max_pages=20):
            for r in index_rows:
                yield r

        def fake_iter_archive(start, end):
            return
            yield  # pragma: no cover - empty generator

        fake_html_dir = self._tmp_dir / "fake_cache"
        fake_html_dir.mkdir(exist_ok=True)

        def fake_fetch_filing(rns_id, url):
            p = fake_html_dir / f"{rns_id}.html"
            if not p.exists():
                p.write_text("<html><body>fake filing</body></html>", encoding="utf-8")
            return p

        args = rs.build_parser().parse_args(
            ["--from", "2026-06-01", "--to", "2026-06-02"]
        )

        # Patch the whole environ dict (clear=True) so the key is genuinely
        # absent rather than just overridden, but seed it with everything
        # already present (minus the key) so unrelated stdlib/tempfile
        # behaviour isn't disturbed.
        env_vars = dict(os.environ)
        if env_has_key:
            env_vars["ANTHROPIC_API_KEY"] = "sk-fake-test-key"
        else:
            env_vars.pop("ANTHROPIC_API_KEY", None)

        buf = io.StringIO()
        with mock.patch.dict("os.environ", env_vars, clear=True), \
             mock.patch.object(rs.scraper, "check_robots", return_value=None), \
             mock.patch.object(rs.scraper, "iter_index", side_effect=fake_iter_index), \
             mock.patch.object(rs.scraper, "iter_archive", side_effect=fake_iter_archive), \
             mock.patch.object(rs.scraper, "fetch_filing", side_effect=fake_fetch_filing), \
             mock.patch.object(rs.parse_pdmr, "parse_announcement",
                               return_value=([], ["required_fields_missing"], "regex")), \
             mock.patch.object(rs.llm_cost, "start_run", return_value="test-run-id"), \
             mock.patch.object(rs.llm_cost, "check_budget", return_value=None), \
             mock.patch.object(rs.llm_cost, "end_run", return_value=None), \
             mock.patch.object(
                 llm_parser, "parse_with_llm",
                 side_effect=llm_parser.MissingApiKeyError(
                     "ANTHROPIC_API_KEY not set")), \
             mock.patch.object(rs.db_health, "check", return_value=True), \
             mock.patch.object(rs.db_health, "backup", return_value=True), \
             mock.patch.object(rs.db_health, "seal", return_value=None), \
             redirect_stdout(buf):
            # This test runs without --dry-run (the LLM-fallback branch is
            # skipped entirely in dry-run mode), so db_health.check/backup
            # would otherwise touch db_health.py's own hardcoded
            # ROOT/.data/directors.db path -- a SEPARATE constant from
            # db.DB_PATH, not covered by _TempDbTestCase's monkeypatch.
            # Mocked out above to keep this test fully tempdir-isolated,
            # matching this file's "never touches the real .data/directors.db"
            # contract.
            rc = rs.run(args)

        text = buf.getvalue()
        m = SCRAPE_STATS_RE.search(text)
        stats = json.loads(m.group(1)) if m else None
        return rc, text, stats

    def test_missing_key_prints_startup_canary_and_counts_blocked_rows(self):
        rc, text, stats = self._run_no_key_scenario(env_has_key=False)

        self.assertEqual(rc, 0)  # non-fatal — a config warning, not a crash
        self.assertIn("::error::ANTHROPIC_API_KEY is not set", text)
        self.assertIsNotNone(stats, "SCRAPE_STATS line missing from output")
        self.assertEqual(stats["llm_missing_key_count"], 1)
        self.assertIn(
            "filing(s) this run were routed to pending SOLELY because "
            "ANTHROPIC_API_KEY was missing", text,
        )

    def test_key_present_suppresses_the_canary(self):
        rc, text, stats = self._run_no_key_scenario(env_has_key=True)

        self.assertNotIn("ANTHROPIC_API_KEY is not set", text)
        # The mocked parse_with_llm still raises MissingApiKeyError in this
        # branch (it's a fixed side_effect) -- that's fine, this test only
        # asserts the startup canary is gated on the real env var, not that
        # every call succeeds.


# ---------------------------------------------------------------------------
# 2. refresh_all.py: _check_scrape_volume_anomaly
# ---------------------------------------------------------------------------

class TestScrapeVolumeAnomalyCheck(_TempDbTestCase):

    def _insert_fake_transaction(self, conn, fingerprint: str, first_seen: str,
                                  ticker: str = "AAA") -> None:
        conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, role_normalized, type, shares, price, "
            "value, context, url, announced_at, cluster_id, first_time_buy, "
            "parser_source, buy_strictness, resulting_shares"
            ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fingerprint, first_seen, first_seen, "2026-06-01", ticker,
                "Acme Plc", "Alice Smith", "Director", "director", "BUY",
                100, 1.0, 100.0, None, "https://example.com", first_seen,
                None, 0, "regex", None, None,
            ),
        )
        conn.commit()

    def _seed_baseline(self, per_day: int, days: int = 7) -> None:
        """Seed `per_day` transactions on each of the last `days` days
        (yesterday back through `days` days ago), all with distinct
        fingerprints, so first_seen-based daily counts average to per_day.
        """
        from datetime import datetime, timedelta, timezone
        conn = db.connect()
        try:
            today = datetime.now(timezone.utc)
            n = 0
            for day_offset in range(1, days + 1):
                day = today - timedelta(days=day_offset)
                for i in range(per_day):
                    n += 1
                    ts = day.replace(hour=10, minute=0, second=0).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    self._insert_fake_transaction(conn, f"fp-seed-{n}", ts)
        finally:
            conn.close()

    def _stdout_for_stats(self, stats: dict) -> str:
        return "SCRAPE_STATS " + json.dumps(stats)

    def _base_stats(self, **overrides) -> dict:
        stats = {
            "window_start": "2026-06-30", "window_end": "2026-07-06",
            "filings_seen": 5, "index_count": 5, "index_error": None,
            "archive_count": 5, "archive_error": None, "clean_writes": 5,
            "inserts": 5, "pending_count": 0, "excluded_at_ingest": 0,
        }
        stats.update(overrides)
        return stats

    def test_warns_on_real_collapse_vs_baseline(self):
        self._seed_baseline(per_day=20)  # ~20/day trailing average
        stdout = self._stdout_for_stats(self._base_stats(inserts=2))

        buf = io.StringIO()
        with redirect_stdout(buf):
            ra._check_scrape_volume_anomaly(stdout)
        out = buf.getvalue()
        self.assertIn("::warning::", out)
        self.assertIn("PDMR volume looks low", out)
        self.assertIn("2 new transactions", out)

    def test_no_warning_when_close_to_baseline(self):
        self._seed_baseline(per_day=20)
        stdout = self._stdout_for_stats(self._base_stats(inserts=18))

        buf = io.StringIO()
        with redirect_stdout(buf):
            ra._check_scrape_volume_anomaly(stdout)
        self.assertNotIn("::warning::", buf.getvalue())

    def test_no_warning_without_history(self):
        # Empty transactions table -- nothing to compare against.
        stdout = self._stdout_for_stats(self._base_stats(inserts=0))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ra._check_scrape_volume_anomaly(stdout)
        self.assertNotIn("::warning::", buf.getvalue())

    def test_no_warning_when_baseline_itself_is_thin(self):
        # A genuinely low-volume period (e.g. 2/day baseline) must not
        # trigger false alarms even if today's count is 0.
        self._seed_baseline(per_day=2)
        stdout = self._stdout_for_stats(self._base_stats(inserts=0))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ra._check_scrape_volume_anomaly(stdout)
        self.assertNotIn("::warning::", buf.getvalue())

    def test_missing_stats_line_is_silently_skipped(self):
        self._seed_baseline(per_day=20)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ra._check_scrape_volume_anomaly("no stats line here at all")
        self.assertNotIn("::warning::", buf.getvalue())

    def test_never_raises_on_malformed_stats_json(self):
        self._seed_baseline(per_day=20)
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                ra._check_scrape_volume_anomaly("SCRAPE_STATS {not valid json}")
            except Exception as e:  # noqa: BLE001
                self.fail(f"_check_scrape_volume_anomaly raised: {e!r}")
        self.assertNotIn("::warning::", buf.getvalue())

    def test_error_hint_included_when_source_failed(self):
        self._seed_baseline(per_day=20)
        stdout = self._stdout_for_stats(self._base_stats(
            inserts=1, archive_error="RuntimeError('advanced-search 500')",
        ))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ra._check_scrape_volume_anomaly(stdout)
        out = buf.getvalue()
        self.assertIn("::warning::", out)
        self.assertIn("archive_error=", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
