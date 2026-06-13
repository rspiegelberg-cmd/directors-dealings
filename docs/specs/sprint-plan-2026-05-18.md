# Sprint plan — 2026-05-18

> Three sequential sprints to clear the open backlog. Each sprint has a
> clear theme, an explicit gate / sign-off pattern where one is needed,
> and a kickoff checklist so an engineer can start the moment Rupert
> says "go."

**Companion docs:**
- [`docs/backlog.md`](../backlog.md) — the living issue list.
- [`docs/specs/backlog-scopes-2026-05-18.md`](backlog-scopes-2026-05-18.md)
  — engineer-ready scopes (QA-corrected). Each sprint references items
  by their B-NNN ID; the engineer reads the scope before starting.

---

## A note on the estimates below

This plan uses three units chosen to be useful **for Rupert specifically**,
not for a hypothetical engineer working alone:

- **Your-attention-minutes** — wall-clock time Rupert actually spends
  (running scripts from PowerShell, eyeballing previews, reading
  pipeline output, deciding at gates). Excludes time when scripts are
  running unattended.
- **Gates** — count of mandatory sign-off pauses where Rupert has to
  decide / approve before the work proceeds. Zero means start-to-finish,
  no interruptions; two means two distinct review-and-approve checkpoints.
- **Risk** — Low / Medium / High based on what could go wrong and how
  hard recovery is. High means irreversible-by-default (e.g. DB
  deletes) with a backup as mitigation.

The per-sprint detail tables further down also include a "Code size"
column (S / M / L) for relative scope sizing — useful for planning my
own work, less useful for you.

## Sprint sequence at a glance

| Sprint | Theme | Items | Your time | Gates | Risk |
|--------|-------|-------|-----------|-------|------|
| 1 | Quick wins + chart honesty | B-002, B-005, B-006, B-008, B-009, B-010 | ~20 min (dashboard review at end) | 0 | Low |
| 2 | IT/CEF data clean-up | B-011 (+ B-016/B-017/B-018 surfaced) | ~45 min (preview review + paste-back of pipeline outputs) | 2 (pre-flight backup verify + preview sign-off) | High (irreversible delete; backup mitigates) |
| 3 | Parser rebuild + tests + polish | B-001+B-004 (+ B-016/B-017), B-003, B-007, B-019 | ~45 min (preview review + dashboard verify for B-007 / B-019) | 2 (preview sign-off + fingerprint-handling decision) | Medium (no DB deletes; risk is bad fingerprint merges from the parser rewrite) |

**Total Rupert-time across the programme:** ~110 minutes spread over the
three sprints. £0.30–0.50 in LLM API cost (B-002 LLM sweep + optional
B-011 classifier disambiguation). Each sprint ends with a clean
dashboard you can inspect before the next sprint starts — no
all-or-nothing risk.

---

# Sprint 1 — Quick wins + chart honesty (your time: ~20 min, gates: 0, risk: low)

**Goal:** Five small but visible improvements to the dashboard, plus
one defensive guard and one race-condition fix. Zero data risk; pure
upside. After this sprint, the dashboard is materially nicer to look
at and you can trust the 12-month chart.

## Scope (6 items, parallelisable within the sprint)

| ID | Title | Code size |
|----|-------|-----------|
| B-002 | LLM sweep — recover 7 pending rows | S |
| B-005 | Reject dates with year < 1990 | XS |
| B-008 | Defensive ISO-date assert in backtest | XS |
| B-006 | repair_dates.py atomic pending write | S |
| B-010 | Transaction table sort + today's-date format | S |
| B-009 | CAR chart — true 12-month buckets + null gaps | M |

## Recommended order

