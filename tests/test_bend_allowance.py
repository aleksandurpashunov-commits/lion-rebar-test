"""Добавка за огъване — единен източник на истина.

ПРАВИЛО (Product Owner, 2026-08-25): в описа влиза това, което е
НАДПИСАНО в чертежа. При F006 проектантът изписва страните, а общата
дължина включва добавка за огъвките:

    56N8x115 -> стреме 25 x 20, кука 10 -> 2*(25+20)+2*10 = 110 при L=115

ЗАЩО ТЕЗИ ТЕСТОВЕ СЪЩЕСТВУВАТ
-----------------------------
Същата проверка живееше на ПЕТ различни места. Поправянето им едно по
едно означаваше, че операторът удря следваща преграда след всеки ъпдейт.
Тестът ``test_no_independent_sum_checks_remain`` пази това да не се
повтори.
"""
from __future__ import annotations

import re
from pathlib import Path

from lion_rebar.engine.bend_allowance import (
    BEND_ALLOWANCE_LIMIT_CM,
    allowance_for,
    explain_mismatch,
    segments_match_total,
)


# ------------------------------------------------ правилото

def test_confirmed_stirrup_is_accepted():
    """Реалният случай, който блокираше записа седем версии подред."""

    assert segments_match_total("F006", 110, 115)


def test_larger_stirrup_with_same_allowance():
    assert segments_match_total("F006", 250, 255)


def test_exact_match_is_accepted():
    assert segments_match_total("F006", 250, 250)


def test_overshoot_is_refused():
    """Сегменти, по-дълги от общата дължина, не са добавка, а грешка."""

    assert not segments_match_total("F006", 260, 250)


def test_excessive_undershoot_is_refused():
    assert not segments_match_total("F006", 110, 200)


def test_open_shapes_get_no_allowance():
    """При F003 сборът НА сегментите Е производствената дължина."""

    assert allowance_for("F003") == 0.0
    assert not segments_match_total("F003", 175, 180)
    assert segments_match_total("F003", 180, 180)


def test_allowance_only_for_closed_stirrup():
    assert allowance_for("F006") == BEND_ALLOWANCE_LIMIT_CM
    for code in ("F001", "F002", "F003", "F004", "F007", ""):
        assert allowance_for(code) == 0.0


def test_explanation_names_the_real_problem():
    assert "НАДВИШАВА" in explain_mismatch("F006", 260, 250)
    assert "добавка за огъване" in explain_mismatch("F006", 110, 200)
    assert explain_mismatch("F006", 110, 115) == ""


def test_invalid_input_is_refused_not_crashing():
    assert not segments_match_total("F006", "не число", 115)


# ------------------------------------------------ всички места минават оттук

_MODULES = (
    "lion_rebar/engineer_decision.py",
    "lion_rebar/ui/engineer_decision_dialog.py",
    "lion_rebar/export/master_engine_v031.py",
    "lion_rebar/export/engine_shape_parameters.py",
    "lion_rebar/export/excel_export.py",
)


def test_every_gate_uses_the_shared_validator():
    """Петте места, които блокираха, вече викат общия модул."""

    for module in _MODULES:
        source = Path(module).read_text(encoding="utf-8")
        assert "segments_match_total" in source, module


def test_no_independent_sum_checks_remain():
    """Никое от петте места не си прави собствена проверка.

    Този тест е защитата срещу повторение: ако някой добави нова проверка
    ``abs(sum(...) - total) > x``, тя ще блокира оператора мълчаливо.
    """

    pattern = re.compile(r"abs\(\s*sum\([^)]*\)\s*-\s*[a-z_]*(?:total|length)")
    for module in _MODULES:
        source = Path(module).read_text(encoding="utf-8")
        matches = [line.strip() for line in source.splitlines()
                   if pattern.search(line)]
        assert not matches, f"{module}: {matches}"


def test_engine_accepts_the_confirmed_stirrup():
    """Проверка през реалния Engine, не само през помощния модул."""

    from lion_rebar.engine.package_io import inspect_engine_bundle
    from lion_rebar.export.engine_shape_parameters import parameters_for_shape

    bundle = next(Path("engine_updates").glob("*v0.3.13*.lionengine"))
    engine, _, _ = inspect_engine_bundle(bundle)
    shape = {item.code: item for item in engine.shapes}["F006"]
    parameters_for_shape(shape, (10.0, 20.0, 25.0, 20.0, 25.0, 10.0),
                         production_length_cm=115.0, turn_angles_deg=())


def test_engine_still_refuses_overshoot():
    from lion_rebar.engine.package_io import inspect_engine_bundle
    from lion_rebar.export.engine_shape_parameters import parameters_for_shape

    bundle = next(Path("engine_updates").glob("*v0.3.13*.lionengine"))
    engine, _, _ = inspect_engine_bundle(bundle)
    shape = {item.code: item for item in engine.shapes}["F006"]
    try:
        parameters_for_shape(shape, (10.0, 20.0, 95.0, 20.0, 95.0, 10.0),
                             production_length_cm=200.0, turn_angles_deg=())
    except Exception:  # noqa: BLE001
        return
    raise AssertionError("надвишаването трябва да се отхвърли")
