# Spec: 10 — PDMR review & edit tool

**Status:** Draft for Rupert sign-off
**Owner:** Rupert
**Author:** PM scoping pass, 2026-06-02
**Feature size:** **L** (large) — the safe-write design is the make-or-break.
**Relationship to staged plan:** Stage 5+ data-quality tooling. Builds on the
existing pending-review quarantine and the `transactions` schema. Depends on the
signal-eval pipeline order (CLAUDE.md).

---

## 1. Problem statement & user goal

Two distinct data-quality problems, one tool:

**(a) Filings that FAILED the parser are invisible and unactionable.** Verified:
failed/ambiguous filings land in `.scripts/_pending_review.json` — **4,352 items**
as of 2026-06-02. Each is keyed by RNS ID and carries the source `url`, the
`headline`, `warnings`, an `extracted` list (often empty = total fail; sometimes a
partial/over-cautious capture), and `parser_source`. Today there is **no UI** to
work this queue — it can only be swept by the LLM (`run_pending_sweep.py`) or
hand-edited as raw JSON. Rupert cannot eyeball a stuck filing, **reject** it as
junk, or **manually key in** the correct transaction.

**(b) Filings that parsed SUCCESSFULLY sometimes parsed WRONG.** Verified live
examples of bad rows in `transactions`: ticker `NET` has company
`", emission allowance market participant, auction platform…"`; the year-as-shares
bug (see `year-as-shares-refix-plan-2026-05-31.md`) has put wrong share counts in
rows. There is no way to correct a single row's director name, role, share volume,
price, company name, or sector from the dashboard — fixes today require a reparse
or a hand-run repair script.

**User goal:** From the dashboard, Rupert can (a) work the failed-filing queue —
reject junk or manually add the real transaction — and (b) open any parsed
transaction, see the **original RNS filing side-by-side**, and correct the fields.
Every edit is audited, reversible, and triggers a signal recompute so the
performance numbers stay honest.

---

## 2. Proposed solution / UX outline

A new **Review** surface in the dashboard, with two tabs:

### Tab A — Failed-filing queue (from `_pending_review.json`)

A list of stuck filings. Each row: RNS headline, best-guess ticker, warning
summary, and the source link. Clicking opens the **side-by-side editor** with the
left pane showing the original filing and the right pane **empty/blank editable
fields** (because the parse failed). Rupert can:

- **Reject** — mark the filing as "not a real PDMR dealing / junk" so it leaves
  the queue and never comes back. (Bundled-boilerplate filings, fetch errors,
  foreign-currency rows with no GBP price.)
- **Add manually** — type the real transaction(s) into the fields and save a new
  `transactions` row.

### Tab B — Parsed-transaction editor (from `transactions`)

Reached from Tab B's own searchable list **or** (better) from an **"Edit" pencil
on each row of a company page / dealings table**. Opens the same side-by-side
editor, but the right pane is **pre-filled** from the existing transaction. Rupert
edits and saves.

### The side-by-side editor (shared by both tabs)

```
┌─ Review: Card Factory plc — RNS 9564925 ──────────────────────────────────┐
│  ┌── ORIGINAL FILING ────────────┐   ┌── EDIT TRANSACTION ─────────────┐  │
│  │ Director/PDMR Shareholding    │   │ Director name [Darcy Willson-Ry] │  │
│  │ Card Factory plc              │   │ Director role [Chief Executive ] │  │
│  │                               │   │ Company name  [Card Factory plc] │  │
│  │ (rendered cached RNS HTML,    │   │ Sector        [Consumer Disc. ▾] │  │
│  │  scrollable — from            │   │ Txn type      [SELL_TAX ▾]       │  │
│  │  _scrape_cache/9564925.html)  │   │ Shares        [91998]            │  │
│  │                               │   │ Price (GBP)   [0.6615]           │  │
│  │  ⤤ open original on Investegate│   │ Value (GBP)   [60856.68] (auto) │  │
│  └───────────────────────────────┘   │ Date          [2026-05-12]       │  │
│                                       │  ⚠ warnings: "Filing also incl…" │  │
│                                       │  [ Save edit ] [ Reject ] [ Cancel] │  │
│                                       └──────────────────────────────────┘  │
│  Edit is STAGED — nothing is written to the live DB until Rupert runs the    │
│  apply step on Windows. Pending edits: 3   [ Show queued edits ]             │
└──────────────────────────────────────────────────────────────────────────┘
```

