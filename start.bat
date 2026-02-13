@echo off
:: start.bat -- One-click launcher for Inbox Sentinel (Windows)
:: Creates venv + installs deps on first run, then starts the GUI.
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set VENV_DIR=.venv
set REQ_FILE=requirements.txt
if "%PORT%"=="" set PORT=8741
if "%OLLAMA_URL%"=="" set OLLAMA_URL=http://localhost:11434

:: ── 1. Python ──────────────────────────────────────────────
set PY=
where python3 >nul 2>&1 && (set PY=python3& goto :found_py)
where python  >nul 2>&1 && (set PY=python&  goto :found_py)
echo ERROR: Python 3 not found.
echo Please install Python 3.10+ from https://www.python.org/downloads/
echo Make sure to check "Add Python to PATH" during installation.
pause
exit /b 1

:found_py
:: Verify minimum version 3.10
for /f %%v in ('!PY! -c "import sys; print(int(sys.version_info >= (3, 10)))" 2^>nul') do set PY_OK=%%v
if not "!PY_OK!"=="1" (
    echo ERROR: Python 3.10+ required.
    !PY! --version
    pause
    exit /b 1
)

:: ── 2. Virtual environment ─────────────────────────────────
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo First run -- setting up environment...
    echo Creating virtual environment...
    !PY! -m venv %VENV_DIR%
    if errorlevel 1 (
        echo ERROR: Could not create virtual environment.
        pause
        exit /b 1
    )
)
set VPYTHON=%VENV_DIR%\Scripts\python.exe

:: ── 3. Dependencies ────────────────────────────────────────
:: Reinstall when stamp is missing or requirements.txt changed
set STAMP=%VENV_DIR%\.deps_installed
set NEED_INSTALL=0
if not exist "%STAMP%" set NEED_INSTALL=1

:: Compare timestamps: if requirements.txt is newer than stamp, reinstall
if exist "%STAMP%" (
    for %%R in (%REQ_FILE%) do for %%S in (%STAMP%) do (
        if "%%~tR" gtr "%%~tS" set NEED_INSTALL=1
    )
)

if "!NEED_INSTALL!"=="1" (
    echo Installing Python dependencies...
    "%VPYTHON%" -m pip install --upgrade pip wheel -q
    "%VPYTHON%" -m pip install -r %REQ_FILE% -q
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        exit /b 1
    )
    echo.> "%STAMP%"
)

:: ── 4. Frontend build ──────────────────────────────────────
if not exist "gui\frontend\dist" (
    where npm >nul 2>&1
    if not errorlevel 1 (
        echo Building frontend ^(first run^)...
        pushd gui\frontend
        call npm install --silent
        call npm run build
        popd
    ) else (
        echo NOTE: npm not found -- running in API-only mode.
        echo       Install Node.js 18+ for the full GUI.
    )
)

:: ── 5. Ollama check ────────────────────────────────────────
curl -sf --max-time 3 "%OLLAMA_URL%/api/tags" >nul 2>&1
if not errorlevel 1 (
    echo Ollama OK ^(%OLLAMA_URL%^)
) else (
    echo NOTE: Ollama not reachable at %OLLAMA_URL%
    echo       Start Ollama for LLM features to work.
)

:: ── 6. Launch ──────────────────────────────────────────────
echo.
echo Starting Inbox Sentinel on http://127.0.0.1:%PORT%
echo Press Ctrl+C to stop.
echo.

:: Open browser after a short delay (best-effort)
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:%PORT%"

"%VPYTHON%" -m uvicorn gui.server:app --host 127.0.0.1 --port %PORT%
pause
