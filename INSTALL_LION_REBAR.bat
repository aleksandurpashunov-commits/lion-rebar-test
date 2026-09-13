@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  LION REBAR DESKTOP - installation
echo ============================================================

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python is not available through the command "python".
  echo Python 3.11 or 3.12 must be installed and added to PATH.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo ERROR: The local Python environment could not be created.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Installation completed.
echo Start the program with START_LION_REBAR.bat
pause
exit /b 0

:fail
echo.
echo ERROR: Installation failed. Check the internet connection and the messages above.
pause
exit /b 1
