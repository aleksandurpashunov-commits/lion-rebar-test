"""Регресия: бутонът "Върни последното масово одобрение" в Review Center.

За разлика от ``tests/test_bulk_approval_operations.py`` (чист backend
тест), този файл кликва РЕАЛНИЯ бутон на ``ReviewCenterDialog`` и проверява,
че диалогът презарежда точното си in-memory състояние от диска след undo —
именно това е класът дефекти, който Стъпка 1 показа, че статичен преглед
на backend-а пропуска (счупена жица между бутон и работещ backend).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from lion_rebar.review.bulk_approval_operations import record_bulk_operation
from lion_rebar.ui.review_center import ReviewCenterDialog

PROJECT_ID = "PID-1"
SOURCE_GENERATION = "GEN-1"
SOURCE_SHA = "a" * 64


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _row(handle: str) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": "2N10x400",
        "annotation_count": 2,
        "diameter_mm": 10,
        "declared_length_cm": 400,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "candidate_handles": ["G1"],
        "alternative_candidates": [],
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "data_quality_flags": [],
        "data_quality_notes": [],
        "candidate_notes": [],
        "analysis_warnings": [],
        "project_id": PROJECT_ID,
        "source_generation_id": SOURCE_GENERATION,
        "source_sha256": SOURCE_SHA,
        "analysis_run_id": "RUN-1",
    }


def _write_report(path: Path, rows: list[dict]) -> None:
    payload = {
        "schema": "lion-rebar-geometry-batch-report/v3",
        "project_name": "Test",
        "generated_at": "2026-09-09T00:00:00+03:00",
        "summary": {},
        "data_identity": {
            "project_id": PROJECT_ID,
            "source_generation_id": SOURCE_GENERATION,
            "source_sha256": SOURCE_SHA,
            "analysis_run_id": "RUN-1",
        },
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_undo_bulk_button_reverts_ledger_state_and_in_memory_dialog(tmp_path: Path, monkeypatch):
    _app()
    working = tmp_path / "working"
    working.mkdir()
    ledger_path = working / "approved_geometry_links.json"
    state_path = working / "review_center_state.json"

    ledger_before = {"schema": "lion-rebar-approved-geometry-links/v1", "links": [], "history": []}
    ledger_before_bytes = json.dumps(ledger_before, ensure_ascii=False).encode("utf-8")
    # Няма state файл преди операцията -- диалогът никога не е бил отварян.
    state_before_bytes = None

    operation_id = record_bulk_operation(
        tmp_path,
        mode="GEOMETRY_SAMPLE",
        applied_by="Тестов инженер",
        affected_handles=["H1"],
        ledger_snapshot=ledger_before_bytes,
        state_snapshot=state_before_bytes,
    )
    assert operation_id

    # "След" операцията: H1 е одобрен и в ledger-а, и в review center state-а
    # -- точно каквото main_window._write_approved_geometry_links_from_report_rows
    # + dialog.mark_geometry_bulk_approved биха записали.
    ledger_after = dict(ledger_before)
    ledger_after["links"] = [{
        "annotation_handle": "H1", "annotation_text": "2N10x400",
        "master_shape_code": "F001",
    }]
    ledger_path.write_text(json.dumps(ledger_after, ensure_ascii=False), encoding="utf-8")

    report_path = tmp_path / "report.json"
    _write_report(report_path, [_row("H1")])

    dialog = ReviewCenterDialog(
        report_path,
        state_path=state_path,
        project_root=tmp_path,
        operator_name="Тестов инженер",
        approved_links={"H1": ledger_after["links"][0]},
    )
    try:
        dialog._state.setdefault("decisions", {})["H1"] = {
            "decision": "APPROVED_BULK_AFTER_GEOMETRY_SAMPLE",
            "at": "2026-09-09T00:00:01+03:00",
        }
        dialog.approved_handles.add("H1")
        dialog._save_state()
        assert "H1" in dialog.approved_handles

        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        infos: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda *a, **k: infos.append(a[-1]) or QMessageBox.StandardButton.Ok,
        )
        emitted = []
        dialog.decisionsChanged.connect(lambda: emitted.append(True))

        dialog._undo_last_bulk_approval()

        assert infos, "Очаквано е потвърждение за успешно връщане."
        assert emitted, "decisionsChanged трябва да се излъчи след undo."
        # Ledger-ът трябва да е байт-точно възстановен.
        assert ledger_path.read_text(encoding="utf-8") == json.dumps(
            ledger_before, ensure_ascii=False
        )
        # State файлът не е съществувал преди операцията -> undo_bulk_operation
        # трябва да го изтрие. Диалогът веднага след това пресмята свеж
        # контролен sample (легитимен, предшестващ тази поправка страничен
        # ефект на _load_and_apply_state/_resolve_sample_handles) и може да
        # го запише отново -- това е ОЧАКВАНО. Важното е, че H1 вече го НЯМА
        # никъде -- нито на диска, нито в паметта на диалога.
        if state_path.exists():
            restored_payload = json.loads(state_path.read_text(encoding="utf-8"))
            assert "H1" not in restored_payload.get("decisions", {})
        assert "H1" not in dialog.approved_handles
        assert "H1" not in dialog._state.get("decisions", {})
    finally:
        dialog.close()


def test_undo_bulk_button_without_project_root_is_fail_closed(tmp_path: Path, monkeypatch):
    _app()
    report_path = tmp_path / "report.json"
    state_path = tmp_path / "review_center_state.json"
    _write_report(report_path, [_row("H1")])
    dialog = ReviewCenterDialog(report_path, state_path=state_path)
    try:
        assert dialog.project_root is None
        warnings: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warnings.append(a[-1]) or QMessageBox.StandardButton.Ok,
        )
        dialog._undo_last_bulk_approval()
        assert warnings, "Без project_root трябва да предупреди, не да гръмне."
    finally:
        dialog.close()


def test_undo_bulk_button_with_no_pending_operations_informs_and_returns(
    tmp_path: Path, monkeypatch
):
    _app()
    report_path = tmp_path / "report.json"
    state_path = tmp_path / "working" / "review_center_state.json"
    _write_report(report_path, [_row("H1")])
    dialog = ReviewCenterDialog(
        report_path, state_path=state_path, project_root=tmp_path,
    )
    try:
        infos: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda *a, **k: infos.append(a[-1]) or QMessageBox.StandardButton.Ok,
        )
        dialog._undo_last_bulk_approval()
        assert infos, "Без чакащи операции трябва да информира, не да гръмне."
    finally:
        dialog.close()
