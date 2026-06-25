# Cloud Migration — Sprint Tracker

**Living status board.** Detail for every B-NNN is in `cloud-migration-execution-plan.md`. Update the **Status** column as work moves; mark gates ✅ when passed. Issues continue the project's B-NNN sequence (next free = **B-172**) and carry the usual agent labels for Linear.

**Status legend:** ⬜ Todo · 🟦 In Progress · ✅ Done · ⛔ Blocked · ⏸ Deferred
**Overall:** 🟢 LIVE + SELF-RUNNING — https://directors-dealings.vercel.app/ (and the GitHub Pages twin). Data in Supabase; front + company pages read it directly in the browser. **M0–M4, M6 + DR all done (2026-06-25).** The 6am job downloads→computes-local→uploads in ~6 min, plus a manual ↻ Refresh button. **Remaining: M5 decommission only** (B-188 retire FUSE notes, B-189 archive local DB) — parked until the daily refresh has run unattended cleanly for ~a week.
✅ **M4 reliability SOLVED (2026-06-23 pivot, shipped 2026-06-25).** The direct-against-Supabase pipeline timed out (eval_signals >45min / backtest >60min over transatlantic latency); the `rendered_pages` publish approach was abandoned. Fix: front page rebuilt as a live client-side direct-read page (M6), and the daily refresh re-architected to compute on runner-local SQLite then bulk-sync to Supabase (B-194), with a race-proof push. **Local files NOT yet deleted** — see M5.
**Linear:** synced 2026-06-25 — all delivered issues Done; B-190–197 added (DIR-127–131). B-NNN→DIR map at the bottom.
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
| ✅ | B-182 | Vercel lift-and-shift static `outputs/` (Gate 2a) — site live | `agent:dashboard-designer` | P1 | 2 |
| ✅ | B-183 | Dynamic `company.html` template + Supabase fetch | `agent:dashboard-designer` | P1 | 5 |
| ✅ | B-184 | Cut over + remove `pending_review.json` (Gate 2b) — 880 static pages gone | `agent:dashboard-designer` | P1 | 2 |

**Gate M3:** ✅ 2a static-live · ✅ 2b template-live (bundle 48 MB → one dynamic template).

## Sprint M4 — Cloud pipeline on GitHub Actions — *"Cloud Migration — M4 Cloud Pipeline"*

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ✅ | B-185 | Daily cron workflow + auto-deploy — `daily-refresh.yml` live (delivered via the B-194 local-compute rewrite) | `agent:general-purpose` | P1 | 3 |
| ✅ | B-186 | Incremental refresh — rolling window (`_compute_scrape_days`) + idempotent upserts; satisfied by B-194 | `agent:general-purpose` | P1 | 3 |
| ✅ | B-187 | Secrets in Actions (DD_DATABASE_URL, DD_FMP_API_KEY) + Supabase (GITHUB_TOKEN); front end uses publishable key only | `agent:general-purpose` | P1 | 1 |

**Gate M4:** ✅ full daily refresh runs with the PC off; site updates (~6 min run, verified).

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
| ✅ | B-190 | Phase 1 DONE — live front page (This Week table + active clusters + top tiles) reading Supabase directly in the browser; `company.html` rebuilt to parity. DIR-127. | `agent:dashboard-designer` | P1 | 5 |
| ✅ | B-191 | Phase 2 DONE — Capital-Deployed chart (client-side, `public_capital_monthly_v`) + Conviction panel. Conviction shipped first as a client-side port of conviction.py; now B-194 populates `conviction_scores`, so UPGRADED to read the REAL graded scores via new `public_conviction_v` (all 6 factors incl. Earn/Past), with the client-side estimate as fallback. Awaiting push. | `agent:dashboard-designer` | P1 | 3 |
| ⬜ | B-192 | Phase 3 — polish/parity (brewing clusters, sparklines, mobile, paper P&L tile) | `agent:dashboard-designer` | P2 | 2 |
| ✅ | B-193 | DONE — `build_dashboard` no longer writes `outputs/index.html`; removed `_publish_live_index`/`_live_shell_html`; dropped Supabase `rendered_pages` table/view. DIR-129. | `agent:general-purpose` | P1 | 2 |

**Gate M6:** ✅ front page live + current with all panels, no pipeline dependency for display. (B-192 polish remains as an optional enhancement.)

## Sprint DR — Daily-refresh reliability (compute re-architecture) — *NEW 2026-06-23*

The data pipeline still must run to refresh **signals/conviction**, and it has the same
latency problem. The front page (M6) fixes *display*; this fixes *data freshness*.

