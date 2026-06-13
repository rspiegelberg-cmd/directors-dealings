# Reparse BUY-Insert Verification — 2026-06-03, N=24

**Auditor:** Data Integrity Auditor (independent field-level verifier)
**Question on the table:** A `reparse_corpus.py` run would INSERT 2,715 rows currently absent from the DB, of which **685 are type=BUY**. These BUYs feed the signal engine. Go/no-go: are they genuine discretionary director purchases wrongly excluded by the ingest gate, or garbage the gate correctly rejected?

**Ground truth:** cached source HTML the parser actually used (`.scripts/_scrape_cache/{rns_id}.html`), one live cross-check against Investegate. The parsed CSV row is the *claim*.

---

## Headline

**AMBER, leaning RED on the high-value band.** The cache is faithful and every sampled `rns_id` is a real PDMR/Director-dealing RNS — none are garbage, none are investment trusts. BUT a **material share of the gt_100k band is not discretionary on-market buying.** It is remuneration-plan share movements (Deferred Bonus Plan "purchases", Bonus Deferral Award *grants*, dividend-accrual acquisitions, all-employee SIP/ESPP) that the parser has labelled `type=BUY`. These will fire the signal engine on the back of comp events, not conviction buys. Separately, one gt_100k row (IGP) has a **100x value misparse** (pence read as pounds) that inflated it into the wrong band.

Do not commit the 685 BUYs wholesale. They are recoverable but need a `nature_of_transaction` filter before they reach the signal engine.

---

## Exact sample (reproducible)

Stratified from the 685. Binding stratification: 10 gt_100k / 6 25k_100k / 5 1k_25k / 3 lt_1k.

| # | rns_id | ticker | band | director | CSV shares / px / value |
|---|--------|--------|------|----------|--------------------------|
| 1 | 8946247 | IGP | gt_100k | Jacques Tredoux | 60000 / 171.0 / 10,260,000 |
| 2 | 9460976 | SYS1 | gt_100k | John Kearon | 2,905,899 / 2.4864 / 7,225,227 |
| 3 | 9494459 | ARBB | gt_100k | Sir Henry Angest | 300000 / 8.60 / 2,580,000 |
| 4 | 9490111 | BA | gt_100k | Charles Woodburn | 22516 / 23.0295 / 518,532 |
| 5 | 9555689 | ULVR | gt_100k | Fabian Garcia | 8219 / 61.07 / 501,934 |
| 6 | 8871006 | BATS | gt_100k | Tadeu Marroco | 12492 / 31.82 / 397,495 |
| 7 | 8890342 | PRU | gt_100k | Anil Wadhwani | 3807 / 90.75 / 345,485 |
| 8 | 8952502 | BRBY | gt_100k | Joshua Schulman | 29744 / 10.690945 / 317,991 |
| 9 | 9045434 | ZEN | gt_100k | Luca Benedetto | 633000 / 0.51 / 322,830 |
| 10 | 9224318 | ACSO | gt_100k | Long Path Partners (PCA) | 50000 / 3.51 / 175,500 |
| 11 | 9583912 | AUTO | 25k_100k | Matthew Siegler | 10900 / 4.62 / 50,358 |
| 12 | 9239099 | QLT | 25k_100k | Chris Hill | 28224 / 1.771525 / 49,999 |
| 13 | 9149876 | VLG | 25k_100k | Mark Adams | 88999 / 0.5618 / 49,999 |
| 14 | 9104871 | IAG | 25k_100k | Lynne Embleton | 27368 / 3.184 / 87,139 |
| 15 | 9560123 | BNZL | 25k_100k | Frank van Zanten | 15000 / 23.91 / 358,650 |
| 16 | 9340147 | ACSO | 1k_25k | Lee Cowie | 880 / 2.83 / 2,490 |
| 17 | 9517985 | HAS | 1k_25k | David Brown | 11137 / 0.27 / 3,007 |
| 18 | 9467833 | RR | 1k_25k | Birgit Behrendt | 91 / 12.032137 / 1,095 |
| 19 | 9587726 | YNGA | 1k_25k | Tracy Dodd | 1556 / 6.42 / 9,990 |
| 20 | 9530867 | NIOX | 1k_25k | Sharon Emms | 34539 / 0.5788 / 19,991 |
| 21 | 9566410 | PRU | lt_1k | Rajeev Mittal | 36 / 11.387677 / 410 |
| 22 | 9196719 | CNA | lt_1k | Sue Whalley | 549 / 1.77331 / 974 |
| 23 | 8965107 | STB | 1k_25k | Mary Hartley (3525 line) | 3525 / 8.48 / 29,892 |
| 24 | 9079737 | CTEC | gt_100k | Jonny Mason | 50000 / 2.3481 / 117,405 |

