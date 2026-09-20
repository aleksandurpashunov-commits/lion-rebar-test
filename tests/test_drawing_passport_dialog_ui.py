"""Екранът на паспорта показва доказателството и не решава вместо оператора."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QMessageBox

from lion_rebar.dxf.convention_profile import PROPOSABLE_ROLES
from lion_rebar.project.convention_profile_decisions import PROFILE_NAME
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.ui.drawing_passport_dialog import DrawingPassportDialog


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


ПРЕДЛОЖЕНИЕ = {
    "schema": "lion-rebar-convention-profile/v1",
    "proposer_version": "1.0",
    "proposals": [
        {
            "target_kind": "LAYER",
            "layer": "Колони",
            "block_name": "",
            "role": "REBAR_GEOMETRY",
            "confidence": 0.95,
            "evidence": "101 надписа имат до себе си обект със своята дължина",
            "sample_handles": ["1A2B", "3C4D", "5E6F"],
            "counts": {"local_length_matches": 101, "control_matches": 2},
            "details": {},
        },
        {
            "target_kind": "BLOCK",
            "layer": "ARM_TXT",
            "block_name": "ARM_N",
            "role": "POSITION_LABEL_BLOCK",
            "confidence": 0.9,
            "evidence": "317 вставки с един и същ набор тагове",
            "sample_handles": ["B915"],
            "counts": {},
            "details": {"diameter_tag": "FI"},
        },
    ],
    "refusals": [
        {
            "target_kind": "LAYER",
            "layer": "Plumbing",
            "block_name": "",
            "role": "REBAR_GEOMETRY",
            "reason": "само 2 надписа намират дължината си тук — под 5",
            "counts": {},
        }
    ],
}


def _identity() -> DataIdentity:
    return DataIdentity(
        project_id="проект-1",
        source_sha256="abc123",
        source_generation_id="генерация-1",
        analysis_run_id="анализ-1",
    )


def _диалог(tmp_path: Path) -> DrawingPassportDialog:
    return DrawingPassportDialog(ПРЕДЛОЖЕНИЕ, tmp_path, _identity(), "Александър")


def test_показва_предложенията_с_доказателство_и_примери(app, tmp_path: Path) -> None:
    диалог = _диалог(tmp_path)

    assert диалог.table.rowCount() == 2
    assert диалог.refusal_table.rowCount() == 1
    assert диалог.table.item(0, 0).text() == "Колони"
    assert "армировъчна геометрия" in диалог.table.item(0, 1).text()
    доказателство = диалог.table.item(0, 2).text()
    assert "101 надписа" in доказателство
    assert "1A2B" in доказателство
    assert диалог.table.item(1, 0).text() == "ARM_TXT / блок ARM_N"
    assert "под 5" in диалог.refusal_table.item(0, 2).text()


def test_по_подразбиране_нищо_не_е_решено(app, tmp_path: Path) -> None:
    """Екран, който решава вместо оператора, е по-лош от никакъв екран."""

    диалог = _диалог(tmp_path)

    assert диалог.pending_decisions() == []
    for row in range(диалог.table.rowCount()):
        combo = диалог.table.cellWidget(row, 3)
        assert combo.currentIndex() == 0
        assert "не решавай" in combo.currentText()
        # всяка роля е избираема, плюс отхвърляне
        текстове = [combo.itemText(индекс) for индекс in range(combo.count())]
        assert any("Отхвърлям" in текст for текст in текстове)
        assert len(текстове) == len(PROPOSABLE_ROLES) + 2


def test_избраното_става_решение(app, tmp_path: Path) -> None:
    диалог = _диалог(tmp_path)
    диалог.table.cellWidget(0, 3).setCurrentIndex(1)  # приемам
    диалог.table.cellWidget(1, 3).setCurrentIndex(диалог.table.cellWidget(1, 3).count() - 1)
    диалог.table.cellWidget(1, 4).setText("това не е надпис, а котата на оста")

    решения = диалог.pending_decisions()

    assert len(решения) == 2
    assert решения[0]["verdict"] == "ACCEPTED"
    assert решения[0]["role"] == "REBAR_GEOMETRY"
    assert решения[0]["proposed_role"] == "REBAR_GEOMETRY"
    assert решения[1]["verdict"] == "REJECTED"
    assert решения[1]["role"] == ""
    assert решения[1]["note"].startswith("това не е надпис")


def test_записва_профила_и_казва_че_още_не_влиза_в_разпознаването(
    app, tmp_path: Path, monkeypatch
) -> None:
    показано: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: показано.append(str(args[2]) if len(args) > 2 else ""),
    )
    диалог = _диалог(tmp_path)
    диалог.table.cellWidget(0, 3).setCurrentIndex(1)

    диалог._save()

    assert диалог.decisions_made == 1
    payload = json.loads((tmp_path / "working" / PROFILE_NAME).read_text(encoding="utf-8"))
    assert payload["decisions"][0]["layer"] == "Колони"
    assert показано and "НЕ влиза в разпознаването" in показано[0]


def test_прозорецът_има_път_до_паспорта_и_пази_при_липсващ_чертеж(
    app, tmp_path: Path, monkeypatch
) -> None:
    """Backend без операторски път е половин функция — действието съществува."""

    import logging
    import re
    from types import SimpleNamespace

    from lion_rebar.engine.package_io import inspect_engine_bundle
    from lion_rebar.project.models import ProjectModel
    from lion_rebar.ui.main_window import MainWindow

    def _най_нов_пакет() -> Path:
        """Най-новият пакетиран engine, сортиран по версия, не по име."""

        корен = Path(__file__).resolve().parents[1] / "engine_updates"

        def ключ(път: Path) -> tuple[int, ...]:
            съвпадение = re.search(r"_v(\d+)\.(\d+)\.(\d+)", път.name)
            return (
                tuple(int(част) for част in съвпадение.groups())
                if съвпадение
                else (0, 0, 0)
            )

        return max(корен.glob("*.lionengine"), key=ключ)

    class _RepositoryStub:
        def __init__(self) -> None:
            self.engine, _manifest, _names = inspect_engine_bundle(_най_нов_пакет())

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
        logging.getLogger("паспорт"),
        session_service=_SessionStub(),
    )
    съобщения: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: съобщения.append(str(args[2]) if len(args) > 2 else ""),
    )

    assert hasattr(window, "action_drawing_passport")

    # без проект и без импорт паспортът не се прави, а обяснява защо
    window.open_drawing_passport()
    assert съобщения and "импортирай DXF" in съобщения[0]

    # с проект без нито един разпознаваем образец правилата мълчат
    съобщения.clear()
    window.project = ProjectModel(name="ПАСПОРТ", root=str(tmp_path))
    window.import_result = SimpleNamespace(entities=[])
    window.open_drawing_passport()
    assert съобщения and "мълчат" in съобщения[0]


def test_смяна_без_мотив_не_записва_нищо(app, tmp_path: Path, monkeypatch) -> None:
    предупреждения: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: предупреждения.append(str(args[2]) if len(args) > 2 else ""),
    )
    диалог = _диалог(tmp_path)
    диалог.table.cellWidget(0, 3).setCurrentIndex(2)  # смени на друга роля

    диалог._save()

    assert диалог.decisions_made == 0
    assert not (tmp_path / "working" / PROFILE_NAME).exists()
    assert предупреждения and "мотив" in предупреждения[0]
