# Upcoming earnings dates — diagnosis & scrape strategy (2026-06-05)

Companion to `product-improvements-2026-06-05-plan.md` item #5. This item carries
a real data-source decision, so it gets its own spec. Updated 2026-06-05 after
Rupert's steer: **get real forward dates from a central free source / company
sites; not real-time — a fortnightly or nightly batch is fine.**

## The problem Rupert reported

> "I cannot see many of the upcoming earnings signals. It is reporting on past
> earnings (still useful) but I cannot see signalling upcoming earnings… figure
> out how we can check for upcoming earnings dates."

## Diagnosis (confirmed in code, not assumed)

The pipeline has a **past-only** earnings calendar:

1. `backfill_reporting_dates.py` scrapes Investegate's RNS search for
   **historical** announcements (Prelim / Interim / Trading Statement) and writes
   one `reporting_dates` row per *past* hit.
2. `build_dashboard.py` (~lines 264–309) draws the 60-day badge by querying
   `reporting_dates WHERE ticker = ? AND report_date >= today`, then flags any
   transaction `0 ≤ (report_date − txn_date) ≤ 60`.

The query is correct — it asks for future dates — but the table only ever holds
**past** dates, so the future set is empty and **the badge is permanently dark**.
The "past earnings" Rupert sees is the same table read without the future filter.

**Root cause: there is no forward-looking earnings calendar in the system.**

## Source research (done 2026-05/06)

| Source | Forward-looking? | AIM / small-cap coverage | Scrapeable | Verdict |
|--------|------------------|--------------------------|-----------|---------|
| **lse.co.uk Financial Diary** (London South East) | **Yes** — date-addressable, navigable weeks/months ahead | **Yes** — AIM + main market, each event carries a TIDM ticker | **Yes** — server-rendered HTML, stable URL params | **PRIMARY** |
| Investegate "Notice of Results" RNS | Yes (date in body) | Yes (every company files via RNS) | Yes — reuses our existing Investegate scraper | Secondary / confirmation |
| Company IR "financial calendar" page | Yes | Per-company | Every site differs | Ad-hoc per-company only |
| Cadence projection (roll history forward) | Synthetic | Any ticker with history | n/a — computed | Last-resort gap-filler |
| Yahoo `calendarEvents` | Yes | Patchy | **401s** (the reason B-096b left Yahoo) | Ruled out |
| Investing.com / Wall Street Horizon etc. | Yes | Skews large-cap / US; paid for full | Mixed | Ruled out |

### Why lse.co.uk Financial Diary wins

Verified by fetching `https://www.lse.co.uk/share-prices/financial-diary.html`:

- **Forward-looking & date-addressable.** `?selected-date=DD-Mon-YYYY`, plus
  `?mode=day|week|month`. We can walk forward as far as we need.
- **Right event types, already grouped:** "Final Results", "Interim Results",
  "Q1/Q2/Q3/Q4 Results", "Trading Announcements", "AGMs" (+ dividends, economic —
  ignored). An event filter exists for exactly these.
- **Ticker on every row.** Each company links as `…?shareprice=TIN…` — the TIDM
  is right there, so matching to our `transactions.ticker` is direct.
- **Covers our universe.** The live page showed AIM small-caps (Cornish Metals
  TIN, Premier Miton PMI, Ondine Biomed OBI, Billington BILN) alongside FTSE
  names — i.e. the same AIM-heavy universe Directors Dealings tracks.
- **Server-rendered** — `web_fetch` returns the real table, no JS execution
  needed.

### The key efficiency win: scrape by date, not by company

Today's historical scraper hits Investegate **once per ticker** (~thousands of
fetches). The Financial Diary is keyed by **date**, so one fetch returns *every*
company reporting that day. To cover the next ~90 days:

