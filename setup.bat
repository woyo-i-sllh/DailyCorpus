@echo off
chcp 65001 >nul
title DailyCorpus 初始化
setlocal
cd /d "%~dp0"
echo 正在创建虚拟环境 .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.9+ 并勾选 Add to PATH
    pause
    exit /b 1
)
echo 正在安装 jieba（词频分词用）...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install jieba
if errorlevel 1 (
    echo [错误] 安装 jieba 失败，请检查网络后重试
    pause
    exit /b 1
)
echo.
echo [完成] 环境就绪。之后直接双击 daily_corpus.bat 即可每天抓取
pause
