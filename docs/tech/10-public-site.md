# Публичный сайт и открытые данные

Сайт — статический: одна страница `index.html` со всеми данными внутри, файлы для скачивания и несколько
служебных файлов. Серверного кода, базы данных, cookies и счётчиков посещений нет.

## Сборка

```
scripts\build_site.cmd                                    :: для показа ОП — закрыт от поисковиков
scripts\build_site.cmd --launch --base-url https://АДРЕС/ :: открытый запуск (после согласования)
```

Скрипт выполняет `run.cmd export_open_data` → `site\build.py`. Перед этим должны быть свежими `site_data`,
`deg_results`, `izbirkom_snapshot`, `crypto_recheck summary` и `data/news/*.json`.

Результат — папка `site/public/` (~10 МБ), её целиком кладут на хостинг:

| Файл | Что это |
|---|---|
| `index.html` | сайт; полный `<head>`: описание, превью для мессенджеров (og), CSP, `robots` |
| `404.html`, `favicon.svg`, `og.png` | страница «не найдено», иконка, картинка превью 1200×630 |
| `robots.txt`, `sitemap.xml` | без `--launch`: `Disallow: /` и пустой sitemap; с `--launch`: открыт |
| `data/*.csv`, `data/*.json` | открытые данные (`src/deg/export_open_data.py`), CSV — UTF-8 с BOM |
| `data/deg2026_tables.xlsx` | те же таблицы для Excel, русские заголовки, вкладка «О данных» |
| `deg2026_open_data.zip` | весь набор одним архивом |
| `data/README.md`, `data/manifest.json` | словарь данных; список файлов, строк и размеров (из него строится раздел «Данные») |

Кроме того, `site/dist/deg2026-site.html` — та же страница без `<head>` в режиме «согласование»
(скачивание отключено) для публикации как Artifact.

## Что публикуется, а что нет

Только суммы по регионам, участкам (номер УИК), голосованиям (id контракта) и строки официальных протоколов.
Не публикуются: обезличенные идентификаторы избирателей, хеши списков, сырые дампы, HAR, переписка.
Лицензия на производные данные — CC BY 4.0, атрибуция «deg2026 / Xandret».

## Показ ОП (до запуска)

* Открыть `site/public/index.html` в браузере — полная версия, работает без интернета, скачивание файлов работает.
  Папку `site/public` можно передать архивом.
* Ссылка на Artifact (claude.ai) приватна и в РФ без VPN недоступна — для ОП не подходит.

## Хостинг

**Основной — домен в зоне .ru у российского хостинга.** Подходит любой, кто отдаёт статические файлы по HTTPS:
обычный виртуальный хостинг (загрузить содержимое `site/public` в корень сайта по SFTP/FTP) или объектное
хранилище с режимом «статический сайт» (например, Yandex Object Storage, Selectel, VK Cloud). Аккаунт, домен и
оплату оформляет Алекс; данные регистранта домена — его решение.

Проверить после выкладки:
* типы файлов: `.csv` → `text/csv; charset=utf-8`, `.xlsx` → `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
  `.zip` → `application/zip`, `.svg` → `image/svg+xml`;
* HTTPS и переадресация с http;
* `404.html` как страница ошибки (в настройках хостинга);
* превью ссылки в Telegram (картинка og.png появится только при сборке с `--base-url`).

Если хостинг позволяет задать заголовки (nginx):

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" always;
add_header X-Content-Type-Options nosniff always;
add_header Referrer-Policy strict-origin-when-cross-origin always;
location /data/ { add_header Cache-Control "public, max-age=3600"; }
error_page 404 /404.html;
```

**Зеркало — GitHub Pages.** Workflow `.github/workflows/pages.yml` выкладывает `site/public` как есть и
запускается только вручную (Actions → «Сайт на GitHub Pages» → Run workflow). В репозитории: Settings → Pages →
Source: GitHub Actions. На бесплатном тарифе Pages работает только для публичного репозитория — либо открыть
репозиторий (история проверена: секретов и сырых данных нет), либо завести отдельный публичный репозиторий только
для `site/public`.

## Журналы доступа

Сам сайт ничего о посетителях не собирает. Журналы запросов (IP-адреса) ведёт хостинг — если есть настройка,
сократить срок их хранения или отключить.

## Чек-лист запуска

1. ОП посмотрела закрытую версию, правки внесены (решение №30).
2. Статусы иноагентов в `data/news/sources.json` сверены с реестром Минюста в день запуска (`_checked`).
3. Согласовано, под каким именем публиковать (сейчас — Xandret).
4. `scripts\build_site.cmd --launch --base-url https://АДРЕС/`, коммит `site/public`.
5. Выкладка на .ru, затем запуск workflow зеркала.
6. Проверка: главная, #data, скачивание xlsx/zip, 404, robots.txt, превью в Telegram, телефон.

## Временная ссылка для показа одному человеку
`scripts\publish_preview.cmd` собирает сайт с пометкой «предварительная версия» в соседнюю папку `..\deg2026-preview`
(только содержимое `site/public`) и отправляет её в отдельный репозиторий `Xandret/deg2026-preview`; GitHub Pages
раздаёт его по адресу https://xandret.github.io/deg2026-preview/. Страница закрыта от поисковиков, но открывается
у любого, у кого есть ссылка, а репозиторий виден в профиле. Убрать ссылку — удалить репозиторий
(Settings → Delete this repository).
