@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
title Google Meet Bot - Firefox Login
echo ================================================================
echo   Google Meet Bot - Firefox Auto Login and Telegram Exporter
echo ================================================================
echo.
python firefox_cookie_tool.py %*
echo.
pause
