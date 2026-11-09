# -*- coding: utf-8 -*-
"""Столчето: начертаната верига от пет отсечки и котата до всяка от тях.

Собственик, 08.11.2026: „Оправи това да разпознава столчетата. Нелепо е
постоянно да ги потвърждавам" и „столчето е нормална форма и не разбирам какво
не чете програмата, има всичко".

Координатите са от „Греди +6.50" (``4362N10x97``, надпис ``AB92``). Същият
детайл „Монтажни столчета" стои в s-8, s-9, тест-006 и два пъти в SS-1.
Matcher-ът взимаше само средната линия ``AB86`` (78 при L=97) и отказваше за
дължина. Верижният път вече четеше 15 + 18,5 + 30 + 18,5 + 15 = 97, но отказваше
на проверката на реда: столчето е начертано в АКСОНОМЕТРИЯ — височините са 78
единици, горната част 66, при надписани 18,5 и 30.

Решението на собственика за същата позиция: F008 ``[15, 18.5, 30, 18.5, 15]``.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf import chain_printed_dimensions as верига
from lion_rebar.dxf.batch_geometry_report import (
    _apply_chain_printed_production_segments,
)
from lion_rebar.dxf.local_detail_resolver import stolche_captions
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.export.auto_gate import validate_production_segment_evidence

СЛОЙ = "Arm - N10"


def _права(handle, начало, край):
    return DxfEntityRecord(
        handle=handle, entity_type="LINE", layer=СЛОЙ,
        geometry={"start": [начало[0], начало[1], 0.0], "end": [край[0], край[1], 0.0]},
    )


def _текст(handle, текст, точка, *, слой="ARMTXT1", височина=10.56, ротация=0.0, кутия=None):
    геометрия = {"insert": [точка[0], точка[1], 0.0], "height": височина, "rotation": ротация}
    if кутия:
        геометрия["visual_bbox"] = [[x, y, 0.0] for x, y in кутия]
    return DxfEntityRecord(handle=handle, entity_type="TEXT", layer=слой, text=текст,
                           geometry=геометрия)


ВЕРИГА = [
    _права("AB8A", (53747.8763, 3977.344), (53799.8378, 3947.344)),
    _права("AB86", (53747.8763, 4055.444), (53747.8763, 3977.344)),
    _права("AB87", (53747.8763, 4055.444), (53690.7186, 4022.444)),
    _права("AB88", (53690.7186, 4022.444), (53690.7186, 3944.344)),
    _права("AB89", (53690.7186, 3944.344), (53638.7571, 3974.344)),
]
КОТИ = [
    _текст("AB8D", "18.5", (53764.735, 4003.386), ротация=90.0),
    _текст("AB8E", "30", (53703.291, 4037.202), ротация=30.0),
    _текст("AB8F", "15", (53648.866, 3945.875), ротация=330.0),
    _текст("AB90", "15", (53760.186, 3951.28), ротация=330.0),
    _текст("AB91", "18.5", (53683.571, 3973.379), ротация=90.0),
]
ЗАГЛАВИЕ = _текст(
    "AB97", "Монтажни столчета", (53694.814, 4101.695), слой="KTXTXT", височина=12.5,
    кутия=[(53694.81, 4101.69), (53826.56, 4101.69), (53694.81, 4116.69), (53826.56, 4116.69)],
)
НАДПИС = _текст(
    "AB92::BJ-FI-L", "4362N10x97", (53784.08, 4009.5), слой="ARM_TXT", височина=13.5,
    кутия=[(53784.08, 4009.5), (53870.0, 4009.5), (53784.08, 4025.7), (53870.0, 4025.7)],
)
СТОЛЧЕ = (15.0, 18.5, 30.0, 18.5, 15.0)


def _f008() -> ShapeDefinition:
    return ShapeDefinition(
        code="F008", name="СТОЛЧЕ", state="LOCKED", active=True, closed=False,
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=45, bend_direction="LEFT"),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=90, bend_direction="RIGHT"),
            SegmentDefinition(index=3, length_code="L3", bend_after_deg=90, bend_direction="RIGHT"),
            SegmentDefinition(index=4, length_code="L2", bend_after_deg=135, bend_direction="LEFT"),
            SegmentDefinition(index=5, length_code="L1"),
        ],
    )


def _ред(**промени):
    ред = {
        "annotation_handle": "AB92::BJ-FI-L",
        "annotation_text": "4362N10x97",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 4362,
        "diameter_mm": 10.0,
        "declared_length_cm": 97.0,
        "batch_status": "РИСК",
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "candidate_handles": ["AB86"],
        "canonical_segment_lengths_cm": [78.1],
        "candidate_notes": [],
    }
    ред.update(промени)
    return ред


def _столчета(обекти=None):
    return stolche_captions([НАДПИС], обекти or [*ВЕРИГА, *КОТИ, ЗАГЛАВИЕ],
                            policy=RecognitionPolicy())


# --------------------------------------------------------------------------
# Верижната присъда
# --------------------------------------------------------------------------

def test_bez_stolche_redat_otkazva_kakto_dosega():
    """Положителна контрола: без заглавието проверката на реда отказва."""

    ук = верига.ВериженУказател([*ВЕРИГА, *КОТИ])
    дневник: list = []
    assert верига.присъда(["AB86"], ук, 97.0, дневник=дневник) is None
    assert дневник[0]["code"] == "REDAT_NE_E_SAGLASUVAN"


def test_stolcheto_dava_reda_ot_kotite():
    ук = верига.ВериженУказател([*ВЕРИГА, *КОТИ])
    р = верига.присъда(["AB86"], ук, 97.0, столче=True)
    assert р is not None
    assert р.участъци_cm == СТОЛЧЕ
    assert р.аксонометрично_столче is True
    assert р.as_dict()["axonometric_stolche"] is True


def test_nesimetrichni_razmeri_ne_sa_stolche():
    """Сборът затваря, но крачетата не са равни — това не е столче."""

    коти = [к if к.handle != "AB8F" else _текст("AB8F", "20", (53648.866, 3945.875), ротация=330.0)
            for к in КОТИ]
    ук = верига.ВериженУказател([*ВЕРИГА, *коти])
    дневник: list = []
    assert верига.присъда(["AB86"], ук, 102.0, столче=True, дневник=дневник) is None
    assert дневник[0]["code"] == "REDAT_NE_E_SAGLASUVAN"


def test_sborat_pak_reshava():
    """Изключението не пипа сбора: L=100 при надписани 97 е отказ."""

    ук = верига.ВериженУказател([*ВЕРИГА, *КОТИ])
    assert верига.присъда(["AB86"], ук, 100.0, столче=True) is None


def test_kracheto_ne_e_nepremenno_po_kasoto():
    """Тест-003: 15/9 — крачето е ПО-ДЪЛГОТО. Редът идва от присвояването."""

    assert верига.симетрично_столче((15.0, 9.0, 20.0, 9.0, 15.0))
    assert верига.симетрично_столче(СТОЛЧЕ)
    assert not верига.симетрично_столче((15.0, 18.5, 30.0, 18.5))
    assert not верига.симетрично_столче((15.0, 18.5, 30.0, 19.5, 15.0))


# --------------------------------------------------------------------------
# Заглавието
# --------------------------------------------------------------------------

def test_zaglavieto_e_na_svoya_nadpis():
    свързани = _столчета()
    assert set(свързани) == {"AB92::BJ-FI-L"}
    assert свързани["AB92::BJ-FI-L"].label_handle == "AB97"
    assert свързани["AB92::BJ-FI-L"].label_distance_raw < 95.0


def test_dalechno_zaglavie_ne_se_svarzva():
    далечно = _текст("AB97", "Монтажни столчета", (53694.814, 4301.695), слой="KTXTXT",
                     кутия=[(53694.81, 4301.69), (53826.56, 4301.69),
                            (53694.81, 4316.69), (53826.56, 4316.69)])
    assert stolche_captions([НАДПИС], [далечно], policy=RecognitionPolicy()) == {}


def test_dva_ravno_blizki_nadpisa_ne_poluchavat_zaglavie():
    """SS-1 има два надписа под едно заглавие; при равенство — на никого."""

    втори = _текст(
        "XX01::BJ-FI-L", "4435N10x99", (53784.08, 4009.5), слой="ARM_TXT", височина=13.5,
        кутия=[(53784.08, 4009.5), (53870.0, 4009.5), (53784.08, 4025.7), (53870.0, 4025.7)],
    )
    assert stolche_captions([НАДПИС, втори], [ЗАГЛАВИЕ], policy=RecognitionPolicy()) == {}


# --------------------------------------------------------------------------
# Целият проход в отчета и портата
# --------------------------------------------------------------------------

def _прекарай(ред, *, столчета=None):
    _apply_chain_printed_production_segments(
        [ред], [*ВЕРИГА, *КОТИ, ЗАГЛАВИЕ],
        stolche=_столчета() if столчета is None else столчета,
        shape_definitions=(_f008(),),
    )
    return ред


def test_redat_stava_f008_s_kotite():
    ред = _прекарай(_ред())
    assert ред["master_shape_code"] == "F008"
    assert ред["production_segments_cm"] == list(СТОЛЧЕ)
    assert ред["production_segment_evidence"]["source"] == "CHAIN_PRINTED_DIMENSIONS"
    assert set(ред["candidate_handles"]) == {"AB86", "AB87", "AB88", "AB89", "AB8A"}
    assert ред["stolche_previous_shape_code"] == "F001"
    assert validate_production_segment_evidence(ред, shape=_f008())


def test_bez_zaglavie_redat_ostava_neprokosnoven():
    ред = _прекарай(_ред(), столчета={})
    assert ред["master_shape_code"] == "F001"
    assert not ред.get("production_segments_cm")


@pytest.mark.parametrize("състояние", ["СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ", "ОДОБРЕНА ВРЪЗКА"])
def test_silen_red_ne_se_pipa(състояние):
    ред = _прекарай(_ред(batch_status=състояние))
    assert ред["master_shape_code"] == "F001"
    assert not ред.get("production_segments_cm")


def test_dokazana_druga_forma_ne_se_pipa():
    ред = _прекарай(_ред(master_shape_code="F003", canonical_segment_lengths_cm=[20, 57, 20]))
    assert ред["master_shape_code"] == "F003"
    assert not ред.get("production_segments_cm")


def test_bez_f008_v_master_nyama_stolche():
    ред = _ред()
    _apply_chain_printed_production_segments(
        [ред], [*ВЕРИГА, *КОТИ, ЗАГЛАВИЕ], stolche=_столчета(), shape_definitions=(),
    )
    assert ред["master_shape_code"] == "F001"
    assert верига.ПОЛЕ not in ред


def test_portata_iska_stolcheto_nanovo():
    """Записът не се вярва: друга форма, чуждо заглавие или несиметрия — отказ."""

    ред = _прекарай(_ред())
    assert validate_production_segment_evidence(ред, shape=_f008())

    друга_форма = {**ред, "master_shape_code": "F009"}
    assert not validate_production_segment_evidence(друга_форма, shape=_f008())

    чуждо = {**ред, верига.ПОЛЕ: {**ред[верига.ПОЛЕ], "stolche_caption_text": "стр.:"}}
    assert not validate_production_segment_evidence(чуждо, shape=_f008())
