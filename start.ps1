# EdgeVid LowBand — One-command launcher
# Starts backend (FastAPI) and frontend (React) in separate windows

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$BACKEND = "$ROOT\backend"
$FRONTEND = "$ROOT\frontend\edgevid-dashboard"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  EdgeVid LowBand — The Camera That Thinks" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[1/2] Starting Backend  -> http://localhost:8000" -ForegroundColor Green
Write-Host "[2/2] Starting Frontend -> http://localhost:3000" -ForegroundColor Green
Write-Host ""

# Start backend in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$BACKEND';
    Write-Host '🧠 EdgeVid Backend Starting...' -ForegroundColor Cyan;
    .\venv\Scripts\python.exe main.py
"

# Small delay so backend has a head start
Start-Sleep -Seconds 2

# Start frontend in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$FRONTEND';
    Write-Host '🖥️  EdgeVid Dashboard Starting...' -ForegroundColor Green;
    npm start
"

Write-Host "Both servers launched in separate windows." -ForegroundColor Yellow
Write-Host "Backend : http://localhost:8000" -ForegroundColor White
Write-Host "Frontend: http://localhost:3000" -ForegroundColor White
Write-Host "API Docs: http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
