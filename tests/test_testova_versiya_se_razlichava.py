# -*- coding: utf-8 -*-
"""Тестовата линия се различава видимо и по данни от основната програма.

Собственик, 08.10.2026: „да го виждам и различавам ясно от основната“.
Основната програма (rc44) е на драйва, тестовата (C:\\lr_exp) — на лаптопа,
но и двете пазят данните си в ``%LOCALAPPDATA%\\LionBuild``. Без отделно име
тестовата, пусната без START_TEST.bat, би писала в engine registry-то и
сесиите на основната.

Ако някога тази линия стане основната програма, тестът ще падне — нарочно:
знакът трябва да се махне съзнателно, не да се пренесе тихо.
"""
from __future__ import annotations

import os
from pathlib import Path

from lion_rebar.config import constants
from lion_rebar.utils.app_paths import AppPaths


def test_тестовата_линия_носи_знак():
    assert constants.APP_EDITION
    assert "ТЕСТОВА" in constants.APP_EDITION


def test_папката_с_данни_не_е_тази_на_основната(tmp_path, monkeypatch):
    monkeypatch.delenv("LION_REBAR_DATA_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    пътища = AppPaths.default()
    assert пътища.root == tmp_path / "LionBuild" / constants.APP_DATA_FOLDER
    assert пътища.root.name != "LionRebarDesktop"


def test_изричната_папка_все_още_важи(tmp_path, monkeypatch):
    # START_TEST.bat и сесиите на Claude пренасочват с LION_REBAR_DATA_ROOT.
    monkeypatch.setenv("LION_REBAR_DATA_ROOT", str(tmp_path / "изрично"))
    assert AppPaths.default().root == tmp_path / "изрично"


class _Хранилище:
    def load_current(self):
        from lion_rebar.engine.models import EnginePackage

        return EnginePackage(engine_id="test-engine", title="Test",
                             version="0.3.14", production_ready=True)


class _Сесия:
    has_active_session = False

    def __init__(self, корен: Path) -> None:
        self.paths = type("Пътища", (), {"root": корен})()

    def active_summary(self):
        return None

    def record_system_event(self, *_args, **_kwargs) -> None:
        return None


def test_прозорецът_показва_знака(tmp_path):
    import logging

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from lion_rebar.ui.main_window import MainWindow

    приложение = QApplication.instance() or QApplication([])
    корен = tmp_path / "данни"
    прозорец = MainWindow(_Хранилище(), logging.getLogger("издание"),
                          session_service=_Сесия(корен))
    try:
        assert constants.APP_EDITION in прозорец.windowTitle()
        assert constants.APP_VERSION in прозорец.windowTitle()
        лента = прозорец.edition_banner
        assert constants.APP_EDITION in лента.text()
        assert str(корен) in лента.text()
    finally:
        прозорец.close()
        прозорец.deleteLater()
        приложение.processEvents()
