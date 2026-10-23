# -*- coding: utf-8 -*-
"""Начертаната ведомост като втори свидетел и източник на правата форма.

Решение на собственика, 22.10.2026 (вариант „Б“): четецът на начертаната
ведомост (``dxf/drawn_schedule.py``) се закача към пътя.

* до всяка позиция се записва какво казва ведомостта; противоречие между нея и
  изнасяния вектор спира автоматичното одобрение;
* когато чертежът няма прът за надписа, редът във ведомостта е еднозначен и
  размерите му са потвърдени, ведомостта дава ПРАВА форма.

Измерено на ``h01-1001`` преди промяната: 94 от 152 минават портата, нито една
одобрена не противоречи на ведомостта, а ``4N8x1550`` стои спряна с
``MISSING_GEOMETRY`` и ``MISSING_ACTIVE_MASTER``, макар ведомостта да казва
„прав, 1550“.

Тук стоят и отказите: прът в чертежа, два реда, непотвърдени размери, повече от
един размер, размер ≠ надписаната дължина, подправено доказателство.
"""
from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import _apply_drawn_schedule_witness
from lion_rebar.dxf.drawn_schedule import DrawnSchedule, ScheduleRow
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.export.auto_gate import (
    is_deterministic_production_source,
    validate_production_segment_evidence,
)
from lion_rebar.export.batch_auto_output import build_batch_export_context
from lion_rebar.export.schedule_witness import (
    CONTRADICTS,
    GEOMETRY_PROVENANCE,
    STRAIGHT_SOURCE,
    WITNESS_FIELD,
)


def _ред_вед(марка: str, диаметър: float, дължина: float, размери=(), статус="ПОТВЪРДЕНИ"):
    return ScheduleRow(
        mark=марка, pcs=10, diameter_mm=диаметър, single_length_cm=дължина,
        total_length_cm=10 * дължина, mass_kg=1.0,
        shape_dimensions=tuple(размери), dimensions_status=статус,
        sample_handles=("S-" + марка,),
    )


def _ведомост(*редове: ScheduleRow) -> DrawnSchedule:
    return DrawnSchedule(
        schema="test", reader_version="1.0", layer="s-kota-sec",
        rows=tuple(редове), refusals=(),
    )


def _без_прът(handle="T1", текст="4N8x1550", диаметър=8.0, дължина=1550.0) -> dict:
    """Надпис, до който чертежът няма прът — както ``4N8x1550`` на h01-1001."""

    return {
        "annotation_handle": handle,
        "annotation_text": текст,
        "annotation_class": "ACTIONABLE",
        "annotation_count": 4,
        "diameter_mm": диаметър,
        "declared_length_cm": дължина,
        "batch_status": "НЯМА КАНДИДАТ",
        "matcher_disposition": "MANUAL",
        "global_conflict": False,
        "data_quality_flags": [],
        "quantity_review_required": False,
        "master_shape_code": "",
        "candidate_handles": [],
        "candidate_notes": [],
        "review_reason_codes": [],
    }


# ==========================================================================
# АНАЛИЗЪТ: свидетелят и правата форма
# ==========================================================================


def test_bez_prat_vedomostta_dava_prava_forma():
    ред = _без_прът()

    _apply_drawn_schedule_witness([ред], _ведомост(_ред_вед("7", 8.0, 1550.0, (1550.0,))))

    assert ред[WITNESS_FIELD]["status"] == "ЕДИН РЕД"
    assert ред[WITNESS_FIELD]["mark"] == "7"
    assert ред["master_shape_code"] == "F001"
    assert ред["production_segments_cm"] == [1550.0]
    assert ред["production_segment_evidence"]["source"] == STRAIGHT_SOURCE
    assert ред["geometry_provenance"] == GEOMETRY_PROVENANCE
    # Портата го доказва наново, САМО от реда.
    assert validate_production_segment_evidence(ред)
    assert is_deterministic_production_source(ред)


def test_prat_v_chertezha_ne_se_zamenya_ot_vedomostta():
    ред = _без_прът()
    ред["candidate_handles"] = ["G1"]
    ред["master_shape_code"] = "F004"

    _apply_drawn_schedule_witness([ред], _ведомост(_ред_вед("7", 8.0, 1550.0, (1550.0,))))

    assert ред["master_shape_code"] == "F004"
    assert "production_segment_evidence" not in ред
    # Свидетелят пак е записан — той е за всички позиции.
    assert ред[WITNESS_FIELD]["status"] == "ЕДИН РЕД"


def test_dva_reda_sas_sashtiya_d_i_l_sa_otkaz():
    ред = _без_прът()

    _apply_drawn_schedule_witness([ред], _ведомост(
        _ред_вед("7", 8.0, 1550.0, (1550.0,)),
        _ред_вед("9", 8.0, 1550.0, (1550.0,)),
    ))

    assert ред[WITNESS_FIELD]["status"] == "НЕЕДНОЗНАЧНО"
    assert ред["master_shape_code"] == ""
    assert "production_segment_evidence" not in ред


def test_nyama_red_vav_vedomostta():
    ред = _без_прът()

    _apply_drawn_schedule_witness([ред], _ведомост(_ред_вед("7", 10.0, 1550.0, (1550.0,))))

    assert ред[WITNESS_FIELD]["status"] == "НЯМА РЕД"
    assert ред["master_shape_code"] == ""


def test_nepotvardeni_razmeri_ne_davat_forma():
    ред = _без_прът()

    _apply_drawn_schedule_witness([ред], _ведомост(
        _ред_вед("7", 8.0, 1550.0, (), статус="НЯМА ЗАТВАРЯЩ НАБОР")))

    assert ред["master_shape_code"] == ""


