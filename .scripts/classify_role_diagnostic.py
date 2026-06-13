"""Corpus diagnostic for the role classifier (Sprint 2 — Performance page v1).

Runs `classify_role` against every row in `_backtest_results.csv` and prints
three plain-text tables to stdout so Rupert can eyeball whether the
classifier behaves sensibly on real data BEFORE any payload code starts
calling it in Sprints 3 and 4.

Tables emitted, in order:

  1. Counts per bucket — ceo_cfo / other_exec / ned / None with percentages
     and a `role_class` path breakdown so Rupert can see how often the regex
     fallback path is exercised (most of the time, on current corpus).

  2. Top 30 most-common (raw role_str, role_class) pairs and their
     classification. This is the "are any obvious CEOs landing as Chairs"
     gut check.

  3. Full list of (raw role_str, role_class) pairs classified as `None`
     (the catch-all). Anything that looks like it SHOULD be in one of
     the three buckets is a regex bug to flag.

The script is **read-only** — opens `.data/directors.db` for SELECT only,
reads the CSV via the same `load_backtest_csv` helper the exporter uses.
No writes to `.data/`, no DB mutations.

Per the Sprint 2 plan: Rupert runs this from PowerShell. The output is
plain text, terminal-friendly, and designed to be reviewed in under 5
minutes.

Usage (PowerShell):

    cd C:\\Dev\\DirectorsDealings
    python .scripts\\classify_role_diagnostic.py |
        Tee-Object -FilePath .data\\_classify_role_diagnostic.txt

The `_classify_role_diagnostic.txt` file is the Sprint 2 approval marker —
once Rupert reviews and approves, retain it as evidence and proceed to
Sprint 3.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402  — used for the optional transactions cross-check
from classify_role import classify_role  # noqa: E402

CSV_PATH = db.DB_DIR / "_backtest_results.csv"

# Hardening: some _backtest_results.csv builds have stray NUL bytes from
# the FUSE write path. csv.DictReader chokes on these, so we strip them
# defensively at the line level — same fix used in other read-only audits.
def _strip_nuls(file_iter):
    for line in file_iter:
        yield line.replace("\x00", "")


def _load_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"error: {path} not found — run backtest.py first.")
    rows: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(_strip_nuls(f)):
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Table renderers — plain-text, monospace-friendly
# ---------------------------------------------------------------------------

def _print_header(text: str) -> None:
    bar = "=" * 72
    print()
    print(bar)
    print(text)
    print(bar)


def _print_subheader(text: str) -> None:
    print()
    print(text)
    print("-" * len(text))


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{100.0 * n / total:5.1f}%"


# ---------------------------------------------------------------------------
# Diagnostic body
# ---------------------------------------------------------------------------

def run() -> int:
    rows = _load_csv_rows(CSV_PATH)
    total = len(rows)
    if total == 0:
        print("CSV is empty — nothing to classify.")
        return 1

    # Per-row classification.
    bucket_counts: Counter = Counter()
    pair_counts: Counter = Counter()           # (result, role_str, role_class) -> count
    by_role_class: dict = {}                   # bucket -> Counter(role_class -> count)
    for r in rows:
        role_class = r.get("role_class") or ""
        role_str = r.get("role") or ""
        result = classify_role(role_class, role_str)
        bucket_label = result if result is not None else "None"
        bucket_counts[bucket_label] += 1
        pair_counts[(bucket_label, role_str, role_class)] += 1
        by_role_class.setdefault(bucket_label, Counter())[role_class or "<empty>"] += 1

    # --- Table 1: counts per bucket ---------------------------------------
    _print_header("CLASSIFY_ROLE — CORPUS DIAGNOSTIC")
    print(f"Source: {CSV_PATH}")
    print(f"Total rows classified: {total}")
    print()
    print(f"{'bucket':<14} {'count':>7} {'pct':>7}   role_class breakdown")
    print(f"{'-' * 14} {'-' * 7} {'-' * 7}   {'-' * 40}")
    for label in ("ceo_cfo", "other_exec", "ned", "None"):
        n = bucket_counts.get(label, 0)
        rc = by_role_class.get(label, Counter())
        breakdown = ", ".join(
            f"{cls}={cnt}" for cls, cnt in rc.most_common()
        ) or "—"
        print(f"{label:<14} {n:>7} {_pct(n, total):>7}   {breakdown}")
    print(f"{'-' * 14} {'-' * 7} {'-' * 7}")
    print(f"{'total':<14} {total:>7} {' 100.0%':>7}")

    # --- Table 2: top 30 raw role strings + classification ----------------
    _print_subheader(
        "TOP 30 (classification, role_str, role_class) — most common"
    )
    print(f"  {'classify':<11} {'role_str':<45} {'role_class':<10} {'count':>6}")
    print(f"  {'-' * 11} {'-' * 45} {'-' * 10} {'-' * 6}")
    for (label, role_str, role_class), n in pair_counts.most_common(30):
        rs = role_str if role_str else "<empty>"
        rc = role_class if role_class else "<empty>"
        if len(rs) > 45:
            rs = rs[:42] + "..."
        print(f"  {label:<11} {rs:<45} {rc:<10} {n:>6}")

    # --- Table 3: full None list ------------------------------------------
    _print_subheader(
        "FULL LIST — classifications that fell to None (review each)"
    )
    none_pairs = [
        (rs, rc, n) for (lbl, rs, rc), n in pair_counts.items() if lbl == "None"
    ]
    none_pairs.sort(key=lambda t: (-t[2], t[0]))   # by count desc, then role_str
    if not none_pairs:
        print("  (none — every row classified into one of the three buckets)")
    else:
        print(f"  {'role_str':<55} {'role_class':<10} {'count':>6}")
        print(f"  {'-' * 55} {'-' * 10} {'-' * 6}")
        none_total = 0
        for rs, rc, n in none_pairs:
            rs_disp = (rs if rs else "<empty>")
            rc_disp = (rc if rc else "<empty>")
            if len(rs_disp) > 55:
                rs_disp = rs_disp[:52] + "..."
            print(f"  {rs_disp:<55} {rc_disp:<10} {n:>6}")
            none_total += n
        print(f"  {'-' * 55} {'-' * 10} {'-' * 6}")
        print(f"  {'TOTAL None rows':<55} {'':<10} {none_total:>6}")

    # --- Footer with sign-off prompt --------------------------------------
    print()
    print("-" * 72)
    print(
        "Review checklist (per Sprint 2 plan):\n"
        "  1. Any 'other_exec' rows that look like CEOs / CFOs?\n"
        "  2. Any 'None' rows that obviously belong in one of the three\n"
        "     buckets? (bare 'Director' and 'PDMR' are EXPECTED here —\n"
        "     they carry no title info and are correctly excluded.)\n"
        "  3. None bucket should be < 10% of total. Check the percentage\n"
        "     in Table 1 above.\n"
    )
    print("If happy: tell Claude 'go to Sprint 3'.")
    print("If not:   paste the surprising rows back — Claude patches the")
    print("          regex in classify_role.py and you re-run this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
