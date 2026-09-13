# LION REBAR v0.1.44.20-rc1 — MILESTONE 1 + REVIEW CENTER 2.0

Основа: `v0.1.44.19-recovery.6.3.1` (ZIP SHA `b97a1a45…`), независимо одитирана.

## Замразени контракти — НЕПРОМЕНЕНИ
- Matcher `shape_recognition.py` = `05210e71ba6dc68c4d98665194cece4a11b3ce15ea684680e070e1f9ac2f024b`
- Engine 0.3.13 = `4a0e3efe5445bcbf7dd818a64a442e28abf5d4bdabbcaecebbc2c8a617a41f93`

## Locked holdout — PASS
| Показател | Договор | Измерено |
|---|---|---|
| actionable_positions | 627 | 627 |
| STRONG total | 605 | 605 |
| STRONG rate | 96.49% | 96.49% |
| F006 STRONG | 100 | 100 |
| manual review | 15 | 15 |
| errors / ambiguous / ownership | 0 / 0 / 0 | 0 / 0 / 0 |

**Changed rows спрямо одитирания 6.3.1: 0 от 802.**

## Milestone 1 — Production Hardening
- **P0-1** AutoCAD Unicode нормализация (`\U+XXXX`, `%%`, MTEXT кодове)
- **P0-2** Frame detection: fallback с адаптивен радиус + provenance + операторско решение
- **P0-3** Review Center current-project recovery (без файлов диалог по подразбиране)
- **P0-4** Точно съобщение при engine mismatch (версия срещу SHA)
- **P0-5** Единно Final Control обезсилване — БЕЗ изтриване на артефакти
- **P0-6** Cross-project isolation с формат-специфични четци
- **P0-7** FINAL_EXPORT_MANIFEST като production verification gate

## Review Center 2.0
- **P1-1** Динамичен MASTER списък — всички активни форми, без таван F008
- **P1-2** Apply to Group — строг подпис от 10 компонента, preview, audit trail, undo
- **P1-3** Apply to All Similar — LEVEL 2, ИЗКЛЮЧЕНА по подразбиране
- **P1-4** Търсене по F-код/име + визуален preview
- **P1-5** Segment refresh при смяна на форма

## Тестове
`495 passed, 0 продуктови провала`. Пет провала са ограничения на CI средата
(липсва Windows `.exe`, read-only DXF shim, monkeypatch на низов път).

## Оставащо за Windows приемане
1. `INSTALL_LION_REBAR.bat` → `BUILD_EXE.bat` → dist smoke
2. Engineer Resolution показва форми след F008
3. `NON_PRODUCTION_POSITION` отключва Final Control
4. Стреме взима размера от оригиналния DXF
5. Excel и DXF — еднакъв производствен набор
6. Втори DXF не наследява нищо
7. Apply to Group: preview → прилагане → undo
8. `FINAL_EXPORT_MANIFEST.json` до Excel-а; при провал статус NOT PRODUCTION VERIFIED

## Изисква решение на Product Owner
- Export mapping за категория „ВЕРТИКАЛИ“ (вътрешна категория съществува, без Excel лист)
- Поведение при UNKNOWN рамка: блокира AUTO, но допуска операторско решение?
