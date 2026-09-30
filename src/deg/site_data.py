"""Данные для сайта (этап 7): агрегаты по регионам и УИК → data/derived/site/site_data.json.

Запуск: run.cmd site_data   (или python -m deg.site_data --memory 1.5GB на слабой машине)
Только агрегаты: никаких user_id / хэшей списков (решение о приватности, docs/decisions.md).
Единый метод УИК — «представительный контракт» (см. dynamics.py), чтобы числа совпадали с таблицей динамики.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import duckdb

from . import config

OUT = config.DERIVED / "site"


def build(memory: str, threads: int) -> dict:
    con = duckdb.connect(str(config.DERIVED / "deg2026.duckdb"), read_only=True)
    con.execute(f"SET memory_limit='{memory}'; SET threads={threads}; SET preserve_insertion_order=false")
    pr = (config.DERIVED / "portal" / "regions.parquet").as_posix()
    pc = (config.DERIVED / "portal" / "contracts.parquet").as_posix()
    con.execute(f"CREATE TEMP TABLE RN AS SELECT region_code::VARCHAR rc, description region FROM read_parquet('{pr}')")
    con.execute("""CREATE TEMP TABLE CR AS SELECT contract_id, mode(region_code) rc, count(*) nb
                   FROM ballots GROUP BY 1""")
    # представительный контракт УИК
    con.execute("""CREATE TEMP TABLE REP AS SELECT rc, uik, contract_id cid, n FROM
                   (SELECT region_code rc, uik, contract_id, count(*) n FROM ballots GROUP BY ALL)
                   QUALIFY row_number() OVER (PARTITION BY rc, uik ORDER BY n DESC, contract_id) = 1""")
    con.execute("""CREATE TEMP TABLE LU AS SELECT r.rc, r.uik, r.n,
                     sum(CASE WHEN l.op='addVotersList' THEN l.n ELSE 0 END) init,
                     sum(CASE WHEN l.op='addToVotersList' THEN l.n ELSE 0 END) added,
                     sum(CASE WHEN l.op='removeFromVotersList' THEN l.n ELSE 0 END) removed
                   FROM REP r JOIN list_events l ON l.contract_id = r.cid AND l.uik = r.uik GROUP BY ALL""")
    con.execute("CREATE TEMP TABLE LU2 AS SELECT *, init + added - removed lst FROM LU")

    regions = {}
    for rc, region, nc, nb in con.execute(
            "SELECT RN.rc, region, count(*), sum(nb) FROM CR JOIN RN ON RN.rc = CR.rc GROUP BY ALL").fetchall():
        regions[rc] = {"code": int(rc), "name": region, "contracts": nc, "ballots_all": int(nb)}

    # портал: сколько контрактов/бюллетеней видно
    con.execute(f"CREATE TEMP TABLE PV AS SELECT DISTINCT contract_id FROM read_parquet('{pc}')")
    for rc, nc, nb in con.execute("""SELECT CR.rc, count(*), sum(nb) FROM CR JOIN PV USING (contract_id) GROUP BY 1""").fetchall():
        if rc in regions:
            regions[rc]["portal_contracts"] = nc
            regions[rc]["portal_ballots"] = int(nb)

    # УИК: список, бюллетени, исключения, распределение явки
    for rc, nu, lst, n, init, rem, u50, u100 in con.execute("""SELECT rc, count(*), sum(lst), sum(n), sum(init), sum(removed),
            count(*) FILTER (WHERE lst >= 50), count(*) FILTER (WHERE lst >= 50 AND n >= lst) FROM LU2 GROUP BY 1""").fetchall():
        if rc in regions:
            regions[rc].update(uiks=nu, list=int(lst), ballots=int(n), init=int(init), removed=int(rem),
                               uik50=u50, uik100=u100)
    for rc, b, k in con.execute("""SELECT rc, least(floor(100.0 * n / lst / 5), 20)::INT b, count(*)
                                   FROM LU2 WHERE lst >= 50 GROUP BY ALL""").fetchall():
        if rc in regions:
            regions[rc].setdefault("hist", [0] * 21)[b] = k
    # УИК с 100 % и верх по явке (номер УИК — публичный номер комиссии)
    top = {}
    for rc, uik, lst, n in con.execute("""SELECT rc, uik, lst, n FROM LU2 WHERE lst >= 50
            QUALIFY row_number() OVER (PARTITION BY rc ORDER BY n / lst DESC, lst DESC, uik) <= 8
            ORDER BY rc, n / lst DESC, lst DESC, uik""").fetchall():   # uik — однозначный порядок при равенстве
        top.setdefault(rc, []).append([int(uik), int(lst), int(n)])
    for rc, v in top.items():
        if rc in regions:
            regions[rc]["top_uiks"] = v

    # почасовая кривая (МСК, час от 18.09 00:00) и сдвиг местного времени
    # floor, а не //: в DuckDB // над DOUBLE даёт дробь, и ::INT округлял её — часы сдвигались на полчаса (сверка 30.09)
    for rc, toff in con.execute("SELECT region_code, (8 - hour(min(ts_msk_s)))::INT FROM ballots GROUP BY 1").fetchall():
        if rc in regions:
            regions[rc]["toff"] = toff
    for rc, h, k in con.execute("""SELECT region_code, floor(epoch(ts_msk_s - TIMESTAMP '2026-09-18 00:00:00') / 3600)::INT h, count(*)
                                   FROM ballots GROUP BY ALL""").fetchall():
        if rc in regions and 0 <= h < 72:
            regions[rc].setdefault("hourly", [0] * 72)[h] = k
    # голоса без пары (все контракты региона)
    for rc, nv in con.execute("SELECT CR.rc, count(*) FROM votes v JOIN CR USING (contract_id) GROUP BY 1").fetchall():
        if rc in regions:
            regions[rc]["votes_all"] = int(nv)

    # динамика из dynamics (единый метод)
    dyn = {}
    with open(config.DERIVED / "dynamics" / "regions_by_year.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["source"] != "ours":
                continue
            dyn.setdefault(r["region"], {})[r["year"]] = {k: float(r[k]) for k in
                ("list", "ballots", "turnout", "first_hour", "uik50", "uik100", "unpaired")}
    for reg in regions.values():
        reg["dyn"] = dyn.get(reg["name"], {})
    return {"regions": sorted(regions.values(), key=lambda r: r["name"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", default="4GB")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    data = build(a.memory, a.threads)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "site_data.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, OUT / "site_data.json")
    r = data["regions"]
    print(f"регионов: {len(r)}; бюллетеней (все контракты): {sum(x['ballots_all'] for x in r):,}; "
          f"список (предст.): {sum(x.get('list', 0) for x in r):,}")


if __name__ == "__main__":
    main()
