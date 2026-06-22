# Cloud Migration — Execution Plan (work breakdown)

**Status:** Planned / not started. **Date:** 2026-06-22.
**Companion docs:** `cloud-migration-plan.md` (phased plan), `cloud-migration-adr.md` (architecture rationale), `cloud-migration-sprint-tracker.md` (status board).

This is the detailed, buildable breakdown. Each task names an **owner**, **acceptance criteria**, and **effort**. Sprints and a status board live in the tracker doc; this doc is the "how".

---

## Adopted decisions (from "based on your recommendations" — override any of these by saying so)

1. **Rendering:** dynamic `company.html` template + read-only client fetch from Supabase. Combined pages (`index`, `performance`) stay static. (ADR §2a)
2. **Repo stays public** — keeps GitHub Actions minutes free; secrets live in encrypted Actions Secrets regardless.
3. **Review app (`server.py`)** runs locally against the cloud DB when needed; not rebuilt as a hosted page. Deferrable — does not block any phase.
4. **Driver:** `psycopg` v3 with `dict_row`; **not** `supabase-py` for Python. Supabase JS client is used only for the front-end read.
5. **Scope:** pipeline-critical scripts ported first; diagnostics/one-off backfills ported lazily.
6. **One database, two access paths** — same Supabase Postgres serves the site and ad-hoc research; no separate analytics store.

## Owner key

- **[R]** = Rupert does it (account setup, anything that writes data from the local PC, git push, Zone-B runs).
- **[C]** = Claude writes/edits code (Zone-A) or runs read-only verification.
- **[C-mcp]** = Claude can drive directly via the connected Supabase / Vercel / GitHub MCPs in this session (e.g. apply schema, run SQL, create deploy) — reduces manual steps. Rupert still owns account-level auth.

---

## Prerequisites (before Sprint M0) — [R], ~30–45 min

| # | Task | Owner | Acceptance |
|---|------|-------|-----------|
| P1 | Create a Supabase account + organisation (free tier). Note: 2 free projects max. | R | Logged in; org exists |
| P2 | Create a Vercel account (Hobby), connect it to the GitHub repo. | R | GitHub repo visible in Vercel |
| P3 | Confirm repo stays **public**. | R | Confirmed |
| P4 | Locally: `pip install "psycopg[binary]"`. | R | Imports cleanly in Windows Python |
| P5 | Have the FMP API key (`DD_FMP_API_KEY`) handy for later secrets setup. | R | Key located |

*Once P1–P2 are done, share access so the connected Supabase/Vercel MCPs point at the right project (or Claude creates the project via `[C-mcp]`).*

---

## Sprint M0 — De-risk spike (Phase 0). **Gate decides whether "fully hands-off" is reachable.**

| B-ID | Task | Owner | Acceptance | Pts |
|------|------|-------|-----------|-----|
| **B-172** | **Supabase connectivity + analysis-path spike.** Create a throwaway project; from Windows Python, `psycopg`-connect, create a 2-column table, insert + select. Then run the same `SELECT` in the Supabase browser SQL editor. | R runs / C writes / C-mcp can create project | Round-trip works from the PC **and** the browser SQL editor returns the row (proves the data-analysis path). | 2 |
| **B-173** | **Cloud-IP scraper spike (THE GATE).** Minimal GitHub Actions workflow that runs `scrape_investegate` for a small recent window **and** a Yahoo price fetch for ~5 tickers, printing results — **no DB writes.** | C writes / R pushes | Runner fetches real filings + prices with no 403/block/throttle. Result recorded. | 3 |
| **B-174** | **Record Phase 0 gate decision.** If green → proceed as planned. If blocked → switch target to "pipeline stays local, writes to Supabase" (still kills FUSE + gives anywhere-access) and annotate downstream sprints. | C | Decision written into the tracker; downstream sprints adjusted if needed. | 1 |

**M0 gate:** B-173 green (or fallback target chosen). Nothing else starts until this is decided.

---

## Sprint M1 — Database foundation on Supabase (Phase 1a)

