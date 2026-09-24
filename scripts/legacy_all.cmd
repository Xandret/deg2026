@echo off
rem Full cycle for 2024 and 2025 dumps: parse -> db -> validate vs Slotin. Logs in logs\
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONPATH=%CD%\src
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
for %%Y in (2024 2025) do (
  %PY% -m deg.run parse_dump_json --year %%Y --workers 12
  %PY% -m deg.run build_db --src data\derived\blocks_%%Y --db data\derived\deg%%Y.duckdb
)
%PY% -m deg.run dynamics
for %%Y in (2024 2025) do (
  %PY% -m deg.run validate_slotin --db data\derived\deg%%Y.duckdb --slotin-dir data\external\slotin\edg%%Y --tag %%Y
)
echo.
echo Done. Logs: logs\
pause
