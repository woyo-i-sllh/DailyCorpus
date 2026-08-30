@echo off
chcp 65001 >nul
title DailyCorpus 每日语料库
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 找不到虚拟环境 .venv，请先双击 setup.bat 初始化
    pause
    exit /b 1
)
echo ============================================
echo    DailyCorpus 每日语料库（大事 + 财经 + AI精选）
echo ============================================
echo.
echo [1/2] 抓取今天的语料并写入数据库...
"%PY%" daily_corpus.py %*
echo.
echo [2/2] DeepSeek 精选要闻 + 简析...
"%PY%" ai_digest.py
echo.
echo [信息] 完成。看板：双击 dashboard.bat
pause
