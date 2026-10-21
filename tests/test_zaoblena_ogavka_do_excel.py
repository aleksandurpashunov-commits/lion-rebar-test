# -*- coding: utf-8 -*-
"""Заоблената огъвка: размерите идват от проектанта, не от начертаните линии.

Чертежът h01-1001 (07.10.2026) чертае всяка огъвка като поредица от къси
хорди. Два пътя след разпознаването четяха суровата полилиния:

* закръгляването към мрежата получаваше 10 участъка срещу 2 разпознати и не се
  произнасяше — поправено: вижда свитата огъвка и дава напр. 20/60/20;
* пътят към Excel пращаше 10 участъка към MASTER („F004 очаква 2 сегмента,
  получени са 10“).

Първата поправка на втория път (свиване и изнасяне на измереното) беше
ГРЕШНА и е отхвърлена: срещу бележките на собственика („2*3N12x150“ → 130/20,
„3N10x425“ → 405/20, „3N14x415“ → 375/40) свитата полилиния дава 128,8/18,8,
404/19, 373,75/38,6 — три от три различни. Начертаните рамена на заоблена
огъвка не са размери. Затова без доказан вектор такъв прът е ЯСЕН отказ с
причината, а с доказан вектор минава с него.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

from lion_rebar.dxf.models import DxfEntityRecord
import pytest

from lion_rebar.export.errors import ExcelExportError
from lion_rebar.export.excel_export import _segments_for_link


def _f004_със_заобление(рамо_a: float, рамо_b: float, радиус: float, хорди: int) -> list[list[float]]:
    """Ъгъл в (0, 0): вертикално рамо нагоре, хоризонтално надясно.

    Огъвката е дъга с център (радиус, радиус), начертана с ``хорди`` отсечки —
    както в чертежа на непознатото бюро (8 хорди по ~0,56 cm).
    """

    точки = [[0.0, рамо_a]]
    for i in range(хорди + 1):
        ъгъл = math.pi + (math.pi / 2) * i / хорди
        точки.append([радиус + радиус * math.cos(ъгъл), радиус + радиус * math.sin(ъгъл)])
    точки.append([рамо_b, 0.0])
    return точки


def _връзка(точки: list[list[float]]) -> tuple[dict, dict]:
    запис = DxfEntityRecord(
        "P1", "LWPOLYLINE", "s-arm",
        {"points": точки, "closed": False},
    )
    return (
        {"annotation_handle": "A1", "annotation_text": "2*3N12x150", "geometry_handles": ["P1"]},
        {"P1": запис},
    )


def _f003_със_заобления(рамо: float, среда: float, радиус: float = 2.9, хорди: int = 8) -> list[list[float]]:
    точки = [[0.0, рамо]]
    for i in range(хорди + 1):
        ъгъл = math.pi + (math.pi / 2) * i / хорди
        точки.append([радиус + радиус * math.cos(ъгъл), радиус + радиус * math.sin(ъгъл)])
    for i in range(хорди + 1):
        ъгъл = -math.pi / 2 + (math.pi / 2) * i / хорди
        точки.append([среда - радиус + радиус * math.cos(ъгъл), радиус + радиус * math.sin(ъгъл)])
    точки.append([среда, рамо])
    return точки


def test_заоблена_огъвка_без_доказани_размери_е_ясен_отказ():
    # Старият код връщаше 10 сурови участъка; първата поправка — 128,8/18,8.
    # И двете са тиха грешка: собственикът е написал 130/20.
    връзка, обекти = _връзка(_f004_със_заобление(18.8, 128.8, 2.9, 8))
    with pytest.raises(ExcelExportError) as грешка:
        _segments_for_link(
            връзка,
            shape=SimpleNamespace(code="F004", segments=[]),
            entity_map=обекти,
            production_length_cm=150.0,
        )
    текст = str(грешка.value)
    assert "заоблена" in текст and "128.8" in текст and "18.8" in текст


def test_две_заоблени_огъвки_на_f003_без_доказани_размери_е_отказ():
    връзка, обекти = _връзка(_f003_със_заобления(20.0, 60.0))
    with pytest.raises(ExcelExportError, match="заоблена"):
        _segments_for_link(
            връзка,
            shape=SimpleNamespace(code="F003", segments=[]),
            entity_map=обекти,
            production_length_cm=100.0,
        )


def test_доказаният_вектор_минава_независимо_от_заоблението():
    # Векторът, записан от портата (напр. закръгляне към мрежата), е този,
    # който стига до Excel — и при заоблена огъвка.
    връзка, обекти = _връзка(_f004_със_заобление(18.8, 128.8, 2.9, 8))
    връзка["production_segment_lengths_cm"] = [130.0, 20.0]
    връзка["production_turn_angles_deg"] = [90.0]
    участъци, завои = _segments_for_link(
        връзка,
        shape=SimpleNamespace(code="F004", segments=[]),
        entity_map=обекти,
        production_length_cm=150.0,
    )
    assert участъци == (130.0, 20.0)
    assert завои == (90.0,)


def test_остър_ъгъл_не_се_променя():
    # Положителна контрола: без заобление пътят е същият като преди.
    връзка, обекти = _връзка([[0.0, 20.0], [0.0, 0.0], [130.0, 0.0]])
    участъци, _ = _segments_for_link(
        връзка,
        shape=SimpleNamespace(code="F004", segments=[]),
        entity_map=обекти,
        production_length_cm=150.0,
    )
    assert участъци == (130.0, 20.0)


def test_мрежата_вижда_свитата_огъвка():
    """Проходът „закръгляне към мрежа“ чете същите свити участъци.

    Без свиването той получаваше 10 участъка срещу 2 разпознати и не се
    произнасяше. „16N10x100/20“ от прегледа: начертано 19/59/19, собственикът
    пише 20/60/20 — мрежата 5 дава точно това.
    """

    from lion_rebar.dxf import grid_rounded_segments as мрежа
    from lion_rebar.dxf.batch_geometry_report import _semantic_segment_lengths_cm

    радиус, хорди = 2.9, 8
    точки = [[0.0, 20.1]]
    for i in range(хорди + 1):
        ъгъл = math.pi + (math.pi / 2) * i / хорди
        точки.append([радиус + радиус * math.cos(ъгъл), радиус + радиус * math.sin(ъгъл)])
    for i in range(хорди + 1):
        ъгъл = -math.pi / 2 + (math.pi / 2) * i / хорди
        точки.append([59.0 - радиус + радиус * math.cos(ъгъл), радиус + радиус * math.sin(ъгъл)])
    точки.append([59.0, 20.1])
    _, обекти = _връзка(точки)
    ред = {"candidate_handles": ["P1"], "declared_length_cm": 100.0, "segment_count": 3}
    участъци = _semantic_segment_lengths_cm(ред, обекти)
    assert близо(участъци, (20.1, 59.0, 20.1))
    присъда = мрежа.verdict_for_row(ред, участъци)
    assert присъда["status"] == мрежа.ПРИЕТО
    assert присъда["segments_cm"] == [20.0, 60.0, 20.0]


def test_мрежата_без_заобление_е_непроменена():
    # Положителна контрола: остри ъгли — участъците са същите като преди.
    from lion_rebar.dxf.batch_geometry_report import _semantic_segment_lengths_cm

    _, обекти = _връзка([[0.0, 20.0], [0.0, 0.0], [60.0, 0.0], [60.0, 20.0]])
    ред = {"candidate_handles": ["P1"]}
    assert _semantic_segment_lengths_cm(ред, обекти) == [20.0, 60.0, 20.0]


def близо(получени, очаквани, допуск: float = 0.05) -> bool:
    return len(получени) == len(очаквани) and all(
        abs(a - b) <= допуск for a, b in zip(получени, очаквани)
    )
