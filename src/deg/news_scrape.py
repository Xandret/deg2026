"""Сбор публичных постов Telegram-каналов (веб-превью t.me/s/) за период выборов → кандидаты в хронологию.

Запуск: run.cmd news_scrape [--channels a,b] [--since 2026-09-10] [--until 2026-09-24] [--time-budget 160]
        run.cmd news_scrape --candidates        # только отбор кандидатов по ключевым словам из уже собранного
Решение №27 (docs/decisions.md): источники без «нежелательных организаций»; «Голос» — с пометкой «иноагент».
Только открытые страницы t.me/s (то, что видно любому без входа), не чаще 1 запроса в секунду, с возобновлением:
состояние каждого канала — в data/external/news/<канал>.state.json, посты — в <канал>.jsonl.
Хронология собирается вручную из кандидатов (data/news/timeline.json) — автоматический отбор только помогает.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import config

CHANNELS = {
    "cikrossii": ("ЦИК России", "официальный"),
    "mintsifry": ("Минцифры России", "официальный"),
    "gosuslugi": ("Госуслуги", "официальный"),
    "oprf_official": ("Общественная палата РФ", "официальный"),
    "tass_agency": ("ТАСС", "СМИ"),
    "rbc_news": ("РБК", "СМИ"),
    "kommersant": ("Коммерсантъ", "СМИ"),
    "vedomosti": ("Ведомости", "СМИ"),
    "interfaxonline": ("Интерфакс", "СМИ"),
    "gazetaru": ("Газета.Ru", "СМИ"),
    "mk_ru": ("Московский комсомолец", "СМИ"),
    "rtvimain": ("RTVI", "СМИ"),
    "fontankaspb": ("Фонтанка", "региональные СМИ"),
    "belpressa": ("БелПресса", "региональные СМИ"),
    "stolicaonego": ("Столица на Онего", "региональные СМИ"),
    "zanashepravo": ("Защищаем наше право (В. Толстогузов)", "наблюдатели"),
    "golosinfo": ("Движение «Голос» (признано иноагентом)", "наблюдатели"),
}
OUT = config.EXTERNAL / "news"
MSK = timezone(timedelta(hours=3))
KEY = re.compile(r"ДЭГ|дистанционн\w* электронн|электронн\w* голосовани|онлайн-голосовани|блокчейн|"
                 r"(Госуслуг\w*).{0,60}(выбор|голос)|(выбор|голос).{0,60}Госуслуг|ЦИК|избирком|Памфилов|Артамонов|"
                 r"наблюдател|явк\w|бюллетен|Ровень|Магадан\w*.{0,40}(голос|выбор)", re.I | re.S)
UA = {"User-Agent": "Mozilla/5.0 (deg2026 research; low-rate)"}


def parse_page(html: str, channel: str):
    soup = BeautifulSoup(html, "lxml")
    posts = []
    for w in soup.select("div.tgme_widget_message"):
        dp = w.get("data-post", "")
        if "/" not in dp:
            continue
        pid = int(dp.split("/")[1])
        t = w.select_one("time[datetime]")
        txt = w.select_one("div.tgme_widget_message_text")
        views = w.select_one("span.tgme_widget_message_views")
        fwd = w.select_one("a.tgme_widget_message_forwarded_from_name")
        posts.append(dict(channel=channel, id=pid, url=f"https://t.me/{channel}/{pid}",
                          dt=t["datetime"] if t else None,
                          text=txt.get_text("\n", strip=True) if txt else "",
                          views=views.get_text(strip=True) if views else None,
                          forwarded_from=fwd.get_text(strip=True) if fwd else None))
    return posts


def scrape(channel, since, until, budget, t0):
    OUT.mkdir(parents=True, exist_ok=True)
    st_path, data_path = OUT / f"{channel}.state.json", OUT / f"{channel}.jsonl"
    st = json.loads(st_path.read_text(encoding="utf-8")) if st_path.exists() else {"before": None, "done": False}
    if st["done"]:
        return True
    seen = set()
    if data_path.exists():
        seen = {json.loads(l)["id"] for l in data_path.open(encoding="utf-8")}
    with data_path.open("a", encoding="utf-8") as out:
        while time.time() - t0 < budget:
            url = f"https://t.me/s/{channel}" + (f"?before={st['before']}" if st["before"] else "")
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code != 200:
                print(f"{channel}: HTTP {r.status_code}, пауза"); time.sleep(10); continue
            posts = parse_page(r.text, channel)
            if not posts:
                st["done"] = True; break
            for p in posts:
                if p["id"] in seen or not p["dt"]:
                    continue
                d = datetime.fromisoformat(p["dt"]).astimezone(MSK)
                if since <= d.date() <= until:
                    out.write(json.dumps(p, ensure_ascii=False) + "\n"); seen.add(p["id"])
            st["before"] = min(p["id"] for p in posts)
            oldest = min(datetime.fromisoformat(p["dt"]).astimezone(MSK).date() for p in posts if p["dt"])
            if oldest < since or st["before"] <= 1:
                st["done"] = True; break
            st_path.write_text(json.dumps(st), encoding="utf-8")
            time.sleep(1.1)
    st_path.write_text(json.dumps(st), encoding="utf-8")
    print(f"{channel}: {'готово' if st['done'] else 'продолжить'}; постов в окне: {len(seen)}", flush=True)
    return st["done"]


def candidates():
    rows = []
    for ch, (name, kind) in CHANNELS.items():
        p = OUT / f"{ch}.jsonl"
        if not p.exists():
            continue
        for l in p.open(encoding="utf-8"):
            x = json.loads(l)
            if KEY.search(x["text"]):
                d = datetime.fromisoformat(x["dt"]).astimezone(MSK)
                rows.append(dict(dt_msk=d.strftime("%Y-%m-%d %H:%M"), channel=ch, source=name, kind=kind,
                                 url=x["url"], views=x["views"], text=x["text"][:600].replace("\n", " ")))
    rows.sort(key=lambda r: r["dt_msk"])
    dst = config.DERIVED / "news"; dst.mkdir(parents=True, exist_ok=True)
    with open(dst / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["dt_msk"]); w.writeheader(); w.writerows(rows)
    print(f"кандидатов: {len(rows)} → {dst / 'candidates.csv'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default=",".join(CHANNELS))
    ap.add_argument("--since", default="2026-09-10")
    ap.add_argument("--until", default="2026-09-24")
    ap.add_argument("--time-budget", type=float, default=1e9)
    ap.add_argument("--candidates", action="store_true")
    a = ap.parse_args()
    if not a.candidates:
        since, until = (datetime.strptime(x, "%Y-%m-%d").date() for x in (a.since, a.until))
        t0 = time.time()
        for ch in a.channels.split(","):
            if time.time() - t0 >= a.time_budget:
                print("бюджет времени исчерпан — запустите ещё раз"); break
            scrape(ch, since, until, a.time_budget, t0)
    candidates()


if __name__ == "__main__":
    main()
