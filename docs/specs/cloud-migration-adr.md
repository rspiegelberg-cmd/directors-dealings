# ADR: Cloud Migration Target Architecture (GitHub Actions + Supabase + Vercel)

**Status:** Proposed
**Date:** 2026-06-22
**Deciders:** Rupert
**Supersedes/extends:** `docs/specs/cloud-migration-plan.md` (the phased plan this ADR reviews and refines)

---

## 1. Context

Today the entire system runs on one Windows PC: Python scripts scrape RNS PDMR filings, write to a local SQLite database (`.data/directors.db`, ≈138 MB), and export a static HTML dashboard (`outputs/`, ≈68 MB) that is already published to GitHub Pages. The migration plan moves three things to the cloud — the **database** to Supabase (hosted Postgres), the **hosting** to Vercel, and the **pipeline** to GitHub Actions on a daily cron — so nothing runs on the PC and the FUSE corruption problem disappears.

The plan's three motivations (kill FUSE corruption, remove the always-on-PC dependency, access from anywhere) are sound and I don't relitigate them. This ADR exists to make sure the *target shape* is designed to optimise five goals that the lift-and-shift plan touches but doesn't fully resolve:

1. **Reduce disk space** — repo + hosted artifacts (the 880-file / 48 MB problem; the 5 MB public JSON).
2. **Site performance** — page weight and load time for the visitor (you, on any device).
3. **Efficient updating** — stop redoing unchanged work on every daily refresh.
4. **Scalability** — the DB grows with every filing; page count grows with every new ticker. What breaks first, and when.
5. **Ongoing data analysis** — *this is the one that's easy to under-serve.* The project's real value is the frequent ad-hoc alpha-research SQL scans (sector-axis tests, role-class CARs, clustered-t recomputes — see the MEMORY log). Today those run as raw `conn.execute(...)` against the local SQLite file. The new architecture must keep that querying **at least as easy**, ideally easier.

These five goals are mostly aligned, but two real tensions run through every decision below: **(a) "fully hands-off in the cloud" vs. "simple enough for a non-engineer to debug"**, and **(b) static pre-built pages vs. a live dynamic template** — a choice that, done wrong, can actively *hurt* the data-analysis goal by splitting the data into a serving copy and a research copy.

### Forces / constraints (verified against the code)

- **`db.py` is stdlib `sqlite3`, no ORM.** The DB dialect is baked directly into the scripts. A grep for SQLite-specific syntax (`INSERT OR REPLACE`, `sqlite3.Row`, `PRAGMA`, `AUTOINCREMENT`, `executescript`, `.fetchall()`) hits **137 occurrences across ~40+ files**; the plan's "82 scripts / 67 call `db.connect()`" figure is the right order of magnitude. `INSERT OR REPLACE` / `INSERT OR IGNORE` is not incidental — it *is* the project's idempotency mechanism (`upsert_transaction`, `eval_signals` writing the `signals` table, every `backfill_*`). Porting it is the bulk of the work.
- **Schema uses `TEXT` for dates and a `^`-prefix convention** (`^FTAS`, `^FTNMX5710`) to store benchmark series in the same `prices` table as stock prices (`db_schema.sql`). This is portable to Postgres unchanged — but it means date comparisons are string comparisons today, which Postgres also supports on ISO-8601 text, so no forced rewrite.
- **The build re-renders everything every run.** `build_dashboard.py` calls `_tickers_with_transactions(conn)` and loops `render_company.render_to_file()` for **every** ticker (the 880 pages), reading per-company transactions/prices/signals/firings from the DB each time — even when only a handful of companies had a new filing. Data is embedded inline into each page (300–550 KB each), which is why pages are heavy and why the bundle is 48 MB.
- **Scraper is `requests` + BeautifulSoup**, no browser — runs fine on a Linux runner *if* datacenter-IP scraping isn't blocked (the plan's Phase 0 spike, correctly the gating risk).
- **Free-tier ceilings:** Supabase free = 500 MB DB + pause after 7 days idle; Vercel Hobby = free but non-commercial; GitHub Actions = unlimited minutes on a public repo.

