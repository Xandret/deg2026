"""Скачивание публичных сырых дампов ДЭГ прошлых лет с Яндекс Диска (для собственного парсинга).

Запуск: run.cmd fetch_yadisk --year 2024      (или 2025)
Файлы кладутся в data/external/raw_dumps/, докачка поддерживается (HTTP Range), по окончании печатается SHA-256.
Ссылки — из README data/feddeg-data репозитория С. Слотина.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time

import requests

from . import config

LINKS = {2024: "https://disk.yandex.ru/d/dP_9pMwBeh1yeQ",      # edg2024.zip, 4 634 698 243 байт
         2025: "https://disk.yandex.ru/d/7NBxGthNRmb0Cg"}      # edg2025.zip, 10 274 014 628 байт
API = "https://cloud-api.yandex.net/v1/disk/public/resources"


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--year", type=int, required=True, choices=sorted(LINKS))
    a = ap.parse_args(argv)
    meta = requests.get(API, params={"public_key": LINKS[a.year]}, timeout=60).json()
    name, size = meta["name"], int(meta["size"])
    out = config.EXTERNAL / "raw_dumps"; out.mkdir(parents=True, exist_ok=True)
    path = out / name
    have = path.stat().st_size if path.exists() else 0
    print(f"{name}: {size:,} байт; уже есть {have:,}")
    if have < size:
        href = requests.get(API + "/download", params={"public_key": LINKS[a.year]}, timeout=60).json()["href"]
        headers = {"Range": f"bytes={have}-"} if have else {}
        with requests.get(href, headers=headers, stream=True, timeout=120) as r, open(path, "ab") as f:
            r.raise_for_status()
            t0, done = time.time(), have
            for chunk in r.iter_content(8 << 20):
                f.write(chunk); done += len(chunk)
                el = time.time() - t0
                print(f"\r{done/size:6.1%}  {(done-have)/max(el,1)/2**20:6.1f} МБ/с", end="", flush=True)
        print()
    if path.stat().st_size != size:
        print("Размер не совпал — запустите ещё раз для докачки."); return 1
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(16 << 20), b""): h.update(b)
    print("SHA-256:", h.hexdigest())
    (out / f"{name}.sha256").write_text(h.hexdigest() + "  " + name + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
