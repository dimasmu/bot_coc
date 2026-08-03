@echo off
echo ========================================
echo   CoC-AutoWeb - Starting both servers
echo ========================================
echo.
echo Backend: http://127.0.0.1:8088
echo Frontend dev: http://localhost:5173 (open this)
echo.

start "CoC-Backend" cmd /c "uv run uvicorn backend.main:app --host 127.0.0.1 --port 8088 --reload"
timeout /t 3 /nobreak >nul
start "CoC-Frontend" cmd /c "cd frontend && npx vite"

echo Both servers starting...
echo Close this window to stop both servers.
pause
