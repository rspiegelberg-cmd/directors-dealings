# Directors Dealings — project guide for Claude

This file documents project-specific guardrails and the specialist agents available for this codebase. Read this before starting any work on the project.

## Project at a glance

A working dashboard that ingests UK RNS PDMR director-dealing announcements, scores them against a 7-tier signal taxonomy (T1–T4, S1, T0, F1 — see `docs/specs/05-phase-3-signal-engine.md`), and tracks each fired signal's cumulative abnormal return at T+1 / T+21 / T+90 vs a sector-matched benchmark (FTSE All-Share fallback), net of 50bps spread + 0.5% stamp duty on non-AIM buys.

Build approach: **5 staged stages with manual gates between each**. Do not auto-proceed past a stage gate. Stages:

1. Foundation — schema + `.scripts/db.py`
2. RNS feed — Investegate scraper + PDMR parser
3. Prices — Yahoo OHLCV backfill for transaction tickers + sector benchmarks
4. Signal engine — 7-signal compute + lookahead-bias test (P3-6, non-negotiable)
5. **Dashboard** — Active-clusters panel + dealings table with signal badges + performance tracker

## ⚠️ FUSE CORRUPTION — PERMANENT RULES (read every session)

**Root cause:** Claude's Linux sandbox accesses `C:\Dev\DirectorsDealings` via a FUSE mount.
FUSE truncates non-sequential binary writes to large files. SQLite writes non-sequentially → corruption.

### Two-zone rule (MANDATORY — never violate this)

| Zone | What lives here | Who writes |
|------|----------------|------------|
| **A — Code** | `.py`, `.html`, `.js`, `.json`, `.md`, `.bat` | Claude (Edit/Write tools) — safe, text files only |
| **B — Data** | `.data/directors.db`, `.data/*.json`, `.scripts/_price_cache/`, `.scripts/_scrape_cache/` | **Windows Python only** — never Claude bash |

**Claude must NEVER (write-path scripts — Rupert runs these from PowerShell):**
- `refresh_all.py`
- `backfill_filings.py`, `backfill_prices.py`, `backfill_announced_at.py`
- `run_scrape.py`
- `repair_dates.py`
- `eval_signals.py`
- `build_dashboard.py`
- `backtest.py`
- `run_pending_sweep.py` (writes DB + triggers eval + build)
- `exclude_investment_trusts.py` (Sprint 2, when built)
- `reparse_corpus.py` (Sprint 3, when built)
- ANY use of bash to write a file under `.data/` or `.scripts/_*_cache/`
- Opening `directors.db` for writing through bash

For these scripts: paste the exact command for Rupert to run, wait
for the output, then continue. Do not propose "I'll just run it."

**Claude CAN safely (run from bash, do not ask Rupert):**

Rupert's standing instruction (2026-05-18): **take on more of the
verification cycle**. The list below doesn't touch `.data/` or the
cache directories, so it's safe in Claude's Linux sandbox. Default
behaviour: **run these and report results**, rather than pasting
PowerShell commands for Rupert to run.

- `python .scripts/test_sparkline.py` — no DB
- `python .scripts/test_repair_dates_atomicity.py` — uses tempfile,
  mocks the DB path; safe on Linux
- `python .scripts/test_stage_02.py`, `test_stage_04.py`,
  `test_stage_05.py`, `test_p3_lookahead.py`, `test_db_smoke.py` —
  older suites; running them is how we triage B-012
- `python -m unittest discover -s .scripts -p "test_*.py"` — full sweep
- Any standalone Python script Claude writes for one-off diagnostics
  that does not write under `.data/` or the cache dirs
- **Read-only DB inspection — PREFERRED method: text snapshots.**
  `cp .data/directors.db /tmp` is NO LONGER reliable — FUSE has been
  observed serving a *truncated* binary read (header page-count >
  actual file length → "database disk image is malformed"), even
  though the write side is fine (verified 2026-06-05). Instead, to
  audit the DB from the sandbox: ask Rupert to run
  `python .scripts/snapshot_db.py` (Windows, strictly read-only,
  opens the DB `mode=ro`, writes TEXT only). Then read the snapshots
  from `.data/_snapshots/` (`transactions.csv`, `signals.csv`,
  `tickers_meta.csv`, `prices_coverage.csv`, `summary.json`). Text
  crosses the FUSE bridge cleanly; the binary does not. Use `--full`
  for the whole prices table.
- Copying TEXT files to `/tmp/` for inspection (binary DB copies may
  truncate — see above)
- Using the Read tool on any path (Read bypasses FUSE — Windows direct)
- Editing code files (Zone A) freely with Edit/Write

**Path translation for bash:** the workspace is mounted at
`/sessions/<session-id>/mnt/DirectorsDealings/`. Use `cd` into that
path or use absolute paths starting with `/sessions/...`. The
mapping table in the system prompt has the full translation.

**Why this isn't "run everything from bash":** FUSE truncates
non-sequential binary writes. SQLite writes non-sequentially → real
corruption (this project has lost data to FUSE four times). Running
a script that opens `directors.db` for writing — even read-mostly
ones like `audit_dates.py` if not isolated to /tmp — risks a
corruption event. The list above is the audited-safe set.

### FUSE bash-cache staleness on Zone A files (discovered 2026-05-18)

