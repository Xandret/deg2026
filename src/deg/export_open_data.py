"""Открытые данные для публичного сайта → site/public/data/ (+ архив deg2026_open_data.zip).

Запуск: run.cmd export_open_data   (после site_data, deg_results, izbirkom_snapshot, crypto_recheck summary)
Только агрегаты и публичные идентификаторы (номера УИК, id контрактов, итоги). Никаких user_id и хешей списков.
Лицензия на наши производные данные — CC BY 4.0 (указать «deg2026 / Xandret»).
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from . import config

OUT = config.REPO / "site" / "public" / "data"
D = config.DERIVED


def write_csv(df: pd.DataFrame, name: str, files: list, sheets: dict | None = None):
    """CSV в UTF-8 с BOM: так Excel открывает кириллицу двойным щелчком, а pandas/R читают как обычный UTF-8."""
    for c in df.columns:  # целые без «.0»
        if df[c].dtype.kind == "f" and df[c].dropna().apply(float.is_integer).all():
            df[c] = df[c].astype("Int64")
    p = OUT / name; tmp = p.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig"); os.replace(tmp, p)
    files.append((name, len(df)))
    if sheets is not None:
        sheets[name] = df


# Русские заголовки для книги Excel (CSV остаются с машинными именами колонок)
RU = {"region_code": "Код региона", "region": "Регион", "rc": "Код региона", "contracts": "Голосований (контрактов)",
      "portal_contracts": "Из них видно на портале", "uiks": "УИК", "list": "В списке (итог)", "list_final": "В списке (итог)",
      "ballots": "Выдано бюллетеней (людей)", "init": "Список изначально", "removed": "Исключено из списка",
      "uik50": "УИК со списком ≥ 50", "uik100": "УИК со 100 % явкой", "ballots_all": "Бюллетеней всего (по всем выборам)",
      "votes_all": "Принято голосов всего", "portal_ballots": "Бюллетеней в голосованиях, видных на портале",
      "msk_offset_h": "Сдвиг от МСК, ч", "turnout_pct": "Явка, %", "first_hour_pct": "Первый час, %",
      "unpaired_pct": "Бюллетени без голоса, %", "removed_pct": "Исключено, %", "portal_share_pct": "Видно на портале, %",
      "year": "Год", "hour_msk": "Час (МСК)", "uik": "УИК", "list_initial": "Список изначально", "list_added": "Добавлено",
      "list_removed": "Исключено", "valid": "Действительных голосов", "district": "Округ", "mapping": "Привязка к округу",
      "candidate": "Кандидат", "position": "Позиция в бюллетене", "deg_votes": "Голосов в ДЭГ", "protocol": "Протокол (1 — округ, 2 — партсписки)",
      "row": "Строка протокола", "label": "Название строки", "oik": "Всего по ОИК", "deg": "ДЭГ", "paper": "Участки (всего − ДЭГ)",
      "source": "Источник", "contract_id": "Контракт", "uik_min": "УИК от", "uik_max": "УИК до"}
SHEETS = [("regions_2026.csv", "Регионы 2026"), ("regions_dynamics_2024_2026.csv", "Регионы 2024–2026"),
          ("duma_deg_party_list_by_region.csv", "ГД партсписки ДЭГ"), ("duma_deg_single_mandate_by_district.csv", "ГД округа ДЭГ"),
          ("duma_paper_vs_deg_protocol_lines.csv", "ГД бумага и ДЭГ"), ("regions_hourly_2026.csv", "По часам"),
          ("uiks_2026.csv", "Участки")]


def write_xlsx(sheets: dict, desc: dict, path: Path):
    from openpyxl.styles import Font
    tmp = path.with_suffix(".tmp.xlsx")
    with pd.ExcelWriter(tmp, engine="openpyxl") as xw:
        about = pd.DataFrame({"": ["ДЭГ-2026 в открытых данных — таблицы для Excel",
                                   "Автор — Xandret. Лицензия CC BY 4.0: можно использовать с указанием источника «deg2026 / Xandret».",
                                   "Источник — копии блокчейна федерального ДЭГ для наблюдателей, портал наблюдения, протоколы izbirkom.ru.",
                                   "Персональных данных нет: только суммы по регионам, участкам и голосованиям.",
                                   "Полный набор (CSV, в т.ч. все 5 103 голосования и криптопроверка) — в архиве deg2026_open_data.zip.", ""]
                                  + [f"«{sh}» — {desc.get(f, '')}" for f, sh in SHEETS if f in sheets]})
        about.to_excel(xw, sheet_name="О данных", index=False, header=False)
        for f, sh in SHEETS:
            if f in sheets:
                sheets[f].rename(columns=RU).to_excel(xw, sheet_name=sh, index=False, freeze_panes=(1, 1))
        for ws in xw.book.worksheets:
            if ws.title == "О данных":
                ws.column_dimensions["A"].width = 120; ws["A1"].font = Font(bold=True, size=13); continue
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for col in ws.columns:
                w = max(len(str(c.value or "")) for c in list(col)[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(8, w + 2), 60)
            ws.auto_filter.ref = ws.dimensions
    os.replace(tmp, path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = []
    sheets: dict = {}
    sd = json.loads((D / "site" / "site_data.json").read_text(encoding="utf-8"))["regions"]
    reg = pd.DataFrame([{k: r.get(k) for k in ("code", "name", "contracts", "portal_contracts", "uiks", "list", "ballots",
                                                "init", "removed", "uik50", "uik100", "ballots_all", "votes_all", "portal_ballots", "toff")}
                        for r in sd])
    reg["turnout_pct"] = (100 * reg.ballots / reg.list).round(2)
    reg["first_hour_pct"] = [r["dyn"].get("2026", {}).get("first_hour") for r in sd]
    reg["unpaired_pct"] = (100 * (1 - reg.votes_all / reg.ballots_all)).round(3)
    reg["removed_pct"] = (100 * reg.removed / reg.init).round(3)
    reg["portal_share_pct"] = (100 * reg.portal_ballots.fillna(0) / reg.ballots_all).round(1)
    reg = reg.rename(columns={"code": "region_code", "name": "region", "toff": "msk_offset_h"})
    write_csv(reg, "regions_2026.csv", files, sheets)

    rows = []
    for r in sd:
        for h, n in enumerate(r["hourly"]):
            if n:
                rows.append(dict(region_code=r["code"], region=r["name"],
                                 hour_msk=str(pd.Timestamp("2026-09-18") + pd.Timedelta(hours=h)), ballots=n))
    write_csv(pd.DataFrame(rows), "regions_hourly_2026.csv", files, sheets)

    con = duckdb.connect(str(D / "deg2026.duckdb"), read_only=True)
    con.execute("SET memory_limit='3GB'")
    uik = con.sql("""
        WITH rep AS (SELECT rc, uik, contract_id cid, n FROM
               (SELECT region_code rc, uik, contract_id, count(*) n FROM ballots GROUP BY ALL)
               QUALIFY row_number() OVER (PARTITION BY rc, uik ORDER BY n DESC, contract_id) = 1)
        SELECT r.rc::INT region_code, r.uik, r.n ballots,
               sum(CASE WHEN l.op='addVotersList' THEN l.n ELSE 0 END) list_initial,
               sum(CASE WHEN l.op='addToVotersList' THEN l.n ELSE 0 END) list_added,
               sum(CASE WHEN l.op='removeFromVotersList' THEN l.n ELSE 0 END) list_removed
        FROM rep r JOIN list_events l ON l.contract_id = r.cid AND l.uik = r.uik GROUP BY ALL ORDER BY 1, 2""").df()
    uik["list_final"] = uik.list_initial + uik.list_added - uik.list_removed
    uik["turnout_pct"] = (100 * uik.ballots / uik.list_final.where(uik.list_final > 0)).round(2)
    names = dict(zip(reg.region_code, reg.region)); uik.insert(1, "region", uik.region_code.map(names))
    write_csv(uik, "uiks_2026.csv", files, sheets)

    dyn = pd.read_csv(D / "dynamics" / "regions_by_year.csv"); dyn = dyn[dyn.source == "ours"].drop(columns=["source"])
    write_csv(dyn.rename(columns={"list": "list_final", "turnout": "turnout_pct", "first_hour": "first_hour_pct",
                                  "unpaired": "unpaired_pct"}), "regions_dynamics_2024_2026.csv", files, sheets)

    for src, name in (("deg_results/pl_by_region.csv", "duma_deg_party_list_by_region.csv"),
                      ("deg_results/pl_by_tik.csv", "duma_deg_party_list_by_tik.csv"),
                      ("deg_results/sm_by_district.csv", "duma_deg_single_mandate_by_district.csv"),
                      ("izbirkom_snapshot/paper_vs_deg_lines.csv", "duma_paper_vs_deg_protocol_lines.csv")):
        write_csv(pd.read_csv(D / src), name, files, sheets)

    dc = pd.read_csv(D / "deg_results" / "duma_contracts.csv")
    pc = pd.read_parquet(D / "portal" / "contracts.parquet")[["contract_id", "election_name", "level", "district_name"]]
    allc = con.sql("""SELECT b.contract_id, mode(b.region_code)::INT region_code, count(*) ballots
                      FROM ballots b GROUP BY 1""").df()
    votes = con.sql("SELECT contract_id, count(*) votes FROM votes GROUP BY 1").df()
    allc = allc.merge(votes, on="contract_id", how="left").merge(pc, on="contract_id", how="left") \
               .merge(dc[["contract_id", "kind", "district", "district_how", "tik_pair"]], on="contract_id", how="left")
    allc["on_portal"] = allc.election_name.notna()
    allc.insert(2, "region", allc.region_code.map(names))
    write_csv(allc, "contracts_2026.csv", files)

    st = pd.read_csv(D / "crypto_verify" / "statuses.csv")[["src", "contract", "status"]]
    summ = json.loads((D / "crypto_verify" / "summary.json").read_text(encoding="utf-8"))
    cls = {(x["src"], x["contract"]): x["cls"] for x in summ["not_first_pass"]}
    st["verified_how"] = [cls.get((s, c), "first_pass") for s, c in zip(st.src, st.contract)]
    st["label"] = st.verified_how.map(summ["labels"])
    st.insert(0, "year", st.src.map({"edg2024": 2024, "edg2025": 2025}).fillna(2026).astype(int))
    write_csv(st.rename(columns={"src": "chain", "status": "first_pass_status"}), "crypto_verification_2024_2026.csv", files)

    for f in ("timeline.json", "sources.json"):
        shutil.copy(config.REPO / "data" / "news" / f, OUT / f"news_{f}"); files.append((f"news_{f}", None))

    readme = ["# Открытые данные deg2026 — ДЭГ-2026 по данным блокчейна", "",
              "Производные данные проекта deg2026 (автор — Xandret). Лицензия CC BY 4.0: можно использовать и "
              "перерабатывать с указанием источника «deg2026 / Xandret».",
              "Источники: дампы блокчейна федерального ДЭГ (копии для наблюдателей), портал наблюдения "
              "stat.vybory.gov.ru, протоколы izbirkom.ru (в т.ч. открытый снимок github.com/Arhydemon/elections_20_09_26, "
              "проверенный нами), верификатор С. Слотина. Методика — раздел «Проверка» на сайте и docs/ в репозитории.",
              "Персональных данных нет: только агрегаты, номера комиссий и идентификаторы голосований.", "",
              "CSV — UTF-8 с BOM, разделитель запятая. В Excel удобнее открыть deg2026_tables.xlsx или «Данные → Из текста/CSV».", "",
              "| Файл | Строк | Что внутри |", "|---|---:|---|"]
    desc = {"regions_2026.csv": "32 региона: список, явка ДЭГ, первый час, бюллетени без голоса, УИК со 100 %, исключения, видимость на портале",
            "regions_hourly_2026.csv": "выдача бюллетеней по часам (МСК) и регионам",
            "uiks_2026.csv": "участки: список (начальный, добавлены, исключены), бюллетени и явка по «представительному» голосованию УИК",
            "regions_dynamics_2024_2026.csv": "те же показатели по регионам за 2024–2026 гг. (единый метод)",
            "duma_deg_party_list_by_region.csv": "итоги ДЭГ по партспискам Госдумы по регионам (голоса)",
            "duma_deg_party_list_by_tik.csv": "итоги ДЭГ по партспискам по каждой из 1 044 частей (ТИК)",
            "duma_deg_single_mandate_by_district.csv": "итоги ДЭГ по кандидатам 81 одномандатного округа; mapping — точно/реконструкция",
            "duma_paper_vs_deg_protocol_lines.csv": "строки протоколов ОИК: всего, ДЭГ, бумага — для 36 округов",
            "contracts_2026.csv": "все 5 103 голосования: регион, выборы (если видны на портале), бюллетени, голоса, тип ГД-контракта",
            "crypto_verification_2024_2026.csv": "итог криптопроверки каждого голосования 2024–2026 и каким способом подтверждён",
            "news_timeline.json": "хронология событий 11–24.09 со сверкой по данным", "news_sources.json": "реестр источников хронологии"}
    xl = OUT / "deg2026_tables.xlsx"
    write_xlsx(sheets, desc, xl)
    files.append(("deg2026_tables.xlsx", None))
    desc["deg2026_tables.xlsx"] = "те же таблицы одной книгой Excel с русскими заголовками (без списка голосований и криптопроверки)"
    for n, k in files:
        readme.append(f"| `{n}` | {'' if k is None else f'{k:,}'.replace(',', ' ')} | {desc.get(n, '')} |")
    (OUT / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    z = OUT.parent / "deg2026_open_data.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(OUT.iterdir()):
            zf.write(p, f"deg2026_open_data/{p.name}")
    manifest = [dict(file=n, rows=k, bytes=(OUT / n).stat().st_size, desc=desc.get(n, "")) for n, k in files]
    (OUT / "manifest.json").write_text(json.dumps(dict(zip_bytes=z.stat().st_size, files=manifest), ensure_ascii=False, indent=1), encoding="utf-8")
    for m in manifest:
        print(f"{m['file']:45s} {str(m['rows'] or ''):>8} {m['bytes']/1024:9.0f} КБ")
    print(f"архив: {z.stat().st_size/1024/1024:.1f} МБ")


if __name__ == "__main__":
    main()
