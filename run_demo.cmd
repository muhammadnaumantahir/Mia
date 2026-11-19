@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Mia's virtual environment was not found.
  echo First run:
  echo   py -3.13 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e ".[dev,gateway,monitoring]"
  pause
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example. Review it before using optional providers.
  )
)

if not exist "config\applications.json" (
  if exist "config\applications.example.json" (
    copy "config\applications.example.json" "config\applications.json" >nul
  )
)

echo Starting Mia on http://127.0.0.1:8765
echo Keep this window open while presenting. Press Ctrl+C to stop Mia.
".venv\Scripts\python.exe" -m mia
