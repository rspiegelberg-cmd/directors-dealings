# Roadmap & Sprint plan — Sprint 20 onward

**Date:** 2026-06-03
**Author:** Product Manager (Claude) consulted by Rupert; synthesised from `docs/backlog.md`, `docs/backlog-2026-05-29.md`, the four roadmap specs (07/08/09/10), the 2026-06-02 incident plans, and project memory.
**Status:** DRAFT for Rupert review. Sprint numbering deliberately **restarts at 20** to avoid confusion with the old Sprint 1–15 sequence.
**Purpose:** one place that says (a) what shipped on 2 June, (b) everything still open, in plain English, and (c) how it's grouped into shippable sprints with a clear running order.

---

## 1. What shipped 2026-06-02 (so we stop re-listing it)

The ingest-gate incident is **done**. All four faults Rupert spotted are fixed and verified:

| Problem | Plain description | Status |
|---------|-------------------|--------|
| A — over-strict filter | A harmless warning was binning whole good filings. Gate split into "blocking" vs "advisory". | ✅ Shipped |
| B — holding pen never emptied | The 4,352-item backlog file now drains; recovered real dealings (e.g. a GNC £150k NED cluster buy). 4,352 → 4,281. | ✅ Shipped |
| C — buy/sell flips | A stray word elsewhere on the page flipped buys into sells. Now reads only the right cell. 5-buy reparse confirmed: JMAT/GEN/UTL/CAD/PSN back to BUY. | ✅ Shipped |
| D — scraper skipped ~1-in-6 filings | Keyword filter replaced with "trust Investegate's own category"; date window + pagination fixed; non-RNS providers now captured. | ✅ Shipped |

Source-of-truth record: memory `project-ingest-gate-incident-2026-06-02`. The two incident plan docs now carry a `SHIPPED 2026-06-02` header.

**Also shipped 2026-06-03 (MTM column hotfix):** Rupert spotted the MTM column showing UTG −41% and CLX +40% — wildly wrong. Root cause: the newest filings store `announced_at` as a headline date (`"02 Jun 2026"`) not ISO; the exporter's blind `[:10]` slice turned it into a garbage string that sorted before every real date, so the entry price defaulted to the oldest close on record (~1yr old). Display symptom **fixed in `export_dashboard_json.py`** via a new `_to_iso_day()` normaliser; verified against live data (7 rows affected, all 1–3 Jun). The **upstream cause and a signal side-effect remain open → tracked as B-094 in Sprint 20.**

---

## 2. Roadmap status — the four big specs

| Spec | What it is | Status (verified 2026-06-03) |
|------|-----------|------------------------------|
| **07 — Conviction sizing** | Size paper trades by the director's £ conviction instead of a flat £1k. | **OPEN but superseded.** The "paper book" it extends (`paper_trader.py`) was never built, and its premise ("are signals negative only because of flat sizing?") was overtaken by the B1/B2/universe cleanup. **Recommend: close it; fold a smaller "does alpha scale with £ size?" question into Sprint 24.** |
| **08 — Behavioural signals + universe cleanup** | New B1 (lone conviction buy) & B2 (anti-crowded cluster kill) signals; strip out investment trusts; count only "real" buys. | **Mostly SHIPPED.** B1, B2, the strict-buy column, and the trust exclusion are all live. Only the "gap to 65% hit rate" tail is open — and that maps onto the signal-review items in Sprint 24. |
| **09 — Company search box** | Type a ticker or name, jump to that company's page. | **✅ SHIPPED** (~2026-05-28). Header typeahead (`companySearch`) is live in `outputs/index.html`. No sprint needed. |
| **10 — PDMR review & edit tool** | A screen to fix wrong rows and hand-key failed filings, safely. | **OPEN.** Large, multi-phase, writes to the database. → Sprint 25 (after the backup is fixed). |

---

## 3. The plan — Sprints 20 → 25

Guiding principle: **fix the data, then protect it, then complete it, then build on it.** You just shipped a big ingest change; the worst thing now would be to layer new features and signal judgements on top of numbers we haven't yet confirmed are right.

### Sprint 20 — "Trust the numbers" (data correctness) — ✅ DONE 2026-06-03

> Closed. B-093 (304 buys recovered), year-as-shares + value-as-price corrected via scoped reparse + surgical delete, F1 outlier already gone. Pence/pounds + multi-leg price errors deferred to a parser slice. See `sprint-20-plan.md` and the shipped log.

