# B-194 — Runner-local SQLite compute (design)

**Status:** designed (infra-review 2026-06-24) + Phase 1 coded. Not yet wired into the
daily workflow.

## Problem
`eval_signals` + `backtest` fire thousands of tiny per-row queries. On US GitHub runners
against eu-west-1 Supabase, each pays ~80–100ms transatlantic latency → they exceed
45/60-min timeouts and the daily pipeline can't finish. So signals/conviction never
refresh in the cloud.

## Fix (3 bulk transfers instead of thousands of latency-bound queries)
1. **Download** all Supabase tables → a fresh local SQLite on the runner (a few big SELECTs).
2. **Run** the whole pipeline against that local SQLite with `DD_FORCE_SQLITE=1` (in-process,
   microsecond queries — the original/native path the scripts were written for).
3. **Upload** the changed tables back to Supabase.

Sound because the compute scripts were written for SQLite first (Postgres support added
later, B-176/B-179); running on SQLite is the best-tested path and bypasses the dialect
wrapper entirely.

## Key mechanics (verified, file:line)
- `db.backend()` (db.py:58-77): returns sqlite unless `DD_DATABASE_URL` set; **`DD_FORCE_SQLITE`
  forces sqlite even when the URL is set** → the lever. Download/upload scripts use the URL
  (raw psycopg); the pipeline step sets `DD_FORCE_SQLITE=1`.
- Fresh SQLite auto-schema: `db.migrate(conn)` (db.py:475) applies `db_schema.sql` + migration
  chain → head. A blank runner DB becomes schema-correct on first connect.
- FK-safe table order (migrate_to_postgres.py:42-55): transactions → tickers_meta → prices →
  meta → reporting_dates → short_positions → isin_ticker_map → director_pay → backtest_runs →
  signals → paper_trades → conviction_scores. Same order both directions.

## Sync strategy: full TRUNCATE+reload (NOT upsert)
The runner's SQLite is a faithful full copy + this run's changes → it IS the desired end
state. Replace-all is correct by construction and handles deletions (eval_signals DELETEs
stale signals). Safe because nothing else writes Supabase between download and upload (front
pages are read-only; the scrape runs on the runner; `concurrency` blocks overlapping daily
runs). One low risk: a manual cloud-targeting Zone-B write during the ~minutes window would be
overwritten — document a "no manual writes during 06:00 UTC" note.

## Tables
- **Download:** all 12 (download-all/upload-all is simplest + safe; `prices` ~780k rows is the
  only heavy one — bandwidth-bound, seconds).
- **Upload (written by compute):** transactions, prices, tickers_meta, reporting_dates,
  signals, paper_trades, backtest_runs, conviction_scores, meta. Upload all 12 anyway (the 3
  manually-maintained tables round-trip unchanged — harmless, removes risk).

## Workflow change (daily-refresh.yml)
```
- Download Supabase → local sqlite   (download_from_postgres.py; env DD_DATABASE_URL)
- Run pipeline against local sqlite   (refresh_all.py; env DD_FORCE_SQLITE=1; NO DD_DATABASE_URL)
- Upload local sqlite → Supabase      (migrate_to_postgres.py; if pipeline succeeded; env DD_DATABASE_URL)
- (existing) commit & push rebuilt outputs/
```
`refresh_all.py` needs no change — with DD_FORCE_SQLITE the existing db_health backup path
activates (a happy accident). Once compute is local, cut the signals/backtest timeouts
(refresh_all.py:162-165) and job timeout-minutes (120→~30).

## Build phases
1. **download_from_postgres.py** + round-trip parity proof. **DONE (coded).** Riskiest new
   code; prove in isolation first.
2. Confirm `migrate_to_postgres.py` reused unchanged as the upload (dry-run).
3. Wire a **copy** of the workflow (`workflow_dispatch` only) so the live 06:00 job is
   untouched; trigger manually, watch timings drop, verify parity.
4. Cut timeouts + promote to the scheduled job.

## Rupert actions
- **No new secret** (reuses existing `DD_DATABASE_URL` / `DD_FMP_API_KEY`).
- Phase 1 test (now): run `download_from_postgres.py --sqlite-path .data\_test_download.db`
  locally and confirm `RESULT: PASS`. Reads Supabase, writes only the throwaway file.
- Phase 3: one manual trigger of the temp workflow + a glance at parity afterward.

## Net new code
One ~60-line script (`download_from_postgres.py`, DONE) + ~6 lines workflow YAML + a timeout
trim. Everything else reuses `migrate_to_postgres.py` and the existing `DD_FORCE_SQLITE` lever.
