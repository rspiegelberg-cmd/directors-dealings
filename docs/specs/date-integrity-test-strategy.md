# Date integrity — diagnosis, test strategy and confidence panel

**Status:** plan for review (no code written yet)
**Author:** Claude (engineering:testing-strategy)
**Date:** 2026-05-15

This document explains why dates keep going wrong, lays out a layered test
strategy to catch the problem early, and proposes a small "date health"
panel so you can see green-or-red on every pipeline run.

---

## A. Why dates keep breaking — the diagnosis

Every transaction in the system has **three different dates** that must
agree, and the system has no automated check that they actually do:

| Field                         | What it is                                | Where it comes from                                   |
|-------------------------------|-------------------------------------------|-------------------------------------------------------|
| `transactions.date`           | Transaction date — the day the director dealt | `parse_pdmr.parse_iso_date()` reading the filing HTML |
| `transactions.announced_at`   | Filing date/time — when RNS published it  | JSON-LD `dateCreated` in the filing HTML              |
| `transactions.first_seen`     | When *we* ingested it                     | `db.iso_now()`                                        |

Under UK MAR rules `date` must be within ~3 business days of `announced_at`.
If it isn't, something has gone wrong.

### The four root-cause patterns I found in your code

1. **Silent parser fallback (the big one).**
   `parse_pdmr.parse_iso_date()` first tries the explicit label
   "Date of [the] transaction". If that pattern fails it falls through to
   `max(candidates)` — the **latest date appearing anywhere in the document.**
   That fallback historically picked up option expiry dates, RSU
   maturity dates, AGM dates, and the announcement date itself. It produces
   a plausible-looking but completely wrong `date`, with no warning emitted.
   You already fixed one trigger (optional "the") but the silent fallback
   is still wired in — any new filing layout that doesn't match the label
   regex will silently produce a wrong date again.

2. **Ambiguous date formats.**
   `_DATE_FMTS` includes `"%d/%m/%Y"` and `"%d-%m-%Y"` (UK style) but
   NOT `"%m/%d/%Y"`. For unambiguous dates this is correct. For ambiguous
   ones (e.g. `03/04/2026` could be 3 April or 4 March) the parser silently
   picks UK ordering. If a US-style ADR filing slips in, you get a six-month
   error and no warning.

3. **No format guard at the database boundary.**
   `transactions.date TEXT NOT NULL` accepts literally any string. There is
   no CHECK constraint enforcing `YYYY-MM-DD`. A wrong-format date will be
   stored happily, then sort wrong in `backtest.py`'s `bisect_right`,
   producing wrong T+1 entry dates.

4. **Two separate `parse_iso_date` functions exist.**
   One in `parse_pdmr.py` (writing into the DB), one in
   `dashboard/render_helpers.py` (reading for display). They have different
   logic. If one accepts a format the other doesn't, you get a row that
   stores fine but renders blank — or vice versa.

### Why this is more dangerous than it looks

It's not just a cosmetic issue on the dashboard.

`backtest.py:_select_firings()` does
`COALESCE(NULLIF(announced_at, ''), date)` to decide the entry day for
each signal. So if **either** `date` or `announced_at` is wrong, the entry
day is wrong, and your T+1, T+21, T+90 returns all reference the **wrong
price** vs the **wrong benchmark window**. Your performance tracker is
silently lying.

That's the central reason testing this matters: a bad date doesn't just
move a row around the dashboard — it corrupts the very numbers you'll use
to decide which signal tier to act on.

---

## B. The test strategy — four layers

Apply the testing pyramid principle: many cheap unit tests, fewer
integration tests, a tiny number of end-to-end checks.

```
        /  E2E (1 test)  \      Fixture → scrape → parse → DB → dashboard renders correctly
       /  Integration (5–8) \   Whole-DB invariants run after every refresh_all
      /     Unit (30–50)      \ Date parsing, format handling, edge cases
     /  Static guards (3–4)    \ Schema CHECK constraint, parser hard-stop, no silent fallback
```

### Layer 1 — Static guards (must-have, fix now)

These prevent bad data from being written in the first place. One-off
code changes, no test runner needed.

