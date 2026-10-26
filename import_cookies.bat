@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title أداة استخراج واعتماد جلسة حساب المدرسة (state.json)
cd /d "%~dp0"
if "%~1"=="" (
    python login.py
) else (
    python login.py "%~1"
)
if %errorlevel% neq 0 (
    echo.
    echo حدث خطأ أثناء التشغيل.
    pause
)
