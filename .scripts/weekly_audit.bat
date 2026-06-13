@echo off
REM Phase 11.5 -- Weekly data integrity audit
REM Add to Windows Task Scheduler: run every Monday at 08:00
REM Action: Start a program -> python
REM Arguments: .scripts\weekly_audit.bat
REM Start in: C:\Dev\DirectorsDealings

cd /d C:\Dev\DirectorsDealings

set LOGFILE=.data\_weekly_audit.log
echo. >> %LOGFILE%
echo ========================================= >> %LOGFILE%
echo Weekly audit -- %date% %time% >> %LOGFILE%
echo ========================================= >> %LOGFILE%

python .scripts\phase11_integrity_check.py >> %LOGFILE% 2>&1

REM Check if any FAIL or REVIEW lines appeared
findstr /i "FAIL REVIEW" %LOGFILE% | findstr "%date%" > nul
if %ERRORLEVEL% EQU 0 (
    echo ALERT: Integrity check found issues on %date% -- see .data\_weekly_audit.log
) else (
    echo Audit clean -- %date%
)
