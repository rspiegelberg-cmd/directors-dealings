# Spec: Cluster-Buy Detector

**Status:** Ready to build
**Owner:** Rupert
**Target ship:** This week (one focused sitting)
**Author:** PM scoping pass, 2026-05-04

---

## One-line summary

Surface "cluster buys" — multiple directors at the same company buying within a 30-day window — as a first-class signal on the dashboard, with a dedicated panel listing active clusters at the top.

## Why this signal, why first

Cluster buys are the single most-evidenced subset of insider-trading alpha. The thesis: when one director buys, you have one person's view of the company. When two or three buy in the same window, you have correlated conviction — much harder to dismiss as personal liquidity, diversification, or routine portfolio behaviour. Academic and practitioner studies of UK and US insider data consistently show cluster buys outperform isolated buys by a wide margin over 6–12 months.

Today the dashboard treats every transaction as one row of equal weight. A daily browse means scanning 30–50 rows looking for the few that matter. The cluster panel turns that into "look at the panel first, then everything else if you have time." It's the highest signal-density change available and uses only data we already have.

## User story

As a UK equity trader using this app for daily idea generation, I open the dashboard each morning and want the first thing I see to be: "these are the companies where multiple insiders are buying right now." I want to see who's buying, when, how much in aggregate, and click through to the company page to dig deeper.

## Scope

**In scope.**

A new "Active clusters" panel rendered above the existing dealings table on `directors-dealings-dashboard.html`. The panel lists every ticker with an active cluster, sorted by recency of the most recent BUY in the cluster. Each row shows: ticker, company name, number of distinct directors involved, total aggregate £ value of the cluster's BUY transactions, the date range spanning the cluster, and a link to the per-company page.

A `CLUSTER` badge appended to the existing transaction rows in the main table, on every BUY row that participates in a cluster. Visual treatment: small coloured pill, same row, no layout change.

A new script `.scripts/detect_clusters.py` that reads `dealings-log.json`, computes clusters, writes results to `.scripts/clusters.json`. Called by `update.py` after `refresh.py` has updated the log, before the dashboard window is regenerated.

A change to `.scripts/refresh.py` (or whichever script writes the dashboard HTML) to consume `clusters.json` and render the panel + badges.

**Out of scope.**

Sells, exercises, grants, SIPs. Cluster signal is a BUY-only phenomenon; mixing in other types muddies it.

Per-director cluster scoring (e.g. "this director has been in 3 winning clusters"). That's a leaderboard feature, scoped separately as #3 in the roadmap.

Automatic alerts (email, push). Push notifications are a separate feature; the user opens the dashboard daily anyway.

Configurable cluster window. The 30-day window is hard-coded. Future tuning is a one-line change to a constant.

Cluster scoring/ranking beyond "active vs not." No "this is a 4-star cluster" weighting in v1.

## The detection rule (precise)

A cluster is a set of BUY transactions where:

1. All transactions share the same `ticker`.
2. The set contains at least 2 *distinct* directors (by `director` name, exact match — director name normalisation is out of scope; if "John Smith" and "John A Smith" appear in the log they count as different people for now and will need a tidy-up pass later).
3. For every transaction in the set, there exists at least one other transaction in the set whose `date` is within 30 calendar days. Equivalently: the cluster is a connected component under the relation "same ticker, distinct directors, dates within 30 days."
4. Only BUY transactions count. SELL, SELL_TAX, EXERCISE, GRANT, and SIP rows are excluded entirely (they don't form clusters and don't break clusters).

A cluster is "active" if its most recent BUY is within the last 90 days. Active clusters appear in the panel. Older clusters are still tagged with the badge in the main table but don't surface in the panel.

**Why connected-component, not pairwise:** if director A buys on day 1, director B buys on day 25, and director C buys on day 50, all three belong to the same cluster (A↔B and B↔C overlap windows even though A and C are 49 days apart). A naive pairwise-only rule would split this into two two-person clusters, which understates the signal.

## Data shape

`.scripts/clusters.json`:

```json
{
  "generated_at": "2026-05-04T14:30:00Z",
  "window_days": 30,
  "active_window_days": 90,
  "clusters": [
    {
      "cluster_id": "CHH-2025-12-18",
      "ticker": "CHH",
      "company": "Churchill China",
      "director_count": 3,
      "directors": ["Caroline Stevens", "..."],
      "transaction_count": 4,
      "total_value": 24512.10,
      "first_buy_date": "2025-12-18",
      "last_buy_date": "2025-12-22",
      "active": true,
      "fingerprints": ["2025-12-18|CHH|Caroline Stevens|BUY|1547", "..."]
    }
  ]
}
```

`cluster_id` format: `{ticker}-{first_buy_date}`. Stable across runs unless the cluster's earliest date changes.

