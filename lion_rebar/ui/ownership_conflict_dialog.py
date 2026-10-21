# -*- coding: utf-8 -*-
"""Операторски път за спорно владение на геометрия (§ 6.2).

ЗАЩО СЪЩЕСТВУВА. ``export/ownership_decisions.py`` работи и е покрит с тестове,
но backend без операторски път не е завършена функция — същият урок като при
``unknown_frame_dialog``. Измерено на замразената база от 29.09.2026: **52
позиции в 21 клъстера** стояха спрени само защото нищо в програмата не
позволяваше на оператора да каже чия е спорната геометрия.

КАКВО ПОКАЗВА. Един ред на СПОР, не на позиция: спорът е по свързани компоненти
(А спори с Б, Б спори с В — един спор), а решението обхваща целия състав. Всеки
член се вижда поименно с надписа, броя и дължината си, за да бъде проверен
поотделно, преди да се потвърди групата с едно действие.

КАКВО НЕ ПРАВИ. Не предлага „правилния" собственик. Коя е истинската позиция е
инженерен въпрос за чертежа; програмата знае само че два надписа сочат един
обект. Празен избор значи „не решавай сега" и спорът остава.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lion_rebar.export.ownership_decisions import (
    ВИД_СПОДЕЛЕНА,
    ВИД_СОБСТВЕНИК,
    OwnershipDecisionError,
    geometry_key,
    record_ownership_decision,
)
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.session.operator_identity import НЕЗАЯВЕН


def conflict_clusters(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Споровете от отчета: по един запис на КЛЪСТЕР, с членовете поименно.

    Чете полетата, които анализът вече е записал на реда
    (``ownership_conflict_key`` и ``ownership_conflict_members``) — тук нищо не
    се пресмята наново, за да не се разминат двете гледни точки.
    """

    по_ключ: dict[tuple[str, ...], dict[str, Any]] = {}
    редове_по_handle = {
        str(ред.get("annotation_handle") or ""): ред for ред in rows
    }
    for ред in rows:
        if not bool(ред.get("global_conflict")):
            continue
        ключ = geometry_key(ред.get("ownership_conflict_key") or ())
        членове = tuple(
            sorted(
                {
                    str(стойност)
                    for стойност in (ред.get("ownership_conflict_members") or ())
                    if str(стойност or "").strip()
                }
            )
        )
        if not ключ or not членове:
            continue
        по_ключ.setdefault(
            ключ,
            {
                "geometry_key": ключ,
                "member_handles": членове,
                "members": [
                    {
                        "annotation_handle": handle,
                        "annotation_text": str(
                            (редове_по_handle.get(handle) or {}).get("annotation_text")
                            or ""
                        ),
                        "annotation_count": (
                            редове_по_handle.get(handle) or {}
                        ).get("annotation_count"),
                        "declared_length_cm": (
                            редове_по_handle.get(handle) or {}
                        ).get("declared_length_cm"),
                    }
                    for handle in членове
                ],
            },
        )
    return [по_ключ[ключ] for ключ in sorted(по_ключ)]


