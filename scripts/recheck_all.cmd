@echo off
rem Перепроверка голосований, которые криптопроверка не подтвердила (docs/tech/08-crypto.md).
rem 1) дампы только с нужными контрактами, results переставлен в конец блока; 2) верификатор на них;
rem 3) без испорченных бюллетеней; 4) полная проверка (ZKP) двух голосований, где итог не сошёлся,
rem    и она же без бюллетеней, не прошедших ZKP (как их исключила система).
cd /d "%~dp0\.."
chcp 65001 >nul
set PYTHONPATH=src
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe -m deg.run
%PY% crypto_recheck extract --parallel 5
%PY% crypto_recheck verify --parallel 5 --workers 4
%PY% crypto_recheck extract --src shard-2 --only GxFDFErjo12eef4VvHHhY1A87nWUiKCP9WSC4tFioD6V,GKpStUwFYLG2PyZKkTXbAUrJueT3EzXi53fsnVvzp3cV,8tHeqdJHNg6V8wySibofoyzLZC1PEPLckwnAjo9PLWUW --name bad-2026a
%PY% crypto_recheck extract --src shard-4 --only 6uR4gwW3dPZSAfHgBpSygFdfrNt8c2SyByWcjcZXnMQr --name bad-2026b
%PY% crypto_recheck extract --src shard-6 --only JAQNfkBZER6YRr7Pgis2q5B265sxujLp7kuT2V6KCbCM --name bad-2026c
%PY% crypto_recheck verify --src bad-2026a,bad-2026b,bad-2026c --allow-bad-ballots
%PY% crypto_recheck extract --src edg2024 --only Ai7tA69ZGacudLdrZ8Gsnv9geRHsFdPBAbmHY3UCSkKd --name mismatch-2024
%PY% crypto_recheck verify --src mismatch-2026,mismatch-2024 --full --workers 8
%PY% crypto_recheck verify --src mismatch-2026,mismatch-2024 --full --allow-bad-ballots --workers 8
echo.
echo Готово. Итоги: data\derived\crypto_recheck\*.verify.json
pause
