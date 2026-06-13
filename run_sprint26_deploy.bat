@echo off
REM Sprint 26 "Company intelligence layer" deployment script.
REM
REM Runs the one-time enrichment backfills introduced in Sprint 26:
REM   B-097 / B-101 / B-105 -- backfill_ticker_meta.py
REM                            (market cap, website, AIM benchmark fix)
REM   B-096                  -- backfill_reporting_dates.py
REM                            (upcoming results dates)
REM
REM Then rebuilds the dashboard as normal.
REM
REM GATE: After backfill_ticker_meta runs, this script pauses and shows
REM which tickers had their benchmark changed to ^AIM. Review these before
REM continuing -- the backfill_benchmarks + export steps will shift their
REM CAR numbers (B-105 gate, locked 2026-06-03).
REM
REM Run from the project root:
REM     run_sprint26_deploy.bat

cd /d "%~dp0"

echo ================================================
echo  Sprint 26 -- Company Intelligence Layer Deploy
echo ================================================
echo.

REM ── Sanity check Python ───────────────────────────────────────────────────
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    pause
    exit /b 1
)

REM ── Pre-flight DB health check ────────────────────────────────────────────
echo [pre-flight] Checking DB health...
python .scripts\db_health.py check >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: directors.db failed integrity check.
    echo Run start.bat to attempt auto-restore from backup.
    echo.
    pause
    exit /b 2
)
echo [pre-flight] DB OK.
echo.

REM ═════════════════════════════════════════════════════════════════════════
REM  STEP 1 -- backfill_ticker_meta.py
REM  Fetches Yahoo quoteSummary per ticker:
REM    - market_cap_gbp + shares_outstanding  (B-097)
REM    - website_url                          (B-101)
REM    - AIM exchange detection → is_aim=1 + benchmark_symbol='^AIM'  (B-105)
REM  ~784 tickers × 0.5s = ~7 minutes. Resume-safe.
REM ═════════════════════════════════════════════════════════════════════════
echo [step 1/5] Enriching tickers_meta from Yahoo quoteSummary...
echo            (market cap, website, AIM detection -- ~7 min)
echo.
python .scripts\backfill_ticker_meta.py --verbose --resume
if errorlevel 1 (
    echo.
    echo ERROR: backfill_ticker_meta.py failed (exit %ERRORLEVEL%).
    echo Check output above for details.
    echo.
    pause
    exit /b 3
)
echo.
echo [step 1/5] Done.
echo.

REM ═════════════════════════════════════════════════════════════════════════
REM  B-105 GATE: Show which tickers were assigned ^AIM benchmark.
REM  These tickers will have their CAR recalculated against FTSE AIM
REM  instead of FTSE All-Share. Review before proceeding.
REM ═════════════════════════════════════════════════════════════════════════
echo ------------------------------------------------
echo  B-105 GATE -- AIM benchmark reassignment review
echo ------------------------------------------------
python -c "
import sqlite3, sys
conn = sqlite3.connect('.data/directors.db')
rows = conn.execute(
    \"SELECT ticker, sector FROM tickers_meta WHERE benchmark_symbol='^AIM' ORDER BY ticker\"
).fetchall()
conn.close()
if not rows:
    print('  No AIM tickers detected -- backfill_ticker_meta found no AIM exchange stocks.')
    print('  (This is unexpected. Check Yahoo quoteSummary is returning exchange data.)')
else:
    print(f'  {len(rows)} ticker(s) now benchmarked against ^AIM (FTSE AIM All-Share):')
    for t, s in rows:
        print(f'    {t:<8}  {s or \"(no sector)\"}')
print()
print('  CAR numbers for these tickers will shift after rebuild.')
print('  The new benchmark is more accurate for small-cap AIM stocks.')
"
echo.
echo  Press any key to continue with backfill_benchmarks (fetches ^AIM prices)
echo  or Ctrl+C to abort and investigate first.
echo.
pause

