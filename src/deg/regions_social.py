"""Социология явки: показатели ДЭГ по 32 регионам против социально-экономических показателей Росстата.

Запуск: run.cmd regions_social        (после site_data и deg_results)
Вход:   data/external/rosstat/xlsx/Раздел *.xlsx — «Регионы России. Социально-экономические показатели — 2025»,
        электронная версия (rosstat.gov.ru/folder/210/document/47652, архив Pril_Region_Pokaz_2025.rar, см. SOURCE.txt)
Выход:  data/derived/regions_social/{indicators.csv, correlations.csv, report.json}
        data/derived/site/social.json — для сайта

Метод: ранговая корреляция Спирмена (n = 32, связь монотонная, выбросы не тянут), 95 % доверительный интервал —
бутстреп по регионам (10 000 повторов), p — перестановочный тест (10 000). Поправка Холма на все пары
«показатель ДЭГ × показатель Росстата». Это экологические корреляции: они говорят о регионах, а не о людях,
и не доказывают причинность (docs/tech/12-regions-social.md).
"""
from __future__ import annotations

import json
import os
import re

import numpy as np
import openpyxl
import pandas as pd

from . import config

XLSX = config.EXTERNAL / "rosstat" / "xlsx"
OUT = config.DERIVED / "regions_social"
SRC = getattr(config, "SRC_DERIVED", config.DERIVED)

# (ключ, файл, лист, подпись, единица)
ROSSTAT = [
    ("urban", "Раздел 1 - Население.xlsx", "1.3.", "Городское население", "%"),
    ("old", "Раздел 1 - Население.xlsx", "1.6.3.", "Старше трудоспособного возраста", "%"),
    ("pop", "Раздел 1 - Население.xlsx", "1.1.", "Численность населения", "тыс. чел."),
    ("unemp", "Раздел 2 - Труд.xlsx", "2.10.1.", "Уровень безработицы", "%"),
    ("income", "Раздел 3 - Уровень жизни населения.xlsx", "3.3.", "Медианный душевой доход", "руб./мес."),
    ("inet", "Раздел 17 - Информационные и коммуникационные технологии.xlsx", "17.7.1.", "Пользуются интернетом", "%"),
    ("inet_daily", "Раздел 17 - Информационные и коммуникационные технологии.xlsx", "17.7.2.", "Интернет каждый день", "%"),
    ("bband", "Раздел 17 - Информационные и коммуникационные технологии.xlsx", "17.6.3.", "Широкополосный интернет дома", "% домохозяйств"),
]
X_KEYS = ["urban", "old", "income", "unemp", "inet", "inet_daily", "bband"]
DEG = [("uptake", "Охват ДЭГ (записались на 100 жителей)"), ("turnout", "Явка ДЭГ"), ("first_hour", "Первый час"),
       ("uik100", "Доля УИК со 100 %"), ("removed", "Исключены из списка"), ("unpaired", "Бюллетени без голоса"),
       ("er", "«Единая Россия» в ДЭГ (Госдума)")]


def norm(s: str) -> str:
    s = re.sub(r"[\d\)\*]+$", "", str(s).strip())            # сноски «1)»
    s = s.replace("ё", "е").replace("Ё", "Е").lower()
    s = re.sub(r"\s*[-–—]\s*", "-", s)
    s = s.replace("чувашская республика-чувашия", "чувашская республика")
    s = re.sub(r"\(.*?\)", "", s)
    return re.sub(r"\s+", " ", s).strip()


