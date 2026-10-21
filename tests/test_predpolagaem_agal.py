# -*- coding: utf-8 -*-
"""Отказаните участъци отказват и ъгъла: никаква „мълчалива C форма".

## Случаят, който този файл пази

`s-11 / 92F7::BJ-FI-L`, надпис „36N6x40". В същия чертеж има **осем** позиции с
този надпис. Седем излизат с участъци [10; 20; 10] и НАЧЕРТАНИТЕ ъгли
`[-135°; +135°]` — Z. Осмата излизаше с:

* ``production_segments_cm = None`` — участъците ѝ са отказани;
* ``canonical_turn_angles_deg = [90°; 135°]`` — ъглите на **C**-шаблона;
* статус ``СИЛЕН ПО ГЕОМЕТРИЯ``, тоест автоматично одобрена.

Собствените ѝ бележки казваха точно какво не е наред — два пъти — и завършваха с
„остава за оператора". Но нищо не я оставяше за оператора: тя не беше сред
четирите спрени позиции на `s-11`. Седем пъти Z, веднъж C, без нито един въпрос.

Измерено върху целия корпус: 16 позиции с непотвърдени изведени участъци, 11 от
тях излизат автоматично, и **точно една** носи ъгли на шаблона без доказан
вектор. Поправката спира една позиция от 2562 — цената в автоматика е нула.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.batch_geometry_report import (
    _block_authoritative_segment_evidence,
    носи_предполагаем_ъгъл,
)


def ред(**промени) -> dict:
    """Редът на `92F7` в състоянието, в което беше дефектен."""

    основа = {
        "annotation_handle": "92F7::BJ-FI-L",
        "annotation_text": "36N6x40",
        "declared_length_cm": 40.0,
        "master_shape_code": "F007",
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "canonical_turn_angles_deg": [90.0, 135.0],   # шаблонът, не чертежът
        "canonical_segment_lengths_cm": [10.0, 20.0, 10.0],
        "review_reason_codes": ["CURVED_LAYER_ALIAS"],
    }
    основа.update(промени)
    return основа


# --------------------------------------------------------------------------
# Самото разпознаване на предполагаемия ъгъл
# --------------------------------------------------------------------------

def test_agal_bez_dokazan_vektor_e_predpolagaem():
    """Ъгъл ИМА, доказан вектор НЯМА → ъгълът може да е само от шаблона."""

    assert носи_предполагаем_ъгъл(ред()) is True


def test_dokazaniyat_vektor_pravi_agala_nepredpolagaem():
    """Със собствен производствен вектор ъгълът вече стъпва на нещо."""

    assert носи_предполагаем_ъгъл(
        ред(production_segments_cm=[10.0, 20.0, 10.0])) is False


def test_bez_agli_nyama_kakvo_da_se_predpolaga():
    """Позиция без ъгли не произвежда предположение — няма какво да се спира."""

    assert носи_предполагаем_ъгъл(ред(canonical_turn_angles_deg=[])) is False
    assert носи_предполагаем_ъгъл(ред(canonical_turn_angles_deg=None)) is False


@pytest.mark.parametrize("боклук", [
    ["не е число"], [None], [True], [False], ["90"],
])
def test_nechislovite_agli_ne_se_broyat_za_agli(боклук):
    """``True`` е ``int`` в Python — но не е ъгъл. Не бива да спира позиция."""

    assert носи_предполагаем_ъгъл(
        ред(canonical_turn_angles_deg=боклук)) is False


def test_prazen_vektor_ne_e_dokazatelstvo():
    """Празен списък участъци е същото като липсващ — не доказва ъгъла."""

    assert носи_предполагаем_ъгъл(ред(production_segments_cm=[])) is True


# --------------------------------------------------------------------------
# Какво прави спирането
# --------------------------------------------------------------------------

def test_spiraneto_prashta_poziciyata_pri_operatora():
    """Спряната позиция носи причина, код за преглед и вдигнат флаг."""

    r = ред()
    _block_authoritative_segment_evidence(r, "DERIVED_SEGMENTS_UNCONFIRMED")
    assert r["production_export_blocked"] is True
    assert r["production_export_block_reason"] == "DERIVED_SEGMENTS_UNCONFIRMED"
    assert "DERIVED_SEGMENTS_UNCONFIRMED" in r["review_reason_codes"]
    # Предишните кодове не се губят.
    assert "CURVED_LAYER_ALIAS" in r["review_reason_codes"]


def test_spiraneto_NE_proizvezhda_agal():
    """Спирането не поправя ъгъла и не измисля друг — само спира.

    Важно е кое НЕ се прави: не се вписва „вероятно −135°", не се взима ъгълът
    на съседна позиция със същия надпис. Операторът получава оригиналната форма
    и конкретното липсващо, и решава сам.
    """

    r = ред()
    преди = list(r["canonical_turn_angles_deg"])
    _block_authoritative_segment_evidence(r, "DERIVED_SEGMENTS_UNCONFIRMED")
    assert r["canonical_turn_angles_deg"] == преди
    assert "production_segments_cm" not in r


def test_spryana_poziciya_ne_minava_kam_eksporta():
    """Портата на експорта чете точно този флаг."""

    from lion_rebar.export.auto_gate import AUTO_EXPORT_BATCH_STATUSES

    r = ред()
    assert r["batch_status"] in AUTO_EXPORT_BATCH_STATUSES      # преди спирането
    _block_authoritative_segment_evidence(r, "DERIVED_SEGMENTS_UNCONFIRMED")
    излиза = (r["batch_status"] in AUTO_EXPORT_BATCH_STATUSES
              and not r.get("production_export_blocked"))
    assert излиза is False


# --------------------------------------------------------------------------
# K2: недоказан НЕОБХОДИМ ъгъл — бележка не стига
# --------------------------------------------------------------------------

def ред_куки(**промени) -> dict:
    """`s-8/7DE4`: кука, чието късо рамо е начертано 26 при отпечатани 35."""

    основа = {
        "annotation_handle": "7DE4::BJ-FI-L",
        "annotation_text": "32N10x1070",
        "master_shape_code": "F004",
        "batch_status": "СИЛЕН",
        "production_segments_cm": [1035.0, 35.0],
        "canonical_turn_angles_deg": [],
        "contextual_rule_code": None,
        "proven_form": {
            "state": "UNRESOLVED",
            "legs_to_scale": [True, False],
            "bends": [{"index": 0, "state": "UNRESOLVED",
                       "drawn_turn_deg": 90.0, "drawn_as": "SHARP_CORNER"}],
        },
    }
    основа.update(промени)
    return основа


def test_nedokazaniyat_neobhodim_agal_e_nahodka():
    """Вектор има, форма НЕразрешена, правило няма, завой начертан → находка."""

    from lion_rebar.dxf.batch_geometry_report import необходимият_ъгъл_е_недоказан

    обяснение = необходимият_ъгъл_е_недоказан(ред_куки())
    assert обяснение
    assert "F004" in обяснение and "90.0" in обяснение


@pytest.mark.parametrize("промяна,защо", [
    ({"contextual_rule_code": "BEAM_ENGINEERING_90_F003"}, "правилото го покрива"),
    ({"production_segments_cm": None}, "няма производствен вектор"),
    ({"proven_form": {"state": "DERIVED_DIRECTION", "bends": []}}, "формата е доказана"),
    ({"proven_form": {"state": "APPROVED_RULE", "bends": []}}, "по одобрено правило"),
    ({"proven_form": {"state": "UNRESOLVED", "bends": []}}, "няма начертан завой"),
    ({"proven_form": None}, "няма присъда изобщо"),
])
def test_kogato_NE_e_nahodka(промяна, защо):
    """Всеки от тези случаи НЕ е недоказан необходим ъгъл."""

    from lion_rebar.dxf.batch_geometry_report import необходимият_ъгъл_е_недоказан

    assert необходимият_ъгъл_е_недоказан(ред_куки(**промяна)) == "", защо


def test_pravият_prat_nyama_kakvo_da_dokazva():
    """Без завои няма необходим ъгъл — прав прът не се спира."""

    from lion_rebar.dxf.batch_geometry_report import необходимият_ъгъл_е_недоказан

    r = ред_куки(master_shape_code="F001",
                 production_segments_cm=[1070.0],
                 proven_form={"state": "UNRESOLVED", "bends": []})
    assert необходимият_ъгъл_е_недоказан(r) == ""
