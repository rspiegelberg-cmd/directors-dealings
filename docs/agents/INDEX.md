# Agent team — Directors Dealings project

These are role definitions invoked via the `Agent` tool. Each is a prose system-prompt that a `general-purpose` or `Plan` agent reads into context before doing the work.

**How to invoke:** spawn an `Agent` (subagent_type `general-purpose` for builders, `Plan` for planners), and pass the contents of the relevant `.md` file as the system role inside your prompt — plus the specific task brief.

## The team

| Agent | File | When to invoke |
|---|---|---|
| **Product Manager** | [product-manager.md](product-manager.md) | Spec writing, scope decisions, prioritisation, acceptance criteria |
| **Project Manager** | [project-manager.md](project-manager.md) | Status reports, dependency tracking, gate decisions, schedule keeping |
| **Back-end Engineer** | [backend-engineer.md](backend-engineer.md) | `.scripts/` Python: parsers, scrapers, DB, signal engine, backtest |
| **Front-end Engineer** | [frontend-engineer.md](frontend-engineer.md) | HTML/CSS/JS dashboards in `outputs/`, Tailwind + Chart.js |
| **Dashboard Designer** | [dashboard-designer.md](dashboard-designer.md) | Visual design, info hierarchy, badge palette + sparkline spec, continuous model assessment |
| **DevOps Engineer** | [devops-engineer.md](devops-engineer.md) | Schedules, logging, secrets, runbooks, operational cadence |
| **Token-Efficiency Engineer** | [token-efficiency.md](token-efficiency.md) | LLM prompt audits, cost ceilings, redundant-call detection |
| **QA** | [qa.md](qa.md) | Independent verification, test runs, regression checks, file-integrity gates |
| **Analyst** | [analyst.md](analyst.md) | Signal-performance analysis, cohort cuts, regime checks, hypothesis generation |
| **Trader** | [trader.md](trader.md) | Markets-realism check, taxonomy refinement, risk + sizing critique, UK tax/cost realism |
| **Quant Researcher** | [quant-researcher.md](quant-researcher.md) | New-feature/signal hypotheses from the corpus, signal-taxonomy review for genuine edge, overfitting & out-of-sample robustness, signal-decay/interaction research |
| **Data Integrity Auditor** | [data-integrity-auditor.md](data-integrity-auditor.md) | Field-level audit of `directors.db` vs source Investegate filings; treats DB as a claim, source as ground truth |

## Shared rules (apply to every agent on this project)

1. **Local-only workflow.** No git ceremony, no branches, no PRs unless Rupert explicitly asks.
2. **Plan-first.** Non-trivial changes always start with a written plan; no surprise edits.
3. **Truncation discipline.** The FUSE mount has truncated files mid-edit 8+ times. Every code/doc write over ~100 lines uses bash heredoc; every write is followed by `wc -l` + AST/tail/sha verification. See `memory/feedback_truncation_check_mandatory.md`.
4. **Independent verification.** Builder agents don't grade their own work; QA does a separate pass.
5. **Honesty over polish.** Better to flag a real concern than ship a clean-looking false success.
6. **Snapshot after every pipeline run.** Any Zone-B run that writes the DB (`refresh_all`, `eval_signals --rebuild`, `reparse_corpus`, any `backfill_*`, `run_pending_sweep`, export+build, etc.) MUST be followed by `python .scripts/snapshot_db.py` (Rupert, Windows). It opens `directors.db` read-only and writes TEXT dumps to `.data/_snapshots/` (`*.csv` + `summary.json`). Agents inspect the DB by reading those snapshots — NEVER by `cp`-ing the binary (FUSE serves truncated binary reads). If the snapshot looks stale, ask Rupert to re-run it. See `memory/reference_snapshot_db_for_inspection.md`.

## Project context every agent should know

Working folder: `C:\Dev\DirectorsDealings`. Stages 1–4 complete. Stage 5 (dashboard) in progress. Data state: 2,383 transactions, 1,387 BUYs, 750 signal firings, 146 measurable backtest rows. Sector benchmark currently `^FTAS` across the board (FTSE sub-indices 404 on Yahoo anonymous). Lifetime API spend ~$57.
