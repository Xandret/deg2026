"""Парсер сырых дампов ДЭГ прошлых лет (2024, 2025): один большой JSONL внутри zip.

Запуск (Windows):  run.cmd parse_dump_json --year 2025 --workers 12
Выход: data/derived/blocks_<год>/ — те же таблицы, что у parse_blocks (схема общая), цепочка «y<год>».

Отличие от day3-2026: дамп — один поток (одна цепочка), поэтому главный процесс читает zip построчно,
режет на пачки по 100 блоков и раздаёт процессам. Очередь ограничена (--inflight), чтобы не держать
20 ГБ в памяти. Возобновление — по маркерам _done (поток всё равно перечитывается, но разбор пропускается).
Формат транзакций тот же (105 → 104/103, те же operation) — проверено выборкой по edg2025.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import threading
import time
import zipfile
from multiprocessing import get_context
from pathlib import Path

from . import config
from .parse_blocks import _write, parse_data

H_RE = re.compile(rb'"height":\s*(\d+)')
FILES = {2024: ("edg2024.zip", "final.json"), 2025: ("edg2025.zip", "edg2025.json")}


def _height(line: bytes) -> int:
    m = None
    for m in H_RE.finditer(line[-400:]):
        pass
    if m is None:
        m = list(H_RE.finditer(line))[-1]
    return int(m.group(1))


def work(args):
    data, chain, lo, hi, out = args
    stem = f"{chain}-{lo:09d}-{hi:09d}"
    marker = out / "_done" / f"{stem}.json"
    if marker.exists():
        return {"chunk": stem, "skipped": True}
    try:
        rows = parse_data(data, chain, lo, hi)
        counts = _write(rows, out, stem)
        ops = collections.Counter(r["op"] for t in ("ballots", "votes", "list_events", "contracts",
                                                     "contract_events") for r in rows[t])
        res = {"chunk": stem, "counts": counts, "ops": dict(ops)}
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".tmp"); tmp.write_text(json.dumps(res)); os.replace(tmp, marker)
        return res
    except Exception as e:  # noqa: BLE001
        return {"chunk": stem, "error": repr(e)}


def batches(zpath: Path, member: str, size: int):
    with zipfile.ZipFile(zpath) as z, z.open(member) as f:
        buf, lo = [], None
        for line in f:
            if not line.strip():
                continue
            h = _height(line)
            lo = h if lo is None else lo
            buf.append(line)
            if len(buf) >= size:
                yield b"".join(buf), lo, h
                buf, lo = [], None
        if buf:
            yield b"".join(buf), lo, _height(buf[-1])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True, choices=sorted(FILES))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--inflight", type=int, default=0, help="макс. пачек в очереди (по умолчанию 3×workers)")
    ap.add_argument("--limit", type=int, default=0, help="тест: только N пачек")
    a = ap.parse_args(argv)
    zname, member = FILES[a.year]
    zpath = config.EXTERNAL / "raw_dumps" / zname
    out = config.DERIVED / f"blocks_{a.year}"; out.mkdir(parents=True, exist_ok=True)
    chain = f"y{a.year}"
    sem = threading.BoundedSemaphore(a.inflight or 3 * a.workers)
    total = collections.Counter(); errors = []; n_done = 0; t0 = time.time()
    lock = threading.Lock()

    def cb(res):
        nonlocal n_done
        sem.release()
        with lock:
            n_done += 1
            if "error" in res:
                errors.append(res); print("ОШИБКА", res, flush=True)
            else:
                total.update(res.get("ops", {}))
            if n_done % 50 == 0:
                print(f"[{n_done}] {(time.time()-t0)/60:.1f} мин; бюллетеней {total['blindSigIssue']:,}, "
                      f"голосов {total['vote']:,}", flush=True)

    ctx = get_context("spawn")
    with ctx.Pool(a.workers, maxtasksperchild=200) as pool:
        for i, (data, lo, hi) in enumerate(batches(zpath, member, a.batch)):
            if a.limit and i >= a.limit:
                break
            sem.acquire()
            pool.apply_async(work, ((data, chain, lo, hi, out),), callback=cb,
                             error_callback=lambda e: (sem.release(), errors.append(repr(e))))
        pool.close(); pool.join()
    summ = {"year": a.year, "batches": n_done, "errors": len(errors), "ops": dict(total),
            "minutes": round((time.time() - t0) / 60, 1)}
    (out / "_summary.json").write_text(json.dumps({**summ, "error_list": errors[:50]}, ensure_ascii=False, indent=1))
    print("ИТОГ:", json.dumps(summ, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
