#!/usr/bin/env python3
"""dedup_transactions.py — find and remove logical duplicate transactions.

THREE duplicate classes are handled:

CLASS A — Exact logical duplicates
    Same (date, ticker, LOWER(director), type, shares, price) but DIFFERENT
    fingerprints.  Caused by two separate scraping passes using slightly
    different fingerprinting logic.
    Action: always auto-remove the NEWER duplicate, keep the OLDER row.

CLASS B — Near-duplicates (same deal, announced twice)
    Same (ticker, LOWER(director), type, shares, price) and transaction dates
    within 7 days of each other.  Caused by companies re-filing an RNS
    correction/replacement, or the same announcement being scraped as two
    slightly-different records.

    Two sub-classes:
      B-SAME  : both rows share the same Investegate announcement ID
                (extracted from the URL) — clearly the same filing.
                Action: auto-remove the less-complete duplicate.
      B-REVIEW: different URLs / no URL.  Could be two genuine separate
                purchases OR a filing error.
                Action: printed as candidates for Rupert's manual review;
                NOT auto-removed by --confirm unless --include-review is
                also passed.

CLASS C — URL-based parse artefacts (same URL, same director, wrong type or
          wrong share count)
    Three sub-classes, all auto-removable:
      C-SPURIOUS_BUY   : same URL + director + shares, one row is BUY and the
                         other is SIP / EXERCISE / GRANT / SELL_TAX.  The named
                         type is correct; the BUY is a parsing artefact.
      C-PARSE_FRAGMENT : same URL + director + type, shares differ wildly
                         (one <= 50, the other > 100).  The tiny-shares row is
                         a parser fragment.
      C-SELL_VS_SELL_TAX: same URL + director + shares, types are SELL and
                         SELL_TAX.  SELL_TAX is the correct label; SELL is
                         removed.

All Class-A + Class-B-SAME + Class-C changes are wrapped in a single DB
transaction — atomic, rolls back on error.

Usage
-----
    # Dry-run — shows full plan, touches nothing:
    python .scripts/dedup_transactions.py

    # Dedup Class A + Class B-SAME + Class C (safe auto cases):
    python .scripts/dedup_transactions.py --confirm

    # Also remove B-REVIEW candidates (use only after manual inspection):
    python .scripts/dedup_transactions.py --confirm --include-review

Zone-B: MUST be run from Windows PowerShell, never from Claude bash.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".data" / "directors.db"
BACKUP_PATH = ROOT / ".data" / "directors_pre_dedup.db"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(readonly: bool = False) -> sqlite3.Connection:
    uri_flag = "?mode=ro" if readonly else ""
    con = sqlite3.connect(f"file:{DB_PATH}{uri_flag}", uri=True, check_same_thread=False)
    if not readonly:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Class A — exact logical duplicates
# ---------------------------------------------------------------------------

def _find_class_a_pairs(rows: list) -> list[tuple[str, str]]:
    """Return (keeper_fp, remover_fp) for Class-A exact-logical duplicates.

    Keeper = OLDEST first_seen; tiebreak on seen_count DESC then fingerprint.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        key = (r["date"], r["ticker"], r["dir"], r["type"], str(r["shares"]), str(r["price"]))
        groups[key].append(r)

    pairs: list[tuple[str, str]] = []
    for grp in groups.values():
        if len(grp) < 2:
            continue
        sorted_grp = sorted(
            grp,
            key=lambda r: (r["first_seen"], -r["seen_count"], r["fingerprint"]),
        )
        keeper = sorted_grp[0]
        for remover in sorted_grp[1:]:
            pairs.append((keeper["fingerprint"], remover["fingerprint"]))
    return pairs


# ---------------------------------------------------------------------------
# Class B — near-duplicates (close dates, same deal fields)
# ---------------------------------------------------------------------------

_ANN_ID_RE = re.compile(r"/(\d{7,})(?:[/?]|$)")


def _ann_id(url: str) -> str | None:
    """Extract the Investegate announcement ID from a URL, or None."""
    if not url:
        return None
    m = _ANN_ID_RE.search(url)
    return m.group(1) if m else None


