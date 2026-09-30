"""Партийная принадлежность кандидатов одномандатных округов Госдумы → data/derived/deg_results/sm_parties.csv.

Запуск: run.cmd candidate_parties        (после deg_results и izbirkom_snapshot)
Источник: регистрационные записи кандидатов из снимка izbirkom (candidates.jsonl: fullName, districtNum,
electionAssociation — партия-выдвиженец или «самовыдвижение»). Сопоставление: ФИО (без учёта регистра и «ё»)
+ номер округа (номер ОИК из дерева комиссий); если по номеру не нашлось — только по ФИО, если оно однозначно.
Дата рождения и другие личные поля из снимка не используются и никуда не выводятся.
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict

import pandas as pd

from . import config
from .izbirkom_snapshot import norm

SNAP = config.EXTERNAL / "izbirkom_snapshot"
OUT = config.DERIVED / "deg_results" / "sm_parties.csv"

SHORT = [("КОММУНИСТЫ РОССИИ", "Коммунисты России", "КР"), ("ЕДИНАЯ РОССИЯ", "Единая Россия", "ЕР"), ("ЛДПР", "ЛДПР", "ЛДПР"),
         ("СПРАВЕДЛИВАЯ РОССИЯ", "Справедливая Россия — За правду", "СРЗП"), ("КПРФ", "КПРФ", "КПРФ"),
         ("НОВЫЕ ЛЮДИ", "Новые люди", "НЛ"), ("ПЕНСИОНЕР", "Партия пенсионеров", "ПП"), ("РОДИНА", "Родина", "Родина"),
         ("ЗЕЛЁНЫЕ", "Зелёные", "Зелёные"), ("ЗЕЛЕНЫЕ", "Зелёные", "Зелёные"), ("ЯБЛОКО", "Яблоко", "Яблоко"),
         ("ПРЯМОЙ ДЕМОКРАТИИ", "Партия прямой демократии", "ППД")]


def party(assoc: str):
    if not assoc or assoc.strip().lower() == "самовыдвижение":
        return "самовыдвижение", "самовыдв."
    u = assoc.upper()
    for k, full, short in SHORT:
        if k in u:
            return full, short
    return assoc.strip(), assoc.strip()


def key(name: str) -> str:
    n = re.sub(r"\s*\(.*?\)\s*", " ", name)            # «БЕЛОВА (ТЕН) Ольга» → «БЕЛОВА Ольга»
    return re.sub(r"\s+", " ", n.upper().replace("Ё", "Е")).strip()


def main():
    cands = [json.loads(l) for l in open(SNAP / "candidates.jsonl", encoding="utf-8")]
    by_nd, by_n = {}, defaultdict(set)
    for c in cands:
        k = key(c["fullName"])
        by_n[k].add(c.get("electionAssociation") or "")
        if int(c.get("districtNum") or 0) > 0:
            by_nd[(k, int(c["districtNum"]))] = c.get("electionAssociation") or ""
    com = pd.read_parquet(config.DERIVED / "izbirkom_snapshot" / "commissions.parquet")
    oik = {norm(n): int(num) for n, num, t in zip(com.name, com.number, com.type) if t == 3}
    rows, miss = [], []
    for r in csv.DictReader(open(config.DERIVED / "deg_results" / "sm_by_district.csv", encoding="utf-8")):
        k = key(r["candidate"]); num = oik.get(norm(r["district"]))
        assoc = by_nd.get((k, num)) if num else None
        how = "фио+округ"
        if assoc is None:
            s = by_n.get(k, set())
            assoc, how = (next(iter(s)), "фио") if len(s) == 1 else (None, "не найдено")
        full, short = party(assoc) if assoc is not None else ("", "")
        if assoc is None:
            miss.append(r["candidate"])
        rows.append(dict(district=r["district"], district_num=num, candidate=r["candidate"], party=full, party_short=short, match=how))
    tmp = OUT.with_suffix(".tmp"); pd.DataFrame(rows).to_csv(tmp, index=False, encoding="utf-8"); os.replace(tmp, OUT)
    d = pd.DataFrame(rows)
    print(d.match.value_counts().to_string()); print("не найдено:", miss)
    print(d.party_short.value_counts().to_string())


if __name__ == "__main__":
    main()
