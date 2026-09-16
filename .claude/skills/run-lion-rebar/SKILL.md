---
name: run-lion-rebar
description: Пуска LION REBAR DESKTOP с отделната папка с данни на Claude, чака истинския прозорец, снима го и го затваря. Use to run or launch the app, or to screenshot it, when a change must be seen in the real UI (drawing view, dialogs, menus), not only in tests.
---

# Пускане на приложението

Проверено на 2026-09-16: прозорецът излиза за 14–19 s (драйвът е бавен),
снимката и затварянето минават.

## Преди пускане

- `LION_REBAR_DATA_ROOT` идва от `.claude/settings.json`. Скриптът отказва да
  тръгне без него: с папката на собственика стартът пише в работната му сесия
  и може да смени активния му engine.
- Виж дали собственикът има отворена програма — процеси `python*` с
  `run_dev.py`, които не си пуснал ти. **Не ги затваряй.** Твоят екземпляр
  тръгва до неговия.
- След промяна в кода пускай наново: Python кешира модулите.

## Пускане

От `release/`, в PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .claude\skills\run-lion-rebar\start_app.ps1 -Screenshot <scratchpad>\window.png
```

Отпечатва `APP_PID`, `WINDOW_AFTER_SECONDS`, `TITLE` и `SCREENSHOT`. Отвори
снимката с Read. При `NO_WINDOW` скриптът показва края на дневника.

## Затваряне

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .claude\skills\run-lion-rebar\start_app.ps1 -ClosePid <APP_PID>
```

Затваря само подадения процес и само през прозореца му. Ако след 15 s още
работи, най-вероятно е отворен диалог (тече експорт или пакетен анализ) —
направи снимка, не убивай процеса на сляпо.

## Какво вече е струвало време

- Обвивката `.venv\Scripts\python.exe` има **две** деца: `conhost.exe` и
  `python3.12.exe`. Прозорецът е на второто. Проверка на първото дете дава
  „няма прозорец" при работеща програма.
- При нормален старт дневникът съдържа само `logging initialized`. Това не е
  блокиране.
- Дневникът и registry-то на тези пускания са в `~\.lion_rebar_desktop_claude`.
  Папката е извън `AppData`, затова PowerShell и Git Bash я виждат — Store
  Python пренасочва само записите в `AppData`.
- Модалните диалози (`ImportReportDialog.exec()` след импорт и др.) спират
  всичко, което води прозореца отвън.
- Числата (позиции, одобрени, експорт) не се проверяват през прозореца — за тях
  е умението `lion-rebar-full-path`.