| B-ID | Task | Owner | Acceptance | Pts |
|------|------|-------|-----------|-----|
| **B-175** | **Port schema → Postgres.** Translate `db_schema.sql` + the chained migrations to a Postgres schema. Critical: add **explicit `UNIQUE` constraints** for every natural key the upserts rely on (SQLite `INSERT OR REPLACE` uses the PK implicitly; Postgres `ON CONFLICT` needs a named constraint). Drop `PRAGMA`; `AUTOINCREMENT`→`IDENTITY`. Carry existing indexes + add `transactions(role_normalized, type, date)` and `signals(signal_id, fired_at)`. | C / C-mcp applies via `apply_migration` | Schema applies cleanly to Supabase; every upsert target has a unique constraint; indexes present. | 3 |
| **B-176** | **Rewrite `db.py`.** `psycopg` v3 `connect()` reading a DSN env var; `dict_row` row factory (the `sqlite3.Row` shim so `r["col"]` works everywhere); split `executescript` migrations into per-statement execution; keep public function names. Add a **dual-backend switch** (sqlite when no Postgres DSN) so the PC stays a dev mirror and tests keep their fast sqlite path. | C | `db.connect()` returns a working Postgres connection; `set_meta`/`get_meta`/`migrate`/`upsert_transaction` work against Supabase; sqlite path still works. | 3 |
| **B-177** | **SQLite → Postgres data migrator.** Script that reads each table from the local `directors.db` and bulk-loads to Supabase (use `COPY` for the big `prices` table). Excludes the 105 MB pre-dedup backup. | C writes / R runs (reads local PC binary) | Row counts per table match SQLite exactly; spot-check 5 rows per key table; `summary.json` parity. | 3 |
| **B-178** | **Serving view + RLS.** Create `public_company_v` (pre-joined per-company shape the template needs) and read-only RLS policies allowing the anon role to `SELECT` the serving view only. | C / C-mcp via `execute_sql` | View returns correct shape for a sample ticker; anon key can read the view, cannot read/write raw tables. | 2 |

**M1 gate:** data in Supabase with row-count parity; `db.py` works against the cloud DB.

---

## Sprint M2 — Pipeline dialect port + verification (Phase 1b)

| B-ID | Task | Owner | Acceptance | Pts |
|------|------|-------|-----------|-----|
| **B-179** | **Port pipeline-critical scripts** to Postgres dialect: `scrape`/`backfill_filings`, `parse_pdmr`, `eval_signals`, `backtest`, `detect_clusters`, `export_dashboard_json`, `build_dashboard`, `render_*`, `close_paper_trades`. Changes: `?`→`%s`, `INSERT OR REPLACE/IGNORE`→`ON CONFLICT … DO UPDATE/NOTHING`, `sqlite3.Row.keys()` patterns, remove `sqlite_master`/`PRAGMA`. | C | Each script runs against Supabase without dialect errors. | 5 |
| **B-180** | **Idempotency + parity verification.** Run the full pipeline locally pointed at Supabase, twice in a row. | R runs / C verifies read-only | Second run produces **zero net changes** (idempotency smoke test); dashboard output / DB counts match the current local build. | 2 |
| **B-181** | **Test-suite strategy.** Keep the fast sqlite path for the existing `unittest discover` suite; add a small Postgres integration smoke (connect, upsert, ON CONFLICT, view read). | C / R runs full sweep on Windows | Existing suite stays green on sqlite; pg smoke passes against Supabase. | 2 |

**M2 gate (the big one):** pipeline runs end-to-end on Supabase with parity + idempotency; **FUSE guardrails are now obsolete in principle.** Local system still intact as fallback.

---

## Sprint M3 — Hosting + dynamic template (Phase 2, two gates)

| B-ID | Task | Owner | Acceptance | Pts |
|------|------|-------|-----------|-----|
| **B-182** | **Vercel lift-and-shift (Gate 2a).** Point Vercel at the repo, serve existing static `outputs/` unchanged. GitHub Pages stays as fallback. Optional custom domain. | C-mcp via Vercel MCP / R confirms | Live dashboard served by Vercel, reachable from any device; matches current site. | 2 |
| **B-183** | **Dynamic `company.html` template.** One template + Supabase JS client (anon key, read-only via RLS) querying `public_company_v` by ticker. Renders a light shell + one read. | C (dashboard-designer) | Opening `/company?ticker=BARC` renders live from Supabase; page weight far below today's 300–550 KB. | 5 |
| **B-184** | **Cut over + clean up (Gate 2b).** Rewire `index`/links to the template; delete the 880 static company files; **remove `pending_review.json` (5 MB) from the public bundle.** | C | 880 files gone; bundle collapses from 48 MB to ~one template; review queue no longer web-served; links work. | 2 |

**M3 gates:** 2a static-live, then 2b template-live.

---

## Sprint M4 — Cloud pipeline on GitHub Actions (Phase 3)

