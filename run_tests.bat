@echo off
REM B-003 -- Windows test runner. Runs all `test_*.py` in .scripts via
REM unittest discover. Exits non-zero on the first failing test so this
REM batch file can be used as a CI gate.
REM
REM Usage (from project root):
REM     run_tests.bat
REM
REM Or run an individual test file directly:
REM     python -m unittest .scripts.test_parser

cd /d "%~dp0"

echo ==========================================
echo  Directors Dealings -- running test suite
echo ==========================================
echo.

python -m unittest discover -s .scripts -p "test_*.py" -v
set RC=%ERRORLEVEL%

echo.
if %RC% NEQ 0 (
    echo TESTS FAILED ^(exit %RC%^)
    exit /b %RC%
)
echo All tests passed.
exit /b 0
