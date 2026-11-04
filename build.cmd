@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   重新打包 wb-migrate.exe
echo.

set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" (
  if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set PY="%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  )
)
if "%PY%"=="" (
  echo   找不到 Python，请先安装 Python 3.10 以上版本。
  pause
  exit /b 1
)

%PY% -c "import PyInstaller, webview" 2>nul
if errorlevel 1 (
  echo   缺少打包工具，正在安装 PyInstaller 与 pywebview ...
  %PY% -m pip install pyinstaller pywebview || goto :fail
)

%PY% make_icon.py || goto :fail

%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name wb-migrate --icon app.ico ^
  --add-data "ui.html;." ^
  --collect-all webview --collect-all pythonnet --collect-all clr_loader ^
  --hidden-import webview.platforms.edgechromium ^
  --exclude-module tkinter --exclude-module unittest --exclude-module pydoc ^
  wb_app.py || goto :fail

echo.
echo   完成： dist\wb-migrate.exe
echo.
pause
exit /b 0

:fail
echo.
echo   打包失败，看上面的报错。
pause
exit /b 1
