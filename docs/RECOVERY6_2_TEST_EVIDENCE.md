# LION REBAR v0.1.44.17-recovery.6.2 — Test Evidence

## Обхват

Проверени са:

- пълно инженерно решение и история на заменените връзки;
- точна форма/брой/Ø/L/сегменти;
- MASTER preflight за ръчните производствени размери;
- експлицитно решение без DXF геометрия;
- пренасочване на AUTO ownership и защита на ръчния ownership;
- финално общо USER потвърждение само на безопасни редове;
- забрана диагностичен проблем да бъде приет масово;
- Final Integrity разделение safe/engineering pending;
- пълен Excel context с коригиран брой, Ø, L, текст и сегменти;
- включване на engineer-resolved позиция без отделна геометрия;
- Decision Journal v2;
- отделяне на calibration evidence от rule candidates;
- запис на полето „Как трябва да бъде“ в JSON и CSV;
- explicit project states COMPLETE / INCOMPLETE / NO_FINAL_CONTROL;
- regression на всички съществуващи тестове.

## Резултат

```text
385 passed
12 skipped
1 deselected (PySide6 UI import в Linux средата)
```

Skipped условията са исторически fixtures, PySide6 UI imports и dist smoke без Windows `dist` папка. Няма нов функционален fail.

## Реален recognition regression

Двата заключени реални DXF-а са изпълнени с непроменени Engine 0.3.13 и Matcher pair.

### `original.dxf`

```text
Обработени надписи: 315
Реални позиции: 150
CONTROL: 148
MANUAL: 2
Силни по геометрия: 116
Силни F006: 32
Ownership конфликти: 0
MASTER двусмислия: 0
Грешки: 0
```

### `Греди тест - 003.dxf`

```text
Обработени надписи: 348
Реални позиции: 166
CONTROL: 155
MANUAL: 11
Силни по геометрия: 123
Силни F006: 32
Ownership конфликти: 2
MASTER двусмислия: 0
Грешки: 0
```

Промените в Recovery 6.2 са след batch recognition и не променят нито един от двата baseline резултата.

## Replay на реалната блокирана Session с 274 нерешени позиции

Каченият от оператора Recovery 6.1 Learning Pack (SHA256 `c546c0c8d6d20fb88d55831700385d4a1eb98d0e49c8fdb6a4bbd8053ef77e1f`) е подаден към новия Recovery 6.2 Review Summary classifier без промяна на batch резултатите.

```text
Проект R6_SESSION_TEST_A-1
Actionable: 315
Вече решени/одобрени: 38
Безопасни за едно финално USER потвърждение: 271
Реални инженерни казуси: 6
```

Това възпроизвежда точно наблюдавания UX проблем: вместо Final Control да показва общо 274 „неодобрени“ без действие, Recovery 6.2 отделя 271 технически валидни позиции за един бутон и оставя само 6 позиции за индивидуално инженерно решение. Записан диагностичен проблем не се поглъща от общото потвърждение.

Машинночетимо доказателство: `docs/RECOVERY6_2_REAL_SESSION_REPLAY.json`.

## Static / package gates

- `python -m compileall -q lion_rebar` — PASS;
- Engine 0.3.13 bundle SHA `4a0e3efe5445bcbf7dd818a64a442e28abf5d4bdabbcaecebbc2c8a617a41f93` — непроменен спрямо Recovery 6.1;
- `lion_rebar/dxf/shape_recognition.py` SHA `05210e71ba6dc68c4d98665194cece4a11b3ce15ea684680e070e1f9ac2f024b` — непроменен;
- `batch_geometry_report.py`, `geometry_candidates.py` и `local_detail_resolver.py` — без diff спрямо Recovery 6.1;
- Beam Ledger grouping логиката е непроменена; Excel row-source логиката е разширена само да използва пълната USER производствена спецификация;
- ZIP integrity — PASS (`281` entries, `testzip()` без повреден член);
- вътрешен SHA256 manifest — PASS (`279/279` release файла);
- повторно разархивиране — PASS;
- критичният пакет от 49 Engineer Decision / Final Confirmation / Integrity / Export / Learning Pack / Session tests върху разархивирания ZIP — PASS.

## Оставащ Windows gate

Linux средата няма PySide6 и не може да докаже реалната интеракция с компилирания EXE. Преди release-ready статус трябва да мине:

```text
INSTALL_LION_REBAR.bat
→ BUILD_EXE.bat
→ dist smoke PASS
→ Review Center: проблем + „как трябва да бъде“
→ Engineer Decision: форма/брой/Ø/L/сегменти
→ избор на друга геометрия
→ explicit no-geometry case
→ Confirm all remaining safe
→ Final Control = всички позиции / 0 unresolved
→ Excel + DXF включват AUTO + USER позиции
→ END SESSION Learning Pack съдържа calibration + rule candidates
```
