"""Stage 2 LLM cost ledger and ceiling guard.

Maintains `.scripts/_llm_cost.json` (relative to project root). Tracks
per-run spend so the orchestrator can abort cleanly when a budget
ceiling is hit, preventing a stuck loop from draining the API balance.

Public surface:
    start_run() -> str (run_id)
    record_call(input_tokens, output_tokens, model) -> float (usd)
    check_budget(run_id, budget_usd) -> None  (raises BudgetExceededError)
    end_run(run_id) -> None
    BudgetExceededError
    LEDGER_PATH

Pricing constants are baked in for `claude-sonnet-4-6`:
    Input  : $3 / MTok
    Output : $15 / MTok
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / ".scripts" / "_llm_cost.json"


# Public Anthropic Sonnet pricing as of build time.
PRICE_TABLE = {
    "claude-sonnet-4-6": {"in_per_mtok": 3.00, "out_per_mtok": 15.00},
    "default":          {"in_per_mtok": 3.00, "out_per_mtok": 15.00},
}


class BudgetExceededError(Exception):
    """Raised when a run's USD spend hits or exceeds the ceiling."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_ledger() -> dict:
    return {"lifetime_usd": 0.0, "runs": []}


def _load() -> dict:
    if not LEDGER_PATH.exists():
        return _empty_ledger()
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_ledger()


def _save(data: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # L-B: fsync the tmp file before rename so a hard reset can't strand
    # an orphan .tmp ledger with no committed ledger to swap in.
    # NOTE: open in "rb+" (read+write), not "rb" — Windows fsync requires
    # a writable handle (raises EBADF on read-only handles).
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(LEDGER_PATH))


def _find_run(data: dict, run_id: str) -> dict | None:
    for r in data.get("runs", []):
        if r.get("run_id") == run_id:
            return r
    return None


def start_run() -> str:
    """Open a new run record and return its id."""
    data = _load()
    run_id = uuid.uuid4().hex[:12]
    data.setdefault("runs", []).append({
        "run_id": run_id,
        "started_at": _iso_now(),
        "finished_at": None,
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "usd": 0.0,
    })
    _save(data)
    return run_id


def _price_for(model: str) -> dict:
    return PRICE_TABLE.get(model, PRICE_TABLE["default"])


def record_call(
    input_tokens: int,
    output_tokens: int,
    model: str,
    run_id: str | None = None,
) -> float:
    """Record one API call and return its USD cost.

    If `run_id` is given, the call attributes to that run AND to the
    lifetime total. If `run_id` is None, lifetime-only.
    """
    rate = _price_for(model)
    usd = (
        (input_tokens / 1_000_000.0) * rate["in_per_mtok"]
        + (output_tokens / 1_000_000.0) * rate["out_per_mtok"]
    )
    data = _load()
    data["lifetime_usd"] = round(data.get("lifetime_usd", 0.0) + usd, 6)
    if run_id:
        run = _find_run(data, run_id)
        if run is None:
            # Permissive: if the run record is missing, create it.
            run = {
                "run_id": run_id,
                "started_at": _iso_now(),
                "finished_at": None,
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "usd": 0.0,
            }
            data.setdefault("runs", []).append(run)
        run["calls"] = run.get("calls", 0) + 1
        run["tokens_in"] = run.get("tokens_in", 0) + int(input_tokens)
        run["tokens_out"] = run.get("tokens_out", 0) + int(output_tokens)
        run["usd"] = round(run.get("usd", 0.0) + usd, 6)
    _save(data)
    return usd


def check_budget(run_id: str, budget_usd: float) -> None:
    """Raise BudgetExceededError if the run's spend has hit the ceiling."""
    data = _load()
    run = _find_run(data, run_id)
    if run is None:
        return
    if run.get("usd", 0.0) >= budget_usd:
        raise BudgetExceededError(
            f"run {run_id}: spent ${run['usd']:.4f} >= ${budget_usd:.2f} budget"
        )


def end_run(run_id: str) -> None:
    data = _load()
    run = _find_run(data, run_id)
    if run is None:
        return
    run["finished_at"] = _iso_now()
    _save(data)
