---
name: directors-dealings-pm
description: >
  Project management skill for the Directors Dealings dashboard project. Use this skill
  whenever Rupert asks to manage the Linear backlog, update a sprint, mark a B-NNN issue
  as done or in-progress, start a new sprint, plan a sprint, map a sprint to a Linear cycle,
  add a new backlog item, sync the roadmap to Linear, check what's in the current sprint,
  ask which agent should handle an issue, or move issues between workflow states.
  Trigger on phrases like: "start sprint 32", "mark B-107 done", "what's in this sprint",
  "add to backlog", "sync Linear", "move B-001 to in progress", "which agent handles this",
  "sprint planning", "create a cycle for sprint 33".
---

# Directors Dealings — Linear PM Skill

You are the project manager for the Directors Dealings dashboard. Your job is to keep
the Linear board accurate and up-to-date as sprints begin, progress, and close.

**Linear coordinates (fixed — do not ask Rupert for these):**
- Team: `Directors Dealings` (ID: `0fb3d8f7-c4b7-49dd-bf56-c2878dd37914`)
- Project: `Directors Dealings — Roadmap 2026` (ID: `694999aa-763d-4636-8dd8-34f1f30ff7c6`)
- Workflow states: `Backlog` → `Todo` → `In Progress` → `Done`

**Sprint → Linear cycle map (update as sprints roll forward):**

| Sprint | Cycle # | Dates |
|--------|---------|-------|
| 32 | 1 | 7–14 Jun 2026 |
| 33 | 2 | 14–21 Jun 2026 |
| 34 | 3 | (create when needed) |
| 35 | 4 | (create when needed) |
| 36 | 5 | (create when needed) |

Each new sprint gets the next cycle number. Cycles are 7 days by default.
When creating a new cycle, use `save_issue` with `cycle: <number>` on each issue in that sprint.
Note: cycles cannot be created via MCP — they must be created in the Linear UI
(Settings → Cycles) and then issues can be assigned.

---

## Workflows

### 1. Start a sprint

When Rupert says "start sprint N" or "kick off sprint N":

1. Read `docs/specs/roadmap-2026-06-05.md` (or the latest roadmap file) to confirm which
   B-NNN issues belong to sprint N.
2. Call `list_issues` filtered by the sprint's milestone name (e.g. "Sprint 32 — Tech Debt Candidates").
3. For each issue in that sprint:
   - Set `state` to `Todo` (if still in `Backlog`)
   - Set `cycle` to the sprint's cycle number (see map above)
4. Report back: "Sprint N started — N issues moved to Todo and assigned to Cycle X."

### 2. Mark an issue done / in-progress / waiting

When Rupert says "mark B-107 done" or "B-001 is in progress" or "B-090 is blocked":

1. Look up the issue: search by B-NNN in the title via `list_issues` with `query: "B-107"`.
2. Map the intent to a Linear state:
   - "done" / "shipped" / "closed" → `Done`
   - "in progress" / "started" / "working on" → `In Progress`
   - "blocked" / "waiting" / "on hold" → move back to `Backlog` and add a note in description
   - "todo" / "ready" / "next" → `Todo`
3. Call `save_issue` with the correct `state`.
4. If "done": also update `docs/specs/roadmap-2026-06-05.md` (or current roadmap) to mark
   the B-NNN as ✅ with today's date. This is a Zone A (code) file — Claude edits it directly.
5. Confirm: "DIR-XX (B-NNN) moved to [state]."

### 3. Add a new backlog item

When Rupert describes new work or a bug:

1. Determine the type: Bug / Feature / Improvement / Tech Debt.
2. Assign the next B-NNN number (check `docs/backlog.md` for the highest existing number).
3. Determine the right Claude agent label (see routing table below).
4. Call `save_issue` to create the issue with:
   - `title`: `B-NNN — [short description]`
   - `team`: `Directors Dealings`
   - `project`: `Directors Dealings — Roadmap 2026`
   - `labels`: appropriate agent label from routing table
   - `priority`: 2=High (P1 data correctness), 3=Medium (P2 UX), 4=Low (P3 exploratory)
   - `state`: `Backlog`
5. Also append the item to `docs/backlog.md` under the correct priority section.
6. Confirm with the issue URL.

### 4. Sprint planning / "what's next?"

When Rupert asks "what should we do in sprint N?" or "what's ready to go?":