| # | Guard | Where | Effect |
|---|-------|-------|--------|
| G1 | Remove the latest-wins fallback in `parse_iso_date`. If the label doesn't match, return `None` and emit `could_not_parse_tx_date`. | `parse_pdmr.py` | No more silent option-expiry dates. The filing goes to pending review instead of being wrong. |
| G2 | Add a strict-format check in `upsert_transaction`. Refuse to insert if `date` doesn't match `^\d{4}-\d{2}-\d{2}$`. | `db.py` | DB can never contain non-ISO dates. |
| G3 | Add an invariant check in `upsert_transaction`: refuse to insert if `announced_at` is populated AND `date > announced_at + 7 days`. Warn-and-skip rather than fail the whole batch. | `db.py` | Cleans-up logic from `clean_bad_dates.py` becomes prevention. |
| G4 | Collapse the two `parse_iso_date` functions to one shared module (`.scripts/dates.py`). Both `parse_pdmr.py` and `dashboard/render_helpers.py` import it. | new module | Single source of truth for what counts as a parseable date. |

### Layer 2 — Unit tests (catch parser regressions cheaply)

A new file `.scripts/test_dates.py` (stdlib `unittest`, no pytest needed).
Each test is one assertion against a string-in / date-out boundary. These
run in <1 second.

**Test classes and example assertions:**

- `TestTryOneDate` — every format in `_DATE_FMTS`, plus ordinals
  ("27th April 2026"), commas ("April 27, 2026"), and trailing dots.
- `TestParseIsoDate` — label-based wins over latest-wins (positive case),
  label absent → returns None (negative case after G1), bundled filings
  return None.
- `TestAmbiguousDates` — `03/04/2026` parses as 3 April (documented UK
  default), `04/27/2026` returns None (US format not supported).
- `TestRejectsBadInput` — empty string, `None`, `"asdf"`, future date
  > today + 1 year, date < 1990-01-01.
- `TestDashboardParity` — `dates.parse_iso_date()` returns the same
  result whether called from parser or dashboard.

**Golden fixture set** at `.scripts/fixtures/dates/`:

A handful of small, hand-crafted HTML snippets — one for each historical
bug pattern. Each fixture has an adjacent `.expected.json`:

```
fixtures/dates/
  01_label_with_the.html                 → expects "2026-04-27"
  02_label_without_the.html              → expects "2026-04-27"  (Mondi-style)
  03_no_label_only_option_expiry.html    → expects None (must NOT pick the expiry)
  04_ordinal_27th.html                   → expects "2026-04-27"
  05_us_format.html                      → expects None
  06_zar_currency.html                   → expects "2026-04-27" + foreign_currency warning
  07_bundled_pdmr.html                   → expects [] + bundled warning
  08_announce_eq_tx_date.html            → expects "2026-04-27"  (tx and filing same day)
  09_old_tx_recent_filing.html           → expects the correct tx date
  ...
```

The fixtures are tiny — < 1 KB each. They live in the repo and are the
single thing that future-you (or me) checks before touching the parser.

### Layer 3 — DB invariants (catch silent corruption after every run)

A new script `.scripts/audit_dates.py` that opens the DB read-only and
checks five invariants. Exits 0 if all green, non-zero if anything failed.
This is the heart of your "confidence panel".

The five invariants:

| # | Invariant | What it catches |
|---|-----------|-----------------|
| I1 | Every `transactions.date` matches `YYYY-MM-DD`. | Format drift; would be caught at write time by G2, but a backwards-compatible safety net. |
| I2 | Every `transactions.date` ≤ today + 1 day. | The classic "future-dated transaction" bug. |
| I3 | Where `announced_at` is populated, `date` ≤ `announced_at + 7 days`. | Catches the latest-wins fallback escapes. |
| I4 | Where `announced_at` is populated, `date` ≥ `announced_at - 3 years`. | Catches the inverse case (parser picked a stale historic date). |
| I5 | For every `signals` row, the referenced `transactions.date` is parseable, ISO-formatted, and within ± 1 year of `announced_at`. | Catches firings tied to bad transactions — exactly the rows that drive the performance tracker. |

`audit_dates.py` outputs:

