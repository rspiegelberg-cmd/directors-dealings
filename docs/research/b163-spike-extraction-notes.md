# B-163 spike — extraction notes (data-collection phase)

Date: 2026-06-10. Method: WebSearch + web_fetch + sandbox curl/pdftotext.
Rung (a) Companies House API: **untested as designed** (no API key) — but see the
CH-web finding below, which partially substitutes for it. Rungs exercised: (b) issuer
IR PDF, (c) web search / press.

## Headline numbers

- **Found 15/20 (75%) overall.**
- **4 of the 5 fails are the same failure mode: the buying director was appointed
  too recently to appear in any published remuneration disclosure** (TOO appointed
  May 2025 vs FY2024 accounts; IGR CEO-designate May 2026; PANR chairman installed 2026;
  POLR CEO promoted Sep 2025 vs FY Mar 2026 AR not yet out).
  Excluding that timing gap, **15/16 established directors found (94%)**.
- The only established-director fail was VTY (Vistry): the FY2025 AR exists and
  contains the figure, but the PDF hides behind a JS pdf-viewer and press only covers
  policy maxima. A human with a browser would get it in ~10 minutes — fail is
  budget/extraction, not availability.
- Effort: median **3 operations** (searches + fetches) per company; range 1–6.
  Comfortably under the 10-min/report go threshold once the toolchain below is used.

## Per-stratum hit rate

| Stratum | Found | Fails | Notes |
|---|---|---|---|
| <£50m | 3/4 | TOO | KZG £5k nominal salary; CAD/SYS1 exact tables |
| £50–250m | 2/4 | IGR, PANR | both new-appointee gaps |
| £250m–1b | 2/4 | VTY, POLR | VTY = extraction fail; POLR = new-appointee gap |
| £1–5b | 4/4 | — | all exact or press-corroborated |
| >£5b | 4/4 | — | press alone often sufficient |

## Where the figures hid

- **Main Market**: always in the Sch 8 "single total figure" audited table of the
  Annual Report / standalone DRR PDF. PSN, HOC, CAD, GNC, BNZL extracted exactly.
- **AIM**: better than feared. Every AIM company whose report we opened had a
  **per-director** table (SYS1, ARBB, ATOM, MAB1) — Rule 19 / QCA practice, not just
  aggregates. The Companies-House-iXBRL-aggregate worry did not materialise in this
  sample (though we never needed the iXBRL route).
- **Mega-caps (>£5b)**: press/aggregator coverage (CityAM, gurufocus, Simply Wall St)
  surfaces the single figure in 1–2 searches without touching a PDF.

## Toolchain findings (the big cost-model news)

1. **`curl` + `pdftotext -layout` in the sandbox works on issuer AR PDFs** (tested up
   to 15.5MB / Hochschild). Tables come out grep-able. This collapses rung (b) from
   "~5 min manual PDF reading" to ~30 seconds scripted. No Zone-B writes involved.
2. **Plain `web_fetch` truncates at ~125k chars** — fine for standalone DRR PDFs
   (Bunzl), but full ARs get cut before the remuneration section (SYS1, CAD both
   truncated mid-document). Use curl+pdftotext for anything bigger than a DRR extract.
3. **Companies House web documents download without an API key**
   (`find-and-update.../filing-history` → `…/document?format=pdf`). Filing history
   also gives appointment dates for free — that is how the TOO gap was diagnosed.
   A keyed CH API is still worth testing for batch use, but is not a blocker.
4. Some IR sites are JS shells where no PDF URL is reachable by curl (Vistry,
   Berkeley, BAE investor portals). Press/aggregator is the fallback there.

## Data-quality caveats for the metric design

- **Currency**: 5 of 15 found figures are not GBP-native (HOC, CAD, ATOM in USD;
  GNC, ULVR in EUR). The pipeline needs an FX conversion at FY-end.
- **Zero/near-zero pay**: ASC Deputy Chair waives all fees (£0); KZG interim CEO on
  £5k nominal with deferred fees. Salary-multiple = buy/pay explodes or divides by
  zero exactly where insider conviction stories are most interesting. Needs a floor
  or a separate bucket.
- **NED fees vs exec single figure** mix fine mechanically but mean different things
  (£37k chair fee vs £3.1m CEO package) — the multiple is not comparable across the
  two without a role-aware normalisation (we already have the 8-tier role taxonomy).
- **Part-year roles**: ULVR's €5.6m covers 2 months CFO + 10 months CEO; BKG's £8.0m
  is his last full CEO year before moving to Exec Chair. "Latest FY pay" is noisy
  around role transitions.
- **Staleness**: "latest FY" lags 3–15 months behind the buy date (e.g. ARBB FY2024
  for a 2026 buy).
- **The new-appointee hole is structural and correlated with the signal**: directors
  frequently buy on or shortly after appointment — exactly when no pay figure exists.
  Any production design must treat "no disclosure yet (appointed < 1 FY ago)" as its
  own NULL bucket, not a parse failure.

## Provisional read vs thresholds (go/no-go memo comes later, after B-156 overlap test)

75% overall sits in the **CONDITIONAL (60–79%)** band; 94% on established directors
at ~3 ops/name is **go-leaning** *if* the metric scope excludes (or NULL-buckets)
recently appointed directors and applies FX + zero-pay handling. Aggregate-only
disclosure — the designated NO-GO trigger — was **not observed in any of the 16
reports actually opened**, including all AIM names.
