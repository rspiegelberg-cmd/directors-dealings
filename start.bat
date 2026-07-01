@echo off
cd /d "%~dp0"

echo ========================================
echo  Directors Dealings Dashboard
echo ========================================
echo.
echo  NOTE: the LIVE dashboard now runs in the cloud and is the
echo  primary surface:
echo.
echo      https://directors-dealings.vercel.app/
echo.
echo  It reads Supabase directly and is always current, with the
echo  PC off. This local server is now an OPTIONAL legacy preview
echo  and may not match the live site. See
echo  docs\specs\HOW-IT-RUNS-NOW.md.
echo.

REM Sanity-check Python is installed.
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python is not installed or not on PATH.
    echo.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM ── Local DB health checks REMOVED (cloud migration M5) ────────────────────
REM The old db_health restore / auto-seal / fail-stale gating operated on the
REM local .data\directors.db, which is now an ARCHIVED cold backup, not live
REM data. Those checks would hard-fail launch on a deliberately-stale archive,
REM so they have been removed. The data of record is Supabase Postgres.

REM ── Clear a stale/corrupted refresh status file (harmless if absent) ───────
if exist ".data\_refresh_status.json" (
    python -c "import json; json.load(open('.data/_refresh_status.json'))" >nul 2>nul
    if errorlevel 1 (
        del /f /q ".data\_refresh_status.json" >nul 2>nul
        echo Cleared stale refresh status file.
    )
)

REM ── Warn if a locally-built dashboard HTML is not present ───────────────────
if not exist "outputs\index.html" (
    echo NOTE: outputs\index.html is not present locally. The live site builds
    echo this in the cloud, so a local copy may not exist. Use the live URL above.
    echo.
)

echo Starting local preview server at http://localhost:5000 ...
echo Your browser will open in a moment.
echo.
echo *** Leave this window open while using the local preview. ***
echo *** Close it (or press Ctrl+C) when you are done.          ***
echo.

python server.py

echo.
echo Server stopped. Press any key to close this window.
pause >nul
