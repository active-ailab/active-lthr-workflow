@echo off
setlocal enabledelayedexpansion

set SCRIPT_DIR=%~dp0
set PY=python

where %PY% >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  exit /b 1
)

echo [INFO] Running LTHR Jira Analyzer in batch mode...
%PY% "%SCRIPT_DIR%run_lt_log_analysis.py" --issue-file "%SCRIPT_DIR%issues.txt"
set CODE=%ERRORLEVEL%

if %CODE% neq 0 (
  echo [ERROR] Analyzer failed with exit code %CODE%.
  exit /b %CODE%
)

echo [OK] Done.
exit /b 0
