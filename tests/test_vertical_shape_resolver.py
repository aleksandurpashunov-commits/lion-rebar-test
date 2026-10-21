"""Регресионни тестове за извеждане на форма от котировки.

Еталон: реален чертеж на вертикални елементи, тип „Тип 1a — 170x25 — 2 бр.“
Формите и размерите са потвърдени от Product Owner (2026-08-25):

    200 бр. Ø8  L=135  -> F006 стреме 35 x 20, кука 10
     80 бр. Ø8  L=180  -> F003 прът с два винкела 15 + 150 + 15
     32 бр. Ø16 L=400  -> F001 прав прът
     24 бр. Ø6  L=40   -> F007 ес 10 + 20 + 10
     16 бр. Ø8  L=360  -> F001 прав прът
"""
from __future__ import annotations

from lion_rebar.dxf.vertical_shape_resolver import (
    BEND_DISCREPANCY_LIMIT_CM,
    derive_shape,
    infer_preferred_hook,
    label_kind,
    resolve_vertical_shape,
    snap,
)

ORIGIN = (0.0, 0.0)


def _dims(*values, spread: float = 30.0):
    """Котировки, подредени на нарастващо разстояние от позицията."""

    return [
        {"measurement": value, "point": (spread * (index + 1), 0.0)}
        for index, value in enumerate(values)
    ]


def _label(kind: str, distance: float):
    text = {"LONGITUDINAL": "Надл.арм.:", "S_HOOK": "Есове пълнеж:",
            "STIRRUP": "Стремена"}[kind]
    return [{"text": text, "point": (distance, 0.0)}]


# ------------------------------------------------------ закръгляне

def test_snap_rounds_measured_dimensions_to_production_sizes():
    assert snap(209.8) == 210.0
    assert snap(29.8) == 30.0
    assert snap(25.2) == 25.0
    assert snap(150.0) == 150.0


def test_snap_keeps_half_centimetre_when_far_from_integer():
    assert snap(22.5) == 22.5


# ------------------------------------------------------ уравнения

def test_stirrup_dimensions_are_read_not_computed():
    """Product Owner: описва се каквото ПИШЕ в чертежа.

    2*(35+20) + 2*10 = 130 при L=135. Разликата от 5 cm е добавката за
    огъване на проектанта и НЕ е наш проблем -- размерите се приемат
    както са надписани.
    """

    evidence = derive_shape(ORIGIN, _dims(35, 20, 10), 135)
    assert evidence is not None
    assert evidence.shape_code == "F006"
    assert [round(x) for x in evidence.segments] == [35, 20, 10]
    assert evidence.allowance == 5.0
    assert evidence.source == "НАДПИСАНИ КОТИРОВКИ"


def test_two_angle_bar_middle_is_computed_exactly():
    """Средата се ИЗЧИСЛЯВА от дължината, за да не влезе 209.8 в описа."""

    evidence = derive_shape(ORIGIN, _dims(15, 209.8), 240)
    assert evidence.shape_code == "F003"
    assert [round(x) for x in evidence.segments] == [15, 210, 15]
    assert all(float(x).is_integer() for x in evidence.segments)


def test_reference_type_1a_two_angle_bar():
    evidence = derive_shape(ORIGIN, _dims(15, 150, 10, 20), 180)
    assert evidence.shape_code == "F003"
    assert [round(x) for x in evidence.segments] == [15, 150, 15]


def test_reference_square_stirrup_needs_repeated_value():
    """Квадратно стреме 20x20 иска два пъти стойността 20."""

    evidence = derive_shape(ORIGIN, _dims(20, 10), 105, preferred_hook=10.0)
    assert evidence.shape_code == "F006"
    assert [round(x) for x in evidence.segments] == [20, 20, 10]