| B-ID | Task | Owner | Acceptance | Pts |
|------|------|-------|-----------|-----|
| **B-185** | **Daily cron workflow.** `schedule` ~06:00 UTC: checkout → setup-python → `pip install` → run incremental refresh against Supabase → rebuild combined static pages → POST Vercel **Deploy Hook**. Concurrency guard so runs can't overlap. | C writes / R pushes | Manual `workflow_dispatch` run completes green end-to-end; site redeploys. | 3 |
| **B-186** | **Make the refresh incremental.** Rolling-window daily scrape (full re-scrape becomes a manual backfill); run `eval_signals`/`backtest` over affected fingerprints (`--from/--to`), not `--rebuild`; emit a run-summary (rows added, signals fired, pages built). | C | Daily run touches only changed data; run-summary printed; idempotent on re-run. | 3 |
| **B-187** | **Secrets.** Supabase write DSN, `DD_FMP_API_KEY`, Vercel deploy hook → Actions Secrets; front-end env holds only the Supabase **anon** key. | R sets secrets / C wires | Workflow reads secrets; no secret in logs; front-end never holds the service-role key. | 1 |

**M4 gate:** a full daily refresh runs automatically with the PC switched off, and the live site updates.

---

## Sprint M5 — Decommission + docs (Phase 4)

| B-ID | Task | Owner | Acceptance | Pts |
|------|------|-------|-----------|-----|
| **B-188** | **Retire FUSE regime.** Remove the FUSE-corruption / two-zone section from `CLAUDE.md`; document the new backend (`db.py` DSN, dev-mirror usage); update working rules. | C | CLAUDE.md reflects cloud reality; no stale FUSE rules. | 2 |
| **B-189** | **Archive + deploy docs.** Archive local `directors.db` as a cold backup; update/retire `push_to_github.bat`, `start.bat`, snapshot scripts that assume local SQLite. | C / R archives the file | Old artefacts archived or updated; a short "how it runs now" note exists. | 1 |

**M5 gate:** docs updated; running fully in the cloud; local PC is an optional dev mirror.

---

## Dependencies (what blocks what)

```
M0 (B-173 gate) ──► M1 ──► M2 ──► M3 ──► M4 ──► M5
                     │
   B-175 ─► B-176 ─► B-177 ─► B-178      (within M1, in order)
   B-179 ─► B-180 ─► B-181               (within M2)
   B-182 ─► B-183 ─► B-184               (within M3; 2a before 2b)
   B-185 ─► B-186 ─► B-187               (within M4)
```

The whole chain is gated on **B-173** (cloud scraping). Everything is **additive** — the existing local SQLite + GitHub Pages system keeps working untouched until **M5**, so any phase can be paused or rolled back by simply not advancing.

## Effort & sequencing (rough, session-sized — not calendar dates)

| Sprint | Theme | Pts | Rough effort |
|--------|-------|-----|--------------|
| M0 | De-risk spike | 6 | ½–1 day |
| M1 | DB foundation | 11 | 2 focused sessions |
| M2 | Pipeline port + verify | 9 | 2 focused sessions (the surprises live here) |
| M3 | Hosting + template | 9 | 1–2 sessions |
| M4 | Cloud pipeline | 7 | 1 session |
| M5 | Decommission | 3 | ½ day |

Total ≈ **45 pts / ~6 sprints**. M1+M2 (the SQLite→Postgres port) is the real bulk; everything else is lighter.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Cloud-IP scraping blocked from Actions | Medium | High | **B-173 gate before any other work.** Fallback: pipeline stays local, writes to Supabase. |
| Upsert breaks — missing unique constraint after port | Medium | High | B-175 explicitly adds named UNIQUE constraints for every `ON CONFLICT` target; B-180 idempotency test catches it. |
| Data migration type coercion (TEXT dates, numerics) | Low-Med | Medium | B-177 row-count + spot-check parity; dates stay ISO-8601 TEXT (portable). |
| Test suite assumes SQLite | High (expected) | Low | B-176 dual-backend switch keeps the fast sqlite path; B-181 adds a pg smoke. |
| Supabase 500 MB cap as data grows | Low (years away) | Medium | Don't migrate pre-dedup backup; prune `prices` history; monitor DB size (see plan §4a). |
| Changing too much at once | Medium | High | Strict gates; lift-and-shift before template (M3); never combine DB + host + render in one step. |

## Verification strategy (per gate)

Every gate requires: (1) the acceptance criteria above met; (2) the **idempotency smoke test** (pipeline runnable twice, second run = zero net change) from M2 onward; (3) row-count parity vs the current local DB; (4) the existing `unittest discover` sweep green on the sqlite dev path. High-stakes gates (M2, M4) get an independent QA pass by a specialist agent before being marked Done — per the project's standing QA-gate rule.