**Left pane — the original filing.** Verified retrievable two ways:
1. The cached raw HTML at `.scripts/_scrape_cache/{rns_id}.html` (7,188 files
   present; e.g. `9564925.html` exists). The RNS ID is the trailing path segment
   of the filing `url` (both `_pending_review.json[item].url` and
   `transactions.url` end in `/{rns_id}`). So we can always map a row → its cached
   HTML, falling back to the live `url` link if the cache file is missing.
2. The `url` itself, as an "open original" external link.
   (Note: `transactions.context` is **empty** in practice — verified — so it is
   NOT a source of raw text. Use the HTML cache.)

**Right pane — editable fields.** Mapped 1:1 to columns (see §3). `value` is
auto-recomputed as `shares × price` on the client but stays editable for the
disclosed-value-but-undisclosed-price case (`price=0`).

---

## 3. Data dependencies

### Reads

- **`_pending_review.json`** (Tab A): keyed by RNS ID; fields `url`, `headline`,
  `warnings[]`, `extracted[]`, `parser_source`, `used_llm`. *Verified shape.*
- **`transactions`** (Tab B). Editable columns and their constraints (verified
  from `db_schema.sql` + live `PRAGMA`):
  - `director` TEXT, `role` TEXT, `role_normalized` TEXT (migration 004),
    `company` TEXT, `type` TEXT **CHECK IN ('BUY','SELL','SELL_TAX','EXERCISE',
    'GRANT','SIP')**, `shares` INTEGER, `price` REAL (0 = undisclosed),
    `value` REAL.
  - **`fingerprint` is the PRIMARY KEY and is derived** =
    `date|ticker|director|type|shares`. Editing any of those four fields changes
    the fingerprint → this is the single biggest data-model gotcha (see §4).
  - `cluster_id`, `first_time_buy`, `buy_strictness`, `parser_source` exist but
    are **derived** — the editor should not let Rupert hand-edit them; they are
    recomputed downstream.
- **`tickers_meta`** for sector (`sector` is per-ticker here, NOT on
  `transactions`). Editing "company sector" means an UPDATE to `tickers_meta`, not
  `transactions`. *Verified.*
- **`_scrape_cache/{rns_id}.html`** — the original filing for the left pane.

### Writes (the dangerous part — see §4 for the safe path)

- INSERT / UPDATE / soft-delete rows in **`transactions`**.
- UPDATE `sector` in **`tickers_meta`**.
- Remove/flag items in **`_pending_review.json`** (reject / promoted-to-tx).
- Append to an **audit log** (new file, see §4).

### New artifacts

- `.data/_edit_queue.json` — staged edits awaiting apply (the FUSE-safe buffer).
- `.data/_edit_audit.jsonl` — append-only audit trail (JSONL, never RMW — per
  CLAUDE.md memory "no read-modify-write JSON arrays in hot paths").
- A new exported `outputs/data/pending_review.json` (sanitized subset of the
  4,352 items for the Tab A list — the raw file lives in `.scripts/` Zone B).

---

## 4. Architecture & write path — **the make-or-break design**

### The constraint

Per CLAUDE.md: Claude's Linux sandbox corrupts the SQLite DB through the FUSE
mount (truncates non-sequential binary writes — this project has lost data 4×).
**Only Windows Python may write `directors.db`.** But the dashboard is served by
Flask (`server.py`) which **runs on Windows** — so the question is not "can we
write the DB" (we can, from Windows) but "how do we structure the write so it is
safe, auditable, and never tempts a Claude-sandbox write."

