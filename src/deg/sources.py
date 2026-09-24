"""Поиск «чанков» сырого дампа блокчейна ДЭГ и проверка их непрерывности.

Чанк — файл `<from>-<to>.jsonl.gz` (одна строка = один блок в JSON), обычно 100 блоков.
Источники дампа day3 (единственный полный, 20.09 18:08–21:51 МСК):
  * edg_day3_safe/shard-N/          — распакованные каталоги (shard-1..6 целиком, shard-0 с блока 27501)
  * edg_day3_safe.tar               — tar (обрезан в конце), в нём shard-0 блоки 1–27700

Tar не сжат, поэтому данные члена можно читать напрямую по смещению (offset_data) —
это позволяет раздать чтение по процессам без распаковки tar на диск.

Файлы `*.history-*` — старые версии перезаписанных хвостовых чанков, их пропускаем.
"""
from __future__ import annotations

import json
import re
import tarfile
from dataclasses import dataclass, replace
from pathlib import Path

from . import config

CHUNK_RE = re.compile(r"^(\d+)-(\d+)\.jsonl(\.gz)?$")


@dataclass(frozen=True)
class Chunk:
    chain: str            # имя каталога цепочки: shard-0 … shard-6
    start: int            # первая высота блока
    end: int              # последняя высота блока (включительно)
    kind: str             # "file" | "tar"
    path: str             # путь к файлу или к tar
    offset: int = 0       # для tar: смещение данных члена
    size: int = 0         # для tar: размер данных члена
    gz: bool = True
    min_height: int = 0   # блоки ниже отбрасываются (при частичном перекрытии чанков)

    def read_bytes(self) -> bytes:
        if self.kind == "file":
            return Path(self.path).read_bytes()
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(self.size)
        if len(data) != self.size:
            raise IOError(f"tar member truncated: {self}")
        return data


def _dir_chunks(root: Path) -> list[Chunk]:
    out = []
    for shard_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("shard-")):
        for f in shard_dir.iterdir():
            m = CHUNK_RE.match(f.name)
            if m:
                out.append(Chunk(shard_dir.name, int(m[1]), int(m[2]), "file", str(f), gz=bool(m[3])))
    return out


def _tar_chunks(tar_path: Path) -> list[Chunk]:
    """Читает заголовки tar последовательно; на обрыве архива останавливается."""
    out = []
    try:
        with tarfile.open(tar_path, "r:") as tf:
            for ti in tf:
                if not ti.isfile():
                    continue
                parts = Path(ti.name).parts          # edg_day3_safe/shard-0/000000001-000000100.jsonl.gz
                if len(parts) < 3:
                    continue
                m = CHUNK_RE.match(parts[-1])
                if m:
                    out.append(Chunk(parts[-2], int(m[1]), int(m[2]), "tar", str(tar_path),
                                     ti.offset_data, ti.size, bool(m[3])))
    except (tarfile.ReadError, EOFError, OSError):
        pass  # обрезанный tar: берём всё, что успели прочитать
    return out


def plan(day3_dir: Path = config.DAY3_DIR, day3_tar: Path = config.DAY3_TAR) -> tuple[list[Chunk], dict]:
    """Собирает непрерывное покрытие по каждой цепочке: файлы каталога приоритетнее tar.

    Возвращает (список чанков, отчёт о покрытии {chain: {...}}).
    """
    files = _dir_chunks(day3_dir)
    tars = _tar_chunks(day3_tar) if day3_tar.exists() else []
    by_chain: dict[str, list[Chunk]] = {}
    for c in files + tars:
        by_chain.setdefault(c.chain, []).append(c)

    chosen: list[Chunk] = []
    report = {}
    for chain, cs in sorted(by_chain.items()):
        # сначала файлы, потом tar; при равном старте — файл
        cs.sort(key=lambda c: (c.start, 0 if c.kind == "file" else 1, -c.end))
        covered = 0
        picked, gaps, skipped = [], [], 0
        for c in cs:
            if c.end <= covered:
                skipped += 1
                continue
            if c.start != covered + 1:
                if c.start > covered + 1:
                    gaps.append([covered + 1, c.start - 1])
                else:
                    # частичное перекрытие: чанк начинается внутри уже покрытого диапазона.
                    # Блоки <= covered парсер отбросит (фильтр min_height).
                    pass
            picked.append((c, covered + 1))
            covered = c.end
        for c, min_h in picked:
            chosen.append(replace(c, min_height=min_h))
        state = day3_dir / chain / "state.json"
        fp = json.loads(state.read_text())["chain_fingerprint"] if state.exists() else None
        report[chain] = {
            "chunks": len(picked), "first": picked[0][0].start if picked else None, "last": covered,
            "gaps": gaps, "skipped_duplicates": skipped,
            "from_tar": sum(1 for c, _ in picked if c.kind == "tar"), "fingerprint": fp,
        }
    return chosen, report


if __name__ == "__main__":
    chunks, rep = plan()
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    print("total chunks:", len(chunks))
