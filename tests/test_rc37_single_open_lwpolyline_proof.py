from __future__ import annotations

import copy
import inspect
import math

import pytest

from lion_rebar.dxf.batch_geometry_report import (
    _apply_single_open_lwpolyline_production_segments,
    build_batch_geometry_report,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.auto_gate import validate_production_segment_evidence


QUARTER_BULGE = -math.tan(math.radians(90.0) / 4.0)


def _row(shape: str, length: float, handle: str) -> dict:
    return {
        "annotation_handle": "ANN",
        "frame_category_code": "VERTICALS",
        "master_shape_code": shape,
        "master_ambiguous_shape_codes": [],
        "candidate_handles": [handle],
        "declared_length_cm": length,
        "canonical_segment_lengths_cm": [],
        "canonical_turn_angles_deg": [],
    }


def _f003(handle: str = "U") -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="REBAR",
        geometry={
            "closed": False,
            "points": [
                [0.0, 0.0, 0.0],
                [12.5, 0.0, QUARTER_BULGE],
                [15.0, -2.5, 0.0],
                [15.0, -207.5, QUARTER_BULGE],
                [12.5, -210.0, 0.0],
                [0.0, -210.0, 0.0],
            ],
        },
    )


def _f006(handle: str = "S") -> DxfEntityRecord:
    # Six straight production runs (10, 20, 35, 20, 35, 10) joined by
    # five tangent R=2.5 arcs.  The two hook arcs sum to 270 degrees and
    # leave a 5 cm (= 2R) open overlap gap.
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="STIRRUPS",
        geometry={
            "closed": False,
            "points": [
                [0.0, 0.0, 0.0],
                [7.3005673763, -6.8338653767, -0.6801808649388219],
                [5.5921010320, -11.1590072208, 0.0],
                [-9.4078989660, -11.1590072206, QUARTER_BULGE],
                [-11.9078989663, -8.6590072206, 0.0],
                [-11.9078989658, 21.3409927794, QUARTER_BULGE],
                [-9.4078989658, 23.8409927794, 0.0],
                [5.5921010321, 23.8409927796, QUARTER_BULGE],
                [8.0921010321, 21.3409927796, 0.0],
                [8.0921010319, -8.6590072206, -0.6563080393584722],
                [3.8836346878, -10.4841490645, 0.0],
                [-3.4169326884, -3.6502836878, 0.0],
            ],
        },
    )


@pytest.mark.parametrize(
    ("row", "entity", "expected"),
    [
        (_row("F003", 240.0, "U"), _f003(), [15.0, 210.0, 15.0]),
        (
            _row("F006", 135.0, "S"),
            _f006(),
            [10.0, 20.0, 35.0, 20.0, 35.0, 10.0],
        ),
    ],
)
def test_exact_single_open_curved_topology_is_complete_production_proof(
    row: dict,
    entity: DxfEntityRecord,
    expected: list[float],
) -> None:
    assert _apply_single_open_lwpolyline_production_segments([row], [entity]) == 1
    assert row["canonical_segment_lengths_cm"] == expected
    assert row["production_segments_cm"] == expected
    assert row["production_segment_evidence"]["source"] == (
        "SINGLE_OPEN_LWPOLYLINE_TOPOLOGY"
    )
    assert validate_production_segment_evidence(row)


@pytest.mark.parametrize(
    ("prior_reason", "must_remain_blocked"),
    [
        ("F006_PRINTED_DIMENSIONS_MISSING", False),
        ("F006_PRINTED_DIMENSIONS_MISMATCH", True),
        ("CONFLICTING_PRODUCTION_SEGMENT_EVIDENCE", True),
    ],
)
def test_topology_supersedes_only_missing_printed_dimension_soft_block(
    prior_reason: str,
    must_remain_blocked: bool,
) -> None:
    row = _row("F006", 135.0, "S")
    row["production_export_blocked"] = True
    row["production_export_block_reason"] = prior_reason

    assert _apply_single_open_lwpolyline_production_segments([row], [_f006()]) == 1
    assert validate_production_segment_evidence(row)
    assert row["production_export_blocked"] is must_remain_blocked
    assert (
        str(row.get("production_export_block_reason") or "")
        == (prior_reason if must_remain_blocked else "")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "closed",
        "extra_candidate",
        "wrong_frame",
        "non_tangent",
        "unequal_radius",
        "wrong_total",
    ],
)
def test_topology_proof_fails_closed_on_any_missing_invariant(mutation: str) -> None:
    row = _row("F006", 135.0, "S")
    entity = _f006()
    if mutation == "closed":
        entity.geometry["closed"] = True
    elif mutation == "extra_candidate":
        row["candidate_handles"] = ["S", "OTHER"]
    elif mutation == "wrong_frame":
        row["frame_category_code"] = "BEAMS"
    elif mutation == "non_tangent":
        entity.geometry["points"][2][0] += 1.0
    elif mutation == "unequal_radius":
        entity.geometry["points"][3][2] = -0.35
    elif mutation == "wrong_total":
        row["declared_length_cm"] = 150.0

    assert _apply_single_open_lwpolyline_production_segments([row], [entity]) == 0
    assert not row.get("production_segment_evidence")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("geometry_handle", "FOREIGN"),
        ("path_closed", True),
        ("line_lengths_raw", [10.0] * 6),
        ("arc_radii_raw", [2.5, 2.5, 4.0, 2.5, 2.5]),
        ("arc_sweeps_deg", [-120.0, -90.0, -90.0, -90.0, -120.0]),
        ("tangent_deviations_deg", [0.0] * 9 + [3.0]),
        ("hooked_closure_gap_raw", 8.0),
    ],
)
def test_persisted_topology_proof_rejects_tampering(field: str, value: object) -> None:
    row = _row("F006", 135.0, "S")
    assert _apply_single_open_lwpolyline_production_segments([row], [_f006()]) == 1
    forged = copy.deepcopy(row)
    forged["production_segment_evidence"][field] = value
    assert not validate_production_segment_evidence(forged)


def test_generic_open_polyline_is_not_promoted_as_f003() -> None:
    entity = DxfEntityRecord(
        handle="U",
        entity_type="LWPOLYLINE",
        layer="REBAR",
        geometry={
            "closed": False,
            "points": [[0.0, 0.0, 0.0], [15.0, 0.0, 0.0], [15.0, 210.0, 0.0]],
        },
    )
    row = _row("F003", 240.0, "U")
    assert _apply_single_open_lwpolyline_production_segments([row], [entity]) == 0
    assert not validate_production_segment_evidence(row)


def test_pipeline_classifies_frames_before_topology_and_before_dimension_fallback() -> None:
    source = inspect.getsource(build_batch_geometry_report)
    frame_index = source.index("_apply_beam_engineering_shape_law(")
    topology_index = source.index(
        "_apply_single_open_lwpolyline_production_segments(rows, entities)"
    )
    dimension_index = source.index("_apply_dimension_derived_production_sizes(rows, entities)")

    assert frame_index < topology_index < dimension_index
