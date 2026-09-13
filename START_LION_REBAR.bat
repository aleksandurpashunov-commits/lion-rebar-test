@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\\Scripts\\python.exe" (
  echo Run INSTALL_LION_REBAR.bat first.
  pause
  exit /b 1
)
call ".venv\\Scripts\\activate.bat"
python run_dev.py