1. **B-008** first (XS — warm-up, builds familiarity with `backtest.py`).
2. **B-005** (XS — same file area as the next session's parser work).
3. **B-010** (S — visible UX win, sets a good momentum signal).
4. **B-006** (S — race-condition fix; mechanical).
5. **B-009** (M — the most thinking; do it once warmed up).
6. **B-002** last (S — needs a successful dashboard rebuild
   afterwards to surface the 7 recovered rows).

Items 1–5 are independent. The engineer can re-order or parallelise.
Item 6 (B-002) ends with an `eval_signals.py` → `build_dashboard.py`
re-run so the recovered rows actually appear.

## Gates

None. All items are low-risk: small code changes, no destructive DB
work, no irreversible decisions.

## Definition of Done

- All 6 scopes' acceptance criteria pass (see scope doc per item).
- `audit_dates.py` runs green at end of sprint.
- Dashboard rebuilt; visual confirmation:
  - Transaction tables show "Today" / "18 May" format, freshest at top.
  - Performance-page CAR chart shows real gaps for unmatured months
    (not a misleading flat 0% line).
  - 7 previously-pending rows now appear on Today / company pages.
- One smoke run of the full refresh pipeline (`refresh_all.py`)
  completes successfully end-to-end.

## What you (Rupert) see at the end

A cleaner, more honest dashboard. Same data, better surface. No
behaviour change to signal scoring or performance tracking — that's
Sprint 2.

## Kickoff checklist (before engineer starts)

- [ ] Engineer has read this sprint section + the relevant scope items
      in `backlog-scopes-2026-05-18.md`.
- [ ] Local environment runs `start.bat` successfully and the dashboard
      opens.
- [ ] `audit_dates.py` is green on the current DB (Rupert's "morning
      refresh" from 2026-05-18 already confirmed this).
- [ ] Engineer knows the FUSE rules (CLAUDE.md): never write `.data/`
      from bash; use Windows Python for all DB writes; use the Read
      tool to verify file edits.

---

# Sprint 2 — IT/CEF data clean-up (your time: ~45 min, gates: 2, risk: high) — COMPLETED 2026-05-18

**Goal:** Remove investment trusts, VCTs, REITs, and closed-end funds
from the dataset so signal scoring reflects only operating-company
director dealings. This is the single biggest source of signal noise
in the current data, and the dynamics of IT/CEF director trading are
fundamentally different from real insider activity.

## Scope (1 item)

| ID | Title | Code size |
|----|-------|-----------|
| B-011 | Exclude investment trusts / CEFs / VCTs / REITs | L |

## Gates

This sprint has **two hard gates**. The engineer does not proceed past
either without Rupert's explicit sign-off.

### Gate 1 — Pre-flight backup (mandatory, before any other work)

The engineer takes a Windows-side Python backup of `.data/directors.db`
to `.data/directors.db.pre-it-purge.bak`, verifies it opens with
`PRAGMA integrity_check`, and confirms the backup is on disk.

**Sign-off:** None needed; this is a procedural gate. But the engineer
shares the backup filename + integrity-check output with Rupert before
moving on, just so you both know it's there.

### Gate 2 — Preview sign-off (mandatory, before DELETE)

The engineer runs the classifier (AIC scrape + Yahoo `quoteType` +
name regex) and produces `.data/_excluded_it_cef.preview.csv` listing
every ticker that would be deleted, with:

- `ticker`, `company`, `source` (A/B/C/multi), `signed_off_by`,
  `signed_off_at`

Rupert reviews the preview together with the engineer. **Look for:**

- False positives — operating companies caught by the name regex
  (e.g. "Trustpilot" hopefully not; if it appears, flag it).
- False negatives — well-known ITs not on the list (e.g. Scottish
  Mortgage, Pershing Square Holdings, City of London Investment Trust).
- Total row count — sanity-check: is this in the ballpark of what you
  expected (~hundreds, not thousands)?

**Sign-off:** Rupert fills in the `signed_off_by` and `signed_off_at`
columns. The engineer's runner script refuses to DELETE without those
fields populated.

If the preview looks wrong, the engineer adjusts the classifier
(e.g. tighten the name regex, add specific tickers to a manual
exclude / include list) and re-runs the preview. Iterate until you
sign it off.

## Definition of Done

- `.data/directors.db.pre-it-purge.bak` exists and is integrity-clean.
- `.data/_excluded_it_cef.preview.csv` has Rupert's sign-off rows
  filled in.
- After delete:
  - Zero transactions remain whose ticker is in the excluded list.
  - Zero signals reference deleted fingerprints.
  - `.data/_excluded_it_cef.csv` lists every deleted fingerprint
    (audit trail, separate from preview).
- `eval_signals.py`, `backtest.py`, and `build_dashboard.py` re-run
  in that order without error.
- `audit_dates.py` still green.
- Scrape pipeline rejects future IT/CEF filings at ingest — verify by
  running `run_scrape.py` in dry-run mode and confirming any new
  IT/CEF filing gets logged to `.data/_excluded_at_ingest.log`.

## What you (Rupert) see at the end

- Active Clusters panel shows fewer items (IT cluster activity gone).
- Performance tracker numbers shift — CAR figures should be cleaner
  because IT NAV-discount buying isn't dragging the signal mean down.
- The Today / company pages no longer show IT/REIT dealings.
- A short summary message from the engineer: "Deleted N rows across
  M tickers; new transaction count is X." Inspect-and-verify.

## Rollback plan

If, after viewing the cleaned dashboard, you decide the exclusion was
too aggressive (e.g. you actually want REITs included):

1. Engineer copies `directors.db.pre-it-purge.bak` back to
   `directors.db` (Windows Python — never bash).
2. Re-run `eval_signals.py` → `backtest.py` → `build_dashboard.py`.

You're back to pre-sprint state in 15 minutes. This is why we backed
up first.

## Kickoff checklist

- [ ] Sprint 1 is complete and `audit_dates.py` is green.
- [ ] Engineer has read B-011 scope in full.
- [ ] Rupert has 30 minutes blocked to review the preview list when
      Gate 2 fires (mid-sprint, not the end). Engineer should give
      ~24 hours' notice.
- [ ] Decision on how aggressive to be on regex source (catch-all)
      flagged to Rupert in advance — over-inclusion is easier to
      reverse than under-inclusion (because reverse is "do nothing,"
      forward is "delete again").

---

# Sprint 3 — Parser rebuild + tests + polish (your time: ~45 min, gates: 2, risk: medium)

**Goal:** Make the parser table-aware so it captures bulk-filing rows
2 through N (currently lost), lock in the fixes with unit tests, and
ship the late-filings visibility badge. After this sprint, you have a
significantly more complete dataset and an automated safety net.

## Scope (4 items + 2 folded)

The first three are strictly sequential. B-019 is a parallel
dashboard-polish task — can be picked up at any point but is most
naturally done last alongside B-007, since both touch the dashboard
render layer.

| ID | Title | Code size | Depends on |
|----|-------|-----------|------------|
| B-001 + B-004 + B-016 + B-017 | Table-aware parser (multi-row + cell-boundary fixes) | L | Sprint 2 complete |
| B-003 | Unit tests on parser (Layer 2) | M | B-001+B-004 done |
| B-007 | I6 informational late-filings badge | S | B-003 done (clean data) |
| B-019 | CAR chart per-series toggle + solo mode | S | none (independent of parser work) |

**Note on B-016 + B-017 (added 2026-05-18, post-Sprint-2):** Two
additional parser data-quality items were surfaced during the Sprint 2
IT/CEF ticker review. Both share the same root cause as B-001/B-004
(regex crossing `<td>` boundaries) and are folded into the same fix
rather than tracked as separate work items:

- **B-016** — ~30 tickers have a `company` field of `", emission
  allowance market participant, auction platform, auctioneer or auction
  monitor"` (regulatory-disclosure boilerplate from MAR Article 19,
  mis-extracted from the bottom of the filing). Tickers include SPT,
  YOU, ZTF, NET, EBQ, CAU, CKT, GELN, DFIJ, EEE, JAR, NAR, AAZ, CAM,
  CHF, CHRT, GOT, JEL, LIFS, LIKE, PAF, QHE, RFX, SAL, SOLG, STS, VEIL,
  AMS, PBEE.
- **B-017** — 5 tickers (AAL, GLE, SSPG, PCTN, RPI) have a director's
  name in the `company` field — the mirror of B-004 but for the
  company cell.

The B-001+B-004 implementation should add sentinel-rejection on the
extracted `company` value: reject "emission allowance market
participant" and other known boilerplate strings, plus reject any
`company` value that matches the same row's director cell.

## Pre-step

Before any code work, install BeautifulSoup: `pip install
beautifulsoup4`. (Status 2026-05-18: confirmed installed during
Sprint 2 — version 4.14.3 is on Rupert's Python 3.13 site-packages.
No new install needed for Sprint 3.)

## Gates

### Gate 1 — Preview sign-off before corpus re-parse

The corpus re-parse may create or modify hundreds of rows. Before
writing anything to the live DB, the engineer produces
`.data/_reparse_corpus_preview.csv` showing:

- Total rows that would be created / modified.
- Count of director-name fixes.
- 5 sample diffs (existing row → proposed row).

Rupert reviews and signs off.

### Gate 1a — Fingerprint stability decision (within Gate 1)

The scope offers two options for handling rows whose fingerprint
changes due to a corrected director name:

- **Option A (recommended):** detect "this looks like a corrected
  version of an existing row" by matching on (date, ticker, type,
  shares, price) and update in place. Cleaner; risks accidentally
  merging genuinely-different rows.
- **Option B:** add a fingerprint-version field; old rows marked
  superseded. More machinery, less merge risk.

Rupert chooses A or B at this gate. I'd default to A — the
(date, ticker, type, shares, price) match is tight enough that
genuine collisions are vanishingly rare for director-dealing data.

## Recommended order

1. **Install BeautifulSoup + confirm with Rupert** (5 min).
2. **B-001 + B-004** (L — the bulk of the sprint):
   - Build the table-aware parser.
   - Hand-fixture test against filing 9541612.
   - Run the **preview-only** corpus re-parse → produce preview CSV.
   - Pause for Rupert sign-off + fingerprint-handling decision.
   - Run the corpus re-parse with `--confirm`.
   - Trigger `eval_signals.py` → `build_dashboard.py`.
3. **B-003** (M): write the tests against the now-fixed parser
   behaviour. Locks the new behaviour in.
4. **B-007** (S): I6 informational badge — visibility into how
   many late filings exist.
5. **B-019** (S): CAR chart per-series toggle + solo mode. Independent
   of the parser sequence; do it whenever, e.g. while the corpus
   re-parse is running in the background.

## Definition of Done

- Filing 9541612 has 4 rows in `transactions` with the expected dates.
- Zero rows have a `\n` in the director field or contain "plc" /
  field-label noise.
- `python -m unittest .scripts/test_parser.py` runs all green.
- `run_tests.bat` exits 0 on success, non-zero on failure.
- Data-quality panel shows the new "N late-disclosed transactions"
  grey badge.
- Total `transactions` count is higher than pre-sprint (because
  multi-row bulk filings now contribute fully).
- `audit_dates.py` green.

## What you (Rupert) see at the end

- More complete data — every bulk filing now contributes all its
  rows, not just the first.
- A regression-tested parser. Future parser changes can be made with
  confidence because there's an automated safety net.
- A new neutral badge on the data-quality panel showing late-filing
  visibility.

## Kickoff checklist

- [ ] Sprint 2 complete; `audit_dates.py` green; IT/CEF rows gone.
- [ ] Rupert has decided on Option A vs B for fingerprint handling
      (or has agreed to decide live at Gate 1a — fine either way).
- [ ] Confirmed: BeautifulSoup install is OK to proceed.
- [ ] Engineer has read all three scope items in full.

---

# Cross-sprint reminders

**FUSE rules apply throughout** (CLAUDE.md):
- Never write `.data/` or `_*_cache/` from bash. Windows Python only.
- Use the Read tool to verify code edits, not bash `cat` / `wc`.
- The DB self-backs-up after every successful pipeline run — but for
  Sprint 2, take the explicit `.pre-it-purge.bak` snapshot manually
  (the auto-backup will overwrite as soon as the next pipeline runs).

**Definition of "Done" pattern across all sprints:**
1. Code changes complete.
2. Mandatory truncation check via Read tool on every edited file
   (CLAUDE.md mandatory rule).
3. `audit_dates.py` green.
4. Dashboard rebuilt and visually inspected.
5. Engineer reports a one-paragraph summary to Rupert before moving
   to the next sprint.

**Re-check the backlog at sprint kickoff.** Things may have come up in
the interim (a new bug, a missed edge case from the previous sprint).
Open `docs/backlog.md` first thing, scan for new items, decide whether
to absorb into this sprint or punt to a follow-on.

---

# What happens after Sprint 3

The original backlog is cleared. At that point:

- Review `docs/backlog.md` for any items added during the three
  sprints (likely — sprint work surfaces new glitches).
- Decide whether to start a Stage 6 work programme (e.g. expanded
  signal taxonomy, paper-trading automation, alerting) or hold and
  monitor the cleaned dataset for a month before adding more.
- Run a backtest comparison: pre-IT/CEF-purge vs. post — does the
  signal performance actually improve, or was it noise? Useful data
  for deciding whether more aggressive exclusions (foreign issuers,
  small-cap, etc.) are worth doing.
- **B-018 (P3) — classifier refresh strategy.** Sprint 2 left the
  IT/CEF classifier reliant on `.scripts/manual_include.csv` plus the
  conservative name regex (AIC scraping and Yahoo `quoteType` were
  both unavailable). Fine for a one-shot but fragile for any quarterly
  re-classification. If/when we want a sustainable refresh, the
  cleanest path is to wire the classifier into the LSE's monthly
  "Listed Investment Funds" Excel (~3 hours of work). See B-018 in
  `docs/backlog.md` for the full options analysis.
