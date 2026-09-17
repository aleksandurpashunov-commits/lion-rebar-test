"""Собствените коти на стремето отменят блока „липсват надписани размери“.

s-11, Тип 1b/1c (2026-09-17): детайлът 144N8x125 / 864N8x125 е стреме 25×25 с
кука 10, котирано с три коти, чиито опорни точки лежат върху самия контур.
Едната опашка обаче е начертана 10,2 вместо 10. Пасът за отпечатаните размери
сглобява размерите от измерената геометрия, вижда „несиметрична кука“ и
блокира позицията като F006_PRINTED_DIMENSIONS_MISSING. След това котите
изобщо не се четат, защото блокираният ред се пропуска.

Собственик: „по-редно е да взимаме надписите за верни, а не да мерим всеки
един елемент“. Геометрията доказва формата; размерите идват от котите.

Детайлът е намален от s-11: върховете и котите са преместени към началото.
"""

from __future__ import annotations

import math

from lion_rebar.dxf.batch_geometry_report import (
    _apply_dimension_derived_production_sizes,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.auto_gate import (
    is_deterministic_production_source,
    validate_production_segment_evidence,
)

# Отворената полилиния 9744 от s-11 (кука, четири страни с r=2,5, кука),
# преместена с (-2669.05, -1120.62).
_STIRRUP_VERTICES = (
    (13.84, 16.91),
    (20.68, 24.21),
    (25.00, 22.50),
    (25.00, 2.50),
    (22.50, 0.00),
    (2.50, 0.00),
    (0.00, 2.50),
    (0.00, 22.50),
    (2.50, 25.00),
    (22.50, 25.00),
    (24.33, 20.79),
    (17.49, 13.22),
)


def _dimension(handle: str, value: float, first, second) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="DIMENSION",
        layer="koti",
        geometry={
            "measurement": value,
            "text_midpoint": [
                (first[0] + second[0]) / 2.0,
                (first[1] + second[1]) / 2.0 + 8.0,
                0.0,
            ],
            "defpoint2": [first[0], first[1], 0.0],
            "defpoint3": [second[0], second[1], 0.0],
        },
    )


def _entities(*, width_second=(24.99, 22.50), height_first=(22.50, 25.00),
              height_second=(22.50, 0.00), usual_hook_twenty=True) -> list[DxfEntityRecord]:
    entities = [
        DxfEntityRecord(
            handle="T1C::BJ-FI-L",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            geometry={"insert": [-35.0, -13.0, 0.0]},
            text="864N8x125",
        ),
        DxfEntityRecord(
            handle="STIRRUP",
            entity_type="LWPOLYLINE",
            layer="Stremena",
            geometry={"points": [[x, y, 0.0] for x, y in _STIRRUP_VERTICES]},
        ),
        # Котите 9740 (25 хоризонтално), 9741 (25 вертикално) и 9742 (10 по
        # куката) — опорните им точки лежат върху върховете на контура.
        _dimension("DIM-W", 24.9897, (0.00, 22.50), width_second),
        _dimension("DIM-H", 25.0, height_first, height_second),
        _dimension("DIM-HOOK", 10.0, (13.84, 16.91), (20.68, 24.21)),
    ]
    if usual_hook_twenty:
        # В s-11 най-честата малка кота е страната 20 на другите стремена,
        # затова „обичайната кука“ на чертежа излиза 20.
        entities.extend(
            _dimension(f"FAR-20-{index}", 20.0, (500.0 + 60.0 * index, 0.0),
                       (520.0 + 60.0 * index, 0.0))
            for index in range(3)
        )
    return entities


def _blocked_row() -> dict:
    return {
        "annotation_handle": "T1C::BJ-FI-L",
        "annotation_class": "ACTIONABLE",
        "declared_length_cm": 125.0,
        "master_shape_code": "F006",
        "candidate_handles": ["STIRRUP"],
        "candidate_notes": [],
        "review_reason_codes": [],
        "f006_production_status": "F006_PRODUCTION_REVIEW",
        "f006_production_reason": "F006_PRINTED_DIMENSIONS_MISSING",
        "f006_printed_dimensions_missing": True,
        "f006_asymmetric_hooks": True,
        "production_export_blocked": True,
        "production_export_block_reason": "F006_PRINTED_DIMENSIONS_MISSING",
    }


