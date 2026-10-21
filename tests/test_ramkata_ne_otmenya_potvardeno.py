# -*- coding: utf-8 -*-
"""Начертаната рамка НЕ отменя вече потвърдено решение за греда.

Собственик, 02.10.2026:

> Самото очертаване на рамка не е достатъчно основание да се отмени предишно
> потвърдено решение. Преди запис трябва да виждаш коя позиция се мести,
> откъде и накъде.

ДЕЛТАТА, която този файл пази. Дотук в `project/integrity._freeze_beam_assignments`
рамката печелеше БЕЗУСЛОВНО: `area_label` се прилагаше преди изобщо да се
погледне `operator_beam_decisions`, а решението за позиция се четеше само
когато автоматиката се е отказала. Тоест един правоъгълник мълчаливо отменяше
`record_beam_decision` — решение, което сочи ЕДНА позиция и една греда, с автор
и мотив.

Сега при разминаване печели РЕШЕНИЕТО, а спорът се вижда като
`BEAM_AREA_OVERRIDES_CONFIRMED_DECISION`, за да бъде решен изрично: или
решението се оттегля, или рамката се прекроява.

Когато двете сочат една и съща греда, спор няма и нищо не се променя.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.beam_decisions import (
    OPERATOR_METHOD,
    record_beam_decision,
    withdraw_beam_decision,
)
from lion_rebar.export.operator_areas import (
    KIND_BEAM,
    OPERATOR_AREA_BEAM_METHOD,
    record_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.integrity import _freeze_beam_assignments
from lion_rebar.review_center_model import ACTIONABLE_CLASS

# Две заглавия на греди и една позиция между тях. Координатите следват
# „тест-006": заглавията са едно под друго през СТЪПКА, а позицията стои под
# първото — тоест автоматиката я дава на „Гр. A.1".
СТЪПКА = 400.0
_ЗАГЛАВИЕ_1 = "Гр. A.1 25/50"
_ЗАГЛАВИЕ_2 = "Гр. A.2 25/50"
_ПОЗИЦИЯ_XY = (300.0, 1000.0 - 0.745 * СТЪПКА)
#: Рамката на ВТОРАТА греда, разтеглена нагоре така, че да хване позицията.
_РАМКА = (50.0, 1000.0 - СТЪПКА - 0.9 * СТЪПКА, 400.0, 1000.0)


def _самоличност() -> DataIdentity:
    return DataIdentity(
        project_id="P", source_sha256="S", source_generation_id="G", analysis_run_id="R"
    )


def _обекти() -> list[DxfEntityRecord]:
    return [
        DxfEntityRecord(
            "MARKER", "TEXT", "$Ramka", {"insert": [0.0, 2000.0, 0.0]}, "ЧЕРТЕЖ:"
        ),
        DxfEntityRecord(
            "FRAME", "TEXT", "$Ramka", {"insert": [104.0, 2000.0, 0.0]},
            "АРМИРОВКА ГРЕДИ НА КОТА +6.50",
        ),
        DxfEntityRecord(
            "B1", "TEXT", "_text", {"insert": [100.0, 1000.0, 0.0], "height": 35.0},
            _ЗАГЛАВИЕ_1,
        ),
        DxfEntityRecord(
            "B2", "TEXT", "_text",
            {"insert": [100.0, 1000.0 - СТЪПКА, 0.0], "height": 35.0}, _ЗАГЛАВИЕ_2,
        ),
        DxfEntityRecord(
            "P1", "BLOCK_ATTR_TEXT", "arm_gredi",
            {"insert": [_ПОЗИЦИЯ_XY[0], _ПОЗИЦИЯ_XY[1], 0.0]}, "4N12x240",
        ),
    ]


def _товар() -> dict[str, Any]:
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
            for e in _обекти()
        ]
    }


def _замрази(корен: Path):
    return _freeze_beam_assignments(
        rows=[
            {
                "annotation_class": ACTIONABLE_CLASS,
                "annotation_handle": "P1",
                "annotation_text": "4N12x240",
            }
        ],
        links_by_annotation={
            "P1": {
                "annotation_handle": "P1",
                "annotation_text": "4N12x240",
                "resolved_annotation_text": "4N12x240",
                "geometry_handles": [],
            }
        },
        extracted_payload=_товар(),
        project_root=корен,
        identity=_самоличност(),
    )


def _рамка_за_втората(корен: Path) -> None:
    record_operator_area(
        корен,
        box=_РАМКА,
        kind=KIND_BEAM,
        title_handle="B2",
        title_text=_ЗАГЛАВИЕ_2,
        frame_index=0,
        decided_by="Александър",
        note="оградено заедно със заглавието на Гр. A.2",
        identity=_самоличност(),
    )


def _решение(корен: Path, beam_handle: str, beam_name: str) -> None:
    record_beam_decision(
        корен,
        annotation_handle="P1",
        beam_label_handle=beam_handle,
        decided_by="Александър",
        note="позицията е на тази греда по чертежа",
        identity=_самоличност(),
        assignment_is_unresolved=True,
        known_beam_handles={beam_handle: beam_name},
    )


def test_bez_reshenie_ramkata_pechelli(tmp_path: Path) -> None:
    """Изходното положение, непроменено: рамката бие АВТОМАТИКАТА."""

    _рамка_за_втората(tmp_path)

    assignments, issues = _замрази(tmp_path)

    assert [i.code for i in issues] == []
    assert assignments["P1"]["beam_name"] == _ЗАГЛАВИЕ_2
    assert assignments["P1"]["beam_assignment_method"] == OPERATOR_AREA_BEAM_METHOD


def test_ramkata_ne_otmenya_potvardenoto_reshenie(tmp_path: Path) -> None:
    """ЧЕРВЕНИЯТ случай: дотук рамката мълчаливо местеше позицията."""

    _решение(tmp_path, "B1", _ЗАГЛАВИЕ_1)
    _рамка_за_втората(tmp_path)

    assignments, issues = _замрази(tmp_path)

    # Печели РЕШЕНИЕТО, не рамката.
    assert assignments["P1"]["beam_name"] == _ЗАГЛАВИЕ_1
    assert assignments["P1"]["beam_assignment_method"] == OPERATOR_METHOD
    # И спорът се вижда, вместо да се реши мълчаливо.
    кодове = [i.code for i in issues]
    assert "BEAM_AREA_OVERRIDES_CONFIRMED_DECISION" in кодове
    спор = next(
        i for i in issues if i.code == "BEAM_AREA_OVERRIDES_CONFIRMED_DECISION"
    )
    assert спор.annotation_handles == ["P1"]
    assert _ЗАГЛАВИЕ_1 in спор.message and _ЗАГЛАВИЕ_2 in спор.message


def test_sporat_ne_blokira_eksporta(tmp_path: Path) -> None:
    """Спорът е ПРЕДУПРЕЖДЕНИЕ: позицията има греда, просто не тази от рамката."""

    _решение(tmp_path, "B1", _ЗАГЛАВИЕ_1)
    _рамка_за_втората(tmp_path)

    _assignments, issues = _замрази(tmp_path)

    спор = next(
        i for i in issues if i.code == "BEAM_AREA_OVERRIDES_CONFIRMED_DECISION"
    )
    assert str(спор.severity).upper() == "WARNING"


def test_sashtata_greda_ne_e_spor(tmp_path: Path) -> None:
    """Решение и рамка за ЕДНА и съща греда: няма какво да се решава."""

    _решение(tmp_path, "B2", _ЗАГЛАВИЕ_2)
    _рамка_за_втората(tmp_path)

    assignments, issues = _замрази(tmp_path)

    assert [i.code for i in issues] == []
    assert assignments["P1"]["beam_name"] == _ЗАГЛАВИЕ_2


def test_sled_ottegliane_ramkata_pak_pechelli(tmp_path: Path) -> None:
    """Изричното решение е оттегляне на старото — тогава рамката важи."""

    _решение(tmp_path, "B1", _ЗАГЛАВИЕ_1)
    _рамка_за_втората(tmp_path)
    withdraw_beam_decision(
        tmp_path,
        annotation_handle="P1",
        withdrawn_by="Александър",
        note="грешно решение, позицията е на Гр. A.2",
    )

    assignments, issues = _замрази(tmp_path)

    assert [i.code for i in issues] == []
    assert assignments["P1"]["beam_name"] == _ЗАГЛАВИЕ_2
    assert assignments["P1"]["beam_assignment_method"] == OPERATOR_AREA_BEAM_METHOD