REM ═════════════════════════════════════════════════════════════════════════
REM  STEP 2 -- backfill_benchmarks.py
REM  Fetches price history for all benchmark symbols in tickers_meta,
REM  including the newly-assigned ^AIM. This is required before
REM  export_dashboard_json.py / backtest.py can compute AIM-benchmarked CAR.
REM ═════════════════════════════════════════════════════════════════════════
echo [step 2/5] Backfilling benchmark prices (^FTAS + ^AIM + any others)...
python .scripts\backfill_benchmarks.py --verbose
if errorlevel 1 (
    echo.
    echo ERROR: backfill_benchmarks.py failed (exit %ERRORLEVEL%).
    echo.
    pause
    exit /b 4
)
echo.
echo [step 2/5] Done.
echo.

REM ═════════════════════════════════════════════════════════════════════════
REM  STEP 3 -- backfill_reporting_dates.py  (B-096)
REM  Fetches upcoming earnings / results dates from Yahoo calendarEvents.
REM  ~784 tickers × 0.5s = ~7 minutes. Resume-safe.
REM  Note: Yahoo calendarEvents is sparse for UK small/mid-caps.
REM  Expect many tickers to return 0 dates -- that is normal.
REM ═════════════════════════════════════════════════════════════════════════
echo [step 3/5] Backfilling reporting dates from Yahoo calendarEvents...
echo            (upcoming results dates for 60-day badge -- ~7 min)
echo.
python .scripts\backfill_reporting_dates.py --verbose --resume
if errorlevel 1 (
    echo.
    echo ERROR: backfill_reporting_dates.py failed (exit %ERRORLEVEL%).
    echo.
    pause
    exit /b 5
)
echo.
echo [step 3/5] Done.
echo.

REM ═════════════════════════════════════════════════════════════════════════
REM  STEP 4 -- export_dashboard_json.py
REM  Rebuilds signals.json + dealings.json from DB + backtest CSV.
REM ═════════════════════════════════════════════════════════════════════════
echo [step 4/5] Exporting dashboard JSON...
python .scripts\export_dashboard_json.py
if errorlevel 1 (
    echo.
    echo ERROR: export_dashboard_json.py failed (exit %ERRORLEVEL%).
    echo.
    pause
    exit /b 6
)
echo [step 4/5] Done.
echo.

REM ═════════════════════════════════════════════════════════════════════════
REM  STEP 5 -- build_dashboard.py
REM  Renders all HTML pages including updated company pages with:
REM    - Website link (B-101)
REM    - Market cap chip (B-097)
REM    - Reporting date badges (B-096)
REM    - AIM benchmark in footers (B-105)
REM ═════════════════════════════════════════════════════════════════════════
echo [step 5/5] Building dashboard HTML...
python .scripts\build_dashboard.py
if errorlevel 1 (
    echo.
    echo ERROR: build_dashboard.py failed (exit %ERRORLEVEL%).
    echo.
    pause
    exit /b 7
)
echo [step 5/5] Done.
echo.

REM ── Summary ───────────────────────────────────────────────────────────────
echo ================================================
echo  Sprint 26 deploy complete.
echo ================================================
echo.
echo  What changed:
echo    B-105: AIM tickers now benchmarked against ^AIM (check CAR shift)
echo    B-097: Market cap + shares outstanding in tickers_meta
echo    B-101: Website links on company pages
echo    B-096: Reporting date badges on company pages (60-day window)
echo.
echo  Schema version advanced to 8.
echo.
echo  Next steps:
echo    1. Open the dashboard and spot-check a known AIM ticker (e.g. ARC,
echo       FEVR) -- their CAR columns should have shifted vs yesterday.
echo    2. Check a company page for a market-cap chip and website link.
echo    3. If any AIM CAR change looks wrong, check tickers_meta:
echo           python -c "import sqlite3; conn=sqlite3.connect('.data/directors.db'); print([dict(r) for r in conn.execute('SELECT ticker,is_aim,benchmark_symbol FROM tickers_meta WHERE is_aim=1').fetchall()])"
echo.
pause
