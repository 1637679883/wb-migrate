@echo off
rem wb-migrate launcher (Windows)
rem Keep this file ASCII-only: cmd.exe mangles UTF-8 text inside batch blocks.
setlocal
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
echo [wb-migrate] Python 3 is not available.
echo Install Python 3.8+ or run the script with WorkBuddy's bundled Python:
echo   "%%USERPROFILE%%\.workbuddy\binaries\python\versions\*\python.exe" wb_migrate.py list
echo.
exit /b 1

:find_bundled
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
)
exit /b 0

:run
"%PYEXE%" "%HERE%wb_migrate.py" %*
exit /b %errorlevel%
