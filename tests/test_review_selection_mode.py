from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from lion_rebar.ui.review_center import ReviewCenterDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_report(path: Path, *, with_candidate: bool) -> None:
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "ANN1",
        "annotation_text": "11N8x130",
        "batch_status": "НЯМА КАНДИДАТ" if not with_candidate else "СИЛЕН",
        "candidate_handles": ["G1"] if with_candidate else [],
        "alternative_candidates": [],
        "diameter_mm": 8,
        "declared_length_cm": 130,
        "segment_count": 0 if not with_candidate else 1,
        "data_quality_flags": [],
        "data_quality_notes": [],
        "candidate_notes": [],
        "analysis_warnings": [],
    }
    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": "Test",
        "generated_at": "2026-07-25T00:00:00+03:00",
        "summary": {},
        "rows": [row],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_two_row_report(path: Path) -> None:
    rows = [
        {
            "annotation_class": "ACTIONABLE",
            "annotation_handle": "ANN1",
            "annotation_text": "58N16x1020",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "REVIEW",
            "candidate_handles": ["G1"],
            "alternative_candidates": [],
            "master_shape_code": "F001",
            "master_shape_name": "ПРАВ ПРЪТ",
            "diameter_mm": 16,
            "declared_length_cm": 1020,
            "segment_count": 1,
            "data_quality_flags": [],
            "data_quality_notes": [],
            "candidate_notes": [],
            "analysis_warnings": [],
        },
        {
            "annotation_class": "ACTIONABLE",
            "annotation_handle": "ANN2",
            "annotation_text": "8N10x790",
            "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
            "matcher_disposition": "REVIEW",
            "candidate_handles": ["G2", "G3"],
            "alternative_candidates": [],
            "master_shape_code": "F004",
            "master_shape_name": "ПРЪТ С ЕДИН ВИНКЕЛ",
            "diameter_mm": 10,
            "declared_length_cm": 790,
            "segment_count": 2,
            "data_quality_flags": [],
            "data_quality_notes": [],
            "candidate_notes": [],
            "analysis_warnings": [],
        },
    ]
    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": "Test",
        "generated_at": "2026-07-28T09:00:00+03:00",
        "summary": {},
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")



def _select_queue(dialog: ReviewCenterDialog, code: str) -> None:
    for index in range(dialog.queue_combo.count()):
        if dialog.queue_combo.itemData(index) == code:
            dialog.queue_combo.setCurrentIndex(index)
            assert dialog.current_row() is not None
            return
    raise AssertionError(f"Queue not found: {code}")


def _write_far_association_report(path: Path, *, with_reference_geometry: bool) -> None:
    row = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "ANN1",
        "annotation_text": "22N25x481",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": ["FAR_HANDLE"],
        "reference_geometry_handles": (
            ["PXG_OWN_0_0", "PXG_OWN_1_0"] if with_reference_geometry else []
        ),
        "review_reason_codes": ["FAR_ASSOCIATION"],
        "matcher_blocker_codes": ["NO_MASTER_MATCH", "FAR_ASSOCIATION"],
        "alternative_candidates": [],
        "diameter_mm": 25,
        "declared_length_cm": 481,
        "segment_count": 0,
        "data_quality_flags": [],
        "data_quality_notes": [],
        "candidate_notes": [],
        "analysis_warnings": [],
    }
    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": "Test",
        "generated_at": "2026-09-12T00:00:00+03:00",
        "summary": {},
        "rows": [row],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_far_association_preview_prefers_own_reference_geometry(tmp_path: Path) -> None:
    """Owner-confirmed 2026-09-12, verified against real data: a
    FAR_ASSOCIATION candidate never coincides with the annotation's own
    reference geometry, so when both exist the quick-preview default must
    show the (bluer, more trustworthy) reference geometry instead of the
    far candidate -- without touching candidate_handles or its
    availability via "Всички кандидати"."""

    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    _write_far_association_report(report, with_reference_geometry=True)
    dialog = ReviewCenterDialog(report, state_path=state)
    preview_emitted: list[dict] = []
    reference_emitted: list[dict] = []
    dialog.previewRequested.connect(lambda row: preview_emitted.append(dict(row)))
    dialog.referenceGeometryRequested.connect(lambda row: reference_emitted.append(dict(row)))
    _select_queue(dialog, "ALL")

    dialog._preview_current()

    assert reference_emitted, "expected referenceGeometryRequested, not the far candidate"
    assert not preview_emitted
    assert reference_emitted[0]["candidate_handles"] == ["FAR_HANDLE"]
    assert "Далечният кандидат" in dialog.result_label.text()
    # candidates_button must stay enabled -- the far candidate is still
    # reachable via "Всички кандидати", never removed by this preference.
    assert dialog.candidates_button.isEnabled()
    dialog.close()


