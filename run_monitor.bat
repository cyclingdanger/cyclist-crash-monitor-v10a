@echo off
cd /d "%~dp0"
echo Installing/checking required packages...
python -m pip install -r requirements.txt
echo.
echo Starting U.S. Cyclist Crash Monitor...
python app.py
pause
