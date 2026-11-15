@echo off
:: Run the python executable directly from the venv
:: Prefer the local .venv, fall back to a plain venv folder
:: %* passes all arguments from the .bat to the .py script
set "PY=.\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=.\venv\Scripts\python.exe"
"%PY%" vivid.py %*

pause