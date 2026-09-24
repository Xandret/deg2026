"""Сверка ДЭГ: блокчейн ↔ официальные протоколы izbirkom (по HAR-записям из обычного браузера).

Запуск: run.cmd reconcile_izbirkom            (берёт все *.har из data/external/izbirkom/)
        python -m deg.reconcile_izbirkom --db <путь к deg2026.duckdb>

Логика (обоснование — docs/tech/03-izbirkom.md):
  1. Из HAR собираем дерево комиссий (ЦИК → субъект → ОИК → ТИК → УИК) и все расшифрованные протоколы
     (ключ: комиссия + protocolNum; 1 — одномандатный/мажоритарный, 2 — партсписки).
  2. ДЭГ в протокол ТИК не входит, только в протокол ОИК ⇒ ДЭГ(ОИК) = протокол ОИК − Σ протоколов её ТИК.
  3. Контракты цепочки привязываем к ТИК по номерам УИК (узлы type 5 в дереве; номер УИК уникален в субъекте)
     и выбираем контракт нужного бюллетеня: число позиций бюллетеня (dimension) = числу кандидатов/партий
     в протоколе, контракт покрывает УИК этой ТИК. Неоднозначность — в отчёт, не угадываем.
  4. Сравниваем построчно: избиратели (список − исключённые), выдано, голоса, недействительные (голоса − Σ итогов),
     действительные, голоса за каждую позицию.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import urllib.parse
from pathlib import Path

import duckdb
import orjson

from . import config
from .izbirkom_deob import deobfuscate, reports_from_har, table_rows

IZB = config.EXTERNAL / "izbirkom"
OUT = config.DERIVED / "reconcile"


def load_hars(files):
    nodes, reps = {}, {}
    elec_subject = {}                     # electionsId -> код субъекта (для региональных выборов без узла type 2)
    node_elec = {}

    def walk(n, eid=None):
        nid = n["externalId"]
        if eid: node_elec[nid] = eid
        old = nodes.get(nid, {})
        kids = [c["externalId"] for c in n.get("children") or []] or old.get("kids", [])
        nodes[nid] = dict(id=n.get("id"), parent=n.get("parentId"), type=n.get("type"),
                          name=n.get("name"), num=n.get("number"), kids=kids)
        for c in n.get("children") or []:
            walk(c, eid)

    for f in files:
        h = json.load(open(f, encoding="utf-8"))
        for e in h["log"]["entries"]:
            u = e["request"]["url"]; t = e["response"].get("content", {}).get("text")
            m = re.search(r"/elections/(\d+)$", u)
            if m and t and e["response"]["status"] == 200:
                try:
                    elec_subject[m.group(1)] = str(json.loads(t)["subjectRf"]["externalId"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
            if "commissionClassifiers" in u and e["response"]["status"] == 200 and t:
                try:
                    d = json.loads(t)
                    em = re.search(r"electionsId=(\d+)", u)
                    walk(d, em.group(1) if em else None) if isinstance(d, dict) and "externalId" in d else None
                except (json.JSONDecodeError, KeyError):
                    pass
        for rep in reports_from_har(f):
            q = json.loads(urllib.parse.parse_qs(urllib.parse.urlparse(rep["url"]).query)["data"][0])["requestParams"]
            t, _ = deobfuscate(rep["payload"])
            rows = [r for r in table_rows(t) if len(r) >= 3 and r[0].isdigit()]
            vals = {int(r[0]): int(re.sub(r"\D", "", r[-1]) or 0) for r in rows}
            labels = {int(r[0]): r[1] for r in rows}
            reps[(q["commissionClassifierId"], q.get("protocolNum"))] = dict(vals=vals, labels=labels, file=Path(f).name)
    by_id = {v["id"]: k for k, v in nodes.items()}
    for k, v in nodes.items():
        v["parent_ext"] = by_id.get(v["parent"])
        v["subject_fallback"] = elec_subject.get(node_elec.get(k))
    return nodes, reps


def subject_code(nodes, ext):
    start = ext
    while ext and nodes.get(ext, {}).get("type") not in (2, None):
        ext = nodes[ext]["parent_ext"]
    if ext in nodes and nodes[ext].get("type") == 2:
        return str(nodes[ext]["num"])
    return nodes.get(start, {}).get("subject_fallback")        # региональные выборы: корень — ИК субъекта


def uiks_of(nodes, ext):
    out = set()
    for k in nodes.get(ext, {}).get("kids", []):
        n = nodes.get(k, {})
        if n.get("type") == 5 and n.get("num") is not None:
            out.add(int(n["num"]))
        else:
            out |= uiks_of(nodes, k)
    return out


def chain_for_tik(con, region, uiks, n_positions):
    """Контракты цепочки на этих УИК с нужным числом позиций; возвращает список кандидатов с покрытием."""
    if not uiks:
        return []
    ul = ",".join(map(str, sorted(uiks)))
    dim = f"[[1,1,{n_positions}]]"
    return con.execute(f"""
        WITH b AS (SELECT contract_id, uik FROM ballots WHERE region_code = '{region}')
        SELECT b.contract_id,
               count(DISTINCT b.uik) FILTER (WHERE b.uik IN ({ul})) AS in_tik,
               count(DISTINCT b.uik) AS all_uik
        FROM b JOIN contracts k USING (contract_id)
        WHERE k.dimension = '{dim}'
        GROUP BY 1 HAVING in_tik > 0 ORDER BY in_tik DESC""").fetchall()


def chain_totals(con, cids):
    ids = "','".join(cids)
    nb = con.execute(f"SELECT count(*) FROM ballots WHERE contract_id IN ('{ids}')").fetchone()[0]
    nv = con.execute(f"SELECT count(*) FROM votes WHERE contract_id IN ('{ids}')").fetchone()[0]
    add, rem = con.execute(f"""SELECT coalesce(sum(n) FILTER (WHERE op='addVotersList'),0),
        coalesce(sum(n) FILTER (WHERE op='removeFromVotersList'),0) FROM list_events WHERE contract_id IN ('{ids}')""").fetchone()
    res = None
    for (p,) in con.execute(f"SELECT payload FROM contract_events WHERE op='results' AND contract_id IN ('{ids}')").fetchall():
        v = orjson.loads(orjson.loads(p)["results"]["RESULTS"])[0]
        res = v if res is None else [a + b for a, b in zip(res, v)]
    return dict(list=add - rem, issued=nb, votes=nv, results=res or [])


def reconcile(con, nodes, reps):
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    for (oik, pn), orep in reps.items():
        if nodes.get(oik, {}).get("type") != 3:
            continue
        tiks = [k for k in nodes[oik]["kids"] if nodes.get(k, {}).get("type") == 4]
        missing = [nodes[t]["name"] for t in tiks if (t, pn) not in reps]
        entry = dict(oik=nodes[oik]["name"], protocol=pn, n_tik=len(tiks), missing_tik_reports=missing)
        if not tiks or missing:
            entry["status"] = "нет протоколов части ТИК — сверка невозможна"; report.append(entry); continue
        ov = orep["vals"]
        S = collections.Counter()
        for t in tiks:
            S.update(reps[(t, pn)]["vals"])
        deg = {r: ov.get(r, 0) - S.get(r, 0) for r in ov}
        cand_rows = sorted(r for r in ov if r >= 13)
        region = subject_code(nodes, oik)
        picked, ambiguous = [], []
        for t in tiks:
            u = uiks_of(nodes, t)
            c = chain_for_tik(con, region, u, len(cand_rows))
            full = [x for x in c if x[1] == len(u) or x[1] == x[2]]
            if len(full) == 1:
                picked.append(full[0][0])
            else:
                ambiguous.append(dict(tik=nodes[t]["name"], n_uik=len(u), candidates=c[:5]))
        entry.update(region=region, contracts=len(picked), ambiguous=ambiguous)
        if ambiguous:
            entry["status"] = "не удалось однозначно привязать контракты части ТИК"; report.append(entry); continue
        ch = chain_totals(con, picked)
        chain = {1: ch["list"], 2: ch["issued"], 4: ch["issued"], 8: ch["votes"],
                 9: ch["votes"] - sum(ch["results"]), 10: sum(ch["results"])}
        for i, r in enumerate(cand_rows):
            chain[r] = ch["results"][i] if i < len(ch["results"]) else None
        lines = []
        for r in sorted(ov):
            exp = chain.get(r, 0)
            lines.append(dict(row=r, label=orep["labels"].get(r, ""), oik=ov[r], sum_tik=S.get(r, 0),
                              deg_protocol=deg[r], deg_chain=exp, diff=deg[r] - exp))
        bad = [l for l in lines if l["diff"] != 0]
        entry.update(status="СОВПАДАЕТ" if not bad else f"РАСХОЖДЕНИЯ в {len(bad)} строках", lines=lines,
                     contract_ids=picked)
        report.append(entry)
    # ОИК, у которых есть протоколы ТИК, но нет протокола самой ОИК
    have = {k for k in reps}
    for ext, n in nodes.items():
        if n.get("type") != 3: continue
        for pn in {p for (_, p) in have}:
            tk = [k for k in n["kids"] if (k, pn) in have]
            if tk and (ext, pn) not in have:
                report.append(dict(oik=n["name"], protocol=pn, n_tik=len(n["kids"]), missing_tik_reports=[],
                                   status=f"есть {len(tk)} протоколов ТИК, но НЕТ протокола самой ОИК"))
    (OUT / "reconcile_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=config.DERIVED / "deg2026.duckdb")
    a = ap.parse_args(argv)
    files = sorted(IZB.glob("*.har"))
    nodes, reps = load_hars(files)
    print(f"HAR: {len(files)}, узлов дерева: {len(nodes)}, протоколов: {len(reps)}")
    con = duckdb.connect(str(a.db), read_only=True)
    for e in reconcile(con, nodes, reps):
        print(f"\n== {e['oik']} | protocolNum={e['protocol']} | ТИК: {e['n_tik']} | {e['status']}")
        if e.get("missing_tik_reports"):
            print("   нет протоколов ТИК:", ", ".join(e["missing_tik_reports"]))
        for amb in e.get("ambiguous", []):
            print("   неоднозначно:", amb)
        for l in e.get("lines", []):
            mark = "✓" if l["diff"] == 0 else f"≠ {l['diff']:+}"
            print(f"   {l['row']:>2} {l['label'][:45]:45} ОИК {l['oik']:>8} ΣТИК {l['sum_tik']:>8} "
                  f"ДЭГ {l['deg_protocol']:>7} цепь {l['deg_chain']:>7} {mark}")


if __name__ == "__main__":
    main()
