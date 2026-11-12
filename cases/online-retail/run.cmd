@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
set "RESULT=1"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up the case Python environment...
    python -m venv .venv || goto :done
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r requirements.txt || goto :done
".venv\Scripts\python.exe" summarize.py
set "RESULT=%ERRORLEVEL%"

:done
if not "%RESULT%"=="0" echo Run failed. See the error above.
if /i "%~1"=="--no-pause" goto :exit
pause

:exit
popd
exit /b %RESULT%
