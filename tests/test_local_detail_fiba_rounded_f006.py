from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.geometry_candidates import (
    F006_STRONG_CONFIDENCE,
    build_geometry_candidate_context,
    find_geometry_candidates,
)
from lion_rebar.dxf.local_detail_resolver import build_local_detail_index
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


def _text(
    handle: str,
    text: str,
    x: float,
    y: float,
    *,
    rotation: float = 0.0,
    layer: str = "ARM_TXT1",
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer=layer,
        text=text,
        geometry={
            "insert": [x, y, 0.0],
            "visual_anchor": [x, y, 0.0],
            "rotation": rotation,
            "height": 3.0,
        },
    )


def _line(handle: str, start: tuple[float, float], end: tuple[float, float]) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer="ARM_Bars",
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
    )


def _arc(
    handle: str,
    center: tuple[float, float],
    start_angle: float,
    end_angle: float,
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="ARC",
        layer="ARM_Bars",
        geometry={
            "center": [*center, 0.0],
            "radius": 3.0,
            "start_angle": start_angle,
            "end_angle": end_angle,
        },
    )


def _shapes() -> tuple[ShapeDefinition, ...]:
    return (
        ShapeDefinition(
            code="F003",
            name="ПРЪТ С ДВА ВИНКЕЛА",
            state="LOCKED",
            active=True,
            segments=[
                SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
                SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
                SegmentDefinition(index=3, length_code="L3"),
            ],
            recognition={"segment_count": 3, "distinct_from": ["F005"]},
        ),
        ShapeDefinition(
            code="F005",
            name="ФИБА",
            state="LOCKED",
            active=True,
            segments=[
                SegmentDefinition(index=1, length_code="L1", bend_after_deg=90),
                SegmentDefinition(index=2, length_code="L2", bend_after_deg=90),
                SegmentDefinition(index=3, length_code="L1"),
            ],
            recognition={
                "segment_count": 3,
                "requires_equal_end_arms": True,
                "distinct_from": ["F003"],
            },
        ),
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
    )


def _fixture_entities() -> list[DxfEntityRecord]:
    # Rounded F006: four tangent sides, four quarter arcs and two keys.
    entities = [
        _line("TOP", (3.0, 70.0), (37.0, 70.0)),
        _line("BOTTOM", (3.0, 0.0), (37.0, 0.0)),
        _line("LEFT", (0.0, 3.0), (0.0, 67.0)),
        _line("RIGHT", (40.0, 3.0), (40.0, 67.0)),
        _arc("ATR", (37.0, 67.0), 0.0, 90.0),
        _arc("ABR", (37.0, 3.0), 270.0, 0.0),
        _arc("ATL", (3.0, 67.0), 90.0, 180.0),
        _arc("ABL", (3.0, 3.0), 180.0, 270.0),
        _line("KEY1", (0.0, 60.0), (14.1421356, 45.8578644)),
        _line("KEY2", (6.0, 70.0), (20.1421356, 55.8578644)),
        _text("S-LABEL", "%%Uстр.:", -35.0, -20.0),
        _text("S-ANN", "29N10x150", 0.0, -20.0, layer="ARM_TXT"),
        _text("S-W", "34", 18.0, 78.0, rotation=0.0),
        _text("S-H", "30", -12.0, 35.0, rotation=90.0),
        _text("S-K", "10", 12.0, 55.0, rotation=45.0),
    ]
    # Neighbouring U fiba: 40 + 14 + 40 = 94.
    entities.extend(
        [
            _line("F-L", (80.0, 0.0), (80.0, 40.0)),
            _line("F-M", (80.0, 40.0), (94.0, 40.0)),
            _line("F-R", (94.0, 40.0), (94.0, 0.0)),
            _text("F-LABEL", "%%Uфиби.:", 72.0, -20.0),
            _text("F-ANN", "29N8x94", 105.0, -20.0, layer="ARM_TXT"),
            _text("F-W", "14", 84.0, 48.0, rotation=0.0),
            _text("F-H", "40", 70.0, 20.0, rotation=90.0),
        ]
    )
    return entities


def test_local_prefixes_are_paired_one_to_one() -> None:
    entities = _fixture_entities()
    policy = RecognitionPolicy()
    index = build_local_detail_index(entities, entities, policy=policy)

    stirrup = index.assignment_for("S-ANN")
    fiba = index.assignment_for("F-ANN")
    assert stirrup is not None and stirrup.shape_code == "F006"
    assert fiba is not None and fiba.shape_code == "F005"
    assert {"S-W", "S-H", "S-K"}.issubset(set(stirrup.dimension_handles))
    assert {"F-W", "F-H"}.issubset(set(fiba.dimension_handles))


def test_rounded_stirrup_wins_over_neighbouring_fiba_geometry() -> None:
    entities = _fixture_entities()
    policy = RecognitionPolicy()
    index = build_local_detail_index(entities, entities, policy=policy)
    context = build_geometry_candidate_context(entities, policy=policy)
    stirrup = index.assignment_for("S-ANN")
    assert stirrup is not None

    analysis = find_geometry_candidates(
        next(item for item in entities if item.handle == "S-ANN"),
        entities,
        shape_definitions=_shapes(),
        policy=policy,
        context=context,
        local_detail_shape_code=stirrup.shape_code,
        local_detail_rule_code=stirrup.rule_code,
        local_detail_rule_note=stirrup.note,
        allowed_dimension_handles=stirrup.dimension_handles,
    )

    assert analysis.candidates
    top = analysis.candidates[0]
    assert top.master_shape_code == "F006"
    assert top.confidence == F006_STRONG_CONFIDENCE
    assert set(top.handles) == {
        "TOP",
        "BOTTOM",
        "LEFT",
        "RIGHT",
        "ATR",
        "ABR",
        "ATL",
        "ABL",
        "KEY1",
        "KEY2",
    }
    assert top.canonical_segment_lengths_cm == (10.0, 34.0, 30.0, 34.0, 30.0, 10.0)
    assert set(top.handles).isdisjoint({"F-L", "F-M", "F-R"})


def test_batch_resolves_f006_and_f005_without_shared_geometry(tmp_path: Path) -> None:
    entities = _fixture_entities()
    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="LOCAL_DETAIL",
        shape_definitions=_shapes(),
        policy=RecognitionPolicy(),
    )
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    by_handle = {row["annotation_handle"]: row for row in payload["rows"]}
    stirrup = by_handle["S-ANN"]
    fiba = by_handle["F-ANN"]

    assert stirrup["master_shape_code"] == "F006"
    assert stirrup["batch_status"] == "СИЛНО СТРЕМЕ F006"
    assert stirrup["f006_production_status"] == "F006_PRODUCTION_EXACT"
    assert fiba["master_shape_code"] == "F005"
    assert fiba["canonical_segment_lengths_cm"] == [40.0, 14.0, 40.0]
    assert stirrup["global_conflict"] is False
    assert fiba["global_conflict"] is False
    assert set(stirrup["candidate_handles"]).isdisjoint(fiba["candidate_handles"])


def test_f006_export_canonicalization_preserves_locked_traversal() -> None:
    from lion_rebar.export.excel_export import _canonicalize_for_master

    assert _canonicalize_for_master(
        "F006", (10.0, 34.0, 30.0, 34.0, 30.0, 10.0)
    ) == (10.0, 34.0, 30.0, 34.0, 30.0, 10.0)
    # A rotated drawing is the same production identity.
    assert _canonicalize_for_master(
        "F006", (10.0, 20.0, 50.0, 20.0, 50.0, 10.0)
    ) == (10.0, 50.0, 20.0, 50.0, 20.0, 10.0)
