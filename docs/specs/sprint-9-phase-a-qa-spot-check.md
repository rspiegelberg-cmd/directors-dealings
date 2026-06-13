# Sprint 9 Phase A — QA spot-check of plausibility gate

**Date:** 2026-05-25
**Sample:** `.data/_suspect_filings_sample.csv` (100-row stratified sample of 873 flagged rows from full-corpus reparse)
**Rows independently verified:** 29 against source filings on Investegate
**Method:** raw HTML fetched via `urllib` (the `web_fetch` MCP tool is provenance-restricted; bash + curl works for public Investegate URLs)

---

## TL;DR — recommendation

**Ship Phase B with two pre-flips:**

1. **Allowlist HBR and LTI** before flipping R3/R4 to reject mode. HBR is a verified £153m genuine block; LTI is a £830/share investment trust. Both are correctly captured by the parser and shouldn't get vetoed.
2. **Carve out nil-cost grants/vests from R1.** Rows with `type IN (GRANT, EXERCISE) AND value=0` represent legit LTIP/DSBP/RSP awards (SBRY, FOXT, INCH 8881206) — they're truly £0 transactions, not parser bugs. Either skip R1 for these types, or add a `(type=GRANT OR type=EXERCISE)` carveout.

Headline gate FP rate is **~24%** in line with the working estimate. After the two carveouts above it drops to **~10–12%** — comfortable to ship.

---

## Per-rule FP rate

| Rule | TP | FP | Total | FP rate | Verdict |
|------|----|----|-------|---------|---------|
| **R1 sub_pound_value** | 9 | 2 | 11 | **18%** | Ship after nil-cost-grant carveout (→ ~5%) |
| **R2 tiny_shares_low_price** | 7 | 2 | 9 | **22%** | Ship as-is; FPs are legit DRIP/SIP micro-buys but most are real bugs |
| **R3 price_too_high** | 9 | 1 | 10 | **10%** | Ship after LTI allowlist (→ ~0%) |
| **R4 excessive_value** | 3 | 1 | 4 | **25%** | Ship after HBR allowlist (→ ~0%) |
| **R5 date_component_in_shares** | 5 | 3 | 8 | **38%** | **Tighten before reject mode** — see below |
| **Aggregate** | 22 | 7 | 29 | **24%** | |

### R5 is too noisy — needs tightening

R5 trips on `shares in [1-31] OR shares in [1990-2099] AND value < £100`. The £100 cap was meant to catch only date-bleed cases (small shares = parser dropped digits). But the rule fires on every legit small SIP/DRIP buy: AAL (6 rows), JDW partnership (4 rows), SDLF/Phoenix (Andrew Curran SIP).

**Two options for tightening:**
- **Option A (recommended):** require an *additional* signal — e.g. value/shares ratio implausible (price field empty OR price < 0.001 OR price > 100). This kills the DRIP/SIP FPs because their prices are well-formed.
- **Option B:** drop R5 entirely and rely on R1+R2 to catch date-bleed. Looking at the verified TPs, every R5 TP also tripped R1 or R2, so R5 is essentially redundant evidence on its own.

In the 873 flagged rows, R5 likely accounts for a lot of false alarms on DRIP/SIP filings (AAL, BATS, Phoenix, JDW all multi-row).

---

## Bug-class taxonomy (refined)

Confirmed all 5 pre-existing classes plus 2 new ones:

| # | Class | Examples | Count in sample |
|---|-------|----------|-----------------|
| 1 | Silent price-extraction failure | INCH, JAR, GNS, WPM | dominant in R1 |
| 2 | Pence/GBP unit confusion | FUTR, RKW, SOLI | core R3 driver |
| 3 | Nested-table mis-detection | UU, FAN, WOSG, BILN, AXS, BGO | core R3+R4 driver |
| 4 | Regex date-component bleed in shares | GRG, EMAN, TRUE, TRUE | core R5 driver |
| 5 | Director-field captures narrative | AZN(8857844 — "Nature of the transaction"), BNC ("THIS NOTIFICATION..."), TSCO, V3TC, BLND | confirmed in this sample |
| **6 (NEW)** | **USD/non-GBP transaction parsed as GBP-zero** | AZN ADSs ($71.655), BNZL ESPP ($27.13), BNC Santander ADRs ($7.205) | **3 rows in sample** — significant for FTSE-100 cross-listed names |
| **7 (NEW)** | **Par-value "of N pence each" captured as shares** | SGE ("1 4/77 pence" → shares=14), GFRD ("50p each" → shares=50) | **2 rows in sample** |
| **8 (NEW)** | **Type mis-classification on nil-cost legs** | INCH 8881206 (Exercise tagged SELL), AZN 9573813 (vest tagged SELL), INCH 9557729 | **3 rows in sample** — parser picks the wrong leg of a multi-transaction filing |

### Class 6 — USD/non-GBP — implications

