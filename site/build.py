"""Сборка страницы сайта: шаблон + geo.json + агрегаты → site/dist/deg2026-site.html.

Запуск из корня репозитория:  .venv\\Scripts\\python.exe site\\build.py
Перед этим: run.cmd site_data, run.cmd deg_results, run.cmd izbirkom_snapshot; хронология — data/news/timeline.json.
"""
import json, re, csv, os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
os.chdir(HERE)
geo = json.load(open('geo.json', encoding='utf-8'))
geo_out = {'regions': [{k: r[k] for k in ('d', 'code', 'name', 'cx', 'cy')} for r in geo['regions']]}
data = json.load(open(REPO/'data/derived/site/site_data.json', encoding='utf-8'))
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
t = open(sys.argv[1] if len(sys.argv) > 1 else 'page.tmpl.html', encoding='utf-8').read()
j = lambda o: json.dumps(o, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
t = t.replace('__GEO__', j(geo_out)).replace('__DATA__', j(data)).replace('__RES__', j(res)).replace('__NEWS__', j(json.load(open(REPO/'data/news/timeline.json', encoding='utf-8'))))
(HERE/'dist').mkdir(exist_ok=True); open(HERE/'dist'/'deg2026-site.html', 'w', encoding='utf-8').write(t)
# preview wrapper for local screenshots
open(HERE/'dist'/'preview.html', 'w', encoding='utf-8').write('<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>' + t + '</body></html>')
print(os.path.getsize(HERE/'dist'/'deg2026-site.html'))