### Recommended design: **stage-then-apply queue** (Option 1)

This mirrors the pattern `server.py` already uses for `/api/deprecate` (atomic
JSON write to `.data/signal_status.json`) and `/api/refresh-all` (spawns a Windows
subprocess). We extend exactly that proven shape.

**Flow:**

1. **Browser → Flask (Windows).** The editor POSTs an edit to a new endpoint
   `POST /api/stage-edit` on `server.py`. The body is one edit intent (add /
   update / reject), with the target fingerprint or RNS ID and the new field
   values.
2. **Flask stages it (Windows, JSON only — safe).** `server.py` appends the edit
   to `.data/_edit_queue.json` using the **same `os.replace` atomic-write helper
   it already has**. **No SQLite write happens here.** This is a Zone-B *JSON*
   write performed by Windows Python — safe, and crucially it does **not** open
   the DB.
3. **Apply step (Windows Python, Zone B, Rupert-run).** A new
   `.scripts/apply_edits.py` reads `_edit_queue.json`, validates each edit, opens
   `directors.db` **on Windows**, applies the INSERT/UPDATE/soft-delete inside a
   single transaction, writes the audit trail, empties the queue, and then runs
   the **standard downstream pipeline** (see §4 "recompute"). Claude must NEVER
   run this — it is on the forbidden list alongside `eval_signals.py` etc. The
   spec hands Rupert the exact command.

**Why stage-then-apply rather than letting Flask write the DB directly:**

- It keeps every DB write inside one auditable, Rupert-triggered script that also
  owns the recompute sequence — you never get a transaction edited but signals not
  re-evaluated.
- It batches edits: Rupert can queue 20 corrections, eyeball the queued list, then
  apply once (one DB transaction, one recompute, one backup).
- The Flask endpoint stays a *pure JSON writer*, identical in risk profile to the
  already-shipped `/api/deprecate`. The thing that touches SQLite is a normal
  Windows pipeline script with the self-healing backup (`db_health.py`) firing
  after it, consistent with every other write-path script.
- **Claude (this assistant) is never in the DB write path at all** — eliminating
  the FUSE-corruption vector by construction.

**Alternative considered — Option 2: Flask writes the DB live.** `server.py` runs
on Windows, so it *could* open `directors.db` and UPDATE on each save. Rejected as
the default because: (a) it couples every keystroke-save to a live DB transaction
+ immediate recompute (slow, and recompute is a multi-minute pipeline); (b) it
spreads DB-write logic into the long-running server process rather than the
audited one-shot scripts; (c) no natural batching / "review before commit" step.
It remains a fallback if Rupert wants instant single-row saves and is willing to
trigger recompute manually.

### The fingerprint problem (critical)

`fingerprint = date|ticker|director|type|shares` is the PK. If Rupert edits the
director name, type, shares, or date, the fingerprint **changes**. A naïve UPDATE
would either violate the PK or silently orphan the `signals` / `paper_trades` rows
that reference the old fingerprint by FK.

