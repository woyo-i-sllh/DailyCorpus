@echo off
chcp 65001 >nul
title DailyCorpus 数据看板
cd /d "%~dp0"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0dashboard.py"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
