# Spec: 09 — Company search tool

**Status:** Draft for Rupert sign-off
**Owner:** Rupert
**Author:** PM scoping pass, 2026-06-02
**Feature size:** **S** (small)
**Relationship to staged plan:** Stage 5 (Dashboard) enhancement. Self-contained;
no dependency on Feature 10 (PDMR editor).

---

## 1. Problem statement & user goal

Today the only way to reach a company's page (`outputs/companies/{TICKER}.html`)
is to find the company in the "Today" or "This week" dealings table on the index
page and click its link. There are **521 companies** with at least one
transaction (verified 2026-06-02), but the index table only shows the recent
feed. If Rupert wants to look at a company that last filed three weeks ago, there
is no way to navigate to it directly — he has to know the file name.

**User goal:** Type a ticker (e.g. `AAF`) **or** a company name (e.g. `Airtel`)
into a search box in the dashboard header and be taken straight to that company's
page. Fast, forgiving of partial input, keyboard-friendly.

This is a navigation convenience, not an analytics feature. Keep it small.

---

## 2. Proposed solution / UX outline

A search box lives in the dashboard header (`<header>` in `outputs/index.html`,
and ideally on every page via the shared renderer). As Rupert types, a typeahead
dropdown shows up to ~8 matching companies. Enter or click navigates to
`companies/{TICKER}.html`.

**Matching behaviour (locked recommendation):**

- **Ticker match — exact-prefix, highest priority.** Typing `AA` surfaces `AAF`,
  `AAL`, `AAZ` first, ticker-prefix matches ranked above name matches.
- **Name match — case-insensitive substring.** Typing `airtel` matches
  "Airtel Africa plc". Substring, not fuzzy/Levenshtein — that is deliberately
  out of scope (adds complexity for marginal benefit on a 521-row list; revisit
  only if Rupert finds substring matching too strict in practice).
- Results show **both** the ticker and the full company name so Rupert can
  disambiguate (e.g. two companies that both contain "Group").

**No-match:** dropdown shows a single greyed "No match for '<query>'" row; Enter
does nothing. (We do **not** fall back to a Google-style "did you mean".)

