@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
title Google Meet Bot - Firefox Login
python firefox_cookie_tool.py %*
pause
