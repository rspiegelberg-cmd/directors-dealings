# Cloud Migration — Sprint Tracker

**Living status board.** Detail for every B-NNN is in `cloud-migration-execution-plan.md`. Update the **Status** column as work moves; mark gates ✅ when passed. Issues continue the project's B-NNN sequence (next free = **B-172**) and carry the usual agent labels for Linear.

**Status legend:** ⬜ Todo · 🟦 In Progress · ✅ Done · ⛔ Blocked · ⏸ Deferred
**Overall:** 🟢 LIVE URL — https://directors-dealings.vercel.app/. Data in Supabase; **company pages are live + working** (direct browser read). M0–M3 done.
⚠️ **M4 BLOCKED — architecture pivot (2026-06-23).** The cloud pipeline CANNOT reliably rebuild/publish the front page: heavy compute (`eval_signals` >45min, `backtest` >60min) is too slow on US GitHub runners against eu-west-1 Supabase (thousands of small queries × transatlantic latency). The `rendered_pages` publish approach is **abandoned**. **Decision: rebuild the front page as a live, client-side direct-read page** (like the company pages) → **new Sprint M6** (spec: `live-front-page-spec.md`). Daily-refresh reliability gets its own re-architecture task (compute on runner-local SQLite, bulk-sync to Supabase). **Do NOT delete local files** until the live front page ships AND the daily refresh is reliable.
**Linear:** synced 2026-06-22 — 6 milestones + 18 issues. B-NNN→DIR map at the bottom.
**Environment:** Supabase project `directors-dealings` — ref `mmiaiauybzsdcbrrcxfc`, host `db.mmiaiauybzsdcbrrcxfc.supabase.co`, region eu-west-1, Postgres 17.6. Driveable directly via the connected Supabase connector.

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
| ✅ | B-172 | Supabase connectivity + analysis-path spike — verified | `agent:data-integrity-auditor` | P1 | 2 |
| ✅ | B-173 | **Cloud-IP scraper spike (GATE)** — PASS | `agent:general-purpose` | P1 | 3 |
| ✅ | B-174 | Record Phase 0 gate decision — full cloud confirmed | `agent:general-purpose` | P1 | 1 |

