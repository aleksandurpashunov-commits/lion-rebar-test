"""Всяка активна форма в ENGINE трябва да е употребима (rc44).

Собственик, 2026-09-13: „А защо след ф010 позициите ги няма?"

Отговорът беше, че ``master_engine_v031`` е буквален порт на заключения
ENGINE v0.3.1, който свършва на F009:

    MASTER_ENGINE_FILENAME = "OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html"
    MASTER_FORM_CODES = tuple(f"F{index:03d}" for index in range(1, 10))

Стълбищните форми влизат в ENGINE с v0.3.2 на 27.07.2026, самият ENGINE
стигна до 0.3.9, а производственият слой остана на 0.3.1. F010 беше добавен
на ръка, F019 — на 13.09.2026, останалите никой не ги пренесе.

Това не беше безобидно. Диалозите за ръчен избор
(``engineer_decision_dialog``, ``manual_form_assignment_dialog``) филтрират
по ``shape.active``, така че F011, F012, F013, F014 и F016 СЕ ПОКАЗВАХА на
оператора — и всяка от тях даваше „Неподдържан MASTER код". Програмата
предлагаше избор, който не може да изпълни.

Този тест пази цялото условие, а не конкретния списък: ако утре в ENGINE
влезе нова активна форма, той ще падне, докато производственият слой не я
догони.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    parameters_from_segments,
    render_master_svg,
    total_for,
)

_RELEASE = Path(__file__).resolve().parents[1]


def _engine():
    bundles = sorted((_RELEASE / "engine_updates").glob("*.lionengine"))
    if not bundles:
        pytest.skip("няма ENGINE пакет в engine_updates")
    engine, _manifest, _names = inspect_engine_bundle(bundles[-1])
    return engine


def _selectable(engine):
    """Точно филтърът, който ползват двата диалога за ръчен избор."""

    return [
        shape
        for shape in engine.shapes
        if shape.active and str(shape.state).upper() != "RETIRED"
    ]


def _segment_count(shape) -> int:
    recognition = shape.recognition or {}
    return int(recognition.get("segment_count") or len(shape.segments) or 3)


def test_има_какво_да_се_избира() -> None:
    """Предпазва теста от това да мине празен."""

    assert len(_selectable(_engine())) >= 14


def test_всяка_предлагана_форма_се_смята() -> None:
    """Форма, която операторът може да избере, трябва да дава размери.

    ``F002`` е изключена от този обход, защото приема сегменти, но описва
    производствените си размери като проекции (DX2/DY2) и затова има
    собствени тестове; тук се проверява, че кодът ѝ изобщо е познат.
    """

    неподдържани = []
    for shape in _selectable(_engine()):
        count = _segment_count(shape)
        segments = tuple(50.0 for _ in range(count))
        try:
            parameters_from_segments(
                shape.code,
                segments,
                production_length_cm=50.0 * count,
                turn_angles_deg=(90.0,) * max(0, count - 1),
            )
        except MasterDimensionError as error:
            if "Неподдържан MASTER код" in str(error):
                неподдържани.append(f"{shape.code} {shape.name}")
        except Exception:  # noqa: BLE001 - друга причина, не липсваща форма
            continue

    assert not неподдържани, (
        "Тези форми се предлагат на оператора, но производственият слой не ги "
        "познава: " + ", ".join(неподдържани)
    )


def test_всяка_предлагана_форма_се_рисува() -> None:
    """Без рисунка операторът вижда грешка вместо форма."""

    без_рисунка = []
    for shape in _selectable(_engine()):
        count = _segment_count(shape)
        values = {f"L{index}": 50.0 for index in range(1, count + 1)}
        try:
            svg = render_master_svg(shape.code, values)
        except Exception:  # noqa: BLE001
            без_рисунка.append(f"{shape.code} {shape.name}")
            continue
        if "<svg" not in svg:
            без_рисунка.append(f"{shape.code} {shape.name}")

    assert not без_рисунка, (
        "Тези форми се предлагат на оператора, но нямат рисунка: "
        + ", ".join(без_рисунка)
    )


def test_общата_дължина_е_известна_за_всяка_предлагана_форма() -> None:
    """``total_for`` връща 0.0 за непознат код — това мълчи вместо да спре."""

    нулеви = []
    for shape in _selectable(_engine()):
        count = _segment_count(shape)
        values = {f"L{index}": 50.0 for index in range(1, count + 1)}
        if total_for(shape.code, values) <= 0.0:
            нулеви.append(f"{shape.code} {shape.name}")

    assert not нулеви, (
        "За тези форми общата дължина излиза 0: " + ", ".join(нулеви)
    )


def test_оттеглените_форми_не_се_предлагат() -> None:
    """F015, F017 и F018 са RETIRED и нямат производствени параметри."""

    codes = {shape.code for shape in _selectable(_engine())}

    assert not ({"F015", "F017", "F018"} & codes)
