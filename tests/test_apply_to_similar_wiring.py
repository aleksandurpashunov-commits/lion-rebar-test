"""Функционален regression тест за "Приложи към всички подобни".

Независимият одит намери същия клас дефект като "Приложи към групата" преди
Фаза 0: backend-ът (``review/apply_to_similar.py``) имаше готов, тестван
преглед (``build_similar_plan``), но НЯМАШЕ функция за реално прилагане
(``apply_similar_plan``/undo), UI диалог, нито един бутон в Review Center.

Тези тестове изпълняват реалния код (не грепват текста на файла): изграждат
истински ``ApplyToSimilarDialog``, кликат бутона, четат резултата от диска.
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
from lion_rebar.review.apply_to_similar import load_similar_operations
from lion_rebar.ui.review_center import ReviewCenterDialog

PROJECT_ID = "PID-1"
SOURCE_GENERATION = "GEN-1"
SOURCE_SHA = "a" * 64


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _identity() -> DataIdentity:
    return DataIdentity(
        project_id=PROJECT_ID, source_sha256=SOURCE_SHA,
        source_generation_id=SOURCE_GENERATION, analysis_run_id="RUN-1",
    )


def _row(handle: str, *, diameter_mm: float = 10, count: int = 2) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": "2N10x400",
        "annotation_count": count,
        "diameter_mm": diameter_mm,
        "declared_length_cm": 400,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
        "candidate_handles": [f"{handle}-G"],
        "master_shape_code": "F001",
        "master_shape_name": "ПРАВ ПРЪТ",
        "matcher_blocker_codes": [],
        "review_reason_codes": [],
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
        report_path, state_path=state_path, project_root=tmp_path,
        operator_name="Тестов инженер",
    )


def test_dialog_builds_real_preview_of_matching_and_mismatched_rows(tmp_path: Path):
    """Регресия: преди тази поправка нямаше НИКОЙ извикващ build_similar_plan от UI."""

    _app()
    rows = [
        _row("REP-1"), _row("SIM-1"),
        _row("MISMATCH-1", diameter_mm=12),  # различен Ø -- не е "подобна"
    ]
    dialog = _make_dialog(tmp_path, rows)
    try:
        _select_handle(dialog, "REP-1")
        assert dialog.current_row()["annotation_handle"] == "REP-1"

        from lion_rebar.ui.apply_to_similar_dialog import ApplyToSimilarDialog

        similar_dialog = ApplyToSimilarDialog(
            dialog.current_row(), list(dialog._rows),
            {"master_shape_code": "F001"}, tmp_path, _identity(),
            operator="Тестов инженер", parent=dialog,
        )
        try:
            included = {t.annotation_handle for t in similar_dialog._plan.included}
            assert included == {"SIM-1"}
            # MISMATCH-1 не съвпада по подпис -- изобщо не е кандидат.
            assert all(
                t.annotation_handle != "MISMATCH-1"
                for t in similar_dialog._plan.targets
            )
        finally:
            similar_dialog.close()
    finally:
        dialog.close()


def test_missing_project_root_stays_fail_closed(tmp_path: Path, monkeypatch):
    """Без project_root бутонът трябва да си остане заключен, не да гръмне."""

    _app()
    rows = [_row("REP-1")]
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
        dialog._apply_to_similar_current()
        assert warnings, "Очаквано е предупреждение за липсваща проектна идентичност."
    finally:
        dialog.close()


def test_apply_is_blocked_when_explicitly_disabled(tmp_path: Path):
    """Функцията е включена по подразбиране от 2026-09-09, но остава
    възможно да се изключи (напр. бъдеща настройка на проекта) — прегледът
    остава реален, прилагането се блокира."""

    _app()
    from lion_rebar.ui.apply_to_similar_dialog import ApplyToSimilarDialog

    rows = [_row("REP-1"), _row("SIM-1")]
    dialog = ApplyToSimilarDialog(
        rows[0], rows, {"master_shape_code": "F001"}, tmp_path, _identity(),
        operator="Тестов инженер", enabled=False,
    )
    try:
        assert dialog._plan.enabled is False
        assert dialog._plan.refusal == "FEATURE_DISABLED"
        assert dialog._plan.can_apply is False
        # Бутонът "Приложи" трябва да е неактивен именно защото can_apply е False.
        from PySide6.QtWidgets import QDialogButtonBox

        buttons = dialog.findChild(QDialogButtonBox)
        apply_btn = buttons.button(QDialogButtonBox.StandardButton.Apply)
        assert apply_btn.isEnabled() is False
    finally:
        dialog.close()


def test_apply_to_similar_end_to_end_with_production_default_persists_operation(
    tmp_path: Path, monkeypatch,
):
    """Собственикът включи функцията на 2026-09-09 — production подразбиране
    е вече ``True``, без да се подава ``enabled=`` изрично (точно както
    review_center.py я вика). Реалният ApplyToSimilarDialog прилага и
    записва обратимата операция трайно на диска."""

    _app()
    from lion_rebar.ui.apply_to_similar_dialog import ApplyToSimilarDialog
    from lion_rebar.review.apply_to_similar import FEATURE_ENABLED_DEFAULT

    assert FEATURE_ENABLED_DEFAULT is True
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)

    rows = [_row("REP-1"), _row("SIM-1"), _row("SIM-2")]
    dialog = ApplyToSimilarDialog(
        rows[0], rows, {"master_shape_code": "F001"}, tmp_path, _identity(),
        operator="Тестов инженер",
    )
    try:
        assert dialog._plan.can_apply is True
        assert dialog._plan.affected_count == 2  # SIM-1, SIM-2 (REP-1 е представителят)
        dialog.note.setPlainText("Двете позиции са доказано еднакви — тестов запис.")
        dialog._apply()
        assert dialog.operation_id, "Операцията трябва да завърши успешно и да върне id."

        operations = load_similar_operations(tmp_path)
        applied = [op for op in operations if op.get("action") == "APPLY_TO_SIMILAR"]
        assert applied, "Действието трябва да е записано трайно на диска."
        assert applied[-1]["operation_id"] == dialog.operation_id
        assert set(applied[-1].get("affected_handles") or []) == {"SIM-1", "SIM-2"}
    finally:
        dialog.close()
