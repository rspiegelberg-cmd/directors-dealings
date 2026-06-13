@echo off
cd /d "%~dp0"

echo ========================================
echo  Directors Dealings - Stage 5 Dashboard
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    pause
    exit /b 1
)

echo Serving:  outputs/
echo URL:      http://localhost:5001
echo.
echo NOTE: This is the simple static server. The Refresh button will NOT
echo work in this mode. Use start.bat for the full experience.
echo.
echo Leave this window open while using the dashboard.
echo Press Ctrl+C to stop.
echo.

REM Open browser after 1.5s delay (runs in background while server starts)
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5001"

python -m http.server 5001 --directory outputs

pause