def _find_class_b_pairs(rows: list, window_days: int = 7) -> dict[str, list]:
    """Return {'same': [(keeper_fp, remover_fp, info), ...],
               'review': [(fp_a, fp_b, info), ...]}

    'same'   = same announcement ID → auto-remove safe
    'review' = different / missing URLs → flag for manual inspection
    """
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        # Key excludes date — we want near-matches on different dates
        key = (r["ticker"], r["dir"], r["type"], str(r["shares"]), str(r["price"]))
        groups[key].append(r)

    same_pairs: list[tuple[str, str, dict]] = []
    review_pairs: list[tuple[str, str, dict]] = []

    for key, grp in groups.items():
        if len(grp) < 2:
            continue
        for a, b in combinations(grp, 2):
            # Skip pairs already captured as Class-A (same date)
            if a["date"] == b["date"]:
                continue
            try:
                da = date.fromisoformat(a["date"])
                db_ = date.fromisoformat(b["date"])
            except ValueError:
                continue
            diff = abs((da - db_).days)
            if diff > window_days:
                continue

            info = {
                "ticker": a["ticker"],
                "director": a["director"],
                "type": a["type"],
                "shares": a["shares"],
                "price": a["price"],
                "date_a": a["date"],
                "date_b": b["date"],
                "diff_days": diff,
                "url_a": a["url"] or "",
                "url_b": b["url"] or "",
                "fp_a": a["fingerprint"],
                "fp_b": b["fingerprint"],
            }

            id_a = _ann_id(a["url"] or "")
            id_b = _ann_id(b["url"] or "")

            if id_a and id_b and id_a == id_b:
                # Same announcement ID — safe to auto-remove.
                # Keep the row with the more complete URL (longer), older as tiebreak.
                if len(a["url"] or "") >= len(b["url"] or ""):
                    same_pairs.append((a["fingerprint"], b["fingerprint"], info))
                else:
                    same_pairs.append((b["fingerprint"], a["fingerprint"], info))
            else:
                review_pairs.append((a["fingerprint"], b["fingerprint"], info))

    return {"same": same_pairs, "review": review_pairs}


# ---------------------------------------------------------------------------
# Class C — URL-based parse artefacts
# ---------------------------------------------------------------------------

_NAMED_TYPES = {"SIP", "EXERCISE", "GRANT", "SELL_TAX"}


def _find_class_c_pairs(rows: list) -> list[tuple[str, str, str]]:
    """Return [(keeper_fp, remover_fp, reason), ...] for Class-C URL artefacts.

    Only handles unambiguous pairs for the same director within the same URL.
    Groups with 3+ rows for the same director are skipped.
    """
    url_map: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("url"):
            url_map[r["url"]].append(r)

    pairs: list[tuple[str, str, str]] = []

    for url, group in url_map.items():
        if len(group) < 2:
            continue

        # sub-group by normalised director name
        dir_map: dict[str, list] = defaultdict(list)
        for r in group:
            dir_map[r["dir"]].append(r)

        for norm_dir, dir_rows in dir_map.items():
            if len(dir_rows) < 2:
                continue
            if len(dir_rows) > 2:
                continue  # ambiguous — skip

            a, b = dir_rows
            sa, sb = int(a["shares"]), int(b["shares"])

            # C-PARSE_FRAGMENT: same type, one very small share count
            if a["type"] == b["type"]:
                mn_r, mx_r = (a, b) if sa <= sb else (b, a)
                mn_s, mx_s = min(sa, sb), max(sa, sb)
                if mn_s <= 50 and mx_s > 100:
                    reason = (
                        f"C-PARSE_FRAGMENT | {mn_r['ticker']} | {norm_dir} | "
                        f"type={mn_r['type']} | del_shares={mn_s} keep_shares={mx_s}"
                    )
                    pairs.append((mx_r["fingerprint"], mn_r["fingerprint"], reason))
                    continue

            if sa != sb:
                continue  # different shares but not a fragment — skip

            type_pair = {a["type"], b["type"]}

            # C-SPURIOUS_BUY: named type + BUY for same shares
            if "BUY" in type_pair and type_pair & _NAMED_TYPES:
                buy_r = a if a["type"] == "BUY" else b
                named_r = a if a["type"] != "BUY" else b
                reason = (
                    f"C-SPURIOUS_BUY | {buy_r['ticker']} | {norm_dir} | "
                    f"real_type={named_r['type']} | shares={sa}"
                )
                pairs.append((named_r["fingerprint"], buy_r["fingerprint"], reason))
                continue

            # C-SELL_VS_SELL_TAX: keep the more specific SELL_TAX
            if type_pair == {"SELL", "SELL_TAX"}:
                sell_r = a if a["type"] == "SELL" else b
                tax_r = a if a["type"] == "SELL_TAX" else b
                reason = (
                    f"C-SELL_VS_SELL_TAX | {sell_r['ticker']} | {norm_dir} | "
                    f"shares={sa}"
                )
                pairs.append((tax_r["fingerprint"], sell_r["fingerprint"], reason))
                continue

    return pairs


