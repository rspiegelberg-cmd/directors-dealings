"""Sprint 25 Phase 2 -- apply staged PDMR edits to directors.db.

ZONE B SCRIPT -- run from Windows PowerShell only.
NEVER run from Claude's Linux bash sandbox (FUSE = corruption risk).

Usage::

    python .scripts/apply_edits.py [--dry-run] [--no-pipeline] [--verbose]

Reads ``.data/_edit_queue.json``, validates every queued edit, applies
them all to ``directors.db`` in ONE database transaction (all-or-nothing),
writes an append-only audit trail to ``.data/_edit_audit.jsonl``, clears
the queue, takes a DB backup, then optionally runs the signal/export/build
pipeline so the dashboard reflects the changes immediately.

Three edit actions:

    update  -- correct fields on an existing parsed transaction.
               Non-key fields (role, company, price, value, sector):
               plain UPDATE.
               Key fields (date, ticker, director, type, shares) change
               the fingerprint: DELETE old tx + signals, INSERT new tx.

    add     -- manually key in a transaction from a failed filing.
               Inserts with parser_source='manual' and a sticky
               protection flag so future reparses skip the RNS ID.

    reject  -- mark a pending filing as junk. Deletes any already-
               ingested transactions that came from the same RNS URL.
               Records the RNS ID in ``.data/_rejected_rns_ids.json``
               so future scrape/sweep passes skip it permanently.

Pipeline (unless --no-pipeline)::

    eval_signals.py -> backtest.py -> export_dashboard_json.py -> build_dashboard.py

This is the same subset refresh_all.py uses for post-signal updates.
The upstream steps (detect_clusters, classify_issuers, fetch_sectors) are
NOT run because a manual edit doesn't change ticker membership or sector
classification. If the edit changes a ticker to a new/unknown one, run
refresh_all.py afterwards instead.

Audit trail::

    .data/_edit_audit.jsonl -- one JSON line per applied edit:
    {"ts": "<iso>", "action": "...", "edit_id": "...",
     "target_fingerprint": "...", "before": {...}, "after": {...}}

Undo::

    Stage the inverse edit (before -> after swapped) via the /review
    interface, then re-run apply_edits.py. The ``.bak`` backup is the
    safety net for catastrophic rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import db_health  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EDIT_QUEUE_PATH     = ROOT / ".data" / "_edit_queue.json"
EDIT_AUDIT_PATH     = ROOT / ".data" / "_edit_audit.jsonl"
REJECTED_IDS_PATH   = ROOT / ".data" / "_rejected_rns_ids.json"
MANUAL_IDS_PATH     = ROOT / ".data" / "_manual_rns_ids.json"   # Phase 3: add-action sticky
APPLY_STATUS_PATH   = ROOT / ".data" / "_apply_status.json"

# ---------------------------------------------------------------------------
# Constants mirroring server.py validation
# ---------------------------------------------------------------------------

VALID_TX_TYPES = {"BUY", "SELL", "SELL_TAX", "EXERCISE", "GRANT", "SIP"}
VALID_ACTIONS  = {"update", "add", "reject", "delete"}  # delete added Phase 4 (undo of adds)
KNOWN_SECTORS  = {
    "Financials", "Energy", "Health Care", "Industrials",
    "Consumer Discretionary", "Consumer Staples", "Materials",
    "Technology", "Utilities", "Communication Services", "Real Estate",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FP_RE   = re.compile(r"^[0-9a-f]{8,32}$")
_RNS_RE  = re.compile(r"^\d{5,12}$")

# Key fields -- changing any of these changes the fingerprint.
FINGERPRINT_FIELDS = {"date", "ticker", "director", "type", "shares"}

# Pipeline executed after a successful apply (unless --no-pipeline).
PIPELINE_STEPS = [
    ("eval_signals",         "eval_signals.py",          []),
    ("backtest",             "backtest.py",               []),
    ("export_dashboard_json","export_dashboard_json.py",  []),
    ("build_dashboard",      "build_dashboard.py",        []),
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_status(status: str, step: str = "", step_label: str = "",
                  queue_size: int = 0, error: str | None = None,
                  completed: list | None = None) -> None:
    """Write apply progress to _apply_status.json for the Flask UI to poll."""
    try:
        db.atomic_write_json(APPLY_STATUS_PATH, {
            "status":      status,       # idle | running | done | error
            "step":        step,
            "step_label":  step_label,
            "queue_size":  queue_size,
            "started_at":  _iso_now(),
            "error":       error,
            "completed":   completed or [],
        })
    except Exception:
        pass  # status writes are best-effort; never abort the real work


def _make_fingerprint(date: str, ticker: str, director: str,
                      tx_type: str, shares: int) -> str:
    """SHA-1 of 'date|ticker|director|type|shares', first 16 hex chars.
    Matches parse_pdmr._fingerprint -- keep in sync.
    """
    raw = f"{date}|{ticker}|{director}|{tx_type}|{shares}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(zip(row.keys(), tuple(row)))


# ---------------------------------------------------------------------------
# Queue + audit I/O
# ---------------------------------------------------------------------------

def read_queue() -> list[dict]:
    """Return the list of staged edits (empty list if queue missing/empty)."""
    if not EDIT_QUEUE_PATH.exists():
        return []
    try:
        q = json.loads(EDIT_QUEUE_PATH.read_text(encoding="utf-8"))
        return q.get("edits") or []
    except Exception:
        return []


def clear_queue() -> None:
    """Reset the queue to empty (version intact)."""
    db.atomic_write_json(EDIT_QUEUE_PATH, {"version": 1, "edits": []})


def append_audit(entry: dict) -> None:
    """Append one line to the JSONL audit trail (never rewrite the whole file).

    JSONL = one JSON object per line. Never read-modify-write the whole file.
    Uses 'a' append mode so a crash mid-write leaves at most one partial line.
    """
    EDIT_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EDIT_AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_rejected_ids() -> set[str]:
    """Return the set of RNS IDs already marked as rejected."""
    if not REJECTED_IDS_PATH.exists():
        return set()
    try:
        data = json.loads(REJECTED_IDS_PATH.read_text(encoding="utf-8"))
        return set(data.get("rejected_rns_ids") or [])
    except Exception:
        return set()


def add_rejected_id(rns_id: str) -> None:
    """Add an RNS ID to the persistent rejected-IDs list (atomic write)."""
    ids = read_rejected_ids()
    ids.add(rns_id)
    db.atomic_write_json(
        REJECTED_IDS_PATH,
        {"rejected_rns_ids": sorted(ids), "updated_at": _iso_now()},
    )


def read_manual_ids() -> set[str]:
    """Return the set of RNS IDs that have been manually added via apply_edits."""
    if not MANUAL_IDS_PATH.exists():
        return set()
    try:
        data = json.loads(MANUAL_IDS_PATH.read_text(encoding="utf-8"))
        return set(data.get("manual_rns_ids") or [])
    except Exception:
        return set()


def add_manual_id(rns_id: str) -> None:
    """Record an RNS ID as manually resolved via an 'add' action.

    Phase 3 sticky protection: run_pending_sweep.py and other ingest paths
    check this list before re-processing a pending entry, so a manually-added
    transaction is never overwritten by a future LLM sweep.
    """
    ids = read_manual_ids()
    ids.add(rns_id)
    db.atomic_write_json(
        MANUAL_IDS_PATH,
        {"manual_rns_ids": sorted(ids), "updated_at": _iso_now()},
    )


def all_resolved_ids() -> set[str]:
    """Return the union of rejected and manually-added RNS IDs.

    Used by run_pending_sweep.py and other ingest scripts to skip
    any RNS that has already been handled by apply_edits.py.
    """
    return read_rejected_ids() | read_manual_ids()


PENDING_REVIEW_PATH = HERE / "_pending_review.json"


def remove_from_pending_queue(rns_ids: list[str]) -> None:
    """Remove resolved RNS IDs from .scripts/_pending_review.json.

    Called after a successful apply for 'add' and 'reject' actions so the
    review UI (Tab A) no longer shows processed filings after the next
    export_dashboard_json run (which is part of the pipeline).

    Zone B write — runs from Windows Python (apply_edits.py). Atomic:
    uses db.atomic_write_json (tmp + os.replace).
    """
    if not rns_ids or not PENDING_REVIEW_PATH.exists():
        return
    try:
        data = json.loads(PENDING_REVIEW_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[apply_edits] WARNING: could not read _pending_review.json: {exc}")
        return

    items = data.get("items")
    if not isinstance(items, dict):
        return

    # B-136: rns_ids can contain duplicates — several staged 'add' edits often
    # share one RNS (e.g. a multi-director filing keyed in row by row). Dedupe
    # before deleting, and use pop(default) so a repeated id can never raise
    # KeyError and abort the post-commit step.
    seen: set[str] = set()
    removed: list[str] = []
    for r in rns_ids:
        if r in items and r not in seen:
            seen.add(r)
            removed.append(r)
    if not removed:
        return

    for r in removed:
        items.pop(r, None)

    data["items"] = items
    data["count"] = len(items)
    data["generated_at"] = _iso_now()

    try:
        db.atomic_write_json(PENDING_REVIEW_PATH, data, ensure_ascii=False)
        print(f"[apply_edits] Removed {len(removed)} resolved item(s) from pending queue "
              f"({', '.join(removed[:5])}{'…' if len(removed) > 5 else ''})")
    except Exception as exc:
        print(f"[apply_edits] WARNING: could not update _pending_review.json: {exc}"
              " — item(s) will reappear in Tab A until next scrape.")


# ---------------------------------------------------------------------------
# Field validation (mirrors server.py _validate_fields)
# ---------------------------------------------------------------------------

def validate_edit(edit: dict) -> list[str]:
    """Return a list of error strings. Empty list = valid."""
    errors: list[str] = []
    action = edit.get("action", "")

    if action not in VALID_ACTIONS:
        return [f"Unknown action {action!r}"]

    if action == "reject":
        if not _RNS_RE.match(str(edit.get("target_rns_id") or "")):
            errors.append("reject requires a valid target_rns_id (5-12 digits)")
        return errors

    if action in ("update", "delete"):
        if not _FP_RE.match(str(edit.get("target_fingerprint") or "")):
            errors.append(f"{action} requires a valid target_fingerprint (8-32 hex chars)")

    if action == "add":
        if not _RNS_RE.match(str(edit.get("target_rns_id") or "")):
            errors.append("add requires a valid target_rns_id (5-12 digits)")

    fields = edit.get("fields") or {}
    if action == "update" and not fields:
        errors.append("update: fields must not be empty")

    if action == "add":
        for req in ("date", "ticker", "director", "type", "shares"):
            if not fields.get(req) and fields.get(req) != 0:
                errors.append(f"add: '{req}' is required")

    t = fields.get("type")
    if t is not None and t not in VALID_TX_TYPES:
        errors.append(f"type must be one of {sorted(VALID_TX_TYPES)}, got {t!r}")

    for nf in ("shares", "price", "value"):
        v = fields.get(nf)
        if v is not None:
            try:
                fv = float(v)
                if fv < 0:
                    errors.append(f"{nf} must be >= 0, got {fv}")
                if nf == "shares" and fv != int(fv):
                    errors.append(f"shares must be a whole number, got {fv}")
            except (TypeError, ValueError):
                errors.append(f"{nf} must be numeric, got {v!r}")

    d = fields.get("date")
    if d is not None and not _DATE_RE.match(str(d)):
        errors.append(f"date must be YYYY-MM-DD, got {d!r}")

    return errors


# ---------------------------------------------------------------------------
# Apply individual edits (inside the outer transaction)
# ---------------------------------------------------------------------------

def apply_update(conn, edit: dict, verbose: bool) -> tuple[dict, dict]:
    """Apply an 'update' edit. Returns (before, after) dicts.

    If only non-key fields changed: plain UPDATE.
    If any key field changed: DELETE old tx+signals, INSERT new tx.
    """
    fp_old = edit["target_fingerprint"]
    fields = edit.get("fields") or {}

    # Fetch current row
    row = conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?", (fp_old,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Fingerprint {fp_old!r} not found in transactions")

    before = _row_to_dict(row)

    # Merge changes onto current values
    after_vals = dict(before)
    for k, v in fields.items():
        if k == "shares":
            after_vals[k] = int(float(v))
        elif k in ("price", "value"):
            after_vals[k] = float(v)
        else:
            after_vals[k] = v

    # Handle sector separately (lives in tickers_meta, not transactions)
    sector_change = fields.get("sector")
    if sector_change and after_vals.get("ticker"):
        conn.execute(
            "UPDATE tickers_meta SET sector = ? WHERE ticker = ?",
            (sector_change, after_vals["ticker"]),
        )
        if verbose:
            print(f"  sector updated: {after_vals['ticker']} -> {sector_change!r}")

    # Determine if fingerprint changes
    changed_key = FINGERPRINT_FIELDS.intersection(fields.keys())
    if changed_key:
        # Fingerprint-changing edit: delete-old + insert-new
        fp_new = _make_fingerprint(
            str(after_vals["date"]),
            str(after_vals["ticker"]),
            str(after_vals["director"]),
            str(after_vals["type"]),
            int(after_vals["shares"]),
        )
        if verbose:
            print(f"  fingerprint change: {fp_old} -> {fp_new}")

        # Check for collision
        conflict = conn.execute(
            "SELECT fingerprint FROM transactions WHERE fingerprint = ?", (fp_new,)
        ).fetchone()
        if conflict:
            raise ValueError(
                f"New fingerprint {fp_new!r} already exists in transactions — "
                "cannot apply this edit without a conflict. "
                "Review the target row manually."
            )

        # Cascade: delete signals referencing the old fingerprint
        n_signals = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE fingerprint = ?", (fp_old,)
        ).fetchone()[0]
        conn.execute("DELETE FROM signals WHERE fingerprint = ?", (fp_old,))
        if verbose:
            print(f"  deleted {n_signals} signal row(s) for old fingerprint")

        # B-135: paper_trades also FK to transactions(fingerprint) with no
        # cascade. Clear them before the parent delete or the FK aborts the
        # whole batch.
        conn.execute("DELETE FROM paper_trades WHERE fingerprint = ?", (fp_old,))

        # Delete old transaction
        conn.execute("DELETE FROM transactions WHERE fingerprint = ?", (fp_old,))

        # Role normalisation for new row
        from role_normalize import normalize_role  # noqa: PLC0415
        role_normalized = normalize_role(after_vals.get("role"))

        now = _iso_now()
        conn.execute(
            "INSERT INTO transactions ("
            "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
            "company, director, role, role_normalized, type, shares, price, "
            "value, context, url, announced_at, cluster_id, first_time_buy, "
            "parser_source, buy_strictness"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fp_new,
                before.get("first_seen", now),
                now,
                before.get("seen_count", 1),
                str(after_vals["date"]),
                str(after_vals["ticker"]),
                str(after_vals.get("company") or ""),
                str(after_vals["director"]),
                str(after_vals.get("role") or ""),
                role_normalized,
                str(after_vals["type"]),
                int(after_vals["shares"]),
                float(after_vals.get("price") or 0.0),
                float(after_vals.get("value") or 0.0),
                before.get("context"),
                before.get("url"),
                before.get("announced_at"),
                None,   # cluster_id -- recomputed by eval_signals
                0,      # first_time_buy -- recomputed
                "manual",  # sticky: parser_source='manual' survives reparses
                before.get("buy_strictness"),
            ),
        )
        after_vals["fingerprint"] = fp_new
        after_vals["parser_source"] = "manual"
        if verbose:
            print(f"  re-inserted as {fp_new}")
    else:
        # Non-key edit: plain UPDATE
        set_clauses: list[str] = []
        params: list = []
        # Non-key editable columns only. (director/ticker/date/type/shares change
        # the fingerprint and are handled in the key-field branch above.)
        editable_cols = {
            "role", "company", "price", "value", "announced_at",
        }
        for col in editable_cols:
            if col in fields:
                set_clauses.append(f"{col} = ?")
                if col in ("price", "value"):
                    params.append(float(fields[col]))
                else:
                    params.append(str(fields[col]))

        # Mark as manually edited
        set_clauses.append("parser_source = 'manual'")
        set_clauses.append("last_seen = ?")
        params.append(_iso_now())

        if set_clauses:
            params.append(fp_old)
            conn.execute(
                f"UPDATE transactions SET {', '.join(set_clauses)} WHERE fingerprint = ?",
                params,
            )
        if verbose:
            print(f"  updated {fp_old}: {list(fields.keys())}")

    return before, after_vals


class AddTxAlreadyExists(Exception):
    """B-134: raised by apply_add when the to-be-added fingerprint is already
    in the transactions table.

    This is NON-FATAL. The whole point of an 'add' edit is to get this
    transaction into the DB; if it is already there (e.g. a later ingest
    picked it up before the queue was applied), the desired end-state is
    already satisfied. The run() loop catches this, marks the edit as
    resolved (drops it off the review queue), and keeps applying the rest of
    the batch instead of rolling everything back.
    """

    def __init__(self, fingerprint: str, detail: str = ""):
        super().__init__(detail or fingerprint)
        self.fingerprint = fingerprint
        self.detail = detail


def apply_add(conn, edit: dict, verbose: bool) -> tuple[dict, dict]:
    """Apply an 'add' edit (manually add a transaction from a failed filing)."""
    fields = edit.get("fields") or {}
    from role_normalize import normalize_role  # noqa: PLC0415

    date      = str(fields["date"])
    ticker    = str(fields["ticker"]).strip().upper()
    director  = str(fields["director"])
    tx_type   = str(fields["type"])
    shares    = int(float(fields["shares"]))
    price     = float(fields.get("price") or 0.0)
    value     = float(fields.get("value") or 0.0)
    role      = str(fields.get("role") or "")
    company   = str(fields.get("company") or "")

    fp = _make_fingerprint(date, ticker, director, tx_type, shares)

    # Collision check: if this exact fingerprint already exists, skip.
    existing = conn.execute(
        "SELECT fingerprint FROM transactions WHERE fingerprint = ?", (fp,)
    ).fetchone()
    if existing:
        # B-134: non-fatal — the tx is already in the DB, which is the desired
        # outcome of an 'add'. Signal the caller to skip+resolve this one edit
        # rather than abort the whole batch.
        raise AddTxAlreadyExists(
            fp,
            f"{date} / {ticker} / {director} / {tx_type} / {shares}",
        )

    now = _iso_now()
    role_normalized = normalize_role(role)

    conn.execute(
        "INSERT INTO transactions ("
        "fingerprint, first_seen, last_seen, seen_count, date, ticker, "
        "company, director, role, role_normalized, type, shares, price, "
        "value, context, url, announced_at, cluster_id, first_time_buy, "
        "parser_source, buy_strictness"
        ") VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'manual', NULL)",
        (
            fp, now, now,
            date, ticker, company, director, role, role_normalized,
            tx_type, shares, price, value,
            None,                                     # context
            f"https://www.investegate.co.uk/announcement/rns/-/-/{edit.get('target_rns_id', '')}",
            date + "T00:00:00Z",                      # announced_at -- fallback to midnight on tx date
            None,                                     # cluster_id
        ),
    )

    after = {
        "fingerprint": fp, "date": date, "ticker": ticker,
        "company": company, "director": director, "role": role,
        "type": tx_type, "shares": shares, "price": price, "value": value,
        "parser_source": "manual",
    }
    if verbose:
        print(f"  inserted {fp}: {ticker} {tx_type} {shares} shares")
    return {}, after


def apply_reject(conn, edit: dict, verbose: bool) -> tuple[dict, dict]:
    """Apply a 'reject' edit.

    Deletes any transactions from this RNS ID (via url match) and their
    signals. Records the rns_id in _rejected_rns_ids.json so future
    scrapes skip it permanently.
    """
    rns_id = str(edit.get("target_rns_id") or "")
    url_pattern = f"%/{rns_id}"   # matches Investegate URL ending in the rns_id

    # Find and delete matching transactions + their signals
    affected = conn.execute(
        "SELECT fingerprint FROM transactions WHERE url LIKE ?", (url_pattern,)
    ).fetchall()

    deleted_fps = [r["fingerprint"] for r in affected]
    if deleted_fps:
        placeholders = ','.join('?' * len(deleted_fps))
        conn.execute(
            f"DELETE FROM signals WHERE fingerprint IN ({placeholders})",
            deleted_fps,
        )
        # B-135: paper_trades also FK to transactions(fingerprint) with no
        # cascade — clear children before deleting the parent rows.
        conn.execute(
            f"DELETE FROM paper_trades WHERE fingerprint IN ({placeholders})",
            deleted_fps,
        )
        conn.execute(
            "DELETE FROM transactions WHERE url LIKE ?", (url_pattern,)
        )
        if verbose:
            print(f"  deleted {len(deleted_fps)} tx row(s) for rns_id {rns_id}")
    else:
        if verbose:
            print(f"  no transactions found for rns_id {rns_id} (queue-only reject)")

    # NOTE: do NOT call add_rejected_id() here. The DB delete is inside the
    # outer transaction which has not yet committed. Calling it here would write
    # the ID to _rejected_rns_ids.json even if the transaction is later rolled
    # back (leaving the RNS ID permanently blacklisted with no DB deletion).
    # The caller (run()) collects rns_ids in `pending_rejected_ids` and calls
    # add_rejected_id() only after conn.commit() succeeds.

    before = {"deleted_fingerprints": deleted_fps}
    after  = {"rns_id": rns_id, "rejected": True, "reason": edit.get("reject_reason")}
    return before, after


def apply_delete(conn, edit: dict, verbose: bool) -> tuple[dict, dict]:
    """Apply a 'delete' edit — Phase 4 undo of a manual 'add'.

    Safeguard: only deletes rows with parser_source='manual'. This prevents
    accidental deletion of parser-ingested rows via the undo path.

    Deletes the transaction row + any signals referencing its fingerprint.
    """
    fp = edit["target_fingerprint"]

    # Fetch current row
    row = conn.execute(
        "SELECT * FROM transactions WHERE fingerprint = ?", (fp,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Fingerprint {fp!r} not found in transactions")

    before = _row_to_dict(row)

    # Safeguard: refuse to delete non-manual rows
    if before.get("parser_source") != "manual":
        raise ValueError(
            f"Fingerprint {fp!r} has parser_source={before.get('parser_source')!r}. "
            "The delete action only removes rows added manually via this tool "
            "(parser_source='manual'). Use the reject action to remove parser-ingested rows."
        )

    # Cascade: delete signals
    n_signals = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE fingerprint = ?", (fp,)
    ).fetchone()[0]
    conn.execute("DELETE FROM signals WHERE fingerprint = ?", (fp,))
    if verbose:
        print(f"  deleted {n_signals} signal row(s) for fingerprint {fp}")

    # B-135: paper_trades also FK to transactions(fingerprint) with no cascade.
    conn.execute("DELETE FROM paper_trades WHERE fingerprint = ?", (fp,))

    # Delete the transaction
    conn.execute("DELETE FROM transactions WHERE fingerprint = ?", (fp,))
    if verbose:
        print(f"  deleted manual transaction {fp}: {before.get('ticker')} "
              f"{before.get('type')} {before.get('shares')} shares")

    after = {"deleted": True, "fingerprint": fp}
    return before, after


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

_STEP_LABELS = {
    "eval_signals":          "Recomputing signal firings…",
    "backtest":              "Running signal backtests…",
    "export_dashboard_json": "Rebuilding dashboard JSON…",
    "build_dashboard":       "Regenerating HTML pages…",
}


def run_pipeline(verbose: bool, queue_size: int = 0) -> bool:
    """Run the post-edit pipeline. Returns True on full success."""
    print("\n[apply_edits] Running post-edit pipeline...")
    completed: list[str] = []
    for key, script, extra_args in PIPELINE_STEPS:
        label = _STEP_LABELS.get(key, key)
        _write_status("running", key, label, queue_size=queue_size, completed=completed)
        cmd = [sys.executable, "-u", str(HERE / script)] + extra_args
        print(f"  [{key}] {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(ROOT))
        if result.returncode != 0:
            msg = f"Pipeline step '{key}' failed (exit {result.returncode})"
            print(f"  [{key}] FAILED (exit {result.returncode}) — pipeline aborted.")
            _write_status("error", key, msg, queue_size=queue_size,
                          completed=completed, error=msg)
            return False
        completed.append(key)
        print(f"  [{key}] done")
    _write_status("done", "complete", "All done — dashboard rebuilt", queue_size=0,
                  completed=completed)
    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, no_pipeline: bool = False,
        verbose: bool = False) -> int:
    """Apply all queued edits. Returns exit code (0 = success)."""

    # 1. Read queue
    edits = read_queue()
    if not edits:
        print("[apply_edits] Edit queue is empty — nothing to apply.")
        _write_status("idle")
        return 0

    n = len(edits)
    print(f"[apply_edits] {n} edit(s) to apply.")
    _write_status("running", "validating", f"Validating {n} edit(s)…", queue_size=n)

    # 2. Validate all edits BEFORE opening the DB
    all_errors: list[str] = []
    for i, edit in enumerate(edits):
        errs = validate_edit(edit)
        for e in errs:
            all_errors.append(f"  edit[{i}] ({edit.get('edit_id','')}): {e}")

    if all_errors:
        msg = "Validation failed: " + "; ".join(all_errors[:3])
        print("[apply_edits] Validation failed — no changes written:")
        for e in all_errors:
            print(e)
        _write_status("error", "validating", "Validation failed", queue_size=n, error=msg)
        return 2

    if dry_run:
        print("[apply_edits] --dry-run: validation passed, no changes written.")
        for edit in edits:
            print(f"  would apply: {edit.get('action')} {edit.get('edit_id','')}")
        return 0

    # 3. Backup DB before writing
    _write_status("running", "backup", "Backing up database…", queue_size=n)
    print("[apply_edits] Taking pre-apply DB backup...")
    if not db_health.backup():
        print("[apply_edits] WARNING: pre-apply backup failed — proceeding anyway.")

    # 4. Open DB and apply all edits in ONE transaction
    _write_status("running", "applying", f"Applying {n} edit(s) to database…", queue_size=n)
    conn = db.connect()
    audit_entries: list[dict] = []
    pending_rejected_ids: list[str] = []   # flushed to disk AFTER commit
    resolved_rns_ids: list[str] = []       # add + reject RNS IDs to purge from pending queue
    skipped_adds: list[str] = []           # B-134: 'add' edits whose tx already existed
    try:
        conn.execute("BEGIN")

        for edit in edits:
            action = edit["action"]
            edit_id = edit.get("edit_id", "unknown")
            if verbose:
                print(f"\n  Applying {action} {edit_id}")

            try:
                if action == "update":
                    before, after = apply_update(conn, edit, verbose)
                elif action == "add":
                    before, after = apply_add(conn, edit, verbose)
                    rns = str(edit.get("target_rns_id") or "")
                    if rns:
                        resolved_rns_ids.append(rns)       # post-commit: remove from pending queue
                elif action == "reject":
                    before, after = apply_reject(conn, edit, verbose)
                    rns = str(edit.get("target_rns_id") or "")
                    if rns:
                        pending_rejected_ids.append(rns)   # post-commit: persist reject list
                        resolved_rns_ids.append(rns)       # post-commit: remove from pending queue
                elif action == "delete":
                    before, after = apply_delete(conn, edit, verbose)
                else:
                    raise ValueError(f"Unknown action {action!r}")
            except AddTxAlreadyExists as dup:
                # B-134: duplicate 'add' — the tx is already in the DB, so treat
                # this edit as already-resolved. Do NOT roll back; keep applying
                # the rest of the batch. Mark the RNS resolved so it drops off
                # the review queue (the post-commit add_manual_id pass also picks
                # it up via the action=="add" comprehension below).
                print(f"  [skip] add {edit_id}: fingerprint {dup.fingerprint} "
                      f"already in DB ({dup.detail}) — marking resolved")
                rns = str(edit.get("target_rns_id") or "")
                if rns:
                    resolved_rns_ids.append(rns)
                skipped_adds.append(edit_id)
                audit_entries.append({
                    "ts":                 _iso_now(),
                    "action":             "add-skipped",
                    "edit_id":            edit_id,
                    "target_fingerprint": dup.fingerprint,
                    "target_rns_id":      edit.get("target_rns_id"),
                    "before":             {},
                    "after":              {"fingerprint": dup.fingerprint,
                                           "note": "already in DB — add skipped"},
                    "reject_reason":      None,
                })
                continue
            except Exception as exc:
                conn.rollback()  # rolls back the BEGIN; finally will close
                print(f"\n[apply_edits] ERROR applying {action} {edit_id}: {exc}")
                print("  All edits ROLLED BACK. No changes written to DB.")
                print("  Fix the issue and re-run apply_edits.py.")
                # Terminal status so the UI never spins forever on this path.
                _write_status("error", "applying",
                              f"Edit {edit_id} failed — all edits rolled back",
                              queue_size=n, completed=[], error=str(exc))
                return 1

            audit_entries.append({
                "ts":                 _iso_now(),
                "action":             action,
                "edit_id":            edit_id,
                "target_fingerprint": edit.get("target_fingerprint"),
                "target_rns_id":      edit.get("target_rns_id"),
                "before":             before,
                "after":              after,
                "reject_reason":      edit.get("reject_reason"),
            })

        conn.commit()
        applied_n = len(edits) - len(skipped_adds)
        print(f"\n[apply_edits] {applied_n} edit(s) committed to DB.")
        if skipped_adds:
            print(f"[apply_edits] {len(skipped_adds)} add(s) skipped "
                  f"(already in DB) and marked resolved: {', '.join(skipped_adds)}")

        # Post-commit: flush rejected IDs and remove resolved items from the
        # pending queue. Both are done AFTER commit so the two stores stay
        # consistent — a rollback leaves neither changed.
        for rns_id in pending_rejected_ids:
            if rns_id:
                add_rejected_id(rns_id)
        if pending_rejected_ids:
            print(f"[apply_edits] {len(pending_rejected_ids)} RNS ID(s) added to reject list.")

        # Phase 3: record manually-added RNS IDs so ingest paths skip them.
        pending_manual_ids = [str(e.get("target_rns_id") or "")
                              for e in edits if e.get("action") == "add"
                              and e.get("target_rns_id")]
        for rns_id in pending_manual_ids:
            add_manual_id(rns_id)
        if pending_manual_ids:
            print(f"[apply_edits] {len(pending_manual_ids)} RNS ID(s) marked as manually resolved.")

        # Remove all resolved RNS IDs (add + reject) from _pending_review.json
        # so Tab A no longer shows them after the next export run.
        if resolved_rns_ids:
            remove_from_pending_queue(resolved_rns_ids)

    except Exception as exc:
        conn.rollback()  # finally will close
        msg = str(exc)
        print(f"[apply_edits] Unexpected error: {msg}")
        print("  All edits ROLLED BACK.")
        _write_status("error", "applying", "Database error — all edits rolled back",
                      queue_size=n, error=msg)
        return 1
    finally:
        conn.close()

    # 5. Write audit trail (JSONL append -- one line per edit)
    for entry in audit_entries:
        append_audit(entry)
    print(f"[apply_edits] Audit written to {EDIT_AUDIT_PATH}")

    # 6. Clear the queue
    clear_queue()
    print("[apply_edits] Edit queue cleared.")

    # 7. Post-apply backup
    db_health.backup()
    print("[apply_edits] Post-apply DB backup taken.")

    # 8. Pipeline
    if no_pipeline:
        print("\n[apply_edits] --no-pipeline: skipping dashboard rebuild.")
        print("  Run manually when ready:")
        for _, s, a in PIPELINE_STEPS:
            print(f"    python .scripts/{s} {' '.join(a)}")
        _write_status("done", "complete", f"{n} edit(s) applied — pipeline skipped", queue_size=0)
    else:
        ok = run_pipeline(verbose, queue_size=n)
        if not ok:
            return 1

    print("\n[apply_edits] Done.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Apply staged PDMR edits from _edit_queue.json to directors.db."
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Validate the queue but write nothing.")
    p.add_argument("--no-pipeline", action="store_true",
                   help="Skip eval_signals/backtest/export/build after apply.")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-edit detail.")
    args = p.parse_args()
    return run(dry_run=args.dry_run, no_pipeline=args.no_pipeline,
               verbose=args.verbose)


if __name__ == "__main__":
    try:
        _rc = main()
    except BaseException as _exc:  # noqa: BLE001 — guarantee a terminal status
        # Any abnormal exit (crash, KeyboardInterrupt, killed pipeline step that
        # re-raises) must leave a terminal status so the review UI's progress
        # poll stops spinning. Best-effort; never mask the original error.
        try:
            _write_status("error", "crashed",
                          f"Apply crashed: {_exc}", error=str(_exc))
        except Exception:
            pass
        raise
    raise SystemExit(_rc)
