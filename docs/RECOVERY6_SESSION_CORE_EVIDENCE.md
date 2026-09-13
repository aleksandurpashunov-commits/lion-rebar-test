# LION REBAR v0.1.44.15-recovery.6 — Session Core Evidence

Дата: 2026-08-12

## Цел

Да се отдели ежедневната инженерна работа от release разработката:

```text
DXF → Layer Classification → Engine/Matcher → Review Queue
    → USER Decision → Final Project State + Decision Journal
    → Global Session Database → END SESSION → Learning Pack
    → Audit / Regression → Candidate Engine/Matcher Update
```

USER решенията са авторитетни за конкретния проект, но не променят автоматично Engine или Matcher.

## Реализирано

### 1. Cross-project Session Database

- SQLite база под `%LOCALAPPDATA%/LionBuild/LionRebarDesktop/session_core/`.
- Сесията е независима от проектната папка.
- Активната сесия се възстановява след нормален рестарт/затваряне.
- Събитията се записват транзакционно и се индексират по проект, тип и annotation handle.
- `SESSION_RECOVERY_LATEST.json` е лек recovery snapshot; SQLite е авторитетният източник.

### 2. Decision Journal

Review Center записва:

- annotation handle и текст;
- DXF SHA;
- Engine id/version/SHA;
- Matcher SHA;
- текущ batch status и evidence band;
- предложена MASTER форма;
- избрана форма/геометрия/сегменти;
- причина, бележка и conflict flags.

Project-local файл: `working/decision_journal.jsonl`.

### 3. Layer Classification

Автоматични роли:

- REBAR_GEOMETRY
- REBAR_TEXT
- DIMENSIONS
- LEADERS
- BEAM_GEOMETRY
- STIRRUPS
- LEGEND
- FRAME
- USER_HELPER
- NOISE
- UNKNOWN

Само `NOISE` с confidence >= 0.95 се скрива по подразбиране и се изключва от batch input. `UNKNOWN` остава видим и релевантен. Това е fail-open за нов проектантски стил и fail-closed само за доказан шум.

### 4. END SESSION

Нов toolbar/menu action `Край на сесията`:

- записва session summary;
- създава Decision Journal CSV/JSON;
- включва преносим `session_database_extract.json` без абсолютни машинни пътища;
- включва хронологията на събитията;
- включва Layer Classification CSV;
- включва до 10 batch JSON отчета на проект;
- генерира възпроизводим AI diagnostic ZIP за всеки проект;
- групира `rule_candidates.json`;
- създава `START_PROMPT_BG.md`;
- пише timestamped ZIP и стабилен `LION_REBAR_LEARNING_PACK_LATEST.zip`.

### 5. Safe learning boundary

Всеки rule candidate има статус:

`CANDIDATE_ONLY_REQUIRES_AUDIT_AND_REGRESSION`

Няма код, който автоматично публикува правило или модифицира Engine/Matcher.

## Непроменени производствени компоненти

- Engine 0.3.13;
- Matcher SHA `05210e71...`;
- shape_recognition.py;
- F003/F005/F006 правила;
- Local Detail Resolver 5.3;
- Beam Ledger 5.2;
- AUTO lifecycle 4.2;
- всички производствени tolerance-и;
- Final Integrity и export gate.

## Тестове

Нови целеви проверки:

- Session Database survives restart;
- explicit END SESSION closes current session;
- next action creates a new session;
- Learning Pack contains summary/journal/session database extract/rule candidates/manifest;
- Decision Journal is written project-locally;
- bulk approvals expand to one journal row per real position;
- Layer classifier hides only high-confidence NOISE;
- UNKNOWN remains visible and is not filtered;
- high-confidence noise filtering is deterministic.

Пълен Linux audit:

- `349 passed`;
- `12 skipped`;
- `1 deselected/environment-blocked` UI import test поради липсващ PySide6;
- новите Session/Layer/Learning/Journal проверки: `10/10 PASS`.

Реален headless regression след layer prefilter:

| DXF | Обработени | Реални позиции | CONTROL | MANUAL | F006 | Ownership | Ambiguity | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `original.dxf` | 315 | 150 | 148 | 2 | 32 | 0 | 0 | 0 |
| `Греди тест - 003.dxf` | 348 | 166 | 155 | 11 | 32 | 2 | 0 | 0 |

И при двата файла автоматично са отстранени само слоевете `Defpoints` и `solid`; всички UNKNOWN слоеве остават в анализа. Резултатите са идентични с recovery.5.3 baseline.

## Windows smoke gate

1. `INSTALL_LION_REBAR.bat`.
2. `BUILD_EXE.bat` и задължително `Build complete and dist smoke tests passed`.
3. Проект A в папка A: импорт, batch, поне едно решение.
4. Проект B в различна папка: импорт, batch, поне едно решение.
5. Status bar: една сесия, 2 проекта.
6. Затваряне и повторно отваряне: същата session id.
7. `Край на сесията`.
8. Проверка на `LION_REBAR_LEARNING_PACK_LATEST.zip`.
9. Ново стартиране: нова session id; старата остава ENDED в SQLite.

Машинночетимият реален DXF smoke отчет е в `docs/RECOVERY6_REAL_DXF_SMOKE.json`.
