"""Заглавие на греда се чете и извън рамката „ГРЕДИ“ (rc44).

Собственик, 2026-09-13, пред списъка с греди: „гр-13 я няма в списъка да я
избера и да нанеса позициите."

В ``s-9`` има 32 заглавия на греди, а се разпознаваха 26. Шестте липсващи —
B.1, B.7, B.13, B.18, B.24, B.29 — са на същия слой ``Section``, но попадат в
рамката ДЕТАЙЛИ вместо в рамката ГРЕДИ. По нищо не личи, че е грешка на
проектанта; просто така е чертано.

Последицата беше тежка: операторът НЕ МОЖЕШЕ да ги посочи изобщо — нито от
падащия списък, нито с рамка (тя иска разпознато заглавие вътре). А
разстоянието не решава спора: за една и съща позиция Гр. B.19 е на 463
единици, а Гр. B.18 — на 492. Шест процента разлика, и едната изобщо не
участва в състезанието.

КАКВО НЕ СЕ ПРОМЕНЯ
-------------------
Автоматичното свързване. ``_assign_beam_label`` търси само сред заглавията
със същия ``frame_index``, така че греда от рамка ДЕТАЙЛИ не може да
открадне позиция от рамка ГРЕДИ. Проверено върху всичките 149 позиции в
гредовата рамка на ``s-9``: 0 променени връзки.
"""
from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.excel_export import _assign_beam_label, _detect_beam_labels
from lion_rebar.export.frame_detection import detect_drawing_frames


def _sheet() -> list[DxfEntityRecord]:
    """Два листа: ГРЕДИ и ДЕТАЙЛИ, всеки със свое заглавие на греда."""

    return [
        # Рамка 1 — ГРЕДИ
        DxfEntityRecord("M1", "TEXT", "$Ramka", {"insert": [0.0, 5000.0, 0.0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord(
            "F1", "TEXT", "$Ramka", {"insert": [104.0, 5000.0, 0.0]},
            "АРМИРОВКА ГРЕДИ НА КОТА +5.90",
        ),
        DxfEntityRecord(
            "BL19", "TEXT", "Section",
            {"insert": [500.0, 2678.0, 0.0], "height": 35.0}, "Гр. B.19 25/55",
        ),
        # Рамка 2 — ДЕТАЙЛИ, далеч вдясно
        DxfEntityRecord("M2", "TEXT", "$Ramka", {"insert": [50000.0, 5000.0, 0.0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord(
            "F2", "TEXT", "$Ramka", {"insert": [50104.0, 5000.0, 0.0]},
            "ДЕТАЙЛИ АРМИРОВКА НА КОТА +5.90",
        ),
        DxfEntityRecord(
            "BL13", "TEXT", "Section",
            {"insert": [50500.0, 2678.0, 0.0], "height": 35.0}, "Гр. B.13 25/50",
        ),
    ]


def _labels():
    entities = _sheet()
    return entities, _detect_beam_labels(entities, detect_drawing_frames(entities))


def test_чете_се_и_заглавието_от_рамка_детайли() -> None:
    """Без това операторът няма как да посочи Гр. B.13."""

    _entities, labels = _labels()

    titles = {label.title for label in labels}

    assert "Гр. B.19 25/55" in titles
    assert "Гр. B.13 25/50" in titles, "заглавието от рамка ДЕТАЙЛИ е изпуснато"


def test_всяко_заглавие_помни_своята_рамка() -> None:
    """``frame_index`` е защитата, която пази автоматиката непроменена."""

    entities, labels = _labels()
    frames = detect_drawing_frames(entities)
    по_заглавие = {label.title: label.frame_index for label in labels}

    assert len(frames) == 2
    assert по_заглавие["Гр. B.19 25/55"] != по_заглавие["Гр. B.13 25/50"]


def test_греда_от_друга_рамка_не_краде_позиция() -> None:
    """Най-важното: автоматиката остава каквато беше.

    Позиция в рамката ГРЕДИ се свързва с Гр. B.19, а не с Гр. B.13 — дори
    сега, когато втората вече се чете.
    """

    entities, labels = _labels()
    frames = detect_drawing_frames(entities)
    гредова = next(f for f in frames if "ГРЕДИ" in str(f.combined_title).upper())

    # Точка вдясно и под заглавието на Гр. B.19 — в приетия ѝ прозорец.
    assignment = _assign_beam_label(520.0, 2400.0, int(гредова.index), labels)

    assert assignment is not None
    assert assignment.label.title == "Гр. B.19 25/55"


def test_позиция_без_заглавие_в_своята_рамка_остава_нерешена() -> None:
    """Съседната рамка не се ползва като резерва — fail-closed."""

    entities, labels = _labels()
    frames = detect_drawing_frames(entities)
    гредова = next(f for f in frames if "ГРЕДИ" in str(f.combined_title).upper())

    # Далеч вляво от единственото заглавие в тази рамка.
    assignment = _assign_beam_label(-9000.0, 2400.0, int(гредова.index), labels)

    assert assignment is None
