# Cloud Migration Plan — GitHub + Supabase + Vercel

**Status:** Scoping / plan-first. Not yet started.
**Date:** 2026-06-22
**Goal (Rupert's decisions, locked):** Move data and compute off the local Windows PC entirely. Fully cloud-hosted, hands-off. Personal use (not commercial). Primary motivations: (1) stop the FUSE data-corruption problem for good, (2) remove dependence on one always-on PC, (3) access the dashboard from anywhere.

---

## 1. The one-paragraph summary

Today the whole system lives on your PC: Python scripts scrape RNS filings, write to a local SQLite database (`directors.db`, ~138 MB), and export a static HTML dashboard that's already published to GitHub Pages. The plan moves three things to the cloud: the **database** goes from local SQLite to **Supabase** (a hosted Postgres database), the **website hosting** moves from GitHub Pages to **Vercel**, and the **pipeline** (scrapers + signal engine) moves from your PC to **GitHub Actions** running on a daily timer. End state: you never run anything locally; the system refreshes itself every morning and the dashboard updates on its own. Code already lives on GitHub, so that leg is essentially done.

**Honest headline:** this is achievable and the running cost is essentially £0/month at your scale — but it is a real re-engineering project, not a copy-paste move. The single biggest task is converting the database from SQLite to Postgres, because SQLite-specific code has leaked into ~82 of your scripts. I'd strongly push back on doing this all in one go. The phased plan below front-loads the biggest win (corruption gone) and de-risks the one thing that could sink the whole effort (cloud scraping) before you commit serious effort.

---

## 2. Target architecture (what lives where)

| Layer | Today | After migration |
|---|---|---|
| **Code** | GitHub (public repo, already done) | GitHub — no change |
| **Database** | Local SQLite `.data/directors.db` (138 MB) | **Supabase** hosted Postgres |
| **Pipeline** (scrape → parse → signals → backtest) | Python on your Windows PC, run manually | **GitHub Actions**, daily cron, automatic |
| **Dashboard build** (static HTML export) | Python on your PC → GitHub Pages | GitHub Actions → **Vercel** |
| **Review/edit app** (`server.py` Flask) | localhost:5000 on your PC | See §6 — decision needed |
| **Secrets** (FMP API key, etc.) | Local environment variables | GitHub Actions Secrets + Vercel env vars |

Data flow, end state: *GitHub Actions wakes daily → scrapes → writes to Supabase → rebuilds the static dashboard → pushes it to Vercel → you open the site from any device.*

---

## 3. The three things that make this non-trivial (verified against your code)

These are the findings from inspecting the actual codebase, not generic warnings.

**3.1 — SQLite → Postgres is the big lift, and it's not isolated to one file.**
Your data layer (`db.py`) is deliberately stdlib-only SQLite — no SQLAlchemy abstraction sitting between your code and the database. That kept things simple locally, but it means the database "dialect" is baked directly into your scripts: **82 scripts contain SQLite-specific syntax** (`INSERT OR REPLACE`, `sqlite3.Row`, `PRAGMA`, `AUTOINCREMENT`, etc.) and **67 call `db.connect()`**. Postgres speaks a slightly different SQL. So the migration isn't "swap one connection string" — it's "rewrite the connection layer **and** audit ~82 scripts for dialect differences." This is very doable but it's the bulk of the work and the part most likely to throw surprises. It's why the plan treats the DB move as its own gated phase. *(The concrete strategy — `psycopg` + `dict_row` shim + `ON CONFLICT` + `?`→`%s` + pipeline-critical-scripts-first — is spelled out in Phase 1 below.)*

**3.2 — Scraping from a cloud IP is the project-killing risk, so we test it first.**
Good news: your scraper is `requests` + `BeautifulSoup` (no browser automation), so it runs fine on a Linux cloud runner. The risk: sites like Investegate and Yahoo Finance often treat traffic from datacenter IP ranges (which GitHub Actions uses) more harshly than your home broadband — more 403s, more rate-limiting, sometimes outright blocks. Scrapers that work perfectly from your PC can fail from the cloud. **If this breaks and can't be worked around, the "fully hands-off" goal is compromised** (you'd keep scraping locally and only push the data up). That's why Phase 0 is a one-day spike to prove it before you invest in the rest.

**3.3 — The 500 MB free-database ceiling.**
Your DB is 138 MB today (~28% of the Supabase free limit) and grows with every filing. You're fine for now, but it's the one number to watch. If you cross 500 MB, Supabase Pro is ~$25/month. We can also keep size down by not migrating the 105 MB `directors_pre_dedup.db` backup and by pruning old price history if needed.

---

## 4. What it costs (personal-use scale)