| Item | ID | Plain explainer | Size |
|------|----|-----------------|------|
| Price pence vs pounds audit | B-060 | UK shares are quoted in pence but filings mix pence and pounds. If some prices are stored in the wrong unit, every £ value on every page is wrong by 100×. Confirm and fix. | M |
| "Year-as-shares" re-fix | (plan written) | A bug occasionally recorded the calendar year (e.g. "2026") as the number of shares. The earlier fix was incomplete — 32 suspect rows remain. Finish it. Plan: `year-as-shares-refix-plan-2026-05-31.md`. | S–M |
| UTL/UIL NED-buy not firing | B-093 | A £69,922 NED buy stores correctly but fires no T3 signal. £69,922 clears the £10k bar easily, so it's almost certainly being (correctly) excluded as an investment trust — confirm that, or find the bug. | S |
| +4232% F1 outlier | (from spec 07) | A single broken record (likely an unadjusted stock split) distorts the average return for the F1 signal. Same family of bug as the two above. | S |
| `announced_at` non-ISO date format | B-094 | The newest scraper path stores the announcement date as headline text (`"02 Jun 2026"`) instead of ISO (`2026-06-02`). This caused the MTM column bug Rupert spotted on 2 Jun (display symptom already hotfixed 2026-06-03). **Two pieces remain:** (1) fix the scraper to write ISO `announced_at` at source, so the whole pipeline gets clean dates rather than each consumer defending itself; (2) harden the two timing signals — `b1_lone_conviction_buy` and `b2_crowded_cluster_kill` both do `strptime(announced_at[:10])` inside a try/except and **silently return None on a non-ISO date**, so those signals quietly fail to evaluate on the freshest filings. Also one-off normalise the ~7 rows already stored non-ISO (or fold into the next reparse). | S–M |

*Why first: every feature and every signal verdict downstream is only as trustworthy as these rows, and these are known-wrong. B-094 belongs here specifically because it doesn't just dirty a displayed number — it silently suppresses two signals on the newest filings, and §3's whole premise is that you can't judge signals on data you don't trust.*

### Sprint 21 — "Process insurance" (operational safety) — ✅ DONE 2026-06-03

> Closed. B-024 (backup) + B-015 (sweep safety) verified already-implemented and marked RESOLVED; B-084 clean. B-012 needs a one-line Windows-side `unittest discover` to triage; B-085 (browser smoke-check) deferred to Sprint 24. See `sprint-21-plan.md`.

| Item | ID | Plain explainer | Size |
|------|----|-----------------|------|
| Repair self-healing backup | B-024 | The "database backs itself up after every run" safety net is broken — the backup file stopped refreshing. You've lost data to file-corruption four times; this must work before any DB-writing sprint. | M |
| Triage stale test suites | B-012 | A handful of old automated tests error out, creating noise that hides real regressions. Fix or retire them so "all green" means something. | S |
| Sweep safety check | B-015 | A date-validation safety check is silently skipped for some recovered rows. Close the gap. | S |
| Hidden-element + browser smoke check | B-084 / B-085 | Two recent UI bugs were invisible to code review and only showed up in a live browser. Add a cheap manual/headless check to catch that class of bug. | S–M |

*Why second: these are the seatbelts. A broken backup is an unacceptable risk to carry into Sprint 26 (the editor), which writes to the database heavily.*

### Sprint 22 — "Recover the lost filings" (data completeness) — ◑ Phase 1 DONE; B-090 is NEXT after B-094

> Phase 1 banked: B-091 historical backfill shipped; B-094 (backfill missing the IT/CEF filter) found + fixed + purged. **B-090 (bundled ~2,349) and B-092 (non-RNS layouts ~200) deferred to a dedicated "Recovery" sprint** — fragmented-layout parser work, done together with tests. See `sprint-22-plan.md`.

| Item | ID | Plain explainer | Size |
|------|----|-----------------|------|
| Historical backfill | B-091 | The discovery fix stops *future* losses. This goes back and re-scrapes launch→today to recover everything the old keyword filter silently dropped over the project's life. The drain can't reach these — they were never downloaded. | M |
| Non-RNS provider layouts | B-092 | The wider net now catches filings from other providers (TotalEnergies, Next 15, M&G, Magnum, Ferguson). Their page layouts differ and may not parse. Scan the holding pen after the next live run and add support where needed. | S–M |
| Bundled multi-director recovery | B-090 | The biggest missing chunk: ~2,271 filings that announce several directors at once, which the parser refuses rather than risk mis-attributing. Recovering them is a deliberate multi-row extraction project — high value (Hikma, Dr Martens, Great Portland land here), high risk. | L |

