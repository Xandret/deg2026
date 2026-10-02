"""Итоги ДЭГ по выборам в Госдуму для всех 32 регионов → data/derived/deg_results/.

Запуск: run.cmd deg_results
Идея (docs/tech/06-deg-results.md):
  * Все 2 088 контрактов ГД (162 видны на портале, 1 926 — нет) разбиваются на пары с одинаковым набором УИК:
    каждая пара — одна ТИК, в ней партсписок (PL) и одномандатный округ (SM). Пар 1 044, непарных нет.
  * PL/SM: у видимых — по названиям на портале; у невидимых — по размерности бюллетеня (партий 10);
    если в паре обе размерности = 10 (Вологодская, Калининградская, Липецкая) — PL тот, чьё
    распределение голосов ближе к видимым PL региона (разрыв на порядок: ~0,1 против ~1,1).
  * SM → округ: видимый контракт — по порталу; невидимый — по размерности, если в регионе она у округов
    уникальна (точно); иначе по номерам УИК контракта и дереву комиссий из снимка izbirkom (точно, how='uik');
    если и этого нет — по близости распределения голосов к видимому контракту округа (реконструкция,
    помечается how='sim', нуждается в проверке по протоколам ОИК).
Выход: duma_contracts.csv, pl_by_region.csv, pl_by_tik.csv, sm_by_district.csv. Только итоги голосований
(публичные), без данных об избирателях.
"""
from __future__ import annotations

import json
import os

import duckdb
import numpy as np
import pandas as pd

from . import config

PARTIES = ["ЕР", "ЛДПР", "ППД", "Зелёные", "СРЗП", "Родина", "КПРФ", "Пенсионеры", "Коммунисты России", "Новые люди"]
OUT = config.DERIVED / "deg_results"


def share(v):
    v = np.asarray(v, float)
    return v / max(1.0, v.sum())


def uik_to_oik() -> dict:
    """(код субъекта, номер УИК) → название ОИК по дереву комиссий выборов в Госдуму из открытого снимка izbirkom
    (data/external/izbirkom_snapshot/commissions.jsonl: у каждой записи — дети с parentId). Нет снимка — пустой словарь."""
    f = config.EXTERNAL / "izbirkom_snapshot" / "commissions.jsonl"
    if not f.exists():
        return {}
    node = {}
    for line in open(f, encoding="utf-8", errors="replace"):
        rec = json.loads(line)
        rc = rec.get("_subjectRfCode")
        for n in [rec, *(rec.get("children") or [])]:
            v = node.setdefault(n["id"], {})
            for k in ("parentId", "type", "number", "name"):
                if n.get(k) is not None:
                    v[k] = n[k]
            if rc is not None:
                v.setdefault("rc", str(rc))

    def oik(i):
        for _ in range(6):
            n = node.get(i)
            if not n:
                return None
            if n.get("type") == 3:
                return n.get("name")
            i = n.get("parentId")
        return None

    return {(n["rc"], int(n["number"])): o for i, n in node.items()
            if n.get("type") == 5 and n.get("number") is not None and "rc" in n and (o := oik(i))}


