# Before/after diff — v0.1.44.5 → v0.1.44.6

## Производствен код

### `lion_rebar/dxf/recognition_policy.py`

```diff
+ position_key_manual_review_override_enabled
+ straight_bar_exact_length_rule_enabled
+ straight_bar_exact_length_master_codes
+ straight_bar_exact_length_ratio
+ straight_bar_exact_length_max_abs_cm
+ exact_length_tolerance_for_master(L, master_code)
```

Добавени са закони 28 и 29. Стойностите по подразбиране са изключени или
равни на стария глобален допуск; производственото активиране идва само от
Engine v0.3.12.

### `lion_rebar/dxf/geometry_candidates.py`

```diff
- exact_length_tolerance(L)
+ exact_length_tolerance_for_master(L, unambiguous_master_code)
+ F001_EXTENDED_EXACT_LENGTH audit reason
```

MASTER-специфичният допуск участва в confidence, exact unique проверката,
dimension tiebreak и near-gap exact-L gate. Не-F001 формите получават същата
стойност като преди.

### `lion_rebar/dxf/matcher_evidence.py`

Evidence редът `PARAMETER_LENGTH` използва същия MASTER-специфичен допуск,
за да няма разминаване между решението и показания праг.

### `lion_rebar/dxf/batch_geometry_report.py`

```diff
- ownership → conflicts → position key
+ position key → ownership → conflicts
+ skip ownership_reference_only rows
+ position_key_manual_override_count
+ f001_extended_exact_length_count
+ atomic main JSON/checkpoint writes
```

Resolver-ът пази snapshot на заменения кандидат и оригиналния статус. Общият
позиционен брояч остава обратно съвместим.

### `lion_rebar/ui/main_window.py`

Диалогът показва двата нови независими брояча.

### Версионни файлове

- `lion_rebar/config/constants.py`: `0.1.44.5 → 0.1.44.6`;
- `lion_rebar/__init__.py`: `0.1.44.5 → 0.1.44.6`;
- `pyproject.toml`: `0.1.44.5 → 0.1.44.6`.

### Непроменен файл

`lion_rebar/dxf/shape_recognition.py` е byte-for-byte непроменен със SHA-256
`fe46be28af71d50a490298809ef8e8688abe371ce4563ae17d9c140e6ae5fe69`.

## Engine

Нов файл:
`engine_updates/lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine`.

Нови production настройки в `engine.json`:

```json
{
  "position_key_manual_review_override_enabled": true,
  "straight_bar_exact_length_rule_enabled": true,
  "straight_bar_exact_length_master_codes": ["F001"],
  "straight_bar_exact_length_ratio": 0.007,
  "straight_bar_exact_length_max_abs_cm": 7.0
}
```

Огледално ownership правило няма, защото находката не обяснява реалните
конфликтни редове.

## Тестове

### `tests/test_position_key_legend_resolver.py`

Добавени:

- позитивен тест: manual слаб кандидат се заменя преди ownership;
- негативен тест: при изключен Engine флаг кандидатът не се заменя.

### `tests/test_f001_exact_length_calibration.py`

Добавени:

- позитивен `L=750/F001`;
- негативни `L=900`, `L=270`, не-F001 и изключен Engine флаг.

### `tests/test_engine_v0312_position_key_f001_policy.py`

Проверява version/title/parent, всички нови настройки, publish validation и
20-те Engine control tests.

## Документация

- `README_BG.md` и `CHANGELOG.md` — нова версия;
- `docs/RELEASE_v0.1.44.6_BG.md` — диагноза и acceptance;
- `docs/REAL_DXF_POSITION_KEY_F001_HOLDOUT_v0.1.44.6.json` — машинен
  before/after одит.
