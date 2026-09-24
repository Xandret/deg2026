"""Сборка сайта ДЭГ-2026.

Запуск из корня репозитория:
    .venv\\Scripts\\python.exe site\\build.py                     # предпросмотр + публичная сборка (закрыта от поисковиков)
    .venv\\Scripts\\python.exe site\\build.py --launch --base-url https://example.ru/   # открытый запуск

Перед этим: run.cmd site_data, deg_results, izbirkom_snapshot, crypto_recheck summary, export_open_data.
Результат:
    site/dist/deg2026-site.html   — страница без <head> (для публикации как Artifact) и preview.html
    site/public/                  — готовый статический сайт: index.html, 404.html, robots.txt, favicon.svg,
                                    data/*, deg2026_open_data.zip. Кладётся на любой хостинг как есть.
"""
import argparse, csv, datetime as dt, html, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument('--repo', default=str(HERE.parent))
ap.add_argument('--tmpl', default=str(HERE / 'page.tmpl.html'))
ap.add_argument('--out', default=str(HERE))
ap.add_argument('--launch', action='store_true', help='разрешить индексацию (только после согласования с ОП)')
ap.add_argument('--base-url', default='', help='адрес сайта со слешем на конце, напр. https://deg2026.ru/')
a = ap.parse_args()
REPO, OUT = Path(a.repo), Path(a.out)
BASE = a.base_url.rstrip('/') + '/' if a.base_url else ''

geo = json.load(open(HERE / 'geo.json', encoding='utf-8'))
geo_out = {'regions': [{k: r[k] for k in ('d', 'code', 'name', 'cx', 'cy')} for r in geo['regions']]}
data = json.load(open(REPO / 'data/derived/site/site_data.json', encoding='utf-8'))
PUB = OUT / 'public'
man_p = PUB / 'data' / 'manifest.json'
if not man_p.exists():
    sys.exit('нет site/public/data/manifest.json — сначала run.cmd export_open_data')
files = json.load(open(man_p, encoding='utf-8'))
files['date'] = dt.datetime.fromtimestamp(man_p.stat().st_mtime).strftime('%d.%m.%Y')


