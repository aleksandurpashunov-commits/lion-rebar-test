"""Форма от редактора влиза в ENGINE като чернова.

Собственик, 23.09.2026: „можем ли да направим такъв HTML, който аз да запазя и
от него да вкарваме формите, защото много добре ги рисува и много лесно“.

Пакетът тук е точно този, който бутонът „Свали текущата .json“ произвежда за
„Кобилица-1“ — същите числа, които собственикът показа на екрана.
"""
from __future__ import annotations

import copy

import pytest

from lion_rebar.engine.draft_import import (
    DraftImportError,
    LOCKED_FORM_CODES,
    read_draft_package,
    shape_from_editor_form,
    shapes_from_editor_package,
)


def _чернова(**промени) -> dict:
    форма = {
        "proposed_code": "F022",
        "name": "Кобилица-1",
        "status": "ЧЕРНОВА_ЗА_ПРОВЕРКА",
        "initial_direction": "НАГОРЕ",
        "separate_overlapping_ends": True,
        "segments": [
            {"index": 1, "code": "L1", "length_cm": 17, "bend_after_deg": 90,
             "bend_direction": "НАДЯСНО", "dimension_position": "АВТО",
             "dimension_offset": 6},
            {"index": 2, "code": "L2", "length_cm": 30, "bend_after_deg": 45,
             "bend_direction": "НАДЯСНО", "dimension_position": "АВТО",
             "dimension_offset": 6},
            {"index": 3, "code": "L3", "length_cm": 24, "bend_after_deg": 45,
             "bend_direction": "НАЛЯВО", "dimension_position": "ОТГОРЕ",
             "dimension_offset": 8},
            {"index": 4, "code": "L4", "length_cm": 50, "bend_after_deg": None,
             "bend_direction": "НЯМА", "dimension_position": "АВТО",
             "dimension_offset": 6},
        ],
        "segment_count": 4,
        "bend_count": 3,
        "total_length_cm": 121,
        "calculation": "17 + 30 + 24 + 50 = 121 cm",
        "geometry_key": "N|17@90R|30@45R|24@45L|50",
        "notes": "",
    }
    форма.update(промени)
    return форма


def _пакет(*форми) -> dict:
    return {
        "schema": "lion-rebar-shape-draft/v1",
        "locked_forms": sorted(LOCKED_FORM_CODES),
        "forms": list(форми or (_чернова(),)),
    }


# --- прочитът на файла ------------------------------------------------------


def test_приема_пакет_списък_и_една_чернова():
    """Редакторът сам чете и трите вида; тук не се стеснява."""

    assert len(read_draft_package(_пакет())) == 1
    assert len(read_draft_package([_чернова(), _чернова(proposed_code="F023")])) == 2
    assert len(read_draft_package(_чернова())) == 1


def test_празен_файл_е_отказ():
    with pytest.raises(DraftImportError, match="нито една чернова"):
        read_draft_package({"schema": "lion-rebar-shape-draft/v1", "forms": []})
    with pytest.raises(DraftImportError):
        read_draft_package("не е json обект")


# --- формата ----------------------------------------------------------------


def test_черновата_става_форма_едно_към_едно():
    форма = shape_from_editor_form(_чернова())

    assert (форма.code, форма.name) == ("F022", "Кобилица-1")
    assert форма.initial_direction == "UP"
    assert [s.length_code for s in форма.segments] == ["L1", "L2", "L3", "L4"]
    assert [s.bend_after_deg for s in форма.segments] == [90.0, 45.0, 45.0, None]
    assert [s.bend_direction for s in форма.segments] == ["RIGHT", "RIGHT", "LEFT", "NONE"]


def test_страната_и_отстъпът_на_размера_се_пренасят():
    """Колоните „РАЗМЕР“ и „ОТСТЪП“ на редактора са част от формата."""

    трети = shape_from_editor_form(_чернова()).segments[2]
    assert трети.dimension_position == "TOP"
    assert трети.dimension_offset == 8.0


def test_формата_излиза_ЧЕРНОВА():
    """Вносителят не публикува и не одобрява."""

    форма = shape_from_editor_form(_чернова())
    assert форма.state == "DRAFT"
    assert форма.locked is False


def test_носи_контролен_тест_от_начертаните_размери():
    форма = shape_from_editor_form(_чернова())

    assert len(форма.tests) == 1
    тест = форма.tests[0]
    assert тест["shape_code"] == "F022"
    assert тест["source"] == "IMPORTED_FROM_EDITOR"
    assert тест["dimensions"] == "L1=17; L2=30; L3=24; L4=50"
    assert тест["production_length_cm"] == pytest.approx(121.0)


def test_геометричният_ключ_и_отметката_се_пазят():
    форма = shape_from_editor_form(_чернова())
    assert форма.recognition["geometry_key"] == "N|17@90R|30@45R|24@45L|50"
    assert форма.recognition["separate_overlapping_ends"] is True
    assert форма.recognition["imported_from_editor"] is True


