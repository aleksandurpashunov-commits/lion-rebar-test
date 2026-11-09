"""Груповото действие пренася решението на представителя.

Собственик, 08.11.2026 („Греди +6.50", шестте фиби ``32N8x94``): „досадно е
да цъкам 10 вместо 1 път". Фибите са F005 без участъци в отчета. Груповото
действие четеше само реда, затова след потвърждение отказваше с „Огъната
форма е без производствени сегменти" — три опита, нито една промяна — и
собственикът ги реши една по една с ``40 + 14 + 40``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.apply_to_group import group_changes_for, load_group_operations
from lion_rebar.ui.review_center import ReviewCenterDialog

PROJECT_ID = "PID-1"
SOURCE_GENERATION = "GEN-1"
SOURCE_SHA = "a" * 64
ФИБА = [40.0, 14.0, 40.0]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _фиба(handle: str, геометрия: str) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": "32N8x94",
        "annotation_count": 32,
        "diameter_mm": 8,
        "declared_length_cm": 94,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": [геометрия],
        "alternative_candidates": [],
        "master_shape_code": "F005",
        "master_shape_name": "ФИБА",
        "matcher_blocker_codes": ["SCHEMATIC_SCALE", "WEAK_CANDIDATE_SEPARATION"],
        "review_reason_codes": ["SCHEMATIC_SCALE"],
        "data_quality_flags": [],
        "data_quality_notes": [],
        "candidate_notes": [],
        "analysis_warnings": [],
        "project_id": PROJECT_ID,
        "source_generation_id": SOURCE_GENERATION,
        "source_sha256": SOURCE_SHA,
        "analysis_run_id": "RUN-1",
    }


def _решение() -> dict:
    return {
        "decision": "ENGINEER_RESOLVED",
        "action_class": "CORRECTION",
        "shape_code": "F005",
        "count": 32,
        "diameter_mm": 8.0,
        "production_length_cm": 94.0,
        "production_segment_lengths_cm": list(ФИБА),
        "reason_code": "CONFIRM_CORRECT",
    }


def _диалог(tmp_path: Path, rows: list[dict], decisions: dict | None = None) -> ReviewCenterDialog:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": "Test",
        "generated_at": "2026-11-08T00:00:00+02:00",
        "summary": {},
        "data_identity": {
            "project_id": PROJECT_ID,
            "source_generation_id": SOURCE_GENERATION,
            "source_sha256": SOURCE_SHA,
            "analysis_run_id": "RUN-1",
        },
        "rows": rows,
    }, ensure_ascii=False), encoding="utf-8")
    state = tmp_path / "review_center_state.json"
    dialog = ReviewCenterDialog(report, state_path=state, project_root=tmp_path,
                                operator_name="Тестов инженер")
    if decisions:
        dialog._state.setdefault("decisions", {}).update(decisions)
    return dialog


def _избери(dialog: ReviewCenterDialog, handle: str) -> None:
    index = next(i for i, row in enumerate(dialog._visible_rows)
                 if str(row.get("annotation_handle")) == handle)
    dialog.table.selectRow(index)


def test_reshenieto_e_iztochnikat_kogato_go_ima():
    ред = _фиба("REP", "G1")
    assert group_changes_for(ред, _решение()) == {
        "master_shape_code": "F005",
        "production_segments_cm": ФИБА,
        "production_length_cm": 94.0,
    }


def test_bez_reshenie_ostava_redat():
    ред = {**_фиба("REP", "G1"), "production_segments_cm": [40, 14, 40]}
    assert group_changes_for(ред, None) == {
        "master_shape_code": "F005", "production_segments_cm": [40, 14, 40]}
    # Изключване не е инженерно решение за форма — не се пренася като такова.
    assert group_changes_for(ред, {"decision": "NON_PRODUCTION_POSITION"})[
        "master_shape_code"] == "F005"


def test_bez_uchastatsi_preduprezhdava_predi_dialoga(tmp_path: Path, monkeypatch):
    """Положителна контрола: без решение огънатата форма не стига до диалога."""

    _app()
    dialog = _диалог(tmp_path, [_фиба("REP", "G1"), _фиба("SIB", "G2")])
    try:
        предупреждения: list[str] = []
        monkeypatch.setattr(QMessageBox, "warning",
                            lambda *a, **k: предупреждения.append(a[1]) or QMessageBox.StandardButton.Ok)
        отворен: list = []
        monkeypatch.setattr("lion_rebar.ui.apply_to_group_dialog.ApplyToGroupDialog",
                            lambda *a, **k: отворен.append(a))
        _избери(dialog, "REP")
        dialog._apply_to_group_current()
        assert предупреждения == ["Няма участъци"]
        assert not отворен
    finally:
        dialog.close()


def test_resheniyata_na_predstavitelya_stigat_do_grupata(tmp_path: Path, monkeypatch):
    """Целият път: реален диалог, реален запис — членът получава 40 + 14 + 40."""

    _app()
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    # Модалното предупреждение би спряло теста завинаги; тук то е провал.
    предупреждения: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: предупреждения.append(a[1]) or QMessageBox.StandardButton.Ok)
    rows = [_фиба("REP", "G1"), _фиба("SIB", "G2")]
    dialog = _диалог(tmp_path, rows, {"REP": _решение()})
    try:
        from lion_rebar.ui import apply_to_group_dialog as модул

        истински = модул.ApplyToGroupDialog
        уловени: dict = {}

        class Потвърждаващ(истински):
            def exec(self):
                уловени["changes"] = dict(self._plan.changes)
                self.note.setPlainText("Фибите са един и същ елемент — тестов запис.")
                self._apply()
                return bool(self.operation_id)

        monkeypatch.setattr(модул, "ApplyToGroupDialog", Потвърждаващ)
        _избери(dialog, "REP")
        dialog._apply_to_group_current()

        assert not предупреждения
        assert уловени["changes"]["production_segments_cm"] == ФИБА
        приложени = [op for op in load_group_operations(tmp_path)
                     if op.get("action") == "APPLY_TO_GROUP"]
        assert приложени and "SIB" in приложени[-1]["affected_handles"]
        връзки = json.loads((tmp_path / "working" / "approved_geometry_links.json")
                            .read_text(encoding="utf-8"))["links"]
        записано = next(връзка for връзка in връзки if връзка["annotation_handle"] == "SIB")
        assert записано["master_shape_code"] == "F005"
        assert записано["production_segment_lengths_cm"] == ФИБА
    finally:
        dialog.close()
