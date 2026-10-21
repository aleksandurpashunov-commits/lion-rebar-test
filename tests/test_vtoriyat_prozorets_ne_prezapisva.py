# -*- coding: utf-8 -*-
"""Вторият прозорец не записва ОСТАРЯЛО състояние върху по-ново решение.

Уговорка на собственика, 01.10.2026:

> Двата отворени прозореца остават важен риск. Заключването на файла може да
> предотврати едновременен запис, но трябва да се провери дали вторият прозорец
> не записва остаряло състояние върху по-ново решение. Докато това не е
> проверено или надеждно блокирано, § 7.4 е изпълнен частично.

ПРОВЕРЕНО: не беше. Заключването пази файла от едновременен ЗАПИС, но всеки
прозорец държи своя памет за състоянието. Редът

    A отваря → B отваря → A реши X → B реши Y

даваше файл само с Y: решението на A изчезваше при напълно атомарен и заключен
запис. Загубата идва от остарялото ЧЕТЕНЕ, не от записа.

БЛОКИРАНО: при записа, под същата ключалка, се сверява отпечатъкът на файла с
този при зареждането. Разминаване значи, че друг е писал — записът се ОТКАЗВА,
чуждото решение остава непокътнато и операторът научава веднага, че неговото НЕ
е записано. Отказът е по-добър от тихата загуба.

Тук се проверява и другият случай от същото семейство (§ 7.6): нечетим файл със
състоянието вече не се чете като празен. Дотук прозорецът се отваряше с нула
решения и първият запис триеше единственото копие на операторската работа.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from lion_rebar.ui.review_center import ReviewCenterDialog, ReviewStateError

PROJECT_ID = "PID-2"
SOURCE_GENERATION = "GEN-2"
SOURCE_SHA = "b" * 64


@pytest.fixture(scope="module")
def app() -> QApplication:
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
        "geometry_length_raw": 400.0,
        "numeric_difference": 0.0,
        "distance_raw": 5.0,
        "project_id": PROJECT_ID,
        "source_generation_id": SOURCE_GENERATION,
        "source_sha256": SOURCE_SHA,
        "analysis_run_id": "RUN-1",
    }


def _write_report(path: Path, rows: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-geometry-batch-report/v3",
                "project_name": "Test",
                "generated_at": "2026-10-01T00:00:00+03:00",
                "summary": {},
                "data_identity": {
                    "project_id": PROJECT_ID,
                    "source_generation_id": SOURCE_GENERATION,
                    "source_sha256": SOURCE_SHA,
                    "analysis_run_id": "RUN-1",
                },
                "rows": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _прозорец(tmp_path: Path) -> ReviewCenterDialog:
    отчет = tmp_path / "report.json"
    if not отчет.is_file():
        _write_report(отчет, [_row("A-1", candidate_handle="G1"),
                              _row("B-1", candidate_handle="G2")])
    return ReviewCenterDialog(
        отчет,
        state_path=tmp_path / "review_center_state.json",
        project_root=tmp_path,
        operator_name="Тестов инженер",
    )


def _реши(прозорец: ReviewCenterDialog, handle: str, бележка: str) -> None:
    прозорец._state.setdefault("decisions", {})[handle] = {
        "decision": "ENGINEER_RESOLVED",
        "note": бележка,
    }
    прозорец._save_state()


def test_resheniyata_na_dvata_prozoretsa_oceljavat(app, tmp_path, monkeypatch):
    """Различни позиции: нищо не се губи и нищо не се отказва.

    Дотук вторият запис даваше файл само със СВОЕТО решение — решението на
    първия изчезваше при напълно атомарен и заключен запис. Загубата идваше от
    остарялото ЧЕТЕНЕ, не от записа.
    """

    предупреждения: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: предупреждения.append(str(a[2])))
    )

    първи = _прозорец(tmp_path)
    втори = _прозорец(tmp_path)
    try:
        _реши(първи, "A-1", "решение на първия прозорец")
        _реши(втори, "B-1", "решение на втория прозорец")

        записано = json.loads(
            (tmp_path / "review_center_state.json").read_text(encoding="utf-8")
        )
        assert записано["decisions"]["A-1"]["note"] == "решение на първия прозорец"
        assert записано["decisions"]["B-1"]["note"] == "решение на втория прозорец"
        assert втори._state_conflict is False
        assert предупреждения == []
    finally:
        първи.deleteLater()
        втори.deleteLater()


def test_edna_i_sashta_pozitsia_v_dvata_prozoretsa_e_otkaz(app, tmp_path, monkeypatch):
    """Истинският конфликт: и двамата са решили СЪЩАТА позиция, различно."""

    предупреждения: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: предупреждения.append(str(a[2])))
    )

    първи = _прозорец(tmp_path)
    втори = _прозорец(tmp_path)
    try:
        _реши(първи, "A-1", "решението на първия")

        with pytest.raises(ReviewStateError):
            _реши(втори, "A-1", "ДРУГО решение за същата позиция")

        записано = json.loads(
            (tmp_path / "review_center_state.json").read_text(encoding="utf-8")
        )
        # Чуждото решение е непокътнато, нашето НЕ е записано.
        assert записано["decisions"]["A-1"]["note"] == "решението на първия"
        assert втори._state_conflict is True
        assert предупреждения and "НЕ Е ЗАПИСАНО" in предупреждения[0]
        assert "A-1" in предупреждения[0]
    finally:
        първи.deleteLater()
        втори.deleteLater()


def test_edin_prozorets_zapisva_posledovatelno_bez_otkaz(app, tmp_path):
    """Положителна контрола: сверката не пречи на нормалната работа."""

    прозорец = _прозорец(tmp_path)
    try:
        _реши(прозорец, "A-1", "първо")
        _реши(прозорец, "B-1", "второ")

        записано = json.loads(
            (tmp_path / "review_center_state.json").read_text(encoding="utf-8")
        )
        assert set(записано["decisions"]) >= {"A-1", "B-1"}
        assert прозорец._state_conflict is False
    finally:
        прозорец.deleteLater()


def test_nechetimo_sastoyanie_ne_otvarya_prozoretsa(app, tmp_path):
    """§ 7.6: празното състояние би изтрило единственото копие на работата."""

    _write_report(tmp_path / "report.json", [_row("A-1", candidate_handle="G1")])
    (tmp_path / "review_center_state.json").write_text("{непълен", encoding="utf-8")

    with pytest.raises(ReviewStateError):
        ReviewCenterDialog(
            tmp_path / "report.json",
            state_path=tmp_path / "review_center_state.json",
            project_root=tmp_path,
        )

    # Повреденият файл НЕ е презаписан.
    assert (tmp_path / "review_center_state.json").read_text(encoding="utf-8") == "{непълен"


def test_lipsvashto_sastoyanie_e_normalno(app, tmp_path):
    """Нов проект няма състояние — това не е повреда."""

    прозорец = _прозорец(tmp_path)
    try:
        assert прозорец._state.get("decisions") == {}
        # Прозорецът записва базовото състояние при отваряне, затова отпечатъкът
        # е на вече записания файл, а не None.
        assert прозорец._state_fingerprint == прозорец._state_file_fingerprint()
    finally:
        прозорец.deleteLater()