| Service | Plan | Cost | The catch to watch |
|---|---|---|---|
| GitHub | Free, public repo | **£0** — unlimited Actions minutes for public repos | Scheduled workflows auto-disable after 60 days of *zero* repo activity (a daily run prevents this) |
| Supabase | Free | **£0** | 500 MB DB cap; **project pauses after 7 days with no database queries** — a daily pipeline write keeps it awake |
| Vercel | Hobby (free) | **£0** | Hobby is **non-commercial only** — fine for you now; the day this earns money you must move to Pro (~$20/mo). 100 GB bandwidth/mo (far more than personal use needs) |

**Total: £0/month** at personal scale. The two "pause/disable" caveats both resolve themselves automatically because you'll have a daily job running. The two upgrade triggers to remember: DB > 500 MB → Supabase Pro; project goes commercial → Vercel Pro.

---

## 4a. Scalability runway — what breaks first, when, and the cheapest fix

At single-user scale you have years of runway and every limit has a cheap mitigation. This table is so you recognise the warning sign when it comes — not so you act now. **The one number to monitor is DB size against the 500 MB cap.**

| Limit | When you hit it | Cheapest mitigation |
|---|---|---|
| **Supabase 500 MB DB cap** (138 MB today, ~28%) | The `prices` table dominates growth (daily OHLCV × every ticker × benchmarks). Crossing 500 MB is the real watch number. | (1) Don't migrate the 105 MB pre-dedup backup. (2) Prune `prices` history beyond your longest backtest window (~18 months covers T+365). (3) Only then Supabase Pro (~$25/mo). |
| **Supabase egress / requests** | Only if the site got popular — it won't (single user). One read per page view is trivial. | Materialised view + CDN-cache the combined pages. Effectively never an issue. |
| **Page count / build time** | Under static-per-company, build time + repo size grow linearly with tickers (already 880). | The dynamic template (Phase 2) removes this **structurally** — page count becomes constant (1). |
| **GitHub Actions minutes** | Unlimited on a public repo; only bites if you go **private** (~2,000 min/mo free). | Stay public, or keep the daily job short (incremental scrape). |
| **Vercel Hobby "non-commercial"** | The day the project earns money. | Vercel Pro (~$20/mo) — a licensing trigger, not a scale limit. |
| **Supabase 7-day idle pause** | Only if the daily pipeline stops. | The daily write *is* the keep-alive. Self-resolving. |

---

## 4b. Data-analysis access (post-migration) — the project's core value

The whole point of the project is the frequent ad-hoc alpha-research SQL scans (sector-axis tests, role-class CARs, clustered-t recomputes). The migration must keep that **at least as easy** — and it actually makes it easier.

**Principle: one database, two access paths, no second copy.** Do *not* stand up a separate analytics warehouse, read replica, or OLAP store — at 138 MB single-user that's pure overkill, it would double storage against the 500 MB cap, and it reintroduces the serving-vs-research drift you want to avoid. The *same* Supabase Postgres serves the live site and answers research scans.

