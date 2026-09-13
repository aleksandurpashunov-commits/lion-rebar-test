"""Функционален regression тест за "Приложи към групата".

Независимият одит на rc43 намери груповото решение за RELEASE BLOCKER:
``ReviewCenterDialog`` никога не получаваше ``project_root``/``data_identity``,
``self._rows`` никога не се присвояваше, бутонът викаше несъществуващ
``self._current_row()`` вместо ``self.current_row()``, а самият
``ApplyToGroupDialog`` четеше несъществуващото поле ``member.included``
(вместо ``member.eligible``) при построяване на таблицата си — четири
независими дефекта, всеки от които сам би блокирал функцията.

Тези тестове изпълняват реалния код (не грепват текста на файла), за да не
се повтори пропускът, отбелязан в одита: 113 от 1040 теста само проверяваха
присъствие на низ и не хващаха счупен production caller.
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
from lion_rebar.review.apply_to_group import load_group_operations
from lion_rebar.ui.review_center import ReviewCenterDialog

PROJECT_ID = "PID-1"
SOURCE_GENERATION = "GEN-1"
SOURCE_SHA = "a" * 64


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _row(handle: str, *, candidate_handle: str) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": "2N10x400",
        "annotation_count": 2,
        "diameter_mm": 10,
        "declared_length_cm": 400,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": [candidate_handle],
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
        "generated_at": "2026-09-08T00:00:00+03:00",
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


def _select_handle(dialog: ReviewCenterDialog, handle: str) -> None:
    index = next(
        i for i, row in enumerate(dialog._visible_rows)
        if str(row.get("annotation_handle")) == handle
    )
    dialog.table.selectRow(index)


def _make_dialog(tmp_path: Path, rows: list[dict]) -> ReviewCenterDialog:
    report_path = tmp_path / "report.json"
    state_path = tmp_path / "review_center_state.json"
    _write_report(report_path, rows)
    return ReviewCenterDialog(
        report_path,
        state_path=state_path,
        project_root=tmp_path,
        operator_name="Тестов инженер",
    )


def test_dialog_receives_project_identity_and_full_row_list(tmp_path: Path):
    """Регресия за Д-1/Д-2: project_root/data_identity/_rows трябва да се задават."""

    _app()
    rows = [_row("REP-1", candidate_handle="G1"), _row("SIB-1", candidate_handle="G2")]
    dialog = _make_dialog(tmp_path, rows)
    try:
        assert dialog.project_root == tmp_path
        assert isinstance(dialog.data_identity, DataIdentity)
        assert dialog.data_identity.project_id == PROJECT_ID
        assert dialog.data_identity.source_generation_id == SOURCE_GENERATION
        assert len(dialog._rows) == 2
        assert {row["annotation_handle"] for row in dialog._rows} == {"REP-1", "SIB-1"}
    finally:
        dialog.close()


def test_missing_project_root_stays_fail_closed(tmp_path: Path, monkeypatch):
    """Без project_root бутонът трябва да си остане заключен, не да гръмне."""

    _app()
    rows = [_row("REP-1", candidate_handle="G1")]
    report_path = tmp_path / "report.json"
    state_path = tmp_path / "review_center_state.json"
    _write_report(report_path, rows)
    dialog = ReviewCenterDialog(report_path, state_path=state_path)
    try:
        assert dialog.project_root is None
        warnings: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: warnings.append(args[-1]) or QMessageBox.StandardButton.Ok,
        )
        _select_handle(dialog, "REP-1")
        dialog._apply_to_group_current()
        assert warnings, "Очаквано е предупреждение за липсваща проектна идентичност."
    finally:
        dialog.close()


def test_apply_to_group_button_calls_real_current_row(tmp_path: Path, monkeypatch):
    """Регресия за третия дефект: бутонът викаше self._current_row(), което не съществува."""

    _app()
    rows = [_row("REP-1", candidate_handle="G1"), _row("SIB-1", candidate_handle="G2")]
    dialog = _make_dialog(tmp_path, rows)
    try:
        _select_handle(dialog, "REP-1")
        assert dialog.current_row() is not None
        assert dialog.current_row()["annotation_handle"] == "REP-1"

        captured = {}

        class FakeApplyToGroupDialog:
            def __init__(self, representative, rows, changes, project_root, identity, operator, parent=None):
                captured["representative"] = representative
                captured["rows"] = rows
                captured["project_root"] = project_root
                captured["identity"] = identity
                captured["operator"] = operator
                self.operation_id = "FAKE-OP-1"

            def exec(self):
                return True

        monkeypatch.setattr(
            "lion_rebar.ui.apply_to_group_dialog.ApplyToGroupDialog",
            FakeApplyToGroupDialog,
        )
        emitted = []
        dialog.decisionsChanged.connect(lambda: emitted.append(True))

        dialog._apply_to_group_current()

        assert captured["representative"]["annotation_handle"] == "REP-1"
        assert len(captured["rows"]) == 2
        assert captured["project_root"] == tmp_path
        assert captured["identity"].project_id == PROJECT_ID
        assert captured["operator"] == "Тестов инженер"
        assert emitted, "decisionsChanged трябва да се излъчи след успешно приложение."
    finally:
        dialog.close()


def test_apply_to_group_dialog_end_to_end_persists_operation(tmp_path: Path, monkeypatch):
    """Регресия за четвъртия дефект: member.included не съществуваше -> AttributeError.

    Строи реалния ``ApplyToGroupDialog`` (без mock) върху две доказано
    еднакви позиции и проверява, че групово приложеното решение наистина се
    записва трайно през ``apply_group_plan``.
    """

    _app()
    from lion_rebar.ui.apply_to_group_dialog import ApplyToGroupDialog

    # QMessageBox.information() на успех е модален и блокира headless
    # теста за постоянно, ако не се подмени - трябва само да не гърми.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)

    rows = [_row("REP-1", candidate_handle="G1"), _row("SIB-1", candidate_handle="G2")]
    identity = DataIdentity(
        project_id=PROJECT_ID,
        source_sha256=SOURCE_SHA,
        source_generation_id=SOURCE_GENERATION,
        analysis_run_id="RUN-1",
    )
    changes = {"master_shape_code": "F001"}
    dialog = ApplyToGroupDialog(
        rows[0], rows, changes, tmp_path, identity,
        operator="Тестов инженер",
    )
    try:
        assert dialog._plan.affected_count == 1  # само SIB-1; REP-1 е представителят
        dialog.note.setPlainText("Двете позиции са доказано еднакви — тестов запис.")
        dialog._apply()
        assert dialog.operation_id, "Операцията трябва да завърши успешно и да върне id."

        operations = load_group_operations(tmp_path)
        applied = [op for op in operations if op.get("action") == "APPLY_TO_GROUP"]
        assert applied, "Груповата операция трябва да е записана трайно на диска."
        assert applied[-1]["operation_id"] == dialog.operation_id
        assert "SIB-1" in (applied[-1].get("affected_handles") or [])
    finally:
        dialog.close()
