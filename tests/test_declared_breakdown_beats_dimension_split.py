"""Декларираната разбивка бие изведената от котировки (rc44).

Собственик, 2026-09-13: „позицията е 20, 200, 20 няма спор щом съм я дал
като решение."

Реален случай, проект ``s-7``, четири позиции — ``68N10x230``, ``68N10x240``
и два пъти ``34N10x240``. Проектантът е декларирал собствена разбивка
([20, 200, 20]), но в описа влизаше [12.5, 215, 12.5], изведена от
котировките наблизо (``F003_SEGMENTS_DERIVED``).

Коварното е, че **сборът не издава коя е вярната**: и двете дават 240 cm.
Само едната обаче е прътът, който трябва да се реже.

Причината беше един ред в ``_apply_proxy_declared_shape_auto_promotion``:

    elif matcher_code and row.get("production_segments_cm"):
        # Already agrees and already has production segments.
        continue

Съгласие за ФОРМАТА беше взето за съгласие и за РАЗМЕРИТЕ, и повишаването
прескачаше реда, преди изобщо да погледне декларацията.

Декларацията не печели, защото е декларация — печели, защото се доказва:
остават всички долни проверки (HIGH увереност на резолвера, семейството
пресметнато наново от ъглите на рамената, затваряне на декларираната
дължина, валидни MASTER параметри). Не мине ли ги, редът си остава с това,
което вече има.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.batch_geometry_report import (
    _apply_proxy_declared_shape_auto_promotion,
    _segment_vectors_agree,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import ShapeDefinition

_HANDLE = "PXANN_7A1A5A"
_ЗАЯВЕНО = [20.0, 200.0, 20.0]      # проектантът
_ИЗВЕДЕНО = [12.5, 215.0, 12.5]     # котировките; сборът е същият
_ОБЩО = 240.0


def _shapes() -> list[ShapeDefinition]:
    return [
        ShapeDefinition(
            code="F003",
            name="ОТВОРЕНА U-ФОРМА — АДАПТИВНА ОРИЕНТАЦИЯ",
            active=True,
        )
    ]


def _annotation(segments=None, **raw_extra) -> DxfEntityRecord:
    raw = {
        "declared_shape_source": "PROXY_ARC_VERIFIED_DECLARED_SEGMENTS",
        "declared_shape_code": "F003",
        "declared_shape_confidence": "HIGH",
        "declared_shape_segments_cm": list(
            _ЗАЯВЕНО if segments is None else segments
        ),
        "declared_shape_bend_arc_count": 0,
        # Ъглите на начертаните рамена: двете външни вертикални, средното
        # хоризонтално — U-форма, и портата ги преизчислява наново.
        "declared_shape_leg_directions_deg": [90.0, 180.0, 90.0],
        "length_cm": _ОБЩО,
    }
    raw.update(raw_extra)
    return DxfEntityRecord(
        _HANDLE, "BLOCK_ATTR_TEXT", "arm_shaibi",
        {"insert": [0.0, 0.0, 0.0]}, "68N10x240", raw=raw,
    )


def _row(**extra) -> dict:
    row = {
        "annotation_handle": _HANDLE,
        "annotation_text": "68N10x240",
        "master_shape_code": "F003",
        "production_segments_cm": list(_ИЗВЕДЕНО),
        "production_length_cm": _ОБЩО,
        "declared_length_cm": _ОБЩО,
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД",
    }
    row.update(extra)
    return row


def _promote(row: dict, annotation: DxfEntityRecord) -> int:
    return _apply_proxy_declared_shape_auto_promotion(
        [row],
        annotations_by_handle={_HANDLE: annotation},
        shape_definitions=_shapes(),
    )


class TestДекларациятаПобеждава:
    def test_различна_разбивка_се_подменя(self) -> None:
        """Същата форма, различни размери — проектантът има думата."""

        row = _row()

        applied = _promote(row, _annotation())

        assert applied == 1
        assert row["production_segments_cm"] == _ЗАЯВЕНО
        evidence = row["production_segment_evidence"]
        assert evidence["source"] == "PROXY_ARC_VERIFIED_DECLARED_SEGMENTS"
        assert evidence["segments"] == _ЗАЯВЕНО

    def test_еднаквият_сбор_не_е_доказателство(self) -> None:
        """И двете разбивки дават 240 — затова сборът не решава спора."""

        assert sum(_ЗАЯВЕНО) == sum(_ИЗВЕДЕНО) == _ОБЩО

    def test_съвпадаща_разбивка_не_се_пипа(self) -> None:
        """Няма спор — няма и работа."""

        row = _row(production_segments_cm=list(_ЗАЯВЕНО))

        assert _promote(row, _annotation()) == 0
        assert row["production_segments_cm"] == _ЗАЯВЕНО
        assert "production_segment_evidence" not in row

    def test_дребната_разлика_е_същата_разбивка(self) -> None:
        """Закръгляне под допуска не е спор."""

        row = _row(production_segments_cm=[20.05, 199.95, 20.0])

        assert _promote(row, _annotation()) == 0


class TestЗащититеОстават:
    def test_декларация_която_не_затваря_не_печели(self) -> None:
        """Разбивка, която не дава обявената дължина, не е доказателство."""

        row = _row()

        applied = _promote(row, _annotation(segments=[20.0, 150.0, 20.0]))

        assert applied == 0
        assert row["production_segments_cm"] == _ИЗВЕДЕНО

    def test_ниска_увереност_не_печели(self) -> None:
        row = _row()

        applied = _promote(
            row, _annotation(declared_shape_confidence="MEDIUM")
        )

        assert applied == 0
        assert row["production_segments_cm"] == _ИЗВЕДЕНО

    def test_ъгли_които_сочат_друга_форма_не_печелят(self) -> None:
        """Семейството се преизчислява наново от ъглите, не се вярва на кода."""

        row = _row()

        applied = _promote(
            row,
            _annotation(declared_shape_leg_directions_deg=[0.0, 30.0, 0.0]),
        )

        assert applied == 0
        assert row["production_segments_cm"] == _ИЗВЕДЕНО

    def test_спорна_собственост_върху_геометрията_спира_всичко(self) -> None:
        row = _row(
            geometry_owner_conflict=True,
            candidate_handles=["PXG_7A1A5A_2_0"],
        )

        applied = _promote(row, _annotation())

        assert applied == 0
        assert row["production_segments_cm"] == _ИЗВЕДЕНО


class TestСравнениеНаРазбивки:
    @pytest.mark.parametrize(
        "лява, дясна, очаквано",
        [
            ([20.0, 200.0, 20.0], [20.0, 200.0, 20.0], True),
            ([20.0, 200.0, 20.0], [20.05, 200.0, 20.0], True),
            ([20.0, 200.0, 20.0], [12.5, 215.0, 12.5], False),
            ([20.0, 200.0, 20.0], [20.0, 200.0], False),
            (None, [20.0], False),
            ([20.0], None, False),
            ([], [], False),
        ],
    )
    def test_сравнение(self, лява, дясна, очаквано) -> None:
        assert _segment_vectors_agree(лява, дясна) is очаквано
