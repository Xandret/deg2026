"""Независимый парсер сырого дампа блокчейна федерального ДЭГ-2026 → parquet.

Запуск (Windows):   run.cmd parse_blocks --workers 12
Тест (маленький):   run.cmd parse_blocks --limit 20 --out data\\derived\\blocks_test

Что делает. Каждый чанк (100 блоков, JSONL.gz) обрабатывается отдельным процессом и превращается
в набор parquet-файлов — по одному на таблицу. Итоговые таблицы собираются позже (build_db.py)
через DuckDB как объединение всех частей.

Структура транзакций (Waves Enterprise, см. docs/tech/01-parser.md):
  внешняя tx type=105 («результат исполнения контракта», подписана нодой)
    └─ tx: type=104 (вызов контракта, подписан сервисом) либо 103 (создание контракта)
         params[]: operation=blindSigIssue | vote | addVotersList | removeFromVotersList |
                   startVoting | addMainKey | decryption | commissionDecryption | results | …
    └─ results[]: что контракт записал в своё состояние
Три отметки времени: блок (block_ts), внешняя tx (outer_ts), внутренняя tx (tx_ts), все в мс UTC.

Выходные таблицы (одна строка = одно событие):
  blocks          chain, height, block_ts, n_tx, generator
  contracts       создание контракта (type 103): contract_id, poll_id, commission_id, bulletin_hash, dimension …
  ballots         выдача бюллетеня (blindSigIssue): contract_id, region_code, uik, user_id
  votes           приём голоса (vote): contract_id, vote_hash, blindsig_sha1, vote_len
  list_events     addVotersList / removeFromVotersList (агрегат на транзакцию): n, voters_count
  list_hashes     по одному хешу избирателя на строку (для дедупликации людей; ЛОКАЛЬНО, не публикуется)
  contract_events startVoting, addMainKey, finishVoting, decryption, commissionDecryption, results, прочее
  other_tx        всё, что не распознано (чтобы ничего не потерять)
Во все события: chain, height, block_ts, outer_id, outer_ts, tx_id, tx_ts, contract_id, op.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import sys
import time
import traceback
from multiprocessing import get_context
from pathlib import Path

import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from . import config
from .sources import Chunk, plan

TABLES = ("blocks", "contracts", "ballots", "votes", "list_events", "list_hashes",
          "contract_events", "other_tx")

# Явные схемы: одинаковые типы во всех частях → DuckDB читает их одним read_parquet без сюрпризов.
S = pa.string(); I64 = pa.int64(); I32 = pa.int32()
EVENT = [("chain", S), ("height", I32), ("block_ts", I64), ("outer_id", S), ("outer_ts", I64),
         ("tx_id", S), ("tx_ts", I64), ("contract_id", S), ("op", S)]
SCHEMAS = {
    "blocks": pa.schema([("chain", S), ("height", I32), ("block_ts", I64), ("n_tx", I32),
                         ("generator", S), ("signature", S), ("reference", S)]),
    "contracts": pa.schema(EVENT + [("poll_id", S), ("commission_id", S), ("bulletin_hash", S),
                                    ("dimension", S), ("vote_type", S), ("blind_sig_type", S),
                                    ("image", S), ("image_hash", S), ("contract_name", S),
                                    ("params_json", S)]),
    "ballots": pa.schema(EVENT + [("region_code", S), ("uik", I32), ("user_id", S), ("n_data", I32)]),
    "votes": pa.schema(EVENT + [("vote_hash", S), ("blindsig_sha1", S), ("vote_len", I32)]),
    "list_events": pa.schema(EVENT + [("region_code", S), ("uik", I32), ("n", I32), ("voters_count", I32)]),
    "list_hashes": pa.schema([("chain", S), ("tx_id", S), ("contract_id", S), ("op", S),
                              ("region_code", S), ("uik", I32), ("user_hash", S)]),
    "contract_events": pa.schema(EVENT + [("payload", S), ("result_keys", S)]),
    "other_tx": pa.schema([("chain", S), ("height", I32), ("block_ts", I64), ("type", S),
                           ("inner_type", S), ("op", S), ("id", S), ("keys", S)]),
}
# Для этих операций payload храним целиком (они маленькие и нужны для анализа)
FULL_PAYLOAD_OPS = {"startVoting", "finishVoting", "results", "stopVoting", "addMainKey"}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _json_maybe(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return orjson.loads(v)
        except orjson.JSONDecodeError:
            return v
    return v


def parse_chunk(chunk: Chunk) -> dict[str, list[dict]]:
    raw = chunk.read_bytes()
    data = gzip.decompress(raw) if chunk.gz else raw
    rows = {t: [] for t in TABLES}
    for line in data.splitlines():
        if not line.strip():
            continue
        b = orjson.loads(line)
        h = int(b["height"])
        if h < chunk.min_height or h < chunk.start or h > chunk.end:
            continue
        bts = int(b["timestamp"])
        txs = b.get("transactions") or []
        rows["blocks"].append(dict(chain=chunk.chain, height=h, block_ts=bts, n_tx=len(txs),
                                   generator=b.get("generator"), signature=b.get("signature"),
                                   reference=b.get("reference")))
        for t in txs:
            ttype = str(t.get("type"))
            inner = t.get("tx")
            if ttype != "105" or not isinstance(inner, dict):
                rows["other_tx"].append(dict(chain=chunk.chain, height=h, block_ts=bts, type=ttype,
                                             inner_type=None, op=None, id=t.get("id"),
                                             keys=",".join(sorted(t.keys()))))
                continue
            params = {p["key"]: p.get("value") for p in inner.get("params") or []}
            itype = str(inner.get("type"))
            op = params.get("operation") or ("createContract" if itype == "103" else f"type{itype}")
            contract_id = inner.get("contractId") or (inner.get("id") if itype == "103" else None)
            base = dict(chain=chunk.chain, height=h, block_ts=bts, outer_id=t.get("id"),
                        outer_ts=_int(t.get("timestamp")), tx_id=inner.get("id"),
                        tx_ts=_int(inner.get("timestamp")), contract_id=contract_id, op=op)
            results = t.get("results") or []

            if op == "blindSigIssue":
                d = _json_maybe(params.get("data")) or []
                uid = d[0].get("userId") if d and isinstance(d[0], dict) else None
                rows["ballots"].append(dict(base, region_code=str(params.get("primaryUikRegionCode")),
                                            uik=_int(params.get("primaryUikNumber")), user_id=uid,
                                            n_data=len(d) if isinstance(d, list) else None))
            elif op == "vote":
                vote_hash = None
                for r in results:
                    if str(r.get("key", "")).startswith("VOTE_"):
                        vote_hash = (_json_maybe(r.get("value")) or {}).get("vote")
                bs = params.get("blindSig") or ""
                rows["votes"].append(dict(base, vote_hash=vote_hash,
                                          blindsig_sha1=hashlib.sha1(bs.encode()).hexdigest() if bs else None,
                                          vote_len=len(params.get("vote") or "")))
            elif op in ("addVotersList", "removeFromVotersList", "restoreToVotersList", "addToVotersList"):
                hashes = _json_maybe(params.get("userIdHashes")) or []
                region = str(params.get("primaryUikRegionCode"))
                uik = _int(params.get("primaryUikNumber"))
                rows["list_events"].append(dict(base, region_code=region, uik=uik, n=len(hashes),
                                                voters_count=_int(params.get("votersCount"))))
                for hsh in hashes:
                    rows["list_hashes"].append(dict(chain=chunk.chain, tx_id=base["tx_id"],
                                                    contract_id=contract_id, op=op, region_code=region,
                                                    uik=uik, user_hash=hsh))
            elif op == "createContract":
                p = params
                rows["contracts"].append(dict(
                    base, poll_id=p.get("pollId"), commission_id=p.get("commissionId"),
                    bulletin_hash=p.get("bulletinHash"), dimension=str(p.get("dimension")),
                    vote_type=p.get("type"), blind_sig_type=p.get("blindSigType"),
                    image=inner.get("image"), image_hash=inner.get("imageHash"),
                    contract_name=inner.get("contractName"),
                    params_json=orjson.dumps({k: v for k, v in p.items()
                                              if k not in ("jwtTokenRegistrator",)}).decode()))
            else:
                payload = None
                if op in FULL_PAYLOAD_OPS:
                    payload = orjson.dumps({"params": {k: v for k, v in params.items() if k != "operation"},
                                            "results": {r.get("key"): r.get("value") for r in results}}).decode()
                rows["contract_events"].append(dict(
                    base, payload=payload,
                    result_keys=",".join(str(r.get("key"))[:40] for r in results[:10])))
    return rows


def _write(rows: dict[str, list[dict]], out: Path, stem: str) -> dict[str, int]:
    counts = {}
    for t, rs in rows.items():
        counts[t] = len(rs)
        if not rs:
            continue
        d = out / t
        d.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rs, schema=SCHEMAS[t])
        tmp = d / f".{stem}.parquet.tmp"
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, d / f"{stem}.parquet")      # атомарно: недописанных частей не бывает
    return counts


def work(args) -> dict:
    chunk, out = args
    stem = f"{chunk.chain}-{chunk.start:09d}-{chunk.end:09d}"
    done_marker = out / "_done" / f"{stem}.json"
    if done_marker.exists():                          # возобновление: чанк уже разобран
        return json.loads(done_marker.read_text()) | {"skipped": True}
    t0 = time.time()
    try:
        rows = parse_chunk(chunk)
        counts = _write(rows, out, stem)
        ops = collections.Counter(r["op"] for t in ("ballots", "votes", "list_events", "contracts",
                                                     "contract_events") for r in rows[t])
        res = {"chunk": stem, "kind": chunk.kind, "counts": counts, "ops": dict(ops),
               "first_ts": rows["blocks"][0]["block_ts"] if rows["blocks"] else None,
               "last_ts": rows["blocks"][-1]["block_ts"] if rows["blocks"] else None,
               "sec": round(time.time() - t0, 2)}
        done_marker.parent.mkdir(parents=True, exist_ok=True)
        tmpm = done_marker.with_suffix(".tmp"); tmpm.write_text(json.dumps(res)); os.replace(tmpm, done_marker)
        return res
    except Exception as e:  # noqa: BLE001 — ошибка одного чанка не должна ронять весь прогон
        return {"chunk": stem, "error": repr(e), "trace": traceback.format_exc()[-2000:]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", type=Path, default=config.DERIVED / "blocks")
    ap.add_argument("--limit", type=int, default=0, help="взять только N чанков (для теста)")
    ap.add_argument("--chains", default="", help="через запятую: shard-0,shard-3")
    ap.add_argument("--min-bytes", type=int, default=0, help="тест: брать чанки не меньше N байт")
    ap.add_argument("--time-budget", type=float, default=0,
                    help="секунд; по истечении корректно остановиться (для сред с лимитом времени на команду)")
    a = ap.parse_args(argv)

    chunks, report = plan()
    if not a.time_budget:
        print("Покрытие цепочек:", json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    done_dir = a.out / "_done"
    if done_dir.exists():                              # не планировать уже готовое
        done = {p.stem for p in done_dir.glob("*.json")}
        chunks = [c for c in chunks if f"{c.chain}-{c.start:09d}-{c.end:09d}" not in done]
    if a.chains:
        keep = set(a.chains.split(","))
        chunks = [c for c in chunks if c.chain in keep]
    if a.min_bytes:
        chunks = [c for c in chunks if (c.size or Path(c.path).stat().st_size) >= a.min_bytes]
    # крупные чанки первыми — лучше балансировка по процессам
    chunks.sort(key=lambda c: -(c.size or Path(c.path).stat().st_size))
    if a.limit:
        chunks = chunks[: a.limit]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "_coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))

    total = collections.Counter(); errors = []; t0 = time.time()
    print(f"Чанков: {len(chunks)}, процессов: {a.workers}, выход: {a.out}", flush=True)
    ctx = get_context("spawn")                        # одинаково в Windows и Linux
    stopped = False
    with ctx.Pool(a.workers, maxtasksperchild=50) as pool:
        for i, res in enumerate(pool.imap_unordered(work, [(c, a.out) for c in chunks]), 1):
            if a.time_budget and time.time() - t0 > a.time_budget:
                print(f"Лимит времени: остановка после {i} чанков (готовое сохранено)", flush=True)
                pool.terminate(); stopped = True
                break
            if "error" in res:
                errors.append(res); print("ОШИБКА", res["chunk"], res["error"], flush=True)
                continue
            total.update(res.get("ops", {}))
            if i % 25 == 0 or i == len(chunks):
                el = time.time() - t0
                print(f"[{i}/{len(chunks)}] {el/60:.1f} мин, осталось ~{el/i*(len(chunks)-i)/60:.1f} мин; "
                      f"ballots={total['blindSigIssue']:,} votes={total['vote']:,}", flush=True)
    remaining = len(chunks) - (i if chunks else 0) if stopped else 0
    summary = {"chunks": len(chunks), "remaining_approx": remaining, "errors": len(errors), "ops": dict(total),
               "minutes": round((time.time() - t0) / 60, 1)}
    if stopped:
        print("ЧАСТИЧНО:", json.dumps(summary, ensure_ascii=False), flush=True)
        return 3
    (a.out / "_summary.json").write_text(json.dumps({**summary, "error_list": errors}, ensure_ascii=False, indent=1))
    print("ИТОГ:", json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
