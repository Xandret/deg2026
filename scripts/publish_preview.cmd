@echo off
rem Временная ссылка для показа одному человеку: только готовый сайт (site\public), без кода и сырых данных.
rem Папка ..\deg2026-preview — отдельный маленький git-репозиторий -> github.com/Xandret/deg2026-preview -> GitHub Pages.
rem Сначала на GitHub: New repository "deg2026-preview", Public, без README. Затем запустить этот файл.
rem Адрес для показа: https://xandret.github.io/deg2026-preview/  (закрыт от поисковиков; удалить репозиторий = убрать ссылку)
cd /d "%~dp0\.."
chcp 65001 >nul
set PYTHONUTF8=1
set DEST=%~dp0..\..\deg2026-preview
.venv\Scripts\python.exe site\build.py --preview --pub "%DEST%" --base-url https://xandret.github.io/deg2026-preview/ || goto :err
cd /d "%DEST%"
if not exist ".git" (
  git init -b main || goto :err
  git config user.name Xandret
  git config user.email Xandret@users.noreply.github.com
  git remote add origin https://github.com/Xandret/deg2026-preview.git
)
rem файлы сборки уже с LF: не перекодировать (иначе git предупреждает «LF will be replaced by CRLF»)
git config core.autocrlf false
(echo * text=auto eol=lf& echo *.png binary& echo *.zip binary& echo *.xlsx binary)> .gitattributes
git add -A
git commit -m "Предварительная версия сайта ДЭГ-2026" || echo (изменений нет)
git push -u origin main || goto :err
echo.
echo Готово. Если это первый раз: GitHub -^> deg2026-preview -^> Settings -^> Pages -^> Branch: main, папка / (root) -^> Save.
echo Через 1-2 минуты сайт откроется: https://xandret.github.io/deg2026-preview/
pause
exit /b 0
:err
echo ОШИБКА — см. вывод выше.
pause
exit /b 1