def test_bend_allowance_varies_between_positions():
    """Добавката не е константа: 5, 6 или друга до допустимия праг.

    Ползват се реалните разстояния от чертежа: при изкуствено бедни данни
    22x22 и 22x20 са еднакво валидни и изборът е произволен.
    """

    dims = [{"measurement": value, "point": (distance, 0.0)}
            for value, distance in ((22, 40), (20, 50), (10, 60), (25, 120))]
    evidence = derive_shape(ORIGIN, dims, 110, expected_shape="F006",
                            preferred_hook=10.0, preferred_gap=6.0)
    assert [round(x) for x in evidence.segments] == [22, 20, 10]
    assert 0 <= evidence.allowance <= BEND_DISCREPANCY_LIMIT_CM


def test_large_discrepancy_goes_to_the_operator():
    """Голямо разминаване значи коти от друга фигура -> ръчен преглед."""

    assert derive_shape(ORIGIN, _dims(20, 20, 10), 300,
                        expected_shape="F006") is None


def test_designer_hook_convention_resolves_ambiguity():
    """25/20/5 и 20/20/10 дават сходен сбор; печели обичайната кука.

    Реален случай 50N8x105: петицата е кота от съседна фигура.
    """

    dims = _dims(20, 10, 115, 25, 105, 120, 5, 95)
    assert [round(x) for x in derive_shape(ORIGIN, dims, 105,
            expected_shape="F006", preferred_hook=10.0).segments] == [20, 20, 10]


def test_preferred_hook_is_inferred_from_the_drawing():
    """Конвенцията се извежда от ТОЗИ чертеж, не отвън."""

    assert infer_preferred_hook([10, 10, 10, 20, 5, 10, 35, 700]) == 10.0
    assert infer_preferred_hook([700, 800]) is None


def test_long_side_may_be_far_from_the_label():
    """Дългата страна се коти под контура и е далеч от надписа.

    Реален случай 1x(25N8x395): стреме 165 x 20, кота 165 е дванадесета
    по близост.
    """

    # Реални разстояния от чертежа: 165 е на 212 единици, вътре в радиуса.
    measured = [(20, 119), (25, 128), (10, 154), (710, 163), (18, 168),
                (20, 169), (17.8, 186), (35, 189), (10, 198), (820, 212),
                (165, 212)]
    dims = [{"measurement": value, "point": (distance, 0.0)}
            for value, distance in measured]
    evidence = derive_shape(ORIGIN, dims, 395, expected_shape="F006",
                            preferred_hook=10.0)
    assert [round(x) for x in evidence.segments] == [165, 20, 10]


def test_expected_shape_constrains_the_search():
    """Формата идва от matcher-а; котировките дават само размерите.

    Без ограничението L=740 с кота 710 излизаше стреме 322x25 --
    геометрично възможно, производствено безсмислено.
    """

    evidence = derive_shape(ORIGIN, _dims(710, 25, 20), 740,
                            expected_shape="F003")
    assert evidence is None or evidence.shape_code == "F003"


def test_measured_solution_beats_computed_one():
    """L=135 се затваря и като стреме 35x20, и като прът 20/95/20.

    Печели решението, чиито размери ги ИМА в чертежа.
    """

    evidence = derive_shape(ORIGIN, _dims(35, 20, 10), 135)
    assert evidence.shape_code == "F006"
    assert evidence.computed_count == 0
    assert evidence.measured_count >= 3


# ------------------------------------------------------ fail-closed

def test_no_dimensions_returns_none():
    assert derive_shape(ORIGIN, [], 135) is None


def test_solution_with_computed_value_is_only_medium():
    """Решение, чийто главен размер е ИЗЧИСЛЕН, не е с висока увереност.

    Само с две котировки уравнението често се затваря, но липсва трета
    измерена стойност. Такъв резултат отива при оператора с MEDIUM.
    """

    evidence = derive_shape(ORIGIN, _dims(7, 9), 137)
    assert evidence is not None
    assert evidence.confidence == "MEDIUM"
    assert evidence.computed_count >= 1


