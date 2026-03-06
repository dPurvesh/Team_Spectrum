@echo off
title EdgeVid LowBand - The Camera That Thinks
color 0B

echo.
echo  ============================================================
echo   EDGEVID LOWBAND - HackArena'26 // Team Spectrum
echo   "The Camera That Thinks"
echo  ============================================================
echo.

:: Run the PowerShell setup and start script (skip profile to avoid conda)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"

:: No pause needed - servers run in separate windows