def test_far_association_without_reference_geometry_still_shows_far_candidate(
    tmp_path: Path,
) -> None:
    """No annotation-owned geometry exists for this row -- falls back to
    the pre-existing behaviour (show the far candidate, it's all there is)."""

    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    _write_far_association_report(report, with_reference_geometry=False)
    dialog = ReviewCenterDialog(report, state_path=state)
    preview_emitted: list[dict] = []
    reference_emitted: list[dict] = []
    dialog.previewRequested.connect(lambda row: preview_emitted.append(dict(row)))
    dialog.referenceGeometryRequested.connect(lambda row: reference_emitted.append(dict(row)))
    _select_queue(dialog, "ALL")

    dialog._preview_current()

    assert preview_emitted
    assert not reference_emitted
    dialog.close()


def test_no_candidate_preview_enters_selection_without_transparent_window(tmp_path: Path) -> None:
    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    _write_report(report, with_candidate=False)
    dialog = ReviewCenterDialog(report, state_path=state)
    emitted: list[dict] = []
    dialog.locateAnnotationRequested.connect(lambda row: emitted.append(dict(row)))
    # Фаза 2: "NONE" (без кандидат) вече е обединена в "PROBLEMS" опашката.
    _select_queue(dialog, "PROBLEMS")

    assert dialog.preview_button.text() == "Покажи надписа и избери"
    dialog._preview_current()

    assert len(emitted) == 1
    assert emitted[0]["annotation_handle"] == "ANN1"
    assert dialog.windowOpacity() == 1.0
    assert "режим за избор" in dialog.result_label.text().lower()
    dialog.close()


def test_candidate_preview_keeps_existing_visual_fade(tmp_path: Path) -> None:
    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    _write_report(report, with_candidate=True)
    dialog = ReviewCenterDialog(report, state_path=state)
    emitted: list[dict] = []
    dialog.previewRequested.connect(lambda row: emitted.append(dict(row)))
    _select_queue(dialog, "ALL")

    assert dialog.preview_button.text() == "1. Покажи"
    dialog._preview_current()

    assert len(emitted) == 1
    assert dialog.windowOpacity() < 1.0
    dialog.close()


def test_details_follow_new_row_when_approval_reuses_table_index_zero(tmp_path: Path) -> None:
    _app()
    report = tmp_path / "report.json"
    state = tmp_path / "state.json"
    _write_two_row_report(report)
    dialog = ReviewCenterDialog(report, state_path=state)
    _select_queue(dialog, "WORK")

    assert dialog.current_row()["annotation_handle"] == "ANN1"
    assert "58N16x1020" in dialog.details.toPlainText()
    assert "F001" in dialog.details.toPlainText()

    dialog.mark_approved("ANN1")

    details = dialog.details.toPlainText()
    assert dialog.current_row()["annotation_handle"] == "ANN2"
    assert "8N10x790" in details
    assert "F004" in details
    assert "58N16x1020" not in details
    assert "F001" not in details
    dialog.close()
