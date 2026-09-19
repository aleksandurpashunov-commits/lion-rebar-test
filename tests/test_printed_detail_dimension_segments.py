"""„Форма от котите до надписа" — регресия по измереното в реален чертеж.

Числата и разстоянията тук са снети от „Сграда бета\\Плоча_кота 14.90"
(19.09.2026) и са сверени с решенията на собственика за същите позиции:

* ``11N8x390`` → F006 ``[10, 165, 20, 165, 20, 10]``
* ``60N8x182`` → F010 ``[7, 79, 10, 79, 7]``
* ``1180N8x190`` (бордът) → ОТКАЗ: надписаните коти дават 189 при L=190
* щурците ``32N8x166``/``28N8x166`` → F006 ``[10, 40, 33, 40, 33, 10]`` през
  спецификацията ``стр.: N8x166``

Преди тази промяна и петте оставаха „НЯМА КАНДИДАТ" с празен панел.
"""

from __future__ import annotations

import copy

from lion_rebar.dxf.batch_geometry_report import (
    PRINTED_DETAIL_SEGMENT_SOURCE,
    PRINTED_REPEATED_LABEL_SEGMENT_SOURCE,
    PRINTED_SPECIFICATION_SEGMENT_SOURCE,
    _apply_printed_detail_dimension_segments,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.printed_detail_dimensions import (
    near_miss_note,
    printed_dimension_value,
    shape_from_printed_dimensions,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.export.auto_gate import validate_production_segment_evidence


HOOK_MIN_CM = 4.0
HOOK_MAX_CM = 20.0


def _text(handle: str, text: str, x: float, y: float, *, layer: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer=layer,
        text=text,
        geometry={
            "insert": [x, y, 0.0],
            "visual_anchor": [x, y, 0.0],
            "height": 12.0,
        },
    )


def _shapes() -> tuple[ShapeDefinition, ...]:
    return (
        ShapeDefinition(
            code="F006",
            name="СТРЕМЕ (БИГЛА)",
            state="LOCKED",
            active=True,
            closed=True,
            segments=[
                SegmentDefinition(index=1, length_code="L1", bend_after_deg=135),
                SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
                SegmentDefinition(index=3, length_code="L3", bend_after_deg=90),
                SegmentDefinition(index=4, length_code="L2", bend_after_deg=90),
                SegmentDefinition(index=5, length_code="L3", bend_after_deg=135),
                SegmentDefinition(index=6, length_code="L1"),
            ],
        ),
        ShapeDefinition(
            code="F010",
            name="ОТВОРЕНА ПЕТСЕГМЕНТНА ФОРМА — НАДЯСНО",
            state="LOCKED",
            active=True,
            segments=[
                SegmentDefinition(index=index, length_code=f"L{index}")
                for index in range(1, 6)
            ],
        ),
    )


def _row(
    handle: str,
    text: str,
    *,
    count: int | None,
    diameter_mm: float,
    length_cm: float,
) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": text,
        "annotation_class": "ACTIONABLE" if count is not None else "MISSING_COUNT",
        "annotation_count": count,
        "diameter_mm": diameter_mm,
        "declared_length_cm": length_cm,
        "batch_status": "НЯМА КАНДИДАТ" if count is not None else "НЕПЪЛЕН НАДПИС",
        "candidate_handles": [],
        "candidate_notes": [],
        "analysis_warnings": [],
    }


def _fixture() -> tuple[list[DxfEntityRecord], dict[str, DxfEntityRecord], list[dict]]:
    """Четирите реални случая с действителните им координати."""

    entities: list[DxfEntityRecord] = []
    annotations: dict[str, DxfEntityRecord] = {}

    def annotation(handle: str, text: str, x: float, y: float) -> None:
        record = _text(handle, text, x, y, layer="ARMTXT")
        entities.append(record)
        annotations[handle] = record

    # 1) Бордът 11N8x390: кота 165 до рамото, кота 10 до куката.
    annotation("BORD-ANN", "11N8x390", 136219.2, 2186.7)
    entities.append(_text("BORD-LBL", "%%Uстр.:", 136168.6, 2186.9, layer="ARMTXT1"))
    entities.append(_text("BORD-165", "165", 136166.7, 2372.4, layer="ARM_TXT1"))
    entities.append(_text("BORD-10", "10", 136205.3, 2506.6, layer="ARM_TXT1"))
    entities.append(_text("BORD-STEP", "s=20", 136222.2, 2168.1, layer="ARMTXT1"))
    # Съседните есове със СВОЯТА кота 20 — не бива да влиза в стремето.
    annotation("ESOVE-ANN", "22N6x40", 136079.3, 2115.2)
    entities.append(_text("ESOVE-LBL", "есове:", 136070.6, 2170.5, layer="ARMTXT1"))
    entities.append(_text("ESOVE-20", "20", 136065.3, 2135.0, layer="ARMTXT1"))

    # 2) 60N8x182: пет коти 7, 7, 79, 79, 10.
    annotation("OPEN-ANN", "60N8x182", 138598.4, 2249.3)
    entities.append(_text("OPEN-LBL", "%%Uстр.:", 138509.8, 2248.0, layer="ARMTXT1"))
    entities.append(_text("OPEN-7A", "7", 138584.1, 2295.4, layer="ARM_TXT1"))
    entities.append(_text("OPEN-7B", "7", 138548.0, 2274.5, layer="ARM_TXT1"))
    entities.append(_text("OPEN-79A", "79", 138541.4, 2366.6, layer="ARM_TXT1"))
    entities.append(_text("OPEN-79B", "79", 138590.7, 2366.2, layer="ARM_TXT1"))
    entities.append(_text("OPEN-10", "10", 138567.1, 2455.2, layer="ARM_TXT1"))

    # 3) Бордът 1180N8x190: 8 + 79 + 15 + 79 + 8 = 189 при L=190 → отказ.
    annotation("MISS-ANN", "1180N8x190", 137590.9, 1219.8)
    entities.append(_text("MISS-LBL", "%%Uстр.:", 137526.4, 1223.4, layer="ARMTXT1"))
    entities.append(_text("MISS-8A", "8", 137596.2, 1290.9, layer="ARM_TXT1"))
    entities.append(_text("MISS-8B", "8", 137550.1, 1270.0, layer="ARM_TXT1"))
    entities.append(_text("MISS-79A", "79", 137543.5, 1362.1, layer="ARM_TXT1"))
    entities.append(_text("MISS-79B", "79", 137602.8, 1361.7, layer="ARM_TXT1"))
    entities.append(_text("MISS-15", "15", 137572.0, 1450.6, layer="ARM_TXT1"))

    # 4) Щурците: спецификация стр.: N8x166 с коти 40 и 33; позициите са в
    #    таблицата, на стотици единици от котите.
    annotation("SPEC", "N8x166", 139633.3, 2400.3)
    entities.append(_text("SPEC-LBL", "%%Uстр.:", 139589.7, 2402.0, layer="ARMTXT1"))
    entities.append(_text("SPEC-40", "40", 139562.8, 2458.6, layer="ARMTXT1"))
    entities.append(_text("SPEC-33", "33", 139605.5, 2513.2, layer="ARMTXT1"))
    annotation("LINTEL-1", "32N8x166", 139294.1, 2242.1)
    annotation("LINTEL-9", "28N8x166", 139294.1, 1938.9)
    annotation("LINTEL-LONG", "8N12x610", 139111.3, 2242.1)

    rows = [
        _row("BORD-ANN", "11N8x390", count=11, diameter_mm=8.0, length_cm=390.0),
        _row("ESOVE-ANN", "22N6x40", count=22, diameter_mm=6.0, length_cm=40.0),
        _row("OPEN-ANN", "60N8x182", count=60, diameter_mm=8.0, length_cm=182.0),
        _row("MISS-ANN", "1180N8x190", count=1180, diameter_mm=8.0, length_cm=190.0),
        _row("SPEC", "N8x166", count=None, diameter_mm=8.0, length_cm=166.0),
        _row("LINTEL-1", "32N8x166", count=32, diameter_mm=8.0, length_cm=166.0),
        _row("LINTEL-9", "28N8x166", count=28, diameter_mm=8.0, length_cm=166.0),
        _row("LINTEL-LONG", "8N12x610", count=8, diameter_mm=12.0, length_cm=610.0),
    ]
    return entities, annotations, rows


def _apply(rows: list[dict], entities, annotations) -> int:
    return _apply_printed_detail_dimension_segments(
        rows,
        entities=entities,
        annotations_by_handle=annotations,
        policy=RecognitionPolicy(),
        shape_definitions=_shapes(),
    )


def _by_handle(rows: list[dict]) -> dict[str, dict]:
    return {str(row["annotation_handle"]): row for row in rows}


# ---------------------------------------------------------------- прочитът


def test_two_printed_dimensions_close_the_closed_stirrup() -> None:
    shape = shape_from_printed_dimensions(
        [165.0, 10.0], 390.0, hook_min_cm=HOOK_MIN_CM, hook_max_cm=HOOK_MAX_CM
    )
    assert shape is not None
    assert shape.shape_code == "F006"
    assert shape.segments == (10.0, 165.0, 20.0, 165.0, 20.0, 10.0)
    assert shape.computed_value_cm == 20.0


def test_specification_dimensions_compute_the_hook() -> None:
    shape = shape_from_printed_dimensions(
        [40.0, 33.0], 166.0, hook_min_cm=HOOK_MIN_CM, hook_max_cm=HOOK_MAX_CM
    )
    assert shape is not None
    assert shape.segments == (10.0, 40.0, 33.0, 40.0, 33.0, 10.0)
    assert shape.computed_value_cm == 10.0


def test_five_symmetric_dimensions_give_the_open_stirrup() -> None:
    shape = shape_from_printed_dimensions(
        [7.0, 7.0, 79.0, 79.0, 10.0],
        182.0,
        hook_min_cm=HOOK_MIN_CM,
        hook_max_cm=HOOK_MAX_CM,
    )
    assert shape is not None
    assert shape.shape_code == "F010"
    assert shape.segments == (7.0, 79.0, 10.0, 79.0, 7.0)
    assert shape.computed_value_cm is None


def test_border_that_does_not_close_is_refused() -> None:
    """Пет коти, които дават 189 при L=190 — отказ, не нагласяне.

    Подмножеството {8, 79} затваря F006 (2×(8+79+8) = 190). Точно затова
    броят на котите е част от прочита: пет коти никога не се свеждат до две.
    """

    assert (
        shape_from_printed_dimensions(
            [8.0, 8.0, 79.0, 79.0, 15.0],
            190.0,
            hook_min_cm=HOOK_MIN_CM,
            hook_max_cm=HOOK_MAX_CM,
        )
        is None
    )
    note = near_miss_note([8.0, 8.0, 79.0, 79.0, 15.0], 190.0)
    assert note is not None
    assert "189" in note and "190" in note


def test_only_two_or_five_printed_dimensions_are_read() -> None:
    for values in ([165.0], [45.0, 10.0, 20.0], [7.0, 7.0, 79.0, 79.0]):
        assert (
            shape_from_printed_dimensions(
                values, 390.0, hook_min_cm=HOOK_MIN_CM, hook_max_cm=HOOK_MAX_CM
            )
            is None
        )


def test_hook_outside_the_measured_range_is_refused() -> None:
    # 2×(30 + 100 + 40) = 340: „куката" 30 cm не е кука.
    assert (
        shape_from_printed_dimensions(
            [100.0, 40.0], 340.0, hook_min_cm=HOOK_MIN_CM, hook_max_cm=HOOK_MAX_CM
        )
        is None
    )


def test_step_and_variable_texts_are_not_dimensions() -> None:
    assert printed_dimension_value("s=20") is None
    assert printed_dimension_value("~ L ~") is None
    assert printed_dimension_value("N8x166") is None
    assert printed_dimension_value("%%U165") == 165.0


# ------------------------------------------------------------- през реда


def test_border_stirrup_gets_shape_and_segments_from_its_own_detail() -> None:
    entities, annotations, rows = _fixture()
    # Решени: бордът, отвореното стреме и двата щурца през спецификацията.
    assert _apply(rows, entities, annotations) == 4

    row = _by_handle(rows)["BORD-ANN"]
    assert row["master_shape_code"] == "F006"
    assert row["production_segments_cm"] == [10.0, 165.0, 20.0, 165.0, 20.0, 10.0]
    evidence = row["production_segment_evidence"]
    assert evidence["source"] == PRINTED_DETAIL_SEGMENT_SOURCE
    assert evidence["label_handle"] == "BORD-LBL"
    # Котата 20 на есовете е ТЯХНА и не влиза в стремето.
    assert sorted(evidence["dimension_handles"]) == ["BORD-10", "BORD-165"]
    assert validate_production_segment_evidence(row) is True


def test_contradicting_suggestion_is_dropped() -> None:
    """Предложение по близки котировки не бива да спори с доказан прочит."""

    entities, annotations, rows = _fixture()
    for row in rows:
        if row["annotation_handle"] == "BORD-ANN":
            row["suggested_shape_code"] = "F003"
            row["suggested_segments_cm"] = [16.0, 358.0, 16.0]
    _apply(rows, entities, annotations)
    row = _by_handle(rows)["BORD-ANN"]
    assert row["master_shape_code"] == "F006"
    assert not row.get("suggested_shape_code")
    assert not row.get("suggested_segments_cm")


def test_neighbouring_esove_detail_is_not_touched() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = _by_handle(rows)["ESOVE-ANN"]
    assert not row.get("master_shape_code")
    assert not row.get("production_segments_cm")


def test_open_stirrup_row_gets_f010() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = _by_handle(rows)["OPEN-ANN"]
    assert row["master_shape_code"] == "F010"
    assert row["production_segments_cm"] == [7.0, 79.0, 10.0, 79.0, 7.0]
    assert validate_production_segment_evidence(row) is True


def test_border_row_that_does_not_close_stays_for_the_operator() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = _by_handle(rows)["MISS-ANN"]
    assert not row.get("master_shape_code")
    assert not row.get("production_segments_cm")
    assert any("НЕ ЗАТВАРЯТ" in str(note) for note in row["candidate_notes"])


def test_specification_resolves_the_lintel_stirrups() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    by_handle = _by_handle(rows)
    for handle in ("LINTEL-1", "LINTEL-9"):
        row = by_handle[handle]
        assert row["master_shape_code"] == "F006"
        assert row["production_segments_cm"] == [10.0, 40.0, 33.0, 40.0, 33.0, 10.0]
        evidence = row["production_segment_evidence"]
        assert evidence["source"] == PRINTED_SPECIFICATION_SEGMENT_SOURCE
        assert evidence["specification_handle"] == "SPEC"
        assert validate_production_segment_evidence(row) is True


def test_position_without_matching_specification_is_left_alone() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = _by_handle(rows)["LINTEL-LONG"]
    assert not row.get("master_shape_code")
    assert not row.get("production_segments_cm")


def test_position_with_a_geometric_candidate_is_left_alone() -> None:
    entities, annotations, rows = _fixture()
    for row in rows:
        if row["annotation_handle"] == "BORD-ANN":
            row["candidate_handles"] = ["SOME-LINE"]
    _apply(rows, entities, annotations)
    row = _by_handle(rows)["BORD-ANN"]
    assert not row.get("master_shape_code")


def test_two_specifications_with_different_sizes_refuse_both() -> None:
    entities, annotations, rows = _fixture()
    # Втора спецификация за същия Ø и дължина, но с други размери.
    other = _text("SPEC2", "N8x166", 140633.3, 2400.3, layer="ARMTXT1")
    entities.append(other)
    annotations["SPEC2"] = other
    entities.append(_text("SPEC2-LBL", "%%Uстр.:", 140589.7, 2402.0, layer="ARMTXT1"))
    entities.append(_text("SPEC2-45", "45", 140562.8, 2458.6, layer="ARMTXT1"))
    entities.append(_text("SPEC2-28", "28", 140605.5, 2513.2, layer="ARMTXT1"))
    rows.append(_row("SPEC2", "N8x166", count=None, diameter_mm=8.0, length_cm=166.0))

    _apply(rows, entities, annotations)
    by_handle = _by_handle(rows)
    for handle in ("LINTEL-1", "LINTEL-9"):
        assert not by_handle[handle].get("master_shape_code")


def test_proven_shape_elsewhere_refutes_the_specification() -> None:
    entities, annotations, rows = _fixture()
    rows.append(
        {
            **_row("OTHER", "12N8x166", count=12, diameter_mm=8.0, length_cm=166.0),
            "master_shape_code": "F003",
            "candidate_handles": ["REAL-GEOMETRY"],
        }
    )
    _apply(rows, entities, annotations)
    by_handle = _by_handle(rows)
    for handle in ("LINTEL-1", "LINTEL-9"):
        assert not by_handle[handle].get("master_shape_code")


# ------------------------------------------------------------------ гейтът


def test_gate_refuses_tampered_printed_values() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = copy.deepcopy(_by_handle(rows)["BORD-ANN"])
    row["production_segment_evidence"]["printed_values_cm"] = [165.0, 20.0]
    assert validate_production_segment_evidence(row) is False


def test_gate_refuses_a_foreign_label() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = copy.deepcopy(_by_handle(rows)["BORD-ANN"])
    row["production_segment_evidence"]["label_text"] = "есове:"
    assert validate_production_segment_evidence(row) is False


def test_gate_refuses_when_a_candidate_appears_later() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = copy.deepcopy(_by_handle(rows)["OPEN-ANN"])
    row["candidate_handles"] = ["LATE-GEOMETRY"]
    assert validate_production_segment_evidence(row) is False


def test_panel_names_each_of_the_three_paths() -> None:
    """Операторът трябва да вижда ОТКЪДЕ идват размерите, не само че са налице."""

    from lion_rebar.ui.review_center import _production_segment_source_label

    labels = {
        source: _production_segment_source_label(
            {
                "production_segments_cm": [10.0, 40.0, 33.0, 40.0, 33.0, 10.0],
                "production_segment_evidence": {"source": source},
            }
        )
        for source in (
            PRINTED_DETAIL_SEGMENT_SOURCE,
            PRINTED_SPECIFICATION_SEGMENT_SOURCE,
            PRINTED_REPEATED_LABEL_SEGMENT_SOURCE,
        )
    }
    assert len(set(labels.values())) == 3
    assert all("АВТОМАТИЧНО" in value for value in labels.values())


def test_gate_refuses_a_specification_with_another_length() -> None:
    entities, annotations, rows = _fixture()
    _apply(rows, entities, annotations)
    row = copy.deepcopy(_by_handle(rows)["LINTEL-1"])
    row["production_segment_evidence"]["specification_length_cm"] = 170.0
    assert validate_production_segment_evidence(row) is False
