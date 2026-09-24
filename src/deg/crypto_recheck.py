"""Разбор и перепроверка результатов криптопроверки (этап после crypto_verify).

Запуск:
  run.cmd crypto_recheck parse                      # логи logs/verify-*.log → data/derived/crypto_verify/statuses.csv
  run.cmd crypto_recheck extract [--src shard-3] [--only ID,ID]
        # для непроверенных контрактов (INCOMPLETE/FAILED) — отдельные дампы только с их транзакциями,
        # внутри блока транзакция `results` переставлена в конец (остальной порядок сохранён)
  run.cmd crypto_recheck verify [--src ...] [--allow-bad-ballots]
        # верификатор (без изменений в его коде) на этих дампах; обёртка лишь записывает подробности
        # (итог верификатора, опубликованный итог, проверку частичных расшифровок) в JSON.
        # --allow-bad-ballots: снимает ранний выход «бюллетень не прошёл проверку» — итог сверяется
        #   без испорченного бюллетеня, как его исключила система. Это отдельный, помеченный режим.

Зачем (docs/tech/08-crypto.md): в реальных блоках транзакция `results` нередко стоит ПЕРЕД частичными
расшифровками того же контракта (порядок в блоке не совпадает с временем транзакций). Верификатор читает
строго по порядку и в момент `results` расшифровок ещё не видел → INCOMPLETE. Все данные при этом в том же блоке.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
from pathlib import Path

import orjson

from . import config

# Для процессов-воркеров верификатора (Windows, spawn): DLL и путь к верификатору — на уровне модуля.
if os.environ.get("DEG_GCRYPT_DIR") and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(os.environ["DEG_GCRYPT_DIR"])
if os.environ.get("DEG_VERIFIER_DIR") and os.environ["DEG_VERIFIER_DIR"] not in sys.path:
    sys.path.insert(0, os.environ["DEG_VERIFIER_DIR"])

OUT_V = config.DERIVED / "crypto_verify"
OUT_R = config.DERIVED / "crypto_recheck"
SOURCES = {**{f"shard-{i}": config.DERIVED / "chain_dumps" / f"shard-{i}.jsonl.gz" for i in range(7)},
           "edg2024": config.EXTERNAL / "raw_dumps" / "edg2024.zip",
           "edg2025": config.EXTERNAL / "raw_dumps" / "edg2025.zip"}
ST = re.compile(r"([1-9A-HJ-NP-Za-km-z]{32,44}): (RESULTS VERIFIED[^;]*|VERIFIED|FAILED|INCOMPLETE); (.*)$")


def verifier_dir() -> Path:
    return config.EXTERNAL / "verifier"


def parse_logs():
    OUT_V.mkdir(parents=True, exist_ok=True)
    rows, notes, anomalies = [], [], []
    for src in SOURCES:
        path = config.LOGS / f"verify-{src}.log"
        if not path.exists():
            continue
        last, buf = None, b""
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 24)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf); buf = parts.pop()
                for p in parts:
                    s = p.decode("utf-8", "replace").strip()
                    if not s:
                        continue
                    m = ST.search(s)
                    if m:
                        kv = dict(x.split("=", 1) for x in m.group(3).split("; ") if "=" in x)
                        last = m.group(1)
                        rows.append(dict(src=src, contract=last, status=m.group(2).split(" (")[0], **kv))
                    elif "note:" in s:
                        notes.append(dict(src=src, contract=last, note=s.split("note:", 1)[1].strip()))
                    elif "input anomaly:" in s:
                        m2 = re.search(r"transaction (\S+) for (\S+)", s)
                        anomalies.append(dict(src=src, tx=m2.group(1), contract=m2.group(2)))
        print(f"{src}: {sum(r['src'] == src for r in rows)} голосований", flush=True)
    for name, data in (("statuses", rows), ("notes", notes), ("anomalies", anomalies)):
        with open(OUT_V / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            keys = sorted({k for r in data for k in r}, key=lambda k: (k not in ("src", "contract", "status"), k))
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(data)
    return rows


def targets(src):
    with open(OUT_V / "statuses.csv", encoding="utf-8") as f:
        return {r["contract"] for r in csv.DictReader(f) if r["src"] == src and not r["status"].startswith("RESULTS VERIFIED")}


def tx_contract_op(outer):
    inner = outer.get("tx") if isinstance(outer, dict) else None
    if outer.get("type") != 105 or not isinstance(inner, dict):
        return None, None
    cid = inner.get("id") if inner.get("type") == 103 else inner.get("contractId")
    op = next((p.get("value") for p in inner.get("params") or [] if p.get("key") == "operation"), None)
    return cid, op


def extract(src, only=None, name=None):
    sys.path.insert(0, str(verifier_dir()))
    from crypto.stream import iter_dump_lines          # тот же читатель дампов, что у верификатора
    want = set(only) if only else targets(src)
    OUT_R.mkdir(parents=True, exist_ok=True)
    name = name or src
    dst = OUT_R / f"{name}.jsonl.gz"; tmp = dst.with_suffix(".tmp")
    small = len(want) <= 50
    pats = [w.encode() for w in want]
    n_blocks = n_tx = moved = 0
    with gzip.open(tmp, "wb", compresslevel=3) as out:
        for line in iter_dump_lines(SOURCES[src]):
            if not line.strip():
                continue
            if small:
                b = line.encode() if isinstance(line, str) else line
                if not any(p in b for p in pats):
                    continue
            block = orjson.loads(line)
            keep, tail = [], []
            for outer in block.get("transactions") or []:
                cid, op = tx_contract_op(outer)
                if cid in want:
                    (tail if op == "results" else keep).append(outer)
            if not keep and not tail:
                continue
            moved += sum(1 for t in tail if keep)      # results, после которых в блоке шли другие транзакции
            block["transactions"] = keep + tail
            out.write(orjson.dumps(block) + b"\n")
            n_blocks += 1; n_tx += len(keep) + len(tail)
    os.replace(tmp, dst)
    info = dict(src=src, name=name, contracts=len(want), blocks=n_blocks, transactions=n_tx, results_moved_in_block=moved)
    (OUT_R / f"{name}.extract.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(info, flush=True)


def verify(src, allow_bad=False, workers=1, full=False):
    """Запускает _verify_inner отдельным процессом с нужным окружением (как crypto_verify)."""
    import subprocess
    from .crypto_verify import find_gcrypt
    vdir = verifier_dir(); dll = find_gcrypt() if os.name == "nt" else None
    env = dict(os.environ, PYTHONUTF8="1", DEG_VERIFIER_DIR=str(vdir),
               PYTHONPATH=os.pathsep.join([str(config.REPO / "src"), str(vdir)]))
    if dll:
        env["DEG_GCRYPT_DIR"] = dll; env["PATH"] = dll + os.pathsep + env.get("PATH", "")
    cmd = [sys.executable, "-m", "deg.crypto_recheck", "_inner", "--src", src, "--workers", str(workers)]
    cmd += ["--allow-bad-ballots"] * allow_bad + ["--full"] * full
    log = config.LOGS / f"recheck-{src}{'.allowbad' if allow_bad else ''}{'.full' if full else ''}.log"
    with open(log, "w", encoding="utf-8") as f:
        code = subprocess.call(cmd, env=env, cwd=dll or str(vdir), stdout=f, stderr=subprocess.STDOUT)
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
    print(f"{src}: код {code} — {tail[0] if tail else ''}", flush=True)


def verify_inner(src, allow_bad=False, workers=1, full=False):
    from crypto import stream
    records = []
    orig_emit = stream.StreamVerifier._emit

    def emit(self, result):
        orig_emit(self, result)
        records.append(dict(contract=result.contract, bad_zkp=list(getattr(result, 'bad_zkp', [])), bad_shape=list(getattr(result, 'bad_shape', [])), bad_blind=list(getattr(result, 'bad_blind_signature', [])), wrong_sig=list(getattr(result, 'wrong_tx_signature', [])), revotes=list(getattr(result, 'revotes', [])), verdict=result.verdict, results_match=result.results_match,
                            final_result_verified=result.final_result_verified, accepted=result.accepted,
                            valid=result.valid_bulletins, tally=result.tally, published=getattr(result, "published", None),
                            partial_decryption_ok=getattr(result, "partial_decryption_ok", None),
                            key_aggregation_ok=getattr(result, "key_aggregation_ok", None),
                            notes=list(result.notes), error=result.error))
    stream.StreamVerifier._emit = emit
    if allow_bad:
        orig_fin = stream.Election.finalize

        def finalize(self):
            r = self.result
            if r.valid_bulletins != r.accepted:
                bad, r.accepted = r.accepted, r.valid_bulletins
                try:
                    res = orig_fin(self)
                finally:
                    res.accepted = bad
                res.notes.append(f"[deg2026] проверено без {bad - res.valid_bulletins} испорченных бюллетеней (режим --allow-bad-ballots)")
                return res
            return orig_fin(self)
        stream.Election.finalize = finalize
    code = stream.verify(OUT_R / f"{src}.jsonl.gz", workers=workers, results_only=not full)
    tag = (".allowbad" if allow_bad else "") + (".full" if full else "")
    (OUT_R / f"{src}{tag}.verify.json").write_text(json.dumps(dict(src=src, code=code, records=records),
                                                             ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    ok = sum(1 for r in records if r["final_result_verified"])
    print(f"\n{src}{tag}: голосований {len(records)}, итог подтверждён {ok}, "
          f"не совпал {sum(1 for r in records if r['results_match'] is False)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["parse", "extract", "verify", "_inner"])
    ap.add_argument("--src", default=",".join(SOURCES))
    ap.add_argument("--only", default="")
    ap.add_argument("--name", default="", help="имя выходного дампа (для --only)")
    ap.add_argument("--parallel", type=int, default=1, help="сколько источников обрабатывать одновременно")
    ap.add_argument("--allow-bad-ballots", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--full", action="store_true", help="полная проверка: подписи, слепые подписи, ZKP бюллетеней")
    a = ap.parse_args()
    if a.mode == "parse":
        parse_logs(); return
    srcs = a.src.split(",")
    if a.parallel > 1 and len(srcs) > 1 and a.mode in ("extract", "verify"):
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(a.parallel) as ex:
            if a.mode == "extract":
                list(ex.map(extract, srcs))
            else:
                list(ex.map(verify, srcs, [a.allow_bad_ballots] * len(srcs), [a.workers] * len(srcs), [a.full] * len(srcs)))
        return
    for src in srcs:
        if a.mode == "extract":
            extract(src, [x for x in a.only.split(",") if x] or None, a.name or None)
        elif a.mode == "verify":
            verify(src, a.allow_bad_ballots, a.workers, a.full)
        else:
            verify_inner(src, a.allow_bad_ballots, a.workers, a.full)


if __name__ == "__main__":
    main()
