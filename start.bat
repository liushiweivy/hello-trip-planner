@echo off
rem HelloAgents trip planner: one-click start (backend :8000 + frontend :5173)
rem NOTE: keep this file pure ASCII. UTF-8 Chinese + chcp 65001 corrupts cmd's
rem batch parser (byte-offset drift) and the script dies mid-file.
cd /d %~dp0

rem local default "py -3" points to a broken 3.14, pin 3.11 explicitly
set PYTHON=py -3.11

echo ============================================
echo  HelloAgents Trip Planner - one-click start
echo ============================================

if not exist "backend\.venv" (
  echo [1/3] first run: creating backend venv and installing deps...
  cd backend
  %PYTHON% -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  cd ..
) else (
  echo [1/3] backend venv ready
)

if not exist "frontend\node_modules" (
  echo [2/3] installing frontend deps...
  cd frontend
  call npm install
  cd ..
) else (
  echo [2/3] frontend deps ready
)

if not exist "backend\.env" (
  echo [!] backend\.env missing, copying template - fill LLM_API_KEY and restart
  copy backend\.env.example backend\.env >nul
)
if not exist "frontend\.env" copy frontend\.env.example frontend\.env >nul

echo [3/3] starting backend :8000 and frontend :5173 ...
rem NOTE: do NOT inline "chcp 65001 >nul && ..." into a cmd /k string - the
rem quoted-compound form intermittently fails with "path not found" on this
rem machine. Delegate to bat files instead (bat-file context is reliable).
start "trip-backend"  cmd /k "%~dp0run-backend.bat"
timeout /t 3 >nul
start "trip-frontend" cmd /k "%~dp0run-frontend.bat"

echo.
echo backend API docs: http://localhost:8000/docs
echo frontend page:    http://localhost:5173
echo (close the two spawned console windows to stop the services)
