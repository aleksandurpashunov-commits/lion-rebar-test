"""Финалният контрол чете областта, начертана преди анализа (rc44).

Собственик, 2026-09-13: „да направим рамките преди теста на чертежа, за да
може програмата да го прочете."

Проверява се самото замразяване — ``_freeze_beam_assignments``, функцията,
която Финалният контрол вика, — а не помощен модул около него. Тя е мястото,
където гредата и груповият етикет спират да бъдат догадка и се записват в
ledger-а, от който Excel export-ът чете и нищо друго.

Двете половини на задачата, всяка със своя чертеж:

  * ГРЕДИ (``s-9``): осемте позиции, които автоматиката отказва да свърже,
    защото стоят вляво и над заглавието си. Една рамка ги решава наведнъж.
  * ШАЙБИ (``s-7``): заглавията („Ш 3-25/600") съществуват, но затворените
    контури, по които се разпознават групите при колоните, там ги няма —
    описът излизаше накуп. Рамката е ръчният заместител на липсващия контур.

Координатите са реални, снети от самите чертежи.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.operator_areas import (
    KIND_BEAM,
    KIND_GROUP,
    OPERATOR_AREA_BEAM_METHOD,
    record_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.integrity import _freeze_beam_assignments
from lion_rebar.review_center_model import ACTIONABLE_CLASS

# --- s-9, рамка „греди" -----------------------------------------------------
# „Гр. B.19 25/55" стои на (113837, 2678); позиция 14C11 -- на (113382, 2762),
# тоест 455 единици ВЛЯВО при праг 71 и 84 НАД заглавието. Затова
# ``_assign_beam_label`` отказва, и с право: разширяването на прозореца
# дотолкова беше измерено върху 460 позиции и мести 27 верни на съседна греда.
_BEAM_TITLE = "Гр. B.19 25/55"
_BEAM_TITLE_XY = (113837.0, 2678.0)
_UNPROVEN_XY = (113382.0, 2762.0)
_BEAM_AREA = (113317.0, 2638.0, 113897.0, 2938.0)

# --- s-7, рамка „шайби" -----------------------------------------------------
_WALL_TITLE = "Ш 3-25/600"
_WALL_TITLE_XY = (6845.0, -139420.0)
_WALL_POSITION_XY = (6900.0, -139500.0)
_WALL_AREA = (6445.0, -139620.0, 7245.0, -139360.0)


def _identity() -> DataIdentity:
    return DataIdentity(
        project_id="P", source_sha256="S", source_generation_id="G", analysis_run_id="R"
    )


def _payload(entities: list[DxfEntityRecord]) -> dict[str, Any]:
    return {
        "entities": [
            {
                "handle": e.handle,
                "entity_type": e.entity_type,
                "layer": e.layer,
                "geometry": e.geometry,
                "text": e.text,
                "raw": e.raw,
            }
            for e in entities
        ]
    }


def _row(handle: str, text: str, **extra) -> dict[str, Any]:
    row = {
        "annotation_class": ACTIONABLE_CLASS,
        "annotation_handle": handle,
        "annotation_text": text,
    }
    row.update(extra)
    return row


def _link(handle: str, text: str) -> dict[str, Any]:
    return {
        "annotation_handle": handle,
        "annotation_text": text,
        "resolved_annotation_text": text,
        "geometry_handles": [],
    }


# ---------------------------------------------------------------------------
# ГРЕДИ
# ---------------------------------------------------------------------------

def _beam_entities() -> list[DxfEntityRecord]:
    return [
        # Заглавният блок, както го дава чертежът: маркер „ЧЕРТЕЖ:" на слой
        # ``$Ramka`` и заглавието на ~104 единици вдясно, на същия ред.
        DxfEntityRecord(
            "MARKER", "TEXT", "$Ramka", {"insert": [113000.0, 5000.0, 0.0]},
            "ЧЕРТЕЖ:",
        ),
        DxfEntityRecord(
            "FRAME", "TEXT", "$Ramka", {"insert": [113104.0, 5000.0, 0.0]},
            "АРМИРОВКА ГРЕДИ НА КОТА +5.90 ; +8.90 и +11.90 (B)",
        ),
        DxfEntityRecord(
            "BL19", "TEXT", "_text",
            {"insert": [_BEAM_TITLE_XY[0], _BEAM_TITLE_XY[1], 0.0], "height": 35.0},
            _BEAM_TITLE,
        ),
        DxfEntityRecord(
            "14C11", "BLOCK_ATTR_TEXT", "arm_gredi",
            {"insert": [_UNPROVEN_XY[0], _UNPROVEN_XY[1], 0.0]},
            "3N16x685",
        ),
    ]


def _freeze_beams(root: Path):
    return _freeze_beam_assignments(
        rows=[_row("14C11", "3N16x685")],
        links_by_annotation={"14C11": _link("14C11", "3N16x685")},
        extracted_payload=_payload(_beam_entities()),
        project_root=root,
        identity=_identity(),
    )


def test_без_рамка_позицията_остава_блокирана(tmp_path: Path) -> None:
    """Изходното положение: автоматиката отказва и си остава отказала."""

    assignments, issues = _freeze_beams(tmp_path)

    assert [i.code for i in issues] == ["BEAM_ASSIGNMENT_NOT_UNIQUE"]
    assert "14C11" not in assignments


def test_рамката_дава_гредата_на_позицията_вътре(tmp_path: Path) -> None:
    """Същият вход, но с рамка, начертана преди анализа.

    Това е разликата, която собственикът поиска: едно действие покрива цяла
    група, вместо решение за всяка позиция поотделно.
    """

    record_operator_area(
        tmp_path,
        box=_BEAM_AREA,
        kind=KIND_BEAM,
        title_handle="BL19",
        title_text=_BEAM_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="детайлът е над и вляво от заглавието на Гр. B.19",
        identity=_identity(),
    )

    assignments, issues = _freeze_beams(tmp_path)

    assert issues == []
    frozen = assignments["14C11"]
    assert frozen["beam_name"] == _BEAM_TITLE
    assert frozen["beam_label_handle"] == "BL19"
    assert frozen["beam_assignment_method"] == OPERATOR_AREA_BEAM_METHOD


def test_рамка_около_друга_греда_не_пипа_позицията(tmp_path: Path) -> None:
    """Рамката важи там, където е начертана, и никъде другаде."""

    record_operator_area(
        tmp_path,
        box=(200000.0, 2638.0, 200500.0, 2938.0),
        kind=KIND_BEAM,
        title_handle="BL19",
        title_text=_BEAM_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="рамка на съвсем друго място",
        identity=_identity(),
    )

    assignments, issues = _freeze_beams(tmp_path)

    assert [i.code for i in issues] == ["BEAM_ASSIGNMENT_NOT_UNIQUE"]
    assert "14C11" not in assignments


def test_рамка_от_друг_чертеж_не_се_наследява(tmp_path: Path) -> None:
    """Identity gate: рамка, чертана върху друга редакция, не важи тук."""

    record_operator_area(
        tmp_path,
        box=_BEAM_AREA,
        kind=KIND_BEAM,
        title_handle="BL19",
        title_text=_BEAM_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="рамка от предишната редакция на чертежа",
        identity=DataIdentity(
            project_id="P", source_sha256="S",
            source_generation_id="ДРУГО_ПОКОЛЕНИЕ", analysis_run_id="R",
        ),
    )

    assignments, issues = _freeze_beams(tmp_path)

    assert [i.code for i in issues] == ["BEAM_ASSIGNMENT_NOT_UNIQUE"]
    assert "14C11" not in assignments


def test_рамката_не_отменя_доказана_връзка(tmp_path: Path) -> None:
    """Позиция точно под заглавието си се решава от автоматиката.

    Рамката покрива и нея, но не бива да я пипа: операторско решение никога
    не отменя доказана автоматична връзка — то само решава недоказана.
    """

    proven = (113850.0, 2450.0)  # вдясно и под заглавието: в приетия прозорец
    entities = _beam_entities() + [
        DxfEntityRecord(
            "PROVEN", "BLOCK_ATTR_TEXT", "arm_gredi",
            {"insert": [proven[0], proven[1], 0.0]}, "2N12x300",
        )
    ]
    record_operator_area(
        tmp_path,
        box=(113300.0, 2300.0, 113900.0, 2938.0),
        kind=KIND_BEAM,
        title_handle="BL19",
        title_text=_BEAM_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="широка рамка, която покрива и доказаната позиция",
        identity=_identity(),
    )

    assignments, _issues = _freeze_beam_assignments(
        rows=[_row("14C11", "3N16x685"), _row("PROVEN", "2N12x300")],
        links_by_annotation={
            "14C11": _link("14C11", "3N16x685"),
            "PROVEN": _link("PROVEN", "2N12x300"),
        },
        extracted_payload=_payload(entities),
        project_root=tmp_path,
        identity=_identity(),
    )

    assert assignments["PROVEN"]["beam_assignment_method"] != OPERATOR_AREA_BEAM_METHOD
    assert assignments["14C11"]["beam_assignment_method"] == OPERATOR_AREA_BEAM_METHOD


# ---------------------------------------------------------------------------
# ШАЙБИ
# ---------------------------------------------------------------------------

def _wall_entities() -> list[DxfEntityRecord]:
    return [
        # Чертежът на шайбите няма поле „ЧЕРТЕЖ:": това е един логически лист,
        # разпознат по едно недвусмислено заглавие плюс точно един рамков
        # INSERT — точно както в истинския файл (``ramka_1``).
        DxfEntityRecord(
            "RAMKA", "INSERT", "ramka_1", {"insert": [0.0, -145000.0, 0.0]}, "",
        ),
        DxfEntityRecord(
            "FRAME", "TEXT", "$Ramka", {"insert": [6104.0, -138000.0, 0.0]},
            "АРМИРОВКА ШАЙБИ ОТ КОТА +18,18 ДО КОТА +21,48",
        ),
        DxfEntityRecord(
            "SH3", "TEXT", "C_TXT_shaibi",
            {"insert": [_WALL_TITLE_XY[0], _WALL_TITLE_XY[1], 0.0], "height": 30.0},
            _WALL_TITLE,
        ),
        DxfEntityRecord(
            "PXANN_1", "BLOCK_ATTR_TEXT", "arm_shaibi",
            {"insert": [_WALL_POSITION_XY[0], _WALL_POSITION_XY[1], 0.0]},
            "34N12x415",
        ),
    ]


def _freeze_walls(root: Path, **row_extra):
    return _freeze_beam_assignments(
        rows=[_row("PXANN_1", "34N12x415", **row_extra)],
        links_by_annotation={"PXANN_1": _link("PXANN_1", "34N12x415")},
        extracted_payload=_payload(_wall_entities()),
        project_root=root,
        identity=_identity(),
    )


def test_без_рамка_шайбата_няма_групов_етикет(tmp_path: Path) -> None:
    """Изходното положение при ``s-7``: 0 от 57 позиции с етикет."""

    assignments, _issues = _freeze_walls(tmp_path)

    assert assignments["PXANN_1"]["column_group_label"] == ""


def test_рамката_дава_груповия_етикет_на_шайбата(tmp_path: Path) -> None:
    """С това описът излиза блок по блок, както при колоните."""

    record_operator_area(
        tmp_path,
        box=_WALL_AREA,
        kind=KIND_GROUP,
        title_handle="SH3",
        title_text=_WALL_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="позициите под заглавието на Ш 3",
        identity=_identity(),
    )

    assignments, _issues = _freeze_walls(tmp_path)

    assert assignments["PXANN_1"]["column_group_label"] == _WALL_TITLE


def test_рамката_не_отменя_автоматичния_групов_етикет(tmp_path: Path) -> None:
    """При колоните програмата сама намира контура и заглавието.

    Там рамката няма работа: тя попълва празно, не презаписва доказано.
    """

    автоматичен = "%%UK23, K24 (35/35) - 2бр."
    record_operator_area(
        tmp_path,
        box=_WALL_AREA,
        kind=KIND_GROUP,
        title_handle="SH3",
        title_text=_WALL_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="рамка върху вече разпозната група",
        identity=_identity(),
    )

    assignments, _issues = _freeze_walls(tmp_path, column_group_label=автоматичен)

    assert assignments["PXANN_1"]["column_group_label"] == автоматичен


def test_рамка_за_греда_не_раздава_групови_етикети(tmp_path: Path) -> None:
    """Двата вида област не се смесват."""

    record_operator_area(
        tmp_path,
        box=_WALL_AREA,
        kind=KIND_BEAM,
        title_handle="SH3",
        title_text=_WALL_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="сгрешен вид област",
        identity=_identity(),
    )

    assignments, _issues = _freeze_walls(tmp_path)

    assert assignments["PXANN_1"]["column_group_label"] == ""
