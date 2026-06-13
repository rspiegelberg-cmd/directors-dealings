"""Sprint 3 / B-001 + B-004 + B-016 + B-017 — corpus reparse runner.

Walks every cached HTML file in ``.scripts/_scrape_cache/``, re-runs
the table-aware parser, and reconciles each filing's extraction against
the existing rows in ``transactions``.

Reconciliation (Option A — Rupert decision 2026-05-18):

    For each row the new parser produces:

      1. Exact fingerprint match  -> unchanged (no DB write).
      2. Match on (date, ticker, type, shares) -> UPDATE in place.
         The existing row's director, company, role, price, value
         and fingerprint are rewritten. Metadata (seen_count,
         first_seen, last_seen, cluster_id, first_time_buy) is
         preserved. Each update is appended to
         ``.data/_reparse_director_fixes.log``.
      3. No match -> INSERT new row.

    Rows for the same URL that have no match in any of the new
    extractions are ORPHANED -- they're typically leftover wrong-data
    rows from the old regex parser (e.g. filing 9541612's
    ``shares=2024`` row where the year was picked up as the share
    count). The ``--delete-orphans`` flag (off by default) deletes
    them together with their referencing signals / paper_trades rows.

CLI:

    python .scripts/reparse_corpus.py --preview
    python .scripts/reparse_corpus.py --preview --limit 50    # sample
    python .scripts/reparse_corpus.py --confirm
    python .scripts/reparse_corpus.py --confirm --delete-orphans

FUSE rule (CLAUDE.md): this script writes ``.data/directors.db`` and
``.data/*.csv`` / ``.log``. Run from Windows PowerShell, never from
the Linux sandbox. The pre-sprint-3 backup at
``.data/directors.db.pre-sprint-3.bak`` must exist before ``--confirm``.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
from parse_pdmr import parse_announcement  # noqa: E402

ROOT = HERE.parent
DATA_DIR = ROOT / ".data"
CACHE_DIR = HERE / "_scrape_cache"
PREVIEW_CSV = DATA_DIR / "_reparse_corpus_preview.csv"
FIXES_LOG = DATA_DIR / "_reparse_director_fixes.log"
BACKUP_PATH = DATA_DIR / "directors.db.pre-sprint-3.bak"
# Sprint 9 Phase B (2026-05-25) — structured diff emission after reparse.
DIFF_REPORT_PATH = DATA_DIR / "_reparse_diff_sprint-09.json"

# Hard safety guard: refuse to proceed if --confirm would touch more
# rows than this fraction of the DB. 50 % is well above any plausible
# Sprint 3 reparse impact (typical is < 20 %) and well below "wiped
# the DB" territory. Override with --force-safety-override.
SAFETY_MAX_AFFECTED_FRACTION = 0.5


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# B-167 (2026-06-11) — recover url / announced_at from the cached HTML.
#
# Root cause of the 4,501 no-url rows: process_filing() derived the filing
# URL solely from existing DB rows ("url = existing[0]['url'] if existing
# else ''"). Any filing with no DB row yet (e.g. the ~74% of filings
# quarantined by the 2026-06-02 ingest-gate incident) parsed with url=""
# and announced_at="", so every row _apply_insert wrote carried an empty
# url and announced_at. The cached Investegate HTML embeds both values:
# an og:url <meta> tag (the canonical announcement URL) and a JSON-LD
# "dateCreated" timestamp (same source run_scrape._extract_announced_at
# uses). Fall back to those whenever the DB has nothing.

_OG_URL_RE = re.compile(
    r"property=['\"]og:url['\"]\s+content=['\"]([^'\"]+)['\"]"
)
_DATE_CREATED_RE = re.compile(
    r'"dateCreated"\s*:\s*"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"'
)


def _url_from_html(html: str) -> str:
    """Canonical announcement URL from the og:url meta tag ('' if absent)."""
    m = _OG_URL_RE.search(html)
    if not m:
        return ""
    url = m.group(1).strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def _normalize_role(raw_role) -> str:
    """Canonical role bucket — same path db.upsert_transaction uses.

    B-167: _apply_insert previously omitted role_normalized entirely
    (the separate role-backfill sweep papered over it). Populate it at
    insert time so reparse-inserted rows match the canonical ingest path.
    """
    from role_normalize import normalize_role
    return normalize_role(raw_role)


def _announced_at_from_html(html: str) -> str:
    """Investegate publish timestamp from the JSON-LD block ('' if absent)."""
    m = _DATE_CREATED_RE.search(html)
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# DB helpers

def _load_excluded_tickers(conn) -> set:
    """Tickers we must never (re-)ingest. Drawn from two sources:

    1. ``tickers_meta.is_excluded_issuer = 1`` — the live classifier flag.
    2. ``.data/_excluded_it_cef.csv`` — the append-mode audit log of
       every ticker ever deleted by Sprint 2's IT/CEF purge.

    The audit-log fallback is defensive: ``classify_issuers.py`` resets
    is_excluded_issuer to 0 at the start of every run and re-applies it
    based on the current dataset. If a previous classifier run flagged
    a ticker via name regex (Source C), and that ticker's transactions
    were subsequently deleted, a later classifier run will NOT re-flag
    it (no transactions left for the regex to match). Without this
    second source, the reparse would re-import the deleted rows from
    the cached HTML and undo the Sprint 2 cleanup.
    """
    excluded: set = set()
    for r in conn.execute(
        "SELECT ticker FROM tickers_meta WHERE is_excluded_issuer = 1"
    ).fetchall():
        excluded.add(r["ticker"])
    audit_path = DATA_DIR / "_excluded_it_cef.csv"
    if audit_path.exists():
        with audit_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = (row.get("ticker") or "").strip()
                if t:
                    excluded.add(t)
    return excluded


def _load_existing_by_url(conn) -> dict:
    """Return ``{rns_id: [row_dict, ...]}`` for every transactions row.

    The rns_id is the last path segment of the URL; this is the same
    key the cached HTML files are named by.
    """
    by_rns: dict = {}
    rows = conn.execute(
        "SELECT * FROM transactions WHERE url IS NOT NULL AND url <> ''"
    ).fetchall()
    for r in rows:
        url = r["url"]
        rns_id = url.rstrip("/").rsplit("/", 1)[-1]
        by_rns.setdefault(rns_id, []).append(dict(r))
    return by_rns


# ---------------------------------------------------------------------------
# Reconciliation

def reconcile(existing: list, new_rows: list, excluded_tickers: set) -> dict:
    """Compute per-filing reconciliation actions.

    Returns a dict with:
      ``actions``  : list of {"kind": ..., "new_row": dict, "existing_fp": str|None}
                     kind in {"unchanged", "update", "insert"}
      ``orphans``  : list of existing-row dicts that no new row matched.

    Rows whose ticker is in ``excluded_tickers`` are dropped from both
    new and existing perspectives -- we don't re-insert excluded
    issuers, and any leftover excluded-issuer rows are flagged for
    orphan deletion.
    """
    # Drop excluded tickers from the new extraction
    new_rows = [r for r in new_rows if r.get("ticker") not in excluded_tickers]

    existing_by_fp = {r["fingerprint"]: r for r in existing}
    matched_fps: set = set()
    actions: list = []

    for new in new_rows:
        new_fp = new["fingerprint"]

        # 1) Exact fingerprint match -> unchanged
        if new_fp in existing_by_fp:
            matched_fps.add(new_fp)
            actions.append({"kind": "unchanged", "new_row": new,
                            "existing_fp": new_fp})
            continue

        # 2) Option A match on (date, ticker, type, shares)
        match_fp = None
        for fp, ex in existing_by_fp.items():
            if fp in matched_fps:
                continue
            if (ex["date"] == new["date"]
                    and ex["ticker"] == new["ticker"]
                    and ex["type"] == new["type"]
                    and int(ex["shares"]) == int(new["shares"])):
                match_fp = fp
                break
        if match_fp:
            matched_fps.add(match_fp)
            actions.append({"kind": "update", "new_row": new,
                            "existing_fp": match_fp})
            continue

        # 3) No match -> insert
        actions.append({"kind": "insert", "new_row": new,
                        "existing_fp": None})

    orphans = [r for r in existing if r["fingerprint"] not in matched_fps]
    return {"actions": actions, "orphans": orphans}


# ---------------------------------------------------------------------------
# Per-filing processing

def process_filing(conn, rns_id: str, html: str,
                   existing_by_rns: dict, excluded_tickers: set) -> dict:
    """Parse one cached filing and reconcile against existing rows.

    Returns the dict from ``reconcile`` plus a few diagnostics.
    """
    existing = existing_by_rns.get(rns_id, [])
    url = existing[0]["url"] if existing else ""
    announced_at = existing[0]["announced_at"] if existing else ""
    # B-167: when the DB has no row for this filing (or blank fields),
    # recover both values from the cached HTML itself so inserted rows
    # never carry an empty url / announced_at again.
    if not url:
        url = _url_from_html(html)
    if not announced_at:
        announced_at = _announced_at_from_html(html)

    new_rows, warnings, _src = parse_announcement(
        html, url, rns_id, announced_at,
    )

    result = reconcile(existing, new_rows, excluded_tickers)
    result["rns_id"] = rns_id
    result["url"] = url
    result["warnings"] = warnings
    return result


# ---------------------------------------------------------------------------
# Preview writer

def _write_preview(per_filing: list, totals: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PREVIEW_CSV.with_suffix(".csv.tmp")
    fields = [
        "rns_id", "ticker", "n_unchanged", "n_update", "n_insert",
        "n_orphan", "sample_diff",
    ]
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        # SUMMARY row at top
        writer.writerow({
            "rns_id": "SUMMARY",
            "ticker": (
                f"filings_touched={totals['filings_touched']}  "
                f"unchanged={totals['unchanged']}  "
                f"director_fixes={totals['update']}  "
                f"inserts={totals['insert']}  "
                f"orphans={totals['orphan']}"
            ),
            "n_unchanged": totals["unchanged"],
            "n_update": totals["update"],
            "n_insert": totals["insert"],
            "n_orphan": totals["orphan"],
            "sample_diff": "",
        })
        for p in per_filing:
            counts = {"unchanged": 0, "update": 0, "insert": 0}
            sample = ""
            for a in p["actions"]:
                counts[a["kind"]] += 1
                if not sample and a["kind"] == "update":
                    nr = a["new_row"]
                    sample = (
                        f"old fp {a['existing_fp']} dir->{nr['director']!r}"
                    )
                elif not sample and a["kind"] == "insert":
                    nr = a["new_row"]
                    sample = (
                        f"new row {nr['date']} {nr['ticker']} "
                        f"{nr['director']!r} shares={nr['shares']}"
                    )
            if not sample and p["orphans"]:
                o = p["orphans"][0]
                sample = (
                    f"orphan fp {o['fingerprint']} "
                    f"{o['date']} {o['ticker']} {o['director']!r} "
                    f"shares={o['shares']}"
                )
            ticker = ""
            if p["actions"]:
                ticker = p["actions"][0]["new_row"].get("ticker", "") or ""
            elif p["orphans"]:
                ticker = p["orphans"][0]["ticker"]
            if any(v > 0 for v in counts.values()) or p["orphans"]:
                writer.writerow({
                    "rns_id": p["rns_id"],
                    "ticker": ticker,
                    "n_unchanged": counts["unchanged"],
                    "n_update": counts["update"],
                    "n_insert": counts["insert"],
                    "n_orphan": len(p["orphans"]),
                    "sample_diff": sample,
                })
    tmp.replace(PREVIEW_CSV)
    return PREVIEW_CSV


# ---------------------------------------------------------------------------
# Apply (--confirm)

def _apply_update(conn, existing_fp: str, new_row: dict) -> None:
    """UPDATE the existing row's content + fingerprint in place.

    Preserves seen_count, first_seen, cluster_id, first_time_buy.
    Cascades signals / paper_trades to the new fingerprint by
    delete-and-let-eval-signals-re-fire. (Simpler than chasing a PK
    update across two referencing tables in SQLite.)
    """
    new_fp = new_row["fingerprint"]
    now = _iso_now()
    # 1) Strip referencing rows for the old fingerprint. eval_signals
    #    will re-fire any signals that should attach to the new row.
    conn.execute("DELETE FROM signals WHERE fingerprint = ?", (existing_fp,))
    conn.execute("DELETE FROM paper_trades WHERE fingerprint = ?",
                 (existing_fp,))
    # 2) If the target fingerprint already exists in the DB, the correct
    #    version of this row is already present (e.g. an earlier insert in
    #    this run, or a pre-existing clean row). Just drop the stale old row.
    already_exists = conn.execute(
        "SELECT 1 FROM transactions WHERE fingerprint = ?", (new_fp,)
    ).fetchone()
    if already_exists:
        conn.execute(
            "DELETE FROM transactions WHERE fingerprint = ?", (existing_fp,)
        )
        return
    # 3) Normal path: rewrite the transactions row in place.
    conn.execute(
        "UPDATE transactions SET "
        "  fingerprint = ?, last_seen = ?, "
        "  date = ?, ticker = ?, company = ?, director = ?, role = ?, "
        "  type = ?, shares = ?, price = ?, value = ?, "
        # B-167: NULLIF treats the legacy ''-blanks like NULL so a reparse
        # heals empty urls / timestamps; never overwrites a real value.
        "  url = COALESCE(NULLIF(url, ''), ?), "
        "  announced_at = COALESCE(NULLIF(announced_at, ''), ?), "
        # B-156: fill resulting_shares when the reparse extracted it; never
        # overwrite an existing non-NULL value.
        "  resulting_shares = COALESCE(resulting_shares, ?) "
        "WHERE fingerprint = ?",
        (
            new_fp, now,
            new_row["date"], new_row["ticker"], new_row["company"],
            new_row["director"], new_row.get("role"),
            new_row["type"], int(new_row["shares"]),
            float(new_row.get("price") or 0.0),
            float(new_row.get("value") or 0.0),
            new_row.get("url"), new_row.get("announced_at"),
            new_row.get("resulting_shares"),
            existing_fp,
        ),
    )


def _apply_insert(conn, new_row: dict) -> None:
    now = _iso_now()
    conn.execute(
        "INSERT OR IGNORE INTO transactions ("
        "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        "company, director, role, role_normalized, type, shares, price, "
        "value, context, "
        "url, announced_at, cluster_id, first_time_buy, parser_source, "
        "buy_strictness, "  # B-167 — was silently dropped before
        "resulting_shares"  # B-156
        ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_row["fingerprint"], now, now,
            new_row["date"], new_row["ticker"], new_row["company"],
            new_row["director"], new_row.get("role"),
            _normalize_role(new_row.get("role")),  # B-167
            new_row["type"], int(new_row["shares"]),
            float(new_row.get("price") or 0.0),
            float(new_row.get("value") or 0.0),
            new_row.get("context"),
            new_row.get("url"), new_row.get("announced_at"),
            None, 0, "regex",
            new_row.get("buy_strictness"),
            new_row.get("resulting_shares"),
        ),
    )


def _apply_delete_orphan(conn, orphan_fp: str) -> None:
    conn.execute("DELETE FROM signals WHERE fingerprint = ?", (orphan_fp,))
    conn.execute("DELETE FROM paper_trades WHERE fingerprint = ?",
                 (orphan_fp,))
    conn.execute("DELETE FROM transactions WHERE fingerprint = ?",
                 (orphan_fp,))


def _append_fix_log(rns_id: str, existing_fp: str, new_row: dict) -> None:
    FIXES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FIXES_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": _iso_now(),
            "rns_id": rns_id,
            "old_fingerprint": existing_fp,
            "new_fingerprint": new_row["fingerprint"],
            "date": new_row["date"],
            "ticker": new_row["ticker"],
            "director": new_row["director"],
            "company": new_row["company"],
        }) + "\n")


# ---------------------------------------------------------------------------
# Sprint 9 Phase B — structured diff report
#
# Captures the impact of the Phase B parser fixes + plausibility-gate
# flip in a JSON file Rupert can read at Gate 3. Emitted by both the
# preview path (mode='preview', projected counts; no DB delta) and the
# apply path (mode='applied', actual before/after counts). Read-only on
# the DB — safe to fail open.


def _query_value_distribution(conn) -> dict:
    """Return transaction-value band counts from the live DB.

    Bands: < £1k, £1k..£1m, > £1m. The QA audit showed ~38% of rows
    pre-fix had value < £1k (mostly silent price-extraction misreads);
    that figure should drop materially after Phase B closes Class 1.
    """
    lt_1k = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE value < 1000"
    ).fetchone()[0]
    mid = conn.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE value >= 1000 AND value <= 1000000"
    ).fetchone()[0]
    gt_1m = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE value > 1000000"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0]
    return {
        "lt_1k": lt_1k,
        "between_1k_and_1m": mid,
        "gt_1m": gt_1m,
        "total": total,
    }


def _collect_warning_counts(per_filing: list) -> dict:
    """Count Phase-B-introduced warnings across all per-filing results.

    The parser emits these strings into the per-filing `warnings` list
    when the gate or the D.3 drop fires:
      - "plausibility_rejected:R1_sub_pound_value,R3_price_too_high"
      - "plausibility_flagged:R5_date_component_in_shares"
      - "zero_price_non_grant"
    Returns: by-rule counts + zero-price-drop count + rejected/flagged
    totals.
    """
    rule_counts: dict = {
        "R1_sub_pound_value": 0,
        "R2_tiny_shares_low_price": 0,
        "R3_price_too_high": 0,
        "R4_excessive_value": 0,
        "R5_date_component_in_shares": 0,
    }
    zero_price_drops = 0
    rejected_total = 0
    flagged_total = 0
    for p in per_filing:
        warnings = p.get("warnings") or []
        for w in warnings:
            if w == "zero_price_non_grant":
                zero_price_drops += 1
                continue
            if w.startswith("plausibility_rejected:"):
                rejected_total += 1
                reasons = w.split(":", 1)[1].split(",")
                for r in reasons:
                    if r in rule_counts:
                        rule_counts[r] += 1
                continue
            if w.startswith("plausibility_flagged:"):
                flagged_total += 1
                reasons = w.split(":", 1)[1].split(",")
                for r in reasons:
                    if r in rule_counts:
                        rule_counts[r] += 1
    return {
        "by_rule": rule_counts,
        "zero_price_drops": zero_price_drops,
        "rejected_total": rejected_total,
        "flagged_total": flagged_total,
    }


def _sample_orphans(per_filing: list, n: int = 10) -> list:
    """Sample up to n orphan rows for the diff report."""
    samples: list = []
    for p in per_filing:
        for o in (p.get("orphans") or []):
            samples.append({
                "rns_id": p.get("rns_id"),
                "fingerprint": o.get("fingerprint"),
                "date": o.get("date"),
                "ticker": o.get("ticker"),
                "director": o.get("director"),
                "type": o.get("type"),
                "shares": o.get("shares"),
                "price": o.get("price"),
                "value": o.get("value"),
            })
            if len(samples) >= n:
                return samples
    return samples


def _sample_actions(per_filing: list, kind: str, n: int = 10) -> list:
    """Sample up to n actions of a given kind (insert / update)."""
    samples: list = []
    for p in per_filing:
        for a in (p.get("actions") or []):
            if a.get("kind") != kind:
                continue
            nr = a["new_row"]
            samples.append({
                "rns_id": p.get("rns_id"),
                "fingerprint": nr.get("fingerprint"),
                "existing_fp": a.get("existing_fp"),
                "date": nr.get("date"),
                "ticker": nr.get("ticker"),
                "director": nr.get("director"),
                "type": nr.get("type"),
                "shares": nr.get("shares"),
                "price": nr.get("price"),
                "value": nr.get("value"),
            })
            if len(samples) >= n:
                return samples
    return samples


def _write_diff_report(
    mode: str,
    per_filing: list,
    totals: dict,
    tx_total_before: int,
    tx_total_after: int,
    value_dist_before: dict,
    value_dist_after: dict | None,
    delete_orphans: bool,
) -> Path:
    """Emit `.data/_reparse_diff_sprint-09.json`.

    `mode` is 'preview' or 'applied'. In preview mode `value_dist_after`
    is None and the inserted/updated counts are projected (what WOULD
    happen with --confirm). In applied mode both reflect the post-commit
    state.
    """
    warning_counts = _collect_warning_counts(per_filing)
    report = {
        "sprint": 9,
        "phase": "B",
        "mode": mode,
        "generated_at": _iso_now(),
        "summary": {
            "transactions_before": tx_total_before,
            "transactions_after": (
                tx_total_after if mode == "applied" else None
            ),
            "filings_touched": totals["filings_touched"],
            "rows_unchanged": totals["unchanged"],
            "rows_updated_in_place": totals["update"],
            "rows_inserted": totals["insert"],
            "orphans_seen": totals["orphan"],
            "orphans_deleted": (
                totals["orphan"]
                if (mode == "applied" and delete_orphans)
                else 0
            ),
        },
        "phase_b_warnings": warning_counts,
        "value_distribution_shift": {
            "before": value_dist_before,
            "after": value_dist_after,
        },
        "samples": {
            "orphans": _sample_orphans(per_filing, n=10),
            "inserts": _sample_actions(per_filing, "insert", n=10),
            "updates": _sample_actions(per_filing, "update", n=10),
        },
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DIFF_REPORT_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(DIFF_REPORT_PATH)
    return DIFF_REPORT_PATH


# ---------------------------------------------------------------------------
# Main

def run(args) -> int:
    if args.confirm and not BACKUP_PATH.exists() and not args.skip_backup_check:
        raise SystemExit(
            f"Refusing to --confirm: pre-flight backup not found at "
            f"{BACKUP_PATH}. Take it from PowerShell with "
            f'python -c "import shutil; shutil.copyfile(...)" before '
            f"running this script. Pass --skip-backup-check to override "
            f"(NOT recommended)."
        )

    cache_files = sorted(CACHE_DIR.glob("*.html"))
    if not cache_files:
        raise SystemExit(f"No cached HTML files at {CACHE_DIR}.")
    if getattr(args, "only_rns", ""):
        # Surgical scoping: restrict to an explicit set of rns_ids. Accept
        # either a path to a one-per-line file or a comma-separated list.
        spec = args.only_rns
        wanted: set = set()
        p = Path(spec)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    wanted.add(line)
        else:
            wanted = {s.strip() for s in spec.split(",") if s.strip()}
        if not wanted:
            raise SystemExit(f"--only-rns given but no rns_ids parsed from {spec!r}.")
        cache_files = [f for f in cache_files if f.stem in wanted]
        missing = sorted(wanted - {f.stem for f in cache_files})
        print(f"--only-rns: scoping to {len(wanted)} rns_id(s); "
              f"{len(cache_files)} cached file(s) found"
              + (f"; {len(missing)} have NO cache (skipped): "
                 f"{', '.join(missing[:10])}" if missing else ""))
        if not cache_files:
            raise SystemExit("None of the requested rns_ids have cached HTML.")
    if args.limit:
        cache_files = cache_files[:args.limit]

    conn = db.connect()
    try:
        excluded_tickers = _load_excluded_tickers(conn)
        existing_by_rns = _load_existing_by_url(conn)
        tx_total_before = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        # Sprint 9 Phase B — capture value distribution before reparse
        # so the diff report can show how much Phase B shifted the
        # < £1k / £1k-£1m / > £1m bands.
        value_dist_before = _query_value_distribution(conn)

        per_filing: list = []
        totals = {"filings_touched": 0, "unchanged": 0,
                  "update": 0, "insert": 0, "orphan": 0}

        for i, html_path in enumerate(cache_files, 1):
            rns_id = html_path.stem
            try:
                html = html_path.read_text(encoding="utf-8",
                                           errors="replace")
            except OSError as e:
                print(f"WARN: skip {html_path.name}: {e}", file=sys.stderr)
                continue
            result = process_filing(
                conn, rns_id, html, existing_by_rns, excluded_tickers,
            )

            counts = {"unchanged": 0, "update": 0, "insert": 0}
            for a in result["actions"]:
                counts[a["kind"]] += 1
            if (counts["update"] + counts["insert"]
                    + len(result["orphans"])) > 0:
                totals["filings_touched"] += 1
            for k in ("unchanged", "update", "insert"):
                totals[k] += counts[k]
            totals["orphan"] += len(result["orphans"])

            per_filing.append(result)

            if args.verbose and (counts["update"] + counts["insert"]
                                 + len(result["orphans"])) > 0:
                print(
                    f"  {i:5d}/{len(cache_files)} {rns_id}: "
                    f"unchanged={counts['unchanged']} "
                    f"update={counts['update']} "
                    f"insert={counts['insert']} "
                    f"orphan={len(result['orphans'])}"
                )

        # Safety guard
        affected = totals["update"] + totals["insert"]
        if args.delete_orphans:
            affected += totals["orphan"]
        affected_fraction = (affected / tx_total_before
                             if tx_total_before else 0.0)
        if (affected_fraction > SAFETY_MAX_AFFECTED_FRACTION
                and not args.force_safety_override):
            raise SystemExit(
                f"Refusing: reparse would touch "
                f"{affected_fraction * 100:.1f}% of {tx_total_before} "
                f"existing rows (> {SAFETY_MAX_AFFECTED_FRACTION * 100:.0f}% "
                f"safety limit). Pass --force-safety-override if you've "
                f"reviewed the preview and accept the impact."
            )

        # --- Output ---
        if args.preview or not args.confirm:
            path = _write_preview(per_filing, totals)
            # Sprint 9 Phase B — preview-mode diff report.
            diff_path = _write_diff_report(
                mode="preview",
                per_filing=per_filing,
                totals=totals,
                tx_total_before=tx_total_before,
                tx_total_after=tx_total_before,  # no DB write in preview
                value_dist_before=value_dist_before,
                value_dist_after=None,
                delete_orphans=args.delete_orphans,
            )
            print()
            print("PREVIEW written (NO DB writes).")
            print(f"  path:             {path}")
            print(f"  diff report:      {diff_path}")
            print(f"  cache files:      {len(cache_files)}")
            print(f"  filings touched:  {totals['filings_touched']}")
            print(f"  unchanged rows:   {totals['unchanged']}")
            print(f"  director fixes:   {totals['update']}")
            print(f"  new inserts:      {totals['insert']}")
            print(f"  orphan candidates: {totals['orphan']} "
                  f"(would delete with --delete-orphans)")
            print()
            print("Next steps:")
            print("  1. Eyeball the preview CSV.")
            print(
                "  2. Inspect Phase B impact: "
                "phase_b_warnings + value_distribution_shift "
                "in the diff report JSON."
            )
            print("  3. If happy, run with --confirm (and optionally "
                  "--delete-orphans).")
            print("  4. Pipeline rebuild: python .scripts/eval_signals.py "
                  "&& python .scripts/build_dashboard.py")
            return 0

        # --- Apply ---
        print(f"Authorised. Applying {totals['update']} updates + "
              f"{totals['insert']} inserts"
              + (f" + {totals['orphan']} orphan deletes"
                 if args.delete_orphans else "")
              + " ...")

        conn.execute("BEGIN")
        try:
            for p in per_filing:
                for a in p["actions"]:
                    if a["kind"] == "update":
                        _apply_update(conn, a["existing_fp"], a["new_row"])
                        _append_fix_log(p["rns_id"], a["existing_fp"],
                                        a["new_row"])
                    elif a["kind"] == "insert":
                        _apply_insert(conn, a["new_row"])
                if args.delete_orphans:
                    for o in p["orphans"]:
                        _apply_delete_orphan(conn, o["fingerprint"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        tx_total_after = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        # Sprint 9 Phase B — applied-mode diff report (post-commit
        # value distribution + final summary).
        value_dist_after = _query_value_distribution(conn)
        diff_path = _write_diff_report(
            mode="applied",
            per_filing=per_filing,
            totals=totals,
            tx_total_before=tx_total_before,
            tx_total_after=tx_total_after,
            value_dist_before=value_dist_before,
            value_dist_after=value_dist_after,
            delete_orphans=args.delete_orphans,
        )
        print()
        print("DONE.")
        print(f"  transactions before: {tx_total_before}")
        print(f"  transactions after:  {tx_total_after}")
        print(f"  director fixes log:  {FIXES_LOG}")
        print(f"  diff report:         {diff_path}")
        print()
        print("Next steps (run from PowerShell):")
        print("    python .scripts/eval_signals.py")
        print("    python .scripts/backtest.py")
        print("    python .scripts/build_dashboard.py")
        print("    python .scripts/audit_dates.py")
        # B-024: best-effort bak refresh after a successful corpus reparse.
        # Same rationale as exclude_investment_trusts.py — ad-hoc scripts
        # historically left .bak stale.
        try:
            import db_health
            db_health.seal()
        except Exception as e:
            print(f"[db_health] post-script seal failed (non-fatal): {e}")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Re-parse the cached corpus and reconcile DB rows."
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true",
                   help="Write the preview CSV; no DB writes (default).")
    g.add_argument("--confirm", action="store_true",
                   help="Apply inserts + Option A updates to the DB.")
    ap.add_argument("--delete-orphans", action="store_true",
                    help="With --confirm, also delete existing rows that "
                         "no new extraction matched (typically old "
                         "wrong-shares rows fixed by the new parser).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N cached files (for "
                         "testing).")
    ap.add_argument("--only-rns", default="",
                    help="Scope the reparse to a specific set of rns_ids "
                         "(the cached-HTML stems). Accepts a comma-separated "
                         "list OR a path to a text file with one rns_id per "
                         "line (blank lines and '#' comments ignored). Use "
                         "this for surgical, audit-scoped fixes so the run "
                         "stays well under the safety limit instead of "
                         "reparsing the whole corpus.")
    ap.add_argument("--skip-backup-check", action="store_true",
                    help="Skip the pre-flight backup existence check. "
                         "NOT recommended.")
    ap.add_argument("--force-safety-override", action="store_true",
                    help="Allow > 50%% row-impact runs. Only after you've "
                         "reviewed the preview.")
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm:
        args.preview = True
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
