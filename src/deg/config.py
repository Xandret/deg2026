"""Пути проекта. Всё вычисляется относительно расположения репозитория,
поэтому одинаково работает в Windows и в Linux-VM, где папка смонтирована иначе.

Раскладка:
    <DATA_ROOT>/              — папка «ДЭГ IT»: исходники, которые мы НИКОГДА не изменяем
        edg_day1.zip, edg_day2.zip, edg_day3/edg_day3_safe/..., elections-main.zip, ChatExport_*/
    <DATA_ROOT>/deg2026/      — этот репозиторий
        data/derived/         — всё, что мы вычисляем (parquet, duckdb)
        data/external/        — то, что скачали сами (izbirkom, stat.vybory, публичный edg2026.tar)
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO.parent

DERIVED = REPO / "data" / "derived"
EXTERNAL = REPO / "data" / "external"
LOGS = REPO / "logs"

# --- исходные дампы (read-only) ---
DAY1_ZIP = DATA_ROOT / "edg_day1.zip"
DAY2_ZIP = DATA_ROOT / "edg_day2.zip"
DAY3_DIR = DATA_ROOT / "edg_day3" / "edg_day3_safe"
DAY3_TAR = DATA_ROOT / "edg_day3" / "edg_day3_safe.tar"
SLOTIN_REPO_ZIP = DATA_ROOT / "elections-main.zip"  # только для сверки, не как источник

for _p in (DERIVED, EXTERNAL, LOGS):
    _p.mkdir(parents=True, exist_ok=True)