def results_data(REPO):
    """Итоги ДЭГ по Госдуме (все регионы и округа) + бумага там, где есть официальный протокол.
    Бумага: data/derived/izbirkom_snapshot/paper_vs_deg_lines.csv (снимок izbirkom, сверенный с нашими HAR
    и с блокчейном, + наши HAR). Доли — от суммы голосов за кандидатов/партии (действительные бюллетени)."""
    base = REPO / 'data/derived'
    PN = ['Единая Россия', 'ЛДПР', 'Партия прямой демократии', 'Зелёные', 'Справедливая Россия', 'Родина',
          'КПРФ', 'Партия пенсионеров', 'Коммунисты России', 'Новые люди']
    COLS = ['ЕР', 'ЛДПР', 'ППД', 'Зелёные', 'СРЗП', 'Родина', 'КПРФ', 'Пенсионеры', 'Коммунисты России', 'Новые люди']
    L = {}
    for r in csv.DictReader(open(base/'izbirkom_snapshot/paper_vs_deg_lines.csv', encoding='utf-8')):
        L.setdefault((r['district'], int(r['protocol'])), {})[int(r['row'])] = (int(r['paper']), int(r['deg']), r['source'])
    def cand_shares(d):
        rows = sorted(n for n in d if n >= 13); tot = sum(d[n][0] for n in rows) or 1
        return [100 * d[n][0] / tot for n in rows]
    # округа
    sm = {}
    for r in csv.DictReader(open(base/'deg_results/sm_by_district.csv', encoding='utf-8')):
        dn = r['district']; lines = L.get((dn, 1))
        d = sm.setdefault(dn, {'id': len(sm), 'region': r['region'], 'district': dn.split(' – ')[-1], 'full': dn,
                               'contracts': int(r['contracts']),
                               'mapping': 'verified' if lines else ('exact' if r['mapping'] == 'exact' else 'recon'),
                               'source': (next(iter(lines.values()))[2] if lines else None), 'cands': []})
        d['cands'].append({'name': r['candidate'], 'deg': int(r['deg_votes']), 'paper': None})
    for dn, d in sm.items():
        lines = L.get((dn, 1))
        if lines:
            sh = cand_shares(lines)
            assert len(sh) == len(d['cands']), dn
            for c, s in zip(d['cands'], sh):
                c['paper'] = round(s, 3)
            iss = lambda k: sum(lines[n][k] for n in (3, 4, 5))
            d['turnout'] = {'paper': 100 * iss(0) / lines[1][0], 'deg': 100 * iss(1) / lines[1][1],
                            'share': 100 * lines[10][1] / (lines[10][0] + lines[10][1])}
    # партии по регионам: бумага, только если все округа региона есть
    reg_d = {}
    for dn, d in sm.items():
        reg_d.setdefault(d['region'], []).append(dn)
    pl = []
    for r in csv.DictReader(open(base/'deg_results/pl_by_region.csv', encoding='utf-8')):
        v = [int(float(r[c])) for c in COLS]; paper = None; dl = reg_d.get(r['region'], [])
        if dl and all((dn, 2) in L for dn in dl):
            tot = [0] * 10
            for dn in dl:
                rows = sorted(n for n in L[(dn, 2)] if n >= 13)
                for i, n in enumerate(rows):
                    tot[i] += L[(dn, 2)][n][0]
            paper = [round(100 * x / sum(tot), 3) for x in tot]
        pl.append({'region': r['region'], 'rc': int(r['rc']), 'deg': v, 'valid': sum(v), 'paper': paper})
    nat = [sum(x['deg'][i] for x in pl) for i in range(len(COLS))]
    turnout = [{'label': d['full'].replace(' – ', ': '), 'region': d['region'], **d['turnout']} for d in sm.values() if d.get('turnout')]
    return {'parties': PN, 'pl': pl, 'plNat': nat, 'sm': list(sm.values()), 'turnout': sorted(turnout, key=lambda x: x['label'])}

