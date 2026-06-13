"""READ-ONLY diagnostic: size the pence/pounds value misparse (B-060).

Context (B-060 / spec parser-fix-comp-events-and-pence-2026-06-03 Fix 2):
`parse_pdmr.py` stores `price` in POUNDS. Explicit-pence inputs ("171p")
are divided by 100. But a BARE numeric price cell with no currency marker
is assumed to be POUNDS, whereas UK RNS table cells usually quote pence.
So "171" (meaning 171p = GBP 1.71) is stored as GBP 171 and
`value = price * shares` is inflated 100x.

R3 in `parse_pdmr.py` rejects any row with price > GBP 200 (unless the
ticker is in HIGH_PRICED_TRUST_ALLOWLIST), so the worst offenders are
quarantined. The live contamination window is therefore roughly
GBP 50 - 200 per share (and GBP 10 - 50 is a softer suspect band):
genuine UK per-share prices above ~GBP 50 are rare.

This script writes NOTHING to the DB or caches. Run from PowerShell:

    python .scripts/_diag_pence_value.py

It prints:
  1. price distribution buckets across all stored transactions
  2. suspect rows (price > GBP 50, ticker not in the high-priced allowlist)
     -- the strong pence-misread candidates, with url for cross-checking
  3. how many suspects are type=BUY and how many have a fired signal
     (blast radius on the signal layer / briefs)
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

# Genuine high-priced UK shares (quoted in pence at 4-5 figures, so a
# legitimately large GBP-per-share price). Mirror of parse_pdmr.py
# HIGH_PRICED_TRUST_ALLOWLIST -- keep in sync.
HIGH_PRICED_ALLOWLIST = {"LTI", "NXT", "AZN", "GAW"}

STRONG_SUSPECT_GBP = 50.0   # above this, ticker not allowlisted == likely pence
SOFT_SUSPECT_LO = 10.0      # 10-50 is a softer band worth a glance


def _has_column(conn, table: str, col: str) -> bool:
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def price_buckets(conn) -> None:
    print("=== 1. PRICE DISTRIBUTION (price is stored in GBP/share) ===")
    bounds = [
        (None, 1.0, "< 1"),
        (1.0, 10.0, "1 - 10"),
        (10.0, 50.0, "10 - 50"),
        (50.0, 100.0, "50 - 100   <-- suspect"),
        (100.0, 200.0, "100 - 200  <-- suspect"),
        (200.0, None, "> 200      (R3 should have blocked these)"),
    ]
    for lo, hi, lbl in bounds:
        q = "SELECT COUNT(*) FROM transactions WHERE price IS NOT NULL AND price > 0"
        if lo is not None:
            q += f" AND price >= {lo}"
        if hi is not None:
            q += f" AND price < {hi}"
        n = conn.execute(q).fetchone()[0]
        print(f"  GBP {lbl:42s} {n}")
    zero = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE price IS NULL OR price = 0"
    ).fetchone()[0]
    print(f"  GBP {'0 / null (grants, nil-cost, parse fail)':42s} {zero}")
    mn, mx, av = conn.execute(
        "SELECT MIN(price), MAX(price), AVG(price) FROM transactions "
        "WHERE price IS NOT NULL AND price > 0"
    ).fetchone()
    print(f"  min={mn}  max={mx}  avg={av:.3f}" if av else "  (no priced rows)")


def suspects(conn) -> list:
    allowlist = ",".join(f"'{t}'" for t in HIGH_PRICED_ALLOWLIST)
    has_audit = _has_column(conn, "transactions", "price_audit")
    audit_sel = "COALESCE(price_audit,'(none)') AS audit" if has_audit \
        else "'(no col)' AS audit"
    rows = list(conn.execute(
        f"SELECT fingerprint, date, ticker, director, type, shares, price, "
        f"       value, url, {audit_sel} "
        f"FROM transactions "
        f"WHERE price > {STRONG_SUSPECT_GBP} "
        f"  AND ticker NOT IN ({allowlist}) "
        f"ORDER BY price DESC"
    ))
    print(f"\n=== 2. STRONG SUSPECTS: price > GBP {STRONG_SUSPECT_GBP:.0f}, "
          f"not allowlisted ({len(rows)} rows) ===")
    # B-060: most of these are now resolved. Break down by price_audit so a
    # large suspect count doesn't read as 'still broken'. ok_pounds = legit
    # high-priced (e.g. LSEG); unresolved/no_market = flagged out of signals
    # and GBP metrics; corrected_pence = fixed; (none) = not yet reconciled.
    by_audit = Counter(r["audit"] for r in rows)
    print("   flag status (post-reconciliation):")
    for flag in ("corrected_pence", "ok_pounds", "unresolved", "no_market",
                 "(none)", "(no col)"):
        if by_audit.get(flag):
            tag = "excluded from signals + GBP metrics" if flag in (
                "unresolved", "no_market") else (
                "legit / trusted" if flag in ("ok_pounds", "corrected_pence")
                else "NOT yet reconciled -- run backfill_price_units")
            print(f"     {flag:16s} {by_audit[flag]:4d}   ({tag})")
    print("   (if real price is pence, divide price & value by 100 to check "
          "against the filing)")
    for r in rows[:60]:
        implied_pence = r["price"] / 100.0
        implied_val = (r["value"] or 0) / 100.0
        print(f"  {r['ticker']:6s} {r['date']} {r['type']:8s} "
              f"[{r['audit']:14s}] "
              f"px=GBP{r['price']:>9.2f} (->{implied_pence:>7.2f} if pence)  "
              f"sh={r['shares']:>9}  val=GBP{r['value'] or 0:>14,.0f} "
              f"(->{implied_val:>12,.0f})  {r['director'][:20]:20s}")
    if len(rows) > 60:
        print(f"  ... {len(rows) - 60} more")
    return rows


def soft_band(conn) -> None:
    allowlist = ",".join(f"'{t}'" for t in HIGH_PRICED_ALLOWLIST)
    n = conn.execute(
        f"SELECT COUNT(*) FROM transactions "
        f"WHERE price >= {SOFT_SUSPECT_LO} AND price <= {STRONG_SUSPECT_GBP} "
        f"  AND ticker NOT IN ({allowlist})"
    ).fetchone()[0]
    print(f"\n=== 2b. SOFT band GBP {SOFT_SUSPECT_LO:.0f}-{STRONG_SUSPECT_GBP:.0f} "
          f"(needs manual check; many are genuine) === {n} rows")


def blast_radius(conn, suspect_rows: list) -> None:
    print("\n=== 3. BLAST RADIUS ===")
    n_buy = sum(1 for r in suspect_rows if r["type"] == "BUY")
    print(f"  suspects total: {len(suspect_rows)}   of which type=BUY: {n_buy}")
    if not _has_column(conn, "signals", "fingerprint"):
        print("  (no signals table -- skip fired-signal check)")
        return
    fps = [r["fingerprint"] for r in suspect_rows]
    if not fps:
        print("  no suspects -> no fired signals")
        return
    fired = Counter()
    qmarks = ",".join("?" for _ in fps)
    for r in conn.execute(
        f"SELECT signal_id, COUNT(*) n FROM signals "
        f"WHERE fingerprint IN ({qmarks}) GROUP BY signal_id", fps
    ):
        fired[r["signal_id"]] = r["n"]
    total_fired = sum(fired.values())
    print(f"  suspect fingerprints with a FIRED signal: {total_fired}")
    for sid, n in fired.most_common():
        print(f"    {sid:18s} {n}")
    if total_fired:
        print("  ^ these signals' CAR means may be distorted by 100x value rows")


def main() -> None:
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        price_buckets(conn)
        rows = suspects(conn)
        soft_band(conn)
        blast_radius(conn, rows)
    finally:
        conn.close()
    print("\nDone. READ-ONLY -- nothing written.")


if __name__ == "__main__":
    main()
