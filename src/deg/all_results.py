"""Итоги ДЭГ по всем выборам 2026 года (не только Госдума): губернатор, заксобрания, муниципальные.

Запуск: run.cmd all_results      (после build_db, portal, deg_results)
Выход: data/derived/all_results/
    contracts.csv  — каждое из 5 103 голосований: выборы, округ, тип, мандатов, список, бюллетени, голоса,
                     совпадение итога цепочки с порталом
    options.csv    — строки бюллетеня: позиция, кандидат/партия (имя — с портала), голоса ДЭГ (из цепочки)
    races.csv      — «гонки»: округ (одномандатный/многомандатный) или вся территория (партсписок, губернатор,
                     глава); голоса суммированы по всем голосованиям-частям, победители в ДЭГ
    report.json    — сверка с порталом и сводка

Источники: итог — транзакция results каждого контракта (проверена криптографически, docs/tech/08-crypto.md);
названия выборов, округов, кандидатов — портал stat.vybory.gov.ru (docs/tech/02-portal.md). Госдума берётся из
deg_results (1 926 её голосований на портале не видны — A-06).
"""
from __future__ import annotations

import json
import os
import re

import duckdb
import pandas as pd

from . import config

OUT = config.DERIVED / "all_results"

PARTY_KEYS = [("КОММУНИСТЫ РОССИИ", "Коммунисты России"), ("ЕДИНАЯ РОССИЯ", "Единая Россия"), ("ЛДПР", "ЛДПР"),
              ("СПРАВЕДЛИВАЯ РОССИЯ", "Справедливая Россия"), ("КПРФ", "КПРФ"),
              ("КОММУНИСТИЧЕСКАЯ ПАРТИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ", "КПРФ"), ("НОВЫЕ ЛЮДИ", "Новые люди"),
              ("ПЕНСИОНЕР", "Партия пенсионеров"), ("РОДИНА", "Родина"), ("ЗЕЛЁНЫЕ", "Зелёные"), ("ЗЕЛЕНЫЕ", "Зелёные"),
              ("ПРЯМОЙ ДЕМОКРАТИИ", "Партия прямой демократии"), ("ЯБЛОКО", "Яблоко"), ("РОСТА", "Партия Роста"),
              ("ГРАЖДАНСКАЯ ПЛАТФОРМА", "Гражданская платформа"), ("РАЗВИТИЕ", "Развитие")]
PARTY_RE = re.compile(r"партия|ПАРТИЯ|объединени|ОБЪЕДИНЕНИ|отделени|ОТДЕЛЕНИ")


def party(name: str) -> str | None:
    """Каноническое название, если строка бюллетеня — партия (партсписок), иначе None."""
    u = name.upper()
    for k, v in PARTY_KEYS:
        if k in u:
            return v
    if PARTY_RE.search(name):
        return re.sub(r"^\d+\.\s*", "", name).strip()
    return None


def kind_of(level: str, election: str, is_pl: bool, seats: int) -> str:
    e = election.lower()
    if level == "FEDERAL":
        return "duma_pl" if is_pl else "duma_sm"
    if "губернатор" in e or re.search(r"\bглав[аы]\b", e):
        return "head_region" if level == "REGIONAL" else "head_local"
    tier = "reg" if level == "REGIONAL" else "mun"
    if is_pl:
        return f"{tier}_pl"
    return f"{tier}_mm" if seats > 1 else f"{tier}_sm"


