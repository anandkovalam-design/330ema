@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
set "ZERODHA_CLOUD_MODE=false"
echo Starting ANAND 3/30 EMA Zerodha real-time dashboard...
py -m streamlit run src\aadithya_quantlab\zerodha_live_trading\app.py
if errorlevel 1 (
  echo.
  echo Dashboard failed to start. Install dependencies with:
  echo   py -m pip install -r requirements.txt
  pause
)
endlocal