# ---------------------------------------------------------------------------
# Signal / paper-trade helpers
# ---------------------------------------------------------------------------

def _build_plan(con: sqlite3.Connection, keeper_fp: str, remover_fp: str) -> dict:
    """Build action plan for one (keeper, remover) pair."""
    remover_signals = con.execute(
        "SELECT signal_id, signal_version FROM signals WHERE fingerprint = ?",
        (remover_fp,),
    ).fetchall()

    reassign, delete_sig = [], []
    for sig in remover_signals:
        exists = con.execute(
            "SELECT 1 FROM signals WHERE signal_id=? AND signal_version=? AND fingerprint=?",
            (sig["signal_id"], sig["signal_version"], keeper_fp),
        ).fetchone()
        if exists:
            delete_sig.append((sig["signal_id"], sig["signal_version"]))
        else:
            reassign.append((sig["signal_id"], sig["signal_version"]))

    pt_count = con.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE fingerprint=?", (remover_fp,)
    ).fetchone()[0]

    return {
        "keeper_fp": keeper_fp,
        "remover_fp": remover_fp,
        "signals_reassign": reassign,
        "signals_delete": delete_sig,
        "paper_trades_delete": pt_count,
    }


def _execute_plan(con: sqlite3.Connection, plan: dict) -> None:
    keeper_fp = plan["keeper_fp"]
    remover_fp = plan["remover_fp"]

    for sig_id, sig_ver in plan["signals_reassign"]:
        con.execute(
            "UPDATE signals SET fingerprint=? "
            "WHERE signal_id=? AND signal_version=? AND fingerprint=?",
            (keeper_fp, sig_id, sig_ver, remover_fp),
        )
    for sig_id, sig_ver in plan["signals_delete"]:
        con.execute(
            "DELETE FROM signals WHERE signal_id=? AND signal_version=? AND fingerprint=?",
            (sig_id, sig_ver, remover_fp),
        )
    con.execute("DELETE FROM paper_trades WHERE fingerprint=?", (remover_fp,))
    con.execute("DELETE FROM transactions WHERE fingerprint=?", (remover_fp,))


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _print_plans(label: str, plans: list[dict]) -> None:
    if not plans:
        print(f"  (none)")
        return
    sigs_r = sum(len(p["signals_reassign"]) for p in plans)
    sigs_d = sum(len(p["signals_delete"]) for p in plans)
    pts = sum(p["paper_trades_delete"] for p in plans)
    print(f"  Rows to remove   : {len(plans)}")
    print(f"  Signals reassign : {sigs_r}")
    print(f"  Signals delete   : {sigs_d}")
    print(f"  Paper-trades del : {pts}")
    print()
    for i, p in enumerate(plans, 1):
        keeper_s = p["keeper_fp"][:12]
        remover_s = p["remover_fp"][:12]
        notes = []
        if p["signals_reassign"]:
            notes.append("reassign sigs: " + ", ".join(s[0] for s in p["signals_reassign"]))
        if p["signals_delete"]:
            notes.append("delete dup sigs: " + ", ".join(s[0] for s in p["signals_delete"]))
        if p["paper_trades_delete"]:
            notes.append(f"del {p['paper_trades_delete']} paper_trade(s)")
        note_str = ("  [" + " | ".join(notes) + "]") if notes else ""
        print(f"    [{i:3d}] KEEP {keeper_s}  REMOVE {remover_s}{note_str}")


