"""Запуск верификатора с подключённым libgcrypt в Windows.

Python ≥3.8 в Windows не ищет DLL по PATH и в текущей папке, поэтому каталог GnuPG добавляется через
os.add_dll_directory. Делается это на уровне модуля: процессы-воркеры верификатора (multiprocessing, spawn)
заново импортируют главный модуль, и DLL-каталог добавится и в них.
"""
import os
import sys

_D = os.environ.get("DEG_GCRYPT_DIR")
if _D and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(_D)
sys.dont_write_bytecode = True

if __name__ == "__main__":
    from verify import main          # verify.py из data/external/verifier (через PYTHONPATH)
    raise SystemExit(main())
