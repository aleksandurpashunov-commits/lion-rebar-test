"""Операторският блок излиза със СВОЕ заглавие в листа „греди".

Собственик, 19.09.2026: „всеки отделен блок направен от оператора трябва да
има свое заглавие и ред в изходния ексел (не да се прибавя някъде на слуки)."

Дефектът, който това поправя, е измерен върху „Сграда бета\\Плоча_кота 14.90":
двадесет и две позиции — щурците „Щурцове 1 до 9", „Б-B.2 капандури" и
„Борд -A.1 върху плоча", общо 3254 kg или 7,1% от описа — излизаха в листа
„греди" под Гр. B.8, Гр. B.12 и Гр. B.14. Две неща се събираха:

* ``_beam_sections`` групираше САМО по ``beam_name``, а клонът за
  операторската група стоеше след ``category_code == "BEAMS"`` и не се
  достигаше. Заглавието на блока беше вярно записано в ledger-а и не се
  четеше;
* ``beam_name`` идваше от евристиката по близост (``AUTO_BEAM_REGION_V1``),
  чийто собствен показател за тези редове беше най-слабият в чертежа
  (3,0–3,9 стъпки на ред) — тя се протягаше далеч и пак побеждаваше
  изричното решение на оператора.

Координатите на гредата са същите като в ``test_operator_area_final_control``:
позиция на (113850, 2450) е в приетия прозорец на „Гр. B.19 25/55" и
автоматиката ѝ дава тази греда. Точно нея тук покриваме с блок.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.excel_export import (
    ExcelExportError,
    ExportPosition,
    _beam_sections,
    _validate_beam_assignments,
)
from lion_rebar.export.frame_detection import DrawingFrame
from lion_rebar.export.operator_areas import (
    KIND_BEAM,
    KIND_GROUP,
    OPERATOR_AREA_BEAM_METHOD,
    OPERATOR_AREA_GROUP_METHOD,
    record_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.integrity import _freeze_beam_assignments
from lion_rebar.review_center_model import ACTIONABLE_CLASS

_BEAM_TITLE = "Гр. B.19 25/55"
_BEAM_TITLE_XY = (113837.0, 2678.0)
#: Вдясно и под заглавието: евристиката приема тази позиция за гредата.
_INSIDE_WINDOW = (113850.0, 2450.0)
_BEAM_AREA = (113317.0, 2638.0, 113897.0, 2938.0)
_BLOCK_TITLE = "Щурцове 1 до 9"
_BLOCK_AREA = (113700.0, 2300.0, 114000.0, 2600.0)


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


def _entities() -> list[DxfEntityRecord]:
    return [
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
            "BLOCKTITLE", "TEXT", "Section",
            {"insert": [113750.0, 2560.0, 0.0], "height": 25.0}, _BLOCK_TITLE,
        ),
        DxfEntityRecord(
            "POS", "BLOCK_ATTR_TEXT", "arm_gredi",
            {"insert": [_INSIDE_WINDOW[0], _INSIDE_WINDOW[1], 0.0]}, "32N8x166",
        ),
    ]


def _freeze(root: Path):
    return _freeze_beam_assignments(
        rows=[
            {
                "annotation_class": ACTIONABLE_CLASS,
                "annotation_handle": "POS",
                "annotation_text": "32N8x166",
            }
        ],
        links_by_annotation={
            "POS": {
                "annotation_handle": "POS",
                "annotation_text": "32N8x166",
                "resolved_annotation_text": "32N8x166",
                "geometry_handles": [],
            }
        },
        extracted_payload=_payload(_entities()),
        project_root=root,
        identity=_identity(),
    )


def _record_block(root: Path) -> None:
    record_operator_area(
        root,
        box=_BLOCK_AREA,
        kind=KIND_GROUP,
        title_handle="BLOCKTITLE",
        title_text=_BLOCK_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="щурците са свой елемент, не армировка на гредата",
        identity=_identity(),
    )


# ------------------------------------------------- Финален контрол


def test_без_блок_автоматиката_дава_гредата(tmp_path: Path) -> None:
    """Изходното положение: евристиката закача позицията за гредата."""

    assignments, _issues = _freeze(tmp_path)

    assert assignments["POS"]["beam_name"] == _BEAM_TITLE
    assert assignments["POS"]["beam_assignment_method"] == "AUTO_BEAM_REGION_V1"
    assert assignments["POS"]["column_group_label"] == ""


def test_блокът_спира_гредата_по_близост(tmp_path: Path) -> None:
    """Същият вход с операторски блок: гредата отпада, блокът остава.

    Това е редът, който собственикът иска: изричното твърдение бие
    близостта. Преди тази поправка ledger-ът носеше И блока, И чуждата
    греда — и листът „греди" показваше втората.
    """

    _record_block(tmp_path)

    assignments, issues = _freeze(tmp_path)

    frozen = assignments["POS"]
    assert frozen["column_group_label"] == _BLOCK_TITLE
    assert frozen["beam_name"] == ""
    assert frozen["beam_label_handle"] == ""
    assert frozen["beam_assignment_method"] == OPERATOR_AREA_GROUP_METHOD
    # Липсващата греда НЕ е грешка: операторът е казал къде отива позицията.
    assert [issue.code for issue in issues] == []


def test_рамката_за_греда_бие_блока(tmp_path: Path) -> None:
    """И двете рамки покриват позицията: гредата е по-конкретното твърдение."""

    _record_block(tmp_path)
    record_operator_area(
        tmp_path,
        box=(113700.0, 2300.0, 114000.0, 2938.0),
        kind=KIND_BEAM,
        title_handle="BL19",
        title_text=_BEAM_TITLE,
        frame_index=1,
        decided_by="Александър",
        note="тази позиция все пак е на гредата",
        identity=_identity(),
    )

    assignments, _issues = _freeze(tmp_path)

    assert assignments["POS"]["beam_name"] == _BEAM_TITLE
    assert assignments["POS"]["beam_assignment_method"] == OPERATOR_AREA_BEAM_METHOD


# ------------------------------------------------- листът „греди"


def _position(
    handle: str,
    text: str,
    *,
    beam_name: str = "",
    beam_label_handle: str = "",
    block: str = "",
) -> ExportPosition:
    return ExportPosition(
        annotation_handle=handle,
        annotation_text=text,
        count=1,
        diameter_mm=8.0,
        production_length_cm=166.0,
        shape_code="F006",
        shape_name="СТРЕМЕ (БИГЛА)",
        shape_revision="1",
        geometry_handles=(),
        segment_lengths_cm=(10.0, 40.0, 33.0, 40.0, 33.0, 10.0),
        frame_index=1,
        beam_name=beam_name,
        beam_label_handle=beam_label_handle,
        beam_assignment_method=(
            OPERATOR_AREA_GROUP_METHOD if block else "AUTO_BEAM_REGION_V1"
        ),
        column_group_label=block,
    )


def _frame() -> DrawingFrame:
    return DrawingFrame(
        index=1,
        marker_handle="MARKER",
        anchor_x=0.0,
        anchor_y=0.0,
        title_text="АРМИРОВКА ГРЕДИ",
        supporting_text="",
        category_code="BEAMS",
        suggested_sheet_name="греди",
        confidence="HIGH",
    )


def test_блокът_е_свой_раздел_със_своето_заглавие() -> None:
    positions = [
        _position("A", "3N16x685", beam_name="Гр. B.2 25/55", beam_label_handle="B2"),
        _position("B", "32N8x166", block=_BLOCK_TITLE),
        _position("C", "28N8x166", block=_BLOCK_TITLE),
        _position("D", "2N12x300", beam_name="Гр. B.10 25/50", beam_label_handle="B10"),
    ]

    sections = _beam_sections(positions)

    titles = [section.title for section in sections]
    # Гредите по номер, блокът след тях — със своето заглавие, не под греда.
    assert titles == ["Гр. B.2 25/55", "Гр. B.10 25/50", _BLOCK_TITLE]
    block = sections[-1]
    assert {group.positions[0].annotation_handle for group in block.groups} == {"B", "C"}
    assert block.label_handle == ""
    for section in sections[:-1]:
        assert len(section.groups) == 1


def test_блокът_не_се_слива_с_греда_със_същото_име() -> None:
    """Ключът на раздела е заглавието плюс произходът, не само текстът."""

    positions = [
        _position("A", "3N16x685", beam_name=_BLOCK_TITLE, beam_label_handle="B99"),
        _position("B", "32N8x166", block=_BLOCK_TITLE),
    ]

    sections = _beam_sections(positions)

    assert len(sections) == 2
    assert {section.label_handle for section in sections} == {"B99", ""}


def test_позиция_в_блок_не_блокира_експорта() -> None:
    """Без греда, но с блок: проверката на гредите не се обажда."""

    _validate_beam_assignments(
        [_frame()],
        [],
        [_position("B", "32N8x166", block=_BLOCK_TITLE)],
    )


def test_позиция_без_греда_и_без_блок_пак_блокира() -> None:
    try:
        _validate_beam_assignments([_frame()], [], [_position("X", "32N8x166")])
    except ExcelExportError as exc:
        assert "без еднозначна греда" in str(exc)
    else:  # pragma: no cover - защитата трябва да се обади
        raise AssertionError("експортът трябваше да блокира")
