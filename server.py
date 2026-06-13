"""
Directors Dealings -- Local Web Server
=======================================
Serves the Stage 5 dashboard and provides the pipeline API.

    python server.py

Press Ctrl+C to stop.

Routes
------
GET  /                        -- serves outputs/index.html (Stage 5 dashboard)
GET  /review                  -- Sprint 25: PDMR review surface
GET  /api/status              -- health check
POST /api/refresh-all         -- kick off refresh_all.py pipeline as subprocess
GET  /api/refresh-status      -- poll pipeline progress
POST /api/refresh-reset       -- reset status to idle (when not running)
POST /api/deprecate           -- deprecate / reactivate a signal_id
GET  /api/rns-html/<rns_id>   -- Sprint 25: serve cached RNS HTML for the side-by-side viewer
GET  /api/tx/<fingerprint>    -- Sprint 25: return one transaction's fields as JSON
POST /api/stage-edit          -- Sprint 25 Phase 1: stage an edit intent
GET  /api/edit-queue          -- Sprint 25 Phase 1: return current edit queue
DELETE /api/edit-queue/<id>   -- Sprint 25 Phase 1: unstage a specific edit
POST /api/apply-edits         -- Sprint 25 Phase 2: spawn apply_edits.py as subprocess
GET  /api/apply-status        -- Sprint 25 Phase 2: poll apply progress
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, Response

# ── App setup ─────────────────────────────────────────────────────────────────

app  = Flask(__name__, static_folder="outputs", static_url_path="")
PORT = 5000

ROOT = Path(__file__).resolve().parent

_SCRAPE_CACHE_DIR = ROOT / ".scripts" / "_scrape_cache"
_DB_PATH          = ROOT / ".data" / "directors.db"
_RNS_ID_RE        = re.compile(r"^\d{5,12}$")


# ── Static serving ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


# ── Sprint 25: /review ────────────────────────────────────────────────────────

@app.route("/review")
def review():
    """Serve the PDMR review surface (Sprint 25 Phase 0)."""
    return app.send_static_file("review.html")


# ── Sprint 25: /api/rns-html/<rns_id> ────────────────────────────────────────

@app.route("/api/rns-html/<rns_id>")
def api_rns_html(rns_id):
    """Return the cached RNS filing HTML for the side-by-side viewer.

    The rns_id must be purely numeric (5–12 digits) to prevent any path
    traversal. Returns the raw cached HTML with a restrictive CSP header so
    the browser sandboxes it correctly when loaded in an <iframe>.

    Falls back to a plain ``{"error": "not_found"}`` JSON if the cache file
    is missing (the UI then falls back to the external Investegate link).
    """
    if not _RNS_ID_RE.match(rns_id):
        return jsonify({"error": "invalid_rns_id"}), 400

    cache_file = _SCRAPE_CACHE_DIR / f"{rns_id}.html"
    if not cache_file.exists():
        return jsonify({"error": "not_found", "rns_id": rns_id}), 404

    try:
        html_bytes = cache_file.read_bytes()
    except OSError as exc:
        return jsonify({"error": "read_error", "detail": str(exc)}), 500

    return Response(
        html_bytes,
        mimetype="text/html; charset=utf-8",
        headers={
            # Strict sandbox: allow-same-origin lets JS read the DOM;
            # no allow-scripts to prevent any inline JS in the RNS HTML
            # from running in the context of our server.
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:;"
            ),
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


# ── Sprint 25: /api/tx/<fingerprint> ─────────────────────────────────────────

_FP_RE = re.compile(r"^[0-9a-f]{8,32}$")


@app.route("/api/tx/<fingerprint>")
def api_tx(fingerprint):
    """Return one transaction's fields as JSON for the right-pane viewer.

    Read-only. Opens a fresh read-only SQLite connection each call (safe —
    no write path; WAL mode allows concurrent reads). Returns 404 if the
    fingerprint is not in the DB.
    """
    if not _FP_RE.match(fingerprint):
        return jsonify({"error": "invalid_fingerprint"}), 400

    if not _DB_PATH.exists():
        return jsonify({"error": "db_not_found"}), 503

    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True,
                              check_same_thread=False)
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT t.fingerprint, t.date, t.ticker, t.company, t.director,
                   t.role, t.role_normalized, t.type, t.shares, t.price,
                   t.value, t.url, t.announced_at, t.parser_source,
                   t.buy_strictness, tm.sector
            FROM   transactions t
            LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker
            WHERE  t.fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()
        con.close()
    except sqlite3.OperationalError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500

    if row is None:
        return jsonify({"error": "not_found", "fingerprint": fingerprint}), 404

    url = row["url"] or ""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    rns_id = tail if (tail.isdigit() and 5 <= len(tail) <= 12) else ""

    return jsonify({
        "fingerprint":    row["fingerprint"],
        "date":           row["date"],
        "ticker":         row["ticker"],
        "company":        row["company"] or "",
        "director":       row["director"] or "",
        "role":           row["role"] or "",
        "role_normalized": row["role_normalized"],
        "type":           row["type"],
        "shares":         row["shares"],
        "price":          row["price"],
        "value":          row["value"],
        "url":            url,
        "rns_id":         rns_id,
        "announced_at":   row["announced_at"] or "",
        "parser_source":  row["parser_source"] or "",
        "buy_strictness": row["buy_strictness"] or "",
        "sector":         row["sector"] or "",
        "has_cache":      (_SCRAPE_CACHE_DIR / f"{rns_id}.html").exists() if rns_id else False,
    })


# ── Sprint 25 Phase 1: /api/stage-edit, /api/edit-queue ─────────────────────

_EDIT_QUEUE_PATH = ROOT / ".data" / "_edit_queue.json"
_EDIT_QUEUE_LOCK = threading.Lock()

_VALID_TX_TYPES = {"BUY", "SELL", "SELL_TAX", "EXERCISE", "GRANT", "SIP"}
_VALID_ACTIONS  = {"update", "add", "reject"}
_KNOWN_SECTORS  = {
    "Financials", "Energy", "Health Care", "Industrials",
    "Consumer Discretionary", "Consumer Staples", "Materials",
    "Technology", "Utilities", "Communication Services", "Real Estate",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read_edit_queue() -> dict:
    """Return the edit queue dict. Returns empty queue on missing/corrupt file."""
    if not _EDIT_QUEUE_PATH.exists():
        return {"version": 1, "edits": []}
    try:
        return json.loads(_EDIT_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "edits": []}


def _write_edit_queue_atomic(queue: dict) -> None:
    """Atomically write the edit queue using the same os.replace pattern."""
    _EDIT_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _EDIT_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    os.replace(tmp, _EDIT_QUEUE_PATH)


def _validate_fields(fields: dict, action: str) -> list[str]:
    """Validate editable transaction fields. Returns list of error strings (empty = ok)."""
    errors = []
    if action == "reject":
        return errors   # no field validation for reject

    # Required fields for "add"
    if action == "add":
        for req in ("date", "ticker", "director", "type", "shares"):
            if not fields.get(req) and fields.get(req) != 0:
                errors.append(f"'{req}' is required for add action")

    # Field-level validation (only if the field is present)
    t = fields.get("type")
    if t is not None and t not in _VALID_TX_TYPES:
        errors.append(f"type must be one of {sorted(_VALID_TX_TYPES)}, got {t!r}")

    for num_field in ("shares", "price", "value"):
        v = fields.get(num_field)
        if v is not None:
            try:
                fv = float(v)
                if fv < 0:
                    errors.append(f"{num_field} must be >= 0")
                if num_field == "shares" and fv != int(fv):
                    errors.append("shares must be a whole number")
            except (TypeError, ValueError):
                errors.append(f"{num_field} must be numeric")

    d = fields.get("date")
    if d is not None:
        if not _DATE_RE.match(str(d)):
            errors.append("date must be YYYY-MM-DD")

    tk = fields.get("ticker")
    if tk is not None and not str(tk).strip():
        errors.append("ticker must not be empty")

    for str_field in ("director", "company"):
        sv = fields.get(str_field)
        if sv is not None and not str(sv).strip():
            errors.append(f"{str_field} must not be empty")

    sector = fields.get("sector")
    if sector and sector not in _KNOWN_SECTORS:
        # Warn but don't block — new sectors can appear via Yahoo Finance
        errors.append(
            f"sector {sector!r} not in known list — check spelling. "
            f"Known: {sorted(_KNOWN_SECTORS)}"
        )

    return errors


@app.route("/api/stage-edit", methods=["POST"])
def api_stage_edit():
    """Stage one edit intent in .data/_edit_queue.json (no DB write).

    Body:
        {
          "action":             "update" | "add" | "reject",
          "target_fingerprint": "<hex>",   // for update
          "target_rns_id":      "<digits>", // for add / reject
          "fields":             { <col>: <val>, ... },
          "reject_reason":      "junk" | "boilerplate" | "foreign_currency" | "duplicate" | ""
        }

    Response:
        { "ok": true, "edit_id": "...", "queue_length": N }
    """
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    action = (data.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return jsonify({"ok": False, "error": f"action must be one of {sorted(_VALID_ACTIONS)}"}), 400

    target_fp  = (data.get("target_fingerprint") or "").strip()
    target_rns = (data.get("target_rns_id") or "").strip()
    fields     = data.get("fields") or {}
    reject_reason = (data.get("reject_reason") or "").strip()

    # Target validation
    if action == "update":
        if not _FP_RE.match(target_fp):
            return jsonify({"ok": False, "error": "target_fingerprint missing or invalid"}), 400
    elif action in ("add", "reject"):
        if not _RNS_ID_RE.match(target_rns):
            return jsonify({"ok": False, "error": "target_rns_id missing or invalid (must be 5–12 digits)"}), 400

    if action != "reject" and not isinstance(fields, dict):
        return jsonify({"ok": False, "error": "fields must be a JSON object"}), 400
    if action == "update" and not fields:
        return jsonify({"ok": False, "error": "fields must not be empty for update"}), 400

    # Field-level validation
    field_errors = _validate_fields(fields, action)
    if field_errors:
        return jsonify({"ok": False, "error": "validation failed", "field_errors": field_errors}), 422

    # Coerce types
    if "shares" in fields:
        try:
            fields["shares"] = int(float(fields["shares"]))
        except (TypeError, ValueError):
            pass
    if "ticker" in fields:
        fields["ticker"] = str(fields["ticker"]).strip().upper()

    with _EDIT_QUEUE_LOCK:
        queue = _read_edit_queue()
        edits = queue.get("edits") or []
        edit_id = f"edit-{_iso_utc_now().replace(':', '').replace('-', '').replace('T', '-').replace('Z', '')}-{len(edits)}"
        entry = {
            "edit_id":            edit_id,
            "staged_at":          _iso_utc_now(),
            "action":             action,
            "target_fingerprint": target_fp  or None,
            "target_rns_id":      target_rns or None,
            "fields":             fields,
            "reject_reason":      reject_reason or None,
        }
        edits.append(entry)
        queue["edits"] = edits
        try:
            _write_edit_queue_atomic(queue)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {exc}"}), 500

    return jsonify({"ok": True, "edit_id": edit_id, "queue_length": len(edits)})


@app.route("/api/edit-queue", methods=["GET"])
def api_edit_queue():
    """Return the current edit queue."""
    with _EDIT_QUEUE_LOCK:
        queue = _read_edit_queue()
    return jsonify(queue)


@app.route("/api/edit-queue/<edit_id>", methods=["DELETE"])
def api_unstage_edit(edit_id):
    """Remove a specific edit from the queue by its edit_id."""
    if not re.match(r"^edit-[\w-]+$", edit_id):
        return jsonify({"ok": False, "error": "invalid edit_id"}), 400
    with _EDIT_QUEUE_LOCK:
        queue = _read_edit_queue()
        before = len(queue.get("edits") or [])
        queue["edits"] = [e for e in (queue.get("edits") or [])
                          if e.get("edit_id") != edit_id]
        after = len(queue["edits"])
        if before == after:
            return jsonify({"ok": False, "error": "edit_id not found"}), 404
        try:
            _write_edit_queue_atomic(queue)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {exc}"}), 500
    return jsonify({"ok": True, "removed": edit_id, "queue_length": after})


# ── Sprint 25 Phase 2: /api/apply-edits, /api/apply-status ──────────────────

_APPLY_STATUS_PATH = ROOT / ".data" / "_apply_status.json"
_APPLY_LOCK        = threading.Lock()
_APPLY_SCRIPT      = ROOT / ".scripts" / "apply_edits.py"


_APPLY_LOG_PATH    = ROOT / ".data" / "_apply_last.log"
_APPLY_STALE_SECS  = 900   # a "running" status older than this is recoverable


def _apply_read_status() -> dict:
    try:
        return json.loads(_APPLY_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "idle"}


def _apply_status_is_stale(status: dict, max_age_sec: int = _APPLY_STALE_SECS) -> bool:
    """True if a 'running' status is old enough to be treated as a dead run.

    Guards against a crashed apply (e.g. server window closed mid-pipeline)
    leaving _apply_status.json frozen on 'running' and locking out all future
    applies forever.
    """
    if status.get("status") != "running":
        return False
    started = status.get("started_at")
    if not started:
        return True  # running with no timestamp → assume dead
    try:
        ts = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return (datetime.now(timezone.utc) - ts).total_seconds() > max_age_sec


def _apply_set_idle() -> None:
    """Force the apply status file back to idle (atomic)."""
    _APPLY_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _APPLY_STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"status": "idle"}), encoding="utf-8")
    os.replace(tmp, _APPLY_STATUS_PATH)


@app.route("/api/apply-edits", methods=["POST"])
def api_apply_edits():
    """Spawn apply_edits.py as a background subprocess.

    Body (optional): { "no_pipeline": false }
    Returns 202 on success; 409 if already running; 400 if queue empty.
    """
    with _APPLY_LOCK:
        # Reject if a *fresh* run is already in progress. A stale 'running'
        # (older than _APPLY_STALE_SECS — e.g. a crashed run that left the
        # status frozen) is treated as recoverable so the user is never
        # permanently locked out.
        current = _apply_read_status()
        if current.get("status") == "running" and not _apply_status_is_stale(current):
            return jsonify({"ok": False, "reason": "already_running",
                            "status": current}), 409

        # Reject if queue is empty
        queue = _read_edit_queue()
        n_edits = len(queue.get("edits") or [])
        if n_edits == 0:
            return jsonify({"ok": False, "reason": "queue_empty",
                            "error": "No edits staged — nothing to apply."}), 400

        try:
            body = request.get_json(silent=True) or {}
        except Exception:
            body = {}
        no_pipeline = bool(body.get("no_pipeline", False))

        # Seed status immediately so the first poll sees "running"
        _APPLY_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _APPLY_STATUS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "status": "running", "step": "queued",
            "step_label": "Starting…", "queue_size": n_edits,
            "started_at": _iso_utc_now(), "completed": [], "error": None,
        }), encoding="utf-8")
        os.replace(tmp, _APPLY_STATUS_PATH)

        # Spawn apply_edits.py as detached subprocess (same pattern as refresh_all).
        # Redirect output to .data/_apply_last.log (not DEVNULL) so that if the
        # run dies mid-pipeline there is a diagnosable trail instead of silence.
        args = [sys.executable, "-u", str(_APPLY_SCRIPT)]
        if no_pipeline:
            args.append("--no-pipeline")
        try:
            _APPLY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(_APPLY_LOG_PATH, "w", encoding="utf-8")
            subprocess.Popen(
                args,
                cwd=str(ROOT),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Parent's copy of the fd can be closed; the child keeps its own.
            log_fh.close()
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "reason": "spawn_failed",
                            "error": repr(e)}), 500

    return jsonify({"ok": True, "queue_size": n_edits,
                    "no_pipeline": no_pipeline}), 202


@app.route("/api/apply-status", methods=["GET"])
def api_apply_status():
    """Poll the current apply_edits.py progress."""
    return jsonify(_apply_read_status())


@app.route("/api/apply-reset", methods=["POST"])
def api_apply_reset():
    """Force the apply status back to idle so a stuck run can be retried.

    Permitted unless a *fresh* run is genuinely in progress (a stale 'running'
    is treated as a dead run and may be reset). Mirrors /api/refresh-reset.
    """
    with _APPLY_LOCK:
        current = _apply_read_status()
        if current.get("status") == "running" and not _apply_status_is_stale(current):
            return jsonify({"ok": False, "reason": "running",
                            "status": current}), 409
        _apply_set_idle()
        return jsonify({"ok": True})


# ── Sprint 25 Phase 4: /api/audit-log ────────────────────────────────────────

_EDIT_AUDIT_PATH = ROOT / ".data" / "_edit_audit.jsonl"
_AUDIT_LOG_MAX   = 50   # max entries returned (most recent first)


@app.route("/api/audit-log", methods=["GET"])
def api_audit_log():
    """Return the last N entries from _edit_audit.jsonl (most recent first).

    Query params:
        n=<int>   max entries to return (default 50, max 200)

    Response: { "ok": true, "entries": [...], "total_lines": <int> }
    Each entry is the raw JSON object from the JSONL file.
    """
    try:
        n = min(int(request.args.get("n", _AUDIT_LOG_MAX)), 200)
    except (TypeError, ValueError):
        n = _AUDIT_LOG_MAX

    if not _EDIT_AUDIT_PATH.exists():
        return jsonify({"ok": True, "entries": [], "total_lines": 0})

    try:
        raw = _EDIT_AUDIT_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    total = len(lines)
    recent = lines[-n:][::-1]   # last N, most-recent first

    entries = []
    for line in recent:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"_raw": line, "_parse_error": True})

    return jsonify({"ok": True, "entries": entries, "total_lines": total})


# ── /api/status ───────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    """Simple health check. Powers the green dot in the dashboard header."""
    return jsonify({
        "status": "ok",
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


# ── /api/deprecate ────────────────────────────────────────────────────────────

_SIGNAL_STATUS_PATH = ROOT / ".data" / "signal_status.json"
_DEPRECATE_LOCK = threading.Lock()
_VALID_SIGNAL_IDS = {
    "t0", "t1", "t2", "t3", "t4", "s1", "f1",
    "t0_cluster_combo", "t1_ceo_cfo_buy", "t2_exec_buy",
    "t3_ned_buy", "t4_other_buy", "s1_cluster_buy", "f1_first_time_buy",
}


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_signal_status_atomic(payload: dict, target: Path) -> None:
    """Atomically write the signal_status JSON."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