| Need | How it's served |
|---|---|
| Live site reads (company page, index, performance) | Supabase auto-generated client, **read-only via Row-Level Security** on public tables |
| Ad-hoc research scans (today's `conn.execute` work) | **Supabase SQL Editor** in the browser for quick scans; **direct Postgres connection string** for scripted scans (`psycopg`) or a notebook (`pandas.read_sql`) |
| Heavier exploration / charting | Point a notebook or a free BI tool (Metabase, `psql`) at the same connection string |

This is strictly *more* capable than today's "open the SQLite file on the PC + CSV snapshots" workaround — and works from any device.

**Serving view vs. raw research tables.** Keep one schema of raw tables (as today) and expose the *site's* read surface as a small Postgres **view** (`public_company_v`) that pre-joins the per-company shape the template needs. Your messy, evolving research queries stay against the raw tables — they're not in the serving path, so refactoring them never risks breaking the site. Use a plain view first (zero maintenance, always live); promote to a *materialised* view only if the live join ever proves slow (won't for years at 138 MB).

**Indexing for the scan patterns.** Carry the existing indexes across, then add two cheap composites the alpha scans will thank you for: `transactions(role_normalized, type, date)` and `signals(signal_id, fired_at)`. Add others only when a scan is measurably slow (Postgres partial indexes are a free-tier-friendly tool SQLite lacks).

---

## 5. Phased plan with gates (recommended)

Each phase delivers standalone value and ends at a manual gate — same discipline as your sprints. Crucially, **Phase 1 alone solves the corruption problem**, so even if you paused after it, you'd be in a far better place.

### Phase 0 — De-risk before committing (½–1 day)
- Stand up a throwaway Supabase project; confirm you can connect and load a small table.
- **Run the scraper from a GitHub Actions runner** against Investegate + Yahoo for a handful of tickers. Does cloud-IP scraping work, get throttled, or get blocked? *This is the decision point for whether "fully hands-off" is realistic.*
- **Gate:** Green light only if cloud scraping works (or has a clear workaround). If it's blocked, we revise the target to "pipeline stays local, writes to Supabase" — still a big win, just not 100% PC-free.

### Phase 1 — Database to Supabase (the corruption fix) — biggest single phase
- Create the production Supabase project.
- Port the schema (`db_schema.sql` + chained migrations) to Postgres; carry existing indexes; add `transactions(role_normalized, type, date)` and `signals(signal_id, fired_at)`.
- Rewrite `db.py` using a concrete, named dialect strategy (not "audit 82 scripts" hand-waving):
  - **Driver: `psycopg` (v3), not `supabase-py`** — your code is DB-API-shaped (`.execute()`/`.fetchall()`), which `psycopg` matches directly; `supabase-py` is a REST idiom that would force rewriting every query site.
  - **`sqlite3.Row` shim:** set `psycopg`'s row factory to `dict_row` so `r["close"]` keeps working everywhere — this is the single setting that avoids editing every `.fetchall()` loop (bonus: real dicts fix the known `sqlite3.Row` has-no-`.get()` bug).
  - **`INSERT OR REPLACE` / `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO UPDATE / DO NOTHING`** (this is the project's idempotency mechanism — port it carefully; it's concentrated in `upsert_transaction` and a few `backfill_*`/`eval_signals` sites).
  - **`?` → `%s`** placeholders (mechanical find-and-replace, review-guarded); split `executescript` migrations into per-statement execution; drop `PRAGMA foreign_keys` (Postgres enforces FKs by default).
  - Do **not** add SQLAlchemy or a query builder "while you're in there" — that's a second migration bolted on the first.
- Port the **pipeline-critical** scripts first (db.py, scrape/backfill_filings, eval_signals, backtest, detect_clusters, export_dashboard_json, build_dashboard); migrate diagnostics/one-off backfills lazily. This shrinks the gate to "the daily run works," not "all 82 scripts compile."
- Create the `public_company_v` serving view and apply read-only RLS to public tables (see §4b).
- Migrate the 138 MB of data (excluding the pre-dedup backup) into Supabase.
- Run the **full pipeline locally, but pointed at Supabase**, and verify the dashboard output matches today's.
- **Gate:** Pipeline runs end-to-end against the cloud DB with identical results; the FUSE guardrails become irrelevant. Your DB is now safe.

### Phase 2 — Hosting to Vercel (access from anywhere) — done as TWO gated steps
**Step 2a — lift-and-shift (prove hosting first):**
- Connect the repo to Vercel; serve the *existing static* `outputs/` unchanged. GitHub Pages stays as fallback.
- Optional: a custom domain.
- **Gate:** Live dashboard served by Vercel, reachable from any device.

**Step 2b — flip company pages to the dynamic template (separate gate):**
- Replace the 880 static company files with **one `company.html` template + read-only client-side Supabase fetch** (see §6a). Delete the 880 files.
- **Remove `outputs/data/pending_review.json` (5 MB) from the public bundle** — it's the review queue, not visitor data, and should never have been web-served.
- Keep the two combined pages (`index.html`, `performance.html`) static for now — they aggregate across all companies, you look at them daily, and they rebuild cheaply once per run.
- **Gate:** Company pages render live from Supabase; bundle collapses from 48 MB to ~one template.

### Phase 3 — Pipeline to GitHub Actions (removes the PC entirely)
- Write the daily cron workflow (≈06:00 UTC, after the London RNS day): install deps → **incremental** refresh → rebuild combined static pages → trigger Vercel deploy.
- **Make the refresh incremental, not a full rebuild** (the update-efficiency goal):
  - Scope the daily scrape to a **rolling recent window** (e.g. last N days); a full corpus re-scrape is a *manual backfill*, never the daily job. The fingerprint upsert already makes re-seen rows no-ops.
  - Run `eval_signals`/`backtest` over **affected fingerprints** (`--from/--to/--signal`), not `--rebuild`.
  - Under Step 2b there are no per-company pages to re-render — the daily build only regenerates the two combined pages.
  - Idempotency contract: the pipeline must run twice in a row with the second run producing zero net changes (this is the CI smoke test).
- Trigger the redeploy with a **Vercel Deploy Hook** (a URL you POST to) stored in Secrets — no CLI, no auth dance.
- Move all secrets (FMP API key, Supabase connection string, deploy hook) into GitHub Actions Secrets; the front end holds only the Supabase **anon/publishable** key (read-only via RLS), never the service-role key.
- **Gate:** A full day's refresh runs automatically with your PC switched off, and the live site updates.

### Phase 4 — Decommission & cleanup (½ day)
- Retire the entire FUSE-corruption guardrail section from `CLAUDE.md` (no longer applicable — a genuinely nice simplification).
- Keep the local setup as a dev/backup environment, not the source of truth.
- **Gate:** Documentation updated; you're running fully in the cloud.

---

## 6. Open decisions before we build

1. **The review/edit app (`server.py`).** Your local Flask app lets you approve/reject/correct filings. Three options once the DB is in the cloud: (a) keep running it locally against the cloud DB when you need to review (simplest); (b) rebuild it as a small hosted admin page (more work, accessible anywhere); (c) drop interactive review and handle corrections via direct SQL/scripts. **Recommended default: (a)** — minimal work, you rarely need it on the move. **This is deferrable and does not block Phase 1** — don't let it become a blocker; decide it during Phase 2/3.
2. **Repo visibility.** Your repo is currently **public** (which is what gives you free unlimited Actions minutes). Putting database credentials in GitHub Secrets is safe in a public repo (secrets aren't exposed), but worth a conscious confirmation that you're comfortable keeping the *code* public. If you ever make it private, Actions minutes become limited (~2,000/min mo free).
3. **Scope of Phase 1's script audit.** *Resolved (ADR-endorsed):* port pipeline-critical scripts first, migrate diagnostics/one-off backfills lazily — see Phase 1. Flag if you'd rather do a big-bang port instead.

---

## 6a. Rendering model — static pages vs. live template (Rupert's question, 2026-06-22)

**Current state (verified):** the build pre-bakes **880 per-company HTML files (~48 MB, the bulk of the 68 MB site)** plus large combined pages (`performance.html` 1.4 MB, `index.html` 920 KB). It is *not* 880 hand-written pages — `render_company.py` is a single renderer called 880 times. The inefficiency is **pre-generating every page to disk ahead of time, including pages no one visits, and re-rendering all 880 on every daily run** even when only a handful changed.

**What the migration unlocks:** with data in Supabase and the site on Vercel, we can switch company pages to **one `company.html` template that fetches a single company's data live when the page is opened** — no pre-built files. This is impossible on GitHub Pages today because there is no live database for a page to query.

**Honest benefit ranking (storage is the weakest reason):**
- *Build efficiency* — stop re-rendering ~870 unchanged pages every run. Real win.
- *Freshness* — live template always shows current data; static is only as fresh as the last build.
- *Visitor page weight* — current pages are 300–550 KB because data is embedded in every file; a template + fetch ships a light shell. Real win.
- *Storage* — 48 MB of static HTML is nearly free to host. Not a real constraint; ignore as a motivation.

**Other efficiencies spotted:**
- `outputs/data/pending_review.json` is **5 MB in the public folder** — if served to the browser, every visitor may be downloading the whole review queue. Audit and likely remove from the public bundle.
- Each company page embeds its own copy of shared scaffolding/data instead of referencing one shared source — duplication across 880 files.
- The combined `performance*.html` pages are rebuilt wholesale each run.

**Recommended approach & sequencing (now reflected in Phase 2 above as two gated steps):**
- **Step 2a — lift-and-shift the existing static output to Vercel first** and prove hosting works.
- **Step 2b — *then* flip company pages to the dynamic template** as its own gated step — never simultaneously with the DB/host/pipeline moves, because changing everything at once makes failures impossible to localise (especially for a non-specialist).
- For 880 low-traffic personal pages, prefer the simplest dynamic model: **one template + read-only client-side fetch from Supabase** (no serverless function). Expose only read-only public data via Supabase row-level security.
- Keep the two combined aggregate pages static; only the *company* pages become dynamic.
- Rejected: Vercel ISR / Next.js — marginally better first-paint, but forces adopting React + a build framework to host a read-only data viewer. Over-engineering for a single user.

**Decision needed:** confirm the dynamic template (Step 2b) as the Phase 2 target. *(The ADR endorses this — see `cloud-migration-adr.md` §2(a).)*

---

## 7. My recommendation

Approve Phase 0 only, as a spike. It's a day of work and it answers the single question that determines whether your stated goal ("move everything to the cloud") is fully reachable or needs a small compromise. Everything after Phase 0 is straightforward-but-substantial execution, and we should size Phase 1 properly (it's the real one) once Phase 0 is green. Do **not** attempt all four phases in one push — the database conversion deserves its own gate with verification, exactly like your signal-engine work.