def test_dimensions_below_minimum_are_ignored():
    """Котировки под 4 cm не са производствени размери."""

    assert derive_shape(ORIGIN, _dims(1.0, 2.5, 3.0), 135) is None


def test_distant_dimensions_are_ignored():
    far = [{"measurement": 35, "point": (5000.0, 0.0)},
           {"measurement": 20, "point": (5000.0, 0.0)}]
    assert derive_shape(ORIGIN, far, 135) is None


def test_zero_length_returns_none():
    assert derive_shape(ORIGIN, _dims(35, 20, 10), 0) is None


# ------------------------------------------------------ етикети

def test_label_kind_recognises_groups():
    assert label_kind("Надл.арм.:") == "LONGITUDINAL"
    assert label_kind("ВЕРТ.АРМИРОВКА") == "LONGITUDINAL"
    assert label_kind("%%UЕсове пълнеж:") == "S_HOOK"
    assert label_kind("Стремена") == "STIRRUP"
    assert label_kind("нещо друго") is None


def test_longitudinal_label_makes_straight_bar():
    evidence = resolve_vertical_shape(ORIGIN, 400, _label("LONGITUDINAL", 25), [])
    assert evidence.shape_code == "F001"
    assert evidence.segments == ()


def test_s_hook_label_takes_segments_from_dimensions():
    evidence = resolve_vertical_shape(
        ORIGIN, 40, _label("S_HOOK", 22), _dims(10, 20))
    assert evidence.shape_code == "F007"
    assert [round(x) for x in evidence.segments] == [10, 20, 10]


def test_distant_label_is_ignored():
    """Етикет отвъд прага не се приписва (чужди етикети са на 222-239)."""

    evidence = resolve_vertical_shape(
        ORIGIN, 135, _label("LONGITUDINAL", 240), _dims(35, 20, 10))
    assert evidence.shape_code == "F006"


def test_proven_stirrup_overrides_a_borderline_label():
    """Реален случай: 2184 бр. Ø8 L=110 е стреме, не надлъжен прът."""

    dims = [{"measurement": value, "point": (distance, 0.0)}
            for value, distance in ((22, 40), (20, 50), (10, 60), (25, 130))]
    evidence = resolve_vertical_shape(
        ORIGIN, 110, _label("LONGITUDINAL", 118), dims,
        preferred_hook=10.0, preferred_gap=6.0)
    assert evidence.shape_code == "F006"
    assert [round(x) for x in evidence.segments] == [22, 20, 10]


def test_close_label_wins_over_weak_stirrup():
    """Близък етикет „Надл.арм.“ не се презаписва."""

    evidence = resolve_vertical_shape(
        ORIGIN, 400, _label("LONGITUDINAL", 23), _dims(25, 18))
    assert evidence.shape_code == "F001"


def test_evidence_is_serialisable_for_audit():
    evidence = derive_shape(ORIGIN, _dims(35, 20, 10), 135)
    payload = evidence.to_dict()
    for key in ("shape_code", "segments", "source", "confidence",
                "bend_allowance_cm", "measured_dimensions"):
        assert key in payload


# ------------------------------------------------ свързаност с анализа

def test_resolver_is_wired_into_the_batch_report():
    """Модул без production caller не е завършена функция.

    Открито при Windows пробега: резолверът съществуваше и минаваше 20
    теста, но не се викаше отникъде — числата в анализа не се промениха.
    """

    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    assert "_apply_dimension_derived_production_sizes" in source
    assert "from lion_rebar.dxf.vertical_shape_resolver import (" in source
    assert "derive_shape," in source
    assert "infer_preferred_hook," in source
    # Извиква се в тялото на build_batch_geometry_report, не само дефинирана.
    start = source.index("def build_batch_geometry_report")
    body = source[start:start + 20000]
    assert "_apply_dimension_derived_production_sizes(rows, entities" in body


