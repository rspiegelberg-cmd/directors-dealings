# Alpha Research — Quant + Trader Agent Findings (2026-06-10)

Two-agent review: (1) Quant agent ran a factor/interaction scan on the live snapshot data (1,331 distinct BUY transactions with backtest outcomes, 2025-06 → 2026-06); (2) Trader/research agent surveyed the academic and practitioner literature on director-dealing alpha, UK-focused.

---

## 1. Headline verdict from our own data (Quant agent)

**There is no positive combination in the current dataset.** Net of costs and benchmark, the average buy signal underperforms at every horizon (mean net CAR T+90 = **-3.5%**, hit rate 37%, n=859). Out of 142 two-way factor cells with n≥30, 49 passed a strict |t|≥2.5 robustness bar — **and all 49 are negative.** No positive cell survives the first-half/second-half robustness check.

### What IS robust: a negative filter (avoid list)

| Cell | n | mean net CAR T+90 | hit rate | t |
|---|--:|--:|--:|--:|
| CFO × small-cap | 39 | -14.7% | 10% | -4.0 |
| micro-cap (<£50m) × cluster | 42 | -14.2% | 19% | -2.6 |
| mid-cap × tiny value (<£10k) | 76 | -13.2% | 14% | -8.1 |
| exec (T2) × small-cap | 30 | -12.4% | 10% | -3.4 |
| small-cap × cluster | 182 | -10.2% | 21% | -6.4 |

All negative in both halves of the sample. Screening these out removes the worst losers. The "least bad" segments — large-cap (>£1b, -0.9%, t=-1.6), NEDs (-1.6%), Financials (+0.4%, 68% hit, weak t) — are flat, not positive, and not half-sample robust.

OLS: market cap is the dominant stable axis (t=+4.9); transaction £ adds nothing once size is controlled; cluster flag actively hurts (t=-2.8).

### PCA hypothesis: NOT supported

PCA buys: **-4.3%** net CAR T+90 (n=70, t=-1.9) vs -3.5% for everyone else — statistically indistinguishable (two-sample t=-0.37), unchanged after winsorizing. Driven by a few outliers (TRST +80%, CPAI -72%). The impression that PCA performs best almost certainly came from raw/gross returns or memorable winners, not benchmark-adjusted net CAR. No PCA sub-segment turns reliably positive at n≥30.

### Key caveats

- **One-year data window only** (2025-06 → 2026-06), a rising-market period where directors' picks lagged the benchmark (+1.9% raw vs +4.4% benchmark at T+90). A different year could flip signs. This is the dominant limitation.
- T+180 thins to n=589; T+365 unusable (n=36).
- 142 cells tested → multiple-comparison risk; every cell is a hypothesis, not a fact.

---

## 2. What the literature says (Trader agent)

Core finding across 40 years of studies: **buys predict, sells don't**, but the raw average buy signal is weak — essentially all the alpha lives in a filtered "high-conviction" subset. Practitioner composites (Dardas 2011, 2iQ) find the top conviction bucket earns ~+20% 12-month excess vs ~0% for the average buy and *negative* for low-conviction buys.

### Conditioning variables by evidence strength

1. **Buy vs sell** — buys ~+0.5–0.7%/mo; sells ≈ 0 (Lakonishok & Lee 2001; Jeng-Metrick-Zeckhauser 2003).
2. **Opportunistic vs routine** — strip calendar-habitual traders; opportunistic ~+0.8–1.0%/mo, routine ≈ 0. Biggest single modern lift (Cohen-Malloy-Pomorski 2012; Ali-Hirshleifer 2017).
3. **Cluster buys** — 2+ insiders within days roughly doubles the signal (Kang-Kim-Wang 2018; Alldredge & Blank 2019).
4. **Small-cap/illiquid** — effect concentrated in small, low-coverage names (~+7.4% over 12mo for small-cap clusters).
5. **Seniority** — CEO/CFO/Chair > NED (Seyhun 1986); one 2026 microcap ML study pushes back.
6. **Size relative to holding/salary** — bigger relative buys more informative.
7. **First-buy / net-seller reversal** — conviction filter.
8. **Distance from 52-week low** — evidence genuinely mixed on direction.

**Decay:** ~¼ of buy alpha in first 5 days, ~½ in first month, rest over 6–12 months. The T+90 cap cuts off the back half of the curve.

### UK/AIM specifics

