# WINDOWS ПРИЕМАНЕ — v0.1.44.20-rc29

## Статус

rc29 е source release candidate. Не е production приета версия, докато не
мине този Windows/EXE операторски тест и новият реален holdout.

## Какво трябва да се провери

1. Създай нов проект и изпълни пакетен анализ.
2. Реши една UNKNOWN рамка като `VERTICALS`.
3. Пусни Final Control.
   Очаквано: `final_control_export_config.json` съществува и
   `final_control_freeze.json` съдържа роля `EXPORT_CONFIG` с SHA-256.
4. Направи Excel + DXF export.
   Очаквано: `FINAL_EXPORT_MANIFEST.json` е schema v2 и съдържа седем роли:
   `FINAL_LEDGER`, `EXCEL`, `FINAL_DXF`, `EXPORT_CONFIG`, `EXCEL_TRACE`,
   `DXF_TRACE`, `FINAL_CONTROL_FREEZE`. Статусът е `PRODUCTION VERIFIED`.
5. Направи контролно копие на export папката. В Excel trace промени категория
   `VERTICALS` на `BOTTOM` и пусни manifest verification.
   Очаквано: `EXPORT_CATEGORY_MISMATCH` и/или `ARTIFACT_SHA_MISMATCH`.
6. Пусни Final Control наново, промени един байт във
   `final_control_export_config.json`, после опитай export.
   Очаквано: отказ по SHA/fingerprint, без `PRODUCTION VERIFIED`.
7. Провери F006 с валидни размери 35×20, кука 10, L=135.
   Очаквано: сегменти 130 cm се приемат с добавката за огъване.
8. Провери невалидния контролен случай 100×50, кука 10, L=135.
   Очаквано: `F006_PRODUCTION_REVIEW`, export blocked.
9. Приложи групово решение и го върни преди export.
   Очаквано: точен байтов rollback.
10. Повтори групово решение, направи verified production export и опитай Undo.
    Очаквано: Undo е отказан, защото след действието има production export.

## Задължителен реален holdout

Пусни заключения DXF срещу rc28 и rc29 с един и същ Engine/Matcher. Запиши
SHA-256 на двата row файла и точния брой различни редове. Не използвай
`rows_baseline_6_3_1.json` като име за rc28 reference.

## Известно ограничение

Споделената схематична геометрия при легендните позиции още не е решена.
Тя изисква отделен reference-geometry модел; не трябва да се поправя с общо
отслабване на ownership правилата за всички греди и вертикали.
