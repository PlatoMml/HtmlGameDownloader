@echo off
rem ============================================================
rem  HtmlGameDownloader launcher
rem
rem  IMPORTANT: This file must stay ASCII-only.
rem  cmd.exe parses .bat bytes using the OEM codepage (GBK on zh-CN
rem  Windows), while `chcp 65001` only changes the console output
rem  codepage. Mixing UTF-8 Chinese text with GBK parsing corrupts
rem  the script and makes it exit instantly when double-clicked.
rem  All Chinese messaging is therefore delegated to Python.
rem ============================================================
setlocal
title Html Game Downloader
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto :no_python

python -c "import PySide6" >nul 2>nul
if errorlevel 1 goto :install_deps

goto :run


:install_deps
echo [setup] Installing dependencies, please wait...
python -m pip install -r requirements.txt
if errorlevel 1 goto :deps_failed


:run
python -m hgd %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" goto :run_failed
exit /b 0


:no_python
echo.
echo [ERROR] Python not found in PATH.
echo         Install Python 3.9+ from https://www.python.org/downloads/
echo         and make sure "Add python.exe to PATH" is checked.
echo.
pause
exit /b 1


:deps_failed
echo.
echo [ERROR] Failed to install dependencies.
echo         Run this manually to see details:
echo             python -m pip install -r requirements.txt
echo.
pause
exit /b 1


:run_failed
echo.
echo [ERROR] Program exited with code %RC%.
echo.
pause
exit /b %RC%
