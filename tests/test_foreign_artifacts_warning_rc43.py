from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from lion_rebar.engine.models import EnginePackage
from lion_rebar.project.isolation import IsolationReport, IsolationViolation
from lion_rebar.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _RepositoryStub:
    def load_current(self) -> EnginePackage:
        return EnginePackage(
            engine_id="test-engine",
            title="Test",
            version="0.3.14",
            production_ready=True,
        )


class _SessionStub:
    has_active_session = False

    def active_summary(self):
        return None

    def record_system_event(self, *_args, **_kwargs) -> None:
        return None


class _ProjectManagerStub:
    """Mirrors ProjectManager.last_isolation_report's real (dataclass) shape.

    Regression for a real bug found 2026-09-12: main_window.py read this
    as a dict (`report.get("artifacts")`, `item.get("isolated")`), but
    project.isolation.IsolationReport/IsolationViolation are dataclasses
    with `.violations` / `.artifact` / `.reason` attributes -- no such
    dict keys exist. Every existing test for this method only grepped the
    source text (test_operator_paths_wired.py), so the mismatch shipped
    silently and crashed open_project() for ANY previously-opened project
    (AttributeError escaping the un-caught exception types in
    open_project()'s try/except, closing over the DXF import with it --
    "Отвори проект" did nothing visible). This test exercises the real
    dataclasses, not a stand-in dict.
    """

    def __init__(self, report: IsolationReport | None) -> None:
        self.last_isolation_report = report


def _window() -> MainWindow:
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("foreign-artifacts-warning"),
        session_service=_SessionStub(),
    )
    window._session_project_event = lambda *a, **k: None
    return window


def test_warn_about_foreign_artifacts_does_not_crash_with_real_violations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app()
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok
    )
    window = _window()
    window.project_manager = _ProjectManagerStub(
        IsolationReport(
            project_id="current-project",
            checked=2,
            violations=[
                IsolationViolation(
                    artifact="approved_geometry_links.json",
                    reason="PROJECT_ID_MISMATCH",
                    detail="other-project-id",
                ),
            ],
        )
    )

    # Must not raise -- this is exactly the call open_project() makes.
    window._warn_about_foreign_artifacts()

    window.close()


def test_warn_about_foreign_artifacts_no_op_when_report_is_none() -> None:
    _app()
    window = _window()
    window.project_manager = _ProjectManagerStub(None)

    window._warn_about_foreign_artifacts()

    window.close()


def test_warn_about_foreign_artifacts_no_op_when_no_violations() -> None:
    _app()
    window = _window()
    window.project_manager = _ProjectManagerStub(
        IsolationReport(project_id="current-project", checked=3, violations=[])
    )

    window._warn_about_foreign_artifacts()

    window.close()
