"""Метаданные голосований с открытого портала наблюдения ПТК ДЭГ (stat.vybory.gov.ru/api).

Запуск (Windows, нужен российский IP):  run.cmd portal
Повторный запуск бесплатный: каждый ответ API кешируется в data/external/portal/raw/ как есть
(первичный источник — прикладывается к курсовой как доказательная база, с датой получения).

Зачем. В самой цепочке у голосования есть только bulletinHash — ни названия выборов, ни округа,
ни кандидатов. Их публикует портал. Цепочка API (порядок обхода взят из открытого кода
С. Слотина, github.com/sslotin/elections, feddeg/parser/parse.py — ссылка обязательна):
    voting/regions                                   → регионы
    elections/region/{code}/elections                → выборы региона (по уровням)
    elections/districts?regionCode=&electionId=      → округа выборов
    statistics/voting?electionId=&districtId=        → голосования округа: contractId + счётчики портала
    results/voting/{contractId}                      → итоги с именами кандидатов/партий

Выход (data/derived/portal/):
    regions.parquet     region_code, name, description
    contracts.parquet   contract_id, region_code, region_name, election_id, election_name, level,
                        district_id, district_name, counters_json, voting_json
    results.parquet     contract_id, position, name, value
    _summary.json       сколько чего получено, сколько запросов, ошибки
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from . import config

API = "https://stat.vybory.gov.ru/api"
HEADERS = {"User-Agent": "deg2026-research (MSU sociology coursework; contact via repository)",
           "Accept": "application/json"}


class Portal:
    """Вежливый клиент: кеш на диске, ограниченное число потоков, пауза между запросами, повторы."""

    def __init__(self, raw_dir: Path, workers: int, delay: float, base: str = API):
        self.raw = raw_dir; self.raw.mkdir(parents=True, exist_ok=True)
        self.base = base.rstrip("/"); self.delay = delay
        self.local = threading.local()
        self.pool = ThreadPoolExecutor(workers)
        self.calls = 0; self.hits = 0; self.errors: list[str] = []
        self.lock = threading.Lock()

    def _session(self):
        if not hasattr(self.local, "s"):
            self.local.s = requests.Session(); self.local.s.headers.update(HEADERS)
        return self.local.s

    def _file(self, path: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-." else "_" for c in path)
        return self.raw / f"{safe[:180]}.json"

    def get(self, path: str, attempts: int = 5):
        f = self._file(path)
        if f.exists():
            with self.lock: self.hits += 1
            return json.loads(f.read_text(encoding="utf-8"))["body"]
        err = None
        for i in range(attempts):
            try:
                time.sleep(self.delay)
                r = self._session().get(f"{self.base}/{path}", timeout=60)
                if r.status_code == 200:
                    body = r.json()
                    rec = {"url": f"{self.base}/{path}", "fetched_utc": datetime.now(timezone.utc).isoformat(),
                           "status": r.status_code, "body": body}
                    tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(f)
                    with self.lock: self.calls += 1
                    return body
                err = f"HTTP {r.status_code}"
                if r.status_code in (403, 451):          # доступ закрыт — повторять бессмысленно
                    break
            except requests.RequestException as e:
                err = repr(e)
            time.sleep(2 * (i + 1))
        with self.lock: self.errors.append(f"{path}: {err}")
        return None

    def many(self, paths):
        return list(self.pool.map(self.get, paths))


def collect(p: Portal):
    reg = p.get("voting/regions")
    if not reg:
        raise SystemExit("Портал недоступен (см. ошибки). Нужен российский IP / без VPN.")
    regions = reg["data"]["regions"]
    print(f"регионов: {len(regions)}", flush=True)
    region_rows = [{"region_code": str(r["code"]), "name": r.get("name"), "description": r.get("description")}
                   for r in regions]

    el_payloads = p.many([f"elections/region/{r['code']}/elections" for r in regions])
    dpaths, dmeta = [], []
    for r, pay in zip(regions, el_payloads):
        if not pay: continue
        for level in pay["data"]["elections"]:
            for e in level["elections"]:
                dpaths.append(f"elections/districts?regionCode={r['code']}&electionId={e['electionId']}")
                dmeta.append((str(r["code"]), e["electionId"], e.get("name") or e.get("electionName"),
                              level.get("level") or level.get("name") or level.get("type")))
    print(f"выборов: {len(dpaths)}", flush=True)

    vpaths, vmeta = [], []
    for (rc, eid, ename, lvl), pay in zip(dmeta, p.many(dpaths)):
        if not pay: continue
        for d in pay["data"]:
            vpaths.append(f"statistics/voting?electionId={eid}&districtId={d['id']}")
            vmeta.append((rc, eid, ename, lvl, d["id"], d.get("name")))
    print(f"округов: {len(vpaths)}", flush=True)

    names = {r["region_code"]: r["description"] or r["name"] for r in region_rows}
    contract_rows = []
    for (rc, eid, ename, lvl, did, dname), pay in zip(vmeta, p.many(vpaths)):
        if not pay: continue
        data = pay["data"]
        for v in data.get("votings") or []:
            cnt = v.get("counters") or {}
            contract_rows.append(dict(
                contract_id=cnt.get("contractId"), region_code=str(data.get("regionCode", rc)),
                region_name=names.get(str(data.get("regionCode", rc))) or data.get("regionName"),
                election_id=str(eid), election_name=data.get("electionName") or ename, level=str(lvl),
                district_id=str(did), district_name=data.get("districtName") or dname,
                counters_json=json.dumps(cnt, ensure_ascii=False),
                voting_json=json.dumps({k: v2 for k, v2 in v.items() if k != "counters"}, ensure_ascii=False)))
    print(f"голосований (контрактов): {len(contract_rows)}", flush=True)

    ids = sorted({c["contract_id"] for c in contract_rows if c["contract_id"]})
    res_rows = []
    for cid, pay in zip(ids, p.many([f"results/voting/{c}" for c in ids])):
        for i, a in enumerate(((pay or {}).get("data") or {}).get("answers") or []):
            res_rows.append(dict(contract_id=cid, position=i, name=a.get("name"),
                                 value=int(a["value"]) if str(a.get("value", "")).lstrip("-").isdigit() else None))
    print(f"строк итогов: {len(res_rows)}", flush=True)
    return region_rows, contract_rows, res_rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2, help="пауза перед каждым запросом, с")
    ap.add_argument("--base", default=API)
    a = ap.parse_args(argv)
    t0 = time.time()
    p = Portal(config.EXTERNAL / "portal" / "raw", a.workers, a.delay, a.base)
    regions, contracts, results = collect(p)
    out = config.DERIVED / "portal"; out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("regions", regions), ("contracts", contracts), ("results", results)):
        if rows:
            pq.write_table(pa.Table.from_pylist(rows), out / f"{name}.parquet")
    summ = {"regions": len(regions), "contracts": len(contracts), "result_rows": len(results),
            "api_calls": p.calls, "cache_hits": p.hits, "errors": len(p.errors), "error_list": p.errors[:200],
            "minutes": round((time.time() - t0) / 60, 1)}
    (out / "_summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summ.items() if k != "error_list"}, ensure_ascii=False))
    if p.errors:
        print("Первые ошибки:", *p.errors[:10], sep="\n  ")
    return 1 if p.errors else 0


if __name__ == "__main__":
    sys.exit(main())
