@echo off
title Directors Dealings - Database Backup (RETIRED)
REM ============================================================
REM  RETIRED 2026-06 (cloud migration M5).
REM
REM  This script used to copy the local SQLite database
REM  (.data\directors.db) to OneDrive / Google Drive. That local
REM  database is no longer the live data -- the data now lives in
REM  Supabase Postgres, which is its own backup, and every daily
REM  refresh is also saved in the GitHub history.
REM
REM  There is nothing to back up here anymore. This file is kept
REM  only so anything that still calls it exits cleanly.
REM
REM  See docs/specs/HOW-IT-RUNS-NOW.md for the current setup.
REM ============================================================

echo.
echo  backup_db.bat is RETIRED.
echo.
echo  The database now lives in Supabase (its own backup), and every
echo  daily refresh is saved in the GitHub history. Nothing local to
echo  back up. See docs\specs\HOW-IT-RUNS-NOW.md.
echo.
if /i not "%~1"=="nopause" pause
exit /b 0