A separate FUSE quirk affects even safe Zone A (code) files: after
Claude edits a file via the Edit / Write tool, Claude's Linux bash
sandbox may keep reading an older cached version for several
minutes. The Read tool bypasses FUSE (Windows-direct) and shows
ground truth. **Symptom:** Python tests fail with SyntaxError on a
line Claude just fixed, and `wc -l` from bash shows fewer lines than
the Read tool reports.

**Workaround (for running tests in this state):**
1. Verify the file is correct via the Read tool.
2. Reconstruct the file in `/tmp/` via a heredoc using content from
   the Read tool — do NOT `cp` from the FUSE mount, which itself
   serves the stale view.
3. Also copy the project's `.scripts/db_schema.sql` and
   `.scripts/schema_migrations/*.sql` into the `/tmp/` mirror so
   `db.connect()` works (the schema files don't drift, so a regular
   `cp` is fine for these).
4. Run Python against the `/tmp/` mirror.

This adds ~30 seconds of friction per iteration. For a first-pass
test run that passes, the regular bash path works fine. Only fall
back to the /tmp dance when a test result looks anomalous and the
Read tool disagrees with what bash sees.

**Verification after code edits:** Always use the **Read tool** (not bash `cat`/`wc`) to verify file integrity. The FUSE mount may show a stale/truncated view in bash. The Read tool accesses Windows files directly and is the ground truth.

**Self-healing backup:** `directors.db` now backs itself up to `.data/directors.db.bak` after every successful pipeline run. `start.bat` restores from backup if the primary DB fails integrity check. See `.scripts/db_health.py`.

---

## Working rules (memory-derived — see `MEMORY.md`)

- **Local-only workflow.** No git ceremony, no branches, no PRs unless Rupert explicitly asks.
- **Plan-first.** For any non-trivial change, produce a written plan first (Plan agent), then build, then independent verification.
- **Truncation check is mandatory after every code write.** Use the **Read tool** (not bash) to verify. Check: (a) line count matches expectation, (b) tail of file is complete, (c) key logic is present.
- **Deploy specialist agents proactively.** Don't do everything in the main thread when a sub-agent is the right tool.
- **Snapshot the DB after every pipeline run.** Any Zone-B run that writes the DB (`refresh_all`, `eval_signals --rebuild`, `reparse_corpus`, `backfill_*`, `run_pending_sweep`, export+build, etc.) must end with `python .scripts/snapshot_db.py` (Rupert, Windows — read-only, writes TEXT to `.data/_snapshots/`). Claude/agents inspect the DB by reading those snapshots, never by `cp`-ing the binary (FUSE truncates binary reads). Paste the snapshot step into every Zone-B command block.

## Specialist agents for this project

Agent definitions live in `docs/agents/`. They're prose system prompts you (Claude) load when delegating a slice of work — typically by invoking the `general-purpose` agent and pasting the relevant agent definition into the prompt as the role to play.

### dashboard-designer

**File:** `docs/agents/dashboard-designer.md`

**When to invoke.** Any visual-design question on the dashboard surface — layouts, information hierarchy, signal-tier visual language, performance-tracker UI, mobile breakpoints, chart selection, colour systems, design specs, or critique of an existing mock / running page.

**When NOT to invoke.** Backend / data-model work, signal-engine math, scraping, performance calculations, or production HTML/JS implementation. Use Plan or general-purpose for those, with the designer's spec as input.

**How to invoke from a session.** Either:

- Read `docs/agents/dashboard-designer.md` into context yourself, then act in that role for the slice of work, OR
- Spawn an `Agent` (subagent_type `general-purpose`) and pass the contents of `docs/agents/dashboard-designer.md` as the system role inside the prompt, plus the specific design brief.

The designer's deliverable is a **design spec**, not the production implementation — wireframe + structured spec + Tailwind/Chart.js snippets for the tricky bits + data fields required from upstream + what's deliberately out of scope.

## Where to put files

- Specs → `docs/specs/`
- Agent definitions → `docs/agents/`
- Stage plans → `docs/specs/stage-NN-plan.md`
- Production code per spec 03 schema convention (`.data/`, `.scripts/`)

## Reference

- `docs/specs/05-phase-3-signal-engine.md` — the 7-signal taxonomy (source of truth for tier visual hierarchy)
- `docs/specs/03-phase-1-backfill-storage.md` — DB schema (source of truth for data shapes the dashboard reads)
- `docs/specs/stage-01-plan.md` — Stage 1 plan template; later stages follow the same pattern
- **Working folder on disk: `C:\Dev\DirectorsDealings`** (single source of truth — do not use any other path)
## Knowledge Base — Claude Brain
Rupert keeps a persistent second-brain wiki at: C:\Dev\Claude Brain\Claude Brain

When you need background you don't already have in THIS project — about Rupert, his other
projects, past decisions, investments, people, or recurring build-environment gotchas —
read the brain in this order and stop as soon as you have enough:
1. wiki/hot.md                  — recent context (~500 words)
2. wiki/index.md                — full catalogue of pages
3. wiki/<folder>/_index.md then the specific page(s) you need (3-5 max)

Cite brain pages by name when you use them. Keep it lean: do NOT read the brain for routine
work already covered by this project's own files, or for tasks unrelated to its knowledge.
If the brain folder isn't accessible in this session, carry on without it.
After a meaningful decision or finding here, consider noting it for the brain (tell Rupert).
