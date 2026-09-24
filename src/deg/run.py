"""Запускатель модулей с логом: python -m deg.run <модуль> [аргументы]

Дублирует весь вывод (stdout+stderr) в logs/<модуль>-<ГГГГММДД-ЧЧММСС>.log в UTF-8.
Нужен потому, что конвейер PowerShell (Tee-Object) в Windows портит кириллицу и пишет UTF-16.
"""
from __future__ import annotations

import runpy
import sys
from datetime import datetime

from . import config


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    mod = sys.argv[1]
    log = config.LOGS / f"{mod}-{datetime.now():%Y%m%d-%H%M%S}.log"
    f = open(log, "w", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.__stdout__, f)
    sys.stderr = Tee(sys.__stderr__, f)
    print(f"# {datetime.now():%Y-%m-%d %H:%M:%S}  deg.{mod} {' '.join(sys.argv[2:])}\n# log: {log}")
    sys.argv = [f"deg.{mod}"] + sys.argv[2:]
    code = 0
    try:
        runpy.run_module(f"deg.{mod}", run_name="__main__", alter_sys=True)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        import traceback; traceback.print_exc(); code = 1
    print(f"# exit code {code}; log: {log}")
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    f.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
