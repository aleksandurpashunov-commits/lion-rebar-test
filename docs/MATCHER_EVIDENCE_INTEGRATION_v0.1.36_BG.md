# Matcher Evidence / Decision Layer — v0.1.36

## Цел

`lion_rebar.dxf.matcher_evidence` е чист, обясним слой между Matcher и
работния процес по одобрение. Той:

- не променя геометрия;
- не записва връзка;
- не публикува Engine;
- не превръща вътрешния matcher score в измислен процент;
- връща доказателства, blocker codes и препоръка.

`AUTO_ELIGIBLE` означава само „покрива условията за автоматизация“. Отделният
workflow решава дали и как да приложи одобрение.

## Препоръчано място за интеграция

След `_analysis_row(...)` в пакетния отчет:

```python
from lion_rebar.dxf.matcher_evidence import (
    MatcherCalibration,
    evaluate_report_row,
)

decision = evaluate_report_row(
    row,
    approved_master_codes=approved_master_codes,
    policy=resolved_policy,
    calibration=active_calibration,
)
row["matcher_decision"] = decision.to_dict()
row["matcher_disposition"] = decision.disposition.value
row["matcher_blocker_codes"] = [
    gate.code for gate in decision.safety_gates
]
```

`approved_master_codes` трябва да се получи от конкретната pinned Engine
ревизия и да съдържа само активни `APPROVED`/`LOCKED` форми. Наличието на
`master_shape_code` в отчета не е достатъчно доказателство.

## Работни опашки

| Disposition | Значение | Действие |
|---|---|---|
| `AUTO_ELIGIBLE` | Еднозначно, без safety blocker, в освободена калибрация | Workflow може да го приеме според отделна политика; layer-ът не записва |
| `CONTROL` | Еднозначно, но липсва field calibration/профил/независим Ø | Контролна извадка |
| `MANUAL` | Има реална двусмислица или поправима проектантска особеност | Решение от Александър |
| `UNKNOWN` | Липсва или си противоречи основно доказателство | Нова форма, поправка на DXF или данните |

## Стабилни blocker codes

- `AMBIGUOUS_MASTER`
- `NEAR_GAP`
- `FAR_ASSOCIATION`
- `SCHEMATIC_SCALE`
- `MIRROR_NOT_PERMITTED`
- `LAYER_MISMATCH`
- `LENGTH_MISMATCH`
- `INCOMPLETE_TEXT`
- `GEOMETRY_OWNERSHIP_CONFLICT`
- `FOREIGN_GEOMETRY`
- `BRANCHED_GEOMETRY`
- `DISCONNECTED_GEOMETRY`
- `NO_CANDIDATE`
- `NO_MASTER_MATCH`
- `UNAPPROVED_MASTER`

UI трябва да маршрутизира по code, а не чрез търсене на думи в българското
съобщение.

## Калибрация

Автоматичната допустимост изисква `MatcherCalibration`, съдържаща:

- идентификатор и Engine fingerprint;
- преминали регресионни случаи;
- независими, ръчно етикетирани реални случаи;
- нула грешни автоматични приемания;
- изрично освободен обхват от MASTER кодове.

Синтетичните завъртания и огледала доказват инвариантност, но не доказват
самостоятелно поведението върху шумни проектантски DXF файлове.

## Обясними доказателства

`MatcherDecision.evidence` съдържа отделни записи за:

- MASTER топология и контролен профил;
- производствена/геометрична L и разлика;
- разстояние надпис–геометрия;
- предимство пред алтернативите;
- слой и диаметър;
- контролирана празнина;
- шум/чужда геометрия.

`MatcherDecision.transforms` показва огледалност, обратен обход, равномерен
мащаб и дали завъртането е нормализирано. Абсолютният градус е `None`, защото
текущият matcher сравнява относителните чупки и не съхранява ненужен измислен
ъгъл.
