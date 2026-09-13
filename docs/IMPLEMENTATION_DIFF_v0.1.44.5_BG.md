# Before/after diff — v0.1.44.4 → v0.1.44.5

Това е логическият diff за всеки производствен и тестов файл. Документацията,
версионните полета и manifest-ът са проследими отделно в release пакета.

## Производствен код

### `lion_rebar/dxf/geometry_core_v2.py`

```diff
+ def arc_path_from_geometry(geometry):
+     sweep = (end_angle - start_angle) % 360
+     exact_length = radius * radians(sweep)
+     return PolylinePath(..., PathElement(kind=ARC, length_raw=exact_length))
```

Bulge формулата остава в `element_from_bulge`; новият helper прилага същия
модел към самостоятелен DXF `ARC`, включително sweep през 0°.

### `lion_rebar/dxf/geometry_candidates.py`

```diff
- geometry_length = sum(
-     p.semantic_length_raw if p.semantic_length_raw is not None else p.length_raw
-     for p in shape
- )
+ geometry_length = sum(p.length_raw for p in shape)
```

```diff
- elif entity_type in {"LWPOLYLINE", "POLYLINE"}:
-     path = polyline_path_from_geometry(record.geometry)
+ elif entity_type in {"LWPOLYLINE", "POLYLINE", "ARC"}:
+     path = arc_path_from_geometry(record.geometry) if entity_type == "ARC" else ...
```

Добавен е само тесен `ESove/F007` admission/strong път. Той отказва далечна
геометрия без пълния contextual override и не променя общия search radius.

### `lion_rebar/manual_assignment.py`

```diff
- SUPPORTED_MANUAL_GEOMETRY = {"LINE", "LWPOLYLINE", "POLYLINE"}
- total_length_cm = round(sum(topology_chords), 6)
+ SUPPORTED_MANUAL_GEOMETRY = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC"}
+ total_length_cm = round(sum(exact_source_path_lengths), 6)
```

Топологичните сегменти остават отделни; само общата измерена L става физическа.

### `lion_rebar/dxf/stirrup_group_resolver.py` — нов файл

Новият модул изпълнява точен pipeline:

```text
точен слой → извита ос → exact arc L → unique F006
→ един кандидат ИЛИ една пълна повторена редица → повторна matcher проверка
```

При две групи, страничен точен кандидат, различен fingerprint, прекалена
стъпка/отклонение или несъвместим брой връща `None`.

### `lion_rebar/dxf/batch_geometry_report.py`

```diff
+ apply position-key references
+ apply stirrup exact-length/group references
```

Добавени са отделни count/CSV/JSON audit полета за resolution code,
представител, членове, разстояния, точни L и първоначален статус. Резултатът е
reference-only и не създава автоматично approval/ownership.

### `lion_rebar/dxf/recognition_policy.py`

```diff
- c_shape_curve_total_tolerance_cm = 2.0
+ c_shape_curve_total_tolerance_cm = 2.5
+ c_shape_curve_search_distance_raw = 120.0
+ stirrup_length_group_rule_enabled = True
+ stirrup_length_group_layer_aliases = ("STREMENA", "СТРЕМЕНА")
+ stirrup_length_group_master_codes = ("F006",)
+ stirrup_length_search_distance_raw = 205.0
+ stirrup_arc_length_tolerance_cm = 2.0
+ stirrup_group_min_members = 2
+ stirrup_group_max_member_spacing_raw = 80.0
+ stirrup_group_max_row_deviation_raw = 5.0
+ stirrup_group_repeat_length_tolerance_cm = 0.5
+ stirrup_group_require_count_compatibility = True
```

Добавени са закони 26 и 27. Production стойностите идват от Engine v0.3.11.

### `lion_rebar/dxf/shape_recognition.py`

```diff
  без промяна
```

`match_shape_length_relations` продължава да проверява отношенията на
производствените прави сегменти, не общата cut-length. SHA-256 е идентичен
преди/след.

## Тестове

### `tests/test_geometry_core_v2_bulge.py`

Добавени/променени проверки за точна bulge L, самостоятелен ARC и запазване на
неразрешена крива извън MASTER топологията.

### `tests/test_manual_assignment.py`

Добавени позитивни проверки за exact bulge total и `ARC radius × sweep`.

### `tests/test_stirrup_group_resolver.py` — нов файл

Позитивни: един точен отдалечен кандидат; една повторена редица.
Негативни: два отделни точни кандидата; група по-голяма от annotation count.

### `tests/test_f007_esove_resolver.py`

Добавен реалният нормализиран `6N6x40` контур. Позитивен тест на 57 units и
негативен тест на 61 units доказват, че тесният 60-unit gate не се разлива.

### `tests/test_evidence_tiebreak_v01443.py`

Актуализирано е очакването за отделния F007 strong gate от 60 units; generic
редът остава 45 units.

### `tests/test_engine_v0311_arc_stirrup_policy.py` — нов файл

Проверява версия/title/parent, production-ready, всяка нова engine настройка,
publish validation и всичките 20 Engine control tests.

## Версия и документация

- `lion_rebar/config/constants.py`: `0.1.44.4 → 0.1.44.5`;
- `lion_rebar/__init__.py`: `0.1.44.4 → 0.1.44.5`;
- `pyproject.toml`: `0.1.44.4 → 0.1.44.5`;
- `README_BG.md`, `CHANGELOG.md`: нова секция v0.1.44.5;
- `engine_updates/...v0.3.11....lionengine`: нов Engine с настройки и верен title;
- `docs/RELEASE_v0.1.44.5_BG.md`: логика и acceptance резултати;
- `docs/REAL_DXF_ARC_STIRRUP_HOLDOUT_v0.1.44.5.json`: редов before/after;
- `SHA256_MANIFEST.json`: пресъздаден след финалната проверка.
