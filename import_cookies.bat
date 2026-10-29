@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python login.py %*
if errorlevel 1 pause
