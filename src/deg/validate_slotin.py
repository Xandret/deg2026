"""Сверка наших таблиц (из сырых блоков) с опубликованными таблицами С. Слотина (edg2026.zip).

Запуск: run.cmd validate_slotin

Зачем. Наш парсер написан независимо. Если на ~12 млн событий результаты совпадают до секунды,
это подтверждает и наш код, и их таблицы (независимое воспроизведение). Расхождения — предмет
отдельного разбора, а не повод «подогнать».

Таблицы Слотина используются ТОЛЬКО здесь, как эталон для сверки. Источник: elections-main.zip →
data/feddeg-data/edg2026.zip (снимок репозитория github.com/sslotin/elections, коммит dcc332c1).

Проверки:
  A. итоговые количества;
  B. бюллетени: мультимножество (contract_id, uik, секунда) — EXCEPT ALL в обе стороны;
  C. голоса:    мультимножество (contract_id, секунда);
  D. списки:    (contract_id, uik, секунда, count, тип) — addVotersList↔initial, remove↔remove;
  E. итоги:     RESULTS из транзакций results против results.csv;
  F. покрытие:  контракты Слотина, которых нет в нашем дампе, и наоборот.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

import duckdb
import orjson

from . import config

INNER = "data/feddeg-data/edg2026.zip"


def ensure_slotin_csv(dest: Path) -> Path:
    out = dest / "edg2026"
    names = ("elections", "results", "ballots", "votes", "voter_list_events")
    if all((out / f"{n}.csv").exists() for n in names):
        return out
    out.mkdir(parents=True, exist_ok=True)
    inner_zip = dest / "edg2026.zip"
    if not inner_zip.exists():
        with zipfile.ZipFile(config.SLOTIN_REPO_ZIP) as z:
            name = next(n for n in z.namelist() if n.endswith(INNER))
            tmp = inner_zip.with_suffix(".tmp")
            with z.open(name) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 16 << 20)
            tmp.replace(inner_zip)                    # атомарно: оборванная копия не примется за целую
    with zipfile.ZipFile(inner_zip) as z:
        for n in z.namelist():
            if n.endswith(".csv"):
                tmp = out / (Path(n).name + ".tmp")
                with z.open(n) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 16 << 20)
                tmp.replace(out / Path(n).name)
    return out


def flat(v):
    if isinstance(v, str):
        try:
            v = orjson.loads(v)
        except orjson.JSONDecodeError:
            return v
    while isinstance(v, list) and len(v) == 1 and isinstance(v[0], list):
        v = v[0]
    return v


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=config.DERIVED / "deg2026.duckdb")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--memory", default="8GB")
    ap.add_argument("--slotin-dir", type=Path, default=None, help="готовая папка с CSV (для тестов)")
    ap.add_argument("--checks", default="A,B,C,D,E,F", help="какие проверки выполнить (для слабых машин — по частям)")
    ap.add_argument("--tmp", type=Path, default=None)
    ap.add_argument("--partitions", type=int, default=1, help="делить сравнение на N частей по contract_id")
    a = ap.parse_args(argv)

    sdir = a.slotin_dir or ensure_slotin_csv(config.EXTERNAL / "slotin")
    con = duckdb.connect(str(a.db))
    con.execute(f"SET threads={a.threads}; SET memory_limit='{a.memory}'; SET preserve_insertion_order=false")
    if a.tmp:
        con.execute(f"SET temp_directory='{a.tmp.as_posix()}'")
    checks = set(a.checks.split(","))
    have = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    for t in ("elections", "results", "ballots", "votes", "voter_list_events"):
        if f"s_{t}" in have:
            continue                                   # уже загружено прошлым запуском
        con.execute(f"CREATE TABLE s_{t} AS SELECT * FROM read_csv_auto('{(sdir / (t + '.csv')).as_posix()}', header=true)")
    q = lambda sql: con.execute(sql).fetchall()
    out = config.DERIVED / "validation_slotin.json"
    rep = json.loads(out.read_text()) if out.exists() else {}

    if "A" in checks: rep["A_totals"] = {
        "ballots": {"ours": q("SELECT count(*) FROM ballots")[0][0], "slotin": q("SELECT count(*) FROM s_ballots")[0][0]},
        "votes": {"ours": q("SELECT count(*) FROM votes")[0][0], "slotin": q("SELECT count(*) FROM s_votes")[0][0]},
        "initial_sum": {"ours": q("SELECT sum(n) FROM list_events WHERE op='addVotersList'")[0][0],
                        "slotin": q("SELECT sum(count) FROM s_voter_list_events WHERE type='initial'")[0][0]},
        "remove_sum": {"ours": q("SELECT sum(n) FROM list_events WHERE op='removeFromVotersList'")[0][0],
                       "slotin": q("SELECT sum(count) FROM s_voter_list_events WHERE type='remove'")[0][0]},
        "contracts": {"ours_created": q("SELECT count(*) FROM contracts")[0][0],
                      "slotin": q("SELECT count(*) FROM s_elections")[0][0]},
    }

    def diff(ours_sql, slot_sql, name, key_cols):
        """Сравнение мультимножеств через счётчики по ключу, по частям (partitions) — чтобы влезть
        в память слабой машины. only_ours = Σ max(n_ours − n_slotin, 0), и симметрично."""
        con.execute(f"CREATE OR REPLACE TEMP VIEW o AS {ours_sql}")
        con.execute(f"CREATE OR REPLACE TEMP VIEW s AS {slot_sql}")
        keys = ", ".join(key_cols)
        only_o = only_s = n_o = n_s = 0
        worst = {}
        for part in range(a.partitions):
            flt = f"hash(contract_id) % {a.partitions} = {part}"
            r = q(f"""WITH a AS (SELECT {keys}, count(*) n FROM o WHERE {flt} GROUP BY ALL),
                           b AS (SELECT {keys}, count(*) n FROM s WHERE {flt} GROUP BY ALL),
                           j AS (SELECT coalesce(a.contract_id, b.contract_id) contract_id,
                                        coalesce(a.n, 0) na, coalesce(b.n, 0) nb
                                 FROM a FULL OUTER JOIN b USING ({keys}))
                      SELECT contract_id, sum(na), sum(nb), sum(greatest(na-nb,0)), sum(greatest(nb-na,0))
                      FROM j GROUP BY 1""")
            for cid, na, nb, oo, os_ in r:
                n_o += na; n_s += nb; only_o += oo; only_s += os_
                if oo or os_:
                    worst[cid] = int(oo) - int(os_)
        rep[name] = {"ours": int(n_o), "slotin": int(n_s), "only_ours": int(only_o), "only_slotin": int(only_s),
                     "contracts_with_diff": len(worst),
                     "worst_contracts": sorted(worst.items(), key=lambda kv: -abs(kv[1]))[:15]}

    if "B" in checks: diff("SELECT contract_id, uik::BIGINT uik, ts_msk_s t FROM ballots",
         "SELECT contract_id, uik::BIGINT uik, timestamp::TIMESTAMP t FROM s_ballots", "B_ballots",
         ["contract_id", "uik", "t"])
    if "C" in checks: diff("SELECT contract_id, ts_msk_s t FROM votes",
         "SELECT contract_id, timestamp::TIMESTAMP t FROM s_votes", "C_votes", ["contract_id", "t"])
    if "D" in checks: diff("""SELECT contract_id, uik::BIGINT uik, ts_msk_s t, n::BIGINT c,
               CASE op WHEN 'addVotersList' THEN 'initial' WHEN 'removeFromVotersList' THEN 'remove' ELSE op END ty
            FROM list_events""",
         "SELECT contract_id, uik::BIGINT uik, timestamp::TIMESTAMP t, count::BIGINT c, type ty FROM s_voter_list_events",
         "D_voter_lists", ["contract_id", "uik", "t", "c", "ty"])

    if "E" in checks:
      ours_res = {cid: flat(orjson.loads(p)["results"].get("RESULTS")) for cid, p in
                q("SELECT contract_id, payload FROM contract_events WHERE op='results' AND payload IS NOT NULL")}
      slot_res = {cid: flat(r) for cid, r in q("SELECT contract_id, results FROM s_results")}
      common = [c for c in ours_res if c in slot_res]
      mism = [c for c in common if ours_res[c] != slot_res[c]]
      rep["E_results"] = {"ours_contracts_with_results": len(ours_res), "compared": len(common),
                        "equal": len(common) - len(mism), "mismatch_examples":
                        [(c, ours_res[c], slot_res[c]) for c in mism[:10]]}

    if "F" in checks: rep["F_coverage"] = {
        "slotin_not_in_our_ballots": q("SELECT count(*) FROM s_elections WHERE contract_id NOT IN (SELECT DISTINCT contract_id FROM ballots)")[0][0],
        "our_ballot_contracts_not_in_slotin": q("SELECT count(DISTINCT contract_id) FROM ballots WHERE contract_id NOT IN (SELECT contract_id FROM s_elections)")[0][0],
        "slotin_contracts_without_our_results": q("SELECT count(*) FROM s_elections WHERE contract_id NOT IN (SELECT contract_id FROM contract_events WHERE op='results')")[0][0],
    }
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    print("Сохранено:", out)


if __name__ == "__main__":
    main()
