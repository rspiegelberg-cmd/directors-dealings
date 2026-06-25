"""Stage 5 dashboard build orchestrator.

CLI::

    python -u .scripts/build_dashboard.py [--rebuild] [--verbose]
                                          [--out-dir PATH]
                                          [--signals-json PATH]
                                          [--dealings-json PATH]
                                          [--status-json PATH]

Reads ``dashboard/data/signals.json`` + ``dashboard/data/dealings.json`` +
``.data/signal_status.json`` and writes:

  * ``outputs/index.html``                -- daily action surface
  * ``outputs/performance.html``          -- diagnostics deep-dive
  * ``outputs/data/{signals,dealings}.json`` -- copies, so the dashboard
                                             can ``fetch()`` data via the
                                             same-origin Flask server.

B-184 cutover: per-ticker company pages are NO LONGER built here. They are
served by the single dynamic template ``outputs/company.html`` (reads
``?ticker=`` from the URL, fetches ``public_company_v`` from Supabase). All
company links emitted by the renderers now point at
``company.html?ticker={TICKER}``. The private review queue
(``pending_review.json`` / ``tx_index.json``) is also no longer copied into
the public ``outputs/data/`` bundle — see ``_copy_data_dir``.

Stdlib-only. Uses the DB for per-company data (transactions / prices /
clusters / firings + matured CAR from .data/_backtest_results.csv).

Truncation discipline: every output is written via .tmp + os.replace,
then a post-write sha256 + wc-by-line check is logged when --verbose is
set.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date as date_cls
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402

from dashboard import (  # noqa: E402
    render_index, render_performance, render_performance_drilldown,
    render_company,
)
from dashboard import render_helpers as rh  # noqa: E402
from dashboard import render_health_panel  # noqa: E402
import render_baskets   # noqa: E402  (B-140 basket report page)

DEFAULT_SIGNALS_JSON = ROOT / "dashboard" / "data" / "signals.json"
DEFAULT_DEALINGS_JSON = ROOT / "dashboard" / "data" / "dealings.json"
DEFAULT_STATUS_JSON = ROOT / ".data" / "signal_status.json"
DEFAULT_OUT_DIR = ROOT / "outputs"
DEFAULT_CSV_PATH = ROOT / ".data" / "_backtest_results.csv"
DEFAULT_CLUSTERS_PATH = ROOT / ".scripts" / "clusters.json"
DEFAULT_PENDING_PATH = ROOT / ".scripts" / "_pending_review.json"
DEFAULT_AUDIT_REPORT = ROOT / ".data" / "_date_audit_report.json"

# URL slug pattern for per-ticker fallback when extracted[].ticker is missing.
# Investegate URLs look like:
#   .../rns/card-factory--card/director-pdmr-shareholding/9564925
# The two-dash separator immediately precedes the lowercase ticker.
_URL_TICKER_RE = re.compile(r"--([a-z0-9._]+)/", re.IGNORECASE)


def _detect_build_sha() -> str:
    """Return git short SHA, or 'local' if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "local"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _load_backtest_rows(csv_path: Path) -> list[dict]:
    """Read _backtest_results.csv as list[dict]. Empty on missing file."""
    if not csv_path.exists():
        return []
    out = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(row)
    return out


def _norm_signal_id_short(long_id: str) -> str:
    return rh.SIGNAL_LONG_TO_SHORT.get(long_id, long_id)


