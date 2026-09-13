# recovery.4 — Test Evidence

## AUTO lifecycle (tests/test_auto_link_lifecycle.py): 5/5 PASS
## F006 form/production split (tests/test_f006_production_split.py): 12/12 PASS
## Recognition pair (tests/test_recognition_pair.py): 2/2 PASS
## Engine control tests: 20/20 | Transformation fixture: 224/224, 0 failures
## Full suite (без pytest/ezdxf-зависимите): 169 passed, 0 нови провала

## Holdout Test_v_0_1_43_1.dxf (802 реда / 627 позиции)
Recognition:
  STRONG total: 605 (96.49%)  [proven baseline 609; recovery.3: 538]
  MANUAL REVIEW: 15 | ERROR: 0 | AMBIGUOUS: 0 | ownership conflicts: 0
  silent wrong: 0 (gained-vs-proven: 0; всяко ново силно е одитирано)
  F006 total: 107 | F006 form STRONG: 100 (proven: 104)
Production readiness (нов слой):
  F006_PRODUCTION_EXACT: 33
  F006_PRODUCTION_DERIVED: 0 (реалните редове нямат 6 еднозначни сегмента;
    никакви размери не са измисляни)
  F006_PRODUCTION_REVIEW: 70 (production export БЛОКИРАН за тях)
  asymmetric-hook cases: 74
Документирано отклонение от baseline (4 реда):
  33N8x110, 28N8x110 (x2), 28N8x150 — Δ=2.44 cm > допуск 2.1 cm.
  В proven baseline бяха STRONG само защото старият pipeline не проверяваше
  общата дължина на F006. Отклонението е инженерно обосновано, не е
  оптимизирано скрито.


## RELEASE CANDIDATE addendum (fail-closed hardening)
Blocker поправен: form-preserving fallback се допуска САМО за
F006AsymmetricHookError (структуриран exception, хвърлян на единственото
raise място в _canonicalize_for_master). String matching премахнат.
Несвързани ExcelExportError/ValueError/TypeError са fail-closed към
ЗА РЪЧЕН ПРЕГЛЕД дори при HOOKED_CLOSURE + Δ<=2.1 + без конфликти.

Тестове: F006 split suite 17/17 (12 стари + A, B, B2, C, C2).
Текстов двойник на съобщението НЕ отключва fallback (тест C2).
Holdout след patch: идентичен на draft (605/15/0/0/0; F006 100;
EXACT 33 / DERIVED 0 / REVIEW 70; gained-vs-proven 0, lost 4 документирани).

Recognition pair:
  Matcher SHA256: 05210e71ba6dc68c4d98665194cece4a11b3ce15ea684680e070e1f9ac2f024b
  Engine 0.3.13 file SHA256: 4a0e3efe5445bcbf7dd818a64a442e28abf5d4bdabbcaecebbc2c8a617a41f93
  Rules SHA256: c53720eec3d6fa859150f230a025c276645c03d21904ffd4231e2d2b0c512f91

Windows/dist smoke: ENVIRONMENT BLOCKED в одитната среда (няма Windows).
Пакетът е RELEASE CANDIDATE, НЕ final release, до реален smoke по 10-те
точки от заданието.
