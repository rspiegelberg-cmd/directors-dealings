"""Sprint 64 / B-168 -- director salary-multiple collection.

Populates the `director_pay` table (migration 015) so the backtest can compute
the salary-multiple conviction feature. Feature column only -- no firing signal.
Spec: docs/specs/b168-salary-multiple-plan.md.

FUSE rule (CLAUDE.md): this script writes .data/directors.db and
.scripts/_pay_cache/. Run from Windows PowerShell, never from the Linux sandbox.

Design (resumable harness, three lanes that all feed one validated upsert path)
------------------------------------------------------------------------------
The B-163 spike showed sources are heterogeneous: ~2/3 of established directors
have a clean audited remuneration table in an AR/DRR PDF (scriptable via
curl+pdftotext), the rest sit behind JS portals or only in press (human ~3 ops
each). So this script does NOT pretend to fully auto-scrape 952 names. It:

  --worklist      Build the prioritised target list from the DB (firing-frequency
                  order, board directors only -- PCA-only names excluded). Writes
                  .data/_pay_worklist.csv. This is the spine the human works down.

  --from-sources  Auto lane (rung b). Read .data/_pay_sources.csv
                  (ticker, director, fy_end, ar_url, ar_published_at, currency),
                  download each AR PDF (cached), pdftotext it, extract the single
                  figure + base salary, FX-convert, classify, stage for upsert.

  --from-manual   Human lane (rungs c / JS-portal / nominal / new-appointee).
                  Read .data/_pay_manual.csv (ticker, director, fy_end, pay_native,
                  currency, pay_kind, status, ar_published_at, source_url,
                  source_rung, confidence, machine_readable). Same validated path.

All lanes are preview-by-default; pass --confirm to write. Every applied upsert
is appended to .data/_director_pay_backfill.log (JSONL append, never RMW). Cache
under .scripts/_pay_cache/ makes re-runs free and the annual refresh incremental.

CLI:
    python .scripts/backfill_director_pay.py --worklist
    python .scripts/backfill_director_pay.py --from-sources            # preview
    python .scripts/backfill_director_pay.py --from-sources --confirm
    python .scripts/backfill_director_pay.py --from-manual --confirm
    python .scripts/snapshot_db.py                                     # then snapshot
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import director_pay as dp  # noqa: E402

CACHE_DIR = HERE / "_pay_cache"
AUDIT_LOG = db.DB_DIR / "_director_pay_backfill.log"
WORKLIST_CSV = db.DB_DIR / "_pay_worklist.csv"
SOURCES_CSV = db.DB_DIR / "_pay_sources.csv"
MANUAL_CSV = db.DB_DIR / "_pay_manual.csv"

USER_AGENT = ("DirectorsDealingsBot/1.0 (personal research project; "
              "contact rspiegelberg@gmail.com)")

# Buy-side signal ids that put a director in scope. b2 is a kill-filter (not a
# buy); t5_pca_buy is a PCA (non-board) -- both excluded, so PCA-only names drop.
_BUY_SIGNALS = (
    "b1_lone_conviction_buy", "f1_first_time_buy", "s1_cluster_buy",
    "t0_cluster_combo", "t1a_ceo_founder_buy", "t1b_cfo_buy", "t2_exec_buy",
    "t3_ned_buy", "t4_other_buy", "t6_company_sec_buy", "t7_chair_buy",
)


# --- Pure helpers (unit-tested; no network / no DB) -------------------------

_NUM = r"[\d][\d,]{2,}(?:\.\d+)?"   # 1,234 / 11,600 / 3,108,751(.00)


def _to_number(s: str):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def extract_pay_figures(text: str) -> dict:
    """Best-effort extraction of {total, base} from pdftotext output.

    Targets the audited "single total figure of remuneration" table common to
    Main-Market ARs and most AIM DRRs (the format the spike found scriptable).
    Conservative: returns None for a figure it cannot find on a recognised row,
    so the manual lane can fill the rest rather than this guessing. Numbers are
    returned in the document's own units (FX handled later by the caller).
    """
    if not text:
        return {"total": None, "base": None}
    flat = re.sub(r"[ \t]+", " ", text)
    total = base = None

    # Single total figure -- prefer an explicit "single total figure" / "total
    # remuneration" / "total (single figure)" row; fall back to a "Total" row.
    for pat in (
        r"single total figure(?:\s+of\s+remuneration)?[^\n]*?(" + _NUM + r")",
        r"total\s+remuneration[^\n]*?(" + _NUM + r")",
        r"\btotal\b[^\n]*?(" + _NUM + r")",
    ):
        m = re.search(pat, flat, re.IGNORECASE)
        if m:
            total = _to_number(m.group(1))
            break

    # Base salary -- "base salary" preferred over a bare "salary" row.
    for pat in (
        r"base salary[^\n]*?(" + _NUM + r")",
        r"\bsalary\b[^\n]*?(" + _NUM + r")",
    ):
        m = re.search(pat, flat, re.IGNORECASE)
        if m:
            base = _to_number(m.group(1))
            break

    return {"total": total, "base": base}


def build_record(*, ticker, director_name, fy_end, pay_native, currency,
                 pay_kind, status="ok", role_class=None, ar_published_at=None,
                 source_url=None, source_rung=None, confidence=None,
                 machine_readable=0) -> dict:
    """Assemble a director_pay upsert dict: FX-convert, classify nominal, map
    pay_kind -> pay_type. Returns a row ready for dp.upsert_director_pay.

    pay_kind in {total, base, ned_fees, none}. A no-figure / non-ok status row
    collapses to pay_type='none', fy_end='' so the 4-part unique key stays clean.
    """
    dkey = dp.director_key(director_name)
    base_row = {
        "ticker": ticker, "director_key": dkey,
        "director_name_raw": director_name, "role_class": role_class,
        "ar_published_at": ar_published_at or None, "source_url": source_url,
        "source_rung": source_rung, "confidence": confidence,
        "machine_readable": 1 if machine_readable else 0,
    }

    if pay_kind == "none" or status != "ok":
        st = status if status != "ok" else "extraction_fail"
        return dict(base_row, fy_end="", pay_type="none", pay_status=st,
                    pay_native=None, currency=None, fx_rate=None,
                    fx_date=None, pay_gbp=None)

    conv = dp.convert_to_gbp(pay_native, currency, fy_end)
    if conv is None:
        # bad figure or unsupported currency -> record as extraction_fail
        return dict(base_row, fy_end="", pay_type="none",
                    pay_status="extraction_fail", pay_native=None,
                    currency=None, fx_rate=None, fx_date=None, pay_gbp=None)

    nominal = dp.classify_nominal(conv["pay_gbp"])
    if nominal == "ok":
        pay_type = {"total": "single_figure_total", "base": "base_salary",
                    "ned_fees": "ned_fees"}.get(pay_kind, "single_figure_total")
    else:
        pay_type = nominal  # fee_waiver_zero / nominal

    return dict(base_row, fy_end=fy_end or "", pay_type=pay_type,
                pay_status="ok", pay_native=float(pay_native),
                currency=conv["currency"], fx_rate=conv["fx_rate"],
                fx_date=conv["fx_date"], pay_gbp=conv["pay_gbp"])


# --- DB: worklist selection -------------------------------------------------

def select_targets(conn) -> list[dict]:
    """In-scope (ticker, director) targets ordered by buy-signal frequency.

    Joins buy-side signals to transactions, drops excluded issuers and PCA-only
    names, and aggregates by the canonical director_key. role_class is the most
    recent role_normalized seen for that director.
    """
    placeholders = ",".join("?" for _ in _BUY_SIGNALS)
    rows = conn.execute(
        f"SELECT t.ticker AS ticker, t.director AS director, "
        f"       t.role_normalized AS role_class, t.date AS dt "
        f"FROM signals s JOIN transactions t ON t.fingerprint = s.fingerprint "
        f"LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        f"WHERE s.signal_id IN ({placeholders}) "
        f"  AND t.ticker NOT LIKE '^%' AND t.director IS NOT NULL "
        f"  AND COALESCE(tm.is_excluded_issuer, 0) = 0",
        _BUY_SIGNALS,
    ).fetchall()

    agg: dict[tuple, dict] = {}
    for r in rows:
        dkey = dp.director_key(r["director"])
        key = (r["ticker"], dkey)
        rec = agg.get(key)
        if rec is None:
            agg[key] = rec = {
                "ticker": r["ticker"], "director": r["director"],
                "director_key": dkey, "role_class": r["role_class"],
                "buy_signals": 0, "_latest": r["dt"] or "",
            }
        rec["buy_signals"] += 1
        if (r["dt"] or "") >= rec["_latest"]:   # keep most-recent role + name
            rec["_latest"] = r["dt"] or ""
            rec["role_class"] = r["role_class"]
            rec["director"] = r["director"]
    out = list(agg.values())
    out.sort(key=lambda d: d["buy_signals"], reverse=True)
    return out


def write_worklist(targets: list[dict]) -> None:
    WORKLIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    with WORKLIST_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "ticker", "director", "director_key",
                    "role_class", "buy_signals", "status"])
        for i, t in enumerate(targets, 1):
            w.writerow([i, t["ticker"], t["director"], t["director_key"],
                        t["role_class"] or "", t["buy_signals"], ""])


# --- Network / binary (thin wrappers; not unit-tested) ----------------------

def _cache_paths(url: str) -> tuple[Path, Path]:
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.pdf", CACHE_DIR / f"{h}.txt"


def fetch_pdf_text(url: str, *, verbose: bool = False) -> str | None:
    """Download a PDF (cached) and return its pdftotext -layout output (cached).

    Returns None on download / conversion failure (caller records extraction_fail).
    Requires the `pdftotext` binary (poppler-utils) on PATH.
    """
    pdf_path, txt_path = _cache_paths(url)
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8", errors="replace")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not pdf_path.exists():
        try:
            import requests
            resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                                timeout=60)
            resp.raise_for_status()
            pdf_path.write_bytes(resp.content)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  download fail {url}: {exc}", file=sys.stderr)
            return None
    try:
        out = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                             capture_output=True, timeout=120)
        if out.returncode != 0:
            if verbose:
                print(f"  pdftotext fail {url}: {out.stderr[:200]!r}",
                      file=sys.stderr)
            return None
        text = out.stdout.decode("utf-8", errors="replace")
        txt_path.write_text(text, encoding="utf-8")
        return text
    except FileNotFoundError:
        print("  ERROR: pdftotext not found -- install poppler-utils.",
              file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  pdftotext error {url}: {exc}", file=sys.stderr)
        return None


# --- Apply path -------------------------------------------------------------

def _append_audit(entries: list[dict]) -> None:
    if not entries:
        return
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _stage(records: list[dict], conn, *, confirm: bool, verbose: bool) -> int:
    """Upsert (or preview) a list of built records. Returns count written."""
    n = 0
    audit = []
    for rec in records:
        if verbose:
            verb = "UPSERT" if confirm else "WOULD UPSERT"
            print(f"  {verb}: {rec['ticker']:6} {rec['director_key'][:24]:24} "
                  f"{rec['pay_type']:18} "
                  f"{('GBP %0.0f' % rec['pay_gbp']) if rec.get('pay_gbp') else rec['pay_status']}")
        if confirm:
            dp.upsert_director_pay(conn, rec)
            audit.append({"ts": db.iso_now(), "ticker": rec["ticker"],
                          "director_key": rec["director_key"],
                          "fy_end": rec.get("fy_end", ""),
                          "pay_type": rec["pay_type"],
                          "pay_status": rec["pay_status"],
                          "pay_gbp": rec.get("pay_gbp")})
        n += 1
    if confirm:
        conn.commit()
        _append_audit(audit)
    return n


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  (no {path.name} found at {path})")
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run_from_sources(conn, *, confirm: bool, verbose: bool) -> None:
    rows = _read_csv(SOURCES_CSV)
    print(f"_pay_sources.csv rows: {len(rows)}")
    records, fails = [], 0
    for r in rows:
        text = fetch_pdf_text(r.get("ar_url", ""), verbose=verbose)
        if not text:
            fails += 1
            records.append(build_record(
                ticker=r["ticker"], director_name=r["director"],
                fy_end=r.get("fy_end"), pay_native=None,
                currency=r.get("currency"), pay_kind="none",
                status="extraction_fail", ar_published_at=r.get("ar_published_at"),
                source_url=r.get("ar_url"), source_rung="b"))
            continue
        figs = extract_pay_figures(text)
        for kind, val in (("total", figs["total"]), ("base", figs["base"])):
            if val is None:
                continue
            records.append(build_record(
                ticker=r["ticker"], director_name=r["director"],
                fy_end=r.get("fy_end"), pay_native=val,
                currency=r.get("currency") or "GBP", pay_kind=kind,
                ar_published_at=r.get("ar_published_at"),
                source_url=r.get("ar_url"), source_rung="b",
                confidence="high", machine_readable=1))
    n = _stage(records, conn, confirm=confirm, verbose=verbose)
    print(f"  staged {n} record(s); {fails} source(s) failed extraction")


def run_from_manual(conn, *, confirm: bool, verbose: bool) -> None:
    rows = _read_csv(MANUAL_CSV)
    print(f"_pay_manual.csv rows: {len(rows)}")
    records = []
    for r in rows:
        records.append(build_record(
            ticker=r["ticker"], director_name=r["director"],
            fy_end=r.get("fy_end"),
            pay_native=_to_number(r.get("pay_native", "")) if r.get("pay_native") else None,
            currency=r.get("currency") or "GBP",
            pay_kind=r.get("pay_kind") or "total",
            status=r.get("status") or "ok",
            ar_published_at=r.get("ar_published_at"),
            source_url=r.get("source_url"), source_rung=r.get("source_rung") or "c",
            confidence=r.get("confidence") or "medium",
            machine_readable=int(r.get("machine_readable") or 0)))
    n = _stage(records, conn, confirm=confirm, verbose=verbose)
    print(f"  staged {n} record(s)")


def coverage_report(conn) -> None:
    total = conn.execute("SELECT COUNT(*) c FROM director_pay").fetchone()["c"]
    by_status = conn.execute(
        "SELECT pay_status, COUNT(*) c FROM director_pay GROUP BY pay_status"
    ).fetchall()
    by_type = conn.execute(
        "SELECT pay_type, COUNT(*) c FROM director_pay GROUP BY pay_type"
    ).fetchall()
    print()
    print(f"director_pay rows: {total}")
    print("  by status:", {r["pay_status"]: r["c"] for r in by_status})
    print("  by type:  ", {r["pay_type"]: r["c"] for r in by_type})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worklist", action="store_true",
                    help="Build the prioritised target list -> _pay_worklist.csv")
    ap.add_argument("--from-sources", action="store_true",
                    help="Auto lane: extract pay from AR PDFs in _pay_sources.csv")
    ap.add_argument("--from-manual", action="store_true",
                    help="Human lane: ingest figures from _pay_manual.csv")
    ap.add_argument("--confirm", action="store_true",
                    help="Apply writes. Default: preview only.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    conn = db.connect()   # applies migration 015 if not yet applied
    try:
        if args.worklist:
            targets = select_targets(conn)
            write_worklist(targets)
            print(f"Worklist: {len(targets)} in-scope (ticker, director) targets "
                  f"-> {WORKLIST_CSV}")
            if targets:
                print("  top 5:", [(t["ticker"], t["buy_signals"])
                                   for t in targets[:5]])
        if args.from_sources:
            run_from_sources(conn, confirm=args.confirm, verbose=args.verbose)
        if args.from_manual:
            run_from_manual(conn, confirm=args.confirm, verbose=args.verbose)
        if not (args.worklist or args.from_sources or args.from_manual):
            print("Nothing to do. Pass --worklist, --from-sources, or --from-manual.")
        coverage_report(conn)
        if not args.confirm and (args.from_sources or args.from_manual):
            print("\nPREVIEW (no DB writes). Re-run with --confirm to apply.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
