# -*- coding: utf-8 -*-
"""Прав прът, начертан с друга дължина: изнася се по надписа, в жълто.

Собственик, 08.11.2026 („Греди +6.50"): потвърди надписа при осем прави пръта,
начертани 5–100 cm различно — „да излиза просто в Excel с жълто, за проверка,
и отстрани точно какво му е". 09.11.2026: „за всички разлики (отклонения от
чертежа), като да има обосновка за това защо е в жълто".

Числата са на ``3N25x680`` (начертан 674,95) и ``29N12x1100`` (начертан 1000)
от същия чертеж.
"""
from __future__ import annotations

import pytest
from openpyxl import Workbook

from lion_rebar.dxf.batch_geometry_report import _apply_label_over_drawing
from lion_rebar.export import label_over_drawing as по_надписа
from lion_rebar.export.auto_gate import (
    is_deterministic_production_source,
    validate_production_segment_evidence,
)
from lion_rebar.export.excel_export import (
    DRAWING_DEVIATION_FILL,
    ExportGroup,
    ExportPosition,
    _mark_drawing_deviation,
)


def _ред(**промени):
    ред = {
        "annotation_handle": "8FDE::BJ-FI-L",
        "annotation_text": "3N25x680",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 3,
        "diameter_mm": 25.0,
        "declared_length_cm": 680.0,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "candidate_handles": ["8FD0"],
        "segment_count": 1,
        "effective_geometry_length_cm": 674.9519,
        "candidate_score_gap": 0.081,
        "candidate_distance_gap_raw": 55.4,
        "alternative_candidates": [{"handles": ["X1"]}],
        "diameter_layer_match": False,
        "layer_diameter_mm": None,
        "matcher_blocker_codes": [],
        "review_reason_codes": [],
        "data_quality_flags": [],
        "candidate_notes": [],
    }
    ред.update(промени)
    return ред


def test_pravat_prat_otiva_po_nadpisa_s_obosnovka():
    ред = _ред()
    assert _apply_label_over_drawing([ред]) == 1
    assert ред["production_segments_cm"] == [680.0]
    assert ред["production_segment_evidence"]["source"] == по_надписа.SOURCE
    отклонение = ред[по_надписа.FIELD]
    assert отклонение["drawn_cm"] == 675.0
    assert отклонение["difference_cm"] == -5.0
    assert "Начертан 675 cm, надписан 680 cm" in отклонение["note"]
    assert "5 cm по-къс" in отклонение["note"]
    assert validate_production_segment_evidence(ред)
    assert is_deterministic_production_source(ред)


def test_stoten_metar_po_dalag_sashto():
    ред = _ред(annotation_text="29N12x1100", declared_length_cm=1100.0,
               effective_geometry_length_cm=1000.0, batch_status="РИСК",
               matcher_blocker_codes=["LENGTH_MISMATCH"], diameter_layer_match=True,
               layer_diameter_mm=12.0)
    assert _apply_label_over_drawing([ред]) == 1
    assert ред[по_надписа.FIELD]["difference_cm"] == -100.0


def test_kota_za_pokritie_ne_spira():
    """Положителна контрола: „5" наоколо е покритие, не кота на пръта (3N25x680)."""

    ред = _ред(nearby_dimensions_cm=[60.0, 5.0, 555.0, 24.0, 30.0, 170.0])
    assert _apply_label_over_drawing([ред]) == 1


def test_bez_otklonenie_nyama_kakvo_da_se_pishe():
    """Положителна контрола: начертаното съвпада — правилото мълчи."""

    ред = _ред(effective_geometry_length_cm=680.2)
    assert _apply_label_over_drawing([ред]) == 0
    assert not ред.get("production_segments_cm")