*Why third: you fixed discovery; now harvest what it found. B-090 is a real project, so it anchors the sprint. Sequenced after correctness so we're not auditing a flood of brand-new rows on top of unverified ones.*

> *Company search box (spec 09 / B-059) is already shipped — header typeahead in `outputs/index.html`. No sprint required.*

### ~~Sprint 23 — Signal taxonomy reckoning~~ — DEFERRED (2026-06-03, Rupert decision)

Not ready to retire signals yet. Items B-080/B-081/B-082/B-078 are parked until
there is enough post-cleanup data to judge fairly. Sprint numbering jumps 23→24.

### Sprint 24 — "Performance lens polish" (low-risk niceties)

| Item | ID | Plain explainer | Size |
|------|----|-----------------|------|
| Horizon toggle | (Sprint-15 draft) | Switch the cohort charts between T+1 / T+21 / T+90 / T+252 so you read each signal at the holding period you actually trade. Backend already computes all four; this is wiring. | M |
| CSV export of drill-down | B-075 (29-May) | Pull a month's trades into a spreadsheet for your own analysis. | S |
| Persist selected view | B-076 (29-May) | Re-open the page where you left off; shareable links. | S |
| Remove dead sparkline helper | B-083 | Tidy-up after the old 12-week column was removed. | XS |

*Why grouped: low-risk polish that gates nothing. Slot opportunistically.*

### Sprint 25 — "PDMR editor" (spec 10, multi-gate)

| Item | ID | Plain explainer | Size |
|------|----|-----------------|------|
| PDMR review & edit tool | spec 10 | A dashboard screen to (a) work the failed-filing queue — reject junk or hand-key the real trade — and (b) open any parsed row beside its original filing and correct it. Every edit staged, audited, reversible, recompute-triggering. | L |

*Why last: it's the only Large, DB-writing item (highest corruption risk — needs Sprint 21's backup fix first), and Phase 0 alone (read-only view of the queue) delivers value with zero write risk. Don't build an editor on data you haven't trusted yet. Run it through the spec's own Phase 0→4 staging.*

---

## 4. Kill / defer recommendations

1. **Close spec 07 (conviction sizing) as superseded.** Its paper book was never built and its premise was overtaken by the universe cleanup. Re-scope the live question ("does alpha scale with £ size?") into Sprint 23's B-078 rather than building a standalone feature.
2. **Hard-cap the editor's failed-filing tab.** 4,281 pending items is a drowning hazard; surface only the recoverable buckets and let B-090 absorb the bundled boilerplate programmatically.

---

## 5. B-number collision — needs cleanup before Sprint 20

Three documents independently reused **B-072 → B-077**:

| Number | 29-May backlog | Sprint-15 draft | 2-Jun incident |
|--------|----------------|-----------------|----------------|
| B-072 | Horizon toggle | Horizon decision gate | Bundled multi-PDMR |
| B-073 | Small-multiples grid | Loader/export | Historical backfill |
| B-074 | Multi-signal overlay | Front-end chart | Non-RNS layouts |
| B-075 | CSV export | Tooltip/labels | UTL T3 |

**Proposed fix (applied in this doc):** freeze the 29-May backlog numbers as the originals, and renumber the **2-Jun incident** items into a clean **B-090+** block:

- B-090 = bundled multi-PDMR recovery (was incident B-072)
- B-091 = historical never-scraped backfill (was incident B-073)
- B-092 = non-RNS provider layouts (was incident B-074)
- B-093 = UTL/UIL T3 diagnosis (was incident B-075)
- **B-094 = `announced_at` non-ISO date fix (new, 2026-06-03 — source scraper fix + b1/b2 signal robustness)**

The Sprint-15-draft B-072–B-077 phase IDs are superseded by this plan's sprint structure (horizon toggle now lives in Sprint 25). **Rupert to confirm** this mapping, then it propagates to `docs/backlog.md` as the single source of truth.

---

## 6. Open questions for Rupert

1. **Confirm the B-090+ renumber** (§5) so every sprint references unambiguous IDs.
2. **Spec 07 — kill as superseded?** (Recommendation: yes.)
3. **Signal-deprecation appetite (Sprint 23):** ready to actually cut T3/S1/F1 if they show no edge, or wait for more post-cleanup data first? This decides whether Sprint 23 is "decide-and-cut" or "measure-and-wait".
4. **PDMR editor design (Sprint 25):** confirm the stage-then-apply write path (spec 10 §4 Option 1) before any build — it's the make-or-break decision.
