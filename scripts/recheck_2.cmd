@echo off
rem Второй шаг перепроверки (docs/tech/08-crypto.md): 2 голосования 2026 с расшифровкой в следующем блоке
rem и полная проверка голосования 2025 года, где итог не сошёлся.
cd /d "%~dp0\.."
chcp 65001 >nul
set PYTHONPATH=src
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe -m deg.run
%PY% crypto_recheck extract --src shard-5 --only GGCQL8Zw8d61QSq3XcoRrcKntqfQy3M6vVJPFhLNiDCB,EkDkkASKJcRg9DsJy2fHLFZyXjzYvG2Qf2hsDZVkUfJH --name late-2026 --defer-results
%PY% crypto_recheck verify --src late-2026
%PY% crypto_recheck extract --src edg2025 --only HW5wWVmZYsxFeKLfz1gwsnN9zg5fARwKtuMprysaNYS3 --name mismatch-2025
%PY% crypto_recheck verify --src mismatch-2025 --full --workers 8
%PY% crypto_recheck verify --src mismatch-2025 --full --allow-bad-ballots --workers 8
%PY% crypto_recheck summary
echo.
pause
