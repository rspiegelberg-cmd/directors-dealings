"""Stage 2 smoke + unit + integration test suite.

23 cases. Self-cleaning: monkey-patches `db.DB_PATH` and `db.DB_DIR`
to a tempdir, and rmtree's it in finally. Never touches the real
`.data/directors.db`.

Mocks the LLM via monkey-patch on `llm_parser.parse_with_llm`.
No real Anthropic API calls. No live network fetches.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db
import parse_pdmr as p
import scrape_investegate as scraper
import llm_cost
import llm_parser
import run_scrape


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
# Test cases
# ---------------------------------------------------------------------------

def case_01_ordinal_27th_april() -> None:
    # parse_iso_date requires a "Date of the transaction" label prefix (legacy
    # bare-string behaviour was removed when the false-positive fix landed).
    assert p.parse_iso_date("Date of the transaction: 27th April 2026") == "2026-04-27"


def case_02_ordinal_1st_may() -> None:
    assert p.parse_iso_date("Date of the transaction: 1st May 2026") == "2026-05-01"


def case_03_american_ordinal() -> None:
    assert p.parse_iso_date("Date of the transaction: April 27th, 2026") == "2026-04-27"


def case_04_no_ordinal_regression() -> None:
    assert p.parse_iso_date("Date of the transaction: 27 April 2026") == "2026-04-27"


def case_05_latest_wins_multi_date() -> None:
    # "latest-wins" only applies within the 200-char label window now.
    assert p.parse_iso_date("Date of the transaction: 28 and 30 April 2026") == "2026-04-30"


def case_06_number_re_gbp_variants() -> None:
    matches = list(p.NUMBER_RE.finditer("£1,234.56 then 50p then GBp 50"))
    nums = [m.group("num") for m in matches if m.group("num")]
    assert "1,234.56" in nums, nums
    assert "50" in nums, nums


def case_07_number_re_foreign_detect() -> None:
    # Detect dollar, EUR, USD, EUR symbol — match group should carry curr or post.
    found_curr = set()
    for txt in ("$100", "EUR 100", "€100", "USD 100"):
        for m in p.NUMBER_RE.finditer(txt):
            if m.group("curr"):
                found_curr.add(m.group("curr").upper())
            if m.group("post"):
                found_curr.add(m.group("post").upper())
    assert any(c in found_curr for c in ("$", "EUR", "USD", "€")), found_curr


def case_08_bundled_warning_non_none() -> None:
    text = "Notification 1 of 4\nName: Alice\nPosition: CEO\nNotification 2 of 4\nName: Bob\nPosition: CFO"
    w = p._bundled_name_warning(text)
    assert w is not None
    assert "Alice" in w and "Bob" in w


def case_09_bundled_warning_none_on_single() -> None:
    text = "Name: Alice\nPosition: CEO\nAcquisition of Shares"
    w = p._bundled_name_warning(text)
    assert w is None, w


def case_10_html_to_text_paragraph_breaks() -> None:
    html = "<html><body><p>Foo</p><p>Bar</p><table><tr><td>A</td><td>B</td></tr></table></body></html>"
    text = p.html_to_text(html)
    assert "Foo" in text and "Bar" in text
    # Paragraph separation: at least one newline between Foo and Bar
    assert "Foo" in text.split("Bar")[0]
    assert "\n" in text


def case_11_parse_clean_buy() -> None:
    html = (HERE / "fixtures" / "clean_buy_synthetic.html").read_text(encoding="utf-8")
    ex, w, src = p.parse_announcement(
        html, "https://x", "synth-buy", "2026-04-27",
        headline="Churchill China plc (CHH) Director/PDMR Shareholding",
    )
    assert ex and len(ex) == 1, (ex, w)
    assert ex[0]["type"] == "BUY"
    assert ex[0]["ticker"] == "CHH"
    assert ex[0]["shares"] == 1000
    assert src == "regex"
    assert not w


def case_12_parse_clean_sell() -> None:
    html = (HERE / "fixtures" / "clean_sell_synthetic.html").read_text(encoding="utf-8")
    ex, w, src = p.parse_announcement(
        html, "https://x", "synth-sell", "2026-04-30",
        headline="Rolls-Royce Holdings (RR.) Director/PDMR Shareholding",
    )
    assert ex and ex[0]["type"] == "SELL"
    assert ex[0]["shares"] == 5000


def case_13_parse_bundled_pdmr() -> None:
    html = (HERE / "fixtures" / "bundled_pdmr_9540067.html").read_text(encoding="utf-8")
    ex, w, src = p.parse_announcement(html, "https://x", "9540067", "2026-04-27")
    assert ex == []
    assert any("bundled" in wn.lower() for wn in w)
    # Warning lists the named PDMRs
    bundled_w = next(wn for wn in w if "bundled" in wn.lower())
    assert "Richard Oldfield" in bundled_w
    assert "Meagen Burnett" in bundled_w


def case_14_orchestrator_writes_one_row(conn) -> None:
    """End-to-end: regex parses clean, orchestrator upserts one row."""
    html = (HERE / "fixtures" / "clean_buy_synthetic.html").read_text(encoding="utf-8")
    ex, w, src = p.parse_announcement(
        html, "https://x", "synth-buy", "2026-04-27",
        headline="Churchill China plc (CHH) Director/PDMR Shareholding",
    )
    assert ex and not w
    run_scrape._upsert_transaction(conn, ex[0], src)
    row = conn.execute(
        "SELECT ticker, shares, parser_source, seen_count FROM transactions WHERE fingerprint = ?",
        (ex[0]["fingerprint"],),
    ).fetchone()
    assert row is not None
    assert row["ticker"] == "CHH"
    assert row["shares"] == 1000
    assert row["parser_source"] == "regex"
    assert row["seen_count"] == 1


def case_15_orchestrator_rerun_increments_seen_count(conn) -> None:
    html = (HERE / "fixtures" / "clean_buy_synthetic.html").read_text(encoding="utf-8")
    ex, w, src = p.parse_announcement(
        html, "https://x", "synth-buy", "2026-04-27",
        headline="Churchill China plc (CHH) Director/PDMR Shareholding",
    )
    run_scrape._upsert_transaction(conn, ex[0], src)
    row = conn.execute(
        "SELECT seen_count FROM transactions WHERE fingerprint = ?",
        (ex[0]["fingerprint"],),
    ).fetchone()
    # case_14 already inserted with seen_count=1; this should bump to 2.
    assert row["seen_count"] == 2, row["seen_count"]
    # And we shouldn't have a second row.
    cnt = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE fingerprint = ?",
        (ex[0]["fingerprint"],),
    ).fetchone()["n"]
    assert cnt == 1


def case_16_pending_json_shape(tmp_dir: Path) -> None:
    pending_path = tmp_dir / "_pending_review.json"
    # Patch orchestrator's PENDING_PATH for this test.
    original = run_scrape.PENDING_PATH
    run_scrape.PENDING_PATH = pending_path
    try:
        items = {"9540067": {"url": "x", "warnings": ["bundled"], "extracted": []}}
        run_scrape._write_pending(items)
        data = json.loads(pending_path.read_text(encoding="utf-8"))
        assert "generated_at" in data
        assert data["count"] == 1
        assert "9540067" in data["items"]
    finally:
        run_scrape.PENDING_PATH = original


def case_17_real_db_untouched() -> None:
    """Track real DB mtime before/after suite (deferred to main)."""
    pass


def case_18_barclays_sip_classifies_as_sip() -> None:
    html = (HERE / "fixtures" / "sip_barclays_9564893.html").read_text(encoding="utf-8")
    ex, w, src = p.parse_announcement(
        html, "https://x", "9564893", "2026-05-12",
        headline="Barclays PLC (BARC) Director/PDMR Shareholding",
    )
    assert ex, (ex, w)
    assert ex[0]["type"] == "SIP", f"got {ex[0]['type']}, expected SIP"
    assert ex[0]["director"] == "Taalib Shaah"
    assert ex[0]["shares"] == 554


def case_19_llm_fallback_mock(conn) -> None:
    """Set up a mocked llm_parser.parse_with_llm returning a canned row;
    verify orchestrator writes with parser_source='llm'.
    """
    canned = {
        "fingerprint": "mocked-llm-001",
        "date": "2026-05-08",
        "ticker": "TST",
        "company": "Test plc",
        "director": "Mock Director",
        "role": "CEO",
        "type": "BUY",
        "shares": 250,
        "price": 1.50,
        "value": 375.0,
        "context": None,
        "url": "https://x",
        "announced_at": "2026-05-08",
    }
    with mock.patch.object(llm_parser, "parse_with_llm", return_value=([canned], [])):
        # Direct upsert simulating orchestrator's call after LLM cleans up.
        run_scrape._upsert_transaction(conn, canned, "llm")
    row = conn.execute(
        "SELECT parser_source, ticker FROM transactions WHERE fingerprint = ?",
        ("mocked-llm-001",),
    ).fetchone()
    assert row is not None
    assert row["parser_source"] == "llm"
    assert row["ticker"] == "TST"


def case_20_budget_exceeded_aborts(tmp_dir: Path) -> None:
    """check_budget raises when ceiling hit; orchestrator aborts."""
    # Monkey-patch llm_cost.LEDGER_PATH to a fresh tempfile.
    original = llm_cost.LEDGER_PATH
    llm_cost.LEDGER_PATH = tmp_dir / "_llm_cost_test.json"
    try:
        run_id = llm_cost.start_run()
        # Pretend we've spent way over budget.
        llm_cost.record_call(input_tokens=10_000_000, output_tokens=10_000_000,
                             model="claude-sonnet-4-6", run_id=run_id)
        try:
            llm_cost.check_budget(run_id, budget_usd=1.0)
        except llm_cost.BudgetExceededError as e:
            assert "budget" in str(e).lower()
            return
        raise AssertionError("BudgetExceededError not raised")
    finally:
        llm_cost.LEDGER_PATH = original


def case_21_schema_migration_idempotent(tmp_dir: Path) -> None:
    """Start with a Stage 1 v1 DB, run migrate, confirm column + version."""
    # Build a v1-shape DB by hand, no parser_source column.
    db_path = tmp_dir / "legacy_v1.db"
    import sqlite3
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE transactions ("
        "fingerprint TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, "
        "seen_count INTEGER, date TEXT, ticker TEXT, company TEXT, "
        "director TEXT, role TEXT, type TEXT, shares INTEGER, price REAL, "
        "value REAL, context TEXT, url TEXT, announced_at TEXT, "
        "cluster_id TEXT, first_time_buy INTEGER)"
    )
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    c.commit()
    # Now run migrate.
    db.migrate(c)
    cols = [r[1] for r in c.execute("PRAGMA table_info(transactions)").fetchall()]
    assert "parser_source" in cols, cols
    val = c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    # migrate() runs the full chain to the current head (12 as of Sprint 58).
    assert int(val) >= 2, val
    head = val
    # Re-run: no error, version stays at head (idempotent).
    db.migrate(c)
    db.migrate(c)
    val = c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    assert val == head, f"version drifted: {head!r} -> {val!r}"
    c.close()


def case_22_stage1_smoke_still_passes() -> None:
    """Import and call test_db_smoke.main() directly.

    Previously used subprocess, which hit FUSE cache-staleness on the
    test file and produced spurious SyntaxErrors. Direct import avoids
    that — the module is already cached in sys.modules after the first
    import (or freshly loaded from the Windows-side path via importlib).
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "test_db_smoke", str(HERE / "test_db_smoke.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main()
    assert rc == 0, f"stage1 smoke failed: rc={rc}"


def case_23_iter_archive_uses_advanced_search() -> None:
    """iter_archive() must hit /advanced-search/draw with categories[]=16,
    walk in 30-day descending chunks, and yield rows from the parsed HTML.

    Updated 2026-05-13 for pagination support: each chunk now walks
    page=1, page=2, ... so the mock returns canned PDMR HTML on page=1
    of each chunk and an empty page on page=2 (end-of-pages signal).
    """
    canned_newer = (
        '<html><body>'
        '<a href="https://www.investegate.co.uk/announcement/rns/acme-plc--ACM/director-pdmr-shareholding/9999001">'
        'Acme plc (ACM) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/beta-corp--BTA/director-pdmr-shareholding/9999002">'
        'Beta Corp (BTA) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/gamma-ltd--GMA/pdmr-dealing/9999003">'
        'Gamma Ltd (GMA) PDMR Dealing</a>'
        '<a href="/announcement/rns/random-plc--RDM/some-other-rns/9999099">'
        'Random plc (RDM) Trading Update</a>'
        '</body></html>'
    )
    canned_older = (
        '<html><body>'
        '<a href="/announcement/rns/delta-plc--DLT/director-pdmr-shareholding/9999004">'
        'Delta plc (DLT) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/epsilon-plc--EPS/director-pdmr-shareholding/9999005">'
        'Epsilon plc (EPS) Director/PDMR Shareholding</a>'
        '</body></html>'
    )
    canned_empty = (
        '<html><body><table><tbody>'
        '<tr><td>No results found for your search criteria.</td></tr>'
        '</tbody></table></body></html>'
    )

    fetched_urls: list[str] = []

    def fake_fetch(url, retries=3, extra_headers=None):
        fetched_urls.append(url)
        # Return canned content on page=1 of each chunk, empty on page=2+.
        if "page=1" in url and "date_to=2025-11-14" in url:
            return canned_newer
        if "page=1" in url and "date_to=2025-10-15" in url:
            return canned_older
        return canned_empty

    with mock.patch.object(scraper, "_fetch", side_effect=fake_fetch):
        rows = list(scraper.iter_archive("2025-10-15", "2025-11-14"))

    # Two chunks, each walks page=1 (content) then page=2 (empty -> stop).
    # So 4 total fetches expected.
    assert len(fetched_urls) == 4, (
        f"expected 4 fetches (2 chunks x 2 pages each), got {len(fetched_urls)}: {fetched_urls}"
    )
    for u in fetched_urls:
        assert "/advanced-search/draw" in u, u
        assert "categories%5B%5D=16" in u or "categories[]=16" in u, u
        assert "exclude_navs=1" in u, u
        assert "date_from=" in u and "date_to=" in u, u
        assert "page=" in u, u
    # Descending chunk walk: newer chunk first, older second.
    assert "date_to=2025-11-14" in fetched_urls[0], fetched_urls[0]
    assert "page=1" in fetched_urls[0]
    assert "date_to=2025-11-14" in fetched_urls[1], fetched_urls[1]
    assert "page=2" in fetched_urls[1]
    assert "date_to=2025-10-15" in fetched_urls[2], fetched_urls[2]
    assert "page=1" in fetched_urls[2]
    assert "date_to=2025-10-15" in fetched_urls[3], fetched_urls[3]
    assert "page=2" in fetched_urls[3]

    ids = [r["rns_id"] for r in rows]
    assert ids == ["9999001", "9999002", "9999003", "9999004", "9999005"], ids
    assert "9999099" not in ids, ids
    for r in rows:
        assert set(r.keys()) == {
            "rns_id", "url", "headline", "ticker_hint", "announced_at"
        }, r
        assert r["url"].startswith("https://www.investegate.co.uk/"), r["url"]


def case_24_iter_archive_walks_multiple_pages() -> None:
    """page=1 returns 3 PDMRs, page=2 returns 2 more, page=3 returns 0.
    iter_archive should yield 5 unique rows and fetch the endpoint 3 times.
    """
    page1_html = (
        '<html><body>'
        '<a href="/announcement/rns/aaa-plc--AAA/director-pdmr-shareholding/8888001">'
        'Alpha plc (AAA) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/bbb-plc--BBB/director-pdmr-shareholding/8888002">'
        'Bravo plc (BBB) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/ccc-plc--CCC/pdmr-dealing/8888003">'
        'Charlie plc (CCC) PDMR Dealing</a>'
        '</body></html>'
    )
    page2_html = (
        '<html><body>'
        '<a href="/announcement/rns/ddd-plc--DDD/director-pdmr-shareholding/8888004">'
        'Delta plc (DDD) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/eee-plc--EEE/director-pdmr-shareholding/8888005">'
        'Echo plc (EEE) Director/PDMR Shareholding</a>'
        '</body></html>'
    )
    page3_html = (
        '<html><body><table><tbody>'
        '<tr><td>No results found for your search criteria.</td></tr>'
        '</tbody></table></body></html>'
    )

    fetched_urls: list[str] = []

    def fake_fetch(url, retries=3, extra_headers=None):
        fetched_urls.append(url)
        if "page=1" in url:
            return page1_html
        if "page=2" in url:
            return page2_html
        return page3_html

    with mock.patch.object(scraper, "_fetch", side_effect=fake_fetch):
        rows = list(scraper.iter_archive("2025-11-10", "2025-11-14"))

    ids = [r["rns_id"] for r in rows]
    assert ids == ["8888001", "8888002", "8888003", "8888004", "8888005"], ids
    # Single 5-day chunk; pagination walked page=1, page=2, page=3.
    assert len(fetched_urls) == 3, (
        f"expected 3 page fetches in single chunk, got {len(fetched_urls)}: {fetched_urls}"
    )
    assert "page=1" in fetched_urls[0], fetched_urls[0]
    assert "page=2" in fetched_urls[1], fetched_urls[1]
    assert "page=3" in fetched_urls[2], fetched_urls[2]


def case_25_iter_archive_detects_pagination_loop() -> None:
    """Server returns the same content for page=1 and page=2 (loop-back
    bug). iter_archive should detect the loop, stop paginating this chunk,
    and yield each filing exactly once.
    """
    same_page_html = (
        '<html><body>'
        '<a href="/announcement/rns/foo-plc--FOO/director-pdmr-shareholding/7777001">'
        'Foo plc (FOO) Director/PDMR Shareholding</a>'
        '<a href="/announcement/rns/bar-plc--BAR/director-pdmr-shareholding/7777002">'
        'Bar plc (BAR) Director/PDMR Shareholding</a>'
        '</body></html>'
    )

    fetched_urls: list[str] = []

    def fake_fetch(url, retries=3, extra_headers=None):
        fetched_urls.append(url)
        # Server bug: returns same content regardless of page param.
        return same_page_html

    with mock.patch.object(scraper, "_fetch", side_effect=fake_fetch):
        rows = list(scraper.iter_archive("2025-11-10", "2025-11-14"))

    ids = [r["rns_id"] for r in rows]
    # Each filing yielded exactly once even though server looped.
    assert ids == ["7777001", "7777002"], ids
    assert len(ids) == len(set(ids)), f"duplicates yielded: {ids}"
    # Loop detection should stop after page=2 (page 1 yields, page 2 is
    # a full repeat -> stop). Hard cap of 20 should never be hit.
    assert len(fetched_urls) <= 3, (
        f"loop not detected, kept paginating: {len(fetched_urls)} fetches"
    )
    assert "page=1" in fetched_urls[0]
    assert "page=2" in fetched_urls[1]


def case_26_llm_post_messages_retries_on_transient() -> None:
    """First call raises URLError (transient), second succeeds. Verify
    the retry kicks in and returns the success response.
    """
    canned_response = {
        "content": [{"type": "text", "text": '{"ok": true}'}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=60):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate WinError 10054 (connection forcibly closed) as a
            # URLError wrapping a ConnectionResetError.
            import urllib.error
            raise urllib.error.URLError("connection forcibly closed")

        # Second call: return a fake response object.
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(canned_response).encode("utf-8")

        return FakeResp()

    # Also patch time.sleep so the test doesn't actually wait 2s.
    with mock.patch.object(llm_parser.urllib.request, "urlopen", side_effect=fake_urlopen), \
         mock.patch.object(llm_parser.time, "sleep") as sleep_mock:
        result = llm_parser._post_messages(
            "fake-key", "fake-prompt", "claude-sonnet-4-6", timeout=5
        )

    assert call_count["n"] == 2, f"expected exactly 2 attempts, got {call_count['n']}"
    assert result == canned_response, result
    # Retry slept once with 2s.
    sleep_calls = [c.args for c in sleep_mock.call_args_list]
    assert (2.0,) in sleep_calls, f"expected 2.0s sleep between attempts, saw: {sleep_calls}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tmp_dir_str = tempfile.mkdtemp(prefix="dd_stage2_")
    tmp_dir = Path(tmp_dir_str)
    real_db = db.DB_PATH
    real_mtime = real_db.stat().st_mtime if real_db.exists() else None

    try:
        # Monkey-patch DB to tempdir BEFORE any connect().
        db.DB_DIR = tmp_dir
        db.DB_PATH = tmp_dir / "directors.db"

        run_case("01. parse_iso_date 27th April", case_01_ordinal_27th_april)
        run_case("02. parse_iso_date 1st May",  case_02_ordinal_1st_may)
        run_case("03. parse_iso_date American", case_03_american_ordinal)
        run_case("04. parse_iso_date no-ord",   case_04_no_ordinal_regression)
        run_case("05. parse_iso_date multi",    case_05_latest_wins_multi_date)
        run_case("06. NUMBER_RE GBP",            case_06_number_re_gbp_variants)
        run_case("07. NUMBER_RE foreign",        case_07_number_re_foreign_detect)
        run_case("08. bundled non-None",         case_08_bundled_warning_non_none)
        run_case("09. bundled None on single",   case_09_bundled_warning_none_on_single)
        run_case("10. html_to_text breaks",      case_10_html_to_text_paragraph_breaks)
        run_case("11. parse clean BUY",          case_11_parse_clean_buy)
        run_case("12. parse clean SELL",         case_12_parse_clean_sell)
        run_case("13. parse bundled PDMR",       case_13_parse_bundled_pdmr)

        # Open one connection for cases 14, 15, 19.
        conn = db.connect()
        try:
            run_case("14. orchestrator writes row",       lambda: case_14_orchestrator_writes_one_row(conn))
            run_case("15. rerun increments seen_count",  lambda: case_15_orchestrator_rerun_increments_seen_count(conn))
            run_case("19. LLM fallback mock writes 'llm'", lambda: case_19_llm_fallback_mock(conn))
        finally:
            conn.close()

        run_case("16. pending JSON shape",   lambda: case_16_pending_json_shape(tmp_dir))
        run_case("17. real DB untouched",    case_17_real_db_untouched)  # post-check below
        run_case("18. Barclays SIP",         case_18_barclays_sip_classifies_as_sip)
        run_case("20. budget exceeded",      lambda: case_20_budget_exceeded_aborts(tmp_dir))
        run_case("21. migration idempotent", lambda: case_21_schema_migration_idempotent(tmp_dir))
        run_case("22. stage1 smoke passes",  case_22_stage1_smoke_still_passes)
        run_case("23. iter_archive advanced-search", case_23_iter_archive_uses_advanced_search)
        run_case("24. iter_archive walks multi-page", case_24_iter_archive_walks_multiple_pages)
        run_case("25. iter_archive detects loop",     case_25_iter_archive_detects_pagination_loop)
        run_case("26. LLM retry on transient",        case_26_llm_post_messages_retries_on_transient)

        # Post-check for case 17: real DB mtime unchanged.
        ok = True
        if real_mtime is not None and real_db.exists():
            now_mtime = real_db.stat().st_mtime
            ok = (now_mtime == real_mtime)
        # Patch the recorded result for case 17.
        for i, (n, _, _) in enumerate(RESULTS):
            if n.startswith("17."):
                RESULTS[i] = (n, ok, "" if ok else "real DB mtime changed")
                break

        passed = sum(1 for _, ok, _ in RESULTS if ok)
        failed = sum(1 for _, ok, _ in RESULTS if not ok)
        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        shutil.rmtree(tmp_dir_str, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
