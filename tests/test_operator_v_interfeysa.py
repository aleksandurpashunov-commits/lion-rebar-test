# -*- coding: utf-8 -*-
"""Текущият оператор се ВИЖДА в прозореца и може да се смени.

Точки 1 и 2 от решението на собственика (01.10.2026): „При първото записване на
операторско решение се въвежда име и то се запомня. Текущият оператор се вижда в
интерфейса и може да се сменя."

Истински прозорец и истинско меню, не съвпадение в изходния текст. Проверява се
и че отказът да се заяви име НЕ блокира записването на решението: да спрем
инженерната работа заради име би било по-лошо от видимата празнота, а измислено
име — най-лошото.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QInputDialog, QMenu

from lion_rebar.session.operator_identity import НЕЗАЯВЕН, claimed_operator, declare_operator


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _ПътищаЗаглушка:
    def __init__(self, settings_file: Path) -> None:
        self.settings_file = settings_file


class _СесияЗаглушка:
    has_active_session = False

    def __init__(self, settings_file: Path) -> None:
        self.paths = _ПътищаЗаглушка(settings_file)
        self.session_id = "S-TEST"

    def active_summary(self):
        return None

    def record_system_event(self, *_args, **_kwargs) -> None:
        return None


def _прозорец(settings_file: Path):
    from lion_rebar.engine.package_io import inspect_engine_bundle
    from lion_rebar.ui.main_window import MainWindow

    class _Хранилище:
        def __init__(self) -> None:
            пакети = sorted(
                (Path(__file__).resolve().parents[1] / "engine_updates").glob(
                    "*.lionengine"
                )
            )
            self.engine, _manifest, _names = inspect_engine_bundle(пакети[-1])

        def load_current(self):
            return self.engine

    return MainWindow(
        _Хранилище(),
        logging.getLogger("оператор"),
        session_service=_СесияЗаглушка(settings_file),
    )


def _действие_оператор(прозорец):
    сесия = next(
        меню
        for меню in прозорец.menuBar().findChildren(QMenu)
        if меню.title() == "Сесия"
    )
    return next(
        действие
        for действие in сесия.actions()
        if действие.text().startswith("Оператор")
    )


def test_menyuto_pokazva_che_operatorat_e_nezayaven(app, tmp_path):
    прозорец = _прозорец(tmp_path / "settings.json")
    try:
        assert прозорец.operator_name == ""
        assert _действие_оператор(прозорец).text() == "Оператор: незаявен…"
        # В новите записи влиза НЕЗАЯВЕН, а не историческото „оператор".
        assert прозорец._operator_for_record() == НЕЗАЯВЕН
    finally:
        прозорец.close()
        прозорец.deleteLater()


def test_zayavenoto_ime_se_vizhda_pri_sledvashtiya_start(app, tmp_path):
    настройки = tmp_path / "settings.json"
    declare_operator(настройки, "Александър Пашунов")

    прозорец = _прозорец(настройки)
    try:
        assert прозорец.operator_name == "Александър Пашунов"
        assert _действие_оператор(прозорец).text() == "Оператор: Александър Пашунов…"
        assert прозорец._operator_for_record() == "Александър Пашунов"
    finally:
        прозорец.close()
        прозорец.deleteLater()


def test_smyanata_zapisva_i_obnovyava_nadpisa(app, tmp_path, monkeypatch):
    настройки = tmp_path / "settings.json"
    прозорец = _прозорец(настройки)
    try:
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("Мария Иванова", True))
        )
        прозорец.change_operator_name()

        assert прозорец.operator_name == "Мария Иванова"
        assert claimed_operator(настройки) == "Мария Иванова"
        assert _действие_оператор(прозорец).text() == "Оператор: Мария Иванова…"
    finally:
        прозорец.close()
        прозорец.deleteLater()


def test_otkazat_da_se_zayavi_ime_ne_blokira(app, tmp_path, monkeypatch):
    """Решението се записва и без име — с НЕЗАЯВЕН автор."""

    настройки = tmp_path / "settings.json"
    прозорец = _прозорец(настройки)
    try:
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
        )
        assert прозорец._ensure_operator_declared() is False
        assert прозорец.operator_name == ""
        assert прозорец._operator_for_record() == НЕЗАЯВЕН
        assert claimed_operator(настройки) == ""
    finally:
        прозорец.close()
        прозорец.deleteLater()


def test_rolyata_ne_se_priema_za_ime(app, tmp_path, monkeypatch):
    настройки = tmp_path / "settings.json"
    прозорец = _прозорец(настройки)
    съобщения: list[str] = []
    try:
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("оператор", True))
        )
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *a, **k: съобщения.append(str(a[2]))),
        )
        прозорец.change_operator_name()

        assert прозорец.operator_name == ""
        assert claimed_operator(настройки) == ""
        assert съобщения and "роля" in съобщения[0]
    finally:
        прозорец.close()
        прозорец.deleteLater()