| Status | B-ID | Item | Agent label | Pri | Pts |
|--------|------|------|-------------|-----|-----|
| ✅ | B-194 | **DONE + LIVE 2026-06-25.** Daily run #7 (full flow) GREEN: downloaded → computed → uploaded in ~6min. **conviction_scores 0→162 rows** (window 06-25), signals refreshed (2751), data current to 06-25. The 6am job is now self-sufficient — signals + conviction refresh automatically. Heavy compute on runner-LOCAL SQLite (download→compute DD_FORCE_SQLITE→upload). **DESIGNED** (infra-review, low-risk, mostly reuse) — design: `b194-local-compute-design.md`. **Phase 1 DONE + PASS** (Rupert ran `download_from_postgres.py` 2026-06-25 — all 12 tables parity OK, incl. prices 784,812; bug fixed = set DD_FORCE_SQLITE before db.migrate so it builds a sqlite schema while DD_DATABASE_URL drives the psycopg source). **Phase 3a PROVEN 2026-06-25**: b194-test.yml ran **Success in 6m 8s, CLEAN (no exit-1)** — full pipeline (scrape→signals→backtest→build) completed vs the old 30-47min timeout. **Phase 3b DONE (code)**: `daily-refresh.yml` rewritten to the full flow (download → refresh_all DD_FORCE_SQLITE → migrate_to_postgres upload [gated on pipeline success] → commit; job timeout 120→30). Next: Rupert push + trigger Daily refresh ONCE (this one DOES write back) → verify upload parity + signals refreshed, then the 6am schedule is self-sufficient. **This is the fix for the whole reliability problem** — signals/conviction will refresh automatically. | `agent:general-purpose` | P1 | 8 |
| ✅ | B-195 | DONE — covered free by GitHub's built-in Actions-failure email notifications (no token/code needed). Noted alongside B-197/DIR-131. | `agent:general-purpose` | P2 | 1 |
| ✅ | B-196 | DONE + LIVE — **corrected diagnosis: NOT discovery, NOT exclusion.** The 3 buys (CTA/KLSO/GANA) were discovered + fetched but the PARSER dropped them on non-standard RNS layouts (labels "Name of entity"/"Full name of person Dealing", nested `Price (p)`/`Volume(s)` pence-in-header, inline price/vol, sub-penny). Fixed `parse_pdmr.py` (`_extract_via_aggregate_table`); all 3 ingested to Supabase + verified. DIR-130. | `agent:data-integrity-auditor` | P1 | 2 |

**Gate DR:** ✅ scheduled run completes reliably in the cloud (~6 min), PC off, signals + conviction fresh.

## Progress summary

| Sprint | Done / total | Gate |
|--------|--------------|------|
| M0 | 3 / 3 | ✅ |
| M1 | 4 / 4 | ✅ |
| M2 | 3 / 3 | ✅ |
| M3 | 3 / 3 | ✅ |
| M4 | 3 / 3 | ✅ |
| M6 | 3 / 4 | ✅ (B-192 polish optional, open) |
| DR | 3 / 3 | ✅ |
| M5 | 0 / 2 | ⬜ parked (B-188, B-189 — wait ~a week) |
| **Total** | **22 / 24** | live + self-running; only M5 cleanup left |

## B-NNN → Linear ID map (synced 2026-06-22)

| B | Linear | | B | Linear | | B | Linear |
|---|--------|---|---|--------|---|---|--------|
| B-172 | DIR-101 | | B-178 | DIR-107 | | B-184 | DIR-113 |
| B-173 | DIR-102 | | B-179 | DIR-108 | | B-185 | DIR-114 |
| B-174 | DIR-103 | | B-180 | DIR-109 | | B-186 | DIR-115 |
| B-175 | DIR-104 | | B-181 | DIR-110 | | B-187 | DIR-116 |
| B-176 | DIR-105 | | B-182 | DIR-111 | | B-188 | DIR-117 |
| B-177 | DIR-106 | | B-183 | DIR-112 | | B-189 | DIR-118 |

**M6 + DR (added 2026-06-25):** B-190 → DIR-127 · B-194 → DIR-128 · B-193 → DIR-129 · B-196 → DIR-130 · B-197 → DIR-131. (B-191 folded into DIR-127; B-192 open, no ticket yet; B-195 covered free, noted in DIR-131.)

**Status 2026-06-25:** migration complete and live — all delivered issues are **Done** in Linear. Only **M5 decommission** remains open (B-188/DIR-117 retire FUSE notes, B-189/DIR-118 archive local DB), parked until the daily refresh has run unattended cleanly for ~a week. B-192 (front-page polish) is an optional enhancement.
