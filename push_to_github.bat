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
set GIT_OPTIONAL_LOCKS=0
git config gc.auto 0
git config gc.autoDetach false
git config maintenance.auto false
git config fetch.writeCommitGraph false

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
echo Pushing to GitHub...
git push
REM Only sync if the push was rejected (remote moved on) - this avoids the
REM git-pull/fetch repack that triggers the ".git/objects" deletion prompt.
REM On the normal path (push succeeds) we never pull, so it stays clean.
if errorlevel 1 (
  echo.
  echo Remote has newer commits - syncing once, then pushing again...
  git pull --no-edit --no-rebase
  git push
)

echo.
echo ============================================================
echo  All done. The website will refresh in a minute or two.
echo  Check the Actions tab on GitHub for deploy status.
echo ============================================================
pause
