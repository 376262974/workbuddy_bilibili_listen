@echo off
setlocal
cd /d "%~dp0"
REM Prefer the standalone .exe (no Python needed, no console flash, most reliable)
if exist "dist\bili_audio_player.exe" (
    echo [run] Launching standalone exe ...
    "dist\bili_audio_player.exe"
    goto :eof
)
REM Fallback: run with the local .venv (requires Python environment)
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment ...
    python -m venv .venv
    if errorlevel 1 (
        echo [error] Failed to create venv. Is Python installed and on PATH?
        pause
        exit /b 1
    )
    echo [setup] Installing dependencies (yt-dlp, numpy, pyaudio) ...
    ".venv\Scripts\python.exe" -m pip install -U pip
    ".venv\Scripts\python.exe" -m pip install yt-dlp numpy pyaudio
)
echo [run] Using python:
".venv\Scripts\python.exe" --version
echo [run] Starting BiliBili Audio Player ...
".venv\Scripts\python.exe" bili_audio_player.py > "run.log" 2>&1
set RC=%errorlevel%
echo. >> "run.log"
echo [exit code] %RC% >> "run.log"
if %RC% neq 0 (
    echo.
    echo ===================================================
    echo  The program exited with an error (code %RC%).
    echo  Showing run.log:
    echo ===================================================
    type "run.log"
    echo.
    echo  (also check error.log in this folder for details)
    echo.
) else (
    echo [done] Program closed normally.
)
echo.
echo Press any key to close this window ...
pause >nul
endlocal
