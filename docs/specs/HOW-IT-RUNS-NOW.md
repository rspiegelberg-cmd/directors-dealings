# How it runs now — operator guide

*One page. Written for Rupert. Updated 2026-06-26 (cloud migration M5 close-out).*

## The short version

Everything runs in the cloud. **Your PC does not need to be on.**

- The **data** lives in Supabase (a hosted Postgres database).
- The **website** (https://directors-dealings.vercel.app/) reads that data
  straight from your browser, so it's always current.
- Once a day at **06:00 UTC**, a GitHub robot wakes up, refreshes the data,
  and updates the site. You don't have to do anything.

## The daily refresh (automatic)

GitHub Actions runs `.github/workflows/daily-refresh.yml` every morning. It:

1. Downloads the current data from Supabase to a temporary copy.
2. Runs the full pipeline (scrape new RNS filings → score signals →
   backtest → build) on that copy. Takes about 6 minutes.
3. Uploads the new results back to Supabase — but **only if the pipeline
   finished cleanly**, so a bad run can never overwrite good data.
4. Rebuilds the site.

If anything fails, GitHub emails you automatically (no setup needed).

## When you want to refresh *right now*

Two ways, both with the PC off or on:

- Click the **↻ Refresh** button on the live site, **or**
- Go to the repo's **Actions** tab → *Daily refresh* → **Run workflow**.

## If you change the code

Editing code is the only thing that still happens on your PC. To publish a
change, double-click **`push_to_github.bat`**. That commits your changes and
pushes them to GitHub; the site redeploys a minute or two later.

That's the whole deploy story now — **one button, no database backup step**
(the database backs itself up in Supabase, and every daily run is saved in the
GitHub history).

## What changed from the old (local) setup

| Old | Now |
|-----|-----|
| Data in a local SQLite file (`.data/directors.db`) | Data in Supabase Postgres (the source of truth) |
| PC had to be on to refresh | Runs in the cloud, PC off |
| `start.bat` ran a local web server | Live site reads Supabase directly; `start.bat` is now just an optional local preview |
| `backup_db.bat` copied the DB to OneDrive/Drive | Retired — Supabase + GitHub history are the backup |
| FUSE corruption rules in `CLAUDE.md` | Gone — nothing writes a local DB anymore |

## The archived local database

`.data/directors.db` is kept as a **cold backup only**. It is no longer read
or written by anything live. If you ever want it fully out of the way, move it
to a backup folder — see the close-out note in the migration tracker. Keeping
it costs nothing and gives you a local snapshot of the data as it stood at
migration time.

## Where the details are

- `docs/specs/cloud-migration-sprint-tracker.md` — the full migration record.
- `CLAUDE.md` → "Backend & how it runs now" — the developer-facing version.
- `.github/workflows/daily-refresh.yml` — the actual daily job.