def build():
    con = duckdb.connect(str(config.DERIVED / "deg2026.duckdb"), read_only=True)
    con.execute("SET memory_limit='3GB'")
    pc = pd.read_parquet(config.DERIVED / "portal" / "contracts.parquet")
    pr = pd.read_parquet(config.DERIVED / "portal" / "results.parquet")
    rn = pd.read_parquet(config.DERIVED / "portal" / "regions.parquet")
    names = dict(zip(rn.region_code.astype(str), rn.description))
    dims = con.sql("SELECT contract_id, any_value(dimension) dim FROM contracts GROUP BY 1").df()
    res = con.sql("SELECT contract_id, payload FROM contract_events WHERE op = 'results'").df()
    res["vec"] = res.payload.map(lambda p: json.loads(json.loads(p)["results"]["RESULTS"])[0])
    sig = con.sql("""SELECT contract_id, mode(region_code) rc, count(*) ballots,
                        md5(string_agg(DISTINCT uik::VARCHAR, ',' ORDER BY uik::VARCHAR)) sig,
                        min(uik) uik_min, max(uik) uik_max, count(DISTINCT uik) uiks
                     FROM ballots GROUP BY 1 ORDER BY 1""").df()
    d = (sig.merge(dims, on="contract_id").merge(res[["contract_id", "vec"]], on="contract_id")
         .merge(pc[["contract_id", "level", "district_name"]], on="contract_id", how="left"))
    d = d[(d.level == "FEDERAL") | d.level.isna()].copy()
    d["visible"] = d.level.notna()
    first = pr[pr.position == 0].set_index("contract_id").name
    d["vis_kind"] = d.contract_id.map(first).map(
        lambda s: None if pd.isna(s) else ("PL" if s.startswith("1.") and "ЕДИНАЯ РОССИЯ" in s else "SM"))

    sizes = d.groupby(["rc", "sig"]).size()
    assert (sizes == 2).all(), "ожидались пары PL+SM на каждую ТИК"
    plref = {rc: np.mean([share(v) for v in g.vec], axis=0) for rc, g in d[d.vis_kind == "PL"].groupby("rc")}
    kind, how_k = {}, {}
    for (rc, _), g in d.groupby(["rc", "sig"]):
        a, b = g.iloc[0], g.iloc[1]
        va, vb = (v if isinstance(v, str) else None for v in (a.vis_kind, b.vis_kind))   # NaN в pandas 3 — истина
        if va or vb:
            x, y, xv = (a, b, va) if va else (b, a, vb)
            kind[x.contract_id], kind[y.contract_id] = xv, ("SM" if xv == "PL" else "PL")
            how_k[a.contract_id] = how_k[b.contract_id] = "portal"
        elif a.dim != b.dim:
            ka = "PL" if a.dim == "[[1,1,10]]" else "SM"
            kind[a.contract_id], kind[b.contract_id] = ka, ("SM" if ka == "PL" else "PL")
            how_k[a.contract_id] = how_k[b.contract_id] = "dimension"
        else:
            da, db = (np.abs(share(v) - plref[rc]).sum() for v in (a.vec, b.vec))
            kind[a.contract_id], kind[b.contract_id] = ("PL", "SM") if da < db else ("SM", "PL")
            how_k[a.contract_id] = how_k[b.contract_id] = f"similarity {min(da, db):.2f}/{max(da, db):.2f}"
    d["kind"], d["kind_how"] = d.contract_id.map(kind), d.contract_id.map(how_k)
    d["tik_pair"] = d.groupby(["rc", "sig"]).ngroup()

    # одномандатные округа
    sm = d[d.kind == "SM"]
    vis = sm[sm.visible]
    # По номерам УИК: ОИК, к которой по дереву комиссий izbirkom относятся УИК, выдавшие бюллетени контракта.
    # Точнее сходства результатов: 02.10 сходство ошиблось в 4 контрактах Московской обл. (обмен между округами).
    u2o = uik_to_oik()
    by_uik = {}
    if u2o:
        hid = "','".join(sm[~sm.visible].contract_id)
        cu = con.sql(f"""SELECT contract_id, region_code::VARCHAR rc, uik, count(*) n FROM ballots
                         WHERE contract_id IN ('{hid}') GROUP BY ALL""").df()
        cu["oik"] = [u2o.get((rc, int(u))) for rc, u in zip(cu.rc, cu.uik)]
        for cid, g in cu.groupby("contract_id"):
            top = g.groupby("oik").n.sum()
            if len(top) and top.max() >= 0.99 * g.n.sum():
                by_uik[cid] = top.idxmax()
    dist, how_d = {}, {}
    for _, r in sm.iterrows():
        if r.visible:
            dist[r.contract_id], how_d[r.contract_id] = r.district_name, "portal"; continue
        cand = vis[(vis.rc == r.rc) & (vis.dim == r.dim)]
        if len(cand) == 1:
            dist[r.contract_id], how_d[r.contract_id] = cand.district_name.iloc[0], "dimension"; continue
        if by_uik.get(r.contract_id) in set(cand.district_name):
            dist[r.contract_id], how_d[r.contract_id] = by_uik[r.contract_id], "uik"; continue
        sims = sorted((np.abs(share(r.vec) - share(v)).sum(), dn) for v, dn in zip(cand.vec, cand.district_name))
        dist[r.contract_id] = sims[0][1]
        how_d[r.contract_id] = f"sim {sims[0][0]:.2f}/{sims[1][0]:.2f}" if len(sims) > 1 else "sim"
    d["district"] = d.contract_id.map(dist)
    d.loc[d.kind == "SM", "district_how"] = d.contract_id.map(how_d)
    d["region"] = d.rc.map(names)
    return d, pr


def main():
    d, pr = build()
    OUT.mkdir(parents=True, exist_ok=True)
    def save(df, name):
        tmp = OUT / (name + ".tmp"); df.to_csv(tmp, index=False, encoding="utf-8"); os.replace(tmp, OUT / name)
    cols = ["contract_id", "rc", "region", "tik_pair", "kind", "kind_how", "visible", "district", "district_how",
            "dim", "uiks", "uik_min", "uik_max", "ballots"]
    out = d[cols].copy(); out["votes_valid"] = d.vec.map(sum); out["results"] = d.vec.map(json.dumps)
    save(out, "duma_contracts.csv")

    pl = d[d.kind == "PL"]
    m = pd.DataFrame(pl.vec.tolist(), columns=PARTIES)
    m.insert(0, "region", pl.region.values); m.insert(0, "rc", pl.rc.values); m.insert(0, "contract_id", pl.contract_id.values)
    m["uik_min"], m["uik_max"], m["uiks"] = pl.uik_min.values, pl.uik_max.values, pl.uiks.values
    save(m, "pl_by_tik.csv")
    g = m.groupby(["rc", "region"])[PARTIES].sum().reset_index(); g["valid"] = g[PARTIES].sum(axis=1)
    save(g, "pl_by_region.csv")

    sm = d[d.kind == "SM"]
    rows = []
    for dn, gdf in sm.groupby("district"):
        vis = gdf[gdf.visible].iloc[0]
        cands = pr[pr.contract_id == vis.contract_id].sort_values("position").name.tolist()
        tot = np.sum(np.array(gdf.vec.tolist()), axis=0)
        exact = all(not str(h).startswith("sim") for h in gdf.district_how)
        for i, nm in enumerate(cands):
            rows.append({"region": vis.region, "district": dn, "contracts": len(gdf), "mapping": "exact" if exact else "reconstructed",
                         "candidate": nm, "position": i, "deg_votes": int(tot[i])})
    save(pd.DataFrame(rows), "sm_by_district.csv")
    print(f"контрактов ГД: {len(d)} (PL {int((d.kind == 'PL').sum())}, SM {int((d.kind == 'SM').sum())}); "
          f"регионов: {g.shape[0]}; голосов PL в ДЭГ: {int(g.valid.sum()):,}; "
          f"округов: {sm.district.nunique()}, из них точно сопоставлены: "
          f"{sum(all(not str(h).startswith('sim') for h in x.district_how) for _, x in sm.groupby('district'))}")


if __name__ == "__main__":
    main()
