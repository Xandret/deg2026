"""Собирает parquet-части парсера в одну базу DuckDB: data/derived/deg2026.duckdb.

Запуск: run.cmd build_db

- Дедупликация по tx_id (на случай перекрытия чанков / повторов в цепочке).
- Добавляет время по Москве: ts_msk (с миллисекундами) и ts_msk_s (обрезано до секунды, как у
  С. Слотина — чтобы сверка была побайтной). Источник времени — внутренняя транзакция (tx_ts):
  момент, когда сервис ДЭГ сформировал вызов контракта. outer_ts (нода) и block_ts сохранены.
- Время хранится «наивным» МСК (без зоны): UTC + 3 ч. Переходов на летнее время в РФ нет.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from . import config
from .parse_blocks import SCHEMAS

MSK = "INTERVAL 3 HOUR"
EVENT_TABLES = ("ballots", "votes", "list_events", "contract_events", "contracts")


def build(src: Path, db_path: Path, threads: int, memory: str, only: set | None = None, tmp: Path | None = None) -> dict:
    con = duckdb.connect(str(db_path))
    con.execute(f"SET threads={threads}; SET memory_limit='{memory}'; "
                f"SET temp_directory='{(tmp or db_path.parent / 'duckdb_tmp').as_posix()}'; SET preserve_insertion_order=false")
    report = {}
    g = lambda t: (src / t / "*.parquet").as_posix()

    want = lambda t: only is None or t in only
    if want("blocks"):
      con.execute(f"CREATE OR REPLACE TABLE blocks AS SELECT DISTINCT ON (chain, height) *, "
                f"make_timestamp(block_ts*1000) + {MSK} AS block_msk FROM read_parquet('{g('blocks')}') "
                f"ORDER BY chain, height")
    for t in EVENT_TABLES:
        if not want(t):
            continue
        if not list((src / t).glob("*.parquet")):
            empty = SCHEMAS[t].empty_table()           # пустая таблица с правильной схемой
            con.register("empty_t", empty)
            con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT *, NULL::TIMESTAMP ts_msk, "
                        f"NULL::TIMESTAMP ts_msk_s FROM empty_t")
            con.unregister("empty_t")
            report[t] = "нет данных (создана пустая таблица)"; continue
        raw = con.execute(f"SELECT count(*) FROM read_parquet('{g(t)}')").fetchone()[0]
        # Потоковая загрузка без сортировки (DISTINCT ON + ORDER BY на 12 млн строк не влезает в память
        # слабой машины), затем проверка дублей по tx_id и точечное удаление, если они есть.
        con.execute(f"""CREATE OR REPLACE TABLE {t} AS
            SELECT *,
                   make_timestamp(tx_ts*1000) + {MSK}                    AS ts_msk,
                   date_trunc('second', make_timestamp(tx_ts*1000) + {MSK}) AS ts_msk_s
            FROM read_parquet('{g(t)}')""")
        dups = con.execute(f"SELECT count(*) - count(DISTINCT tx_id) FROM {t}").fetchone()[0]
        if dups:
            con.execute(f"""DELETE FROM {t} WHERE rowid IN (
                SELECT rid FROM (SELECT rowid rid, row_number() OVER (PARTITION BY tx_id ORDER BY height) rn FROM {t})
                WHERE rn > 1)""")
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        report[t] = {"rows_raw": raw, "rows_dedup": n, "duplicates": raw - n}
    if want("list_hashes") and list((src / "list_hashes").glob("*.parquet")):
        con.execute(f"CREATE OR REPLACE TABLE list_hashes AS SELECT DISTINCT * FROM read_parquet('{g('list_hashes')}')")
        report["list_hashes"] = con.execute("SELECT count(*) FROM list_hashes").fetchone()[0]
    if want("blocks"):
      report["blocks"] = con.execute("SELECT chain, count(*) n, min(height) h0, max(height) h1, "
                                   "min(block_msk)::VARCHAR t0, max(block_msk)::VARCHAR t1 "
                                   "FROM blocks GROUP BY chain ORDER BY chain").fetchall()
    con.close()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=config.DERIVED / "blocks")
    ap.add_argument("--db", type=Path, default=config.DERIVED / "deg2026.duckdb")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--memory", default="8GB")
    ap.add_argument("--only", default="", help="через запятую: собрать только эти таблицы")
    ap.add_argument("--tmp", type=Path, default=None, help="каталог для временных файлов DuckDB")
    a = ap.parse_args(argv)
    t0 = time.time()
    rep = build(a.src, a.db, a.threads, a.memory, set(a.only.split(",")) if a.only else None, a.tmp)
    rep["minutes"] = round((time.time() - t0) / 60, 1)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    rp = config.DERIVED / f"_build_report{'_' + a.only.replace(',', '_') if a.only else ''}.json"
    rp.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