**✅ GATE PASSED 2026-06-22** — B-173 ran on a real GitHub Actions runner (run #1): Investegate **30 index rows**, Yahoo **5/5 tickers**, `GATE RESULT: PASS`. Decision: **proceed with the full cloud pipeline (M1+)** — no local-scrape fallback needed. Minor note: Yahoo returned only ~2 price rows/ticker over 30 days — reachability proven, verify history depth in B-177/B-186.

**Still open in M0:** B-172 Supabase connectivity — needs the prerequisites first (create free Supabase project, `pip install "psycopg[binary]"`, set `DD_DATABASE_URL`, run `.scripts\spike_supabase_conn.py`).

**Gate M0:** ✅ B-173 green on GitHub Actions. Scraper gate cleared.

## Sprint M1 — DB foundation on Supabase — *"Cloud Migration — M1 DB Foundation"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ✅ | B-175 | Port schema → Postgres — 12 tables applied to Supabase | `agent:data-integrity-auditor` | P1 | 3 |
| ✅ | B-176 | Rewrite `db.py` (dual-backend) — built + QA PASS | `agent:data-integrity-auditor` | P1 | 3 |
| ✅ | B-177 | Data migrator — **904,207 rows loaded, PARITY PASS, connector-verified** | `agent:data-integrity-auditor` | P1 | 3 |
| ⏭ | B-178 | Serving view + RLS — **resequenced to M3** (built with the template it serves) | `agent:dashboard-designer` | P1 | 2 |

**Gate M1:** ✅ **MET + infra-signed-off.** All 904,207 rows in Supabase, full row-count parity, `db.py` Postgres path proven live at scale. Full schema reconciled to the **code** (caught + fixed a latent prod bug: conviction_scores wrote to dead `week_start` columns → empty panel; cloud now uses `window_end` and will populate). Infra verdict: **GO-with-conditions** — conditions R-1 (fail-loud try/except) + R-3 (partial-upsert care) folded into B-179 acceptance. B-178 moved to M3 (built with the template). Open follow-up: R-2 value-level parity spot-check (bundle with next Rupert touchpoint).

## Sprint M2 — Pipeline port + verify — *"Cloud Migration — M2 Pipeline Port"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ✅ | B-179 | Port pipeline scripts to PG dialect — QA PASS, infra GO | `agent:general-purpose` | P1 | 5 |
| ✅ | B-180 | Idempotency + parity — **pipeline ran clean end-to-end on Supabase** (`status:done`) | `agent:general-purpose` | P1 | 2 |
| ✅ | B-181 | Test-safety guard (force sqlite under unittest) — leaked-fixtures incident closed | `agent:general-purpose` | P2 | 2 |

**M2 done (2026-06-22).** Live validation took 6 dialect-fix iterations (each failed LOUD — fail-loud working): literal-`%` escape, HAVING-alias→aggregate, `CAST(? AS TEXT)` optional-filter params (×6), positional→named row access, + `_attach_sql_note` tooling. Two operational gotchas hit & recorded in memory: **stale `.pyc` cache** (clear `__pycache__` + `PYTHONDONTWRITEBYTECODE`) and a **test run leaking 33 fixtures into prod** (cleaned via migrator re-run; guarded in db.py). Final clean run connector-verified: tx 6,571 / test_rows 0 / signals 2,746 / conviction 183 (10 surfaced) / i3_bad 0.

**Gate M2 (big one):** ✅ **MET.** Pipeline runs end-to-end on Supabase, parity holds, audit passes, conviction panel populates (latent prod bug fixed). **The FUSE corruption regime is now obsolete in practice** (data lives in Postgres). Next: **M3 — Vercel hosting + dynamic template + serving view (B-178) → the new URL.**

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

## Sprint M6 — Live front page (direct-read) — *NEW 2026-06-23* — *"Cloud Migration — M6 Live Front Page"*

Replaces the abandoned pipeline-render/`rendered_pages` approach. Full detail in
`docs/specs/live-front-page-spec.md`.

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ⬜ | B-190 | Phase 1 — `public_recent_dealings_v` + This Week table + active clusters + top tiles (client-side, reuse company-page code) | `agent:dashboard-designer` | P1 | 5 |
| ⬜ | B-191 | Phase 2 — Conviction panel (`public_conviction_v`) + Capital-Deployed charts (client-side) | `agent:dashboard-designer` | P1 | 3 |
| ⬜ | B-192 | Phase 3 — polish/parity (brewing clusters, sparklines, mobile, paper P&L tile) | `agent:dashboard-designer` | P2 | 2 |
| ⬜ | B-193 | Remove dead publish path: `_publish_live_index` from `build_dashboard`, drop `rebuild-pages.yml` + `rendered_pages` table/view, stop overwriting `outputs/index.html` | `agent:general-purpose` | P1 | 2 |

**Gate M6:** ⬜ front page live + current with all panels, no pipeline dependency for display.

## Sprint DR — Daily-refresh reliability (compute re-architecture) — *NEW 2026-06-23*

The data pipeline still must run to refresh **signals/conviction**, and it has the same
latency problem. The front page (M6) fixes *display*; this fixes *data freshness*.

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| 🟦 | B-194 | Heavy compute on runner-LOCAL SQLite (download→compute DD_FORCE_SQLITE→upload). **DESIGNED** (infra-review, low-risk, mostly reuse) — design: `b194-local-compute-design.md`. **Phase 1 DONE + PASS** (Rupert ran `download_from_postgres.py` 2026-06-25 — all 12 tables parity OK, incl. prices 784,812; bug fixed = set DD_FORCE_SQLITE before db.migrate so it builds a sqlite schema while DD_DATABASE_URL drives the psycopg source). **Phase 3a PROVEN 2026-06-25**: b194-test.yml ran **Success in 6m 8s, CLEAN (no exit-1)** — full pipeline (scrape→signals→backtest→build) completed vs the old 30-47min timeout. **Phase 3b DONE (code)**: `daily-refresh.yml` rewritten to the full flow (download → refresh_all DD_FORCE_SQLITE → migrate_to_postgres upload [gated on pipeline success] → commit; job timeout 120→30). Next: Rupert push + trigger Daily refresh ONCE (this one DOES write back) → verify upload parity + signals refreshed, then the 6am schedule is self-sufficient. **This is the fix for the whole reliability problem** — signals/conviction will refresh automatically. | `agent:general-purpose` | P1 | 8 |
| ⬜ | B-195 | Interim safety net — alert (email) if a scheduled daily run fails or publishes stale | `agent:general-purpose` | P2 | 1 |
| 🟦 | B-196 | Scraper coverage gap (CTA/KLSO/GANA real director buys missed). **DIAGNOSED**: not the parser (all 3 parse to clean BUYs) — it's DISCOVERY: daily run read only 5 index pages, so filings off those pages were never fetched. **FIX CODED** in `run_scrape.py`: read 20 index pages + add `iter_archive` advanced-search backstop, deduped by rns_id. Awaiting push + scrape test. | `agent:data-integrity-auditor` | P1 | 2 |

**Gate DR:** ⬜ scheduled 6am run completes reliably in the cloud, PC off, signals fresh.

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
