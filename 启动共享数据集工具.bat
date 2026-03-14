@echo off
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0shared_dataset_tool.py"
) else (
    python "%~dp0shared_dataset_tool.py"
)
