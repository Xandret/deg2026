"""Динамика ДЭГ 2024 → 2025 → 2026 по регионам на едином методе.

Запуск: run.cmd dynamics
Источники (решение №17, docs/decisions.md — вариант «в»):
  2024, 2025 — ПРЕДВАРИТЕЛЬНО готовые таблицы С. Слотина (edg2024.zip, edg2025.zip из elections-main.zip),
               со ссылкой; для цифр, которые войдут в текст, — позже собственный парсинг сырых дампов.
  2026       — наши таблицы из сырых блоков (deg2026.duckdb).

Единый метод (в 2024/25 в таблицах нет идентификатора избирателя, поэтому считаем не людей, а
«представительный контракт» УИК — контракт с наибольшим числом бюллетеней на этом УИК; он почти всегда
= выборы, в которых участвуют все избиратели УИК):
  список    — начальный список − исключённые (по представительному контракту УИК)
  явка      — бюллетени / список
  первый час— доля бюллетеней, выданных в первый час первого дня (08:00–09:00 местного;
              местное время = МСК + сдвиг, сдвиг по первому бюллетеню региона ≈ 08:00)
  100 % УИК — УИК со списком ≥ 50, где бюллетеней = списку
  без голоса— (бюллетени − голоса) / бюллетени по всем контрактам региона
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

import duckdb

from . import config

SQL_METRICS = """
WITH off AS (SELECT region, (8 - hour(min(ts)))::INT AS toff, min(ts)::DATE AS d0 FROM B GROUP BY 1),
rep AS (SELECT region, uik, arg_max(contract_id, n) AS cid FROM
          (SELECT region, uik, contract_id, count(*) n FROM B GROUP BY ALL) GROUP BY 1, 2),
lst AS (SELECT r.region, r.uik, sum(L.lst) AS lst FROM rep r JOIN L ON L.contract_id = r.cid AND L.uik = r.uik GROUP BY 1, 2),
bal AS (SELECT r.region, r.uik, count(*) AS n,
               count(*) FILTER (WHERE (b.ts + to_hours(o.toff))::DATE = o.d0 + INTERVAL 0 DAY
                                  AND hour(b.ts + to_hours(o.toff)) = 8) AS h1
        FROM rep r JOIN B b ON b.contract_id = r.cid AND b.uik = r.uik JOIN off o ON o.region = r.region GROUP BY 1, 2),
u AS (SELECT l.region, l.uik, l.lst, coalesce(b.n, 0) n, coalesce(b.h1, 0) h1 FROM lst l LEFT JOIN bal b USING (region, uik)),
unp AS (SELECT region, count(*) nb FROM B GROUP BY 1),
unv AS (SELECT e.region, count(*) nv FROM V JOIN E e USING (contract_id) GROUP BY 1)
SELECT u.region, sum(lst) AS list, sum(n) AS ballots, round(100 * sum(n) / sum(lst), 2) AS turnout,
       round(100 * sum(h1) / sum(n), 1) AS first_hour,
       count(*) FILTER (WHERE lst >= 50) AS uik50,
       count(*) FILTER (WHERE lst >= 50 AND n >= lst) AS uik100,
       round(100 * (1 - any_value(unv.nv) / any_value(unp.nb)), 2) AS unpaired
FROM u JOIN unp USING (region) JOIN unv USING (region)
GROUP BY 1 ORDER BY 1
"""


def slotin_year(con, year: int, src: Path):
    d = src / f"edg{year}"
    con.execute(f"CREATE OR REPLACE TEMP VIEW E AS SELECT contract_id, region FROM read_csv_auto('{(d/'elections.csv').as_posix()}')")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW B AS SELECT b.contract_id, b.uik::BIGINT uik, b.timestamp::TIMESTAMP ts, e.region
                    FROM read_csv_auto('{(d/'ballots.csv').as_posix()}') b JOIN E e USING (contract_id)""")
    con.execute(f"CREATE OR REPLACE TEMP VIEW V AS SELECT contract_id FROM read_csv_auto('{(d/'votes.csv').as_posix()}')")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW L AS SELECT contract_id, uik::BIGINT uik,
                    (initial_voters + added_voters - removed_voters) lst FROM read_csv_auto('{(d/'uiks.csv').as_posix()}')""")


def ours_2026(con, portal_regions: Path):
    con.execute(f"""CREATE OR REPLACE TEMP VIEW RN AS SELECT region_code, description AS region
                    FROM read_parquet('{portal_regions.as_posix()}')""")
    con.execute("""CREATE OR REPLACE TEMP VIEW E AS SELECT c.contract_id, rn.region FROM
                    (SELECT contract_id, mode(region_code) rc FROM ballots GROUP BY 1) c JOIN RN rn ON rn.region_code = c.rc""")
    con.execute("CREATE OR REPLACE TEMP VIEW B AS SELECT b.contract_id, b.uik::BIGINT uik, b.ts_msk_s ts, e.region FROM ballots b JOIN E e USING (contract_id)")
    con.execute("CREATE OR REPLACE TEMP VIEW V AS SELECT contract_id FROM votes")
    con.execute("""CREATE OR REPLACE TEMP VIEW L AS SELECT contract_id, uik::BIGINT uik,
                    sum(CASE WHEN op = 'addVotersList' THEN n ELSE -n END) lst FROM list_events GROUP BY 1, 2""")


def ensure_slotin_years(dest: Path, years=(2024, 2025)):
    for y in years:
        out = dest / f"edg{y}"
        if (out / "ballots.csv").exists():
            continue
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(config.SLOTIN_REPO_ZIP) as z:
            name = next(n for n in z.namelist() if n.endswith(f"data/feddeg-data/edg{y}.zip"))
            inner = dest / f"edg{y}.zip"
            with z.open(name) as s, open(inner, "wb") as t:
                shutil.copyfileobj(s, t, 16 << 20)
        with zipfile.ZipFile(inner) as z:
            z.extractall(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=config.DERIVED / "deg2026.duckdb")
    ap.add_argument("--slotin", type=Path, default=config.EXTERNAL / "slotin")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--memory", default="8GB")
    a = ap.parse_args(argv)
    ensure_slotin_years(a.slotin)
    con = duckdb.connect(str(a.db), read_only=True)
    con.execute(f"SET threads={a.threads}; SET memory_limit='{a.memory}'")
    frames = []
    for y in (2024, 2025):
        slotin_year(con, y, a.slotin)
        frames.append(con.execute(SQL_METRICS).df().assign(year=y, source="slotin"))
    ours_2026(con, config.DERIVED / "portal" / "regions.parquet")
    frames.append(con.execute(SQL_METRICS).df().assign(year=2026, source="ours"))
    import pandas as pd
    df = pd.concat(frames)
    out = config.DERIVED / "dynamics"; out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "regions_by_year.csv", index=False, encoding="utf-8")
    piv = df.pivot_table(index="region", columns="year", values=["turnout", "first_hour", "uik100", "unpaired"])
    piv.to_csv(out / "regions_pivot.csv", encoding="utf-8")
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    print(df.groupby("year")[["list", "ballots", "uik50", "uik100"]].sum())
    print(piv.round(2))


if __name__ == "__main__":
    main()
