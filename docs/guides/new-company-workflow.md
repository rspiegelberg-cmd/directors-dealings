# New company classification workflow

**Purpose:** When a new ticker appears in the dashboard (via a fresh RNS filing),
it needs a market-cap classification (`small_cap = 1` for < £500m, `0` for >= £500m)
before it appears on the correct size-band performance page.

---

## Normal automated path

`refresh_all.py` runs the full pipeline in sequence:

1. Scrapes new filings from Investegate.
2. Calls `backfill_ticker_meta.py` — fetches market cap from Yahoo Finance.
3. Calls `classify_small_cap.py` — sets `small_cap` flag from market cap.
4. Calls `backtest.py` — writes `_backtest_results.csv` with `small_cap` column.
5. Calls `export_dashboard_json.py` — writes `signals_small.json` + `signals_large.json`.
6. Calls `build_dashboard.py` — renders `performance_small.html` + `performance_large.html`.

For most tickers Yahoo Finance resolves the market cap automatically.
No manual step is needed.

---

## When the "Unclassified" chip shows > 0

The index page shows a chip:

> **N companies unclassified** — small and large-cap pages may be incomplete.

This means `N` tickers in `tickers_meta` have `market_cap_gbp IS NULL`.
Yahoo Finance could not resolve them.

### Step 1 — Confirm Yahoo cannot resolve the ticker

Run from Windows PowerShell (Zone B — reads DB, no writes):

```powershell
python .scripts\backfill_ticker_meta.py --missing-only
```

This retries the Yahoo lookup for unclassified tickers only. If a ticker
is still NULL after this run, it is genuinely unresolvable via Yahoo.

### Step 2 — Check what is unclassified

Run from bash (read-only, safe):

```bash
python .scripts/manual_classify.py --check
```

Prints every ticker with `market_cap_gbp IS NULL`. Cross-reference with:
- Yahoo Finance search (yfinance may use a different symbol)
- LSE website: https://www.londonstockexchange.com/stock/{ticker}
- Reuters or Bloomberg for market cap data

### Step 3 — Add the override

Open `.scripts/manual_classify.py` and add a row to the `OVERRIDES` list:

```python
("TICK", 123_000_000, "manual-company-name-YYYY-MM-DD"),
```

Format: `(ticker, market_cap_in_gbp, source_note)`.

The threshold is £500,000,000 — tickers below are `small_cap = 1`.

### Step 4 — Apply the override and deploy

Run from Windows PowerShell (Zone B — writes to DB):

```powershell
python .scripts\manual_classify.py
python .scripts\classify_small_cap.py
python .scripts\backtest.py
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
python .scripts\snapshot_db.py
```

The `--check` flag in Step 2 is the only safe-from-bash operation.
Steps 4 are all Zone B — run from PowerShell only.

---

## What the `--check` flag does

```powershell
python .scripts\manual_classify.py --check
```

- Connects to the DB read-only (SELECT only, no writes).
- Runs: `SELECT ticker FROM tickers_meta WHERE market_cap_gbp IS NULL`.
- Prints each ticker and a count summary.
- Exits cleanly without modifying anything.

Safe to run at any time, including from the bash sandbox.

---

## MANUAL_CAP / OVERRIDES dict

The `OVERRIDES` list in `manual_classify.py` covers tickers that are:

- Delisted (no live Yahoo data): e.g. DLG (acquired by Aviva), BBB (delisted AIM).
- Primary listing moved: e.g. AHT (NYSE), INDV (NASDAQ).
- Too small for Yahoo to track reliably: e.g. CFCP, COR, ENET.

When you add a new entry, include a `source_note` in the format
`manual-company-name-YYYY-MM-DD` so future maintainers can trace where
the number came from.
