from __future__ import annotations

from dataclasses import replace
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.review.decision_suggestions import (
    CONFLICT,
    READY,
    DecisionSuggestion,
    ProductionResolution,
    StrictDecisionContext,
    SuggestionProvenance,
)
from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _shape(code: str, name: str) -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=name,
        active=True,
        state="LOCKED",
        locked=True,
        revision=1,
        segments=[
            SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
            SegmentDefinition(index=2, length_code="L2", bend_after_deg=-90),
            SegmentDefinition(index=3, length_code="L3"),
        ],
    )


def _row() -> dict:
    return {
        "annotation_handle": "CURRENT",
        "annotation_text": "29N8x94",
        "annotation_count": 29,
        "diameter_mm": 8,
        "declared_length_cm": 94,
        "candidate_handles": ["G-CURRENT"],
        "candidate_layer": "ARM-N8",
        "candidate_basis": "DXF_CENTERLINE_AND_DIMENSIONS",
        "master_shape_code": "F003",
        "master_shape_name": "ДВА ВИНКЕЛА",
        "canonical_segment_lengths_cm": [20, 54, 20],
        "canonical_turn_angles_deg": [90, 90],
        "batch_status": "REVIEW",
        "matcher_disposition": "MATCHER_REVIEW",
        "review_reason_codes": ["SHAPE_REVIEW"],
    }


def _ready_suggestion() -> DecisionSuggestion:
    context = StrictDecisionContext((("case", "same"),), "context-sha")
    resolution = ProductionResolution(
        shape_code="F005",
        shape_name="ФИБА",
        shape_revision=1,
        count=29,
        diameter_mm=8,
        production_length_cm=94,
        segment_lengths_cm=(40, 14, 40),
        turn_angles_deg=(90, -90),
        action_class="CORRECTION",
        reason_code="WRONG_SHAPE",
        reason="Грешна MASTER форма",
        historical_note_present=True,
    )
    provenance = SuggestionProvenance(
        stable_event_id="EVENT-1",
        session_id="SESSION-ENDED",
        occurred_at="2026-09-06T12:00:00+03:00",
        project_name="R-7",
        source_sha256="source-sha",
        drawing_family_id="family-sha",
        source_generation_id="GEN-1",
        annotation_handle="OLD",
        engine_version="0.3.14",
        action_class="CORRECTION",
        reason_code="WRONG_SHAPE",
        delivery_sources=("PROJECT_OUTBOX_DELIVERED",),
    )
    return DecisionSuggestion(
        READY,
        "Еднозначно предишно решение.",
        context=context,
        proposal=resolution,
        provenance=(provenance,),
    )


def _dialog(suggestion: DecisionSuggestion) -> EngineerDecisionDialog:
    return EngineerDecisionDialog(
        row=_row(),
        shapes=[_shape("F003", "ДВА ВИНКЕЛА"), _shape("F005", "ФИБА")],
        decision_suggestion=suggestion,
        engine_version="0.3.14",
        project_name="R-7",
    )


def test_ready_suggestion_changes_nothing_before_explicit_button() -> None:
    _app()
    dialog = _dialog(_ready_suggestion())

    assert dialog.suggestion_frame.isHidden() is False
    assert dialog.use_suggestion_button.isHidden() is False
    assert dialog.shape_combo.currentData() == "F003"
    assert dialog.segments_edit.text() == "20 + 54 + 20"
    assert dialog.reason_combo.currentData() != "WRONG_SHAPE"
    assert dialog.note_edit.toPlainText() == ""
    assert dialog._available_handles == ["G-CURRENT"]
    dialog.close()


def test_explicit_accept_prefills_material_result_but_not_geometry_or_note() -> None:
    _app()
    dialog = _dialog(_ready_suggestion())
    dialog.use_suggestion_button.click()

    assert dialog.shape_combo.currentData() == "F005"
    assert dialog.segments_edit.text() == "40 + 14 + 40"
    assert dialog.reason_combo.currentData() == "WRONG_SHAPE"
    assert dialog.note_edit.toPlainText() == ""
    assert dialog._available_handles == ["G-CURRENT"]
    assert dialog.use_candidate_check.isChecked()

    dialog._accept()
    payload = dialog.result_payload
    assert payload is not None
    assert payload["geometry"]["handles"] == ["G-CURRENT"]
    assert payload["resolved"]["turn_angles_deg"] == [90, -90]
    assert payload["decision_suggestion"]["outcome"] == "ACCEPTED_UNCHANGED"
    assert payload["decision_suggestion"]["acceptance"]["source_event_ids"] == [
        "EVENT-1"
    ]