- **MAR closed periods** mean UK buys cluster just AFTER results — the US "buy before earnings" signal barely exists here. The high-information moment is the **first trading window after results**.
- **Cost model risk:** Friederich et al. found LSE director-buy alpha positive gross but ~zero net of spread. AIM round-trip spreads commonly run 5–10%, not 50bps. The flat cost assumption may flatter backtests on small names.
- AIM stamp-duty exemption: handled correctly already.
- UK fast reporting makes the announcement timestamp a sharp event marker (Fidrmuc-Goergen-Renneboog 2006).

---

## 3. Reconciling the two reports

The literature says clusters + small-caps amplify alpha; our data says they're the worst losers. Three possible explanations, in order of likelihood:

1. **Window effect** — one rising-market year; small-caps broadly lagged. The literature's effects are measured over decades.
2. **Missing conviction filter** — without the routine/opportunistic split and relative-size data, our "cluster" and "small-cap" buckets mix high-conviction trades with noise (and the literature says the unfiltered average is ~zero or negative).
3. **Cost/benchmark measurement** — 95% of small-cap firings benchmark against ^FTAS fallback (sector coverage only 24%), and the flat cost assumption distorts small-cap economics in both directions.

Conclusion: **don't abandon the strategy on one year of data, but stop expecting the existing fields to yield a positive signal.** The path to alpha runs through new data points + cost-model honesty + a longer window.

---

## 4. Recommended backlog candidates (ranked by value ÷ effort)

| # | Item | Effort | Source |
|---|---|---|---|
| 1 | **Routine vs opportunistic flag** — director buys in same calendar month most years = routine | Compute from own DB | Free |
| 2 | **Parse "resulting holding" from RNS filings** → % increase in director's stake (strongest missing signal) | Parser extension | Already in the filings |
| 3 | **Dynamic per-stock spread (Corwin-Schultz from OHLCV)** replacing flat cost — urgent correction, makes backtests honest | Compute from existing Yahoo data | Free |
| 4 | **Fix sector coverage** (24% of tx) — prerequisite for trustworthy sector-matched CARs (extends B-147/DIR-74) | Data backfill | Free/cheap |
| 5 | **First-buy / net-seller-reversal flag** | Compute from own DB | Free |
| 6 | **Distance from 52-week low/high** | Compute from OHLCV | Free |
| 7 | **"First window after results" flag** (UK MAR timing) | Compute from reporting_dates | Free |
| 8 | **Extend price history backward** to escape the one-year-window problem | Yahoo backfill | Free |
| 9 | Director salary multiple (annual-report scrape) | High effort | Phase 2 |
| 10 | Short interest (FCA daily CSV, ≥0.5% positions only) | Medium | Free, patchy on microcaps |

Items 1, 3, 5, 6, 7 require **no new data feeds** — all derivable from data already in the DB.

---

## 5. Realistic expectations

Net-of-cost alpha in this space is modest, fragile, and small-capacity. Even the best published filters show low hit rates (~38% precision) with positive expectancy — winners pay for losers. Strategy is viable at personal/boutique size, not at scale. Any backtest using flat 50–100bps costs on AIM names should be treated as optimistic until item #3 ships.

## Sources

- Lakonishok & Lee (2001), RFS — https://academic.oup.com/rfs/article-abstract/14/1/79/1587398
- Jeng, Metrick & Zeckhauser (2003) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=146029
- Cohen, Malloy & Pomorski (2012), "Decoding Inside Information" — https://www.nber.org/system/files/working_papers/w16454/w16454.pdf
- Ali & Hirshleifer (2017), JFE — https://bpb-us-e2.wpmucdn.com/sites.uci.edu/dist/c/362/files/2020/07/Opportunism.pdf
- Fidrmuc, Goergen & Renneboog (2006), J. Finance — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2006.01008.x
- Friederich, Gregory, Matatko & Tonks (2002), LSE — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4355018
- Kang, Kim & Wang (2018) — http://apjfs.org/resource/global/cafm/2018-5-4.pdf
- Alldredge & Blank (2019) — https://onlinelibrary.wiley.com/doi/abs/10.1111/jfir.12172
- Zhao (2026), microcap ML study — https://arxiv.org/html/2602.06198v1
- 2iQ practitioner review (incl. Dardas 2011) — https://www.2iqresearch.com/blog/profiting-from-insider-transactions-a-review-of-the-academic-research
- FCA MAR closed periods — https://www.fca.org.uk/markets/market-abuse/regulation
