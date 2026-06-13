"""B-167 -- unit tests for backfill_urls.py (url + announced_at restore).

No network, no real DB: tempfile DB via mock-patched db.DB_PATH, synthetic
cache HTML in a TemporaryDirectory, parse_announcement mocked where the
test exercises matching rather than parsing.

Run:  python -m unittest test_b167_url_backfill -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import backfill_urls
import db as db_mod


# ---------------------------------------------------------------------------
# Synthetic Investegate-shaped HTML (og:url + JSON-LD dateCreated)

def _fake_html(rns_id: str, slug: str = "acme-plc--acm",
               date_created: str = "2025-03-04 07:00:01") -> str:
    return (
        "<html><head>"
        f"<meta property='og:url' content='http://www.investegate.co.uk/"
        f"announcement/rns/{slug}/director-pdmr-shareholding/{rns_id}'/>"
        '<script type="application/ld+json">'
        f'{{"@type":"NewsArticle","dateCreated":"{date_created}"}}'
        "</script>"
        "</head><body>stub</body></html>"
    )


def _tx_row(fp, *, url="", announced_at="", tx_type="BUY", shares=100):
    return {
        "fingerprint": fp, "date": "2025-03-03", "ticker": "ACM",
        "company": "Acme plc", "director": "Jane Dough",
        "role": "Chief Executive Officer", "type": tx_type,
        "shares": shares, "price": 1.5, "value": 1.5 * shares,
        "context": None, "url": url, "announced_at": announced_at,
        "buy_strictness": None, "resulting_shares": None,
    }


def _parsed(fp):
    """Minimal parse_announcement output row -- only fingerprint matters."""
    return {"fingerprint": fp}


# ---------------------------------------------------------------------------
# Group 1: HTML extraction helpers

class TestHtmlExtraction(unittest.TestCase):

    def test_og_url_extracted_and_https_normalised(self):
        html = _fake_html("9100001")
        self.assertEqual(
            backfill_urls._url_from_html(html),
            "https://www.investegate.co.uk/announcement/rns/acme-plc--acm/"
            "director-pdmr-shareholding/9100001",
        )

    def test_og_url_double_quotes_variant(self):
        html = ('<meta property="og:url" content="https://www.investegate.'
                'co.uk/announcement/rns/x--y/z/9100002"/>')
        self.assertTrue(
            backfill_urls._url_from_html(html).endswith("/9100002"))

    def test_og_url_absent_returns_empty(self):
        self.assertEqual(backfill_urls._url_from_html("<html></html>"), "")

    def test_announced_at_extracted_iso(self):
        html = _fake_html("9100001", date_created="2025-03-04 07:00:01")
        self.assertEqual(
            backfill_urls._announced_at_from_html(html),
            "2025-03-04T07:00:01Z",
        )

    def test_announced_at_absent_returns_empty(self):
        self.assertEqual(
            backfill_urls._announced_at_from_html("<html></html>"), "")

    def test_real_cache_fixture_if_present(self):
        fixture = HERE / "fixtures" / "clean_buy_9562545.html"
        if not fixture.exists():
            self.skipTest("fixture not present")
        html = fixture.read_text(encoding="utf-8", errors="replace")
        url = backfill_urls._url_from_html(html)
        if url:  # some fixtures are body-only extracts
            self.assertTrue(url.startswith("https://www.investegate.co.uk/"))
            self.assertIn("9562545", url)


# ---------------------------------------------------------------------------
# Group 2: ambiguity resolution

class TestResolve(unittest.TestCase):

    def test_lowest_numeric_rns_id_wins(self):
        cands = [
            {"rns_id": "9100009", "url": "u9", "announced_at": ""},
            {"rns_id": "9100001", "url": "u1", "announced_at": ""},
        ]
        self.assertEqual(backfill_urls._resolve(cands)["url"], "u1")

    def test_non_numeric_rns_id_sorts_last(self):
        cands = [
            {"rns_id": "weird", "url": "uw", "announced_at": ""},
            {"rns_id": "9100005", "url": "u5", "announced_at": ""},
        ]
        self.assertEqual(backfill_urls._resolve(cands)["url"], "u5")


# ---------------------------------------------------------------------------
# Group 3: end-to-end against a tempfile DB + synthetic cache

class TestBackfillEndToEnd(unittest.TestCase):

    def _seed_db(self, conn):
        # fp-blank:      blank url, blank announced_at  -> restorable
        # fp-blank-ann:  blank url, POPULATED announced_at -> url only
        # fp-haveurl:    populated url -> must never be touched
        # fp-orphan:     blank url, no cache file reproduces it
        rows = [
            _tx_row("fp-blank"),
            _tx_row("fp-blank-ann", announced_at="2024-01-01T00:00:00Z",
                    shares=200),
            _tx_row("fp-haveurl", url="https://example/existing",
                    announced_at="2024-01-01T00:00:00Z", shares=300),
            _tx_row("fp-orphan", tx_type="SELL", shares=400),
        ]
        for r in rows:
            db_mod.upsert_transaction(conn, r, "regex")
        conn.commit()

    def _run(self, tmp: Path, argv, parse_map):
        """Run backfill_urls.main() with patched paths + parser."""
        def fake_parse(html, url, rns_id, announced_at, **kw):
            self.assertEqual(url, "")          # must replay buggy args
            self.assertEqual(announced_at, "")
            return parse_map.get(rns_id, []), [], "regex"

        out = io.StringIO()
        with mock.patch.object(backfill_urls, "CACHE_DIR",
                               tmp / "cache"), \
             mock.patch.object(backfill_urls, "AUDIT_LOG",
                               tmp / "audit.log"), \
             mock.patch.object(backfill_urls, "parse_announcement",
                               side_effect=fake_parse), \
             mock.patch.object(sys, "argv", ["backfill_urls.py"] + argv), \
             redirect_stdout(out):
            backfill_urls.main()
        return out.getvalue()

    def _setup_world(self, tmp: Path):
        (tmp / "cache").mkdir()
        # 9100001 reproduces fp-blank and fp-blank-ann.
        (tmp / "cache" / "9100001.html").write_text(
            _fake_html("9100001"), encoding="utf-8")
        # Ambiguity pair: 9100002 + 9100003 both reproduce fp-blank.
        (tmp / "cache" / "9100003.html").write_text(
            _fake_html("9100003"), encoding="utf-8")
        return {
            "9100001": [_parsed("fp-blank"), _parsed("fp-blank-ann")],
            "9100003": [_parsed("fp-blank")],
        }

    def test_preview_makes_no_writes(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with mock.patch.object(db_mod, "DB_PATH", tmp / "t.db"):
                conn = db_mod.connect()
                try:
                    self._seed_db(conn)
                    parse_map = self._setup_world(tmp)
                    out = self._run(tmp, [], parse_map)
                    self.assertIn("PREVIEW", out)
                    got = conn.execute(
                        "SELECT url FROM transactions "
                        "WHERE fingerprint='fp-blank'").fetchone()
                    self.assertEqual(got["url"], "")
                    self.assertFalse((tmp / "audit.log").exists())
                finally:
                    conn.close()

    def test_confirm_restores_url_and_announced_at(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with mock.patch.object(db_mod, "DB_PATH", tmp / "t.db"):
                conn = db_mod.connect()
                try:
                    self._seed_db(conn)
                    parse_map = self._setup_world(tmp)
                    out = self._run(tmp, ["--confirm"], parse_map)
                    self.assertIn("DONE.", out)

                    # fp-blank: url restored from the EARLIEST filing
                    # (9100001 < 9100003) + announced_at filled.
                    got = conn.execute(
                        "SELECT url, announced_at FROM transactions "
                        "WHERE fingerprint='fp-blank'").fetchone()
                    self.assertTrue(got["url"].endswith("/9100001"))
                    self.assertEqual(got["announced_at"],
                                     "2025-03-04T07:00:01Z")

                    # fp-blank-ann: url restored, announced_at PRESERVED.
                    got = conn.execute(
                        "SELECT url, announced_at FROM transactions "
                        "WHERE fingerprint='fp-blank-ann'").fetchone()
                    self.assertTrue(got["url"].endswith("/9100001"))
                    self.assertEqual(got["announced_at"],
                                     "2024-01-01T00:00:00Z")

                    # fp-haveurl untouched.
                    got = conn.execute(
                        "SELECT url FROM transactions "
                        "WHERE fingerprint='fp-haveurl'").fetchone()
                    self.assertEqual(got["url"], "https://example/existing")

                    # fp-orphan still blank (reported as unreachable).
                    got = conn.execute(
                        "SELECT url FROM transactions "
                        "WHERE fingerprint='fp-orphan'").fetchone()
                    self.assertEqual(got["url"], "")
                    self.assertIn("still unreachable", out)
                finally:
                    conn.close()

    def test_audit_log_jsonl_and_ambiguity_flag(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with mock.patch.object(db_mod, "DB_PATH", tmp / "t.db"):
                conn = db_mod.connect()
                try:
                    self._seed_db(conn)
                    parse_map = self._setup_world(tmp)
                    self._run(tmp, ["--confirm"], parse_map)
                    lines = [json.loads(l) for l in
                             (tmp / "audit.log").read_text(
                                 encoding="utf-8").splitlines()]
                    self.assertEqual(len(lines), 2)   # fp-blank, fp-blank-ann
                    by_fp = {e["fingerprint"]: e for e in lines}
                    e = by_fp["fp-blank"]
                    self.assertEqual(e["rns_id"], "9100001")
                    self.assertTrue(e["ambiguous"])
                    self.assertEqual(e["n_candidate_filings"], 2)
                    self.assertTrue(e["announced_at_set"])
                    self.assertIn("ts", e)
                    self.assertIn("url", e)
                    e2 = by_fp["fp-blank-ann"]
                    self.assertFalse(e2["ambiguous"])
                    self.assertFalse(e2["announced_at_set"])
                finally:
                    conn.close()

    def test_confirm_is_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with mock.patch.object(db_mod, "DB_PATH", tmp / "t.db"):
                conn = db_mod.connect()
                try:
                    self._seed_db(conn)
                    parse_map = self._setup_world(tmp)
                    self._run(tmp, ["--confirm"], parse_map)
                    out2 = self._run(tmp, ["--confirm"], parse_map)
                    # Second run: previously-restored rows are no longer
                    # targets; only fp-orphan remains.
                    self.assertRegex(out2, r"Rows with blank url:\s+1\b")
                    lines = (tmp / "audit.log").read_text(
                        encoding="utf-8").splitlines()
                    self.assertEqual(len(lines), 2)   # no duplicate entries
                finally:
                    conn.close()


# ---------------------------------------------------------------------------
# Group 4: reparse_corpus source fix (B-167)

class TestReparseCorpusFix(unittest.TestCase):

    def test_process_filing_recovers_url_from_html(self):
        import reparse_corpus as rc
        html = _fake_html("9100007")
        with mock.patch.object(rc, "parse_announcement",
                               return_value=([], [], "regex")) as p:
            rc.process_filing(None, "9100007", html, {}, set())
            _args, _ = p.call_args
            # parse_announcement(html, url, rns_id, announced_at)
            self.assertTrue(_args[1].endswith("/9100007"))
            self.assertTrue(_args[1].startswith(
                "https://www.investegate.co.uk/"))
            self.assertEqual(_args[3], "2025-03-04T07:00:01Z")

    def test_process_filing_prefers_existing_db_url(self):
        import reparse_corpus as rc
        html = _fake_html("9100008")
        existing = {"9100008": [
            {"url": "https://db/url", "announced_at": "2020-01-01T00:00:00Z",
             "fingerprint": "x"},
        ]}
        with mock.patch.object(rc, "parse_announcement",
                               return_value=([], [], "regex")) as p:
            rc.process_filing(None, "9100008", html, existing, set())
            _args, _ = p.call_args
            self.assertEqual(_args[1], "https://db/url")
            self.assertEqual(_args[3], "2020-01-01T00:00:00Z")

    def test_apply_insert_writes_url_strictness_role_normalized(self):
        import reparse_corpus as rc
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with mock.patch.object(db_mod, "DB_PATH", tmp / "t.db"):
                conn = db_mod.connect()
                try:
                    row = _tx_row("fp-ins",
                                  url="https://example/9100009",
                                  announced_at="2025-03-04T07:00:01Z")
                    row["buy_strictness"] = "STRICT_BUY"
                    rc._apply_insert(conn, row)
                    conn.commit()
                    got = conn.execute(
                        "SELECT url, announced_at, buy_strictness, "
                        "role_normalized, parser_source FROM transactions "
                        "WHERE fingerprint='fp-ins'").fetchone()
                    self.assertEqual(got["url"], "https://example/9100009")
                    self.assertEqual(got["announced_at"],
                                     "2025-03-04T07:00:01Z")
                    self.assertEqual(got["buy_strictness"], "STRICT_BUY")
                    self.assertEqual(got["role_normalized"], "CEO")
                    self.assertEqual(got["parser_source"], "regex")
                finally:
                    conn.close()

    def test_apply_update_heals_blank_url(self):
        import reparse_corpus as rc
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with mock.patch.object(db_mod, "DB_PATH", tmp / "t.db"):
                conn = db_mod.connect()
                try:
                    db_mod.upsert_transaction(
                        conn, _tx_row("fp-old"), "regex")
                    conn.commit()
                    new_row = _tx_row("fp-new",
                                      url="https://example/healed",
                                      announced_at="2025-03-04T07:00:01Z")
                    rc._apply_update(conn, "fp-old", new_row)
                    conn.commit()
                    got = conn.execute(
                        "SELECT url, announced_at FROM transactions "
                        "WHERE fingerprint='fp-new'").fetchone()
                    self.assertEqual(got["url"], "https://example/healed")
                    self.assertEqual(got["announced_at"],
                                     "2025-03-04T07:00:01Z")
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