@pytest.mark.parametrize("промяна", [
    {"global_conflict": True},
    {"master_ambiguous_shape_codes": ["F003"]},
    {"matcher_blocker_codes": ["GEOMETRY_OWNERSHIP_CONFLICT"]},
    {"matcher_blocker_codes": ["WEAK_CANDIDATE_SEPARATION"]},
    {"matcher_blocker_codes": ["LENGTH_MISMATCH", "FAR_ASSOCIATION"]},
    {"review_reason_codes": ["FAR_ASSOCIATION"]},
    {"data_quality_flags": ["COUNT_CONFLICT"]},
    {"master_shape_code": "F003"},
    {"segment_count": 3},
    {"candidate_handles": []},
    {"batch_status": "СИЛЕН"},
    {"diameter_layer_match": False, "layer_diameter_mm": 16.0},
    {"candidate_score_gap": -0.011, "candidate_distance_gap_raw": 229.7},
    {"candidate_score_gap": 0.01, "candidate_distance_gap_raw": 3.0},
    {"production_segments_cm": [675.0]},
    {"contextual_rule_code": "U_PROPORTION_F003"},
    {"schedule_witness": {"status": "SCHEDULE_CONTRADICTS"}},
    # Тест-002 „21N10x340/20": начертан 320, коти 320 и 20 — недочертана кука.
    {"declared_length_cm": 340.0, "effective_geometry_length_cm": 320.0,
     "nearby_dimensions_cm": [320.0, 440.0, 20.0, 140.5, 180.0]},
])
def test_vsyaka_druga_prichina_ostava_za_preglad(промяна):
    ред = _ред(**промяна)
    assert _apply_label_over_drawing([ред]) == 0
    assert по_надписа.FIELD not in ред


def test_portata_ne_vyarva_na_zapisa():
    ред = _ред()
    _apply_label_over_drawing([ред])
    assert validate_production_segment_evidence(ред)
    подправен = {**ред, "global_conflict": True}
    assert not validate_production_segment_evidence(подправен)
    друг_вектор = {**ред, "production_segments_cm": [675.0],
                   "production_segment_evidence": {**ред["production_segment_evidence"], "segments": [675.0]}}
    assert not validate_production_segment_evidence(друг_вектор)


def test_ruchno_potvardeniyat_prat_sashto_e_zhalt():
    """Връзка от инженерно решение — без записана обосновка, с геометрия."""

    връзка = {"master_shape_code": "F001", "geometry_handles": ["8FD0"],
              "production_length_cm": 680.0, "effective_geometry_length_cm": 674.95}
    assert "Начертан 675 cm, надписан 680 cm" in по_надписа.deviation_note(връзка)
    assert по_надписа.deviation_note({**връзка, "effective_geometry_length_cm": 680.1}) == ""
    assert по_надписа.deviation_note({**връзка, "master_shape_code": "F003"}) == ""
    assert по_надписа.deviation_note({**връзка, "geometry_handles": []}) == ""


def test_staro_reshenie_bez_nachertana_dalzhina():
    """Решение отпреди 09.11: начертаната е само сурова. Приема се само в cm."""

    старо = {"master_shape_code": "F001", "geometry_handles": ["8F7E"],
             "approved_by": "OWNER_ENGINEER_DECISION", "production_length_cm": 680.0,
             "declared_length_cm": 680.0, "effective_geometry_length_cm": 680.0,
             "geometry_length_raw": 674.9519, "raw_numeric_difference": 5.0481}
    assert "Начертан 675 cm, надписан 680 cm" in по_надписа.deviation_note(старо)
    # Чертеж в mm: суровата 6749,5 не е в cm — не се гадае.
    в_мм = {**старо, "geometry_length_raw": 6749.519}
    assert по_надписа.deviation_note(в_мм) == ""


def _позиция(бележка: str) -> ExportPosition:
    return ExportPosition(
        annotation_handle="8FDE::BJ-FI-L", annotation_text="3N25x680", count=3,
        diameter_mm=25.0, production_length_cm=680.0, shape_code="F001",
        shape_name="ПРАВ ПРЪТ", shape_revision=1, geometry_handles=("8FD0",),
        frame_index=0, drawing_deviation_note=бележка,
    )


def test_excel_redat_e_zhalt_s_belezhka():
    лист = Workbook().active
    бележка = по_надписа.obosnovka(675.0, 680.0)
    _mark_drawing_deviation(лист, 7, ExportGroup(key=("k",), positions=[_позиция(бележка)]))
    for колона in range(1, 12):
        assert лист.cell(7, колона).fill.fgColor.rgb.endswith(DRAWING_DEVIATION_FILL)
    assert бележка in лист.cell(7, 6).comment.text
    assert "3N25x680" in лист.cell(7, 6).comment.text


def test_excel_bez_otklonenie_ne_se_otsvetyava():
    лист = Workbook().active
    _mark_drawing_deviation(лист, 7, ExportGroup(key=("k",), positions=[_позиция("")]))
    assert лист.cell(7, 1).fill.fill_type is None
    assert лист.cell(7, 6).comment is None