The dashboard renderer joins on `fingerprints` to know which transaction rows get the badge.

## UI

**Panel (above main table).** Header: "Active clusters — last 90 days." Subhead: "*N* tickers where multiple directors are buying." Rows sorted by `last_buy_date` descending. Each row is a single line: `{ticker}  {company}  {director_count} directors · £{total_value}  {first_buy_date} → {last_buy_date}`. The whole row links to `companies/{ticker}.html`. If no active clusters, the panel renders "No active clusters in the last 90 days." in muted text rather than disappearing — absence of clusters is itself information.

**Badge (in main table).** A `CLUSTER` pill, distinct colour from the existing `BUY` type pill. Placed adjacent to the type. Hover tooltip (or alt text): `Part of cluster: {director_count} directors, £{total_value}`.

No changes to the existing table columns, sort, or filters.

## Implementation plan

1. **Write `.scripts/detect_clusters.py`.** Reads `../dealings-log.json`, runs the connected-component algorithm above, writes `../.scripts/clusters.json`. Stdlib only, in keeping with the rest of the codebase. Idempotent: same input → same output.

2. **Wire it into `update.py`.** Add a step after `refresh.py` (which updates the log) and before whatever regenerates the dashboard. The step is a single `subprocess.run(["python", ".scripts/detect_clusters.py"], check=True)`.

3. **Update the dashboard renderer.** Whichever script writes `directors-dealings-dashboard.html` reads `.scripts/clusters.json`, renders the panel above the table, and adds the badge to applicable rows. CSS for the new pill is added inline — no separate stylesheet change.

4. **Update per-company pages.** `gen_company_pages.py` already runs per ticker; have it consume `clusters.json` too and add a "This company has an active cluster" callout on its page when applicable. Small change, big payoff.

5. **Manual sanity-check pass.** Open the dashboard. Confirm: (a) the CHH 2025-12-18 multi-director event shows up as a cluster, (b) at least one other expected cluster surfaces, (c) the count and £ totals match a hand-tally for one cluster, (d) badges appear on every row that should have them and only on those rows.

## Acceptance criteria

- Running `python .scripts/detect_clusters.py` produces a valid `.scripts/clusters.json` with the schema above.
- The dashboard renders the active-clusters panel above the main table.
- Every BUY row in the main table that participates in any cluster (active or not) has a `CLUSTER` badge; no other row has one.
- The CHH 2025-12-18 multi-director event appears as a single cluster in the panel and tags all four CHH BUY rows from that filing.
- The empty-state message renders correctly when filtering the log to a date range with no clusters (verified by temporarily editing the active-window constant down).
- `update.py` end-to-end run completes without error and the dashboard reflects clusters from any new filings.
- No regression: existing dashboard rows, sorting, and per-company pages render identically apart from the additions.

## Edge cases and known issues

**Director name variants.** If the same person appears as "John Smith" and "John A. Smith" in different filings, today they count as two directors and could spuriously trigger a cluster of 1+1=2 distinct directors that's actually one person. Acceptable for v1; flag for later cleanup. A grep over `dealings-log.json` for near-duplicate director names would surface most cases.

**Same-day filings from one company.** When a company files several PDMR notices on the same day (often the case after a results window), multiple directors buying on the same day is genuine cluster signal and the algorithm handles it correctly. No special case needed.

**Tax-lot sells next to buys.** A director selling shares to cover tax (`SELL_TAX`) on the same day they were granted shares isn't a buy and shouldn't pollute the cluster. Already handled — only BUYs participate.

**Late notifications.** The CHH example is a "late notification" — the disclosure date is months after the transaction date. The detector keys on transaction `date`, not disclosure date, which is correct (the trades did happen close together). Worth noting in the panel that `last_buy_date` is the trade date, not the announcement date.

**Single-filing multi-director clusters.** Some companies file one combined PDMR notice covering multiple directors who all bought the same day. This produces a strong but one-shot cluster. Treat the same as any other — a cluster is a cluster.

## Effort

Roughly 4–6 hours of focused coding for someone comfortable with the codebase. Breaks down as: 60–90 minutes for the detector script, 90–120 minutes for the dashboard renderer changes (including the per-company callout), 30–45 minutes for CSS, 60 minutes for testing and tweaks.

## What this unlocks

Once `clusters.json` exists, the broader signals overlay (#1 in the roadmap) becomes easier — CLUSTER is one of its five badges, and the others (CONVICTION, POST-DROP, SIZE, FIRST-TIME) follow the same "compute on refresh, write to JSON, consume in renderer" pattern. The cluster detector is the prototype for that whole layer.

The leaderboard feature (#3) depends on the price backfill, which is being kicked off in parallel today.