---

## 2. Decisions

### (a) Rendering model — static-per-company vs. dynamic template vs. ISR

**The problem in numbers.** 880 pre-built HTML files (~48 MB) are the bulk of the 68 MB site. Each is 300–550 KB because the data is embedded inline. Every daily run re-renders all 880 from the DB even though typically only a handful changed. And `outputs/data/pending_review.json` is **5 MB sitting in the public folder** — if any page fetches it, every visit downloads the entire review queue. So this one decision touches four of the five goals at once (disk, performance, update-efficiency, and — via where the data lives — analysis).

**Options considered.**

| Option | Disk (repo+host) | Visitor performance | Update efficiency | Complexity for a beginner | Scales to 3,000 tickers? |
|---|---|---|---|---|---|
| **A. Keep static-per-company, lift-and-shift** | Worst — 48 MB+ grows linearly with tickers; every page in git history | OK once loaded, but 300–550 KB/page | Worst — re-renders all 880 every run | Lowest (no change) | No — build time + repo size grow linearly |
| **B. One `company.html` template + client-side read-only fetch from Supabase** | Best — one template (~tens of KB), zero per-company files | Light shell + one API call; visitor downloads only their company's rows | Best — nothing to re-render; page is always live | Low-medium — it's just `fetch()` + a JS template; **no serverless code** | Yes — page count is constant (1), DB does the work |
| **C. Vercel ISR (Next.js, server-rendered, cached per page)** | Good — pages cached not committed | Best (pre-rendered HTML, CDN-cached) | Good — regenerates only on-demand/on-revalidate | **Highest** — requires adopting Next.js, a build framework, React mental model | Yes, but at the cost of a framework you'd have to learn |

**Recommendation: Option B — one dynamic template + read-only client-side fetch from Supabase.** This is the plan's §6a recommendation and I endorse it, with sharper reasoning:

- It collapses the 48 MB / 880-file problem to a single template file. Disk goal: solved, and it *stays* solved as tickers grow (Decision (d)).
- The visitor downloads a light shell plus only the rows for the company they opened — a genuine performance win over the current 300–550 KB inline pages.
- "Re-render 870 unchanged pages every morning" simply ceases to exist as a concept. Update-efficiency: solved structurally, not optimised.
- Crucially for the **analysis goal**: Option B keeps Supabase as the *single source of truth that the site reads live*. There is no second "published" copy of the data to drift from the research copy. The page and your SQL scans hit the same tables. (Static pages, by contrast, are a frozen snapshot — fine for viewing, but they tempt you into treating the exported JSON as the data, which is how serving/research copies diverge.)

**Reject C (ISR/Next.js) explicitly:** it gives marginally better first-paint via CDN caching, but it forces a non-engineer to adopt React + a build framework to host what is fundamentally a read-only data viewer. That is over-engineering for a single-user site. The simplest thing that hits the goals is B.

**Keep the big combined pages (`index.html`, `performance.html`) static for now.** They aggregate across all companies, they're the ones you actually look at daily, and they're only rebuilt once per run (cheap). Don't dynamise them in the same step — convert *company* pages to the template, leave the two combined pages as a static export, and revisit only if build time becomes a problem (it won't for years). This also de-risks: if the dynamic template misbehaves, your daily surface still works.

**The 5 MB `pending_review.json`: remove it from the public bundle now, regardless of rendering model.** It's the review queue, not visitor data. It should never have been web-served. Under Option B the review/edit workflow (Decision in §2(c)/Open Items) reads it locally or from a private location — not from `outputs/`.

**Sequencing (this matters):** do the lift-and-shift first (DB → Supabase, host → Vercel serving the *existing static* output), prove it green, *then* flip company pages to the dynamic template as its own gated step. Changing the data layer, the host, and the rendering model simultaneously makes any failure impossible to localise — exactly the wrong move for a beginner debugging alone.