def _safe_float(s):
    if s is None:
        return None
    try:
        if s == "":
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _build_company_record(conn, ticker: str, today: date_cls,
                          backtest_rows: list[dict],
                          clusters_json: list[dict],
                          active_clusters_short: dict,
                          generated_at: str,
                          pending_per_ticker: dict | None = None) -> dict:
    """Build a single per-ticker company dict for the company-page renderer."""
    # Ticker meta.  Sprint 26: include enrichment columns with COALESCE fallback
    # so pages build correctly on DBs that haven't run backfill_ticker_meta yet.
    meta = conn.execute(
        "SELECT ticker, sector, is_aim, benchmark_symbol, "
        "  COALESCE(market_cap_gbp, NULL)     AS market_cap_gbp, "
        "  COALESCE(shares_outstanding, NULL) AS shares_outstanding, "
        "  COALESCE(website_url, NULL)        AS website_url "
        "FROM tickers_meta WHERE ticker=?",
        (ticker,),
    ).fetchone()
    sector = meta["sector"] if meta else None
    is_aim = bool(meta["is_aim"]) if meta else False
    market_cap_gbp = float(meta["market_cap_gbp"]) if meta and meta["market_cap_gbp"] else None
    shares_outstanding = (float(meta["shares_outstanding"])
                          if meta and meta["shares_outstanding"] else None)
    website_url = meta["website_url"] if meta else None
    # Recent prices (trailing 13 months).
    cutoff = (today - timedelta(days=400)).isoformat()
    price_rows = conn.execute(
        "SELECT date, close, volume FROM prices WHERE ticker=? AND date >= ? "
        "ORDER BY date ASC",
        (ticker, cutoff),
    ).fetchall()
    # B-133: the company-page renderer reads each price row's benchmark close
    # from a `bench` key, but this builder never wrote it -> the BMK column was
    # always blank on every company. Resolve the ticker's benchmark symbol
    # (falls back to the universal ^FTAS), load its closes over the same window,
    # and attach `bench` per date. Renderer already tolerates None.
    bench_sym = (meta["benchmark_symbol"] if meta and meta["benchmark_symbol"]
                 else "^FTAS") or "^FTAS"
    bench_by_date = {
        r["date"]: (float(r["close"]) if r["close"] is not None else None)
        for r in conn.execute(
            "SELECT date, close FROM prices WHERE ticker=? AND date >= ? "
            "ORDER BY date ASC",
            (bench_sym, cutoff),
        ).fetchall()
    }
    prices = [
        {"date": r["date"], "close": float(r["close"]) if r["close"] is not None else None,
         "volume": int(r["volume"] or 0),
         "bench": bench_by_date.get(r["date"])}
        for r in price_rows
    ]
    latest_close = prices[-1]["close"] if prices else None
    prev_close = prices[-2]["close"] if len(prices) >= 2 else None
    company_name = None
    # Transactions for this ticker (latest first).
    # B-010: explicit (date DESC, announced_at DESC NULLS LAST, fingerprint ASC)
    # so per-company transaction tables match the Today / Performance pages.
    # Old COALESCE mixed timestamp + date strings; split them for stability.
    tx_rows = conn.execute(
        "SELECT fingerprint, date, announced_at, director, role, type, shares, "
        "       price, value, company, url, price_audit "
        "FROM transactions WHERE ticker=? "
        "ORDER BY date DESC, "
        "         (announced_at IS NULL OR announced_at = '') ASC, "
        "         announced_at DESC, fingerprint ASC",
        (ticker,),
    ).fetchall()
    # Signals per fingerprint (short codes).
    fp_to_sids = {}
    for fp_row in conn.execute(
        "SELECT s.fingerprint, s.signal_id FROM signals s "
        "JOIN transactions t ON t.fingerprint = s.fingerprint "
        "WHERE t.ticker=?", (ticker,),
    ).fetchall():
        fp_to_sids.setdefault(fp_row["fingerprint"], []).append(
            _norm_signal_id_short(fp_row["signal_id"]))
    txns = []
    for r in tx_rows:
        if company_name is None and r["company"]:
            company_name = r["company"]
        txns.append({
            "fingerprint": r["fingerprint"],
            "date": r["date"],
            "announced_at": r["announced_at"],
            "director": r["director"],
            "role": r["role"] or "",
            "txn_type": r["type"],
            "shares": int(r["shares"] or 0),
            "price": float(r["price"] or 0),
            "value": float(r["value"] or 0),
            "url": r["url"] or "",
            "company": r["company"] or "",
            "signals": fp_to_sids.get(r["fingerprint"], []),
            # B-120: unverified-price flag (B-060 audit) for the company table.
            "unverified": (
                (r["price_audit"] if "price_audit" in r.keys() else None)
                in ("unresolved", "no_market")
            ),
        })

    # Firings + matured CAR from backtest CSV (filtered to this ticker).
    firings = []
    for row in backtest_rows:
        if row.get("ticker") != ticker:
            continue
        firings.append({
            "fired_at": row.get("fired_at") or "",
            "entry_date": row.get("entry_date") or "",
            "signal_id": _norm_signal_id_short(row.get("signal_id") or ""),
            "director": row.get("role") or "",
            "car_t1":   _multiply_to_pct(row.get("net_car_t1")),
            "car_t30":  _multiply_to_pct(row.get("net_car_t30")),
            "car_t90":  _multiply_to_pct(row.get("net_car_t90")),
            "car_t365": _multiply_to_pct(row.get("net_car_t365")),
        })
    # Director name override from transactions where possible (CSV stores role
    # in the same column as director).
    fp_to_director = {r["fingerprint"]: r["director"] for r in tx_rows}
    for row, csv_row in zip(firings, [r for r in backtest_rows if r.get("ticker") == ticker]):
        fp = csv_row.get("fingerprint")
        if fp in fp_to_director:
            row["director"] = fp_to_director[fp] or row["director"]

    # Clusters that touch this ticker.
    clusters = []
    for c in clusters_json or []:
        if c.get("ticker") == ticker:
            clusters.append({
                "cluster_id":      c.get("cluster_id") or "-",
                "first_buy_date":  c.get("first_buy_date"),
                "last_buy_date":   c.get("last_buy_date"),
                "director_count":  c.get("director_count") or 0,
                "aggregate_value": c.get("aggregate_value_gbp") or 0,
                "active":          bool(c.get("s1_active") or c.get("active")),
            })

    # Active-cluster summary (from the signals.json active_clusters list).
    active_cluster = active_clusters_short.get(ticker)
    # Recent firing (in last 7 days).
    recent = None
    if firings:
        firings_sorted = sorted(
            firings, key=lambda r: r.get("fired_at") or "", reverse=True)
        try:
            most_recent_dt = datetime.fromisoformat(
                (firings_sorted[0].get("fired_at") or "").replace("Z", "+00:00")
            ).date()
            if (today - most_recent_dt).days <= 7:
                recent = {
                    "signal_id": firings_sorted[0].get("signal_id"),
                    "director": firings_sorted[0].get("director"),
                    "days_ago": (today - most_recent_dt).days,
                    "value": next(
                        (t["value"] for t in txns
                         if t["fingerprint"] == (
                             next((r.get("fingerprint")
                                   for r in backtest_rows
                                   if r.get("ticker") == ticker), ""))),
                        0),
                }
        except Exception:
            pass

    # Sprint 26 B-096: Fetch upcoming reporting dates for this ticker.
    # Also fetch confidence so we can label estimates as "(est)" on the dashboard.
    # Gracefully returns [] if reporting_dates table doesn't exist yet
    # (pre-migration or before backfill_reporting_dates.py is run).
    reporting_dates_list: list[str] = []
    rd_confidence: dict[str, str] = {}  # date_str -> 'confirmed' | 'est'
    try:
        rd_rows = conn.execute(
            "SELECT report_date, confidence FROM reporting_dates "
            "WHERE ticker = ? AND report_date >= ? "
            "ORDER BY report_date ASC",
            (ticker, today.isoformat()),
        ).fetchall()
        reporting_dates_list = [r["report_date"] for r in rd_rows]
        rd_confidence = {r["report_date"]: r["confidence"] for r in rd_rows}
    except Exception:
        pass  # Table may not exist on pre-migration DB — degrade gracefully.

    # All reporting dates (past + future) for chart markers.
    # Carried as a list of {date, confidence, type} dicts so the chart renderer
    # can draw vertical lines at historical results dates.
    all_reporting_dates: list[dict] = []
    try:
        all_rd_rows = conn.execute(
            "SELECT report_date, confidence, report_type, source_url "
            "FROM reporting_dates "
            "WHERE ticker = ? ORDER BY report_date DESC",
            (ticker,),
        ).fetchall()
        all_reporting_dates = [
            {"date": r["report_date"], "confidence": r["confidence"],
             "type": r["report_type"],
             "source_url": r["source_url"] if r["source_url"] else None}
            for r in all_rd_rows
        ]
    except Exception:
        pass

    # Sprint 26 B-096: flag each transaction row that falls within the
    # 60-day pre-results window for any reporting date.
    # Attach the nearest upcoming reporting_date + est flag to the txn dict.
    for txn in txns:
        txn_date_str = (txn.get("date") or "")[:10]
        near_report = None
        if txn_date_str:
            try:
                txn_d = date_cls.fromisoformat(txn_date_str)
                for rd_str in reporting_dates_list:
                    rd = date_cls.fromisoformat(rd_str)
                    days_before = (rd - txn_d).days
                    if 0 <= days_before <= 60:
                        near_report = rd_str
                        break  # Nearest upcoming date wins.
            except (ValueError, TypeError):
                pass
        txn["near_reporting_date"] = near_report
        txn["near_reporting_est"] = (
            rd_confidence.get(near_report) == "est"
        ) if near_report else False

    next_reporting_date = reporting_dates_list[0] if reporting_dates_list else None
    next_reporting_is_est = (
        rd_confidence.get(next_reporting_date) == "est"
    ) if next_reporting_date else False

    return {
        "ticker": ticker,
        "company": company_name or ticker,
        "sector": sector,
        "is_aim": is_aim,
        "market_cap_gbp": market_cap_gbp,
        "shares_outstanding": shares_outstanding,
        "website_url": website_url,
        "next_reporting_date": next_reporting_date,
        "next_reporting_is_est": next_reporting_is_est,
        "reporting_dates": reporting_dates_list,
        "all_reporting_dates": all_reporting_dates,
        "latest_close": latest_close,
        "prev_close": prev_close,
        "generated_at": generated_at,
        "active_cluster": active_cluster,
        "recent_firing": recent,
        "prices": prices,
        "transactions": txns,
        "firings": firings,
        "clusters": clusters,
        "pending_review": (pending_per_ticker or {}).get(ticker),
    }


