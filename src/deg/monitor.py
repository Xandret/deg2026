"""ДЭГ-монитор (прототип): автоматические сигналы по журналу блокчейна, проверка «задним числом».

Запуск: run.cmd monitor [--db data/derived/deg2026.duckdb] [--tag 2026]
Выход:  data/derived/monitor/alerts_<tag>.csv, summary_<tag>.json; data/derived/site/monitor.json (для 2026)

Идея: наблюдатель во время голосования получает журнал с задержкой в минуты. Правила ниже используют только то, что
уже есть в журнале к моменту сигнала (окна «назад» и короткое окно «вперёд» ≤ 30 мин — это задержка сигнала,
она указана в каждом правиле). Так их можно запускать и на живой выгрузке: следующий шаг — читать растущий дамп
dump-chunked.py и проверять правила раз в 5–10 минут (docs/tech/14-monitor.md).

Правила:
  R1 early_finish  — голосование закрыто (finishVoting) раньше объявленного конца приёма (20.09, 20:15 местного)
  R2 stall         — регион перестал выдавать бюллетени: ≥ 300 за час, затем 0 за 20 мин, днём (09–19 местного)
                     (задержка 20 мин). Порог по одному голосованию давал ложные сигналы: утренний пик сменяется тишиной
                     и в здоровых голосованиях — поэтому регион целиком.
  R3 accept_gap    — регион: за 30 мин выдано ≥ 300 бюллетеней, а голосов принято меньше половины (задержка 30 мин)
  R4 uik_100       — на участке со списком ≥ 50 бюллетень получили все, кто в списке (момент достижения)
  R5 rejected_vote — транзакция голоса есть, а контракт голос не записал (нет результата VOTE_)
  R6 feed_gap      — в цепочке нет новых блоков > 5 мин во время голосования: сбой источника/выгрузки, не выборов
"""
from __future__ import annotations

import argparse
import json
import os

import duckdb
import pandas as pd

from . import config