KIND_RU = {"duma_pl": "Госдума, партсписок", "duma_sm": "Госдума, одномандатный округ",
           "head_region": "Губернатор", "head_local": "Глава муниципального образования",
           "reg_pl": "Заксобрание, партсписок", "reg_sm": "Заксобрание, одномандатный округ",
           "reg_mm": "Заксобрание, многомандатный округ", "mun_pl": "Муниципальный совет, партсписок",
           "mun_sm": "Муниципальный совет, одномандатный округ", "mun_mm": "Муниципальный совет, многомандатный округ",
           "unknown": "Тип не определён (портал не публикует бюллетень)"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DERIVED / "deg2026.duckdb"), read_only=True)
    con.execute("SET memory_limit='3GB'")
    base = con.sql("""
        WITH c AS (SELECT contract_id, any_value(dimension) dim FROM contracts GROUP BY 1),
             r AS (SELECT contract_id, any_value(payload) payload FROM contract_events WHERE op = 'results' GROUP BY 1),
             b AS (SELECT contract_id, mode(region_code)::INT rc, count(*) ballots, count(DISTINCT uik) uiks FROM ballots GROUP BY 1),
             v AS (SELECT contract_id, count(*) votes FROM votes GROUP BY 1),
             l AS (SELECT contract_id, sum(CASE WHEN op = 'addVotersList' THEN n ELSE -n END) list_final FROM list_events GROUP BY 1)
        SELECT c.contract_id, c.dim, r.payload, b.rc, b.ballots, b.uiks, v.votes, l.list_final
        FROM c LEFT JOIN r USING (contract_id) LEFT JOIN b USING (contract_id)
               LEFT JOIN v USING (contract_id) LEFT JOIN l USING (contract_id)""").df()
    base["res"] = base.payload.map(lambda s: json.loads(json.loads(s)["results"]["RESULTS"])[0])
    base["seats"] = base.dim.map(lambda d: json.loads(d)[0][1])
    base["options"] = base.dim.map(lambda d: json.loads(d)[0][2])
    assert (base.res.map(len) == base.options).all(), "длина итога ≠ числу строк бюллетеня"

    pc = pd.read_parquet(config.DERIVED / "portal" / "contracts.parquet")
    prr = pd.read_parquet(config.DERIVED / "portal" / "results.parquet")
    dc = pd.read_csv(config.DERIVED / "deg_results" / "duma_contracts.csv")
    sm = pd.read_csv(config.DERIVED / "deg_results" / "sm_by_district.csv")
    names = {r.rc: r.region for r in dc.drop_duplicates("rc").itertuples()}
    names.update(dict(zip(pc.region_code, pc.region_name)))

    df = base.merge(pc[["contract_id", "election_id", "election_name", "level", "district_id", "district_name",
                        "counters_json", "voting_json"]], on="contract_id", how="left")
    df["on_portal"] = df.election_id.notna()
    duma = dc.set_index("contract_id")
    # Госдума: невидимые на портале контракты — из deg_results
    miss = ~df.on_portal & df.contract_id.isin(duma.index)
    df.loc[miss, "level"] = "FEDERAL"
    df.loc[miss, "election_name"] = "Выборы депутатов Государственной Думы Федерального Собрания Российской Федерации девятого созыва"
    df.loc[df.contract_id.isin(duma.index), "district_name"] = df.contract_id.map(duma.district)
    df.loc[df.contract_id.isin(duma.index), "election_id"] = "duma2026"
    assert df.level.notna().all(), "есть голосования без выборов"
    df["region"] = df.rc.map(names)

    # строки бюллетеня
    pr = prr.sort_values(["contract_id", "position"]).groupby("contract_id")
    pnames = pr.name.apply(list).to_dict(); pvals = pr.value.apply(list).to_dict()
    rows, check = [], {"compared": 0, "match": 0, "mismatch": [], "no_names": 0}
    for r in df.itertuples():
        nm = pnames.get(r.contract_id)
        if nm is not None:
            check["compared"] += 1
            if pvals[r.contract_id] == list(r.res[:len(nm)]) and len(nm) == len(r.res):
                check["match"] += 1
            else:
                check["mismatch"].append(r.contract_id)
        elif r.level == "FEDERAL":
            d = duma.loc[r.contract_id]
            if d.kind == "PL":
                nm = ["Единая Россия", "ЛДПР", "Партия прямой демократии", "Зелёные", "Справедливая Россия", "Родина",
                      "КПРФ", "Партия пенсионеров", "Коммунисты России", "Новые люди"]
            else:
                c = sm[sm.district == d.district].sort_values("position").candidate.tolist()
                nm = c if len(c) == len(r.res) else None
        if nm is None:
            check["no_names"] += 1
        for i, v in enumerate(r.res):
            n = nm[i] if nm else None
            rows.append(dict(contract_id=r.contract_id, position=i + 1, name=n, party=party(n) if n else None, votes=v))
    opt = pd.DataFrame(rows)
    agg = opt.assign(has=opt["name"].notna(), pty=opt["party"].notna()).groupby("contract_id")[["has", "pty"]].mean()
    is_pl = (agg.has == 1) & (agg.pty > 0.5)
    df["names"] = df.contract_id.isin(opt[opt.name.notna()].contract_id)
    # тип бюллетеня с портала: UNION — партсписок, SINGLE — кандидаты (совпадает с разбором имён во всех 3 001 случае)
    vtype = df.voting_json.map(lambda s: json.loads(s).get("type") if isinstance(s, str) else None)
    df["is_pl"] = vtype.eq("UNION").where(vtype.notna(), df.contract_id.map(is_pl).fillna(False))
    check["type_vs_names_agree"] = int((vtype.eq("UNION") == df.contract_id.map(is_pl)).where(df.names & vtype.notna()).sum())
    df.loc[df.contract_id.isin(duma.index), "is_pl"] = df.contract_id.map(duma.kind).eq("PL")
    df["kind"] = [kind_of(l, e, p, s) for l, e, p, s in zip(df.level, df.election_name, df.is_pl, df.seats)]
    df.loc[df.contract_id.isin(duma.index), "kind"] = df.is_pl.map({True: "duma_pl", False: "duma_sm"})
    df["kind_ru"] = df.kind.map(KIND_RU)
    df["marks"] = df.res.map(sum)
    df["turnout_pct"] = (100 * df.ballots / df.list_final.where(df.list_final > 0)).round(2)
    cnt = df.counters_json.dropna().map(json.loads)
    df.loc[cnt.index, "portal_issued"] = cnt.map(lambda c: c.get("issued"))
    df.loc[cnt.index, "portal_voted"] = cnt.map(lambda c: c.get("voted"))
    check["counters_issued_match"] = int((df.portal_issued == df.ballots).sum())
    check["counters_voted_match"] = int((df.portal_voted == df.votes).sum())
    check["counters_compared"] = int(df.portal_issued.notna().sum())

    # гонки: партсписок, губернатор и глава — вся территория выборов; округа — district_id
    whole = df.kind.isin(["duma_pl", "reg_pl", "mun_pl", "head_region", "head_local"])
    df["race"] = df.election_id.astype(str) + "|" + df.kind + "|" + \
        df.district_name.where(~whole | df.kind.eq("duma_pl"), "").fillna("")
    # Госдума-партсписок — по регионам
    df.loc[df.kind == "duma_pl", "race"] = "duma2026|duma_pl|" + df.region
    opt = opt.merge(df[["contract_id", "race"]], on="contract_id")
    races = []
    for race, g in df.groupby("race"):
        o = opt[opt.contract_id.isin(g.contract_id)]
        key = o["name"].fillna("№" + o.position.astype(str))
        if g.kind.iloc[0].endswith("_pl"):                  # партсписок: партия под каноническим названием
            key = o["party"].fillna(key)
        tot = o.groupby(key, sort=False).votes.sum().sort_values(ascending=False)
        first = g.iloc[0]; seats = int(first.seats)
        marks = int(tot.sum())
        races.append(dict(race=race, region=first.region, rc=first.rc, level=first.level, kind=first.kind,
                          kind_ru=first.kind_ru, election=first.election_name,
                          district=(first.district_name if not (whole.loc[g.index].all() and first.kind != "duma_pl") else ""),
                          contracts=len(g), seats=seats, candidates=len(tot), list_final=int(g.list_final.sum()),
                          ballots=int(g.ballots.sum()), votes=int(g.votes.sum()), marks=marks,
                          turnout_pct=round(100 * g.ballots.sum() / max(1, g.list_final.sum()), 2),
                          names=bool(g.names.all()),
                          leaders=json.dumps([[n, int(v), round(100 * v / max(1, marks), 2)] for n, v in tot.head(max(seats, 3)).items()],
                                             ensure_ascii=False),
                          margin_pp=round(100 * (tot.iloc[seats - 1] - tot.iloc[seats]) / max(1, marks), 2) if len(tot) > seats else None))
    races = pd.DataFrame(races).sort_values(["region", "level", "election", "district"])

    cols = ["contract_id", "rc", "region", "level", "kind", "kind_ru", "election_id", "election_name", "district_name",
            "seats", "options", "uiks", "list_final", "ballots", "votes", "marks", "turnout_pct", "on_portal", "names",
            "portal_issued", "portal_voted", "race"]
    for d, name in ((df[cols], "contracts.csv"), (opt.drop(columns=["race"]), "options.csv"), (races, "races.csv")):
        tmp = OUT / f"{name}.tmp"; d.to_csv(tmp, index=False, encoding="utf-8"); os.replace(tmp, OUT / name)
    summ = {"contracts": len(df), "by_kind": df.kind.value_counts().to_dict(),
            "races_by_kind": races.kind.value_counts().to_dict(),
            "elections": int(df.election_id.nunique()), "check_portal": {**check, "mismatch": check["mismatch"][:20],
                                                                          "mismatch_n": len(check["mismatch"])},
            "no_names_regions": df[~df.names].region.value_counts().to_dict()}
    (OUT / "report.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    site_json(df, opt.drop(columns=["race"]), races, check)
    print(json.dumps(summ, ensure_ascii=False, indent=1))


def site_json(df: pd.DataFrame, opt: pd.DataFrame, races: pd.DataFrame, check: dict):
    """Компактные данные для раздела «Все выборы» → data/derived/site/elections.json."""
    KINDS = ["head_region", "reg_pl", "reg_sm", "reg_mm", "head_local", "mun_pl", "mun_sm", "mun_mm"]
    loc = races[races.kind.isin(KINDS)].copy()
    els = sorted(loc.election.unique()); ei = {e: i for i, e in enumerate(els)}
    def short(d):
        d = re.sub(r"(?i)\s*(одно|дву|двух|тр[её]х|четыр[её]х|пяти|шести|семи|восьми|девяти|десяти|много)?мандатный\s+"
                   r"избирательный\s+округ\s*", " ", d or "")
        return re.sub(r"№\s+", "№", re.sub(r"\s+", " ", d)).strip()
    rows = []
    for r in loc.itertuples():
        L = json.loads(r.leaders)
        rows.append([int(r.rc), KINDS.index(r.kind), ei[r.election], short(r.district) if isinstance(r.district, str) else "",
                     int(r.seats), int(r.candidates), int(r.ballots), float(r.turnout_pct),
                     L[0][0], L[0][2], None if pd.isna(r.margin_pp) else float(r.margin_pp), int(bool(r.names))])
    gov = races[races.kind == "head_region"].iloc[0]
    go = opt[opt.contract_id.isin(df[df.race == gov.race].contract_id)].groupby("name", sort=False).votes.sum().sort_values(ascending=False)
    pl = {}
    for kind in ("reg_pl", "duma_pl"):
        for r in races[races.kind == kind].itertuples():
            o = opt[opt.contract_id.isin(df[df.race == r.race].contract_id)]
            t = o.groupby(o["party"].fillna(o["name"])).votes.sum()
            pl.setdefault(r.region, {})[kind] = {k: round(100 * v / t.sum(), 3) for k, v in t.items()}
            if kind == "reg_pl":
                pl[r.region].update(ballots=int(r.ballots), turnout=float(r.turnout_pct), names=bool(r.names),
                                    election=r.election)
    zs = [dict(region=k, **v) for k, v in pl.items() if "reg_pl" in v]
    by = df.groupby("kind").agg(contracts=("contract_id", "size"), ballots=("ballots", "sum"), elections=("election_id", "nunique"))
    summ = {k: {**{c: int(v) for c, v in by.loc[k].items()}, "races": int((races.kind == k).sum())} for k in by.index}
    kal = df[~df.names]
    out = dict(kinds=KINDS, kindRu=[KIND_RU[k] for k in KINDS], elections=els, races=rows, summary=summ,
               gov=dict(region=gov.region, ballots=int(gov.ballots), turnout=float(gov.turnout_pct), list=int(gov.list_final),
                        cands=[[n, int(v), round(100 * v / go.sum(), 2)] for n, v in go.items()]),
               zs=sorted(zs, key=lambda z: z["region"]),
               check=dict(compared=check["compared"], match=check["match"], hidden=int(len(kal)),
                          hidden_ballots=int(kal.ballots.sum()), hidden_elections=int(kal.election_id.nunique()),
                          hidden_region=(kal.region.iloc[0] if len(kal) else None), total=int(len(df)),
                          elections=int(df.election_id.nunique())))
    p = config.DERIVED / "site" / "elections.json"; p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)
    print(f"сайт: {p} ({p.stat().st_size // 1024} КБ, {len(rows)} гонок)")


if __name__ == "__main__":
    main()
