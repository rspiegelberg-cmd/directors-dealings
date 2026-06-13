@echo off
cd /d "%~dp0"

echo ========================================
echo  Directors Dealings Dashboard
echo ========================================
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

REM ── DB + CSV health check + auto-restore ──────────────────────────────────
REM db_health.py checks integrity and restores from backup if corrupted.
python .scripts\db_health.py restore >nul 2>nul
python .scripts\db_health.py check >nul 2>nul
if errorlevel 1 (
    echo.
    echo WARNING: directors.db is missing or corrupted and no backup exists.
    echo The dashboard will start but show no data.
    echo Click the Refresh button once it opens to rebuild from scratch.
    echo.
)

REM ── B-024: stale-backup defence ────────────────────────────────────────────
REM Ad-hoc scripts (Sprint scripts, sweeps, repair tools) write to the DB
REM but historically never refreshed .data/directors.db.bak. Before today,
REM the bak only refreshed when refresh_all.py finished cleanly. Net result:
REM if the user accumulated days of Sprint work between refreshes, the only
REM available backup was pre-Sprint -- a real bug surfaced 2026-05-18.
REM
REM We do TWO things at startup now:
REM   1. auto-seal: if .bak is >24h old AND the live DB passes integrity_check,
REM      take a fresh backup so the bak always tracks recent Sprint work.
REM   2. warn-stale: if .bak is still >24h old after auto-seal (e.g. live DB
REM      was sick so auto-seal refused), print a visible warning so Rupert
REM      can investigate before clicking through to the dashboard.
python .scripts\db_health.py auto-seal 24
python .scripts\db_health.py warn-stale 24

REM ── B-024 Sprint 4: hard-fail when .bak is dangerously stale ───────────────
REM warn-stale 24 above is informational only — it doesn't block. After
REM Sprint 4 added the C-3 backup pattern to every Zone B script, the .bak
REM should refresh on every successful pipeline. If it hasn't been refreshed
REM in 48 hours something is wrong — either nothing has run (fine, but the
REM user should know), or the pattern is being silently skipped (not fine).
REM Either way: refuse to launch the dashboard until the user takes action.
REM The fail-stale subcommand prints the exact recovery command in its banner.
python .scripts\db_health.py fail-stale 48
if errorlevel 1 (
    echo.
    pause
    exit /b 3
)

REM ── Clear a stale/corrupted refresh status file ────────────────────────────
if exist ".data\_refresh_status.json" (
    python -c "import json; json.load(open('.data/_refresh_status.json'))" >nul 2>nul
    if errorlevel 1 (
        del /f /q ".data\_refresh_status.json" >nul 2>nul
        echo Cleared stale refresh status file.
    )
)

REM ── Sprint 10 Phase 4: clear a stale-but-valid "running" status ───────────
REM Threshold: 45 minutes per Gate 1 decision. The malformed-JSON check
REM above doesn't catch the case where _refresh_status.json is VALID JSON
REM but shows status="running" from a long-dead pipeline (e.g. the
REM 2026-05-22 incident where refresh_all.py died mid-step and the
REM dashboard spinner hung indefinitely on every subsequent launch).
REM Detection: status == "running" AND age of updated_at >= 2700s (45min).
REM Action: delete _refresh_status.json + its .tmp orphan, echo audit line.
if exist ".data\_refresh_status.json" (
    python -c "import json,datetime as dt,pathlib,sys;p=pathlib.Path('.data/_refresh_status.json');s=json.loads(p.read_text());ts=s.get('updated_at') or s.get('started_at');st=s.get('status');now=dt.datetime.now(dt.timezone.utc);age=(now-dt.datetime.fromisoformat(ts.replace('Z','+00:00'))).total_seconds() if ts else 0;sys.exit(7 if st=='running' and age>=2700 else 0)" >nul 2>nul
    if errorlevel 7 (
        del /f /q ".data\_refresh_status.json" >nul 2>nul
        del /f /q ".data\_refresh_status.json.tmp" >nul 2>nul
        echo Cleared stale "running" status file ^(age ^>= 45 min^).
    )
)

REM ── Warn if dashboard HTML not built yet ───────────────────────────────────
if not exist "outputs\index.html" (
    echo WARNING: outputs\index.html does not exist yet.
    echo The server will start but there is nothing to show.
    echo Click the Refresh button on the dashboard once it loads.
    echo.
)

echo Starting server at http://localhost:5000 ...
echo Your browser will open in a moment.
echo.
echo *** Leave this window open while using the dashboard. ***
echo *** Close it (or press Ctrl+C) when you are done.      ***
echo.

python server.py

echo.
echo Server stopped. Press any key to close this window.
pause >nul