def test_dva_razmera_ne_sa_prava_forma():
    """27 + 150 = 177: огънат прът. Вариант „Б“ дава САМО права форма."""

    ред = _без_прът(текст="2N12x177", диаметър=12.0, дължина=177.0)

    _apply_drawn_schedule_witness([ред], _ведомост(_ред_вед("3", 12.0, 177.0, (27.0, 150.0))))

    assert ред["master_shape_code"] == ""
    assert "production_segment_evidence" not in ред


def test_podpraveno_dokazatelstvo_ne_minava_portata():
    ред = _без_прът()
    _apply_drawn_schedule_witness([ред], _ведомост(_ред_вед("7", 8.0, 1550.0, (1550.0,))))

    ред["production_segment_evidence"]["schedule_mark"] = "99"

    assert not validate_production_segment_evidence(ред)
    assert not is_deterministic_production_source(ред)


# ==========================================================================
# ПОРТАТА: през авторитетния зареждач на експорта
# ==========================================================================


def _engine() -> EnginePackage:
    return EnginePackage(
        engine_id="test-engine", title="TEST", version="0.3.12", production_ready=True,
        shapes=[
            ShapeDefinition(code="F001", name="ПРАВ ПРЪТ", state="LOCKED", locked=True,
                            segments=[SegmentDefinition(index=1, length_code="L1")]),
            ShapeDefinition(code="F004", name="ПРЪТ С ЕДИН ВИНКЕЛ", state="LOCKED", locked=True,
                            segments=[SegmentDefinition(index=1, length_code="L1"),
                                      SegmentDefinition(index=2, length_code="L2")]),
        ],
    )


def _обекти() -> list[DxfEntityRecord]:
    return [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "0", {"insert": [100, 0, 0]}, "АРМИРОВКА ГРЕДИ"),
        DxfEntityRecord("BEAM", "TEXT", "0", {"insert": [100, 750, 0]}, "Гр. B.1 25/50"),
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "4N8x1550"),
        DxfEntityRecord("T2", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [800, 500, 0]}, "2N8x275"),
        DxfEntityRecord("G3", "LINE", "Arm - N8", {"start": [800, 500, 0], "end": [1050, 500, 0]}),
        DxfEntityRecord("G4", "LINE", "Arm - N8", {"start": [1050, 500, 0], "end": [1050, 525, 0]}),
    ]


def _огънат(handle="T2", текст="2N8x275") -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": текст,
        "annotation_class": "ACTIONABLE",
        "annotation_count": 2,
        "diameter_mm": 8.0,
        "declared_length_cm": 275.0,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "CONTROL",
        "global_conflict": False,
        "data_quality_flags": [],
        "quantity_review_required": False,
        "master_shape_code": "F004",
        "candidate_handles": ["G3", "G4"],
        "candidate_notes": [],
        "canonical_segment_lengths_cm": [250.0, 25.0],
        "production_segments_cm": [250.0, 25.0],
    }


def _отчет(tmp_path: Path, редове: list[dict]) -> Path:
    път = tmp_path / "batch.json"
    път.write_text(json.dumps({"cancelled": False, "rows": редове}, ensure_ascii=False),
                   encoding="utf-8")
    return път


def test_pravata_forma_ot_vedomostta_minava_portata(tmp_path: Path):
    ред = _без_прът()
    _apply_drawn_schedule_witness([ред], _ведомост(_ред_вед("7", 8.0, 1550.0, (1550.0,))))

    контекст = build_batch_export_context(
        _отчет(tmp_path, [ред, _огънат()]), entities=_обекти(), engine=_engine())

    позиции = {п.annotation_handle: п for п in контекст.positions}
    assert "T1" in позиции, [б.reason_codes for б in контекст.blockers]
    assert позиции["T1"].shape_code == "F001"
    assert tuple(позиции["T1"].segment_lengths_cm) == (1550.0,)
    assert tuple(позиции["T1"].geometry_handles) == ()


def test_bez_vedomost_sashtiyat_red_ostava_spryan(tmp_path: Path):
    """Положителна контрола: без свидетеля портата спира реда както преди."""

    контекст = build_batch_export_context(
        _отчет(tmp_path, [_без_прът(), _огънат()]), entities=_обекти(), engine=_engine())

    блокери = {б.annotation_handle: б for б in контекст.blockers}
    assert "MISSING_GEOMETRY" in блокери["T1"].reason_codes


def test_protivorechie_s_vedomostta_spira(tmp_path: Path):
    """Чертежът дава 250 + 25, ведомостта — 255 + 20: спира, не избира."""

    огънат = _огънат()
    _apply_drawn_schedule_witness([огънат], _ведомост(_ред_вед("5", 8.0, 275.0, (255.0, 20.0))))
    чист = _без_прът()
    _apply_drawn_schedule_witness([чист], _ведомост(_ред_вед("7", 8.0, 1550.0, (1550.0,))))

    контекст = build_batch_export_context(
        _отчет(tmp_path, [огънат, чист]), entities=_обекти(), engine=_engine())

    блокери = {б.annotation_handle: б for б in контекст.blockers}
    assert CONTRADICTS in блокери["T2"].reason_codes
    assert "255" in блокери["T2"].reason_text


def test_saglasnata_vedomost_ne_spira(tmp_path: Path):
    огънат = _огънат()
    _apply_drawn_schedule_witness([огънат], _ведомост(_ред_вед("5", 8.0, 275.0, (25.0, 250.0))))

    контекст = build_batch_export_context(
        _отчет(tmp_path, [огънат]), entities=_обекти(), engine=_engine())

    assert [п.annotation_handle for п in контекст.positions] == ["T2"]
