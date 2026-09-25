"""Завоите на редукцията идват от чертежа, а не от наум сложени 90° (rc44).

Собственикът, 2026-09-25, експортирайки „т-1":

    Excel + DXF export не завърши.
    F002 262B::BJ-FI-L reduction turns must have opposite signs: 90° / 90°.

Пътят е ДРУГ, не proxy-ят от 13.09 (``test_proxy_promotion_turn_angles``):
позицията е блоков надпис, а производствените ѝ сегменти са изведени от
отпечатаните коти A-E (``MATCHER_LOCAL_A_E_CANONICAL``). Там редът няма
``canonical_turn_angles_deg`` и ``batch_auto_output`` слагаше
``[90.0] * (брой сегменти - 1)``.

Измереното е по-лошо от формален отказ. При 90/90 заключеният MASTER смята
``DY2 = 40 cm`` — прът, който стъпва настрани с цялата дължина на редукцията,
тоест Z, не редукция. Десет позиции в чертежа (530 пръта) носеха същото.

Чертежът обаче знае отговора: началната и крайната права са успоредни и
перпендикулярното разстояние между тях е 4.0061 cm при надписана редукция 40.
Собственик, 2026-09-25, на въпроса кое число да влезе в режещия лист: „4".
Това е същото правило като на 13.09 — ъгълът се извежда от ОТМЕСТВАНЕТО и
НАДПИСАНАТА редукция, за да излезе ``DY2`` на кръглото число на проектанта.
"""
from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.batch_geometry_report import _f002_reduction_turn_angles_deg
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.auto_gate import validate_production_segment_evidence
from lion_rebar.export.excel_export import (
    ExportPosition,
    _validate_f002_turn_consistency,
)
from lion_rebar.export.master_engine_v031 import parameters_from_segments

#: Реалната геометрия на „162N22x495" от чертежа на собственика, преместена в
#: началото на координатната система: три свързани LINE обекта, начертани
#: 135 / 40.5742 / 319.6273 при надписани 135 / 40 / 320.
_A = (0.0, 0.0)
_B = (0.0, -135.0001)
_C = (4.0372, -175.3729)
_D = (4.0061, -495.0002)

DECLARED_SEGMENTS = (135.0, 40.0, 320.0)
DECLARED_TOTAL = 495.0


def _line(handle: str, start: tuple[float, float], end: tuple[float, float]) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle,
        "LINE",
        "ARM-GORNA",
        {"start": [start[0], start[1], 0.0], "end": [end[0], end[1], 0.0]},
    )


def _entity_map() -> dict[str, DxfEntityRecord]:
    records = (
        _line("A1", _A, _B),
        _line("A2", _B, _C),
        _line("A3", _C, _D),
    )
    return {record.handle: record for record in records}


def _row(**overrides: object) -> dict:
    row = {
        "annotation_handle": "262B::BJ-FI-L",
        "annotation_text": "162N22x495",
        "master_shape_code": "F002",
        "candidate_handles": ["A1", "A2", "A3"],
        "canonical_segment_lengths_cm": list(DECLARED_SEGMENTS),
        "canonical_turn_angles_deg": [],
        "declared_length_cm": DECLARED_TOTAL,
        "production_length_cm": DECLARED_TOTAL,
        "production_segments_cm": list(DECLARED_SEGMENTS),
        # Пълната верига на притежание, която портата иска: сегментите са
        # изведени от две отпечатани коти A-E, които затварят точно.
        "dimension_evidence_applied": True,
        "dimension_resolution_strategy": "A_E_TOTAL_MINUS_PRINTED",
        "dimension_text_handles": ["D1", "D2"],
        "dimension_total_difference_cm": 0.0,
        "production_segment_evidence": {
            "shape_code": "F002",
            "segments": list(DECLARED_SEGMENTS),
            "turn_angles_deg": [],
            "source": "MATCHER_LOCAL_A_E_CANONICAL",
            "candidate_handles": ["A1", "A2", "A3"],
            "dimension_handles": ["D1", "D2"],
            "dimension_measurements_cm": [
                {"handle": "D1", "measurement_cm": 135.0},
                {"handle": "D2", "measurement_cm": 320.0},
            ],
            "dimension_resolution_strategy": "A_E_TOTAL_MINUS_PRINTED",
            "dimension_total_difference_cm": 0.0,
        },
    }
    row.update(overrides)
    return row


