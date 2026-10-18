@echo off
rem trip-planner backend runner (spawned by start.bat in its own window)
rem keep pure ASCII; runs in bat-file context so no cmd /c quote issues
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d %~dp0backend
.venv\Scripts\python.exe run.py
echo.
echo [trip-backend] exited with code %errorlevel%
