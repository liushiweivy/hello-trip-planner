@echo off
rem trip-planner frontend runner (spawned by start.bat in its own window)
rem keep pure ASCII; runs in bat-file context so no cmd /c quote issues
chcp 65001 >nul
cd /d %~dp0frontend
npm run dev
echo.
echo [trip-frontend] exited with code %errorlevel%
