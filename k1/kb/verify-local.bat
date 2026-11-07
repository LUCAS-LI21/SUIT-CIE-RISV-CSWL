@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  K1 local-compute proof
echo  Proves the answer comes from the K1 board, not this PC
echo ============================================================

where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] node.exe not found in PATH.
  echo         Install Node.js, or read verify-local.js manually.
  pause
  exit /b 1
)

node verify-local.js
if errorlevel 1 (
  echo.
  echo [ERROR] Verification failed. Most likely the adb tunnel is down.
  echo         Run start-chat.bat first, then try again.
)

echo.
pause