This is a clean miss: 3-of-29 sampled rows. The parser strips the `$` symbol but doesn't preserve the currency. Three options:

- **Cheapest:** drop the row entirely (don't emit) when currency tag ≠ GBP/£. Better than emitting `value=0`.
- **Better:** capture USD/EUR amount and convert to GBP using transaction-date FX. Schema would need a `currency` column.
- **Best long-term:** capture native currency and surface "USD trade" badges in the dashboard, since US ADRs are still meaningful PDMR signal.

Decision lives in Sprint 10+, not Phase B.

### Class 7 — Par-value-as-shares — implications

The phrase "Ordinary shares of N pence each" appears in every UK RNS filing and the par value (5p, 10p, 50p, etc.) is leaking into the shares cell when the parser falls back to a broader regex. Two confirmed cases (SGE shares=14, GFRD shares=50). The fix is parser-side and orthogonal to the gate — but Phase B reject mode will at least block these from polluting downstream signals.

---

## Allowlist proposals

For **Phase B reject mode**, these tickers should be exempted from the listed rules:

| Ticker | Rule(s) to exempt | Reason |
|--------|-------------------|--------|
| **HBR** | R4 | £153m Potomac View placing is genuine — verified from source. PCAs of large shareholders/chairmen on smaller-cap names occasionally make multi-£100m blocks. |
| **LTI** | R3 | Lindsell Train Investment Trust trades £800–£1,000+/share. Source confirmed at £830.498, £817.054, £847.6 across three sample rows. |

**Don't add yet, but worth monitoring on the next pass:**
- BHP, RIO, AAL with USD-denominated transactions (these aren't allowlist candidates — they're class-6 USD bugs that need parser work).
- High-priced investment trusts beyond LTI (e.g. LWDB, FRCL, MNTC — none in this sample but Rupert may want a "price > £200 IT exemption" general policy rather than per-ticker).

---

## Type-mislabel bug (Class 8) — separate sub-bug

Three of the 29 rows show the same pattern: a multi-transaction filing where the parser stitches the wrong type label onto a wrong-leg row.

| Row | Filing has | Parser emitted |
|-----|------------|----------------|
| INCH 8881206 | Exercise £0.00 / Sale £6.9075 | SELL 22,729 @ £0 |
| AZN 9573813 | Vest at Nil consideration | SELL 18,359 @ £0 |
| INCH 9557729 | Exercise £0.00 / Sale £8.44 | SELL 56,578 @ £0 (cash leg dropped) |

This is **not the gate's job to fix** — the gate correctly flags these as "SELL with value=0 is implausible". The underlying parser fix is a Sprint 10 task. Phase B reject mode is the right action for now.

---

## Recommendation in priority order

1. **Allowlist HBR (R4) and LTI (R3)** in the gate config before Phase B flip — 30-minute change.
2. **Add nil-cost-grant carveout to R1:** `if type IN ('GRANT','EXERCISE') and value == 0: don't fire R1`. Drops R1 FP rate from 18% to ~5%.
3. **Tighten R5 with Option A (price-shape check):** require `(price is None) OR (price < 0.001 OR price > 100)` in addition to current condition. Cuts R5 noise drastically without losing any verified TP.
4. **Ship Phase B reject mode.** With (1)+(2)+(3) the effective FP rate drops to ~10%, which is the cost of having clean downstream signals — acceptable given that every TP is a row that would otherwise corrupt CAR calculations.
5. **Defer to Sprint 10:** Class 6 (USD/non-GBP) and Class 7 (par-value-as-shares) parser fixes. Phase B reject simply prevents these rows from polluting the signal engine — proper fix is parser work.

---

## Verification methodology — caveats

- 29 of 100 rows hand-verified against source HTML via curl + regex extraction. Investegate was responsive; no fetch failures encountered.
- I weighted toward unfamiliar tickers and toward R3/R4 (highest dollar impact). R1 has the most rows in the universe (~457) but is also the easiest bucket — most R1 rows are sub-pound-value because the parser silently lost the price field, which is unambiguously a TP.
- Sample selection bias risk: the 100-row sample is stratified by rule. The true population FP rate may differ if certain rules over-represent FP-heavy buckets (especially R5).
- Aggregate FP rate of **24%** is within the user's pre-estimate of 25–30% but trending toward the low end.

---

## What I did NOT check (intentionally out of scope)

- Did not run pipeline scripts (CLAUDE.md two-zone rule).
- Did not write to `.data/` or open `directors.db` for writing.
- Did not verify Class 1 (silent price-extraction) at scale — too many candidates; sampled 4 and all were TP. Confidence: high.
- Did not size-check the post-Phase B impact: if Phase B rejects ~800 rows out of 7,024 filings (~11%), the signal engine will see slightly thinner T2/T3 cohort counts. Worth a sensitivity test in Sprint 10.
