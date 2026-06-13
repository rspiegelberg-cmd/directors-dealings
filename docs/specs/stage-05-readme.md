# Stage 5 — read me first

**Purpose:** orient anyone (you, a future Claude session, a sub-agent) before they touch Stage 5 work. Read this first, then pull up the spec you need.

## What Stage 5 is

The Directors Dealings dashboard — a static HTML page that surfaces today's PDMR buy signals, tracks each signal type's edge vs benchmark over four horizons (T+1 / T+21 / T+90 / T+252), and links into per-ticker detail pages. Built on Tailwind CDN + Chart.js. No backend, no build step. Reads two JSON files produced by an upstream data pipe.

## Reading order

1. **`stage-05-design-notes.md`** — the *what*. All locked design decisions from the 2026-05-13 design session: per-signal scoreboard as centrepiece, horizon dropdown set, status auto-computation, MTM definition, active-cluster definition, signal-badge tooltips, sparkline spec, v1 scope cuts (no mobile, no alerts, no sells, manual refresh). Read this to understand intent.

2. **`stage-05-build-spec.md`** — the *how* for the main dashboard. Architecture, data contracts for `signals.json` and `dealings.json`, per-component specs with Tailwind class hints + Chart.js sketches, build order, acceptance criteria, gates. This is the brief a generalist agent would build from.

3. **`stage-05-1-company-page.md`** — the *how* for the per-ticker detail page that opens on row click. Header strip, annotated price chart with director-buy markers, transactions table with mandatory RNS source links, signal-firing history showing T+1/T+21/T+90/T+252 CAR per firing, cluster history. Sister deliverable to spec 2.

4. **`stage-04-6-dashboard-exporter.md`** — the data pipe that feeds Stage 5. Python script that reads SQLite + the Stage 4 backtest CSV and writes the two JSON files. Field-by-field derivation. Without this, Stage 5 has no data.

## Build gates (do not start production HTML until all four are green)

1. Stage 4 ships — signal engine + backtest with CAR outputs at T+1/T+21/T+90/T+252.
2. **`stage-04-5-data-quality.md`** ships — the F1 +4232% outlier fix (Yahoo `adjclose` switch). Without it the F1 mean CAR is a lie at every horizon.
3. Stage 4.6 exporter built — converts Stage 4 outputs into the JSON files Stage 5 reads.
4. Company-page spec drafted (done — spec 3 above).

## Designer agent

`docs/agents/dashboard-designer.md` — invoke for any visual-design question on the dashboard surface. Carries Rupert's continuous model-assessment mandate: every invocation re-reads the latest backtest, flags kill candidates, proposes model adjustments in writing, defends the dashboard against optimistic readings.

## Open calls before build

- Confirm or kill the "brewing" cluster definition (2+ directors, most recent buy 30–90 days back). In the wireframe; not yet in spec 01.
- Decide whether the deprecate button ships as a one-click instruction toast (current default) or waits for a write-to-disk endpoint.
- Decide whether to default to dark mode or light mode for v1 (the design notes don't pin this — current mockups assume light).

## Working-rules reminder

- Local-only workflow — no git / PR ceremony.
- Plan-first for any non-trivial change.
- Truncation check is mandatory after every code write. Bash heredoc is the default for spec writes over ~100 lines (see `memory/feedback_truncation_check_mandatory.md`).
- Deploy specialist agents proactively. The dashboard-designer agent above is the right tool for visual-design questions; spawn it.
