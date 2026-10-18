@echo off
REM One-click start (Windows). First run installs everything.
cd /d "%~dp0"
if not exist .venv (
  echo First run: creating environment and installing packages...
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
if not exist .env copy .env.example .env
where ffmpeg >nul 2>nul || echo WARNING: ffmpeg not found - voice recordings will not work. Install: winget install ffmpeg
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8000/demo"
uvicorn app.main:app --reload --port 8000
pause
