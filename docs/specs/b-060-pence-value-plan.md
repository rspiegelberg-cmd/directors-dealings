# B-060 — Pence/pounds value misparse — plan

**Status:** Built + self-QA'd (2026-06-05). Code + tests green (helper 16/16,
backfill integration 4/4). Remaining: Rupert runs the Zone-B correction +
re-grade, then the full `unittest discover` sweep is the gate. DIR-35 In Progress.
**Linear:** DIR-35 (P1, 3 pts, `agent:data-integrity-auditor`).
**Supersedes/absorbs:** "Fix 2 — pence/pounds" in
`parser-fix-comp-events-and-pence-2026-06-03.md` (scoped, never built).

## The bug (confirmed in code)

`parse_pdmr.py` stores `price` in **pounds**, and `value = price * shares`.
Conversion at extraction time:

| Cell shape | Handling | Correct? |
|---|---|---|
| `171p`, `171 pence` | `÷100` → £1.71 | ✅ |
| `£1.71`, `GBP 1.71` | as-is → £1.71 | ✅ |
| **bare `171`** (no symbol) | **assumed pounds → £171** | ❌ **100× too high** |

UK RNS table cells routinely quote the price in **pence with no `p` suffix**, so
a bare `171` means 171p = £1.71 but is stored as £171. `value` is then 100×
inflated. Confirmed example from the prior spec: **IGP 8946247** — a real ~£103k
buy stored as ~£10.26m.

Relevant code: `_parse_price_vol` line ~835 and `_parse_price_cell` line ~1695
(the `else: # bare number → assume pounds` branch).

### Why it isn't worse (existing guard)

`reject_suspicious_row` **R3** drops any row with `price > £200` (unless the
ticker is in `HIGH_PRICED_TRUST_ALLOWLIST = {LTI, NXT, AZN, GAW}`) to a pending
queue. So the worst offenders are quarantined, not stored. The **live
contamination window is roughly £50–£200/share** (genuine UK per-share prices
above ~£50 are rare), with a softer £10–£50 band. The diagnostic sizes this.

### Why the obvious fix is wrong (history — do not repeat)

A naive *"bare ≥ 1.0 ⇒ pence"* rule was tried and **reverted**: it divided
genuine pound-quoted bare prices by 100 (e.g. Shell 9580273 corrupted to
£0.3252). A bare number is genuinely ambiguous from text alone. The fix must
**disambiguate using outside information**, not a blanket rule.

## Single source of truth (good news)

`value` is computed once in the parser; `export_dashboard_json.py` and
`backtest.py` read the stored `value`/`price` and never re-scale by 100. So the
fix is isolated to parse/ingest — no double-conversion hunt downstream.

## Proposed fix — market-close reconciliation (recommended)

For a **bare** price only (explicit `£`/`p`/`GBP` stay authoritative and
unchanged):

1. Parser emits the bare value plus a warning `bare_price_unit_ambiguous`
   instead of silently assuming pounds.
2. A reconciliation step (in the ingest / `reparse_corpus` path, which has DB
   access) compares both interpretations — `val` (pounds) and `val/100` (pence)
   — against the **market close from the `prices` table** on/nearest the
   transaction date. On-market PDMR deals execute at ~market price, so the
   correct interpretation is the one within tolerance (e.g. ±30%) of close.
3. If neither is within tolerance, or `prices` has no row for that ticker/date
   → **route to pending review** (never guess).

Why this over the spec's "reconcile against prose-stated total": we already have
the `prices` table; capturing the prose consideration figure is a second
extraction problem. Market-close reconciliation reuses existing data and also
catches non-bare misparses as a bonus sanity check.

**Alternative (lighter)** if the diagnostic shows only a handful of affected
rows: a one-off targeted correction list (fingerprint → correct price) applied
by a Zone-B backfill, plus the parser warning so new ones get caught. Decide
after sizing.

## Sequenced plan

1. **Size it (Rupert, read-only).** Run `python .scripts/_diag_pence_value.py`
   from PowerShell; paste the three sections. This gives the row count + the
   signal blast radius and decides fix depth.
