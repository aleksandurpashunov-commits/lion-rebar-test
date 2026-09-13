# RECOVERY 5.1 — EXPORT RESOURCE PACKAGING FIX

## Дефект

При реалния Windows export `MASTER preflight` спря с липсващ файл:

```text
_internal/lion_rebar/resources/OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html
```

Файлът съществуваше в source пакета, но `BUILD_EXE.bat` включваше само `engine_updates` и не пакетираше `lion_rebar/resources`.

## Поправка

PyInstaller командата вече включва:

```text
--add-data "lion_rebar\resources;lion_rebar\resources"
```

Така в one-folder dist се създават:

```text
_internal/lion_rebar/resources/OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html
_internal/lion_rebar/resources/export_template_v1.xlsx
```

След build автоматично се стартира `tests/test_dist_smoke.py`. Build-ът е FAIL, ако:

- няма Engine bundle;
- няма Excel шаблон;
- няма заключен MASTER HTML;
- SHA256 на MASTER HTML е различен;
- Engine bundle не се отваря;
- EXE приключи преждевременно.

## Непроменени компоненти

- Engine 0.3.13;
- Matcher;
- candidate separation от Recovery 5;
- F003/F005;
- F006;
- AUTO lifecycle;
- length tolerances;
- Final Control / production gate.

## Windows приемателен тест

1. `INSTALL_LION_REBAR.bat`
2. `BUILD_EXE.bat` — трябва да завърши с `Build complete and dist smoke tests passed.`
3. Стартиране на `dist/LION_REBAR_DESKTOP/LION_REBAR_DESKTOP.exe`
4. Отваряне на вече завършения проект 150/150
5. `Експорт Excel + DXF`
6. Потвърждение, че MASTER preflight минава и са създадени Excel и DXF.

## Автоматични тестове в одитната среда

- Full non-UI suite: **329 passed, 12 skipped, 1 environment-blocked/deselected**.
- Targeted packaging/export suite: **16 passed, 6 skipped** (5 dist tests чакат реално построен Windows dist; 1 UI тест чака PySide6).
- Нов packaging contract: **3/3 PASS**.

Реалният Windows `BUILD_EXE.bat` вече изпълнява dist smoke тестовете автоматично след PyInstaller.