def read_sheet(path, sheet):
    """Таблица «регион × год» → {норм. имя: (год, значение)} по последнему году с данными."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = next(w for w in wb.worksheets if w.title.strip().rstrip(".") == sheet.strip().rstrip("."))
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows) if sum(isinstance(x, (int, float)) and 1990 <= x <= 2030 for x in r) >= 3)
    years = {j: int(x) for j, x in enumerate(rows[hdr_i]) if isinstance(x, (int, float)) and 1990 <= x <= 2030}
    title = " ".join(str(x) for r in rows[:hdr_i] for x in r if x)
    out = {}
    for r in rows[hdr_i + 1:]:
        if not r or not isinstance(r[0], str):
            continue
        vals = [(years[j], r[j]) for j in sorted(years) if j < len(r) and isinstance(r[j], (int, float))]
        if vals:
            name = r[0]
            # «Архангельская область без автономного округа» — это код 29 (НАО — отдельный субъект, код 83)
            if re.search(r"\sбез\s+автономн", name):
                out[norm(re.sub(r"\s+без\s+автономн.*$", "", name))] = vals[-1]; continue
            out.setdefault(norm(name), vals[-1])                 # первое вхождение (без дублей «в т.ч.»)
    return out, re.sub(r"\s+", " ", title)


def spearman(x, y):
    rx, ry = pd.Series(x).rank().values, pd.Series(y).rank().values
    return float(np.corrcoef(rx, ry)[0, 1])


def _rowcorr(a, b):
    a = a - a.mean(axis=1, keepdims=True); b = b - b.mean(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (a * b).sum(axis=1) / np.sqrt((a * a).sum(axis=1) * (b * b).sum(axis=1))


def boot_perm(x, y, n_boot, rng):
    """Бутстреп-ДИ и перестановочный p для rho Спирмена (векторно, ранги со средними при совпадениях)."""
    n = len(x)
    idx = rng.integers(0, n, (n_boot, n))
    rb = _rowcorr(pd.DataFrame(x[idx]).rank(axis=1).values, pd.DataFrame(y[idx]).rank(axis=1).values)
    rb = rb[np.isfinite(rb)]
    rx, ry = pd.Series(x).rank().values, pd.Series(y).rank().values
    perm = np.argsort(rng.random((n_boot, n)), axis=1)
    rp = _rowcorr(np.broadcast_to(rx, (n_boot, n)), ry[perm])
    return rb, rp


def main(n_boot: int = 10000, seed: int = 20260920):
    OUT.mkdir(parents=True, exist_ok=True)
    sd = json.loads((SRC / "site" / "site_data.json").read_text(encoding="utf-8"))["regions"]
    df = pd.DataFrame([{"code": r["code"], "region": r["name"], "list": r["list"], "ballots": r["ballots"],
                        "first_hour": r["dyn"].get("2026", {}).get("first_hour"),
                        "uik100": 100 * r["uik100"] / max(1, r["uik50"]), "removed": 100 * r["removed"] / r["init"],
                        "unpaired": 100 * (1 - r["votes_all"] / r["ballots_all"])} for r in sd])
    df["turnout"] = 100 * df.ballots / df.list
    pl = pd.read_csv(SRC / "deg_results" / "pl_by_region.csv")
    COLS = ["ЕР", "ЛДПР", "ППД", "Зелёные", "СРЗП", "Родина", "КПРФ", "Пенсионеры", "Коммунисты России", "Новые люди"]
    pl["er"] = 100 * pl["ЕР"] / pl[COLS].sum(axis=1)
    df = df.merge(pl[["rc", "er"]], left_on="code", right_on="rc", how="left").drop(columns="rc")
    meta, miss = {}, {}
    key = df.region.map(norm)
    for k, f, sh, label, unit in ROSSTAT:
        d, title = read_sheet(XLSX / f, sh)
        got = key.map(lambda n: d.get(n))
        miss[k] = df.region[got.isna()].tolist()
        df[k] = got.map(lambda t: t[1] if t else np.nan)
        yrs = sorted({t[0] for t in got.dropna()})
        meta[k] = dict(label=label, unit=unit, table=sh, file=f, years=yrs, title=title[:200])
    df["uptake"] = 100 * df["list"] / (1000 * df["pop"])
    meta["uptake"] = dict(label="Охват ДЭГ", unit="записавшихся на 100 жителей", note="список ДЭГ ÷ численность населения (Росстат, 1.1)")

    rng = np.random.default_rng(seed)
    res = []
    n = len(df)
    for yk, ylab in DEG:
        for xk in X_KEYS:
            m = df[[xk, yk]].dropna()
            x, y = m[xk].values, m[yk].values
            rho = spearman(x, y)
            bs, perm = boot_perm(x, y, n_boot, rng)
            p = (np.sum(np.abs(perm) >= abs(rho) - 1e-12) + 1) / (n_boot + 1)
            res.append(dict(y=yk, y_label=ylab, x=xk, x_label=meta[xk]["label"], n=len(x), rho=round(rho, 3),
                            ci_lo=round(float(np.percentile(bs, 2.5)), 3), ci_hi=round(float(np.percentile(bs, 97.5)), 3),
                            p=round(float(p), 4)))
    cor = pd.DataFrame(res).sort_values("p")
    m = len(cor)                                                  # поправка Холма
    adj = np.maximum.accumulate([(m - i) * p for i, p in enumerate(cor.p)])
    cor["p_holm"] = np.minimum(1, adj).round(4)
    cor["significant"] = cor.p_holm < 0.05
    cor = cor.sort_values(["y", "x"])

    for d, name in ((df, "indicators.csv"), (cor, "correlations.csv")):
        tmp = OUT / f"{name}.tmp"; d.to_csv(tmp, index=False, encoding="utf-8"); os.replace(tmp, OUT / name)
    rep = dict(n_regions=n, missing=miss, meta=meta, significant=cor[cor.significant].to_dict("records"),
               strongest=cor.reindex(cor.rho.abs().sort_values(ascending=False).index).head(10).to_dict("records"))
    (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    site = dict(meta={k: meta[k] for k in X_KEYS + ["uptake"]}, deg=DEG, x=X_KEYS,
                rows=df[["code", "region"] + X_KEYS + [k for k, _ in DEG]].round(3).to_dict("records"),
                cor=cor.to_dict("records"))
    p = config.DERIVED / "site" / "social.json"; p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)
    print("не найдено в Росстате:", {k: v for k, v in miss.items() if v})
    print(cor.reindex(cor.rho.abs().sort_values(ascending=False).index).head(15).to_string(index=False))


if __name__ == "__main__":
    main()