```
=== Date integrity audit (2026-05-15 14:02 UTC) ===
I1  Format check (YYYY-MM-DD)               PASS  (2630/2630)
I2  No future-dated transactions             PASS  (0 future-dated)
I3  date <= announced_at + 7d                FAIL  (3 anomalies)
I4  date >= announced_at - 3y                PASS  (0 anomalies)
I5  Signal-row date integrity                PASS  (412/412)

OVERALL: 1 failure
See .data/_date_audit_report.json for row-level detail.
```

The JSON sidecar lists every offending fingerprint so you can drill in.

### Layer 4 — End-to-end smoke test

A single test `.scripts/test_stage_dates_e2e.py` that:

1. Reads one known-good fixture HTML.
2. Runs the full parse → DB upsert path against a temporary SQLite file.
3. Asserts `date` in the DB matches the expected value.
4. Renders the company page from that temp DB.
5. Asserts the rendered HTML contains the expected formatted date.

This is the one test that proves the chain end to end. Slow (~1 second)
but priceless — it would have caught every historical regression.

---

## C. Confidence panel — your green/red traffic light

You asked for confidence in the outputs. The deliverable is a tiny HTML
panel rendered as part of the dashboard build, sitting at the top of
`index.html`:

```
┌─ Data quality (refresh 2026-05-15 14:02 UTC) ─────────────────────┐
│  Transactions       2,630                                          │
│  Date format        ✓ 2,630/2,630 ISO YYYY-MM-DD                   │
│  No future dates    ✓ 0 future-dated                               │
│  Date vs filing     ✗ 3 anomalies   →  review list                 │
│  Signal integrity   ✓ 412/412 firings have valid dates             │
│                                                                    │
│  Last refresh OK:   2026-05-15 13:44 UTC                           │
│  Last audit:        2026-05-15 14:02 UTC                           │
└────────────────────────────────────────────────────────────────────┘
```

Implementation: `audit_dates.py` already writes `_date_audit_report.json`.
`build_dashboard.py` reads it and renders this panel at the top of
`index.html`. If any check fails, the panel is red and links to a sub-page
listing the offending rows.

This sits **above** the dealings table, so you literally cannot miss it.

---

## D. Suggested build order — phased and reversible

Each phase is independently useful — you don't have to do them all to
benefit. I'd suggest:

| Phase | What | Why first | Effort |
|-------|------|-----------|--------|
| **P1** | G4 (shared date module) + `audit_dates.py` (Layer 3) | Pure read-only diagnostics. Tells you the size of the current problem. Zero risk. | ~1 hour |
| **P2** | Confidence panel in `build_dashboard.py` | Makes P1 visible. The thing you actually asked for. | ~30 min |
| **P3** | Layer 2 unit tests + fixtures | Locks in current good behaviour. Catches future regressions. | ~2 hours |
| **P4** | G1 (kill latest-wins fallback), G2 (format guard), G3 (sanity guard) | Prevents new bad rows. Backed by P3 tests. | ~1 hour |
| **P5** | End-to-end smoke test (Layer 4) | Final safety net. Belt + braces. | ~1 hour |

The genius of doing P1+P2 first is that **before changing any logic you
get a clear, factual readout of how many rows are bad today** — which
lets us measure the fix's impact in P4.

---

## E. What this strategy does NOT cover (deliberately)

- **Timezone handling.** All `announced_at` values are UTC per Investegate;
  `date` has no time component. We compare on date-prefix so this works,
  but if you ever ingest a non-UTC RNS source, we'll need to revisit.
- **The performance tracker's own date arithmetic.** The bisect / trading
  day offsets in `backtest.py` are the next thing I'd test, but they
  depend on good `date` values upstream. Fix the source first.
- **Backfilling historic bad rows.** Your existing `repair_dates.py` and
  `clean_bad_dates.py` already do this. After P4 lands, run them once
  more and they'll find very little.

---

## F. What I need from you before building

One decision: **do you want me to start with P1 + P2 (the diagnostic +
confidence panel) so we get visibility first, or jump straight to P4
(fix the parser) to stop the bleeding?**

My recommendation is P1 + P2 first — it's safer, it gives you a real
number for "how many rows are bad", and you'll see immediate value at
the top of the dashboard. Then we can do P3 + P4 confidently with the
panel showing the count going to zero.
