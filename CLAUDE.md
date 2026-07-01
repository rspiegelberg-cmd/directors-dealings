# Directors Dealings — project guide for Claude

This file documents project-specific guardrails and the specialist agents available for this codebase. Read this before starting any work on the project.

## Project at a glance

A working dashboard that ingests UK RNS PDMR director-dealing announcements, scores them against a 7-tier signal taxonomy (T1–T4, S1, T0, F1 — see `docs/specs/05-phase-3-signal-engine.md`), and tracks each fired signal's cumulative abnormal return at T+1 / T+21 / T+90 vs a sector-matched benchmark (FTSE All-Share fallback), net of 50bps spread + 0.5% stamp duty on non-AIM buys.

The original build ran as 5 staged stages (foundation → RNS feed → prices → signal engine → dashboard), all complete. The project has since **migrated to a cloud backend** (see below).

## Backend & how it runs now (cloud — migrated 2026-06-25)

The project moved off the local SQLite + FUSE regime. The data of record now lives in **Supabase Postgres**, and the live site reads it directly in the browser.

**Where things live now**

- **Database:** Supabase Postgres — project `directors-dealings`, ref `mmiaiauybzsdcbrrcxfc`, host `db.mmiaiauybzsdcbrrcxfc.supabase.co`, region eu-west-1, Postgres 17. **This is the single source of truth.** Driveable directly via the connected Supabase connector.
- **Site:** https://directors-dealings.vercel.app/ (Vercel) plus a GitHub Pages twin. The front page and `company.html` fetch Supabase **directly in the browser** (publishable/anon key only) — no pipeline step renders them, so they are current the moment an upload finishes.
- **Code:** public GitHub repo `rspiegelberg-cmd/directors-dealings`.
- **Local `.data/directors.db`:** archived cold backup only — **no longer the working DB.** Do not treat it as live data.

**`db.py` is dual-backend** (B-176). It picks the backend at runtime from env vars:

- `DD_DATABASE_URL` set → Postgres via psycopg v3 (the cloud path).
- `DD_DATABASE_URL` unset → local SQLite (`.data/directors.db`) — used for local dev and, importantly, as the runner-local compute copy inside CI.
- `DD_FORCE_SQLITE=1` forces the SQLite path even when `DD_DATABASE_URL` is set. CI sets this so the heavy compute runs against the fast local copy, then uploads.

**The daily refresh** runs entirely in GitHub Actions — PC off — via `.github/workflows/daily-refresh.yml` (06:00 UTC schedule + manual *Run workflow*, and the site's ↻ Refresh button). The flow (B-194) is download → compute-local → upload, because computing directly over the network to eu-west-1 timed out at 45–60 min:

1. `download_from_postgres.py` — Supabase → a fresh local SQLite on the runner.
2. `refresh_all.py` with `DD_FORCE_SQLITE=1` — full pipeline (scrape → signals → backtest → build) against that local copy (~6 min).
3. `migrate_to_postgres.py` — upload changed tables back to Supabase, **gated on pipeline success** so a bad run never overwrites good data.
4. Commit the rebuilt static pages; Vercel redeploys.

**Inspecting the data (Claude):** use the connected **Supabase connector** (read-only `execute_sql` / `list_tables`) against Postgres. The old `snapshot_db.py` text-dump dance was a FUSE workaround for the local SQLite and is retired.

**Editing code (Claude):** code files still live on the FUSE-mounted Windows folder, so one FUSE quirk survives — after an Edit/Write, Claude's bash sandbox can serve a **stale cached view** of that file for a few minutes. The **Read tool bypasses FUSE and is ground truth** — always verify edits with Read, not bash `cat`/`wc`. (The old binary-DB-corruption and two-zone rules are gone: nothing Claude touches writes the live DB anymore — the data is in Postgres and the pipeline runs in CI.)

---

## Working rules (memory-derived — see `MEMORY.md`)

- **Trunk-based, no PR ceremony.** Commits go straight to `main`; no branches or PRs unless Rupert explicitly asks. Pushing to GitHub is what deploys the site.
- **Plan-first.** For any non-trivial change, produce a written plan first (Plan agent), then build, then independent verification.
- **Truncation check is mandatory after every code write.** Use the **Read tool** (not bash) to verify. Check: (a) line count matches expectation, (b) tail of file is complete, (c) key logic is present. (See the FUSE bash-cache note above for why bash can disagree.)
- **Deploy specialist agents proactively.** Don't do everything in the main thread when a sub-agent is the right tool.
- **Inspect data read-only via the Supabase connector**, never by copying or opening a local DB file. The live data is in Postgres.

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
- `docs/specs/cloud-migration-sprint-tracker.md` — the cloud migration record (M0–M6 + DR) and current live status
- `docs/specs/HOW-IT-RUNS-NOW.md` — one-page operator guide to the cloud pipeline
- **Working folder on disk: `C:\Dev\DirectorsDealings`** (single source of truth for code — do not use any other path)

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
