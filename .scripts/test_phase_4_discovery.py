"""Phase 4 discovery-gap fix — unit tests.

Covers the inverted keep+deny headline filter (`_row_is_pdmr`), the
fail-open date window + pagination in `iter_index`, and the read-only
dry-discovery preview wiring.

All read-only: no DB, no live network, no cache writes. `iter_index` is
exercised against a mocked `scrape_investegate._fetch` returning canned
index HTML — exactly like the existing test_stage_02 iter_archive cases.

Run:
    python .scripts/test_phase_4_discovery.py
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scrape_investegate as scraper


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
# Filter: ACCEPT the 11 previously-missed headlines (the actual gap)
# ---------------------------------------------------------------------------

# (ticker, headline) for each of the 11 tickers dropped on 1-2 Jun.
MISSED_HEADLINES = [
    ("AT.",  "AT. Conditional LTIP Awards"),
    ("BRES", "Blackrock Energy (BRES) Exercise of Share Options"),
    ("FDM",  "FDM Group (FDM) Director Shareholding & Block Listing Application"),
    ("MAB1", "Mortgage Advice Bureau (MAB1) Directors' Shareholdings and PDMR notification"),
    ("MICC", "Magnum (MICC) Directorate Dealing"),
    ("NFG",  "Next Fifteen (NFG) Director/PCA Dealing"),
    ("ONDO", "Ondo InsurTech (ONDO) Share Incentive Plan Purchase"),
    ("QUBE", "Qube Holdings (QUBE) Director Dealing"),
    ("TTE",  "TotalEnergies (TTE) Notification of transactions"),
    ("VTU",  "Vertu Motors (VTU) Notification of PDMR's interests"),
    ("MTLN", "Metlen (MTLN) PDMR transaction notification"),
]


def case_accepts_all_missed_headlines() -> None:
    for ticker, headline in MISSED_HEADLINES:
        assert scraper._row_is_pdmr(headline), (
            f"WRONGLY DROPPED {ticker}: {headline!r}"
        )


# ---------------------------------------------------------------------------
# Filter: REJECT clear non-PDMR announcement types
# ---------------------------------------------------------------------------

REJECT_HEADLINES = [
    "Some Trust plc Net Asset Value(s)",
    "Some Trust plc NAV and Portfolio Update",
    "Acme plc Form 8.3 - Target plc",
    "Acme plc Rule 8.5 - Bidco plc",
    "Acme plc Total Voting Rights",
    "TVR",
    "Acme plc Transaction in Own Shares",
    "Acme plc Share Buyback Programme",
    "Acme plc Repurchase of Ordinary Shares",
    "Acme plc Half-year Results",
    "Acme plc Final Results",
    "Acme plc Trading Statement",
    "Acme plc Trading Update",
    "Acme plc Holding(s) in Company",
    "Acme plc Block Listing Six Monthly Return",
    "Block Listing Application",
    "EBT share purchase",
    "Market purchase of shares for EBT",
]


def case_rejects_clear_non_pdmr() -> None:
    for headline in REJECT_HEADLINES:
        assert not scraper._row_is_pdmr(headline), (
            f"WRONGLY KEPT non-PDMR: {headline!r}"
        )


def case_tvr_with_dealing_word_is_kept() -> None:
    # A PDMR notification that mentions TVR in passing must NOT be dropped.
    assert scraper._row_is_pdmr("Acme plc Director/PDMR Shareholding and TVR")
    # But a standalone TVR housekeeping notice is dropped.
    assert not scraper._row_is_pdmr("Acme plc Total Voting Rights")


def case_empty_headline_fails_open() -> None:
    # Blank/odd headline -> KEEP (fail-open), never silently drop.
    assert scraper._row_is_pdmr("")
    assert scraper._row_is_pdmr(None)  # type: ignore[arg-type]


def case_compound_block_listing_kept() -> None:
    # FDM-style compound headline: block-listing co-occurs with a dealing
    # word -> KEEP. Only a bare block-listing notice is dropped.
    assert scraper._row_is_pdmr(
        "FDM Group (FDM) Director Shareholding & Block Listing Application"
    )


def case_dealing_word_overrides_corporate_action() -> None:
    # QA hardening (R-1/R-2): a Tier-2 corporate-action word in a headline that
    # ALSO carries a dealing hint must be KEPT, never silently dropped.
    keep = [
        "Acme plc PDMR Dealing - Transaction in Own Shares",
        "Acme plc Director buys shares after trading update",
        "Acme plc PDMR dealing following final results announcement",
    ]
    for h in keep:
        assert scraper._row_is_pdmr(h), f"WRONGLY DROPPED compound dealing: {h!r}"
    # …but the same corporate-action words with NO dealing hint stay dropped.
    for h in ("Acme plc Transaction in Own Shares", "Acme plc Trading Update",
              "Acme plc Final Results"):
        assert not scraper._row_is_pdmr(h), f"WRONGLY KEPT non-PDMR: {h!r}"


def case_substring_boundaries_safe() -> None:
    # Word boundaries must not false-drop on embedded substrings.
    assert scraper._row_is_pdmr("NavInvest plc Director/PDMR Shareholding")
    assert scraper._row_is_pdmr("Concept Group (CPT) Director Dealing")


def case_filing_link_accepts_all_pip_sources() -> None:
    # The link matcher must accept every Primary Information Provider, not just
    # rns/prn — BZW/EQS/GNW carried real PDMR filings that were being dropped.
    hrefs = {
        "bzw": "/announcement/bzw/totalenergies-se--tte/director-pdmr-shareholding/9598260",
        "gnw": "/announcement/gnw/the-magnum-ice-cream-company--micc/director-pdmr-shareholding/9597500",
        "eqs": "/announcement/eqs/m-g-credit-income-investment-trust--mgci/director-pdmr-shareholding/9597600",
        "rns": "/announcement/rns/johnson-matthey--jmat/director-pdmr-shareholding/9598140",
        "prn": "/announcement/prn/cadogan-energy-solutions--cad/director-pdmr-shareholding/9595626",
    }
    for src, href in hrefs.items():
        html = f'<a href="{href}">Director/PDMR Shareholding</a>'
        assert scraper._FILING_LINK_RE.search(html), f"link dropped for source {src}"
    # …but a link with no trailing numeric filing id must NOT match.
    assert not scraper._FILING_LINK_RE.search(
        '<a href="/announcement/rns/some-co--abc/no-id/">x</a>'
    )


# ---------------------------------------------------------------------------
# iter_index: index walk keeps the awkward headlines, drops denylist noise
# ---------------------------------------------------------------------------

def _row(slug_id: str, ticker: str, headline: str, time_str: str = "08:00 AM 02-Jun-2026") -> str:
    return (
        f"<tr><td>{time_str}</td><td>"
        f'<a href="/announcement/rns/{ticker.lower()}-plc--{ticker}/some-slug/{slug_id}">'
        f"{headline}</a></td><td>({ticker})</td></tr>"
    )


def case_iter_index_keeps_awkward_and_drops_noise() -> None:
    html = "<html><body><table>" + "".join([
        _row("9000001", "AT", "AT. Conditional LTIP Awards"),
        _row("9000002", "BRES", "Blackrock (BRES) Exercise of Share Options"),
        _row("9000003", "QUBE", "Qube (QUBE) Director Dealing"),
        _row("9000004", "MTLN", "Metlen (MTLN) PDMR transaction notification"),
        # Noise that must be dropped:
        _row("9000099", "TRST", "Some Trust (TRST) Net Asset Value"),
        _row("9000098", "BIDC", "Acme (BIDC) Form 8.3 - Target plc"),
        _row("9000097", "BUYB", "Acme (BUYB) Transaction in Own Shares"),
    ]) + "</table></body></html>"

    with mock.patch.object(scraper, "_fetch", return_value=html):
        rows = list(scraper.iter_index("2026-06-01", "2026-06-02", max_pages=1))

    ids = {r["rns_id"] for r in rows}
    assert {"9000001", "9000002", "9000003", "9000004"} <= ids, ids
    assert "9000099" not in ids and "9000098" not in ids and "9000097" not in ids, ids


def case_iter_index_date_window_filters() -> None:
    html = "<html><body><table>" + "".join([
        _row("9100001", "INW", "In-window (INW) Director Dealing", "08:00 AM 02-Jun-2026"),
        _row("9100002", "OLD", "Old (OLD) Director Dealing", "08:00 AM 15-May-2026"),
        _row("9100003", "NEW", "Future (NEW) Director Dealing", "08:00 AM 10-Jun-2026"),
    ]) + "</table></body></html>"

    with mock.patch.object(scraper, "_fetch", return_value=html):
        rows = list(scraper.iter_index("2026-06-01", "2026-06-02", max_pages=1))

    ids = {r["rns_id"] for r in rows}
    assert "9100001" in ids, ids          # in window -> kept
    assert "9100002" not in ids, ids      # before window -> dropped
    assert "9100003" not in ids, ids      # after window -> dropped


def case_iter_index_date_fail_open() -> None:
    # A row with NO parseable date must be KEPT even with a window set.
    html = (
        "<html><body><table>"
        '<tr><td>no time here</td><td>'
        '<a href="/announcement/rns/nodate-plc--NOD/slug/9200001">'
        "Nodate (NOD) Director Dealing</a></td><td>(NOD)</td></tr>"
        "</table></body></html>"
    )
    with mock.patch.object(scraper, "_fetch", return_value=html):
        rows = list(scraper.iter_index("2026-06-01", "2026-06-02", max_pages=1))
    ids = {r["rns_id"] for r in rows}
    assert "9200001" in ids, ("fail-open violated: dateless row dropped", ids)


# ---------------------------------------------------------------------------
# iter_index: pagination
# ---------------------------------------------------------------------------

def case_iter_index_paginates_then_stops_on_empty() -> None:
    page1 = "<html><body><table>" + _row("9300001", "AAA", "AAA Director Dealing") + "</table></body></html>"
    page2 = "<html><body><table>" + _row("9300002", "BBB", "BBB Director Dealing") + "</table></body></html>"
    page3 = "<html><body><table></table></body></html>"  # no links -> stop

    fetched: list[str] = []

    def fake_fetch(url, retries=3, extra_headers=None):
        fetched.append(url)
        if "page=2" in url:
            return page2
        if "page=3" in url:
            return page3
        return page1  # page 1 (no page param)

    with mock.patch.object(scraper, "_fetch", side_effect=fake_fetch):
        rows = list(scraper.iter_index("2026-06-01", "2026-06-02", max_pages=5))

    ids = [r["rns_id"] for r in rows]
    assert ids == ["9300001", "9300002"], ids
    # Walked page1, page2, page3 (empty -> stop). Should NOT walk page4/5.
    assert len(fetched) == 3, fetched
    assert "?show=300" in fetched[0] and "page=" not in fetched[0], fetched[0]
    assert "page=2" in fetched[1], fetched[1]
    assert "page=3" in fetched[2], fetched[2]


def case_iter_index_stops_when_page_all_before_window() -> None:
    # Page 1 in-window, page 2 entirely older than start -> stop, no page 3.
    page1 = "<html><body><table>" + _row("9400001", "AAA", "AAA Director Dealing", "08:00 AM 02-Jun-2026") + "</table></body></html>"
    page2 = "<html><body><table>" + _row("9400002", "BBB", "BBB Director Dealing", "08:00 AM 01-Jan-2026") + "</table></body></html>"

    fetched: list[str] = []

    def fake_fetch(url, retries=3, extra_headers=None):
        fetched.append(url)
        return page2 if "page=2" in url else page1

    with mock.patch.object(scraper, "_fetch", side_effect=fake_fetch):
        rows = list(scraper.iter_index("2026-06-01", "2026-06-02", max_pages=5))

    ids = [r["rns_id"] for r in rows]
    assert ids == ["9400001"], ids
    # page1 (yields), page2 (all before window, yields nothing -> stop).
    assert len(fetched) == 2, fetched


def case_iter_index_dedup_across_pages() -> None:
    # Endpoint ignores ?page=N and repeats page 1: must not double-yield.
    same = "<html><body><table>" + _row("9500001", "AAA", "AAA Director Dealing") + "</table></body></html>"

    with mock.patch.object(scraper, "_fetch", return_value=same):
        rows = list(scraper.iter_index("2026-06-01", "2026-06-02", max_pages=3))

    ids = [r["rns_id"] for r in rows]
    assert ids == ["9500001"], ("duplicate yielded across repeated pages", ids)


# ---------------------------------------------------------------------------
# discover_preview wiring (read-only; never fetches filings)
# ---------------------------------------------------------------------------

def case_discover_preview_lists_candidates() -> None:
    import discover_preview
    html = "<html><body><table>" + "".join([
        _row("9600001", "AAA", "AAA Director Dealing"),
        _row("9600099", "TRST", "Trust (TRST) Net Asset Value"),
    ]) + "</table></body></html>"

    with mock.patch.object(scraper, "_fetch", return_value=html):
        rows = discover_preview.discover("2026-06-01", "2026-06-02", max_pages=1)

    ids = {r["rns_id"] for r in rows}
    assert "9600001" in ids and "9600099" not in ids, ids


def case_discover_preview_never_fetches_filings() -> None:
    # Guard: discover() must NOT call fetch_filing (no downloads/caching).
    import discover_preview
    html = "<html><body><table>" + _row("9700001", "AAA", "AAA Director Dealing") + "</table></body></html>"

    with mock.patch.object(scraper, "_fetch", return_value=html), \
         mock.patch.object(scraper, "fetch_filing",
                           side_effect=AssertionError("fetch_filing must not be called")):
        rows = discover_preview.discover("2026-06-01", "2026-06-02", max_pages=1)
    assert rows and rows[0]["rns_id"] == "9700001", rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    run_case("accepts all 11 missed headlines", case_accepts_all_missed_headlines)
    run_case("rejects clear non-PDMR", case_rejects_clear_non_pdmr)
    run_case("TVR with dealing word kept; standalone dropped", case_tvr_with_dealing_word_is_kept)
    run_case("empty/None headline fails open (kept)", case_empty_headline_fails_open)
    run_case("compound block-listing kept", case_compound_block_listing_kept)
    run_case("dealing word overrides corporate-action drop", case_dealing_word_overrides_corporate_action)
    run_case("substring boundaries safe", case_substring_boundaries_safe)
    run_case("filing link accepts all PIP sources", case_filing_link_accepts_all_pip_sources)
    run_case("iter_index keeps awkward, drops noise", case_iter_index_keeps_awkward_and_drops_noise)
    run_case("iter_index date window filters", case_iter_index_date_window_filters)
    run_case("iter_index date fail-open keeps dateless", case_iter_index_date_fail_open)
    run_case("iter_index paginates then stops on empty", case_iter_index_paginates_then_stops_on_empty)
    run_case("iter_index stops when page all before window", case_iter_index_stops_when_page_all_before_window)
    run_case("iter_index dedups across repeated pages", case_iter_index_dedup_across_pages)
    run_case("discover_preview lists candidates", case_discover_preview_lists_candidates)
    run_case("discover_preview never fetches filings", case_discover_preview_never_fetches_filings)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
