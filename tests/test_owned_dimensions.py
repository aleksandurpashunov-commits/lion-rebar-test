"""Котировките принадлежат на контура, който описват.

ПРАВИЛО (Product Owner): програмата ЧЕТЕ надписаното от проектанта, не
комбинира числа, докато не се получи сметка.

Опорните точки на котировката лежат върху върховете на контура —
измерено в реален чертеж: 0.0 единици за собствените, десетки за чуждите.
Това е връзка, начертана от проектанта, а не преценка по разстояние.

Реалният дефект, който това поправя: `25N8x165` излизаше `45 x 25`,
защото комбинацията се връзваше аритметично (2*(45+25)+2*10 = 160, L=165),
но 45 и 25 не са надписани около това стреме.
"""
from __future__ import annotations

from types import SimpleNamespace

from lion_rebar.dxf.vertical_shape_resolver import (
    OWNERSHIP_TOLERANCE,
    dimensions_owned_by_contours,
    read_owned_stirrup,
    snap,
)


def _contour(handle: str, points):
    return SimpleNamespace(
        handle=handle, entity_type="LWPOLYLINE",
        geometry={"points": [[x, y, 0.0] for x, y in points]})


def _dimension(measurement, first, second):
    return SimpleNamespace(
        handle="D", entity_type="DIMENSION",
        geometry={"measurement": measurement,
                  "defpoint2": [first[0], first[1], 0.0],
                  "defpoint3": [second[0], second[1], 0.0]})


def _stirrup(handle="S1", width=35.0, height=20.0):
    return _contour(handle, [(0, 0), (width, 0), (width, height), (0, height)])


# ------------------------------------------------ принадлежност

def test_dimension_on_the_contour_belongs_to_it():
    entities = [_stirrup(), _dimension(35.0, (0, 0), (35, 0))]
    owned = dimensions_owned_by_contours(entities)
    assert owned["S1"] == [35.0]


def test_dimension_far_from_any_contour_is_not_claimed():
    entities = [_stirrup(), _dimension(45.0, (900, 900), (945, 900))]
    owned = dimensions_owned_by_contours(entities)
    assert owned.get("S1") is None


def test_dimension_is_assigned_to_the_nearest_contour_only():
    """Кота върху едно стреме не се приписва на съседното."""

    entities = [
        _stirrup("A", 35.0, 20.0),
        _contour("B", [(500, 0), (545, 0), (545, 25), (500, 25)]),
        _dimension(45.0, (500, 0), (545, 0)),
    ]
    owned = dimensions_owned_by_contours(entities)
    assert owned.get("B") == [45.0]
    assert "A" not in owned


def test_measurements_are_snapped_to_production_values():
    """34.9897 е измерено; проектантът вижда 35."""

    entities = [_stirrup(), _dimension(34.9897, (0, 0), (35, 0))]
    assert dimensions_owned_by_contours(entities)["S1"] == [35.0]


def test_tolerance_is_documented_and_tight():
    assert OWNERSHIP_TOLERANCE <= 3.0


# ------------------------------------------------ четене без комбиниране

def test_three_owned_values_are_read_as_written():
    assert read_owned_stirrup([35.0, 20.0, 10.0]) == [35.0, 20.0, 10.0]


def test_largest_is_width_smallest_is_hook():
    assert read_owned_stirrup([10.0, 165.0, 20.0]) == [165.0, 20.0, 10.0]


def test_two_values_mean_a_square_stirrup():
    assert read_owned_stirrup([20.0, 10.0], preferred_hook=10.0) == [20.0, 20.0, 10.0]


def test_two_values_with_unexpected_hook_are_refused():
    """При две стойности няма как да се провери коя е куката."""

    assert read_owned_stirrup([50.0, 15.0], preferred_hook=10.0) is None


def test_single_value_is_not_enough():
    assert read_owned_stirrup([20.0]) is None


def test_more_than_three_distinct_owned_values_are_ambiguous():
    assert read_owned_stirrup([100.0, 50.0, 20.0, 10.0]) is None


def test_no_arithmetic_check_against_total_length():
    """Сборът НЕ се проверява — описва се каквото е надписано.

    2*(35+20)+2*10 = 130, а надписът може да е 135. Разликата е добавка за
    огъване на проектанта и не е повод за отказ.
    """

    assert read_owned_stirrup([35.0, 20.0, 10.0]) == [35.0, 20.0, 10.0]


def test_duplicates_do_not_create_a_third_value():
    assert read_owned_stirrup([20.0, 20.0, 10.0],
                              preferred_hook=10.0) == [20.0, 20.0, 10.0]


# ------------------------------------------------ свързаност

def test_ownership_is_the_first_source_in_the_analysis():
    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    assert "dimensions_owned_by_contours" in source
    start = source.index("def _apply_dimension_derived_production_sizes")
    body = source[start:start + 26000]
    # Първото срещане на derive_shape е предварителното минаване за
    # добавката за огъване; резервният път е този СЛЕД четенето на
    # собствените коти.
    owned = body.index("read_owned_stirrup(")
    fallback = body.index("evidence = derive_shape(", owned)
    assert owned < fallback, "собствените коти се четат ПРЕДИ търсенето по близост"
    assert "# 2) Резервен път" in body


def test_owned_source_is_recorded_for_audit():
    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    assert "СОБСТВЕНИ КОТИРОВКИ НА КОНТУРА" in source
    assert "OWNED_DIMENSIONS" in source


def test_viewport_shows_production_values_not_raw_measurements():
    from pathlib import Path

    view = Path("lion_rebar/ui/drawing_view.py").read_text(encoding="utf-8")
    assert "snap(value)" in view
    assert "f\"{value:g}\"" not in view
