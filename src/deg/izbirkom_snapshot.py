"""Снимок протоколов izbirkom (сторонняя выгрузка) → сверка с нашими данными → бумага против ДЭГ.

Запуск: run.cmd izbirkom_snapshot
Источник: github.com/Arhydemon/elections_20_09_26 (снимок izbirkom.ru от 22.09.2026), файлы лежат в
data/external/izbirkom_snapshot/ (SHA256SUMS.txt, SOURCE.txt). Решение №25, docs/tech/07-izbirkom-snapshot.md.

Снимок НЕПОЛНЫЙ (примерно треть комиссий), поэтому модуль:
  A. сверяет снимок с протоколами, которые мы записали сами (HAR): совпадают ли строки один в один;
  B. сверяет официальные протоколы ДЭГ (isDeg — отдельный «протокол ДЭГ» под ОИК) с блокчейном;
  C. считает бумагу = протокол ОИК − протокол ДЭГ там, где есть оба; для партий по региону — только если
     в снимке есть все ОИК региона;
  D. там, где протокола ДЭГ нет, а протокол ОИК записан нами (HAR): бумага = протокол ОИК − ДЭГ из журнала.
     Метод проверяется здесь же: ДЭГ из журнала сравнивается со ВСЕМИ строками официальных протоколов ДЭГ (B2).
Строки протокола нумеруются как в печатной форме (infoPrintNum): 1–12 — показатели, 13+ — кандидаты/партии.
"""
from __future__ import annotations

import glob
import json
import os
import re

import duckdb
import pandas as pd

from . import config

SNAP = config.EXTERNAL / "izbirkom_snapshot"
OUT = config.DERIVED / "izbirkom_snapshot"
PARTY_KEYS = ["ЕДИНАЯ РОССИЯ", "ЛДПР", "ПРЯМОЙ ДЕМОКРАТИИ", "ЗЕЛЁНЫЕ", "СПРАВЕДЛИВАЯ РОССИЯ", "РОДИНА",
              "КПРФ", "ПЕНСИОНЕРОВ", "КОММУНИСТЫ РОССИИ", "НОВЫЕ ЛЮДИ"]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.upper().replace("Ё", "Е").replace("–", "-")).strip()


def party_idx(text: str):
    t = text.upper()
    if "КОММУНИСТЫ РОССИИ" in t:
        return 8
    for i, k in enumerate(PARTY_KEYS):
        if k in t:
            return i
    return None


def load(con):
    OUT.mkdir(parents=True, exist_ok=True)
    rows_pq, com_pq = OUT / "rows.parquet", OUT / "commissions.parquet"
    if not rows_pq.exists():
        con.execute(f"""COPY (SELECT commissionClassifierId::VARCHAR cid,
                   trim(both '"' FROM parentCommissionClassifierId::VARCHAR) pid, protocolNum pn, isDeg is_deg,
                   CASE WHEN category = 'records' THEN infoNum ELSE try_cast(infoPrintNum AS INT) END row_no,
                   category, infoText AS label, try_cast(value AS BIGINT) AS value, signDateTime
                 FROM read_json_auto('{(SNAP / 'result_rows.part-*.jsonl.gz').as_posix()}', format='newline_delimited', union_by_name=true))
                 TO '{rows_pq.as_posix()}.tmp' (FORMAT parquet)""")
        os.replace(f"{rows_pq}.tmp", rows_pq)
    if not com_pq.exists():
        con.execute(f"""COPY (SELECT externalId::VARCHAR ext, id, name, type, number, _subjectRfCode rc
                 FROM read_json_auto('{(SNAP / 'commissions.jsonl').as_posix()}', format='newline_delimited'))
                 TO '{com_pq.as_posix()}.tmp' (FORMAT parquet)""")
        os.replace(f"{com_pq}.tmp", com_pq)
    con.execute(f"CREATE OR REPLACE VIEW r AS SELECT * FROM '{rows_pq.as_posix()}'")
    con.execute(f"CREATE OR REPLACE VIEW cm AS SELECT * FROM '{com_pq.as_posix()}'")


def protocol(con, cid, pn, deg=False):
    rows = con.execute("SELECT row_no, label, value FROM r WHERE cid = ? AND pn = ? AND is_deg = ? ORDER BY row_no",
                       [cid, pn, deg]).fetchall()
    return {n: (l, v) for n, l, v in rows}


def deg_rows(ch: dict, cand_rows: list) -> dict | None:
    """Строки протокола ДЭГ по журналу (как в reconcile_izbirkom): список, выдано, в ящиках = принято,
    недействительные = принято − Σ итога, действительные, голоса по позициям. None — число позиций не сходится."""
    if len(cand_rows) != len(ch["results"]):
        return None
    valid = sum(ch["results"])
    rows = {1: ch["list"], 2: ch["issued"], 4: ch["issued"], 8: ch["votes"], 9: ch["votes"] - valid, 10: valid}
    rows.update({r: ch["results"][i] for i, r in enumerate(cand_rows)})
    return rows


