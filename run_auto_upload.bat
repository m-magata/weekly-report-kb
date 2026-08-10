@echo off
rem ============================================================
rem  Runs auto_upload.py (called from Windows Task Scheduler).
rem
rem  - Appends output to logs\auto_upload_YYYYMMDD.log
rem  - Running twice in one day appends to the same log file
rem  - Exits with auto_upload.py's exit code (0 = OK, 1 = errors)
rem
rem  NOTE: This file is intentionally ASCII-only.
rem  cmd.exe parses .bat files using the system code page (cp932 here),
rem  so UTF-8 Japanese text inside this file gets mis-parsed and breaks
rem  the script. All Japanese text in the log comes from auto_upload.py,
rem  which writes UTF-8 on its own.
rem
rem  NOTE: The target folder Q: is a mapped network drive, so it is only
rem  visible inside a logged-on user session. Register the scheduled task
rem  with "run only when user is logged on" (see register_task.ps1).
rem ============================================================

rem Use UTF-8 so Python output is not garbled
chcp 65001 > nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

setlocal

rem Move to the folder containing this batch file (= project root)
cd /d "%~dp0"

rem Prepare the log folder
if not exist "logs" mkdir "logs"

rem Get YYYYMMDD (%date% format is locale dependent, so ask PowerShell)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
set "LOGFILE=logs\auto_upload_%TODAY%.log"

rem Resolve python (fall back to the default install path if not on PATH)
set "PYTHON=python"
where python >nul 2>&1
if errorlevel 1 set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"

>> "%LOGFILE%" echo(
>> "%LOGFILE%" echo ============================================================
>> "%LOGFILE%" echo  START: %date% %time%
>> "%LOGFILE%" echo  CMD  : "%PYTHON%" auto_upload.py
>> "%LOGFILE%" echo ============================================================

"%PYTHON%" auto_upload.py >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

>> "%LOGFILE%" echo ------------------------------------------------------------
>> "%LOGFILE%" echo  END  : %date% %time%  (exit code=%RC%)
>> "%LOGFILE%" echo(

endlocal & exit /b %RC%
