@echo off
rem ============================================================
rem  Runs auto_upload.py (called from Windows Task Scheduler).
rem
rem  Runs auto_upload.py twice:
rem    PASS 1: no options        -> imports unregistered files only (past months)
rem    PASS 2: --force --month N -> re-imports the CURRENT month, overwriting
rem
rem  Why pass 2: files for the current month are still being written. Once a
rem  report is registered, a normal run skips it as DUP, so later edits by the
rem  stores would never reach the DB. Forcing the current month every run keeps
rem  it in sync. Past months are left alone (they are already complete).
rem
rem  - Appends output to logs\auto_upload_YYYYMMDD.log
rem  - Running twice in one day appends to the same log file
rem  - Exits non-zero if EITHER pass reports errors
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

rem Get YYYYMMDD and the current month (%date% format is locale dependent,
rem so ask PowerShell instead of slicing %date%)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).Month"') do set "THISMONTH=%%i"
set "LOGFILE=logs\auto_upload_%TODAY%.log"

rem Resolve python (fall back to the default install path if not on PATH)
set "PYTHON=python"
where python >nul 2>&1
if errorlevel 1 set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"

>> "%LOGFILE%" echo(
>> "%LOGFILE%" echo ############################################################
>> "%LOGFILE%" echo  START: %date% %time%
>> "%LOGFILE%" echo  PYTHON: "%PYTHON%"
>> "%LOGFILE%" echo ############################################################

rem ---- PASS 1: past months, unregistered files only -----------------------
>> "%LOGFILE%" echo(
>> "%LOGFILE%" echo ============================================================
>> "%LOGFILE%" echo  PASS 1/2: auto_upload.py  (new files only)
>> "%LOGFILE%" echo ============================================================
"%PYTHON%" auto_upload.py >> "%LOGFILE%" 2>&1
set "RC1=%ERRORLEVEL%"

rem ---- PASS 2: current month, forced overwrite ----------------------------
>> "%LOGFILE%" echo(
>> "%LOGFILE%" echo ============================================================
>> "%LOGFILE%" echo  PASS 2/2: auto_upload.py --force --month %THISMONTH%
>> "%LOGFILE%" echo ============================================================
"%PYTHON%" auto_upload.py --force --month %THISMONTH% >> "%LOGFILE%" 2>&1
set "RC2=%ERRORLEVEL%"

rem Exit non-zero if either pass failed
set "RC=%RC1%"
if not "%RC2%"=="0" set "RC=%RC2%"

>> "%LOGFILE%" echo ------------------------------------------------------------
>> "%LOGFILE%" echo  END  : %date% %time%  (pass1=%RC1% pass2=%RC2% exit code=%RC%)
>> "%LOGFILE%" echo(

endlocal & exit /b %RC%