def _multiply_to_pct(s):
    """Backtest CSV stores net_car_t* as fractions (-0.149 = -14.9%).
    Convert to percent for the dashboard renderers.
    """
    v = _safe_float(s)
    if v is None:
        return None
    return v * 100.0


def _build_active_clusters_lookup(signals_data: dict) -> dict:
    """Return ticker -> active_cluster dict from signals.json."""
    out = {}
    for c in (signals_data.get("active_clusters") or []):
        t = c.get("ticker")
        if not t:
            continue
        # Keep the first occurrence (signals.json lists them sorted by recency).
        out.setdefault(t, c)
    return out


def _ticker_from_pending_item(item: dict) -> str | None:
    """Best-effort ticker for a single _pending_review.json item.

    1. Prefer ``extracted[0].ticker`` when present.
    2. Fall back to ticker slug in the Investegate URL: ``--{TICKER}/``.
    3. Return None when neither yields a candidate.
    """
    if not isinstance(item, dict):
        return None
    extracted = item.get("extracted") or []
    if isinstance(extracted, list) and extracted:
        first = extracted[0] if isinstance(extracted[0], dict) else None
        if first:
            t = (first.get("ticker") or "").strip().upper()
            if t:
                return t
    url = (item.get("url") or "").strip()
    if url:
        m = _URL_TICKER_RE.search(url)
        if m:
            return m.group(1).strip().upper()
    return None


