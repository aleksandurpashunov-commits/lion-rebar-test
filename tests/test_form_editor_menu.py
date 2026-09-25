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
import sys
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


def _прозорец(съобщения: list[str]):
    """Най-малкото, което ``open_form_editor`` докосва."""

    from lion_rebar.ui import main_window as module

    class _Bar:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def showMessage(self, message: str, _timeout: int = 0) -> None:  # noqa: N802
            self.messages.append(message)

    class _Window:
        open_form_editor = module.MainWindow.open_form_editor

        def __init__(self) -> None:
            self.bar = _Bar()

        def statusBar(self):  # noqa: N802 — Qt име
            return self.bar

        def _error(self, message: str) -> None:
            съобщения.append(message)

        def _info(self, message: str) -> None:
            съобщения.append(message)

    return _Window()


def test_opening_reports_a_missing_editor_instead_of_crashing(monkeypatch) -> None:
    """Липсващ файл е съобщение, не авария — и нищо не се отваря."""

    съобщения: list[str] = []
    monkeypatch.setattr(
        "lion_rebar.engine.draft_import.form_editor_path",
        lambda: Path("няма-такъв-файл.html"),
    )

    _прозорец(съобщения).open_form_editor()

    assert съобщения and "Липсва редакторът на форми" in съобщения[0]


def test_the_editor_goes_through_the_browser_not_the_file_association(
    monkeypatch,
) -> None:
    """Регресията от 25.09: асоциацията за .html отвори редактора в Notepad.

    Собственик, 25.09.2026, със снимка на 207 571 знака суров HTML. Тук се
    пази, че прозорецът вика браузърния път, а не общото отваряне на файл.
    """

    import lion_rebar.utils.open_in_browser as browser

    поискани: list[Path] = []
    monkeypatch.setattr(
        browser,
        "open_html_in_browser",
        lambda path: поискани.append(Path(path)) or None,
    )

    съобщения: list[str] = []
    прозорец = _прозорец(съобщения)
    прозорец.open_form_editor()

    assert поискани == [form_editor_path()]
    assert съобщения == []
    # Операторът научава откъде се внася свалената .json.
    assert прозорец.bar.messages and "Внеси форми от" in прозорец.bar.messages[0]


def test_a_browser_that_will_not_start_is_reported_with_the_path(
    monkeypatch,
) -> None:
    import lion_rebar.utils.open_in_browser as browser

    monkeypatch.setattr(
        browser, "open_html_in_browser", lambda path: "Браузърът не тръгна."
    )

    съобщения: list[str] = []
    _прозорец(съобщения).open_form_editor()

    assert съобщения
    assert "Браузърът не тръгна." in съобщения[0]
    assert str(form_editor_path()) in съобщения[0]


class TestBrowserResolution:
    """``utils.open_in_browser`` — асоциацията на ПРОТОКОЛА, не на файла."""

    def test_the_url_takes_the_place_of_the_percent_one(self) -> None:
        from lion_rebar.utils.open_in_browser import browser_argv

        argv = browser_argv(
            r'"C:\Program Files\App\browser.exe" --single-argument %1',
            "file:///C:/x.html",
        )

        assert argv == [
            r"C:\Program Files\App\browser.exe",
            "--single-argument",
            "file:///C:/x.html",
        ]

    def test_a_command_without_a_placeholder_still_gets_the_url(self) -> None:
        from lion_rebar.utils.open_in_browser import browser_argv

        argv = browser_argv(r'"C:\App\browser.exe"', "file:///C:/x.html")

        assert argv == [r"C:\App\browser.exe", "file:///C:/x.html"]

    def test_an_empty_command_gives_no_argv(self) -> None:
        from lion_rebar.utils.open_in_browser import browser_argv

        assert browser_argv("", "file:///C:/x.html") == []

    @pytest.mark.skipif(
        sys.platform != "win32", reason="Изборът на протокол е на Windows."
    )
    def test_windows_resolves_a_real_browser(self) -> None:
        """Командата идва от избора за http, затова е браузър, не Notepad."""

        from lion_rebar.utils.open_in_browser import (
            browser_argv,
            default_browser_command,
        )

        command = default_browser_command()
        if command is None:
            pytest.skip("Няма избран браузър за http на тази машина.")
        argv = browser_argv(command, "file:///C:/x.html")

        assert argv and Path(argv[0]).is_file()
        assert argv[-1] == "file:///C:/x.html"

    def test_a_missing_file_is_reported_before_any_launch(self, tmp_path) -> None:
        from lion_rebar.utils.open_in_browser import open_html_in_browser

        проблем = open_html_in_browser(tmp_path / "няма.html")

        assert проблем and "Липсва файлът" in проблем
