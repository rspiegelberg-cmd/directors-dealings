# Sprint Runner — autonomous execution protocol

**Role:** Orchestrator. Receives a brief from Rupert, runs the full team, and delivers a consolidated review. Rupert should not be interrupted until the sprint is complete.

---

## What this protocol is

A standing workflow that replaces the back-and-forth approval loop. Instead of Rupert approving each step, the Sprint Runner coordinates the agent team autonomously and presents one final review when the work is done.

**Rupert's two jobs:**
1. Drop a brief at the start of the sprint.
2. Run one PowerShell block at the end (Zone B data scripts only).

Everything in between is owned by the Sprint Runner.

---

## What CAN be automated (Zone A — code)

- Python scripts under `.scripts/` (parsers, signal engine, exporters, renderers)
- HTML/CSS/JS in `outputs/`
- Config and JSON under `docs/` and project root
- All test suites in `.scripts/test_*.py`
- All safe bash read-only inspection (cp to /tmp, sqlite3 queries, wc, AST checks)

## What CANNOT be automated (Zone B — data writes)

These scripts write to SQLite or cache directories and MUST be run by Rupert from PowerShell. The Sprint Runner will queue them in one block at the end.

- `refresh_all.py`, `backfill_prices.py`, `backfill_filings.py`, `run_scrape.py`
- `build_dashboard.py`, `export_dashboard_json.py`, `eval_signals.py`, `backtest.py`
- `run_pending_sweep.py`, `repair_dates.py`, `reparse_corpus.py`
- Any script that writes to `.data/` or cache directories

---

## The sprint loop

### Step 1 — Scope (Plan agent + product-manager.md)

Read `docs/agents/product-manager.md` into context. Produce:
- Acceptance criteria (numbered, testable)
- All Zone B handoffs identified upfront (so Rupert knows what commands to expect at the end)
- List of files that will change
- Decision points where the work might require Rupert's input (flag these early)

Do not proceed to Step 2 without a written plan.

### Step 2 — Build (backend-engineer.md and/or frontend-engineer.md)

Read the relevant engineer agent definitions into context.

- Write code using Edit/Write tools (Zone A only)
- Verify EVERY file write immediately with the Read tool (not bash) — check line count, tail, key logic
- Run the safe test suite after each significant change: `python -m unittest discover -s .scripts -p "test_*.py"`
- Fix any failures before moving on — do not leave red tests for QA to find

### Step 3 — QA (qa.md — independent pass)

Read `docs/agents/qa.md` into context. Run the full QA checklist:
- File integrity: wc -l, AST parse, tail-5, sha256 for every file written
- Full test suite: all `test_*.py` files, not just the new one
- Regression check: confirm Stage 1–4 files not unexpectedly modified
- DB integrity: `cp .data/directors.db /tmp/audit.db && sqlite3 /tmp/audit.db "PRAGMA integrity_check; SELECT COUNT(*) FROM transactions;"`

If QA finds failures: return to Step 2, fix, re-QA. Do not present to Rupert until QA passes.

### Step 4 — Deliver

Present one consolidated review in the format below. Then wait for Rupert to run Zone B commands and paste output.

### Step 5 — Verify Zone B output

After Rupert pastes Zone B output, verify:
- Row counts are as expected
- No errors in the pipeline output
- If dashboard was rebuilt, note any signal count changes vs. baseline

If anything looks wrong, diagnose and either fix (Zone A) or tell Rupert what to re-run (Zone B).

---

## Decision rules during execution (no interrupting Rupert)

| Decision type | Rule |
|---|---|
| Two valid code approaches | Pick the simpler/more conservative one. Document in sprint summary. |
| Test failure with an obvious fix | Fix it, note in summary. |
| Ambiguous acceptance criteria | Use the most literal interpretation. Flag in summary. |
| **Signal logic change** | STOP. Present options to Rupert before proceeding. |
| **Schema change (new column/table)** | STOP. Present migration plan to Rupert before proceeding. |
| **Firing count changes >5%** | STOP. Produce a diff report and present to Rupert. |
| Unknown bug discovered mid-sprint | Fix if safe (Zone A). If it requires Zone B to validate, add to handoff block. |
| New bug found that's out of scope | Note as B-NNN in sprint summary. Do not scope-creep. |

---

## Delivery format (short summary + diff)

```
## Sprint complete — {brief title}

### What changed
[2–5 bullet summary of code changes]

### Test results
[Suite name] — N passed, 0 failed (delta: +N new tests)
[Any regressions: NONE or list them]

### Decisions made autonomously
[Any calls I made without asking — and why]

### Open items (out of scope)
[Any new bugs found, logged as B-NNN]

### Zone B — run these commands
Paste the following into PowerShell in order:

    cd C:\Dev\DirectorsDealings
    python refresh_all.py
    [any other Zone B scripts, in sequence]
    python .scripts/snapshot_db.py

Then paste the output back here and I'll verify.

**Always append `python .scripts/snapshot_db.py` as the LAST command of any
Zone-B block that wrote the DB.** It's read-only and refreshes the text
snapshots in `.data/_snapshots/` that the agents read to verify row counts,
signal changes, and `price_audit` state. Never inspect the DB by copying the
binary — FUSE truncates it.
```

---

## How to invoke this protocol

At the start of a session, Rupert pastes a brief. Format can be loose — a sentence is fine. The Sprint Runner reads all relevant CLAUDE.md rules and MEMORY.md before starting, then runs the loop above.

**Example briefs:**
- "Fix the announced_at date parsing so MTM is correct"
- "Build CAR chart Phase 2 — cohort_performance.json export"
- "Harden the parser against the two-table layout bug"
- "Add T1a signal module for CEO+Founder cohort"

**What the Sprint Runner does NOT do:**
- Merge stages — one sprint, one discrete scope
- Auto-proceed past a STOP decision
- Run Zone B scripts from bash (non-negotiable)
- Touch `.data/` or cache directories directly
