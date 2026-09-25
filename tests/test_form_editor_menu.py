"""Редакторът на форми се отваря от програмата (rc44).

Редакторът се доставя от 23.09.2026 — влиза в манифеста и в EXE сборката
(``BUILD_EXE.bat``, ``--add-data lion_rebar\\resources``) — а вносителят му
„Внеси форми от редактора“ е натиснат с тест. Липсваше само едно: нищо в
програмата не го отваряше, тоест операторът трябваше да знае къде на диска
стои един HTML файл.

Тестът пази трите неща, които правят това вярно: файлът наистина е там,
пътят се смята като при заключения MASTER (за да важи и в EXE сборката), и
менюто MASTER носи действие, което го отваря.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QMenu

from lion_rebar.engine.draft_import import FORM_EDITOR_FILENAME, form_editor_path
from lion_rebar.export.master_engine_v031 import master_engine_path


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_the_editor_is_actually_shipped() -> None:
    path = form_editor_path()

    assert path.is_file(), path
    assert path.name == FORM_EDITOR_FILENAME
    assert "FORM_EDITOR" in path.name


def test_the_editor_sits_beside_the_locked_master() -> None:
    """Същата папка ``resources`` — затова важи и в EXE сборката."""

    assert form_editor_path().parent == master_engine_path().parent
    assert form_editor_path().parent.name == "resources"


def test_the_shipped_file_is_the_form_editor_not_a_copy_of_the_master() -> None:
    text = form_editor_path().read_text(encoding="utf-8", errors="replace")

    # Редакторът е този, който изнася пакета за вносителя.
    assert "lion-rebar-shape-draft/v1" in text


def test_build_exe_ships_the_resources_folder() -> None:
    """Ако папката спре да се пакетира, менюто ще сочи към липсващ файл."""

    script = Path(__file__).resolve().parents[1] / "BUILD_EXE.bat"
    text = script.read_text(encoding="utf-8", errors="replace")

    assert "lion_rebar\\resources;lion_rebar\\resources" in text


def test_the_master_menu_really_carries_the_action(app) -> None:
    """Истински прозорец, истинско меню — не съвпадение в изходния текст."""

    import logging

    from lion_rebar.engine.package_io import inspect_engine_bundle
    from lion_rebar.ui.main_window import MainWindow

    class _RepositoryStub:
        def __init__(self) -> None:
            bundles = sorted(
                (Path(__file__).resolve().parents[1] / "engine_updates").glob(
                    "*.lionengine"
                )
            )
            self.engine, _manifest, _names = inspect_engine_bundle(bundles[-1])

        def load_current(self):
            return self.engine

    class _SessionStub:
        has_active_session = False

        def active_summary(self):
            return None

        def record_system_event(self, *_args, **_kwargs) -> None:
            return None

    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("редактор"),
        session_service=_SessionStub(),
    )
    try:
        master = next(
            menu
            for menu in window.menuBar().findChildren(QMenu)
            if menu.title() == "MASTER"
        )
        надписи = [action.text() for action in master.actions()]

        assert "Редактор на форми…" in надписи
        action = next(
            item for item in master.actions() if item.text() == "Редактор на форми…"
        )
        assert action.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_opening_reports_a_missing_editor_instead_of_crashing(monkeypatch) -> None:
    """Липсващ файл е съобщение, не авария — и нищо не се отваря."""

    from lion_rebar.ui import main_window as module

    отворени: list[str] = []
    съобщения: list[str] = []

    class _Window:
        open_form_editor = module.MainWindow.open_form_editor

        def _error(self, message: str) -> None:
            съобщения.append(message)

    monkeypatch.setattr(
        "lion_rebar.engine.draft_import.form_editor_path",
        lambda: Path("няма-такъв-файл.html"),
    )
    monkeypatch.setattr(
        "PySide6.QtGui.QDesktopServices.openUrl",
        lambda *args, **kwargs: отворени.append(str(args)) or True,
    )

    _Window().open_form_editor()

    assert отворени == []
    assert съобщения and "Липсва редакторът на форми" in съобщения[0]