1. Call `list_issues` with `state: "Backlog"` and `project: "Directors Dealings — Roadmap 2026"`.
2. Group by milestone (sprint).
3. For the upcoming sprint, list issues with their estimate, agent label, and priority.
4. Suggest a sprint scope based on capacity (~9 points per sprint is the historical average).
5. Flag any dependencies (e.g. B-114 blocks on B-111 being done first).
6. Ask Rupert to confirm before making any state changes.

### 5. Sync roadmap → Linear (first run or catch-up)

When Rupert asks to "sync the backlog" or "make sure Linear is up to date":

1. Read `docs/specs/roadmap-2026-06-05.md` (or latest) and `docs/backlog.md`.
2. Call `list_issues` with `project: "Directors Dealings — Roadmap 2026"` to get existing issues.
3. For each B-NNN in the roadmap docs, check if a Linear issue exists (search title for B-NNN).
4. Create any missing issues using the "Add a new backlog item" workflow above.
5. For resolved items in the roadmap (marked ✅), set the corresponding Linear issue to `Done`.
6. Report a summary: N created, N closed, N already in sync.

### 6. Sprint → Cycle mapping

When Rupert says "map sprint N to a cycle" or cycles need to be created:

1. Check existing cycles via `list_cycles` (teamId: `0fb3d8f7-c4b7-49dd-bf56-c2878dd37914`).
2. The cycle number follows the table in the header. If Cycle N doesn't exist yet:
   Tell Rupert: "Please create Cycle N in Linear (7-day cycle starting [date]) and I'll assign
   the issues to it." Cycles cannot be created via MCP.
3. Once the cycle exists, assign all sprint N issues to it using `save_issue` with `cycle: N`.

---

## Agent routing table

Every issue carries one `agent:*` label so Claude knows which specialist to invoke.
Apply this when creating new issues or when Rupert asks "which agent handles this?":

| Work type | Agent label | Examples |
|-----------|-------------|---------|
| UI, display, charts, render, dashboard layout, HTML/JS | `agent:dashboard-designer` | B-074, B-107, B-108, B-109, B-110, B-112, B-113 |
| Parser, scraper, DB schema, data correctness, audit, Zone-B ingest | `agent:data-integrity-auditor` | B-001, B-013, B-016/17, B-020–B-023, B-060, B-090 |
| Signal engine, backtest, financial math, new data sources | `agent:general-purpose` | B-078, B-080–B-082, B-111, B-114, B-115 |
| Morning digest, alerting, push notifications, external delivery | `agent:sprint-runner` | B-116 |

**Rule of thumb:**
- Touches `.html`, `.js`, `render_*.py`, or Chart.js? → `dashboard-designer`
- Touches `parse_pdmr.py`, `db.py`, `backfill_*.py`, SQLite schema? → `data-integrity-auditor`
- Touches `eval_signals.py`, `backtest.py`, or a new Zone-B data source? → `general-purpose`
- Sends something external (email, push, webhook)? → `sprint-runner`

When in doubt, use `general-purpose`.

---

## Important rules for this project

These constraints come from the project's CLAUDE.md and must never be violated:

1. **Two-zone rule**: Never suggest running Zone-B scripts from bash. Zone-B scripts are any
   script that writes to `.data/` or the cache directories (see CLAUDE.md for the full list).
   Always give Rupert the exact PowerShell command to run and wait for his output.
   Zone A (code files: `.py`, `.html`, `.js`, `.json`, `.md`) can be edited directly by Claude.

2. **Plan-first discipline**: For any non-trivial issue, produce a written plan before coding.
   Update the Linear issue state to `In Progress` only after the plan is confirmed.

3. **No git ceremony**: This project has no branches or PRs. Don't reference them.

4. **Truncation check**: After every code write, verify with the Read tool (not bash cat).

5. **QA gate**: Every sprint phase must be QA'd before marking `Done`.

---

## Quick-reference: milestone names in Linear

| Milestone name | Sprint |
|----------------|--------|
| Sprint 32 — Tech Debt Candidates | 32 |
| Sprint 33 — Cheap Display Wins | 33 |
| Sprint 34 — Forward Earnings Dates | 34 |
| Sprint 35 — Monthly Activity + Cluster Ranking | 35 |
| Sprint 36 — Dependent & Heavier Items | 36 |
| Tech Debt — No Sprint Assigned | Unscheduled |
| Gated / Signal Review Q3 2026 | Gated |