class TestTurnsComeFromTheDrawing:
    def test_the_two_turns_are_opposite_and_equal(self) -> None:
        turns = _f002_reduction_turn_angles_deg(
            _row(), DECLARED_SEGMENTS, entity_map=_entity_map()
        )

        assert turns is not None
        assert len(turns) == 2
        assert turns[0] * turns[1] < 0
        assert abs(abs(turns[0]) - abs(turns[1])) <= 0.2

    def test_the_offset_lands_on_the_designers_round_number(self) -> None:
        """Начертаното отместване е 4.0061; проектантът е чертал 4."""

        turns = _f002_reduction_turn_angles_deg(
            _row(), DECLARED_SEGMENTS, entity_map=_entity_map()
        )

        assert turns is not None
        assert 40.0 * math.sin(math.radians(abs(turns[0]))) == pytest.approx(
            4.0, abs=1e-9
        )

    def test_the_engine_gets_the_reduction_not_a_z_bar(self) -> None:
        """С 90/90 същият Engine смята DY2 = 40 cm. Това е дефектът."""

        turns = _f002_reduction_turn_angles_deg(
            _row(), DECLARED_SEGMENTS, entity_map=_entity_map()
        )
        assert turns is not None

        proven = parameters_from_segments(
            "F002",
            DECLARED_SEGMENTS,
            production_length_cm=DECLARED_TOTAL,
            turn_angles_deg=turns,
        )
        invented = parameters_from_segments(
            "F002",
            DECLARED_SEGMENTS,
            production_length_cm=DECLARED_TOTAL,
            turn_angles_deg=(90.0, 90.0),
        )

        assert proven.values["DY2"] == pytest.approx(4.0, abs=1e-9)
        assert invented.values["DY2"] == pytest.approx(40.0, abs=1e-9)

    def test_the_exported_angles_satisfy_the_excel_validator(self) -> None:
        turns = _f002_reduction_turn_angles_deg(
            _row(), DECLARED_SEGMENTS, entity_map=_entity_map()
        )
        assert turns is not None

        position = ExportPosition(
            annotation_handle="262B::BJ-FI-L",
            annotation_text="162N22x495",
            count=162,
            diameter_mm=22.0,
            production_length_cm=DECLARED_TOTAL,
            shape_code="F002",
            shape_name="ПРАВ ПРЪТ С РЕДУКЦИЯ",
            shape_revision=1,
            geometry_handles=("A1", "A2", "A3"),
            frame_index=1,
            segment_lengths_cm=DECLARED_SEGMENTS,
            turn_angles_deg=tuple(turns),
        )

        _validate_f002_turn_consistency(position)


class TestNothingIsInvented:
    def test_a_bar_that_does_not_crank_is_refused(self) -> None:
        """Двата завоя в една и съща посока не са редукция — това е Z/U."""

        entity_map = {
            "A1": _line("A1", (0.0, 0.0), (0.0, -135.0)),
            "A2": _line("A2", (0.0, -135.0), (40.0, -135.0)),
            "A3": _line("A3", (40.0, -135.0), (40.0, -455.0)),
        }

        assert (
            _f002_reduction_turn_angles_deg(
                _row(), DECLARED_SEGMENTS, entity_map=entity_map
            )
            is None
        )

    def test_missing_geometry_gives_no_turns(self) -> None:
        assert (
            _f002_reduction_turn_angles_deg(_row(), DECLARED_SEGMENTS, entity_map={})
            is None
        )

    def test_another_shape_is_not_touched(self) -> None:
        assert (
            _f002_reduction_turn_angles_deg(
                _row(master_shape_code="F003"),
                DECLARED_SEGMENTS,
                entity_map=_entity_map(),
            )
            is None
        )


class TestTheGateNoLongerApprovesInventedTurns:
    def test_f002_without_proven_turns_is_not_auto_approved(self) -> None:
        """Портата приемаше реда, защото сама си слагаше 90/90 наум."""

        assert validate_production_segment_evidence(_row()) is False

    def test_f002_with_proven_turns_is_approved(self) -> None:
        turns = _f002_reduction_turn_angles_deg(
            _row(), DECLARED_SEGMENTS, entity_map=_entity_map()
        )
        assert turns is not None
        evidence = dict(_row()["production_segment_evidence"])
        evidence["turn_angles_deg"] = list(turns)

        assert (
            validate_production_segment_evidence(
                _row(
                    canonical_turn_angles_deg=list(turns),
                    production_segment_evidence=evidence,
                )
            )
            is True
        )