OUT = config.DERIVED / "monitor"
END_LOCAL = pd.Timestamp("2026-09-20 20:15:00")
RULES = {"early_finish": ("R1", "Голосование закрыто раньше срока", "high"),
         "stall": ("R2", "Регион перестал выдавать бюллетени", "medium"),
         "accept_gap": ("R3", "Голоса почти не принимаются", "high"),
         "uik_100": ("R4", "Участок: бюллетень получили все из списка", "low"),
         "rejected_vote": ("R5", "Голос отклонён контрактом", "low"),
         "feed_gap": ("R6", "Пауза в цепочке блоков", "info")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DERIVED / "deg2026.duckdb"))
    ap.add_argument("--tag", default="2026")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(a.db, read_only=True)
    con.execute("SET memory_limit='3GB'")
    sd = json.loads((config.DERIVED / "site" / "site_data.json").read_text(encoding="utf-8"))["regions"]
    toff = pd.DataFrame([{"rc": int(r["code"]), "toff": r["toff"], "region": r["name"]} for r in sd])
    con.register("toff", toff)
    # регион голосования — самый частый регион его бюллетеней
    con.execute("""CREATE TEMP TABLE crc AS SELECT b.contract_id, mode(b.region_code)::INT rc FROM ballots b GROUP BY 1""")
    con.execute("CREATE TEMP TABLE cr AS SELECT crc.*, t.toff, t.region FROM crc JOIN toff t USING (rc)")
    alerts = []

    # R1
    fin = con.sql("""SELECT e.contract_id, min(e.ts_msk) fin, cr.toff, cr.region FROM contract_events e JOIN cr USING (contract_id)
                     WHERE e.op = 'finishVoting' GROUP BY ALL""").df()
    fin["deadline_msk"] = END_LOCAL - pd.to_timedelta(fin.toff, unit="h")
    early = fin[fin.fin < fin.deadline_msk - pd.Timedelta(seconds=30)].copy()
    early["min_early"] = ((early.deadline_msk - early.fin).dt.total_seconds() / 60).round(1)
    for r in early.itertuples():
        alerts.append(dict(ts_msk=r.fin, rule="early_finish", region=r.region, contract_id=r.contract_id, uik=None,
                           value=r.min_early, detail="закрыто на " + (f"{int(r.min_early // 60)} ч {int(r.min_early % 60)} мин" if r.min_early >= 120
                                                                    else f"{r.min_early:g} мин") + " раньше 20:15 местного"))

    finmap = fin.set_index("contract_id").fin
    # R2: регион целиком перестал выдавать бюллетени (днём): ≥ 300 за час, затем 0 за 20 мин
    rb2 = con.sql("""SELECT cr.region, any_value(cr.toff) toff, time_bucket(INTERVAL 10 MINUTE, b.ts_msk) t, count(*) n
                     FROM ballots b JOIN cr USING (contract_id) GROUP BY cr.region, t""").df()
    for reg, g in rb2.groupby("region"):
        s_ = g.set_index("t").n.asfreq("10min", fill_value=0); off = float(g.toff.iloc[0])
        prev = s_.rolling(6).sum().shift(1); nxt = s_[::-1].rolling(2).sum()[::-1]
        local = s_.index + pd.Timedelta(hours=off)
        day = (local.hour >= 9) & (local.hour < 19) & (local < END_LOCAL - pd.Timedelta(hours=1))
        hit = (prev >= 300) & (nxt == 0) & day
        for t0 in hit[hit].index[:1]:
            alerts.append(dict(ts_msk=t0 + pd.Timedelta(minutes=20), rule="stall", region=reg, contract_id=None, uik=None,
                               value=int(prev.loc[t0]), detail=f"{int(prev.loc[t0])} бюллетеней за час, затем 0 за 20 мин (с {t0:%d.%m %H:%M} МСК)"))

    # R6: в цепочке нет новых блоков дольше 5 минут, пока идёт голосование (проблема источника, а не выборов)
    gaps = con.sql("""SELECT chain, height, block_msk, block_msk - lag(block_msk) OVER (PARTITION BY chain ORDER BY height) gap
                      FROM blocks""").df()
    gaps = gaps[(gaps.gap > pd.Timedelta(minutes=5)) & (gaps.block_msk > pd.Timestamp("2026-09-18 00:00")) &
                (gaps.block_msk < pd.Timestamp("2026-09-20 21:30"))]
    for r in gaps.itertuples():
        alerts.append(dict(ts_msk=r.block_msk - r.gap + pd.Timedelta(minutes=5), rule="feed_gap", region=None, contract_id=None, uik=None,
                           value=round(r.gap.total_seconds() / 60, 1), detail=f"{r.chain}: блок {r.height} после паузы {r.gap.total_seconds() / 60:.1f} мин"))

    # R3: регион, 30 мин
    rb = con.sql("""SELECT cr.region, time_bucket(INTERVAL 10 MINUTE, b.ts_msk) t, count(*) n FROM ballots b JOIN cr USING (contract_id) GROUP BY ALL""").df()
    rv = con.sql("""SELECT cr.region, time_bucket(INTERVAL 10 MINUTE, v.ts_msk) t, count(*) n FROM votes v JOIN cr USING (contract_id)
                    WHERE v.vote_hash IS NOT NULL GROUP BY ALL""").df()
    for reg, g in rb.groupby("region"):
        b = g.set_index("t").n.asfreq("10min", fill_value=0)
        v = rv[rv.region == reg].set_index("t").n.reindex(b.index, fill_value=0)
        b30, v30 = b.rolling(3).sum(), v.rolling(3).sum()
        bad = (b30 >= 300) & (v30 < 0.5 * b30)
        # отдельные эпизоды: разрыв > 1 ч
        last = None
        for t in bad[bad].index:
            if last is None or t - last > pd.Timedelta(hours=1):
                alerts.append(dict(ts_msk=t + pd.Timedelta(minutes=10), rule="accept_gap", region=reg, contract_id=None, uik=None,
                                   value=round(float(v30[t] / b30[t]), 3),
                                   detail=f"за 30 мин выдано {int(b30[t])}, принято {int(v30[t])} ({100 * v30[t] / b30[t]:.0f} %)"))
            last = t

    # R4: момент, когда на участке выдано столько бюллетеней, сколько людей в списке
    u = con.sql("""
        WITH rep AS (SELECT rc, uik, contract_id cid, n FROM
               (SELECT region_code::INT rc, uik, contract_id, count(*) n FROM ballots GROUP BY ALL)
               QUALIFY row_number() OVER (PARTITION BY rc, uik ORDER BY n DESC, contract_id) = 1),
             lst AS (SELECT r.rc, r.uik, r.cid, sum(CASE WHEN l.op = 'addVotersList' THEN l.n ELSE -l.n END) lst
                     FROM rep r JOIN list_events l ON l.contract_id = r.cid AND l.uik = r.uik GROUP BY ALL),
             seq AS (SELECT b.region_code::INT rc, b.uik, b.ts_msk, row_number() OVER (PARTITION BY b.region_code, b.uik ORDER BY b.ts_msk) k
                     FROM ballots b JOIN lst ON b.contract_id = lst.cid AND b.uik = lst.uik AND b.region_code::INT = lst.rc)
        SELECT s.rc, s.uik, s.ts_msk, lst.lst FROM seq s JOIN lst ON s.rc = lst.rc AND s.uik = lst.uik
        WHERE lst.lst >= 50 AND s.k = lst.lst""").df()
    rn = dict(zip(toff.rc, toff.region))
    for r in u.itertuples():
        alerts.append(dict(ts_msk=r.ts_msk, rule="uik_100", region=rn.get(r.rc), contract_id=None, uik=int(r.uik), value=int(r.lst),
                           detail=f"УИК {int(r.uik)}: выдан {int(r.lst)}-й бюллетень из {int(r.lst)} в списке"))

    # R5
    rj = con.sql("""SELECT v.ts_msk, v.contract_id, cr.region FROM votes v JOIN cr USING (contract_id) WHERE v.vote_hash IS NULL""").df()
    for r in rj.itertuples():
        f = finmap.get(r.contract_id)
        alerts.append(dict(ts_msk=r.ts_msk, rule="rejected_vote", region=r.region, contract_id=r.contract_id, uik=None, value=None,
                           detail="голос отправлен, но не записан" + (f"; голосование закрыто в {f:%H:%M:%S} МСК" if f is not None else "")))

    al = pd.DataFrame(alerts).sort_values("ts_msk")
    al["code"] = al.rule.map(lambda k: RULES[k][0]); al["title"] = al.rule.map(lambda k: RULES[k][1])
    al["severity"] = al.rule.map(lambda k: RULES[k][2])
    tmp = OUT / f"alerts_{a.tag}.csv.tmp"; al.to_csv(tmp, index=False, encoding="utf-8"); os.replace(tmp, OUT / f"alerts_{a.tag}.csv")
    summ = {"alerts": len(al), "by_rule": al.rule.value_counts().to_dict(),
            "first": al.groupby("rule").ts_msk.min().astype(str).to_dict()}
    (OUT / f"summary_{a.tag}.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    if a.tag == "2026":
        # эпизоды: сигналы одного правила в пределах 10 мин — один эпизод
        eps = []
        for rule, g in al.groupby("rule"):
            g = g.sort_values("ts_msk"); start = None; bucket = []
            for r in list(g.itertuples()) + [None]:
                if r is not None and start is not None and (rule != "uik_100" and r.ts_msk - bucket[-1].ts_msk <= pd.Timedelta(minutes=10)
                                                            or rule == "uik_100" and r.ts_msk.date() == start.date()):
                    bucket.append(r); continue
                if bucket:
                    regs = sorted({x.region for x in bucket if isinstance(x.region, str)})
                    eps.append(dict(t=bucket[0].ts_msk.strftime("%d.%m %H:%M:%S"), t_end=bucket[-1].ts_msk.strftime("%d.%m %H:%M:%S"),
                                    ts=bucket[0].ts_msk.isoformat(), rule=rule, code=RULES[rule][0], title=RULES[rule][1], sev=RULES[rule][2],
                                    n=len(bucket), regions=regs[:6], n_regions=len(regs), detail=bucket[0].detail))
                if r is None: break
                start = r.ts_msk; bucket = [r]
        an = {"early_finish": lambda e: "A-01" if e["ts"] < "2026-09-19" else "A-02", "accept_gap": lambda e: "A-03",
              "rejected_vote": lambda e: "A-10", "uik_100": lambda e: "A-08"}
        for e in eps:
            e["anomaly"] = an.get(e["rule"], lambda e: None)(e)
        eps.sort(key=lambda e: e["ts"])
        site = dict(rules=[dict(key=k, code=v[0], title=v[1], sev=v[2], n=int(summ["by_rule"].get(k, 0))) for k, v in RULES.items()],
                    episodes=eps)
        p = config.DERIVED / "site" / "monitor.json"
        tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
        print(f"эпизодов: {len(eps)} → {p}")


if __name__ == "__main__":
    main()