def check_against_har(con):
    """A. Протоколы из наших HAR ↔ те же протоколы в снимке."""
    from .reconcile_izbirkom import IZB, load_hars
    files = sorted(glob.glob(str(IZB / "*.har")))
    if not files:
        return {"status": "нет HAR"}
    _, reps = load_hars(files)
    have = {c for (c,) in con.execute("SELECT DISTINCT cid FROM r").fetchall()}
    res = {"protocols_compared": 0, "rows_compared": 0, "mismatches": []}
    for (cid, pn), rep in reps.items():
        if cid not in have or pn is None:
            continue
        snap = protocol(con, cid, int(pn))
        if not snap:
            continue
        res["protocols_compared"] += 1
        for n, v in rep["vals"].items():
            if n in snap:
                res["rows_compared"] += 1
                if snap[n][1] != v:
                    res["mismatches"].append(dict(cid=cid, pn=pn, row=n, har=v, snapshot=snap[n][1]))
    return res


def main():
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    load(con)
    report = {"har_check": check_against_har(con)}

    dc = pd.read_csv(config.DERIVED / "deg_results" / "duma_contracts.csv")
    dc["results"] = dc.results.map(json.loads)
    sm_district = dc[dc.kind == "SM"].set_index("tik_pair").district
    dc["district"] = dc.tik_pair.map(sm_district)          # PL-контракт получает округ своей пары
    districts = sorted(dc.district.dropna().unique())
    oiks = con.execute("SELECT ext, name, rc FROM cm WHERE type = 3").df()
    oiks["key"] = oiks.name.map(norm)
    deg_prot = dict(con.execute("SELECT DISTINCT pid, cid FROM r WHERE is_deg").fetchall())
    from .reconcile_izbirkom import chain_totals
    chcon = duckdb.connect(str(config.DERIVED / "deg2026.duckdb"), read_only=True)

    lines, deg_checks, cov, full_checks = [], [], [], []
    for dn in districts:
        o = oiks[oiks.key == norm(dn)]
        if o.empty:
            cov.append(dict(district=dn, oik_found=False)); continue
        ext = o.ext.iloc[0]; dcid = deg_prot.get(ext)
        entry = dict(district=dn, oik_found=True, oik_protocol=bool(protocol(con, ext, 1)), deg_protocol=bool(dcid))
        cov.append(entry)
        for pn, kind in ((1, "SM"), (2, "PL")):
            chain = dc[(dc.district == dn) & (dc.kind == kind)]
            vec = [sum(x) for x in zip(*chain.results)] if len(chain) else []
            if dcid:
                dp = protocol(con, dcid, pn, deg=True)
                cand = [(n, l, v) for n, (l, v) in dp.items() if n >= 13]
                if kind == "SM":
                    names = dc[(dc.district == dn) & (dc.kind == "SM") & dc.visible].contract_id
                    # порядок кандидатов в контракте = порядок в бюллетене = порядок в протоколе (по алфавиту)
                chain_vals = vec
                ok = len(cand) == len(chain_vals) and all(v == chain_vals[i] for i, (_, _, v) in enumerate(cand))
                deg_checks.append(dict(district=dn, protocol=pn, rows=len(cand), match=ok,
                                       official=[v for *_, v in cand], chain=chain_vals))
                # B2: все строки (1–12 и позиции) ДЭГ из журнала ↔ официальный протокол ДЭГ
                dv = deg_rows(chain_totals(chcon, chain.contract_id.tolist()), [n for n, *_ in cand])
                diff = None if dv is None else {n: (v, dv.get(n, 0)) for n, (_, v) in dp.items() if v != dv.get(n, 0)}
                full_checks.append(dict(district=dn, protocol=pn, rows=len(dp), match=diff == {},
                                        diff={str(k): v for k, v in (diff or {}).items()}))
            op = protocol(con, ext, pn)
            if op and dcid:
                dp = protocol(con, dcid, pn, deg=True)
                for n, (l, v) in op.items():
                    d = dp.get(n, (l, 0))[1]
                    lines.append(dict(district=dn, region=o.rc.iloc[0], protocol=pn, row=n, label=l.strip(),
                                      oik=v, deg=d, paper=v - d))
    report["deg_protocol_vs_chain"] = {
        "compared": len(deg_checks), "match": sum(c["match"] for c in deg_checks),
        "mismatches": [c for c in deg_checks if not c["match"]]}
    report["coverage"] = {"districts": len(districts), "oik_found": sum(c["oik_found"] for c in cov),
                          "oik_protocol": sum(c.get("oik_protocol", False) for c in cov),
                          "deg_protocol": sum(c.get("deg_protocol", False) for c in cov),
                          "both": sum(c.get("oik_protocol", False) and c.get("deg_protocol", False) for c in cov)}
    # + округа, которые мы сверили сами по HAR (reconcile_izbirkom): бумага = Σ ТИК, ДЭГ = протокол ОИК − Σ ТИК
    have = {(l["district"], l["protocol"]) for l in lines}
    for l in lines:
        l["source"] = "снимок izbirkom"
    rr = config.DERIVED / "reconcile" / "reconcile_report.json"
    if rr.exists():
        rc_by_name = dict(zip(oiks.key, oiks.rc))
        for e in json.loads(rr.read_text(encoding="utf-8")):
            if e.get("status") != "СОВПАДАЕТ" or (e["oik"], e["protocol"]) in have or e["oik"] not in districts:
                continue
            for ln in e["lines"]:
                lines.append(dict(district=e["oik"], region=rc_by_name.get(norm(e["oik"])), protocol=e["protocol"],
                                  row=ln["row"], label=ln["label"].strip(), oik=ln["oik"], deg=ln["deg_protocol"],
                                  paper=ln["sum_tik"], source="наша запись HAR"))
    # D. + округа, где у нас есть только протокол ОИК (HAR, без протоколов ТИК): ДЭГ — из журнала (проверка B2)
    from .reconcile_izbirkom import IZB, load_hars
    nodes, reps = load_hars(sorted(glob.glob(str(IZB / "*.har"))))
    have = {(l["district"], l["protocol"]) for l in lines}
    by_key = {norm(dn): dn for dn in districts}
    rc_by_name = dict(zip(oiks.key, oiks.rc))
    har_only = []
    for (cid, pn), rep in sorted(reps.items(), key=lambda kv: str(kv[0])):
        dn = by_key.get(norm(nodes.get(cid, {}).get("name") or ""))
        if nodes.get(cid, {}).get("type") != 3 or not dn or pn not in (1, 2) or (dn, pn) in have:
            continue
        cids = dc[(dc.district == dn) & (dc.kind == ("SM" if pn == 1 else "PL"))].contract_id.tolist()
        dv = deg_rows(chain_totals(chcon, cids), sorted(r for r in rep["vals"] if r >= 13))
        if dv is None:
            har_only.append(dict(district=dn, protocol=pn, status="число позиций в протоколе ≠ в журнале")); continue
        neg = [r for r, v in rep["vals"].items() if v - dv.get(r, 0) < 0]
        har_only.append(dict(district=dn, protocol=pn, status="ok" if not neg else "отрицательная бумага", rows=neg,
                             file=rep["file"]))
        if neg:
            continue
        for r in sorted(rep["vals"]):
            d = dv.get(r, 0)
            lines.append(dict(district=dn, region=rc_by_name.get(norm(dn)), protocol=pn, row=r,
                              label=rep["labels"].get(r, "").strip(), oik=rep["vals"][r], deg=d,
                              paper=rep["vals"][r] - d, source="наша запись HAR; ДЭГ — журнал"))
        have.add((dn, pn))
    report["deg_chain_vs_protocol_all_rows"] = {
        "compared": len(full_checks), "match": sum(c["match"] for c in full_checks),
        "rows": sum(c["rows"] for c in full_checks), "mismatches": [c for c in full_checks if not c["match"]]}
    report["har_oik_only"] = {"protocols": len(har_only), "used": sum(h["status"] == "ok" for h in har_only),
                              "problems": [h for h in har_only if h["status"] != "ok"]}
    pd.DataFrame(lines).to_csv(OUT / "paper_vs_deg_lines.csv", index=False, encoding="utf-8")
    # что осталось собрать вручную: ОИК и номер протокола
    got = {(l["district"], l["protocol"]) for l in lines}
    todo = [dict(district=dn, protocol=pn) for dn in districts for pn in (1, 2) if (dn, pn) not in got]
    pd.DataFrame(todo).to_csv(OUT / "todo_oik_protocols.csv", index=False, encoding="utf-8")
    report["still_missing"] = {"districts": len({t["district"] for t in todo}), "protocols": len(todo)}
    pd.DataFrame(cov).to_csv(OUT / "coverage.csv", index=False, encoding="utf-8")
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    h = report["har_check"]; dg = report["deg_protocol_vs_chain"]
    print(f"A. HAR ↔ снимок: протоколов {h.get('protocols_compared')}, строк {h.get('rows_compared')}, "
          f"расхождений {len(h.get('mismatches', []))}")
    print(f"B. протокол ДЭГ ↔ блокчейн: протоколов {dg['compared']}, совпало {dg['match']}")
    b2, ho = report["deg_chain_vs_protocol_all_rows"], report["har_oik_only"]
    print(f"B2. все строки протокола ДЭГ ↔ журнал: протоколов {b2['compared']}, строк {b2['rows']}, совпало {b2['match']}")
    print(f"D. протокол ОИК (наш HAR) − ДЭГ (журнал): протоколов {ho['protocols']}, использовано {ho['used']}, "
          f"проблем {len(ho['problems'])}")
    print(f"C. покрытие округов: {report['coverage']}")
    print(f"D. осталось собрать: {report['still_missing']} → {OUT / 'todo_oik_protocols.csv'}")


if __name__ == "__main__":
    main()
