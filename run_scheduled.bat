@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
".venv\Scripts\python.exe" daily_corpus.py --since-hours 24 >> "logs\run.log" 2>&1
".venv\Scripts\python.exe" ai_digest.py >> "logs\run.log" 2>&1
