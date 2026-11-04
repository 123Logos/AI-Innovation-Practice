@echo off
REM =====================================================================
REM  start.bat - double-click entry point for start-local.ps1
REM
REM  Why this file exists:
REM    Windows PowerShell blocks unsigned .ps1 files unless the execution
REM    policy allows them. The switch below sets the policy for THIS
REM    powershell.exe PROCESS ONLY. It does not touch the registry, does
REM    not change the machine-wide (LocalMachine) or user (CurrentUser)
REM    policy, and it stops existing the moment this window closes.
REM    It also cannot override a Group Policy setting, so nothing here
REM    weakens a managed machine.
REM
REM  Nothing in this script requests administrator rights, and the
REM  service it starts does not need them.
REM =====================================================================

cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local.ps1"

echo.
echo [start.bat] PowerShell finished with exit code %ERRORLEVEL%.
echo [start.bat] Backend and frontend keep running in their own two windows.
echo [start.bat] Close those two windows to stop everything.
pause
