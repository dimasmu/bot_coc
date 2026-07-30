#!/usr/bin/env bash
set -e

echo "========================================"
echo "  CoC-AutoWeb - Starting both servers"
echo "========================================"
echo ""
echo "Backend: http://127.0.0.1:8000"
echo "Frontend: http://localhost:5173 (open this)"
echo ""

uv run fastapi dev backend/main.py &
BACKEND_PID=$!
sleep 3

cd frontend && npx vite &
FRONTEND_PID=$!

echo "Both servers started. Press Ctrl+C to stop."
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