def test_dimension_step_runs_after_the_proven_path():
    """Котировките са ВТОРИ източник, не заместват доказаните размери."""

    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    printed = source.index("_apply_f006_printed_production_dimensions(\n        rows,")
    derived = source.index("_apply_dimension_derived_production_sizes(rows, entities")
    assert printed < derived


def test_only_rows_without_proven_sizes_are_touched():
    """Никога не се презаписват вече доказани производствени размери."""

    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    start = source.index("def _apply_dimension_derived_production_sizes")
    body = source[start:start + 18000]
    assert 'row.get("production_segments_cm")' in body
    assert 'master != "F006"' in body


def test_designer_hook_convention_is_inferred_per_drawing():
    """Конвенцията се извежда от текущия чертеж, не се внася отвън."""

    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    start = source.index("def _apply_dimension_derived_production_sizes")
    body = source[start:start + 5000]
    assert "infer_preferred_hook(" in body
    assert "preferred_hook=preferred_hook" in body


def test_open_shapes_are_covered_too():
    """F002/F003/F004/F007 също получават доказани сегменти."""

    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    start = source.index("def _apply_dimension_derived_production_sizes")
    body = source[start:start + 14000]
    assert '("F003", "F004", "F007", "F002")' in body


# ------------------- потвърдени от чертежа еталони (Product Owner)

def _drawing(*pairs):
    return [{"measurement": value, "point": (distance, 0.0)}
            for value, distance in pairs]


def test_confirmed_stirrup_255():
    """1092N8x255 -> 95 x 20, кука 10 (потвърдено в AutoCAD)."""

    dims = _drawing((22, 40), (10, 55), (95, 70), (20, 95),
                    (18, 120), (100, 140), (25, 160))
    evidence = derive_shape((0.0, 0.0), dims, 255, expected_shape="F006",
                            preferred_hook=10.0, preferred_gap=5.0)
    assert [round(x) for x in evidence.segments] == [95, 20, 10]


def test_confirmed_stirrup_115():
    """56N8x115 -> 25 x 20, кука 10."""

    dims = _drawing((20, 40), (25, 55), (10, 70), (145, 120),
                    (150, 140), (95, 160), (75, 180), (22, 200))
    evidence = derive_shape((0.0, 0.0), dims, 115, expected_shape="F006",
                            preferred_hook=10.0, preferred_gap=5.0)
    assert [round(x) for x in evidence.segments] == [25, 20, 10]


def test_square_stirrup_beats_three_distinct_values():
    """50N8x105 -> 20 x 20, кука 10, а НЕ 25 x 14.

    По-ранна версия предпочиташе комбинации с три различни числа и
    избираше изкуственото 25 x 14.
    """

    dims = _drawing((20, 49), (10, 72), (115, 90), (25, 100),
                    (105, 110), (14, 120), (5, 130))
    evidence = derive_shape((0.0, 0.0), dims, 105, expected_shape="F006",
                            preferred_hook=10.0, preferred_gap=5.0)
    assert [round(x) for x in evidence.segments] == [20, 20, 10]


def test_preferred_gap_resolves_ambiguity():
    """95x22 (разлика 1) и 95x20 (разлика 5) се връзват и двете."""

    dims = _drawing((22, 40), (10, 55), (95, 70), (20, 95))
    with_gap = derive_shape((0.0, 0.0), dims, 255, expected_shape="F006",
                            preferred_hook=10.0, preferred_gap=5.0)
    assert [round(x) for x in with_gap.segments] == [95, 20, 10]


def test_gap_convention_is_inferred_from_the_drawing():
    from lion_rebar.dxf.vertical_shape_resolver import infer_preferred_gap

    assert infer_preferred_gap([5.0, 5.0, 5.0, 1.0, 6.0]) == 5.0
    assert infer_preferred_gap([1.0, 2.0]) is None
