# B-163 spike — go/no-go memo: salary-multiple conviction feature

Date: 2026-06-11. Inputs: `b163-spike-sample.csv` (20-company extraction sample),
`b163-spike-extraction-notes.md`, B-156 deployed snapshot (`transactions.csv` with
`resulting_shares`), `alpha-research-2026-06-10.md`. Protocol: sprint-61-plan §3.

## Verdict: CONDITIONAL-GO — build it, but scoped

Build the salary-multiple feature for **signal-relevant tickers only** (top ~100 by
buy-signal firing frequency), refreshed annually after AR season, as a scoped B-16x
ticket. Do not attempt full 627-ticker coverage.

## 1. Extraction results vs thresholds

| Measure | Result | Threshold read |
|---|---|---|
| Raw hit rate | 15/20 (75%) | CONDITIONAL band (60–79%) |
| In-scope hit rate (established directors) | 15/16 (**94%**) | **GO band (≥80%)** |
| Effort | median 3 search/fetch ops, range 1–6 | well under 10 min/report |
| Aggregate-only disclosure (the NO-GO trigger) | **0/16 reports opened** | not triggered |

4 of the 5 fails are one failure mode: the buying director was **appointed too recently**
to appear in any published remuneration disclosure (TOO, IGR, PANR, POLR). The fifth
(VTY) is a tooling fail — the figure exists behind a JS PDF viewer; a human gets it in
~10 min.

Per-stratum: <£50m 3/4 · £50–250m 2/4 · £250m–1b 2/4 · £1–5b 4/4 · >£5b 4/4.
All sub-100% strata are new-appointee gaps except VTY.

Rung distribution: (a) CH API untested (no key; CH **web** documents download keyless —
partial substitute). (b) issuer AR/DRR PDF: 10/15. (c) press/aggregator: 5/15.
Machine-readability: 9/15 clean audited per-director tables via curl+pdftotext
(works to 15.5MB); 2/15 partial (narrative disclosures); 4/15 press-figure only.
AIM was better than feared — every AIM report opened had a per-director table
(Rule 19 / QCA practice); the iXBRL-aggregate worry did not materialise.

## 2. Overlap test vs B-156 %-stake-increase: INCONCLUSIVE (n=1)

Cross-referencing the 15 priced directors against the post-B-156 snapshot: they have
**32 BUY rows**, of which exactly **1** has `resulting_shares` populated (HOC, Landin,
2026-05-11: salary_multiple 0.14×, stake increase +12.0%). n=1 < 5 → no Spearman rho;
the redundancy test designed in §3.5 **cannot be run** on current data. B-156 populated
only 55/1,872 BUY rows (2.9%) — below even its reset 10% acceptance bar.

What the inconclusive result actually tells us: the two features have **structurally
different coverage**. Salary-multiple needs an annual report — available for ~all
established board directors (94% here). %-stake needs the filing itself to state the
resulting holding — present in ~3% of rows. They cannot substitute for each other at
these coverage levels; they are **complements**, and the |rho|≥0.7 close-as-redundant
branch is moot for the foreseeable corpus.

(FX note: 5/15 pay figures converted at rough spot USD 0.79 / EUR 0.85, not FY-end
rates — fine for a spike, a build should pin FY-end rates.)

## 3. Why CONDITIONAL-GO (the argument)

**For building:** the alpha research (2026-06-10) found **no positive factor in the
current dataset** — every robust cell is negative. The literature is equally clear that
the alpha in director dealing lives in a filtered *high-conviction* subset (top
conviction bucket ~+20% 12-month excess vs ~0 for the average buy), and buy-size-
relative-to-pay is the canonical conviction measure. New conviction features are the
only credible path to a positive signal, and this one is 94% extractable at ~3
operations per company with a mostly-scripted toolchain. B-156's sparse 2.9% coverage
strengthens, not weakens, the case: the alternative conviction denominator barely exists.

**Why not unconditional GO:** (1) ~27% of BUY rows (PCAs + non-board PDMRs) are
structurally out of scope — board directors only. (2) The new-appointee hole is
*correlated with the signal* (directors often buy at appointment, exactly when no pay
figure exists). (3) Pay figures lag the buy by 3–15 months and need annual refresh.
None of that justifies paying for full-universe coverage before the feature has proven
any predictive value. Scoping to the ~100 tickers where buy signals actually fire
captures most of the testable sample at a fraction of the collection cost.

**Edge cases any build must handle (all observed in a 20-name sample):**
- **FX**: USD/EUR-denominated reports (5/15) → convert at FY-end rate, store currency.
- **£0 / nominal pay**: ASC fee waiver (£0 denominator), KZG £5k interim salary —
  floor or separate "nominal-pay" bucket; the multiple explodes exactly where the
  conviction story is most interesting.
- **NED fees vs exec package**: £37k chair fee vs £3.1m CEO single figure are not
  comparable multiples — normalise via the existing 8-tier role taxonomy.
- **Recently appointed**: own NULL bucket ("no disclosure yet, appointed <1 FY ago"),
  never a parse failure.
- **Part-year / role transitions** (ULVR, BKG): flag, don't average.

**Single biggest risk of this recommendation:** we spend a sprint building a clean
salary-multiple column and it turns out — like every factor scanned so far — to work
only as a *negative* filter, with too little forward sample to validate for a year or
more. That is a real possibility; the mitigation is the scoped build (small collection
cost, annual refresh) and the fact that this is the best-documented conviction filter
in the published literature, so it is the right next experiment even if it fails.

## 4. Proposed next step

Ticket a scoped **B-16x: salary-multiple pipeline** — top ~100 firing tickers,
curl+pdftotext rung-(b) first with press fallback, FY-end FX, role-aware
normalisation, NULL buckets (new-appointee, nominal-pay, out-of-scope PCA/non-board),
annual refresh after AR season. Estimate 5–8 pts. Re-run the §3.5 overlap test
whenever B-156 BUY coverage reaches a usable n (≥5 paired rows).
