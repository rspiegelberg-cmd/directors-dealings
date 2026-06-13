# Data-Integrity Audit — B-060 price units + year-as-shares

**Date:** 2026-06-03
**Auditor:** data-integrity-auditor (independent, read-only)
**Source of truth:** locally cached Investegate filing HTML (`.scripts/_scrape_cache/{rns_id}.html`)
**DB snapshot:** `/tmp/audit2.db` (fresh copy after Rupert's `buy_strictness` backfill; `PRAGMA integrity_check` = ok)
**Companion data:** `docs/audits/diff_2026-06-03_b060_yearshares.csv` (196 per-row verdicts)

---

## Headline

Both flagged cohorts are **real, systemic data-corruption patterns** — not detection noise. Every row I could open against its original filing in Cohort B was wrong. In Cohort A the picture is more nuanced: roughly **half** of the rows I could verify are genuinely wrong, and **half are false alarms** (the database is actually correct; Yahoo's price quirk triggered the flag).

The most important limitation up front: **65% of all transactions in the database carry no source URL** (older `regex`-parsed corpus). For those rows there is no cached filing to check against, so I cannot pronounce them right or wrong. They are reported as `NO_URL` / `AMBIGUOUS` (unverifiable), not as clean.

| Cohort | Flagged | Verifiable vs filing | Genuinely WRONG | DB CORRECT (false alarm) | Ambiguous | Unverifiable (no URL) |
|--------|--------:|---------------------:|----------------:|-------------------------:|----------:|----------------------:|
| A — price units | 166 | 80 | **42** | 32 | 6 | 86 |
| B — year-as-shares | 30 | 14 | **14** | 0 | 0 | 16 (strongly suspect) |

---

## Cohort A — price units

### Field-level match rate (verifiable rows only, n=80)

| Verdict | Count | Share |
|---------|------:|------:|
| MAJOR_MISMATCH (price wrong) | 42 | 53% |
| MATCH (DB correct) | 32 | 40% |
| AMBIGUOUS | 6 | 7% |

So of the rows I could actually check, **just over half the price flags are real**. The other ~40% are the DB being right and Yahoo being the odd one out (Yahoo reports some lines in GBp vs GBP, which is exactly the ×100 signal the detector keys on).

### Top failure patterns (the 42 genuinely-wrong rows)

| # | Mechanism | Count | Example |
|---|-----------|------:|---------|
| ii | **Failed extraction → placeholder `1.0` (or `0.01`)** | 21 | CRDA, LSEG, ABDN, LGEN, CRDA × many — multi-leg filings (a Nil-price option/award line **plus** a real market line). Parser collapses to `price=1.0`. |
| i | **Pence stored as pounds (×100 too high)** | 13 | PAGE `136.63` should be `£1.3663`; DOTD `52.412` → `£0.52412`; ART `44.7` → `£0.447`; CKT, IGP, FVA. Filing states the number in pence (often with no `p`/`£` symbol) and the parser banked it as pounds. |
| iii | **Pounds stored as pence (÷100 too low)** | 5 | SVT `0.2758` should be `£27.58`; WINE `0.0062` → `£0.62`; EBQ. Caused by malformed filing tokens like `£27.58p` / `£0.62p`. |
| iv | **Aggregate VALUE captured as price** | 1 | WOSG `66643.0` — that is the **£66,643 total consideration**, not a per-share price. True price `£5.185`. The single most dangerous class: internally plausible, passes every sanity check. |
| v | Other / garbage | 2 | GRG `4.0` → `£16.93` (also broken in Cohort B); GMR `0.2` → `£0.325`. |

**Dominant pattern: multi-leg filings defeating the parser (21 of 42).** When a filing lists an option-exercise/award leg (Nil price) alongside the actual market purchase, the parser fails and writes a `1.0` placeholder instead of the real market price. This is the biggest single fix lever.

### Notable individual cases
- **WOSG** — value-as-price. Net-flat to the eye, catastrophically wrong on the number. Only caught because £66,643 is too big to be a share price.
- **PRU (×2)** — filing is denominated in **HKD** (Prudential's Hong Kong line), not pence-vs-pounds. Correct value needs an FX conversion, so these stay AMBIGUOUS, not auto-fixable.
- **CRDA ×6, LSEG ×4, ABDN ×3, LGEN ×3** — same multi-leg failure repeating for the same issuers; these are predictable and scriptable.

---

## Cohort B — year-as-shares

### Field-level match rate

| Verdict | Count |
|---------|------:|
| MAJOR_MISMATCH (shares wrong) | **14** (every verifiable row) |
| AMBIGUOUS (no URL — unverifiable, but pattern-matches) | 16 |
| MATCH | 0 |

**Every single row I could open against its filing is wrong.** The parser captured the transaction's **calendar year** (2025 / 2026) as the share **volume**. Because these rows are internally consistent (`value = shares × price` recomputed on the bad number), they pass every arithmetic sanity check — consistency proves nothing here, which is exactly why source reading was required.

### Confirmed wrong (with true volume from the filing)

| Ticker | Date | Director | DB shares | TRUE shares | Source quote |
|--------|------|----------|----------:|------------:|--------------|
| REC | 2025-09-29 | Thomas Arnold | 2025 | **375** | "£0.6008 375 shares" |
| LORD | 2025-11-13 | K. Kilpatrick | 2025 | **30,000** | "£0.226 30,000" |
| MARS | 2025-12-24 | Justin Platt | 2025 | **158,309** | "£0.587290 158,309" |
| REC | 2025-12-29 | Thomas Arnold | 2025 | **407** | "£0.5540 407 shares" |
| ACRM | 2026-02-03 | Richard Mayall | 2026 | **1,000,000** | "0.9p 1,000,000" |
| REC | 2026-02-27 | Thomas Arnold | 2026 | **392** | "£0.5757 392 shares" |
| WCAT | 2026-04-24 | Mandhir Singh | 2026 | **20,002,000** | "Disposal GBP0.00075 20,002,000" |
| CAD | 2026-04-30 | Thibaut de Gaudemar | 2026 | **3,828,943** | "£0.045 Volume 3,828,943" |
| TRUE | 2026-05-07 | Trevor Brown | 2026 | **175,000** | "1.9p Volume 175,000" |
| TRUE | 2026-05-18 | Trevor Brown | 2026 | **7,500** | "2.7p Volume 7,500" |
| GRG | 2026-05-19 | Richard Smothers | 2026 | **1,615** | "16.93 Volume 1615" |
| EMAN | 2026-05-20 | Charles Dorfman | 2026 | **127,083** | "Aggregated volume 127,083" |
| V3TC | 2026-05-21 | Fungai Ndoro | 2026 | **868,405** | "Purchased 868,405 at 2.3029p" |
| EMAN | 2026-05-27 | Charles Dorfman | 2026 | **67,649** | "35.5 pence 67,649" |

- **EMAN / Charles Dorfman ×2 — independently confirmed WRONG**, matching the prior memory note. Both legs (127,083 and 67,649) are real volumes captured as `2026`.
- **REC / Thomas Arnold ×3, TRUE / Trevor Brown ×2** — recurring director+ticker pairs, all wrong. These are dividend-reinvestment / regular small purchases where the volume sits in an unusual column the parser misreads.
- **16 AMBIGUOUS rows** (BWY, SCLP, SFOR ×2, RHR, TKO ×3, KEN, TST ×2, ESNT ×2, LSL ×3) have no source URL so cannot be source-verified — **but every one has `shares == its own transaction year`**, the exact fingerprint of the confirmed bug. I rate them high-confidence-suspect, not proven.

---

## Signal-engine blast radius

Of the **56 confirmed-wrong rows** across both cohorts, **21 have already fired a signal** (some fired several):

| Signal | Fires on wrong rows |
|--------|--------------------:|
| f1_first_time_buy | 14 |
| s1_cluster_buy | 8 |
| b1_lone_conviction_buy | 5 |
| t3_ned_buy | 5 |
| t1a_ceo_founder_buy | 2 |
| t1b_cfo_buy | 2 |
| t6_company_sec_buy | 1 |

**Why this matters for the product's core promise (CAR tracking):**
- **Cohort B (volume)** directly corrupts position sizing and any volume-weighted or notional-based logic. A "buy" of 2,026 shares vs the real 20,002,000 (WCAT) is off by four orders of magnitude — it understates conviction massively and can suppress or distort cluster/conviction signals.
- **Cohort A (price)** corrupts the per-share entry price, which is the anchor for every abnormal-return calculation. A price wrong by ×100 (PAGE, DOTD) or a placeholder `1.0` (CRDA/LSEG) feeds a garbage entry point into T+1/T+21/T+90 CAR. The WOSG value-as-price case is the worst — it looks completely normal.

These 21 fired signals should be treated as **untrustworthy until the underlying rows are repaired and the signals re-evaluated.**

---

## Recommended parser fixes (ranked by leverage)

1. **Multi-leg filing handling (fixes ~21 Cohort-A rows).** When a filing's Price(s)/Volume(s) block has more than one line — typically a Nil/option leg + a real market leg — the parser must (a) skip Nil/`consideration`/award lines, and (b) take the actual market-purchase price, not default to `1.0`. This is the single highest-value fix.

2. **Volume-column disambiguation (fixes all of Cohort B).** The parser is grabbing the date/year token as the volume. It needs to anchor volume to the column that follows the price token (and cross-check against the aggregate volume line, which is almost always present and correct). A hard guard — "reject any parsed `shares` value that exactly equals the transaction's calendar year, and re-extract" — would catch the whole class cheaply.

3. **Pence-vs-pounds normaliser (fixes ~13 Cohort-A rows).** Many filings give the price as a bare number in pence with no symbol, or with the unit only in a column header ("Price (p/share)", "Price(p)"). The parser must read the unit context and divide by 100 when pence is indicated. Add malformed-token handling for `£NN.NNp` and `£0.NNp` (treat as pounds, ignore the stray `p`).

4. **Aggregate-value-as-price guard (fixes WOSG; prevents future silent failures).** Reject a per-share price that, when multiplied by the volume, does **not** reconcile with the filing's stated aggregate consideration — i.e. cross-validate price × volume against the disclosed total. This is the only defence against the value-as-price class because it passes all other checks.

5. **HKD / non-GBP detection.** Flag filings denominated in HKD/USD/EUR (Prudential etc.) for separate handling or FX conversion rather than storing the foreign-currency figure as if it were GBP.

---

## Reparse / repair scope

- **Auto-repairable now (highest confidence):** the 14 Cohort-B rows (true volumes listed above) and the ~40 Cohort-A rows with an unambiguous correct value in `diff_2026-06-03_b060_yearshares.csv` (`correct_value` column). These can be corrected in place from the CSV.
- **Needs the parser fixes above before a clean reparse:** the recurring multi-leg issuers (CRDA/LSEG/ABDN/LGEN) — better to fix the parser and reparse than hand-patch, since these issuers file this way every time.
- **Cannot be auto-fixed:** PRU (HKD, needs FX); CGL, CGS, MOON, SOU-style Nil/award and unmarked-unit rows (6 AMBIGUOUS) — need a human eyeball.
- **Out of reach until the corpus gets URLs:** the 86 NO_URL Cohort-A rows and 16 NO_URL Cohort-B rows. A backfill of source URLs onto the legacy regex corpus is a prerequisite for ever auditing the majority of the database.
- **After any repair:** re-run signal evaluation — 21 signals are currently sitting on corrupt inputs.

---

## Limitations (stated honestly)

1. **Coverage is the dominant limitation.** I could verify 80/166 Cohort-A rows and 14/30 Cohort-B rows. The rest have no source URL. My "genuinely wrong" counts are floors, not totals — the true error count is almost certainly higher in the unverifiable tail.
2. **Cohort A `correct_value` is best-effort per-share.** For multi-leg filings I report the market-purchase leg; where a filing gives both a per-line and an aggregate-average price (e.g. DOTD) I used the per-line figure. Small rounding differences vs the dashboard are possible.
3. **Unit inference for bare numbers** (no `p`/`£` symbol, e.g. CGL `85.85`) relied on filing context; where context was genuinely absent I marked AMBIGUOUS rather than guess.
4. **I did not write to `directors.db`.** This is a read-only finding. All repairs are Rupert-run / parser-fix work.
5. The cohorts were pulled with the exact SQL provided; I did not re-derive them differently, so any detector blind-spots in that SQL are inherited here.
