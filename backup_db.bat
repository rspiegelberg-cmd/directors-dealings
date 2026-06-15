@echo off
setlocal enabledelayedexpansion
title Directors Dealings - Database Backup
REM ============================================================
REM  Copies the database to OneDrive AND Google Drive (whichever
REM  are set up on this PC). Keeps the 14 most recent copies in
REM  each. Run this when the pipeline is NOT mid-run.
REM ============================================================

set "SRC=C:\Dev\DirectorsDealings\.data\directors.db"
if not exist "%SRC%" (
  echo ERROR: Database not found at %SRC%
  pause
  exit /b 1
)

REM --- Timestamp like 2026-06-13_0915 ---
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set "TS=%%a"
set "NAME=directors_%TS%.db"

set "ANYDONE=0"

REM ---------- OneDrive ----------
if defined OneDrive (
  call :backup "%OneDrive%\DirectorsDealings-Backups" OneDrive
) else (
  echo [skip] OneDrive not detected on this PC.
)

REM ---------- Google Drive ----------
set "GDRIVE="
if exist "G:\My Drive" set "GDRIVE=G:\My Drive"
if exist "%USERPROFILE%\Google Drive" set "GDRIVE=%USERPROFILE%\Google Drive"
if defined GDRIVE (
  call :backup "!GDRIVE!\DirectorsDealings-Backups" "Google Drive"
) else (
  echo [skip] Google Drive folder not found ^(looked for G:\My Drive and your user folder^).
)

echo.
if "%ANYDONE%"=="0" (
  echo No cloud folders were found. Make sure OneDrive or Google Drive
  echo desktop sync is set up, then run this again.
) else (
  echo Backup complete.
)
echo.
if /i not "%~1"=="nopause" pause
exit /b 0

REM ============================================================
:backup
REM  %~1 = destination folder, %~2 = label
set "DEST=%~1"
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%SRC%" "%DEST%\%NAME%" >nul
if errorlevel 1 (
  echo [FAIL] %~2 - could not copy ^(is the file locked by a running pipeline?^)
  goto :eof
)
echo [OK]   %~2: %DEST%\%NAME%
set "ANYDONE=1"
REM --- prune: keep the 14 newest, delete the rest ---
for /f "skip=14 delims=" %%f in ('dir /b /o-d "%DEST%\directors_*.db" 2^>nul') do del "%DEST%\%%f"
goto :eof
