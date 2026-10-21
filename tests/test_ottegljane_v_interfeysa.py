# -*- coding: utf-8 -*-
"""Оттеглянето на инженерно решение през НОРМАЛНИЯ интерфейс.

Проверява се поведението на истинския прозорец и на истинския диалог, не
текстът на изходния файл: менюто носи действието, обработчикът оттегля с автор
и мотив, изброява зависимите приети резултати и НЕ трие историята. Остарял,
вече отворен редактор не може да приеме оттегленото предложение.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.review import decision_reuse as пр
from lion_rebar.session.decision_contract import (
    JOURNAL_NAME,
    append_project_decision_event,
    mark_project_decision_delivered,
    withdrawn_decision_ids,
)

КОРЕН_НА_КОДА = Path(__file__).resolve().parents[1]

САМОЛИЧНОСТ = {
    "project_id": "PROEKT-UI",
    "source_sha256": "e" * 64,
    "source_generation_id": "GEN-UI",
    "analysis_run_id": "RUN-UI",
}


@pytest.fixture(scope="module")
def app():
    съществуващо = QApplication.instance()
    if съществуващо is not None:
        return съществуващо
    return QApplication([])


class _Хранилище:
    def __init__(self) -> None:
        пакети = sorted((КОРЕН_НА_КОДА / "engine_updates").glob("*.lionengine"))
        self.engine, _манифест, _бележки = inspect_engine_bundle(пакети[-1])

    def load_current(self):
        return self.engine


class _Сесия:
    has_active_session = False

    def active_summary(self):
        return None

    def record_system_event(self, *_а, **_к) -> None:
        return None

    def record_project_event(self, *_а, **_к) -> None:
        return None


class _Проект:
    def __init__(self, корен: Path) -> None:
        self.root = str(корен)
        self.name = корен.name
        self.version = "1"
        self.engine_version = "0.3.15"
        self.extensions = {"data_identity": dict(САМОЛИЧНОСТ)}


def _досие(корен: Path) -> Path:
    работна = корен / "working"
    работна.mkdir(parents=True, exist_ok=True)
    (работна / "project.json").write_text(
        json.dumps(
            {
                "name": корен.name,
                "project_id": САМОЛИЧНОСТ["project_id"],
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.15",
                "extensions": {"data_identity": САМОЛИЧНОСТ},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return корен


def _решение(корен: Path, *, handle: str, основание: list[str] | None = None):
    товар = {
        "annotation_handle": handle,
        "annotation_text": "350N12x300",
        "action_class": "OVERRIDE",
        "reason_code": "NO_GEOMETRY",
        "reason": "Липсва или не е необходима отделна геометрия",
        "note": "вярно",
        "actor": "Тестов оператор",
        "data_identity": САМОЛИЧНОСТ,
        "original": {
            "count": 350,
            "diameter_mm": 12.0,
            "declared_length_cm": 300.0,
            "shape_code": None,
            "segment_lengths_cm": [],
            "geometry_handles": [],
            "batch_status": "НЯМА КАНДИДАТ",
        },
        "resolved": {
            "count": 350,
            "diameter_mm": 12.0,
            "production_length_cm": 300.0,
            "shape_code": "F001",
            "shape_name": "ПРАВ ПРЪТ",
            "shape_revision": 1,
            "segment_lengths_cm": [300.0],
            "turn_angles_deg": [],
        },
        "geometry": {"handles": []},
    }
    if основание:
        товар["preizpolzvane"] = {
            "схема": "lion-rebar-preizpolzvane-ui-odit/v1",
            "изрично_прието": True,
            "основание": основание,
        }
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)
    mark_project_decision_delivered(корен, плик["event_id"], session_id="S-UI")
    return плик


def _прозорец(app, корен: Path | None = None):
    from lion_rebar.ui.main_window import MainWindow

    assert app is not None

    прозорец = MainWindow(
        _Хранилище(), logging.getLogger("оттегляне"), session_service=_Сесия()
    )
    if корен is not None:
        прозорец.project = _Проект(корен)
        прозорец.operator_name = "Тестов оператор"
    return прозорец


def test_menyuto_nosi_otteglyaneto(app):
    прозорец = _прозорец(app)
    try:
        анализ = next(
            меню
            for меню in прозорец.menuBar().findChildren(QMenu)
            if меню.title() == "Анализ"
        )
        надписи = [действие.text() for действие in анализ.actions()]
        assert "Оттегляне на инженерно решение…" in надписи
    finally:
        прозорец.close()
        прозорец.deleteLater()


def test_obrabotchikat_otteglya_s_avtor_i_motiv(app, tmp_path, monkeypatch):
    корен = _досие(tmp_path / "proekt")
    донорски = _решение(корен, handle="81E9::BJ-FI-L")
    зависимо = _решение(
        корен, handle="ZAVISIMO-1", основание=[донорски["event_id"]]
    )

    съобщения: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *а, **к: съобщения.append(str(а[2]))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *а, **к: съобщения.append("ПРЕДУПРЕЖДЕНИЕ " + str(а[2]))),
    )
    избрано: list[str] = []

    def _избор(_родител, _заглавие, _текст, елементи, *_а, **_к):
        избрано.extend(елементи)
        # Изборът е по ПОЗИЦИЯТА: двата записа имат един и същ надпис и час.
        return next(ред for ред in елементи if "ZAVISIMO-1" in ред), True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(_избор))
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *а, **к: ("формата беше сгрешена", True)),
    )

    прозорец = _прозорец(app, корен)
    try:
        прозорец.withdraw_engineer_decision()
    finally:
        прозорец.close()
        прозорец.deleteLater()

    # Изборът е предложил ДВЕТЕ живи решения и всяко носи своята позиция:
    # без нея два записа с един надпис и един час са неразличими.
    assert len(избрано) == 2
    assert any("81E9::BJ-FI-L" in ред for ред in избрано)
    assert any("ZAVISIMO-1" in ред for ред in избрано)
    оттеглени = withdrawn_decision_ids(корен)
    # Най-новото е зависимото; то е първото в списъка и точно него оттеглихме.
    оттегленото = зависимо["event_id"]
    assert оттегленото in оттеглени
    assert оттеглени[оттегленото]["reason"] == "формата беше сгрешена"
    assert оттеглени[оттегленото]["actor"] == "Тестов оператор"
    # ИСТОРИЯТА НЕ Е ИЗТРИТА.
    журнал = (корен / "working" / JOURNAL_NAME).read_text(encoding="utf-8")
    assert оттегленото in журнал
    assert "ENGINEER_DECISION_WITHDRAWN" in журнал
    assert съобщения and "оттеглено" in съобщения[0]


def test_otteglyaneto_izbroyava_zavisimite_za_povtorna_proverka(
    app, tmp_path, monkeypatch,
):
    корен = _досие(tmp_path / "proekt")
    донорски = _решение(корен, handle="81E9::BJ-FI-L")
    _решение(корен, handle="ZAVISIMO-1", основание=[донорски["event_id"]])

    съобщения: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *а, **к: съобщения.append(str(а[2]))),
    )
    # Избира се ДОНОРСКОТО решение — по позицията, не по реда в списъка.
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        staticmethod(
            lambda _р, _з, _т, елементи, *а, **к: (
                next(ред for ред in елементи if "81E9::BJ-FI-L" in ред), True
            )
        ),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *а, **к: ("донорът беше грешен", True)),
    )
    прозорец = _прозорец(app, корен)
    try:
        прозорец.withdraw_engineer_decision()
    finally:
        прозорец.close()
        прозорец.deleteLater()

    assert съобщения
    assert "ЗА ПОВТОРНА ПРОВЕРКА" in съобщения[0]
    зависими = пр.зависими_за_повторна_проверка(корен)
    assert len(зависими) == 1
    assert зависими[0]["annotation_handle"] == "ZAVISIMO-1"


def test_otkazan_motiv_ne_otteglya_nishto(app, tmp_path, monkeypatch):
    корен = _досие(tmp_path / "proekt")
    _решение(корен, handle="81E9::BJ-FI-L")
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        staticmethod(lambda _р, _з, _т, елементи, *а, **к: (елементи[0], True)),
    )
    # Операторът е натиснал „Откажи" в полето за мотив.
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *а, **к: ("", False))
    )
    прозорец = _прозорец(app, корен)
    try:
        прозорец.withdraw_engineer_decision()
    finally:
        прозорец.close()
        прозорец.deleteLater()
    assert withdrawn_decision_ids(корен) == {}


def test_ostaryaliyat_redaktor_otkazva_prieto_sled_otteglyane(app, tmp_path):
    """Диалогът пита НОВИЯ указател непосредствено преди да приеме."""

    from lion_rebar.session.decision_contract import withdraw_project_decision
    from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog

    корен = _досие(tmp_path / "proekt")
    донорски = _решение(корен, handle="DONOR-UI")
    указател_при_отваряне = пр.Указател.от_проект(корен)
    ред = {
        "annotation_handle": "CEL-UI",
        "annotation_text": "350N12x300",
        "annotation_class": "ACTIONABLE",
        "annotation_count": 350,
        "diameter_mm": 12.0,
        "declared_length_cm": 300.0,
        "batch_status": "НЯМА КАНДИДАТ",
        "master_shape_code": None,
        "canonical_segment_lengths_cm": [],
        "candidate_handles": [],
        "data_identity": {
            **САМОЛИЧНОСТ,
            "source_generation_id": "GEN-DRUGA",
        },
        "matcher_blocker_codes": ["NO_CANDIDATE"],
    }
    предложение = указател_при_отваряне.предложи(
        ред, самоличност={**САМОЛИЧНОСТ, "source_generation_id": "GEN-DRUGA"},
        активни_форми={"F001": 1},
    )
    assert предложение.състояние == пр.ПРИЛОЖИМО

    # Операторът оттегля решението, докато редакторът е отворен.
    withdraw_project_decision(
        корен, [донорски["event_id"]],
        actor="Тестов оператор", reason="сгрешено",
    )
    нов_указател = пр.Указател.от_проект(корен)

    хранилище = _Хранилище()
    диалог = EngineerDecisionDialog(
        row=dict(ред),
        shapes=хранилище.engine.shapes,
        engine_version=хранилище.engine.version,
        project_name="proekt",
        reuse_proposal=предложение,
        reuse_index=нов_указател,
    )
    try:
        диалог._accept_reuse_proposal()
        assert диалог._reuse_accepted is False
        assert "не е в сила" in диалог._reuse_refused_reason
        assert диалог.reuse_accept_button.isEnabled() is False
    finally:
        диалог.deleteLater()


def test_dialogat_pokazva_zashtoto_i_koe_pole_e_podkrepeno(app, tmp_path):
    from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog

    корен = _досие(tmp_path / "proekt")
    _решение(корен, handle="DONOR-UI")
    указател = пр.Указател.от_проект(корен)
    друга = {**САМОЛИЧНОСТ, "source_generation_id": "GEN-DRUGA"}
    ред = {
        "annotation_handle": "CEL-UI",
        "annotation_text": "350N12x300",
        "annotation_count": 350,
        "diameter_mm": 12.0,
        "declared_length_cm": 300.0,
        "batch_status": "НЯМА КАНДИДАТ",
        "canonical_segment_lengths_cm": [],
        "candidate_handles": [],
        "data_identity": друга,
    }
    предложение = указател.предложи(
        ред, самоличност=друга, активни_форми={"F001": 1}
    )
    хранилище = _Хранилище()
    диалог = EngineerDecisionDialog(
        row=dict(ред),
        shapes=хранилище.engine.shapes,
        engine_version=хранилище.engine.version,
        project_name="proekt",
        reuse_proposal=предложение,
        reuse_index=указател,
    )
    try:
        текст = диалог.reuse_label.text()
        # Петте неща, които операторът трябва да вижда.
        assert "Какво се предлага" in текст
        assert "Произход" in текст
        assert "Защо е намерен за сходен" in текст
        assert "Поле по поле" in текст
        assert "Остава за теб" in текст
        assert "НЕ се пренасят" in текст
        # Старото поле за предложения е скрито: едно място, не две.
        assert диалог.suggestion_frame.isHidden() is True
        # Преди изричното действие предложението НЕ е прието.
        assert диалог._reuse_accepted is False
        диалог._accept_reuse_proposal()
        assert диалог._reuse_accepted is True
        assert диалог.count_edit.text() == "350"
        assert диалог.length_edit.text() in {"300", "300.0"}
        assert диалог.reuse_accept_button.isEnabled() is False
    finally:
        диалог.deleteLater()


def _red_bez_geometriya() -> dict:
    return {
        "annotation_handle": "CEL-UI",
        "annotation_text": "350N12x300",
        "annotation_count": 350,
        "diameter_mm": 12.0,
        "declared_length_cm": 300.0,
        "batch_status": "НЯМА КАНДИДАТ",
        "canonical_segment_lengths_cm": [],
        "candidate_handles": [],
        "data_identity": {**САМОЛИЧНОСТ, "source_generation_id": "GEN-DRUGA"},
    }


def _dialog_s_predlozhenie(корен):
    from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog

    указател = пр.Указател.от_проект(корен)
    друга = {**САМОЛИЧНОСТ, "source_generation_id": "GEN-DRUGA"}
    ред = _red_bez_geometriya()
    предложение = указател.предложи(
        ред, самоличност=друга, активни_форми={"F001": 1}
    )
    assert предложение.състояние == пр.ПРИЛОЖИМО
    хранилище = _Хранилище()
    return EngineerDecisionDialog(
        row=dict(ред),
        shapes=хранилище.engine.shapes,
        engine_version=хранилище.engine.version,
        project_name="proekt",
        reuse_proposal=предложение,
        reuse_index=указател,
    )


def test_priemaneto_popalva_proslediva_belezhka_no_ne_i_potvarzhdenieto(
    app, tmp_path,
):
    """Празният лист отпада; преценката — не.

    При решение без DXF геометрия бележката И потвърждението са задължителни по
    правило, което е отпреди преизползването (`NO_RELIABLE_GEOMETRY`) и важи и
    когато операторът решава сам. Приемането на предложение НЕ го разхлабва: то
    само спестява писането на ръка, като предлага ПРОСЛЕДИМ ФАКТ.

    Текстът нарочно НЕ съдържа твърдение от името на оператора — няма
    „проверено", няма „вярно". Това остава негова дума.
    """

    корен = _досие(tmp_path / "proekt")
    донорски = _решение(корен, handle="DONOR-UI")
    диалог = _dialog_s_predlozhenie(корен)
    try:
        assert диалог.note_edit.toPlainText().strip() == ""
        диалог._accept_reuse_proposal()

        бележка = диалог.note_edit.toPlainText()
        # Проследимост: кое решение, кой чертеж, коя позиция, кой източник.
        assert донорски["event_id"][:8] in бележка
        assert "DONOR-UI" in бележка
        assert "F001" in бележка
        assert "размерите НЕ са на донора" in бележка
        # НЕ говори от името на оператора и НЕ копира бележката на донора.
        for дума in ("проверено", "вярно", "потвърждавам"):
            assert дума not in бележка.lower()

        # ПОТВЪРЖДЕНИЕТО ОСТАВА: без кутийката записът е забранен.
        assert диалог.confirm_check.isHidden() is False
        assert диалог.confirm_check.isChecked() is False
        assert диалог.save_button.isEnabled() is False
        диалог.confirm_check.setChecked(True)
        диалог._refresh()
        assert диалог.save_button.isEnabled() is True
    finally:
        диалог.deleteLater()


def test_vavedenata_belezhka_ne_se_prezapisva_ot_priemaneto(app, tmp_path):
    """Написаното от оператора е негово: приемането не го замества."""

    корен = _досие(tmp_path / "proekt")
    _решение(корен, handle="DONOR-UI")
    диалог = _dialog_s_predlozhenie(корен)
    try:
        диалог.note_edit.setPlainText("сверено с детайл 3 на лист 7")
        диалог._accept_reuse_proposal()
        assert диалог.note_edit.toPlainText() == "сверено с детайл 3 на лист 7"
    finally:
        диалог.deleteLater()


def test_neprilozhimoto_predlozhenie_ne_se_predlaga_za_priemane(app, tmp_path):
    from lion_rebar.ui.engineer_decision_dialog import EngineerDecisionDialog

    корен = _досие(tmp_path / "proekt")
    товар = {
        "annotation_handle": "DONOR-F007",
        "annotation_text": "2170N10x47",
        "action_class": "OVERRIDE",
        "reason_code": "NO_GEOMETRY",
        "reason": "тест",
        "note": "вярно",
        "actor": "Оператор",
        "data_identity": САМОЛИЧНОСТ,
        "original": {
            "count": 2170,
            "diameter_mm": 10.0,
            "declared_length_cm": 47.0,
            "shape_code": "F007",
            "segment_lengths_cm": [],
            "geometry_handles": [],
        },
        "resolved": {
            "count": 2170,
            "diameter_mm": 10.0,
            "production_length_cm": 47.0,
            "shape_code": "F007",
            "shape_name": "S / C-ОБРАЗНА ФОРМА",
            "shape_revision": 1,
            "segment_lengths_cm": [13.0, 21.0, 13.0],
            "turn_angles_deg": [],
        },
        "geometry": {"handles": []},
    }
    плик = append_project_decision_event(корен, "ENGINEER_RESOLVED", товар)
    mark_project_decision_delivered(корен, плик["event_id"], session_id="S-UI")
    указател = пр.Указател.от_проект(корен)
    друга = {**САМОЛИЧНОСТ, "source_generation_id": "GEN-DRUGA"}
    ред = {
        "annotation_handle": "CEL-F007",
        "annotation_text": "2170N10x47",
        "annotation_count": 2170,
        "diameter_mm": 10.0,
        "declared_length_cm": 47.0,
        "batch_status": "НЯМА КАНДИДАТ",
        "canonical_segment_lengths_cm": [],
        "candidate_handles": [],
        "data_identity": друга,
    }
    предложение = указател.предложи(
        ред, самоличност=друга, активни_форми={"F007": 1}
    )
    assert предложение.състояние == пр.САМО_ФОРМА
    хранилище = _Хранилище()
    диалог = EngineerDecisionDialog(
        row=dict(ред),
        shapes=хранилище.engine.shapes,
        engine_version=хранилище.engine.version,
        project_name="proekt",
        reuse_proposal=предложение,
        reuse_index=указател,
    )
    try:
        # Видимо е, но БЕЗ бутон за приемане.
        assert диалог.reuse_accept_button.isHidden() is True
        текст = диалог.reuse_label.text()
        # Донорските размери се показват САМО в изречението „не се пренасят".
        assert "Размерите на донора (13/21/13) НЕ се пренасят." in текст
        assert "САМО_ФОРМА" in текст
    finally:
        диалог.deleteLater()
