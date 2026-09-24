"""Криптографическая проверка итогов ДЭГ-2026 независимым верификатором (режим --results-only).

Запуск: run.cmd crypto_verify [--full] [--chains shard-0,shard-3] [--workers 3]
Что проверяется (--results-only): агрегация зашифрованных голосов (ElGamal на эллиптической кривой) по каждому
голосованию, два доказательства Chaum–Pedersen корректности расшифровки и совпадение расшифрованной суммы
с опубликованным RESULTS. Полный режим (--full) дополнительно проверяет подписи и ZKP каждого бюллетеня
(по оценке автора верификатора — порядка сотни CPU-часов на 2026 год).

Верификатор — открытая реализация С. Слотина (github.com/sslotin/elections, feddeg/verification/opensource,
снимок из elections-main.zip, коммит dcc332c1). Мы его не модифицируем: копируем как есть в
data/external/verifier/ и запускаем по одной цепочке. Ссылка в тексте обязательна.

Стрибог (ГОСТ Р 34.11-2012) верификатор берёт из libgcrypt. В Windows нужен GnuPG (gnupg.org → «Simple installer
for GnuPG»): модуль сам найдёт libgcrypt-20.dll в стандартных местах и проверит контрольный пример из стандарта.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import config

VERIFIER_DIR = config.EXTERNAL / "verifier"
PREFIX = "feddeg/verification/opensource/"
GOST_M1 = b"012345678901234567890123456789012345678901234567890123456789012"
GOST_H1 = "9d151eefd8590b89daa6ba6cb74af9275dd051026bb149a452fd84e5e57b5500"   # ГОСТ Р 34.11-2012, пример 1 (256 бит)
DLL_DIRS = [r"C:\Program Files (x86)\gnupg\bin", r"C:\Program Files\gnupg\bin",
            r"C:\Program Files (x86)\GnuPG\bin", r"C:\Program Files (x86)\Gpg4win\bin"]


def extract_verifier() -> Path:
    if (VERIFIER_DIR / "verify.py").exists():
        return VERIFIER_DIR
    with zipfile.ZipFile(config.SLOTIN_REPO_ZIP) as z:
        for n in z.namelist():
            i = n.find(PREFIX)
            if i >= 0 and not n.endswith("/") and n.endswith(".py"):
                rel = n[i + len(PREFIX):]
                dst = VERIFIER_DIR / rel; dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(z.read(n))
    return VERIFIER_DIR


def find_gcrypt() -> str | None:
    if os.name != "nt":
        return None                                  # в Linux verifier найдёт libgcrypt сам
    for d in [os.environ.get("GCRYPT_DIR", "")] + DLL_DIRS:
        if d and (Path(d) / "libgcrypt-20.dll").exists():
            return d
    return None


def selftest_streebog(dll_dir: str | None) -> bool:
    try:
        if os.name == "nt":
            os.add_dll_directory(dll_dir)
            g = ctypes.CDLL(str(Path(dll_dir) / "libgcrypt-20.dll"))
        else:
            g = ctypes.CDLL("libgcrypt.so.20")
        g.gcry_check_version.restype = ctypes.c_char_p; g.gcry_check_version(None)
        alg = g.gcry_md_map_name(b"STRIBOG256"); out = ctypes.create_string_buffer(32)
        g.gcry_md_hash_buffer(alg, out, GOST_M1, len(GOST_M1))
        return out.raw.hex() == GOST_H1
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="")
    ap.add_argument("--workers", type=int, default=2, help="процессов внутри одной цепочки (до ~1 ГБ RAM каждый)")
    ap.add_argument("--parallel", type=int, default=4, help="сколько цепочек одновременно")
    ap.add_argument("--years", default="", help="дополнительно проверить сырые дампы прошлых лет: 2024,2025")
    ap.add_argument("--full", action="store_true", help="полная проверка (очень долго)")
    a = ap.parse_args(argv)
    vdir = extract_verifier()
    dll = find_gcrypt()
    if os.name == "nt" and not dll:
        print("Не найден libgcrypt-20.dll. Установите GnuPG для Windows (https://gnupg.org/download/ → Simple installer)"
              " или укажите папку в переменной GCRYPT_DIR."); return 2
    if not selftest_streebog(dll):
        print("Стрибог не прошёл контрольный пример ГОСТ — проверка не запускается."); return 2
    print("Стрибог: контрольный пример ГОСТ Р 34.11-2012 пройден.", flush=True)
    dumps = config.DERIVED / "chain_dumps"
    files = sorted(dumps.glob("shard-*.jsonl.gz"))
    if a.chains:
        keep = set(a.chains.split(",")); files = [f for f in files if f.name.split(".")[0] in keep]
    for y in filter(None, a.years.split(",")):
        z = config.EXTERNAL / "raw_dumps" / f"edg{y}.zip"
        if z.exists():
            files.append(z)
    if not files:
        print("Нет data/derived/chain_dumps/*.jsonl.gz — сначала run.cmd make_chain_dumps"); return 2
    env = dict(os.environ, PYTHONUTF8="1")
    if dll:
        env["PATH"] = dll + os.pathsep + env.get("PATH", "")
    procs, results, t0 = {}, {}, time.time()
    queue = list(files)
    while queue or procs:
        while queue and len(procs) < a.parallel:
            f = queue.pop(0); name = f.name.split(".")[0]
            log = open(config.LOGS / f"verify-{name}.log", "w", encoding="utf-8")
            cmd = [sys.executable, str(vdir / "verify.py"), str(f), "--workers", str(a.workers)]
            if not a.full:
                cmd.append("--results-only")
            # cwd = папка GnuPG: verifier ищет libgcrypt-20.dll по относительному имени
            procs[name] = (subprocess.Popen(cmd, cwd=dll or vdir, stdout=log, stderr=subprocess.STDOUT, env=env), log)
            print(f"[{(time.time()-t0)/60:5.1f} мин] запущено: {name}", flush=True)
        time.sleep(5)
        for name, (p, log) in list(procs.items()):
            if p.poll() is not None:
                log.close(); results[name] = p.returncode; del procs[name]
                tail = (config.LOGS / f"verify-{name}.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
                print(f"[{(time.time()-t0)/60:5.1f} мин] {name}: код {p.returncode} — {tail[0] if tail else ''}", flush=True)
    meaning = {0: "проверено", 1: "НАРУШЕНИЕ/ошибка", 2: "неполная проверка"}
    summ = {n: {"code": c, "meaning": meaning.get(c, "?")} for n, c in sorted(results.items())}
    (config.DERIVED / "crypto_verify.json").write_text(json.dumps({"mode": "full" if a.full else "results-only",
                                                                  "minutes": round((time.time()-t0)/60, 1),
                                                                  "chains": summ}, ensure_ascii=False, indent=1))
    print(json.dumps(summ, ensure_ascii=False))
    return max(results.values()) if results else 2


if __name__ == "__main__":
    sys.exit(main())