def _load_pending_per_ticker(pending_path: Path) -> dict:
    """Load _pending_review.json and bucket items per ticker.

    Returns ``{ticker: {"total": N, "categories": [...]}}`` using the same
    PENDING_BUCKET_SPEC + classifier as export_dashboard_json. Missing or
    unreadable file -> {} (silent fail; per-ticker panel just hides).
    """
    out: dict = {}
    if pending_path is None or not Path(pending_path).exists():
        return out
    try:
        from export_dashboard_json import (  # noqa: E402
            _load_pending_items,
            _classify_pending_warnings,
            PENDING_BUCKET_SPEC,
        )
    except Exception:
        return out

    items = _load_pending_items(Path(pending_path))
    if not items:
        return out

    # Build per-ticker bucket counts.
    spec_by_id = {s["id"]: s for s in PENDING_BUCKET_SPEC}
    per_ticker_counts: dict[str, dict[str, int]] = {}
    for rns_id, rec in items.items():
        ticker = _ticker_from_pending_item(rec)
        if not ticker:
            continue
        warns = rec.get("warnings") if isinstance(rec, dict) else None
        bucket = _classify_pending_warnings(warns or [])
        counts = per_ticker_counts.setdefault(ticker, {})
        counts[bucket] = counts.get(bucket, 0) + 1

    # Convert counts -> categories list (sorted by count desc, "other" last).
    for ticker, counts in per_ticker_counts.items():
        total = sum(counts.values())
        if total <= 0:
            continue
        named: list[dict] = []
        for spec in PENDING_BUCKET_SPEC:
            n = counts.get(spec["id"], 0)
            if n <= 0:
                continue
            named.append({
                "id":           spec["id"],
                "name":         spec["name"],
                "count":        n,
                "pct":          round(100.0 * n / total, 1),
                "recoverable":  spec["recoverable"],
                "description":  spec["description"],
            })
        named.sort(key=lambda d: d["count"], reverse=True)
        other_n = counts.get("other", 0)
        if other_n > 0:
            named.append({
                "id":           "other",
                "name":         "Other",
                "count":        other_n,
                "pct":          round(100.0 * other_n / total, 1),
                "recoverable":  "unknown",
                "description":  "Uncategorised -- review warnings text",
            })
        out[ticker] = {"total": total, "categories": named}
    return out


