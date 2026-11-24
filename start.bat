@echo off
cd /d "%~dp0"
title AvtoHata bot
echo Preparing environment, first run takes about a minute...
findstr /c:"3.15" ".venv\pyvenv.cfg" >nul 2>nul || py -V:3.15 -m venv --clear .venv
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q --upgrade -r requirements.txt
echo.
echo BOT IS RUNNING. Keep this window open. Stop: Ctrl+C
echo.
".venv\Scripts\python.exe" bot.py
echo.
echo Bot stopped.
pause