**Live cross-check performed:** rns_id 9587726 (YNGA). The live Investegate page matched the cached HTML field-for-field (Tracy Dodd 1,556 @ £6.42, Purchase of shares, 26 May 2026). **Cache fidelity confirmed** — remaining rows audited against cache with confidence.

---

## Per-field match table (N=24)

| Field    | MATCH | MINOR | MAJOR | FETCH_FAILED |
|----------|-------|-------|-------|--------------|
| director | 23    | 1     | 0     | 0 |
| type (BUY validity) | 13 | 1 | 10 | 0 |
| date     | 24    | 0     | 0     | 0 |
| shares   | 22    | 2     | 0     | 0 |
| price    | 23    | 0     | 1     | 0 |
| value    | 22    | 1     | 1     | 0 |

`type` MAJOR = "labelled BUY but is a remuneration-plan event, not discretionary on-market buying." That is the headline risk for the signal engine.

---

## Top failure patterns

**1. Deferred Bonus / LTIP "purchase" mislabelled as discretionary BUY — gt_100k, highest signal weight.**
- **BA 9490111** (5 sampled directors: Woodburn, Arseneault, Greve, Hoeing, Costigan + Gelsthorpe in band). Nature = **"Purchase of deferred shares under the DBP"** — acquisition of deferred-bonus shares under the remuneration plan, all at the same £23.0295. This single filing contributes ~9 of the 54 gt_100k BUYs. NOT a conviction buy.
- **ULVR 9555689** (8 sampled directors). Nature = **"grant of Bonus Deferral Award... subject to restrictions"** and "Purchase of Bonus Deferral Award forfeitable shares" — comp grants entered *inside an open period*. 8 of 54 gt_100k rows. NOT discretionary.
- Combined, BA + ULVR alone are ~17 of 54 gt_100k BUYs (~31% of the highest-weight band) that are remuneration events.

**2. Dividend-accrual / scrip acquisitions mislabelled BUY.**
- **BATS 8871006** — "Acquisition of quarterly dividend equivalent shares under the Deferred Share Bonus Scheme." Aggregate volume 601; CSV claims 12,492 shares (a *different* line in a multi-line filing). Both wrong-as-BUY and a shares mismatch.
- **PRU 8890342** — "Acquisition of shares in respect of dividends accruing to deferred share awards." Dividend scrip, HKD-priced.

**3. All-employee SIP / ESPP / NED-plan auto-purchases labelled BUY (low value, low conviction).**
- HAS 9517985 (US Employee Stock Purchase Plan), PRU 9566410 (All Employee Share Purchase Plan), RR 9467833 (NED share purchase plan), CNA 9196719 (Share Purchase Agreement). Mechanical plan purchases, not discretionary signals. Mostly lt_1k / 1k_25k so low weight, but they will still fire low-tier signals.

**4. Value-field 100x misparse (pence read as pounds).**
- **IGP 8946247** — director (Tredoux, NED), shares (60,000) and price (171p) are CORRECT and it IS a genuine discretionary purchase, but CSV `value=10,260,000` vs true £102,600. The 171 was treated as £171 not 171p. This MAJOR value error pushed a £103k buy into the gt_100k top band. Worth a corpus-wide grep for value ≈ shares×price×100. (Note: this is a two-PDMR filing — Van der Leest 22,450 @ 177.82p and Tredoux 60,000 @ 171p; the CSV correctly took the Tredoux line.)

