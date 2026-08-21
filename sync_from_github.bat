@echo off
title Directors Dealings - Sync this PC FROM GitHub
REM ============================================================
REM  RUN THIS FIRST, BEFORE push_to_github.bat.
REM
REM  What it does: makes this PC's copy of the code match GitHub
REM  exactly. The daily refresh robot commits to GitHub every
REM  morning, so this PC drifts behind - on 2026-08-21 it was 35
REM  commits (5 weeks) behind. Pushing from a behind copy is what
REM  makes deploys dangerous, so sync first, always.
REM
REM  What it does NOT touch: your .env file, the .data folder,
REM  caches, or anything else Git does not track. Those are left
REM  exactly as they are.
REM
REM  Safety net: before changing anything it parks your current
REM  position on a branch called "backup-local-before-sync" and
REM  stashes any uncommitted edits, so nothing is ever gone for
REM  good.
REM ============================================================
cd /d C:\Dev\DirectorsDealings

set GIT_OPTIONAL_LOCKS=0
git config gc.auto 0
git config gc.autoDetach false

if exist ".git\index.lock" del /f ".git\index.lock"
if exist ".git\HEAD.lock"  del /f ".git\HEAD.lock"

echo ============================================================
echo  Syncing this PC from GitHub
echo ============================================================

echo.
echo Fetching from GitHub...
git fetch origin main
if errorlevel 1 goto :fetch_failed

echo.
echo Parking a safety bookmark (branch: backup-local-before-sync)...
git branch -f backup-local-before-sync HEAD

echo.
echo Stashing any uncommitted edits (recover later with: git stash list)...
git stash push -m "pre-sync backup %DATE% %TIME%"

echo.
echo Matching this PC to GitHub...
git reset --hard origin/main
if errorlevel 1 goto :reset_failed

echo.
echo ============================================================
echo  Done. This PC now matches GitHub exactly.
echo  It is now safe to run push_to_github.bat.
echo ============================================================
git log -1 --oneline
pause
exit /b 0

:fetch_failed
echo.
echo ############################################################
echo  STOPPED: could not reach GitHub. Nothing was changed.
echo  Check your internet connection and try again.
echo ############################################################
pause
exit /b 1

:reset_failed
echo.
echo ############################################################
echo  STOPPED: the sync did not complete. Nothing was lost - your
echo  previous position is on the branch
REM  (literal branch name below, kept out of the echo for clarity)
echo  backup-local-before-sync
echo  Ask Claude before doing anything else.
echo ############################################################
pause
exit /b 1
