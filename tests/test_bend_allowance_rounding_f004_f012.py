"""Добавката за огъване при F004/F012: закръгляне нагоре до кратно на 5 cm.

Измерено на 21.09.2026 по запазените пакетни отчети:

* прав прът F001 -- 177 позиции в "тест-001" и "d-2": недостиг НУЛА;
* F004/F012 -- 36 позиции в същите чертежи: всички с недостиг и всички
  точно ``ceil(сбор / 5) * 5``.

Случаите тук са НАСТОЯЩИ позиции от "тест-001" (операторска проверка на
собственика, 21.09.2026), а не измислени числа.
"""
import pytest

from lion_rebar.engine.bend_allowance import (
    BEND_ROUNDING_SHAPES,
    BEND_ROUNDING_STEP_CM,
    explain_mismatch,
    rounded_production_total,
    segments_match_total,
)


# (форма, сбор на начертаните участъци, надписана дължина, надпис в чертежа)
REAL_CASES = [
    ("F004", 453.0, 455.0, "28N12x455 -- начертани 440 + 13"),
    ("F004", 113.0, 115.0, "34N8x115 -- начертани 13 + 100"),
    ("F004", 163.0, 165.0, "44N8x165"),
    ("F004", 166.0, 170.0, "45N8x170 -- недостиг 4 cm"),
    ("F004", 316.0, 320.0, "3N16x320 -- недостиг 4 cm"),
    ("F004", 217.37, 220.0, "3N14x220 -- дъга, недостиг 2,63 cm"),
    ("F012", 462.25, 465.0, "28N12x465 -- недостиг 2,75 cm"),
    ("F004", 158.0, 160.0, "10N10x160 - 145/13, примерът на собственика"),
]


@pytest.mark.parametrize("code,total,declared,label", REAL_CASES)
def test_rounding_up_to_five_is_accepted(code, total, declared, label):
    """Недостигът се приема, когато надписаното Е следващото кратно на 5."""

    assert segments_match_total(code, total, declared), label
    assert explain_mismatch(code, total, declared) == ""


def test_straight_bar_is_never_rounded():
    """F001 няма огъвка: 177 измерени позиции с недостиг нула.

    Ако закръглянето важеше и за правия прът, сгрешен размер щеше да мине.
    """

    assert "F001" not in BEND_ROUNDING_SHAPES
    assert not segments_match_total("F001", 113.0, 115.0)
    assert rounded_production_total("F001", 113.0) is None


def test_sum_already_round_gets_no_allowance():
    """450 сочи себе си: закръгляне не оправдава недостиг при кръгъл размер."""

    assert rounded_production_total("F004", 450.0) == pytest.approx(450.0)
    assert not segments_match_total("F004", 450.0, 455.0)


def test_declared_that_is_not_the_rounding_target_is_refused():
    """457 не е цел на закръгляне -- по-строго от плосък таван от 4 cm."""

    assert rounded_production_total("F004", 453.0) == pytest.approx(455.0)
    assert not segments_match_total("F004", 453.0, 457.0)


def test_overrun_is_refused_for_rounding_shapes_too():
    """Надвишаването НИКОГА не е добавка -- асиметрията се пази."""

    assert not segments_match_total("F004", 457.0, 455.0)
    assert not segments_match_total("F012", 467.0, 465.0)


def test_deficit_beyond_one_step_is_refused():
    """Недостиг над една стъпка не може да е закръгляне."""

    assert not segments_match_total("F004", 447.0, 455.0)


def test_explanation_names_the_rounding_target():
    message = explain_mismatch("F004", 453.0, 457.0)
    assert "455" in message
    assert f"{BEND_ROUNDING_STEP_CM:g}" in message


def test_existing_flat_allowances_are_untouched():
    """F006 = 8 cm и F009 = 2,1 cm остават както са измерени."""

    assert "F006" not in BEND_ROUNDING_SHAPES
    assert "F009" not in BEND_ROUNDING_SHAPES
    assert segments_match_total("F006", 110.0, 115.0)
    assert not segments_match_total("F006", 100.0, 115.0)
    assert segments_match_total("F009", 189.0, 190.0)


# --- Собственик, 22.09.2026 -------------------------------------------------
# „оправи това с допуска +- няколко сантиметра при огъвките да се показва така,
# както е на чертежа, нищо че не съвпада 100% с общата дължина.“
#
# Случаят: F008 СТОЛЧЕ в „тест-001“ — надписаните 15 + 9 + 20 + 9 + 15 = 68
# при обявени 70. Вчера го реши, като вдигна крачето на 16; днес иска
# чертежът да се показва както е.

СТОЛЧЕТА = [
    ("F008", 68.0, 70.0, "645N10x70 — надписани 15/9/20/9/15"),
    ("F008", 76.0, 80.0, "535N10x80 — надписани 15/13/20/13/15"),
]


@pytest.mark.parametrize("code,total,declared,label", СТОЛЧЕТА)
def test_столчето_показва_надписаните_размери(code, total, declared, label):
    assert segments_match_total(code, total, declared), label


def test_закръглянето_важи_за_всяка_форма_С_огъвка():
    """Правилото е обърнато: изключението е изброено, не допуснатите."""

    from lion_rebar.engine.bend_allowance import STRAIGHT_SHAPES

    assert STRAIGHT_SHAPES == frozenset({"F001"})
    for code in ("F002", "F003", "F005", "F006", "F007", "F008",
                 "F010", "F011", "F020"):
        assert rounded_production_total(code, 68.0) == pytest.approx(70.0), code


def test_правият_прът_остава_вън():
    assert rounded_production_total("F001", 68.0) is None
    assert not segments_match_total("F001", 68.0, 70.0)


def test_неизвестна_форма_не_получава_закръгляне():
    """Празен код значи „не се знае каква е формата“ — нищо не се приема."""

    assert rounded_production_total(None, 68.0) is None
    assert rounded_production_total("", 68.0) is None
    assert not segments_match_total(None, 68.0, 70.0)


def test_асиметрията_не_е_пипната_при_новите_форми():
    """Надвишаване и недостиг извън целта остават отказ."""

    assert not segments_match_total("F008", 72.0, 70.0)   # надвишава
    assert not segments_match_total("F008", 68.0, 72.0)   # 72 не е цел
    assert not segments_match_total("F008", 63.0, 70.0)   # над една стъпка
