# LION REBAR v0.1.44.19-recovery.6.3.1 — DATA INTEGRITY / F006 HOTFIX — RC

Дата: 2026-08-14

## 1. Присъда

Това е **source release candidate**, не окончателен production release.

Локализираната F006 регресия от отхвърлената recovery.6.3 е поправена и покрита с автоматичен replay. Data-integrity промените са запазени. Окончателното приемане остава блокирано от два външни теста, които не могат да бъдат изпълнени в текущата Linux среда:

1. заключения реален holdout `Test_v_0_1_43_1.dxf` не е включен в source пакета;
2. PySide6/Windows EXE операторският тест не е наличен тук.

## 2. Корен на F006 регресията

Recovery.6.3 добави `lion_rebar/export/engine_shape_parameters.py`. При F006 с доказана `HOOKED_CLOSURE` топология и асиметрични куки новият слой изпреварваше историческия MASTER валидатор и връщаше общ `ExcelExportError`.

Recovery.4 gate разпознава специалния тип `F006AsymmetricHookError`, за да раздели:

- **Form recognition:** доказано STRONG F006;
- **Production dimensions:** `EXACT`, `DERIVED` или `REVIEW`.

Когато специалният тип беше изгубен, fail-closed логиката приемаше случая като непозната export грешка и сваляше формата в manual review.

## 3. Корекция

Въведен е общ typed contract в:

- `lion_rebar/export/errors.py`;
- `lion_rebar/export/engine_shape_parameters.py`;
- `lion_rebar/export/batch_auto_output.py`;
- `lion_rebar/export/excel_export.py`.

`parameters_for_shape()`:

1. валидира F006 куки преди общата Engine проверка;
2. хвърля точния `F006AsymmetricHookError`;
3. никога не го wrap-ва в generic export exception;
4. оставя Recovery.4 gate да пази STRONG form classification и да решава production readiness отделно.

Не се създават измислени симетрични куки.

## 4. Data Integrity

### 4.1 Идентичност

Всеки производен payload получава:

- `project_id`;
- `source_sha256`;
- `source_generation_id`;
- `analysis_run_id`.

### 4.2 Смяна на DXF

При различен source SHA:

- старото производно съдържание от `working/` се мести в `working/source_generations/...`;
- старият source се архивира;
- създава се нов `source_generation_id`;
- стар batch report, Review state и approvals не могат да бъдат намерени като текущи.

Повторен избор на byte-identical DXF не създава нова source generation.

### 4.3 Final Control

Final Control:

- проверява root identity и identity на всеки report row;
- отхвърля чужди/стари AUTO links;
- допуска legacy USER решение само при доказано текущ report row и съществуваща текуща геометрия;
- записва Final Ledger;
- замразява frame/beam ownership;
- копира cloud/external batch report в проекта;
- заключва седем артефакта с SHA-256.

Excel и DXF export отказват при промяна на който и да е замразен артефакт.

## 5. Ръчни инженерни решения

### 5.1 NON_PRODUCTION_POSITION

Доказано несъществуващ надпис може да бъде маркиран като непроизводствен само с:

- `action_class=EXCLUSION`;
- `reason_code=NON_PRODUCTION_POSITION`;
- непразна инженерна бележка;
- структурирано evidence с `OWNER_ENGINEER_ORIGINAL_DXF_CHECK`, `original_dxf_checked=true` и операторската бележка.

Редът остава в одитния Ledger, но не влиза в Excel/DXF и не блокира завършването.

### 5.2 Всички MASTER форми

Engineer Resolution зарежда всички активни, non-retired форми от заключения Engine. Няма hardcoded ограничение F001–F008.

### 5.3 Решение без geometry handles

Допустимо е само при изричен engineer override/correction. Ledger записва:

- `geometry_handles_count=0`;
- `geometry_provenance=NONE_ENGINEER_OVERRIDE` (или историческия `NONE_ENGINEER_CORRECTION`, когато такъв запис вече съществува);
- общ брояч `engineer_resolved_without_geometry_count`.

## 6. Автоматични тестове

Изпълнен е пълният Linux test suite:

```text
410 passed
12 skipped
0 failed
```

Дванадесетте skip-а са изрично условни: пет теста изискват PySide6/Qt,
пет dist-smoke теста изискват изграден Windows EXE и два исторически Engine
holdout fixture-а не са в source checkout-а. Статичният contract тест за
`batch_geometry_worker.py` вече чете source файла директно и не изисква Qt
import; първоначалните му три проверки са запазени.

Отделните фокусирани пробези са:

```text
F006 pipeline: 28 passed, 0 failed
Data integrity / Final Ledger / Engineer Decision / Export: 54 passed, 1 PySide6 skip, 0 failed
```

Нови задължителни regression сценарии:

- typed F006 signal е един и същ class object във всички слоеве;
- фокусиран replay на всичките 67 реда от одитирания regression механизъм пази 67/67 STRONG F006;
- engine_shape_parameters връща `F006AsymmetricHookError`;
- Recovery.4 gate пази STRONG F006 и отделен production status;
- синтетичен 67-редов replay през новия слой дава 67/67 STRONG F006, 67 typed asymmetric signals и 0 generic downgrades;
- нов source архивира производното състояние;
- byte-identical source пази generation;
- in-place редактиран source се staging-ва безопасно;
- report row от чужд run блокира Final Control и AUTO approval;
- external report се staging-ва и export остава self-contained;
- frozen artifact mutation блокира export;
- beam assignment не се recompute-ва след Final Control;
- NON_PRODUCTION evidence gate;
- geometry-less engineer counter/ledger marker;
- реалният Engine 0.3.13 подава всички 15 активни форми в Engineer Resolution: F001–F008, F010–F014, F016 и F019; retired F009/F015/F017/F018 са изключени.

## 7. Holdout gate

Независимият одит на отхвърлената 6.3 отчита:

| Метрика | 6.2 | Отхвърлена 6.3 |
|---|---:|---:|
| STRONG F006 | 100 | 33 |
| F006 manual review | 15 | 82 |
| Общо STRONG | 605 (96.49%) | 538 (85.81%) |
| ERROR | 0 | 0 |
| AMBIGUOUS | 0 | 0 |

Новите 6.3.1 числа са **PENDING**, защото заключеният DXF не е в пакета. Финален release се допуска само ако:

- STRONG F006 се върне на 100;
- F006 manual review се върне на 15;
- total STRONG се върне на 605 или всяко отклонение е обяснено ред по ред;
- ERROR=0;
- AMBIGUOUS=0;
- silent wrong=0;
- нито един `F006_PRODUCTION_REVIEW` ред не влиза в production export.

## 8. Непроменени защитени компоненти

```text
Matcher SHA-256
05210e71ba6dc68c4d98665194cece4a11b3ce15ea684680e070e1f9ac2f024b

Engine 0.3.13 SHA-256
4a0e3efe5445bcbf7dd818a64a442e28abf5d4bdabbcaecebbc2c8a617a41f93
```

## 9. Операторски acceptance test

Не презаписвай recovery.6.2. Разархивирай RC в отделна папка и провери:

1. формите след F008 се виждат в Engineer Resolution;
2. фантомен надпис може да се реши като NON_PRODUCTION с evidence;
3. Final Control позволява export след това;
4. проблемната греда получава правилния размер на стремето;
5. Excel и DXF имат еднакъв набор позиции;
6. след промяна на решение старият Final Control се инвалидира;
7. втори различен DXF не наследява редове/решения от първия.
