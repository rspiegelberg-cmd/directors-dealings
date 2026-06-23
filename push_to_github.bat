@echo off
title Directors Dealings - Backup + Push
REM ============================================================
REM  ONE double-click does everything:
REM    1. Backs up the database to OneDrive / Google Drive
REM    2. Saves all changes to git and pushes to GitHub
REM  The live website updates a minute or two after the push.
REM  Run this AFTER rebuilding the dashboard, when the pipeline
REM  is NOT mid-run.
REM ============================================================
cd /d C:\Dev\DirectorsDealings

REM ------------------------------------------------------------
REM  Stop Git's automatic garbage-collection. On Windows it pops
REM  up "Deletion of directory '.git/objects/..' failed. Should I
REM  try again?" and stalls this script. Turning it off (repo-only)
REM  makes the push run start-to-finish with no prompts. These are
REM  idempotent - safe to run every time.
REM ------------------------------------------------------------
git config gc.auto 0
git config gc.autoDetach false

echo ============================================================
echo  STEP 1 of 2 - Backing up the database to the cloud
echo ============================================================
call backup_db.bat nopause

echo.
echo ============================================================
echo  STEP 2 of 2 - Saving and pushing to GitHub
echo ============================================================

echo.
echo Staging changes...
git add -A

echo.
echo Committing...
git commit -m "Update dashboard %DATE% %TIME%"
if errorlevel 1 echo (Nothing new to commit - continuing.)

echo.
echo Syncing with any cloud updates (daily refresh) before pushing...
git pull --no-edit

echo.
echo Pushing to GitHub...
git push

echo.
echo ============================================================
echo  All done. The website will refresh in a minute or two.
echo  Check the Actions tab on GitHub for deploy status.
echo ============================================================
pause