def _print_review(review: list[tuple[str, str, dict]]) -> None:
    if not review:
        print("  (none)")
        return
    print(f"  {len(review)} candidate(s) — NOT auto-removed unless --include-review is passed")
    print()
    for i, (fp_a, fp_b, info) in enumerate(review, 1):
        print(
            f"    [{i:2d}] {info['ticker']:6} | {info['director'][:35]:35} | "
            f"{info['type']:8} | {info['shares']:>10} @ {info['price']}"
        )
        print(
            f"         date_A={info['date_a']}  date_B={info['date_b']}  "
            f"({info['diff_days']}d apart)"
        )
        print(f"         URL_A: {info['url_a'][-70:] or '(none)'}")
        print(f"         URL_B: {info['url_b'][-70:] or '(none)'}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Execute the dedup (default is dry-run).",
    )
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="Also remove Class-B-REVIEW near-dup candidates (inspect list first).",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=7,
        metavar="DAYS",
        help="Date window for Class-B near-duplicate detection (default 7).",
    )
    args = parser.parse_args()
    dry_run = not args.confirm

    if not DB_PATH.exists():
        sys.exit(f"ERROR: DB not found at {DB_PATH}")

    # -----------------------------------------------------------------------
    # Load all rows once (read-only)
    # -----------------------------------------------------------------------
    con_ro = _connect(readonly=True)
    sql = """
        SELECT fingerprint, first_seen, last_seen, seen_count,
               date, ticker, company, director,
               LOWER(TRIM(director)) AS dir,
               type, shares, price, url
        FROM transactions
        ORDER BY date, ticker, LOWER(TRIM(director)), type, shares, price,
                 first_seen ASC, seen_count DESC, fingerprint ASC
    """
    rows = [dict(r) for r in con_ro.execute(sql).fetchall()]
    total_before = len(rows)

    # -----------------------------------------------------------------------
    # Detect duplicates
    # -----------------------------------------------------------------------
    a_pairs = _find_class_a_pairs(rows)
    b_result = _find_class_b_pairs(rows, window_days=args.window)
    b_same_pairs = b_result["same"]   # list of (keeper_fp, remover_fp, info)
    b_review = b_result["review"]     # list of (fp_a, fp_b, info)
    c_pairs = _find_class_c_pairs(rows)  # list of (keeper_fp, remover_fp, reason)

    # Build action plans
    a_plans = [_build_plan(con_ro, k, r) for k, r in a_pairs]
    b_same_plans = [_build_plan(con_ro, k, r) for k, r, _ in b_same_pairs]
    c_plans = [_build_plan(con_ro, k, r) for k, r, _ in c_pairs]
    b_review_plans = []
    if args.include_review:
        # Keep the row with the non-empty URL; fallback to older first_seen
        for fp_a, fp_b, info in b_review:
            row_a = next(r for r in rows if r["fingerprint"] == fp_a)
            row_b = next(r for r in rows if r["fingerprint"] == fp_b)
            has_url_a = bool(info["url_a"])
            has_url_b = bool(info["url_b"])
            if has_url_a and not has_url_b:
                keeper, remover = fp_a, fp_b
            elif has_url_b and not has_url_a:
                keeper, remover = fp_b, fp_a
            elif row_a["first_seen"] <= row_b["first_seen"]:
                keeper, remover = fp_a, fp_b
            else:
                keeper, remover = fp_b, fp_a
            b_review_plans.append(_build_plan(con_ro, keeper, remover))

    con_ro.close()

    # -----------------------------------------------------------------------
    # Print summary
    # -----------------------------------------------------------------------
    mode = "DRY-RUN" if dry_run else "EXECUTING"
    print(f"\n{'='*64}")
    print(f"  dedup_transactions.py  [{mode}]")
    print(f"  Total transactions in DB: {total_before}")
    print(f"{'='*64}\n")

    print(f"CLASS A — Exact logical duplicates (same deal, different fingerprint)")
    print(f"{'─'*64}")
    _print_plans("Class A", a_plans)

    print(f"\nCLASS B-SAME — Near-duplicates with matching announcement ID (auto-safe)")
    print(f"{'─'*64}")
    _print_plans("Class B-SAME", b_same_plans)

    print(f"\nCLASS B-REVIEW — Near-duplicates for manual inspection")
    print(f"{'─'*64}")
    _print_review(b_review)

    if args.include_review and b_review_plans:
        print(f"\n  --include-review plans ({len(b_review_plans)} rows):")
        _print_plans("Class B-REVIEW (included)", b_review_plans)

    print(f"\nCLASS C — URL-based parse artefacts (SPURIOUS_BUY / PARSE_FRAGMENT / SELL_VS_SELL_TAX)")
    print(f"{'─'*64}")
    if c_plans:
        c_cats: dict[str, int] = defaultdict(int)
        for _, _, reason in c_pairs:
            c_cats[reason.split("|")[0].strip()] += 1
        for cat, n in sorted(c_cats.items()):
            print(f"  {cat}: {n}")
        print()
    _print_plans("Class C", c_plans)

    all_plans = a_plans + b_same_plans + (b_review_plans if args.include_review else []) + c_plans

    if dry_run:
        total_auto = len(a_plans) + len(b_same_plans) + len(c_plans)
        print(f"\n{'='*64}")
        print(f"  DRY-RUN SUMMARY")
        print(f"  Auto-removable (Class A + B-SAME + C) : {total_auto} rows")
        print(f"  For review (Class B-REVIEW)            : {len(b_review)} pairs")
        print(f"  Run with --confirm to apply Class A + B-SAME + C.")
        print(f"  Add --include-review to also apply B-REVIEW after inspection.")
        print(f"{'='*64}\n")
        return

    if not all_plans:
        print("Nothing to remove. Database is clean.")
        return

    # -----------------------------------------------------------------------
    # Backup
    # -----------------------------------------------------------------------
    print(f"\nBacking up DB to {BACKUP_PATH.name} ...")
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"  Backup written: {BACKUP_PATH}")

    # -----------------------------------------------------------------------
    # Execute — single atomic transaction
    # -----------------------------------------------------------------------
    print(f"\nApplying {len(all_plans)} removals ...")
    con = _connect(readonly=False)
    try:
        with con:
            for plan in all_plans:
                _execute_plan(con, plan)
    except Exception as exc:
        con.close()
        sys.exit(f"\nERROR — rolled back. DB unchanged.\n{exc}")

    # Post-run verification
    remaining_a = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT date, ticker, LOWER(TRIM(director)) AS dir, type, shares, price
            FROM transactions
            GROUP BY date, ticker, dir, type, shares, price
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    tx_after = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    sig_after = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    con.close()

    print(f"\n{'='*64}")
    print(f"  DONE")
    print(f"  Transactions: {total_before} → {tx_after}  (-{total_before - tx_after})")
    print(f"  Signals in DB after : {sig_after}")
    print(f"  Remaining Class-A duplicates: {remaining_a}  (should be 0)")
    print(f"{'='*64}")

    if remaining_a > 0:
        print(f"\nWARNING: {remaining_a} exact-dup groups remain — investigate manually.")
    else:
        print(f"\nRemember to run:")
        print(f"  python .scripts/snapshot_db.py")
        print(f"  python .scripts/eval_signals.py --rebuild")
        print(f"  python .scripts/export_dashboard_json.py")
        print(f"  python .scripts/build_dashboard.py")


if __name__ == "__main__":
    main()
