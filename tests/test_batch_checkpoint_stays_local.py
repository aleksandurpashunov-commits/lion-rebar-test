"""Междинната контролна точка не се пише в папката на отчета, когато е дадена
отделна локална папка.

ЗАЩО. Точката е целият отчет дотук, записан наново с ``fsync`` на всеки
``checkpoint_every`` надписа. Измерено 21.09.2026 на „т-1“ (3 006 надписа):
300 записа, файлът расте до 108 MB, сборът е около 16 GB. Прозорецът пише
отчетите в Google Drive на собственика, тоест тези 16 GB минават през
стриймваща файлова система. Разходът е квадратичен по брой надписи.

Крайните файлове -- отчетът и ``progress_final.json`` -- остават в папката на
отчета: местенето е само на работното досие.
"""
import json

import pytest

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.models import DxfEntityRecord


def _annotation(handle: str, text: str, x: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer="ARM",
        text=text,
        geometry={"insert": [x, 0.0, 0.0]},
    )


@pytest.fixture()
def annotations():
    return [_annotation(f"A{i:03d}", f"{i + 2}N10x{200 + i * 5}", i * 100.0)
            for i in range(6)]


def test_checkpoint_goes_to_the_given_local_folder(tmp_path, annotations):
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    local = tmp_path / "local"

    result = build_batch_geometry_report(
        annotations,
        cloud,
        project_name="ПРОБА",
        checkpoint_every=1,
        checkpoint_parent=local,
    )

    report_dir = result.report_dir
    # междинната точка НЕ е при отчета
    assert not (report_dir / "progress.json").exists()
    # крайните файлове са при отчета
    assert (report_dir / "progress_final.json").exists()
    assert report_dir.parent == cloud

    payload = json.loads((report_dir / "progress_final.json").read_text(encoding="utf-8"))
    assert payload["total_annotations"] == len(annotations)


def test_local_checkpoint_is_cleaned_up_after_a_finished_report(tmp_path, annotations):
    """Готовият отчет заменя работната точка — тя не остава да расте локално."""

    cloud = tmp_path / "cloud"
    cloud.mkdir()
    local = tmp_path / "local"

    result = build_batch_geometry_report(
        annotations,
        cloud,
        project_name="ПРОБА",
        checkpoint_every=1,
        checkpoint_parent=local,
    )

    leftovers = list(local.rglob("progress.json")) if local.exists() else []
    assert leftovers == []
    assert (result.report_dir / "progress_final.json").exists()


def test_default_keeps_the_checkpoint_with_the_report(tmp_path, annotations):
    """Без подадена папка поведението е непроменено — договорът се пази."""

    out = tmp_path / "out"
    out.mkdir()

    result = build_batch_geometry_report(
        annotations,
        out,
        project_name="ПРОБА",
        checkpoint_every=1,
    )

    assert (result.report_dir / "progress.json").exists()
    assert (result.report_dir / "progress_final.json").exists()
