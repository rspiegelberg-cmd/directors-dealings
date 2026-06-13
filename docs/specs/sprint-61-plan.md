# Sprint 61 Plan — New Data Feeds (Conviction + Short Interest)

Kicked off 2026-06-10. Cycle 4, target 13 Jun. Items: B-156 (DIR-87), B-163 spike (DIR-94), B-164 (DIR-95).
Planned by three parallel agents 2026-06-10; consolidated with two corrections (migration
numbering collision fixed: B-156 = 013, B-164 = 014; deploy-order discrepancy flagged in §4).

**Total estimate: 16 pts (5 + 8 + 3) — over the ~9/sprint average. Build order: B-156 → B-164 → B-163 spike**
(spike's overlap test consumes B-156 output, so it goes last).

---

## 1. B-156 — Resulting-holding parse → % stake increase (5 pts)

### Reality check from the corpus
~93% of the 7,442 cached filings use the MAR Article 19 template, which has NO
resulting-holding field. The figure appears only in classic-narrative filings:
~13% of purchase-like filings. **Acceptance bar: ≥10% of BUY rows populated (stretch 15%)**,
not "most". The MAR "Aggregated volume" must explicitly NOT be captured (guard test).

Real pattern examples: 8855732 ("Following this transaction Rob Thomas is beneficially
interested in 5,629 shares"), 8861668, 8863449, 8863696 ("the total holding of MS YVONNE
STILLHART is 13,718 Shares"), 8858155 (Wynnstay — table "Resulting beneficial interest in
Ordinary Shares"), 8856771 (table "Total resulting beneficial holding").

### Build steps
1. **Migration 013** `013_resulting_shares.sql`: `ALTER TABLE transactions ADD COLUMN resulting_shares INTEGER;`
   Wire into `db.py::_apply_schema_migrations` (12→13). Decision: do NOT store the derived
   pct (pure function of shares + resulting_shares; computed at consumption time, B-160 pattern).
2. **Parser** (`parse_pdmr.py`): document-level helper run once per filing, attached to all
   4 emission paths as `"resulting_shares"` (int|None). Fingerprint untouched.
   - Family N1 — narrative regex anchored on "Following the/this/these transaction(s)…"
     capturing the share count + window for name attribution; finditer for multi-PDMR filings.
   - Family T2 — classic table with header `resulting … beneficial (interest|holding)`;
     (name, value) pairs. Skip percentage columns.
   - Attribution: single candidate + single row → attach; else require surname token match; else None.
   - Guards (fail → None + warning, never a dropped row): integer ≥1; reject year-values
     1990–2099; BUY requires resulting_shares ≥ shares (`resulting_lt_shares` warning);
     never sourced from "Aggregated volume"/"issued share capital".
   - Out of scope: %-only statements; LLM parser path (stays NULL).
3. **Write paths**: `db.py::upsert_transaction` — add column + `COALESCE(resulting_shares, ?)`
   on the existing-row branch. `reparse_corpus.py` `_apply_insert`/`_apply_update` — add column
   with COALESCE.
4. **Backfill (the main vehicle)**: new `.scripts/backfill_resulting_shares.py`, cloned from
   `backfill_buy_strictness.py` (rows where column IS NULL, parse cached HTML per rns_id,
   match by fingerprint, UPDATE in place). Preview default, `--confirm` to apply. Audit log
   JSONL → `.data/_resulting_shares_backfill.log`. Sweep all types; report BUY population rate.
5. **Derived metric** `holding_pct_increase = shares / (resulting_shares − shares)`:
   NULL when resulting NULL, type != BUY, or prior == 0 (first-time holding — use existing
   first-buy flag as its own bucket). No winsorisation at write time.
6. **Surfacing**: `backtest.py` — extend `_select_firings` SQL + HEADER with
   `resulting_shares`, `holding_pct_increase` before `windows_available` (57 cols);
   `export_dashboard_json.py` — verify `SELECT t.*` flows through any key whitelist.
7. **Tests** `test_b156_resulting_shares.py`: 5 narrative forms; table family (2 directors);
   attribution rules; BUY guard; MAR-template false-positive fixture (clean_buy_9562545.html →
   None); end-to-end parse_announcement on new fixture 8855732.html; upsert round-trip with
   migration 013; HEADER position/length; pct math incl. NULL cases.

Files: `parse_pdmr.py`, `db.py`, `backfill_resulting_shares.py` (new), `backtest.py`,
`reparse_corpus.py`, `013_resulting_shares.sql` (new), tests + fixture.

---

## 2. B-164 — FCA short-interest ingest (8 pts)

### Source spec (verified live 2026-06-10)
- URL (stable, overwritten daily): `https://www.fca.org.uk/publication/data/short-positions-daily-update.xlsx` (~3.1 MB).
- Two sheets with DATED names (match by prefix): `Current Disclosures DD.MM.YYYY` (594 rows
  today) and `Historic Disclosures DD.MM.YYYY` (106,726 rows, back to ~2012).
- Columns: Position Holder | Name of Share Issuer | ISIN | Net Short Position (%) (0.0 rows
  = closed below 0.5%, KEEP) | Position Date (Excel datetime).
- Threshold ≥0.5%, 0.1% increments, each UK business day.
- **⚠️ REGIME CHANGE — 13 July 2026 (PS26/5):** individual disclosure ends; replaced by
  anonymised aggregated (ANSP) per-issuer monthly. The historic sheet is a one-shot backfill
  of 13 years of holder-level data that becomes unfetchable after 13 Jul. **Ingest before then.**
  ANSP adapter = separate follow-up ticket once FCA publishes the new format.
- New dependency: **openpyxl** (Rupert: `pip install openpyxl`).

### Build steps
1. **Migration 014** `014_short_positions.sql` (renumbered from agent's 013; head 13→14):
   table `short_positions` (id, position_holder NULL-able for future ANSP rows, issuer_name,
   isin, ticker NULL until mapped, net_short_pct REAL, position_date ISO, source DEFAULT
   'ssr_daily', fetched_at; UNIQUE(position_holder, isin, position_date, source); indexes on
   (ticker, position_date) and isin) + table `isin_ticker_map` (isin PK, ticker, method
   'name_match'|'openfigi'|'manual', mapped_at).
2. **Aggregate helper** `aggregate_short_pct(conn, ticker, date)`: as-of join — per holder,
   most recent row ≤ date, sum pcts (0.0 exits self-cancel). ROW_NUMBER CTE.
3. **Ticker mapping** (tickers_meta has NO isin/name column; anchor = transactions.company):
   (a) normalised company-name match (strip PLC/LIMITED/GROUP/HOLDINGS, punctuation);
   (b) OpenFIGI ISIN lookup fallback (free, cached into isin_ticker_map);
   (c) manual override CSV `.data/_isin_overrides.csv` (pattern of _excluded_it_cef.csv).
   Expected BUY-ticker coverage only ~10–25% (0.5% threshold skews large-cap; our universe
   skews AIM) — acceptable for an exploratory column; coverage metric makes it explicit.
4. **Script** `.scripts/backfill_short_interest.py` (Zone B): template backfill_lse_diary.py +
   db_health backup pattern. Download to `.scripts/_short_cache/short-positions-YYYYMMDD.xlsx`
   (dated copies = our own archive post-regime-change). Idempotent upsert (ON CONFLICT
   DO UPDATE). Flags: `--from/--to`, `--dry-run`, `--remap`. ASCII-only prints.
   **Mandatory coverage log**: % of distinct BUY tickers (443 in current snapshot; 627 total)
   with ≥1 disclosure within ±90d of any BUY announcement; printed + appended to
   `.data/_short_coverage.jsonl`.
5. **backtest.py**: one HEADER col `short_pct_at_announcement` (NULL-safe, reuses the
   aggregate helper).
6. **Tests** `test_short_interest.py`: in-test 2-sheet XLSX fixture (no network); sheet-prefix
   matching; Excel-datetime→ISO; 0.0 rows kept; name normaliser; upsert idempotency; as-of
   aggregate (updates supersede, exits cancel); coverage calc; migration head-pin "14".

---

## 3. B-163 — Salary-multiple feasibility spike (3 pts)

### Regulatory hook (verified)
Main Market: single-total-figure table per director is mandatory (Sch 8, SI 2008/410 as
amended by SI 2013/1981). AIM: Rule 19 requires per-director remuneration in accounts.
BUT: board directors only → **~27% of BUY rows (PCAs + non-board PDMRs) are structurally
out of scope** — must be stated in the memo. Companies House iXBRL likely tags only
AGGREGATE remuneration (verify during spike); assume PDF table extraction.

### Protocol
1. **Sample**: 20 companies, 4 per cap stratum (<£50m, £50–250m, £250m–1b, £1–5b, >£5b),
   drawn from recent buy-signal firings, targeting the specific buying director; includes
   4 NEDs (fees) and ≥4 AIM names. Candidate list in the agent output (KZG, TOO, CAD, SYS1 /
   ARBB, IGR, ATOM, PANR / VTY, POLR, ASC, MAB1 / PSN, HOC, BKG, GNC / BA., ULVR, BNZL, III).
   Avoid over-weighting mega-cap routine repeat buyers (PRU/RR/BATS/LLOY).
2. **Method ladder** (record rung per report): (a) Companies House API accounts fetch
   (~3 min once a fetch script exists — script writes to /tmp or docs/, NEVER .data/);
   (b) issuer IR annual-report PDF (~5 min); (c) manual (~5–10 min); else FAIL.
3. **Record CSV** (20 rows): ticker, director, stratum, aim_flag, rung, figure_found,
   figure_type (single-figure / Rule-19 / aggregate-only), pay_gbp, fy_end, machine_readable,
   minutes_spent, confidence, notes.
4. **Go threshold**: ≥80% of in-scope sample at <10 min/report avg → GO. 60–79% or
   10–20 min → CONDITIONAL (large-cap only). <60% or aggregate-only dominant → NO-GO.
5. **Overlap test (after B-156 lands)**: Spearman rho on log(salary_multiple) vs
   log(%-stake-increase) for the same transactions. |rho| ≥ 0.7 → redundant, close B-163
   as covered by B-156. < 0.5 → measures something different, stays a candidate.
6. **Deliverable**: ½-page go/no-go memo (template in agent output).

---

## 4. Deploy sequence (Rupert, Windows PowerShell — run after each build lands)

⚠️ **Order discrepancy to verify at build time:** the B-156 agent claims backtest joins the
signals table (eval first); our standing rule (memory, B-151 incident) is **backtest BEFORE
eval_signals whenever backtest HEADER/CAR columns change**. Default to the standing rule;
verify `backtest.py`'s actual inputs during the B-156 build and correct this section if needed.

After B-156:
```powershell
cd C:\Dev\DirectorsDealings
python -m unittest discover -s .scripts -p "test_*.py"
Copy-Item .data\directors.db .data\directors.db.pre-b156.bak
python .scripts\backfill_resulting_shares.py            # preview (applies migration 013)
python .scripts\backfill_resulting_shares.py --confirm  # update-in-place + audit log
python .scripts\backtest.py
python .scripts\eval_signals.py --rebuild
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

After B-164 (data-only first pass can stop after the coverage log + snapshot):
```powershell
pip install openpyxl
python .scripts\backfill_short_interest.py --dry-run    # parse + mapping report, no writes
python .scripts\backfill_short_interest.py              # full historic ingest + coverage log
python .scripts\snapshot_db.py
# once the HEADER column lands:
python .scripts\backtest.py
python .scripts\eval_signals.py --rebuild
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

B-163 spike: no Zone-B writes. CH-API script outputs land in docs/research/, record CSV in
docs/research/b163-spike-sample.csv, memo in docs/research/b163-spike-memo.md.

---

## 5. Risks / flags

- **Scope = 16 pts vs ~9 velocity.** Mitigation: build order B-156 → B-164 → B-163; B-163
  spike can slip to Sprint 62 without harm (overlap test needs B-156 data anyway).
- **B-164 deadline pressure is real but manageable**: historic ingest must run before 13 Jul.
- **B-156 expectation reset**: 10–15% population, not "most" — the feature will be sparse;
  factor scan must treat NULL as its own bucket.
- **Migration sequencing**: B-156 = 013, B-164 = 014. If B-164 somehow lands first, swap —
  but build order above prevents this.
