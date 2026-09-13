"""Стъпка 4 от Фаза 0 — freeze ``status`` вече се проверява в библиотечния слой.

Независимият одит на rc43 намери: ``assert_export_allowed`` (UI гейт) e
единственото място, което чете ``final_control_freeze.json``-ия статус
(``VALID``/``INVALID``). ``load_export_context`` (истинският библиотечен
gate, зад Excel И DXF export) проверяваше само schema + идентичност +
SHA-256 на всеки frozen artifact — никога самото поле ``status``.

Практическа последица: ``invalidate_final_control`` пише ``status`` в
СЪЩИЯ freeze файл, чиито artifact-и вече е хеширал. Ако впоследствие
промяната бъде върната (undo на bulk одобрение, undo на групово решение) и
файловете се озоват байт-точно в предишното си състояние, SHA-256
проверките вече не хващат нищо — само ``status`` пази истината, че freeze-ът
е обезсилен и изисква нов Финален контрол. Този тест възпроизвежда точно
този сценарий директно (без нужда от истински bulk approve/undo цикъл):
обезсилва freeze-а, без да пипа НИТО ЕДИН frozen файл, и очаква
``load_export_context`` да откаже, не заради заобиколен UI бутон, а защото
самата библиотечна функция вече проверява.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.export.errors import ExcelExportError
from lion_rebar.export.excel_export import load_export_context
from lion_rebar.project.final_control_lifecycle import invalidate_final_control
from lion_rebar.project.integrity import reconcile_project_integrity

from test_recovery_6_3_1_data_integrity import _current_project, _engine  # type: ignore


def test_export_refuses_when_freeze_status_is_invalidated_even_with_unchanged_bytes(
    tmp_path: Path,
) -> None:
    _manager, project, _identity, entities, report_path = _current_project(tmp_path)
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)
    assert result.full_specification_complete is True
    assert result.freeze_required is True

    # Работи веднага след Финален контрол -- бял тест, потвърждава fixture-а.
    load_export_context(project, entities, _engine())

    # Обезсилва freeze-а точно както би направило bulk одобрение/групово
    # решение -- БЕЗ да пипа нито един от frozen файловете, чиито SHA-256
    # вече е записан в манифеста. Това е сценарият, в който старата
    # хеш-само проверка не хващаше нищо.
    invalidation = invalidate_final_control(
        project.root,
        event="ENGINEER_DECISION",
        changed_fields=["master_shape_code"],
        actor="TEST",
        note="симулира production-impacting промяна без файлова мутация",
    )
    assert invalidation.changed is True

    with pytest.raises(ExcelExportError, match="обезсилен"):
        load_export_context(project, entities, _engine())


def test_export_refuses_legacy_freeze_manifest_without_status_field(tmp_path: Path) -> None:
    """Allowlist, не blocklist: липсващ ``status`` не значи валиден."""

    import json

    _manager, project, _identity, entities, report_path = _current_project(tmp_path)
    result = reconcile_project_integrity(project.root, report_path=report_path, apply=True)
    assert result.freeze_required is True

    manifest_path = Path(result.freeze_manifest_path or "")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    del payload["status"]
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ExcelExportError, match="обезсилен"):
        load_export_context(project, entities, _engine())
