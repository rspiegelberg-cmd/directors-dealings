@echo off
REM ============================================================
REM  Directors Dealings - daily forward-calendar refresh
REM  Scrapes the LSE financial diary for the next 3 months and
REM  rebuilds the dashboard so pre-results badges stay current.
REM
REM  Zone B (writes the DB) -> runs on WINDOWS Python only.
REM  Registered via Windows Task Scheduler, NOT the Cowork
REM  scheduler, because the DB write must not go through the
REM  Linux/FUSE mount.
REM
REM  Logging: every run writes a dated log to
REM    .data\_diary_logs\diary_YYYYMMDD_HHMMSS.log  (stdout + stderr).
REM  Inspect this log when Task Scheduler shows a non-zero exit code.
REM  Log files older than 30 days are pruned automatically.
REM ============================================================
cd /d "%~dp0"

REM ── Log file setup ──────────────────────────────────────────
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set RUNTS=%%i
if not exist ".data\_diary_logs" mkdir ".data\_diary_logs"
set LOGFILE=.data\_diary_logs\diary_%RUNTS%.log
echo [diary] Run started %date% %time% >> "%LOGFILE%"

REM ── Prune logs older than 30 days ───────────────────────────
forfiles /p ".data\_diary_logs" /m "diary_*.log" /d -30 /c "cmd /c del @path" >nul 2>nul

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found on PATH. >> "%LOGFILE%"
    echo ERROR: Python not found on PATH.
    exit /b 1
)

echo [1/6] DB health restore/check...
echo [1/6] DB health restore/check >> "%LOGFILE%"
python .scripts\db_health.py restore >> "%LOGFILE%" 2>&1

echo [2/6] Scraping LSE financial diary (next 3 months)...
echo [2/6] Scraping LSE financial diary >> "%LOGFILE%"
python .scripts\backfill_lse_diary.py --months 3 >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Diary scrape FAILED - aborting. >> "%LOGFILE%"
    echo Diary scrape FAILED - aborting.
    exit /b 1
)

echo [3/6] Projecting estimated future earnings dates...
echo [3/6] Projecting estimated future earnings dates >> "%LOGFILE%"
python .scripts\backfill_expected_reporting_dates.py >> "%LOGFILE%" 2>&1

echo [4/6] Exporting dashboard JSON...
echo [4/6] Exporting dashboard JSON >> "%LOGFILE%"
python .scripts\export_dashboard_json.py >> "%LOGFILE%" 2>&1

echo [5/6] Rebuilding dashboard...
echo [5/6] Rebuilding dashboard >> "%LOGFILE%"
python .scripts\build_dashboard.py >> "%LOGFILE%" 2>&1

echo [6/6] Snapshotting DB (read-only, text)...
echo [6/6] Snapshotting DB >> "%LOGFILE%"
python .scripts\snapshot_db.py >> "%LOGFILE%" 2>&1

echo [diary] Done %date% %time% >> "%LOGFILE%"
echo.
echo Done %date% %time%
echo [diary] Full log: %LOGFILE%