def test_рисува_се_като_в_редактора():
    """Същият пренесен рисувач, същите надписи."""

    import re

    from lion_rebar.export.engine_draft_render import render_shape_draft_svg

    svg = render_shape_draft_svg(shape_from_editor_form(_чернова()), [17, 30, 24, 50])
    надписи = re.findall(r'class="draft-dimension-text"[^>]*>([^<]+)<', svg)
    assert надписи == ["17 cm", "30 cm", "24 cm", "50 cm"]


# --- отказите ---------------------------------------------------------------


def test_сбор_който_не_отговаря_на_обявеното_е_отказ():
    """Отказът е по-добър от догадката: не се внася „каквото става“."""

    with pytest.raises(DraftImportError, match="обявената дължина"):
        shape_from_editor_form(_чернова(total_length_cm=999))


@pytest.mark.parametrize("код", sorted(LOCKED_FORM_CODES))
def test_заключена_форма_не_се_пренаписва(код):
    with pytest.raises(DraftImportError, match="заключена"):
        shape_from_editor_form(_чернова(proposed_code=код))


@pytest.mark.parametrize("код", ["", "Ф022", "F22", "F0222", "КОБИЛИЦА"])
def test_кодът_трябва_да_е_във_вида_F0NN(код):
    with pytest.raises(DraftImportError, match="вида F0NN"):
        shape_from_editor_form(_чернова(proposed_code=код))


def test_формата_иска_име():
    with pytest.raises(DraftImportError, match="иска име"):
        shape_from_editor_form(_чернова(name="   "))


def test_участък_без_положителна_дължина_е_отказ():
    сурова = _чернова()
    сурова["segments"][1]["length_cm"] = 0
    with pytest.raises(DraftImportError, match="положително"):
        shape_from_editor_form(сурова)


def test_последният_участък_не_носи_завой():
    сурова = _чернова()
    сурова["segments"][-1]["bend_after_deg"] = 90
    сурова["segments"][-1]["bend_direction"] = "НАЛЯВО"
    with pytest.raises(DraftImportError, match="последен"):
        shape_from_editor_form(сурова)


def test_завой_без_посока_е_отказ():
    сурова = _чернова()
    сурова["segments"][0]["bend_direction"] = "НЯМА"
    with pytest.raises(DraftImportError, match="без посока"):
        shape_from_editor_form(сурова)


def test_липсващ_ъгъл_по_средата_е_отказ():
    сурова = _чернова()
    сурова["segments"][1]["bend_after_deg"] = None
    with pytest.raises(DraftImportError, match="липсва ъгъл"):
        shape_from_editor_form(сурова)


@pytest.mark.parametrize("ъгъл", [-1, 181, 360])
def test_ъгъл_извън_обхвата_е_отказ(ъгъл):
    сурова = _чернова()
    сурова["segments"][0]["bend_after_deg"] = ъгъл
    with pytest.raises(DraftImportError, match="извън"):
        shape_from_editor_form(сурова)


def test_непозната_дума_е_отказ_а_не_подразбиране():
    with pytest.raises(DraftImportError, match="непозната стойност"):
        shape_from_editor_form(_чернова(initial_direction="НАКЪДЕТО_И_ДА_Е"))


# --- пакетът ----------------------------------------------------------------


def test_целият_пакет_влиза_наведнъж():
    втора = _чернова(proposed_code="F023", name="Кобилица-3")
    форми = shapes_from_editor_package(_пакет(_чернова(), втора))
    assert [ф.code for ф in форми] == ["F022", "F023"]


def test_повтореният_код_в_един_файл_е_отказ():
    with pytest.raises(DraftImportError, match="два пъти"):
        shapes_from_editor_package(_пакет(_чернова(), _чернова()))


def test_сблъсък_със_съществуваща_форма_е_отказ():
    with pytest.raises(DraftImportError, match="вече съществува"):
        shapes_from_editor_package(_пакет(), taken_codes=["F020", "F022"])


def test_един_негоден_отказва_целия_файл():
    """Не се внася половин пакет."""

    лоша = _чернова(proposed_code="F023", total_length_cm=999)
    with pytest.raises(DraftImportError):
        shapes_from_editor_package(_пакет(_чернова(), лоша))


def test_радиусът_се_приема_ако_дойде():
    """``РАДИУС_ОГЪВКА_СМ`` още е празно навсякъде, но не се губи."""

    сурова = _чернова()
    сурова["segments"][0]["bend_radius_cm"] = 4.0
    форма = shape_from_editor_form(сурова)
    assert форма.segments[0].radius_cm == 4.0
    assert shape_from_editor_form(_чернова()).segments[0].radius_cm is None
