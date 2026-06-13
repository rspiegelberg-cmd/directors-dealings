# Stage 2 fixtures

Test inputs for `.scripts/test_stage_02.py`. Each fixture exercises a
specific parser path. The parser must never silently mis-attribute a
transaction; these inputs anchor that contract.

| File | Source | Retrieved | Type | What it asserts |
|---|---|---|---|---|
| `bundled_pdmr_9540067.html` | https://www.investegate.co.uk/announcement/rns/schroders--sdr/director-pdmr-shareholding/9540067 | 2026-05-12 | bundled (real) | The parser detects "bundled multi-PDMR" and refuses to split (`extracted=[]`); the warning string names each numbered PDMR (Richard Oldfield, Meagen Burnett, Johanna Kyrklund, Georg Wunderlin) with their roles. |
| `sip_barclays_9564893.html` | https://www.investegate.co.uk/announcement/rns/barclays--barc/director-pdmr-shareholding/9564893 | 2026-05-12 | SIP (real) | The parser classifies the Taalib Shaah / Barclays SIP-trustee acquisition as `type=SIP`, NOT `BUY`. Exercises non-discretionary detection. Date pulled from the explicit `Date of the transaction` label (2026-05-07), not the announcement-day intro (2026-05-12). |
| `clean_buy_9562545.html` | Auto-picked from 60-day smoke scrape (2026-05-13) | 2026-05-13 | BUY (real) | F&C Investment Trust (FCIT), 300 shares at £3.3121 ≈ £993.63. Real Investegate template. |
| `clean_sell_9564757.html` | Auto-picked from 60-day smoke scrape (2026-05-13) | 2026-05-13 | SELL (real) | Personal Group Holdings (PGH), 306 shares at £3.84 = £1,175.04. Real Investegate template. |
| `clean_buy_synthetic.html` | **SYNTHETIC** (hand-written using the canonical Investegate RNS template) | 2026-05-12 | BUY | Churchill China plc / CHH, Jane Doe (CFO), 1000 shares at £3.21 = £3,210. Deterministic; exercises the "Price(s)\nVolume(s):" table layout and BUY classification. |
| `clean_sell_synthetic.html` | **SYNTHETIC** | 2026-05-12 | SELL | Rolls-Royce Holdings / RR, John Smith (CEO), 5000 shares at 820p (£8.20 per share) = £41,000. Exercises pence-to-pounds coercion and SELL classification. |
| `clean_sell_9563991.html` | Superseded stub | 2026-05-13 | (n/a) | Was an auto-picked candidate that turned out to be bundled-multi-PDMR after a later parser hardening pass. Replaced by clean_sell_9564757.html. Kept as a tombstone. |

## Fixtures that are real vs synthetic

Schroders (9540067), Barclays (9564893), F&C IT (9562545), PGH
(9564757) are real Investegate-served HTML fetched live during the
Stage 2 build. The two `*_synthetic.html` files are hand-written
using the canonical Investegate RNS template — they exist alongside
the real ones to give the test suite deterministic, version-controlled
inputs that won't change if Investegate's HTML changes.

The test suite asserts against both the real and synthetic fixtures,
so a parser regression on either path lights up the test output.
