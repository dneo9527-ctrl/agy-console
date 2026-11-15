@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0runtime\python.exe" -u "%~dp0launcher.py" %*
set "AGY_EXIT=%ERRORLEVEL%"
if not "%AGY_EXIT%"=="0" pause
exit /b %AGY_EXIT%
