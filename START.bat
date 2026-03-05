@echo off
title EdgeVid LowBand — The Camera That Thinks
color 0B

echo.
echo  ============================================================
echo   EDGEVID LOWBAND — Hackarena'26 // Team Spectrum
echo   "The Camera That Thinks"
echo  ============================================================
echo.

:: Step 1 — Build React frontend if not already built
set BUILD_DIR=%~dp0frontend\edgevid-dashboard\build
set INDEX_HTML=%BUILD_DIR%\index.html

if not exist "%INDEX_HTML%" (
    echo  [1/2] Building React dashboard...
    cd /d "%~dp0frontend\edgevid-dashboard"
    call npm install --silent
    call npm run build
    if errorlevel 1 (
        echo.
        echo  ERROR: React build failed. Make sure Node.js is installed.
        pause
        exit /b 1
    )
    echo  [1/2] Build complete!
) else (
    echo  [1/2] React build found — skipping rebuild.
)

echo.
echo  [2/2] Starting backend server...
echo.
echo  ============================================================
echo   Dashboard will open at: http://localhost:8000/app
echo   API docs at:            http://localhost:8000/docs
echo  ============================================================
echo.

:: Step 2 — Start FastAPI backend in background, open browser after delay
cd /d "%~dp0backend"
start "" /b cmd /c "ping -n 5 localhost >nul && start http://localhost:8000/app"
call venv\Scripts\python.exe main.py

pause
