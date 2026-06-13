"""Sprint 9 Phase A — read-only audit CLI for `.data/_suspect_filings.jsonl`.

Reads the JSONL file that `parse_pdmr._log_suspect_filing` writes during
a pipeline or audit run and prints a summary so Rupert can decide at
Gate 2 whether to flip the plausibility gate from warn-only to reject
mode.

Read-only on the DB. The only side effect is writing a stratified CSV
sample at `.data/_suspect_filings_sample.csv` when --sample is passed.

Usage:
    python .scripts/audit_suspect_filings.py --summary
    python .scripts/audit_suspect_filings.py --rule R3_price_too_high
    python .scripts/audit_suspect_filings.py --sample 100

Format note: this CLI reads BOTH the new JSONL file
(`_suspect_filings.jsonl`, one entry per line — preferred) and the
legacy JSON-array file (`_suspect_filings.json`, single array) so that
any leftover Phase A data from earlier runs is still surfaced.

See docs/specs/sprint-plan-2026-05-22-sprint9.md Section 4 for the rule
definitions (R1-R5) and Phase A deliverable spec.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".data"
SUSPECT_JSONL = DATA_DIR / "_suspect_filings.jsonl"
SUSPECT_JSON_LEGACY = DATA_DIR / "_suspect_filings.json"
SAMPLE_PATH = DATA_DIR / "_suspect_filings_sample.csv"


def _load() -> list:
    """Load every entry from the JSONL file plus any legacy JSON-array
    file. Skips malformed lines with a stderr warning rather than
    aborting (the parser's logger is best-effort and a partial line is
    possible after a crash)."""
    entries: list = []

    if SUSPECT_JSONL.exists():
        with SUSPECT_JSONL.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception as e:
                    print(
                        f"WARN: skipped malformed line {i} in "
                        f"{SUSPECT_JSONL.name}: {e}",
                        file=sys.stderr,
                    )

    # Legacy JSON-array file from earlier Phase A runs (pre-JSONL switch).
    if SUSPECT_JSON_LEGACY.exists():
        try:
            data = json.loads(SUSPECT_JSON_LEGACY.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries.extend(data)
        except Exception as e:
            print(
                f"WARN: could not parse legacy {SUSPECT_JSON_LEGACY.name}: {e}",
                file=sys.stderr,
            )

    return entries


def _print_summary(entries: list) -> None:
    n = len(entries)
    print(f"\n=== Sprint 9 Phase A plausibility audit ===")
    print(f"Source: {SUSPECT_JSONL}")
    if SUSPECT_JSON_LEGACY.exists():
        print(f"   +  : {SUSPECT_JSON_LEGACY} (legacy)")
    print(f"Total flagged rows: {n}")
    if n == 0:
        print("(nothing flagged - either the pipeline hasn't run yet or "
              "every row passed every rule)")
        return

    # Counts by rule (a single row may trip multiple rules).
    rule_counter: Counter = Counter()
    for entry in entries:
        for r in entry.get("reasons") or []:
            rule_counter[r] += 1

    print("\nBy rule (a row may fire multiple rules):")
    rule_descriptions = {
        "R1_sub_pound_value":        "value < GBP 1 on non-SIP/DIV/GRANT",
        "R2_tiny_shares_low_price":  "shares < 100 AND price < GBP 1",
        "R3_price_too_high":         "price > GBP 200 (impossible per-share)",
        "R4_excessive_value":        "value > GBP 100m AND ticker not in allowlist",
        "R5_date_component_in_shares": "shares looks like a date component",
    }
    for rule_id in sorted(rule_descriptions):
        count = rule_counter.get(rule_id, 0)
        desc = rule_descriptions[rule_id]
        print(f"  {rule_id:32s} {count:5d}   ({desc})")

    # Concentration by ticker.
    ticker_counter: Counter = Counter()
    for entry in entries:
        t = (entry.get("row") or {}).get("ticker") or "?"
        ticker_counter[t] += 1
    print(f"\nTop 10 tickers by flagged-row count:")
    for ticker, count in ticker_counter.most_common(10):
        print(f"  {ticker:8s} {count:5d}")

    # Date range.
    dates = sorted({(e.get("row") or {}).get("date")
                    for e in entries if (e.get("row") or {}).get("date")})
    if dates:
        print(f"\nDate range of flagged rows: {dates[0]} -> {dates[-1]}")

    print(f"\nNext step: python .scripts/audit_suspect_filings.py --sample 50")
    print(f"           opens a CSV preview at {SAMPLE_PATH}")


def _print_rule(entries: list, rule_id: str) -> None:
    matches = [e for e in entries if rule_id in (e.get("reasons") or [])]
    print(f"\n{rule_id}: {len(matches)} rows")
    if not matches:
        return
    for e in matches[:20]:
        row = e.get("row") or {}
        print(f"  {row.get('date'):10s}  {row.get('ticker'):6s}  "
              f"{row.get('director','?')[:30]:30s}  "
              f"{row.get('type'):8s}  "
              f"shares={row.get('shares')} price={row.get('price')} "
              f"value={row.get('value')}")
    if len(matches) > 20:
        print(f"  ... and {len(matches) - 20} more (use --sample for CSV)")


def _write_sample_csv(entries: list, sample_n: int) -> None:
    if not entries:
        print("No entries to sample.", file=sys.stderr)
        return
    # Stratify: take up to sample_n//5 from each rule bucket, then top up
    # with most-recent rows.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "logged_at", "rns_id", "reasons",
        "date", "ticker", "director", "role", "type",
        "shares", "price", "value", "url",
    ]
    selected: list = []
    seen_fingerprints: set = set()

    rules = sorted({r for e in entries for r in (e.get("reasons") or [])})
    per_rule = max(1, sample_n // max(1, len(rules)))

    for rule in rules:
        bucket = [e for e in entries if rule in (e.get("reasons") or [])]
        for e in bucket[:per_rule]:
            fp = (e.get("row") or {}).get("fingerprint")
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            selected.append(e)

    # Top up with the most-recent flagged rows that weren't already taken.
    if len(selected) < sample_n:
        for e in reversed(entries):
            fp = (e.get("row") or {}).get("fingerprint")
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            selected.append(e)
            if len(selected) >= sample_n:
                break

    with SAMPLE_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for e in selected:
            row = e.get("row") or {}
            writer.writerow({
                "logged_at": e.get("logged_at", ""),
                "rns_id": e.get("rns_id", ""),
                "reasons": "|".join(e.get("reasons") or []),
                "date": row.get("date", ""),
                "ticker": row.get("ticker", ""),
                "director": row.get("director", ""),
                "role": row.get("role", ""),
                "type": row.get("type", ""),
                "shares": row.get("shares", ""),
                "price": row.get("price", ""),
                "value": row.get("value", ""),
                "url": e.get("url", ""),
            })
    print(f"Wrote {len(selected)} rows to {SAMPLE_PATH}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="store_true",
                    help="Print headline counts + top tickers (default)")
    ap.add_argument("--rule", choices=[
        "R1_sub_pound_value", "R2_tiny_shares_low_price",
        "R3_price_too_high", "R4_excessive_value",
        "R5_date_component_in_shares",
    ], help="Print first 20 rows that tripped a specific rule")
    ap.add_argument("--sample", type=int, metavar="N",
                    help="Write N stratified-sample rows to CSV")
    args = ap.parse_args(argv)

    entries = _load()

    if args.rule:
        _print_rule(entries, args.rule)
    elif args.sample:
        _write_sample_csv(entries, args.sample)
    else:
        _print_summary(entries)

    return 0


if __name__ == "__main__":
    sys.exit(main())
