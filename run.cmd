@echo off
rem Usage: run.cmd <module> [args...]   e.g.  run.cmd parse_blocks --workers 12
rem Output is also saved to logs\<module>-<timestamp>.log (UTF-8)
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Run setup.cmd first & pause & exit /b 1 )
set PYTHONPATH=%~dp0src
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m deg.run %*
pause
