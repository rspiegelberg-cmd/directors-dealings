# Cloud Migration — Sprint Tracker

**Living status board.** Detail for every B-NNN is in `cloud-migration-execution-plan.md`. Update the **Status** column as work moves; mark gates ✅ when passed. Issues continue the project's B-NNN sequence (next free = **B-172**) and carry the usual agent labels for Linear.

**Status legend:** ⬜ Todo · 🟦 In Progress · ✅ Done · ⛔ Blocked · ⏸ Deferred
**Overall:** 🟦 Started — Linear issues created (DIR-101→118); M0 spike code written — 0 / 18 done · 0 / 45 pts · awaiting **Prerequisites** + **M0 gate run on GitHub**
**Linear:** synced 2026-06-22 — 6 milestones + 18 issues. B-NNN→DIR map at the bottom.

> **Critical path:** everything is gated on **B-173** (does cloud-IP scraping work from GitHub Actions?). The existing local SQLite + GitHub Pages system stays fully working until **M5**, so this can pause or roll back at any gate.

---

## Prerequisites (Rupert)

| Status | Item |
|--------|------|
| ⬜ | P1 Supabase account + org (free) |
| ⬜ | P2 Vercel account (Hobby) linked to GitHub repo |
| ⬜ | P3 Confirm repo stays public |
| ⬜ | P4 `pip install "psycopg[binary]"` locally |
| ⬜ | P5 FMP API key located for later |

---

## Sprint M0 — De-risk spike — *Linear milestone: "Cloud Migration — M0 Spike"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| 🟦 | B-172 | Supabase connectivity + analysis-path spike | `agent:data-integrity-auditor` | P1 | 2 |
| 🟦 | B-173 | **Cloud-IP scraper spike (GATE)** | `agent:general-purpose` | P1 | 3 |
| ⬜ | B-174 | Record Phase 0 gate decision / adjust target | `agent:general-purpose` | P1 | 1 |

**M0 kicked off 2026-06-22:** spike code written — `.scripts/spike_cloud_scrape.py` (B-173) + `.github/workflows/spike-cloud-scrape.yml` + `.scripts/spike_supabase_conn.py` (B-172). Local smoke run PASSED (30 Investegate rows, 5/5 Yahoo) — but that's the *sandbox* IP, **not** GitHub's. Gate is undecided until the workflow runs on GitHub Actions.

**Gate M0:** ⬜ B-173 green **on a GitHub Actions runner** (or fallback target chosen).

## Sprint M1 — DB foundation on Supabase — *"Cloud Migration — M1 DB Foundation"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ⬜ | B-175 | Port schema → Postgres (+ unique constraints, indexes) | `agent:data-integrity-auditor` | P1 | 3 |
| ⬜ | B-176 | Rewrite `db.py` (psycopg, dict_row, dual-backend) | `agent:data-integrity-auditor` | P1 | 3 |
| ⬜ | B-177 | SQLite → Postgres data migrator (row parity) | `agent:data-integrity-auditor` | P1 | 3 |
| ⬜ | B-178 | `public_company_v` serving view + read-only RLS | `agent:data-integrity-auditor` | P1 | 2 |

**Gate M1:** ⬜ data in Supabase with row-count parity; `db.py` works against the cloud DB.

## Sprint M2 — Pipeline port + verify — *"Cloud Migration — M2 Pipeline Port"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ⬜ | B-179 | Port pipeline-critical scripts to PG dialect | `agent:general-purpose` | P1 | 5 |
| ⬜ | B-180 | Idempotency + parity verification | `agent:general-purpose` | P1 | 2 |
| ⬜ | B-181 | Test-suite strategy (sqlite fast-path + pg smoke) | `agent:general-purpose` | P2 | 2 |

**Gate M2 (big one):** ⬜ pipeline end-to-end on Supabase, parity + idempotency; FUSE rules obsolete in principle.

## Sprint M3 — Hosting + dynamic template — *"Cloud Migration — M3 Hosting + Template"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ⬜ | B-182 | Vercel lift-and-shift static `outputs/` (Gate 2a) | `agent:dashboard-designer` | P1 | 2 |
| ⬜ | B-183 | Dynamic `company.html` template + Supabase fetch | `agent:dashboard-designer` | P1 | 5 |
| ⬜ | B-184 | Cut over + remove `pending_review.json` (Gate 2b) | `agent:dashboard-designer` | P1 | 2 |

**Gate M3:** ⬜ 2a static-live · ⬜ 2b template-live (bundle 48 MB → ~one template).

## Sprint M4 — Cloud pipeline on GitHub Actions — *"Cloud Migration — M4 Cloud Pipeline"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ⬜ | B-185 | Daily cron workflow + Vercel deploy hook | `agent:general-purpose` | P1 | 3 |
| ⬜ | B-186 | Incremental refresh (rolling window + scoped eval) | `agent:general-purpose` | P1 | 3 |
| ⬜ | B-187 | Secrets to Actions/Vercel; anon key only on front end | `agent:general-purpose` | P1 | 1 |

**Gate M4:** ⬜ full daily refresh runs with the PC off; site updates.

## Sprint M5 — Decommission + docs — *"Cloud Migration — M5 Decommission"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ⬜ | B-188 | Retire FUSE regime from `CLAUDE.md`; document new backend | `agent:general-purpose` | P2 | 2 |
| ⬜ | B-189 | Archive local DB; update deploy scripts/docs | `agent:data-integrity-auditor` | P2 | 1 |

**Gate M5:** ⬜ docs updated; running fully in the cloud.

---

## Progress summary

| Sprint | Done / total | Pts done / total | Gate |
|--------|--------------|------------------|------|
| M0 | 0 / 3 | 0 / 6 | ⬜ |
| M1 | 0 / 4 | 0 / 11 | ⬜ |
| M2 | 0 / 3 | 0 / 9 | ⬜ |
| M3 | 0 / 3 | 0 / 9 | ⬜ |
| M4 | 0 / 3 | 0 / 7 | ⬜ |
| M5 | 0 / 2 | 0 / 3 | ⬜ |
| **Total** | **0 / 18** | **0 / 45** | — |

## B-NNN → Linear ID map (synced 2026-06-22)

| B | Linear | | B | Linear | | B | Linear |
|---|--------|---|---|--------|---|---|--------|
| B-172 | DIR-101 | | B-178 | DIR-107 | | B-184 | DIR-113 |
| B-173 | DIR-102 | | B-179 | DIR-108 | | B-185 | DIR-114 |
| B-174 | DIR-103 | | B-180 | DIR-109 | | B-186 | DIR-115 |
| B-175 | DIR-104 | | B-181 | DIR-110 | | B-187 | DIR-116 |
| B-176 | DIR-105 | | B-182 | DIR-111 | | B-188 | DIR-117 |
| B-177 | DIR-106 | | B-183 | DIR-112 | | B-189 | DIR-118 |

All 18 issues are in **Backlog** under the 6 "Cloud Migration — M*" milestones. To begin, say *"start sprint M0"* (moves M0 issues to Todo / a cycle) — cycles are created in the Linear UI, then issues assigned via the `directors-dealings-pm` skill.
