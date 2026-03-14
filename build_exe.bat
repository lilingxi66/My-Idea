@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --windowed --onefile --name SharedDatasetTool --icon "assets\shared_dataset_icon.ico" --add-data "assets\shared_dataset_icon.ico;assets" "shared_dataset_tool.py"

if exist "%~dp0dist\SharedDatasetTool.exe" (
    echo.
    echo Build complete: dist\SharedDatasetTool.exe
) else (
    echo.
    echo Build failed. Please check the output above.
)
