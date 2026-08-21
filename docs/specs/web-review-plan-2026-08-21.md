# Reviewing filings from the website — plan

**Status:** proposal, awaiting Rupert's sign-off
**Written:** 2026-08-21
**Prereq shipped same day:** migration 018 (`pending_filings`), migration 019
(anon write lockdown), migration 020 (`public_pending_v`)

---

## 1. The problem, stated plainly

A filing the parser can't read is a missed signal, and a missed signal is the
product. Today the only way to rescue one is to sit at the Windows PC, run
`start.bat`, and use `review.html` against the local Flask server. The published
site has a Review link that 404s on everything.

Two separate things were broken, and only one of them is a website problem:

1. **The queue was being destroyed daily.** It lived only in
   `.scripts/_pending_review.json`, which is gitignored, so once the scrape moved
   into GitHub Actions (2026-06-25) it was written to a disposable runner and
   thrown away with it. **Fixed 2026-08-21** — it now lives in `pending_filings`.
2. **The review UI has no backend on the web.** Still true. That is what this
   plan is about.

## 2. What has to be true for web review to work

| Constraint | Consequence |
|---|---|
| Vercel serves static files only | No `/api/*`. Something else must accept writes. |
| The Supabase anon key is published in the site's JavaScript | Writes must be authenticated. Anon must stay read-only (enforced by migration 019). |
| Browsers cannot run Python | "Apply & publish" cannot recompute signals. That work must move to GitHub Actions. |
| The daily job `TRUNCATE`s and reloads every table from the runner's local copy | A web write only survives if it is written **before** the morning download, or is stored somewhere the reload does not touch. |

That last row is the one that quietly kills naive designs. It is the reason the
plan below uses an **append-only intent log** rather than editing `transactions`
from the browser.

## 3. Recommended shape

**The browser never edits data. It records decisions. The pipeline applies them.**

```
  Browser (logged in)                Supabase                  GitHub Actions
  ────────────────────               ────────                  ──────────────
  read queue        ──────────►  public_pending_v
  "reject this"     ──────────►  rpc: record_review_action
  "here are the     ──────────►  rpc: record_review_action
   correct numbers"                      │
                                   review_actions
                                    (append-only)
                                         │
                                         ▼
                              06:00 daily refresh
                              1. download from Postgres
                              2. apply_review_actions.py   ◄── NEW STEP
                              3. scrape / signals / backtest
                              4. upload to Postgres
                                         │
                                         ▼
                                  site is current
```

Decisions made during the day appear on the site the next morning. A decision
made *during* the 14-minute run simply waits for the following day — it is never
lost, because it stays unapplied.

## 4. Why not Vercel serverless functions

They would work. They are the wrong trade here: a second backend to secure,
another place to keep database credentials, and it duplicates what Supabase
already provides (auth, RPC, row-level security). One backend is cheaper to run
and much cheaper to reason about. **Recommendation: don't.**

## 5. Phases

### Phase 1 — See the queue from anywhere (~half a day)

- `review.html` reads `public_pending_v` from Supabase directly, the same way the
  front page already reads its data. Live, not a daily snapshot.
- Add `source_text` to `pending_filings`: the first ~8KB of the filing as plain
  text, written by the scraper. Reviewing means reading, and the cached HTML dies
  with the runner. At roughly 2,000 filings a year this is ~16MB — immaterial
  against Supabase's 500MB.
- No login needed. Read-only.
- **Value on its own:** triage from a phone. See what's stuck and how long it has
  been stuck, and decide what's worth the trip to the PC.

### Phase 2 — Make decisions from the web (~1–2 days)

- Turn on Supabase Auth, email magic link, allowlisted to Rupert's address only.
- New append-only table:

  ```sql
  review_actions (
    id          bigint identity primary key,
    kind        text  check (kind in ('reject','correct','add')),
    rns_id      text,          -- for queue decisions
    fingerprint text,          -- for corrections to an existing transaction
    payload     jsonb not null default '{}',
    reason      text,
    created_by  text not null, -- auth.uid()
    created_at  timestamptz not null default now(),
    applied_at  timestamptz,   -- null = not yet applied
    applied_run text
  )
  ```

- **Writes go through a `security definer` RPC, not a table grant.** The browser
  calls `record_review_action(...)`; the function checks `auth.uid()` against the
  allowlist and inserts one row. The browser never holds INSERT on any table, so
  the blast radius of a leaked session is one function with a fixed shape.
- `review_actions` is **excluded** from the migrate/download table lists, so the
  daily TRUNCATE-and-reload cannot touch it.

### Phase 3 — Apply the decisions (~half a day)

- New `.scripts/apply_review_actions.py`, wired into `refresh_all.STEPS`
  immediately after the download and **before** `run_scrape` (so a corrected
  filing isn't re-queued in the same run).
- It reads `WHERE applied_at IS NULL`, applies each action to the local SQLite
  (insert or correct a transaction; set `pending_filings.status`), and stamps
  `applied_at`. The rest of the pipeline then recomputes signals normally.
- Hard step, not soft: if decisions can't be applied, the run should fail loudly
  rather than silently publish stale judgements. (Contrast with what happened to
  `export_dashboard_json.py` — a step that failed quietly for seven weeks.)

### Phase 4 — Optional: "publish now" (~half a day)

A button that triggers the daily-refresh workflow on demand rather than waiting
for 06:00. **Do not put a GitHub token in the browser.** The safe route is a
Supabase Edge Function holding the token as a server-side secret and calling the
GitHub API. Only worth building if waiting until morning proves annoying.

## 6. What this does not change

The local tool stays. It is better for bulk work — it has the cached filing HTML
and can run the pipeline immediately. The web surface is for triage and single
decisions, which is the 90% case.

## 7. Risks, honestly

| Risk | Mitigation |
|---|---|
| Auth misconfigured, writes exposed | RPC + `auth.uid()` allowlist; anon already revoked (migration 019). Verify by attempting a write with the published anon key before going live. |
| A rejected filing gets resurrected by a later scrape | Already handled: the scraper's upsert never touches `status`, and only `status='pending'` rows load back into the working set. Covered by test. |
| Postgres is not authoritative between runs | True today, and the deeper issue. The download → compute → upload dance exists because computing over the network to eu-west-1 timed out. Worth revisiting separately — it is the root cause of this whole class of awkwardness. |
| Scope creep into a full CRUD admin panel | Ship Phase 1 alone first and use it for a fortnight before committing to Phase 2. |

## 8. Cost

No new spend. Supabase Auth and the extra table sit inside the existing free
tier; GitHub Actions minutes are unchanged (one extra fast step).

## 9. Recommendation

Ship **Phase 1 only**, then live with it for two weeks. It is cheap, needs no
authentication, and answers the question that actually matters day to day —
*what am I missing?* Phases 2–4 are only worth building if, having seen the
queue, you find yourself wanting to act on it away from the PC.