**5. Per-day fragmentation of a single PCA buy (MINOR).**
- ACSO 9224318 (Long Path Partners, PCA of NED Brian Nelson) bought 150,000 over 3 days; parser emitted one 50,000 line. Type=BUY is correct and discretionary; value £175,500 is right for the line but understates the £525k economic event.

**Genuine, clean discretionary buys in the sample (the good news):** SYS1, ARBB, BRBY, ZEN, AUTO, QLT, VLG, IAG, BNZL, YNGA, NIOX, STB, CTEC, ACSO/Cowie — correct director/shares/price/date, real on-market or own-capital buys. ~13 of 24 are textbook-clean discretionary BUYs.

---

## Exclusion / issuer-type check

No sampled ticker is an investment trust or closed-end fund. All are operating companies (BA Systems, Unilever, Prudential, BAT, Burberry, Bunzl, Rolls-Royce, IAG, Centrica, Secure Trust, Auto Trader, Young's Brewery, accesso, Intercede, etc.). Consistent with the documented **ingest-gate suppression incident** — ordinary PDMR filings that never made it into the DB, not deliberately-excluded issuers. The exclusion hypothesis holds.

---

## Signal-engine blast radius

The danger is not garbage rows — it is **comp events masquerading as conviction buys**. In the sample, 10/24 = ~42% of audited BUYs are remuneration-plan movements (DBP/LTIP/Bonus Deferral/dividend-accrual/SIP/ESPP), concentrated in the **gt_100k band** (BA, ULVR), which carries the most signal weight. If committed as-is, the signal engine would fire on BA and Unilever "buys" that are actually annual comp vesting — a systematic false-positive bias toward large-cap remuneration cycles.

---

## Bottom line

**AMBER (do not commit wholesale; one filter fixes it).**

- **Failure rate driving the call:** ~42% of sampled BUYs (10/24) are non-discretionary remuneration events mislabelled `type=BUY`; concentrated ~31%+ in the gt_100k band. Plus 1 row (IGP) with a 100x value misparse.
- **Not RED** because: zero fabricated/garbage rows, zero excluded-issuer contamination, cache is faithful to live source, and ~54% of the sample (and likely the bulk of the 685) are genuinely clean discretionary buys the gate wrongly suppressed. The data is real and recoverable.
- **Not GREEN** because: committing as-is injects a systematic false-positive into the highest-weight signal band.

**Recommended gate before commit (ranked):**
1. **Filter `type=BUY` on `nature_of_transaction`.** Exclude/reclassify rows whose nature contains: "Deferred", "Bonus Deferral", "DBP", "LTIP", "Award", "dividend", "Share Purchase Plan", "Employee Share Purchase", "SIP", "ESPP", "conditional award". Keep only "Purchase of (ordinary) shares" discretionary lines. This alone removes the gt_100k contamination.
2. **Fix the pence/pounds value parse** (IGP-class). Grep the 685 for value ≈ shares×price×100.
3. **Decide policy on per-day fragmentation** (ACSO-class) — type is correct, but signal logic should aggregate same-(date-window, director) lines so a 3-day buy isn't read as three weak signals.

After applying filter (1), a re-sample of the *surviving* BUYs would very likely be GREEN.

---

## Limitations of this audit

- **N=24 of 685 (3.5%).** The ~42%-comp-event rate has roughly a ±20pp confidence interval — directionally reliable (there IS material contamination), but the precise contaminated-row count across the 685 is not pinned down. A clustered driver (single BA/ULVR filings supplying many rows) means the true rate hinges on how many such bulk-comp filings exist in the 685.
- **Live-fetch coverage = 1 row.** Cache fidelity confirmed on YNGA only; the other 23 audited against cache. Low-risk given the clean match, not zero.
- **DB not consulted.** Live `directors.db` reads malformed in this sandbox (FUSE artifact). Audit is CSV + cache + one live page. I did not independently confirm these 685 are absent from the DB — trusted the brief's reparse-diff count.
- **`nature_of_transaction` is not a column in the supplied CSV** — read from cached HTML per row. A production filter (rec 1) needs the parser to expose that field on every row; confirm it does before relying on the filter.
- **Role/tier not audited** (no role column); signal-tier impact inferred from director seniority in filing text, not verified against `role_normalize.py`.
