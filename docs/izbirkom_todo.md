# Что осталось записать на izbirkom (HAR)

**02.10.2026:** из 92 страниц ОИК записаны 81 (файлы `data/external/izbirkom/oikNNN_sm|pl.har`). Бумага теперь есть
для 79 из 81 округа: бумага = протокол ОИК − ДЭГ (официальный протокол ДЭГ из снимка, а где его нет — журнал;
метод проверен на всех 1 361 строке 66 официальных протоколов ДЭГ — docs/tech/07-izbirkom-snapshot.md, B2).
Пересчёт после записи: `run.cmd reconcile_izbirkom`, `run.cmd izbirkom_snapshot`, `scriptsuild_site.cmd`.

Как записывать — `docs/izbirkom_links.md`. Главное: дождаться, пока на странице появится таблица протокола, и только
потом сохранять HAR (10 из 92 первых файлов сохранились без таблицы).

## 1. Протоколы ОИК: 11 страниц

Сгенерировано `run.cmd izbirkom_snapshot` → `data/derived/izbirkom_snapshot/todo_oik_protocols.csv`.


### Московская область
- [ ] **Балашихинский** (ОИК № 118) — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/944df59a-ee24-458b-9057-9719592a3a1d/results?type=6&report=242) → `oik118_pl.har`
- [ ] **Красногорский** (ОИК № 121) — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/869d48fd-89a9-42fd-a14e-f16709948b7f/results?type=7&report=242) → `oik121_sm.har`
- [ ] **Красногорский** (ОИК № 121) — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/869d48fd-89a9-42fd-a14e-f16709948b7f/results?type=6&report=242) → `oik121_pl.har`
- [ ] **Люберецкий** (ОИК № 122) — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/21089eed-f4e5-4bdc-883b-a32e50fc16c4/results?type=7&report=242) → `oik122_sm.har`

### Нижегородская область
- [ ] **Борский** (ОИК № 135) — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/f747db67-0090-44fc-82fb-e613041cbb31/results?type=6&report=242) → `oik135_pl.har`
- [ ] **Нижегородский** (ОИК № 131) — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/baacf9f6-6c67-42f4-878a-56490bb8c4d1/results?type=7&report=242) → `oik131_sm.har`

### Псковская область
- [ ] **Псковский** (ОИК № 150) — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/95462477-2dc5-45f7-83c6-4367c2fa1bd5/results?type=6&report=242) → `oik150_pl.har`

### Республика Алтай
- [ ] **Алтайский** (ОИК № 2) — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/c7bc5985-7cb0-4e8a-aa33-809d38514848/results?type=7&report=242) → `oik002_sm.har`

### Удмуртская Республика
- [ ] **Ижевский** (ОИК № 39) — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/6f315def-1bbe-4950-a509-49a6754020ba/results?type=6&report=242) → `oik039_pl.har`

### Челябинская область
- [ ] **Златоустовский** (ОИК № 192) — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/528269d7-6be6-4abc-bdfa-7867a60cd3c1/results?type=7&report=242) → `oik192_sm.har`
- [ ] **Златоустовский** (ОИК № 192) — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/528269d7-6be6-4abc-bdfa-7867a60cd3c1/results?type=6&report=242) → `oik192_pl.har`

## 2. A-10: протоколы ТИК Серпуховского округа (8 страниц)

Отдельного протокола «Дистанционное электронное голосование» в дереве комиссий сайта нет (в снимке он есть, на
страницах — нет). Строка 9 ДЭГ = строка 9 протокола ОИК − Σ строк 9 пяти ТИК. Протоколы ОИК № 128 уже записаны
(oik128_sm.har, oik128_pl.har), ТИК «Бронницы» есть в снимке. Ожидаем строку 9 ДЭГ = 1 в обеих формах.

- [ ] **ТИК Серпухов (128 округ ГД)** — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/f2abbaad-528c-4159-9ca7-2f8e4c0adbf5/results?type=7&report=242) → `oik128_tik_серпухов_sm.har`
- [ ] **ТИК Серпухов (128 округ ГД)** — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/f2abbaad-528c-4159-9ca7-2f8e4c0adbf5/results?type=6&report=242) → `oik128_tik_серпухов_pl.har`
- [ ] **ТИК Ступино (128 округ ГД)** — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/d3267ec5-77e3-4a19-b92d-e0c1de09227b/results?type=7&report=242) → `oik128_tik_ступино_sm.har`
- [ ] **ТИК Ступино (128 округ ГД)** — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/d3267ec5-77e3-4a19-b92d-e0c1de09227b/results?type=6&report=242) → `oik128_tik_ступино_pl.har`
- [ ] **ТИК Раменский (128 округ ГД)** — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/4c59aea0-9e97-4073-92b1-a3c349f0c676/results?type=7&report=242) → `oik128_tik_раменский_sm.har`
- [ ] **ТИК Раменский (128 округ ГД)** — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/4c59aea0-9e97-4073-92b1-a3c349f0c676/results?type=6&report=242) → `oik128_tik_раменский_pl.har`
- [ ] **ТИК Чехов (128 округ ГД)** — одномандатный округ: [открыть](http://www.izbirkom.ru/election/587813923/commission/7820f1ba-a29e-4c5c-a97b-4d70197df30f/results?type=7&report=242) → `oik128_tik_чехов_sm.har`
- [ ] **ТИК Чехов (128 округ ГД)** — партийные списки: [открыть](http://www.izbirkom.ru/election/587813923/commission/7820f1ba-a29e-4c5c-a97b-4d70197df30f/results?type=6&report=242) → `oik128_tik_чехов_pl.har`

После записи ТИК `reconcile_izbirkom` сам посчитает ДЭГ = ОИК − Σ ТИК по Серпуховскому и сверит все строки с журналом.