**Multiple matches:** all shown in the dropdown (capped at 8, with a "+N more —
keep typing" hint if truncated). Enter selects the top-ranked row. Rupert can
arrow-key down the list.

**Empty input:** dropdown hidden.

### Text wireframe

```
┌─ Directors Dealings — Today ───────────────────────────────────────────────┐
│  Directors Dealings - Today        [ 🔍 Search ticker or company…  ]  ↻ Refresh   Performance │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
              user types "air"  ──►  ▼
                                   ┌──────────────────────────────────┐
                                   │ AAF   Airtel Africa plc          │ ◄ ticker-prefix, ranked top
                                   │ FAIR  Fair Oaks Income Ltd       │ ◄ name substring "air"
                                   │ RPS   Repsol ... (name match)    │
                                   │ … keep typing to narrow          │
                                   └──────────────────────────────────┘
                                     Enter ► navigates to companies/AAF.html
```

### Placement

The header is `h-12` (48px), currently holding the title (left) and a `<nav>`
(right: Refresh button + Performance link). Recommendation: insert the search box
**centre/right of the header, left of the Refresh button**. On narrow screens it
can collapse to a search icon that expands on click — but mobile is low priority
for this internal tool, so a simple always-visible input that shrinks is fine for
v1.

---

## 3. Data dependencies

The search needs a **complete list of (ticker, company name) pairs for all 521
companies** — not just the recent-dealings feed. Verified facts:

- `transactions` table holds `ticker` and `company` columns for every filing
  (4,631 rows, 521 distinct tickers). This is the source of truth for the pair
  list.
- Company name lives on **`transactions.company`**, NOT on `tickers_meta`
  (`tickers_meta` carries sector/benchmark/is_aim/market_cap but no name —
  verified).
- The existing exported JSONs (`outputs/data/dealings.json`,
  `outputs/data/signals.json`) only cover the recent feed / fired signals, so
  **neither is a complete company index.** A new index file is required.
- Company page filenames are produced by `build_dashboard._sanitize_ticker()`
  (uppercase, dots like `NG.` / `BT.A` preserved, other unsafe chars → `_`).
  The search index must store the **sanitized filename**, not just the raw
  ticker, so the link is always correct. (Verified: pages exist as `AAF.html`,
  etc.)

### New artifact to produce: `outputs/data/search_index.json`

Written by the existing dashboard exporter (`export_dashboard_json.py`) on every
build — it already opens the DB and writes JSON atomically. One extra query +
one extra `_atomic_write_json` call. Shape:

```json
{
  "generated_at": "2026-06-02T19:11:29Z",
  "companies": [
    { "ticker": "AAF", "company": "Airtel Africa plc", "href": "companies/AAF.html" },
    { "ticker": "AAL", "company": "Anglo American plc", "href": "companies/AAL.html" }
  ]
}
```

Query: `SELECT DISTINCT ticker, company FROM transactions ORDER BY ticker`.
One subtlety to resolve (see open questions): a ticker can have **multiple
company-name spellings** across its history (parser drift). Pick the
**most-recent non-empty** company name per ticker (`ORDER BY date DESC LIMIT 1`
per ticker) so the label is the cleanest available.

**Read by:** new client-side JS in the dashboard pages.
**Written by:** `export_dashboard_json.py` (Zone B script — Rupert runs it as part
of the normal build; Claude must not run it).

---

## 4. Architecture & write path

**Fully client-side. No server changes. No DB writes.** This is the key
simplification.

- The exporter writes `search_index.json` (a few hundred small rows — trivially
  small, well under 100 KB).
- Each dashboard page fetches `data/search_index.json` once on load and holds the
  array in memory.
- Typeahead filtering runs in plain JS in the browser. 521 rows is tiny — no
  index library, no debounce gymnastics needed; a simple `.filter()` on each
  keystroke is instant.
- Selecting a result sets `window.location = href`.

**Why client-side and not via Flask:** the data is static between builds, the set
is small, and the Flask server (`server.py`) is only running when Rupert has
started it locally. Making search depend on a live `/api/search` endpoint would
break the search box whenever the server is off but the static HTML is open. The
static-JSON approach works whether or not the server is running — consistent with
how `dealings.json` / `signals.json` are already consumed. **No FUSE write-path
concern at all** because search never writes; the only write is the exporter
producing the JSON, which Rupert already runs on Windows.

### Integration into the renderer

The header is emitted by the shared renderer layer (`render_helpers.py` /
`render_index.py` / `templates.py`). To get search on **every** page (index,
performance, company pages) with one edit, add the search box markup + the small
JS module to the shared header template rather than to each page. Confirm with the
dashboard-designer agent for the exact Tailwind markup so it matches the existing
`h-12` header styling (the visual-design slice is explicitly the designer's
remit per CLAUDE.md).

---

## 5. Edge cases

1. **Ticker with a dot** (`NG.`, `BT.A`): the `href` from the index already uses
   the sanitized filename, so links resolve. Make sure the *display* shows the
   real ticker (`NG.`) while the link uses the sanitized name.
2. **Same substring in many names** ("Group", "plc", "Holdings"): cap dropdown at
   8 + "keep typing" hint. Ranking puts ticker-prefix hits first.
3. **Two companies, same/overlapping name** (e.g. a renamed company that kept
   trading under two names): both rows show with distinct tickers; Rupert picks.
4. **A ticker exists in `transactions` but has no company page** (e.g. excluded
   investment trust whose page was never generated, or a brand-new ticker added
   since the last `gen_company_pages` run): the link would 404. Mitigation: build
   the index from the same ticker set `gen_company_pages.py` uses
   (`_tickers_with_transactions`), and run the exporter in the same pipeline pass
   that regenerates company pages so they stay in sync. Flag stale links as a
   known limitation rather than over-engineering a per-link existence check.
5. **Stale index after a refresh** (new company filed today, index not yet
   rebuilt): search won't find it until the next build. Acceptable — same
   staleness window as the rest of the static dashboard.
6. **Excluded issuers (IT/CEF/VCT/REIT):** decide whether they should be
   searchable. They are filtered *out* of the Active Clusters and dealings feed
   but their company pages may still exist. Recommendation: include them in
   search (so Rupert can still inspect them) but that's an open question below.

---

## 6. Risks & open questions for Rupert

1. **Multiple company-name spellings per ticker.** The parser has historically
   produced garbage company names (verified: ticker `NET` has company
   `", emission allowance market participant, …"`). If we pick the most-recent
   name and that one happens to be the garbage one, the search label looks
   broken. **Decision needed:** is "most-recent non-empty name per ticker" good
   enough, or do we want the *most-frequent* name? (Most-frequent is more robust
   to one-off parser errors; one extra GROUP BY.) — *This is also exactly the
   class of bug Feature 10's editor would let you fix at source.*
2. **Should excluded issuers be searchable?** (See edge case 6.)
3. **Fuzzy matching scope.** I've scoped substring-only. Confirm you're happy not
   to have typo-tolerant fuzzy matching in v1.
4. **Search on company pages too, or index/performance only?** Putting it in the
   shared header gives it everywhere for the same effort; confirm that's wanted.
5. **Assumption I could not fully verify:** I confirmed the header is emitted with
   Tailwind classes in `outputs/index.html`, but I did not trace every code path
   in `render_helpers.py` / `templates.py` to confirm a *single* shared header
   template feeds all three page types. If headers are duplicated per-renderer,
   "add once, get everywhere" becomes "add in 2–3 places." Build step 1 below
   resolves this.

---

## 7. Effort estimate & sequencing

**T-shirt size: S.** Main cost drivers: (a) confirming the shared-header code path
and not breaking existing renderers (CLAUDE.md memory: "grep every callsite before
editing a shared utility"); (b) the designer pass for header markup.

Suggested staging (one gate, this is small enough to be a single sprint):

1. **Exporter change (Zone B, Rupert runs).** Add the
   `search_index.json` writer to `export_dashboard_json.py`. Resolve the
   one-name-per-ticker rule (open question 1). Verify the JSON contains all 521
   tickers with correct `href`s. *Gate: eyeball the JSON.*
2. **Designer pass.** dashboard-designer agent specs the header search box markup
   (Tailwind, matches `h-12` header, dropdown styling, keyboard states).
3. **Front-end build (Zone A, Claude-safe).** Add the search box + JS to the
   shared header template. Grep all header callsites first. Verify with the Read
   tool after each write (FUSE truncation rule).
4. **Manual QA.** Start the server, test: ticker prefix, name substring, no-match,
   dot-ticker, keyboard nav. *Gate: Rupert tries it.*

No new tests strictly required (pure client JS), but a tiny unit test asserting
`export_dashboard_json` emits one index row per distinct ticker is cheap
insurance and fits the project's test discipline.