- `?mode=week` → ~13 fetches per quarter, **or**
- `?mode=month` → ~3 fetches per quarter (validate month-mode lists the whole
  month's events during build; the day view confirmed the grouped-table shape).

Then filter the parsed events down to the tickers we actually hold. This flips
the cost model from thousands of calls to a dozen — which is what makes a nightly
run trivially cheap.

## Recommended strategy

**Primary:** nightly (or fortnightly — Rupert's call) batch scrape of the
lse.co.uk Financial Diary for the next ~90 days, filtered to held tickers,
written as **confirmed** future `reporting_dates`.

**Secondary (optional, later):** for held tickers the Diary doesn't cover, fetch
Investegate "Notice of Results" RNS and parse the future date from the body.

**Ad-hoc:** a per-company helper that reads one company's IR financial-calendar
page on demand (Rupert "adhoc by company website page"). Kept manual — IR sites
are too heterogeneous to batch reliably.

**Last-resort fallback:** for a held ticker no source covers, roll its own
history forward (annual Prelim, ~6-month Interim) and store as **`est`**, clearly
labelled, so a badge still appears. Never shown as a confirmed date.

This gives real, confirmed dates for the bulk of the book from one cheap central
source, with graceful degradation rather than silence.

---

## Phase A — lse.co.uk Financial Diary scraper (build first)

**New Zone-B script:** `backfill_lse_diary.py` (writes DB → Rupert runs it from
PowerShell; Claude never runs it).

**Fetch.** Walk `?mode=week&selected-date=…` from this week forward ~13 weeks
(≈90 days). Reuse the polite-fetch scaffolding from `backfill_reporting_dates.py`
(custom User-Agent, rate-limit, gzip, retry/backoff, per-page cache with a short
TTL — diary entries move, so cache ~24–48h, not 30 days).

**Parse.** For each results-type section (Final / Interim / Q1–Q4 / Trading
Announcements), extract `(date, company_name, TIDM)` from the table rows
(`shareprice=<TIDM>` in the row's links). Map event type → our `report_type`:
- Final Results → `PRELIM`
- Interim Results → `INTERIM`
- Q1–Q4 Results → `QUARTERLY` (new type) or fold into `TRADING_STMT` — decide at
  build; keep distinct if cheap.
- Trading Announcements → `TRADING_STMT`
- AGM / dividends / economic → **ignore** (closed-period flag is about results).

**Filter & write.** Keep only TIDMs present in `transactions.ticker`. Upsert into
`reporting_dates` with `source='lse_diary'`, `confidence='confirmed'`. Because
diary dates can shift, **replace** the prior `source='lse_diary'` future rows for
each ticker each run rather than accumulating.

**Schema (migration 009).** `reporting_dates` already has `source`. Add a nullable
`confidence` column (`'confirmed' | 'est'`, default `'confirmed'` for existing
rows). No other change — `build_dashboard`'s `report_date >= today` query then
picks up the new confirmed future rows unchanged.

**Display (#5 proper — "all relevant boxes").** `build_dashboard` badge logic is
unchanged. Extend the badge beyond the company page (where
`render_company._reporting_date_badge` + per-txn `near_badge` already live) to the
**main dashboard dealings table** and the **This Week table** for any transaction
inside the 60-day pre-results window. Show the date and, when `confidence='est'`,
an "(est)" qualifier so a synthetic date never reads as confirmed.

**Effort.** M.

**Risks.**
- *Ticker mismatch.* LSE TIDMs vs our stored tickers may differ on suffixes
  (`.`, dual lines like YNGA/YNGN, preference-share codes). Build a small
  normalisation + an unmatched-TIDM log to audit coverage. Reuse the AIM/`.`
  handling already in the meta backfill.
- *ToS / politeness.* lse.co.uk is free public data ("provided free of charge,
  as-is") — scrape politely (identified UA, rate-limit, cache, low frequency).
  Note: it is third-party data; treat as best-effort, not authoritative.
- *Date shifts.* Companies move results dates; the nightly/fortnightly refresh +
  replace-on-rerun keeps us current. Short cache TTL matters here.
- *FUSE / Zone B.* Writes the DB → Rupert runs it; Claude never does.

**Test plan (Claude-safe — no DB writes, runs in sandbox per CLAUDE.md):**
- Unit on the pure parser against a **saved sample** of the diary HTML (commit a
  fixture): asserts it extracts the right `(date, TIDM, report_type)` tuples and
  ignores AGM/dividend/economic rows.
- Unit on ticker normalisation/matching for the awkward cases above.
- Integration against a `/tmp` copy of the DB (read-only) to report coverage:
  how many held tickers get ≥1 confirmed future date.

---

## Phase B — "Notice of Results" RNS fallback (optional, later)

Extend `backfill_reporting_dates.py` with a 4th search type "Notice of Results";
the announcement date is past but the **results date is in the body** ("…will
announce results on 12 June 2026"). Parse that forward date, write
`source='investegate', confidence='confirmed'`. Use only for held tickers the LSE
Diary missed. Body-text date parsing needs its own regex + fixtures (fuzzier than
the search-result parsing already in the script). **Decision:** ship Phase A
first; add B only if Diary coverage gaps prove material.

---

## Cadence & pipeline sequence

Rupert's steer: not real-time. **Recommend nightly** (cheap — ~13 week-fetches),
**fortnightly acceptable** as a floor. Slots into the existing backfill order:

```
1. fetch_sectors.py
2. backfill_ticker_meta.py
3. backfill_benchmarks.py
4. backfill_reporting_dates.py     # historical (unchanged — keeps past dates)
5. backfill_lse_diary.py           # NEW — confirmed FUTURE dates (primary)
6. (optional) backfill_expected_reporting_dates.py  # est. fallback for gaps
7. export_dashboard_json.py
8. build_dashboard.py
```

If automating: a scheduled task can run the Zone-B batch on Rupert's machine
(Windows Task Scheduler) on the chosen cadence; it must respect the FUSE/Zone-B
rule (runs as Windows Python, not via Claude bash).

## Open questions for Rupert

1. **Cadence:** nightly (recommended, still cheap) or fortnightly?
2. **Quarterly results:** keep `Q1–Q4` as a distinct `QUARTERLY` type, or fold
   into the trading-statement bucket for the badge?
3. **Fallback estimates:** want the synthetic "(est)" gap-filler at all, or only
   ever show confirmed dates and leave gaps blank?

## Sources

- [London South East — Financial Diary](https://www.lse.co.uk/share-prices/financial-diary.html)
- [Live Charts UK — Company Results & Financial Calendar](https://www.livecharts.co.uk/share_prices/resultscalendar.php)
