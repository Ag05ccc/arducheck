@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe arducheck.py %*
) else (
    python arducheck.py %*
)
pause
