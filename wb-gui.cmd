@echo off
rem wb-gui launcher (Windows) - double click to start
rem Keep this file ASCII-only: cmd.exe mangles UTF-8 text inside batch blocks.
setlocal
chcp 65001 >nul 2>nul
title wb-migrate GUI
set "HERE=%~dp0"
set "PYEXE="

python -c "import sys" >nul 2>nul
if %errorlevel%==0 set "PYEXE=python"
if defined PYEXE goto run

python3 -c "import sys" >nul 2>nul
if %errorlevel%==0 set "PYEXE=python3"
if defined PYEXE goto run

call :find_bundled
if defined PYEXE goto run

echo.
echo [wb-migrate] Python 3 was not found on this computer.
echo Install Python 3.8+ first, or run wb_gui.py with WorkBuddy's bundled Python:
echo   "%%USERPROFILE%%\.workbuddy\binaries\python\versions\*\python.exe" wb_gui.py
echo.
pause
exit /b 1

:find_bundled
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
exit /b 0

:run
"%PYEXE%" "%HERE%wb_gui.py" %*
echo.
echo [wb-migrate] Stopped. Press any key to close this window.
pause >nul
exit /b 0
