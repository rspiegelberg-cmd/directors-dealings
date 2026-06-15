@echo off
REM ============================================================================
REM  confirm_director_pay.bat  (B-168)
REM  Confirms collected executive pay from .data\_pay_manual.csv into the
REM  director_pay table. Two-stage with an EYEBALL pause: preview first,
REM  review it, then apply.
REM
REM  This only writes the director_pay table. It does NOT re-run the backtest
REM  or rebuild the dashboard -- do that separately (deploy_director_pay.bat or
REM  the manual sequence) once you've collected a worthwhile batch.
REM ============================================================================
cd /d C:\Dev\DirectorsDealings

echo.
echo ============================================================
echo  STEP 1 of 2 -- PREVIEW (no changes are written to the DB)
echo ============================================================
python .scripts\backfill_director_pay.py --from-manual
if errorlevel 1 goto :error

echo.
echo ============================================================
echo  REVIEW the preview above before applying.
echo  Sanity checks:
echo    - any row where a CFO total exceeds its CEO at the same
echo      company is suspicious - abort and fix it first;
echo    - figures should look right for the company size.
echo.
echo  Press Ctrl+C now to ABORT, or any other key to APPLY.
echo ============================================================
pause

echo.
echo ============================================================
echo  STEP 2 of 2 -- APPLYING to the database
echo ============================================================
python .scripts\backfill_director_pay.py --from-manual --confirm
if errorlevel 1 goto :error
python .scripts\snapshot_db.py
if errorlevel 1 goto :error

echo.
echo Done. director_pay updated and snapshots refreshed.
echo (To make the salary-multiple show up in the backtest, run the
echo  backtest/eval/export/build deploy sequence separately.)
pause
goto :eof

:error
echo.
echo *** A step failed (see the output above). Nothing further applied. ***
pause