@app.route("/api/deprecate", methods=["POST"])
def api_deprecate():
    """Write a signal_id into .data/signal_status.json (atomic).

    Body: { "signal_id": "t3", "action": "deprecate"|"reactivate" }
    Response: { "ok": true, "signal_id": "t3", "status": "...",
                "deprecated": [...], "written_at": "..." }
    """
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    signal_id = (data.get("signal_id") or "").strip()
    action    = (data.get("action") or "deprecate").strip().lower()

    if not signal_id or signal_id not in _VALID_SIGNAL_IDS:
        return jsonify({"ok": False, "error": f"invalid signal_id: {signal_id!r}"}), 400
    if action not in {"deprecate", "reactivate"}:
        return jsonify({"ok": False, "error": f"invalid action: {action!r}"}), 400

    with _DEPRECATE_LOCK:
        current = {"deprecated": [], "updated_at": None}
        if _SIGNAL_STATUS_PATH.exists():
            try:
                current = json.loads(_SIGNAL_STATUS_PATH.read_text(encoding="utf-8"))
            except Exception:
                current = {"deprecated": [], "updated_at": None}

        deprecated = set(current.get("deprecated") or [])
        if action == "deprecate":
            deprecated.add(signal_id)
        else:
            deprecated.discard(signal_id)

        payload = {
            "deprecated": sorted(deprecated),
            "updated_at": _iso_utc_now(),
        }
        try:
            _write_signal_status_atomic(payload, _SIGNAL_STATUS_PATH)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"write failed: {exc}"}), 500

    return jsonify({
        "ok":         True,
        "signal_id":  signal_id,
        "status":     "deprecated" if action == "deprecate" else "active",
        "deprecated": payload["deprecated"],
        "written_at": payload["updated_at"],
    })