def test_edit_after_accept_is_explicit_in_audit() -> None:
    _app()
    dialog = _dialog(_ready_suggestion())
    dialog.use_suggestion_button.click()
    dialog.count_edit.setText("30")
    dialog._accept()

    assert dialog.result_payload is not None
    assert dialog.result_payload["resolved"]["count"] == 30
    assert (
        dialog.result_payload["decision_suggestion"]["outcome"]
        == "ACCEPTED_THEN_EDITED"
    )


def test_geometryless_suggestion_clears_current_geometry_and_requires_new_note(
) -> None:
    _app()
    ready = _ready_suggestion()
    assert ready.proposal is not None
    geometryless = replace(
        ready,
        proposal=replace(
            ready.proposal,
            reason_code="NO_GEOMETRY",
            reason="Няма доказана DXF геометрия",
            geometry_mode="NO_DXF_GEOMETRY",
            historical_note_present=True,
        ),
    )
    dialog = _dialog(geometryless)
    dialog.use_suggestion_button.click()

    assert dialog.use_candidate_check.isChecked() is False
    assert dialog.note_edit.toPlainText() == ""
    assert dialog.note_toggle_button.isChecked()
    assert dialog.note_edit.isHidden() is False
    assert dialog.save_button.isEnabled() is False
    dialog.use_candidate_check.setChecked(True)
    assert dialog.save_button.isEnabled() is False
    assert "NO_GEOMETRY" in dialog.status_label.text()
    dialog.use_candidate_check.setChecked(False)
    dialog.note_edit.setPlainText("Нова проверка за текущата позиция")
    assert dialog.confirm_check.isHidden() is False
    dialog.confirm_check.setChecked(True)
    assert dialog.save_button.isEnabled() is True

    dialog._accept()
    payload = dialog.result_payload
    assert payload is not None
    assert payload["geometry"]["handles"] == []
    assert payload["note"] == "Нова проверка за текущата позиция"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "Тази стара бележка не бива да се копира" not in serialized
    assert "prior_note" not in serialized


def test_conflicting_history_is_visible_and_cannot_be_applied() -> None:
    _app()
    suggestion = DecisionSuggestion(
        CONFLICT,
        "Открити са 2 различни решения. Нищо не се попълва.",
        context=StrictDecisionContext((("case", "same"),), "context-sha"),
    )
    dialog = _dialog(suggestion)

    assert dialog.suggestion_frame.isHidden() is False
    assert dialog.use_suggestion_button.isHidden()
    assert dialog.shape_combo.currentData() == "F003"
    assert "КОНФЛИКТ" in dialog.suggestion_label.text()
    dialog.close()


def test_selected_geometry_context_is_snapshotted_before_decision() -> None:
    _app()
    row = _row()
    row.update({
        "candidate_handles": ["SELECTED-1", "SELECTED-2"],
        "candidate_basis": "OWNER_SELECTED_DXF_GEOMETRY",
        "geometry_provenance": "OWNER_SELECTED_DXF_GEOMETRY",
        "data_quality_flags": ["MANUAL_SELECTION"],
    })
    dialog = EngineerDecisionDialog(
        row=row,
        shapes=[_shape("F003", "ДВА ВИНКЕЛА")],
        engine_version="0.3.14",
        project_name="R-7",
    )
    dialog._accept()

    assert dialog.result_payload is not None
    snapshot = dialog.result_payload["row_context"]
    assert snapshot["candidate_handles"] == ["SELECTED-1", "SELECTED-2"]
    assert snapshot["candidate_basis"] == "OWNER_SELECTED_DXF_GEOMETRY"
    assert snapshot["geometry_provenance"] == "OWNER_SELECTED_DXF_GEOMETRY"
    assert snapshot["data_quality_flags"] == ["MANUAL_SELECTION"]
    assert (
        dialog.result_payload["original"]["candidate_basis"]
        == "OWNER_SELECTED_DXF_GEOMETRY"
    )
    dialog.close()
