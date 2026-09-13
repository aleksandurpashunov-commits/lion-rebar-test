"""Пакетният анализ чете областите, не само Финалният контрол (rc44).

Собственик, 2026-09-13: „да направим рамките преди теста на чертежа, за да
може програмата да го прочете." „Тестът на чертежа" е пакетният анализ —
рамката трябва да си личи още в Review Center, а не чак след Финален контрол.

Проверено и върху истинския чертеж на шайбите: без области анализът дава 0
групови етикета от 59 реда, с 15 области — 29.
"""
from __future__ import annotations

import inspect

from lion_rebar.dxf.batch_geometry_report import (
    _apply_operator_area_group_labels,
    build_batch_geometry_report,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.operator_areas import KIND_BEAM, KIND_GROUP, OperatorArea

_TITLE = "Ш 3-25/600"


def _area(kind: str = KIND_GROUP, title: str = _TITLE, **kw) -> OperatorArea:
    params = dict(
        area_id="A1", x0=6445.0, y0=-139620.0, x1=7245.0, y1=-139360.0,
        kind=kind, title_handle="SH3", title_text=title, frame_index=1,
        decided_by="Александър", note="позициите под заглавието на Ш 3",
        at="2026-09-13T00:00:00+00:00", project_id="P", source_sha256="S",
        source_generation_id="G", analysis_run_id="R",
    )
    params.update(kw)
    return OperatorArea(**params)


def _annotation(handle: str, x: float, y: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle, "BLOCK_ATTR_TEXT", "arm_shaibi", {"insert": [x, y, 0.0]}, "34N12x415"
    )


def _rows(*handles: str) -> list[dict]:
    return [{"annotation_handle": h} for h in handles]


def test_анализът_приема_областите_като_вход() -> None:
    """Анализът не ходи сам до файловата система — областите му се подават."""

    signature = inspect.signature(build_batch_geometry_report)

    assert "operator_areas" in signature.parameters
    assert signature.parameters["operator_areas"].default == ()


def test_позиция_в_областта_получава_груповия_етикет() -> None:
    rows = _rows("PXANN_1")

    applied = _apply_operator_area_group_labels(
        rows,
        annotations_by_handle={"PXANN_1": _annotation("PXANN_1", 6900.0, -139500.0)},
        operator_areas=(_area(),),
    )

    assert applied == 1
    assert rows[0]["column_group_label"] == _TITLE


def test_позиция_извън_областта_остава_празна() -> None:
    rows = _rows("PXANN_2")

    applied = _apply_operator_area_group_labels(
        rows,
        annotations_by_handle={"PXANN_2": _annotation("PXANN_2", 99999.0, 0.0)},
        operator_areas=(_area(),),
    )

    assert applied == 0
    assert "column_group_label" not in rows[0]


def test_автоматичният_етикет_не_се_презаписва() -> None:
    """При колоните програмата сама намира контура и заглавието.

    Рамката попълва празно, не отменя доказано — същото правило като при
    гредите.
    """

    автоматичен = "%%UK23, K24 (35/35) - 2бр."
    rows = [{"annotation_handle": "PXANN_1", "column_group_label": автоматичен}]

    applied = _apply_operator_area_group_labels(
        rows,
        annotations_by_handle={"PXANN_1": _annotation("PXANN_1", 6900.0, -139500.0)},
        operator_areas=(_area(),),
    )

    assert applied == 0
    assert rows[0]["column_group_label"] == автоматичен


def test_област_за_греда_не_раздава_групови_етикети() -> None:
    rows = _rows("PXANN_1")

    applied = _apply_operator_area_group_labels(
        rows,
        annotations_by_handle={"PXANN_1": _annotation("PXANN_1", 6900.0, -139500.0)},
        operator_areas=(_area(kind=KIND_BEAM),),
    )

    assert applied == 0


def test_две_припокриващи_се_области_са_отказ() -> None:
    """Двусмислието спира, вместо да избере едната наслуки."""

    rows = _rows("PXANN_1")

    applied = _apply_operator_area_group_labels(
        rows,
        annotations_by_handle={"PXANN_1": _annotation("PXANN_1", 6900.0, -139500.0)},
        operator_areas=(_area(), _area(area_id="A2", title="Ш 4 - 25/350")),
    )

    assert applied == 0
    assert "column_group_label" not in rows[0]


def test_без_области_анализът_не_променя_нищо() -> None:
    rows = _rows("PXANN_1")

    assert _apply_operator_area_group_labels(
        rows,
        annotations_by_handle={"PXANN_1": _annotation("PXANN_1", 6900.0, -139500.0)},
        operator_areas=(),
    ) == 0
    assert rows == [{"annotation_handle": "PXANN_1"}]
