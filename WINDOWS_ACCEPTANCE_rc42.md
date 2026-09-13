# Windows операторски чеклист — LION REBAR v0.1.44.20-rc42

Дата: 07.09.2026 г.  Engine: 0.3.14.

Работи само върху ново копие на H-3. Не променяй оригиналния проект и не
заменяй rc41, докато критичните проверки по-долу не са PASS.

## 1. Стартиране

- [ ] PASS / [ ] FAIL — Разархивирай rc42 в отделна папка. При нужда изпълни
  `INSTALL_LION_REBAR.bat`, след това `START_LION_REBAR.bat`.
- [ ] PASS / [ ] FAIL — Заглавието показва `v0.1.44.20-rc42`.
- [ ] PASS / [ ] FAIL — Отвори новото копие на H-3; оригиналният H-3 остава с
  непроменени дата и съдържание.

## 2. Четирите гранични F006 стремена

Пусни нов „Пакетен анализ + Excel“ върху копието.

- [ ] PASS / [ ] FAIL — `36N8x350` е MASTER F006 със сегменти
  `10 + 20 + 145 + 20 + 145 + 10`.
- [ ] PASS / [ ] FAIL — `108N8x245` е MASTER F006 със сегменти
  `10 + 20 + 90 + 20 + 90 + 10`.
- [ ] PASS / [ ] FAIL — `36N8x245` е MASTER F006 със сегменти
  `10 + 20 + 90 + 20 + 90 + 10`.
- [ ] PASS / [ ] FAIL — `36N8x175` е MASTER F006 със сегменти
  `10 + 20 + 55 + 20 + 55 + 10`.
- [ ] PASS / [ ] FAIL — И четирите са `CONTROL`, без matcher blocker и без
  `UNRESOLVED_CURVE_GEOMETRY`.
- [ ] PASS / [ ] FAIL — В детайла сегментният source е
  `АВТОМАТИЧНО ИЗВЕДЕНО ОТ СОБСТВЕНИ КОТИ`, а не операторско решение.
- [ ] PASS / [ ] FAIL — Други непълни, конфликтни или нееднозначни hooked
  closures остават за инженерно решение и не се одобряват автоматично.

## 3. Starred ключове и 12 shared F004 позиции

- [ ] PASS / [ ] FAIL — Ключовете `3a*`, `3b*`, `3c*`, `3d*` се различават
  от близките ключове без `*`.
- [ ] PASS / [ ] FAIL — Редът `N20 - L=380` се прочита коректно.
- [ ] PASS / [ ] FAIL — 12-те H-3 позиции от starred семейството са MASTER
  F004 с обща геометрия `706D + 7070`, сегменти `360 + 20 = 380 cm` и
  правилния Ø от съответния легенден ред.
- [ ] PASS / [ ] FAIL — Общата геометрия не създава ownership conflict между
  тези 12 legend-reference позиции.

## 4. Локализация на казуса в Review Center

Отвори позиция без приет кандидат и използвай „Покажи“.

- [ ] PASS / [ ] FAIL — Надписът е ограден в червено.
- [ ] PASS / [ ] FAIL — Локалната зона за търсене е син пунктир.
- [ ] PASS / [ ] FAIL — Ако има отхвърлена геометрия, тя е кехлибарен пунктир.
- [ ] PASS / [ ] FAIL — Изгледът остава локален; дълга отхвърлена линия не
  отдалечава zoom-а до целия чертеж.
- [ ] PASS / [ ] FAIL — Смени казуса: старите overlay рамки изчезват.
- [ ] PASS / [ ] FAIL — Затвори Review Center директно с `X`: overlays,
  selection highlight/filter и временните manual toolbar действия изчезват;
  обикновеният избор в чертежа отново работи.

## 5. Fail-closed manual geometry guard

Влез в „Избери друга геометрия“ за подходящ тестов казус.

- [ ] PASS / [ ] FAIL — Схематичната рамка `6FD7` не може да бъде избрана.
- [ ] PASS / [ ] FAIL — Защитените legend handles `706D` и `7070` не могат
  да бъдат избрани ръчно, въпреки че служат като автоматична shared F004
  референция.
- [ ] PASS / [ ] FAIL — Реална разрешена ARM LINE/POLYLINE остава избираема.
- [ ] PASS / [ ] FAIL — Опит за запис със защитен/неразрешен handle показва
  явна грешка и не създава инженерно решение.
- [ ] PASS / [ ] FAIL — При техническа грешка в Engine/index guard-а ръчният
  избор се блокира; няма permissive fallback.
- [ ] PASS / [ ] FAIL — Смени към проект без source DXF: старият H-3 чертеж,
  избраните обекти и панелите изчезват, вместо да останат под новия проект.
- [ ] PASS / [ ] FAIL — Върни се към копието на H-3: чертежът и защитният
  индекс се изграждат наново и разрешена ARM геометрия отново се избира.

## 6. MASTER срещу ПРЕДЛОЖЕНИЕ

- [ ] PASS / [ ] FAIL — Ред с реален `master_shape_code` показва `MASTER`.
- [ ] PASS / [ ] FAIL — Ред само със `suggested_shape_code` показва
  `ПРЕДЛОЖЕНИЕ`, не `MASTER`, и не се записва без операторско действие.
- [ ] PASS / [ ] FAIL — Автоматично извлечените от собствени коти сегменти
  имат отделен provenance label.

## 7. Final Control и export

- [ ] PASS / [ ] FAIL — Ако всички останали реални H-3 позиции са решени,
  „Финален контрол“ създава валиден freeze manifest.
- [ ] PASS / [ ] FAIL — Ако остава друг реален нерешен казус, Final Control
  продължава да блокира с точната причина; не го отчитай като rc42 дефект.
- [ ] PASS / [ ] FAIL — След успешен контрол „Експорт Excel + DXF“ завършва
  `PRODUCTION VERIFIED`; Excel и DXF се отварят без repair предупреждение.

## 8. Ако има FAIL

Не поправяй оригиналния H-3. Запази:

- снимка на целия прозорец и Review Center;
- точния надпис и handle, ако се виждат;
- `working/final_integrity_report.json`;
- `working/final_integrity_ledger.csv`;
- последния batch report и diagnostic ZIP.

## Автоматична база преди операторския тест

PASS: 14/14 H-3/adversarial F006; 105/105 geometry/F006/UI; 149/149
batch/ownership/production; отделни overlay, starred legend и manual-guard
regression пакети PASS; 155/155 целеви resolver/backend/UI/source-lifecycle
проверки PASS. Fault-injection тестовете потвърждават same-generation rollback,
changed-generation clear, post-disk-write UI failure clear и transient guard
recovery без fail-open.

Пълен release pytest: **1114 събрани = 1103 passed + 11 skipped, 0 failed**.

Пакет: **LION_REBAR_v0_1_44_20-rc42_WINDOWS.zip**

SHA-256: виж външния файл
**LION_REBAR_v0_1_44_20-rc42_WINDOWS.zip.sha256**, създаден след ZIP-а.

Оператор: ____________________  Дата: __________  Общ резултат: PASS / FAIL
