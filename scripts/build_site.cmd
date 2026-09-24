@echo off
rem Сборка публичного сайта: открытые данные + страница → site\public\ (готово к выкладке на любой хостинг).
rem   scripts\build_site.cmd                                   — закрыто от поисковиков (для показа ОП)
rem   scripts\build_site.cmd --launch --base-url https://АДРЕС/  — открытый запуск, только после согласования
cd /d "%~dp0\.."
chcp 65001 >nul
set PYTHONPATH=src
set PYTHONUTF8=1
.venv\Scripts\python.exe -m deg.run export_open_data || goto :err
.venv\Scripts\python.exe site\build.py %* || goto :err
echo.
echo Готово: откройте site\public\index.html в браузере — это полная версия сайта, работает без интернета.
pause
exit /b 0
:err
echo ОШИБКА сборки — см. вывод выше.
pause
exit /b 1
