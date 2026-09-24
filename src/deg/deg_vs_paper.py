"""ДЭГ против бумаги по сверенным округам (из data/derived/reconcile/reconcile_report.json).

Запуск: run.cmd deg_vs_paper   (сначала run.cmd reconcile_izbirkom)
Бумага = Σ протоколов ТИК; ДЭГ = данные цепочки (совпадают с «ОИК − ΣТИК»).
Для каждого кандидата/партии: доля от действительных на бумаге и в ДЭГ, разница в п.п.
Явка: бумага = выданные (строки 3+4+5) / список (строка 1); ДЭГ = выданные / список ДЭГ.
"""
from __future__ import annotations

import json

import pandas as pd

from . import config


def main():
    rep = json.loads((config.DERIVED / "reconcile" / "reconcile_report.json").read_text(encoding="utf-8"))
    rows, summary = [], []
    for e in rep:
        if e.get("status") != "СОВПАДАЕТ":
            continue
        L = {l["row"]: l for l in e["lines"]}
        paper_valid, deg_valid = L[10]["sum_tik"], L[10]["deg_chain"]
        p_turn = (L[3]["sum_tik"] + L[4]["sum_tik"] + L[5]["sum_tik"]) / L[1]["sum_tik"]
        d_turn = L[2]["deg_chain"] / L[1]["deg_chain"]
        summary.append(dict(oik=e["oik"], protocol=e["protocol"], paper_turnout=round(100 * p_turn, 1),
                            deg_turnout=round(100 * d_turn, 1),
                            deg_share_of_votes=round(100 * deg_valid / (deg_valid + paper_valid), 1)))
        for r in sorted(k for k in L if k >= 13):
            p = 100 * L[r]["sum_tik"] / paper_valid; d = 100 * L[r]["deg_chain"] / deg_valid
            rows.append(dict(oik=e["oik"], protocol=e["protocol"], candidate=L[r]["label"],
                             paper_pct=round(p, 2), deg_pct=round(d, 2), diff_pp=round(d - p, 2)))
    out = config.DERIVED / "deg_vs_paper"; out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "candidates.csv", index=False, encoding="utf-8")
    pd.DataFrame(summary).to_csv(out / "districts.csv", index=False, encoding="utf-8")
    pd.set_option("display.width", 220); pd.set_option("display.max_colwidth", 45)
    print(pd.DataFrame(summary).to_string(index=False)); print()
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