class OwnershipConflictDialog(QDialog):
    """Позволява на оператора да реши чия е спорната геометрия."""

    def __init__(
        self,
        clusters: Sequence[Mapping[str, Any]],
        project_root: Path,
        identity: DataIdentity,
        operator: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("LION REBAR — Спорно владение на геометрия")
        self.resize(1040, 460)
        self._clusters = [dict(клъстер) for клъстер in clusters]
        self._project_root = Path(project_root)
        self._identity = identity
        self._operator = str(operator or "").strip() or НЕЗАЯВЕН
        self._choice_boxes: list[QComboBox] = []
        self._note_edits: list[QLineEdit] = []
        self.decisions_made = 0

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Един начертан обект е първи кандидат на няколко надписа. Еднаквият "
            "надпис НЕ доказва еднакво решение, нито отделно количество — затова "
            "програмата спира и пита.\n\n"
            "За всеки спор избери КОЙ надпис владее геометрията, или заяви че тя "
            "е ОБЩА по замисъл (един начертан обект, няколко реда в описа).\n\n"
            "При избран собственик останалите надписи НЕ изнасят тази геометрия "
            "и остават за собствено решение — те са отделени, не решени. "
            "Решението се записва с автор, време и одитна следа и може да бъде "
            "оттеглено."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(self._clusters), 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Спорна геометрия", "Надписи в спора", "Решение", "Обосновка"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setDefaultSectionSize(52)
        for индекс, клъстер in enumerate(self._clusters):
            геометрия = QTableWidgetItem(", ".join(клъстер["geometry_key"]))
            геометрия.setFlags(геометрия.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(индекс, 0, геометрия)

            описание = QTableWidgetItem(_членове_текст(клъстер["members"]))
            описание.setFlags(описание.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(индекс, 1, описание)

            избор = QComboBox(self.table)
            избор.addItem("— не решавай сега —", ("", ""))
            for член in клъстер["members"]:
                избор.addItem(
                    "Владее го: %s" % _кратко(член),
                    (ВИД_СОБСТВЕНИК, член["annotation_handle"]),
                )
            избор.addItem("Обща геометрия за всички", (ВИД_СПОДЕЛЕНА, ""))
            self.table.setCellWidget(индекс, 2, избор)
            self._choice_boxes.append(избор)

            бележка = QLineEdit(self.table)
            бележка.setPlaceholderText("Задължително при взето решение")
            self.table.setCellWidget(индекс, 3, бележка)
            self._note_edits.append(бележка)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Запиши решенията"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Откажи")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _conflict_members(self) -> dict[tuple[str, ...], tuple[str, ...]]:
        return {
            tuple(клъстер["geometry_key"]): tuple(клъстер["member_handles"])
            for клъстер in self._clusters
        }

    def _save(self) -> None:
        чакащи: list[tuple[dict, str, str, str]] = []
        for клъстер, избор, бележка in zip(
            self._clusters, self._choice_boxes, self._note_edits
        ):
            вид, собственик = избор.currentData() or ("", "")
            if not вид:
                continue
            текст = бележка.text().strip()
            if not текст:
                QMessageBox.warning(
                    self,
                    "Липсва обосновка",
                    "Спорът за геометрия %s има избрано решение, но няма "
                    "обосновка.\n\nРешение за владение изисква мотив."
                    % ", ".join(клъстер["geometry_key"]),
                )
                return
            чакащи.append((клъстер, вид, собственик, текст))

        if not чакащи:
            QMessageBox.information(
                self, "Няма решения", "Не е взето решение за нито един спор."
            )
            return

        записани = 0
        try:
            for клъстер, вид, собственик, текст in чакащи:
                record_ownership_decision(
                    self._project_root,
                    kind=вид,
                    key=клъстер["geometry_key"],
                    owner_annotation_handle=собственик,
                    member_handles=клъстер["member_handles"],
                    decided_by=self._operator,
                    note=текст,
                    identity=self._identity,
                    conflict_members=self._conflict_members(),
                )
                записани += 1
        except OwnershipDecisionError as error:
            QMessageBox.warning(self, "Решението не беше записано", str(error))
            self.decisions_made = записани
            return
        except Exception as error:  # noqa: BLE001 - помощен път
            QMessageBox.critical(
                self, "Грешка", "Решенията не бяха записани докрай.\n\n%s" % error
            )
            self.decisions_made = записани
            return

        self.decisions_made = записани
        QMessageBox.information(
            self,
            "Записано",
            "Записани решения за владение: %d.\n\nПусни пакетния анализ наново, "
            "за да влязат в сила." % записани,
        )
        self.accept()


def _кратко(член: Mapping[str, Any]) -> str:
    части = [str(член.get("annotation_text") or член.get("annotation_handle") or "?")]
    брой = член.get("annotation_count")
    дължина = член.get("declared_length_cm")
    ако = []
    if брой not in (None, ""):
        ако.append("бр. %s" % брой)
    if дължина not in (None, ""):
        ако.append("L=%s" % дължина)
    if ако:
        части.append("(%s)" % ", ".join(ако))
    части.append("[%s]" % член.get("annotation_handle"))
    return " ".join(части)


def _членове_текст(членове: Sequence[Mapping[str, Any]]) -> str:
    return " · ".join(_кратко(член) for член in членове)