---

### (b) Database & analytics access — one Postgres serving the site AND ad-hoc research

This is the goal most likely to be quietly degraded by a careless migration, so it gets real weight.

**Principle: one database, two access paths, no second copy.** Resist the instinct (and resist any enterprise advice) to stand up a separate analytics warehouse, a read replica, or an OLAP store. At 138 MB and single-user, the *same* Supabase Postgres instance serves the live site and answers your research scans with room to spare. A separate warehouse would (i) double your storage against the 500 MB cap, (ii) require an ETL sync you'd have to maintain, and (iii) reintroduce the exact serving-vs-research drift you're trying to avoid. **Overkill — do not do it.**

**How the two access paths work:**

| Need | How it's served | Effort |
|---|---|---|
| Live site reads (company page, index, performance) | Supabase auto-generated REST/JS client, **read-only via Row-Level Security** on the public tables | Low — built in |
| Ad-hoc alpha-research SQL scans (today's `conn.execute` work) | **Supabase SQL Editor** in the browser for quick scans; **direct Postgres connection string** for scripted scans (`psycopg`) or a notebook | Low — both built in |
| Heavier exploratory / charting analysis | Point a notebook (Jupyter/pandas `read_sql`) or a free BI tool (Metabase free, or `psql`) at the same connection string | Low — standard Postgres tooling |

The migration actually **upgrades** the analysis goal rather than threatening it: today a research scan means opening the SQLite file on the PC (and tiptoeing around FUSE snapshots). After migration you get a browser SQL editor against the live data from any device, plus `pandas.read_sql(query, conn)` over a real connection — strictly more capable than `sqlite3` + CSV snapshots.

**Indexing for the scan patterns.** The research scans group and filter on the same axes repeatedly: `ticker`, `date`/`announced_at`, `role_normalized`/`type`, sector (via `tickers_meta`), and join `signals`/`transactions`/`prices`. The current schema already indexes `(ticker, date)`, `director`, `(type, date)` on transactions and `(date)` on prices. **Carry those across verbatim, then add two cheap composite indexes that the alpha scans will thank you for:** `transactions(role_normalized, type, date)` and `signals(signal_id, fired_at)`. Postgres also lets you add a partial index (e.g. `WHERE type='BUY'`) later if a specific scan gets slow — a free-tier-friendly tool SQLite lacks. Do **not** pre-build exotic indexes speculatively; add them when a scan is measurably slow.

**Serving view vs. raw research tables — yes, but keep it lightweight.** Recommendation: keep **one schema of raw tables** (transactions, prices, signals, tickers_meta, …) as today, and expose the **site's read surface as a small number of Postgres views or materialised views** rather than letting the public client query raw tables directly. Concretely:

- A `public_company_v` view (or materialised view) that pre-joins the per-company shape the template needs (txns + signals + latest prices), so the client does one clean read and you control exactly what's exposed via RLS.
- Keep your messy, evolving research queries against the raw tables — they're not in the serving path, so refactoring them never risks breaking the site.

Use a **plain view** first (zero maintenance, always live). Only promote to a **materialised view** (with a refresh at the end of the daily pipeline) if the live join proves too slow on the free tier — which, at 138 MB, it won't for a long time. This gives you the clean separation the goal asks for (serving layer vs. research layer) **without a second database and without an ETL job**.

---

### (c) Pipeline execution & update efficiency (GitHub Actions)

**Cron design.** One workflow, `schedule: cron` once daily (after the London RNS day closes — e.g. 06:00 UTC). Steps: checkout → `pip install` deps → run the pipeline against Supabase (connection string from Secrets) → rebuild the dashboard → trigger a Vercel deploy. The daily run doubles as the keep-alive that prevents both the Supabase 7-day pause and the GitHub 60-day workflow auto-disable — both free-tier caveats resolve themselves for free.

**Make the refresh incremental — this is the update-efficiency goal.** The architecture already has the right bones; lean on them:

- **Scrape/parse is already idempotent** via `upsert_transaction` (fingerprint = `date|ticker|director|type|shares`, re-seen rows just bump `seen_count`). So a daily run that re-fetches recent filings inserts only genuinely new rows. Keep that. The one change: scope the daily scrape to a **rolling recent window** (e.g. last N days of Investegate), not the whole corpus — full re-scrapes are a backfill operation, run manually, not the daily job.
- **Signals/backtest** likewise upsert (`INSERT OR REPLACE` on the natural PK → must become Postgres `INSERT ... ON CONFLICT ... DO UPDATE`; see Decision (e)). Run `eval_signals`/`backtest` only over the new/affected fingerprints where the CLI already supports `--from/--to/--signal`, not `--rebuild`, in the daily path.
- **Rendering** is where the biggest waste is today (all 880 pages every run). **Option B (dynamic company template) eliminates this entirely** — there are no per-company files to rebuild. The daily build then only regenerates the two combined static pages, which is cheap. This is the clean synergy: the rendering decision *is* the update-efficiency fix.

**Idempotency contract.** Every pipeline step must be safe to re-run on the same day with no duplicate rows and no drift — already true via the upsert pattern; preserve it exactly when porting to `ON CONFLICT`. The pipeline should be runnable end-to-end twice in a row with the second run producing zero net changes (this is your CI smoke test).

**Triggering the Vercel redeploy.** Simplest: a **Vercel Deploy Hook** (a URL you POST to) called as the last step of the Actions workflow, with the hook URL stored in Secrets. No Vercel CLI, no extra auth dance. Under Option B, most "updates" are just live data — a redeploy is only needed when the *template or combined static pages* change, so you may end up triggering it on code push (Vercel's native git integration) and not from the data pipeline at all. Decide this at build time; both are one line.

**Secrets management.** Supabase connection string, FMP API key, Vercel deploy hook → **GitHub Actions Secrets** (encrypted, never exposed in logs, safe even in a public repo) and **Vercel env vars** for anything the front end needs. The front end must only ever hold the Supabase **anon/publishable** key (read-only via RLS), never the service-role key.

---

### (d) Scalability runway — what breaks first, when, and the cheapest fix

The honest headline: **at single-user scale you have years of runway, and every limit has a cheap mitigation.** The point of this table is so you recognise the warning sign when it comes, not so you act now.

| Limit | When you hit it | Cheapest mitigation |
|---|---|---|
| **Supabase 500 MB DB cap** (138 MB today, ~28%) | The `prices` table dominates DB growth (daily OHLCV × every ticker × benchmarks). At current trajectory, low-hundreds-of-MB for a good while; crossing 500 MB is the real watch number. | (1) Don't migrate the 105 MB `directors_pre_dedup.db` backup. (2) Prune `prices` history beyond the longest backtest window you actually use (T+365 → ~18 months is plenty; older daily closes can be dropped or rolled to monthly). (3) Only then, Supabase Pro (~$25/mo). |
| **Supabase egress / API requests** (free tier has a monthly egress allowance) | Only a concern if the site became popular — it won't (single user). A dynamic template doing one read per page view is trivially within free egress. | Materialised view to cut per-view query cost; CDN-cache the combined static pages on Vercel. Effectively never an issue at personal scale. |
| **Page count / build time** | Under static-per-company, build time and repo size grow linearly with tickers (already 880; this is the thing that genuinely degrades). | **Option B removes this entirely** — page count is constant. This is a structural fix, not a tuning one. |
| **GitHub Actions minutes** | Unlimited on a public repo. Only bites if you make the repo **private** (~2,000 min/mo free). | Stay public (you already are), or keep the daily job short (incremental scrape, no full rebuild). |
| **Vercel Hobby "non-commercial"** | The day the project earns money. | Vercel Pro (~$20/mo). Not a scale limit — a licensing trigger. |
| **Supabase 7-day idle pause** | Only if the daily pipeline stops running. | The daily write *is* the keep-alive. Self-resolving. |

**What to monitor (one-line answer):** DB size against 500 MB, and (if you ever go private) Actions minutes. Everything else is self-healing or years away.

---

### (e) SQLite → Postgres dialect strategy — the thinnest viable path

This is the bulk of the labour and the part most likely to surprise. The goal is **minimum scripts touched, maximum behaviour preserved.**

**Driver: `psycopg` (v3), not `supabase-py`.** Your code is built around a DB-API connection with `.execute()` / `.fetchall()` and `sqlite3.Row` rows. `psycopg` is the direct Postgres equivalent of that exact shape; `supabase-py` is a REST client with a different idiom (`.table().select()`) that would force rewriting all ~40 query sites. Use `psycopg` for everything Python (pipeline + scripts), and reserve the Supabase JS/REST client for the *front-end read-only fetch only*. Two clients, each used where it fits.

**Centralise the dialect in `db.py` — it's already the chokepoint.** `db.py` is the single connection factory (`connect()`), and it already has `set_meta` written as portable `INSERT ... ON CONFLICT DO UPDATE` (which is valid Postgres!). Concentrate the porting there:

1. **`connect()`** → open a `psycopg` connection to Supabase instead of `sqlite3.connect(DB_PATH)`. Keep the function name and return a connection whose `.execute()`/`.fetchall()` callers don't all change.
2. **`sqlite3.Row` shim.** Callers index rows both by name (`r["close"]`) and position, and use `"col" in r.keys()` (see `build_dashboard.py`). Configure `psycopg`'s row factory to return **dict-like rows** (`psycopg.rows.dict_row`). This single setting makes `r["close"]` work everywhere and is *absolutely worth it* — it's the difference between editing `db.py` once vs. auditing every `.fetchall()` loop. (Note the known footgun already in MEMORY: `sqlite3.Row` has no `.get()` — moving to real dicts actually *fixes* that class of bug.)
3. **`INSERT OR REPLACE` / `INSERT OR IGNORE`** → Postgres `INSERT ... ON CONFLICT (cols) DO UPDATE SET ...` / `DO NOTHING`. There is no auto-rewrite; these must be edited by hand, but they're concentrated in the upsert helpers (`upsert_transaction`) and a handful of `backfill_*`/`eval_signals` sites. Centralise the transaction upsert in `db.py` (it already is) so most callers inherit the fix.
4. **`executescript()`** (used for schema + migrations) has no Postgres equivalent — split the schema/migration SQL and run statements via `psycopg`'s multi-statement execution. The migration chain in `db.py` is forward-only and idempotent; keep that design, just change the execution call.
5. **`PRAGMA foreign_keys = ON`** → drop it (Postgres enforces FKs by default). `AUTOINCREMENT` → `BIGSERIAL`/`IDENTITY` where present.
6. **Parameter placeholders:** `sqlite3` uses `?`; `psycopg` uses `%s`. This is the most mechanical, highest-count change. **Do this as a single find-and-replace pass guarded by review** — it's tedious but low-risk because it's purely syntactic.

**A compatibility shim IS worth it — but only for the row factory and `connect()`, not a full abstraction layer.** Do *not* introduce SQLAlchemy or a query builder "while you're in there" — that's a second migration bolted onto the first and exactly the over-engineering to avoid. The right amount of abstraction is: `db.py` owns the connection + row shape, query sites keep their hand-written SQL with `%s` placeholders.

**Scope: pipeline-critical scripts first (gate-shrinking).** The plan's Open Decision #3 asks whether to port all ~82 at once. **Port the daily-pipeline path first** (`db.py`, `run_scrape`/`backfill_filings`, `eval_signals`, `backtest`, `detect_clusters`, `export_dashboard_json`, `build_dashboard`), prove it green end-to-end against Supabase, and migrate diagnostics/one-off backfills lazily as you next need them. That shrinks the Phase 1 gate to "the daily run works" rather than "all 82 scripts compile."

---

## 3. Trade-off analysis (cross-cutting)

**"Fully hands-off cloud" vs. "simple to debug as a beginner."** This is the central tension. Full automation (Actions cron, auto-deploy) is the goal, but it also means failures happen at 6 a.m. when you're not watching, in a Linux runner you can't poke at. Mitigations that keep it debuggable: (1) keep the local PC as a working dev/backup environment that can run the *same* pipeline against the *same* Supabase DB — so you can reproduce any cloud failure locally; (2) make every step idempotent so "just re-run it" is always a safe first response; (3) have the Actions workflow write a short run-summary (rows added, signals fired, pages built) you can read without diffing the DB. Do **not** chase zero-touch perfection — a system you can't debug is worse than one you occasionally nudge.

**Static vs. dynamic and the data-analysis goal.** The subtle risk: static pre-built pages quietly encourage a mental model where "the data" is the exported JSON/HTML snapshot, and the live DB is just an upstream detail. That's how a serving copy and a research copy drift apart (a pattern this project has already been bitten by — see the MEMORY note where an exporter `try/except` swallowed a schema mismatch and shipped an empty panel while tests passed). **Option B (live template reading Supabase) structurally prevents this**: the page you view and the SQL you analyse hit the same tables. Choosing dynamic isn't only a disk/performance win — it keeps the analysis goal honest.

**Lift-and-shift vs. redesign, sequenced.** Doing the DB port, the host move, the pipeline move, and the rendering redesign all at once would be faster on paper and catastrophic in practice for a solo non-engineer. The phased gates (each delivering standalone value, FUSE-fix first) are the right discipline — same as the project's signal-engine staging.

---

## 4. Consequences

**What gets easier:**
- The FUSE corruption regime — the two-zone rule, snapshot dance, Windows-only writes, `.bak` self-healing — **all becomes irrelevant.** That entire section of `CLAUDE.md` can retire. This is the single biggest quality-of-life win.
- Ad-hoc analysis improves: browser SQL editor + real `pandas.read_sql` against live data from any device, vs. today's SQLite-file + CSV-snapshot workaround.
- Update efficiency stops being a tuning problem and becomes a non-problem (dynamic template; incremental scrape).
- Disk: the 48 MB/880-file bundle collapses to one template; repo stops growing linearly with tickers.

**What gets harder / new costs:**
- You now depend on three external services (GitHub, Supabase, Vercel) instead of one PC. Each has its own dashboard, auth, and failure modes to learn.
- Debugging moves from "open the file on my PC" to "read a runner log / query a cloud DB." Mitigated by keeping the local dev mirror.
- The dialect port is real, one-time labour with a tail of diagnostics scripts to migrate lazily.
- A new failure class: cloud-IP scraping blocks (the Phase 0 gating risk). If it bites, the compromise is "pipeline stays local, writes to Supabase" — still kills FUSE, still gets you cloud data + anywhere-access, just not 100% PC-free.

**What to revisit:**
- DB size vs. 500 MB cap — set a calendar reminder to check quarterly; act on `prices` pruning before paying for Pro.
- Whether the company-page view needs to become *materialised* (only if the live join slows down — unlikely for years).
- The review/edit app (`server.py`) — see Open Items; defer the decision, don't let it block Phase 1.

---

## 5. Action items (mapped to the plan's Phase 0–4, with the goal each serves)

**Phase 0 — De-risk (½–1 day)**
1. Stand up a throwaway Supabase project; load a small table via `psycopg`; run a SQL scan from the browser editor to confirm the analysis path. *(Goal 5)*
2. Run the scraper from a GitHub Actions runner against Investegate + Yahoo for a few tickers. **Gate the whole migration on this.** *(enables Goals 3+4 hands-off)*

**Phase 1 — Database to Supabase (the corruption fix; biggest phase)**
3. Port the schema (`db_schema.sql` + the 16 chained migrations) to Postgres; carry the existing indexes; add `transactions(role_normalized, type, date)` and `signals(signal_id, fired_at)`. *(Goals 4, 5)*
4. Rewrite `db.py`: `psycopg` `connect()`, `dict_row` row factory (the `sqlite3.Row` shim), split `executescript` migrations. Keep public function names. *(Goal 5; foundation for all)*
5. Port the **pipeline-critical** dialect (`INSERT OR REPLACE` → `ON CONFLICT`, `?` → `%s`) in the daily-path scripts only; defer diagnostics. *(Goal 3)*
6. Create the `public_company_v` serving view; apply read-only RLS to public tables. *(Goals 2, 5 — separates serving from research)*
7. Migrate the 138 MB of data (excluding the pre-dedup backup); run the full pipeline locally pointed at Supabase; verify dashboard output matches today's. **Gate.** *(all)*

**Phase 2 — Hosting to Vercel**
8. Serve the *existing static* `outputs/` from Vercel first (lift-and-shift), GitHub Pages as fallback. **Gate: live from any device.** *(remove-PC goal)*
9. *Then, as a separate gated step:* flip company pages to **Option B** — one `company.html` template + read-only Supabase fetch. Delete the 880 static files and remove `pending_review.json` from the public bundle. **Gate.** *(Goals 1, 2, 3)*

**Phase 3 — Pipeline to GitHub Actions**
10. Write the daily cron workflow: install → incremental scrape (rolling window) → `eval`/`backtest` over affected fingerprints → rebuild combined static pages → POST Vercel deploy hook. *(Goals 3, 4)*
11. Move all secrets to Actions Secrets / Vercel env vars; front end gets the anon key only. **Gate: a full refresh runs with the PC off.** *(remove-PC goal)*

**Phase 4 — Decommission & cleanup (½ day)**
12. Retire the FUSE-corruption section from `CLAUDE.md`; keep the PC as a dev/backup mirror that can run the same pipeline against Supabase. **Gate.** *(maintainability)*

---

## 6. Recommended edits to `cloud-migration-plan.md` (do not apply here — for Rupert to action)

1. **§2 / §6a — make Option B (dynamic company template) the explicit Phase 2 *second* step, not Phase 2's premise.** The plan already leans this way; tighten it to: lift-and-shift static to Vercel first (gate), *then* flip to the template (separate gate). State plainly that the two combined pages stay static.
2. **Add a new sub-section: "Data-analysis access" (currently absent).** The plan covers the site and pipeline but never states how ad-hoc research scans work after migration. Add the Decision (b) content: same DB, two paths (SQL editor + `psycopg`/notebook), serving **view** vs. raw research tables, and the two extra indexes. This is the project's core value and the plan is silent on it.
3. **§3.1 / Phase 1 — specify the dialect strategy concretely:** `psycopg` (not `supabase-py`), `dict_row` shim for `sqlite3.Row`, `ON CONFLICT` for `INSERT OR REPLACE`, `?`→`%s` pass, `executescript` split. Currently the plan says "rewrite the connection layer and audit 82 scripts" without naming the *how*.
4. **§5 Phase 3 — add the incremental-refresh contract:** rolling-window daily scrape (full re-scrape is a manual backfill), run `eval`/`backtest` over affected fingerprints not `--rebuild`, and note that Option B removes per-company re-rendering. The plan says "run the daily pipeline" without distinguishing incremental from full.
5. **§4 / new — add the scalability table** (Decision (d)): the limit → when → mitigation grid, and name DB size vs. 500 MB as the one number to monitor, with `prices`-pruning as the pre-paid-tier lever.
6. **§6a — promote "remove `pending_review.json` from the public folder" from a spotted efficiency to a Phase 2 action item.** It's a 5 MB public leak of the review queue and should be explicit, not a footnote.
7. **§6 Open Decision #1 (review app) — note it's deferrable and does not block Phase 1.** Recommend Option (a) (run `server.py` locally against the cloud DB when needed) as the default so it doesn't become a blocker.
