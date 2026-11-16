@echo off
setlocal
pushd "%~dp0"
echo Installing the Agy Console companion skill and enabling default collaboration for this Codex home.
"runtime\python.exe" -B install_codex_skill.py --enable-default
set "result=%errorlevel%"
popd
pause
exit /b %result%
