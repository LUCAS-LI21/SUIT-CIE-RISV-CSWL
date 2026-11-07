@echo off
chcp 65001 >nul
setlocal
rem ============================================================
rem  Set Windows pagefile (C:) to a fixed 16 GB
rem  Reason: current 8 GB pagefile caps the system commit limit,
rem  which makes hvigor/es2abc/java packer fail with
rem  "os::commit_memory failed / pagefile too small (errno 1455)"
rem  while building HarmonyOS HAP packages on this machine.
rem  REQUIRES ADMIN. Takes effect after reboot.
rem ============================================================

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   ADMIN REQUIRED
  echo   Right-click this file and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo Current setting:
powershell -NoProfile -Command "Get-CimInstance Win32_PageFileSetting | Select-Object Name,InitialSize,MaximumSize | Format-List"

echo.
echo Applying 16384 MB / 16384 MB ...
powershell -NoProfile -Command "$pf = Get-CimInstance Win32_PageFileSetting; Set-CimInstance -InputObject $pf -Property @{InitialSize=16384; MaximumSize=16384}; Get-CimInstance Win32_PageFileSetting | Select-Object Name,InitialSize,MaximumSize | Format-List"

if errorlevel 1 (
  echo.
  echo   FAILED. Nothing changed.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  Done. New size: 16384 MB.
echo  REBOOT REQUIRED for it to take effect.
echo  After reboot the commit limit becomes ~47 GB (31.7 RAM + 16).
echo ============================================================
echo.
pause
