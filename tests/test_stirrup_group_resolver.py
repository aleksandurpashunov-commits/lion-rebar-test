from __future__ import annotations

import math
from pathlib import Path

from lion_rebar.dxf.geometry_candidates import build_geometry_candidate_context
from lion_rebar.dxf.geometry_core_v2 import polyline_path_from_geometry
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.stirrup_group_resolver import (
    resolve_stirrup_length_group_annotation,
)
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex


def _engine():
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.11_ARC_LENGTH_STIRRUP_GROUP_PRODUCTION_DRAFT.lionengine"
    )
    return inspect_engine_bundle(path)[0]


def _stirrup(handle: str, *, dy: float) -> DxfEntityRecord:
    raw = [
        [11.1590072210, 11.9078989661, 0.0],
        [4.3251418444, 19.2084663422, 0.6801808649],
        [0.0, 17.5, 0.0],
        [0.0, 2.5, math.tan(math.radians(90.0) / 4.0)],
        [2.5, 0.0, 0.0],
        [47.5, 0.0, math.tan(math.radians(90.0) / 4.0)],
        [50.0, 2.5, 0.0],
        [50.0, 17.5, math.tan(math.radians(90.0) / 4.0)],
        [47.5, 20.0, 0.0],
        [2.5, 20.0, 0.6563080394],
        [0.6748581559, 15.7915336538, 0.0],
        [7.5087235328, 8.4909662777, 0.0],
    ]
    points = [[x, y + dy, bulge] for x, y, bulge in raw]
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="Stremena",
        geometry={"points": points, "closed": False},
    )


def _annotation(text: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text=text,
        geometry={"insert": [25.0, 0.0, 0.0], "height": 2.5},
    )


def _resolve(entities, annotation, *, policy=None):
    engine = _engine()
    resolved_policy = policy or RecognitionPolicy.from_settings(engine.settings)
    context = build_geometry_candidate_context(entities, policy=resolved_policy)
    return resolve_stirrup_length_group_annotation(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=resolved_policy,
        context=context,
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )


def _declared_length() -> int:
    path = polyline_path_from_geometry(_stirrup("S", dy=0.0).geometry)
    assert path is not None
    return round(path.exact_length_raw)


def test_unique_remote_stirrup_is_selected_by_exact_arc_length() -> None:
    geometry = _stirrup("S1", dy=100.0)
    annotation = _annotation(f"25N8x{_declared_length()}")

    resolution = _resolve([annotation, geometry], annotation)

    assert resolution is not None
    assert resolution.code == "STIRRUP_EXACT_ARC_LENGTH_UNIQUE"
    assert resolution.member_handles == ("S1",)
    assert resolution.candidate.master_shape_code == "F006"


def test_repeated_row_is_one_group_candidate_not_two_ambiguous_objects() -> None:
    first = _stirrup("S1", dy=100.0)
    second = _stirrup("S2", dy=150.0)
    annotation = _annotation(f"25N8x{_declared_length()}")

    resolution = _resolve([annotation, first, second], annotation)

    assert resolution is not None
    assert resolution.code == "STIRRUP_EXACT_ARC_LENGTH_GROUP"
    assert set(resolution.member_handles) == {"S1", "S2"}
    assert resolution.declared_count == 25


def test_two_length_matches_outside_one_row_remain_unresolved() -> None:
    first = _stirrup("S1", dy=70.0)
    second = _stirrup("S2", dy=220.0)
    annotation = _annotation(f"25N8x{_declared_length()}")
    policy = RecognitionPolicy(stirrup_length_search_distance_raw=260.0)

    resolution = _resolve([annotation, first, second], annotation, policy=policy)

    assert resolution is None


def test_group_larger_than_annotation_count_is_rejected() -> None:
    first = _stirrup("S1", dy=100.0)
    second = _stirrup("S2", dy=150.0)
    annotation = _annotation(f"1N8x{_declared_length()}")

    resolution = _resolve([annotation, first, second], annotation)

    assert resolution is None
