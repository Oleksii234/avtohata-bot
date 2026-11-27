@echo off
cd /d "%~dp0"
title AvtoHata bot - DEMO
if not exist ".env.demo" (
    echo File .env.demo not found. See README, section "Demo mode".
    pause
    exit /b 1
)
echo Preparing environment, first run takes about a minute...
findstr /c:"3.15" ".venv\pyvenv.cfg" >nul 2>nul || py -V:3.15 -m venv --clear .venv
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q --upgrade -r requirements.txt
echo.
echo DEMO BOT IS RUNNING. Keep this window open. Stop: Ctrl+C
echo.
".venv\Scripts\python.exe" bot.py --demo
echo.
echo Demo bot stopped.
pause
