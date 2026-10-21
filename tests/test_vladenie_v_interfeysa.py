# -*- coding: utf-8 -*-
"""Спорното владение е решимо през НОРМАЛНИЯ интерфейс (§ 6.2).

Собственик, 02.10.2026: „липсващ операторски път за 52 позиции над 23 надписа.
Той трябва да стане използваем през нормалния интерфейс."

Backend без операторски път не е завършена функция — същият урок като при
`unknown_frame_dialog`. Затова тук се изпълнява РЕАЛНИЯТ диалог: сглобява се от
редовете на отчета, избира се решение, натиска се „Запиши" и се проверява какво
е стигнало до диска.

Пази се и обратното: без избор не се записва нищо, а решение без обосновка се
отказва.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from lion_rebar.export.ownership_decisions import (
    ВИД_СПОДЕЛЕНА,
    ВИД_СОБСТВЕНИК,
    load_ownership_decisions,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.ui.ownership_conflict_dialog import (
    OwnershipConflictDialog,
    conflict_clusters,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _самоличност() -> DataIdentity:
    return DataIdentity(
        project_id="P", source_sha256="S", source_generation_id="G", analysis_run_id="R1"
    )


def _ред(handle: str, *, текст="2N14x600", геометрия=("G1",), членове=("A", "B")) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": текст,
        "annotation_class": "ACTIONABLE",
        "annotation_count": 2,
        "declared_length_cm": 600.0,
        "global_conflict": True,
        "geometry_owner_conflict": True,
        "candidate_handles": list(геометрия),
        "ownership_conflict_key": list(геометрия),
        "ownership_conflict_members": list(членове),
    }


def _редове() -> list[dict]:
    return [_ред("A"), _ред("B")]


def test_klasterite_se_sglobyavat_ot_otcheta():
    клъстери = conflict_clusters(_редове())

    assert len(клъстери) == 1
    клъстер = клъстери[0]
    assert клъстер["geometry_key"] == ("G1",)
    assert клъстер["member_handles"] == ("A", "B")
    # Всеки член се вижда ПОИМЕННО, за да бъде проверен поотделно.
    assert [член["annotation_handle"] for член in клъстер["members"]] == ["A", "B"]
    assert клъстер["members"][0]["annotation_count"] == 2


def test_chist_otchet_nyama_klasteri():
    редове = [dict(ред, global_conflict=False) for ред in _редове()]

    assert conflict_clusters(редове) == []


def _диалог(app, tmp_path):
    return OwnershipConflictDialog(
        conflict_clusters(_редове()),
        tmp_path,
        _самоличност(),
        operator="Александър Пашунов",
    )


def test_izborat_sobstvenik_stiga_do_diska(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    диалог = _диалог(app, tmp_path)
    try:
        избор = диалог._choice_boxes[0]
        индекс = next(
            i for i in range(избор.count())
            if избор.itemData(i) == (ВИД_СОБСТВЕНИК, "A")
        )
        избор.setCurrentIndex(индекс)
        диалог._note_edits[0].setText("прътът е начертан веднъж, надписът на A е негов")

        диалог._save()

        assert диалог.decisions_made == 1
        решения = load_ownership_decisions(tmp_path, _самоличност())
        решение = решения[("G1",)]
        assert решение.kind == ВИД_СОБСТВЕНИК
        assert решение.owner_annotation_handle == "A"
        assert решение.member_handles == ("A", "B")
        assert решение.decided_by == "Александър Пашунов"
        assert решение.owns("A") and not решение.owns("B")
    finally:
        диалог.deleteLater()


def test_izborat_obshta_geometriya_stiga_do_diska(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    диалог = _диалог(app, tmp_path)
    try:
        избор = диалог._choice_boxes[0]
        индекс = next(
            i for i in range(избор.count())
            if избор.itemData(i) == (ВИД_СПОДЕЛЕНА, "")
        )
        избор.setCurrentIndex(индекс)
        диалог._note_edits[0].setText("един начертан обект, два реда в описа")

        диалог._save()

        решение = load_ownership_decisions(tmp_path, _самоличност())[("G1",)]
        assert решение.kind == ВИД_СПОДЕЛЕНА
        assert решение.owns("A") and решение.owns("B")
    finally:
        диалог.deleteLater()


def test_bez_obosnovka_ne_se_zapisva(app, tmp_path, monkeypatch):
    предупреждения: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: предупреждения.append(str(a[2])))
    )
    диалог = _диалог(app, tmp_path)
    try:
        диалог._choice_boxes[0].setCurrentIndex(1)

        диалог._save()

        assert диалог.decisions_made == 0
        assert load_ownership_decisions(tmp_path, _самоличност()) == {}
        assert предупреждения and "обосновка" in предупреждения[0]
    finally:
        диалог.deleteLater()


def test_bez_izbor_ne_se_zapisva_nishto(app, tmp_path, monkeypatch):
    съобщения: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: съобщения.append(str(a[2])))
    )
    диалог = _диалог(app, tmp_path)
    try:
        диалог._save()

        assert диалог.decisions_made == 0
        assert load_ownership_decisions(tmp_path, _самоличност()) == {}
        assert съобщения and "нито един" in съобщения[0]
    finally:
        диалог.deleteLater()


def test_menyuto_nosi_deystvieto(app):
    """Пътят е в НОРМАЛНИЯ интерфейс, не само в кода."""

    import logging

    from PySide6.QtWidgets import QMenu

    from lion_rebar.engine.package_io import inspect_engine_bundle
    from lion_rebar.ui.main_window import MainWindow

    class _Хранилище:
        def __init__(self) -> None:
            пакети = sorted(
                (Path(__file__).resolve().parents[1] / "engine_updates").glob(
                    "*.lionengine"
                )
            )
            self.engine, _m, _n = inspect_engine_bundle(пакети[-1])

        def load_current(self):
            return self.engine

    class _Сесия:
        has_active_session = False

        def active_summary(self):
            return None

        def record_system_event(self, *_a, **_k) -> None:
            return None

    прозорец = MainWindow(
        _Хранилище(), logging.getLogger("владение"), session_service=_Сесия()
    )
    try:
        анализ = next(
            меню
            for меню in прозорец.menuBar().findChildren(QMenu)
            if меню.title() == "Анализ"
        )
        надписи = [действие.text() for действие in анализ.actions()]
        assert "Спорно владение на геометрия…" in надписи
    finally:
        прозорец.close()
        прозорец.deleteLater()