def test_owned_dimensions_lift_the_missing_printed_dimensions_block() -> None:
    row = _blocked_row()

    assert _apply_dimension_derived_production_sizes([row], _entities()) == 1

    # 2·(25 + 25 + 10) = 120 при L=125: добавка за огъване 5 cm.
    assert row["production_segments_cm"] == [10.0, 25.0, 25.0, 25.0, 25.0, 10.0]
    assert row["production_segment_evidence"]["source"] == "F006_OWNED_CONTOUR_DIMENSIONS"
    assert sorted(row["production_segment_evidence"]["dimension_handles"]) == [
        "DIM-H",
        "DIM-HOOK",
        "DIM-W",
    ]
    assert row["f006_production_status"] == "F006_PRODUCTION_DERIVED"
    assert row["f006_production_reason"] == "OWNED_DIMENSIONS"
    assert row["production_export_blocked"] is False
    assert validate_production_segment_evidence(row)
    assert is_deterministic_production_source(row)


def test_square_stirrup_is_read_from_three_owned_dimensions_without_the_usual_hook() -> None:
    # Без блок: четецът по стойности иска куката да е „обичайната“ (тук 20),
    # а трите коти сами казват коя е куката.
    row = _blocked_row()
    for key in (
        "f006_production_status",
        "f006_production_reason",
        "production_export_blocked",
        "production_export_block_reason",
    ):
        row.pop(key)

    assert _apply_dimension_derived_production_sizes([row], _entities()) == 1
    assert row["production_segments_cm"] == [10.0, 25.0, 25.0, 25.0, 25.0, 10.0]
    assert row["production_segment_evidence"]["source"] == "F006_OWNED_CONTOUR_DIMENSIONS"


def test_other_production_blocks_stay_terminal() -> None:
    row = _blocked_row()
    row["production_export_block_reason"] = "CONFLICTING_PRODUCTION_SEGMENT_EVIDENCE"

    assert _apply_dimension_derived_production_sizes([row], _entities()) == 0
    assert row["production_export_blocked"] is True
    assert "production_segments_cm" not in row


def test_missing_printed_dimensions_block_is_not_repaired_by_nearby_dimensions() -> None:
    # Котите са до стремето, но не върху него: принадлежност няма, а по
    # близост блокът не се отменя.
    row = _blocked_row()
    entities = [
        entity
        for entity in _entities(usual_hook_twenty=False)
        if entity.entity_type != "DIMENSION"
    ]
    entities.extend(
        [
            _dimension("NEAR-W", 25.0, (0.0, 40.0), (25.0, 40.0)),
            _dimension("NEAR-H", 25.0, (40.0, 0.0), (40.0, 25.0)),
            _dimension("NEAR-HOOK", 10.0, (40.0, 40.0), (50.0, 40.0)),
        ]
    )

    assert _apply_dimension_derived_production_sizes([row], entities) == 0
    assert row["production_export_blocked"] is True
    assert "production_segments_cm" not in row


def _records(*values) -> list[dict]:
    return [
        {"handle": handle, "measurement_cm": value}
        for handle, value in zip(("DIM-W", "DIM-H", "DIM-HOOK"), values)
    ]


def test_explicit_square_needs_the_two_sides_in_perpendicular_directions() -> None:
    from lion_rebar.dxf.batch_geometry_report import _explicit_square_stirrup

    entity_map = {entity.handle: entity for entity in _entities(usual_hook_twenty=False)}
    assert _explicit_square_stirrup(_records(25.0, 25.0, 10.0), entity_map) == [25.0, 25.0, 10.0]

    # Двете 25 мерят в една посока (горе и долу): височината не е котирана.
    parallel = {
        entity.handle: entity
        for entity in _entities(
            height_first=(0.00, 2.50),
            height_second=(25.00, 2.50),
            usual_hook_twenty=False,
        )
    }
    assert _explicit_square_stirrup(_records(25.0, 25.0, 10.0), parallel) is None


def test_explicit_square_refuses_other_arrangements() -> None:
    from lion_rebar.dxf.batch_geometry_report import _explicit_square_stirrup

    entity_map = {entity.handle: entity for entity in _entities(usual_hook_twenty=False)}
    assert _explicit_square_stirrup(_records(25.0, 10.0, 10.0), entity_map) is None
    assert _explicit_square_stirrup(_records(25.0, 25.0, 25.0), entity_map) is None
    assert _explicit_square_stirrup(_records(25.0, 25.0), entity_map) is None
    assert math.isclose(
        _explicit_square_stirrup(_records(25.0, 25.0, 10.0), entity_map)[2], 10.0
    )
