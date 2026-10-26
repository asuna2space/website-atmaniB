@echo off
chcp 65001 > nul
title Google Meet Silent Bot
echo ======================================================
echo    Google Meet Silent Bot
echo ======================================================
echo [INFO] Student Name: hazem
echo [INFO] Status: Running in background silently...
echo.
cd /d "%~dp0"
python bot.py
pause