2. **Confirm strategy** (market-close reconciliation vs targeted correction).
3. **Build (Claude, Zone A).** Parser warning + reconciliation helper; write the
   positive/negative test sets *first* (TDD):
   - **Pence regression:** IGP 8946247 → price ≈ £1.71, value ≈ £103k.
   - **Pounds non-regression:** Shell 9580273 and a basket of genuine
     pound-quoted bare buys → unchanged (NOT divided by 100).
   - Ambiguous / no-market-price → routed to pending, not stored wrong.
4. **QA gate.** Full `unittest discover` sweep + Read-tool truncation check.
   `test_p3_lookahead.py` still green.
5. **Correct the corpus (Rupert, Zone B / PowerShell).** Targeted
   `reparse_corpus.py` (or a narrow correction backfill) over affected rows,
   then `eval_signals --rebuild` + export + build to re-grade signals cleanly.
6. **Re-run the diagnostic** → expect the suspect band cleared.

## Decision (locked 2026-06-05) + diagnostic findings

Rupert signed off **full market-close reconciliation** across all bare-priced
rows (not the lighter targeted correction). `_diag_pence_value.py` on the live
corpus found:

- **188 strong suspects** (price > £50/share). **120 have a fired signal**
  (f1=46, s1=33, t3=19, b1=5, t1a=5, t1b=5, …) — live signal CAR / value-weighting
  is contaminated. 57 suspects are type=BUY.
- Plus a **soft band of 1,364 rows (£10–50)** to sweep in the same pass.
- **Two failure modes** the one reconciliation mechanism must handle:
  - **Mode A — pence-as-pounds (the B-060 target).** `÷100` yields the right
    price, confirmed against market: IGP 8948382/… 171→£1.71 (real ~£103k),
    GBG, FVA, GNC, PAGE, QQ./KIE/BGO SIPs.
  - **Mode B — garbage price (total or junk grabbed as per-share), mostly on
    GRANT/EXERCISE.** `÷100` does NOT fix it (IDOX £1.8m/share; FAN value
    £1.7bn; WOSG £856m; BILN £277m). R3's £200 reject doesn't apply to
    grants/exercises, so these slipped in. Reconciliation must **quarantine**
    these (neither reading matches market close) rather than guess.

## Resolution rule (the helper `price_reconcile.reconcile_price`)

Pure function — inputs `(price_raw_gbp, shares, market_close_gbp, tx_type)`,
returns `(price_gbp, status)` where status ∈ {`ok_pounds`, `corrected_pence`,
`unresolved_quarantine`, `no_market_price`}:

1. If `market_close` is missing → `no_market_price` (route to pending; never guess).
2. Compute ratio of each reading to `market_close`:
   `r_pounds = price_raw / close`, `r_pence = (price_raw/100) / close`.
3. Pick the reading whose ratio is within tolerance **TOL = ±35%** of 1.0.
   - only pounds in tolerance → `ok_pounds` (unchanged)
   - only pence in tolerance → `corrected_pence` (price = raw/100)
   - both in tolerance (only when raw≈close≈ tiny) → prefer pounds (no change)
   - neither → `unresolved_quarantine`
4. GRANT/EXERCISE with a £0/nil-cost or strike price far from market are
   expected → only quarantine these if `value` is implausibly large
   (price×shares above a hard ceiling, e.g. £100m) to avoid false-flagging
   legitimate nil-cost awards. Tolerance tuning documented from the diagnostic.

Build = Zone A (`price_reconcile.py` + `test_b060_pence_reconcile.py`, TDD) and a
Zone-B `backfill_price_units.py` (Rupert runs: corrects `corrected_pence`,
quarantines `unresolved_quarantine`/`no_market_price`, logs every change). Then
`eval_signals --rebuild` + export + build to re-grade signals.

## Out of scope
- Comp-event STRICT_BUY reclassification (Fix 1 of the 06-03 spec) — separate.
- Foreign-currency (USD/EUR) rows — already routed to pending.

## Risks
- `prices` coverage gaps (illiquid AIM names, transaction-date holidays) → those
  rows go to pending rather than being corrected automatically. Acceptable.
- Tolerance band tuning: too tight → false pendings; too loose → mis-resolves.
  Set from the diagnostic's observed spread; document the chosen value.
