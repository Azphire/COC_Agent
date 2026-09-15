@echo off
setlocal
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is missing. Install uv, then run: uv sync --directory "%~dp0backend" --locked
  exit /b 1
)
if not exist "%~dp0backend\.venv\Scripts\python.exe" (
  echo Install backend dependencies: uv sync --directory "%~dp0backend" --locked
  echo Install frontend dependencies: npm.cmd --prefix "%~dp0frontend" ci
  exit /b 1
)
"%~dp0backend\.venv\Scripts\python.exe" -X utf8 "%~dp0backend\scripts\launch.py" %*
exit /b %errorlevel%
