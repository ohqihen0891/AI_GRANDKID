#!/usr/bin/env bash
# One-click start (Mac/Linux). First run installs everything.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: creating environment and installing packages..."
  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
else
  . .venv/bin/activate
fi
[ -f .env ] || cp .env.example .env
command -v ffmpeg >/dev/null || echo "WARNING: ffmpeg not found - voice recordings will not work (brew install ffmpeg)"
( sleep 4; (open "http://localhost:8000/demo" || xdg-open "http://localhost:8000/demo") >/dev/null 2>&1 ) &
uvicorn app.main:app --reload --port 8000
