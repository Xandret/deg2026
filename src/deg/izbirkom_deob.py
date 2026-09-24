"""Восстановление таблиц izbirkom.ru из ответа отчёта (JSON {success, html, script}).

Как устроена защита (сентябрь 2026). Сервер отдаёт HTML таблицы с испорченными числами и JS-скрипт,
который после загрузки выполняет последовательность операций над ячейками. Имена функций и
переменных случайные в каждом ответе, но типов операций пять:
  SET     (класс, значение)          — всем элементам класса присвоить значение
  CUT     (класс, позиция)           — удалить один символ на позиции (−1 = последний)
  MOVE    (симв, откуда, куда, ячейка, точка) — взять символ из одной ячейки и вставить в другую
  SWAP    (ячейка1, ячейка2)         — обменять содержимое
  OVERLAY (класс, ячейка)            — наложить поверх ячейки другой элемент; видно его содержимое
Номера ячеек — индексы <td> внутри таблицы в порядке документа; «содержимое ячейки» — самый
глубокий последний потомок (функция lec в скрипте).

Мы не исполняем JS: разбираем тело каждой функции, определяем её тип и роли параметров по
шаблону, затем применяем операции к дереву lxml в том же порядке, что и браузер. Результат
проверяется: во всех числовых ячейках должны остаться только цифры/точка/%/пробел.

Важно: модуль работает ТОЛЬКО с ответами, полученными человеком в обычном браузере (HAR).
Автоматический обход антибот-проверки сайта (challenge/solve) мы сознательно не делаем.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from lxml import html as LH

FUNC_RE = re.compile(r"var\s+(\w+)\s*=\s*function\s*\(([^)]*)\)\s*\{")


def _extract_functions(script: str) -> dict[str, tuple[list[str], str]]:
    out = {}
    for m in FUNC_RE.finditer(script):
        name, params = m.group(1), [p.strip() for p in m.group(2).split(",") if p.strip()]
        i, depth = m.end(), 1
        while depth and i < len(script):
            depth += {"{": 1, "}": -1}.get(script[i], 0); i += 1
        out[name] = (params, script[m.end(): i - 1])
    return out


def _classify(params: list[str], body: str) -> tuple[str, dict[str, int]] | None:
    """Тип операции и индексы параметров по ролям."""
    P = {p: k for k, p in enumerate(params)}
    if "charAt(" in body:                                             # MOVE
        lecs = re.findall(r"var\s+(\w+)\s*=\s*lec\(\s*\w+\[(\w+)\]\s*\)", body)
        src_var = re.search(r"(\w+)\.innerHTML\.trim\(\)\.charAt\((\w+)\)", body)
        src_td = next(p for v, p in lecs if v == src_var.group(1))
        dst_td = next(p for v, p in lecs if v != src_var.group(1))
        dot = re.search(r"if\s*\(\s*(\w+)\s*!==", body).group(1)
        chv = re.search(r"var\s+(\w+)\s*=\s*\w+\.innerHTML\.trim\(\)\.charAt", body).group(1)
        ins = re.search(r"\.splice\(\s*(\w+)\s*,\s*\w+\s*,\s*" + chv + r"\s*\)", body).group(1)
        return "MOVE", dict(ch=P[src_var.group(2)], src=P[src_td], ins=P[ins], dst=P[dst_td], dot=P[dot])
    if "getBoundingClientRect" in body:                                # OVERLAY
        cls = re.search(r"getElementsByClassName\((\w+)\)", body).group(1)
        td = re.search(r"getElementsByTagName\(\w+\)\[(\w+)\]", body).group(1)
        return "OVERLAY", dict(cls=P[cls], td=P[td])
    if re.search(r"\.splice\(\s*(\w+)\s*,\s*1\s*\)", body):              # CUT
        cls = re.search(r"getElementsByClassName\((\w+)\)", body).group(1)
        pos = re.search(r"\.splice\(\s*(\w+)\s*,\s*1\s*\)", body).group(1)
        return "CUT", dict(cls=P[cls], pos=P[pos])
    if body.count("lec(") >= 2 and "getElementsByTagName" in body:      # SWAP
        a, b = re.findall(r"lec\(\s*\w+\[(\w+)\]\s*\)", body)[:2]
        return "SWAP", dict(a=P[a], b=P[b])
    m = re.search(r"getElementsByClassName\((\w+)\)", body)
    v = re.search(r"\.innerHTML\s*=\s*(\w+)\s*;", body)
    if m and v and v.group(1) in P:                                     # SET
        return "SET", dict(cls=P[m.group(1)], val=P[v.group(1)])
    return None


def _args(s: str) -> list:
    out = []
    for tok in re.findall(r"'[^']*'|\"[^\"]*\"|[\w.\-]+", s):
        if tok[0] in "'\"": out.append(tok[1:-1])
        elif tok == "false": out.append(False)
        elif tok == "true": out.append(True)
        elif re.fullmatch(r"-?\d+", tok): out.append(int(tok))
        else: out.append(tok)                                          # имя корневого элемента
    return out


def _jsplice(arr: list, start: int, delete: int, *items):
    n = len(arr)
    if start < 0: start = max(n + start, 0)
    start = min(start, n)
    arr[start:start + delete] = list(items)


def _lec(el):
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids: return el
    last = kids[-1]
    return _lec(last) if [c for c in last if isinstance(c.tag, str)] else last


def _get(el) -> str:
    return (el.text or "") + "".join(LH.tostring(c, encoding="unicode") for c in el)


def _set(el, value: str):
    for c in list(el): el.remove(c)
    el.text = value


def _by_class(root, cls):
    return root.xpath(f".//*[contains(concat(' ', normalize-space(@class), ' '), ' {cls} ')]")


def deobfuscate(payload: dict):
    """payload — JSON ответа отчёта. Возвращает (таблица lxml, лог операций)."""
    doc = LH.fromstring(f"<div>{payload['html']}</div>")
    script = payload["script"]
    funcs = {n: _classify(p, b) for n, (p, b) in _extract_functions(script).items()}
    funcs = {n: v for n, v in funcs.items() if v}
    main = script[script.find("var a = function(){"):]
    main = main[: main.find("listen(window")] if "listen(window" in main else main
    root_cls = re.search(r"getElementsByClassName\('(\w+)'\)\[0\]", main).group(1)
    table = _by_class(doc, root_cls)[0]
    tds = table.xpath(".//td")
    log = []
    for m in re.finditer(r"(\w+)\(([^()]*)\)", main):
        name = m.group(1)
        if name not in funcs: continue
        kind, r = funcs[name]; a = _args(m.group(2))
        if kind == "SET":
            for el in _by_class(table, a[r["cls"]]): _set(el, str(a[r["val"]]))
        elif kind == "CUT":
            for el in _by_class(table, a[r["cls"]]):
                v = list(_get(el)); 
                if v: _jsplice(v, a[r["pos"]], 1); _set(el, "".join(v))
        elif kind == "MOVE":
            src, dst = _lec(tds[a[r["src"]]]), _lec(tds[a[r["dst"]]])
            v = list(_get(dst))
            if a[r["dot"]] is not False: _jsplice(v, a[r["dot"]], 0, ".")
            ch = _get(src).strip()[a[r["ch"]]: a[r["ch"]] + 1]
            _jsplice(v, a[r["ins"]], 0, ch); _set(dst, "".join(v))
        elif kind == "SWAP":
            x, y = _lec(tds[int(a[r["a"]])]), _lec(tds[int(a[r["b"]])])
            vx, vy = _get(x), _get(y); _set(x, vy); _set(y, vx)
        elif kind == "OVERLAY":
            ov = _by_class(table, a[r["cls"]])
            if ov:
                _set(_lec(tds[a[r["td"]]]), ov[0].text_content()); ov[0].getparent().remove(ov[0])
        log.append((kind, a[:-1]))
    return table, log


NUM_OK = re.compile(r"^[\d\s.,%]*$")


def table_rows(table) -> list[list[str]]:
    rows = []
    for tr in table.xpath(".//tr"):
        cells = [" ".join(c.text_content().split()) for c in tr.xpath("./td|./th")]
        if cells: rows.append(cells)
    return rows


def check(rows: list[list[str]]) -> list[str]:
    """Ячейки, похожие на числа, но с «мусором» — признак неверной расшифровки."""
    bad = []
    for r in rows:
        for c in r:
            if re.search(r"\d", c) and not NUM_OK.match(c) and len(c) < 20:
                bad.append(c)
    return bad


def reports_from_har(har_path) -> list[dict]:
    h = json.load(open(har_path, encoding="utf-8"))
    out = []
    for e in h["log"]["entries"]:
        url = e["request"]["url"]
        txt = e["response"].get("content", {}).get("text")
        if "/reports/" in url and "data=" in url and txt and e["response"]["status"] == 200:
            try:
                p = json.loads(txt)
            except json.JSONDecodeError:
                continue
            if p.get("html") and p.get("script"):
                out.append({"url": url, "payload": p, "time": e.get("startedDateTime")})
    return out


if __name__ == "__main__":
    import sys
    for rep in reports_from_har(sys.argv[1]):
        t, log = deobfuscate(rep["payload"])
        rows = table_rows(t)
        print("#", rep["time"], "| операций:", len(log), "| подозрительных ячеек:", check(rows)[:10])
        for r in rows[:40]:
            print(" | ".join(r))
