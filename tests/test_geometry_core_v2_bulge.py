from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from lion_rebar.dxf.geometry_candidates import (
    build_geometry_candidate_context,
    find_geometry_candidates,
)
from lion_rebar.dxf.geometry_core_v2 import (
    GeometryRepresentation,
    arc_path_from_geometry,
    classify_path_representation,
    polyline_path_from_geometry,
    semantic_path_from_polyline,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


def _rounded_u(
    *,
    handle: str = "U1",
    layer: str = "Horiz.arm",
) -> DxfEntityRecord:
    # 15 + 205 + 15 = 235 tangent-run dimensions.  The two quarter arcs
    # have R=2.5 and are encoded in the standard DXF bulge form.
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer=layer,
        geometry={
            "points": [
                [205.0, 0.0, 0.0],
                [205.0, 12.5, math.tan(math.radians(90.0) / 4.0)],
                [202.5, 15.0, 0.0],
                [2.5, 15.0, math.tan(math.radians(90.0) / 4.0)],
                [0.0, 12.5, 0.0],
                [0.0, 0.0, 0.0],
            ],
            "closed": False,
        },
    )


def _annotation(text: str = "25N8x235") -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text=text,
        geometry={
            "insert": [90.0, -10.0, 0.0],
            "height": 2.5,
        },
    )


def _three_segment_u_shape() -> ShapeDefinition:
    return ShapeDefinition(
        code="FTEST-U",
        name="U TEST",
        state="APPROVED",
        active=True,
        mirror_allowed=True,
        segments=[
            SegmentDefinition(
                index=1,
                length_code="L1",
                drawing_direction="UP",
                bend_after_deg=90.0,
                bend_direction="LEFT",
            ),
            SegmentDefinition(
                index=2,
                length_code="L2",
                drawing_direction="LEFT",
                bend_after_deg=90.0,
                bend_direction="LEFT",
            ),
            SegmentDefinition(
                index=3,
                length_code="L3",
                drawing_direction="DOWN",
            ),
        ],
    )


def test_tangent_quarter_bulges_become_two_bends_not_four_chord_turns():
    path = polyline_path_from_geometry(_rounded_u().geometry)
    assert path is not None
    semantic = semantic_path_from_polyline(path)

    assert path.curve_count == 2
    assert semantic.representation is GeometryRepresentation.CURVED_CENTERLINE
    assert semantic.collapsed_arc_count == 2
    assert semantic.unresolved_arc_count == 0
    assert semantic.safe_for_linear_matching is True
    assert len(semantic.points) == 4
    assert tuple(value for point in semantic.points for value in point) == pytest.approx(
        (205.0, 0.0, 205.0, 15.0, 0.0, 15.0, 0.0, 0.0)
    )
    assert semantic.semantic_length_raw == pytest.approx(235.0)
    assert semantic.arc_radii_raw == pytest.approx((2.5, 2.5))


def test_rounded_u_matches_three_segment_master_topology():
    annotation = _annotation()
    geometry = _rounded_u()

    analysis = find_geometry_candidates(
        annotation,
        [annotation, geometry],
        shape_definitions=[_three_segment_u_shape()],
    )

    assert analysis.candidates
    candidate = analysis.candidates[0]
    assert candidate.master_shape_code == "FTEST-U"
    assert candidate.segment_count == 3
    expected_arc_length = 25.0 + 200.0 + 2.0 * 2.5 * math.pi / 2.0
    assert candidate.geometry_length_raw == pytest.approx(expected_arc_length)
    assert candidate.numeric_difference == pytest.approx(235.0 - expected_arc_length)
    assert candidate.geometry_representation == "CURVED_CENTERLINE"
    assert candidate.source_curve_count == 2
    assert candidate.normalized_bend_count == 2
    assert candidate.unresolved_curve_count == 0
    assert candidate.source_arc_radii_raw == pytest.approx((2.5, 2.5))


