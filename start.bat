@echo off
setlocal

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [setup] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [error] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [setup] Installing dependencies...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [error] Failed to install dependencies.
    pause
    exit /b 1
)

echo [start] Launching Media Tester Web UI...
start http://127.0.0.1:7860
"%PYTHON_EXE%" -m uvicorn app:app --host 127.0.0.1 --port 7860

if errorlevel 1 (
    echo [error] Media Tester exited with an error.
    pause
    exit /b 1
)

endlocal
