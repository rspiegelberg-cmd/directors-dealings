# Live Front Page — Spec & Scope (2026-06-23)

**Status:** Planned. Not yet built. Supersedes the "pipeline renders index.html →
publishes to `rendered_pages` → shell fetches it" approach, which is **abandoned**
(see "Why" below).

---

## 1. Why (the problem this solves)

The front "This Week" page was a **static file**: the pipeline (`build_dashboard`)
rendered `outputs/index.html` from computed JSON, and it was pushed to GitHub →
Vercel. Three things broke once we moved to the cloud:

1. **Two masters / clobber.** The cloud pipeline rebuilt the page, but Rupert's
   local `push_to_github.bat` (`git add -A`) pushed his **stale** local copy on top
   of it. The stale one kept winning.
2. **The fatal one — transatlantic compute latency.** The pipeline's heavy steps
   (`eval_signals`, `backtest`) run on **US GitHub runners** against the
   **eu-west-1 Supabase** DB. They fire **thousands of small queries**, each paying
   ~80–100ms round-trip. Result:
   - `eval_signals` exceeded **45 min** (timed out).
   - `backtest` exceeded **60 min** (killed by the job cap).
   Both blow their timeouts, so the pipeline **cannot reliably rebuild/publish the
   front page**. Verified across daily-refresh runs #2/#3 and rebuild-pages run #2.
3. The `rendered_pages` (DB-stored pre-rendered HTML + thin shell) approach we built
   to fix #1 is therefore moot — it still depended on the pipeline finishing, which
   it can't.

**The company pages already proved the right answer:** they read Supabase **directly
in the browser** (anon key, serving views), render client-side, and have worked
instantly and reliably the entire time. The front page should work the same way.

---

## 2. Goal

`outputs/index.html` becomes a **self-contained, client-side page** that queries
Supabase serving views with the publishable/anon key and renders the home page in
the browser. Properties:

- **Always current** — reads live data on every page load. No rebuild step.
- **No pipeline dependency** for the data it shows (with one caveat on signal/
  conviction *freshness*, see §6).
- **Cannot be clobbered** — it's static logic; the content is the DB.
- **Reuses** the company-page patterns already shipped (`outputs/company.html`):
  tier palette, signal badges, role chips, type filter, company links.

---

## 3. Scope — phased

### Phase 1 — Core (pure-data, always fresh) — highest value
- **View:** `public_recent_dealings_v` — dealings in the last ~14 days, with
  `signals[]`, company meta (sector, mkt cap, AIM), `url`, `fingerprint`.
- **Top tiles:** "Dealings this week" count, "Active clusters" count.
- **This Week table:** date, ticker → `company.html?ticker=`, director, role chip,
  type (BUY/SELL colour), value, **signal badges** (severity-sorted, tier-coloured,
  tooltips), RNS ↗ link. Type filter chips + sortable headers.
- **Active clusters panel:** derived **client-side** from recent buys — ≥2 distinct
  directors buying the same ticker within 30 days. Cards: ticker, company,
  #directors, aggregate £, first/last buy dates, link.

### Phase 2 — Analytics panels
- **Conviction Score panel:** view `public_conviction_v` (`conviction_scores` joined
  to `transactions` for names); render the rolling top picks with band badges.
  *Freshness depends on the pipeline having computed scores — acceptable (weekly).* 
- **Capital Deployed:** client-side aggregation of buy £ over time, split All /
  Small-cap / Large-cap, as a Chart.js line + 3-month moving average. Pure data →
  always fresh.

### Phase 3 — Polish / parity
- Brewing clusters (30–90d) + 8-week sparklines.
- Paper P&L tile (only if `paper_trades` is exposed).
- Optional data-quality/health panel (may stay off the public site).
- Visual-parity pass vs the old `render_index` output (headers, spacing, mobile
  breakpoints).

---

## 4. Serving views to create (Supabase)

- `public_recent_dealings_v` (Phase 1) — same projection as `public_company_v` but
  filtered to a rolling recent window across **all** tickers (`announced_at >=
  CURRENT_DATE - 14`), ordered by date desc.
- `public_conviction_v` (Phase 2) — `conviction_scores` + director/company names.
- Clusters + capital-deployed are derived client-side; add aggregating views only if
  payload size / browser cost demands it.
- All views: `GRANT SELECT ... TO anon`. RLS stays ON for base tables; plain views
  owned by `postgres` bypass base-table RLS for the anon reader — same proven pattern
  as `public_company_v` / `public_prices_v`.

---

## 5. Deployment model & cleanup

- **Replace `outputs/index.html`** with the live page (committed once). From then on
  the file is stable logic; the content is the DB.
- **`build_dashboard` must stop owning the front page:** remove `_publish_live_index`
  (the rendered_pages shell-swap) and stop rendering/overwriting `outputs/index.html`
  for the front page. This kills the clobber loop and the dead publish path.
- **`rebuild-pages.yml`** was built for the abandoned publish approach → remove or
  repurpose. (Performance pages have the *same* latency problem and are future
  candidates for the same live-conversion treatment.)
- `rendered_pages` table + `public_rendered_pages_v` view → drop once the live page
  is in (harmless to leave temporarily).

---

## 6. Out of scope here → separate task: **daily-refresh reliability**

The data pipeline (`scrape → eval_signals → backtest → conviction`) still must run to
keep the **signals** and **conviction** tables fresh, and it has the **same
transatlantic latency problem**. The live front page makes the *display* reliable,
but signal/conviction *freshness* still rides on the pipeline.

- **Important nuance:** the **dealings list itself** (This Week) comes from the
  `transactions` table, which the **scrape** writes directly (the scrape is fast —
  it's the *compute* steps that are slow). So the list is always fresh. Only the
  **signal badges** and **conviction** lag until `eval_signals`/conviction run.
- **Proposed fix (separate ticket):** run the heavy compute on the runner's **local
  SQLite** instead of over the network — bulk-download Supabase → local sqlite (a few
  big queries, fast), run the pipeline with `DD_FORCE_SQLITE` **in-process** (fast,
  like Rupert's PC did), then bulk-upload changed tables back to Supabase. Makes the
  daily refresh complete in **minutes**. This is the real long-term reliability fix.
- Interim safety net: an email/alert if a scheduled run fails.

---

## 7. Sequencing & effort

1. **Phase 1** (views + This Week + clusters + tiles) — ~½ session. Deploy + verify.
2. **Phase 2** (conviction + capital) — ~½ session.
3. **Phase 3** (polish/parity) — small.
4. **Daily-refresh re-architecture** (separate, parallel) — ~1 session; the bigger lift.

**Deploy/verify caveat:** Vercel domain can't be loaded by Claude's browser tools, so
visual verification is via Rupert's screenshot or direct DB checks after each deploy.

---

## 8. Decisions locked

- Front page = client-side direct-read (NOT pipeline-rendered). **Final.**
- Reuse company-page palette/badge/role/link code verbatim where possible.
- Phase 1 ships first and is independent of pipeline compute (list is always fresh).
- Keep all panels over time (no permanent feature loss) — phased, not dropped.