# ── /api/refresh-* ────────────────────────────────────────────────────────────

_REFRESH_LOCK        = threading.Lock()
_REFRESH_STATUS_PATH = ROOT / ".data" / "_refresh_status.json"
_REFRESH_SCRIPT      = ROOT / ".scripts" / "refresh_all.py"


def _refresh_read_status() -> dict:
    """Read the worker-managed status JSON. Returns idle dict on missing/corrupt."""
    try:
        return json.loads(_REFRESH_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "idle"}


def _refresh_set_idle() -> None:
    _REFRESH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _REFRESH_STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"status": "idle"}), encoding="utf-8")
    os.replace(tmp, _REFRESH_STATUS_PATH)


def _spawn_refresh_subprocess(scrape_days, no_llm: bool, skip_scrape: bool) -> None:
    """Spawn refresh_all.py as a detached subprocess.

    scrape_days=None → refresh_all.py auto-detects the delta from the DB.
    """
    args = [sys.executable, "-u", str(_REFRESH_SCRIPT)]
    if scrape_days is not None:
        args += ["--scrape-days", str(int(scrape_days))]
    if no_llm:
        args.append("--no-llm")
    if skip_scrape:
        args.append("--skip-scrape")
    subprocess.Popen(
        args,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


@app.route("/api/refresh-all", methods=["POST"])
def api_refresh_all():
    """Kick off the full pipeline as a background subprocess.

    Body (all optional)::
        { "scrape_days": 60, "no_llm": false, "skip_scrape": false }

    Returns 202 on success; 409 if a refresh is already running.
    """
    with _REFRESH_LOCK:
        current = _refresh_read_status()
        if current.get("status") == "running":
            return jsonify({"ok": False, "reason": "already_running",
                            "status": current}), 409

        try:
            body = request.get_json(silent=True) or {}
        except Exception:
            body = {}

        _sd = body.get("scrape_days")
        scrape_days = int(_sd) if _sd and int(_sd) > 0 else None
        no_llm      = bool(body.get("no_llm"))
        skip_scrape = bool(body.get("skip_scrape"))

        # Seed status to "running" so a fast poll sees something honest.
        _REFRESH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _REFRESH_STATUS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "status":     "running",
            "step":       "queued",
            "step_label": "Starting pipeline",
            "started_at": _iso_utc_now(),
            "completed":  [], "log": [], "error": None,
        }), encoding="utf-8")
        os.replace(tmp, _REFRESH_STATUS_PATH)

        try:
            _spawn_refresh_subprocess(scrape_days, no_llm, skip_scrape)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "reason": "spawn_failed",
                            "error": repr(e)}), 500

        return jsonify({"ok": True, "status": _refresh_read_status()}), 202


@app.route("/api/refresh-status", methods=["GET"])
def api_refresh_status():
    """Read-only status poll. Returns the current refresh state."""
    return jsonify(_refresh_read_status())


@app.route("/api/refresh-reset", methods=["POST"])
def api_refresh_reset():
    """Force the status back to idle. Only permitted when not running."""
    with _REFRESH_LOCK:
        current = _refresh_read_status()
        if current.get("status") == "running":
            return jsonify({"ok": False, "reason": "running"}), 409
        _refresh_set_idle()
        return jsonify({"ok": True})


# ── Startup ───────────────────────────────────────────────────────────────────

def _open_browser():
    time.sleep(1.2)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    print("\n==========================================")
    print("  Directors Dealings -- Local Server")
    print(f"  http://localhost:{PORT}")
    print("  Press Ctrl+C to stop")
    print("==========================================\n")
    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
