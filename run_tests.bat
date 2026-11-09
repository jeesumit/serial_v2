@echo off
REM Runs the complete automated test suite on this PC (Python + browser-UI tests). Needs Python 3.11+; Node 18+ for the UI tests.
setlocal
cd /d %~dp0
if not exist .venv-test python -m venv .venv-test || goto :fail
call .venv-test\Scripts\activate.bat
python -m pip install -q -r requirements-dev.txt || goto :fail
python -m pytest tests -q -p no:cacheprovider || goto :fail
where node >nul 2>nul
if errorlevel 1 ( echo Node.js not found - browser UI tests skipped ) else (
  pushd tests\js
  call npm ci --no-audit --no-fund || ( popd & goto :fail )
  node --test || ( popd & goto :fail )
  popd
)
echo.& echo ALL TESTS PASSED
exit /b 0
:fail
echo.& echo TESTS FAILED
exit /b 1
