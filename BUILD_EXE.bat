@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run INSTALL_LION_REBAR.bat first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m pip install "pyinstaller>=6.10,<7"
if errorlevel 1 goto :fail

pyinstaller --noconfirm --clean --windowed --name LION_REBAR_DESKTOP ^
  --add-data "engine_updates;engine_updates" ^
  --add-data "lion_rebar\resources;lion_rebar\resources" ^
  run_dev.py
if errorlevel 1 goto :fail

set "LION_REBAR_DIST=%CD%\dist\LION_REBAR_DESKTOP"
echo.
echo Running mandatory dist smoke tests...
python -m pytest tests\test_dist_smoke.py -q
if errorlevel 1 (
  echo.
  echo Dist smoke tests failed. The EXE package is not release-safe.
  goto :fail
)

echo.
echo Build complete and dist smoke tests passed.
echo EXE folder: dist\LION_REBAR_DESKTOP\
pause
exit /b 0

:fail
echo.
echo Build failed or the dist package did not pass smoke tests.
pause
exit /b 1
