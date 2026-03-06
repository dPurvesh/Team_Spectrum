# EdgeVid LowBand - Complete Setup & Launch Script
# Run this ONCE to set up everything, then use it to start the system

param(
    [switch]$SetupOnly,
    [switch]$SkipSetup,
    [switch]$FrontendDev
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$BACKEND = "$ROOT\backend"
$FRONTEND = "$ROOT\frontend\edgevid-dashboard"

function Write-Banner {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "   EDGEVID LOWBAND - The Camera That Thinks" -ForegroundColor Cyan
    Write-Host "   HackArena'26 // Team Spectrum" -ForegroundColor DarkCyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Test-Command {
    param([string]$Command)
    try {
        Get-Command $Command -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Install-BackendDeps {
    Write-Host "[BACKEND] Setting up Python environment..." -ForegroundColor Yellow
    
    Set-Location $BACKEND
    
    # Find Windows Python (avoid MSYS2/MinGW Python)
    $script:WinPython = $null
    $PythonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($p in $PythonPaths) {
        if (Test-Path $p) {
            $script:WinPython = $p
            break
        }
    }
    if (-not $script:WinPython) {
        # Fallback to PATH but avoid msys64
        $Found = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*msys64*" } | Select-Object -First 1
        if ($Found) { $script:WinPython = $Found.Source }
    }
    if (-not $script:WinPython) {
        Write-Host "  ERROR: Could not find Windows Python. Install from python.org" -ForegroundColor Red
        exit 1
    }
    Write-Host "  -> Using Python: $script:WinPython" -ForegroundColor Gray
    
    # Check if system Python has required packages (skip venv if so)
    $hasPackages = & $script:WinPython -c "import uvicorn, fastapi, cv2, ultralytics; print('ok')" 2>$null
    if ($hasPackages -eq "ok") {
        Write-Host "  -> System Python has all packages" -ForegroundColor Green
    } else {
        # Install packages to system Python
        Write-Host "  -> Installing Python packages (this may take a few minutes)..." -ForegroundColor Gray
        $pipPath = Split-Path $script:WinPython
        $pipExe = Join-Path $pipPath "Scripts\pip.exe"
        if (-not (Test-Path $pipExe)) {
            $pipExe = Join-Path $pipPath "pip.exe"
        }
        if (Test-Path $pipExe) {
            & $pipExe install -r requirements.txt -q
        } else {
            & $script:WinPython -m pip install -r requirements.txt -q
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: Failed to install Python packages" -ForegroundColor Red
            exit 1
        }
        Write-Host "  -> Python packages installed!" -ForegroundColor Green
    }
    
    # Download YOLO model if not exists
    if (-not (Test-Path "yolov8n.pt")) {
        Write-Host "  -> Downloading YOLOv8-nano model..." -ForegroundColor Gray
        & $script:WinPython -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
    } else {
        Write-Host "  -> YOLOv8 model exists" -ForegroundColor Green
    }
    
    # Create storage directories
    $dirs = @("storage", "storage\events", "storage\idle", "storage\clips", "storage\prebuffer")
    foreach ($dir in $dirs) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
    Write-Host "  -> Storage directories ready" -ForegroundColor Green
    
    Set-Location $ROOT
}

function Install-FrontendDeps {
    Write-Host "[FRONTEND] Setting up React dashboard..." -ForegroundColor Yellow
    
    Set-Location $FRONTEND
    
    # Install npm packages
    if (-not (Test-Path "node_modules")) {
        Write-Host "  -> Installing npm packages..." -ForegroundColor Gray
        npm install --silent 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: npm install failed. Is Node.js installed?" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  -> Node modules exist" -ForegroundColor Green
    }
    
    # Build for production (unless using dev mode)
    if (-not $FrontendDev) {
        if (-not (Test-Path "build\index.html")) {
            Write-Host "  -> Building production bundle..." -ForegroundColor Gray
            npm run build 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  WARNING: Build failed, will use dev server instead" -ForegroundColor Yellow
                $script:FrontendDev = $true
            } else {
                Write-Host "  -> Production build complete!" -ForegroundColor Green
            }
        } else {
            Write-Host "  -> Production build exists" -ForegroundColor Green
        }
    }
    
    Set-Location $ROOT
}

function Start-Backend {
    Write-Host ""
    Write-Host "[STARTING] Backend server on port 8000..." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  EDGEVID BACKEND - FastAPI + YOLOv8 + SNN" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "API Docs: http://localhost:8000/docs" -ForegroundColor Green
    Write-Host "Dashboard: http://localhost:8000/app" -ForegroundColor Green
    Write-Host ""
    Write-Host "Press Ctrl+C to stop the server" -ForegroundColor DarkGray
    Write-Host ""
    
    Set-Location $BACKEND
    & $script:WinPython main.py
}

function Start-FrontendDev {
    Write-Host "[STARTING] Frontend dev server on port 3000..." -ForegroundColor Cyan
    
    Set-Location $FRONTEND
    npm start
}

# ============================================================
# MAIN EXECUTION
# ============================================================

Write-Banner

# Check prerequisites
Write-Host "[CHECK] Verifying system requirements..." -ForegroundColor Yellow

# Find Windows Python (avoid MSYS2/MinGW which hangs)
$WinPython = $null
$PythonPaths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
)
foreach ($p in $PythonPaths) {
    if (Test-Path $p) {
        $WinPython = $p
        break
    }
}
if (-not $WinPython) {
    $Found = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*msys64*" } | Select-Object -First 1
    if ($Found) { $WinPython = $Found.Source }
}

$nodeOk = Test-Command "node"
$npmOk = Test-Command "npm"

if ($WinPython) {
    $pyVer = (& $WinPython --version 2>&1) -replace "Python ", ""
    Write-Host "  -> Python $pyVer" -ForegroundColor Green
} else {
    Write-Host "  -> Python NOT FOUND (Windows Python required)" -ForegroundColor Red
    Write-Host "     Download from: https://www.python.org/downloads/" -ForegroundColor Gray
    exit 1
}

if ($nodeOk -and $npmOk) {
    $nodeVer = (node --version 2>&1)
    Write-Host "  -> Node.js $nodeVer" -ForegroundColor Green
} else {
    Write-Host "  -> Node.js NOT FOUND" -ForegroundColor Red
    Write-Host "     Download from: https://nodejs.org/" -ForegroundColor Gray
    exit 1
}

Write-Host ""

# Setup phase
if (-not $SkipSetup) {
    Install-BackendDeps
    Write-Host ""
    Install-FrontendDeps
    Write-Host ""
}

if ($SetupOnly) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "   SETUP COMPLETE! Run this script again to start." -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    exit 0
}

# Start servers
if ($FrontendDev) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "   STARTING IN DEV MODE" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "   Backend:  http://localhost:8000" -ForegroundColor White
    Write-Host "   Frontend: http://localhost:3000 (dev server)" -ForegroundColor White
    Write-Host "   API Docs: http://localhost:8000/docs" -ForegroundColor White
    Start-FrontendDev
} else {
    # Open browser after a delay
    Start-Job -ScriptBlock { Start-Sleep -Seconds 4; Start-Process "http://localhost:8000/app" } | Out-Null
    Start-Backend
}
