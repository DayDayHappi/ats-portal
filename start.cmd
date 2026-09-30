@echo off
setlocal DisableDelayedExpansion
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" goto not_installed
pushd "%~dp0"
if errorlevel 1 goto bad_directory
"%~dp0.venv\Scripts\python.exe" -m ats_portal.launcher start %*
set "ATS_EXIT=%ERRORLEVEL%"
popd
goto finished
:not_installed
echo Please run install.cmd first.
set "ATS_EXIT=1"
goto finished
:bad_directory
echo Cannot open the application directory. Extract the complete ZIP to a writable local folder.
set "ATS_EXIT=1"
:finished
if not "%ATS_EXIT%"=="0" echo Server stopped with an error. Read the message above; see README-Windows.md.
if not defined ATS_PORTAL_NO_PAUSE pause
exit /b %ATS_EXIT%