def _write_atomic_text(path: Path, text: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return len(text.encode("utf-8"))


def _copy_data_dir(signals_path: Path, dealings_path: Path, out_dir: Path,
                   status_path: Path | None) -> dict:
    """Copy the JSON inputs into outputs/data/ so the page can fetch() them
    via the same-origin Flask static handler.

    B-184: pending_review.json (~5MB) and tx_index.json are NO LONGER copied
    into the public outputs/data/ bundle — they are the private review queue
    and have no place on the public Vercel site. The local-only /review app
    reads them via dedicated Flask routes in server.py that serve them
    straight from dashboard/data/ (their source location, outside outputs/).
    Only the public dashboard JSONs (signals/dealings/signal_status) are
    copied here.
    """
    target = out_dir / "data"
    target.mkdir(parents=True, exist_ok=True)
    info = {}
    # Core dashboard JSONs (public-safe)
    for src, name in [(signals_path, "signals.json"),
                      (dealings_path, "dealings.json")]:
        if src.exists():
            dst = target / name
            tmp = Path(str(dst) + ".tmp")
            tmp.write_bytes(src.read_bytes())
            os.replace(tmp, dst)
            info[name] = dst.stat().st_size
    if status_path is not None and status_path.exists():
        dst = target / "signal_status.json"
        tmp = Path(str(dst) + ".tmp")
        tmp.write_bytes(status_path.read_bytes())
        os.replace(tmp, dst)
        info["signal_status.json"] = dst.stat().st_size
    return info


def _tickers_with_transactions(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM transactions "
        "WHERE ticker IS NOT NULL AND ticker NOT LIKE '^%' "
        "ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


_SAFE_TICKER_RE = re.compile(r"[^A-Z0-9._-]")


def _sanitize_ticker(ticker: str) -> str:
    """Return a safe filename for the ticker. Replaces unsafe chars with '_'.

    Keeps uppercase. Special-cases the LSE 'NG.' / 'BT.A' style suffix dot —
    we preserve the dot since OS allows it and the user expects to match the
    ticker symbol exactly.
    """
    if not ticker:
        return "UNKNOWN"
    return _SAFE_TICKER_RE.sub("_", ticker.upper())


_DRILL_TYPE_CONFIG = [
    # (cohort_type, json_filename, container_key, page_prefix)
    ("bucket", "performance_bucket.json", "buckets", "performance-bucket"),
    ("role",   "performance_role.json",   "roles",   "performance-role"),
    ("sector", "performance_sector.json", "sectors", "performance-sector"),
]


def _drill_slug_for_key(key) -> str:
    """Filename-safe slug for a cohort key. Mirrors `render_performance._slug_for_url`."""
    import re
    return re.sub(r'[^A-Za-z0-9_-]+', '-', str(key)).strip('-')


def _build_drill_pages(out_dir: Path, build_sha: str,
                       verbose: bool = False) -> int:
    """FE Sprint 2 — emit one HTML page per cohort key from the three
    performance_*.json files. Returns total pages written.

    Default view is t30 × 90d (lookback / horizon dropdowns are present
    but inert in v1 — v1.1 will add proper handling).

    B-184: company pages are now served by the dynamic `company.html?ticker=`
    template, so EVERY ticker has a valid page. We therefore pass
    `existing_company_pages=None` to the drilldown renderer, which makes its
    `hasCompanyPage()` return True for all rows (no more italic-faded
    dead-link rows). The old behaviour globbed `outputs/companies/*.html` to
    decide which rows were live — that set is now always empty, so globbing it
    would wrongly fade every row.
    """
    existing_pages = None  # dynamic template → every ticker is linkable

    data_dir = out_dir.parent / "dashboard" / "data"
    total_written = 0
    for cohort_type, json_name, container_key, page_prefix in _DRILL_TYPE_CONFIG:
        json_path = data_dir / json_name
        if not json_path.exists():
            if verbose:
                print(f"  [drill {cohort_type}] {json_name} missing, skipping")
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            if verbose:
                print(f"  [drill {cohort_type}] failed to load {json_name}: {e!r}")
            continue
        cohorts = payload.get(container_key) or {}
        n_for_type = 0
        for cohort_key in cohorts.keys():
            slug = _drill_slug_for_key(cohort_key)
            out_path = out_dir / f"{page_prefix}-{slug}.html"
            html = render_performance_drilldown.render_drilldown_page(
                cohort_type=cohort_type,
                cohort_key=cohort_key,
                payload=payload,
                horizon="t30",
                lookback="90d",
                existing_company_pages=existing_pages or None,
                build_sha=build_sha,
                generated_at_iso=payload.get("generated_at"),
            )
            out_path.write_text(html, encoding="utf-8")
            n_for_type += 1
            total_written += 1
        if verbose:
            print(f"  [drill {cohort_type}] {n_for_type} pages")
    return total_written


# B-193: _live_shell_html / _publish_live_index removed. The Supabase
# rendered_pages publish path is dead — the front page is now a live,
# hand-maintained client-side page (outputs/index.html reads Supabase data
# views directly in the browser). See the index.html skip in build() below.


def build(out_dir: Path, signals_path: Path, dealings_path: Path,
          status_path: Path | None,
          csv_path: Path, clusters_path: Path,
          build_sha: str, verbose: bool = False,
          pending_path: Path | None = None) -> dict:
    summary = {"pages": [], "bytes": 0}
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Pre-step: copy JSON inputs into outputs/data/ for fetch() ----
    data_info = _copy_data_dir(signals_path, dealings_path, out_dir, status_path)
    summary["data_copied"] = data_info

    # ---- Date integrity health panel (read audit report if present) ----
    health_panel_html = render_health_panel.render_panel_from_path(
        DEFAULT_AUDIT_REPORT
    )

    # ---- index.html — DO NOT GENERATE (B-193 / M6) ----
    # The front page is now a LIVE, client-side page: outputs/index.html reads
    # Supabase directly in the browser (like the company pages), and is HAND-
    # MAINTAINED, not generated here. If build_dashboard regenerated it, the
    # daily job would overwrite the live page with the old static render and
    # clobber it (caused a merge conflict 2026-06-25). So we deliberately leave
    # outputs/index.html untouched. The old render_index + rendered_pages
    # publish path is dead (helpers removed in B-193).
    if verbose:
        print("[index] skipped — front page is the live client-side page")

    # ---- performance.html (combined / "All") ----
    n = render_performance.render_to_file(
        signals_path=signals_path, status_path=status_path,
        out_path=out_dir / "performance.html", build_sha=build_sha)
    summary["pages"].append(("performance.html", n,
                             _sha256(out_dir / "performance.html")))
    summary["bytes"] += n
    if verbose:
        print(f"[performance] {n} bytes")

    # ---- performance_small.html (Sprint 56 Phase D) ----
    _data_dir = out_dir.parent / "dashboard" / "data"
    _sig_small = _data_dir / "signals_small.json"
    if _sig_small.exists():
        n = render_performance.render_to_file(
            signals_path=_sig_small, status_path=status_path,
            out_path=out_dir / "performance_small.html",
            build_sha=build_sha, size_band="small")
        summary["pages"].append(("performance_small.html", n,
                                 _sha256(out_dir / "performance_small.html")))
        summary["bytes"] += n
        if verbose:
            print(f"[performance_small] {n} bytes")
    else:
        if verbose:
            print("[performance_small] signals_small.json not found, skipping")

    # ---- performance_large.html (Sprint 56 Phase D) ----
    _sig_large = _data_dir / "signals_large.json"
    if _sig_large.exists():
        n = render_performance.render_to_file(
            signals_path=_sig_large, status_path=status_path,
            out_path=out_dir / "performance_large.html",
            build_sha=build_sha, size_band="large")
        summary["pages"].append(("performance_large.html", n,
                                 _sha256(out_dir / "performance_large.html")))
        summary["bytes"] += n
        if verbose:
            print(f"[performance_large] {n} bytes")
    else:
        if verbose:
            print("[performance_large] signals_large.json not found, skipping")

    # ---- baskets.html (B-140) ----
    baskets_json_path = ROOT / ".data" / "baskets.json"
    n = render_baskets.render_to_file(
        baskets_json_path=baskets_json_path,
        out_path=out_dir / "baskets.html",
        build_sha=build_sha,
    )
    summary["pages"].append(("baskets.html", n,
                             _sha256(out_dir / "baskets.html")))
    summary["bytes"] += n
    if verbose:
        print(f"[baskets] {n} bytes")

    # ---- performance-{bucket|role|sector}-{key}.html (FE Sprint 2) ----
    # 18 drill-down pages (4 bucket + 3 role + 11 sector), one HTML file per
    # cohort key. Reads from dashboard/data/performance_{type}.json. Filename
    # slugifies sector names with spaces (e.g. "Health Care" → "Health-Care").
    drill_n = _build_drill_pages(out_dir, build_sha=build_sha, verbose=verbose)
    summary["drill_pages"] = drill_n

    # ---- companies/{TICKER}.html — REMOVED (B-184 cutover) ----
    # The ~880 static per-ticker pages are no longer generated. They are
    # replaced by the single dynamic template `outputs/company.html`, which
    # reads `?ticker=` from the URL and fetches live data from Supabase
    # (`public_company_v`). All company links now point at
    # `company.html?ticker={TICKER}` (see render_helpers.company_url()).
    # `render_company.py` is left in place but is no longer wired into the
    # build. The stale `outputs/companies/` folder can be deleted once —
    # nothing regenerates it.
    summary["company_pages"] = 0
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stage 5 dashboard builder.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--signals-json", type=Path, default=DEFAULT_SIGNALS_JSON)
    parser.add_argument("--dealings-json", type=Path, default=DEFAULT_DEALINGS_JSON)
    parser.add_argument("--status-json", type=Path, default=DEFAULT_STATUS_JSON)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS_PATH)
    parser.add_argument("--pending-json", type=Path, default=DEFAULT_PENDING_PATH,
                        help="Path to _pending_review.json for per-ticker panels.")
    parser.add_argument("--rebuild", action="store_true",
                        help="B-184: delete the now-stale outputs/companies/ "
                             "folder (per-ticker pages are no longer built; the "
                             "dynamic company.html template replaces them).")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--build-sha", default=None)
    args = parser.parse_args(argv)

    # B-184: outputs/companies/ is no longer regenerated. --rebuild removes the
    # stale folder (old static pages incl. test-ticker junk) once and for all.
    if args.rebuild:
        comp_dir = args.out_dir / "companies"
        if comp_dir.exists():
            # B-184: best-effort delete. If a file/folder is locked (e.g. a
            # browser tab or Explorer window has a company page open), do NOT
            # crash the whole build — skip the locked items and continue. The
            # stale folder is harmless; nothing links to it anymore.
            shutil.rmtree(comp_dir, ignore_errors=True)
            if comp_dir.exists():
                print(f"[warn] could not fully remove {comp_dir} (locked?); "
                      f"safe to delete it manually later. Continuing build.")

    build_sha = args.build_sha or _detect_build_sha()
    summary = build(
        out_dir=args.out_dir,
        signals_path=args.signals_json,
        dealings_path=args.dealings_json,
        status_path=args.status_json if args.status_json.exists() else None,
        csv_path=args.csv,
        clusters_path=args.clusters,
        pending_path=args.pending_json if args.pending_json.exists() else None,
        build_sha=build_sha,
        verbose=args.verbose,
    )
    print(f"Built dashboard with build_sha={build_sha}.")
    print("  index.html, performance.html, baskets.html + drill pages.")
    print("  Company pages: served live by company.html?ticker= "
          "(no static pages built — B-184).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
