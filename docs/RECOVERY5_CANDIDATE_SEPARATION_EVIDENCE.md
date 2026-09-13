# Recovery.5 — candidate-separation evidence alignment

## Присъда

Recovery.4 имаше вътрешно противоречие: ranking-ът вече приемаше spatial/A–E separation като силно доказателство, но evidence Matcher проверяваше само score gap. Recovery.5 не добавя нов армировъчен закон; тя прави двата слоя последователни.

## Реален DXF

| Показател | Преди | След |
|---|---:|---:|
| Реални позиции | 150 | 150 |
| CONTROL | 134 | 148 |
| MANUAL | 16 | 2 |
| AUTO gate | 134 | 148 |
| MASTER ambiguity | 0 | 0 |
| Ownership conflict | 0 | 0 |
| Errors | 0 | 0 |

### Как са разрешени 14-те позиции

- `DIMENSION_A_E`: 2
- `DISTANCE_GAP`: 12

## Оставащи ръчни позиции

| Handle | Надпис | MASTER | Заявена L | Геометрична L | Δ | Причина |
|---|---|---|---:|---:|---:|---|
| `3F12::BJ-FI-L` | `2N10x270` | F001 | 270.000 | 264.641 | 5.359 | Извън текущия точен допуск за L |
| `4DC8::BJ-FI-L` | `2N12x270` | F001 | 270.000 | 263.117 | 6.883 | Извън текущия точен допуск за L |

Допускът не е разширяван, за да се гони 150/150. Тези две позиции остават fail-closed за Review Center.

## Безопасност

- Engine остава `0.3.13`.
- Не са променяни `shape_recognition.py`, F003/F005, F006, production dimensions или AUTO lifecycle.
- Score threshold използва вече съществуващите Engine policy стойности: 0.10 за generic layer и 0.04 за слой с кодиран диаметър.
- Distance threshold използва вече съществуващите Engine policy стойности: 15 raw units за generic layer и 8 за слой с кодиран диаметър.
- A–E override важи само при вече доказан `DIMENSION_CANDIDATE_TIEBREAK`.
- LENGTH mismatch, MASTER ambiguity и ownership conflict продължават да блокират независимо от separation evidence.

Пълните 14 променени реда са в `RECOVERY5_CHANGED_POSITIONS.csv`; машинният отчет е в JSON файла.

## Проверки

- `tests/test_matcher_evidence.py`: 24/24 PASS, включително отделни проверки за generic и diameter-coded score/distance прагове.
- Пълен изпълним suite: 326 passed, 12 skipped, 1 environment-blocked/deselected UI тест без PySide6.
- Реалният `original.dxf` е изпълнен повторно след окончателната корекция: 315/315 надписа, 150/150 кандидата, 148 CONTROL/AUTO, 2 MANUAL, 0 грешки, 0 MASTER двусмислия, 0 ownership конфликти.
- Сравнение на всички 150 actionable решения преди и след последната threshold-alignment корекция: 0 променени решения.
