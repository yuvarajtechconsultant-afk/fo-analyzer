@echo off
title F&O Analyzer
echo Starting F^&O Analyzer...
echo.
echo Local URL  : http://localhost:8000
echo Public URL : https://herbs-idiocy-constable.ngrok-free.dev
echo.
echo Press Ctrl+C to stop.
echo.
cd /d "%~dp0"

:: Start ngrok tunnel in background
start "ngrok" /min cmd /c "ngrok.exe http --url=herbs-idiocy-constable.ngrok-free.dev 8000"

:: Wait a moment for ngrok to connect
timeout /t 3 /nobreak >nul

:: Start FastAPI server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
