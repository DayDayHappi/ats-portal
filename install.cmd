@echo off
setlocal DisableDelayedExpansion
set "PYTHONUTF8=1"
if defined ATS_PORTAL_PYTHON goto custom_python
where py >nul 2>nul
if errorlevel 1 goto python_on_path
py -3 "%~dp0scripts\install_windows.py"
goto finished
:python_on_path
where python >nul 2>nul
if errorlevel 1 goto missing_python
python "%~dp0scripts\install_windows.py"
goto finished
:custom_python
"%ATS_PORTAL_PYTHON%" "%~dp0scripts\install_windows.py"
goto finished
:missing_python
echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/windows/
echo Enable the Python launcher or Add Python to PATH, then run install.cmd again.
set "ATS_EXIT=1"
goto show_result
:finished
set "ATS_EXIT=%ERRORLEVEL%"
:show_result
if not "%ATS_EXIT%"=="0" echo Installation failed. Read the error above; see README-Windows.md.
if not defined ATS_PORTAL_NO_PAUSE pause
exit /b %ATS_EXIT%
