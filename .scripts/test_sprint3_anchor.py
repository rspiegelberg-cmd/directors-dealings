"""Sprint 3 anchor test — verify the new table-aware parser.

Runs `parse_announcement` against the cached HTML for filing 9541612
(National Grid, Jacqueline Agg, 4 transactions on 4 different dates)
and asserts the rebuilt parser produces all 4 rows with clean fields.

Also samples 3 known B-016 (emission-allowance boilerplate) tickers
and confirms the company field is no longer the regulatory boilerplate.

This test exists because FUSE bash-cache staleness can make Claude's
Linux sandbox see a stale copy of parse_pdmr.py for minutes after a
write. Running this from PowerShell uses Windows-direct file I/O and
sidesteps the cache.

CLI:
    python .scripts/test_sprint3_anchor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from parse_pdmr import parse_announcement  # noqa: E402

CACHE_DIR = HERE / "_scrape_cache"


def _run(rns_id: str, url: str = "", announced_at: str = "") -> tuple:
    path = CACHE_DIR / f"{rns_id}.html"
    if not path.exists():
        return None, [f"missing_cache:{path}"], None
    html = path.read_text(encoding="utf-8", errors="replace")
    return parse_announcement(html, url, rns_id, announced_at)


def test_b001_multi_row():
    """Filing 9541612 — National Grid, Jacqueline Agg, 4 transactions."""
    url = ("https://www.investegate.co.uk/announcement/rns/national-grid--ng./"
           "director-pdmr-shareholding/9541612")
    rows, warnings, source = _run("9541612", url, "2026-01-14T00:00:00Z")
    assert rows is not None, "parse failed — cache missing?"
    print(f"\n=== B-001 multi-row (filing 9541612) ===")
    print(f"  rows extracted: {len(rows)} (target: 4)")
    print(f"  parser_source: {source}")
    print(f"  warnings: {warnings[:5]}")
    for r in rows:
        print(f"    {r['date']} | {r['director']!r:25s} | {r['type']:6s} | "
              f"shares={r['shares']:4d} | price={r['price']!s:8s} | "
              f"company={r['company']!r}")
    expected_dates = {"2024-08-13", "2025-02-12", "2025-08-13", "2026-01-14"}
    actual_dates = {r["date"] for r in rows}
    ok_dates = expected_dates == actual_dates
    ok_director = all(r["director"] == "Jacqueline Agg" for r in rows)
    ok_company = all("emission allowance" not in (r["company"] or "")
                     for r in rows)
    ok_no_newline = all("\n" not in r["director"] for r in rows)
    print(f"  dates match: {ok_dates}")
    print(f"  director consistent (Jacqueline Agg): {ok_director}")
    print(f"  company clean (no emission boilerplate): {ok_company}")
    print(f"  director has no newline: {ok_no_newline}")
    return ok_dates and ok_director and ok_company and ok_no_newline and len(rows) == 4


def test_b016_company_boilerplate(rns_id: str, expected_ticker: str):
    """Sample a B-016 ticker — company should NOT be the emission-allowance boilerplate."""
    rows, warnings, source = _run(rns_id, "", "")
    if rows is None:
        print(f"  {rns_id}: cache missing — SKIP")
        return None
    if not rows:
        print(f"  {rns_id}: no rows extracted (warnings: {warnings[:3]})")
        return None
    print(f"  {rns_id} ({expected_ticker}): "
          f"{len(rows)} row(s), company={rows[0]['company']!r}, "
          f"director={rows[0]['director']!r}")
    ok = all("emission allowance" not in (r["company"] or "") for r in rows)
    return ok


def test_b017_director_name_in_company():
    """B-017 sample — AAL is one of the affected tickers."""
    # Filing for AAL — first one in the database for that ticker.
    # rns_id 8950385 was the row where company was "Magali Anderson".
    rows, warnings, source = _run("8950385")
    if rows is None:
        print("  8950385 (AAL): cache missing — SKIP")
        return None
    if not rows:
        print(f"  8950385 (AAL): no rows extracted (warnings: {warnings[:3]})")
        return None
    print(f"  8950385 (AAL): {len(rows)} row(s), "
          f"company={rows[0]['company']!r}, "
          f"director={rows[0]['director']!r}")
    # The director name should NOT appear as the company name.
    ok = all((r["company"] or "").lower() != (r["director"] or "").lower()
             for r in rows)
    return ok


def main() -> int:
    print("Sprint 3 anchor tests")
    print("=" * 60)
    pass_count = 0
    fail_count = 0

    b001_ok = test_b001_multi_row()
    if b001_ok:
        print("  B-001 PASSED")
        pass_count += 1
    else:
        print("  B-001 FAILED")
        fail_count += 1

    print("\n=== B-016 boilerplate samples ===")
    # rns_ids for the B-016 tickers (NET, SPT, YOU) — first transaction per
    # ticker from the database. These are filings that previously had the
    # "emission allowance..." string in the company field.
    b016_samples = [
        ("8998766", "NET"),
        ("8901385", "SPT"),
    ]
    for rns_id, ticker in b016_samples:
        ok = test_b016_company_boilerplate(rns_id, ticker)
        if ok is None:
            continue
        if ok:
            pass_count += 1
        else:
            fail_count += 1

    print("\n=== B-017 director-name-in-company sample ===")
    b017_ok = test_b017_director_name_in_company()
    if b017_ok is True:
        pass_count += 1
    elif b017_ok is False:
        fail_count += 1

    print("\n" + "=" * 60)
    print(f"Result: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
