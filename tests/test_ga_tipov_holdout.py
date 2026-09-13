"""Заключен regression холдоут за втория (нов) проектант — "GA".

Потребителят докладва: 130 верни автоматично одобрени, 2 инженерно
отхвърлени от 132 действителни позиции, чрез "Приеми останалите валидни
силни" в rc43. Този тест реално изпълнява DXF->import->batch анализ пътя
(не грепва текст) и заключва точно тия числа за файла
``tests/fixtures/real_dxf/71_83_GA_tipov.dxf``.

ЗАЩО Е ВАЖЕН
------------
Другият локиран холдоут (``holdout_evidence``) е от оригиналния проектант,
чиито чертежи носят F005/F006 (фиби/стремена) — най-сложната и най-често
поправяна категория. Този нов чертеж НЯМА нито една F005/F006 позиция
(потвърдено: 66×F004, 65×F001, 1×F003, 0×F006) — затова близкият до
перфектния success rate тук не означава, че engine-ът е "решил" по-трудния
случай; означава, че този конкретен проектант никога не докосва най-
рисковата категория. Ако бъдещ ENGINE/Matcher промени тихо понижи strong
rate-а тук ПРИ същите master форми, това е регресия в основния,
непроменен от F005/F006 път (F001/F004), не в легендата.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.importer import DxfImporter
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.package_io import inspect_engine_bundle

FIXTURE = Path(__file__).parent / "fixtures" / "real_dxf" / "71_83_GA_tipov.dxf"
ENGINE_GLOB = "*v0.3.14*.lionengine"

STRONG_STATUSES = {"СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ", "СИЛНО СТРЕМЕ F006"}


def _load_rows(tmp_path: Path) -> tuple[list[dict], object]:
    root = Path(__file__).resolve().parents[1]
    engine_path = next((root / "engine_updates").glob(ENGINE_GLOB))
    engine, _manifest, _notes = inspect_engine_bundle(engine_path)

    entities = DxfImporter().import_file(FIXTURE).entities
    out_dir = tmp_path / "batch"
    out_dir.mkdir()
    report = build_batch_geometry_report(
        entities, out_dir, project_name="GA_TIPOV_HOLDOUT",
        shape_definitions=engine.shapes, control_tests=engine.tests,
        policy=RecognitionPolicy.from_settings(engine.settings),
        engine_metadata={"engine_version": engine.version},
    )
    csv_path = next(out_dir.glob("*/*_geometry_batch.csv"))
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows, report


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Липсва реалният GA DXF fixture.")
def test_ga_tipov_actionable_count_is_stable(tmp_path: Path):
    rows, report = _load_rows(tmp_path)
    actionable = [r for r in rows if r.get("annotation_class") == "ACTIONABLE"]
    assert len(actionable) == 132


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Липсва реалният GA DXF fixture.")
def test_ga_tipov_strong_recognition_does_not_regress(tmp_path: Path):
    """Заключва 130/132 силни — регресия тук е реален производствен риск."""

    rows, _report = _load_rows(tmp_path)
    actionable = [r for r in rows if r.get("annotation_class") == "ACTIONABLE"]
    strong = [r for r in actionable if str(r.get("batch_status") or "") in STRONG_STATUSES]
    assert len(strong) >= 130, (
        f"Силно разпознаване падна на {len(strong)}/132 — проверка на "
        "F001/F004 пътя преди release."
    )


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Липсва реалният GA DXF fixture.")
def test_ga_tipov_has_no_stirrups_or_hooks(tmp_path: Path):
    """Документира структурния факт: чертежът не пипа F005/F006 изобщо.

    Ако бъдещ анализ покаже F005/F006 тук, файлът/fixture-ът е сгрешен, не
    engine-ът -- сигнал да се провери коя версия на чертежа наистина е
    заключена.
    """

    rows, _report = _load_rows(tmp_path)
    actionable = [r for r in rows if r.get("annotation_class") == "ACTIONABLE"]
    shapes = {str(r.get("master_shape_code") or "") for r in actionable}
    assert "F005" not in shapes
    assert "F006" not in shapes


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Липсва реалният GA DXF fixture.")
def test_ga_tipov_risk_rows_are_the_known_length_mismatch(tmp_path: Path):
    """Заключва самоличността на единствените 2 отказани позиции.

    Потребителят инженерно отхвърли точно 2 -- и двете 2N12x100 с
    LENGTH_MISMATCH. Ако утре се появят различни/повече рискови позиции,
    това е промяна в производственото поведение, не козметика.
    """

    rows, _report = _load_rows(tmp_path)
    actionable = [r for r in rows if r.get("annotation_class") == "ACTIONABLE"]
    risk = [r for r in actionable if str(r.get("batch_status") or "") == "РИСК"]
    assert len(risk) == 2
    assert all(r.get("annotation_text") == "2N12x100" for r in risk)
    assert all("LENGTH_MISMATCH" in str(r.get("matcher_blocker_codes") or "") for r in risk)
