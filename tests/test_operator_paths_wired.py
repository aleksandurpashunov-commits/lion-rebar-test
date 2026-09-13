"""Всяка завършена функция има операторски път.

ЗАЩО СЪЩЕСТВУВА
---------------
Независимият одит установи двукратно, че код без production caller не е
завършена функция. Четири модула бяха в това състояние: Apply to Group,
Learning Report, isolation report и решаването на UNKNOWN рамки.

Тестът пази това да не се повтори.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path("lion_rebar/ui/main_window.py").read_text(encoding="utf-8")
REVIEW = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")


def _calls_in(source: str, function_name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    return ""


# ------------------------------------------------ изолация

def test_isolation_report_reaches_the_operator():
    """Проверката съществуваше, но резултатът ѝ никой не четеше."""

    assert "def _warn_about_foreign_artifacts" in MAIN
    body = _calls_in(MAIN, "open_project")
    assert "_warn_about_foreign_artifacts()" in body


def test_isolation_warning_does_not_block():
    """Предупреждава, не отказва — практиката още не е известна."""

    body = _calls_in(MAIN, "_warn_about_foreign_artifacts")
    assert "QMessageBox.warning" in body
    assert "raise" not in body


def test_isolation_warning_is_journalled():
    body = _calls_in(MAIN, "_warn_about_foreign_artifacts")
    assert "FOREIGN_ARTIFACTS_DETECTED" in body


# ------------------------------------------------ отчет за конвенциите

def test_learning_report_has_a_menu_action():
    assert "action_learning_report" in MAIN
    assert "addAction(self.action_learning_report)" in MAIN
    assert "def show_learning_report" in MAIN


def test_learning_report_states_it_changes_nothing():
    body = _calls_in(MAIN, "show_learning_report")
    assert "не се" in body and "автоматично" in body


def test_learning_report_refuses_without_data():
    body = _calls_in(MAIN, "show_learning_report")
    assert "Пусни пакетен анализ" in body


# ------------------------------------------------ UNKNOWN рамки

def test_unknown_frame_dialog_exists():
    path = Path("lion_rebar/ui/unknown_frame_dialog.py")
    assert path.is_file()
    source = path.read_text(encoding="utf-8")
    assert "record_frame_decision" in source
    assert "SELECTABLE_CATEGORIES" in source


def test_unknown_frame_dialog_is_offered_on_blocked_export():
    assert "def _offer_unknown_frame_resolution" in MAIN
    assert "_offer_unknown_frame_resolution()" in MAIN


def test_unknown_frame_decision_requires_a_reason():
    source = Path("lion_rebar/ui/unknown_frame_dialog.py").read_text(encoding="utf-8")
    assert "Липсва обосновка" in source


def test_unknown_frame_dialog_allows_skipping():
    """Рамка без избор остава неопределена — не се налага решение."""

    source = Path("lion_rebar/ui/unknown_frame_dialog.py").read_text(encoding="utf-8")
    assert "не решавай сега" in source


# ------------------------------------------------ Apply to Group

def test_apply_to_group_has_a_button():
    assert "apply_group_button" in REVIEW
    assert "def _apply_to_group_current" in REVIEW
    assert "ApplyToGroupDialog" in REVIEW


def test_apply_to_group_dialog_shows_preview_before_applying():
    source = Path("lion_rebar/ui/apply_to_group_dialog.py").read_text(encoding="utf-8")
    assert "build_group_plan" in source
    assert "Причина за изключване" in source
    # Планът се строи в __init__, прилагането е в _apply -- проверява се
    # редът на ИЗПЪЛНЕНИЕ, не на импортите.
    tree = ast.parse(source)
    init_body = next(
        ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    apply_body = next(
        ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_apply")
    assert "build_group_plan(" in init_body
    assert "apply_group_plan(" in apply_body
    assert "apply_group_plan(" not in init_body


def test_apply_to_group_requires_a_note():
    source = Path("lion_rebar/ui/apply_to_group_dialog.py").read_text(encoding="utf-8")
    assert "Липсва бележка" in source


def test_apply_to_group_refuses_without_master_shape():
    body = _calls_in(REVIEW, "_apply_to_group_current")
    assert "Няма форма" in body


def test_apply_to_group_reports_full_rollback_on_failure():
    source = Path("lion_rebar/ui/apply_to_group_dialog.py").read_text(encoding="utf-8")
    assert "Нито една позиция не е променена" in source


def test_apply_button_disabled_when_plan_refuses():
    source = Path("lion_rebar/ui/apply_to_group_dialog.py").read_text(encoding="utf-8")
    assert "setEnabled(self._plan.can_apply)" in source
