from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.manual_assignment import ManualGeometrySummary
from lion_rebar.manual_dimensions import DIMENSION_SOURCE_ANNOTATION, DIMENSION_SOURCE_MANUAL
from lion_rebar.ui.manual_form_assignment_dialog import (
    SELECT_SHAPE_CODE,
    ManualFormAssignmentDialog,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _summary(
    lengths: tuple[float, ...],
    turns: tuple[float, ...],
    *,
    physical_connected: bool = True,
    training_eligible: bool = True,
) -> ManualGeometrySummary:
    return ManualGeometrySummary(
        handles=tuple(f"G{index}" for index in range(len(lengths))),
        entity_types=("LINE",),
        layers=("ARM_Bars",),
        points=(),
        closed=False,
        connected=True,
        branched=False,
        segment_count=len(lengths),
        segment_lengths_cm=lengths,
        turn_angles_deg=turns,
        total_length_cm=sum(lengths),
        canonical_signature="test",
        physical_connected=physical_connected,
        near_gap_healed=not physical_connected,
        near_gap_bridge_gap_raw=1.40588 if not physical_connected else None,
        near_gap_bridge_handles=("A29", "9CF") if not physical_connected else (),
        training_eligible=training_eligible,
    )


def test_annotation_source_does_not_replace_1150_with_1144_79():
    _app()
    dialog = ManualFormAssignmentDialog(
        row={
            "annotation_handle": "A1",
            "annotation_text": "3N16x1150",
            "annotation_count": 3,
            "diameter_mm": 16,
            "declared_length_cm": 1150,
            "master_shape_code": "F001",
        },
        summary=_summary((1120.0, 24.79), (90.0,)),
        shapes=[ShapeDefinition(code="F004", name="ПРЪТ С ЕДИН ВИНКЕЛ", state="APPROVED")],
    )
    dialog.shape_combo.setCurrentIndex(dialog.shape_combo.findData("F004"))
    dialog.dimension_source_combo.setCurrentIndex(
        dialog.dimension_source_combo.findData(DIMENSION_SOURCE_ANNOTATION)
    )
    dialog.confirm_check.setChecked(True)
    dialog._accept_assignment()

    assert dialog.result_payload is not None
    assert dialog.result_payload["dimensions"]["production_length_cm"] == 1150
    assert dialog.result_payload["dimensions"]["measured_geometry_length_cm"] == pytest.approx(1144.79)
    dialog.close()


def test_manual_f005_segments_are_saved_as_61_18_61():
    _app()
    dialog = ManualFormAssignmentDialog(
        row={
            "annotation_handle": "A2",
            "annotation_text": "400N10x140",
            "annotation_count": 400,
            "diameter_mm": 10,
            "declared_length_cm": 140,
            "master_shape_code": "F003",
        },
        summary=_summary((66.0, 18.0, 66.0), (-90.0, -90.0)),
        shapes=[
            ShapeDefinition(code="F003", name="ПРЪТ С ДВА ВИНКЕЛА", state="APPROVED"),
            ShapeDefinition(code="F005", name="ФИБА", state="APPROVED"),
        ],
    )
    dialog.shape_combo.setCurrentIndex(dialog.shape_combo.findData("F005"))
    dialog.dimension_source_combo.setCurrentIndex(
        dialog.dimension_source_combo.findData(DIMENSION_SOURCE_MANUAL)
    )
    dialog.manual_lengths_edit.setText("61 + 18 + 61")
    dialog.confirm_check.setChecked(True)
    dialog._accept_assignment()

    assert dialog.result_payload is not None
    assert dialog.result_payload["shape_code"] == "F005"
    assert dialog.result_payload["dimensions"]["production_length_cm"] == 140
    assert dialog.result_payload["dimensions"]["production_segment_lengths_cm"] == (61.0, 18.0, 61.0)
    dialog.close()


def test_ambiguous_master_requires_explicit_shape_choice():
    _app()
    dialog = ManualFormAssignmentDialog(
        row={
            "annotation_handle": "A3",
            "annotation_text": "8N10x405",
            "annotation_count": 8,
            "diameter_mm": 10,
            "declared_length_cm": 405,
            "master_shape_code": "F015",
            "master_shape_name": "СТЪЛБИЩНО РАМО 4",
            "master_conflict": True,
            "master_ambiguous_shape_codes": ["F015", "F014"],
        },
        summary=_summary((50.0, 229.0, 111.0, 15.0), (29.0, -29.0, -90.0)),
        shapes=[
            ShapeDefinition(code="F014", name="СТЪЛБИЩНО РАМО 3", state="APPROVED"),
            ShapeDefinition(code="F015", name="СТЪЛБИЩНО РАМО 4", state="APPROVED"),
        ],
    )

    assert dialog.shape_combo.currentData() == SELECT_SHAPE_CODE
    dialog.confirm_check.setChecked(True)
    assert dialog.save_button.isEnabled() is False

    dialog.shape_combo.setCurrentIndex(dialog.shape_combo.findData("F014"))
    assert dialog.save_button.isEnabled() is True
    dialog._accept_assignment()

    assert dialog.result_payload is not None
    assert dialog.result_payload["shape_code"] == "F014"
    dialog.close()


def test_gap_healed_geometry_cannot_be_saved_as_training_prototype():
    _app()
    dialog = ManualFormAssignmentDialog(
        row={
            "annotation_handle": "A1C",
            "annotation_text": "8N12x483",
            "annotation_count": 8,
            "diameter_mm": 12,
            "declared_length_cm": 483,
            "master_conflict": True,
            "master_ambiguous_shape_codes": ["F014", "F015"],
        },
        summary=_summary(
            (50.0, 286.0, 133.840392, 12.0),
            (29.0, -29.0, -90.0),
            physical_connected=False,
            training_eligible=False,
        ),
        shapes=[
            ShapeDefinition(code="F014", name="СТЪЛБИЩНО РАМО 3", state="APPROVED"),
            ShapeDefinition(code="F015", name="СТЪЛБИЩНО РАМО 4", state="APPROVED"),
        ],
    )

    assert dialog.training_check.isChecked() is False
    assert dialog.training_check.isEnabled() is False
    dialog.close()
