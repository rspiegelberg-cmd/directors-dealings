"""manual_classify.py — Hard-coded market cap + size classification for 19 tickers
that neither LSE nor Yahoo Finance can resolve automatically.

Sources:
  - Yahoo-confirmed (10): fetched 2026-06-07 via yfinance
  - Manual research (9): identified from public records 2026-06-07
    * AHT  Ashtead Group — moved primary listing to NYSE 2025
    * DLG  Direct Line Insurance — acquired by Aviva May 2025 (~£3.7B)
    * INDV Indivior — moved primary listing to NASDAQ 2023 (~£3.5B)
    * ADT1 Adriatic Metals — AIM mining co (~£350m)
    * BBB  BigBlu Broadband — delisted AIM, was tiny (~£25m)
    * CFCP Capital for Colleagues — tiny AIM co (~£7m)
    * DGQ  Delta Gold Technologies — tiny (~£20m)
    * KITW Kitwave Group — AIM food wholesaler (~£120m)
    * AWE  Alphawave IP — LSE semiconductor co (~£450m, borderline, treated SMALL)

Threshold: £500m  (matches classify_small_cap.py DEFAULT_THRESHOLD_GBP)

Run from Windows PowerShell (Zone B — writes to DB):
    python .scripts\\manual_classify.py
    python .scripts\\manual_classify.py --dry-run    # preview only, no writes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402

THRESHOLD = 500_000_000  # £500m — matches classify_small_cap.py DEFAULT_THRESHOLD_GBP

# (ticker, market_cap_gbp, source_note)
OVERRIDES: list[tuple[str, float, str]] = [
    # ── Yahoo-confirmed 2026-06-07 ──────────────────────────────────────────
    ("AO",    497_816_160,        "yahoo-confirmed-2026-06-07"),
    ("GFTU",  1_637_891_072,      "yahoo-confirmed-2026-06-07"),
    ("AJAX",  1_404_375,          "yahoo-confirmed-2026-06-07"),
    ("DCTA",  7_048_265,          "yahoo-confirmed-2026-06-07"),
    ("AFC",   167_292_096,        "yahoo-confirmed-2026-06-07"),
    ("COR",   982_127,            "yahoo-confirmed-2026-06-07"),
    ("CCL",   26_379_579_392,     "yahoo-confirmed-2026-06-07"),
    ("SN",    9_731_015_680,      "yahoo-confirmed-2026-06-07"),
    ("CAU",   1_529_555,          "yahoo-confirmed-2026-06-07"),
    ("ENET",  14_000,             "yahoo-confirmed-2026-06-07"),
    # ── Manual research 2026-06-07 ──────────────────────────────────────────
    ("AHT",   20_000_000_000,     "manual-ashtead-nyse-primary-2026-06-07"),
    ("DLG",   3_700_000_000,      "manual-directline-aviva-acq-2026-06-07"),
    ("INDV",  3_500_000_000,      "manual-indivior-nasdaq-2026-06-07"),
    ("ADT1",  350_000_000,        "manual-adriatic-metals-2026-06-07"),
    ("BBB",   25_000_000,         "manual-bigblu-broadband-2026-06-07"),
    ("CFCP",  7_000_000,          "manual-capital-for-colleagues-2026-06-07"),
    ("DGQ",   20_000_000,         "manual-delta-gold-tech-2026-06-07"),
    ("KITW",  120_000_000,        "manual-kitwave-group-2026-06-07"),
    ("AWE",   450_000_000,        "manual-alphawave-ip-borderline-2026-06-07"),
]


def run(*, dry_run: bool = False) -> None:
    conn = db.connect()
    try:
        updated = skipped = missing = 0
        rows = []

        for ticker, cap_gbp, source in OVERRIDES:
            row = conn.execute(
                "SELECT ticker, market_cap_gbp, small_cap FROM tickers_meta WHERE ticker = ?",
                (ticker,),
            ).fetchone()

            if row is None:
                print(f"  SKIP  {ticker:<6}  not in tickers_meta")
                missing += 1
                continue

            small_cap = 1 if cap_gbp < THRESHOLD else 0
            size_label = "SMALL" if small_cap else "LARGE"
            existing_cap = row["market_cap_gbp"]

            if existing_cap is not None:
                print(f"  SKIP  {ticker:<6}  already has market_cap_gbp={existing_cap:,.0f}")
                skipped += 1
                continue

            rows.append((ticker, cap_gbp, small_cap, size_label, source))
            print(
                f"  {'DRY ' if dry_run else 'SET '} {ticker:<6}  "
                f"£{cap_gbp/1_000_000:>8.1f}m  {size_label:<6}  [{source}]"
            )
            updated += 1

        if not dry_run and rows:
            for ticker, cap_gbp, small_cap, _, _ in rows:
                conn.execute(
                    """UPDATE tickers_meta
                          SET market_cap_gbp = ?,
                              small_cap      = ?,
                              updated_at     = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                        WHERE ticker = ?""",
                    (cap_gbp, small_cap, ticker),
                )
            conn.commit()

        print()
        print(f"{'DRY-RUN ' if dry_run else ''}Results: {updated} set, {skipped} already had data, {missing} not in DB")
        if dry_run:
            print("(no changes written — rerun without --dry-run to apply)")
    finally:
        conn.close()


def check_unclassified() -> None:
    """Sprint 56 Phase E: print tickers in tickers_meta with NULL market_cap_gbp.

    Connects read-only (SELECT only, no writes) so it is safe to run from bash.
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT ticker FROM tickers_meta "
            "WHERE market_cap_gbp IS NULL "
            "ORDER BY ticker"
        ).fetchall()
        if not rows:
            print("All tickers have market_cap_gbp — nothing unclassified.")
            return
        print(f"Tickers with market_cap_gbp IS NULL ({len(rows)} total):")
        for r in rows:
            print(f"  {r['ticker']}")
        print()
        print(
            "To fix: research market cap manually, add to OVERRIDES in this file,"
            " then re-run without --check."
        )
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manual market-cap override for unresolvable tickers.")
    ap.add_argument("--dry-run", action="store_true", help="Preview only — no DB writes.")
    ap.add_argument(
        "--check",
        action="store_true",
        help="Print tickers with NULL market_cap_gbp and exit (read-only, safe from bash).",
    )
    args = ap.parse_args(argv)
    if args.check:
        check_unclassified()
        return 0
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
