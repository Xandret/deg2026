"""Склейка чанков day3 в один файл на цепочку: data/derived/chain_dumps/shard-N.jsonl.gz

Нужно для криптопроверки (verify.py читает цельный поток блоков). Многочленный gzip (несколько gzip-потоков
подряд) — корректный формат, поэтому чанки копируются байт-в-байт без перепаковки; только чанк с частичным
перекрытием (min_height > start) распаковывается и фильтруется по высоте.
Запуск: run.cmd make_chain_dumps
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os

import orjson

from . import config
from .sources import plan


def main():
    chunks, report = plan()
    out = config.DERIVED / "chain_dumps"; out.mkdir(parents=True, exist_ok=True)
    by = {}
    for c in chunks:
        by.setdefault(c.chain, []).append(c)
    summary = {}
    for chain, cs in sorted(by.items()):
        cs.sort(key=lambda c: c.start)
        dst = out / f"{chain}.jsonl.gz"; tmp = dst.with_suffix(".tmp")
        h = hashlib.sha256(); n_bytes = 0
        with open(tmp, "wb") as f:
            for c in cs:
                raw = c.read_bytes()
                if c.min_height > c.start or not c.gz:
                    data = gzip.decompress(raw) if c.gz else raw
                    keep = [ln for ln in data.splitlines() if ln.strip() and c.min_height <= orjson.loads(ln)["height"] <= c.end]
                    raw = gzip.compress(b"\n".join(keep) + b"\n")
                f.write(raw); h.update(raw); n_bytes += len(raw)
        os.replace(tmp, dst)
        summary[chain] = {"chunks": len(cs), "first": cs[0].start, "last": cs[-1].end, "bytes": n_bytes, "sha256": h.hexdigest()}
        print(chain, summary[chain], flush=True)
    (out / "_manifest.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
