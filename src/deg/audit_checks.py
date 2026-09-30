"""Сверка утверждений карточек A-01…A-11 с первоисточниками → data/derived/audit/checks.json.

Запуск: run.cmd audit_checks [--only a01,a02,...]
Каждая проверка заново считает числа из журнала (deg2026.duckdb, deg2024/2025.duckdb), сырых ответов портала
(data/external/portal/raw) и протоколов (izbirkom_snapshot/paper_vs_deg_lines.csv). Результат — в docs/audit_2026-10.md.

Определения (единые для всех проверок):
  * люди на УИК — по контракту партийных списков Госдумы (duma_pl): его бюллетень получает каждый участник ДЭГ;
  * список УИК  — добавленные − исключённые по этому контракту;
  * местное время = МСК + timeOffset региона с портала;
  * «первый час» — 18.09 08:00–09:00 местного; «ночь» — до 08:00 и с 20:00 местного.
Только агрегаты: user_id и хеши списков наружу не выходят.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics as st
from collections import defaultdict

import duckdb

from . import config

OUT = config.DERIVED / "audit"
RAW = config.EXTERNAL / "portal" / "raw"


def portal_offsets() -> dict[int, int]:
    tz = {}
    for f in glob.glob(str(RAW / "statistics_voting_*.json")):
        b = json.load(open(f, encoding="utf-8"))["body"]
        b = b.get("data", b) if isinstance(b, dict) else b
        if isinstance(b, dict) and "regionCode" in b:
            tz[int(b["regionCode"])] = int(b["timeOffset"])
    return tz


def connect(threads: int, memory: str):
    con = duckdb.connect(str(config.DERIVED / "deg2026.duckdb"), read_only=True)
    con.execute(f"SET threads={threads}; SET memory_limit='{memory}'")
    tz = portal_offsets()
    rows = [(r["contract_id"], int(r["rc"]), r["region"], r["kind"], tz[int(r["rc"])])
            for r in csv.DictReader(open(config.DERIVED / "all_results" / "contracts.csv", encoding="utf-8-sig"))]
    con.execute("CREATE TEMP TABLE c(contract_id VARCHAR, rc INT, region VARCHAR, kind VARCHAR, off INT)")
    con.executemany("INSERT INTO c VALUES (?,?,?,?,?)", rows)
    return con


def uik_table(con):
    """Список и выдача по УИК через контракт duma_pl (однократно)."""
    if con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='U'").fetchone()[0]:
        return
    con.execute("""CREATE TEMP TABLE L AS SELECT c.rc, l.uik,
                     sum(CASE WHEN l.op='removeFromVotersList' THEN -l.n ELSE l.n END) lst
                   FROM list_events l JOIN c USING (contract_id) WHERE c.kind='duma_pl' GROUP BY 1, 2""")
    con.execute("""CREATE TEMP TABLE B AS SELECT c.rc, b.uik, count(*) n, max(b.ts_msk + to_hours(c.off)) last_loc,
                     count(*) FILTER (WHERE b.ts_msk + to_hours(c.off) >= TIMESTAMP '2026-09-18 08:00'
                                        AND b.ts_msk + to_hours(c.off) <  TIMESTAMP '2026-09-18 09:00') h1,
                     count(*) FILTER (WHERE b.ts_msk < TIMESTAMP '2026-09-19 12:00') n1912,
                     count(*) FILTER (WHERE (b.ts_msk + to_hours(c.off))::DATE = DATE '2026-09-20') n20
                   FROM ballots b JOIN c USING (contract_id) WHERE c.kind='duma_pl' GROUP BY 1, 2""")
    con.execute("""CREATE TEMP TABLE U AS SELECT L.rc, L.uik, L.lst, coalesce(n,0) n, last_loc, coalesce(h1,0) h1,
                     coalesce(n1912,0) n1912, coalesce(n20,0) n20 FROM L LEFT JOIN B USING (rc, uik)""")


def a01(con):
    early = con.execute("""SELECT contract_id, ts_msk FROM contract_events
                           WHERE op='finishVoting' AND ts_msk < TIMESTAMP '2026-09-20 00:00'""").fetchall()
    cid = early[0][0]
    con.execute(f"CREATE TEMP TABLE RU AS SELECT DISTINCT region_code, uik FROM list_events WHERE contract_id='{cid}'")
    per = con.execute("""SELECT l.contract_id,
                           sum(l.n) FILTER (WHERE l.op='addVotersList') added,
                           sum(l.n) FILTER (WHERE l.op='removeFromVotersList') removed
                         FROM list_events l JOIN RU USING (region_code, uik) GROUP BY 1 ORDER BY 1""").fetchall()
    bal = con.execute("""SELECT b.contract_id, count(*), max(b.ts_msk) FROM ballots b JOIN RU USING (region_code, uik)
                         GROUP BY 1 ORDER BY 1""").fetchall()
    votes = con.execute(f"SELECT count(*) FROM votes WHERE contract_id='{cid}'").fetchone()[0]
    return {"early_finish": [[c, str(t)] for c, t in early], "rovenki_contracts": [
        {"contract": c, "added": a, "removed": r or 0} for c, a, r in per],
        "ballots": [[c, n, str(t)] for c, n, t in bal], "votes_closed_contract": votes}


def a02(con):
    fin = con.execute("""SELECT count(*), count(DISTINCT c.rc), min(e.ts_msk), max(e.ts_msk) FROM contract_events e
                         JOIN c USING (contract_id) WHERE e.op='finishVoting' AND c.off=0 AND c.kind LIKE 'duma%'
                           AND e.ts_msk >= TIMESTAMP '2026-09-20 00:00'""").fetchone()
    late = con.execute("""SELECT count(*) FROM votes v JOIN c USING (contract_id)
                          WHERE c.off=0 AND c.kind NOT LIKE 'duma%' AND v.ts_msk > TIMESTAMP '2026-09-20 20:00:43'""").fetchone()[0]
    b1945 = con.execute("""SELECT count(*) FROM ballots b JOIN c USING (contract_id) WHERE c.off=0 AND c.kind LIKE 'duma%'
                           AND b.ts_msk >= TIMESTAMP '2026-09-20 19:45' AND b.ts_msk < TIMESTAMP '2026-09-20 20:00'""").fetchone()[0]
    ends = defaultdict(int)
    for f in glob.glob(str(RAW / "statistics_voting_*.json")):
        b = json.load(open(f, encoding="utf-8"))["body"]
        b = b.get("data", b) if isinstance(b, dict) else b
        for v in (b.get("votings") or []) if isinstance(b, dict) else []:
            ends[v.get("ballotAcceptanceEndDate")] += 1
    return {"duma_msk_closed": fin[0], "duma_msk_regions": fin[1], "from": str(fin[2]), "to": str(fin[3]),
            "votes_after_20_00_43_regional_msk": late, "duma_ballots_msk_19_45_20_00": b1945,
            "portal_acceptance_end": dict(ends)}


def a03_a04(con):
    br = dict(con.execute("SELECT c.region, count(*) FROM ballots b JOIN c USING (contract_id) GROUP BY 1").fetchall())
    vr = dict(con.execute("SELECT c.region, count(*) FROM votes v JOIN c USING (contract_id) GROUP BY 1").fetchall())
    unp = {r: 100 * (br[r] - vr.get(r, 0)) / br[r] for r in br}
    w = """ts_msk >= TIMESTAMP '2026-09-18 08:00' - to_hours(c.off) AND ts_msk < TIMESTAMP '2026-09-18 11:00' - to_hours(c.off)"""
    b3 = dict(con.execute(f"SELECT c.region, count(*) FROM ballots b JOIN c USING (contract_id) WHERE {w} GROUP BY 1").fetchall())
    v3 = dict(con.execute(f"SELECT c.region, count(*) FROM votes v JOIN c USING (contract_id) WHERE {w} GROUP BY 1").fetchall())
    mag = next(r for r in b3 if r.startswith("Магадан"))
    ob = sum(v for r, v in b3.items() if r != mag); ov = sum(v for r, v in v3.items() if r != mag)
    before3 = con.execute("""SELECT c.region, count(*) FROM ballots b JOIN c USING (contract_id)
                             WHERE b.ts_msk < TIMESTAMP '2026-09-18 03:00' GROUP BY 1""").fetchall()
    over = con.execute("""SELECT count(*) FROM (SELECT contract_id, count(*) n FROM ballots GROUP BY 1) b
                          JOIN (SELECT contract_id, count(*) n FROM votes GROUP BY 1) v USING (contract_id) WHERE v.n > b.n""").fetchone()[0]
    years = {}
    for y in (2024, 2025):
        cy = duckdb.connect(str(config.DERIVED / f"deg{y}.duckdb"), read_only=True)
        b = cy.execute("SELECT count(*) FROM ballots").fetchone()[0]; v = cy.execute("SELECT count(*) FROM votes").fetchone()[0]
        years[y] = round(100 * (b - v) / b, 2)
    tb, tv = sum(br.values()), sum(vr.values())
    top = sorted(unp.items(), key=lambda x: -x[1])
    return {"a03": {"magadan_ballots_3h": b3[mag], "magadan_votes_3h": v3.get(mag, 0),
                    "magadan_ratio": round(v3.get(mag, 0) / b3[mag], 3), "others_ratio": round(ov / ob, 3),
                    "ballots_before_03_msk": before3, "magadan_unpaired_pct": round(unp[mag], 2),
                    "median_region_unpaired_pct": round(st.median(unp.values()), 2)},
            "a04": {"ballots": tb, "vote_tx": tv, "unpaired": tb - tv, "pct": round(100 * (tb - tv) / tb, 2),
                    "by_year_pct": {**years, 2026: round(100 * (tb - tv) / tb, 2)},
                    "top": [[r, round(p, 2)] for r, p in top[:3]], "bottom": [[r, round(p, 2)] for r, p in top[-2:]],
                    "contracts_votes_gt_ballots": over}}


def a05(con):
    d = con.execute("""WITH a AS (SELECT DISTINCT user_hash FROM list_hashes WHERE op='addVotersList'),
                            r AS (SELECT DISTINCT user_hash FROM list_hashes WHERE op='removeFromVotersList')
                       SELECT (SELECT count(*) FROM a), (SELECT count(*) FROM r),
                              (SELECT count(*) FROM a WHERE user_hash NOT IN (SELECT user_hash FROM r))""").fetchone()
    years = {}
    for y in (2024, 2025):
        cy = duckdb.connect(str(config.DERIVED / f"deg{y}.duckdb"), read_only=True)
        a, r = cy.execute("""SELECT count(DISTINCT user_hash) FILTER (WHERE op='addVotersList'),
                                    count(DISTINCT user_hash) FILTER (WHERE op='removeFromVotersList') FROM list_hashes""").fetchone()
        years[y] = {"enrolled": a, "removed": r, "pct": round(100 * r / a, 3)}
    return {"enrolled": d[0], "removed": d[1], "final": d[2], "removed_pct": round(100 * d[1] / d[0], 2),
            "ballot_people": 3650803, "turnout_pct": round(100 * 3650803 / d[2], 2), "turnout_from_enrolled": round(100 * 3650803 / d[0], 2),
            "years": years}


def a07_a08(con):
    uik_table(con)
    fz = """lst > 0 AND (last_loc IS NULL OR last_loc::DATE <= DATE '2026-09-19') AND (lst - n) >= 0.15 * lst"""
    nat = con.execute(f"SELECT count(*) FILTER (WHERE lst > 0), count(*) FILTER (WHERE {fz}) FROM U").fetchone()
    sv = con.execute(f"SELECT count(*) FILTER (WHERE lst > 0), count(*) FILTER (WHERE {fz}), max(lst) FILTER (WHERE {fz}) FROM U WHERE rc=66").fetchone()
    grp = con.execute("""SELECT count(*), count(*) FILTER (WHERE n20 > 0) FROM U WHERE rc=66 AND lst BETWEEN 85 AND 120
                         AND n1912 BETWEEN 0.65 * lst AND 0.80 * lst""").fetchone()
    fh = con.execute("""SELECT sum(h1) * 100.0 / sum(n) FROM U""").fetchone()[0]
    by = con.execute("""SELECT c2.region, sum(h1) * 100.0 / sum(n) FROM U JOIN (SELECT DISTINCT rc, region FROM c) c2 USING (rc)
                        GROUP BY 1 ORDER BY 2 DESC LIMIT 2""").fetchall()
    night = con.execute("""SELECT count(*) FILTER (WHERE hour(b.ts_msk + to_hours(c.off)) < 8 OR hour(b.ts_msk + to_hours(c.off)) >= 20) * 100.0 / count(*)
                           FROM ballots b JOIN c USING (contract_id) WHERE c.kind='duma_pl'""").fetchone()[0]
    half = con.execute("""SELECT count(*), count(*) FILTER (WHERE rc=45) FROM U WHERE lst >= 50 AND n > 0 AND h1 > 0.5 * n""").fetchone()
    u100 = con.execute("""SELECT count(*) FILTER (WHERE lst >= 50), count(*) FILTER (WHERE lst >= 50 AND n >= lst),
                                 count(*) FILTER (WHERE rc=21 AND lst >= 50), count(*) FILTER (WHERE rc=21 AND lst >= 50 AND n >= lst),
                                 count(*) FILTER (WHERE n > lst) FROM U""").fetchone()
    return {"a07": {"uiks": nat[0], "frozen": nat[1], "frozen_pct": round(100 * nat[1] / nat[0], 1),
                    "sverdl_uiks": sv[0], "sverdl_frozen": sv[1], "sverdl_frozen_pct": round(100 * sv[1] / sv[0], 1),
                    "sverdl_frozen_max_list": sv[2], "sverdl_group_85_120": grp[0], "sverdl_group_issued_on_20": grp[1]},
            "a08": {"first_hour_pct": round(fh, 1), "top_first_hour": [[r, round(p, 1)] for r, p in by], "night_pct": round(night, 1),
                    "uik_half_first_hour_list50": half[0], "kurgan": half[1], "uik50": u100[0], "uik100": u100[1],
                    "chuv_uik50": u100[2], "chuv_uik100": u100[3], "uik_over_100": u100[4]}}


def a09():
    rows = list(csv.DictReader(open(config.DERIVED / "izbirkom_snapshot" / "paper_vs_deg_lines.csv", encoding="utf-8-sig")))
    D = defaultdict(list)
    for r in rows:
        D[(r["district"], r["protocol"])].append(r)
    res, tp, td = [], [], []
    for (dn, p), rs in D.items():
        if p != "1":
            continue
        by = {int(r["row"]): r for r in rs}; cand = [r for r in rs if int(r["row"]) >= 13]
        P = sum(float(r["paper"]) for r in cand); G = sum(float(r["deg"]) for r in cand)
        w = max(cand, key=lambda r: float(r["paper"])); wd = max(cand, key=lambda r: float(r["deg"]))
        res.append((dn, round(float(w["deg"]) / G * 100 - float(w["paper"]) / P * 100, 1), wd["label"] == w["label"]))
        tp.append(sum(float(by[i]["paper"]) for i in (3, 4, 5)) / float(by[1]["paper"]) * 100)
        td.append(float(by[4]["deg"]) / float(by[1]["deg"]) * 100)
    lo = min(res, key=lambda x: x[1])
    return {"districts": len(res), "winner_lower_in_deg": sum(1 for x in res if x[1] < 0), "median_pp": st.median(x[1] for x in res),
            "min": [lo[0], lo[1]], "winner_first_in_deg_everywhere": all(x[2] for x in res),
            "turnout_paper_median": round(st.median(tp), 1), "turnout_deg_median": round(st.median(td), 1)}


def portal():
    inv, nores = {}, defaultdict(int)
    for f in glob.glob(str(RAW / "results_voting_*.json")):
        b = json.load(open(f, encoding="utf-8"))["body"].get("data") or {}
        for l in b.get("lines", []):
            if l["name"].startswith("Число недействительных"):
                inv[os.path.basename(f)[15:-5]] = l["value"]
    for f in glob.glob(str(RAW / "statistics_voting_*.json")):
        b = json.load(open(f, encoding="utf-8"))["body"]
        b = b.get("data", b) if isinstance(b, dict) else b
        for v in (b.get("votings") or []) if isinstance(b, dict) else []:
            if not v.get("hasResults"):
                nores[b.get("regionName")] += 1
    return {"a10_results_with_line": len(inv), "a10_invalid_nonzero": {k: v for k, v in inv.items() if v},
            "a11_has_results_false_by_region": dict(nores)}


CHECKS = {"a01": a01, "a02": a02, "a03a04": a03_a04, "a05": a05, "a07a08": a07_a08}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="через запятую: a01,a02,a03a04,a05,a07a08,a09,portal")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--memory", default="4GB")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "checks.json"
    res = json.load(open(p, encoding="utf-8")) if p.exists() else {}
    if not only or "a09" in only:
        res["a09"] = a09()
    if not only or "portal" in only:
        res["portal"] = portal()
    todo = [k for k in CHECKS if not only or k in only]
    if todo:
        con = connect(a.threads, a.memory)
        for k in todo:
            res[k] = CHECKS[k](con)
            print(k, "готово", flush=True)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8", newline="\n")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
