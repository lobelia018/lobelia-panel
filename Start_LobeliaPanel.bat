@echo off
setlocal
title LobeliaPanel
cd /d "%~dp0"
echo ==================================================
echo   LobeliaPanel  -  http://127.0.0.1:8788
echo ==================================================
echo.
rem --- Step 2: find Python and start the local engine ---
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"
if not defined PY (
echo [ERROR] Python 3 was not found. Install it from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" during the installation.
echo.
pause
exit /b 1
)
rem --- Optional checks: the gang and baw command line tools ---
where gang >nul 2>nul || echo [!] "gang" was not found. Install it with:  npm i -g @gangfun/cli@latest
where baw  >nul 2>nul || echo [!] "baw" was not found. Install it with:   npm i -g @binance/agentic-wallet
echo.
rem --- Step 3: the engine opens your browser at http://127.0.0.1:8788 by itself ---
echo Starting LobeliaPanel... your browser will open in a moment.
echo Close this window (or press Ctrl+C) to stop it.
echo.
%PY% server.py
echo.
echo LobeliaPanel stopped.
pause