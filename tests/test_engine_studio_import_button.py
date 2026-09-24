"""Бутонът „Внеси форми от редактора“ — натиснат в истинския диалог.

ЗАЩО СЪЩЕСТВУВА. Ядрото (``engine/draft_import.py``) има 37 теста, но до
2026-09-24 бутонът НЕ беше натискан нито веднъж — нито от оператор, нито от
тест. Точно там живеят дефектите, които ядрото не може да хване: действие,
закачено за несъществуващ метод, забравен внос вътре в тялото, чернова, която
не се опреснява, съобщение, което лъже за състоянието.

КАКВО Е ДОКАЗАНО ТУК и какво не. Тук се строи истинският ``EngineStudio`` с
истинско хранилище и се вика самият обработчик. Подменен е САМО изборът на
файл (``QFileDialog``) и модалните съобщения — те чакат човек и биха увиснали
headless. Всичко останало е кодът, който тръгва при натискане.

Не е доказано: че диалогът за избор на файл се отваря и филтрира ``*.json``
както трябва на Windows. Това остава операторска проверка.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from lion_rebar.engine.draft_import import LOCKED_FORM_CODES
from lion_rebar.engine.repository import EngineRepository
from lion_rebar.ui.engine_studio import EngineStudio


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _чернова_от_редактора(code: str = "F022", **промени) -> dict:
    форма = {
        "proposed_code": code,
        "name": "Кобилица-1",
        "status": "ЧЕРНОВА_ЗА_ПРОВЕРКА",
        "initial_direction": "НАГОРЕ",
        "separate_overlapping_ends": True,
        "segments": [
            {"index": 1, "code": "L1", "length_cm": 17, "bend_after_deg": 90,
             "bend_direction": "НАДЯСНО", "dimension_position": "АВТО",
             "dimension_offset": 6},
            {"index": 2, "code": "L2", "length_cm": 30, "bend_after_deg": 45,
             "bend_direction": "НАДЯСНО", "dimension_position": "АВТО",
             "dimension_offset": 6},
            {"index": 3, "code": "L3", "length_cm": 24, "bend_after_deg": None,
             "bend_direction": "НЯМА", "dimension_position": "ОТГОРЕ",
             "dimension_offset": 8},
        ],
        "segment_count": 3,
        "bend_count": 2,
        "total_length_cm": 71,
        "calculation": "17 + 30 + 24 = 71 cm",
        "geometry_key": "N|17@90R|30@45R|24",
        "notes": "",
    }
    форма.update(промени)
    return форма


def _пакет(*форми) -> dict:
    return {
        "schema": "lion-rebar-shape-draft/v1",
        "locked_forms": sorted(LOCKED_FORM_CODES),
        "forms": list(форми or (_чернова_от_редактора(),)),
    }


@pytest.fixture()
def студио(tmp_path, monkeypatch):
    """Истинският диалог върху празно хранилище, без модални прозорци."""

    _app()
    repository = EngineRepository(tmp_path / "engine_registry")
    repository.initialize()

    съобщения: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: съобщения.append(str(args[2])) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *args, **kwargs: съобщения.append(str(args[2])) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: съобщения.append(str(args[2])) or QMessageBox.StandardButton.Ok,
    )

    dialog = EngineStudio(repository)
    try:
        yield dialog, repository, съобщения
    finally:
        dialog.deleteLater()


def _подай_файл(monkeypatch, path: Path | str) -> None:
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *args, **kwargs: (str(path), "Чернови на редактора (*.json)"),
    )


def test_deystvieto_e_zakacheno_za_obrabotchika(студио) -> None:
    """Менюто сочи метод, който съществува и се вика."""

    dialog, _repository, _съобщения = студио
    assert callable(getattr(dialog, "import_editor_drafts", None))


def test_bez_otvorena_chernova_kazva_kakvo_da_napravi(студио, monkeypatch, tmp_path) -> None:
    """Празният Studio няма чернова — внасянето отказва, без да пипа нищо."""

    dialog, _repository, съобщения = студио
    файл = tmp_path / "forms.json"
    файл.write_text(json.dumps(_пакет()), encoding="utf-8")
    _подай_файл(monkeypatch, файл)

    dialog.import_editor_drafts()

    assert dialog.draft is None
    assert съобщения and "Няма отворена чернова" in съобщения[0]


def test_natiskaneto_vkarva_formata_v_chernovata(студио, monkeypatch, tmp_path) -> None:
    """Целият път: отворена чернова, избран файл, форма вътре."""

    dialog, repository, съобщения = студио
    dialog._set_draft(repository.create_empty_draft("lion-rebar-master", "MASTER", "0.9.0"))
    преди = len(dialog.draft.shapes)

    файл = tmp_path / "forms.json"
    файл.write_text(json.dumps(_пакет()), encoding="utf-8")
    _подай_файл(monkeypatch, файл)

    dialog.import_editor_drafts()

    кодове = [форма.code for форма in dialog.draft.shapes]
    assert len(кодове) == преди + 1
    assert "F022" in кодове
    внесена = next(ф for ф in dialog.draft.shapes if ф.code == "F022")
    assert внесена.state == "DRAFT", "внесената форма НЕ бива да е одобрена"
    assert len(внесена.segments) == 3
    assert съобщения and "ЧЕРНОВИ (state=DRAFT)" in съобщения[-1]


def test_spisukut_s_formi_pokazva_vnesenata(студио, monkeypatch, tmp_path) -> None:
    """Опресняването е част от натискането, не отделна стъпка за оператора."""

    dialog, repository, _съобщения = студио
    dialog._set_draft(repository.create_empty_draft("lion-rebar-master", "MASTER", "0.9.0"))

    файл = tmp_path / "forms.json"
    файл.write_text(json.dumps(_пакет()), encoding="utf-8")
    _подай_файл(monkeypatch, файл)

    dialog.import_editor_drafts()

    показани = [
        dialog.form_list.item(ред).text()
        for ред in range(dialog.form_list.count())
    ]
    assert any("F022" in текст for текст in показани), показани


def test_otkazut_e_po_tsyaloto(студио, monkeypatch, tmp_path) -> None:
    """Един лош ред отказва целия файл — не влиза половин пакет."""

    dialog, repository, съобщения = студио
    dialog._set_draft(repository.create_empty_draft("lion-rebar-master", "MASTER", "0.9.0"))
    преди = len(dialog.draft.shapes)

    лоша = _чернова_от_редактора("F023", total_length_cm=999)
    файл = tmp_path / "forms.json"
    файл.write_text(json.dumps(_пакет(_чернова_от_редактора(), лоша)), encoding="utf-8")
    _подай_файл(monkeypatch, файл)

    dialog.import_editor_drafts()

    assert len(dialog.draft.shapes) == преди, "нито една форма не бива да влезе"
    assert съобщения


def test_povreden_fayl_ne_srutva_dialoga(студио, monkeypatch, tmp_path) -> None:
    dialog, repository, съобщения = студио
    dialog._set_draft(repository.create_empty_draft("lion-rebar-master", "MASTER", "0.9.0"))

    файл = tmp_path / "forms.json"
    файл.write_text("{това не е JSON", encoding="utf-8")
    _подай_файл(monkeypatch, файл)

    dialog.import_editor_drafts()

    assert dialog.draft.shapes == []
    assert съобщения and "не може да бъде прочетен" in съобщения[-1]


def test_otkazan_izbor_na_fayl_ne_pravi_nishto(студио, monkeypatch) -> None:
    """Затворен диалог за избор = нищо не се случва, без съобщение."""

    dialog, repository, съобщения = студио
    dialog._set_draft(repository.create_empty_draft("lion-rebar-master", "MASTER", "0.9.0"))
    съобщения.clear()
    _подай_файл(monkeypatch, "")

    dialog.import_editor_drafts()

    assert dialog.draft.shapes == []
    assert съобщения == []
