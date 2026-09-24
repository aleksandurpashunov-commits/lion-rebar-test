"""Диалозите за одобрение предлагат това, което програмата вече знае.

Собственик, 22.09.2026: „трябва ли да пиша всеки път тези текстове“.

Името на одобрилия се иска на ВСЕКИ диалог — по веднъж за всяка одобрена форма
и пак при публикуването — а регистърът го знае от предишния път. Бележката при
публикуване описва какво добавя черновата, а това е изчислимо.

КОНТРОЛНАТА ФРАЗА НЕ СЕ ПРЕДЛАГА и този файл го пази: тя е разликата между
„записах чернова“ и „смених производствения engine“.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.engine.repository import EngineRepository


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _форма(code: str, name: str, *, тестове: int = 0, родена: bool = False) -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=name,
        state="LOCKED",
        locked=True,
        active=True,
        segments=[SegmentDefinition(index=1, length_code="L1")],
        recognition={"born_from_case": True} if родена else {},
        tests=[{"test_id": f"{code}-{n}"} for n in range(тестове)],
    )


def _регистър(tmp_path: Path) -> EngineRepository:
    repo = EngineRepository(tmp_path / "engine_registry")
    repo.initialize()
    return repo


def test_последният_одобрил_идва_от_показалеца(tmp_path: Path):
    repo = _регистър(tmp_path)
    показалец = json.loads(repo.current_file.read_text(encoding="utf-8"))
    показалец["activated_by"] = "Александър Пашунов"
    repo.current_file.write_text(
        json.dumps(показалец, ensure_ascii=False), encoding="utf-8"
    )

    assert repo.last_approver() == "Александър Пашунов"


def test_последният_одобрил_пада_на_одитната_следа(tmp_path: Path):
    """Показалецът може да няма име — одитът пази всяко действие."""

    repo = _регистър(tmp_path)
    показалец = json.loads(repo.current_file.read_text(encoding="utf-8"))
    показалец.pop("activated_by", None)
    repo.current_file.write_text(
        json.dumps(показалец, ensure_ascii=False), encoding="utf-8"
    )
    with repo.audit_file.open("a", encoding="utf-8") as поток:
        поток.write(json.dumps({"action": "SAVE_DRAFT"}, ensure_ascii=False) + "\n")
        поток.write(
            json.dumps({"action": "PUBLISH_ENGINE", "by": "Иван"}, ensure_ascii=False)
            + "\n"
        )
        поток.write(json.dumps({"action": "SAVE_DRAFT"}, ensure_ascii=False) + "\n")

    assert repo.last_approver() == "Иван"


def test_машинно_име_не_се_предлага(tmp_path: Path):
    """Пресен регистър е подписан от ``SYSTEM`` — bootstrap-ът, не човек.

    Полето „Одобрил“ е запис КОЙ е решил. Предложи ли се машинно име, едно
    натискане на Enter подписва решение с него.
    """

    repo = _регистър(tmp_path)
    показалец = json.loads(repo.current_file.read_text(encoding="utf-8"))
    assert показалец["activated_by"] == "SYSTEM"

    assert repo.last_approver() == ""


def test_машинното_име_се_прескача_и_в_одита(tmp_path: Path):
    """Пакетираният engine пише „LION REBAR MANAGED RELEASE“."""

    repo = _регистър(tmp_path)
    with repo.audit_file.open("a", encoding="utf-8") as поток:
        поток.write(
            json.dumps({"action": "PUBLISH_ENGINE", "by": "Александър"},
                       ensure_ascii=False) + "\n"
        )
        поток.write(
            json.dumps({"action": "INSTALL_BUNDLED",
                        "by": "LION REBAR MANAGED RELEASE"},
                       ensure_ascii=False) + "\n"
        )

    assert repo.last_approver() == "Александър"


def test_бележката_за_форма_казва_само_измеримото():
    from lion_rebar.ui.engine_studio import _shape_approval_note

    assert _shape_approval_note(_форма("F021", "Кобилица-1", тестове=1, родена=True)) == (
        "Родена от случай в чертежа; носи един контролен тест от самите случаи."
    )
    assert _shape_approval_note(_форма("F020", "Кобилица", тестове=5, родена=True)) == (
        "Родена от случай в чертежа; носи 5 контролни теста от самите случаи."
    )
    assert _shape_approval_note(_форма("F002", "Редукция", тестове=3)) == (
        "Носи 3 контролни теста."
    )
    assert _shape_approval_note(_форма("F003", "Без тестове")) == ""


def test_бележката_не_твърди_че_тестовете_минават():
    """Одобрението е на оператора; програмата не го прави вместо него."""

    from lion_rebar.ui.engine_studio import _shape_approval_note

    текст = _shape_approval_note(_форма("F021", "Кобилица-1", тестове=1, родена=True))
    for дума in ("минава", "минават", "успешн", "одобр", "верн"):
        assert дума not in текст.lower()


@pytest.mark.parametrize(
    "поле, стойност",
    [("default_by", "Александър"), ("default_note", "Добавена форма F021.")],
)
def test_диалогът_попълва_предложеното(поле, стойност):
    _app()
    from lion_rebar.ui.approval_dialogs import ApprovalDialog

    dialog = ApprovalDialog("Публикуване", "ОДОБРЯВАМ", **{поле: стойност})
    данни = dialog.data()
    assert (данни.by if поле == "default_by" else данни.note) == стойност


def test_контролната_фраза_НИКОГА_не_се_предлага():
    """Единственото поле, което не може да дойде от програмата."""

    _app()
    from lion_rebar.ui.approval_dialogs import ApprovalDialog

    dialog = ApprovalDialog(
        "Публикуване",
        "ОДОБРЯВАМ ПУБЛИКУВАНЕТО",
        default_by="Александър",
        default_note="Добавена форма F021.",
    )
    assert dialog.data().phrase == ""


def test_без_предложения_диалогът_е_празен_както_досега():
    _app()
    from lion_rebar.ui.approval_dialogs import ApprovalDialog

    данни = ApprovalDialog("Публикуване", "ОДОБРЯВАМ").data()
    assert (данни.by, данни.note, данни.phrase) == ("", "", "")
