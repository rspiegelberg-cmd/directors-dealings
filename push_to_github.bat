@echo off
REM ============================================================
REM  Save all local changes and push them to GitHub.
REM  Run this AFTER you have rebuilt the dashboard locally.
REM  The live web page updates a minute or two after the push.
REM ============================================================
cd /d C:\Dev\DirectorsDealings

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

echo.
echo Done. Check the Actions tab on GitHub for the deploy status.
pause