res = results_data(REPO)
t = open(a.tmpl, encoding='utf-8').read()
j = lambda o: json.dumps(o, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
news = {'items': json.load(open(REPO / 'data/news/timeline.json', encoding='utf-8')),
        'sources': json.load(open(REPO / 'data/news/sources.json', encoding='utf-8'))}
for k, v in (('__GEO__', geo_out), ('__DATA__', data), ('__RES__', res), ('__NEWS__', news)):
    assert t.count(k) == 1, k
    t = t.replace(k, j(v))
assert t.count('__FILES__') == 1
t_prev = t.replace('__FILES__', j({**files, 'preview': True})).replace(' download>', '>')   # просмотрщик Artifact не даёт скачивать
t = t.replace('__FILES__', j(files))

# 1) страница без <head> — для Artifact (предпросмотр для согласования) и локального просмотра
dist = OUT / 'dist'; dist.mkdir(exist_ok=True)
(dist / 'deg2026-site.html').write_text(t_prev, encoding='utf-8')
(dist / 'preview.html').write_text('<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>' + t + '</body></html>', encoding='utf-8')

# 2) публичный статический сайт
TITLE = 'ДЭГ-2026 в открытых данных'
DESC = ('Независимый пересчёт федерального электронного голосования 18–20 сентября 2026 года по открытому журналу '
        'блокчейна: явка по регионам и участкам, итоги по партиям и округам, сравнение с бумагой, криптопроверка, '
        'хронология. Данные можно скачать.')
e = lambda s: html.escape(s, quote=True)
og_img = (BASE + 'og.png') if BASE and (PUB / 'og.png').exists() else ''
head = [
    '<!doctype html>', '<html lang="ru">', '<head>', '<meta charset="utf-8">',
    f'<title>{TITLE}</title>',
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
    f'<meta name="description" content="{e(DESC)}">',
    '<meta name="author" content="Xandret">',
    '<meta name="robots" content="' + ('index, follow' if a.launch else 'noindex, nofollow') + '">',
    '<meta name="referrer" content="strict-origin-when-cross-origin">',
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">",
    '<meta name="color-scheme" content="dark light">',
    '<meta name="theme-color" content="#000000">',
    '<link rel="icon" href="favicon.svg" type="image/svg+xml">',
    f'<meta property="og:type" content="website">', '<meta property="og:locale" content="ru_RU">',
    f'<meta property="og:title" content="{TITLE}">', f'<meta property="og:description" content="{e(DESC)}">',
    '<meta property="og:site_name" content="ДЭГ-2026 в открытых данных">',
]
if BASE:
    head += [f'<link rel="canonical" href="{BASE}">', f'<meta property="og:url" content="{BASE}">']
if og_img:
    head += [f'<meta property="og:image" content="{og_img}">', '<meta property="og:image:width" content="1200">',
             '<meta property="og:image:height" content="630">', '<meta name="twitter:card" content="summary_large_image">']
cut = t.index('</style>') + len('</style>')
body_head = t[:cut]
assert body_head.startswith('<title>'), 'шаблон должен начинаться с <title>'
body_head = body_head[body_head.index('<style>'):]          # свой <title>/viewport выше
page = '\n'.join(head) + '\n' + body_head + '\n</head>\n<body>\n' + t[cut:] + \
       '\n<noscript><p style="padding:16px;color:#fff;background:#000">Для карты и таблиц нужен JavaScript. ' \
       'Данные можно скачать без него: <a href="data/deg2026_tables.xlsx">таблицы Excel</a>, ' \
       '<a href="deg2026_open_data.zip">весь набор (ZIP)</a>.</p></noscript>\n</body>\n</html>\n'
(PUB / 'index.html').write_text(page, encoding='utf-8')

FAV = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#3987e5"/>'
       '<path d="M8.5 16.5l5 5 10-11" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg>\n')
(PUB / 'favicon.svg').write_text(FAV, encoding='utf-8')
(PUB / '404.html').write_text('''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Страница не найдена — ДЭГ-2026 в открытых данных</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<style>:root{color-scheme:dark light}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#fff;
font:16px/1.5 "Helvetica Neue",Helvetica,Arial,sans-serif;padding:16px;box-sizing:border-box}
@media (prefers-color-scheme:light){body{background:#f4f4f5;color:#111}}
main{max-width:420px}h1{font-size:28px;margin:0 0 8px}a{color:#3987e5}</style></head>
<body><main><h1>Такой страницы нет</h1><p>Возможно, ссылка устарела. Карта, итоги и данные — на <a href="./">главной странице</a>.</p></main></body></html>
''', encoding='utf-8')
robots = 'User-agent: *\n' + ('Allow: /\n' + (f'Sitemap: {BASE}sitemap.xml\n' if BASE else '') if a.launch else 'Disallow: /\n')
(PUB / 'robots.txt').write_text(robots, encoding='utf-8')
if a.launch and BASE:
    (PUB / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                     f'<url><loc>{BASE}</loc><lastmod>{dt.date.today().isoformat()}</lastmod></url></urlset>\n', encoding='utf-8')
elif (PUB / 'sitemap.xml').exists():
    (PUB / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>\n', encoding='utf-8')
(PUB / '.nojekyll').write_text('', encoding='utf-8')     # GitHub Pages: отдавать файлы как есть

total = sum(p.stat().st_size for p in PUB.rglob('*') if p.is_file())
print(f"dist/deg2026-site.html {os.path.getsize(dist / 'deg2026-site.html'):,} Б; public/index.html "
      f"{os.path.getsize(PUB / 'index.html'):,} Б; весь public {total/1048576:.1f} МБ; "
      + ('ОТКРЫТ ДЛЯ ПОИСКОВИКОВ' if a.launch else 'закрыт от поисковиков (noindex)'))
