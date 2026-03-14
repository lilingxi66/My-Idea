@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0dist" mkdir "%~dp0dist"

if exist "%~dp0dist\SharedDatasetTool.exe" (
    del /f /q "%~dp0dist\SharedDatasetTool.exe"
    if exist "%~dp0dist\SharedDatasetTool.exe" (
        echo.
        echo Cannot replace dist\SharedDatasetTool.exe because it is in use.
        echo Please close the running application and try again.
        exit /b 1
    )
)

python -m PyInstaller --noconfirm --clean --windowed --onefile --name SharedDatasetTool --icon "assets\shared_dataset_icon.ico" --add-data "assets\shared_dataset_icon.ico;assets" "shared_dataset_tool.py"

if exist "%~dp0dist\SharedDatasetTool.exe" (
    echo.
    echo Build complete: dist\SharedDatasetTool.exe
) else (
    echo.
    echo Build failed. Please check the output above.
)