def test_standalone_arc_keeps_exact_arc_length_and_stays_unresolved_for_topology():
    path = arc_path_from_geometry(
        {
            "center": [10.0, 20.0, 0.0],
            "radius": 10.0,
            "start_angle": 0.0,
            "end_angle": 90.0,
        }
    )

    assert path is not None
    assert path.curve_count == 1
    assert path.exact_length_raw == pytest.approx(5.0 * math.pi)
    semantic = semantic_path_from_polyline(path)
    assert semantic.unresolved_arc_count == 1
    assert semantic.safe_for_linear_matching is False

    arc = DxfEntityRecord(
        handle="ARC1",
        entity_type="ARC",
        layer="ARM-GORNA",
        geometry={
            "center": [10.0, 20.0, 0.0],
            "radius": 10.0,
            "start_angle": 0.0,
            "end_angle": 90.0,
        },
    )
    context = build_geometry_candidate_context([arc])
    assert len(context.primitives) == 1
    primitive = context.primitives[0]
    assert primitive.length_raw == pytest.approx(5.0 * math.pi)
    assert primitive.unresolved_curve_count == 1


def test_specialized_curved_layer_alias_is_explicit_and_auditable():
    context = build_geometry_candidate_context([_rounded_u(layer="Stremena")])

    assert len(context.primitives) == 1
    primitive = context.primitives[0]
    assert primitive.geometry_layer_role == "CURVED_CENTERLINE_ALIAS"
    assert primitive.geometry_representation == "CURVED_CENTERLINE"


def test_shema_blockove_is_not_admitted_as_rebar_geometry():
    frame = DxfEntityRecord(
        handle="FRAME",
        entity_type="LWPOLYLINE",
        layer="Shema Blockove",
        geometry={
            "points": [
                [0.0, 0.0, 0.0],
                [200.0, 0.0, 0.0],
                [200.0, 80.0, 0.0],
                [0.0, 80.0, 0.0],
            ],
            "closed": True,
        },
    )
    context = build_geometry_candidate_context([frame])

    assert context.primitives == ()

    annotation = _annotation("8N8x560")
    analysis = find_geometry_candidates(
        annotation,
        [annotation, frame],
        shape_definitions=[],
    )
    assert analysis.candidates == ()
    assert analysis.status == "НЕ Е НАМЕРЕНА ПОДДЪРЖАНА ГЕОМЕТРИЯ"


def test_narrow_closed_envelope_is_not_silently_used_as_centerline():
    outline = DxfEntityRecord(
        handle="OUTLINE",
        entity_type="LWPOLYLINE",
        layer="ARM-GORNA",
        geometry={
            "points": [
                [0.0, 0.0, 0.0],
                [200.0, 0.0, 0.0],
                [200.0, 8.0, 0.0],
                [0.0, 8.0, 0.0],
            ],
            "closed": True,
        },
    )
    path = polyline_path_from_geometry(outline.geometry)
    assert path is not None
    assessment = classify_path_representation(path)
    assert assessment.representation is GeometryRepresentation.STROKED_OUTLINE

    annotation = _annotation("8N8x416")
    analysis = find_geometry_candidates(
        annotation,
        [annotation, outline],
        shape_definitions=[],
    )
    assert analysis.candidates == ()
    assert (
        analysis.rejection_counts[
            "ОЧЕРТАНИЕ БЕЗ ИЗВЛЕЧЕНА ОСЕВА ГЕОМЕТРИЯ"
        ]
        == 1
    )


@pytest.mark.skipif(
    importlib.util.find_spec("ezdxf") is None,
    reason="ezdxf is not installed in this environment",
)
def test_importer_keeps_explicit_lwpolyline_bulges(tmp_path: Path):
    import ezdxf

    from lion_rebar.dxf.importer import DxfImporter

    document = ezdxf.new("R2010")
    modelspace = document.modelspace()
    polyline = modelspace.add_lwpolyline(
        [
            (0.0, 0.0, 0.0),
            (10.0, 0.0, math.tan(math.radians(90.0) / 4.0)),
            (12.0, 2.0, 0.0),
        ],
        format="xyb",
        dxfattribs={"layer": "ARM-GORNA"},
    )
    assert polyline is not None
    path = tmp_path / "bulge.dxf"
    document.saveas(path)

    result = DxfImporter().import_file(path)
    imported = next(
        entity for entity in result.entities if entity.entity_type == "LWPOLYLINE"
    )
    assert imported.geometry["bulges"] == pytest.approx(
        [0.0, math.tan(math.radians(90.0) / 4.0), 0.0]
    )
    assert imported.geometry["curve_segment_count"] == 1
    # Historical third-coordinate storage remains intact for old projects.
    assert [point[2] for point in imported.geometry["points"]] == pytest.approx(
        imported.geometry["bulges"]
    )
