@echo off
cd /d "%~dp0"
set PY=..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

"%PY%" -m pip install -q -r requirements.txt
start "" "http://127.0.0.1:8765"
"%PY%" -m uvicorn server:app --host 127.0.0.1 --port 8765
