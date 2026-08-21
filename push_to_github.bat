@echo off
title Directors Dealings - Push to GitHub (deploy)
REM ============================================================
REM  ONE double-click deploys your code changes:
REM    saves all changes to git and pushes to GitHub.
REM  The live website updates a minute or two after the push.
REM
REM  NOTE (cloud migration, 2026-06): the old "back up the
REM  database" step was REMOVED. The data now lives in Supabase,
REM  which is its own backup, and every daily refresh is saved in
REM  the GitHub history. There is nothing local to back up.
REM  See docs/specs/HOW-IT-RUNS-NOW.md.
REM
REM  SAFETY CHANGE 2026-08-21 (B-203) -- READ THIS:
REM  This script used to end with "git push --force origin main".
REM  That was safe when this PC was the only thing writing to the
REM  repo. It has NOT been safe since the daily refresh moved into
REM  GitHub Actions (2026-06-25): the robot commits rebuilt pages
REM  every morning, so this PC is normally DAYS OR WEEKS BEHIND
REM  GitHub. A force push from here would have thrown all of those
REM  commits away. On 2026-08-21 this PC was 35 commits behind.
REM  It now SYNCS FIRST and pushes normally. If it ever cannot
REM  sync cleanly it stops and tells you, instead of destroying
REM  work.
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
echo  Saving and pushing to GitHub
echo ============================================================

REM Clear any stale git lock files left by interrupted operations
if exist ".git\index.lock" del /f ".git\index.lock"
if exist ".git\HEAD.lock"  del /f ".git\HEAD.lock"
if exist ".git\MERGE_HEAD" git merge --abort 2>nul

echo.
echo Staging changes...
git add -A

echo.
echo Committing...
git commit -m "Update %DATE% %TIME%"
if errorlevel 1 echo (Nothing new to commit - continuing.)

echo.
echo Syncing with GitHub before pushing...
git fetch origin main
if errorlevel 1 goto :sync_failed

REM Replay this PC's commits on top of whatever the daily robot has
REM pushed since. -X theirs keeps THIS PC's version of any file that
REM conflicts (during a rebase "theirs" means the commits being
REM replayed, i.e. yours), which preserves the old force-push intent
REM WITHOUT deleting the robot's history.
git rebase -X theirs origin/main
if errorlevel 1 goto :rebase_failed

echo.
echo Pushing to GitHub...
git push origin main
if errorlevel 1 goto :push_failed

echo.
echo ============================================================
echo  All done. The website will refresh in a minute or two.
echo  Check the Actions tab on GitHub for deploy status.
echo ============================================================
pause
exit /b 0

:sync_failed
echo.
echo ############################################################
echo  STOPPED: could not reach GitHub to sync.
echo  Nothing was pushed. Check your internet connection and
echo  run this again.
echo ############################################################
pause
exit /b 1

:rebase_failed
echo.
echo ############################################################
echo  STOPPED: your changes clash with what is already on GitHub
echo  and could not be merged automatically.
echo  Nothing was pushed and nothing was lost.
echo  Undoing the half-done merge now...
echo ############################################################
git rebase --abort
echo  Ask Claude to sort out the conflict before pushing again.
pause
exit /b 1

:push_failed
echo.
echo ############################################################
echo  STOPPED: the push was rejected. Someone (probably the daily
echo  robot) pushed while this was running. Nothing was lost -
echo  just run this script again.
echo ############################################################
pause
exit /b 1