**Rule for `apply_edits.py`:** when an edit changes any fingerprint component,
treat it as **delete-old + insert-new within one transaction**, and cascade:
re-point or delete dependent `signals` rows (they'll be recomputed anyway), and
flag any `paper_trades` rows for review. When the edit only touches non-key fields
(role, company, price, value, sector), do a plain UPDATE. The
`reparse_corpus.py` Sprint-3 "update in place / match on
(date,ticker,type,shares,price)" precedent (MEMORY.md) is the model to follow for
director-name corrections.

### Recompute after edit (pipeline order — locked in CLAUDE.md/MEMORY)

Editing a transaction's value/shares/price/role changes which signals fire and
their performance. After applying edits, `apply_edits.py` must run the **same
4-step sequence** the rest of the pipeline uses (MEMORY: "export_json BETWEEN
backtest and build"):

```
eval_signals.py  →  backtest.py  →  export_dashboard_json.py  →  build_dashboard.py
```

(Plus `detect_clusters` / `classify_issuers` / `classify_role` upstream if a ticker
or role changed — confirm exact ordering against `refresh_all.py` at build time.)
This is why batching matters: you don't want to run a ~15-minute recompute per
single field edit.

### Audit & undo

- Every applied edit appends one line to `.data/_edit_audit.jsonl`:
  `{ts, action, rns_id|fingerprint, before:{…}, after:{…}, user:"rupert"}`.
  JSONL append (never rewrite the whole array).
- **Undo** = stage a reverse edit from the `before` snapshot and re-apply. A v1
  "undo last edit" can simply re-queue the inverse. Full transactional rollback of
  an already-applied-and-recomputed batch is out of scope for v1 (the
  `directors.db.bak` self-healing backup is the safety net for catastrophe).

---

## 5. Validation (in the editor, before staging)

- **Type** must be one of the 6 CHECK values — use a dropdown, not free text, or
  the apply step will fail the CHECK constraint.
- **Shares** integer ≥ 0; **price** ≥ 0 (0 allowed = undisclosed);
  **value** ≥ 0. Warn (don't block) if `value` ≠ `shares × price` and price ≠ 0 —
  this is exactly how the year-as-shares bug shows up.
- **Date** ISO `YYYY-MM-DD`, not in the future, not absurdly old.
- **Ticker** present and uppercased; if it's a new ticker, warn that no price
  history / company page exists yet.
- **Director / company** non-empty.
- **Sector** edit must be a value the benchmark mapping recognises (dropdown from
  the known sector list) so `resolve_sector_benchmark` doesn't fall back to FTSE
  A-S unintentionally.

Validation runs client-side for UX **and** re-runs server-side in
`apply_edits.py` (never trust the client).

---

## 6. Edge cases

1. **Bundled multi-PDMR filing** (the dominant pending bucket — "names not
   extractable from boilerplate"). One RNS = several real transactions. The
   "Add manually" form must allow adding **multiple** rows from one failed filing,
   then mark the RNS as resolved.
2. **Partial extract** (`extracted` non-empty but incomplete — e.g. the Card
   Factory example captured only the SELL_TAX leg, warning notes a missing EXERCISE
   leg). Editor should pre-fill the captured leg and let Rupert add the missing one.
3. **Editing a fingerprint component** → delete-old/insert-new (see §4).
4. **Reject of a filing that was already partly ingested** — must also remove any
   `transactions` rows that came from that RNS (match on `url` containing the
   rns_id), not just drop the pending item.
5. **Concurrent staging** — Rupert is the only user, but the queue file must use
   atomic `os.replace` (the server already does) so a double-submit can't corrupt
   it. Use a lock like the existing `_DEPRECATE_LOCK`.
6. **Sector edit affects many tickers' cohort stats** — a `tickers_meta.sector`
   change re-buckets every transaction for that ticker in the performance page.
   That's intended, but the recompute must run for the change to show.
7. **Re-ingest overwrite risk** — if Rupert manually corrects a row and then a
   future scrape/reparse re-ingests the same RNS, will it clobber the manual fix?
   The `classify_issuers.py` "resets flags every run" gotcha (MEMORY) is a
   precedent for this hazard. The manual edit needs a **sticky marker** (e.g.
   `parser_source='manual'` + an exclusion list the ingest/reparse path consults),
   mirroring how `_excluded_it_cef.csv` protects exclusions. **This must be solved
   or manual edits silently evaporate on the next refresh.**
8. **Excluded issuers / already-deleted transactions** — editing a row whose
   ticker is flagged excluded should still work but warn.

---

## 7. Risks & open questions for Rupert

1. **Stage-then-apply vs live Flask write** (§4 Option 1 vs 2). Recommendation:
   Option 1 (stage-then-apply). **Confirm.** This is the single biggest decision.
2. **Sticky manual-edit protection** (edge case 7). How aggressively must manual
   fixes survive future reparses? My recommendation: `parser_source='manual'`
   wins, and the reparse/ingest path skips any RNS that has a manual edit logged.
   **Confirm the policy** — it shapes the data model.
3. **Who triggers recompute?** Auto-run the 4-step pipeline at the end of
   `apply_edits.py`, or leave it to Rupert as a separate step? Auto is more correct
   (no stale signals) but adds ~15 min to every apply. Recommendation: auto, with
   a `--no-pipeline` flag for batching (mirrors `run_pending_sweep.py`).
4. **Undo depth.** Is "undo last applied edit" enough for v1, or do you need full
   batch rollback? Recommendation: single-step undo v1; rely on `.bak` for disaster.
5. **Reject taxonomy.** Should "Reject" record *why* (junk / boilerplate / foreign
   currency / duplicate)? Cheap to add and useful for later parser improvement.
6. **Scope of Tab A.** 4,352 pending items is a lot. Most are bundled-boilerplate
   that the LLM sweep handles better than hand entry. Recommendation: Tab A
   **prioritises the recoverable buckets** (the exporter already categorises
   pending into 7 recoverability buckets — `export_dashboard_json.py` §1589) and
   hides the hopeless ones, so Rupert isn't drowning. **Confirm which buckets are
   worth manual attention.**
7. **Assumptions I could not fully verify:**
   - I did not trace `apply`/recompute ordering inside `refresh_all.py`
     end-to-end; the exact upstream steps (clusters/classify) that must re-run
     when a ticker or role changes need confirming at build time against
     `refresh_all.py`.
   - I confirmed `_scrape_cache/{rns_id}.html` exists for the sampled IDs and that
     `url` ends in the rns_id, but did not verify cache coverage for **all** 4,352
     pending items — some older/failed-fetch filings may have no cache file, in
     which case the left pane falls back to the external `url` link only.
   - Whether `tickers_meta` always has a row for every transaction ticker
     (749 meta rows vs 521 tx tickers suggests yes + extras, but a missing-meta
     ticker would make the sector field blank — handle gracefully).

---

## 8. Effort estimate & sequencing

**T-shirt size: L.** Main cost drivers: (1) the safe write path + apply script +
recompute wiring + sticky-edit protection (the genuinely hard, data-integrity
part); (2) the fingerprint delete/insert/cascade logic; (3) the side-by-side
editor UI rendering cached RNS HTML safely; (4) test coverage commensurate with
the project's discipline (this touches the DB, so tests are non-negotiable).

Suggested staging — **multiple gates**, consistent with the staged-gate approach.
Do NOT ship this as one big drop:

- **Phase 0 — read-only review surface.** Tab A + Tab B as **read-only**: list
  pending items, show side-by-side (cached HTML + current field values), no
  writes. Ships value (Rupert can finally *see* the queue) with zero DB risk.
  *Gate.*
- **Phase 1 — staging only.** Add the editor fields + `POST /api/stage-edit` +
  `_edit_queue.json` + "show queued edits" view. Still **no DB write** — edits
  accumulate in the queue, Rupert reviews them. *Gate: inspect the queue JSON.*
- **Phase 2 — apply + audit (Rupert-run Windows script).** `apply_edits.py`:
  validation, fingerprint cascade, audit JSONL, `.bak` backup, plain UPDATE path
  first (non-key edits only). Unit tests against a `/tmp` copy of the schema.
  *Gate: QA agent review before Rupert applies to the real DB.*
- **Phase 3 — fingerprint-changing edits + reject + multi-add + recompute
  wiring + sticky protection.** The hard cases. *Gate.*
- **Phase 4 — undo + reject taxonomy + polish.**

Each phase must be QA'd by a specialist agent before Rupert sees the diff
(MEMORY: "QA agent before every gate decision"), and every code write verified
with the Read tool (FUSE truncation rule).
