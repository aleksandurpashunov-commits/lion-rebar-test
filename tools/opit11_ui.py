# -*- coding: utf-8 -*-
"""Операторските стъпки на Опит 11 през ИСТИНСКИЯ интерфейс.

Пуска се като ОТДЕЛЕН процес от ``tools/opit11.py``. Нищо тук не заобикаля
нормалния път:

* решението се взема в истинския ``ui.engineer_decision_dialog``;
* предложението се приема през истинския обработчик на бутона
  ``_accept_reuse_proposal``;
* записът минава през ``ui.main_window.MainWindow._record_review_event`` —
  същата функция, с която пише прозорецът;
* оттеглянето минава през ``MainWindow.withdraw_engineer_decision`` със
  скриптирани отговори на диалозите за избор и за мотив.

Всяко операторско взаимодействие се БРОИ и се записва поименно. Броят е
СКРИПТОВ и се отчита като скриптов — човешко време не се мери тук.

Снимки на диалога се записват в папката за доказателства.

Действия:
    resi      — операторът решава позицията САМ (режим без памет)
    priemi    — операторът приема предложението (режим с памет)
    otteglyay — операторът оттегля решение през менюто
    pogledni  — само поглежда предложението и го записва, без да решава
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QInputDialog,
    QMessageBox,
)

from lion_rebar.config.constants import APP_VERSION  # noqa: E402
from lion_rebar.export.excel_export import load_pinned_engine  # noqa: E402
from lion_rebar.project.manager import ProjectManager  # noqa: E402
from lion_rebar.review import decision_reuse as пр  # noqa: E402
from lion_rebar.session.decision_contract import (  # noqa: E402
    mark_project_decision_delivered,
)
from lion_rebar.engineer_decision import (  # noqa: E402
    GeometryTakeoverRequired,
    save_engineer_decision,
)
from lion_rebar.ui.engineer_decision_dialog import (  # noqa: E402
    EngineerDecisionDialog,
)
from lion_rebar.ui.main_window import MainWindow  # noqa: E402
from lion_rebar.ui.review_center import ReviewCenterDialog  # noqa: E402


class _Сесия:
    """Сесийната услуга, колкото ѝ трябва на записа. Няма сесийна база."""

    has_active_session = False
    session_id = "S-OPIT-11"

    def active_summary(self):
        return None

    def record_system_event(self, *_а, **_к) -> None:
        return None

    def record_project_event(self, *_а, **_к) -> None:
        return None


class _Хранилище:
    """Хранилището, колкото му трябва на прозореца: ЗАКЛЮЧЕНИЯТ engine.

    Проектът пази своя engine snapshot; ``load_pinned_engine`` го чете оттам,
    точно както прави и безинтерфейсният път. Така диалогът работи със същите
    форми, с които е работил анализът.
    """

    def __init__(self, проект) -> None:
        self._engine = load_pinned_engine(проект, None)

    def load_current(self):
        return self._engine


def _прозорец(проект) -> MainWindow:
    прозорец = MainWindow(
        _Хранилище(проект),
        logging.getLogger("opit11"),
        session_service=_Сесия(),
    )
    прозорец.project = проект
    прозорец.operator_name = "Александър Пашунов"
    return прозорец


def _ред(отчет: pathlib.Path, надпис: str) -> dict:
    данни = json.loads(отчет.read_text(encoding="utf-8-sig"))
    за_надписа = [
        ред
        for ред in данни.get("rows") or []
        if isinstance(ред, dict)
        and str(ред.get("annotation_text") or "").strip().upper()
        == надпис.strip().upper()
    ]
    if not за_надписа:
        raise SystemExit("няма ред с надпис %s в %s" % (надпис, отчет))
    if len(за_надписа) > 1:
        raise SystemExit(
            "надписът %s е на %d реда — опитът иска еднозначен"
            % (надпис, len(за_надписа))
        )
    return за_надписа[0]


def _снимка(джаджа, път: pathlib.Path) -> str:
    път.parent.mkdir(parents=True, exist_ok=True)
    джаджа.resize(джаджа.sizeHint())
    картина = джаджа.grab()
    картина.save(str(път), "PNG")
    return път.name


def _попълни_ръчно(
    диалог,
    ред: dict,
    действия: list[str],
    *,
    форма: str = "F001",
    участъци: "list[float] | None" = None,
    причина: str = "NO_GEOMETRY",
    без_геометрия: bool = True,
) -> None:
    """Операторът въвежда всичко сам — това е режимът БЕЗ предложение.

    ``участъци`` са числата, които ОПЕРАТОРЪТ въвежда. Без тях се пълни
    обявената дължина във всяко поле (вярно само за прав прът с едно рамо).
    """

    индекс = диалог.shape_combo.findData(форма)
    if индекс < 0:
        raise SystemExit("формата %s не е активна в заключения Engine" % форма)
    диалог.shape_combo.setCurrentIndex(индекс)
    действия.append("избор на форма %s от менюто" % форма)
    диалог.count_edit.setText(str(int(ред["annotation_count"])))
    действия.append("въвеждане на бройка")
    диалог.diameter_edit.setText(str(float(ред["diameter_mm"])))
    действия.append("въвеждане на диаметър")
    диалог.length_edit.setText(str(float(ред["declared_length_cm"])))
    действия.append("въвеждане на обща дължина")
    полета = участъковите_полета(диалог)
    стойности = list(участъци or [])
    по_ключ = dict(getattr(диалог, "_segment_indexes_by_key", {}) or {})
    общо_участъци = sum(len(места) for места in по_ключ.values()) or len(полета)
    if стойности and len(стойности) != общо_участъци:
        raise SystemExit(
            "формата %s има %d участъка, подадени са %d"
            % (форма, общо_участъци, len(стойности))
        )
    for ключ, поле in полета:
        места = по_ключ.get(ключ) or ()
        if стойности and места:
            избрани = [стойности[място] for място in места if място < len(стойности)]
            if len(set(избрани)) > 1:
                raise SystemExit(
                    "свързаните участъци %s на %s искат различни числа %s — "
                    "симетричната форма не може да ги изрази"
                    % (ключ, форма, избрани)
                )
            число = избрани[0]
        elif стойности:
            число = стойности[0]
        else:
            число = float(ред["declared_length_cm"])
        поле.setText(str(число))
        действия.append("въвеждане на участък %s" % ключ)
    индекс_причина = диалог.reason_combo.findData(причина)
    if индекс_причина >= 0:
        диалог.reason_combo.setCurrentIndex(индекс_причина)
        действия.append("избор на причина %s" % причина)
    if без_геометрия and диалог.use_candidate_check.isChecked():
        диалог.use_candidate_check.setChecked(False)
        действия.append("изключване на кандидат-геометрията")


def участъковите_полета(диалог):
    return sorted(диалог.segment_edits.items())


def _бележка(диалог, текст: str, действия: list[str]) -> None:
    диалог.note_toggle_button.setChecked(True)
    диалог.note_edit.setPlainText(текст)
    действия.append("въвеждане на бележка")


def _запиши(прозорец, товар: dict, ред: dict, отчет_път, записано: dict) -> dict:
    """ТРИТЕ производствени стъпки, в реда, в който ги прави прозорецът.

    1. ``save_engineer_decision`` — авторитетният писач на решението;
    2. ``ReviewCenterDialog.mark_engineer_resolved`` — състоянието, което
       портата и Финалният контрол четат;
    3. ``_record_review_event`` — журналът и outbox-ът.

    Първият прогон на опита правеше САМО третото и затова решението не
    стигаше до портата: позицията оставаше спряна и „спестената работа" не
    можеше да се измери.
    """

    корен = pathlib.Path(прозорец.project.root)
    annotation_handle = str((товар.get("annotation") or {}).get("handle") or "")
    try:
        записано["път на решението"] = str(save_engineer_decision(корен, товар))
    except GeometryTakeoverRequired as спор:
        # Прозорецът пита оператора; тук отнемането на чужда геометрия НЕ се
        # разрешава мълчаливо — опитът не бива да взима това решение вместо него.
        записано["отказ"] = "геометрията е заета: %s" % спор
        return {}
    състояние = корен / "working" / "review_center_state.json"
    преглед = ReviewCenterDialog(
        отчет_път,
        state_path=състояние,
        project_root=корен,
        operator_name=прозорец.operator_name,
    )
    try:
        записано["присъда на Review Center"] = преглед.mark_engineer_resolved(
            annotation_handle, товар
        )
    finally:
        преглед.close()
        преглед.deleteLater()
    прозорец._record_review_event(
        "ENGINEER_RESOLVED",
        {
            "annotation_handle": annotation_handle,
            "annotation_text": (товар.get("annotation") or {}).get("text"),
            "resolved_annotation_text": (
                товар.get("annotation") or {}
            ).get("resolved_text"),
            "action_class": товар.get("action_class"),
            "reason_code": товар.get("reason_code"),
            "reason": товар.get("reason"),
            "note": товар.get("note"),
            "row_context": товар.get("row_context"),
            "original": товар.get("original"),
            "resolved": товар.get("resolved"),
            "geometry": товар.get("geometry"),
            "diagnostic_problem": товар.get("diagnostic_problem"),
            "decision_suggestion": товар.get("decision_suggestion"),
            "preizpolzvane": товар.get("preizpolzvane"),
        },
    )
    журнал = pathlib.Path(прозорец.project.root) / "working" / (
        "decision_events_v2.jsonl"
    )
    последен = {}
    for линия in журнал.read_text(encoding="utf-8-sig").splitlines():
        try:
            плик = json.loads(линия)
        except ValueError:
            continue
        if isinstance(плик, dict):
            последен = плик
    # Доставката е ръкостискането със сесийната база. Безинтерфейсният опит
    # няма такава, затова се отбелязва изрично — както прави и работният път
    # при успешна доставка.
    if последен.get("event_id"):
        mark_project_decision_delivered(
            pathlib.Path(прозорец.project.root),
            последен["event_id"],
            session_id="S-OPIT-11",
        )
    return последен


def _порта(прозорец, отчет_път: pathlib.Path) -> dict:
    """``build_batch_export_context`` върху СЪЩИЯ анализ, след решението.

    Това е мярката, която протоколът назовава. Нов анализ не се пуска:
    операторът решава и пуска портата върху отчета, който вече има.
    """

    from lion_rebar.export.batch_auto_output import (
        BatchAutoOutputError,
        build_batch_export_context,
    )
    from lion_rebar.export.frame_decisions import load_frame_decisions
    from lion_rebar.project.approval_origin import is_manual_approval
    from lion_rebar.project.data_identity import current_identity
    from lion_rebar.project.integrity import _dxf_records

    корен = pathlib.Path(прозорец.project.root)
    engine = прозорец.engine_repository.load_current()
    самоличност = current_identity(прозорец.project)
    try:
        извадени = json.loads(
            (корен / "working" / "extracted_entities.json").read_text(
                encoding="utf-8-sig")
        )
    except (OSError, ValueError) as грешка:
        return {"ГРЕШКА": "извадените обекти не се четат: %s" % грешка}
    обекти = _dxf_records(извадени)
    try:
        връзки = json.loads(
            (корен / "working" / "approved_geometry_links.json").read_text(
                encoding="utf-8-sig")
        )
    except (OSError, ValueError):
        връзки = {}
    # ``links`` е СПИСЪК, не речник — същото четене като
    # ``MainWindow._read_all_approved_geometry_links``.
    списък = връзки.get("links") if isinstance(връзки, dict) else []
    ръчни = {}
    for връзка in списък if isinstance(списък, list) else []:
        if not isinstance(връзка, dict):
            continue
        handle = str(връзка.get("annotation_handle") or "")
        if handle and is_manual_approval(връзка):
            ръчни[handle] = dict(връзка)
    try:
        контекст = build_batch_export_context(
            отчет_път,
            entities=обекти,
            engine=engine,
            approved_links=ръчни,
            frame_decisions=load_frame_decisions(корен, самоличност),
        )
    except BatchAutoOutputError as грешка:
        return {"ГРЕШКА": str(грешка), "ръчни връзки": len(ръчни)}
    спрени = [
        {
            "handle": спряна.annotation_handle,
            "надпис": спряна.annotation_text,
            "причини": list(спряна.reason_codes),
        }
        for спряна in контекст.blockers
    ]
    решената = [
        с for с in спрени
        if str(с["надпис"]).strip().upper() == "350N12X300"
    ]
    return {
        "actionable": контекст.actionable_count,
        "approved": len(контекст.positions),
        "blocked": len(контекст.blockers),
        "complete": контекст.complete,
        "ръчни връзки": len(ръчни),
        "обучаващата позиция още ли е спряна": bool(решената),
        "спрени (първите 40)": спрени[:40],
    }


def главно() -> int:
    парсер = argparse.ArgumentParser(description=__doc__)
    парсер.add_argument("--proekt", required=True)
    парсер.add_argument("--otchet", default="")
    парсер.add_argument("--nadpis", default="")
    парсер.add_argument("--deystvie", required=True)
    парсер.add_argument("--izhod", required=True)
    парсер.add_argument("--etiket", default="")
    парсер.add_argument("--otteglyay-handle", default="")
    парсер.add_argument("--forma", default="F001")
    парсер.add_argument("--uchastatsi", default="",
                        help="участъците, които операторът въвежда, с запетая")
    парсер.add_argument("--prichina", default="NO_GEOMETRY")
    парсер.add_argument("--s-geometriya", action="store_true",
                        help="решението ПАЗИ кандидат-геометрията")
    аргументи = парсер.parse_args()

    изход = pathlib.Path(аргументи.izhod)
    изход.mkdir(parents=True, exist_ok=True)
    етикет = аргументи.etiket or аргументи.deystvie
    приложение = QApplication.instance() or QApplication([])
    корен = pathlib.Path(аргументи.proekt)
    мениджър = ProjectManager()
    проект = мениджър.open_project(корен)
    отчет: dict = {
        "схема": "opit11-ui/v1",
        "действие": аргументи.deystvie,
        "етикет": етикет,
        "код": APP_VERSION,
        "проект": str(корен),
        "операторски действия": [],
        "снимки": [],
    }
    действия: list[str] = отчет["операторски действия"]
    прозорец = _прозорец(проект)
    try:
        if аргументи.deystvie == "otteglyay":
            избрани: list[str] = []
            съобщения: list[str] = []
            цел = аргументи.otteglyay_handle

            def _избор(_р, _з, _т, елементи, *_а, **_к):
                избрани.extend(елементи)
                действия.append("избор на решение за оттегляне от списъка")
                подходящи = [р for р in елементи if цел and цел in р]
                if not подходящи:
                    raise SystemExit(
                        "няма решение за позиция %s в списъка: %s" % (цел, елементи)
                    )
                return подходящи[0], True

            def _мотив(*_а, **_к):
                действия.append("въвеждане на мотив за оттеглянето")
                return ("обучаващото решение се оказа грешно — опит 11", True)

            QInputDialog.getItem = staticmethod(_избор)
            QInputDialog.getText = staticmethod(_мотив)
            QMessageBox.information = staticmethod(
                lambda *а, **к: съобщения.append(str(а[2]))
            )
            QMessageBox.warning = staticmethod(
                lambda *а, **к: съобщения.append("ПРЕДУПРЕЖДЕНИЕ " + str(а[2]))
            )
            прозорец.withdraw_engineer_decision()
            отчет["избор"] = избрани
            отчет["съобщения"] = съобщения
            отчет["зависими за повторна проверка"] = (
                пр.зависими_за_повторна_проверка(корен)
            )
            (изход / ("UI_%s.json" % етикет)).write_text(
                json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print("ГОТОВО", етикет)
            return 0

        ред = _ред(pathlib.Path(аргументи.otchet), аргументи.nadpis)
        отчет["ред"] = {
            ключ: ред.get(ключ)
            for ключ in (
                "annotation_handle", "annotation_text", "annotation_count",
                "diameter_mm", "declared_length_cm", "batch_status",
                "master_shape_code", "canonical_segment_lengths_cm",
                "candidate_handles", "matcher_blocker_codes",
            )
        }
        engine = прозорец.engine_repository.load_current()
        указател, предложение = прозорец._reuse_proposal_for_row(
            ред, engine=engine
        )
        отчет["предложение"] = (
            предложение.речник() if предложение is not None else None
        )
        редактор = EngineerDecisionDialog(
            row=dict(ред),
            shapes=engine.shapes,
            engine_version=engine.version,
            project_name=проект.name,
            project_version=str(getattr(проект, "version", "") or ""),
            reuse_proposal=предложение,
            reuse_index=указател,
        )
        действия.append("отваряне на редактора за позицията")
        отчет["снимки"].append(
            _снимка(редактор, изход / ("UI_%s_01_otvoren.png" % етикет))
        )
        отчет["текст на предложението"] = (
            редактор.reuse_label.text() if предложение is not None else ""
        )
        # Бутонът живее В полето за предложението. Скрито поле значи скрит
        # бутон, макар самият бутон да не носи ``isHidden``.
        отчет["показан бутон за приемане"] = (
            not редактор.reuse_frame.isHidden()
            and not редактор.reuse_accept_button.isHidden()
            and редактор.reuse_accept_button.isEnabled()
        )
        отчет["показано поле за преизползване"] = (
            not редактор.reuse_frame.isHidden()
        )
        if аргументи.deystvie == "pogledni":
            (изход / ("UI_%s.json" % етикет)).write_text(
                json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            редактор.deleteLater()
            print("ГОТОВО", етикет)
            return 0

        if аргументи.deystvie == "priemi":
            if предложение is None or not предложение.може_да_се_приеме:
                отчет["ГРЕШКА"] = "няма приложимо предложение за приемане"
                (изход / ("UI_%s.json" % етикет)).write_text(
                    json.dumps(отчет, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
                return 3
            редактор._accept_reuse_proposal()
            действия.append("натискане на „Приеми предложението“")
            отчет["приемането мина"] = bool(редактор._reuse_accepted)
            отчет["отказ при приемането"] = редактор._reuse_refused_reason
            _бележка(
                редактор,
                "приетото предложение е проверено срещу този чертеж — опит 11",
                действия,
            )
        elif аргументи.deystvie == "resi":
            участъци = [
                float(част.strip().replace(",", "."))
                for част in аргументи.uchastatsi.split(";")
                if част.strip()
            ]
            _попълни_ръчно(
                редактор, ред, действия,
                форма=аргументи.forma.strip().upper(),
                участъци=участъци or None,
                причина=аргументи.prichina.strip().upper(),
                без_геометрия=not аргументи.s_geometriya,
            )
            _бележка(
                редактор,
                "позицията е решена от оператора без предложение — опит 11",
                действия,
            )
        else:
            raise SystemExit("непознато действие: %s" % аргументи.deystvie)

        редактор._refresh()
        # Истинският диалог иска ИЗРИЧНО потвърждение при решение без
        # геометрия: докато кутийката не е сложена, бутонът „Запиши" стои
        # изключен. Това е операторско действие и се брои като такова.
        if (not редактор.confirm_check.isHidden()) and not редактор.confirm_check.isChecked():
            редактор.confirm_check.setChecked(True)
            действия.append("слагане на потвърждението за решение без геометрия")
            редактор._refresh()
        отчет["снимки"].append(
            _снимка(редактор, изход / ("UI_%s_02_popalnen.png" % етикет))
        )
        отчет["бутонът за запис е активен"] = записът_е_разрешен(редактор)
        редактор._accept()
        действия.append("натискане на „Запиши“")
        товар = редактор.result_payload
        if товар is None:
            отчет["ГРЕШКА"] = "редакторът не върна решение (валидацията отказа)"
            (изход / ("UI_%s.json" % етикет)).write_text(
                json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            return 4
        отчет["решение"] = {
            "action_class": товар.get("action_class"),
            "reason_code": товар.get("reason_code"),
            "resolved": товар.get("resolved"),
            "preizpolzvane": товар.get("preizpolzvane"),
        }
        записано: dict = {}
        плик = _запиши(
            прозорец, товар, ред,
            pathlib.Path(аргументи.otchet), записано,
        )
        отчет["производствен запис"] = записано
        отчет["записан плик"] = {
            "event_id": плик.get("event_id"),
            "event_type": плик.get("event_type"),
            "decision_revision": плик.get("decision_revision"),
            "supersedes_event_ids": плик.get("supersedes_event_ids"),
            "actor": (плик.get("actor") or {}).get("recorded_as"),
            "decision_kind": (плик.get("decision_kind") or {}).get("kind"),
        }
        отчет["брой операторски действия (СКРИПТОВИ)"] = len(действия)
        отчет["порта върху СЪЩИЯ анализ"] = _порта(
            прозорец, pathlib.Path(аргументи.otchet)
        )
        редактор.deleteLater()
        (изход / ("UI_%s.json" % етикет)).write_text(
            json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("ГОТОВО", етикет, "действия:", len(действия))
        return 0
    finally:
        прозорец.close()
        прозорец.deleteLater()
        приложение.processEvents()


def записът_е_разрешен(редактор) -> bool:
    бутон = getattr(редактор, "save_button", None)
    return bool(бутон is not None and бутон.isEnabled())


if __name__ == "__main__":
    raise SystemExit(главно())
